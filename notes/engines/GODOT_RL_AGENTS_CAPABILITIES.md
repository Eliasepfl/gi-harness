# godot_rl_agents advanced capabilities — are we using them, and how do they port to VERIFICATION?

> **multi-agent research, Fable orchestrator + Opus agents, 2026-07-14.** Seven deep-dive agents read
> the pinned library (`~/GI/godot_rl_agents` @ **207b6f4**) + examples (`~/GI/godot_rl_agents_examples`
> @ **d659636**) at file:line; nothing was run (login node). Answers **Elias's question**: *are we using
> all of godot_rl_agents' possibilities — Automated testing, Multi-agent, Parallel training, Multi-process
> parallel training — and how could they port to the TESTING/VERIFICATION part of the pipeline?* Sits
> atop `GODOT_RL_BENCH_AND_PIPELINE.md` (A/B/C), `GODOT_AI_TOOLING_AUDIT.md` (§3 B4, §4),
> `G3_PRIME_SPIKE.md`, `G4_DESIGN.md`. The plugin GDScript is an **empty submodule** at 207b6f4 —
> `sync.gd`/`ai_controller`/sensor refs read from the examples clone. Both self-report protocol **v0.7**
> (`godot_env.py:19-20`, `sync.gd:16-17`).
>
> **LIBRARY-FIRST amendment (Elias, same day, after synthesis):** anything agent-LEARNING related is
> not written from scratch — trusted libraries only, for clearability and because the pipeline will
> later be encapsulated as a Claude MCP server / skills (standard interfaces wrap cleanly). Scope:
> trainers, env protocol, sensors, adversaries. The typed-state verify + witness-replay ORACLE stays
> ours by design. Two verdicts amended under the principle, marked **[LF]** below: the G3' trainer
> (vendored CleanRL-mirror → migrate to SB3, §6.7) and the RL-adversary rung (§4 ladder step 2 rides
> the same migration). The INNER-dialect deviation in §3 is the principle's sanctioned exception —
> it is the certifier, and the library's semantics are documented not-witness-grade.

---

## 1. CAPABILITY MAP

