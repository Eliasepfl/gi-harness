"""Unit tests for witness-warmstart (Backplay reverse curriculum, ``harness.rl.warmstart``).

Pure Python: a duck-typed deterministic fake env (mirrors tests/test_adversary.py) exercises
the prefix-replay + curriculum machinery with NO engine, NO torch, NO SB3 — so it runs in the
offline `reve` env. The in-engine wiring (GymPlanckEnv reset, sb3_trainer Dummy lane) gets an
in-image smoke; the byte-identity claim is pinned here structurally.

Pins the task-required properties:
  * prefix-replay DETERMINISM: same prefix + seed -> same start state;
  * the anneal STAIRCASE: rolling success > threshold steps the prefix down, cadence + log;
  * prefix 0 == VANILLA byte-identity: an empty/fully-annealed curriculum resets exactly
    like the underlying env.
"""

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.rl.warmstart import (  # noqa: E402
    WarmstartCurriculum,
    replay_prefix,
    warmstart_reset,
)


# ====================================================================== #
# Fake env — a deterministic integer ladder (duck-types the env contract)
# ====================================================================== #
class FakeLadderEnv:
    """Deterministic ladder: ``up`` adds 1, ``down`` subtracts 1, ``noop`` holds. Obs is the
    position; goal at ``goal`` terminates. Reset seeds the position deterministically
    (``seed % 3`` so different seeds give distinguishable-but-fixed starts). Duck-types the
    env contract warmstart replays through (``actions``/``reset``/``step``)."""

    actions = ["up", "down", "noop"]

    def __init__(self, *, goal: int = 100):
        self._goal = goal
        self.reset(0)

    def reset(self, seed: int = 0):
        self._x = int(seed) % 3
        self._steps = 0
        return [float(self._x)], {"latched": {}, "n_latched": 0, "result": None}

    def step(self, idx: int):
        a = self.actions[int(idx)]
        if a == "up":
            self._x += 1
        elif a == "down":
            self._x -= 1
        self._steps += 1
        term = self._x >= self._goal
        info = {"latched": {}, "n_latched": 0,
                "result": "success" if term else None, "tick": self._steps}
        return [float(self._x)], (1.0 if term else 0.0), term, False, info


class FakeServeLadderEnv(FakeLadderEnv):
    """FakeLadderEnv + a ``serve_replay`` fast path (duck-types GodotServeEnv's one-round-trip
    bulk replay): applies the whole prefix in one call, landing in the SAME state as N step()
    calls, and returns None (having done nothing) when no leading action is valid — the
    all-or-nothing contract :func:`replay_prefix` relies on to fall back cleanly."""

    def __init__(self, *, goal: int = 100):
        super().__init__(goal=goal)
        self.serve_replay_calls = 0

    def serve_replay(self, names):
        wires = []
        for a in names:
            if a in self.actions:
                wires.append(a)
            else:
                break
        if not wires:
            return None
        self.serve_replay_calls += 1
        obs = info = None
        term = False
        for a in wires:
            obs, _r, t, tr, info = self.step(self.actions.index(a))
            if t or tr:
                term = True
                break
        return obs, info, term


# ====================================================================== #
# 0) Fast-path serve_replay parity + fallback
# ====================================================================== #
def test_replay_prefix_prefers_serve_replay_and_matches_perstep():
    prefix = ["up", "up", "noop", "up", "down", "up"]
    fast = FakeServeLadderEnv()
    obs_fast, _info, term_fast = replay_prefix(fast, prefix, seed=7)
    assert fast.serve_replay_calls == 1                 # the fast path was taken
    obs_gen, _i2, term_gen = replay_prefix(FakeLadderEnv(), prefix, seed=7)
    assert obs_fast == obs_gen                          # byte-identical end state to per-step
    assert term_fast is False and term_gen is False


def test_serve_replay_none_falls_back_to_generic():
    fast = FakeServeLadderEnv()
    # First action not in vocab -> serve_replay returns None (sends nothing) -> generic path,
    # which breaks at the unknown action and yields the post-reset state.
    obs, _info, term = replay_prefix(fast, ["NOPE", "up"], seed=0)
    assert fast.serve_replay_calls == 0
    assert obs == [0.0] and term is False


# ====================================================================== #
# 1) Prefix-replay determinism
# ====================================================================== #
def test_replay_prefix_is_deterministic():
    prefix = ["up", "up", "noop", "up", "down", "up"]
    env = FakeLadderEnv()
    obs_a, info_a, term_a = replay_prefix(env, prefix, seed=7)
    obs_b, info_b, term_b = replay_prefix(env, prefix, seed=7)
    assert obs_a == obs_b                       # same prefix + seed -> same start state
    assert term_a is False and term_b is False
    # And the landed state is exactly the hand-computed one: start = 7%3 = 1, net +3 -> 4.
    assert obs_a == [4.0]


def test_replay_prefix_hands_off_after_prefix():
    # The state after replay is where the learner takes control; a longer prefix lands deeper.
    env = FakeLadderEnv()
    short, _, _ = replay_prefix(env, ["up"] * 3, seed=0)
    long, _, _ = replay_prefix(env, ["up"] * 8, seed=0)
    assert short == [3.0] and long == [8.0]


def test_replay_prefix_unknown_action_stops_early():
    env = FakeLadderEnv()
    obs, _, term = replay_prefix(env, ["up", "up", "NOT_AN_ACTION", "up"], seed=0)
    assert obs == [2.0]                          # stopped at the unknown action
    assert term is False


