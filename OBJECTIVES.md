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

## Adversarial / edge-case testing requirement (Elias, 13 juil. late)

For NEXT versions (lands with the Planck port): games must survive ADVERSARIAL
probes, not just reach the goal. New oracle family (working name G4):
- **Avoidance probe**: policies that actively try NOT to win (minimize
  checkpoint progress) for as long as possible — if success still triggers,
  the goal is degenerate/unavoidable.
- **Single-action-win check** (cheap, immediate candidate): during the existing
  per-action efficacy rollouts, flag success — the jelly-tower agent found a
  game soloable by one repeated action in 8 ticks that our anti-triviality
  threshold (>=5) missed.
- **Breaker probes**: adversarial action fuzzing (max-frequency spam,
  alternating extremes, boundary hugging) hunting NaN/explosions/escapes/
  stuck states — physics robustness under hostile play, beyond the noop rollout.
- Games surviving N adversarial episodes get a "bulletproof" grade in the
  report; failures feed the repair loop like any other layer.

DELEGATION ARCHITECTURE (Elias's idea, 13 juil. late — adopted with refinement):
run cheap/free models IN PARALLEL as attackers on the CERTIFIED game code
written by the smarter model. Three tiers by cost:
- **Tier 0 — mechanical fuzzing, NO LLM**: seeded action fuzz at native speed
  (thousands of episodes/sec). LLMs would be wasted here; this tier is free.
- **Tier 1 — cheap-LLM attack proposers (the idea's sweet spot)**: multiple
  free/cheap models in parallel lanes (hy3, qwen-coder, llama... — separate
  rate limits = free parallelism), each reads the certified game source +
  report and proposes K attacks. CRITICAL DESIGN RULE: attackers output PURE
  DATA (JSON action sequences / parameterized strategies), never code — zero
  sandbox risk, and validation is mechanical replay (did success trigger under
  avoidance? NaN? escape? faster-than-witness shortcut?). Attack proposal is
  much easier than game design, so weak models suffice — the verifier is the
  referee, wrong attacks cost nothing (generator-verifier asymmetry).
- **Tier 2 — smart-model attacks**: reserved for games surviving tiers 0-1;
  "bulletproof" grade requires surviving all three.
Economics: ~1 cheap call per attacker per game + millisecond replays; findings
are repair material AND robustness evidence (training-signal for GI's framing).

## Explorations open

- **PARTS BANK — research DONE (13 juil. soir), synthesis in `notes/PARTS_BANK.md`**
  (details: notes/parts_bank/{assets,mcp_tools,design}.md — design.md = future
  CONTRACTS §9). Verdict GO: no off-the-shelf bank exists (sprite libs carry zero
  physics metadata) — bank = named PRE-CERTIFIED World.add presets + lazy Kenney
  CC0 sprites; one verb `world.part()`, add() stays escape hatch; two-tier prompt
  (+~380 tokens); versioned data pinned per run (integrity + ledger bank_version);
  engine-neutral schema (pymunk → Planck → Phaser). Ledger evidence: 50% of repair
  iterations = physics mis-calibration the bank kills by construction; other 50%
  = verb mistakes → prompt rules. No MCP needed (local-first); PixelLab API
  optional build-time later. Sequencing: v2.2 after current campaign, then A/B
  re-run WITH bank to quantify the repair-rate delta.

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
