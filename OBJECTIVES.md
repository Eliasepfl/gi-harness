# OBJECTIVES — living roadmap (updated 2026-07-13 evening)

> Working doc so no context is lost between sessions. The strategy below was set by
> Elias; the harness serves it. See CONTRACTS.md (normative interfaces) and
> SPEC_VERIFIER.md (verification design + literature).

## The pyramid strategy (current phase: base of games)

Like writing a paper: reference previous methods, compress them into general
(somewhat proven) rules, build upward.

1. **NOW — reliable base of games.** Generate many games across varied prompts.
   Every repair the loop needed is a LESSON (a specific we had to impose).
2. **Harvest → revise the main prompt.** Compress recurring lessons into general
   rules in `gamegen._SYSTEM_PROMPT` (slightly more general tools, clearer
   interaction instructions). No game-specific hardcoding in prompts, ever.
   Reusable "skills" (helper tools for future games) may emerge here.
3. **Exotic prompts.** Only once the base is solid.
4. **Real 2D game engines.** Last step, drawing on all accumulated knowledge.
   (Assignment: "produce playable environments in a game or physics engine" —
   execution context free.)

## Hard rules (set 2026-07-13)

- **Base code is FROZEN during generation runs.** Repairs fix the GENERATED game
  code only. Each run gets a sandbox (its own output dir) that the generated code
  alone may touch; any base-code change during a run invalidates the run
  (integrity manifest check). Base-code changes happen only as explicit,
  justified commits outside runs.
- **No hardcoded game-specific values in prompts or harness functions.** Substrate
  constants (world size, gravity, dt) are fine; design hints are not.
- **Demos are generated ON THE FLY** at demo start (`game demo`), not pre-baked.
  Everything the demo reports (verdict, attempts, witness ticks, checkpoint latch
  ticks, per-attempt failures/hints) comes from the run's machine-readable result
  dict — no human/LLM narration required except the DESIGN block (also stored).
- **Secrets:** `env.py` at repo root, gitignored, never committed/pushed
  (OpenRouter key lives there). Env vars override.

## Generation backends

- `anthropic` (claude-opus-4-8) — no key available in this environment yet.
- `openrouter` (free model, key in env.py) — volume backend for the base-of-games
  campaign; expect lower quality → more repair iterations (that is fine: repairs
  produce lessons).
- `template` — offline test fixture only.

## Generation telemetry (directive 2026-07-13, Elias)

- **Count every failure and every repair** — first-class statistic. Every
  `generate_game` run appends one JSON line to `runs/ledger.jsonl`: prompt,
  backend, model, verdict, attempts, per-attempt failure_class + failed checks,
  repairs applied, wall time, integrity. `harness game stats` aggregates per
  model: completion rate, mean repairs to COMPLETED, failure-class histogram,
  flagrant errors (format non-compliance, forbidden imports, ignored symbols).
- **Report model quality to Elias** after each batch. If the free model's errors
  are too flagrant, he decides the switch to a better OpenRouter model
  (candidate: **GLM 5.2**) — data first, then the call. Model id stays a config
  value in env.py (OPENROUTER_MODEL), so switching is a one-line change.

## Verification stack (v2.1, done)

World substrate (bodies/joints/sensors/forces) → sandbox (AST + subprocess) →
universal oracles: G0 static, G1 rollout sanity + agency + action efficacy +
determinism, G2 goal well-formedness, G3 random-search solvability with
checkpoint-guided second pass → witness replay = demo GIF. Checkpoints: 1..6
game-declared milestone predicates, runner-latched, giving dense progress signal
+ "stuck between X and Y" repair diagnosis. `success` stays a binary unshaped
certificate.

## Explorations open

- **PARTS BANK (direction d'Elias, 13 juil. soir)** — curated bank of items/
  obstacles/elements with PREDICTABLE physical properties (+optional sprites)
  that the generation model pulls by name from prompt context, instead of
  hand-calibrating physics in code. Goal: cut the load on the code-writing
  model (necessary for scaling) and strengthen per-part verifier invariants.
  Design principle: bank = NOUNS (parts), freedom = VERBS (mechanics/rules
  stay free code) — do not regress to v1's genre lock-in. Research in
  `notes/parts_bank/` (assets.md / mcp_tools.md / design.md → synthesis to
  become CONTRACTS §9). Ledger failure classes are the evidence base for what
  a bank eliminates (physics mis-calibration) vs not (goal design).

- **Real game engines (rung 4) — research DONE (13 juil.), synthesis in
  `notes/GAME_ENGINE_INTEGRATIONS.md`** (details per engine in `notes/engines/`).
  Structural finding: ALL existing Claude↔engine integrations are authoring
  layers; nobody ships the per-tick state↔action loop — we'd build it ourselves.
  Recommendation ladder: (1) stay pymunk for the campaign; (2) rung-4 step 1 =
  **Planck.js/Matter.js in pure Node** (loop parity, real engine family, JS as
  2nd generation language, Phaser+CDP on-ramp for visual demos); (3) step 2 =
  Godot WITH Rapier after a ½-day determinism/boot spike (sandboxing GDScript =
  open problem); (4) Roblox = downstream showcase export of certified games only
  (no headless physics — staff-confirmed); (5) skip Unity/Unreal for the loop.
  Cross-lesson: small verb-shaped World API beats big API — resist growth.

- **OpenEnv / Agent World Model infra** (github.com/Snowflake-Labs/agent-world-model,
  merged into github.com/huggingface/OpenEnv): analysis in progress — could wrap our
  games as standard envs (reset/step/reward from checkpoints) for the training step
  of the pipeline (env creation → verification → agent training → next env).
- L3 state-injection keypoints & L4 intent contract (SPEC_VERIFIER.md) — later rungs.
- Effective semantic diversity metric over certified games (COLM 2025) — later.

## Site (public build log)

https://eliasepfl.github.io/ — repo private `Eliasepfl/Eliasepfl.github.io`, site
public, day folders (`day1/`, `day2/`...). Demos only, never harness code, never
secrets. Day-N page: one row of demos (grid), prompt above each GIF, one-line
verifier impact.
