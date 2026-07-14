# godot_rl_agents_examples — construction-pattern audit (for our Godot spec + ORCD)

> Research agent, 2026-07-14. Target: `edbeeching/godot_rl_agents_examples` (the maintained
> env bank for `godot_rl_agents`). Cloned live (`--depth 1`, HEAD `d659636`, 2026-01-22;
> long-path checkout, plugin `addons/` skipped). Elias's directive: mine **CONSTRUCTION
> patterns only** — how the envs are *built* (scene trees, AIController wiring, spawners,
> movers, reward shaping, training/export). **No themes or objectives copied**; our games
> invent their own. This is the pyramid's "learn from validated environments" rung, feeding
> (a) `godotworld/SPEC.md` v2 and the certified `.tscn` bank, (b) the ORCD training phase.
> Evaluation lens = our spec's 3 named gaps (`GODOT_LANE.md`): ordered switch/gate,
> joint contraptions, per-step moving threats.

## Headline findings (read these first)

1. **The bank is ~95% 3D.** Of 21 env folders, exactly **two are genuinely 2D**: `BallChase`
   and `TestExamples/SimpleMemoryTest`. "Flat-looking" envs (CrossTheRoad, DownFall) are built
   with **3D nodes**. So there is almost no *2D-node* prior art to copy — but the **construction
   idioms are dimension-agnostic** and transfer cleanly (AIController wiring, spawners, movers,
   reward shaping, batching, export). I audit the 3D envs for those idioms, not their geometry.
2. **No CI, no single Godot pin.** No `.github/`. Each env is frozen at whatever engine it was
   authored on — **Godot 4.1 → 4.5 spread across envs** (table below). "Maintained" = the repo
   accepts PRs and ships a trained `.onnx` per env; it is *not* continuously re-tested.
3. **Every env requires the .NET/mono build** (`config/features` includes `"C#"` on nearly all;
   `.csproj`/`.sln` present) — because ONNX in-editor inference needs it. Confirms `GODOT_RL_MERGE.md`.
4. **No 2D joints anywhere.** The only joint in the whole bank is `Generic6DOFJoint3D` (3DLander
   legs). Our **joint-contraption gap gets zero direct transfer here** — must be designed, not copied.
5. **The other two gaps DO have clean transfers:** ordered gating → JumperHard's `next` counter;
   moving threats → CrossTheRoad car mover + DownFall rotating hazards + Ships PathFollow.

---

## 1. Inventory (all 21 env folders)

| Env | Dim | Controller base | Action space | Obs style | `reset_after` | Godot | Notes |
|---|---|---|---|---|---|---|---|
| **BallChase** | **2D** | `AIController2D` | `move` cont size 2 | pos(norm)+rel-target+raycast16 | 20000 | 4.1 | the one real 2D env; chase collectible |
| **SimpleMemoryTest** | **2D** | `AIController2D` | `answer` disc size N | one-hot hint + flag bit | (custom) | 4.5 | pure memory/recall; `@export` difficulty knobs |
| SimpleReachGoal | 3D | `AIController3D` | cont | 3 typed raycasts + rel-pos | — | 4.3 | GDScript/C#/C#All variants; **easy quick-test (50k steps/3min)** |
| 3DCarParking | 3D | `AIController3D` | steer/throttle cont | — | 1750 | 4.2 | `disable_parked_cars_count` difficulty knob |
| 3DLander | 3D | `AIController3D` | 7× disc size-2 thrusters | vel+legs+raycast360+goal | 6000 | 4.4 | **only joint in bank** (6DOF legs); noise terrain |
| AirHockey | 3D | `AIController3D` | cont | — | 2500 | 4.4 | 1v1 self-play |
| CrossTheRoad | 3D | `AIController3D` | `movement` disc size 5 | egocentric grid window | 40 | 4.3 | **moving-threats transfer**; grid; has tutorial |
| DefendTheGoal | 3D | `AIController3D` | move disc3 + jump disc2 | raycast+pos+vel+turret+jumptimer | 90 | 4.5 | turret projectile threat |
| DownFall | 3D | `AIController3D` | jump/move/turn cont | raycast+goal+floor+level-1hot | 2000 | 4.2 | **rotating hazards** (spike_roller, swiper); 4-level rotation |
| FPS / RobotFPS | 3D | `AIController*` | cont+disc | raycasts | 0 / — | 4.1/4.3 | multi-agent teams; heavy |
| FlyBy | 3D | `AIController3D` | turn/level cont | — | 200000 | 4.1 | plane; very long episode |
| HovercraftRacing | 3D | `AIController3D` | accel/steer cont | track-points+raycast+rival | 50000 | 4.2 | `number_of_car_groups_to_spawn`; per-group physics layers |
| ItemSortingCart | 3D | `AIController3D` | `acceleration` cont size 1 | 12-vec incl tick + `item_category` | 5000 | 4.4 | catch/sort falling items to correct bin |
| JumperHard | 3D | `AIController3D` | jump/move/turn cont | move+goal+grounded+raycast | 20000 | 4.1 | **ordered jump-pad `next` counter** |
| MultiAgentSimple | 3D | `AIController3D` ×2 policies | cont (robot 2, platform 1) | rel-pos+vel+raycast | 300/1800 | 4.3 | **RLlib multi-agent**; alternating goal; coop |
| MultiLevelRobot | 3D | `AIController3D` | `movement` cont size 2 | tick+goal+coin+enemy+collected | 3500 | 4.2 | **curriculum + collect-N + enemies**; runtime Area3D wrapping |
| Racer | 3D | `AIController3D` | cont | waypoints | 10000 | 4.1 | waypoint reward; `speed_up` knob |
| RobotVolleyball | 3D | `AIController3D` | cont | — | — | 4.2 | 1v1 sport |
| ScoreTheGoal | 3D | `AIController3D` | accel/steer cont | ball+goals+`correct-goal`+`hit` flag | — | 4.3 | push ball to correct goal; one-shot latch |
| Ships | 3D | `AIController3D` | cont | grid sensor + path | — | 4.1 | **PathFollow3D movers**; mines/chests |
| VirtualCamera | 3D | `AIController3D` | cont | — | — | 4.2 | camera-control task |

