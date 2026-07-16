# THE ATLAS BREEDING EXPERIMENT — two arms, six crosses, three certified children

> 2026-07-16. D1's breeding slice (notes/CREATIVE_DIRECTIONS.md), answering Elias's
> design question verbatim: for fusing two working games, do we (A) hand the model the
> two game FILES plus a prompt that *searches for an objective / objective-sequence
> for the CHILD*, or (B) just fuse the two PROMPTS into a new transversal game?
> "On pourrait voir où il se positionne par rapport aux deux jeux parents sur la map
> ATLAS." Both arms built and run on the certified library; every certified child
> placed against its parents on the (solver-effort, entropy) plane.
> Addendum honored (Elias, mid-run): children that failed first-try were RETRIED with
> a doubled repair budget (max_repairs=8); first-try vs budget-rescued is reported
> per child, and UNSOLVED-with-progress children are flagged as `game rescue`
> candidates instead of being written off.

## 1. Design

Both arms ride the UNTOUCHED funnel via the `first_user` override seam — the same
seam `revise_game` already uses. No verifier, repair-loop, or prompt-section change;
the openrouter internals, `_first_user_msg` and `_repair_user_msg` are byte-identical.

**Arm A — objective search over SOURCES** (`_breed_user_msg`, sibling of
`_revise_user_msg`). The model receives BOTH full parent modules + each parent's
original prompt + a fusion brief. The brief is an *objective search*: find one win —
an objective or a sequence of objectives — that only makes sense because ingredients
from both parents coexist in one world; a win neither parent alone could host.
Anti-anchoring holds: the brief prescribes NO mechanic list and no inheritance recipe
("mechanic of A + pressure of B" never appears); which ingredients each parent
contributes is the model's own design judgement.

**Arm B — transversal PROMPT fusion** (`fuse_prompts`). One deterministic template
line quoting both seeds — `a fusion of two games — (<prompt A>) and (<prompt B>) —
design one new transversal game that honors both fantasies at once` — fed through the
NORMAL generation path. The model never sees parent code; fusion happens in prompt
space only. (The leading marker also keeps the child's slug from colliding with a
parent's run dir after `_slug`'s 40-char truncation.)

**Driver** — `breed_game(parent_a, parent_b, arm, ...)` in `harness/gen/gamegen.py`:
loads both parents, infers the engine from parent A's extension (.gd → gdscript),
builds the arm's message, runs the full `_generate_core` (sandbox, integrity freeze,
verify→repair loop, ledger), and stamps `result["breed"] = {arm, parents, prompts,
fused_prompt}`. CLI: `harness game breed <A> <B> --arm A|B --prompt-a ... --prompt-b ...`.

**Placement** — `harness/atlas/breeding.py` (read-only, additive):
`placement(child, parent_a, parent_b, rows)` classifies the child on the
(`solver_expansions`, `witness_entropy`) plane after linear min-max normalisation
over the whole library (same units as the atlas map): `collapsed_onto_parent_*`
(within max(15 % of the parent gap, 0.05)), `between` (projects inside the parent
segment, within max(25 % of gap, 0.08) of it), else `beyond` (left the parents' axis
— new territory), `off_map` when a descriptor is missing. Thresholds are harness-side
judgement calls; nothing here reaches the LLM surface. `render_breeding_svg` overlays
parent–parent–child triangles on the map (dashed parent baseline, solid inheritance
edges, children coloured by arm).

## 2. The runs

Parents: the 6 certified post-uncap games (verdicts from
`~/orcd/scratch/gi/runs/{uncapped_solo,long3d,fix3db}/gen_*.json`). Parent
coordinates (solver expansions, witness entropy):

| slug | exp | ent | dim |
|---|---|---|---|
| a_3d_game_fly_a_small_craft_through_a_se | 4418 | 2.554 | 3D |
| top_down_maze_push_a_heavy_crate_through | 523 | 2.311 | 2D |
| steer_a_car_into_a_parking_slot_without_ | 1403 | 1.551 | 2D |
| pilot_a_drone_through_a_canyon_dodging_r | 1614 | 1.793 | 2D |
| side_view_platformer_hop_across_a_chain_ | 1081 | 1.970 | 2D |
| push_a_heavy_block_through_a_maze_to_the | 69 | 2.250 | 2D |

Pairs (P1 crosses the certified 3D fly-rings, per the mission):

* **P1** = fly-rings 3D × crate-maze-guard — max contrast: 3D flight vs top-down push puzzle.
* **P2** = car-parking × drone-canyon — two vehicles, opposite objectives (precision stop vs hazard traversal).
* **P3** = sinking-platformer × block-maze — platform pressure vs heavy-object logistics.

