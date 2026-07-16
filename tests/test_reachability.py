"""Tests for the G0.5 geometric reachability pre-filter (harness.verify.reachability).

Pure geometry, no engine — a coarse flood-fill over static footprints. The check is
NECESSARY-not-SUFFICIENT: a walled-off target is a hard reject; a reachable target only
means a corridor plausibly exists (G3 still decides dynamic solvability). Tests pin both
directions plus the get-near tolerance, the clearance gate, 3D, and the body-facts
classification (markers are targets, never walls).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from harness.verify import gameverify as gv  # noqa: E402
from harness.verify import treesolve as ts  # noqa: E402
from harness.verify.reachability import (  # noqa: E402
    check_reachability, failure_reachable, targets_and_occupancy, terminal_reachable,
)


def _box_walls(cx, cy, half=60.0, thick=12.0):
    """A SEALED square box centred at (cx, cy), side 2*half, wall thickness `thick`."""
    l, r = cx - half, cx + half
    t, b = cy - half, cy + half
    return [
        ((l, t), (r, t + thick)),          # top
        ((l, b - thick), (r, b)),          # bottom
        ((l, t), (l + thick, b)),          # left
        ((r - thick, t), (r, b)),          # right
    ]


# ====================================================================== #
# 2D reachability — reject vs pass
# ====================================================================== #
def test_walled_off_goal_is_unreachable():
    walls = _box_walls(400, 300)
    res = check_reachability((100, 300), [{"name": "goal", "pos": (400, 300)}],
                             walls, (800, 600))
    assert res["reachable"] is False
    assert res["unreachable"] == ["goal"]
    assert "walled off" in res["detail"]


def test_goal_reachable_when_the_box_has_an_opening():
    walls = _box_walls(400, 300)[:3]        # drop the right wall -> a gap
    res = check_reachability((100, 300), [{"name": "goal", "pos": (400, 300)}],
                             walls, (800, 600))
    assert res["reachable"] is True
    assert res["unreachable"] == []


def test_open_field_is_reachable():
    res = check_reachability((100, 300),
                             [{"name": "gem_a", "pos": (300, 165)},
                              {"name": "gem_b", "pos": (560, 340)}],
                             [((0, 590), (800, 600))],   # a floor strip, walkable above
                             (800, 600))
    assert res["reachable"] is True


def test_no_occupancy_passes_trivially():
    # mini_collect-shaped: two bare markers, no walls at all -> nothing to wall off.
    res = check_reachability((300, 300),
                             [{"name": "gem_a", "pos": (300, 165)},
                              {"name": "gem_b", "pos": (560, 340)}],
                             [], (800, 600))
    assert res["reachable"] is True
    assert res["cells"] is None


def test_one_of_several_targets_walled_off():
    walls = _box_walls(400, 300)
    res = check_reachability((100, 100),
                             [{"name": "free_gem", "pos": (700, 100)},
                              {"name": "trapped_gem", "pos": (400, 300)}],
                             walls, (800, 600))
    assert res["reachable"] is False
    assert res["unreachable"] == ["trapped_gem"]


def test_clearance_gate_blocks_a_gap_narrower_than_the_body():
    # Two world-spanning walls leave a 60 px gap at x~400. A small body (clearance 6)
    # squeezes through; a large body (clearance 40) cannot -> the goal becomes
    # unreachable for the wide body (the gap is narrower than it).
    gap = [((0, 290), (370, 310)), ((430, 290), (800, 310))]   # 60 px gap
    goal = [{"name": "goal", "pos": (400, 500)}]
    thin = check_reachability((400, 100), goal, gap, (800, 600), clearance=6)
    wide = check_reachability((400, 100), goal, gap, (800, 600), clearance=40)
    assert thin["reachable"] is True
    assert wide["reachable"] is False


# ====================================================================== #
# 3D reachability
# ====================================================================== #
def test_3d_slab_separates_spawn_from_target():
    # A slab spanning the whole y-z cross section at x~400 splits the world in two.
    slab = [((390, -100, -100), (410, 700, 500))]
    res = check_reachability((100, 300, 200),
                             [{"name": "goal", "pos": (700, 300, 200)}],
                             slab, (800, 600, 400))
    assert res["dims"] == 3
    assert res["reachable"] is False
    assert res["unreachable"] == ["goal"]


def test_3d_partial_slab_is_passable():
    slab = [((390, -100, -100), (410, 300, 500))]   # only spans HALF the y extent
    res = check_reachability((100, 300, 200),
                             [{"name": "goal", "pos": (700, 300, 200)}],
                             slab, (800, 600, 400))
    assert res["reachable"] is True


# ====================================================================== #
# Body-facts classification (targets_and_occupancy)
# ====================================================================== #
def test_facts_split_marker_target_vs_wall_occupancy():
    bodies = [
        {"name": "player", "pos": [150, 300], "controlled": True, "static": False,
         "half_extents": [16, 16]},
        {"name": "gem", "pos": [400, 300], "static": True},        # bare marker
        {"name": "wall", "pos": [500, 300], "static": True,
         "half_extents": [10, 200]},                               # a real wall
        {"name": "pad", "pos": [600, 300], "static": True, "sensor": True,
         "radius": 40},                                            # sensor goal
    ]
    spawn, clearance, targets, occ = targets_and_occupancy(bodies)
    assert spawn == (150.0, 300.0)
    assert clearance >= 8.0
    names = sorted(t["name"] for t in targets)
    assert names == ["gem", "pad"]              # marker + sensor -> targets, not walls
    assert len(occ) == 1                        # only the real wall is occupancy
    (mn, mx) = occ[0]
    assert mn == [490.0, 100.0] and mx == [510.0, 500.0]


def test_facts_end_to_end_walls_off_a_gem():
    # A gem boxed by four wall bodies (aabb footprints) -> unreachable via the facts path.
    walls = _box_walls(400, 300)
    bodies = [{"name": "player", "pos": [100, 300], "controlled": True, "static": False,
               "half_extents": [16, 16]},
              {"name": "gem", "pos": [400, 300], "static": True}]
    for i, (mn, mx) in enumerate(walls):
        bodies.append({"name": f"wall_{i}", "pos": [(mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2],
                       "static": True, "aabb": [list(mn), list(mx)]})
    spawn, clearance, targets, occ = targets_and_occupancy(bodies)
    assert len(occ) == 4 and [t["name"] for t in targets] == ["gem"]
    res = check_reachability(spawn, targets, occ, (800, 600), clearance=clearance)
    assert res["reachable"] is False and res["unreachable"] == ["gem"]


# ====================================================================== #
# WAVE 1 — PRESSURE: terminal_reachable / failure_reachable (executor seam)
# ====================================================================== #
# A deterministic, physics-free 1-D "trek" FakeExecutor (mirrors test_treesolve.py):
# ``fwd`` moves the body +1, ``back`` -1, ``idle`` nothing. Success at x >= win; an
# optional lethal ``lose`` line (x <= lose -> a `failure` terminal, checked BEFORE
# success, exactly as the runner/serve host order it) gives a REACHABLE loss to hunt.
_R_ACTIONS = ["fwd", "back", "idle"]
_R_STEP = {"fwd": 1, "back": -1, "idle": 0}


class TrekExecutor:
    def __init__(self, batched=False, win=8, lose=None):
        self.batched = batched
        self.win = win
        self.lose = lose            # x <= lose -> failure; None -> never losable
        self.seen = []

    def run_batch(self, game_source, episodes, max_ticks, frames_every=0,
                  escape_margin=None):
        out = []
        for ep in episodes:
            acts = list(ep.get("actions", []))
            self.seen.append(tuple(acts))
            out.append(self._run(acts, max_ticks))
        return out

    def _run(self, actions, max_ticks):
        x, applied, result = 0, 0, "budget"
        latches = {"m_near": None, "m_far": None}
        limit = len(actions) if max_ticks is None else min(len(actions), max_ticks)
        for i in range(limit):
            x += _R_STEP.get(actions[i], 0)
            applied += 1
            if latches["m_near"] is None and x >= 3:
                latches["m_near"] = applied
            if latches["m_far"] is None and x >= 6:
                latches["m_far"] = applied
            if self.lose is not None and x <= self.lose:     # failure BEFORE success
                result = "failure"
                break
            if x >= self.win:
                result = "success"
                break
        if result == "budget" and max_ticks is not None and len(actions) < max_ticks:
            result = "exhausted"
        snap = {"trek": {"pos": [float(x), 0.0], "vel": [0.0, 0.0], "angle": 0.0}}
        return {"result": result, "ticks": applied, "checkpoints": latches,
                "final_snapshot": snap, "actions": actions[:applied]}


@pytest.fixture()
def small_thresholds(monkeypatch):
    monkeypatch.setattr(gv, "PROBE_HORIZON", 120)
    monkeypatch.setattr(gv, "TRIVIAL_TICKS", 5)
    monkeypatch.setattr(ts, "TICK_BUDGET", 6000)


# ---- terminal_reachable ------------------------------------------------ #
def test_terminal_reachable_success_from_spawn(small_thresholds):
    # A solvable game: success is reachable -> reachable via a success terminal.
    v = terminal_reachable(TrekExecutor(win=8), "trek", _R_ACTIONS, horizon=120,
                           budget=6000)
    assert v["reachable"] is True and v["kind"] == "success"
    assert v["verdict"] == "reachable" and v["witness"] is not None


def test_terminal_reachable_failure_when_only_failure(small_thresholds):
    # Success unreachable, but driving back far enough LOSES: a failure terminal still
    # proves the state can leave non-terminal limbo -> reachable (kind failure).
    v = terminal_reachable(TrekExecutor(win=10_000, lose=-4), "trek", _R_ACTIONS,
                           horizon=120, budget=6000)
    assert v["reachable"] is True and v["kind"] == "failure"
    assert v["verdict"] == "reachable" and v["witness"] is not None


def test_terminal_reachable_env_softlock_when_neither(small_thresholds):
    # Neither success nor failure reachable within budget -> a real ENV-softlock: the
    # principled stuck-vs-refusal separator's "stuck ENVIRONMENT" verdict.
    v = terminal_reachable(TrekExecutor(win=10_000, lose=None), "trek", _R_ACTIONS,
                           horizon=60, budget=3000)
    assert v["reachable"] is False and v["kind"] is None
    assert v["verdict"] == "env_softlock" and v["witness"] is None


def test_terminal_reachable_refusal_case_is_reachable(small_thresholds):
    # Elias's stuck-vs-refusal: from a state where a terminal IS reachable, an idle
    # agent is REFUSING, not softlocked. terminal_reachable reports the reachability;
    # the "refusal" label is (terminal_reachable == True) AND (agent did not advance).
    v = terminal_reachable(TrekExecutor(win=8), "trek", _R_ACTIONS, horizon=120,
                           budget=6000)
    assert v["reachable"] is True          # a diligent player CAN still win -> not stuck


def test_terminal_reachable_prefix_rebases(small_thresholds):
    # The prefix wrapper re-bases continuations: advancing part-way then finishing still
    # reaches success (exercises _PrefixExecutor tick/checkpoint re-basing).
    v = terminal_reachable(TrekExecutor(win=8), "trek", _R_ACTIONS,
                           prefix=["fwd", "fwd"], horizon=120, budget=6000)
    assert v["reachable"] is True and v["kind"] == "success"
    assert v["prefix_len"] == 2


# ---- failure_reachable (the gate's failure sweep) ---------------------- #
def test_failure_reachable_finds_a_reachable_loss(small_thresholds):
    # Spamming "back" walks into the lethal line -> the coverage pass finds the loss.
    r = failure_reachable(TrekExecutor(win=8, lose=-4), "trek", _R_ACTIONS,
                          horizon=60, budget=3000)
    assert r["reachable"] is True
    assert r["witness"] is not None and r["n_failed"] >= 1


def test_failure_reachable_none_when_unfailable(small_thresholds):
    # No lose line at all -> no adversarial rollout ever loses -> not reachable.
    r = failure_reachable(TrekExecutor(win=8, lose=None), "trek", _R_ACTIONS,
                          horizon=60, budget=2000)
    assert r["reachable"] is False and r["witness"] is None


def test_failure_reachable_empty_actions():
    r = failure_reachable(TrekExecutor(), "trek", [])
    assert r == {"reachable": False, "witness": None, "n_plans": 0, "n_failed": 0}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
