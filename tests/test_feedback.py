"""Tests for the FEEDBACK COMPILER (harness.gen.feedback) + the g3_prime signals it
reads. Pure/offline: no torch, no network, no Godot — every oracle result dict is
FABRICATED to exercise one taxonomy row (including every no-directive row).

Planks:
  * the full compile_directives mapping table (G3' + G4 rows, incl. the no-directive rows);
  * checkpoint-pair naming correctness from per-checkpoint latch data;
  * still_improving / continue_training surfacing from fabricated curves;
  * fingerprint stability (same defect -> same id) for the convergence guard.
"""
from __future__ import annotations

from harness.gen import feedback as F
from harness.rl import certify as C


# ======================================================================== #
# Fixture builders
# ======================================================================== #
def g3(*, still_improving=False, learnable=False, cp_keys=("m1", "m2", "m3"),
       latch=None, sr=0.0, greedy=0.0, budget=1_000_000, n_eval=32):
    keys = list(cp_keys)
    latch = latch if latch is not None else {k: 0.0 for k in keys}
    return {
        "still_improving": still_improving,
        "learnable": learnable,
        "checkpoint_keys": keys,
        "per_checkpoint_latch_rate": dict(latch),
        "stochastic_success_rate": sr,
        "final_success_rate": greedy,
        "budget_steps": budget,
        "n_eval": n_eval,
    }


def g4_finding(outcome, **extra):
    base = {"outcome": outcome, "tier": 0, "family": "fuzz", "hard": True,
            "detail": f"{outcome} detail", "reproducer": {"seed": 0}, "evidence": {}}
    base.update(extra)
    return base


def g4(*findings):
    hard = any(f.get("outcome") in F.G4_DIRECTIVE_OUTCOMES for f in findings)
    return {"schema": "g4_report/v1", "grade": "open" if hard else "hardened",
            "findings": list(findings)}


def sources(directives):
    return [d.source for d in directives]


# ======================================================================== #
# G3' taxonomy rows
# ======================================================================== #
def test_g3_still_improving_yields_no_directive_but_continue_training():
    o = {"g3_prime": g3(still_improving=True, learnable=False,
                        latch={"m1": 0.0, "m2": 0.0, "m3": 0.0})}
    assert F.compile_directives(o) == []
    assert F.continue_training(o) is True


def test_g3_solved_yields_no_directive():
    o = {"g3_prime": g3(learnable=True, sr=0.8,
                        latch={"m1": 1.0, "m2": 1.0, "m3": 1.0})}
    assert F.compile_directives(o) == []
    assert F.continue_training(o) is False   # already solved -> no continue signal


def test_g3_nothing_latched_is_unsolvable():
    o = {"g3_prime": g3(latch={"m1": 0.0, "m2": 0.0, "m3": 0.0})}
    ds = F.compile_directives(o)
    assert sources(ds) == ["g3_unsolvable"]
    assert ds[0].checkpoint_keys == ("m1",)          # names the first objective
    assert "UNSOLVABLE" in ds[0].text


def test_g3_partial_latch_is_plateau_pair():
    o = {"g3_prime": g3(latch={"m1": 1.0, "m2": 0.9, "m3": 0.05})}
    ds = F.compile_directives(o)
    assert sources(ds) == ["g3_plateau"]
    # last reliably-latched (m2) + first never/rarely-latched (m3)
    assert ds[0].checkpoint_keys == ("m2", "m3")
    assert "m2" in ds[0].text and "m3" in ds[0].text


def test_g3_stalls_at_first_objective():
    o = {"g3_prime": g3(latch={"m1": 0.2, "m2": 0.0, "m3": 0.0})}
    ds = F.compile_directives(o)
    assert sources(ds) == ["g3_plateau"]
    assert ds[0].checkpoint_keys == ("m1",)          # no reliable stage -> the first
    assert "FIRST" in ds[0].text.upper()