(3DCarParking's per-env `readme-license.md` etc. omit training stats; blank cells = not fetched/not stated.)

---

## 2. Construction-pattern cards (file paths are repo-relative into the clone)

### CARD A — 2D scene organization & AIController wiring (`BallChase`)
- **Scene tree** (`examples/BallChase/BallChase.tscn`): root `Node2D` → `Player`
  (`CharacterBody2D`, script `Player.gd`) with children `CollisionShape2D`, `RaycastSensor2D`
  (a `Node2D` running the plugin sensor), `Sprite2D`, and **`AIController2D` as a child in
  `groups=["AGENT"]`** (this group is how `Sync` discovers agents). Siblings: `Fruit` (`Area2D`
  collectible), `Walls` (a `Node2D` container of `Area2D` walls + obstacles), `BackGround`,
  `WorldEnvironment`. **Collisions are wired as scene signals**: each wall's `body_entered` →
  `Player._on_*Wall_body_entered`. `reset_after=20000` is set as a node property on the controller.
- **AIController** (`examples/BallChase/AIController2D.gd`): implements the 4 required methods.
  `get_obs()` returns `{"obs":[...]}` = player pos normalized to **[-1,1]** via `(pos.x/W-0.5)*2`,
  a **normalized relative-target vector** + `distance/1500`, then `append_array(raycast_obs)`.
  `set_action` copies a continuous `move` (size 2) into the player. `get_action_space()` =
  `{"move":{"size":2,"action_type":"continuous"}}`. Timeout reset lives in the controller's
  `_physics_process` (`n_steps>reset_after → done+needs_reset → reset()`).
- **Player physics** (`examples/BallChase/Player.gd`): steering-follow (`velocity += (target-vel)*friction`,
  `move_and_slide()`), **procedural respawn** `_calculate_new_position()` that **rejects positions
  overlapping walls/too close** via `Rect2.intersects` (recursion until valid) — a reusable
  collision-avoiding spawner. Reward assembled game-side (see Card D).

### CARD B — how levels are laid out (three distinct idioms; **none use TileMap**)
1. **Hand-placed nodes** (most envs): obstacles/walls are individually placed `Area2D`/`StaticBody`
   nodes in the `.tscn` (BallChase `Walls/Obstacle4/5`; DownFall's 4 authored levels).
2. **Procedural grid built in code** (`examples/CrossTheRoad/Completed/scripts/grid_map.gd`,
   `class_name Map`): `set_cells()` calls `add_row(tile, second_tile, second_tile_count)`;
   `set_row_tiles` **randomly removes `second_tile_count` columns** (`randi_range`) to scatter
   trees/obstacles, tracks `road_rows`, and randomizes player start. Tiles are `PackedScene`
   instances keyed by a `Dictionary[Vector3i → Tile]`; `get_tile(grid_pos)` is the world query.
   **This is the closest thing to a procedural spawner-driven level in the bank.**
3. **Runtime collider attachment** (`examples/MultiLevelRobot/scenes/level/level_manager.gd`):
   at `_ready()` it *finds* `Coin_*`/`Enemies*` mesh nodes and **wraps each in a fresh `Area3D`
   + `CollisionShape3D` in code**, setting collision layers/masks and connecting `body_entered`.
   Pattern worth stealing: author *visuals* in the scene, attach *colliders/sensors
   programmatically* at load — keeps the authored scene clean and the physics uniform.

### CARD C — the THREE GAP patterns

**C1 — Ordered switch/gate → JumperHard's `next` counter** (`examples/JumperHard/Player.gd`).
The cleanest ordered-enforcement idiom in the bank. A state var `var next = 1` selects the
*current* target pad. The pad triggers are **gated on that counter**:
```gdscript
func _on_First_Pad_Trigger_body_entered(_body):
    if next != 0: return          # <-- pad INERT unless it's this pad's turn
    ai_controller.reward += 100.0
    next = 1                       # advance the sequence
    second_jump_pad.position = calculate_translation(...)   # re-place the NEXT target
```
Reaching pad B only counts when `next==1`, then flips `next=0`. **This is exactly the
"switch B inert until A" logic our `on_contact` cannot express** (ours latches unconditionally).
The mechanism = *a small integer state + an early-return guard in the contact handler*.
MultiAgentSimple's "goal switches to the other platform after being reached" and ScoreTheGoal's
"`hit` flag disables further movement" are the same shape (a latched state gating later events).

**C2 — Moving threats → three driver styles, all deterministic, none via AnimationPlayer for the threat:**
- **Code patrol in `_physics_process`** (`examples/CrossTheRoad/Completed/scripts/car.gd`):
  `position.x += step_size * current_direction`; flip `current_direction` at edges. Spawned/placed
  by `car_manager.gd`: **one car per road row**, `create_car()`/`queue_free()` to match the layout
  on reset, randomized start-x and direction. Threat "contact" is a **grid-cell equality test**
  (`get_grid_position()==...`), not a physics contact — cheap and deterministic.
- **Rotating hazard** (`examples/DownFall/scripts/spike_roller.gd`, `swiper.gd`):
  `rotate_y(rotation_speed*delta)` — a spinning obstacle; `rotation_speed` is an `@export` knob.
- **PathFollow rail** (`examples/Ships/PathFollower.gd`): `extends PathFollow3D`, `progress_ratio
  += delta*speed`. Godot's `PathFollow2D` is the 2D twin — a threat/platform glued to an authored
  `Path2D`/`Curve2D` with one scalar of state. **Best fit for our "per-step moving threat" gap.**

**C3 — Joint contraptions → NOT PRESENT in 2D.** Only `Generic6DOFJoint3D` (3DLander legs,
`examples/3DLander/scenes/game_scene/Lander.tscn`). No `PinJoint2D`/`GrooveJoint2D`/
`DampedSpringJoint2D` anywhere. **Honest verdict: the bank offers no wrecking-ball/catapult
prior art; our joint work is greenfield** (design against Godot docs, not this repo).

### CARD D — reward shaping patterns (dense-shaping is the norm; sparse is the exception)
- **Distance-to-goal delta** (ubiquitous): reward the *improvement* over the best-so-far.
  `if d < best: reward += (best - d); best = d` (BallChase `shaping_reward`, JumperHard, CrossTheRoad
  `reward_approaching_goal`, MultiLevelRobot, MultiAgentSimple). Monotone potential → no reward loops.
- **Step penalty** `-0.01`/step to discourage dawdling (BallChase, JumperHard).
- **Milestone bonus** `+10`/`+100` on collect/reach (BallChase fruit +10, JumperHard pad +100) —
  the same shape as our `+1 per newly-latched checkpoint`.
- **Terminal ±** on success/death (`+1` goal, `-10` wall). Sparse-only exemplar =
  SimpleReachGoal (`+1`/`-1`/`-1`, nothing else) — deliberately the easy trainable baseline.
- **Multi-term shaping** (3DLander): distance + linear-vel + angular-vel + orientation + thruster-usage
  deltas summed per step — an example of aggressive shaping when the task is hard.
- **Reward accrual idiom**: game code adds into `ai_controller.reward` each physics step;
  `get_reward()` **returns then zeroes** it (`var r=reward; reward=0; return r` — JumperHard, DownFall).
  Mirrors our per-decision latch-delta read in `harness/rl/env.py`.

### CARD E — reset & episode termination
- Two reset triggers, both flow through `AIController.reset()`: **(i) internal** timeout
  (`n_steps>reset_after → done`) or death (kill-zone `body_entered`, fall, enemy-cell); **(ii) external**
  `needs_reset` set by the **`Sync` node when Python requests a reset**. Controllers poll `needs_reset`
  in `_physics_process` and call `player.game_over()`.
- **Reset ordering discipline** (`examples/CrossTheRoad/.../robot.gd::reset`): map first (sets new
  start + road rows) → then player position → then cars. Deterministic-reset hygiene matching our
  "fresh world per episode, all mutation in `_physics_process`" rule (`GODOT_RL_MERGE.md §6`).
- **Randomized reset for generalization**: random spawn within bounds (BallChase), random spawn
  rotation (JumperHard `randf_range(-180,180)`), random goal/obstacle from a fixed set (SimpleReachGoal),
  random initial pose+velocity (3DLander). Variety is injected at reset, not by level authoring.

### CARD F — observation design (recurring building blocks)
- **Raycast sensor** (the single most-used obs primitive; our spec has none).
  `examples/BallChase/addons/godot_rl_agents/sensors/sensors_2d/RaycastSensor2D.gd`: a fan of
  `n_rays` `RayCast2D` over `cone_width°`, each obs = `(ray_length - hit_dist)/ray_length` ∈ [0,1]
  (0 = no hit), with `collision_mask` + `collide_with_areas/bodies` filtering. Used by BallChase,
  JumperHard, SimpleReachGoal (3 *typed* sensors: goal/obstacle/ground), HovercraftRacing, 3DLander.
- **Egocentric / local-frame vectors**: targets given `to_local(...)` in the agent's frame, often
  `.normalized()` + a clamped scalar distance (`clamp(d,0,20)/20`). Rarely raw world coords.
- **Normalized-tick obs** `n_steps/float(reset_after)` (ItemSortingCart, MultiLevelRobot, 3DLander)
  — the exact progress signal our `env.py` appends.
- **State one-hots in obs**: current-level one-hot (DownFall), `correct_goal` 0/1 (ScoreTheGoal),
  `item_category` (ItemSortingCart), `all_coins_collected` bit (MultiLevelRobot). Same role as our
  latched-checkpoint one-hot — makes gated/multi-stage state observable to a feed-forward policy.
- **Global clamp** to [-1,1] over the whole obs vector (ItemSortingCart loops and `clampf`s).

### CARD G — difficulty / curriculum knobs (exposed, but ad-hoc — all `@export`)
- **Per-env `@export` difficulty**: `disable_parked_cars_count` (3DCarParking),
  `number_of_car_groups_to_spawn` (HovercraftRacing), `landing_surface_radius` /
  `landing_surface_max_dist_from_center_ratio` / terrain noise (3DLander),
  spawn/vel maxima (`max_initial_velocity`, `max_velocity`), and SimpleMemoryTest's
  `episode_length` / `answer_size` / `hint_after_action_steps`.
- **Level rotation** (a manual curriculum): DownFall/JumperHard-style cycling
  `current_level = (current_level+1) % len(levels)` on episode end (`examples/DownFall/down_fall.gd`);
  MultiLevelRobot gates level N+1 behind "all coins collected" (`check_all_coins_collected`).
- **No automated PLR/ACCEL/parameter-scheduling** anywhere — difficulty is hand-set in the inspector.
  (Good news for us: our G3'/PLR angle is genuinely additive, `GODOT_RL_MERGE.md §4`.)

### CARD H — training configs & headless/export (ORCD-relevant)
- **`Sync` node** = one per scene (`addons/godot_rl_agents/sync.gd`), exposes inspector knobs:
  `control_mode` (0 Human / 1 Training / 2 Onnx-Inference), `speed_up`, `action_repeat`.
  Examples: 3DCarParking training `speed_up=10`, testing `3`; CrossTheRoad training `speed_up=8.0,
  action_repeat=1`, onnx `speed_up=0.1`. `action_repeat` = frame-skip per decision (grid games use 1).
- **In-scene batching** (`examples/BallChase/BatchEnvs.tscn`, also the `main_scene`): the env scene
  is **instanced 16×, tiled spatially** (offset ~1305px x / ~750px y so worlds don't overlap) under
  one root with a single `Sync` + `Camera2D`. Each copy's `AIController` is in group `AGENT`.
  MultiAgentSimple `training_manager.scene_count=8`. This is parallelism-axis (i) from `GODOT_RL_MERGE.md`.
- **Trainers observed** (from per-env readmes): **SB3 PPO** is the default (`stable_baselines3_example.py`);
  **SB3 SAC** (ScoreTheGoal, `gradient_steps=8`); **RLlib** for multi-agent (MultiAgentSimple,
  `env_is_multiagent=true`). Common PPO knobs: `MultiInputPolicy` (dict obs), `n_steps` 32–768,
  `batch_size=n_steps*num_envs`, `n_epochs` 30–60, `target_kl` 0.006–0.02 (early-stop), `ent_coef`
  0–0.02. Discrete actions need plugin PR#16 (3DLander).
- **Reported cost/throughput** (their numbers, anecdotal, manual-stop): SimpleReachGoal 50k steps /
  ~3 min (the "easy" anchor); DefendTheGoal 700k; ScoreTheGoal ~3.1M (`--speedup=8 --n_parallel=8`);
  ItemSortingCart ~17.7M (`--speedup=64 --n_parallel=4`, `timesteps=30M`); 3DLander `--speedup=100
  --timesteps=50M`. **Speedups 8→100; n_parallel 4→8.**
- **Headless export** (`scripts/build_examples.sh`): `GODOT --headless --export-debug "Linux/X11"`
  and `"Windows Desktop"` per env → an executable in `bin/`; then `scripts/example_to_hub.py`
  pushes the folder to a HuggingFace **dataset** repo (`godot-rl` library tag). Training then runs
  the exported binary via `--env_path` (TCP to the trainer). **The build script hardcodes a single
  local mono binary `Godot_v4.2.1-stable_mono_linux` — there is no CI/Docker/Apptainer here.**

---

## 3. TRANSFER MAP (drives the next build wave)

| Extracted pattern (source) | (a) our spec v1 today? | (b) → spec v2 addition | (c) → certified `.tscn` bank |
|---|---|---|---|
| Distance-to-goal **delta shaping** (Card D) | ✅ via ordered `checkpoints` at rings; approximate | consider a `progress_toward(body,target)` checkpoint helper | — |
| **Milestone bonus** +N on reach/collect | ✅ = checkpoint latch (`+1` each) | — | — |
| **Step penalty / velocity clamp** | ✅ `on_step: velocity_clamp`; penalty is implicit in budget | optional `step_cost` in reward (RL-only) | — |
| **Normalized-tick + state one-hot obs** | ✅ our `env.py` already emits both | — | — |
| **Raycast sensor obs** (Card F) | ❌ absent | **ADD `sensors:[{type:raycast, n_rays, cone, length, mask}]`** → obs; high-value, widely used | ship a certified `RaycastSensor2D` node |
| **Ordered gate via `next` counter** (C1) | ⚠️ partial (unconditional latch only) | **ADD `ordered_flag{after:[...]}`** + a **flag-gated `on_contact`/behavior** (guard = state matches) | — |
| **Code-patrol mover** (CrossTheRoad car, C2) | ❌ (`rising_level` moves a scalar, not a body) | **ADD `on_step: move_body{axis|path, speed, bounce}`** | — |
| **Rotating hazard** (spike_roller, C2) | ❌ | `on_step: spin_body{omega}` (or fold into `move_body`) | certified spinner `.tscn` |
| **PathFollow rail mover** (Ships, C2) | ❌ | **ADD `path` primitive + `on_step: path_follow{path, speed}`** (needs `Path2D`/`Curve2D` in spec) | certified `Path2D`+follower `.tscn` |
| **Joint contraption** (C3) | ⚠️ approximate (`Pin/Damped` only) | real distance-joint / kinematic tether (no bank prior art — design fresh) | certified catapult/ball `.tscn` |
| **Procedural grid w/ random obstacle removal** (Card B2) | ❌ (bodies are hand-listed) | optional `spawn_grid{rows,cols,scatter}` generator (later) | — |
| **Collision-avoiding respawn** (BallChase) | n/a (our reset is deterministic seed) | keep deterministic; borrow the AABB-reject trick only if we add random spawns | — |
| **Conditional goal** (ScoreTheGoal correct-goal, ItemSortingCart category) | ⚠️ partial | flag-gated success (`success` reads a per-episode label flag) | — |
| **In-scene batching (16× tiled)** (Card H) | n/a (we batch via executor/JSONL) | keep executor path; batching is the OUTER-rung concern | template for the outer `Sync` scene |

Priority for spec v2, in order: **(1) `sensors:[raycast]`** (biggest obs gap, everyone uses it),
**(2) `on_step:move_body`/`spin_body`** (closes SURVIVE moving-threats), **(3) `ordered_flag` +
flag-gated contact** (closes ACTIVATE-SEQUENCE), **(4) `path`+`path_follow`**, **(5) real joint**
(closes TOPPLE; greenfield). Items map 1:1 onto the three named gaps in `GODOT_LANE.md`.

---

## 4. ORCD notes (headless-at-scale)

- **Export presets**: use the two named in `scripts/build_examples.sh` — `"Linux/X11"` and
  `"Windows Desktop"` — via `--headless --export-debug`. On a Slurm/Linux cluster you want the
  Linux preset → a self-contained executable; `--headless` uses a dummy display (no Xvfb).
- **Mono/.NET is required by the bank's envs** (ONNX inference node). **We can avoid this**: our
  lane is pure-GDScript `runner.gd` + executor JSONL, so we can target the **standard (non-mono)
  Godot** and skip the .csproj/.NET toolchain entirely — smaller, simpler container. Only adopt
  mono if we later run the outer `Sync`/ONNX rung.
- **No pinned engine / no CI / no Docker in this repo.** Versions drift 4.1→4.5 per env; the build
  script hardcodes one local `4.2.1-mono` binary. For ORCD we must **pin our own Godot** (4.7 target
  per `GODOT_RL_MERGE.md §6`) and bake the `--headless --import` cache at image-build time.
- **Parallelism**: two axes both shown here — in-scene duplication (`BatchEnvs.tscn` 16×) and
  process-level `--n_parallel` (4–8). Our executor path (subprocess→JSONL, no sockets) is the
  cleanest Slurm-array fit (`GODOT_RL_MERGE.md §5`); their TCP path needs per-job port offsets.
- **Speedup** used aggressively (8→100 via `Sync.speed_up`); irrelevant on our executor path (we
  drive `--fixed-fps`). **Asset weight**: the bank ships heavy assets (HDRIs, Blender meshes, `.onnx`
  per env) — our certified `.tscn` bank should stay primitive/lightweight to keep images small.
- **Licensing**: repo is **MIT**; several envs carry separate `asset-license.md`/`readme-license.md`
  for their art (CC/attribution). Since we copy **construction only, no assets/themes**, no
  attribution obligation attaches to us — but do not vendor their art.

---

## 5. Honesty block (what I could/couldn't verify)

- **Fetched live & fully read**: cloned HEAD `d659636` (2026-01-22); read in full — BallChase
  (controller/player/scene/BatchEnvs/project.godot), CrossTheRoad Completed (car, car_manager,
  robot, robot_ai_controller, grid_map), DownFall (down_fall, ai_controller, spike_roller),
  JumperHard (Player, AIController3D), Ships PathFollower, MultiAgentSimple platform, MultiLevelRobot
  level_manager, SimpleMemoryTest controller, RaycastSensor2D, both build scripts, and 9 env readmes.
  Verified 2D/3D by grepping actual scene node types; verified joint absence by grepping all `.tscn`/`.gd`.
- **Star/fork count** (~88★/20 forks) from the GitHub landing page via WebFetch — the bank is far
  smaller/less-starred than the *library* (`godot_rl_agents`, ~1.5k★). Not independently re-counted.
- **Training stats are the authors' own anecdotes** (steps-to-solve, ep_mean_reward) pulled from
  per-env readmes; most say "manually stopped" — treat as order-of-magnitude, not benchmarks.
- **Not deep-read** (surveyed via readme/grep only, low transfer value): Racer, FPS/RobotFPS,
  RobotVolleyball, AirHockey, VirtualCamera, FlyBy, 3DCarParking internals, SimpleReachGoal C#
  variants. I read their readmes for the training table but not every script — flag if a builder
  needs one of these expanded.
- **`addons/` plugin internals** (Sync/AIController base classes) were intentionally not re-audited
  here beyond RaycastSensor2D — they're already covered in `GODOT_RL_MERGE.md §1`. The plugin is a
  git submodule (`.gitmodules` → `godot_rl_agents_plugin`) vendored into each env.
- **No CI/workflow files exist** (`.github/` absent) — "maintained/CI status" in the task is
  answered: maintained-by-PR, **no CI**, no per-env test.
