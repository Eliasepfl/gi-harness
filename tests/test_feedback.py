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


def frozen_state(pos, *, vel=(0.0, 0.0), name="player", nearby=None, enclosing=None,
                 ticks=42, last_cp="lip", dim=2):
    """A fabricated frozen_state block (the ENGINE-TRUTH pocket a certified-softlock
    finding now carries) for exercising the directive renderer without an engine."""
    return {
        "controlled": {"name": name, "pos": list(pos), "vel": list(vel)},
        "nearby": (nearby if nearby is not None
                   else [{"name": "goal", "pos": [620.0, 260.0], "dist": 260.0}]),
        "ticks_elapsed": ticks,
        "last_latched_checkpoint": last_cp,
        "dimension": dim,
        "enclosing": enclosing or [],
    }


def test_g4_softlock_renders_engine_frozen_state():
    """The certified-softlock directive raises the ENGINE FACTS (Elias): the frozen
    position + velocity, the named nearby/enclosing bodies, the ticks after the last
    checkpoint, and that the solver PROVED no continuation wins under budget."""
    repro = {"seed": 3, "action_plan": {"kind": "sequence", "sequence": ["up", "up", "left"]},
             "provenance": {"oracle": "inverse_value+tree_refute",
                            "subtree_status": "all_terminal", "budget": 4000}}
    fs = frozen_state([380.0, 400.0], vel=[0.0, 0.0],
                      nearby=[{"name": "goal", "pos": [620.0, 260.0], "dist": 260.0}],
                      enclosing=[{"name": "wall_left",
                                  "aabb": [[260.0, 240.0], [280.0, 560.0]]}],
                      ticks=57, last_cp="over_pit")
    ds = F.compile_directives({"g4": g4(
        g4_finding("softlock", detail="prefix soft-locks the game",
                   reproducer=repro, frozen_state=fs))})
    assert sources(ds) == ["softlock"]
    text = ds[0].text
    assert "SOFTLOCK" in text.upper()
    assert "380" in text and "400" in text                 # the frozen position
    assert "goal" in text                                  # a nearby body name
    assert "wall_left" in text                             # the enclosing geometry name
    assert "over_pit" in text                              # the last checkpoint before the freeze
    assert "57" in text                                    # ticks after that checkpoint
    assert "4000" in text                                  # the solver budget
    assert "PROVED" in text.upper() or "no continuation" in text.lower()
    # the block is carried through on the directive detail (for downstream consumers).
    assert ds[0].detail.get("frozen_state") == fs


def test_g4_softlock_fingerprint_ignores_frozen_coordinates():
    """The dedup fingerprint keys on the DEFECT identity only — two softlocks with
    DIFFERENT frozen coordinates collapse to the SAME fingerprint (the convergence guard
    must not be defeated by volatile positions), yet the rendered text still differs."""
    repro = {"seed": 1, "action_plan": {"kind": "sequence", "sequence": ["right"] * 4},
             "provenance": {"oracle": "tree_refute", "subtree_status": "all_terminal"}}
    d1 = F.compile_directives({"g4": g4(g4_finding(
        "softlock", detail="prefix soft-locks", reproducer=repro,
        frozen_state=frozen_state([120.0, 340.0])))})[0]
    d2 = F.compile_directives({"g4": g4(g4_finding(
        "softlock", detail="prefix soft-locks", reproducer=repro,
        frozen_state=frozen_state([500.0, 120.0], last_cp="crossed")))})[0]
    assert d1.fingerprint == d2.fingerprint                 # same defect -> same id
    assert d1.text != d2.text                               # but the engine facts differ


def test_g4_softlock_without_frozen_state_still_renders():
    # An un-enriched softlock finding (older tier / degraded snapshot) still compiles —
    # the engine-facts clause is simply omitted, never a crash.
    repro = {"seed": 3, "action_plan": {"kind": "sequence", "sequence": ["up", "up", "left"]},
             "provenance": {"oracle": "tree_refute", "subtree_status": "all_terminal"}}
    ds = F.compile_directives({"g4": g4(
        g4_finding("softlock", detail="prefix soft-locks the game", reproducer=repro))})
    assert sources(ds) == ["softlock"]
    assert "SOFTLOCK" in ds[0].text.upper() and "length 3" in ds[0].text


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
# PRESSURE (WAVE 1 failure-witness gate) taxonomy rows
# ======================================================================== #
def pressure(outcome, *, constant_false=False, detail="", evidence=None, reproducer=None):
    return {"outcome": outcome, "constant_false": constant_false, "detail": detail,
            "evidence": evidence or {}, "reproducer": reproducer or {}}


def test_pressure_no_pressure_directive():
    ds = F.compile_directives({"pressure": pressure(
        "no_pressure", constant_false=True, detail="is_failure() is hardcoded false")})
    assert sources(ds) == ["no_pressure"]
    assert ds[0].origin == "pressure"
    assert "CANNOT BE LOST" in ds[0].text.upper()
    assert ds[0].detail["constant_false"] is True


