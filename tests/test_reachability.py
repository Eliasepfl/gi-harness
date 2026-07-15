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

from harness.verify.reachability import (  # noqa: E402
    check_reachability, targets_and_occupancy,
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


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
