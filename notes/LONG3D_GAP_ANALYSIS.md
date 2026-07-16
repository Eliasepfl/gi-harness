# LONG3D Gap Analysis — where the most flagrant gap is, and the tracks to close it

Analyst pass, 2026-07-16. Wave = "long3d": 6 longer, precise prompts, "3D" explicitly
named in 3 of them (Elias's test). Jobs 18033511-16. Results:
`~/orcd/scratch/gi/runs/long3d/gen_{0..5}.json`; code under
`~/gi/scenes/games/<slug>/` (attempts `a1..a5.gd` + promoted `<slug>.gd`).
Prompts: `~/orcd/scratch/gi/prompts_long.txt`. All six jobs finished before this pass.

**Headline.** The model is GOOD at transcribing named mechanics (~79% of the precise
contract items appear in code — cones+3-dent-wreck, sinking platforms, wind gusts,
spires+arch+helipad, flee behaviour, patrol guard, lost-sheep). It fails on two axes
that have nothing to do with mechanic fidelity: (1) **it collapses every explicitly-3D
prompt to 2D — 0/3**, even with 3D skills routed in; (2) **half the games are
built-but-unsolvable** (faithful mechanics tuned into a band the tree-solver can't
clear). Only 2/6 certified — both natural-2D prompts; **0/3 of the "3D" prompts certified.**

---

## 1. The six games — results table

| gen | prompt (short) | dim NAMED | dim CHOSEN | verdict | corr. (=att−1) | root `extends` | why it failed |
|---|---|---|---|---|---|---|---|
| 0 | fly craft through 5 floating rings above a canyon | **3D** | **2D** | ENV_ERROR | 0 recorded* | `Node2D` | OpenRouter 200 + null content; thinking-off salvage fired and STILL null |
| 1 | 3D parking, cones, 3-dent wreck | **3D** | **2D** | ENV_ERROR | 4 | `Node2D` ("2D parking challenge") | UNSOLVED (aligned, never stops) then dead action `coast` |
| 2 | 3D drone: spires, arch, helipad, side wind | **3D** | **2D** | UNSOLVED | 4 | `Node2D` | 0 episodes pass the arch / reach helipad — unsolvable-as-tuned |
| 3 | top-down maze: crate → pressure plate, patrol guard | top-down | 2D ✓ | **COMPLETED** | 3 | `Node2D` | — solved in 90 decision ticks |
| 4 | side-view platformer: sinking platforms, lava, flag | side-view | 2D ✓ | **COMPLETED** | 4 | `Node2D` | — solved in 254 ticks |
| 5 | top-down herding: 3 skittish sheep, flee, pen, timer | top-down | 2D ✓ | UNSOLVED | 4 | `Node2D` | 0 episodes pen even ONE sheep (even after win relaxed to 2/3) |

\* gen_0 caveat: the recorded JSON is the `_dispatch` first-call-unavailable stub
(`attempts:[]`, `design:null`) — see §2. Attempt files `a1..a5.gd` for this exact slug
sit on disk with timestamps concurrent to the run; they are the model's fly-rings output
and are scored below. Whether they were wiped from the record by a mid-loop null-content
(the `_repair_loop` unwind, §2) or are a merged worktree pass, the code is authoritative
and the lost history is itself a finding.

**Dimension result (Elias's test): explicit user-level "3D" moved the model 0/3 times.**
gen_0 fly, gen_1 parking, gen_2 drone all named "3D" in the prompt; all three are
`extends Node2D`. gen_1's own header comment reads `# --- 2D parking challenge ---`.
The skill router even selected `godot-physics-3d` + `godot-3d-world-building` for gen_0/1/2
— so 3D knowledge was in the context and was ignored.

### Episode stats / witness (dug from `layers.G3_solve.checks` + `progress`)
- **gen_1 parking**: 216/216 episodes reached `aligned_in_slot`, **0** reached
  `nearly_stopped` (a1,a2); 192/216 aligned (a3). Solver `tree`, ~23k nodes / 57k ticks.
  Stuck between `aligned_in_slot`→`nearly_stopped`: the car can align but the brake/damp
  tuning never lands it under `WIN_SPEED=1.5` inside the slot. Final a5 = dead action
  `coast` (probed dead in all 5 contexts — `coast` is `pass`, i.e. identical-to-noop;
  the lateral-damp physics already coasts, so the verb changes nothing).
- **gen_2 drone**: a3 → 1935/2616 reached `final_stretch`, 0 `helipad_reached`; a4
  regressed to stuck at `spire_field_cleared` (2466/3360), 0 `arch_passed`. The
  arch-under + soft-landing gate is unthreadable by the tree solver.
- **gen_4 platformer** (the success): a3 was GOAL_ERROR — milestones
  `platform_1..4_landed` were **dead on the winning path** (the solver won without ever
  latching a "landed" checkpoint), so a4 replaced them with x-progress checkpoints
  (`quarter/half/three_quarter_crossed`). Passed a4: solvable, non-trivial (254 ticks),
  replayable, has a failure witness. Witness actions = `hop_long/hop_short/hop_left`.
- **gen_5 herding**: a3/a4 → ~180/240 reached `sheep_endangered`, **0** `first_sheep_penned`.
  Even with `is_success` weakened to 2-of-3 penned, not one sheep is ever penned.
- **gen_3 maze** (success): solved in 90 ticks, guard-catch → reset-to-start works.

---

## 2. Failure taxonomy (with exact strings)

- **NULL-CONTENT / backend fragility (gen_0).** Note string, verbatim:
  `openrouter unavailable (OpenRouter HTTP 200: {"id":"gen-1784178186-HZwyi06xyJmqfVDBMiXL",
  "object":"chat.completion","created":1784178186,"model":"z-ai/glm-5.2",
  "provider":"DigitalOcean",..."choices":[{"...); explicit backend requested -> no template fallback`.
  Model = **z-ai/glm-5.2** via DigitalOcean. Path: `_openrouter_content` returns None on
  blank content → `_openrouter_complete` **salvage fires once, disabling thinking**
  (`{"reasoning":{"enabled":false}}`, gamegen.py:549-551) → still null →
  `_BackendUnavailable`. So the "reliable" thinking-off salvage (measured 2026-07-15,
  49s/$0.04) **did not save gen_0.**
- **ATTEMPT-HISTORY LOSS (harness bug, gamegen.py:668 vs 705-714 / 838).** In
  `_repair_loop`, `produce(feedback)` is called at the TOP of each iteration and its
  report appended only after; a `_BackendUnavailable` from any correction call unwinds
  the loop, discarding the local `attempts` list, and `_dispatch` returns the stub
  `{"game_path":null,"attempts":[],"verdict":"ENV_ERROR","design":null}`. A run that
  generated code is reported as a 0-attempt infra failure. This is why gen_0 shows 0
  attempts despite `a1..a5.gd` on disk.
- **DEAD ACTION (gen_1 `coast`; gen_2 `idle`).** Taxonomy insight: an action whose body
  is `pass` (or otherwise a no-op given the physics) is probed dead in all 5 contexts and
  hard-fails G1. `coast`/`idle` are the canonical trap — the passive verb the fiction
  suggests but the sim already does for free.
- **CONTAINMENT ESCAPE on first attempt (gen_2 `drone`, gen_3 `pusher`, gen_5
  `Sheep_0..2`, gen_4 `platform_3/4`).** Every game's `a0` failed on out-of-bounds or a
  parse/type-inference error. The flee mechanic (gen_5) is itself the escape source:
  "flee off the field" is both the intended stake and a G1 violation until clamped.
- **COMPILE / type-inference (gen_0 `pull`, gen_1 `pull`, gen_2 `is_ctrl`, gen_4 `**`).**
  Untyped `var x := <untyped>` and a stray `**` (Python power operator — GDScript has none).
- **UNSOLVED = built-but-unsolvable (gen_1, gen_2, gen_5).** The dominant failure. Not a
  fidelity failure — the mechanics are present; the constants (brake force, arch gap,
  flee force) sit outside the solver's solvable band.

---

## 3. Prompt-fidelity scores (NEW measurement — named contract items present in code)

Long prompts were precise contracts. Score = named mechanics faithfully in code / named
mechanics. `~` = partial.

**gen_0 fly-rings — ~1.5 / 6 · dim 3D→2D.**
five rings ✗(built 6) · different heights AND depths ✗(flat row y≈200 ±3px; depth
impossible in 2D) · pass each ring in order ✓ · before fuel runs out ~(`_boost` drops
only on thrust, not a drain) · touching ring edge crashes ✗(rings are pass-through
`Area2D`; no edge collision) · canyon wall crashes ✗(walls only clamp position; only the
floor kills). *The prompt's spatial core is un-representable in the chosen dimension.*

**gen_1 parking — ~4.5 / 5 · dim 3D→2D.**
street→marked slot ✓ · between two cone rows ✓(x=±2.5, 6 each) · complete stop inside
lines ~(checks car CENTER in slot, not whole body) · each cone hit dents ✓ · three hits
wreck ✓(`dent≥3`→`is_failure`). *Highest mechanic fidelity of the six; failed on
solvability + dead `coast`, not fidelity.*

**gen_2 drone — ~5.5 / 6 · dim 3D→2D.**
winding canyon ~ · weave between rock spires ✓(3) · under a stone arch ✓ · land gently on
helipad ✓(`soft_landing` speed gate) · side wind pushes off course ✓(`WIND_BASE`/`WIND_KICK`
gusts) · touching rock destroys ✓.

**gen_3 maze — ~5 / 6 · dim correct · COMPLETED.**
push heavy crate ✓ · stone labyrinth ✓ · onto exit pressure plate ✓ · slides only while
pushed ✓(damp) · jam for good in a dead-end ✗(no permanent-jam state) · patrol guard sends
you to start on contact ✓(A↔B patrol, catch→`_start_pos`).

**gen_4 platformer — 5 / 5 · dim correct · COMPLETED.**
chain of platforms ✓ · sink shortly after you land ✓(spring-damped `target_offset` sink —
genuinely implemented) · lava pit ✓ · grab flag at far side ✓ · touching lava ends run ✓.

**gen_5 herding — ~5.5 / 6 mechanics BUT win-drift · dim correct.**
three skittish sheep ✓ · into the pen ✓ · before timer ✓(`TIME_LIMIT=35`) · flee away from
you ✓(`FLEE_FORCE=700`/`RADIUS=130`) · circle behind (emergent) ✓ · off-field lost for good
✓(`"lost"` state). **WIN DRIFT: `is_success` = 2-of-3 penned (line 261), not all three** —
the model quietly relaxed the contract to try to make it solvable, and STILL couldn't.

**Aggregate:** named-mechanics honored ≈ 27/34 ≈ **79%**. Dimension honored **3/6 overall,
0/3 when "3D" explicitly named.** Certified **2/6**, both natural-2D; **3D prompts 0/3.**

---

## 4. Quality bar — the human demos

`examples/` are genuinely 3D and dense: files touching 3D nodes — FPS 25, HovercraftRacing
17, ItemSortingCart 17, ScoreTheGoal 14, 3DCarParking 13. Scene node counts: FPS
`main.tscn` 80, `player.tscn` 85; 3DCarParking `TrainingScene.tscn` 37. `3DCarParking/.../Car.gd`
is a real `VehicleBody3D` — suspension model, `engine_force`/`steering`, brake/reverse
**rear-light material swaps (juice)**, out-of-bounds/rollover/away-from-goal resets, reward
shaping — inside a multi-file scene of imported `.glb` meshes.

Generated games: one self-contained ~250-320-line `.gd`, world built from `Polygon2D`/
`ColorRect` primitives in code, one controlled body + a few static walls, physics faked
with `apply_central_force` + manual lateral damp, minimal juice (a cone recolours on hit).

**Crucial framing:** content-density / imported-mesh / juice gaps are largely **imposed by
the harness contract itself** (api_gdscript.md bans `load()`/`preload()`/external assets and
forces ONE self-contained file). They are a sandbox rule, not a model failure, and cannot
be closed without changing the sandbox. That makes them the wrong place to point.

---

## 5. THE MOST FLAGRANT GAP — dimensionality (argued, not vibed)

Candidates: dimensionality · mechanic count/composition · world proportion · juice ·
controller feel · content density. Elimination:
- **Mechanic count/composition** — NOT the gap: ~79% fidelity, 5-6 composed mechanics per
  game.
- **Content density / world proportion / juice / controller feel** — real vs demos, but
  **contract-imposed** (no assets, single file) and **downstream** of dimension. Closing
  them means changing the sandbox, not the model.
- **Dimensionality** — the model CAN build 3D (contract fully supports it: api_gdscript.md
  line 11 "2D and 3D are both fully supported", line 30 gives both `[x,y]` and `[x,y,z]`),
  the user NAMED 3D three times, the router SELECTED 3D skills — and **3/3 still shipped
  `extends Node2D`.** This is a pure BEHAVIOUR gap, not a capability or contract limit; it
  is the exact axis Elias's test probes; it **destroys the spatial core** of the prompts
  (fly-rings "different heights and depths above a canyon" → a flat horizontal row of rings
  at y≈200); it is the widest divergence from a human bar that is 100% 3D; and it has a
  **concrete, near-one-line proximate cause** (below).

**Verdict: dimensionality is the most flagrant gap.** And it is the cheapest to close.

**Proximate cause (smoking gun).** `harness/gen/gamegen.py:259-262`, `_first_user_msg` —
the user-turn instruction attached to EVERY prompt, gdscript lane included, hardcodes:
> `Design an original 2D physics game for this prompt.`
The system prompt says "dimension is yours, neither is the default"; the very next USER
turn commands "2D". The model obeys the more specific, more recent user instruction over
the user's own "3D" wording. This "2D" is a stale inheritance from the py/js legacy
2D-only lanes, reused verbatim for the 3D-capable gdscript lane. Compounding it:
api_gdscript.md frames every prompt as "a seed, not a spec of its own... invent the
mechanic and surprise us... build the one that is least obvious... different dimension"
(lines 1, 9, 13) — which actively LICENSES discarding an explicit "3D"; and the 3D-only
`PhysicsServer3D.set_active(true)` gotcha (lines 16, 28) raises 3D's certification risk, so
a repair-loop-punished model rationally picks the lower-risk 2D path.

---

## 6. Exploration tracks (paste-ready agent briefs)

### Track A — 3D asset/scene creation via MCP or headless-Blender factory
**Evidence.** Content density is capped by the contract's `load()`/`preload()`/asset ban;
demos rely on imported `.glb`. But the contract ALLOWS building meshes in code
(`ArrayMesh` from vertex arrays, `StandardMaterial3D`, `MeshInstance3D`). So the asset ban
can be sidestepped by BAKING parametric meshes to inline vertex-array GDScript rather than
loading files. **Agent should:** (1) test whether `bpy` (headless Blender) is installable
inside `~/gi/gi-certifier.sif` under `apptainer` (CPU-only mesh gen; no GPU needed);
(2) prototype a "mesh factory" that emits `ArrayMesh`-ready GDScript from a parametric
description (ring, spire, car body, sheep) — bypassing the `load()` ban entirely;
(3) alternatively evaluate a Blender-MCP server the designer LLM can call at design time;
(4) measure token/latency cost on the ORCD stack. **Deliverable:** go/no-go on
headless-Blender-in-apptainer + a working code-baked-mesh path that raises visual density
WITHOUT touching the sandbox ban, plus a cost estimate.

### Track B — forcing stronger variety
**Evidence.** The system prompt already SHOUTS diversity ("DIVERSITY IS THE JOB", "two
prompts sharing a word must not become the same game", "pick the least obvious"), yet all
6 converge to the same skeleton: 2D, single controlled body in a bounded box,
`apply_central_force` hacks, latch-checkpoints; every 3D prompt → 2D. Exhortation is not
working. **Agent should** compare three mechanisms: (i) a QD / MAP-Elites archive over a
behaviour descriptor `{dimension × controlled-body-kind × win-shape × action-count}`,
rewarding empty-cell fills; (ii) an explicit anti-similarity oracle — cheap
feature/embedding diff vs the last N games, fed into the repair loop as a directive ("too
similar to game X on {dim, body}"); (iii) **forced-choice menus** — sample and PIN a
`{dimension, body, win-shape}` descriptor into `_first_user_msg`, turning diversity from
hope into a constraint. Data says (iii) is highest-leverage AND fixes dimensionality at
once (pin dimension=3D for a 3D-named prompt → collapse ends). **Deliverable:** a
variety-forcing mechanism (start: pinned descriptor + repair-loop similarity oracle),
scored by behaviour-descriptor coverage across a batch.

### Track C — prompt errors / bad bias in OUR surfaces  *(highest leverage, cheapest)*
**Evidence (audit done, this pass).** Bias FOUND: `_first_user_msg` (gamegen.py:259-262)
hardcodes "Design an original **2D** physics game" for the 3D-capable gdscript lane —
stale py/js inheritance. Plus the "seed not spec / surprise us / least obvious / neither
dimension is default" framing (api_gdscript.md 1, 9, 13) licenses discarding explicit
constraints; plus the 3D-only `PhysicsServer3D.set_active` gotcha raises 3D risk. **Agent
should:** (1) make `_first_user_msg` dimension-neutral (or dimension-AWARE — echo the
prompt's explicit dimension) for the gdscript lane; (2) add a clause to api_gdscript.md:
"when the prompt NAMES a dimension or a specific mechanic, HONOR it — the seed/surprise
framing applies only to UNspecified aspects"; (3) A/B the fix on the three 3D prompts,
measuring dim-honored rate. **Deliverable:** the one-line fix + prompt clause + a
before/after on {dim-honored, certified} for gen_0/1/2. Do THIS before Track D so the
model comparison isn't confounded by the "2D" bug.

### Track D — model ceiling (is GLM 5.2 enough?)
**Evidence.** Model = `z-ai/glm-5.2` (OpenRouter, DigitalOcean). Its null-content
pathology (spend whole budget thinking → `content=null`) caused gen_0's total loss and
motivated the thinking-off salvage. But ~79% mechanic fidelity shows the model transcribes
contracts competently; the two big failures are the DIMENSION BUG (ours, Track C) and
SOLVABILITY-TUNING (Track E) — neither cleanly a model-ceiling problem. **Agent should**
run a 6-prompt A/B: GLM-5.2 vs a stronger designer on the `anthropic` backend
(`claude-opus-4-8`, adaptive thinking — already wired in `_run_anthropic`), on
{dim-honored, mechanic-fidelity, certified-rate, corrections}. **Run only AFTER Track C**
so the "2D" prompt bug doesn't confound it. Cost is small (6 × ~5 attempts × ~17k tokens).
**Deliverable:** a confounder-free model A/B that isolates ceiling from prompt-bug.

### Track E — solvability / difficulty auto-tuning  *(DATA-suggested, not on the list)*
**Evidence.** 3/6 games are built-but-unsolvable (gen_1 parking stop, gen_2 drone arch,
gen_5 herding pen — the last even after the model self-relaxed the win to 2/3). Faithful
mechanics, constants tuned outside the solver's solvable band; the repair loop's "stuck
between X and Y" hint isn't enough for the model to reason the physics back in. **Agent
should** prototype a difficulty auto-tuner: expose the design's tunable constants (arch
gap, brake force, flee force, timer) and run a bounded coordinate-descent using the
harness's own tree solver toward "just-solvable", OR feed the solver frontier (which
checkpoint it stalls at + margin) as a structured tuning directive. **Deliverable:**
convert UNSOLVED-but-faithful games into COMPLETED WITHOUT changing mechanics — measured on
gen_1/2/5.

### Track F — harness robustness / observability  *(DATA-suggested)*
**Evidence.** gen_0's mid-loop null-content discarded the entire attempt history
(`attempts:[]`, `design:null`) via the `_repair_loop`→`_dispatch` unwind, mis-reporting a
run that generated code as a 0-attempt infra failure; and the thinking-off salvage that
"reliably" works FAILED here. **Agent should:** (1) make `_repair_loop` preserve partial
attempts across a mid-loop `_BackendUnavailable` (return what exists, verdict ENV_ERROR,
history intact); (2) harden null-content salvage — retry N times / rotate provider / on
explicit-openrouter null, fall through to the `anthropic` backend rather than dying.
**Deliverable:** no run ever reports 0 attempts when code was generated; salvage
success-rate measured. Small, but it protects every future measurement.

---

## Appendix — key file references
- Prompt composition / smoking gun: `harness/gen/gamegen.py` — `_first_user_msg` (249-262),
  `_gdscript_system_prompt` (206-222), `_repair_loop` (650-714), salvage `_openrouter_complete`
  (519-553), `_dispatch` stub (819-842).
- Contract: `harness/gen/prompts/api_gdscript.md` (dimension lines 11-16, 28, 30; seed
  framing 1, 9, 13; asset ban 46, 64), `design_block_gdscript.md`.
- Skill routing: `harness/gen/skill_context.py` (LLM router `_llm_route` 304-346;
  "2D/3D physics skill ensured a slot" 358; orchestrator 2D-vs-3D 442).
- Games: `~/gi/scenes/games/{a_3d_game_fly_a_small_craft_through_a_se, a_3d_parking_challenge_drive_a_car_from_,
  a_3d_drone_course_pilot_a_quadcopter_thr, top_down_maze_push_a_heavy_crate_through,
  side_view_platformer_hop_across_a_chain_, top_down_herding_steer_three_skittish_sh}/`
- Demos (bar): `godot_rl_agents_examples/examples/{3DCarParking/scenes/car/Car.gd, FPS, HovercraftRacing, ItemSortingCart, ScoreTheGoal}`
- Results: `~/orcd/scratch/gi/runs/long3d/gen_{0..5}.json`
