# Deploying the GI harness on MIT ORCD "Engaging"

> Source-verified deployment study. Every partition number, quota, module name and flag below is
> quoted from the live ORCD docs (URLs inline). Research date 2026-07-14. Windows-developed repo →
> Linux x86_64 cluster. Compute is not the constraint (Elias, MIT ORCD access); GPUs cap ~8-10.
> Our workloads (LLM_RL_SYSTEMS.md, CONTRACTS §4): per-game PPO ≈ 2M env-steps ≈ 10-20 min on ONE
> CPU, env-stepping bottlenecked; JsExecutor batches thousands of seeded episodes/node; artifacts
> are tiny (JSON reports, action-list witnesses, small policy checkpoints, optional GIFs).

## 1. Resource map (cited)

Cluster total: "Around 80,000 x86 CPU cores and over 1000 GPU cards including A100, RTX6000, L40S,
H100, and H200"; "open to everyone on campus" (orcd.mit.edu/resources/about-engaging-cluster).

**Partitions** (running-jobs/overview/ for limits+walltime; running-jobs/available-resources/ for hardware):

| Partition | Max walltime | Per-user base limit | Hardware |
|-----------|--------------|---------------------|----------|
| `mit_normal` | **12 h** | **96 cores** | CPU-only nodes: 96 / 48 / 32 cores, 376–1510 GB RAM |
| `mit_normal_gpu` | **6 h** | **2 GPUs, 32 cores** | nodes w/ 8× H200 (140 GB) or 4× L40S (44 GB); 60/32 cores; 998–2007 GB |
| `mit_quicktest` | **15 min** | **48 cores** | CPU-only: 96/48 cores |
| `mit_preemptable` | **48 h** | **1024 cores, 4 GPUs** | CPU + GPU (A100, H100 80 GB, L40S, H200); jobs "may be stopped by another job with higher priority" |

Preemptable jobs "should be checkpointed so that they don't lose their progress" (overview/). No
QOS/fair-share numbers are published; check live with `sinfo -p <partition>` (getting-started/).

**Storage** (filesystems-file-transfer/filesystems/) — quota surfaced in `~/orcd/.quota`, refreshed /30 min:

| Space | Path | Quota | Backup | Use |
|-------|------|-------|--------|-----|
| Home | `/home/<user>` | 200 GB | snapshots | code, conda envs, important files |
| Pool | `/home/<user>/orcd/pool` | 1 TB | none | staging larger datasets |
| Scratch | `/home/<user>/orcd/scratch` | 1 TB | none | active-job I/O; **purged after 6 mo idle** |
| PI shared | `/orcd/pool/<n>/<pi>_shared` | 5 TB | none | group data |

**For us:** repo clone + conda env in **home** (backed up, tiny). Run outputs (ledger shards,
witnesses, GIFs) to **scratch**, then rsync the winners back to git. Nothing we produce is big
enough to need pool/PI-shared.

## 2. Environment setup

Low-friction path = **miniforge module + a mamba env** (ORCD's own recommendation; no root, no
containers needed). software/python/ + software/modules/:

