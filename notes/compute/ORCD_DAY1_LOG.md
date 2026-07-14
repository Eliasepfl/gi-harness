# ORCD day-1 execution log — 2026-07-14

> Running record of the §2 checklist of `ORCD_GODOT_RL_PLAN.md`, executed on
> login007. Companion to the GO checklist (§7). Gates marked as they pass.

## Environment facts (measured)

- Login node: `login007`, x86_64, Rocky/EL9 (kernel 5.14.0-570).
- Quota at start (`~/orcd/.quota`, 14:52): HOME 173.0/200 GB (86.5%) — tight;
  apptainer cache+tmp therefore pointed at scratch
  (`~/orcd/scratch/gi/apptainer-{cache,tmp}`). SCRATCH 828/1024 GB.
- GitHub auth: SSH key on the cluster already registered (`Eliasepfl`) —
  cloned via `git@github.com:Eliasepfl/gi-harness.git`, no PAT needed.
- Clone: 3.4 MB, LF-clean (renormalize produced zero diffs; dos2unix no-op).
  `core.autocrlf=input` set globally BEFORE clone, `.gitattributes * text=lf`
  added (untracked for now).
- Checkout lives at `/home/enaha/GI/gi-harness`, symlinked to `~/gi` so every
  verbatim path in the plan/templates works.
- `env.py` restored by hand next to the checkout (OPENROUTER_API_KEY +
  OPENROUTER_MODEL — never committed).
- `notes/compute/gi-certifier.def` did not exist as a file — materialized
  verbatim from plan §1.

## Runbook deviations found

1. **`.def` §1 npm step fails at build time** (attempt 1, exit 127):
   `%environment` exports do NOT apply during `%post`, so npm's
   `#!/usr/bin/env node` shebang can't find `node`. Fix committed to the
   `.def`: `export PATH=/opt/conda/envs/gi/bin:$PATH` in `%post` before the
   npm install. Torch/pymunk/conda stages were already green when it died.
2. **SIF packing dies on the login node without thread caps** (attempt 2):
   `mksquashfs … FATAL ERROR: Failed to create thread`. Login node has 448
   cores but the user slice is `CPUQuota=400% / MemoryMax=10G`; mksquashfs
   spawns a thread per visible core. Fix: build with
   `--mksquashfs-args "-processors 4 -mem 2G"` (apptainer 1.4.2 supports it).
3. **G3' is JS-only** (`harness/rl/certify.py` bridges via `JsExecutor` +
   `PlanckEnv` only) — the §2 step-7 micro-probe cannot fall back to the
   committed `.py` puck game; it needs the showcase rsync like the rest of
   step 7.
4. **No witness travels with git and no CLI verb replays an external
   witness** — verify emits `report["witness"]` to stdout only. Step 8 needs
   (a) a small witness JSON emitted on the Windows box for a *committed* game
   (the puck game works — no full rsync needed for this gate) and (b) a
   ~6-line `run_batch` replay driver (pattern: `certify.py:45-52`) — now
   staged at `~/orcd/scratch/gi/replay_witness.py` (engine-routed py/js/godot).
5. **§3a/§3b manifest line is a defect**: `git ls-files
   'scenes/games/**/game.js'` returns ZERO games *forever* — `scenes/games/`
   is gitignored, and `ls-files` only lists tracked files, so it stays empty
   even after the rsync. Corrected in the staged farm scripts
   (`~/orcd/scratch/gi/{certify_farm,g3p_farm}.sbatch`):
   `find scenes/games -name game.js` (+ `git ls-files` for the tracked godot
   specs). The §3c template was already immune (`ls $IN/games/*/game.js`).
6. **`.def` missed the test deps** — `tests/test_gamegen.py`/`test_generator.py`
   import `anthropic`+`httpx` at top level (collection ERROR without them),
   godot schema tests need `jsonschema`, viewer watch tests import `pygame`
   unguarded. All are in `requirements.txt`; added one pip line to the `.def`
   (attempt 4). Also added `requests` — with compute-node egress confirmed OK
   and the key available, the G4 tier-1 LLM-attacker lane can actually fire
   on-cluster (it cleanly reports `skipped_no_key` when keyless).
