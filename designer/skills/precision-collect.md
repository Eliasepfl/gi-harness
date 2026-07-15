---
id: precision-collect
kind: archetype
created_by: human-seed wave-1
run_id: reseed-2026-07-14
wave: 1
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
load_when: Concept phase — the game's soul is gathering scattered targets, guided by a proximity sensor
rationale: The perception-navigation family (BallChase coin-dash, Ships salvage). The obs abstraction itself is the challenge; potential-shaped precision-collect is the closest fit to our native 2D form and the render benchmark.
provenance: notes/engines/EXAMPLES_STRUCTURE_GUIDE.md §2 (perception-defined navigation) + §4; godotworld/SPEC.md §5/§7 (on_contact + remove_when + flags) + §7b (sensors); collection game-loop decomposition — knowledge derived from thedivergentai/gd-agentic-skills (LGPLv3), paraphrased.
---

# Archetype: precision-collect

The soul: find and gather a set of scattered targets, with a proximity sensor as the
eyes. The world exists to make that sensor meaningful. Objective flip ideas: coin
dash → data-log salvage; chase → hazard-threaded gather.

## Core loop

1. **Sense** — read the fan for the nearest target / hazard.
2. **Approach** — move toward the nearest uncollected target.
3. **Collect** — overlap it; it latches picked-up and is removed.
4. **Repeat** — until every target is gathered.
5. **Finish** — (optional) return to a drop-off zone once all are held.

## Skill-chain of subgoals

- collect the first target → checkpoint `first_pickup`
- collect a majority → checkpoint `most_collected`
- collect the last → checkpoint `all_collected`
- (optional) reach the drop-off → success

## DSL expression

- **Control:** translational `force`/`set_velocity` for a nimble collector, or
  `thrust`+`torque` if the theme is a vehicle. Keep the action set small.
- **Pickups:** each target pairs with the body via `on_contact` → latches a
  `got_target_k` flag (`once: true`); an `on_step` `remove_when` deletes the target
  once its flag is set, so the world visibly empties.
- **Sensing (the spine):** a raycast fan on the collector — 0 = no hit, larger =
  nearer. Widen the cone for surround-search. The world's clutter must let the fan
  DISCRIMINATE a target from a wall, or the game is mis-composed.
- **Success (compound):** all pickup flags AND (if used) `contained(body, dropoff)`.
  Collecting-all alone is fine as a compound because it takes many decisions; add a
  drop-off to raise the bar and avoid a fast-sweep shortcut.
- **Hazards:** static hazard walls a `failure` predicate reads via `contacts`.

## Structural sketch (high-level, no dimensions)

A bounded field with several collectible `sensor` (or small dynamic) targets
scattered so no single dash grabs them all, optional static hazard walls, and an
optional drop-off zone. Target positions and spawn re-rolled each reset. Checkpoints
ladder first → most → all; success on all-collected (plus drop-off if used). Failure
on escape or hazard contact. Target count, spread, and hazard layout are the model's
to choose (respect the 1-6 checkpoint budget — group pickups into milestone flags,
don't make one checkpoint per coin if there are many).
