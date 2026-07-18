"""The g4 INVERSE-VALUE tier wiring — CONFIRM (tree-refutation) + grading + ladder
placement + repair-hint, driven on the py lane through INJECTED candidates so no engine
or trained model is needed. (SEARCH+DETECT are unit-tested in tests/test_adversary.py;
the real gdscript SEARCH->DETECT->CONFIRM end-to-end is tests/test_gd_adversary.py.)

Reuses the momentum-pit ``SOFTLOCK`` / benign ``CONTROL`` fixtures from test_g4.

Design: notes/adversarial/INVERSE_VALUE_G4.md §3-4 (grading) + FEASIBILITY_LITERATURE.md.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness.verify import g4  # noqa: E402
from test_g4 import CONTROL, SOFTLOCK, SMALL, _report, factory  # noqa: E402

# Small, fast oracle sizing (mirrors test_g4's STALE) for the CONFIRM tree solves.
IV = dict(stale_H=30, stale_budget=2500, top_m=6)


def _softlock_report():
    return _report(["run", "run", "leap"] + ["run"] * 6, 9,
                   checkpoints={"lip": 2, "crossed": 3})


# ====================================================================== #
# CONFIRM certifies an injected softlock candidate -> a hard `softlock` finding
# ====================================================================== #
# ====================================================================== #
# VALUE-DEATH wiring: a value_death-kind candidate certifies with its kind carried
# through to the finding's provenance (motion-invariant trigger, identical CONFIRM).
# ====================================================================== #
# ====================================================================== #
# A refutable (benign) candidate is DETECTED but NOT certified -> no finding
# ====================================================================== #
# ====================================================================== #
# Model-gating: with NO model / critic / candidates the tier is a NO-OP
# ====================================================================== #
# ====================================================================== #
# Repair report prefers the finding-level (checkpoint-naming) hint
# ====================================================================== #
# ====================================================================== #
# FROZEN-STATE ENRICHMENT — the ENGINE-TRUTH frozen pocket rides on the finding
# (Elias directive: give the position + state of the game from the engine, so the LLM
# that wrote the game is made aware of the softlock). Derived from the ALREADY-replayed
# prefix episode — zero extra engine runs on the certify path.
# ====================================================================== #
# -- frozen_state helper (pure over a replayed episode dict) -------------- #
def test_frozen_state_helper_bounds_and_orders_nearby():
    ep = {"ticks": 12, "checkpoints": {"lip": 2},
          "final_snapshot": {
              "player": {"pos": [100.0, 100.0], "vel": [0.0, 0.0]},
              "far": {"pos": [900.0, 900.0], "vel": [0.0, 0.0]},
              "near": {"pos": [110.0, 100.0], "vel": [0.0, 0.0]},
          }}
    fs = g4._frozen_state(ep, "player", "lip", n_nearby=1)
    assert fs["controlled"] == {"name": "player", "pos": [100.0, 100.0], "vel": [0.0, 0.0]}
    assert fs["ticks_elapsed"] == 12 and fs["dimension"] == 2
    assert fs["last_latched_checkpoint"] == "lip"
    # nearest-first, capped at n_nearby (the "near" body wins over "far").
    assert [b["name"] for b in fs["nearby"]] == ["near"]
    assert fs["nearby"][0]["dist"] == 10.0
    assert fs["enclosing"] == []


def test_frozen_state_helper_is_snapshot_missing_safe():
    # A weird/missing snapshot must degrade to None, never raise (certify must not break).
    fs = g4._frozen_state({"ticks": 0, "final_snapshot": {}}, "player", None)
    assert fs["controlled"] == {"name": "player", "pos": None, "vel": None}
    assert fs["nearby"] == [] and fs["dimension"] is None and fs["enclosing"] == []
    assert fs["last_latched_checkpoint"] is None


# -- enclosure facts (pure over G0.5 geometry body facts) ---------------- #
def test_enclosing_facts_names_the_pocket_walls():
    geometry = [
        {"name": "wall", "pos": [100.0, 100.0], "static": True,
         "half_extents": [40.0, 40.0]},                  # a real footprint near the point
        {"name": "player", "pos": [100.0, 100.0], "controlled": True},   # never a wall
        {"name": "gem", "pos": [100.0, 100.0], "static": True},          # bare marker (no footprint)
        {"name": "faraway", "pos": [900.0, 900.0], "static": True,
         "half_extents": [10.0, 10.0]},                  # too far to bound the point
    ]
    enc = g4._enclosing_facts([120.0, 100.0], geometry)   # just outside the wall, within pad
    assert [e["name"] for e in enc] == ["wall"]
    assert enc[0]["aabb"] == [[60.0, 60.0], [140.0, 140.0]]


def test_enclosing_facts_empty_without_geometry_or_position():
    assert g4._enclosing_facts([120.0, 100.0], []) == []
    assert g4._enclosing_facts(None, [{"name": "w", "static": True, "pos": [1, 1],
                                       "half_extents": [1, 1]}]) == []


def test_geometry_facts_empty_off_the_gdscript_lane():
    # py/js have no serve check-op geometry -> [] (best-effort omit, no engine spawn).
    assert g4._geometry_facts("py", SOFTLOCK) == []
    assert g4._geometry_facts("gdscript", "") == []