| capability | what it is (one line) | using today | port target | verdict |
|---|---|---|---|---|
| **Sync 0.7 wire protocol** | 4-byte-LE-length + UTF-8 JSON; Python **binds/listens**, Godot **connects out**; verbs {handshake, env_info, reset, step, close} (`godot_env.py:331-351,494-496,353-431`) | partial (our serve is a stdio sketch, opposite orientation) | serve mode (OUTER rung) | **adopt** wire+verbs for outer; **mirror** for inner certifier |
| **Synchronous step-lock** | gate on `tick % action_repeat`, pause SceneTree while reading socket, hold action across N ticks (`sync.gd:156-198`) | partial (our K=6 batch) | G3'-on-godot, serve | **adopt** (== our K=6) |
| **Fixed-delta under speedup** | `physics_ticks_per_second=speedup*60` AND `time_scale=speedup` → per-tick Δ cancels to 1/60 (`sync.gd:61-62`) | no | G3' scale-up | **adopt** (speedup 8 safe for replay) |
| **Seed/reset/done semantics** | `env_seed` seeds GLOBAL rng ONCE at init, reset is a `needs_reset` flag (no scene reload), `done`=both term+trunc (`sync.gd:411-413,482-485`; `godot_env.py:221-222`) | no | automated verification | **skip/rebuild**: reseed-on-reset, seeded full rebuild, split term/trunc |
| **In-scene multi-env batching** | N AIControllers in group `AGENT`, one process, ONE socket, batched obs/act arrays (`sync.gd:294-311,488-516`; `godot_env.py:371`) | no | **G3' scale-up (primary)** | **adopt** — the single-core throughput win |
| **n_parallel multi-process** | k separate Godot procs at `port+p`, seed`+p`, stepped in a Python for-loop (`stable_baselines_wrapper.py:29-38`) | no | — | **skip** (1-core thrash + k ports; use Slurm array) |
| **`call(method)` RPC** | name-dispatches an arbitrary GDScript method on EVERY agent (`godot_env.py:260-268`; `sync.gd:449-455`) | no | G4 introspection | **mirror-idea** as a FIXED whitelisted verb; never expose name-dispatch |
| **`reset_after` step-cap watchdog** | agent auto-`needs_reset` once `n_steps>1000` (`ai_controller_2d.gd:7,76-78`) | partial (our `max_ticks`) | G4 stale-state backstop | **adopt** as last-resort trip (not the oracle) |
| **RaycastSensor2D/3D** | pure-GDScript `force_raycast_update` cone, normalized proximity, stateless, headless, no pixels (`RaycastSensor2D.gd:71-118`) | no | spec-v2 sensors | **adopt** — vendor 2D verbatim (~120 LOC MIT) |
| **Grid / RGB-camera sensors** | GridSensor2D is Area2D-signal, stateful, **no `reset()`** (buffer leaks across episodes, `ISensor2D.gd:24-25`); RGBCamera = pixels (`RGBCameraSensor3D.gd:8`) | no | — | **skip** (stale-state bug / pixels violate rule) |
| **AIController contract** | `get_obs→{obs:[f]}`, `get_action_space`, `set_action`, `get_done`/`needs_reset`, `get_obs_space→box` (`ai_controller_2d.gd:39-113`) | partial (`env.py:33-56` already mirrors it) | G3'-on-godot parity, serve | **mirror-idea**: interface yes, obs LAYOUT stays ours (`env.py:101-133`) |
| **Per-agent obs/reward/action arrays** | multi-agent = N controllers, parallel index-keyed arrays over one socket (`sync.gd:527-554`) | no | G4 protagonist-vs-adversary | **adopt** the index-array contract (lock ordering) |
| **Learned multi-policy** | per-agent `policy_name` → rllib PolicySpec + PettingZoo ParallelEnv (`petting_zoo_wrapper.py:62-78`; `rllib_example.py:68-77`) | no | (learned adversary, deferred) | **defer** — heavy pins (ray[rllib]≤2.38 + PettingZoo); our CleanRL is single-policy |
| **MA templates** | MultiAgentSimple (2 policies), RobotVolleyball (self-play), DefendTheGoal (**scripted** turret, not an agent) (`turret.gd:38-47`) | no | G4 scripted adversary | **adapt** DefendTheGoal pattern (zero Python, deterministic) |
| **Automated-test patterns** | param-over-games conformance (`test_godot_env.py:6-82`), ONNX differential (`stable_baselines_export.py:114-146`), IPS meter (`benchmark_env.py`) | no | G0-G3 CI, cross-lane determinism gate, sps gate | **mirror-idea** (patterns only; asserts too loose) |
| **SB3 wrapper** | VecEnv PPO, VecMonitor stats, onnx export; deps are BASE `install_requires` (`stable_baselines_wrapper.py:13,155-176`) | no | part-A env×method×seed bench | **adapt**: one SB3 proc per array task (1 core/1 seed), NOT its n_parallel |
| **CleanRL single-file PPO/PQN/GRU** | copied `ppo_continuous_action` (64×64 tanh, n_steps=32, ent 1e-4) + PQN + GRU siblings (`examples/clean_rl_example.py:109-138`) | partial (our G3' mirrors it) | G3' probe | **[LF] migrate**: G3' trainer → SB3 PPO (library) behind our env seam; vendored PPO retires once parity on the difficulty map is proven (§6.7) |
| **sample-factory (APPO)** | only true async/multiprocess engine, env_agents=16, many procs+ports (`sample_factory_wrapper.py:106-149`) | no | multi-core adversary (if fat reservation) | **skip** unless greenlit; pin `==2.0.0` if adopted |
| **Per-op socket timeout** | 60 s on accept only; per-conn recv timeout **commented out** → hung game blocks forever, then `json.loads(None)` crash (`godot_env.py:22,346-348,462-492`) | no | G4 stale-state deadline | **adapt** — the defect to invert: recv timeout → typed STALE verdict |
| **Cmdline params / port scheme** | `--port --env_seed --action_repeat --speedup`; DEFAULT_PORT **11008 hard-coded**, no free-port finder (`godot_env.py:21`; `sync.gd:386-417`) | no | Slurm port policy | **adopt** the arg plumbing; **rebuild** port assignment per task |

---

## 2. THE FOUR ELIAS NAMED — what the library has, and the port to verification

**(1) Automated testing.** Thin, training-centric, NOT a verifier. `test_godot_env.py:6-82` launches
5 prebuilt binaries on hard-coded ports, runs 2 resets × 10 random-action steps, asserts only a loose
contract (`len==n_agents`; reward float/int; done bool; info dict). Plus a useful **differential** test
(`verify_onnx_export`, `stable_baselines_export.py:114-146`: 10 obs through torch vs onnxruntime,
`allclose atol=1e-5`) and an IPS benchmark (`benchmark_env.py`). **NO** NaN/finite guards anywhere (grep
clean), no reachability oracle, only the blunt `reset_after` cap, recv timeout disabled; "TestExamples"
is sample games, not an assertion harness (`examples/TestExamples/readme.md:1-3`). → **Port to CI:**
(a) `@parametrize`-over-games conformance matrix asserting our **typed-state schema** (strict
field/type/range), far stricter than "reward is a number"; (b) the `verify_onnx_export` differential
shape → a **cross-lane determinism gate** (same spec+witness through js/py/godot, identical typed-state
trajectories, hard-fail on first divergence); (c) `benchmark_env.py` IPS loop as the ~2000-sps gate;
(d) make handshake mismatch **fatal** (theirs warns, `sync.gd:325-328`). Fuzz tiers / softlock oracle /
tree cross-check we **build ourselves** — no equivalent exists.

**(2) Multi-agent.** Real and cheap: every AIController `add_to_group("AGENT")`
(`ai_controller_2d.gd:30-31`); `sync.gd` group-collects (`:294-295`), buckets by `control_mode`, ships
per-agent obs/reward/done as index-keyed arrays over ONE socket (`:527-554`), each agent its own
space + `policy_name`. Templates: **MultiAgentSimple** (2 policies+action spaces), **RobotVolleyball**
(self-play, opposing rewards), **DefendTheGoal** (a **scripted** turret NOT in group AGENT,
`turret.gd:38-47`); learned heterogeneous policies only on the rllib/PettingZoo path (heavy pins). →
**Port to G4:** protagonist + adversary in ONE deterministic world, one process/port/core — the
reframed tier-1 attacker. Use the **scripted/searched** DefendTheGoal pattern (tree-solver/fuzzer
searches the adversary's admissible param space), **not** the learned path — single-policy CleanRL
can't express asymmetric policies (`clean_rl_wrapper.py:82-92`). Adopt the index-array contract but
**lock agent ordering from the spec**: alignment is implicit (scene-tree order + control_mode) and a
reorder silently misaligns actions with bodies.

**(3) Parallel training.** = **in-scene batching**: N AIControllers step in lockstep, N transitions per
socket round-trip (paper ~12k interactions/s on a 4-core laptop, arXiv:2112.03636); composes with
`--speedup` (physics ticks scale, Δ pinned, `sync.gd:61-62`). → **Port to G3'/G4:** build the spec
world with N duplicated AIController subtrees in group AGENT → one headless process = N envs/core, one
port/boot, batched inference. The **primary** single-core scale lever; keep `n_parallel=1`, get
across-spec parallelism from the Slurm array. Today's `godot_exec.py` is fire-and-collect (pre-baked
actions, no live loop), so this is real new plumbing (live socket + reset/step), not a flag.

**(4) Multi-process parallel training.** `n_parallel` spawns k Godot procs at `port+p`, seed`+p`,
stepped in a **synchronous** Python for-loop (`step_async` is a fake inline alias,
`stable_baselines_wrapper.py:144-152`). Every wrapper uses a different offset (CleanRL/SB3 `port+p`; ray
`worker_index+DEFAULT_PORT+10` `ray_wrapper.py:71`; SF `base_port+1+env_id`) atop hard-coded 11008 with
no free-port finder — a **direct Slurm collision hazard** (SO_REUSEADDR does NOT allow two live
listeners; needs SO_REUSEPORT, unset). → **AVOID the mechanism** (k procs/core thrash + k ports); take
only the **port-derivation idea**: `port = base + SLURM_ARRAY_TASK_ID*stride` (or bind-to-0 + read
`getsockname()`), bind-checked, for the socket lanes (2b/2c/serve).

---

## 3. THE PROTOCOL DECISION — adopt-their-Sync vs our serve verbs

**Resolved (dual-dialect `runner.gd`):** do NOT swap our certifier onto their protocol; do NOT reinvent
the outer rung. Three tiers share ONE synchronous stepping core.

- **OUTER (serve certified games to free trainers): SPEAK Sync 0.7 verbatim** — exact frame (4-byte-LE
  length + UTF-8 JSON, **Python-listens / Godot-connects-out**, `godot_env.py:331-351,494-496` vs
  `sync.gd:368-383`) + verbs {handshake, env_info, reset, step, close} with the discrete/continuous+box
  schema (`godot_env.py:353-431`), so SB3/CleanRL/SF/Ray drive us unmodified. **Inversion vs our stdio
  sketch: `runner.gd` CONNECTS OUT to a Python port, doesn't listen** (matches audit B4 TCP decision —
  stdout log spam makes stdio JSON fragile). Pin MAJOR '0'/MINOR '7' and pin Python to a **0.7 git
  commit, not the stale pip tag**.
