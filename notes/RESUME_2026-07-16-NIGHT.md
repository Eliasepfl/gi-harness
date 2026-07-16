# RESUME — 2026-07-16 night (session-limit handoff)

Session mode: Fable orchestrator + Opus subagents, autonomous. Repo `/home/enaha/GI/gi-harness` (`~/gi` symlinks HERE — cert jobs run in this live tree). Scratch workdir: `/orcd/scratch/orcd/008/enaha/gi` (= `~/orcd/scratch/gi`), logs in `logs/`.

## THE ONE ACTIVE RULE

**MERGE FREEZE**: never merge/commit/push to main while certification jobs run (integrity tripwire fingerprints contract prompts + harness/rl/* + serve host; it killed rich waves v1 AND v2). Freeze is ON until job **18085383** (richv3_2, cargo port) leaves the queue. Check `squeue -u enaha` before ANY merge. Memory: `project_cert_merge_freeze.md`.

## MERGE QUEUE (execute in order at unfreeze)

1. **SHARD** branch `worktree-agent-ad00b1a30be7c476a` @ `769b3d9` — `GodotShardVecEnv` M×K: per-shard core pinning (44→2301 sps) + `SHARD_TORCH_THREADS=2` (503→10502). Clean curve 1×8=4055 / 2×8=7728 / 4×8=12412 sps (3.06×, 76% eff). 35 in-image tests green. Touches sb3_trainer.py, certify.py, cli.py + new godot_shard_env.py.
2. **HEADING** branch `worktree-agent-a364acf7e4517c294` @ `f3f6596` — `ray_frame` auto/body: locked-rotation bodies get velocity-heading retina. Unit-proven (center ray hits wall ahead); fly re-bench honestly NEGATIVE (rings are non-collider markers — invisible to rays; body-local 0.625 vs heading 0.0 ring-1). 91+13 in-image green. Touches serve_game.gd + tests + ARCH notes (disjoint from shard).
3. Combined gate then push. Gate pattern:
   `cd /orcd/scratch/orcd/008/enaha/gi && sbatch --parsable -p mit_preemptable --requeue -c 6 --mem=10G -t 01:00:00 -J gateX -o logs/gateX-%j.log --wrap "module load apptainer/1.4.2 && apptainer exec -B /orcd ~/gi/gi-certifier.sif bash -lc 'cd ~/gi && python -m pytest tests/test_gd_lane.py tests/test_gd_adversary.py tests/test_gd_wiggle.py tests/test_g4.py tests/test_rl_env.py tests/test_gd_rl.py tests/test_gd_shard_vec.py tests/test_sb3_trainer.py tests/test_demo_ready.py tests/test_gameverify.py -q --no-header 2>&1 | tail -2'"`
4. Also commit this file + any uncommitted notes at unfreeze.

## IN-FLIGHT AGENTS (SendMessage by id; nudge on every stop — their in-turn pollers DIE at turn end, ALWAYS)

- **REWARD** `a7d22ed45c584b6d1` (worktree `agent-a7d22ed45c584b6d1`): mission = terminal-dominance reward + Elias's temporal decay + REMOVE checkpoint-name hint (keep K-nearest ego block) → 400k convergence probe on mini_collect → SR>0/demo_ready → flip xfail in test_demo_ready.py ONLY if in-image smoke passes. Was nudged onto its 4 finished D2 sweep jobs (rp_d2a_noliving/b/c_tinyshape/d_mildliving = 18087467/68/78/79). Expect more stop-and-wait cycles: check its sbatch jobs (`rp_*`), nudge with results. MERGE LAST — its files (env.py, sb3_trainer.py, certify.py) overlap shard's; resolve at merge.
- **GIF** `a7a396054f251727b`: 3 breeding-children GIFs (breed_asteroid_dock / breed_crumbling_maze / breed_fusion_b) → `notes/gifs/`, self-judging frames, ≤3 iterations, jobs named `gi-*`. On FINAL: view GIFs (Read frames), then site day3 update (`_src/day3/index.html`, second-wave GIFs section) + `python _build.py` (password gitignored at `_secret/password.txt`).

## WATCHERS ARMED (background bash; task-notification on fire)

- `b76062llp` — rich v3 wave (waits 18085381-83; only cargo left). On fire: read `runs/richprompts/genv3_2.json` verdict → UNFREEZE + merge queue above.
- (Reward-sweep watcher already fired and was handled.)

## FRESH RESULTS TO BUILD ON (all pushed except where noted)

- **Rich v3 doctrine verdict**: shooter-3D CERTIFIED (51 ticks, att 3), heist-2D CERTIFIED (133 ticks, att 5), integrity ok → prompt-first composition VALIDATED under the de-biased contract. Games landed in scenes/games/ + ledger on main.
- **Cargo port** failed v1+v2 identically (crane magnet: out-of-bounds crates, dead grab/release). If v3 fails too → open dedicated lane: grab/release (joint/magnet) generation gap; check skills routing for joints + repair-hint quality.
- **De-bias** `061b720`: fiction menu + frame menu removed from api_gdscript.md; test_prompts INVERTED (menus asserted absent). Elias remains half-sceptical ("du biaisage inversé") — offered pure-silence variant ("whatever your fiction is about", drop "no body kind more expected") if he asks; v3 = empirical support for current form.
- **Router+L1** merged `d6669cb`: index reconciliation defence-in-depth, ceiling 10→14, route diagnosis observable; L1 descriptors with None(unmeasurable)/0(truly-empty) split + anti-gaming guard. L1 UNPOPULATED on library: needs serve host to emit per-body extents (QUEUED — do after HEADING merge, same file serve_game.gd) or a --verify channel pass.
- **Raycast** merged `15a53a7`: 25×5 retina + class bits {static,dynamic,sensor}, 3 obs profiles; PURE RAYS wins on fly (0.625 vs positions 0.0 ring-1) — Elias's "rays alone" instinct confirmed.
- **Skills library**: clean-reinstalled at upstream `5909c32`, index regenerated locally (96 unique; upstream index bug = dup godot-navigation-pathfinding, missing godot-ai-navigation). Local mod to skills_index.json is intentional.
- **Breeding** pushed `1e13ae6`: arm A (source files) beats arm B (prompts) 2/3 vs 1/3; children extrapolate beyond parents; Asteroid Dock = new cell + library entropy record 2.763 bits. Triangles SVG: notes/breeding_triangles.svg. Next: cell-targeted breeding.
- **Scaling doctrine (Elias's cores question ANSWERED)**: farm = linear breadth (games in parallel); sharded M=cores/8 now REAL for single-game depth (3.06× at 32 cores). 1×32 in-scene = 39 sps broadphase collapse — never do in-scene >16.

## NEXT TASKS (priority order)

1. Unfreeze → merge queue (shard, heading, gate, push, commit notes).
2. Shepherd REWARD agent to convergence + xfail flip → merge its branch (conflict care) → demo_ready/rescue/critic chain lights up → re-run demo_ready across library.
3. GIF agent FINAL → judge → site day3 refresh + publish.
4. Serve host extents emission (small lane) → L1 atlas regen (--facts + --verify) → real complexity stats + --complexity panel → breeding targets in complexity cells.
5. Cargo 3/3 fail? → grab/release generator lane.
6. **THE EXPORTER** (frame_t, state_t, reward_t, objective-text) — bullet-3 deliverable, Elias's go PENDING, gated on reward landing. Then: Tow Stitch witness-RL rescue with sharded budget (M=4×8).
7. Site/story: Atlas remains flagship; new material since last publish: breeding triangles, rich-prompt doctrine win, pure-rays result, shard 3.06×.

## METHOD (non-negotiable)

- SLURM only for anything heavy; `mit_preemptable --requeue`; sbatch + IN-TURN poll; max ~5 jobs.
- python on login node: `module load miniforge/24.3.0-0 && conda activate reve` (no `anthropic` there — test_gamegen only in-image).
- Agents that "wait for their poller" are dead — SendMessage nudge with job IDs + what to do with each outcome.
- Never scan /orcd/scratch or /orcd/pool broadly; known subdirs only.
