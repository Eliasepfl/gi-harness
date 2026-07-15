---
id: one-shot-commit
kind: archetype
created_by: human-seed wave-1
run_id: reseed-2026-07-14
wave: 1
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
load_when: Concept phase — the game's soul is a SINGLE decisive strike, then freeze and hold the outcome
rationale: The one-shot-commit family (ScoreTheGoal bank-shot). Aim-and-commit under a one-chance rule, then stillness locks the result. A distinct decision shape: precision of a single action, not sustained control.
provenance: notes/engines/EXAMPLES_STRUCTURE_GUIDE.md §2/§4 (ScoreTheGoal one-shot commit); godotworld/SPEC.md §6 (impulse) + §7 (velocity_clamp) + §8 (contained, speed); combat/aim decomposition — knowledge derived from thedivergentai/gd-agentic-skills (LGPLv3), paraphrased.
---

# Archetype: one-shot-commit

The soul: line up one shot, commit it, and let physics carry the outcome — then
freeze so the result is locked. The skill is the AIM, not continuous piloting.
Objective flip ideas: score-the-goal → bank-shot into a pocket; putt → single
impulse to a target.

## Core loop

1. **Aim** — orient / position the body to set the launch line.
2. **Commit** — deliver ONE impulse (the shot); after this, steering is spent.
3. **Coast** — the body travels under momentum toward the target.
4. **Freeze** — clamp it to rest once it arrives (or after the shot window).
5. **Latch** — success reads pose AND stillness of the settled body in the target.

## Skill-chain of subgoals

- set up the shot (reach the aim state) → checkpoint `aimed`
- commit the strike → checkpoint `struck`
- body settles in the target → success

## DSL expression

- **Aim:** small `torque` / translational nudges to set the launch line (a bounded
  aiming budget), or a `when`-gated rotation.
- **The shot:** a single `impulse` — themed as one-chance by design (after the
  strike, further actions do little because the body is committed to its arc).
- **Freeze:** an `on_step` `velocity_clamp` (or a friction target) so the coasting
  body actually STOPS — otherwise it drifts past forever and success never latches.
- **Success (compound):** `contained(body, target_zone) and speed(body) < small` —
  the body came to rest INSIDE the target. Pose + stillness together is the compound
  latch; a bare `contacts(body, target)` would be a shortcut the attacker punishes.
- **Failure:** the body settles OUTSIDE / in a wrong pocket, or escapes.
- **Sensing:** a raycast fan to read the target bearing during aim.

## Structural sketch (high-level, no dimensions)

A bounded table with the controlled body (a puck / ball), possibly a static
bank/rail to bounce off, and a target pocket (`sensor` zone) placed so a direct line
is blocked — rewarding aim. Target and spawn re-rolled each reset. Checkpoints ladder
aimed → struck → settled; success on rest-in-target, failure on rest-outside or
escape. Table shape, rail placement, and pocket size are the model's to choose — make
the aim MATTER (a random shove shouldn't score, or agency collapses).
