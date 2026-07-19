# gi-harness: text to a playable, code-certified game

Turn a natural-language prompt (*"drive a cart one full lap around an oval track"*) into a
**GDScript game** on Godot 4.7 headless, certified by a **100% programmatic** verifier that
reads engine state directly (positions, velocities, checkpoints) and never looks at a
rendered pixel or asks a VLM. Generation, verification, adversarial attack, RL learnability
probing, and a personalized repair loop all close end to end in code.

**Repo:** https://github.com/Eliasepfl/gi-harness

**Showcase / write-up:** https://eliasepfl.github.io/gi-harness/blog/gi/

## What a game is

The designer LLM writes a **single self-contained `.gd` file**: a plain Node implementing a
duck-typed GameAPI (`build(seed)`, `act(action)`, `state()`, `checkpoints()`,
`is_success()`, `is_failure()`, `actions()`), self-seeded RNG, no scene files, no engine
imports beyond the node tree it builds. The contract (`harness/gen/prompts/api_gdscript.md`)
carries method signatures and hard rules only: no worked examples, no dimension bias, no
magic values (anti-anchoring). Domain knowledge comes from the
[gd-agentic-skills](https://github.com/thedivergentai/gd-agentic-skills) library (LGPLv3,
read at runtime, never vendored): the `godot-master` orchestrator leads, domain skills are
LLM-routed per prompt.

## Verification funnel (cost-ordered, stops at first failure)

| Gate | Question | How |
|---|---|---|
| **G0** | Parses? Contract methods present? No banned APIs (OS/File/network/threads/wall-clock/unseeded RNG)? | standalone `--check-only` + scanner + contract probe |
| **G0.5** | Are all checkpoints geometrically reachable (not walled off)? | occupancy flood-fill over static footprints (necessary, not sufficient) |
| **G1** | Deterministic? Actions alive? (a brake is probed on a *moving* body) | serve host, twin rollouts |
| **G2** | Goal well-formed, not pre-satisfied, milestones latchable? | t=0 facts + guided episodes |
| **G3** | Actually solvable? | Go-Explore tree solver, reduced to a replayable witness `{seed, actions}` |
| **G4** | Robust? (escapes, single-action wins, shortcut-vs-gating, softlocks) | adversarial fuzz ladder to `open / hardened / bulletproof` |
| **G3'** | Learnable by an RL agent? | SB3 PPO/A2C/DQN, in-scene batched vec env, plateau-patience early stop |

A game is "certified" when G0 to G3 pass and the witness replays bit-identically through the
serve host (`godotworld/serve_game.gd`, length-prefixed JSON over TCP, env-scrubbed).

## The feedback loop (`harness game harden`)

Post-cert oracle outcomes become **personalized repair directives** applied to the game's
*current source* (never a blind regen): G4 single-action-win becomes "add a real obstacle";
broken gating names the bypassed checkpoint; an RL plateau names the two checkpoint keys
around the stall. Convergence-guarded: max rounds per finding, `REPAIR_STALLED` on repeated
fingerprints, and a certified file is never overwritten by a fix that fails re-certification.

## Demos and the exporter

`harness game capture <game.gd>` replays the certified witness with a **zero-contact visual
overlay** (proxies mirror body transforms read-only) and records real in-engine frames via
software GL (llvmpipe + Xvfb, headless on the cluster, no GPU). 2D games render in their own
plane; 3D games get camera, light, and optional low-poly assets from the bank
(`assets/manifest.json`). The exporter dumps each certified play as an **episode package**
(`episode.json` + `steps.jsonl` + one PNG frame per tick, T states aligned to T frames): the
same play as code-truth and as pixels, the signal a reward or world model can learn from.

## Setup

```bash
git clone https://github.com/Eliasepfl/gi-harness gi && cd gi
conda create -n godot-rl python=3.11 -y && conda activate godot-rl
pip install -r requirements.txt
# put your model key in env.py (gitignored, never commit):
#   OPENROUTER_API_KEY = "sk-or-..."
```

## Install the live-editor stack

Every step below comes straight from each project's own documentation, linked. Nothing here
is custom or unofficial to download.

- **Godot 4.7** (MIT): the engine. Download the editor binary: https://godotengine.org/download
- **godot-ai** (MIT): the MCP plugin that gives an agent tools inside the live editor. Needs
  the `uv` package manager (per its README). https://github.com/hi-godot/godot-ai
  ```bash
  # prerequisite: the uv package manager, then:
  git clone https://github.com/hi-godot/godot-ai
  cp -r godot-ai/plugin/addons/godot_ai your-project/addons/
  # Project > Project Settings > Plugins > enable "Godot AI"  (serves MCP at 127.0.0.1:8000/mcp)
  ```
- **Hermes agent** (MIT): the agent that drives the editor, point it at any model.
  https://github.com/nousresearch/hermes-agent
  ```bash
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash && hermes setup
  ```
- **gd-agentic-skills** (LGPLv3): GDScript craft knowledge the agent reads at runtime.
  Complements godot-ai, does not clash with it. https://github.com/thedivergentai/gd-agentic-skills
  ```bash
  npx skills add thedivergentai/gd-agentic-skills/skills/godot-master -g -a <agent> -y
  ```
- **godot-rl-agents** (MIT): the RL bridge behind the G3' learnability probe.
  ```bash
  pip install godot-rl
  ```

## Quickstart (offline harness, ORCD cluster)

```bash
module load apptainer/1.4.2   # gi-certifier.sif is the canonical certifier
apptainer exec -B /orcd gi-certifier.sif bash -lc '
  cd ~/gi &&
  python -m harness game new "hop across moving platforms" --engine gdscript --backend openrouter --json &&
  python -m harness game harden <generated>.gd --backend openrouter --json'   # game new --json prints the .gd path
# capture a demo (needs host Xvfb, see scripts/capture_demo.sh)
scripts/capture_demo.sh <generated>.gd demo.gif
# export the dataset (state + pixels, aligned)
scripts/export_library.sh out/dataset --limit 20
```

The whole verifier is also exposed as MCP tools any agent can call:
```json
{ "mcpServers": { "harness": { "command": "python",
  "args": ["-m", "harness.mcp_server", "--transport", "stdio"] } } }
```

Designer model: `z-ai/glm-5.2` via OpenRouter (`env.py`, gitignored, never commit). Swap it
for a free model like `tencent/hy3:free` in `env.py`. Run one game per Slurm job on
`mit_preemptable`; never run Godot bare on a login node.

## Layout

| Area | What |
|---|---|
| `harness/gen/` | gamegen (multi-turn repair loop), skill routing, feedback compiler + harden driver |
| `harness/verify/` | gameverify funnel, gd/godot executors, G4, reachability, capture |
| `harness/rl/` | serve env, batched vec env, SB3 trainer, g3' certify |
| `harness/mcp_server.py` | the funnel as four MCP tools (extract, verify, capture, atlas) |
| `godotworld/` | serve host, capture host, visual dresser, asset loader, demo player |
| `assets/` | curated low-poly bank (manifest committed) |
| `tests/` | the harness's own test suite |

Budget caps live in code as defaults in `harness/designer/budgets.py`. GDScript on Godot is
the only lane.
