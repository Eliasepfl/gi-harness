"""THE ANTI-LAUNDERING GATE — no directive the loop can emit may ask the model to
reduce its ambition (harness.repair_language).

Context (2026-07-16 full-library audit): 76% of the certified library had collapsed onto
ONE archetype — pilot a body into a zone, verbs = thrust + brake. The cause was not the
model's taste, it was OUR OWN repair directives: "make the first stage easier", "make the
goal easier to reach", "fix or remove them". A language model obeys "easier" the only way
it can — by DEMOLISHING: unlocking every door, collapsing a dwell-timer alarm into
disarm-on-touch, lining every objective on one axis. This module pins the rule that
replaced them: MAKE IT REACHABLE, NOT SHALLOWER.

The gate SWEEPS every taxonomy row rather than spot-checking a few strings, so a NEW
directive added later cannot quietly reintroduce the pathology — it must pass this too.

WHY THE BANNED LIST IS SHAPED THE WAY IT IS
-------------------------------------------
The naive gate — "no directive may contain the substring 'remove'" — is WRONG, and
knowing why is the whole point of this fix. The reference-GOOD directive (`g3_unsolvable`)
reads "... WITHOUT removing the goal", and the softlock directive legitimately says
"remove the one-way trap" (deleting a DEFECT, not a mechanic). A substring ban would flag
both, so the real gate distinguishes:

  * BANNED unconditionally: the simplification imperatives ("easier", "simplify",
    "simpler"). No legitimate defect fix needs them — they name the disease.
  * BANNED as a phrasing: removal offered as an OPTION ("fix or remove them"). Deletion
    is always the cheaper branch, so offering it IS choosing it.
  * ALLOWED: removal as a PROHIBITION ("WITHOUT removing the goal", "do NOT remove
    locks") and removal of a genuine DEFECT (a one-way trap, trap geometry).
"""
from __future__ import annotations

import pytest

from harness import repair_language as RL
from harness.gen import curriculum as C
from harness.gen import feedback as F
from harness.gen import gamegen as G
from harness.verify import gameverify as GV


# The simplification imperatives. These words never appear in an honest defect fix; they
# only ever appear when we are asking the model to make the GAME less than it was.
BANNED_WORDS = ("easier", "simplify", "simpler", "less ambitious", "dumb it down")

# Removal offered as an OPTION rather than forbidden as an outcome. `_hint_g3`'s old
# "dead milestone(s) ... — fix or remove them" is the archetype: the model always took
# the second branch, and a staged design lost a stage per repair round.
BANNED_PHRASES = ("or remove them", "or remove it", "fix or remove", "or delete them",
                  "or drop them", "consider removing")


def assert_not_laundering(text: str, label: str) -> None:
    """The gate itself: `text` may not ask the model to reduce its ambition."""
    low = text.lower()
    for word in BANNED_WORDS:
        assert word not in low, (
            f"{label} asks the model to simplify ({word!r}). Ambition laundering: say "
            f"what to make REACHABLE and name the local fix instead. Text: {text!r}")
    for phrase in BANNED_PHRASES:
        assert phrase not in low, (
            f"{label} offers removal as an option ({phrase!r}) — the model always takes "
            f"the cheaper branch and deletes the mechanic. Text: {text!r}")


# ======================================================================== #
# The gate must be able to FAIL (a green gate that catches nothing is worthless)
# ======================================================================== #
# The VERBATIM directives the loop shipped before 2026-07-16. Each one demonstrably
# taught the model to demolish; the gate is only meaningful if it rejects all of them.
HISTORICAL_LAUNDERERS = [
    ("gameverify._hint_unsolved",
     "no episode reached the first milestone 'got_key' in 40 episodes — make the first "
     "stage easier"),
    ("gameverify._hint_unsolved",
     "no random rollout reached success in 40 episodes x 600 ticks — make the goal "
     "easier to reach"),
    ("gameverify._hint_g3",
     "dead milestone(s) never latched on the winning path: reached_sky — fix or remove "
     "them"),
    ("gamegen._UNSOLVED_HINT",
     "no random rollout reached success - make the goal easier to reach or actions more "
     "effective"),
]


