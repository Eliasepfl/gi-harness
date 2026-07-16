"""Unit tests for the S1.5 POLICY-GUIDED DESCENT tier (harness.rl.adversary) — Elias's
return-then-descend idea (STALE_SEEKING_PLAN.md §3.1). Pure Python: duck-typed fake envs
+ scripted critics exercise the alpha-ramp chooser, low-V waypoint selection, prefix-
replay determinism, and the multi-step advantage (descent enters a pocket the greedy
argmin-from-0 attacker cannot compose) with NO engine and NO torch.

The real gdscript SEARCH->DETECT->CONFIRM end-to-end (softlock_maze.gd) is in
tests/test_gd_descent.py; the g4 wiring/grading is tests/test_g4_descent.py.
"""

from __future__ import annotations

import os
import random
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.rl import adversary  # noqa: E402
from harness.rl.adversary import (  # noqa: E402
    ab_bench,
    collect_low_v_states,
    descent_chooser,
    descent_search,
    detect_softlock_window,
    linear_alpha_schedule,
    random_chooser,
    rollout,
    search,
    select_waypoints,
)


# ====================================================================== #
# A 2D maze with a POCKET reachable ONLY by a multi-step TURN.
# From the pocket-approach zone the competent policy goes UP into the (hidden-trap)
# pocket; the anti-policy argmin from the START walks the LEFT edge and never composes
# the turn -> greedy-from-0 misses it, policy-guided descent finds it.
# ====================================================================== #
class FakeMazeEnv:
    """Entering the pocket (x in [3,8], y in [4,7]) PINS the pose to (5,5) forever
    (softlock — frozen while acting, never terminal, goal unreachable). Leaving the
    arena (|x|,|y| out of bounds) is a terminal LOSS. Duck-types the adversary env
    contract (actions/horizon/reset/step/last_snapshot)."""

    actions = ["right", "up", "left", "down"]

    def __init__(self, *, horizon=60, ymax=12, xmax=20):
        self.horizon = horizon
        self._ymax = ymax
        self._xmax = xmax
        self.reset(0)

    @staticmethod
    def _in_pocket(x, y):
        return 3.0 <= x <= 8.0 and 4.0 <= y <= 7.0

    def _snap(self):
        return {"body": {"pos": [float(self._x), float(self._y)],
                         "vel": [0.0, 0.0], "angle": 0.0}}

    def reset(self, seed=0):
        self._x = 0
        self._y = 0
        self._trapped = False
        self.last_snapshot = self._snap()
        return [float(self._x), float(self._y)], {"latched": {}, "n_latched": 0}

    def step(self, idx):
        a = self.actions[int(idx)]
        term = False
        if not self._trapped:
            if a == "right":
                self._x += 1
            elif a == "left":
                self._x -= 1
            elif a == "up":
                self._y += 1
            elif a == "down":
                self._y -= 1
            if self._in_pocket(self._x, self._y):
                self._trapped = True
                self._x, self._y = 5, 5           # PIN -> frozen from here on
            elif not (-2 <= self._x <= self._xmax and -2 <= self._y <= self._ymax):
                term = True                         # left the arena -> LOSS
        self.last_snapshot = self._snap()
        info = {"latched": {}, "n_latched": 0,
                "result": "failure" if term else "budget"}
        return [float(self._x), float(self._y)], (-1.0 if term else 0.0), term, False, info


class ScriptedMazeCritic:
    """A competent-navigator stand-in for a trained G3' policy. In the pocket-approach
    zone (x in [3,8], y<4) the policy goes UP (a POINT MASS — into what it does not know
    is a trap); elsewhere it goes RIGHT toward the goal. V is LOW near the pocket anchor
    (5,5), so the low-V waypoint selection targets the approach. Soundness is critic-
    independent (DETECT+CONFIRM certify regardless); this only shapes the search."""

    source = "scripted_maze"

    @staticmethod
    def _xy(obs):
        o = np.asarray(obs, dtype=float).reshape(-1)
        return float(o[0]), float(o[1])

    def action_probs(self, obs):
        x, y = self._xy(obs)
        if 3.0 <= x <= 8.0 and y < 4.0:               # approach zone -> UP (point mass)
            return np.array([0.0, 1.0, 0.0, 0.0])     # argmin -> "right" (idx 0)
        return np.array([0.85, 0.05, 0.05, 0.05])     # elsewhere -> RIGHT; argmin -> "up"

    def value(self, obs):
        x, y = self._xy(obs)
        return float(((x - 5.0) ** 2 + (y - 5.0) ** 2) ** 0.5)   # low near the pocket


