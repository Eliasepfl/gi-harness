"""The g4 S1.5 POLICY-GUIDED DESCENT tier wiring — CONFIRM (tree-refutation) + grading +
ladder placement (BETWEEN S1 greedy and the deep seeker) + repair-hint + model-gating,
driven on the py lane through INJECTED candidates so no engine or trained model is needed.
(The alpha-ramp chooser / waypoint selection / descent_search are unit-tested in
tests/test_descent.py; the real gdscript SEARCH->DETECT->CONFIRM end-to-end is
tests/test_gd_descent.py.)

Reuses the momentum-pit ``SOFTLOCK`` / benign ``CONTROL`` fixtures from test_g4.

Design: notes/adversarial/STALE_SEEKING_PLAN.md §3.1 (S1.5) + INVERSE_VALUE_G4.md.
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
DESC = dict(stale_H=30, stale_budget=2500, top_m=6)


def _softlock_report():
    return _report(["run", "run", "leap"] + ["run"] * 6, 9,
                   checkpoints={"lip": 2, "crossed": 3})


# ====================================================================== #
# CONFIRM certifies an injected descent candidate -> a hard `softlock` finding
# ====================================================================== #
def test_injected_descent_candidate_certifies_softlock_end_to_end():
    out = g4.run_g4(SOFTLOCK, _softlock_report(), engine="py",
                    world_factory=factory(), tiers=(0,),
                    descent_candidates=[["run", "run", "run", "run"]], **DESC, **SMALL)

    desc = out["descent"]
    assert desc["status"] == "run"
    assert desc["critic_source"] == "injected"
    assert desc["detected"] == 1 and desc["certified"] >= 1

    assert out["grade"] == "open" and out["passed"] is False
    soft = [f for f in out["findings"] if f["outcome"] == "softlock"
            and f["tier"] == "descent"]
    assert soft, "the injected descent prefix must certify through the tree oracle"
    f = soft[0]
    assert f["hard"] is True and f["family"] == "policy_descent+tree_refute"

    prov = f["reproducer"]["provenance"]
    assert prov["oracle"] == "policy_descent+tree_refute"
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


def test_descent_leads_when_it_is_the_only_smart_tier():
    # With ONLY descent injected (no iv), the descent finding leads the ladder.
    out = g4.run_g4(SOFTLOCK, _softlock_report(), engine="py",
                    world_factory=factory(), tiers=(0,),
                    descent_candidates=[["run", "run", "run", "run"]], **DESC, **SMALL)
    assert out["findings"][0]["tier"] == "descent"


def test_inverse_value_still_leads_over_descent():
    # When BOTH smart tiers certify, S1 (inverse_value) leads S1.5 (descent) in the list.
    out = g4.run_g4(SOFTLOCK, _softlock_report(), engine="py",
                    world_factory=factory(), tiers=(0,),
                    iv_candidates=[["run", "run", "run", "run"]],
                    descent_candidates=[["run", "run", "run", "run"]], **DESC, **SMALL)
    tiers = [f["tier"] for f in out["findings"]
             if f["outcome"] == "softlock" and f["tier"] in ("inverse_value", "descent")]
    assert tiers[0] == "inverse_value"
    assert "descent" in tiers


# ====================================================================== #
# A refutable (benign) candidate is DETECTED but NOT certified -> no finding
# ====================================================================== #
def test_injected_control_candidate_is_refuted_not_certified():
    rpt = _report(["push"] * 8, 8, checkpoints={"halfway": 4})
    out = g4.run_g4(CONTROL, rpt, engine="py", world_factory=factory(), tiers=(0,),
                    descent_candidates=[["push", "coast"]], **DESC, **SMALL)
    desc = out["descent"]
    assert desc["status"] == "run"
    assert desc["detected"] == 1 and desc["certified"] == 0
    assert not [f for f in out["findings"] if f["tier"] == "descent"]
    assert all(f["tier"] != "descent" for f in out["hard_findings"])


# ====================================================================== #
# Model-gating: with NO model / critic / candidates the tier is a NO-OP
# ====================================================================== #
def test_descent_ladder_is_a_noop_without_a_model():
    rpt = _report(["push"] * 8, 8, checkpoints={"halfway": 4})
    args = dict(engine="py", world_factory=factory(), tiers=(0,), **SMALL)

    base = g4.run_g4(CONTROL, rpt, **args)                      # no descent args
    assert base["descent"]["status"] == "skipped_no_model"
    assert base["descent"]["findings"] == []
    assert base["descent"]["certified"] == 0
    assert all(f["tier"] != "descent" for f in base["findings"])

    # A benign injected candidate turns the tier on but changes nothing (refuted).
    withd = g4.run_g4(CONTROL, rpt, descent_candidates=[["push", "coast"]],
                      **DESC, **args)
    assert withd["descent"]["status"] == "run"
    assert base["findings"] == withd["findings"]
    assert base["grade"] == withd["grade"]


def test_descent_does_not_perturb_the_inverse_value_tests():
    # Injecting ONLY iv_candidates must leave the descent tier skipped (its dedicated
    # seams are the only trigger) — so the S1 tests keep their exact ladder.
    rpt = _report(["push"] * 8, 8, checkpoints={"halfway": 4})
    out = g4.run_g4(CONTROL, rpt, engine="py", world_factory=factory(), tiers=(0,),
                    iv_candidates=[["push", "coast"]], stale_H=30, stale_budget=2500,
                    top_m=6, **SMALL)
    assert out["descent"]["status"] == "skipped_no_model"
    assert not [f for f in out["findings"] if f["tier"] == "descent"]


# ====================================================================== #
# Repair report prefers the finding-level (checkpoint-naming) hint
# ====================================================================== #
def test_to_repair_report_uses_descent_hint():
    out = g4.run_g4(SOFTLOCK, _softlock_report(), engine="py",
                    world_factory=factory(), tiers=(0,),
                    descent_candidates=[["run", "run", "run", "run"]], **DESC, **SMALL)
    f = [f for f in out["findings"] if f["tier"] == "descent"][0]
    rr = g4.to_repair_report(f)
    assert rr["failure_class"] == "G4_FINDING" and rr["outcome"] == "softlock"
    assert rr["hint"] == f["repair_hint"]      # specific hint wins over the generic map
    assert "lip" in rr["hint"]