@pytest.mark.parametrize("origin,old_text", HISTORICAL_LAUNDERERS)
def test_gate_rejects_the_directives_that_caused_the_monoculture(origin, old_text):
    """Each of these shipped, and each taught the model to flatten the game. If the gate
    ever stops rejecting them it has been defanged."""
    with pytest.raises(AssertionError):
        assert_not_laundering(old_text, origin)


def test_gate_allows_removal_as_a_prohibition_and_as_a_defect_fix():
    """The distinction the naive substring ban gets wrong — and the reason this gate
    bans imperatives and offers, not the word "remove"."""
    # a PROHIBITION: the reference-good g3_unsolvable wording
    assert_not_laundering("bring the first objective within reach WITHOUT removing the "
                          "goal", "prohibition")
    # the shared clause forbidding demolition by name
    assert_not_laundering("do NOT remove locks, timers, stages or hazards", "clause")
    # removing a genuine DEFECT (not a mechanic): the softlock directive
    assert_not_laundering("remove the one-way trap, or add an escape/reset from the "
                          "dead end", "defect fix")


# ======================================================================== #
# The vocabulary itself
# ======================================================================== #
def test_the_shared_clauses_are_not_themselves_laundering():
    for name in ("PRINCIPLE", "PRESERVE_CLAUSE", "PRESERVE_SHORT", "REACHABILITY_FIXES",
                 "CONTAINMENT_REFRAME"):
        assert_not_laundering(getattr(RL, name), f"repair_language.{name}")


# ======================================================================== #
# REFRAME-ON-REPEAT vocabulary (2026-07-17 parser-friction, items 3 + 4)
# ======================================================================== #
def test_reframe_kind_classifies_only_the_two_eligible_families():
    # containment: named by a failed in_bounds/no_escape check OR by the hint text.
    assert RL.reframe_kind("ENV_ERROR", ["G1_rollout.no_escape"], "", None) == "containment"
    assert RL.reframe_kind("ENV_ERROR", [], "dynamic body out of bounds: puck",
                           None) == "containment"
    # last_mile: an UNSOLVED run that reaches milestones but stalls one step short.
    prog = {"reach_counts": {"m1": 40, "m2": 39}, "stuck_after": "m2"}
    assert RL.reframe_kind("UNSOLVED", [], "stuck between m2 and success", prog) == "last_mile"
    # NOT eligible: everything else keeps the pure "N identical -> stop" invariant.
    assert RL.reframe_kind("GOAL_ERROR", [], "success true at t=0", None) is None
    assert RL.reframe_kind("ENV_ERROR", [], "module failed to load", None) is None
    assert RL.reframe_kind("UNSOLVED", [], "0/40 reached success", None) is None   # no reach


def test_containment_reframe_changes_the_approach_and_preserves():
    c = RL.CONTAINMENT_REFRAME
    assert_not_laundering(c, "CONTAINMENT_REFRAME")
    assert "_physics_process" in c and "act()" in c        # the concrete clamp site
    assert "tunnel" in c.lower()                            # names WHY (mid-step overshoot)
    assert "keep the mechanic" in c.lower()                 # preserves the design


def test_last_mile_telemetry_carries_the_reach_numbers_targeted():
    prog = {"reach_counts": {"start": 360, "past_left": 360, "past_right": 359},
            "stuck_after": "past_right"}
    t = RL.last_mile_telemetry(prog)
    assert_not_laundering(t, "last_mile_telemetry")
    assert "Closest-approach telemetry" in t
    assert "'past_right' 359/360" in t                      # the exact reach numbers
    assert "'past_right' -> 'success'" in t                 # the single stuck step named
    assert "leave it untouched" in t                        # preserve the cleared stages
    # No reach data -> empty (fires ONLY on the last-mile family).
    assert RL.last_mile_telemetry(None) == ""
    assert RL.last_mile_telemetry({"reach_counts": {}, "stuck_after": None}) == ""