def test_pressure_failure_unreachable_directive():
    ds = F.compile_directives({"pressure": pressure(
        "failure_unreachable", detail="no rollout ever lost",
        evidence={"n_plans": 28, "n_failed": 0})})
    assert sources(ds) == ["failure_unreachable"]
    assert "UNREACHABLE FAILURE" in ds[0].text.upper()


def test_pressure_has_pressure_yields_no_directive():
    # A reachable failure was witnessed -> healthy, no repair directive.
    assert F.compile_directives({"pressure": pressure(
        "has_pressure", reproducer={"seed": 0, "actions": ["down"], "ticks": 4})}) == []


def test_pressure_empty_yields_no_directive():
    assert F.compile_directives({"pressure": {}}) == []
    assert F.compile_directives({}) == []            # no pressure key at all


def test_pressure_fingerprint_stable_and_distinct():
    a = F.compile_directives({"pressure": pressure("no_pressure", constant_false=True)})[0]
    b = F.compile_directives({"pressure": pressure("no_pressure", constant_false=True)})[0]
    assert a.fingerprint == b.fingerprint                 # same defect -> same id
    c = F.compile_directives({"pressure": pressure("failure_unreachable")})[0]
    assert c.fingerprint != a.fingerprint                 # distinct row -> distinct id


def test_pressure_finding_extracts_from_verify_report():
    finding = pressure("no_pressure", constant_false=True, detail="hardcoded false")
    report = {"layers": {"G3_solve": {"checks": {
        "failure_witness": {"pass": True, "finding": finding}}}}}
    assert F.pressure_finding(report) == finding
    # No gate ran (non-gdscript / rejected earlier) -> {}.
    assert F.pressure_finding({"layers": {"G3_solve": {"checks": {}}}}) == {}
    assert F.pressure_finding({}) == {}
    # End-to-end: extracted finding compiles to the directive.
    assert F.compile_directives({"pressure": F.pressure_finding(report)})[0].source == "no_pressure"


# ======================================================================== #
# DEAD SPACE (WAVE 2 proportion gate) taxonomy row
# ======================================================================== #
def dead_space(outcome, *, linear_ratio=8.0, measure_ratio=64.0, threshold=5.0, dims=2,
               detail=""):
    return {"outcome": outcome, "linear_ratio": linear_ratio, "measure_ratio": measure_ratio,
            "threshold": threshold, "dims": dims, "detail": detail}


def test_dead_space_directive():
    ds = F.compile_directives({"dead_space": dead_space(
        "dead_space", linear_ratio=8.2, detail="the playfield is ~8.2x larger per axis")})
    assert sources(ds) == ["dead_space"]
    assert ds[0].origin == "proportion"
    assert "DEAD SPACE" in ds[0].text.upper() and "8.2x" in ds[0].text
    assert ds[0].detail["linear_ratio"] == 8.2


def test_dead_space_is_difficulty_severity():
    # An over-empty world still CERTIFIES -> a DIFFICULTY-tier polish, not a defect.
    ds = F.compile_directives({"dead_space": dead_space("dead_space")})
    assert ds[0].severity == F.DIFFICULTY
    assert ds[0].to_dict()["severity"] == F.DIFFICULTY


def test_dead_space_proportioned_yields_no_directive():
    assert F.compile_directives({"dead_space": dead_space("proportioned")}) == []
    assert F.compile_directives({"dead_space": {}}) == []
    assert F.compile_directives({}) == []            # no dead_space key at all


def test_dead_space_fingerprint_stable():
    a = F.compile_directives({"dead_space": dead_space("dead_space", linear_ratio=8.0)})[0]
    b = F.compile_directives({"dead_space": dead_space("dead_space", linear_ratio=12.0)})[0]
    assert a.fingerprint == b.fingerprint             # same defect (row) -> same id


def test_dead_space_finding_extracts_from_verify_report():
    finding = dead_space("dead_space", detail="the playfield is ~7x larger per axis")
    # The gdscript lane stashes it top-level only when flagged (cf. runtime_error).
    report = {"dead_space": finding}
    assert F.dead_space_finding(report) == finding
    assert F.dead_space_finding({}) == {}             # proportioned / not flagged -> {}
    # End-to-end: extracted finding compiles to the directive.
    assert F.compile_directives({"dead_space": F.dead_space_finding(report)})[0].source \
        == "dead_space"


def test_dead_space_3d_directive_carries_dims():
    ds = F.compile_directives({"dead_space": dead_space(
        "dead_space", dims=3, measure_ratio=272.0, linear_ratio=6.5)})
    assert ds[0].detail["dims"] == 3 and ds[0].detail["measure_ratio"] == 272.0


