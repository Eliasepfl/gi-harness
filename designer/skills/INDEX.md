---
id: skills-index
kind: index
created_by: human-seed (fable-orchestrator)
run_id: seed-2026-07-14
wave: 0
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
rationale: One-line load-router over the seed skill library so the designer pulls only the relevant craft topic into context.
provenance: seed-2026-07-14 skill library
---

# Designer skill index

Load a skill when its trigger matches the design phase you're in. Each is a self-contained
craft topic; several often apply to one game.

- `world-composition-and-landscape` — choosing a SETTING, naming bodies, laying out static scenery/terrain; making each world read as a distinct PLACE (the 57 recognized names, ground/biome skins, framing walls, terraced ledges, decor rules, enumerated motifs for variety).
- `difficulty-shaping-and-checkpoints` — setting the challenge level and writing the `checkpoints` map; hitting TARGET (gem_cavern recipe: compound objective, 5 ordered milestones, ~100-200 ticks) vs easy/hard, and ordered subgoal gating.
- `fun-mechanics-and-tension` — picking the ONE mechanic archetype and tuning feel; near-miss clearances (~1 body-width), playable timing windows (4-10 ticks), forced two-axis play, escalation, telegraphed traps, commitment latches.
- `rl-learnability` — sanity-checking that a spec will TRAIN not just certify; monotone-distance progress, milestone spacing in action-space, the flood_tower anti-pattern, uniform+clamped control, observable-threat sensors.
- `vocabulary-limits-and-workarounds` — when a seed prompt implies motion/behavior you're unsure the DSL supports; what the runner CANNOT express (movers, spawners, wind, swimming, order-locks, aim verbs) and the honest passive-physics re-cast for each.
- `certification-survival` — finalizing a spec to pass G0-G4; the containment triple, collect-N primitive, hazards-as-sensors, grounded/solidity rules, and the silent-at-load→surface-at-replay failure-signature table.