def test_reframe_clause_dispatches_by_kind():
    prog = {"reach_counts": {"m1": 10, "m2": 9}, "stuck_after": "m2"}
    assert RL.reframe_clause("containment", {}) == RL.CONTAINMENT_REFRAME
    assert "Closest-approach telemetry" in RL.reframe_clause("last_mile",
                                                             {"progress": prog})
    assert RL.reframe_clause("nope", {}) == ""


def test_preserve_clause_names_every_structure_the_model_must_keep():
    """The clause must forbid the SPECIFIC demolitions the audit caught, by name — a
    vague "keep it interesting" would not have saved the heist."""
    low = RL.PRESERVE_CLAUSE.lower()
    for structure in ("lock", "timer", "stage", "hazard", "gating"):
        assert structure in low, f"PRESERVE_CLAUSE must name {structure!r}"
    assert "worse outcome" in low       # collapsing is WORSE than the current failure
    assert "single repeated verb" in low


def test_reachability_fixes_are_actionable_not_a_scold():
    """"Make it reachable" alone would leave the model to guess, and its cheapest guess
    is demolition — the menu of local fixes is what makes the directive actionable."""
    low = RL.REACHABILITY_FIXES.lower()
    assert "placement" in low and "tolerance" in low and "forces" in low


# ======================================================================== #
# THE SWEEP — every directive the feedback compiler can emit
# ======================================================================== #
def _every_directive():
    """One compiled directive per taxonomy row, labelled. Fabricated oracle dicts, so
    this stays pure/offline (no torch, no Godot, no network)."""
    rows = {
        "g3_unsolvable": {"g3_prime": _g3(latch={"m1": 0.0, "m2": 0.0, "m3": 0.0})},
        "g3_plateau": {"g3_prime": _g3(latch={"m1": 1.0, "m2": 0.9, "m3": 0.0})},
        "g3_plateau_first": {"g3_prime": _g3(latch={"m1": 0.2, "m2": 0.0, "m3": 0.0})},
        "g3_difficulty": {"g3_prime": _g3(latch={"m1": 1.0, "m2": 1.0, "m3": 1.0})},
        "single_action_win": {"g4": _g4("single_action_win", action="right", ticks=9)},
        "broken_gating": {"g4": _g4("broken_gating",
                                    evidence={"skipped_checkpoints": ["got_key"]})},
        "softlock": {"g4": _g4("softlock",
                               reproducer={"seed": 1, "action_plan": {"sequence": [1, 2]}})},
        "no_pressure": {"pressure": {"outcome": "no_pressure", "constant_false": True,
                                     "detail": "is_failure() returns false"}},
        "failure_unreachable": {"pressure": {"outcome": "failure_unreachable",
                                             "detail": "never fires"}},
        "dead_space": {"dead_space": {"outcome": "dead_space", "linear_ratio": 8.2,
                                      "detail": "the playfield is ~8.2x larger"}},
        "unanchored_milestone": {"anchoring": {"outcome": "unanchored", "milestones": [
            {"milestone": "reached_zone", "tick": 5, "distance": 440.0,
             "controlled": "ship", "nearest_body": "rock", "tol": 24}]}},
        "runtime_error": {"runtime_error": {"method": "act", "line": 6, "kind": "runtime",
                                            "message": "null 'position'"}},
    }
    for label, oracle in rows.items():
        for d in F.compile_directives(oracle):
            yield label, d


def _g3(*, latch):
    return {"still_improving": False, "learnable": False,
            "checkpoint_keys": list(latch), "per_checkpoint_latch_rate": dict(latch),
            "stochastic_success_rate": 0.0, "final_success_rate": 0.0,
            "budget_steps": 1_000_000, "n_eval": 32}


def _g4(outcome, **extra):
    f = {"outcome": outcome, "tier": 0, "detail": f"{outcome} detail",
         "reproducer": {"seed": 0}, "evidence": {}}
    f.update(extra)
    return {"findings": [f]}


