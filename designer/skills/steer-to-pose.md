---
id: steer-to-pose
kind: archetype
created_by: human-seed wave-1
run_id: reseed-2026-07-14
wave: 1
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
load_when: Concept phase — the game's soul is steering a heading-controlled body and SETTLING on a pose (park, land, dock)
rationale: The largest example family (Lander, CarParking, Hovercraft, FlyBy, Ships). Real steering to a pose target, not translational nudging — the flagship for the thrust/torque + contained() bricks.
provenance: notes/engines/EXAMPLES_STRUCTURE_GUIDE.md §2 (vehicle/heading-control family) + §4; godotworld/SPEC.md §6 (thrust/torque) + §8 (contained); racing/lander genre decompositions — knowledge derived from thedivergentai/gd-agentic-skills (LGPLv3), paraphrased.
---

# Archetype: steer-to-pose

The soul: pilot a body that only moves the way it POINTS, and bring it to rest in a
target pose. Objective flip ideas: park → escape a bay; land → precision-drop on a
moving-free pad; dock → thread to a berth.

## Core loop

1. **Orient** — rotate the body toward the goal bearing.
2. **Drive** — thrust along the current heading to close distance.
3. **Correct** — read the obstacle/goal geometry, re-orient, feather thrust.
4. **Settle** — kill velocity and hold the pose inside the target zone.
5. **Latch** — success fires only while pose AND stillness AND containment all hold.

## Skill-chain of subgoals

- heading acquisition (face the goal) → checkpoint `facing_goal`
- gross approach (close the gap) → checkpoint `near_pad`
- fine alignment (upright / squared to the slot) → checkpoint `aligned`
- dead-stop inside the zone → success

## DSL expression

- **Control:** `torque` (signed angular kick, steers) + `thrust` (impulse along the
  body's current rotation, drives). This is the heading-control pair — translational
  `force`/`impulse` would betray the archetype.
- **Stillness:** an `on_step` `velocity_clamp` on the controlled body so it CAN come
  to rest (bodies coast forever otherwise — see `engine-truths.md`), and the goal
  reads `speed(body) < small`.
- **Pose finish:** `contained(body, pad_zone)` (a `sensor` zone), plus an
  `angle(body)` window if "upright/squared" matters. Beware: a body spun by `torque`
  has an inflated AABB, so make the zone generous or lock rotation at the theme's
  cost.
- **Sensing:** a raycast fan on the body for obstacle/edge proximity (obs spine).
- **Success (compound):** `contained(body,pad) and speed(body) < small [and angle
  window]`. Never a bare `contained` — that would be a shortcut.

## Structural sketch (high-level, no dimensions)

A bounded arena with a single target zone (a `sensor` pad) placed somewhere the body
must both turn and travel to reach. Optional static obstacles between spawn and pad
so the fan has something to discriminate. Spawn pose, pad location, and obstacle
layout re-rolled each reset from `rng`. Checkpoints ladder facing → near → aligned →
settled; failure on escape or (if themed) on a hard impact. Sizes, angles, and
positions are the model's to choose.
