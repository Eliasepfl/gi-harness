# GodotShardVecEnv — M×K sharded RL env (32-cores-per-run unlock)

> Elias, 2026-07-16: *"we can upgrade the budget for the training and ask for 32 cores per
> run — more budget and more steps/ticks."*

## The bottleneck

`GodotBatchVecEnv` (2026-07-15) is already the multi-CPU-per-game seam: **ONE** headless-Godot
`serve_game.gd` process holds **K** in-scene worlds over **ONE** socket, stepped together in a
single `await physics_frame` — a ~8.8× win over the sequential `DummyVecEnv`
(`test_gd_batch_vec.py::test_batch_vec_env_faster_than_dummy`). But **one Godot process cannot
use 32 cores**: its engine loop + the socket collect are a single serialized pipeline. Asking
SLURM for `-c 32` and getting no more throughput than `-c 4` is the waste this fixes.

## The unlock: sharding

`GodotShardVecEnv` (`harness/rl/godot_shard_env.py`) **composes** (never reimplements) **M**
independent `GodotBatchVecEnv` shards of **K** in-scene instances each = **M·K logical envs**,
stepped **concurrently**:

- `step_async(actions)` slices the `M·K` action vector into M contiguous K-slices and hands
  each to its shard's `step_async` — a **pure** stash (no IO), so fan-out is cheap.
- `step_wait()` drives the M shards' socket round-trips on a **thread pool** (one worker per
  shard) and collects the M batched replies **in shard order**. The round-trips are IO-bound
  (`socket.recv` releases the GIL) and the M Godot processes compute on separate cores, so the
  M engine loops genuinely **overlap** — GIL-friendly threads, no subprocess/pickle dance.

Each shard is a `GodotBatchVecEnv`, so this **never widens** the batched lane — it replicates
it M-fold. A non-gdscript game is rejected by the shard constructor with the same typed error.

### Seed scheme (determinism)

Shard *i* is seeded `base_seed + i·K` and holds instances `base_seed + i·K + j` (j∈0..K-1) — so
global slot `i·K + j` gets seed `base_seed + (i·K + j)`: **the same per-slot fixed-seed scheme**
the single batch env uses, extended across `M·K` slots. Results are gathered in shard order
regardless of thread completion, and each shard is deterministic (fixed seed, deterministic
game), so:

- same `(M, K, base_seed)` → **byte-identical rollouts run-to-run**
  (`test_shard_determinism_run_to_run`);
- **M==1 is byte-identical to a bare `GodotBatchVecEnv(game, K, seed=base_seed)`** — the
  regression pin (`test_shard_m1_byte_identical_to_bare_batch`);
- shard *i* instance *j* of an (M=2,K=3) cluster is byte-identical to a lone single-instance
  `GodotServeEnv` at seed `i·K+j` (`test_shard_instance_matches_single_instance_seed`) — the
  worlds neither drift from the single-instance path nor interfere.

### Port safety

Each shard is one process = one loopback listener. Shard *i* binds
`port_base + port_offset_base + i·PORT_STRIDE` (`port_base` defaults to `GIP_PORT_BASE`, exactly
as `GodotServeEnv`/`GodotBatchVecEnv` derive it), so the M shards occupy **disjoint strided
sub-bands**. `PORT_STRIDE=8` fits M≤8 inside the g3p_farm's 64-port-per-task band (8·8=64). On a
SLURM array each task still needs a disjoint `GIP_PORT_BASE` (`47000 + TASK_ID·64`), as before;
within a task `g3_prime` consumes ONE `_port_seq` offset for the whole cluster and the shard env
strides internally.

### CPU-affinity pinning (the M=4-collapse fix — **required for M>1**)

The **critical** finding from the first bench: naive M-process sharding *collapsed* — 4×8 @ -c 32
ran at **44 sps** (25× SLOWER than 1×8), taking 38 min for what 1×8 did in 96 s. Cause: a
headless Godot process sizes its `WorkerThreadPool` from the visible processor count, which is
the **whole SLURM `-c` cgroup**. So M *unpinned* processes in one -c(M·8) cgroup each spawn ~M·8
worker threads → **M× oversubscription** (e.g. 4 processes × 32-thread pools = 128 threads on 32
cores) → cross-process scheduler thrash. One process (M=1) is fine because its pool == the whole
cgroup with no peer to fight.