def test_no_compiled_directive_asks_the_model_to_simplify():
    """THE GATE. Every row of the taxonomy, swept."""
    seen = set()
    for label, d in _every_directive():
        assert_not_laundering(d.text, f"directive {label} ({d.source})")
        seen.add(d.source)
    # the sweep must actually cover the taxonomy, or it proves nothing
    assert seen >= {"g3_unsolvable", "g3_plateau", "g3_difficulty", "single_action_win",
                    "broken_gating", "softlock", "no_pressure", "failure_unreachable",
                    "dead_space", "unanchored_milestone", "runtime_error"}


@pytest.mark.parametrize("label", ["g3_unsolvable", "g3_plateau", "g3_plateau_first",
                                   "g3_difficulty"])
def test_reachability_directives_carry_the_preserve_clause(label):
    """The REACHABILITY family — the rows that fire when the agent cannot get through —
    is exactly where laundering lived, so each must carry the preserve clause verbatim."""
    ds = [d for lb, d in _every_directive() if lb == label]
    assert ds, f"{label} compiled no directive"
    for d in ds:
        assert RL.PRESERVE_CLAUSE in d.text, f"{label} lost the preserve clause"


def test_reachability_directives_say_reachable():
    for label, d in _every_directive():
        if label.startswith("g3_"):
            assert "reachable" in d.text.lower() or "within reach" in d.text.lower(), \
                f"{label} must name the reachability fix: {d.text!r}"


# ======================================================================== #
# THE SWEEP — every hint the verify lane can emit
# ======================================================================== #
def test_unsolved_hints_are_reachability_fixes_not_simplifications():
    """`_hint_unsolved` produced the single most-cited laundering line in the audit:
    "no episode reached the first milestone 'X' ... — make the first stage easier"."""
    checks = {"episodes": {"run": 40}}
    variants = {
        "no progress at all": (checks, None),
        "nothing latched": (checks, {"reach_counts": {"m1": 0, "m2": 0},
                                     "stuck_after": None}),
        "stalled mid-run": (checks, {"reach_counts": {"m1": 34, "m2": 0},
                                     "stuck_after": "m1"}),
    }
    for label, (chk, prog) in variants.items():
        hint = GV._hint_unsolved(chk, prog)
        assert_not_laundering(hint, f"_hint_unsolved [{label}]")
        assert RL.PRESERVE_SHORT in hint, f"_hint_unsolved [{label}] lost the clause"


def test_dead_milestone_hint_keeps_the_milestone():
    """Was "fix or remove them" — the model removed them, every time."""
    hint = GV._hint_g3({"milestones_latched": {"pass": False, "dead": ["reached_sky"]}},
                       {})
    assert_not_laundering(hint, "_hint_g3 dead milestone")
    assert "reached_sky" in hint                  # still typed: names the offender
    assert "do not delete" in hint.lower()


def test_gamegen_unsolved_preamble_is_not_laundering():
    """`_UNSOLVED_HINT` leads EVERY UNSOLVED repair message — the most-fired directive
    in the loop. Was "make the goal easier to reach or actions more effective"."""
    assert_not_laundering(G._UNSOLVED_HINT, "gamegen._UNSOLVED_HINT")
    assert RL.PRESERVE_CLAUSE in G._UNSOLVED_HINT
    assert "no random rollout reached success" in G._UNSOLVED_HINT  # keeps the fact


# ======================================================================== #
# THE SWEEP — the curriculum difficulty directives
# ======================================================================== #
@pytest.mark.parametrize("grade", ["degenerate", "easy", "target", "hard",
                                   "not_learnable"])
def test_curriculum_directives_never_launder(grade):
    profile = {"grade": grade, "milestones": ["m1", "m2"],
               "rl": {"success_rate": 0.1, "steps_to_first_success": 100,
                      "stalling_milestone": "m2", "last_mastered_milestone": "m1"},
               "solver": {"witness_ticks": 40}}
    assert_not_laundering(C.directive(profile), f"curriculum directive [{grade}]")