def test_g3_reaches_all_but_never_wins_is_difficulty():
    o = {"g3_prime": g3(latch={"m1": 1.0, "m2": 1.0, "m3": 1.0}, sr=0.0, greedy=0.0)}
    ds = F.compile_directives(o)
    assert sources(ds) == ["g3_difficulty"]
    assert ds[0].checkpoint_keys == ("m3",)
    assert "NEVER WINS" in ds[0].text.upper()


def test_g3_all_latched_with_some_wins_is_not_a_defect():
    # every milestone reached AND the agent sometimes wins (but < learnable): a
    # genuinely hard-but-learnable game, not a harden defect -> NO directive.
    o = {"g3_prime": g3(learnable=False, latch={"m1": 1.0, "m2": 1.0, "m3": 1.0},
                        sr=0.3, greedy=0.0)}
    assert F.compile_directives(o) == []


# ======================================================================== #
# G4 taxonomy rows
# ======================================================================== #
def test_g4_single_action_win_directive():
    ds = F.compile_directives({"g4": g4(
        g4_finding("single_action_win", hard=False, action="right", ticks=37))})
    assert sources(ds) == ["single_action_win"]
    assert "right" in ds[0].text and "ONE ACTION" in ds[0].text.upper()


def test_g4_broken_gating_names_skipped_checkpoint():
    ds = F.compile_directives({"g4": g4(
        g4_finding("broken_gating", evidence={"skipped_checkpoints": ["got_key"]}))})
    assert sources(ds) == ["broken_gating"]
    assert ds[0].checkpoint_keys == ("got_key",)
    assert "got_key" in ds[0].text and "WITHOUT" in ds[0].text.upper()


def test_g4_softlock_quotes_reproducer():
    repro = {"seed": 3, "action_plan": {"kind": "sequence", "sequence": ["up", "up", "left"]},
             "provenance": {"oracle": "tree_refute", "subtree_status": "all_terminal"}}
    ds = F.compile_directives({"g4": g4(
        g4_finding("softlock", detail="prefix soft-locks the game", reproducer=repro))})
    assert sources(ds) == ["softlock"]
    assert "SOFTLOCK" in ds[0].text.upper()
    assert "length 3" in ds[0].text            # the frozen-state reproducer summary


def test_g4_stuck_is_informational():
    """Unconfirmed heuristic 'stuck' compiles NO directive (first harden wave:
    fuzz-idleness looks identical in any game — an unfixable accusation). Only
    the tree-refutation-CERTIFIED `softlock` class earns a repair round."""
    ds = F.compile_directives({"g4": g4(g4_finding("stuck", hard=False))})
    assert ds == []


def test_g4_informational_findings_yield_no_directive():
    for outcome in ("shortcut_beats_witness", "escape", "nan", "unintended_success",
                    "intended_success", "nothing"):
        ds = F.compile_directives({"g4": g4(g4_finding(outcome))})
        assert ds == [], f"{outcome} must NOT compile to a directive"


def test_g4_duplicate_findings_dedup_to_one_directive():
    ds = F.compile_directives({"g4": g4(
        g4_finding("single_action_win", hard=False, action="right", ticks=30),
        g4_finding("single_action_win", hard=False, action="right", ticks=31))})
    assert sources(ds) == ["single_action_win"]


# ======================================================================== #
# Combined + fingerprints
# ======================================================================== #
def test_combined_g4_first_then_g3():
    o = {"g4": g4(g4_finding("broken_gating", evidence={"skipped_checkpoints": ["k"]})),
         "g3_prime": g3(latch={"m1": 1.0, "m2": 0.9, "m3": 0.0})}
    ds = F.compile_directives(o)
    assert sources(ds) == ["broken_gating", "g3_plateau"]   # G4 defects first


def test_empty_oracle_results():
    assert F.compile_directives({}) == []
    assert F.compile_directives(None) == []
    assert F.continue_training({}) is False