# The (safe) winning-witness stub: right to x=5, up to just BELOW the pocket mouth
# (5,3) — a competent route that skirts the trap. Handoff tick 8 lands at (5,3).
MAZE_WITNESS = ["right"] * 5 + ["up"] * 3


# ====================================================================== #
# 1. Descent chooser — alpha-ramp correctness (pi @ a=0, argmin @ a=1, monotone)
# ====================================================================== #
class PointMassCritic:
    """pi is a point mass on index 1; argmin(pi) is index 0. So 'sample pi' == 1 and
    'anti-policy' == 0 deterministically, isolating the alpha blend."""

    source = "point_mass"

    def action_probs(self, obs):
        return np.array([0.0, 1.0, 0.0, 0.0])

    def value(self, obs):
        return 0.0


ACTS4 = ["a", "b", "c", "d"]


def test_descent_chooser_alpha0_is_pure_policy():
    # alpha == 0 every tick -> NEVER the anti-policy -> always sample pi (index 1).
    choose = descent_chooser(PointMassCritic(), lambda t: 0.0)
    rng = random.Random(0)
    picks = [choose([0.0], ACTS4, rng) for _ in range(200)]
    assert set(picks) == {1}, "at alpha=0 the chooser is pure competent pi"


def test_descent_chooser_alpha1_is_pure_antipolicy():
    # alpha == 1 every tick -> always the anti-policy argmin(pi) (index 0).
    choose = descent_chooser(PointMassCritic(), lambda t: 1.0)
    rng = random.Random(0)
    picks = [choose([0.0], ACTS4, rng) for _ in range(200)]
    assert set(picks) == {0}, "at alpha=1 the chooser is pure anti-policy argmin"


def test_descent_chooser_blend_is_monotone_in_alpha():
    # As alpha rises the FRACTION of anti-policy (index-0) picks rises monotonically.
    # Fresh rng per alpha + a point-mass pi (which never samples index 0) means every
    # index-0 pick came from the alpha branch -> count ~ alpha * N.
    N = 4000
    fracs = []
    for a in (0.0, 0.25, 0.5, 0.75, 1.0):
        choose = descent_chooser(PointMassCritic(), lambda t, a=a: a)
        rng = random.Random(12345)
        picks = [choose([0.0], ACTS4, rng) for _ in range(N)]
        fracs.append(sum(1 for p in picks if p == 0) / N)
    assert fracs[0] == 0.0 and fracs[-1] == 1.0
    assert all(b >= a - 1e-9 for a, b in zip(fracs, fracs[1:])), fracs
    # Each interior fraction tracks its alpha (law of large numbers).
    for frac, a in zip(fracs, (0.0, 0.25, 0.5, 0.75, 1.0)):
        assert abs(frac - a) < 0.05, (frac, a)


def test_linear_alpha_schedule_ramps_0_to_1():
    sched = linear_alpha_schedule(10)
    assert sched(0) == 0.0
    assert sched(10) == 1.0
    assert sched(5) == 0.5
    assert sched(100) == 1.0                    # clamped
    # monotone non-decreasing
    vals = [sched(t) for t in range(0, 12)]
    assert all(b >= a for a, b in zip(vals, vals[1:]))


# ====================================================================== #
# 2. Waypoint low-V selection + prefixes that reach the low-V states
# ====================================================================== #
def test_select_waypoints_orders_by_low_v():
    env = FakeMazeEnv()
    wps = select_waypoints(env, ScriptedMazeCritic(), witness_actions=MAZE_WITNESS,
                           explore_seeds=(0,), eps=0.0, n_waypoints=6)
    assert wps, "waypoint pool must be non-empty"
    vals = [wp["value"] for wp in wps if wp["value"] is not None]
    assert vals == sorted(vals), "waypoints are ordered by V ascending (low-V first)"
    # The witness cut at (5,3) — just below the pocket — is a low-V pick and present.
    srcs = {wp["source"] for wp in wps}
    assert "witness" in srcs
    top = wps[0]
    assert top["value"] <= min(vals) + 1e-9      # the lowest-V waypoint leads


