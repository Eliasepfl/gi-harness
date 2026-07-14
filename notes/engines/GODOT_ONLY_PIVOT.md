# DECISION: Godot-only (Elias, 2026-07-14 late)

> "We stayed stuck on the same perimeter that was given by our first tests on
> pymunk and planck. Only run on Godot's engine. Games are created over these
> environments. We have examples so it is not zero-shot."

## What this means

- **Godot is the sole generation target.** `game new` defaults to
  `engine=godot`; py/js lanes stop receiving new games.
- **The DSL perimeter unfreezes.** The spec vocabulary was implicitly capped
  at the pymunk∩planck∩godot intersection; it may now grow GODOT-NATIVE
  features (tile terrain, camera hints, background/decor layers, movers via
  AnimatableBody2D, heading-control verbs) without three-engine parity. The
  quality-bar research (`GODOT_QUALITY_BAR.md`, in flight) prices the first
  additions.
- **py/js lanes are FROZEN LEGACY, not deleted**: executors + tests stay
  green (regression value, and the certified JS bank remains replayable);
  witness/bank history retained; no new generation, no new features.
- **Few-shot grounding**: the 3 certified example specs + the seeded
  `designer/skills/` library + the quality checklist ride in the designer
  context; the examples repo remains the external quality bar + RL bench.

## Critical path (the one real gap)

**G3'-on-godot does not exist yet** — `g3_prime` drives `PlanckEnv` (JS)
only. Until the serve mode lands, godot games get G0-G4 but no learnability
grade and no curriculum. Build = the settled dual-dialect design
(`GODOT_RL_AGENTS_CAPABILITIES.md` §3/§6.1): outer Sync 0.7 verbatim, inner
determinism-first verbs {init,reset,act,close} with reseed-on-reset + seeded
rebuild + split term/trunc + STALE deadline + no eval verb; shared
synchronous stepping core (tick-gated, fixed-delta speedup); in-scene
batching (N controllers, one process/port/core) as the scale lever; port =
base + SLURM_ARRAY_TASK_ID.

## Retired by this decision

- Cross-lane determinism gate (was CI gain #5) — godot-only makes it moot;
  keep the js/py suites as plain regression.
- The JS showcase as any kind of target (already demoted by Elias earlier
  today; now fully historical).