Fix (`pin_affinity=True`, Linux, M>1): pin shard *i*'s Godot process to a **disjoint contiguous
slice** of the allocated cores (set the parent affinity to the slice right before the child is
spawned, so it inherits it and — Godot *does* read `sched_getaffinity` — sizes its pool to the
slice; parent restored to all cores afterwards so the collector threads still float). Each shard
then gets cgroup/M threads on cgroup/M cores → **no oversubscription**.

Controlled before/after, identical 4×8 @ -c 32 / mini_collect config, only the affinity code
changed: **44 sps → 2301 sps (52×)**, 2285 s → 23 s wall. This is timing-only (never rollout
bytes) and skipped for M==1 (the byte-identical batch path). It is not optional for a real farm
run — without it, `-c 32` sharding is far *slower* than a single process.

### Torch learner threads (the SECOND fix — cap the CPU learner's intra-op pool)

Affinity alone was not enough: a clean instrumented `-c 32` run still showed *negative* scaling
(M=1 824 → M=4 141 sps). **Instrumenting split the pipeline** (`shard_instrument.py`): the sharded
ENV stepping scaled beautifully (pure step, no learner: **4868 → 9491 → 15202 sps**, 3.12× at
M=4), so the env was never the problem. The learner was: `PPO(device="cpu")` lets PyTorch default
its **intra-op thread pool to the whole cgroup (32 threads)**, and for the tiny 2×64 MLP those 32
threads are pure sync overhead AND they float across all cores, fighting the M pinned Godot
collectors. Same job, M=4, only the thread count changed:

| 4×8 @ -c 32, softlock_maze | throughput_sps |
|---|---|
| `torch.set_num_threads(32)` (default) | **503** |
| `torch.set_num_threads(2)` | **10502** |

→ **20.9×.** So the sharded path caps torch to `SHARD_TORCH_THREADS=2` (overridable via
`torch_num_threads`); the non-shard path is left exactly as before. With BOTH fixes the full
g3_prime pipeline scales like the env (below).

## Wiring (all additive; `num_shards=1` default = today's behavior, byte-identical)

| Layer | Change |
|---|---|
| `sb3_trainer.train` | grew `num_shards: int = 1` + `make_shard_venv` factory param. Engages the shard path only when `num_shards>1` **and** a `make_shard_venv` is supplied **and** `num_envs>1` **and** `HARNESS_VECENV!="dummy"`. When engaged, the PPO minibatch is sized off the TRUE rollout width (`venv.num_envs == M·K`) so `num_minibatches` divides the batch exactly as before. |
| `certify.g3_prime` | builds a `make_shard_venv(M, K)` for the gdscript lane and forwards it; `num_shards` rides in via `**train_kwargs` → `train`'s explicit param. Single-process batch path untouched. |
| `certify.rescue_certify` | grew `num_shards: int = RESCUE_NUM_SHARDS` (=1) knob, forwarded to `g3_prime`; records `num_envs`/`num_shards` in the `rescue` provenance block. |
| `cli` | `rl-probe --shards M --num-envs K`; `game rescue --shards M` (pairs with `--num-envs`, `--budget`). |

### Advisory auto-sizing (never mandatory)

`godot_shard_env.plan_num_shards(requested, *, cpus=None, cores_per_shard=8)` →
`max(1, min(requested, (cpus-2)//cores_per_shard))` — caps a request to what a box can host
(reserving 2 cores for the collect loop + OS; `cpus` defaults to `SLURM_CPUS_PER_TASK`). The
trainer/CLI use the caller's `num_shards` **as-is**; this helper is opt-in for a farm preset.

## Budget scaling (Elias's ask)

`RESCUE_BUDGET` (500k) and `g3_prime`'s `DEFAULT_BUDGET` (2M) constants are **intentionally NOT
bumped in code** (that would silently change every existing caller and break budget-pinned
tests). The wall-time-matched budgets are opt-in knobs + the recommended presets below: with
affinity on, a 4×8 run reached ~2–2.7k sps on the 2D benches (vs the pre-fix 44-sps collapse), so
a farm can raise `--budget` alongside `--shards`/`-c` and expect a materially larger step budget
at similar wall on a clean allocation (see the honest node-variance caveat in the bench section).

## Bench: scaling curve