def test_fingerprint_is_stable_across_calls_for_same_defect():
    o = {"g3_prime": g3(latch={"m1": 1.0, "m2": 0.9, "m3": 0.05})}
    a = F.compile_directives(o)[0].fingerprint
    b = F.compile_directives(o)[0].fingerprint
    assert a == b
    # different stall boundary -> different fingerprint
    o2 = {"g3_prime": g3(latch={"m1": 0.9, "m2": 0.0, "m3": 0.0})}
    assert F.compile_directives(o2)[0].fingerprint != a


def test_single_action_fingerprint_ignores_volatile_action():
    # a repeat single-action win by a DIFFERENT action still collapses to one id, so
    # the convergence guard treats "still single-action-winnable" as the same defect.
    a = F.compile_directives({"g4": g4(g4_finding(
        "single_action_win", hard=False, action="right", ticks=30))})[0].fingerprint
    b = F.compile_directives({"g4": g4(g4_finding(
        "single_action_win", hard=False, action="up", ticks=44))})[0].fingerprint
    assert a == b


# ======================================================================== #
# checkpoint-pair localiser (direct)
# ======================================================================== #
def test_checkpoint_pair_monotone():
    keys = ["a", "b", "c", "d"]
    assert F._checkpoint_pair(keys, {"a": 1.0, "b": 1.0, "c": 0.1, "d": 0.0}) == ("b", "c")


def test_checkpoint_pair_all_reliable():
    keys = ["a", "b"]
    assert F._checkpoint_pair(keys, {"a": 1.0, "b": 1.0}) == ("b", None)


def test_checkpoint_pair_none_reliable():
    keys = ["a", "b"]
    assert F._checkpoint_pair(keys, {"a": 0.1, "b": 0.0}) == (None, "a")


# ======================================================================== #
# still_improving surfacing (fabricated curves) + per-checkpoint rate helper
# ======================================================================== #
def test_still_improving_from_climbing_curve():
    climbing = [round(0.1 * i, 3) for i in range(30)]
    assert C.still_improving_from_curve(climbing, patience=40, window=10,
                                        min_delta=0.05) is True


def test_still_improving_from_flat_curve_is_false():
    flat = [1.0] * 60
    assert C.still_improving_from_curve(flat, patience=40, window=10,
                                        min_delta=0.05) is False


def test_still_improving_prefers_plateau_stopped_flag():
    assert C._still_improving({"plateau_stopped": True}) is False
    assert C._still_improving({"plateau_stopped": False}) is True


def test_still_improving_budget_exhausted_is_true():
    # ran to the full budget (no early stop at all) -> was still climbing.
    assert C._still_improving({"stopped_early": False}) is True


def test_still_improving_wallclock_cut_reconstructs_from_curve():
    hp = {"patience": 40, "window": 10, "min_delta": 0.05}
    climbing = [round(0.1 * i, 3) for i in range(30)]
    flat = [1.0] * 60
    # stopped_early True but no plateau flag -> reconstruct: climbing == still improving.
    assert C._still_improving({"stopped_early": True, "hp": hp,
                               "curve_return": climbing}) is True
    assert C._still_improving({"stopped_early": True, "hp": hp,
                               "curve_return": flat}) is False


def test_per_checkpoint_latch_rate():
    eps = [{"latched": {"m1": 5, "m2": None}},
           {"latched": {"m1": 3, "m2": 9}},
           {"latched": {"m1": None, "m2": None}}]
    rates = C._per_checkpoint_latch_rate(eps, ["m1", "m2"])
    assert rates == {"m1": round(2 / 3, 3), "m2": round(1 / 3, 3)}


def test_per_checkpoint_latch_rate_empty():
    assert C._per_checkpoint_latch_rate([], ["m1"]) == {"m1": 0.0}
    assert C._per_checkpoint_latch_rate([{"latched": {}}], []) == {}
