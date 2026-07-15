---
id: hop-traverse
kind: archetype
created_by: human-seed wave-1
run_id: reseed-2026-07-14
wave: 1
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
load_when: Concept phase — the game's soul is hopping a grounded body across gaps to a landing pad
rationale: Precision-platforming traversal (JumperHard Beacon Hop, RobotVolleyball keep-it-over solo). Grounded-gated jumping is the motor skill; the finish is a contained landing. The classic ledge-hop done RIGHT (compound finish, not a bare zone touch).
provenance: notes/engines/EXAMPLES_STRUCTURE_GUIDE.md §2/§4 (JumperHard, RobotVolleyball); godotworld/SPEC.md §6 (grounded-gated impulse) + §8 (grounded, contained); platformer coyote/jump-buffer decomposition — knowledge derived from thedivergentai/gd-agentic-skills (LGPLv3), paraphrased.
---

# Archetype: hop-traverse

The soul: cross a series of surfaces by well-timed grounded jumps and stick a
landing. Objective flip ideas: ledge-hop → beacon hop to a pad; volley → keep a body
aloft toward a target. Note: our platforms are STATIC (no movers yet) — the
challenge is the jump timing and the landing, not dodging.

## Core loop

1. **Ground** — settle on the current surface (grounded true).
2. **Aim** — face / lean toward the next surface.
3. **Jump** — apply a grounded-gated impulse to clear the gap.
4. **Land** — touch down on the next surface without overshooting into the void.
5. **Finish** — reach and settle on the landing pad.

## Skill-chain of subgoals

- leave the start surface (first successful hop) → checkpoint `first_hop`
- reach the mid surface → checkpoint `midway`
- land on the pad → checkpoint `on_pad`
- settle on the pad → success

## DSL expression

- **Control:** a `when: "grounded(body)"`-gated `impulse` (the jump only fires from
  the ground — no mid-air double-jump, and this is also what makes it non-trivial).
  Horizontal aim via a small translational `force` or an angled impulse.
- **Grounded read:** `grounded(body)` = a non-sensor contact from below; the static
  surfaces provide it.
- **Finish:** `contained(body, pad_zone)` AND `grounded(body)` AND `speed(body) <
  small` — landed, still, on the pad. A `velocity_clamp` helps it settle.
- **Success (compound):** on-pad containment AND grounded AND stillness — never a
  bare pad touch (that's a single-action-win the gates punish).
- **Failure:** `pos_y(body) < void_threshold` (fell below the surfaces — remember
  down is -Y) or escape.
- **Sensing:** a raycast fan to gauge the gap to the next surface.

## Structural sketch (high-level, no dimensions)

A bounded space with a start surface, one or more static intermediate surfaces
separated by gaps, and a landing pad (`sensor` zone) at the end. Surface positions,
gap widths, and spawn re-rolled each reset. Checkpoints ladder first-hop → midway →
on-pad; success on settled containment, failure on falling into the void or escape.
Surface count, gap sizes, and heights are the model's to choose — enough gap that a
hop is required (no walking across), not so much that no jump clears it.