Each pair ran BOTH arms as separate `mit_preemptable --requeue` jobs (backend
openrouter / glm-5.2, in-image, HARNESS_GODOT_SPEEDUP=8, GIP_PORT_BASE derived from
the job id; one preemption observed and re-queued cleanly). Children land in the main
checkout `~/gi/scenes/games/`; result JSONs in
`~/orcd/scratch/gi/runs/breeding/breed_P{1..3}{A,B}[_r2].json`; first-try child
sources snapshotted under `.../breeding/first_try_children/` before retries.
Two infra potholes worth recording: compute nodes need `module load apptainer/1.4.2`,
and the runner must be a REAL .py file — the verify sandbox's multiprocessing spawn
cannot re-import a `python3 -` stdin `__main__` (first round died as VERIFY_ERROR on
exactly this).

## 3. Results

| pair | arm | child (slug) | first try | retry (max_repairs=8) | final | placement (effort × entropy) |
|---|---|---|---|---|---|---|
| P1 | A | breed_a_3d_game_fly_a_small_craft_throug | UNSOLVED, 5 att, 31 min | UNSOLVED, 9 att, 33 min | **not certified — RESCUE CANDIDATE** | off_map (no witness) |
| P1 | B | a_fusion_of_two_games_a_3d_game_fly_a_sm | ENV_ERROR, 5 att, 21 min | ENV_ERROR, 5 att, 53 min | not certified | off_map |
| P2 | A | breed_steer_a_car_into_a_parking_slot_wi | **COMPLETED, 2 att, 3.2 min** | — | **certified FIRST-TRY** | **beyond** (t=5.02, d⊥=0.06; raw 3703 exp, 2.763 bits) |
| P2 | B | a_fusion_of_two_games_steer_a_car_into_a | ENV_ERROR, 5 att, 17 min | **COMPLETED, 3 att, 3.4 min** | **certified, budget-RESCUED** | **beyond** (t=2.11, d⊥=0.09; raw 19 exp, 2.067 bits) |
| P3 | A | breed_side_view_platformer_hop_across_a_ | UNSOLVED, 5 att, 7.7 min | **COMPLETED, 2 att, 5.6 min** | **certified, budget-RESCUED** | **beyond**, borderline (t=0.97, d⊥=0.100; raw 2134 exp, 2.267 bits) |
| P3 | B | a_fusion_of_two_games_side_view_platform | GOAL_ERROR, 5 att, 7.3 min | ENV_ERROR, 6 att, 56 min | not certified | off_map (diagnostic placement of its best failed build: collapsed_onto_parent_a) |

Certified children and their found objectives:

* **P2A — "Asteroid Dock"** (arm A, first-try): thread a debris belt, then berth in a
  station dock with a latched angle + speed condition. Sequence-fusion of the drone
  parent's hazard traversal and the parking parent's precision stop.
* **P2B — "Canyon Dock Pilot"** (arm B, rescued): spire canyon run ending in a
  low-speed docking slot — the same two-phase objective found through prompt space.
* **P3A — "Crumbling Maze"** (arm A, rescued): push a heavy block through a maze to
  the exit while the floor tiles crumble under load and collapsed tiles become fatal
  holes — the platformer's sinking pressure re-expressed on the pushed OBJECT.

**Placement picture** (triangle overlay: `notes/breeding_triangles.svg` — dashed
line = parent baseline, solid edges = inheritance, orange = arm A, violet = arm B):
all three certified children sit **beyond** their parents, none between, none
collapsed. P2A is the striking one: its witness entropy (2.763 bits) exceeds every
parent in the library and its solver effort is ~2.6× either parent's. P3A is
borderline beyond (d⊥ = 0.100 vs the 0.08 threshold) — effectively "between-plus":
parent-range entropy with ~2× the effort. P2B is beyond mostly along entropy; its
19-expansion solver effort is honest evidence the rescued build is EASY for the
machine (the repair loop widened the corridor and doubled fuel until rollouts won).

**Coverage delta, honestly.** On a FIXED 6×6 grid over (solver_expansions ×
witness_entropy) with bounds from the final library: before = 5/36 cells, after =
6/36. **One new cell** — colonised by P2A (moderate-effort × top-entropy). P2B and
P3A land in already-lit cells. (The auto-axis atlas render reports 13.9 % → 25 %,
but that comparison switches axes mid-experiment; the fixed-grid +1 is the honest
number.)

## 4. Honest metrics

* **Certification rate per arm:** arm A 2/3 (1 first-try, 1 rescued); arm B 1/3
  (0 first-try, 1 rescued). First-try only: A 1/3, B 0/3.
* **Budget rescues (addendum (a)):** P2B and P3A were rescued by max_repairs=8.
  P1A (UNSOLVED×2), P1B (ENV×2) and P3B (GOAL→ENV) were not. Note the retry does NOT
  lift the compile cap: `_COMPILE_CAP=5` still discards after 5 env-shaped failures,
  which is exactly how both P1B rounds and the P3B retry died — raising max_repairs
  cannot rescue a syntax-error loop.
* **G3 solver budget (addendum (b)):** not raised. `treesolve.TICK_BUDGET = 63000`
  is a frozen-verifier constant (already 3×-raised on 2026-07-15) with no runtime
  knob; changing the verifier is outside this task's lane. The sanctioned path for
  solvable-but-hard children is the RL-witness rescue below.
