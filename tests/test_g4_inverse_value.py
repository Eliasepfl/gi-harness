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
from harness.verify.executors import PyExecutor  # noqa: E402
from test_g4 import CONTROL, SOFTLOCK, SMALL, _report, factory  # noqa: E402

# Small, fast oracle sizing (mirrors test_g4's STALE) for the CONFIRM tree solves.
IV = dict(stale_H=30, stale_budget=2500, top_m=6)


def _softlock_report():
    return _report(["run", "run", "leap"] + ["run"] * 6, 9,
                   checkpoints={"lip": 2, "crossed": 3})


# ====================================================================== #
# CONFIRM certifies an injected softlock candidate -> a hard `softlock` finding
# ====================================================================== #
def test_injected_candidate_certifies_softlock_end_to_end():
    out = g4.run_g4(SOFTLOCK, _softlock_report(), engine="py",
                    world_factory=factory(), tiers=(0,),
                    iv_candidates=[["run", "run", "run", "run"]], **IV, **SMALL)

    iv = out["inverse_value"]
    assert iv["status"] == "run"
    assert iv["critic_source"] == "injected"
    assert iv["detected"] == 1 and iv["certified"] >= 1

    assert out["grade"] == "open" and out["passed"] is False
    soft = [f for f in out["findings"] if f["outcome"] == "softlock"
            and f["tier"] == "inverse_value"]
    assert soft, "the injected softlock prefix must certify through the tree oracle"
    f = soft[0]
    assert f["hard"] is True and f["family"] == "inverse_value+tree_refute"

    prov = f["reproducer"]["provenance"]
    assert prov["oracle"] == "inverse_value+tree_refute"
    assert prov["critic_source"] == "injected"
    assert prov["engine"] == "py" and prov["seed"] == 0 and prov["H"] == 30
    assert "subtree_status" in prov

    # The repair hint names the last checkpoint latched before the freeze ("lip").
    assert "lip" in f["repair_hint"]
    assert prov["last_checkpoint"] == "lip"

    # The persisted reproducer genuinely re-certifies on a fresh executor.
    ap = f["reproducer"]["action_plan"]
    assert ap["kind"] == "sequence"
    recheck = g4.refute_prefix(PyExecutor(world_factory=factory()), SOFTLOCK,
                               out["actions"], ap["sequence"], H=30, budget=2500)
    assert recheck["certified"] is True


def test_inverse_value_findings_lead_the_ladder():
    out = g4.run_g4(SOFTLOCK, _softlock_report(), engine="py",
                    world_factory=factory(), tiers=(0,),
                    iv_candidates=[["run", "run", "run", "run"]], **IV, **SMALL)
    # The PRIMARY smart tier is ahead of tier-0 fuzz / stale in the merged ladder.
    assert out["findings"][0]["tier"] == "inverse_value"


# ====================================================================== #
# VALUE-DEATH wiring: a value_death-kind candidate certifies with its kind carried
# through to the finding's provenance (motion-invariant trigger, identical CONFIRM).
# ====================================================================== #
def test_value_death_candidate_certifies_and_carries_its_kind():
    out = g4.run_g4(
        SOFTLOCK, _softlock_report(), engine="py", world_factory=factory(), tiers=(0,),
        iv_candidates=[{"prefix": ["run", "run", "run", "run"],
                        "provenance": {"kind": "value_death", "source": "injected"},
                        "value": -9.0}],
        **IV, **SMALL)
    iv = out["inverse_value"]
    assert iv["detected"] == 1 and iv["certified"] >= 1
    soft = [f for f in out["findings"] if f["outcome"] == "softlock"
            and f["tier"] == "inverse_value"]
    assert soft, "the value_death prefix must certify through the SAME tree oracle"
    prov = soft[0]["reproducer"]["provenance"]
    # The motion-invariant trigger's provenance kind survives CONFIRM (findings shape
    # is identical to frozen/cycle -> the certified softlock class is unchanged).
    assert prov["kind"] == "value_death"
    assert prov["oracle"] == "inverse_value+tree_refute"


# ====================================================================== #
# A refutable (benign) candidate is DETECTED but NOT certified -> no finding
# ====================================================================== #
def test_injected_control_candidate_is_refuted_not_certified():
    rpt = _report(["push"] * 8, 8, checkpoints={"halfway": 4})
    out = g4.run_g4(CONTROL, rpt, engine="py", world_factory=factory(), tiers=(0,),
                    iv_candidates=[["push", "coast"]], **IV, **SMALL)
    iv = out["inverse_value"]
    assert iv["status"] == "run"
    assert iv["detected"] == 1 and iv["certified"] == 0
    assert not [f for f in out["findings"] if f["tier"] == "inverse_value"]
    # No inverse-value softlock -> the tier did not open the game.
    assert all(f["tier"] != "inverse_value" for f in out["hard_findings"])