8. **`nodeworld/runner.js` truncated its output at exactly 64 KiB on Linux**
   (`js_bad_output: unparseable JSONL ... char 65536`; also the 2 pytest
   fails). Root cause: `main().then(() => process.exit(0))` — on Linux a pipe
   write is ASYNC and `process.exit()` discards everything past the 64 KiB
   pipe buffer; Windows flushes synchronously, so 443 tests were green
   locally. Fixed in `runner.js` (exit via an empty-write callback, which
   queues behind all pending writes). This is exactly the class of cross-OS
   bug the day-1 gates exist for. NEEDS COMMIT.
9. **Godot in-image thrashes on the login node**: traverse verify timed out
   at 180 s with `sys` time ≫ `user` (Godot sizes worker pools from the 448
   visible cores while the user slice caps at CPUQuota=400%). Under
   `srun -c 4` it verifies in seconds; `taskset -c 0-3` on the login node
   also works but stays sys-heavy. RULE: anything that launches Godot runs
   under Slurm (or at minimum taskset), never bare on a login node.
10. **`/orcd` is not bind-mounted by apptainer** (`~/orcd/scratch` is a
   symlink into `/orcd`): in-container access to scratch needs
   `apptainer exec -B /orcd …`. The plan's §3 templates all write scratch
   from inside the container — every one needs the bind. Patched in the
   staged farm scripts.
11. **`python ~/orcd/scratch/gi/rl_probe.py` can't `import harness`** —
   `sys.path[0]` is the SCRIPT's dir, not the cwd. §3b needs
   `PYTHONPATH=~/gi` on the rl_probe invocation. Patched in the staged
   scripts.
7. Conformance audit verdicts (Opus, read-only): CLI verbs+flags ✓,
   `g3_prime(game_path, budget_steps=)` + return keys ✓, `HARNESS_GODOT_EXE`/
   `HARNESS_NODE`/`NODE_PATH`-planck resolution ✓ (bare `require("planck")`
   falls back to NODE_PATH; no `env=` stripping). GodotExecutor auto-runs the
   bare `--headless --import` on first use, matching the #77508 fix. Caveats:
   `game replay --frames` routes only js/py (Godot specs error — §3a replay
   line is a caught no-op for the godot manifest); `g3_prime` *asserts*
   bridge_ok (raises on a broken bridge rather than returning False).

## Gate results

