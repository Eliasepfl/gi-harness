# STATE — project snapshot & restart guide

> Updated 2026-07-14 (very) late. Companion to `OBJECTIVES.md` (roadmap),
> `VERSIONS.md` (module map), `CONTRACTS.md` (normative interfaces).
> Everything below is committed and pushed to `Eliasepfl/gi-harness` (main).

## What this is

An agent harness for the General Intuition challenge: **text prompt → playable
2D game → certified entirely in code** (no VLM, no pixels in verification).
Site: https://eliasepfl.github.io/ (password in `gi-site/_secret/password.txt`,
never committed).

## The pipeline as it stands (v2.4, 439 tests green)

```
prompt ──> designer LLM ──> game (js code | godot JSON spec | py code)
              │  bank MENU (BM25 over 60 certified parts)
              v
        G0 static ─ G1 rollout ─ G2 goal ─ G3 solve (Go-Explore TREE, default)
              │  solidity oracle · WORLD_SIZE≤2400x1600 · duration bar (≥20t)
              v
        G4 adversarial (avoidance / single-action / breaker fuzz) → open|hardened|bulletproof
              v
        G3' RL learnability (PPO CPU, checkpoint-latch reward) → difficulty grade
              v
        curriculum: profile → "harden/ease at <milestone>" directive → next version
              v
        demos: witness GIF · web canvas replayer (~17 KB gzip vs 1.3 MB GIF)
```

Three engines behind one executor seam: **PyExecutor** (pymunk), **JsExecutor**
(Planck.js — the 6 showcase games), **GodotExecutor** (Godot 4.7 headless +
frozen `runner.gd` interpreting declarative JSON specs — 3 example games
certified; spec v1 = 4/7 archetypes full, gaps: ordered switches, joints,
per-step movers → `notes/engines/GODOT_RL_EXAMPLES_AUDIT.md` has the priority
order: raycast sensors → move/spin_body → ordered_flag → path_follow → joints).

## The numbers that matter

- **Day-2 batch**: 6 Opus-authored Planck games, worlds 900–2000 px,
  witnesses 97–261 ticks, **6/6 G4-hardened** (0 escapes / 0 NaN / 0
  single-action wins over ~3,300 adversarial episodes). 5 published on
  day2/ (flood_tower held back — too close to day-1 volcano).
- **Difficulty map @500k steps** (`notes/rl_agent/DIFFICULTY_MAP_R1.md`):
  boulder_run *easy* 1.0 · demolition *easy* 0.97 · gem_cavern **target**
  0.656 · meteor *hard* 0.06 · vault *hard* 0.0 (stalls at `cleared_gap1`) ·
  flood_tower *not_learnable*. Key insight: **G4 robustness and RL difficulty
  are different axes** — the certified set spreads across five grades.
- **Live curriculum round 1**: mechanics work end-to-end; hy3:free failed
  5/5 regenerating the vault from scratch under the v2.3+ bar (best attempt
  stuck between `cleared_gap2`/`in_vault`); bookkeeping bug found+fixed
  (`26b3fc4`). **Revise mode** (certified source + directive as minimal-edit
  task) was being built by an agent at snapshot time — check
  `notes/rl_agent/CURRICULUM_LOOP.md` and `git branch --list "worktree-*"`
  for its outcome. Model routing policy: easy→hy3, target→ship,
  hard→revise (Opus if hy3 fails), not_learnable→ease directive.

## Restarting a work session (local)

1. Read `OBJECTIVES.md`, this file, then the notes index below as needed.
2. Prereqs already on this machine: anaconda base python (pymunk, PIL,
   pytest, torch-cpu), node + `nodeworld/node_modules`, Godot 4.7 console exe
   + rapier in `godotworld/tools/` (gitignored), OpenRouter key in `env.py`
   (gitignored — NEVER commit).
3. Sanity: `python -m pytest tests/ -q` (439 expected) ·
   `python -m harness game verify godotworld/examples/escape.spec.json --json` ·
   `python -m harness game replay <game.js> --frames out.json`.
4. Key CLI verbs: `game new|verify|replay|attack|curriculum`, `game stats`.
5. Conventions that MUST survive: base code frozen during generation runs
   (integrity manifest); demos generated from machine-readable results; days
   on the site = real calendar days, new batches = new rows; count every
   failure (ledger); notes-per-task in `notes/` (context is reused).

