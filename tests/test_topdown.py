"""Tests for TOP-DOWN world mode (SPEC.md §2b, the `world` block).

Every prior Godot game was forced side-view because gravity was hardcoded (0,-900).
A `world` block now selects the VIEW: `side` (default, gravity (0,-900), byte-identical
back-compat) or `topdown` (gravity (0,0); bodies glide; `world.linear_damp` is the
friction analog so a released body coasts to a stop). The x/y plane is the floor seen
from above.

Two tiers, mirroring tests/test_godot_exec.py:

* **Pure-python (always run):** the top-down slalom fixture matches the schema and its
  static inspect_world read suppresses the gravity-dependent warnings.

* **End-to-end (skipped without the Godot binary):** the top-down slalom certifies the
  whole G0-G3 funnel and its witness replays deterministically; a thrust-then-coast body
  comes to REST under linear_damp (the damp-stop sanity); a top-down body does NOT fall
  (zero gravity) while the SAME bodies in side view DO (back-compat, gravity intact); and
  the shipped side-view fixtures stay byte-identical across runs.
"""
from __future__ import annotations

import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.designer import tools as T  # noqa: E402
from harness.verify.executors import GodotExecutor, find_godot_exe  # noqa: E402
from harness.verify.gameverify import verify_game  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXAMPLES = os.path.join(_ROOT, "tests", "fixtures", "godot_specs")
_SLALOM = os.path.join(_EXAMPLES, "topdown_slalom.spec.json")

GODOT_EXE = find_godot_exe()
requires_godot = pytest.mark.skipif(GODOT_EXE is None, reason="Godot binary not present")


def _speed(snap_body: dict) -> float:
    vx, vy = snap_body["vel"]
    return math.hypot(vx, vy)


# ====================================================================== #
# Pure-python: the fixture is well-formed and reads cleanly (always run)
# ====================================================================== #
def test_slalom_fixture_is_topdown_and_well_formed():
    with open(_SLALOM, encoding="utf-8") as fh:
        spec = json.load(fh)
    assert spec["world"] == {"view": "topdown", "linear_damp": 1.5}
    # exactly one controlled dynamic body; walls on all four sides.
    controlled = [b for b in spec["bodies"] if b.get("control") and not b.get("static")]
    assert len(controlled) == 1 and controlled[0]["name"] == "cart"
    wall_names = {b["name"] for b in spec["bodies"] if b["name"].startswith("wall_")}
    assert wall_names == {"wall_top", "wall_bottom", "wall_left", "wall_right"}


def test_slalom_inspect_suppresses_gravity_warnings():
    with open(_SLALOM, encoding="utf-8") as fh:
        spec = json.load(fh)
    out = T.inspect_world(spec)
    assert out["summary"]["view"] == "topdown"
    assert out["summary"]["gravity"] == [0.0, 0.0]
    assert out["summary"]["ballistic"] is None
    kinds = {w["kind"] for w in out["warnings"]}
    assert "floating_static" not in kinds
    assert "forecast_oob" not in kinds
    assert "unsatisfiable_park" not in kinds


# ====================================================================== #
# End-to-end certification (skipped without the Godot binary)
# ====================================================================== #
@requires_godot
def test_topdown_slalom_verifies_end_to_end():
    """thrust + torque + contained()+speed drive the full G0-G3 funnel in a zero-gravity
    arena: the tree solver finds a replayable witness that threads the cones and settles
    the cart onto the bay."""
    rep = verify_game(_SLALOM, sandboxed=False)
    assert rep["passed"] is True, rep
    assert rep["failure_class"] is None
    assert rep["engine"] == "godot"
    for layer in ("G0_static", "G1_rollout", "G2_goal", "G3_solve"):
        assert rep["layers"][layer]["passed"], (layer, rep["layers"][layer])
    w = rep["witness"]
    assert w is not None and w["ticks"] >= 20                    # non-trivial
    assert all(t is not None for t in w["checkpoints"].values()), w["checkpoints"]


