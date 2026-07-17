"""Shared REPAIR VOCABULARY — the wording every repair directive in the loop reuses.

Dependency-free BY DESIGN. Both lanes splice these clauses into the text they send the
model — the verify lane (`harness/verify/gameverify.py` hints) and the gen lane
(`harness/gen/feedback.py` directives, `gamegen`'s repair message, `curriculum`'s
difficulty directives) — and those two packages deliberately avoid module-level imports
of each other (every cross-package import in the tree is function-local, to dodge the
cycle). Living at the harness root and importing NOTHING from harness, this module can
be imported at module level from either side without reopening that cycle.

WHY THIS EXISTS (2026-07-16 ambition audit)
-------------------------------------------
A full-library audit found 76% of the certified library had collapsed onto ONE
archetype: pilot a body into a zone, verbs = thrust + brake. The dominant cause was NOT
the model's taste — it was OUR OWN repair directives. The loop told the model to "make
the first stage easier" / "make the goal easier to reach", and the model complied the
only way it can: it DEMOLISHED the mechanic. The documented case is a 2D heist whose
repair round instantly unlocked ALL doors, turned a dwell-timer alarm into
disarm-on-touch, and lined every objective on one axis so a single repeated verb solved
it. The game was "repaired" into the 76th body-into-zone game. Our own loop laundered
the ambition out.

The rule these constants encode:

    MAKE IT REACHABLE, NOT SHALLOWER.

An opening the agent cannot reach is a REACHABILITY defect — placement, forces,
tolerances, ordering — never a mandate to delete the design. The model must keep its
mechanic, its gating and its multi-stage structure, and fix what makes the opening
unreachable. The reference directive the loop already got RIGHT is
`feedback._compile_g3`'s ``g3_unsolvable``: "Make the opening playable — bring the first
objective within reach of the starting state and verify the ACTIONS actually move the
agent toward it — WITHOUT removing the goal. If nothing can be reached, the game is
broken, not merely hard." Every clause below mirrors that shape.

Vocabulary discipline (enforced by ``tests/test_repair_language.py``):
  * NEVER ask for "easier" / "simpler" — those words name the disease. Say what to make
    REACHABLE instead, and name the local fix that gets there.
  * Removal language may appear ONLY as a PROHIBITION ("WITHOUT removing", "do NOT
    remove") — never as an option on offer ("fix or remove them"), which reads to the
    model as permission to delete the mechanic.
  * Always name the CHEAP, LOCAL, structure-preserving fixes explicitly, so "reachable"
    lands as an actionable instruction rather than a scold.
"""

# The one-line principle. Short enough to head even a terse verify hint.
PRINCIPLE = "make it reachable, not shallower"

# The full preserve-the-design clause — spliced into the gen-lane DIRECTIVES
# (feedback.py / curriculum.py), which are prose blocks the revise model reads in full.
PRESERVE_CLAUSE = (
    "KEEP the mechanic, its gating and its stages: do NOT remove locks, timers, stages "
    "or hazards, do NOT unlock what the design gates, and do NOT line every objective "
    "up on one axis. Collapsing a multi-stage design into a single repeated verb is a "
    "WORSE outcome than the current failure."
)

# The terse twin — spliced into the verify-lane HINTS, which are one-line report fields
# (`report["hint"]`) riding inside a JSON blob, so they stay short.
PRESERVE_SHORT = (
    "keep the mechanic, its gating and every stage — removing locks/timers/stages/"
    "hazards, or collapsing the design into one repeated verb, is a worse outcome than "
    "this failure"
)

# The menu of LOCAL, structure-preserving fixes. This is what makes "reachable"
# ACTIONABLE: every item moves the opening within the player's reach without touching
# what the design gates. Named explicitly because "make it reachable" alone would leave
# the model to guess — and its cheapest guess is demolition.
REACHABILITY_FIXES = (
    "the starting placement, the forces the ACTIONS apply, the trigger tolerances, the "
    "ordering, or the first stage's distance"
)