def test_collect_low_v_states_returns_topk_prefixes():
    env = FakeMazeEnv()
    # A straight witness replay records V per tick; the deepest (lowest-V) states win.
    roll = rollout(env, random_chooser(), seed=0, prefix=MAZE_WITNESS,
                   critic=ScriptedMazeCritic(), max_ticks=len(MAZE_WITNESS))
    low = collect_low_v_states(roll, k=3)
    assert len(low) == 3
    vs = [v for v, _ in low]
    assert vs == sorted(vs), "top-k are the lowest-V states, ascending"
    # Each returned prefix is a genuine action-cut of the rollout.
    for _v, pref in low:
        assert pref and pref == list(roll["actions"])[:len(pref)]


# ====================================================================== #
# 3. Return-phase prefix replay is bit-identical to the witness at that tick
# ====================================================================== #
def test_return_prefix_replay_bit_identical_to_witness():
    env = FakeMazeEnv()
    crit = ScriptedMazeCritic()
    full = rollout(env, random_chooser(), seed=0, prefix=MAZE_WITNESS, critic=crit,
                   max_ticks=len(MAZE_WITNESS))
    for t in (2, 4, 8):
        partial = rollout(env, random_chooser(), seed=0, prefix=MAZE_WITNESS[:t],
                          critic=crit, max_ticks=t)
        assert partial["handoff_tick"] == t
        # The RETURN (deterministic prefix replay) lands byte-identically where the
        # full witness rollout was at tick t — the soundness the plan relies on.
        assert partial["fps"][t] == full["fps"][t]
        assert partial["actions"] == list(MAZE_WITNESS[:t])


# ====================================================================== #
# 4. descent_search finds the pocket that greedy argmin-from-0 CANNOT compose
# ====================================================================== #
def test_descent_search_enters_multistep_pocket():
    env = FakeMazeEnv()
    res = descent_search(env, ScriptedMazeCritic(), witness_actions=MAZE_WITNESS,
                         explore_seeds=(0,), eps=0.0, window=6, descent_ticks=20,
                         max_ticks=60)
    assert res["source"] == "scripted_maze"
    assert res["candidates"], "descent must reach the frozen pocket"
    cand = res["candidates"][0]
    # Replaying the candidate prefix on a fresh env lands FROZEN in the pocket (5,5).
    fresh = FakeMazeEnv()
    roll = rollout(fresh, random_chooser(), seed=0, prefix=cand["prefix"],
                   critic=ScriptedMazeCritic(), max_ticks=len(cand["prefix"]) + 6)
    tail = roll["fps"][-4:]
    from harness.verify.gameverify import EFFICACY_EPS
    from harness.core.statetree import fp_delta
    assert all(fp_delta(tail[0], s) < EFFICACY_EPS for s in tail[1:]), \
        "the descent prefix must land the body in the frozen pocket"


def test_greedy_search_misses_the_multistep_pocket():
    # S1 (pure anti-policy argmin, incl. witness backplay + V-frontier reseed) CANNOT
    # compose the turn into the pocket on this fixture -> zero candidates. This is the
    # multi-step gap the descent tier closes (STALE_SEEKING_PLAN.md §3.1).
    env = FakeMazeEnv()
    res = search(env, ScriptedMazeCritic(), seeds=list(range(4)), eps=0.0, window=6,
                 witness_actions=MAZE_WITNESS, max_ticks=60)
    assert res["candidates"] == [], "greedy argmin-from-0 must not reach the pocket"


# ====================================================================== #
# 5. ab_bench descent arm — plumbing + the multi-step directional signal
# ====================================================================== #
def test_ab_bench_descent_arm_beats_greedy_on_multistep():
    res = ab_bench(lambda: FakeMazeEnv(), ScriptedMazeCritic(), budget_ticks=4000,
                   seeds=list(range(6)), window=6, witness_actions=MAZE_WITNESS,
                   max_ticks=60, descent=True, descent_ticks=20)
    for arm in ("inverse_value", "random", "descent"):
        blk = res[arm]
        assert {"detections", "candidates", "ticks_simulated", "per_1k",
                "rollouts"} <= set(blk)
    # On the MULTI-STEP fixture, descent (S1.5) reaches the pocket while the greedy
    # inverse-value arm (S1) cannot compose the entry.
    assert res["descent"]["candidates"] >= 1
    assert res["descent"]["candidates"] > res["inverse_value"]["candidates"]


def test_ab_bench_without_descent_omits_the_arm():
    res = ab_bench(lambda: FakeMazeEnv(), ScriptedMazeCritic(), budget_ticks=1000,
                   seeds=[0], window=6, witness_actions=MAZE_WITNESS, max_ticks=60)
    assert "descent" not in res


if __name__ == "__main__":       # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
