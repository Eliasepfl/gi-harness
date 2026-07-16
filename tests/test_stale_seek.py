"""stale_seek — the TRAINED stale-seeker (PPO adversary that learns to softlock).

Layered so almost everything runs WITHOUT Godot (fast, in-image):

* reward core (``StaleSeekReward``)  — pure per-step signal machine, no env at all;
* ``fingerprint_from_obs``           — obs-vector -> fingerprint reconstruction;
* ``StaleSeekEnv`` / vec wrapper     — episode logic driven by a scripted STUB env;
* CONFIRM (``confirm_candidates``)   — funnels candidates into the real ``g4.refute_prefix``
                                       over the py-DSL SOFTLOCK fixture (PyExecutor, no Godot);
* ladder GATE                        — deep tier fires only on ``deep=True`` AND only when
                                       the cheap tiers certified nothing (monkeypatched seam).

The one Godot-dependent case (a real PPO seeker training + harvest + confirm on a
``.gd`` softlock) lives in ``tests/test_stale_seek_godot.py`` (skipped without Godot).
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness.rl import stale_seek as ss  # noqa: E402
from harness.rl.env import build_obs_vector, PER_BODY, PER_BODY_3D, EGO_BLOCK_3D  # noqa: E402
from harness.core.statetree import fingerprint, fp_delta  # noqa: E402
from harness.verify import g4  # noqa: E402
from harness.verify.executors import PyExecutor  # noqa: E402
from test_g4 import SOFTLOCK  # noqa: E402  (the py-DSL momentum-pit softlock fixture)
from test_gameverify import factory  # noqa: E402


# ====================================================================== #
# helpers — build obs vectors + fingerprints for a one-body world.
# ====================================================================== #
WORLD = (800.0, 600.0)
BODY_ORDER = ["player"]
CP_KEYS = ["cp"]
OBS_DIM = len(BODY_ORDER) * PER_BODY + len(CP_KEYS) + 1


def _snap(x, y=300.0, vx=0.0, vy=0.0):
    return {"player": {"pos": (float(x), float(y)), "vel": (float(vx), float(vy)),
                       "angle": 0.0}}


def _obs(x, y=300.0, vx=0.0, vy=0.0, latched=False, tick=0, horizon=120):
    obs_state = {"player": {"pos": (x, y), "vel": (vx, vy), "angle": 0.0,
                            "controlled": True}}
    latched_map = {"cp": (tick if latched else None)}
    return build_obs_vector(obs_state, latched_map, BODY_ORDER, CP_KEYS, WORLD,
                            tick, horizon)


def _fp(x, y=300.0, vx=0.0, vy=0.0):
    return fingerprint(_snap(x, y, vx, vy))


# ====================================================================== #
# 1. Reward core — the DETECT precondition shaped into a reward.
# ====================================================================== #
def _params(**kw):
    base = dict(window=4, mobility_min=5.0, horizon=50, time_decay=False,
                r_frozen_step=0.1, r_window_bonus=5.0, r_terminal=-1.0, r_success=-1.0)
    base.update(kw)
    return ss.SeekParams(**base)


def _prime_mobility(rw, start_fp):
    """Walk the body far enough to clear the mobility gate; returns the last fp."""
    prev = start_fp
    x = 0.0
    while rw.state().mobility < rw.p.mobility_min:
        x += 3.0
        cur = _fp(x)
        rw.step(prev, cur, new_latch=False, terminated=False, truncated=False,
                success=False, tick=1, action_applied=True)
        prev = cur
    return prev, x


def test_frozen_reward_fires_only_when_all_preconditions_hold():
    rw = ss.StaleSeekReward(_params())
    prev, x = _prime_mobility(rw, _fp(0.0))
    frozen = _fp(x)                                     # identical state == frozen

    # (a) action applied + no new latch + non-terminal + frozen -> positive reward.
    r, ev = rw.step(prev, frozen, new_latch=False, terminated=False, truncated=False,
                    success=False, tick=10, action_applied=True)
    assert r > 0 and ev is None

    # (b) a NEW checkpoint latched this tick -> progress, not a softlock -> no reward.
    rw2 = ss.StaleSeekReward(_params())
    p2, x2 = _prime_mobility(rw2, _fp(0.0))
    r2, _ = rw2.step(p2, _fp(x2), new_latch=True, terminated=False, truncated=False,
                     success=False, tick=10, action_applied=True)
    assert r2 == 0.0

    # (c) no action applied -> not a "stuck WHILE acting" tick -> no reward.
    rw3 = ss.StaleSeekReward(_params())
    p3, x3 = _prime_mobility(rw3, _fp(0.0))
    r3, _ = rw3.step(p3, _fp(x3), new_latch=False, terminated=False, truncated=False,
                     success=False, tick=10, action_applied=False)
    assert r3 == 0.0

    # (d) the body actually MOVED (fp_delta >= eps) -> not frozen -> no reward.
    rw4 = ss.StaleSeekReward(_params())
    p4, x4 = _prime_mobility(rw4, _fp(0.0))
    r4, _ = rw4.step(p4, _fp(x4 + 50.0), new_latch=False, terminated=False,
                     truncated=False, success=False, tick=10, action_applied=True)
    assert r4 == 0.0


def test_terminal_and_success_are_penalised_truncation_is_neutral():
    p = _params()
    rw = ss.StaleSeekReward(p)
    # A LOSS (terminal, not success) is NOT a softlock -> strong penalty (Elias).
    r_loss, _ = rw.step(_fp(0), _fp(0), new_latch=False, terminated=True,
                        truncated=False, success=False, tick=5)
    assert r_loss == p.r_terminal
    # A WIN defeats the adversary -> strong penalty.
    r_win, _ = rw.step(_fp(0), _fp(0), new_latch=False, terminated=True,
                       truncated=False, success=True, tick=5)
    assert r_win == p.r_success
    # A horizon/oob truncation is a neutral end.
    r_tr, _ = rw.step(_fp(0), _fp(0), new_latch=False, terminated=False,
                      truncated=True, success=False, tick=5)
    assert r_tr == 0.0


def test_mobility_gate_blocks_idle_camping():
    # A body that NEVER moved (mobility stays 0) freezing forever earns nothing —
    # "going into a corner and waiting is not a softlock" (Elias #1b).
    rw = ss.StaleSeekReward(_params(mobility_min=20.0))
    prev = _fp(0.0)
    total = 0.0
    for t in range(1, 30):
        r, ev = rw.step(prev, _fp(0.0), new_latch=False, terminated=False,
                        truncated=False, success=False, tick=t, action_applied=True)
        total += r
        assert ev is None                              # never emits a candidate
    assert total == 0.0


def test_window_completion_emits_one_candidate_then_stops_escalating():
    p = _params(window=4, r_frozen_step=0.1, r_window_bonus=5.0)
    rw = ss.StaleSeekReward(p)
    prev, x = _prime_mobility(rw, _fp(0.0))
    frozen = _fp(x)
    rewards, events = [], []
    for t in range(1, 9):                              # 8 frozen ticks, window=4
        r, ev = rw.step(prev, frozen, new_latch=False, terminated=False,
                        truncated=False, success=False, tick=t, action_applied=True)
        rewards.append(r)
        events.append(ev)
    fired = [e for e in events if e is not None]
    assert len(fired) == 1                             # exactly one window emission
    assert fired[0]["streak"] == p.window
    # The window-complete step carries the big bonus; later frozen steps are capped.
    win_idx = events.index(fired[0])
    assert rewards[win_idx] >= p.r_window_bonus
    assert rewards[-1] < p.r_window_bonus              # no repeated bonus after the window


def test_time_decay_makes_late_freezes_worth_less():
    p = _params(time_decay=True, horizon=100, window=4)
    early = ss.StaleSeekReward(p)
    late = ss.StaleSeekReward(p)
    pe, xe = _prime_mobility(early, _fp(0.0))
    pl, xl = _prime_mobility(late, _fp(0.0))
    re, _ = early.step(pe, _fp(xe), new_latch=False, terminated=False, truncated=False,
                       success=False, tick=5, action_applied=True)
    rl, _ = late.step(pl, _fp(xl), new_latch=False, terminated=False, truncated=False,
                      success=False, tick=90, action_applied=True)
    assert re > rl > 0                                 # same freeze, later == worth less


def test_reward_core_is_deterministic():
    seq = [(_fp(i * 4.0), False, False, False, False, i + 1) for i in range(8)]
    seq += [(_fp(28.0), False, False, False, False, 9 + i) for i in range(6)]

    def run():
        rw = ss.StaleSeekReward(_params())
        prev = _fp(0.0)
        out = []
        for cur, nl, term, trunc, succ, tick in seq:
            r, ev = rw.step(prev, cur, new_latch=nl, terminated=term, truncated=trunc,
                            success=succ, tick=tick, action_applied=True)
            out.append((round(r, 9), ev))
            prev = cur
        return out

    assert run() == run()


# ====================================================================== #
# 1b. VALUE-DEATH low-V occupancy term — the motion-INVARIANT reward sibling.
# ====================================================================== #
def _vd_params(**kw):
    base = dict(low_v_occupancy_coef=0.2, low_v_floor=-1.0)
    base.update(kw)
    return _params(**base)


def test_low_v_occupancy_rewards_a_collapsed_v_while_WIGGLING():
    # Motion-INVARIANT: the body MOVES every tick (never frozen) but V has COLLAPSED
    # (-3 <= floor -1) -> the occupancy term pays (the freeze term is 0 the whole time),
    # and a sustained window emits a value_death candidate for the harvest.
    p = _vd_params(window=4)
    rw = ss.StaleSeekReward(p)
    prev, x = _prime_mobility(rw, _fp(0.0))
    total, events = 0.0, []
    for t in range(1, 7):
        cur = _fp(x + 12.0 * t)                            # moving -> NOT frozen
        r, ev = rw.step(prev, cur, new_latch=False, terminated=False, truncated=False,
                        success=False, tick=t, action_applied=True, value=-3.0)
        total += r
        events.append(ev)
        prev = cur
    assert total > 0.0, "occupancy must pay for collapsed-V presence despite motion"
    fired = [e for e in events if e is not None]
    assert len(fired) == 1 and fired[0]["kind"] == "value_death"
    assert fired[0]["streak"] == p.window


def test_low_v_occupancy_only_pays_below_the_floor():
    # V ABOVE the floor (0.0 > -1.0) while wiggling -> the region is not collapsed ->
    # no occupancy reward, no event (a mediocre-V region cannot farm).
    rw = ss.StaleSeekReward(_vd_params(low_v_occupancy_coef=0.5))
    prev, x = _prime_mobility(rw, _fp(0.0))
    r, ev = rw.step(prev, _fp(x + 10.0), new_latch=False, terminated=False,
                    truncated=False, success=False, tick=3, action_applied=True, value=0.0)
    assert r == 0.0 and ev is None


def test_low_v_occupancy_respects_the_mobility_gate():
    # Collapsed V but the body NEVER moved (mobility 0 < min) -> occupancy earns nothing
    # ("going into a corner and waiting is not a softlock" — anti-idle #1b, preserved).
    rw = ss.StaleSeekReward(_vd_params(low_v_occupancy_coef=0.5, mobility_min=20.0))
    prev = _fp(0.0)
    total = 0.0
    for t in range(1, 12):
        r, ev = rw.step(prev, _fp(0.0), new_latch=False, terminated=False, truncated=False,
                        success=False, tick=t, action_applied=True, value=-5.0)
        total += r
        assert ev is None
    assert total == 0.0


def test_low_v_occupancy_respects_the_no_checkpoint_guard():
    # A NEW checkpoint latching this tick == progress, not a softlock -> no occupancy.
    rw = ss.StaleSeekReward(_vd_params(low_v_occupancy_coef=0.5))
    prev, x = _prime_mobility(rw, _fp(0.0))
    r, ev = rw.step(prev, _fp(x + 10.0), new_latch=True, terminated=False, truncated=False,
                    success=False, tick=3, action_applied=True, value=-5.0)
    assert r == 0.0 and ev is None


def test_low_v_occupancy_is_byte_identical_without_a_critic():
    # Arming the term but passing NO critic value == today's behavior (the term is a pure
    # no-op when no value flows). Pins the mission's "no critic supplied -> byte-identical".
    def run(params):
        rw = ss.StaleSeekReward(params)
        prev, x = _prime_mobility(rw, _fp(0.0))
        frozen = _fp(x)
        out = []
        for t in range(1, 9):
            r, ev = rw.step(prev, frozen, new_latch=False, terminated=False,
                            truncated=False, success=False, tick=t, action_applied=True)
            out.append((round(r, 9), ev))
        return out

    base = run(_params(window=4))                                  # today: term absent
    armed = run(_vd_params(window=4))                              # term ARMED but no value
    assert armed == base


def test_low_v_occupancy_and_freeze_terms_coexist():
    # Body FROZEN and V collapsed -> BOTH terms contribute (occupancy is ALONGSIDE, never
    # replacing, the freeze term); paying more than the freeze term alone.
    prev0 = _fp(0.0)
    freeze_only = ss.StaleSeekReward(_params(window=4))
    both = ss.StaleSeekReward(_vd_params(window=4))
    pf, xf = _prime_mobility(freeze_only, prev0)
    pb, xb = _prime_mobility(both, prev0)
    rf, _ = freeze_only.step(pf, _fp(xf), new_latch=False, terminated=False, truncated=False,
                             success=False, tick=3, action_applied=True, value=-5.0)
    rb, _ = both.step(pb, _fp(xb), new_latch=False, terminated=False, truncated=False,
                      success=False, tick=3, action_applied=True, value=-5.0)
    assert rb > rf > 0.0


# ====================================================================== #
# 2. fingerprint_from_obs — faithful reconstruction from the obs vector.
# ====================================================================== #
def test_fingerprint_from_obs_matches_and_detects_freeze_and_motion():
    o0 = _obs(120.0, 300.0, vx=0.0, tick=0)
    o0b = _obs(120.0, 300.0, vx=0.0, tick=7)           # SAME body state, later tick
    o1 = _obs(180.0, 300.0, vx=0.0, tick=1)            # moved 60px

    f0 = ss.fingerprint_from_obs(o0, BODY_ORDER, WORLD)
    f0b = ss.fingerprint_from_obs(o0b, BODY_ORDER, WORLD)
    f1 = ss.fingerprint_from_obs(o1, BODY_ORDER, WORLD)

    # The tick channel differs but the BODY state is identical -> frozen (delta 0).
    assert fp_delta(f0, f0b) < ss.EFFICACY_EPS
    # Real motion shows up above the threshold.
    assert fp_delta(f0, f1) > ss.EFFICACY_EPS
    # Reconstruction lands within EFFICACY_EPS of the raw-snapshot fingerprint.
    assert fp_delta(f0, _fp(120.0, 300.0)) < ss.EFFICACY_EPS


def test_fingerprint_from_obs_absent_body_is_a_topology_change():
    present = ss.fingerprint_from_obs(_obs(120.0), BODY_ORDER, WORLD)
    gone = np.zeros(OBS_DIM, dtype=np.float32)          # present-bit 0 -> body omitted
    assert fp_delta(present, ss.fingerprint_from_obs(gone, BODY_ORDER, WORLD)) == float("inf")


# ====================================================================== #
# 2b. fingerprint_from_obs — the 3D layout (z, vz, quaternion) round-trips.
# ====================================================================== #
BODY3 = ["craft"]
CP3 = ["cp"]
OBS_DIM_3D = len(BODY3) * PER_BODY_3D + EGO_BLOCK_3D + len(CP3) + 1


def _obs3(x=0.0, y=0.0, z=0.0, vx=0.0, vy=0.0, vz=0.0, angle=0.0, latched=False,
          tick=0, horizon=120):
    obs_state = {"craft": {"pos": (x, y, z), "vel": (vx, vy, vz), "angle": angle,
                           "controlled": True}}
    latched_map = {"cp": (tick if latched else None)}
    return build_obs_vector(obs_state, latched_map, BODY3, CP3, WORLD, tick, horizon,
                            dim=3)


def test_fingerprint_from_obs_3d_freeze_is_zero_across_ticks():
    # SAME 3D body state, later tick (the tick channel differs) -> frozen (delta 0).
    o0 = _obs3(80.0, 60.0, 40.0, angle=0.5, tick=0)
    o0b = _obs3(80.0, 60.0, 40.0, angle=0.5, tick=9)
    f0 = ss.fingerprint_from_obs(o0, BODY3, WORLD, dim=3)
    f0b = ss.fingerprint_from_obs(o0b, BODY3, WORLD, dim=3)
    assert fp_delta(f0, f0b) < ss.EFFICACY_EPS


def test_fingerprint_from_obs_3d_detects_depth_and_rotation():
    # The 2D digest DROPS z/roll; the 3D fingerprint must catch pure z-motion AND a
    # pure orientation change — the whole point of the 3D obs.
    base = ss.fingerprint_from_obs(_obs3(80.0, 60.0, 40.0, angle=0.5), BODY3, WORLD, dim=3)
    moved_z = ss.fingerprint_from_obs(_obs3(80.0, 60.0, 60.0, angle=0.5), BODY3, WORLD, dim=3)
    rotated = ss.fingerprint_from_obs(_obs3(80.0, 60.0, 40.0, angle=1.3), BODY3, WORLD, dim=3)
    assert fp_delta(base, moved_z) > ss.EFFICACY_EPS     # 20 units of depth motion
    assert fp_delta(base, rotated) > ss.EFFICACY_EPS     # yaw 0.5 -> 1.3


def test_fingerprint_from_obs_3d_absent_body_is_a_topology_change():
    present = ss.fingerprint_from_obs(_obs3(80.0, 60.0, 40.0), BODY3, WORLD, dim=3)
    gone = np.zeros(OBS_DIM_3D, dtype=np.float32)        # present-bit 0 -> body omitted
    assert fp_delta(present, ss.fingerprint_from_obs(gone, BODY3, WORLD, dim=3)) == float("inf")


# ====================================================================== #
# 3. Single-env wrapper — episode logic over a scripted STUB env.
# ====================================================================== #
class _StubEnv:
    """A minimal serve-env stand-in driven by a scripted trajectory of
    ``(x, latched, terminated, success)`` per step. No Godot, no sockets."""

    def __init__(self, script, horizon=60):
        from gymnasium.spaces import Box, Discrete
        self.script = list(script)
        self.actions = ["a", "b"]
        self.horizon = horizon
        self._body_order = list(BODY_ORDER)
        self.world_size = WORLD
        self.observation_space = Box(-10.0, 10.0, (OBS_DIM,), dtype=np.float32)
        self.action_space = Discrete(2)
        self._t = 0

    def reset(self, seed=0):
        self._t = 0
        x0, *_ = self.script[0]
        return _obs(x0, tick=0, horizon=self.horizon), {"latched": {}, "n_latched": 0}

    def step(self, action_idx):
        self._t += 1
        x, latched, term, succ = self.script[min(self._t, len(self.script) - 1)]
        obs = _obs(x, latched=latched, tick=self._t, horizon=self.horizon)
        info = {"result": "success" if succ else ("budget"),
                "tick": self._t, "success": succ,
                "n_latched": 1 if latched else 0,
                "latched": {"cp": self._t if latched else None}}
        return obs, 0.0, bool(term), False, info


def test_single_env_wrapper_emits_candidate_and_ends_on_window():
    # Move for 6 ticks (mobility), then freeze at x=30 for a long run.
    script = [(0.0, False, False, False)]
    script += [(6.0 * k, False, False, False) for k in range(1, 6)]   # travel ~30px
    script += [(30.0, False, False, False) for _ in range(10)]        # freeze
    env = _StubEnv(script, horizon=60)
    p = _params(window=4, mobility_min=5.0)
    wrapped = ss.StaleSeekEnv(env, p, end_on_window=True)
    obs, _ = wrapped.reset(seed=0)
    ended, saw_candidate = False, False
    for _ in range(env.horizon):
        obs, r, term, trunc, info = wrapped.step(0)
        if "stale_candidate" in info:
            saw_candidate = True
        if term or trunc:
            ended = True
            break
    assert saw_candidate and ended
    assert len(wrapped.candidates) == 1
    cand = wrapped.candidates[0]
    # The prefix is the pre-freeze approach (no frozen ticks in it).
    assert cand["seed"] == 0 and len(cand["prefix"]) >= 1
    assert len(cand["prefix"]) < env.horizon


def test_single_env_wrapper_penalises_a_terminal_loss():
    script = [(0.0, False, False, False), (10.0, False, False, False),
              (10.0, False, True, False)]              # step 2 terminates (loss)
    wrapped = ss.StaleSeekEnv(_StubEnv(script), _params())
    wrapped.reset(seed=0)
    wrapped.step(0)
    _, r, term, _, _ = wrapped.step(0)
    assert term and r == wrapped.p.r_terminal
    assert wrapped.candidates == []


# ====================================================================== #
# 4. Batched VecEnv wrapper — candidate harvest during training.
# ====================================================================== #
def _make_stub_vecenv(script):
    from stable_baselines3.common.vec_env.base_vec_env import VecEnv
    from gymnasium import spaces

    class _StubVecEnv(VecEnv):
        def __init__(self):
            self.script = list(script)
            self._body_order = list(BODY_ORDER)
            self.world_size = WORLD
            self.actions = ["a", "b"]
            self._base_seed = 0
            self._t = 0
            super().__init__(1, spaces.Box(-10.0, 10.0, (OBS_DIM,), dtype=np.float32),
                             spaces.Discrete(2))

        def reset(self):
            self._t = 0
            x0, *_ = self.script[0]
            return _obs(x0, tick=0)[None, :].astype(np.float32)

        def step_async(self, actions):
            self._pending = actions

        def step_wait(self):
            self._t += 1
            i = min(self._t, len(self.script) - 1)
            x, latched, term, succ = self.script[i]
            done = bool(term) or self._t >= len(self.script) - 1
            frame = _obs(x, latched=latched, tick=self._t).astype(np.float32)
            info = {"result": "success" if succ else "budget", "tick": self._t,
                    "success": succ, "n_latched": 1 if latched else 0}
            if done:
                info["terminal_observation"] = frame.copy()
                info["TimeLimit.truncated"] = not term
            obs = frame[None, :]
            return obs, np.zeros(1, np.float32), np.array([done]), [info]

        def close(self):
            pass

        def env_is_wrapped(self, wrapper_class, indices=None):
            return [False]

        def get_attr(self, attr_name, indices=None):
            return [getattr(self, attr_name, None)]

        def set_attr(self, attr_name, value, indices=None):
            pass

        def env_method(self, *a, **k):
            raise NotImplementedError

    return _StubVecEnv()


def test_vec_wrapper_harvests_a_candidate_into_the_sink():
    script = [(0.0, False, False, False)]
    script += [(6.0 * k, False, False, False) for k in range(1, 6)]   # travel
    script += [(30.0, False, False, False) for _ in range(10)]        # freeze -> window
    venv = _make_stub_vecenv(script)
    sink: list = []
    wrapped = ss.make_stale_seek_vec_wrapper(venv, _params(window=4, mobility_min=5.0),
                                             base_seed=0, candidates=sink)
    wrapped.reset()
    for _ in range(len(script)):
        wrapped.step_async(np.zeros(1, dtype=int))
        _, rewards, dones, _ = wrapped.step_wait()
        if dones[0]:
            break
    assert len(sink) >= 1
    assert sink[0]["seed"] == 0 and len(sink[0]["prefix"]) >= 1


# ====================================================================== #
# 5. Escapability probe + CONFIRM over the real oracle (PyExecutor, no Godot).
# ====================================================================== #
ACTIONS = ["run", "leap"]
TRAPPED_PREFIX = ["run", "run", "run", "run"]          # falls into the pit (pos 3) forever
ESCAPABLE_PREFIX = ["run", "run", "leap"] + ["run"] * 5  # one step from the win (pos 11)


def _pyexec():
    return PyExecutor(world_factory=factory())


def test_escapability_probe_drops_a_near_win_but_keeps_the_trap():
    ex = _pyexec()
    trap = ss.escapability_probe(ex, SOFTLOCK, ACTIONS, TRAPPED_PREFIX, seed=0, k=6, trials=4)
    assert trap["escapable"] is False                  # the pit can never win
    esc = ss.escapability_probe(ex, SOFTLOCK, ACTIONS, ESCAPABLE_PREFIX, seed=0, k=6, trials=6)
    assert esc["escapable"] is True                    # a random tail wins from pos 11


def test_confirm_certifies_a_trapped_candidate_via_the_real_oracle():
    ex = _pyexec()
    res = ss.confirm_candidates(ex, SOFTLOCK, ACTIONS,
                                [{"seed": 0, "prefix": TRAPPED_PREFIX}],
                                H=30, budget=2500, engine="py")
    assert res["certified"] == 1
    f = res["findings"][0]
    assert f["outcome"] == "softlock" and f["hard"] is True
    assert f["tier"] == "seeker"
    assert f["reproducer"]["action_plan"]["sequence"] == TRAPPED_PREFIX
    assert f["reproducer"]["provenance"]["discovered_by"] == "trained_ppo_seeker"


def test_confirm_probes_out_an_escapable_candidate_before_the_oracle():
    ex = _pyexec()
    res = ss.confirm_candidates(ex, SOFTLOCK, ACTIONS,
                                [{"seed": 0, "prefix": ESCAPABLE_PREFIX}],
                                H=30, budget=2500, engine="py", probe=True)
    assert res["certified"] == 0 and res["probed_out"] == 1
    assert res["findings"] == []


def test_confirm_dedups_and_ignores_empty_prefixes():
    ex = _pyexec()
    res = ss.confirm_candidates(
        ex, SOFTLOCK, ACTIONS,
        [{"seed": 0, "prefix": TRAPPED_PREFIX},
         {"seed": 0, "prefix": list(TRAPPED_PREFIX)},   # duplicate
         {"seed": 0, "prefix": []}],                    # not a claim
        H=30, budget=2500, engine="py")
    assert res["candidates_unique"] == 1 and res["certified"] == 1


# ====================================================================== #
# 6. Ladder GATE — the deep tier fires only on the flag AND cheap-empty.
# ====================================================================== #
class _SeekerSpy:
    def __init__(self, findings):
        self.findings = findings
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        return {"findings": list(self.findings), "certified": len(self.findings),
                "refuted": 0, "probed_out": 0, "considered": len(self.findings),
                "candidates_in": 1, "candidates_unique": 1}


def _seeker_finding():
    return {"outcome": "softlock", "tier": "seeker", "family": "tree_refute",
            "hard": True, "detail": "d",
            "reproducer": {"engine": "gdscript", "seed": 0,
                           "action_plan": {"kind": "sequence", "sequence": ["run"]}},
            "evidence": {}}


def test_deep_tier_skipped_when_not_requested():
    block = g4._run_seeker(SOFTLOCK, "gdscript", ["run"], {}, game_path="x.gd",
                           requested=False, cheap_findings=[], seed=0, budget=1,
                           num_envs=1, seeds=(0,), waypoints=(0,), top_m=1,
                           stale_H=30, stale_budget=2500)
    assert block["status"] == "skipped_not_requested" and block["findings"] == []


def test_deep_tier_skipped_when_a_cheap_tier_already_certified(monkeypatch):
    spy = _SeekerSpy([_seeker_finding()])
    monkeypatch.setattr(g4, "_seeker_discover_and_confirm", spy)
    cheap = [{"outcome": "softlock", "hard": True}]     # a cheap-tier softlock already found
    block = g4._run_seeker(SOFTLOCK, "gdscript", ["run"], {}, game_path="x.gd",
                           requested=True, cheap_findings=cheap, seed=0, budget=1,
                           num_envs=1, seeds=(0,), waypoints=(0,), top_m=1,
                           stale_H=30, stale_budget=2500)
    assert block["status"] == "skipped" and spy.calls == 0   # no wasted PPO training


def test_deep_tier_skipped_off_gdscript_lane(monkeypatch):
    spy = _SeekerSpy([])
    monkeypatch.setattr(g4, "_seeker_discover_and_confirm", spy)
    block = g4._run_seeker(SOFTLOCK, "py", ["run"], {}, game_path="x.py",
                           requested=True, cheap_findings=[], seed=0, budget=1,
                           num_envs=1, seeds=(0,), waypoints=(0,), top_m=1,
                           stale_H=30, stale_budget=2500)
    assert block["status"] == "skipped" and spy.calls == 0


def test_deep_tier_runs_and_reports_findings_when_armed_and_cheap_empty(monkeypatch):
    spy = _SeekerSpy([_seeker_finding()])
    monkeypatch.setattr(g4, "_seeker_discover_and_confirm", spy)
    block = g4._run_seeker(SOFTLOCK, "gdscript", ["run"], {}, game_path="x.gd",
                           requested=True, cheap_findings=[], seed=0, budget=1,
                           num_envs=1, seeds=(0,), waypoints=(0,), top_m=1,
                           stale_H=30, stale_budget=2500)
    assert spy.calls == 1 and block["status"] == "run"
    assert block["certified"] == 1 and block["findings"][0]["outcome"] == "softlock"
    assert block["passed"] is False


def test_run_g4_deep_implies_stale_and_wires_seeker_block(monkeypatch):
    # deep=True must run the cheap stale gate AND expose a seeker block; the seeker
    # seam is stubbed so no Godot is needed. On this py game the lane guard skips it.
    spy = _SeekerSpy([])
    monkeypatch.setattr(g4, "_seeker_discover_and_confirm", spy)
    out = g4.run_g4(SOFTLOCK, _mini_report(), engine="py", world_factory=factory(),
                    tiers=(0,), deep=True, fuzz_random=15, fuzz_long=5, noop_heavy=4,
                    alt_periods=(1, 2), stale_H=20, stale_budget=1500,
                    stale_cand_budget=800, top_m=4)
    assert "seeker" in out and out["stale"]["status"] == "run"     # deep implied stale
    assert out["seeker"]["status"] == "skipped"                    # py lane, not gdscript


def _mini_report():
    return {"witness": {"actions": ["run", "run", "leap"] + ["run"] * 6, "ticks": 9,
                        "checkpoints": {"lip": 2, "crossed": 3}},
            "layers": {"G1_rollout": {"checks": {"efficacy": {"effect":
                       {"run": 1.0, "leap": 1.0}}}}}}