@requires_godot
def test_topdown_witness_replays_deterministically():
    reps = [verify_game(_SLALOM, sandboxed=False) for _ in range(2)]
    ticks = {r["witness"]["ticks"] for r in reps}
    latches = {json.dumps(r["witness"]["checkpoints"], sort_keys=True) for r in reps}
    assert len(ticks) == 1, ticks
    assert len(latches) == 1, latches


# ---- damp-stop sanity: a thrust-then-coast body comes to rest --------------
_COAST_SPEC = {
    "engine": "godot",
    "world": {"view": "topdown", "linear_damp": 2.0},
    "meta": {"title": "coast", "prompt": "coast to a stop",
             "actions": ["go", "wait"]},
    "bodies": [
        {"name": "anchor", "shape": "box", "pos": [80, 300], "size": [40, 40],
         "static": True},
        {"name": "puck", "shape": "circle", "pos": [200, 300], "radius": 15,
         "mass": 1.0, "control": True},
    ],
    "act": {
        "go": [{"verb": "impulse", "body": "puck", "vec": [200, 0]}],
        "wait": [{"verb": "torque", "body": "puck", "magnitude": 0.0}],
    },
    "predicates": {"checkpoints": {}},
}


@requires_godot
def test_damp_stop_body_coasts_to_rest_in_topdown():
    ex = GodotExecutor()
    # 3 forward impulses, then 60 ticks of no thrust -> linear_damp coasts it to rest.
    actions = ["go", "go", "go"] + [None] * 60
    rec = ex.run_batch(json.dumps(_COAST_SPEC), [{"seed": 0, "actions": actions}],
                       max_ticks=len(actions))[0]
    puck = rec["final_snapshot"]["puck"]
    assert puck["pos"][0] > 250, puck        # it DID move right (a real coast, not inert)
    assert _speed(puck) < 5.0, puck          # and it came to REST under damping


@requires_godot
def test_side_view_same_bodies_do_not_come_to_rest():
    """Back-compat contrast: drop the `world` block and the SAME bodies fall under the
    restored (0,-900) gravity -> the puck never rests (proves side gravity is intact and
    that only the top-down branch zeroes it)."""
    side = json.loads(json.dumps(_COAST_SPEC))
    del side["world"]                        # -> default side view, gravity (0,-900)
    ex = GodotExecutor()
    actions = ["go", "go", "go"] + [None] * 60
    rec = ex.run_batch(json.dumps(side), [{"seed": 0, "actions": actions}],
                       max_ticks=len(actions))[0]
    puck = rec["final_snapshot"]["puck"]
    assert puck["vel"][1] < -100.0, puck     # falling fast (no floor under it, gravity on)
    assert _speed(puck) > 100.0, puck        # nowhere near rest


# ---- byte-identical back-compat for the shipped side-view fixtures ----------
@requires_godot
@pytest.mark.parametrize("name", ["traverse", "collect2", "escape"])
def test_side_fixtures_stay_byte_identical(name):
    ex = GodotExecutor()
    src = open(os.path.join(_EXAMPLES, f"{name}.spec.json"), encoding="utf-8").read()
    specs = [{"seed": 0, "actions": [None] * 12}]
    runs = [json.dumps(ex.run_batch(src, specs, 12)) for _ in range(2)]
    assert runs[0] == runs[1]                # deterministic side-view trajectory


@requires_godot
def test_side_view_airborne_body_falls_under_gravity():
    # An airborne controlled body on a side spec DROPS under the restored (0,-900): the
    # side-view coast spec's puck (spawned in the air, no floor under it) falls hard.
    side = json.loads(json.dumps(_COAST_SPEC))
    del side["world"]                        # -> default side view, gravity (0,-900)
    ex = GodotExecutor()
    rec = ex.run_batch(json.dumps(side), [{"seed": 0, "actions": [None] * 20}], 20,
                       frames_every=1)[0]
    frames = rec["frames"]
    y0 = frames[0]["entities"]["puck"]["pos"][1]
    y_end = frames[-1]["entities"]["puck"]["pos"][1]
    assert y_end < y0 - 5.0, (y0, y_end)     # fell under gravity (side view intact)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
