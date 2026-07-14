# ORCD run management — docs-sourced operational brief

> 2026-07-14, live-fetched from https://orcd-docs.mit.edu (Opus research agent,
> requested by Elias). Corrections already applied to the staged farm scripts
> (`~/orcd/scratch/gi/*.sbatch`). Companion to `ORCD_GODOT_RL_PLAN.md` §3 and
> `ORCD_DAY1_LOG.md`.

## Hard corrections to the plan's templates

1. **500 jobs/user hard cap** ("The maximum number of jobs per user is set to
   be 500" — recipes/many-jobs/). The plan §3b's `-a 0-511%512` violates it.
   **Elias's standing cap is stricter: ≤200 array tasks (2026-07-14).**
   Fixed: `-a 0-127%128` — the cyclic inner loop (`i += TASK_COUNT`) already
   packs multiple games per task, so smaller arrays lose nothing.
2. **`--requeue` was missing from the preemptable templates.** On
   `mit_preemptable`, PI-group jobs preempt ours (FAQ #14); `--requeue`
   auto-resubmits the stopped task, otherwise the work silently never
   re-runs. Added to both farm scripts. Requeue is per-array-task. Duplicate
   ledger lines from a mid-task requeue are absorbed by `ledger merge`
   dedupe on `(game_id, seed, verdict_hash)` — by design.
3. **No preemption signal/grace is documented** — treat preemption as an
   abrupt kill; per-game idempotency (seeded, own output file) is the
   checkpoint story, not signal handlers.
4. Walltime caps: mit_normal 12 h · mit_quicktest 15 min · mit_preemptable
   48 h. (The standing "--time=24h" habit from other projects is illegal on
   mit_normal.) Elias 2026-07-14: **mit_preemptable also schedules CPUs
   faster than mit_normal** — default idempotent arrays there (observed:
   the same array sat Priority-pending on mit_normal).

## Monitoring one-liners (docs-recommended)

```bash
squeue --me                                          # live; array tasks = JOBID_TASKID
sacct -j <JOBID> -o JobID,State,Elapsed,MaxRSS,ExitCode --units=G   # post-mortem + right-sizing
jobstats <JOBID>                                     # efficiency (bug: may report CPU at ~50%)
sinfo -p mit_preemptable -O Partition,Nodes,CPUs,Memory -e
scancel <JOBID>            # whole array; scancel <JOBID>_<TASK> for one task
```
Pending reasons: `Priority` = queue ahead of you, `Resources` = nothing free.
Preempted tasks show `PREEMPTED`/`REQUEUED` in `sacct` then re-enter `PD`.

## Standing rules (docs + measured)

- **Login node runs nothing** ("not … anything unless it is submitted
  properly through the scheduler"); measured locally: in-image Godot
  thread-thrashes against the 448 visible cores (day-1 log deviation 9) —
  every engine invocation goes through `srun`/`sbatch` (or `taskset` in an
  emergency). Docs are silent on affinity/taskset — our responsibility.
- **Scratch** (`~/orcd/scratch`, 1 TB, no backup): intended for run shards;
  purge only after 6 months of no login — old `runs/$JOBID/` dirs must be
  cleaned by us. Keepers get rsync'd home/committed. Quota via
  `~/orcd/.quota`, never `du`.
- **Apptainer sees `/orcd` only with `-B /orcd`** (day-1 log deviation 10).
- Mem sizing: `--mem-per-cpu=4G` is well under the 7.8 GB/core physical
  ratio on mit_normal nodes; verify with `MaxRSS` and trim.
- Queue etiquette (standing memory): few arrays, throttled; never flood.
