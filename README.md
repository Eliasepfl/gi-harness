# gi-harness — text to a playable, code-certified game

Turn a natural-language prompt (*"drive a cart one full lap around an oval track"*) into a
**GDScript game** running on Godot 4.7 headless, certified by a **100% programmatic**
verifier that reads engine state directly — positions, velocities, checkpoints — and never
looks at a rendered pixel or asks a VLM. Generation, verification, adversarial attack,
RL learnability probing, and a personalized repair loop all close end-to-end in code.

## The lane (post-pivot, 2026-07-15)

The designer LLM writes a **single self-contained `.gd` file**: a plain Node implementing a
duck-typed GameAPI — `build(seed)`, `act(action)`, `state()`, `checkpoints()`,
`is_success()`, `is_failure()`, `actions()` — self-seeded RNG, no scene files, no assets,
no engine imports beyond the node tree it builds. The contract
(`harness/gen/prompts/api_gdscript.md`) carries method signatures and hard rules only —
no worked examples, no dimension bias, no magic values (anti-anchoring). Domain knowledge
comes from the [gd-agentic-skills](https://github.com/thedivergentai/gd-agentic-skills)
library (LGPLv3, read at runtime, never vendored): the godot-master orchestrator leads,
domain skills are LLM-routed per prompt.

## Verification funnel (cost-ordered; stops at first failure)

| Gate | Question | How |
|---|---|---|
| **G0** | Parses? Contract methods present? No banned APIs (OS/File/network/threads/wall-clock/unseeded RNG)? | standalone `--check-only` + scanner + contract probe |
| **G0.5** | Are all checkpoints geometrically reachable (not walled off)? | occupancy flood-fill over static footprints — necessary, not sufficient |
| **G1** | Deterministic? Actions alive? (context-aware: a brake is probed on a *moving* body) | serve host, twin rollouts |
| **G2** | Goal well-formed, not pre-satisfied, milestones latchable? | t=0 facts + guided episodes |
| **G3** | Actually solvable? | Go-Explore tree solver → replayable witness `{seed, actions}` |
| **G4** | Robust? (escapes, single-action wins, shortcut-vs-gating, softlocks) | adversarial fuzz ladder → `open / hardened / bulletproof` |
| **G3′** | Learnable by an RL agent? | SB3 PPO/A2C/DQN, in-scene **batched vec env** (N instances / 1 Godot proc / 1 socket, ~9× throughput), plateau-patience early stop |

A game "certified" means G0–G3 passed and the witness replays bit-identically through the
serve host (`godotworld/serve_game.gd`, length-prefixed JSON over TCP, env-scrubbed).

## The feedback loop (`harness game harden`)

Post-cert oracle outcomes become **personalized repair directives** applied to the game's
*current source* (never a blind regen): G4 single-action-win → "add a real obstacle";
broken gating → names the bypassed checkpoint; RL plateau → names the two checkpoint keys
around the stall; RL still improving → keep training (no directive). Convergence-guarded:
max rounds per finding, `REPAIR_STALLED` on repeated fingerprints, a certified file is
never overwritten by a fix that fails re-certification. Every directive + verdict lands in
the ledger.

## Demos

`harness game capture <game.gd>` replays the certified witness with a **zero-contact
visual overlay** (proxies mirror body transforms read-only; dressed-vs-undressed state
trails are byte-identical by test) and records real in-engine frames via software GL
(llvmpipe + Xvfb — works headless on the cluster, no GPU). 2D games render in their own
plane; 3D games get camera + light + optional low-poly assets from the bank
(`assets/manifest.json`, LLM-routed body→asset matching). `godotworld/demo_player.gd`
lets a human watch or drive any game in desktop Godot.

## Quickstart (ORCD cluster)

```bash
module load apptainer/1.4.2   # gi-certifier.sif = the canonical certifier
apptainer exec -B /orcd gi-certifier.sif bash -lc '
  cd ~/gi &&
  python -m harness game new "hop across moving platforms" --engine gdscript --backend openrouter --json &&
  python -m harness game harden scenes/games/<slug>/<slug>.gd --backend openrouter --json'
# capture a demo (needs host Xvfb — see scripts/capture_demo.sh)
scripts/capture_demo.sh scenes/games/<slug>/<slug>.gd demo.gif
```

Designer model: `z-ai/glm-5.2` via OpenRouter (`env.py`, gitignored — never commit).
Reasoning models need output-budget headroom: 32k ceiling, thinking-off salvage on null
content, hard `ENV_ERROR` (never a silent template) when an explicit backend fails.

Cluster discipline: 1 game per Slurm array task on `mit_preemptable`, ≤200 tasks,
`HARNESS_GODOT_SPEEDUP=8` (paired tick/time scaling — tick-identical), per-task ledger
shards merged with dedupe. Never run Godot bare on a login node.

## Layout

| Area | What |
|---|---|
| `harness/gen/` | gamegen (multi-turn repair loop), skill routing, feedback compiler + harden driver |
| `harness/verify/` | gameverify funnel, gd/godot executors, G4, reachability, capture |
| `harness/rl/` | serve env, batched vec env, SB3 trainer, g3′ certify |
| `godotworld/` | serve host, capture host, visual dresser, asset loader, demo player |
| `assets/` | curated low-poly bank (manifest committed; models rebuilt by `harness/demo/curate_bank.py`) |
| `scenes/games/` | generated games (artifacts, not tracked) |
| `notes/` | design notes + decision history (`notes/engines/GDSCRIPT_LANE.md` is the pivot doc) |
| `harness/legacy/`, `nodeworld/` | frozen py/js lanes (regression floor, not the default) |

`STATE.md` is the living snapshot; `OBJECTIVES.md` the roadmap; `CONTRACTS.md` the
normative interfaces (historical v1 sections marked).