* **RESCUE CANDIDATE (flagged, not run):** **P1A** — both rounds are textbook
  UNSOLVED-with-progress: first-try 555/576 episodes reach `first_grip`; retry
  attempts 8–9 reach `crate_threaded_ring_3` (836/1176 episodes) and stall one stage
  short of `crate_on_plate`. The found objective ("Tow Stitch": shove a heavy crate
  through a fixed SEQUENCE of gates, crash on posts — fly-rings' ordered threading
  imposed on the crate parent's cargo) is the most interesting design of the batch,
  and its first-try solver effort (~17k expansions) would colonise the far-effort
  column where only the uncertified drone-course sits today. `harness game rescue`
  (merged to main mid-experiment) is the right tool; a hard child that maps far is a
  feature, not a defect. P3A's first-try (stuck between `passed_level2`/`level4`,
  215/216 progress) was the other candidate — its budget retry certified it first.
* **Win neither parent could host — judged per child, honestly:** P2A yes (neither
  parent has the other's half; caveat: lander-style docking is a familiar trope —
  novel relative to the parents, not to gaming at large). P2B yes but weaker — same
  fused shape via prompts, and difficulty collapsed during repair. P3A yes — the
  crumble-under-the-block twist requires both parents' cores simultaneously.
  P1A's (uncertified) objective also passes this test on paper.
* **Cloning:** no certified child collapsed onto a parent on the map — naive
  breeding did NOT just clone a parent. The one clone-signal in the batch is in a
  FAILED run: P3B's best build placed `collapsed_onto_parent_a` (diagnostic only).
* **Both arms converged on P2** to the same fused objective (hazard run → precision
  dock) — evidence the fusion attractor for a pair can be strong enough that prompt
  fusion and source fusion find the same child.
* **Cost:** 11 runs, 52 LLM attempts, ~4.0 h summed job wall. Per certified child:
  ~17 attempts / ~80 min wall (including all failures and retries); at the measured
  glm-5.2 per-call range ($0.04–0.22) ≈ $2–11 total, ~$0.7–3.7 per certified child.
  Arm A is also the cheaper arm per certified child (its wins took 2–3.5 attempts in
  ≤6 min; arm B's one win consumed a 5-attempt failed round first).

## 5. Verdict — A vs B — and the next step

**Arm A (sources + objective search) wins.** It certified more (2/3 vs 1/3), needed
no rescue for its first win, produced DESIGN blocks that explicitly name what each
parent contributes, and its single failure is the batch's most interesting artifact
(a deep-progress, rescue-eligible multi-stage objective). Arm B is cheaper to build
but fails mundanely: without sources the model re-derives everything and dies on
GDScript compile errors (the 3D cross killed it twice at the compile cap), and it
produced the only parent-clone signal. Prompt-space fusion is a fine *fallback*; the
files are the better genes.

**What breeding does, on this evidence:** it extrapolates rather than interpolates —
children land BEYOND the parents on the (effort, entropy) plane, not between them —
and it does not clone. But it is cell-INEFFICIENT: unguided, 2 of 3 certified
children landed in already-colonised cells; the map moved +1 cell for ~4 h of node
time. The P1 (2D×3D) cross is the hard one: arm B can't compile it and arm A designs
objectives the frozen solver budget can't finish — dimension-crossing children are
exactly where the rescue lane earns its keep.

**Recommended next step: cell-TARGETED breeding.** The atlas build already names the
emptiest frontiers as qualitative briefs (`build_atlas` → `empty_cells[].brief`).
Compose them: pick the empty cell FIRST, pick the parent pair whose segment points
toward it, and append the cell's brief (qualitative words only — bins and numbers
stay harness-side) to `_breed_user_msg`'s fusion brief. Then (1) run
`harness game rescue` on P1A and place the rescued child, (2) re-run one targeted
arm-A cross per empty frontier and score it on the fixed-grid coverage delta — the
number this note used. If targeted breeding still lands in lit cells, the lever is
the solver-budget/rescue side, not the prompt side.

## Files

* `harness/gen/gamegen.py` — `_breed_user_msg`, `fuse_prompts`, `breed_game` (+ engine-from-path, humanized-slug fallback helpers).
* `harness/cli.py` — `harness game breed`.
* `harness/atlas/breeding.py` — `placement`, `render_breeding_svg`; exported from `harness/atlas/__init__.py`.
* `tests/test_breeding.py` — 16 offline tests (message contracts, arm routing, slug safety, placement math, overlay SVG).
* `notes/breeding_triangles.svg` — the parent-parent-child triangle overlay.
* `runs/breeding/` (worktree, gitignored) — job script, specs, `run_breed.py`, `analyze.py`, `analysis.json`, atlas_before/after.
* `~/orcd/scratch/gi/runs/breeding/` — result JSONs (`breed_P*{,_r2}.json`), job logs, first-try child snapshots.
