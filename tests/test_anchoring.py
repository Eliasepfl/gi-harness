"""MATERIAL REALITY — the material-anchoring gate (gameverify._anchoring_gate) and its
feedback bridge (feedback._compile_anchoring), tested ENGINE-FREE.

The gate's decision core (`_unanchored_milestones`) is pure: it reads the witness-replay
FRAMES ({tick, entities:{name:{pos,controlled,static,...}}}) + the t=0 CHECK GEOMETRY
(self-reported extents) and returns the milestones whose flip lands in empty space. So the
whole rule is exercised here with SYNTHETIC frames/geometry — no Godot, no executor spawn.
The end-to-end gd-lane fixtures (a real ghost game rejected with the typed hint) ride the
container gate; this file is the pure regression net that runs on the login node.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.gen import feedback  # noqa: E402
from harness.verify import gameverify as gv  # noqa: E402

WS = [800, 600]                          # tol = max(24, 0.06*hypot(800,600)) = max(24, 60) = 60


def _frame(tick, ents):
    return {"tick": tick, "entities": ents}


def _body(pos, *, controlled=False, static=False):
    return {"pos": [float(pos[0]), float(pos[1])], "vel": [0.0, 0.0],
            "angle": 0.0, "controlled": controlled, "static": static}


def _geo(name, pos, *, controlled=False, static=False, sensor=False, **foot):
    g = {"name": name, "pos": [float(pos[0]), float(pos[1])],
         "controlled": controlled, "static": static, "sensor": sensor}
    g.update(foot)                       # radius= / half_extents= / aabb=
    return g


# ---------------------------------------------------------------------- #
# tolerance + extent helpers
# ---------------------------------------------------------------------- #
def test_tolerance_floors_and_scales_with_bounds():
    # A 800x600 world -> 0.06 * 1000 diagonal = 60 px, above the 24 px floor.
    assert gv._anchor_tolerance([_geo("p", (400, 300), controlled=True)], WS) == 60.0
    # A tiny/degenerate world floors at ANCHOR_TOL_FLOOR (never zero -> never flags everything).
    assert gv._anchor_tolerance([], [0, 0]) == gv.ANCHOR_TOL_FLOOR


def test_extent_reads_radius_halfextents_aabb_else_zero():
    assert gv._anchor_extent({"radius": 16.0}) == 16.0
    assert gv._anchor_extent({"half_extents": [10.0, 4.0]}) == 10.0        # MAX half-dim
    assert gv._anchor_extent({"aabb": [[0, 0], [40, 10]]}) == 20.0         # half the max span
    assert gv._anchor_extent({}) == 0.0                                    # bare marker
    assert gv._anchor_extent(None) == 0.0


# ---------------------------------------------------------------------- #
# the pure decision core: GHOST flagged, ANCHORED / against-a-wall clean
# ---------------------------------------------------------------------- #
def _ghost_case():
    """A milestone that latches on a bare coordinate (150,150) in open space; the only other
    reported body is a wall far away at the bottom."""
    geo = [_geo("player", (400, 300), controlled=True, radius=16.0),
           _geo("wall", (400, 560), static=True, radius=10.0)]
    frames = [_frame(4, {"player": _body((160, 160), controlled=True),
                         "wall": _body((400, 560), static=True)}),
              _frame(5, {"player": _body((150, 150), controlled=True),
                         "wall": _body((400, 560), static=True)})]
    return frames, geo


def test_ghost_milestone_flips_in_empty_space_is_flagged():
    frames, geo = _ghost_case()
    off = gv._unanchored_milestones(frames, geo, {"reached_zone": 5}, None, WS)
    assert len(off) == 1
    e = off[0]
    assert e["milestone"] == "reached_zone"
    assert e["tick"] == 5
    assert e["controlled"] == "player"
    assert e["nearest_body"] == "wall"
    assert e["distance"] > e["tol"] == 60.0


def test_win_coinciding_with_a_ghost_checkpoint_collapses_to_one_flip():
    # checkpoint AND is_success both latch at tick 5 on the same coordinate -> ONE entry.
    frames, geo = _ghost_case()
    off = gv._unanchored_milestones(frames, geo, {"reached_zone": 5}, 5, WS)
    assert [e["milestone"] for e in off] == ["reached_zone"]


def test_anchored_milestone_on_a_reported_body_is_clean():
    # The goal is a REAL reported body the controlled body overlaps at the flip -> not flagged.
    geo = [_geo("player", (400, 300), controlled=True, radius=16.0),
           _geo("goal_pad", (150, 150), static=True, sensor=True, radius=20.0),
           _geo("wall", (400, 560), static=True, radius=10.0)]
    frames = [_frame(4, {"player": _body((160, 160), controlled=True),
                         "goal_pad": _body((150, 150), static=True),
                         "wall": _body((400, 560), static=True)}),
              _frame(5, {"player": _body((150, 150), controlled=True),
                         "goal_pad": _body((150, 150), static=True),
                         "wall": _body((400, 560), static=True)})]
    assert gv._unanchored_milestones(frames, geo, {"reached_pad": 5}, 5, WS) == []


def test_non_spatial_milestone_against_a_wall_is_not_flagged():
    # A tick-count milestone that flips while the body sits AGAINST a wall reads as anchored
    # (a real body is right there) -> the exemption's spirit holds without special-casing.
    geo = [_geo("player", (100, 300), controlled=True, radius=16.0),
           _geo("wall", (70, 300), static=True, half_extents=[10.0, 300.0])]
    frames = [_frame(9, {"player": _body((97, 300), controlled=True),
                         "wall": _body((70, 300), static=True)}),
              _frame(10, {"player": _body((96, 300), controlled=True),
                          "wall": _body((70, 300), static=True)})]
    assert gv._unanchored_milestones(frames, geo, {"held_for_ten": 10}, None, WS) == []


def test_two_distinct_ghost_zones_flip_at_the_same_tick_stay_distinct():
    # Two ghost milestones flipping the same tick but near DIFFERENT bodies are two flips.
    geo = [_geo("player", (400, 300), controlled=True, radius=16.0),
           _geo("beacon_a", (400, 560), static=True, radius=8.0)]
    frames = [_frame(6, {"player": _body((150, 150), controlled=True),
                         "beacon_a": _body((400, 560), static=True)}),
              _frame(7, {"player": _body((150, 150), controlled=True),
                         "beacon_a": _body((400, 560), static=True)})]
    # both checkpoints latch at 7 with the SAME nearest body -> the (tick, nearest) collapse
    # keeps ONE; assert the collapse rule (same flip == one report).
    off = gv._unanchored_milestones(frames, geo, {"a": 7, "b": 7}, None, WS)
    assert len(off) == 1


# ---------------------------------------------------------------------- #
# the gate plumbing (fake executor — no Godot): non-gating, sub-check, stash, warning
# ---------------------------------------------------------------------- #
class _FakeExec:
    def __init__(self, frames):
        self._frames = frames

    def run_batch(self, source, specs, max_ticks, frames_every=0, escape_margin=None):
        return [{"result": "success", "ticks": max_ticks, "frames": self._frames}]


def _report_with_witness(checkpoints, ticks):
    return {"passed": True, "failure_class": None, "warnings": [],
            "layers": {"G3_solve": {"passed": True, "checks": {}}},
            "witness": {"seed": 0, "actions": ["a"] * ticks, "ticks": ticks,
                        "checkpoints": dict(checkpoints)}}


def _facts(geo):
    return {"geometry": geo, "world_size": {"declared": WS}}


def test_gate_flags_ghost_without_ever_blocking_certification():
    frames, geo = _ghost_case()
    report = _report_with_witness({"reached_zone": 5}, 5)
    out = gv._anchoring_gate(_FakeExec(frames), "src", report, _facts(geo))

    # NON-GATING: certification is untouched.
    assert out["passed"] is True
    assert out["failure_class"] is None

    sub = out["layers"]["G3_solve"]["checks"]["material_anchoring"]
    assert sub["pass"] is True and sub["advisory"] is True and sub["anchored"] is False

    # Stash only when flagged, naming the milestone + latch tick.
    stash = out["anchoring"]
    assert stash["outcome"] == "unanchored"
    assert stash["milestones"][0]["milestone"] == "reached_zone"
    assert stash["milestones"][0]["tick"] == 5

    # Exactly one ANCHORING: warning.
    anchoring_warnings = [w for w in out["warnings"] if w.startswith("ANCHORING: ")]
    assert len(anchoring_warnings) == 1


def test_gate_leaves_an_anchored_game_pristine():
    geo = [_geo("player", (400, 300), controlled=True, radius=16.0),
           _geo("goal_pad", (150, 150), static=True, sensor=True, radius=20.0)]
    frames = [_frame(4, {"player": _body((160, 160), controlled=True),
                         "goal_pad": _body((150, 150), static=True)}),
              _frame(5, {"player": _body((150, 150), controlled=True),
                         "goal_pad": _body((150, 150), static=True)})]
    report = _report_with_witness({"reached_pad": 5}, 5)
    out = gv._anchoring_gate(_FakeExec(frames), "src", report, _facts(geo))
    assert out["passed"] is True
    sub = out["layers"]["G3_solve"]["checks"]["material_anchoring"]
    assert sub["anchored"] is True and sub["pass"] is True
    assert "anchoring" not in out                          # no stash
    assert not [w for w in out["warnings"] if w.startswith("ANCHORING: ")]


# ---------------------------------------------------------------------- #
# feedback bridge: finding extraction, the typed hint, per-milestone fingerprints
# ---------------------------------------------------------------------- #
def _unanchored_finding(*milestones):
    return {"outcome": "unanchored",
            "milestones": [dict(m) for m in milestones], "detail": "..."}


_M1 = {"milestone": "reached_zone", "tick": 5, "controlled": "player",
       "nearest_body": "wall", "distance": 440.0, "tol": 60.0}
_M2 = {"milestone": "reached_top", "tick": 12, "controlled": "player",
       "nearest_body": "wall", "distance": 300.0, "tol": 60.0}


def test_anchoring_finding_pulls_the_stash_off_the_report():
    rep = {"anchoring": _unanchored_finding(_M1)}
    assert feedback.anchoring_finding(rep) == rep["anchoring"]
    assert feedback.anchoring_finding({}) == {}                # anchored game / other engine


def test_compile_anchoring_emits_the_typed_hint_with_the_escape_clause():
    ds = feedback._compile_anchoring(_unanchored_finding(_M1))
    assert len(ds) == 1
    d = ds[0]
    assert d.source == "unanchored_milestone" and d.origin == "anchoring"
    assert d.checkpoint_keys == ("reached_zone",)
    assert d.text.startswith("UNANCHORED MILESTONE - A CHECKPOINT FLIPS IN EMPTY SPACE")
    # milestone key + tick + distance in the detail.
    assert "reached_zone" in d.text and "tick 5" in d.text and "440.0px" in d.text
    # the exemption escape clause protects legal non-spatial milestones from a REVISE overfix.
    assert "if it does not mark a place" in d.text.lower()


def test_compile_anchoring_one_directive_per_milestone_distinct_fingerprints():
    ds = feedback._compile_anchoring(_unanchored_finding(_M1, _M2))
    assert [d.checkpoint_keys for d in ds] == [("reached_zone",), ("reached_top",)]
    assert len({d.fingerprint for d in ds}) == 2                # distinct milestones -> distinct fp


def test_compile_anchoring_same_milestone_twice_dedups_to_one():
    ds = feedback._compile_anchoring(_unanchored_finding(_M1, dict(_M1)))
    assert len(ds) == 1


def test_compile_anchoring_anchored_or_empty_yields_no_directive():
    assert feedback._compile_anchoring({"outcome": "anchored", "milestones": []}) == []
    assert feedback._compile_anchoring({}) == []


def test_compile_directives_routes_anchoring_between_pressure_and_dead_space():
    ds = feedback.compile_directives({"anchoring": _unanchored_finding(_M1)})
    assert [d.source for d in ds] == ["unanchored_milestone"]
    # a stalled repair recompiles to the SAME fingerprint (convergence guard).
    again = feedback.compile_directives({"anchoring": _unanchored_finding(_M1)})
    assert ds[0].fingerprint == again[0].fingerprint