- **INNER (G3'/G4): KEEP our determinism-first verbs, mirror the SHAPE not the code.** Shipped protocol
  is **not witness-grade** — init-only global seed (`sync.gd:411-413`, no reseed on reset), flag reset
  trusting game code (`sync.gd:482-485`), `done`=term=trunc (`godot_env.py:221-222`), and `call`
  name-dispatches arbitrary GDScript (`godot_env.py:260-268`, the run_script class rejected in the MCP
  audit). Inner ADDS: reseed-on-reset, seeded full rebuild, split term/trunc, a per-op deadline → typed
  **STALE** signal (replacing 60 s→None→crash), and **no `call` verb**.
- **STEPPING CORE (shared): MIRROR** gate-on-`(tick % action_repeat)` + pause-tree-while-reading + hold
  action across N ticks (`sync.gd:156-198`), and `physics_ticks_per_second=speedup*60`/
  `time_scale=speedup` (`sync.gd:61-62`) — pin BOTH; for verification keep integer ticks, never vary
  speedup mid-run, and avoid `--fixed-fps` (space-not-`=` parse bug, `godot_env.py:316`).

**Recommendation:** *speak Sync 0.7 on the outer rung (free trainers); build determinism-first verbs on
the inner rung; share one stepping core. Never let their protocol BE the certifier.*

---

## 4. STALE-STATE TIER-1 (condensed design; refs preserved)

Machinery mostly exists: `g4.py` already ships a `stuck` outcome, the `open|hardened|bulletproof`
grader (`g4.py:803`), the pattern registry, the referee. This adds a real ORACLE and folds a hard
`softlock` outcome in. No new engine code — rides `treesolve.py`/`statetree.py`/`g4.py`.

**Oracle (typed-state, no pixels).** Cheap high-recall TRIGGERS gate one expensive high-precision
ORACLE; triggers alone never fail a game.
- **1a state-hash cycling (trigger):** `statetree.fingerprint()` (`statetree.py:90`) + `fp_delta()`
  (`:113`); flag no checkpoint latched for `STUCK_WINDOW` ticks (`g4.py:81`, from `ep["checkpoints"]`)
  AND fingerprint set closes a cycle (`fp_delta<EFFICACY_EPS=1e-3`, `gameverify.py:54`). Rides a
  replayed episode (~free). FP: legit periodic motion → trigger only.
- **1b entity out-of-reach (trigger):** a success-required entity absent from `final_snapshot` or a
  dynamic one escaped `world+ESCAPE_MARGIN` (`world.in_bounds` `world.py:394`; `ESCAPE_MARGIN=200`;
  `ep["oob"]` `g4.py:258,283`) → structurally unreachable. Trigger only.
- **1c bounded tree-refutation (the real oracle):** worlds deterministic given
  `(game,engine,seed,action_prefix)` (`statetree.py:14-24`), so the snapshot **is** the prefix `P`.
  Fresh `StateTree`, plant `P` as a realised leaf, run the **same Go-Explore solver that certified G3**
  on continuations of `P` at horizon `len(P)+H` (`treesolve.py:222-241`, `_random_tail:101`,
  milestone-greedy `_select_leaves:119-149`). **Verdict:** exhaust `TICK_BUDGET` (21000,
  `treesolve.py:63` = one G3 solve) with no `TERMINAL_SUCCESS` under `P` → certify **softlock witness**;
  subtree saturation to `TERMINAL_STUCK/EXHAUSTED` (`statetree.py:70`) is stronger. A **budgeted
  refutation, not a proof**, calibrated to the same solver+budget that produced the G3 witness.
  `nan`/`escape` stay independent hard oracles (`g4.py:280-283`). Dedup by fingerprint, cap top-M=8 →
  cost `M×21000`.

**Attacker ladder** (cheapest-first; admissibility = only in-vocabulary `ACTIONS`, never engine pokes;
`g4._expand`/`_check` enforce by construction `g4.py:130-139`).
1. **Scripted/search (first):** reuse g4 Tier-0 fuzz families (`STRATEGY_VOCAB` `g4.py:111-121`), run
   1a/1b on every episode, add an **inverted-objective** frontier selector (minimise `n_latched()`, max
   cycle count — fork of `_select_leaves`), escalate deepest low-progress leaves to 1c. Batch replay,
   no ports (`godot_exec.py:1-38`). Covers the majority.
2. **RL adversary (survivors only):** the G3' stack with the inverted reward — `+1`/tick cycling,
   `+R_SOFTLOCK` on 1c cert, `−` per newly-latched checkpoint; ~13 min/game
   (`G3_PRIME_SPIKE.md:76,151`); argmax prefix replays via batch executor (`certify._bridge_replay:45`,
   asserted `:115`). **[LF]** trainer = the SB3-migrated G3' engine (§6.7), not new bespoke RL code —
   the inverted reward is a wrapper on our env seam, the learning loop is the library's.
3. **Multi-agent (last resort):** spec-v2 `agents:[{role,controls,action_space}]`, both in group AGENT,
   scripted adversary searches admissible params (DefendTheGoal pattern), off-by-default/isolated.

**Grading:** add hard outcome **`softlock`** to `_HARD_OUTCOMES` (`g4.py:96`) = a 1c-certified prefix;
leave heuristic `stuck` **soft**. Softlock → hard finding → `_grade` returns `open` (`g4.py:803-806`) →
`to_repair_report` (`g4.py:948`); `hardened`/`bulletproof` unchanged. Reproducer = the prefix `P` (as a
`sequence`, `g4.py:342-343`) + provenance `{oracle:"tree_refute", H, budget, engine, seed:0,
subtree_status}` mirroring `gameverify._make_witness:670`. Softlock axis is **orthogonal** to difficulty
(`curriculum.difficulty_profile:232-267`).

---

## 5. SCALING MATH on ORCD

**Baseline (JS PlanckEnv):** ~**2500 env-steps/s** measured (num_envs 8 threaded serve subprocesses,
**IPC-bound** — one JSON round-trip/tick dominates, not physics; `G3_PRIME_SPIKE.md:76,150-156`);
~2000 sps is the working target. **Godot in-scene batching flips the bottleneck to CPU/physics** (one
socket round-trip = N transitions, one batched inference): throughput/core ≈ `(speedup*60 /
action_repeat) * N_inscene`, bounded by core physics rate. K=6 + speedup 8 = `8*60/6 = 80`
env-steps/s **per in-scene env**; the paper's ~12k/s ÷ 4 cores ≈ **~3k/s/core** is the realistic
ceiling once the core saturates (N ≈ 30–40 envs). So godot lands the **same order** as JS (~2–3k
sps/core), physics-bound and IPC-amortised (fire-and-collect batch measured ~480 ticks/s unspeeded,
`godotworld/bench.py:8,140-156` → ~8× headroom under speedup).

| rung (1 core) | steps | @ ~2500 sps | godot in-scene | notes |
|---|---|---|---|---|
| **200k screen** (`certify.py` reduced) | 0.2 M | ~80 s | ~100–200 s | cheap pre-gate |
| **2 M certify** (`g3_prime` default) | 2 M | **~13 min** (`G3_PRIME_SPIKE.md:76,151`) | ~13–30 min | speedup-dependent |
| **1c oracle** (top-M=8) | M×21000 ≈ 0.17 M ticks | ~M×(1–3 min) | — | rides the tree, no socket |
| **T0 scripted+triggers** | ~0.2–0.3 M ticks | secs (JS) / ≤5 s/20-ep Godot batch | — | no ports |

**Array shapes (≤200-task cap, `mit_preemptable`, 1 core/task):** one game = one task. Scripted
T0/T0.5/oracle are **portless** → pack ≤200 freely; run the whole base campaign here. Socket lanes
(2b serve subprocess, 2c, outer serve) derive `port = base + SLURM_ARRAY_TASK_ID`, bind-check, **prefer
in-scene duplication over n_parallel** (one port). >200 games → sequential waves. Reserve the 2 M RL
rung + 2c for scripted-attack survivors (as `bulletproof` is, `g4.py:807`).

---

## 6. BUILD ORDER (delta vs GODOT_RL_BENCH_AND_PIPELINE.md A/B)

Plan A (smoke-bench) and B1-B3/B5 **unchanged**. This refines **B4** and adds a verification lane the
plan didn't scope. Deltas, in the tooling-audit style:

1. **B4 serve — SETTLED to a dual-dialect server.** Plan said "serve mode mirroring `runner.js` ops".
   Refine (§3): OUTER speaks **Sync 0.7 verbatim** (Python listens / Godot connects out, inverted from
   our stdio sketch); INNER keeps our verbs + reseed-on-reset + seeded rebuild + split term/trunc +
   STALE deadline + no `call`; both share the `sync.gd:156-198,61-62` core. **New: G3' scale via
   IN-SCENE BATCHING** (N AIController-equivalents, one process/port/core), not `n_parallel` — new
   live-socket plumbing over today's fire-and-collect `godot_exec.py`.
2. **PORT SAFETY — new must-fix before any socket-lane array.** 11008 has no cross-job offset
   (`godot_env.py:21`); derive `port = base + SLURM_ARRAY_TASK_ID` + bind-check (or bind-to-0 + read
   back via `__init__` reorder). Scripted lanes unaffected.
3. **STALE-STATE TIER-1 — new rung (§4).** Triggers 1a/1b + 1c oracle as a `g4` fn; `softlock` in
   `classify`/`_HARD_OUTCOMES`; inverted-objective frontier selector (fork of `treesolve._select_leaves:119`);
   extend `_reproducer`/`to_repair_report` provenance + a `g4` ledger `stale` roll-up. Then (survivors
   only) RL adversary, then spec-v2 multi-agent scripted adversary.
4. **SPEC-V2 SENSORS — new, additive.** Vendor `RaycastSensor2D.gd` + `ISensor2D.gd` verbatim (MIT,
   strip editor branches) under `godotworld/addons/sensors/`; `runner.gd` reads
   `sensors:[{type:"raycast2d", attach_to, n_rays, ray_length, cone_width, collision_mask}]` and appends
   `get_observation()` as an obs tail; keep our per-body layout (`env.py:101-133`). Add the
   `force_raycast_update`-after-settle guard (GitHub #95359).
5. **CI GAINS (adds to B6).** Param-over-games conformance matrix (typed-state asserts), cross-lane
   determinism gate (verify_onnx_export shape), IPS gate (`benchmark_env.py`), fatal handshake mismatch,
   `pytest-timeout` on every Godot-in-the-loop test.
6. **PART-A BENCH (refines A5).** env×method×seed via **SB3 PPO one proc/array task** (VecMonitor +
   onnx); deps are godot_rl BASE `install_requires` (sb3 2.0-2.4, gymnasium ≤1.0, torch ≤2.8) — pin
   those, **do not `pip install godot_rl`** (drags huggingface/ray/wget).
7. **[LF] G3' TRAINER MIGRATION — new, from the library-first principle.** Replace the vendored
   CleanRL-mirror PPO (`harness/rl/ppo.py`) with **SB3 PPO** driving the SAME env seam
   (`PlanckEnv`/future godot env expose the gymnasium API SB3 needs — `env.py` already mirrors the
   interface) and the SAME witness extraction/bridge (`certify._pick_witness:30`,
   `_bridge_replay:45` stay ours — they are oracle, not learner). Acceptance: re-run the
   difficulty-map games; verdicts (learnable/grade) must match the vendored PPO's within eval noise
   before the vendored loop is deleted. Same pins as #6; this also collapses #6 and G3' onto ONE
   trainer — cleaner for the future MCP/skills encapsulation.

---

## 7. REJECTED / NOT NOW

- **`n_parallel` on 1-core tasks** — k Godot procs thrash one core + open k ports; use Slurm array +
  in-scene batching.
- **`call`/name-dispatch verb in the certifier** — arbitrary GDScript on all agents
  (`godot_env.py:260-268`), the run_script class rejected in the MCP audit; expose only whitelisted verbs.
- **GridSensor2D/3D** — stateful, signal-driven, **no `reset()`** → buffer leaks across episodes
  (`ISensor2D.gd:24-25`), the exact stale-state nondeterminism we hunt; reimplement synchronously if ever.
- **RGBCameraSensor3D** — pixel obs, violates the no-pixels rule (`RGBCameraSensor3D.gd:8`).
- **ONNX in-engine inference** — C#/.NET only (`ONNX_wrapper.gd:3,9-21`), won't load in a stock non-Mono
  headless .sif.
- **ray[rllib] + PettingZoo (learned multi-policy)** — heaviest unpinned extra, sb3-incompatible
  (`stable_baselines_wrapper.py:156`), off-budget; take only the port-derivation idea (`ray_wrapper.py:71`).
- **sample-factory (APPO)** — only real async engine, but env_agents=16 → many procs+ports, unpinned atop
  inactive faster-fifo; defer unless a fat multi-core run is greenlit (then pin `==2.0.0`).
- **`pip install godot_rl` as a runtime dep** — we speak runner.gd's own protocol; buys nothing, drags
  huggingface_hub/wget/tensorboard + may downgrade torch/gymnasium.
- **Their reset(seed=) / done flag for determinism** — reset(seed) silently ignored
  (`godot_env.py:243-258`), `done`=term=trunc (`:221-222`); build both ourselves.