# ======================================================================== #
# Combined + fingerprints
# ======================================================================== #
def test_combined_g4_first_then_g3():
    o = {"g4": g4(g4_finding("broken_gating", evidence={"skipped_checkpoints": ["k"]})),
         "g3_prime": g3(latch={"m1": 1.0, "m2": 0.9, "m3": 0.0})}
    ds = F.compile_directives(o)
    assert sources(ds) == ["broken_gating", "g3_plateau"]   # G4 defects first


def test_combined_g4_pressure_then_g3_order():
    o = {"g4": g4(g4_finding("broken_gating", evidence={"skipped_checkpoints": ["k"]})),
         "pressure": pressure("no_pressure", constant_false=True),
         "g3_prime": g3(latch={"m1": 1.0, "m2": 0.9, "m3": 0.0})}
    ds = F.compile_directives(o)
    assert sources(ds) == ["broken_gating", "no_pressure", "g3_plateau"]  # G4 -> pressure -> G3'


def test_combined_pressure_dead_space_g3_order():
    o = {"pressure": pressure("no_pressure", constant_false=True),
         "dead_space": dead_space("dead_space"),
         "g3_prime": g3(latch={"m1": 1.0, "m2": 0.9, "m3": 0.0})}
    ds = F.compile_directives(o)
    assert sources(ds) == ["no_pressure", "dead_space", "g3_plateau"]  # pressure -> space -> G3'


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
# Directive SEVERITY tiers (2026-07-15 harden wave): DEFECT vs DIFFICULTY.
# A DEFECT is a proof-carrying brokenness worth the full repair budget; a DIFFICULTY
# finding (the two G3' learnability-curve rows) is hard-to-learn, not broken — a nudge only.
# ======================================================================== #
def test_severity_of_pure_mapping():
    assert F.severity_of("g3_plateau") == F.DIFFICULTY
    assert F.severity_of("g3_difficulty") == F.DIFFICULTY
    assert F.severity_of("dead_space") == F.DIFFICULTY   # WAVE-2 proportion polish
    for defect in ("g3_unsolvable", "single_action_win", "broken_gating", "softlock",
                   "no_pressure", "failure_unreachable", "stuck", "anything_else"):
        assert F.severity_of(defect) == F.DEFECT
    assert F.DIFFICULTY_SOURCES == frozenset({"g3_plateau", "g3_difficulty", "dead_space"})
    assert F.DEFECT == "defect" and F.DIFFICULTY == "difficulty"


def test_severity_on_compiled_g3_rows():
    # plateau + stall-at-first + all-latched-never-wins are the SOFT difficulty rows...
    plateau = F.compile_directives({"g3_prime": g3(latch={"m1": 1.0, "m2": 0.9, "m3": 0.05})})
    assert plateau[0].source == "g3_plateau" and plateau[0].severity == F.DIFFICULTY
    stall = F.compile_directives({"g3_prime": g3(latch={"m1": 0.2, "m2": 0.0, "m3": 0.0})})
    assert stall[0].source == "g3_plateau" and stall[0].severity == F.DIFFICULTY
    diff = F.compile_directives({"g3_prime": g3(latch={"m1": 1.0, "m2": 1.0, "m3": 1.0},
                                                sr=0.0, greedy=0.0)})
    assert diff[0].source == "g3_difficulty" and diff[0].severity == F.DIFFICULTY
    # ...but NOTHING-latched is a broken game -> DEFECT, worth the full budget.
    uns = F.compile_directives({"g3_prime": g3(latch={"m1": 0.0, "m2": 0.0, "m3": 0.0})})
    assert uns[0].source == "g3_unsolvable" and uns[0].severity == F.DEFECT


def test_severity_on_compiled_g4_and_pressure_rows_is_defect():
    sa = F.compile_directives({"g4": g4(
        g4_finding("single_action_win", hard=False, action="right", ticks=9))})
    bg = F.compile_directives({"g4": g4(
        g4_finding("broken_gating", evidence={"skipped_checkpoints": ["k"]}))})
    sl = F.compile_directives({"g4": g4(
        g4_finding("softlock", detail="dead end", reproducer={"seed": 1}))})
    npr = F.compile_directives({"pressure": pressure("no_pressure", constant_false=True)})
    fur = F.compile_directives({"pressure": pressure("failure_unreachable")})
    for ds in (sa, bg, sl, npr, fur):
        assert ds[0].severity == F.DEFECT


def test_severity_round_trips_through_to_dict():
    d = F.compile_directives({"g3_prime": g3(latch={"m1": 1.0, "m2": 0.9, "m3": 0.05})})[0]
    assert d.to_dict()["severity"] == F.DIFFICULTY
    d2 = F.compile_directives({"g4": g4(
        g4_finding("single_action_win", hard=False, action="up", ticks=3))})[0]
    assert d2.to_dict()["severity"] == F.DEFECT


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