# ====================================================================== #
# 2) Curriculum sampling geometry
# ====================================================================== #
def test_sample_prefix_len_within_frontier_band_and_capped():
    witness = ["up"] * 20
    cur = WarmstartCurriculum(witness, start_frac=0.9, band_frac=0.1)
    rng = random.Random(0)
    cap = cur.cap_len()
    lo = cur.band_lo()
    assert cap == 18 and lo == 16            # round(0.9*20)=18 capped to L-1=19 -> 18; lo=floor(0.8*20)=16
    for _ in range(200):
        n = cur.sample_prefix_len(rng)
        assert lo <= n <= cap
    # Cap is NEVER the full witness -> replay can't reach the winning terminal.
    assert cur.cap_len() <= len(witness) - 1


def test_frac_zero_always_draws_empty_prefix():
    cur = WarmstartCurriculum(["up"] * 10, start_frac=0.9)
    cur.frac = 0.0
    rng = random.Random(1)
    assert cur.cap_len() == 0
    assert all(cur.sample_prefix_len(rng) == 0 for _ in range(50))
    assert cur.sample_prefix(rng) == []


# ====================================================================== #
# 3) Anneal staircase logic
# ====================================================================== #
def test_staircase_steps_down_on_high_rolling_success():
    cur = WarmstartCurriculum(["up"] * 10, start_frac=0.9, success_threshold=0.7,
                              step_frac=0.1, roll_window=10)
    # 9 successes: window not yet full -> no step.
    for _ in range(9):
        cur.record(success=True)
    assert cur.frac == 0.9 and cur._update_index == 0
    # 10th success fills the window (sr=1.0 > 0.7) -> exactly ONE step-down, buffer cleared so
    # the next step needs a fresh full window.
    cur.record(success=True)
    assert round(cur.frac, 6) == 0.8 and cur._update_index == 1
    assert len(cur._roll) == 0                    # buffer cleared after the step-down
    # One more full all-success window -> another step.
    for _ in range(10):
        cur.record(success=True)
    assert round(cur.frac, 6) == 0.7 and cur._update_index == 2


def test_staircase_rolling_window_mixes_recent_history():
    # The window is a maxlen deque: after a rough patch, the step-down fires as soon as the
    # ROLLING rate crosses threshold (realistic reverse-curriculum behavior), not only on a
    # pristine all-success batch.
    cur = WarmstartCurriculum(["up"] * 10, start_frac=0.9, success_threshold=0.7,
                              step_frac=0.1, roll_window=10)
    for _ in range(10):
        cur.record(success=False)                 # window = 10 failures, sr=0.0
    assert cur.frac == 0.9
    # Successes evict failures; sr crosses 0.7 at the 8th success (window = 2F/8T) -> step.
    for _ in range(8):
        cur.record(success=True)
    assert round(cur.frac, 6) == 0.8 and cur._update_index == 1


def test_staircase_needs_full_window():
    cur = WarmstartCurriculum(["up"] * 10, start_frac=0.9, success_threshold=0.7,
                              roll_window=20)
    for _ in range(19):                           # window not yet full
        cur.record(success=True)
    assert cur.frac == 0.9
    cur.record(success=True)                      # 20th -> full window, sr=1.0 -> step
    assert round(cur.frac, 6) == 0.8


def test_staircase_floors_at_zero_and_logs_trajectory():
    cur = WarmstartCurriculum(["up"] * 10, start_frac=0.2, step_frac=0.1, roll_window=5)
    for _ in range(3):                            # 0.2 -> 0.1 -> 0.0 (then floored)
        for _ in range(5):
            cur.record(success=True)
    assert cur.frac == 0.0
    # Further success does not go negative.
    for _ in range(5):
        cur.record(success=True)
    assert cur.frac == 0.0
    # Trajectory logged an initial point + one per step-down (2 steps down from 0.2).
    fracs = [round(p["frac"], 6) for p in cur.trajectory]
    assert fracs[0] == 0.2 and fracs[-1] == 0.0
    assert cur.done is True                        # fully annealed -> full-game mastery target


# ====================================================================== #
# 4) Prefix 0 == vanilla byte-identity  (via warmstart_reset)
# ====================================================================== #
def test_warmstart_reset_prefix0_is_byte_identical_to_vanilla():
    # A curriculum annealed to frac 0 draws an empty prefix -> warmstart_reset must return
    # EXACTLY the underlying vanilla reset (obs identical; no engine steps consumed).
    cur = WarmstartCurriculum(["up"] * 10, start_frac=0.9)
    cur.frac = 0.0
    env_ws = FakeLadderEnv()
    obs_ws, info_ws = warmstart_reset(env_ws, cur, random.Random(3), seed=5)
    env_van = FakeLadderEnv()
    obs_van, info_van = env_van.reset(seed=5)
    assert obs_ws == obs_van                       # byte-identical start state
    assert env_ws._steps == 0                       # no prefix steps were replayed


def test_warmstart_reset_none_curriculum_is_vanilla():
    env = FakeLadderEnv()
    obs, info = warmstart_reset(env, None, None, seed=2)
    assert obs == [2.0 % 3] or obs == [float(2 % 3)]
    assert env._steps == 0


def test_warmstart_reset_records_prefix_len():
    cur = WarmstartCurriculum(["up"] * 20, start_frac=0.9, band_frac=0.0)  # band 0 -> len==cap
    env = FakeLadderEnv()
    obs, info = warmstart_reset(env, cur, random.Random(0), seed=0)
    assert info["warmstart_prefix_len"] == cur.cap_len() == 18
    assert obs == [18.0]                            # start 0, +18 ups


def test_summary_shape():
    cur = WarmstartCurriculum(["up"] * 34, start_frac=0.9)
    s = cur.summary()
    assert s["witness_len"] == 34 and s["start_frac"] == 0.9
    assert s["final_prefix_len"] == cur.cap_len()
    assert isinstance(s["trajectory"], list) and s["trajectory"]
