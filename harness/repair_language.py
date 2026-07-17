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
