---
id: catch-and-route
kind: archetype
created_by: human-seed wave-1
run_id: reseed-2026-07-14
wave: 1
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
load_when: Concept phase — the game's soul is catching ONE falling body and routing it by its per-drop label
rationale: The sorting/conditional-logistics family (ItemSortingCart Chute Deflector). A NON-navigation skeleton — one motor skill forked by a label observation — guarding the batch against "steer-to-goal is the only shape."
provenance: notes/engines/EXAMPLES_STRUCTURE_GUIDE.md §2 (sorting/conditional logistics) + §4 (fully expressible now); godotworld/SPEC.md §5/§7 (on_contact latch + velocity_clamp) + §7b (sensor zones); harvest/collection loop decompositions — knowledge derived from thedivergentai/gd-agentic-skills (LGPLv3), paraphrased.
---

# Archetype: catch-and-route

The soul: a falling body must be caught and delivered to the CORRECT bin, chosen by
a per-drop random category — one catching skill forked by a label. This is the
anti-sameness insurance: no traversal, no goal-chase. Objective flip ideas:
sort-cart → deflect-to-chute; catch → parcel routing.

## Core loop

1. **Read the label** — this drop's category (encoded as an initial flag / a start
   position / a colour zone the sensor reads).
2. **Position** — move the catcher under the fall line.
3. **Catch** — intercept the falling body; contact latches "caught."
4. **Route** — carry / deflect it toward the bin its label demands.
5. **Deliver** — the body settles in the correct bin; wrong bin = failure.

## Skill-chain of subgoals

- intercept the falling body → checkpoint `caught`
- move it toward the correct side → checkpoint `routed`
- body contained in the right bin → success

## DSL expression

- **Control:** translational `force`/`set_velocity` on a 1-DOF catcher (a paddle /
  cart). No heading control needed — that's the point, a different motor skill.
- **The falling body:** a dynamic body with an initial downward `velocity`; its
  category set per-reset by which of two `sensor` bins is "correct" (seeded `rng`).
- **Catch latch:** `on_contact` between catcher and falling body → `caught` flag.
- **Settle:** `velocity_clamp` so the delivered body comes to rest in a bin (coasts
  forever otherwise); bins are `sensor` zones.
- **Success (compound, conditional):** `contained(item, correct_bin) and
  speed(item) < small`. **Failure:** `contained(item, wrong_bin)` — the fork is what
  makes it a decision, not a reflex.
- **Sensing:** a raycast fan (or the bin `sensor`s) to read the label / fall line.

## Structural sketch (high-level, no dimensions)

A bounded box with a spawn chute up top, one dynamic falling body, a 1-DOF catcher
below, and two (or more) labelled bins. Each reset re-rolls which bin is correct and
where the body drops (seeded). Checkpoints ladder caught → routed → delivered;
success on correct-bin containment + stillness, failure on wrong-bin containment or
escape. The number of bins, the chute placement, and the catcher's range are the
model's to choose. Keep it ONE item per episode (counters beyond flags aren't
expressible).
