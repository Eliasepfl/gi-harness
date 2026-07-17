"""Witness-warmstart training — Backplay / reverse-curriculum episode starts.

THE MOAT ARM of the exploration bake-off. Vanilla PPO collapses to the greedy-0 policy
on the sparse-terminal games whose reward only pays out at a win it never stumbles into.
But every one of those games ships a CERTIFIED tree WITNESS (a winning action sequence).
Witness-warmstart turns that witness into a curriculum: each training episode STARTS from
a replayed PREFIX of the witness — dropped in near the win — so the agent only has to learn
the last few moves, sees the terminal payoff immediately, and bootstraps a value signal.
As the rolling success rate rises the prefix is ANNEALED toward zero (full game from the
true start), so by convergence the policy solves the whole game unaided.

Literature:
  * Resnick, Raileanu, Kapoor, Peysakhovich, Cho, Bruna (2018), "Backplay: 'Man muss
    immer umkehren'" — start episodes from states along a demonstration, annealing the
    start back toward the true initial state as the agent improves.
  * Florensa, Held, Wulfmeier, Zhang, Abbeel (2017), "Reverse Curriculum Generation for
    Reinforcement Learning" — sample start states from a frontier that expands outward
    from the goal as success on the current frontier is achieved.

REUSE, not reinvention. The prefix-replay machinery is the SAME idiom the G4 adversary
already uses (``harness.rl.adversary.rollout``'s Backplay seeding): reset the env to a
seed, replay a witness PREFIX through the SAME serve stepping (``env.step`` per action),
then hand control to the learner at ``handoff_tick = len(prefix)``. :func:`replay_prefix`
below is that loop, factored out so the training-env reset path can reuse it verbatim.

CLEAN + OPT-IN. Nothing here is wired on by default. A trainer attaches a
:class:`WarmstartCurriculum` (built from the game's witness) to the env; when NONE is
attached the reset path is byte-identical to vanilla (see :func:`warmstart_reset`, which
short-circuits to ``env.reset(seed)`` for an empty prefix). No monkeypatching: the env
accepts the curriculum as an injected dependency and calls this module.

Env contract (duck-typed, so a fake env unit-tests this with no engine — mirrors
``harness.rl.adversary``):
    ``.actions``            list[str]  the discrete action vocabulary (witness action
                                       names index into this)
    ``.reset(seed) -> (obs, info)``
    ``.step(idx)  -> (obs, r, term, trunc, info)``
"""

from __future__ import annotations

import os
from collections import deque

# --- Curriculum defaults ([eng.] = calibrated engineering choice) --------- #
START_FRAC = 0.9        # first curriculum level: prefix = 90% of the witness (start near win) [eng.]
SUCCESS_THRESHOLD = 0.7  # rolling success rate that triggers a staircase step-down [eng.]
STEP_FRAC = 0.1         # prefix fraction removed per staircase step (10%) [eng.]
ROLL_WINDOW = 100       # episodes over which rolling success is measured [eng.]
BAND_FRAC = 0.1         # per-slot jitter band below the level (frontier width, Florensa) [eng.]
MIN_FRAC = 0.0          # floor: prefix 0.0 == full-game-from-scratch (curriculum complete)


def replay_prefix(env, prefix, *, seed: int = 0):
    """Backplay seed: reset ``env`` to ``seed`` then REPLAY the action-name ``prefix``
    through the SAME serve stepping (``env.step`` per action), returning the state AFTER
    the last replayed action so the learner takes control from there. This is the exact
    idiom of :func:`harness.rl.adversary.rollout` (reset -> replay prefix -> hand off).

    Returns ``(obs, info, terminated)``. ``terminated`` is True iff a replayed step ended
    the episode (``term`` or ``trunc``) — which should NOT happen for a strict sub-witness
    prefix (length < full witness), so the caller treats it as a signal to fall back to a
    vanilla live start rather than hand the learner an already-done episode.

    Deterministic: for a fixed ``prefix`` and ``seed`` the replay lands in the SAME start
    state every time (the determinism the unit tests pin), because the stepping is
    deterministic under the latched seed and the prefix is replayed verbatim.

    FAST PATH: when the env exposes ``serve_replay`` (GodotServeEnv), the whole prefix is
    replayed in ONE serve round-trip instead of ``len(prefix)`` per-action round-trips —
    profiled 4.3x faster per reset on long prefixes, and byte-identical in end state (same
    in-engine multi-action ``act``). Fake envs / the JS PlanckEnv (no ``serve_replay``) use
    the generic per-``step`` loop below, so the unit tests and the vendored lane are
    unchanged."""
    obs, info = env.reset(seed)
    if not prefix:
        return obs, info, False
    fast = getattr(env, "serve_replay", None)
    if callable(fast) and os.environ.get("HARNESS_WARMSTART_BULK_REPLAY", "1") != "0":
        # HARNESS_WARMSTART_BULK_REPLAY=0 forces the generic per-step path (the A/B control
        # that isolates the one-round-trip fix's end-to-end sps effect; default on).
        res = fast(list(prefix))                  # ONE round-trip; None -> nothing valid, fall back
        if res is not None:
            return res
    actions = list(env.actions)
    terminated = False
    for a in prefix:
        try:
            idx = actions.index(a)
        except ValueError:
            break                                 # prefix action not in vocab -> stop early
        obs, _r, term, trunc, info = env.step(int(idx))
        if term or trunc:
            terminated = True
            break
    return obs, info, terminated


