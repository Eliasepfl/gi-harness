# Parts bank — synthesis (2026-07-13, evening)

> Elias's direction: a curated bank of items/obstacles/elements with PREDICTABLE
> physical properties (+ sprites) that the generation model pulls by name from
> prompt context — cutting the load on the code-writing model (needed for scaling).
> Detailed, source-verified studies: `notes/parts_bank/assets.md` (sourcing),
> `mcp_tools.md` (MCP/AI tooling), `design.md` (architecture — the future
> CONTRACTS §9). This file is the executive synthesis.

## The three load-bearing findings

1. **The bank we want does not exist anywhere — building it IS the value.**
   No sprite library on earth ships collider/physics metadata; every sprite↔
   physics pairing in the wild (Tiled, LDtk, PhysicsEditor, R.U.B.E., Godot) is
   a hand-filled authoring format. Since our verification reads state (never
   pixels), the load-cutting half of a part is its PHYSICS ARCHETYPE — which is
   exactly a named, calibrated, PRE-CERTIFIED preset of `World.add()` kwargs.
   Sprites are a cosmetic key (site demos, engine ports), attached lazily.

2. **Elias's scaling instinct is confirmed by our own ledger.** In the fair
   campaign batch, repair iterations split 50/50: half are physics
   mis-calibration the bank eliminates BY CONSTRUCTION (NaN from bad joint
   setups — including the campaign's only never-solved game, the wrecking ball
   with a 250px lever-arm PinJoint; initial penetrations; floating/out-of-bounds
   spawns; wrong densities; hazard mis-wiring). The other half (dead `wait`
   actions, goal well-formedness, solvability, milestone ordering) are VERB
   mistakes — a bank cannot touch them; they feed the prompt-rules harvest
   instead. The bank is necessary, not sufficient — and now we can say so with
   data, not vibes.

3. **No MCP is needed for the bank itself — local-first wins.** The physics
   half must be deterministic, versioned, offline (seeded runs + integrity
   manifest); every asset tool found only buys presentation. Optional later:
   PixelLab.ai API (NOT its stale MCP) for themed sprites — build-time only,
   after certification, cached into the versioned bank. Bookmark: official
   Phaser editor MCP for the rung-4 on-ramp.

## The design (from design.md — to become CONTRACTS §9)

- **One new verb**: `world.part(name, kind, *, pos, **bounded_overrides)`;
  `world.add()` stays as the escape hatch. Bank = NOUNS (calibrated bodies and
  pre-jointed subassemblies), game code keeps all VERBS (act/on_step/success/
  checkpoints). Honors the "small verb-shaped API" lesson.
- **A part = shape + machine-checkable guarantee.** Per-category invariants
  (terrain→static, hazard→sensor-or-lethal, mobile→joint-settles-without-NaN),
  admitted via one-time offline "bank-CI" (settle-grid reusing G0/G1). In-game
  checks stay cheap; every part arrives pre-certified.
- **Two-tier prompt injection ≈ +380 tokens** (category legend in system prompt
  + themed 8-15-part menu retrieved by prompt keywords) vs ≈ +3600 for a naive
  full catalog (current system prompt is ~1765 tokens). DESIGN block gains a
  machine-readable `Parts used:` line.
- **Bank is versioned DATA, pinned per run**: `banks/parts/<version>/` +
  `bank.lock`; integrity manifest extended to hash the pinned catalog (mid-run
  bank change ⇒ INVALIDATED, same as base code); ledger gains `bank_version`.
- **Engine-neutral schema, per-engine bindings**: one JSON drives pymunk today,
  Planck.js tomorrow (spike PROVEN: byte-identical determinism, ~parity
  throughput), Phaser sprites later — the same bank rides the whole pyramid.
- **Originality guardrails**: ~60-part breadth, themed per-prompt subsets
  (GenSim-style retrieval), bounded overrides, diversity measured over part
  multisets (feeds the effective-semantic-diversity workstream), escape hatch
  preserved for exotic prompts. Originality budget deliberately shifts from
  nouns (reliability) to verbs (variety — where models are strong).

## Sourcing decision

Kenney CC0 exclusively for sprites (~140 2D packs, 60k+ assets, uniform public
domain; Physics Assets ×215 + platformer packs ×~950). Rejected: itch.io
(license heterogeneity), LPC (copyleft), OpenGameArt (mixed; CC0-filter only).
v1 target: ~40-60 archetypes / ~80 curated sprites.

## Verdict & sequencing

GO — build after the current campaign cycle: (1) bank schema + bank-CI +
`world.part()` + prompt tiers [v2.2], (2) re-run the A/B campaign WITH bank to
measure the repair-rate delta (the scaling claim, quantified), (3) carry the
same bank JSON into the Planck.js port (rung 4) where sprites become real.
