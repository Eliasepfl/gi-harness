"""stale_seek — the TRAINED stale-seeker: a PPO adversary that LEARNS to drive a
certified game into a softlock, escalation tier above the greedy anti-policy search.

Elias's brief ("can we not use PPO too for getting into stale states?") + the
2026-07-15 design addendum. This module is the *training* half; the *detection* and
*certification* halves already live in ``harness.verify.g4`` (the stale-state tier:
triggers 1a/1b + the tree-refutation oracle 1c). We REUSE them wholesale — no cert
is ever minted here; a candidate the seeker discovers is only a SUSPECT until
``g4.refute_prefix`` (CONFIRM) refutes reachability from its frozen prefix.

Three pieces, all riding the EXISTING RL stack (GodotServeEnv / GodotBatchVecEnv /
sb3_trainer) — never a new stack:

  1. REWARD  — ``StaleSeekReward`` shapes the DETECT precondition into an RL reward:
     a small, TIME-DECAYING positive per step where the state fingerprint froze
     (``fp_delta < EFFICACY_EPS``) while an action was applied, no new checkpoint
     latched, and the episode did not terminate — escalating over consecutive frozen
     steps, with a big bonus (and a CANDIDATE emission) once a full ``window`` of
     frozen ticks completes. Anti-idling (addendum #1) is baked in: the freeze
     reward DECAYS over episode time, only scores AFTER demonstrated mobility, and
     the window-completion mechanism plus a pre-CONFIRM escapability probe keep
     "walk into a corner and wait" from being profitable. A LOSS (terminal, not
     success) and a WIN are BOTH strongly penalised — a loss is not a softlock
     (Elias's original distinction) and a win defeats the adversary. ALONGSIDE the
     freeze term (never replacing it) an OPTIONAL, flag-gated, motion-INVARIANT low-V
     OCCUPANCY term rewards sustained presence in a COLLAPSED-V state (V <= a relative
     floor) when a critic is supplied — the value-death sibling that catches a body
     WIGGLING in a trap the fingerprint-freeze term structurally misses; it inherits the
     SAME time-decay + mobility gate (anti-camping unchanged) and is OFF by default, so
     with no critic the reward is byte-identical.

  2. HARVEST — ``train_stale_seeker`` trains the seeker over the batched serve env
     and logs every window-complete candidate; ``harvest_candidates`` then rolls the
     trained policy from points SAMPLED ALONG THE WINNING TRAJECTORY (addendum #2:
     use the working policy's competent navigation to reach a deep/low-value waypoint,
     THEN freeze-seek from there) for more candidates.

  3. CONFIRM — ``confirm_candidates`` funnels every candidate into the EXISTING
     ``g4.refute_prefix`` oracle. Certification soundness is unchanged: no cert
     without a refuted, replayable ``{seed, actions}`` witness.

Thresholds tagged ``[eng.]`` are calibrated engineering choices; a concurrent
literature workflow on principled anti-idling / softlock-seeking may refine them.
The DETECT constants (``EFFICACY_EPS``, the no-progress ``window``, the mobility
floor) are IMPORTED from their canonical homes — never re-hardcoded here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

# --- Shared DETECT constants (import, never duplicate) -------------------- #
# EFFICACY_EPS: the px/rad snapshot divergence below which an action had "no effect"
# — the SAME freeze threshold g4's trigger_state_cycling uses. STUCK_WINDOW /
# STUCK_MOVE_MIN: g4's no-progress window + the "must have travelled first" floor
# (Elias directive: a body that never moved is a non-starter, not a soft-lock).
from harness.verify.gameverify import EFFICACY_EPS
from harness.verify.g4 import STUCK_WINDOW, STUCK_MOVE_MIN, PROBE_HORIZON
from harness.core.statetree import fingerprint, fp_delta, FP_DECIMALS_DEFAULT

# Obs-layout constants — the reward reads state through the SAME obs vector the
# policy sees, so the freeze test rides the env's own observation (no side channel).
from harness.rl.env import PER_BODY_2D, PER_BODY_3D, VEL_SCALE, _world_extents

__all__ = [
    "SeekParams", "StaleSeekReward", "fingerprint_from_obs",
    "StaleSeekEnv", "make_stale_seek_vec_wrapper",
    "train_stale_seeker", "harvest_candidates",
    "escapability_probe", "confirm_candidates",
]


# ======================================================================== #
# Reward parameters — one place for every knob (all [eng.] unless imported).
# ======================================================================== #
@dataclass
class SeekParams:
    """Reward shape for the stale-seeker. Freeze/window/mobility thresholds are the
    IMPORTED DETECT constants; the reward magnitudes are calibrated [eng.]."""
    window: int = STUCK_WINDOW              # frozen ticks == a candidate (DETECT, imported)
    eps: float = EFFICACY_EPS               # freeze threshold (DETECT, imported)
    mobility_min: float = STUCK_MOVE_MIN    # px the body must travel before a freeze scores (imported)
    horizon: int = PROBE_HORIZON            # episode length, for the time-decay ramp
    r_frozen_step: float = 0.10             # [eng.] base per-frozen-step reward (small)
    r_window_bonus: float = 5.0             # [eng.] window-complete bonus (the real signal)
    r_terminal: float = -1.0                # [eng.] LOSS/hazard penalty — a loss is NOT a softlock
    r_success: float = -1.0                 # [eng.] WIN penalty — winning defeats the adversary
    time_decay: bool = True                 # freeze reward decays over episode time (anti-idle #1a)
    mobility_gate: bool = True              # freeze scores only after demonstrated mobility (#1b)
    low_v_coef: float = 0.0                 # optional low-V shaping coef (needs a critic; off by default)
    # --- VALUE-DEATH occupancy term (motion-INVARIANT, critic+flag-gated, off by default) ---
    # Reward sustained presence in a COLLAPSED-V state (V <= low_v_floor), the reward-side
    # sibling of adversary.detect_value_death: it fires whether the body is FROZEN or
    # WIGGLING, so the seeker learns to drive into value-death pockets the fingerprint-
    # freeze term structurally misses. The floor is RELATIVE but supplied by the caller
    # (computed once from a witness/calibration V range so the reward stays Markov +
    # online-safe — a fraction of the witness-trajectory V range, the same relative-band
    # idea as the DETECT floor) [eng.]. With no floor/coef/critic -> byte-identical.
    low_v_occupancy_coef: float = 0.0       # per-step low-V occupancy reward (0 == off) [eng.]
    low_v_floor: Optional[float] = None     # relative collapse floor (V <= this == collapsed) [eng.]

    def decay(self, tick: int) -> float:
        """Linear anti-idle ramp: an early freeze is worth full, a late one ~nothing.
        Camping to the horizon earns essentially zero (Elias #1a)."""
        if not self.time_decay:
            return 1.0
        return max(0.0, 1.0 - float(tick) / float(max(1, self.horizon)))


@dataclass
class _SeekState:
    """Per-episode (per-instance) running state for the reward machine."""
    streak: int = 0                 # consecutive frozen ticks
    mobility: float = 0.0           # cumulative travelled distance this episode
    window_start_tick: int = 0      # tick the current frozen run opened
    emitted: bool = False           # window already emitted for the current frozen run
    low_v_streak: int = 0           # consecutive collapsed-V (value-death) ticks
    low_v_start_tick: int = 0       # tick the current low-V run opened
    low_v_emitted: bool = False     # value-death window already emitted this low-V run

    def reset(self) -> None:
        self.streak = 0
        self.mobility = 0.0
        self.window_start_tick = 0
        self.emitted = False
        self.low_v_streak = 0
        self.low_v_start_tick = 0
        self.low_v_emitted = False


class StaleSeekReward:
    """The reward machine. Engine-agnostic and env-free so it unit-tests in isolation:
    feed it per-step signals, get ``(reward, event)`` where ``event`` is ``None`` or a
    window-complete candidate ``{"freeze_start_tick", "streak"}``.

    One instance can drive N parallel instances via an integer key; state is created
    lazily and reset on episode end. Deterministic — no RNG, pure function of inputs."""

    def __init__(self, params: Optional[SeekParams] = None):
        self.p = params or SeekParams()
        self._st: dict[int, _SeekState] = {}

    def state(self, key: int = 0) -> _SeekState:
        st = self._st.get(key)
        if st is None:
            st = _SeekState()
            self._st[key] = st
        return st

    def reset(self, key: int = 0) -> None:
        self.state(key).reset()

    def step(self, prev_fp, cur_fp, *, new_latch: bool, terminated: bool,
             truncated: bool, success: bool, tick: int, action_applied: bool = True,
             value: Optional[float] = None, key: int = 0):
        """Advance one tick. Returns ``(reward, event)``.

        * success terminal           -> ``r_success`` (winning is a loss for us)
        * non-success terminal        -> ``r_terminal`` (a LOSS/hazard, not a softlock)
        * truncation (horizon/oob)    -> 0.0 (neutral end)
        * frozen & mobility satisfied -> escalating, time-decayed positive; a full
          ``window`` -> big bonus + a candidate ``event``
        * otherwise                   -> 0.0 (+ optional low-V shaping)
        """
        p = self.p
        st = self.state(key)

        # -- Terminal handling first: strong, unambiguous signals (Elias). ----
        if success:
            return float(p.r_success), None
        if terminated:                         # terminal but not success == a LOSS
            return float(p.r_terminal), None
        if truncated:                          # horizon / out-of-bounds: neutral end
            return 0.0, None

        # -- Movement since the previous tick (inf == topology change / unknown). --
        moved = fp_delta(prev_fp, cur_fp)
        shaping = 0.0 if (value is None or not p.low_v_coef) else -float(p.low_v_coef) * float(value)

        # -- Motion-INVARIANT low-V occupancy (additive, critic+flag-gated). Reward
        #    sustained presence in a COLLAPSED-V state (V <= the relative floor) whether
        #    the body is FROZEN or WIGGLING — the value-death sibling of the freeze term,
        #    kept ALONGSIDE it. Anti-camping preserved (SAME mobility gate + time decay);
        #    a full window emits a value_death candidate for the harvest. OFF by default
        #    (coef 0 / floor None / value None) -> byte-identical to today.
        occ, vd_event = self._low_v_occupancy(value, new_latch, tick, st)

        frozen = action_applied and (not new_latch) and (moved < p.eps)
        if not frozen:
            # The body moved (or progressed) — accumulate mobility, break the streak.
            if math.isfinite(moved):
                st.mobility += moved
            else:
                # A topology change (body appeared/vanished) is decidedly NOT frozen;
                # count it as ample mobility so a genuine later freeze can score.
                st.mobility += p.mobility_min
            st.streak = 0
            st.emitted = False
            return shaping + occ, vd_event

        # -- Frozen. Gate on demonstrated mobility (anti-idle #1b): a body that never
        #    moved is idling in a corner, not stuck — it does not score or accumulate.
        if p.mobility_gate and st.mobility < p.mobility_min:
            return 0.0, None                     # occ is mobility-gated too -> 0 here

        st.streak += 1
        if st.streak == 1:
            st.window_start_tick = int(tick)

        decay = p.decay(int(tick))
        # Escalating but capped at the window length so an over-long camp cannot farm
        # unbounded reward; the decay makes a late freeze worth little regardless.
        reward = p.r_frozen_step * min(st.streak, p.window) * decay

        event = None
        if st.streak >= p.window and not st.emitted:
            # A FULL window of unbroken freeze == a candidate. The bonus uses the
            # decay at the window's OPENING (an early trap is worth more). Because the
            # window only completes on a SUSTAINED freeze, a "freeze then walk away"
            # never earns it — same-action escapability is handled here; DIFFERENT-
            # action escapability is left to the probe + the CONFIRM oracle.
            reward += p.r_window_bonus * p.decay(st.window_start_tick)
            st.emitted = True
            event = {"freeze_start_tick": int(st.window_start_tick), "streak": int(st.streak)}
        # A freeze window takes priority; else surface a value-death window event.
        return float(reward + occ), (event or vd_event)

    def _low_v_occupancy(self, value, new_latch, tick, st: _SeekState):
        """The motion-INVARIANT low-V occupancy term. Returns ``(reward, event)``. ALL
        gated: a critic value must be supplied, the coef + floor set, no new checkpoint
        this tick (progress is not a softlock), and the SAME mobility gate as the freeze
        term satisfied (a never-moved idler cannot farm — anti-camping #1b). A sustained
        ``window`` of collapsed-V occupancy emits ONE ``value_death`` candidate (for the
        harvest); the per-step reward is decayed + streak-capped like the freeze term, so
        a mediocre-V region cannot farm. OFF (returns ``0.0, None``, no state touched
        beyond a reset) unless armed -> byte-identical default."""
        p = self.p
        if (not p.low_v_occupancy_coef or value is None or p.low_v_floor is None
                or new_latch or float(value) > float(p.low_v_floor)
                or (p.mobility_gate and st.mobility < p.mobility_min)):
            st.low_v_streak = 0
            st.low_v_emitted = False
            return 0.0, None
        st.low_v_streak += 1
        if st.low_v_streak == 1:
            st.low_v_start_tick = int(tick)
        decay = p.decay(int(tick))
        reward = p.low_v_occupancy_coef * min(st.low_v_streak, p.window) * decay
        event = None
        if st.low_v_streak >= p.window and not st.low_v_emitted:
            reward += p.r_window_bonus * p.decay(st.low_v_start_tick)
            st.low_v_emitted = True
            event = {"freeze_start_tick": int(st.low_v_start_tick),
                     "streak": int(st.low_v_streak), "kind": "value_death"}
        return float(reward), event


# ======================================================================== #
# Fingerprint from the observation vector.
# ======================================================================== #
def fingerprint_from_obs(obs, body_order, world_size, dim: int = 2) -> tuple:
    """Reconstruct a state fingerprint from the SAME obs vector the policy sees — so
    the reward's freeze test uses the env's own observation, no side channel into the
    serve process. ``dim`` is the env's PINNED layout dimension (2 or 3).

    ``build_obs_vector`` normalises pos by world-size, vel by ``VEL_SCALE`` and encodes
    orientation as (sin,cos) [2D] or a unit quaternion [3D]; we invert that per present
    body. The mapping is monotonic and float32 resolution (~5e-5 px reconstructed) is
    far finer than ``EFFICACY_EPS`` (1e-3), so the freeze decision is faithful: an
    identical state yields a byte-identical obs and hence ``fp_delta == 0``; any move
    above the threshold shows up above it. A body absent from the obs (present-bit 0)
    is OMITTED, so a topology change makes ``fp_delta`` infinite exactly as the raw
    snapshot path would.

    2D returns the SAME tuple shape as :func:`statetree.fingerprint`. 3D returns an
    extended per-body tuple ``(name, x, y, z, vx, vy, vz, qx, qy, qz, qw)`` (rounded
    to the same decimals): fingerprints from this function are only ever compared to
    one another via :func:`fp_delta` (which is arity-generic), so the wider tuple is
    a strictly MORE faithful freeze test — it also catches pure z-motion and rotation
    the 2D digest drops."""
    w, h, d = _world_extents(world_size)
    obs = np.asarray(obs, dtype=np.float64).reshape(-1)
    if int(dim) == 3:
        out = []
        i = 0
        for name in body_order:
            if obs[i + 0] >= 0.5:                      # present bit
                dec = FP_DECIMALS_DEFAULT
                out.append((
                    str(name),
                    round(float(obs[i + 1]) * w, dec),
                    round(float(obs[i + 2]) * h, dec),
                    round(float(obs[i + 3]) * d, dec),
                    round(float(obs[i + 4]) * VEL_SCALE, dec),
                    round(float(obs[i + 5]) * VEL_SCALE, dec),
                    round(float(obs[i + 6]) * VEL_SCALE, dec),
                    round(float(obs[i + 7]), dec), round(float(obs[i + 8]), dec),
                    round(float(obs[i + 9]), dec), round(float(obs[i + 10]), dec),
                ))
            i += PER_BODY_3D
        return tuple(sorted(out))
    snap: dict = {}
    i = 0
    for name in body_order:
        if obs[i + 0] >= 0.5:                          # present bit
            px = float(obs[i + 1]) * w
            py = float(obs[i + 2]) * h
            vx = float(obs[i + 3]) * VEL_SCALE
            vy = float(obs[i + 4]) * VEL_SCALE
            angle = math.atan2(float(obs[i + 5]), float(obs[i + 6]))
            snap[str(name)] = {"pos": (px, py), "vel": (vx, vy), "angle": angle}
        i += PER_BODY_2D
    return fingerprint(snap)


# ======================================================================== #
# Single-env wrapper — full control of the episode (harvest + easy testing).
# ======================================================================== #
class StaleSeekEnv:
    """Gymnasium-style wrapper that re-shapes a serve env's reward into the stale-seek
    signal and ENDS the episode (truncated) on a window-complete candidate. Duck-typed
    over :class:`GodotServeEnv` (needs ``reset``/``step``/``actions``/``horizon``/
    ``observation_space``/``action_space``/``_body_order``/``world_size``), so a stub
    env exercises the whole episode logic without Godot.

    Records every candidate it emits on ``self.candidates`` as ``{seed, prefix}`` where
    ``prefix`` is the action sequence that led INTO the frozen region (the natural cut
    point CONFIRM plants) — exactly the shape ``confirm_candidates`` consumes."""

    def __init__(self, env, params: Optional[SeekParams] = None, *,
                 end_on_window: bool = True, critic=None):
        self.env = env
        self.actions = list(env.actions)
        self.horizon = int(getattr(env, "horizon", PROBE_HORIZON))
        self.observation_space = env.observation_space
        self.action_space = env.action_space
        self._body_order = list(env._body_order)
        self.world_size = tuple(env.world_size)
        self._dim = int(getattr(env, "_dim", 2))    # env's pinned obs dimension
        p = params or SeekParams()
        # Inherit the env's real horizon into the decay ramp unless the caller pinned one.
        if params is None:
            p.horizon = self.horizon
        self.p = p
        # Optional critic (duck-typed ``value(obs) -> float``) — threads V(s) into the
        # reward so the motion-invariant low-V occupancy term can fire. None -> value is
        # never supplied -> byte-identical to the value-less path (pinned by the tests).
        self.critic = critic
        self.reward = StaleSeekReward(p)
        self.end_on_window = bool(end_on_window)
        self.candidates: list[dict] = []
        self._seed = 0
        self._prev_fp = None
        self._prev_nlatched = 0
        self._hist: list[str] = []
        self._tick = 0

    def reset(self, seed: int = 0):
        obs, info = self.env.reset(seed=seed)
        self._seed = int(seed)
        self.reward.reset()
        self._prev_fp = fingerprint_from_obs(obs, self._body_order, self.world_size,
                                             dim=self._dim)
        self._prev_nlatched = _n_latched(info)
        self._hist = []
        self._tick = 0
        return obs, info

    def step(self, action_idx: int):
        obs, _r, terminated, truncated, info = self.env.step(action_idx)
        self._hist.append(self.actions[int(action_idx)])
        self._tick = int(info.get("tick", self._tick + 1))
        cur_fp = fingerprint_from_obs(obs, self._body_order, self.world_size,
                                      dim=self._dim)
        n_latched = _n_latched(info)
        new_latch = n_latched > self._prev_nlatched
        success = bool(info.get("success"))

        reward, event = self.reward.step(
            self._prev_fp, cur_fp, new_latch=new_latch, terminated=terminated,
            truncated=truncated, success=success, tick=self._tick, action_applied=True,
            value=self._value(obs))

        if event is not None:
            prefix = list(self._hist[:event["freeze_start_tick"]])
            kind = event.get("kind", "frozen")
            self.candidates.append({"seed": self._seed, "prefix": prefix,
                                    "freeze_tick": event["freeze_start_tick"], "kind": kind})
            info = dict(info)
            info["stale_candidate"] = {"seed": self._seed, "prefix": prefix, "kind": kind}
            if self.end_on_window:
                truncated = True                    # candidate emitted -> end the episode

        self._prev_fp = cur_fp
        self._prev_nlatched = n_latched
        return obs, float(reward), terminated, truncated, info

    def _value(self, obs):
        """V(s) from the optional critic (None when no critic -> value-less path)."""
        if self.critic is None:
            return None
        try:
            return float(self.critic.value(obs))
        except Exception:      # noqa: BLE001 - a degenerate critic must not sink the rollout
            return None

    def close(self):
        close = getattr(self.env, "close", None)
        if callable(close):
            close()


def _n_latched(info: dict) -> int:
    """Latched-checkpoint count from a step info, tolerating both the serve env's
    ``n_latched`` and the ``latched`` map shapes."""
    if "n_latched" in info:
        return int(info["n_latched"])
    latched = info.get("latched") or {}
    return sum(1 for v in latched.values() if v is not None)


# ======================================================================== #
# Batched VecEnv wrapper — training throughput (N-in-one-proc at speedup 8).
# ======================================================================== #
def make_stale_seek_vec_wrapper(venv, params: Optional[SeekParams] = None, *,
                                base_seed: int = 0, candidates: Optional[list] = None):
    """Wrap a ``GodotBatchVecEnv`` (or any SB3 ``VecEnv`` exposing ``_body_order`` /
    ``world_size`` / ``actions`` / ``num_envs``) so every step's reward becomes the
    stale-seek signal and window-complete candidates are appended to ``candidates``.

    Built lazily around ``VecEnvWrapper`` so this module imports without SB3. Instance
    ``i`` always plays seed ``base_seed + i`` (the batched env's fixed per-slot scheme),
    which is the seed we record with each candidate.

    The VecEnv autoreset contract forbids forcing a mid-episode done from a wrapper, so
    (unlike the single-env wrapper) the batched wrapper EMITS the candidate + pays the
    bonus but lets the episode ride to its natural end; the reward stays bounded (the
    per-step freeze reward is capped at the window and decays over time, and the bonus
    fires once per frozen run). Candidate discovery is identical."""
    from stable_baselines3.common.vec_env.base_vec_env import VecEnvWrapper

    sink = candidates if candidates is not None else []
    p = params or SeekParams()

    class _StaleSeekVecWrapper(VecEnvWrapper):
        def __init__(self, venv):
            super().__init__(venv)
            self.candidates = sink
            self.p = p
            self.reward = StaleSeekReward(p)
            self._body_order = list(venv._body_order)
            self.world_size = tuple(venv.world_size)
            self._dim = int(getattr(venv, "_dim", 2))   # venv's pinned obs dimension
            self._actions = list(venv.actions)
            self._base_seed = int(getattr(venv, "_base_seed", base_seed))
            n = self.num_envs
            self._prev_fp = [None] * n
            self._prev_nlatched = [0] * n
            self._hist: list[list[str]] = [[] for _ in range(n)]
            self._last_actions = np.zeros(n, dtype=int)

        def reset(self):
            obs = self.venv.reset()
            for i in range(self.num_envs):
                self.reward.reset(i)
                self._prev_fp[i] = fingerprint_from_obs(obs[i], self._body_order,
                                                        self.world_size, dim=self._dim)
                self._prev_nlatched[i] = 0
                self._hist[i] = []
            return obs

        def step_async(self, actions):
            self._last_actions = np.asarray(actions).reshape(-1).astype(int)
            self.venv.step_async(actions)

        def step_wait(self):
            obs, rewards, dones, infos = self.venv.step_wait()
            n = self.num_envs
            out_rewards = np.array(rewards, dtype=np.float32)
            for i in range(n):
                info = infos[i]
                done = bool(dones[i])
                truncated = bool(info.get("TimeLimit.truncated", False))
                terminated = done and not truncated
                success = bool(info.get("success"))
                # On a done step obs[i] is already the RESET obs; the terminal state is
                # stashed in terminal_observation — fingerprint THAT for the freeze test.
                frame_obs = info.get("terminal_observation") if done else obs[i]
                cur_fp = fingerprint_from_obs(frame_obs, self._body_order,
                                              self.world_size, dim=self._dim)

                self._hist[i].append(self._actions[int(self._last_actions[i])])
                n_latched = int(info.get("n_latched", 0))
                new_latch = n_latched > self._prev_nlatched[i]
                tick = int(info.get("tick", len(self._hist[i])))

                reward, event = self.reward.step(
                    self._prev_fp[i], cur_fp, new_latch=new_latch,
                    terminated=terminated, truncated=truncated, success=success,
                    tick=tick, action_applied=True, key=i)
                out_rewards[i] = reward

                if event is not None:
                    prefix = list(self._hist[i][:event["freeze_start_tick"]])
                    self.candidates.append({"seed": self._base_seed + i,
                                            "prefix": prefix,
                                            "freeze_tick": event["freeze_start_tick"]})

                if done:
                    self.reward.reset(i)
                    self._prev_fp[i] = fingerprint_from_obs(obs[i], self._body_order,
                                                            self.world_size, dim=self._dim)
                    self._prev_nlatched[i] = 0
                    self._hist[i] = []
                else:
                    self._prev_fp[i] = cur_fp
                    self._prev_nlatched[i] = n_latched
            return obs, out_rewards, dones, infos

    return _StaleSeekVecWrapper(venv)


# ======================================================================== #
# Train + harvest.
# ======================================================================== #
def train_stale_seeker(game_path: str, *, budget_steps: int = 20000, num_envs: int = 4,
                       seed: int = 0, params: Optional[SeekParams] = None,
                       port_offset: int = 0, horizon: int = PROBE_HORIZON,
                       log=None, wall_clock_budget_s=None, **train_overrides) -> dict:
    """Train the stale-seeker on ``game_path`` over the BATCHED serve env (gdscript lane
    only — ``GodotBatchVecEnv`` is the N-in-one-proc host) and return the trained policy
    plus every candidate emitted during training.

    Reuses ``sb3_trainer.train`` exactly: we hand it a ``make_batch_venv`` factory that
    wraps ``GodotBatchVecEnv`` in the stale-seek reward wrapper, so training is the same
    batched-at-speedup-8 machinery ``g3_prime`` uses. Cost: ONE PPO training per game.

    Returns ``{policy, candidates, train_res, obs_dim, n_actions, actions, horizon}``."""
    import itertools
    from harness.rl import sb3_trainer
    from harness.rl.godot_env import GodotServeEnv
    from harness.rl.godot_vec_env import GodotBatchVecEnv

    p = params or SeekParams()
    p.horizon = int(horizon)
    candidates: list[dict] = []
    _ports = itertools.count(port_offset)
    # The plateau early-stop watches the INNER episodic return (checkpoints/success) —
    # an objective the ADVERSARY does not optimise (it is penalised for winning), so it
    # plateaus at ~0 immediately and would cut training short. Consume the full budget
    # (the honest tick cost) unless the caller pins its own patience.
    train_overrides.setdefault("patience", 10 ** 9)

    def make_env():                                   # single-env fallback / probe / eval
        return GodotServeEnv(game_path, port_offset=next(_ports), horizon=horizon)

    def make_batch_venv(n_instances):
        venv = GodotBatchVecEnv(game_path, n_instances, port_offset=next(_ports),
                                seed=seed, horizon=horizon)
        return make_stale_seek_vec_wrapper(venv, p, base_seed=seed, candidates=candidates)

    probe = make_env()
    obs_dim = probe.observation_space.shape[0]
    n_actions = probe.action_space.n
    actions = list(probe.actions)
    probe.close()

    train_res = sb3_trainer.train(
        make_env, obs_dim, n_actions, total_steps=budget_steps, seed=seed,
        num_envs=num_envs, make_batch_venv=make_batch_venv, log=log,
        wall_clock_budget_s=wall_clock_budget_s, **train_overrides)

    return {"policy": train_res["agent"], "candidates": candidates,
            "train_res": train_res, "obs_dim": obs_dim, "n_actions": n_actions,
            "actions": actions, "horizon": int(horizon), "game_path": game_path}


def _as_predict_fn(policy) -> Callable:
    """Adapt an SB3 model (``.predict``) or a bare ``callable(obs) -> action_idx`` into a
    uniform greedy action function."""
    if hasattr(policy, "predict"):
        def _fn(obs):
            action, _ = policy.predict(np.asarray(obs, dtype=np.float32),
                                       deterministic=True)
            return int(np.asarray(action).reshape(-1)[0])
        return _fn
    return lambda obs: int(policy(obs))


def harvest_candidates(make_env: Callable, policy, *, seeds=(0,), witness=None,
                       waypoints=(0,), params: Optional[SeekParams] = None,
                       max_candidates: int = 64, critic=None) -> list[dict]:
    """Greedy-rollout the trained seeker for more candidates, with witness-waypoint
    seeding (addendum #2): for each ``waypoints`` cut of the WINNING trajectory, replay
    that winning prefix (the working policy's competent navigation to a deep waypoint),
    THEN hand control to the trained seeker to freeze-seek from there.

    ``make_env`` is a 0-arg factory (fresh serve env per rollout); ``witness`` is a
    winning ``{"actions": [...]}`` (or a bare action list). ``waypoints`` are prefix
    LENGTHS to branch from (``0`` == seek from the start). An optional ``critic`` threads
    V(s) into the reward so a WIGGLING value-death pocket (which the freeze term misses)
    is also harvested (motion-invariant low-V occupancy). Deterministic: greedy policy,
    fixed seeds. Returns de-duplicated ``{seed, prefix}`` candidates."""
    predict = _as_predict_fn(policy)
    witness_actions = list(witness.get("actions") if isinstance(witness, dict) else (witness or []))
    p = params or SeekParams()
    out: list[dict] = []
    seen: set = set()

    for seed in seeds:
        for wp in waypoints:
            if out and len(out) >= max_candidates:
                return out
            env = make_env()
            wrapped = StaleSeekEnv(env, p, end_on_window=True, critic=critic)
            try:
                obs, _ = wrapped.reset(seed=int(seed))
                cut = min(int(wp), len(witness_actions))
                idx = {a: i for i, a in enumerate(wrapped.actions)}
                for t in range(wrapped.horizon):
                    if t < cut:                        # follow the winning trajectory
                        a = idx.get(witness_actions[t])
                        if a is None:                  # off-vocab witness token -> stop seeding
                            cut = t
                            a = predict(obs)
                    else:                              # seek from the waypoint
                        a = predict(obs)
                    obs, _r, term, trunc, info = wrapped.step(a)
                    if term or trunc:
                        break
                for cand in wrapped.candidates:
                    key = (cand["seed"], tuple(cand["prefix"]))
                    if key not in seen:
                        seen.add(key)
                        out.append({"seed": cand["seed"], "prefix": cand["prefix"]})
            finally:
                wrapped.close()
    return out[:max_candidates]


# ======================================================================== #
# Escapability probe + CONFIRM (reuses g4.refute_prefix — soundness unchanged).
# ======================================================================== #
def escapability_probe(executor, game_source, actions, prefix, *, seed=0, k: int = 6,
                       trials: int = 4, eps: float = EFFICACY_EPS) -> dict:
    """Cheap pre-CONFIRM filter (Elias #1c): from the frozen state at the end of
    ``prefix``, try a few short RANDOM action tails; if any REACHES SUCCESS, the state
    is trivially escapable to a win — not a softlock — so drop it before spending a full
    CONFIRM budget. Non-winning motion is left to the sound CONFIRM oracle (which tries
    every action systematically). Returns ``{escapable, reason}``.

    This is a recall-safe optimisation, never a certifier: it only ever DROPS a
    candidate that a random tail wins from (which CONFIRM would refute anyway)."""
    import random
    rng = random.Random(hash((tuple(prefix), seed, k, trials)) & 0xffffffff)
    specs = []
    for _ in range(max(1, trials)):
        tail = [actions[rng.randrange(len(actions))] for _ in range(max(1, k))]
        specs.append({"seed": seed, "actions": list(prefix) + tail})
    try:
        eps_runs = executor.run_batch(game_source, specs, len(prefix) + k)
    except Exception:
        return {"escapable": False, "reason": "probe_error"}
    for ep in eps_runs:
        if ep.get("result") == "success":
            return {"escapable": True, "reason": "random_tail_wins"}
    return {"escapable": False, "reason": "no_trivial_escape"}


def confirm_candidates(executor, game_source, actions, candidates, *, H=None, budget=None,
                       engine="py", top_m: int = 8, probe: bool = True, probe_k: int = 6,
                       probe_trials: int = 4) -> dict:
    """Funnel candidates into the EXISTING CONFIRM oracle (``g4.refute_prefix``): plant
    each frozen prefix and run the SAME Go-Explore solver that certifies G3 on its
    continuations. A prefix with NO winning continuation under budget is a CERTIFIED
    softlock witness; the finding is byte-identical in shape to g4's stale-tier
    ``softlock`` finding (hard outcome -> grade ``open``).

    De-dups prefixes, optionally drops trivially-escapable ones (``probe``), caps at
    ``top_m`` before the expensive oracle, and NEVER mints a cert without a refuted,
    replayable witness. Returns ``{findings, certified, refuted, probed_out, ...}``."""
    from harness.verify import g4

    kw = {}
    if H is not None:
        kw["H"] = H
    if budget is not None:
        kw["budget"] = budget

    # De-dup by (seed, prefix); a zero-length prefix is not a claim, drop it.
    uniq: list[dict] = []
    seen: set = set()
    for c in candidates:
        prefix = list(c.get("prefix") or [])
        if not prefix:
            continue
        key = (int(c.get("seed", 0)), tuple(prefix))
        if key not in seen:
            seen.add(key)
            uniq.append({"seed": int(c.get("seed", 0)), "prefix": prefix})

    findings: list[dict] = []
    certified = 0
    refuted = 0
    probed_out = 0
    considered = 0
    for c in uniq:
        if considered >= top_m:
            break
        prefix, seed = c["prefix"], c["seed"]
        if probe:
            pr = escapability_probe(executor, game_source, actions, prefix,
                                    seed=seed, k=probe_k, trials=probe_trials)
            if pr["escapable"]:
                probed_out += 1
                continue
        considered += 1
        try:
            res = g4.refute_prefix(executor, game_source, actions, prefix,
                                   engine=engine, seed=seed, **kw)
        except Exception:
            continue
        if not res["certified"]:
            refuted += 1
            continue
        certified += 1
        findings.append({
            "outcome": "softlock", "tier": "seeker", "family": "tree_refute",
            "hard": True,
            "detail": (f"TRAINED seeker found an action prefix (len {len(prefix)}) that "
                       f"soft-locks the game — the G3 solver found no win in "
                       f"{res['budget']} ticks under it (subtree {res['subtree_status']})"),
            "reproducer": {
                "engine": engine, "seed": seed,
                "action_plan": {"kind": "sequence", "sequence": list(prefix)},
                "provenance": {"oracle": "stale_seek+tree_refute", "H": res["H"],
                               "budget": res["budget"], "engine": engine, "seed": seed,
                               "subtree_status": res["subtree_status"],
                               "discovered_by": "trained_ppo_seeker"},
            },
            "evidence": {"result": "budget", "seed": seed, "prefix_len": len(prefix)},
        })

    return {"findings": findings, "certified": certified, "refuted": refuted,
            "probed_out": probed_out, "considered": considered,
            "candidates_in": len(candidates), "candidates_unique": len(uniq)}