Method: the REAL production path — `g3_prime(game, num_shards=M, num_envs=K=8, num_steps=128,
patience=∞)` — so `throughput_sps = trained_steps / train_wall_s` is the true training
step-rate (env stepping + PPO learner + the python collect loop). `HARNESS_GODOT_SPEEDUP=8`,
in-image (`gi-certifier.sif`), one SLURM job per arm, `-c` = M·8. Two games: `mini_collect`
(2D, cheap per-tick) and `tumble_3d` (3D physics, heavier per-tick). Budget 100k/arm.

<!-- BENCH_TABLE_START -->
**(1) The two controlled fixes** (each: identical config, ONLY the named change):

| fix | config | before | after | gain |
|---|---|---|---|---|
| affinity pinning | 4×8 @ -c 32, mini_collect | 44 sps (2285 s) | 2301 sps (23 s) | **52×** |
| torch thread cap | 4×8 @ -c 32, softlock_maze | 503 sps (torch=32) | 10502 sps (torch=2) | **21×** |

**(2) The clean instrumented curve** — one `-c 32` node, no preemption, softlock_maze, budget 40k:

| M×K | cores | pure ENV step sps | full g3_prime sps (torch=2) | vs M=1 |
|---|---|---|---|---|
| 1×8 | 8  | 4868 | 4055 | 1.00× |
| 2×8 | 16 | 9491 | 7728 | 1.91× |
| 4×8 | 32 | 15202 | 12412 | **3.06×** |

Both columns scale ~linearly (env 3.12×, full pipeline 3.06× at M=4 → ~76% parallel efficiency),
and the full pipeline now tracks the env — the learner is no longer the bottleneck. Affinity was
verified from inside the job: shard *i*'s Godot pid got exactly its disjoint slice (M=4 → 4 procs
× 8 cores, non-overlapping).

**(3) Why sharding — not a bigger single process — is the right way to reach 32 envs.** Two known
in-scene anchors (single process, K sweep; *different bench, not directly comparable*): 1×16 =
**4311 sps**, but 1×32 = **39 sps** — a single process collapses at K=32 (Godot broadphase). The
same 32 logical envs as **4×8 sharded = 12412 sps** (≈318× the 1×32 in-scene). Spreading envs
across processes is what avoids the single-engine collapse.

_`tumble_3d` DEFERRED: the 2D obs builder in the owned `env.py` raises `ValueError: too many
values to unpack` on a 3D `pos` tuple (the concurrent obs-3D work). All bench games are 2D
(`mini_collect` cheap-per-tick, `softlock_maze` heavier). The 3D games — the most compute-bound,
where sharding pays off most — should be re-benched once that fix lands._
<!-- BENCH_TABLE_END -->

### What bounds it / what to trust (honest)

1. **Thread oversubscription — Godot (fixed).** Unpinned M processes each size their pool to the
   whole cgroup → M× oversubscription → 52× collapse. Affinity pinning (mandatory, M>1) fixes it.
2. **Thread oversubscription — torch learner (fixed).** The CPU learner defaulted to 32 intra-op
   threads → 21× collapse at M=4. Capping to 2 fixes it. Both were *oversubscription*, just in
   two different thread pools; once both are pinned/capped, the pipeline scales ~linearly (3.06×).
3. **Node variance on mit_preemptable (real, unavoidable).** Identical configs measured 290–1121
   sps (1×8) and a preempted contended node once inverted the curve; the CLEAN single-node,
   no-preempt curve in table (2) is the trustworthy one. Prefer one `-c 32` job (all M on one
   node) for comparisons; expect run-to-run noise on a shared partition.
4. **The eventual caps (not hit here).** The python `step_wait` collect loop (GIL) will cap the
   overlap once Godot per-tick compute is very cheap; and per-process broadphase caps K (1×32 =
   39 sps) — which is exactly why we shard across processes instead of growing K.

Bottom line: with both oversubscription fixes, **M×K sharding IS a single-game throughput win** —
~3× at 4×8/-c 32 on the 2D games, near the env's own 3.12× ceiling — on top of the farm-level
(independent-jobs) parallelism that was always available.

## Recommended farm preset

<!-- PRESET_START -->
- **Both fixes on by default:** `pin_affinity=True` (M>1) + the sharded torch cap
  (`SHARD_TORCH_THREADS=2`, override with `torch_num_threads`). Non-negotiable for M>1.
