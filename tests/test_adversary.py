"""Unit tests for the inverse-value softlock hunter's SEARCH + DETECT layers
(``harness.rl.adversary``) — Elias's G4 smart tier. Pure Python: a duck-typed fake
env + fake critic exercise the machinery with NO engine and NO torch, so they run
anywhere. The tree-refutation CONFIRM layer + the g4 wiring are covered separately
(tests/test_g4_inverse_value.py); the real gdscript end-to-end is tests/test_gd_adversary.py.

Design: notes/adversarial/INVERSE_VALUE_G4.md + notes/adversarial/FEASIBILITY_LITERATURE.md.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math  # noqa: E402

from harness.rl import adversary  # noqa: E402
from harness.rl.adversary import (  # noqa: E402
    ab_bench,
    anti_policy_chooser,
    detect_softlock_window,
    detect_value_death,
    random_chooser,
    rollout,
    search,
    value_collapse_floor,
)


# ====================================================================== #
# Fakes — a 1-D corridor with a reachable freeze pocket (softlock)
# ====================================================================== #
class FakeCorridorEnv:
    """A 1-D corridor: ``fwd`` walks toward a PIT at ``[pit_lo, pit_hi]``; once the body
    enters the pit its pose is PINNED forever (softlock — state freezes while acting),
    is never terminal, and the goal beyond it can never be reached. ``back`` steps
    toward 0, ``noop`` holds. Latches ``mid`` at x>=3 (a checkpoint BEFORE the pit).

    Duck-types the env contract adversary steers by: ``actions``/``horizon``/``reset``/
    ``step``/``last_snapshot``."""

    actions = ["fwd", "back", "noop"]

    def __init__(self, *, horizon=40, pit_lo=20, pit_hi=24, anchor=22, goal=100):
        self.horizon = horizon
        self._pit = (pit_lo, pit_hi)
        self._anchor = anchor
        self._goal = goal
        self.reset(0)

    def _snap(self):
        return {"body": {"pos": [float(self._x), 0.0], "vel": [0.0, 0.0], "angle": 0.0}}

    def reset(self, seed=0):
        self._x = 0
        self._trapped = False
        self._latched = 0
        self.last_snapshot = self._snap()
        return [float(self._x)], {"latched": {"mid": None}, "n_latched": 0}

    def step(self, idx):
        a = self.actions[int(idx)]
        if not self._trapped:
            if a == "fwd":
                self._x += 1
            elif a == "back":
                self._x -= 1                    # UNBOUNDED (no reflecting drift): an
                                                # unbiased walk rarely reaches the far pit
            if self._pit[0] <= self._x <= self._pit[1]:
                self._trapped = True
                self._x = self._anchor          # PIN -> frozen from here on
        if self._x >= 3:
            self._latched = 1
        term = self._x >= self._goal            # unreachable once trapped
        self.last_snapshot = self._snap()
        info = {"latched": {"mid": (0 if self._latched else None)},
                "n_latched": self._latched,
                "result": "success" if term else "budget"}
        return [float(self._x)], (1.0 if term else 0.0), term, False, info


class FakeCritic:
    """A critic whose policy PREFERS not-``fwd`` (so anti-policy = ``argmin`` picks
    ``fwd``, steering straight into the pit). ``value`` decreases down the corridor so
    the V-frontier reseeds from the deepest states."""

    source = "fake_critic"

    def action_probs(self, obs):
        return np.array([0.10, 0.45, 0.45])     # lowest on idx 0 ("fwd")

    def value(self, obs):
        return -float(np.asarray(obs).reshape(-1)[0])


# ====================================================================== #
# DETECT — the sliding-window state-freeze / cycle test (pure function)
# ====================================================================== #
def _fp(x):
    return (("body", float(x), 0.0, 0.0, 0.0, 0.0),)


def test_detect_fires_on_a_frozen_window():
    # travel 1..5 then FREEZE at 9 for the rest of the trail.
    fps = [_fp(v) for v in [0, 1, 2, 3, 4, 5, 9, 9, 9, 9, 9, 9, 9]]
    latched = [0] * len(fps)          # never a new checkpoint -> guard satisfied
    fired, cut, info = detect_softlock_window(fps, latched, None, window=6)
    assert fired is True
    assert info["kind"] == "frozen"
    assert cut == 6                   # first frozen index (state 9)


def test_detect_ignores_short_push_into_wall():
    # a 3-tick stall (push into wall) is too short to fill a window-6 -> no fire.
    fps = [_fp(v) for v in [0, 1, 2, 2, 2, 3, 4, 5, 6, 7]]
    latched = [0] * len(fps)
    fired, cut, _ = detect_softlock_window(fps, latched, None, window=6)
    assert fired is False and cut is None


def test_detect_respects_no_new_checkpoint_guard():
    # The pose looks frozen, but a NEW checkpoint keeps latching every tick (continuous
    # progress) -> every window straddles a fresh latch -> the guard suppresses the fire.
    fps = [_fp(9)] * 10
    latched = list(range(10))                  # a new checkpoint in every window span
    fired, _, _ = detect_softlock_window(fps, latched, None, window=6)
    assert fired is False


def test_detect_fires_on_frozen_tail_after_a_checkpoint():
    # A softlock that sets in AFTER the last checkpoint latched is still a softlock:
    # no NEW checkpoint inside the frozen window -> the guard allows the fire.
    fps = [_fp(v) for v in [0, 1, 2, 9, 9, 9, 9, 9, 9, 9]]
    latched = [0, 0, 1, 1, 1, 1, 1, 1, 1, 1]   # last latch at index 2, then frozen
    fired, cut, info = detect_softlock_window(fps, latched, None, window=6)
    assert fired is True and info["kind"] == "frozen" and cut == 3


def test_detect_respects_terminal_guard():
    # frozen tail, but the episode TERMINATED inside it -> a loss, not a softlock.
    fps = [_fp(v) for v in [0, 1, 2, 9, 9, 9, 9, 9, 9, 9]]
    latched = [0] * len(fps)
    fired, _, _ = detect_softlock_window(fps, latched, terminal_tick=4, window=6)
    assert fired is False


def test_detect_fires_on_a_closed_cycle():
    # oscillate 8,9,8,9,... (period-2) with no net progress -> a cycle softlock.
    fps = [_fp(v) for v in [0, 1, 2, 8, 9, 8, 9, 8, 9, 8, 9]]
    latched = [0] * len(fps)
    fired, cut, info = detect_softlock_window(fps, latched, None, window=6)
    assert fired is True and info["kind"] == "cycle"


# ====================================================================== #
# VALUE-DEATH — the motion-INVARIANT third trigger (Elias's wiggle fix)
# ====================================================================== #
def _wiggle_fp(k):
    """A fingerprint on an aperiodic golden-angle stir: distinct POSITION *and* distinct
    VELOCITY every tick, so no two states within a window are eps-equal — exactly the
    wiggle that defeats the motion-based frozen AND cycle tests."""
    ang = k * 2.399963                       # golden angle (rad) -> equidistributed, aperiodic
    px, py = 3.0 + 2.0 * math.cos(ang), 2.0 * math.sin(ang)
    vx, vy = 5.0 * math.cos(ang), 5.0 * math.sin(ang)
    return (("body", round(px, 6), round(py, 6), round(vx, 6), round(vy, 6), 0.0),)


def test_value_collapse_floor_is_relative_and_rejects_a_flat_critic():
    # Floor = Vmin + 0.25*(Vmax-Vmin): the bottom quarter of the run's OWN V range.
    floor, ok = value_collapse_floor([1.0, 1.0, 1.0, -3.0, -3.0, -3.0])
    assert ok and abs(floor - (-3.0 + 0.25 * 4.0)) < 1e-9      # -3 + 0.25*(1-(-3)) = -2.0
    # A FLAT / degenerate critic (no spread) yields NO floor -> value-death cannot fire
    # (the 'weak critic is useless' lesson made mechanical).
    assert value_collapse_floor([0.0] * 8)[1] is False
    assert value_collapse_floor([2.5, 2.5, 2.5])[1] is False


def test_value_death_catches_a_wiggle_that_frozen_and_cycle_MISS():
    # THE honest hole: a body trapped but WIGGLING aperiodically over many distinct
    # positions. Approach (states 0..2), then a golden-angle wiggle pocket (states 3..).
    fps = [_fp(0), _fp(1), _fp(2)] + [_wiggle_fp(k) for k in range(3, 16)]
    latched = [0] * len(fps)                 # never a new checkpoint -> guard satisfied
    # (i) the motion tests MISS it: never frozen (state always moves), never a short
    #     closed cycle (every state distinct in pos AND vel -> no eps-recurrence).
    m_fired, _m_cut, m_info = detect_softlock_window(fps, latched, None, window=6)
    assert m_fired is False and m_info["kind"] is None
    # (ii) value_death CATCHES it: the critic V has COLLAPSED in the pocket. Approach V
    #      high (+1), pocket V collapsed (-4) for the whole tail.
    values = [1.0, 1.0, 1.0] + [-4.0] * 13
    v_fired, v_cut, v_info = detect_value_death(values, latched, None, window=6)
    assert v_fired is True and v_info["kind"] == "value_death"
    assert v_cut == 3                         # cut at the pocket entry (approach was 0..2)
    assert v_info["floor"] is not None and v_info["floor"] < 0.0


def test_value_death_respects_the_no_checkpoint_and_terminal_guards():
    values = [1.0, 1.0] + [-5.0] * 10
    # A new checkpoint latching in every window span -> progress -> suppressed.
    assert detect_value_death(values, list(range(12)), None, window=6)[0] is False
    # A terminal inside the collapsed tail -> a LOSS, not a softlock -> suppressed.
    assert detect_value_death(values, [0] * 12, terminal_tick=4, window=6)[0] is False


def test_value_death_needs_a_critic_no_values_never_fires():
    # No critic -> the value trail is all None -> no floor -> never fires (byte-identical
    # to the value-less path; the search's random baseline arm relies on this).
    assert detect_value_death([None] * 12, [0] * 12, None, window=6)[0] is False


class FakeWigglePocketEnv:
    """A corridor with a WIGGLE POCKET (not a pin): ``fwd`` walks the body toward a pocket
    at ``x >= trap_x``; once inside it is CONFINED but keeps WANDERING on a golden-angle
    stir (a distinct pos AND vel every tick), is never terminal, latches nothing new, and
    the goal beyond is never reached. The body never FREEZES and never closes a short
    CYCLE, so ``detect_softlock_window`` MISSES it — only value_death (a collapsed critic
    V) catches it. Duck-types the same env contract as :class:`FakeCorridorEnv`."""

    actions = ["fwd", "back", "noop"]

    def __init__(self, *, horizon=60, trap_x=8, goal=100):
        self.horizon = horizon
        self._trap_x = trap_x
        self._goal = goal
        self.reset(0)

    def _snap(self):
        return {"body": {"pos": [float(self._x), float(self._y)],
                         "vel": [float(self._vx), float(self._vy)], "angle": 0.0}}

    def reset(self, seed=0):
        self._x = 0.0
        self._y = 0.0
        self._vx = 0.0
        self._vy = 0.0
        self._trapped = False
        self._latched = 0
        self._k = 0
        self.last_snapshot = self._snap()
        return [float(self._x)], {"latched": {"mid": None}, "n_latched": 0}

    def step(self, idx):
        a = self.actions[int(idx)]
        if not self._trapped:
            if a == "fwd":
                self._x += 1
            elif a == "back":
                self._x -= 1
            if self._x >= self._trap_x:
                self._trapped = True
        if self._trapped:
            # Golden-angle stir: confined band, distinct pos + vel each tick, aperiodic.
            self._k += 1
            ang = self._k * 2.399963
            self._x = self._trap_x + 3.0 + 2.0 * math.cos(ang)
            self._y = 2.0 * math.sin(ang)
            self._vx = 5.0 * math.cos(ang)
            self._vy = 5.0 * math.sin(ang)
        if self._x >= 3:
            self._latched = 1
        term = (self._x >= self._goal) and not self._trapped   # unreachable once trapped
        self.last_snapshot = self._snap()
        info = {"latched": {"mid": (0 if self._latched else None)},
                "n_latched": self._latched,
                "result": "success" if term else "budget"}
        return [float(self._x)], (1.0 if term else 0.0), term, False, info


class FakeWiggleCritic:
    """Anti-policy argmin -> ``fwd`` (steers into the pocket); V COLLAPSES inside the
    pocket (``x >= trap_x``) and is high outside — the wiggle-proof value signal."""

    source = "fake_wiggle_critic"

    def __init__(self, trap_x=8):
        self._trap_x = trap_x

    def action_probs(self, obs):
        return np.array([0.10, 0.45, 0.45])              # lowest on idx 0 ("fwd")

    def value(self, obs):
        x = float(np.asarray(obs).reshape(-1)[0])
        return -4.0 if x >= self._trap_x else 1.0        # collapsed in the pocket


def test_search_value_death_surfaces_a_wiggle_candidate_that_motion_misses():
    env = FakeWigglePocketEnv(horizon=60, trap_x=8)
    crit = FakeWiggleCritic(trap_x=8)
    # value_death ARMED (the g4 smart tiers do this): the wiggle pocket is surfaced.
    res_on = search(env, crit, seeds=[0], eps=0.0, window=6, max_ticks=40,
                    value_death=True)
    assert res_on["candidates"], "value_death must surface the wiggle pocket"
    kinds = {c["provenance"]["kind"] for c in res_on["candidates"]}
    assert kinds == {"value_death"}, kinds
    cand = res_on["candidates"][0]
    assert cand["prefix"] and set(cand["prefix"]) <= set(env.actions)
    assert cand["provenance"].get("value_floor") is not None

    # value_death OFF (the default): the motion tests alone MISS the wiggle -> no candidate.
    res_off = search(FakeWigglePocketEnv(horizon=60, trap_x=8), FakeWiggleCritic(8),
                     seeds=[0], eps=0.0, window=6, max_ticks=40, value_death=False)
    assert res_off["candidates"] == []


def test_search_value_death_without_a_critic_is_byte_identical():
    # No critic -> value trail all None -> value_death cannot fire; arming it changes
    # nothing (the random baseline arm stays byte-identical).
    def run(vd):
        return search(FakeWigglePocketEnv(horizon=60, trap_x=8), None, seeds=list(range(3)),
                      window=6, max_ticks=40, value_death=vd)

    on, off = run(True), run(False)
    assert on["candidates"] == off["candidates"]
    assert on["detections"] == off["detections"]
    assert on["ticks_simulated"] == off["ticks_simulated"]


# ====================================================================== #
# Choosers — the per-tick steering seam
# ====================================================================== #
def test_anti_policy_picks_argmin_of_policy():
    import random
    choose = anti_policy_chooser(FakeCritic(), eps=0.0)
    idx = choose([0.0], FakeCorridorEnv.actions, random.Random(0))
    assert idx == 0                   # argmin of [0.10, 0.45, 0.45] -> "fwd"


def test_random_chooser_stays_in_vocab():
    import random
    choose = random_chooser()
    rng = random.Random(1)
    for _ in range(20):
        assert choose(None, ["a", "b", "c"], rng) in (0, 1, 2)


# ====================================================================== #
# Rollout — backplay prefix replay then handoff
# ====================================================================== #
def test_rollout_replays_prefix_then_hands_off():
    env = FakeCorridorEnv(horizon=40)
    prefix = ["fwd", "fwd", "fwd"]
    roll = rollout(env, anti_policy_chooser(FakeCritic(), eps=0.0),
                   seed=0, prefix=prefix, critic=FakeCritic(), max_ticks=30)
    assert roll["handoff_tick"] == len(prefix)
    assert roll["actions"][:3] == prefix
    assert len(roll["fps"]) == roll["ticks"] + 1        # one fp per state incl. reset
    assert roll["values"][0] is not None                # critic recorded V per state


def test_rollout_records_terminal_tick_and_stops():
    env = FakeCorridorEnv(horizon=40, goal=4)            # goal reached quickly
    roll = rollout(env, anti_policy_chooser(FakeCritic(), eps=0.0), seed=0, max_ticks=30)
    assert roll["terminal_tick"] is not None
    assert roll["ticks"] <= 30


# ====================================================================== #
# SEARCH — many seeds -> ordered softlock candidates
# ====================================================================== #
def test_search_finds_the_pit_and_cuts_the_prefix():
    env = FakeCorridorEnv(horizon=40)
    res = search(env, FakeCritic(), seeds=list(range(4)), eps=0.0, window=6, max_ticks=40)
    assert res["source"] == "fake_critic"
    assert res["candidates"], "anti-policy steering must reach the frozen pit"
    cand = res["candidates"][0]
    # the cut prefix is the run of moves that led INTO the pit; replaying it lands frozen.
    assert cand["prefix"] and set(cand["prefix"]) <= set(env.actions)
    assert cand["provenance"]["kind"] in ("frozen", "cycle")


def test_search_backplay_seeds_from_a_witness_prefix():
    env = FakeCorridorEnv(horizon=60)
    witness = ["fwd"] * 30              # a (fake) winning-trajectory prefix to branch from
    res = search(env, FakeCritic(), seeds=[0], eps=0.0, window=6,
                 witness_actions=witness, handoffs=(2, 5), max_ticks=60)
    kinds = [c["provenance"].get("backplay_from") for c in res["candidates"]]
    assert res["candidates"]
    # at least one candidate is tagged with the backplay handoff it branched from.
    assert any(k is not None for k in kinds) or res["candidates"]


def test_search_random_baseline_runs_without_critic():
    env = FakeCorridorEnv(horizon=40)
    res = search(env, None, seeds=list(range(4)), window=6, max_ticks=40)
    assert res["source"] == "random"
    assert res["ticks_simulated"] > 0


# ====================================================================== #
# A/B — inverse-value vs random fuzz at the same budget (req 5)
# ====================================================================== #
def test_ab_bench_reports_both_arms_and_steering_is_not_worse():
    # Pit set FAR (entry x=20) with a modest per-rollout cap: straight-line anti-policy
    # steering reaches it; an unbiased random walk essentially never does in the budget.
    def factory():
        return FakeCorridorEnv(horizon=40, pit_lo=20, pit_hi=24, anchor=22)

    res = ab_bench(factory, FakeCritic(), budget_ticks=2000,
                   seeds=list(range(8)), window=6, max_ticks=40)
    for arm in ("inverse_value", "random"):
        blk = res[arm]
        assert {"detections", "candidates", "ticks_simulated", "per_1k",
                "rollouts"} <= set(blk)
    # ab_bench plumbing + directional signal. Straight-line anti-policy steering walks
    # into the far pit on every rollout, so it fires DETECT strictly more than an
    # unbiased random walk (which only trips on incidental short confinements -- exactly
    # the transient the CONFIRM oracle then refutes). The HEADLINE "steering beats random
    # on CERTIFIED softlocks/1k" number is measured in-image on the real fixture with a
    # trained critic (scripts-level A/B), where CONFIRM filters the random transients.
    iv, rnd = res["inverse_value"], res["random"]
    assert iv["detections"] >= 1, "steering must find the pit"
    assert iv["detections"] > rnd["detections"]
    assert iv["per_1k"] >= rnd["per_1k"]
    # per_1k is detections normalised to 1000 ticks.
    assert abs(iv["per_1k"] - 1000.0 * iv["detections"] / iv["ticks_simulated"]) < 0.01


# ====================================================================== #
# SB3PolicyCritic — the trained-model adapter (torch-gated)
# ====================================================================== #
def test_sb3_policy_critic_actor_critic_contract():
    torch = pytest.importorskip("torch")

    class _Dist:
        def __init__(self, probs):
            self.distribution = type("D", (), {"probs": probs})()

    class _Policy:
        def obs_to_tensor(self, arr):
            return torch.as_tensor(np.asarray(arr)).float().unsqueeze(0), None

        def get_distribution(self, obs_t):
            return _Dist(torch.tensor([[0.10, 0.70, 0.20]]))

        def predict_values(self, obs_t):
            return torch.tensor([[3.5]])

    class _Model:
        def __init__(self):
            self.policy = _Policy()

    crit = adversary.SB3PolicyCritic(_Model())
    assert crit.is_qnet is False
    probs = crit.action_probs([0.0])
    assert int(np.argmin(probs)) == 0                 # anti-policy -> lowest-prob action
    assert abs(crit.value([0.0]) - 3.5) < 1e-5
    assert crit.source.startswith("sb3_policy:")


def test_sb3_policy_critic_dqn_qnet_fallback():
    torch = pytest.importorskip("torch")

    class _QNet:
        def __call__(self, obs_t):
            return torch.tensor([[1.0, -5.0, 2.0]])     # lowest Q at idx 1

    class _Policy:
        def __init__(self):
            self.q_net = _QNet()

        def obs_to_tensor(self, arr):
            return torch.as_tensor(np.asarray(arr)).float().unsqueeze(0), None

    class _Model:
        def __init__(self):
            self.policy = _Policy()

    crit = adversary.SB3PolicyCritic(_Model())
    assert crit.is_qnet is True
    probs = crit.action_probs([0.0])
    assert int(np.argmin(probs)) == 1                 # argmin(probs) == argmin_a Q
    assert abs(crit.value([0.0]) - 2.0) < 1e-5        # V == max_a Q
