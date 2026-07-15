---
id: gated-circuit
kind: archetype
created_by: human-seed wave-1
run_id: reseed-2026-07-14
wave: 1
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
load_when: Concept phase — the game's soul is threading ORDERED gates or pickups in sequence, then finishing
rationale: The progression flavor of the vehicle family (Racer, FlyBy mail-run, Hovercraft cargo, Courier circuit). Ordered checkpoints are the mechanic; the finish is a settle. Guards against shortcut-cheating via strict order.
provenance: notes/engines/EXAMPLES_STRUCTURE_GUIDE.md §2/§4 (Racer/FlyBy/Hovercraft); godotworld/SPEC.md §5/§8 (on_contact, checkpoints ordered latch); racing checkpoint + time-trial validation-chain — knowledge derived from thedivergentai/gd-agentic-skills (LGPLv3), paraphrased.
---

# Archetype: gated-circuit

The soul: visit a set of waypoints IN ORDER, then reach a finish — the challenge is
the route and its sequence, not any single leg. Objective flip ideas: race → mail
courier; lap → ordered pickups then dock; slalom → cone gates then settle.

## Core loop

1. **Read the next gate** — identify the current target in the sequence.
2. **Travel** — steer/drive (or translate) toward it.
3. **Clear it** — pass through / touch the current gate; it latches.
4. **Advance** — the next gate becomes live; repeat.
5. **Finish** — after the last gate, settle on the end zone.

## Skill-chain of subgoals

- reach gate 1 → checkpoint `gate_1`
- reach gate 2 (only meaningful after gate 1) → checkpoint `gate_2`
- … up to the ordered cap (1-6 checkpoints) → each a gate
- settle on finish → success

## DSL expression

- **Control:** heading-control (`torque`+`thrust`) for a vehicle feel, or
  translational `force`/`set_velocity` for a floaty courier — pick per theme.
- **Gates:** each gate is a `sensor` zone with an `on_contact` pairing the body and
  the zone to latch a `got_gate_n` flag (`once: true`). The checkpoint map reads
  those flags in order — insertion order IS the intended progression, and the runner
  latches them, so the ORDER is the anti-shortcut guard.
- **Ordering:** make a later checkpoint depend on the earlier flag
  (`flag("got_gate_2") and flag("got_gate_1")`) so skipping the sequence can't latch
  out of order — this is exactly the shortcut-cheat defence.
- **Finish:** `contained(body, finish_zone)` plus `speed(body) < small` (needs an
  `on_step` `velocity_clamp` — bodies coast forever).
- **Success (compound):** all gate flags AND finish containment AND stillness.
- **Sensing:** a forward raycast fan to spot the next gate / avoid walls.

## Structural sketch (high-level, no dimensions)

A bounded course with several `sensor` gates scattered so the natural path threads
them, and one finish zone past the last gate. Gate positions and the spawn re-rolled
each reset. Checkpoints = the gates in intended order; success needs every gate flag
plus a settled finish. Failure on escape or (if themed) on a hazard. The number of
gates, their placement, and the course shape are the model's to choose — keep the
count within the 1-6 checkpoint budget.