- **Shape:** `K=8` in-scene per shard; `M` = cores/8; keep `-c` = **M·8** (each shard owns 8
  dedicated cores). `plan_num_shards(requested, cores_per_shard=8)` computes the cap.
- **The 32-core run Elias asked for: `M=4, K=8, -c 32`** — the clean measured win (12412 sps,
  3.06× over 1×8). `M=2, -c 16` is the conservative middle (1.91×). Do NOT grow a single process
  to K=32 (broadphase collapse, 39 sps); shard instead.
- **Budget (wall-time-matched, Elias's ask):** 4×8 runs ~3× the steps of 1×8 at equal wall, so
  raise `--budget` with `--shards`/`-c`. Recommended farm numbers: **rescue 500k → 1.5M** and
  **g3 2M → 6M** at 4×8/-c 32 (CLI `--shards 4 --budget 1500000` + `sbatch -c 32`). These are
  *recommended presets*, NOT edits to the shipped default constants (`RESCUE_BUDGET`,
  `DEFAULT_BUDGET` unchanged so existing callers + budget-pinned tests are untouched).
- **Ports:** disjoint `GIP_PORT_BASE` per SLURM task (`47000 + TASK_ID·64`); the cluster strides
  internally within the 64-port band.
<!-- PRESET_END -->

## Files

- **new** `harness/rl/godot_shard_env.py` — `GodotShardVecEnv`, the CPU-affinity pinning (the
  `_cpu_cores`/`_core_slices` helpers + the per-shard pin in `__init__`), `plan_num_shards`,
  `PORT_STRIDE`, `CORES_PER_SHARD_ESTIMATE`.
- **new** `tests/test_gd_shard_vec.py` — fake-shard composition unit tier (always run: ports/seeds,
  fan-out, concat, reseed, `plan_num_shards`, `_core_slices`) + Godot e2e determinism/M=1/instance
  pins + a `g3_prime num_shards=2` end-to-end. **35 passed in-image** (with affinity active).
- `harness/rl/sb3_trainer.py` — `num_shards` + `make_shard_venv`; the sharded torch-thread cap
  (`SHARD_TORCH_THREADS=2`, `torch_num_threads` override) — the single-game-throughput fix (additive).
- `harness/rl/certify.py` — `make_shard_venv` factory in `g3_prime`; `RESCUE_NUM_SHARDS` +
  `num_shards` knob in `rescue_certify` (additive; provenance records `num_envs`/`num_shards`).
- `harness/cli.py` — `--shards` / `--num-envs` flags on `rl-probe` and `game rescue`.
- bench harness (scratch): `~/orcd/scratch/gi/shardbench/` — `shard_bench.{py,sbatch}` (matched-core
  arms), `shard_scale2.sbatch` (single-node sweep), `shard_instrument.{py,sbatch}` (env-vs-learner
  split + affinity + torch-threads probe), `shard_curve.{py,sbatch}` (clean corrected curve),
  `shard_tests.sbatch` (in-image tests); raw results under `out/`, logs under `logs/`.

## Reproduce

```bash
# regression + e2e determinism pins (in-image, from the worktree) — expect "35 passed"
sbatch -c 8 --job-name=sh_tests ~/orcd/scratch/gi/shardbench/shard_tests.sbatch
# matched-core scaling arms (one job each; -c overrides the directive)
cd ~/orcd/scratch/gi/shardbench
sbatch -c 8  --job-name=sh_1x8 --export=ALL,M=1,K=8,BUDGET=40000,TAG=1x8 shard_bench.sbatch
sbatch -c 16 --job-name=sh_2x8 --export=ALL,M=2,K=8,BUDGET=40000,TAG=2x8 shard_bench.sbatch
sbatch -c 32 --job-name=sh_4x8 --export=ALL,M=4,K=8,BUDGET=40000,TAG=4x8 shard_bench.sbatch
# to isolate node variance, prefer a single -c 32 job sweeping M in {1,2,4}: shard_scale2.sbatch
```

_Bench caveat: mit_preemptable nodes are shared + `--requeue` restarts land on new nodes, so
absolute sps is noisy run-to-run. The controlled affinity before/after (identical config, code-only
change) and the "no collapse" result are the robust conclusions; treat the scaling ratios as
indicative, not a law._