# ============================================================================ #
# REFRAME-ON-REPEAT vocabulary (2026-07-17 parser-friction lever, items 3 + 4)
# ============================================================================ #
# When the SAME defect survives a repair unchanged, re-sending the identical hint is the
# waste the stall guard exists to stop. But two defect FAMILIES have a concrete alternate
# framing that is worth ONE more targeted attempt before conceding:
#
#   * CONTAINMENT / out-of-bounds escape — the standard "clamp the speed" hint did not
#     land; escalate to the exact APPROACH (clamp position AND velocity in
#     `_physics_process`, never in `act()`; thicken the walls against tunnelling).
#   * LAST-MILE solvability wall — the game reaches its milestones but stalls one step
#     short of the win; the solver already knows HOW CLOSE each episode got, so surface
#     that reach telemetry (which milestone, how many episodes, the single stuck step) so
#     the fix is TARGETED at the wall instead of guessed.
#
# Both stay inside the vocabulary discipline above: preserve the mechanic, name the local
# fix, never say "easier". The numbers a last-mile clause quotes are TEXT ONLY — they
# describe how close the run got, and must NEVER enter a dedup fingerprint (which keys on
# the DEFECT identity, not volatile telemetry), exactly like `feedback._frozen_state_facts`.

# The item-4 escalation: a DIFFERENT containment framing from the standard clamp hint, so
# the repeat attempt changes its approach rather than re-applying what already failed.
CONTAINMENT_REFRAME = (
    "Change the containment APPROACH (the earlier clamp hint did not land): clamp the "
    "controlled body's position AND its velocity every tick inside `_physics_process` — "
    "NEVER inside `act()`, which runs once per decision tick and cannot catch a mid-step "
    "overshoot — and seal the arena with STATIC-body walls thick enough that no single "
    "physics step can tunnel through them. Keep the mechanic and every stage; only stop "
    "the body from leaving the world."
)


def reframe_kind(failure_class, failing_checks, hint, progress):
    """Classify a STALLED verify report into a reframe family, or ``None``.

    Returns ``"containment"`` (item 4) for a body-out-of-bounds / world-escape defect,
    ``"last_mile"`` (item 3) for an UNSOLVED run that reaches milestones but stalls one
    step short, else ``None`` (every other defect keeps the pure "N identical -> stop"
    convergence invariant — no grace attempt is spent on it). Dependency-free and PURE:
    reads only the shapes the loop already has (`failure_class`, the failed-check ids, the
    one-line `hint`, the `progress` reach dict)."""
    checks = failing_checks or []
    hint_l = (hint or "").lower()
    if (any(("in_bounds" in c or "no_escape" in c or "containment" in c) for c in checks)
            or "out of bounds" in hint_l or "escaped the world" in hint_l):
        return "containment"
    if ((failure_class or "") == "UNSOLVED" and progress
            and progress.get("reach_counts") and progress.get("stuck_after") is not None):
        return "last_mile"
    return None


def last_mile_telemetry(progress):
    """Render the solver's closest-approach reach telemetry into ONE targeting clause.

    Uses ONLY the milestone reach counts the report already carries (`reach_counts` +
    `stuck_after` from the tree solver's `_progress`) — there is no geometric distance in
    the pipeline — so it surfaces the exact "how close did the best rollouts get" numbers:
    the full reach profile, the deepest milestone reached, and the single stuck step that
    is the whole remaining gap. Returns "" when no reach telemetry rode along. The counts
    are TEXT ONLY (they never enter a fingerprint)."""
    progress = progress or {}
    reach = progress.get("reach_counts") or {}
    stuck = progress.get("stuck_after")
    if not reach or stuck is None or stuck not in reach:
        return ""
    declared = list(reach)
    total = max(reach.values()) or 1        # ~= episodes run (most reach the first step)
    idx = declared.index(stuck)
    nxt = declared[idx + 1] if idx + 1 < len(declared) else "success"
    profile = ", ".join(f"'{k}' {reach[k]}/{total}" for k in declared)
    return (
        f"Closest-approach telemetry (solver reach profile over ~{total} episodes): "
        f"{profile}. The best rollouts get to '{stuck}' but almost never past it to "
        f"'{nxt}' — the ENTIRE remaining gap is the single '{stuck}' -> '{nxt}' step. "
        f"Aim the fix at THAT step's reachability alone (widen the gap, steady the "
        f"hazard, enlarge the target, or relax the timing at that step); every stage "
        f"through '{stuck}' is already reliably reached, so leave it untouched."
    )


def reframe_clause(kind, report):
    """Dispatch a reframe ``kind`` (from :func:`reframe_kind`) to its escalation clause,
    reading any telemetry off ``report``. "" for an unknown kind or missing telemetry."""
    if kind == "containment":
        return CONTAINMENT_REFRAME
    if kind == "last_mile":
        return last_mile_telemetry((report or {}).get("progress"))
    return ""