```bash
module load miniforge                       # do NOT put module loads in .bashrc; put them in job scripts
mamba create -n gi python=3.12 pymunk pillow pytest numpy   # 3.12 matches CONTRACTS ("Python 3.12, pymunk 7.3.0")
source activate gi
pip install pymunk==7.3.0                    # pin the engine version (determinism, §5); pip only for what conda lacks
# Node + planck for nodeworld/ (no nodejs module is published) — conda-forge ships node:
mamba install -c conda-forge nodejs
cd nodeworld && npm install                  # installs planck into node_modules/  (needs internet → do on LOGIN node, §4c)
# later: torch CPU + CleanRL deps for G3'
pip install torch --index-url https://download.pytorch.org/whl/cpu
```
Rules from the docs: use **mamba** not conda where possible; **never `pip install --user`** ("this
will install into `$HOME/.local` instead of the environment"); export envs to YAML if you keep them
on scratch. Godot headless (later) is cleanest via Apptainer (§below).

**Windows→Linux gotchas for THIS repo:**
- **CRLF.** Git may have checked out CRLF; a `\r` on a shebang or in `nodeworld/*.js` breaks on Linux.
  Fix once: `git config core.autocrlf input` + a `.gitattributes` `* text=lf`, and `dos2unix` any
  shell scripts. Python tolerates `\r\n`; **node and bash do not**.
- **Paths.** `executors.py` already builds paths with `os.path.join` + `_repo_root()` (portable) and
  the node binary is overridable via `HARNESS_NODE` — set it to the conda node if `node` isn't on PATH.
- **pymunk wheels.** manylinux wheels exist for cp312 → `pip install pymunk==7.3.0` is a clean wheel,
  no Chipmunk compile. Pin it so the Linux engine == the certifying engine (§5).
- **Determinism caveat:** pymunk/Chipmunk is double-precision; bit-exactness is only guaranteed for
  the *same* engine build. Pin versions and prefer one canonical Linux image (§5).

**Containers (optional, software/apptainer/):** `module load apptainer/1.4.2`; `singularity pull
img.sif docker://…` ("requires internet access on the login node"); `--nv` for GPUs; home + /tmp are
auto-bound, else `-B /path`. Build with `apptainer build --fakeroot img.sif img.def`. Note: "Images
built on one architecture cannot be used on a … different architecture" — build on an x86 login node.

## 3. Workload mapping + templates

### (a) Per-game RL training (G3') — Slurm **array**, 1 game ≈ 1 core
Embarrassingly parallel; pymunk `Space` is single-thread so **one core per game task** (no
oversubscription). Throughput: at 10-20 min/game, **200 concurrent cores ≈ 600–1200 games/hour**
(200 games / 0.17–0.33 h). mit_normal caps a user at 96 cores; for a 200–1024-core fan-out use
**mit_preemptable** — our tasks are short, seeded and idempotent, so preemption just means re-running
that one game (perfect checkpointing story). Don't spawn thousands of tiny jobs; one array task
loops over a *slice* of the game list (docs' cyclic `seq` pattern) and writes a per-task ledger shard.

```bash
#!/bin/bash
#SBATCH -p mit_preemptable          # 1024-core / 48 h ceiling; or mit_normal for ≤96 cores / 12 h
#SBATCH -a 0-199%200                 # 200 array tasks (throttle %200 = all at once)
#SBATCH -c 1                         # pymunk Space is single-thread → 1 core/task
#SBATCH --mem-per-core=4G
#SBATCH -t 12:00:00
#SBATCH -o scratch/logs/rl-%A-%a.log
module load miniforge && source activate gi
export PYTHONHASHSEED=0
GAMES=(scenes/games/*.py)            # the batch to train
N=${#GAMES[@]}
# each task takes games i where i ≡ TASK_ID (mod TASK_COUNT): disjoint, load-balanced
for ((i=SLURM_ARRAY_TASK_ID; i<N; i+=SLURM_ARRAY_TASK_COUNT)); do
  python -m harness rl train "${GAMES[$i]}" \
    --steps 2000000 --seed "$SLURM_ARRAY_TASK_ID" \
    --ledger "runs/ledger.$SLURM_ARRAY_JOB_ID.$SLURM_ARRAY_TASK_ID.jsonl"   # per-task shard, §5
done
```

### (b) G4 adversarial fuzzing — array, JsExecutor batches inside each task
Thousands of cheap seeded episodes. JsExecutor already batches "EVERY episode of a layer into ONE
node process" (executors.py), so give each core a big seed-range and let it batch — the ~70 ms node
cold-start is amortised. Same array skeleton; heavier per task, fewer tasks:

```bash
#SBATCH -p mit_preemptable
#SBATCH -a 0-63%64
#SBATCH -c 1
#SBATCH -t 08:00:00
module load miniforge && source activate gi
export HARNESS_NODE=$(which node)
# task t fuzzes seeds [t*10000, (t+1)*10000) — disjoint seed shards, no collisions
python -m harness g4 fuzz --seed-lo $((SLURM_ARRAY_TASK_ID*10000)) \
       --seed-hi $(((SLURM_ARRAY_TASK_ID+1)*10000)) \
       --out "runs/g4.$SLURM_ARRAY_TASK_ID.jsonl"
```

### (c) Generation campaigns — need OUTBOUND net to OpenRouter → do them OFF the compute nodes
**Egress answer (source-verified):** ORCD docs explicitly grant internet **on the login node** —
Apptainer image pulls "requires internet access on the login node" (software/apptainer/), and all
"download/install" steps (conda create, `npm install`, container pull) are documented as login-node
or pre-submission actions (software/python/, getting-started/). The docs **nowhere promise outbound
internet from a batch/compute node**, and the ORCD workflow pattern (install before submitting) plus
standard HPC practice (cf. Princeton RC: "compute nodes … do not have Internet access … a running
job cannot download files, install packages or connect to GitHub") means **compute-node egress must
not be assumed.** Verify empirically before relying on it: `srun -p mit_quicktest -t 5 curl -sS -m 10
https://openrouter.ai/api/v1/models >/dev/null && echo OK`.

Recommendation: **generate locally (or on the login node), verify/train remotely.** LLM design calls
are rare and cheap (EnvGen: ~4 calls total for a curriculum); the expensive, egress-free work
(verify funnel, PPO, fuzzing) is what belongs on the cluster. Concretely: run `harness game new`
generation on your Windows box or the login node → `git push` candidate `scenes/games/*.py` → cluster
`git pull` → run arrays (a)/(b), which touch only local code and OpenRouter never. If the egress
probe passes and you *want* generation on-cluster, run it as a **login-node** loop (not sbatch),
writing games to scratch, then submit verify arrays.

### (d) Interactive dev / debug — OnDemand
Web portal **orcd-ood.mit.edu** (accessing-orcd/ondemand-login/) offers "Jupyter notebooks, RStudio,
and XFCE graphical desktop"; you fill a form for time-limit / cores / memory / partition. This is the
role for: first-run smoke tests, the G3' spike (Phase 0 calibration table), watching the live pygame
viewer / GIFs, and `salloc -t 01:00:00 -p mit_normal` shells for quick pymunk checks. Elias's stated
desktop caps (≤64 CPU / 64 GB / 8 h) aren't published as fixed numbers in the docs — they're the form
values bounded by the chosen partition's limits (mit_normal = 12 h/96 cores). Use `mit_quicktest`
(15 min) for throwaway tests to skip the queue.

## 4. Results flow back + determinism workflow

Artifacts are tiny, so **git is the spine** for code + certified games; **rsync/scp for bulk run
output**. Transfer host is `orcd-login.mit.edu` (filesystems-file-transfer/transferring-files/):
`rsync -avz --partial --progress orcd-login.mit.edu:~/orcd/scratch/gi/runs/ ./runs/` (rsync "will
not transfer files that are identical"; Globus/OnDemand File Browser for large pulls). Flow:
generate local → push games → cluster trains/fuzzes to scratch shards → rsync winners back →
`git add` the certified games + merged ledger.

**Determinism — argue ONE canonical certifier = a pinned Linux engine image.** The tension: training
may be nondeterministic (torch/threads) but that's offline; only the *emitted* witness — a greedy
argmax rollout reduced to `{seed, actions[]}` — must replay bit-exactly (LLM_RL_SYSTEMS §4.1;
STATE_TREE keys on the action prefix). Cross-OS float bit-exactness (Windows dev vs Linux) is **not**
guaranteed for double-precision Chipmunk/pymunk. Resolution: **define the certifier by a pinned
container, not by a host.** Build `harness-engine.sif` pinning pymunk 7.3.0 + node + planck versions;
run that same `.sif` both on the cluster and locally. Then "cluster-certified" == "locally-certified"
by construction — the witness is a *candidate* until it replays under the canonical image, and the
cluster runs that image. This beats "cluster proposes / laptop re-certifies thousands of witnesses":
same guarantee, no re-replay bottleneck, and re-certification of the whole bank is one reproducible
`apptainer exec harness-engine.sif python -m harness verify …`. Keep `policy_weights_hash + seed` in
each witness for provenance. (Per-engine note: PyExecutor and JsExecutor are separately canonical —
a witness is bound to its engine, so pin both in the image.)

## 5. Cost of parallel slop — ledger + seed integrity

`runs/ledger.jsonl` append is safe **within one process/node** but **racy across array tasks**: many
nodes appending to one file over a shared FS interleave (NFS append isn't atomic cross-node) → corrupt
lines. Fix (already wired into the templates above): **each task writes its own shard**
`runs/ledger.$SLURM_ARRAY_JOB_ID.$SLURM_ARRAY_TASK_ID.jsonl`; a post-job step merges. Because every
JSONL line is self-contained, merge is order-independent:
```bash
cat runs/ledger.$JOBID.*.jsonl | sort -u > runs/ledger.merged.jsonl   # then dedupe by (game_id,seed) key in-harness
```
Add a `harness ledger merge` that concatenates shards, dedupes on `(game_id, seed, verdict_hash)`,
and appends to the canonical `runs/ledger.jsonl` on the machine that owns it. **Seeds:** each task
must derive a **disjoint** seed range from `SLURM_ARRAY_TASK_ID` (templates b/a do this) so parallel
tasks never repeat or collide work; never seed from wall-clock in a farm.

## 6. Phased adoption

- **This week (zero-risk, high-value):** (i) SSH in, `module load miniforge`, build the `gi` env,
  `git clone` the private repo (push a deploy key or use OnDemand's login-node internet), `npm install`
  in nodeworld on the **login node**; run `pytest` + one `harness game verify` on `mit_quicktest` to
  prove the stack on Linux. (ii) Run the **egress probe** (§3c) and record the answer. (iii) Fire a
  small **verify/fuzz array** (template b, `-a 0-15`) over the existing bank on `mit_normal` — this is
  pure local-code work, no egress, no GPU, and shakes out CRLF/path issues.
- **Next (G3' spike):** OnDemand XFCE desktop for the Phase-0 calibration table (5-10 certified games,
  vendored CleanRL PPO, CPU); confirm 2M-step budget ≈ 10-20 min/game holds on a cluster core.
- **Then (scale):** per-game RL array (template a) on `mit_preemptable` for 600-1200 games/h; build
  `harness-engine.sif` and make it the canonical certifier; add `harness ledger merge`.
- **Later:** GPU only if a batched-Planck+torch experiment or a generalist policy shows up
  (`mit_normal_gpu`, 6 h, `-G 1`); Godot headless via Apptainer; OpenEnv serving as its own rung.

## Sources
- Partitions/walltime/limits: https://orcd-docs.mit.edu/running-jobs/overview/
- Hardware per node/GPU: https://orcd-docs.mit.edu/running-jobs/available-resources/
- Resource-request flags: https://orcd-docs.mit.edu/running-jobs/requesting-resources/
- Job arrays: https://orcd-docs.mit.edu/running-jobs/job-arrays/
- Storage/quotas: https://orcd-docs.mit.edu/filesystems-file-transfer/filesystems/
- File transfer / login host: https://orcd-docs.mit.edu/filesystems-file-transfer/transferring-files/
- Python/conda/mamba: https://orcd-docs.mit.edu/software/python/
- Modules: https://orcd-docs.mit.edu/software/modules/
- Apptainer + login-node internet: https://orcd-docs.mit.edu/software/apptainer/
- OnDemand: https://orcd-docs.mit.edu/accessing-orcd/ondemand-login/  ·  Jupyter/desktop: https://orcd-docs.mit.edu/recipes/jupyter/
- Getting started / salloc / sinfo: https://orcd-docs.mit.edu/getting-started/
- Cluster totals/access: https://orcd.mit.edu/resources/about-engaging-cluster
- Compute-node egress norm (comparative, non-MIT): https://researchcomputing.princeton.edu/get-started/mistakes-avoid