## ORCD phase — how to start (the runbook is written)

**THE document: `notes/compute/ORCD_GODOT_RL_PLAN.md`** — copy-paste runbook.
Condensed path:

1. Open an Engaging session (login node). Run the **day-1 checklist** (§plan):
   module load miniforge → clone with `core.autocrlf=input` → mamba env
   (py3.12, pymunk, torch-cpu, nodejs) → `npm install` in nodeworld/ →
   **egress probe** (`srun ... curl openrouter` — compute-node internet is NOT
   promised; if absent: generate locally, verify/train remotely) → smoke
   `pytest` on `mit_quicktest`.
2. Build the **canonical certifier image** from the verbatim `.sif` definition
   in the plan (ubuntu22.04 + miniforge + node + **Linux Godot 4.7 single
   binary** + rapier `.so` from the same zip we already use + the mandatory
   one-time `--headless --import`). The three Linux-Godot unknowns are marked
   as day-1 GATES in the plan — measure, don't assume.
3. Fire the three Slurm arrays (templates verbatim in the plan):
   **certify farm** (verify+attack, bank-bound, minutes) → **G3' probe farm**
   (200k-screen everything ≈ 2-3 min/game/core; 2M only on the
   learnable-but-not-sharp band) → **curriculum batch rounds**
   (local↔cluster ping-pong: cluster grades N games, local LLM revises the
   non-targets, resubmit).
4. Results flow: scratch shards → `rsync` back → ledger merge
   (dedupe on `(game_id, seed, verdict_hash)`) → `frames.json` feeds the site
   replayer (never GIFs). Witness replay on the certifier image = the
   certificate, wherever training ran.
5. Named follow-up before scale: a `harness rl probe` CLI verb (the plan's
   probe farm currently uses a 6-line inline driver).

## Notes index (the reusable context)

| Area | File |
|---|---|
| Curriculum loop + grades | `notes/rl_agent/CURRICULUM_LOOP.md` |
| Difficulty map r1 | `notes/rl_agent/DIFFICULTY_MAP_R1.md` |
| RL architecture survey | `notes/rl_agent/LLM_RL_SYSTEMS.md` |
| G3' spike results | `notes/rl_agent/G3_PRIME_SPIKE.md` |
| Tree solver wiring | `notes/adversarial/G3_TREE_WIRING.md` |
| G4 design | `notes/adversarial/G4_DESIGN.md` |
| ORCD runbook (THE one) | `notes/compute/ORCD_GODOT_RL_PLAN.md` |
| ORCD cluster map | `notes/compute/ORCD_DEPLOYMENT.md` |
| Web replayer contract | `notes/compute/WEB_REPLAYER.md` |
| Demos audit | `notes/compute/WATCHABLE_DEMOS.md` |
| Godot lane | `notes/engines/GODOT_LANE.md` + `godotworld/SPEC.md` |
| Godot feasibility/spike | `notes/engines/GODOT_MIGRATION.md`, `godotworld/SPIKE_REPORT.md` |
| Godot+RL merge design | `notes/engines/GODOT_RL_MERGE.md` |
| Examples-bank audit | `notes/engines/GODOT_RL_EXAMPLES_AUDIT.md` |
| Skills/plugins surveys | `notes/engines/CLAUDE_GAMEGEN_SKILLS.md`, `GODOT_SKILLS_WORLDGEN.md`, `CLAUDE_GODOT_YOUTUBE.md` |
| Paper: LLM-as-a-Verifier | `notes/papers/LLM_AS_A_VERIFIER.md` |
| Parts bank | `notes/PARTS_BANK.md` + `banks/` |

## Open decisions (Elias's calls)

1. Switch the day-2 gallery from GIFs to the canvas replayer (pilot live at
   `day2/replayer_demo.html`).
2. Opus as designer for curriculum revisions / hard games (agent-backend now,
   `ANTHROPIC_API_KEY` for unattended ORCD campaigns later).
3. Spec v2 build wave (raycast sensors first) + bank→`.tscn` templates.
4. Tier-1 LLM attackers live (bulletproof grade).
5. Day-3 site page when the calendar day warrants it.
