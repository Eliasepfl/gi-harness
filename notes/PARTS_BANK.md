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

## Two-stage selection & retrieval (addendum, same evening — Elias's proposal)

Detailed studies: `notes/parts_bank/retrieval.md` (tech) and `pipeline.md` (design).

- **Physics is never chosen by the model** — it ships pre-certified with each
  part; stage-1 only selects WHICH parts (bounded overrides allowed). Prompt
  exoticism is carried INTO the bank by retrieval, and out of it by the escape
  hatch when the bank can't follow (logged as bank-growth demand).
- **Retrieval stack** (tiny-corpus framing, 60→500 parts): hybrid brute-force —
  BM25 (`bm25s`) ⊕ static Model2Vec embeddings (`potion-retrieval-32M`, MIT,
  sub-ms, byte-reproducible) fused by RRF; NO vector DB, NO reranker, NO
  keyword-extraction step (a 30-token prompt is already keywords). LLM-as-
  selector fails the determinism axis → escape hatch only. Ship BM25-only with
  the campaign; add the dense channel at the exotic-prompt phase.
- **Pipeline = Option A for v2.2**: harness-side retrieval, no extra LLM call —
  stage-1 is a pure function of (prompt, bank_version): deterministic, ~0
  amortized tokens, drop-in to the existing repair loop. Option B ("kit-picker"
  stage-1 LLM emitting a pinned manifest) only if campaign data shows menu
  mis-selection is the residual bottleneck. Option C (in-call tool) deferred.
- **Three design rules**: (1) retrieval external & deterministic, vocabulary out
  of context (legend + ≤15-part themed menu, never the full catalog); (2) pin
  the retrieved set for the whole run — on repair APPEND (one bounded
  missing-kind re-retrieval), never rebuild; (3) the menu is advisory, never a
  cage — strong prompt anchor + "menu is optional" line keeps models in the
  advisory regime (RAG-for-codegen literature), world.add stays open, every
  out-of-menu use feeds `game bank-demand` (ranked authoring queue).
- **Source correction**: GenSim's retrieval is random-sample / embedding-
  similarity over prior tasks, NOT keyword menus — our design is closer to
  ToolLLM's dense-retriever shape; reframed accordingly.
- New telemetry: ledger `pipeline` block (retrieved_set, parts_used, misses,
  re_retrieval, menu_mode); G0 gains manifest compliance (used ⊆ bank ∪
  escape-hatch — hard; menu compliance soft in A).

## Verdict & sequencing

GO — build after the current campaign cycle: (1) bank schema + bank-CI +
`world.part()` + prompt tiers [v2.2], (2) re-run the A/B campaign WITH bank to
measure the repair-rate delta (the scaling claim, quantified), (3) carry the
same bank JSON into the Planck.js port (rung 4) where sprites become real.
