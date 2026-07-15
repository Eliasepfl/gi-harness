# STATE — project snapshot & restart guide

> Updated 2026-07-15 (day 3, evening). Companion to `OBJECTIVES.md` (roadmap),
> `VERSIONS.md` (module map), `CONTRACTS.md` (normative interfaces).
> Everything below is committed and pushed to `Eliasepfl/gi-harness` (main)
> unless marked in-flight.

## What this is

An agent harness for the General Intuition challenge: **text prompt → playable
game → certified entirely in code** (no VLM, no pixels in verification).
Runs on MIT ORCD ("Engaging"), apptainer image `gi-certifier.sif` as the
canonical certifier. Site: https://eliasepfl.github.io/.

## The lane (two pivots, both final)

1. **Godot-only** (2026-07-14): pymunk/planck lanes frozen as regression floor.
2. **Agent-written GDScript** (2026-07-15): the declarative JSON-spec lane is
   PARKED at generation (kills variety); the designer writes a single `.gd`
   GameAPI file (duck-typed: `build/act/state/checkpoints/is_success/
   is_failure/actions`), verified through the serve contract
   (`godotworld/serve_game.gd`). Contract = signatures + hard rules ONLY
   (anti-anchoring: no examples, no dimensions, no magic values).
   Skills: gd-agentic-skills, godot-master orchestrator leads (~24k tokens),
   domain skills LLM-routed. Same principle everywhere: **no hardcoded
   taxonomies on LLM-facing surfaces — light LLM routing over a menu**
   (skills, asset matching, directives).

## Designer model

`z-ai/glm-5.2` (OpenRouter, key in gitignored `env.py`). Hard-won reasoning-model
config (all measured 2026-07-15): output ceiling 32k (GLM thinks 14.5–24k tokens
on real payloads; providers ignore reasoning caps), null-content salvage = one
retry with thinking disabled (49s/$0.04, reliable), `OPENROUTER_REASONING_MAX_TOKENS=off`
switch available (5× cheaper, quality A/B open). An explicitly requested backend
that fails surfaces `ENV_ERROR` — **never** a silent template fallback (the
"GLM 4/4" fake-result incident), and anthropic SDK errors fall through the
auto chain correctly.

## The pipeline (all live on main)

```
prompt ─ skills(godot-master + routed) ─> GLM writes game.gd
   v
G0 parse/contract/banned-APIs ─ G0.5 reachability flood ─ G1 determinism+live-actions(context-aware)
   ─ G2 goal ─ G3 tree solve → witness ─┬─ certified
                                        v
              G4 attack (escape/single-action/shortcut→broken-gating/softlock)
              G3' RL (SB3; in-scene batched vec env: N instances/1 proc/1 socket, ~9x)
                                        v
              harden: oracle outcomes → PERSONALIZED DIRECTIVES → revise from
              CURRENT source (orchestrator kept, routed on directive text) →
              re-certify; guard: max rounds, REPAIR_STALLED, never overwrite certified
                                        v
              capture: zero-contact dresser + software-GL in-engine frames → GIF
              (2D stays 2D — no fake-3D; 3D games get assets from the bank)
```

Key invariants: witness replays bit-identically (lockstep serve stepping,
`HARNESS_GODOT_SPEEDUP=8` tick-paired); dressed-vs-undressed state trails
byte-identical (tested); play-bounds exit = truncation, not a break;
single-action-win = broken-game repair feedback; game processes env-scrubbed.

## Day-3 numbers (honest)

- **Certified games: 5** — cross-road, knock-puck, push-ball (deepseek era) +
  fly-rings (GLM, 2 attempts), drive-cart-lap (GLM, 5 attempts; deepseek failed
  it 5/5 — RL confirmed its version genuinely unsolvable, 0 checkpoints).
- **All 2D** (model defaults to 2D even on 3D-evoking prompts — open item; the
  full funnel is 3D-proven via the `mini_collect_3d` fixture).
- **Batched RL throughput**: 2048 sps vs 233 sequential at N=4 (8.8×);
  farm runs at 1922–3029 sps. Budget default 1M, progress-gated.
