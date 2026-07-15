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
