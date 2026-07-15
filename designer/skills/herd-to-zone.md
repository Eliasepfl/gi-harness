---
id: herd-to-zone
kind: archetype
created_by: human-seed wave-1
run_id: reseed-2026-07-14
wave: 1
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
load_when: Concept phase — the game's soul is shoving a FREE (uncontrolled) body into a goal zone
rationale: The herding skeleton — the multi-body examples (AirHockey, ScoreTheGoal, Ships salvage) collapsed to single-agent: the agent controls itself but must move a SECOND, free body to the goal. Indirect control is the distinct skill.
provenance: notes/engines/EXAMPLES_STRUCTURE_GUIDE.md §2/§4 (AirHockey puck-herding, collapse of multi-body to single-agent); godotworld/SPEC.md §3 (a second dynamic non-control body) + §8 (contained on the free body); RTS/sports herding decomposition — knowledge derived from thedivergentai/gd-agentic-skills (LGPLv3), paraphrased.
---

# Archetype: herd-to-zone

The soul: the agent can only push — it must maneuver a FREE body (a puck, a crate, a
ball) into a goal by bumping it, never by carrying the goal itself. Indirect control
is the whole challenge. Objective flip ideas: air-hockey → herd a puck through gates
to a net; salvage → nudge a crate to the harbor.

## Core loop

1. **Read** — where is the free body relative to the goal?
2. **Get behind it** — position the controlled body on the far side.
3. **Push** — bump the free body toward the goal.
4. **Chase & correct** — re-approach, re-align, push again (it drifts).
5. **Sink** — the free body settles inside the goal zone.

## Skill-chain of subgoals

- make first contact with the free body → checkpoint `first_touch`
- push it past the midfield → checkpoint `advanced`
- free body contained in the goal → success

## DSL expression

- **Two dynamic bodies:** one with `control: true` (the agent), one free dynamic
  body (the puck) — NOT a second controlled body (inexpressible). The agent moves it
  only through collision.
- **Control:** translational `force`/`set_velocity` (a hockey-mallet feel) or
  `thrust`+`torque` for a vehicle pusher.
- **The push:** ordinary physics contact — no special verb; the agent's momentum
  transfers to the free body. Give the puck low friction so it carries.
- **Settle:** an `on_step` `velocity_clamp` on the PUCK so it can come to rest in the
  goal (coasts forever otherwise).
- **Success (compound):** `contained(puck, goal_zone) and speed(puck) < small` — the
  FREE body is in the goal and stopped. Reading the puck (not the agent) is what
  makes it herding; the agent being at the goal proves nothing.
- **Failure:** puck or agent escapes; optionally the puck enters an "own-goal" zone.
- **Sensing:** a raycast fan (wide cone) on the agent to track the puck's bearing.

## Structural sketch (high-level, no dimensions)

A bounded rink with the controlled body, one free puck, and a goal `sensor` zone,
optionally with static bumpers or gates the puck must thread. Puck start, goal
location, and spawn re-rolled each reset. Checkpoints ladder first-touch → advanced →
sunk; success on puck-in-goal + stillness, failure on escape / own-goal. Rink shape,
bumper placement, and puck size are the model's to choose — position the goal so a
straight shove won't do it, forcing real herding.