def warmstart_reset(env, curriculum, rng, seed: int = 0):
    """Reset ``env`` for one training episode under a warmstart ``curriculum``.

    Draw a prefix length for THIS env slot from the curriculum (each slot may draw a
    different length — a frontier band, see :meth:`WarmstartCurriculum.sample_prefix`),
    Backplay-replay it, and return ``(obs, info)`` with ``info['warmstart_prefix_len']``
    recording where the learner took control.

    BYTE-IDENTITY GUARANTEE: an empty prefix (curriculum at ``frac == 0``, or no witness)
    short-circuits to a plain ``env.reset(seed)`` — the returned obs/info are exactly the
    vanilla reset's, so the fully-annealed curriculum is indistinguishable from vanilla
    PPO. An early terminal during replay (defensive; not expected for a sub-witness
    prefix) also falls back to a vanilla live start."""
    prefix = curriculum.sample_prefix(rng) if curriculum is not None else []
    if not prefix:
        obs, info = env.reset(seed)               # vanilla, byte-identical
        return obs, info
    obs, info, terminated = replay_prefix(env, prefix, seed=seed)
    if terminated:                                # defensive fallback -> live start
        obs, info = env.reset(seed)
        info = dict(info or {})
        info["warmstart_prefix_len"] = 0
        return obs, info
    info = dict(info or {})
    info["warmstart_prefix_len"] = len(prefix)
    return obs, info