| Gate (§2 step) | Verdict | Evidence |
|---|---|---|
| 4. **EGRESS probe** | **compute-node internet OK** | `srun -p mit_quicktest -t 5 curl openrouter.ai/api/v1/models` → HTTP 200 (job 17910243) |
| 3. `.sif` build | **PASS** (attempt 4, 1.18 GB, in-build `godot --headless --version` green) | logs: `~/orcd/scratch/gi/logs/sif-build{,2,3,4}.log` |
| 5. in-image pytest | **PASS** — 437p/2f/4s on first run; the 2 fails were the runner.js Linux flush bug (deviation 8), now fixed → 439p/4s | `~/orcd/scratch/gi/logs/pytest-gate.log`, 5m49s on a quicktest node |
| 6. Godot `--import` + traverse verify | **PASS** — `.godot/` present (no extension_list.cfg = stock physics, expected); traverse passed=true, witness 67 ticks, 5/5 checkpoints | via `srun` — see deviation 9 (login-node thread-thrash) |
| 7. JS lane + G3' micro-probe | **PASS** — gem_cavern verify passed=true (post-fix), py-lane puck game passed=true; **G3' 200k: learnable=true, stoch_sr=0.5, bridge_ok=true, 64 s wall @ ~2 000 sps** (matches the plan's throughput anchor) | job 17911xxx on quicktest |
| — certify-farm shakeout | **PASS 9/9** — job **17912251** on **mit_preemptable** (Elias: preemptable gets CPUs faster; 17912131 sat Priority-pending on mit_normal and was moved): all 6 showcase JS games verify passed=true AND re-graded **G4 hardened** at tier-0 **on the cluster** (matches the day-2 Windows results — the cross-OS certification story holds); 3/3 godot specs passed=true (their g4/frames are empty: G4-on-godot + replay-on-godot not wired yet, known plan §B3 gap). 9 ledger shards written; **tier-1 LLM attackers stay OFF** until Elias decides open-decision #2 (key+egress in place, one-flag flip) | run dir: `~/orcd/scratch/gi/runs/17912251/`; scripts: `~/orcd/scratch/gi/{certify_farm,g3p_farm}.sbatch` |
| — `harness ledger merge` | **built + green** (was a named follow-up in the plan §5/GO): `merge_shards` in `harness/core/telemetry.py` + `ledger merge` CLI verb, dedupe on `(game_id, seed, verdict_hash)` with volatile fields (`ts`, `wall_s`) excluded from the hash; 7 new tests (15/15 telemetry file green in-image). Real-data proof: shakeout's 9 shards → `runs/ledger.jsonl` 9 appended; re-merge → 0 appended / 9 duplicates dropped (idempotent). NEEDS COMMIT | `harness ledger merge <shards|dir> --into runs/ledger.jsonl` |
| 8. Windows-witness determinism replay | **RESOLVED BY DECISION (Elias, 14 juil.): staying Linux-only** — the `.sif` IS the canonical certifier by construction; Windows witnesses are candidates, not gates, and the shakeout already re-certified all 6 showcase games in-image (fresh Linux witnesses + hardened grades). `replay_witness.py` stays staged for future external-witness checks | no user action needed |

**Egress consequence:** risk #4 resolves favorably — generation/curriculum LLM
calls *can* run on compute nodes if we ever want them there. Default stays
login-local (cheap, rare calls), but the ping-pong constraint in §4 is now
optional, not forced.

## Decisions (Elias, 14 juil., cluster session)

- **Linux-only from now on** — gate 8 dropped (see table); the pinned `.sif`
  defines certification. Local Windows box remains a dev/generation seat only.
- **G4 tier-1 reframed**: the tier-1 attacker is not specifically an
  OpenRouter LLM — it is ANY agent whose objective is to drive the
  environment into a **stale state** (softlock / success made unreachable),
  proving the environment unreliable. RL adversaries, scripted searchers, and
  LLM agents are all admissible implementations. Research on porting
  `godot_rl_agents` capabilities (multi-agent, parallel training, automated
  testing) to this is in `notes/engines/GODOT_RL_AGENTS_CAPABILITIES.md`.
- Array cap ≤200 tasks; prefer mit_preemptable (see `ORCD_RUN_MANAGEMENT.md`).
- **LIBRARY-FIRST PRINCIPLE (Elias, 14 juil.)**: anything agent-learning
  related is NOT written from scratch — use the trusted library
  (godot_rl_agents @207b6f4 and the frameworks behind it). Clearability is a
  project principle; from-scratch learning code that a library covers gets
  replaced. This also serves the planned encapsulation of the pipeline as a
  Claude MCP server / skills later — standard interfaces wrap cleanly,
  bespoke ones don't. Scope: the LEARNING machinery (trainers, env protocol,
  sensors, adversaries). The typed-state verify + witness-replay oracle
  remains ours by design.

## What did NOT travel with the clone (confirmed)

- `scenes/games/` carries only `a_little_ice_puck_game_a1.py` in git. The six
  v2.3 JS showcase games (gem_cavern, vault, …) need
  `rsync -av "scenes/games/" orcd-login.mit.edu:~/gi/scenes/games/` from the
  local (Windows) machine — **user action**; blocks §2 step 7 (JS lane) and
  the certify-farm manifest beyond the godot examples.
- `godotworld/examples/{traverse,escape,collect2}.spec.json` ARE committed →
  Godot gates runnable now.
- No `runs/` ledger (fresh start, per plan).