- **Parking prompt**: still failing after 5 real attempts — but with 5
  *distinct, correctly diagnosed* flaws (parse → out-of-bounds cones →
  pre-satisfied checkpoint → dead milestones → single-action win). Prime
  first customer for the harden loop.
- **First harden wave**: fly-rings → `HARDENED` (no hard findings);
  knock-puck → `OPEN_UNMAPPED` (soft findings only, honestly no directive);
  cross/push/drive rerunning after the anthropic-auth fall-through fix.
- **Demos**: real in-engine renders live (`demos/day3/*_RENDERED.gif`);
  dot-GIFs retired as demo artifacts (verification-only).

## In flight (worktree agents; merge on landing)

- **Inverse-value G4 attacker** (Elias's design, literature-validated:
  anti-policy search + winning-trajectory prefix seeding + freeze-detect +
  tree-refutation confirm → certified softlock witnesses; A/B vs random fuzz
  required) — `notes/adversarial/INVERSE_VALUE_G4.md`.
- **3D asset dressing** (bank assets on 3D proxies only; 2.5D mode was CUT —
  "if the game is 2D, keep it 2D").

## Queued next (decision-complete)

1. Swarm ADOPT items (`notes/engines/MCP_FEEDBACK_TOOLS.md` — godot-mcp itself
   rejected, mechanics extracted): runtime stderr delta-capture in the 3
   spawners → new directive class; `--check-only` analyzer-errors parse fix;
   engine-truth geometry in the check op; later a headless GDScript LSP sidecar.
2. 3D generation nudge (prompts that evoke 3D should yield 3D games).
3. GPU RL option: conda env `godot-rl` (torch+cu128, editable pinned clone)
   ready for `gdrl` runs on GPU nodes; needs exported env binaries.

## Restarting a work session (cluster)

1. Read this file, then `OBJECTIVES.md`, then notes as needed
   (`notes/engines/GDSCRIPT_LANE.md`, `notes/engines/FEEDBACK_LOOP.md`,
   `notes/adversarial/INVERSE_VALUE_G4.md`, `notes/engines/DEMO_CAPTURE_LANE.md`).
2. Everything runs in-image: `module load apptainer/1.4.2`,
   `apptainer exec -B /orcd ~/gi/gi-certifier.sif ...` (`~/gi` symlinks the repo).
   Godot NEVER bare on login nodes (448-core thread thrash).
3. Slurm: `mit_preemptable` (CPUs fastest), ≤200 array tasks, `--requeue`,
   `GIP_PORT_BASE=$((47000 + TASK_ID*64))`, per-task `HARNESS_LEDGER` shards →
   `harness ledger merge`.
4. Sanity: scoped pytest in-image (full suite is slow; gate the files you touch).
5. Conventions that MUST survive: tree FROZEN during generation runs (integrity
   manifest); count every failure (ledger); notes-per-task in `notes/`;
   secrets only in `env.py`/env vars, never committed.

## Notes index (the load-bearing ones)

| Area | File |
|---|---|
| THE pivot | `notes/engines/GDSCRIPT_LANE.md` |
| Feedback loop spec (Elias) | `notes/engines/FEEDBACK_LOOP.md` |
| Inverse-value G4 (Elias) | `notes/adversarial/INVERSE_VALUE_G4.md` + `FEASIBILITY_LITERATURE.md` |
| MCP research verdict | `notes/engines/MCP_FEEDBACK_TOOLS.md` |
| Capture lane (what worked) | `notes/engines/DEMO_CAPTURE_LANE.md` |
| Asset bank | `notes/engines/ASSET_BANK.md` |
| Why demo≠designer gap | `notes/engines/ASYMMETRY_ANALYSIS.md` |
| ORCD runbook | `notes/compute/ORCD_GODOT_RL_PLAN.md` + `ORCD_DAY1_LOG.md` |
| RL library capabilities | `notes/engines/GODOT_RL_AGENTS_CAPABILITIES.md` |