class WarmstartCurriculum:
    """The Backplay reverse curriculum: a single staircase ``frac`` (fraction of the
    witness replayed as the episode's start prefix) that steps DOWN as the rolling
    success rate rises, from ``start_frac`` (near the win) to ``0.0`` (full game).

    Per-slot diversity (Florensa frontier). On each reset an env slot draws a prefix
    length from a narrow band just BELOW the current level — ``[floor((frac-band)*L),
    floor(frac*L)]`` clamped to ``[0, L-1]`` — so a training batch covers a small frontier
    of nearby start states (which the value function generalizes across) rather than a
    single point. The band collapses to ``{0}`` once ``frac`` reaches 0.

    The prefix is capped at ``L-1`` (never the full witness), so a replay can never itself
    reach the winning terminal — the learner always gets at least one live action.

    Staircase (documented rule). Feed each finished episode's outcome to :meth:`record`.
    Over a rolling window of ``roll_window`` episodes, when the success rate exceeds
    ``success_threshold`` the level steps down by ``step_frac`` and the rolling buffer is
    cleared (so the next step-down is judged on fresh, harder episodes). Every step-down
    appends a point to :attr:`trajectory` — the curriculum trajectory the bake-off logs.

    Deterministic under the ``rng`` handed to :meth:`sample_prefix`; the staircase is a
    pure function of the outcome sequence fed to :meth:`record`."""

    def __init__(self, witness_actions, *, start_frac: float = START_FRAC,
                 success_threshold: float = SUCCESS_THRESHOLD, step_frac: float = STEP_FRAC,
                 roll_window: int = ROLL_WINDOW, band_frac: float = BAND_FRAC,
                 min_frac: float = MIN_FRAC):
        self.witness_actions = list(witness_actions or [])
        self._L = len(self.witness_actions)
        self.start_frac = float(start_frac)
        self.frac = float(start_frac)
        self.success_threshold = float(success_threshold)
        self.step_frac = float(step_frac)
        self.roll_window = int(roll_window)
        self.band_frac = float(band_frac)
        self.min_frac = float(min_frac)
        self._roll: deque = deque(maxlen=self.roll_window)
        self._update_index = 0
        self._episodes = 0
        # trajectory[0] is the initial level; each step-down appends a point.
        self.trajectory: list[dict] = [self._point(rolling_sr=None)]

    # -- level geometry ---------------------------------------------------- #
    def cap_len(self) -> int:
        """Integer prefix cap at the current level: ``round(frac*L)`` clamped to
        ``[0, L-1]`` (never the full witness)."""
        if self._L == 0:
            return 0
        cap = int(round(self.frac * self._L))
        return max(0, min(self._L - 1, cap))

    def band_lo(self) -> int:
        """Lower edge of the per-slot frontier band: ``floor((frac-band_frac)*L)``,
        clamped to ``[0, cap]``."""
        cap = self.cap_len()
        if self._L == 0:
            return 0
        lo = int((self.frac - self.band_frac) * self._L)   # floor via int() on >=0
        return max(0, min(cap, lo))

    def sample_prefix_len(self, rng) -> int:
        """Draw a prefix LENGTH for one env slot: uniform in the integer frontier band
        ``[band_lo(), cap_len()]`` (inclusive). At ``frac == 0`` the band is ``{0}`` so the
        slot always draws 0 (vanilla). ``rng`` is a ``random.Random`` (seeded by the
        caller for reproducibility)."""
        cap = self.cap_len()
        if cap <= 0:
            return 0
        lo = self.band_lo()
        return int(rng.randint(lo, cap))          # inclusive both ends

    def sample_prefix(self, rng) -> list:
        """The witness prefix (list of action names) for one env slot's reset."""
        n = self.sample_prefix_len(rng)
        return list(self.witness_actions[:n])

    # -- staircase --------------------------------------------------------- #
    def record(self, success: bool) -> None:
        """Register one finished episode's outcome. When the rolling window is full and
        its success rate exceeds ``success_threshold``, step the level down."""
        self._episodes += 1
        self._roll.append(1 if success else 0)
        if (self.frac > self.min_frac
                and len(self._roll) >= self.roll_window
                and self.rolling_sr() > self.success_threshold):
            self._advance()

    def rolling_sr(self) -> float:
        return (sum(self._roll) / len(self._roll)) if self._roll else 0.0

    def _advance(self) -> None:
        sr = self.rolling_sr()
        self.frac = max(self.min_frac, round(self.frac - self.step_frac, 6))
        self._update_index += 1
        self._roll.clear()                        # judge the next step-down on fresh episodes
        self.trajectory.append(self._point(rolling_sr=sr))

    def _point(self, *, rolling_sr) -> dict:
        return {"update": self._update_index, "frac": round(self.frac, 6),
                "cap_len": self.cap_len(), "episodes": self._episodes,
                "rolling_sr": (round(rolling_sr, 4) if rolling_sr is not None else None)}

    # -- reporting --------------------------------------------------------- #
    @property
    def current_prefix_len(self) -> int:
        return self.cap_len()

    @property
    def done(self) -> bool:
        """True once the curriculum has fully annealed (prefix 0 -> full-game mastery)."""
        return self.cap_len() == 0

    def summary(self) -> dict:
        return {"witness_len": self._L, "start_frac": self.start_frac,
                "final_frac": round(self.frac, 6), "final_prefix_len": self.cap_len(),
                "n_steps": self._update_index, "episodes": self._episodes,
                "trajectory": list(self.trajectory)}


def build_warmstart_callback(curriculum, *, log=None):
    """Build the opt-in SB3 callback that feeds finished-episode outcomes to ``curriculum``
    so its staircase can anneal (``stable_baselines3`` imported lazily so this module loads
    for the pure-Python curriculum tests without SB3). Added to the trainer's callback list
    ONLY when warmstart is enabled — vanilla runs never construct it.

    On every SB3 step it reads ``infos`` for episode completions (the ``episode`` dict the
    Monitor stamps on done, carrying ``success``) and calls :meth:`WarmstartCurriculum.record`
    once per finished episode. The env slots draw the prefix; this callback drives the
    step-down — a clean split with no monkeypatching."""
    from stable_baselines3.common.callbacks import BaseCallback

    class _WarmstartCallback(BaseCallback):
        def __init__(self):
            super().__init__()
            self.curriculum = curriculum
            self._log = log
            self._last_frac = curriculum.frac

        def _on_step(self) -> bool:
            for info in self.locals.get("infos", []) or []:
                if not info:
                    continue
                ep = info.get("episode")
                if ep is None:
                    continue
                success = bool(ep.get("success", info.get("success", False)))
                self.curriculum.record(success)
            if self._log is not None and self.curriculum.frac != self._last_frac:
                self._last_frac = self.curriculum.frac
                self._log(f"[warmstart] prefix stepped down -> frac={self.curriculum.frac:.3f} "
                          f"cap_len={self.curriculum.cap_len()} "
                          f"episodes={self.curriculum._episodes}")
            return True

    return _WarmstartCallback()