# ====================================================================== #
# Model-gating: with NO model / critic / candidates the tier is a NO-OP
# ====================================================================== #
def test_ladder_is_a_noop_without_a_model():
    rpt = _report(["push"] * 8, 8, checkpoints={"halfway": 4})
    args = dict(engine="py", world_factory=factory(), tiers=(0,), **SMALL)

    base = g4.run_g4(CONTROL, rpt, **args)                      # no inverse-value args
    assert base["inverse_value"]["status"] == "skipped_no_model"
    assert base["inverse_value"]["findings"] == []
    assert base["inverse_value"]["certified"] == 0
    assert all(f["tier"] != "inverse_value" for f in base["findings"])

    # Passing model-tier args is what turns it on; the no-model run's ladder is exactly
    # tier0(+tier1+stale) — the inverse-value tier contributes nothing.
    withiv = g4.run_g4(CONTROL, rpt, iv_candidates=[["push", "coast"]], **IV, **args)
    assert withiv["inverse_value"]["status"] == "run"
    assert base["findings"] == withiv["findings"]              # benign candidate -> ladder unchanged
    assert base["grade"] == withiv["grade"]


# ====================================================================== #
# Repair report prefers the finding-level (checkpoint-naming) hint
# ====================================================================== #
def test_to_repair_report_uses_inverse_value_hint():
    out = g4.run_g4(SOFTLOCK, _softlock_report(), engine="py",
                    world_factory=factory(), tiers=(0,),
                    iv_candidates=[["run", "run", "run", "run"]], **IV, **SMALL)
    f = [f for f in out["findings"] if f["tier"] == "inverse_value"][0]
    rr = g4.to_repair_report(f)
    assert rr["failure_class"] == "G4_FINDING" and rr["outcome"] == "softlock"
    assert rr["hint"] == f["repair_hint"]      # specific hint wins over the generic map
    assert "lip" in rr["hint"]


# ====================================================================== #
# FROZEN-STATE ENRICHMENT — the ENGINE-TRUTH frozen pocket rides on the finding
# (Elias directive: give the position + state of the game from the engine, so the LLM
# that wrote the game is made aware of the softlock). Derived from the ALREADY-replayed
# prefix episode — zero extra engine runs on the certify path.
# ====================================================================== #
def test_inverse_value_finding_carries_frozen_state():
    out = g4.run_g4(SOFTLOCK, _softlock_report(), engine="py",
                    world_factory=factory(), tiers=(0,),
                    iv_candidates=[["run", "run", "run", "run"]], **IV, **SMALL)
    f = [f for f in out["findings"] if f["tier"] == "inverse_value"][0]
    fs = f["frozen_state"]
    # controlled body: the engine-truth frozen pose (name/pos/vel), read from the replay.
    assert fs["controlled"]["name"] == "player"
    assert fs["controlled"]["pos"] is not None and len(fs["controlled"]["pos"]) == 2
    assert fs["controlled"]["vel"] is not None
    assert fs["dimension"] == 2
    assert isinstance(fs["ticks_elapsed"], int) and fs["ticks_elapsed"] >= 1
    # the last milestone before the freeze (already named on the repair hint) rides along.
    assert fs["last_latched_checkpoint"] == "lip"
    # the N nearest OTHER bodies (the static "ground" box), bounded and named+placed.
    names = [b["name"] for b in fs["nearby"]]
    assert "ground" in names and "player" not in names
    assert len(fs["nearby"]) <= g4.FROZEN_NEARBY
    assert all("pos" in b and "name" in b for b in fs["nearby"])
    # py lane has no G0.5 check-op geometry -> enclosure omits gracefully (empty list).
    assert fs["enclosing"] == []


def test_value_death_candidate_finding_carries_frozen_state():
    # The motion-invariant value_death path reuses the SAME finding-construction, so its
    # certified softlocks carry the identical frozen_state shape (coordinator ADOPT).
    out = g4.run_g4(
        SOFTLOCK, _softlock_report(), engine="py", world_factory=factory(), tiers=(0,),
        iv_candidates=[{"prefix": ["run", "run", "run", "run"],
                        "provenance": {"kind": "value_death", "source": "injected"},
                        "value": -9.0}],
        **IV, **SMALL)
    f = [f for f in out["findings"] if f["tier"] == "inverse_value"][0]
    assert f["reproducer"]["provenance"]["kind"] == "value_death"
    fs = f["frozen_state"]
    assert fs["controlled"]["name"] == "player" and fs["controlled"]["pos"] is not None
    assert fs["dimension"] == 2 and fs["last_latched_checkpoint"] == "lip"
    assert "ground" in [b["name"] for b in fs["nearby"]]
    assert fs["enclosing"] == []


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
