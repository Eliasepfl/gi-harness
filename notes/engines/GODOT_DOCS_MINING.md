# Godot 4.7 Official-Docs Mining — Tool Precision & Agent Robustness

_Dated 2026-07-15 (early). Commissioned by Elias: an official godot-docs swarm (5 areas: physics
semantics, best-practices, scripting/Expression, CLI/headless/determinism, 2D rendering/dressing)
cross-checked against our frozen `runner.gd` + `project.godot` + `render.py`. Goal: make
`inspect_world` and friends mirror EXACT engine semantics, not approximations, and harden the
runner/serve contract. Every claim below is doc- or code-cited in §7._

Hey — this is the distilled, actionable version. If you only read one thing, read the §6 gotchas
table and the §3 checklist. The runner is largely correct; the real wins are precision in the
static analyzer and three missing determinism pins.

---

## 1. INSPECT_WORLD PRECISION UPGRADES (mirror the engine exactly)

1. **Gravity vector is `(0,-1)·900 = (0,-900)`** — read `default_gravity` + `default_gravity_vector`
   from the spec/project, DON'T assume Godot's `(0,+1)·980`. Our world is y-UP; "down" = -Y. Every
   floating / unsupported / OOB-below-floor verdict references this vector or it inverts sign.
2. **AABB parity with `runner._bbox`, byte-identical.** Box `size` is FULL extents centered at
   origin (verts at ±size/2, halve it); circle = center±radius, rotation-invariant; box/poly/segment
   = extents of ROTATED vertices. No safe/grow margin is added in 2D (unlike 3D CharacterBody) — do
   not inflate.
3. **Rotated-box AABB, exact formula:** half-extents `(|hx·cosθ|+|hy·sinθ|, |hx·sinθ|+|hy·cosθ|)`
   = abs-sum of rotated Transform2D basis columns. Use this, not a heuristic.
4. **Containment is conservative for rotated bodies.** `contained()`/`contacts()` are AABB-in-AABB;
   a tilted box/poly has an inflated AABB, so "fully inside a zone" can read false when it's
   geometrically inside. WARN when a rotatable body sits in a containment/parking goal.
5. **Overlap timing is one-step-latent.** `Area2D.get_overlapping_bodies()` and the entered/exited
   signals update ONCE during a physics step, reflecting PRE-move positions; a freshly added node
   registers only after one step elapses. `inspect_world`'s "initial overlap" is a tick-0 GEOMETRY
   fact the engine won't SIGNAL until stepped — never treat it as an engine-emitted signal.
6. **Sleeping is disabled** (`can_sleep=false` on every body) AND damping is 0 — a free body coasts
   FOREVER. A "come to rest / park" success predicate is UNSATISFIABLE without a friction surface or
   an on_step velocity_clamp. Assert this precondition; don't model an auto-settle.
7. **Closed-form ballistic forecast:** zero damping + fixed δt=1/60 ⇒ project N ticks exactly:
   `v += g·dt; x += v·dt` per tick (`x += v·dt + ½·g·dt²`). Overlap/OOB warnings become quantitative.
8. **Convexity check:** `poly` verts go straight into `ConvexPolygonShape2D.points` with NO hull
   repair; non-convex ⇒ undefined collision while `_bbox` still reports a plausible AABB. Validate.
9. **Contact-cap warning:** `max_contacts_reported=8` silently drops contacts; flag goals expecting a
   body to touch >8 others (pile-ups/dense stacks) → false "not grounded".
10. **Tunneling heuristic:** CCD is DISABLED and `VMAX=1e5` px/s is allowed; a thin static/segment
    wall whose thinnest dim < per-step displacement (speed/60 px) can be passed through in one tick,
    so `contacts()`/`contained()`/`grounded()` may never fire. Recommend thicker wall / clamp / CCD.
11. **All bodies share `collision_layer=mask=1`:** everything mutually collides and every sensor Area
    monitors every body INCLUDING StaticBody2D. `contained(zone, wall)` can be unexpectedly true;
    selective pass-through is currently inexpressible.
12. **Predicate linter** (static replica of `_pred_error`): flag `&&`/`||`/`&`/`|` (rewrite→and/or/
    not), INT/INT division that likely wants fractions (suggest float literal), and short arg counts
    (Expression requires ALL params, even class-ref-optional ones → silently-false predicate).

## 2. RUNNER / SERVE CORRECTNESS

- **Notification order is a fixed guarantee we rely on.** `_enter_tree` cascades parent→child;
  `_ready` cascades child→parent ("once all child nodes have finished calling theirs"). Set-props-
  THEN-`add_child` (logic_preferences: "change values on a node before adding it to the scene tree")
  is exactly what the runner does for bodies and sensors — CORRECT. `add_child` fires `_enter_tree`/
  `_ready` SYNCHRONOUSLY when the parent is already in-tree, which is why sensors (added after
  `root.add_child(_container)`) must have `n_rays`/`ray_length`/etc set beforehand.
- **`_ready` runs ONCE per node LIFETIME by default** (not per tree-entry — the best-practices page's
  phrasing is about ordering). The runner's teardown = `queue_free()` + fresh `.new()` rebuild
  sidesteps this. ⚠️ LOUD INVARIANT: never "optimize" by reusing nodes across episodes
  (`remove_child`→re-add) — sensor `_ready` won't re-fire and rays silently won't respawn unless
  `request_ready()` is called. Codify in a CONTRACTS comment.
- **`queue_free()` + `await process_frame` before rebuild is load-bearing** — deferred free means the
  previous episode's bodies stay LIVE in the physics space until the idle flush; skip the frame and
  two worlds' bodies briefly coexist and double-count contacts. Keep the pair.
- **Thread-safety: the serve loop is 100% main-thread and MUST stay so.** "Interacting with the
  active scene tree is not thread-safe"; PhysicsServer2D isn't thread-safe unless run-on-separate-
  thread (we pin false). The `_read_n` busy-wait (`OS.delay_msec(1)`, NO `await`) is the CORRECT
  mechanism that freezes the world byte-identically between ops — not a hack. ⚠️ Guard-rail comment
  forbidding any "move socket read to a worker thread" refactor.
- **Expression security: `ALLOWED_IDENTS` is the ONE AND ONLY boundary.** With a base instance,
  bare identifiers dispatch via `base.callp(method,…)` on our QueryCtx (RefCounted→Object), so
  `free()`, `set_script()`, `call()`, `callv()`, `notification()`, `connect()`, `get_instance_id()`
  are all REACHABLE as plain words; and every `@GlobalScope` fn (`str_to_var`, `instance_from_id`,
  `randi`…) is callable with NO base instance. The allow-list (11 query fns + `steps` + 8 math fns +
  and/or/not/true/false), `ALLOWED_OPS="+-*/%(),<>=!"`, and rejection of `.`/`[`/`]`/`\` are what
  close the hole — correctly designed. `const_calls_only=true` is a TRAP: our QueryCtx methods aren't
  METHOD_FLAG_CONST, so flipping it breaks every predicate. Add a red-team fixture asserting the
  above idents all fail `_pred_error`.
- **MISMATCH (minor, perf):** `_add_sensor` calls `load(SENSOR_SCRIPTS[stype])` at build time, inside
  the physics-sensitive rebuild path — docs say `load()` hitches mid-process. Preload the finite
  whitelist at `_initialize` (const table) → free thereafter, fails fast at boot on a bad path.
  Also: `load()` returns null on a typo'd/wrong-case path and the runner silently `return`s (drops
  the sensor) — validate paths at certify time (mirror `diag.gd`'s `ClassDB.class_exists`).
- **`SceneTree.quit(code)` is the ONLY exit-code channel** — `OS.set_exit_code`/`get_exit_code` do
  NOT exist in 4.7 (removed 4.2.1). Codes 0–125; quit is deferred to end-of-iteration (code after it
  still runs). `OS.read_string_from_stdin(65536)` returns ONE newline-terminated line ≤64 KiB — the
  batch protocol must newline-delimit and cap each job.

## 3. DETERMINISM PINS (project.godot deltas — ready checklist)

Godot physics is officially NON-deterministic (floating point) → our witness replay is valid ONLY
same-image + fixed-δt + single physics thread. Say so in SPEC; don't advertise cross-image replay.

Already pinned (KEEP, all correct):
- [x] `physics_ticks_per_second=60`, `max_physics_steps_per_frame=8`
- [x] `physics_jitter_fix=0.0` (default is **0.5**! ≤0 = ticks fully synchronized — load-bearing)
- [x] `2d/run_on_separate_thread=false`, `default_linear_damp=0.0`, `default_angular_damp=0.0`
- [x] runner re-asserts ticks/max-steps/jitter in `_initialize()` (runtime OVERRIDES project.godot —
      valid belt-and-suspenders, not redundant)

MISSING / gaps to close:
- [ ] **`--fixed-fps 60` is MANDATORY on every stepping invocation** and NOTHING in project.godot
      enforces it — it's a CLI flag ("disables real-time synchronization"). Drop it and δt becomes
      wall-clock-dependent → replay voids. Assert its presence in bench.py/launch; add a comment.
- [ ] `physics/common/physics_interpolation=false` (render-only, but pin so a 4.7 point-release
      default flip can't shift recorded transforms if rendering is ever added)
- [ ] `physics/2d/solver/solver_iterations` (pin so contact resolution can't drift between releases)
- [ ] consider explicit `application/run/max_fps=0`
- [ ] preflight assertions at episode start: `time_scale==1`, `physics_jitter_fix==0`,
      `physics_ticks_per_second==60`, `run_on_separate_thread==false` — fail loudly if drifted.
      Adopt `Engine.get_physics_frames()` as the canonical tick counter for capture/replay.

CI gotchas (returncodes lie): `--headless --import` can quit before import finishes (GH #77508, use
`--quit-after 2` or bare `--import`) and returns 1 on success (GH #83449) — verify `.godot/`
artifacts, not returncode. Headless EXPORT returns 0 on error / dies by signal (GH #83042) — stat
the output artifact instead.

## 4. PERFORMANCE PATHS (each: win / cost)

- **Raw `PhysicsServer2D` bodies** (skip RigidBody2D nodes) — WIN: less memory + one fewer layer for
  N-episode batch throughput. COST: high complexity; MUST still step via `await physics_frame`, and
  read state from the `PhysicsDirectBodyState2D` in a `body_set_force_integration_callback` — NEVER
  poll `body_get_state()`/server getters per tick (async server STALLS on value-returning calls,
  "severely decrease performance if you call them every frame"). Prototype behind a flag, byte-diff
  vs the node path before adopting. Negligible at our current body counts.
- **Static typing hot loop** — WIN: optimized opcodes when types known at compile time (no published
  numbers). COST: near-zero. Type `_q_pos/_q_vel/_q_speed/_q_angle/_q_dist`, `_order`/`_checkpoint_
  keys` as `Array[String]`, swap internal GDScript math to typed variants `absf/minf/maxf/clampf/
  floorf/ceilf/signf`. ⚠️ DO NOT rename the DSL names `abs/min/max/clamp/floor/sign` — those run
  inside Expression's evaluator, not compiled GDScript; renaming breaks specs.
- **Preload sensor scripts** (see §2) — WIN: removes a `load()` from the physics-sensitive rebuild,
  fails fast at boot. COST: none.
- **PackedScene for large certified bank templates** — WIN: engine batches instancing, faster than
  procedural `new()+add_child`. COST: only pays off once templates grow; keep procedural rebuild for
  small per-episode counts.

## 5. DRESSING VOCABULARY MAP (bucket-A, asset-free & deterministic-first)

Reality check: `--headless` uses a DUMMY rasterizer that renders NOTHING → `get_texture().get_image()`
is blank in our Apptainer image. In-engine capture needs a software Vulkan (Mesa lavapipe/SwiftShader)
we don't ship. So `render.py`'s Python redraw stays the witness path, and the cheapest visual depth is
a DATA-driven draw-recipe that BOTH a frozen `_draw()` node and `render.py` interpret with no assets.

| spec block | maps to | key props / notes |
|---|---|---|
| `decor` (draw-recipe) | `CanvasItem.draw_*` in a frozen `_draw()` Node2D + PIL | `kind:line\|polyline\|rect\|circle\|arc\|polygon\|dashed_line\|string`, `color[rgba 0..1]`, `width`, `filled`, `z`→CanvasLayer.layer. Painter's order. Fixed kind→fixed call, NO string executed → determinism-safe. **Build this first.** |
| `camera` | `Camera2D` | `zoom` is a MULTIPLIER (>1 = zoom IN, covers zoom²-smaller — INVERTED from Godot 3!); `position_smoothing_speed` px/sec asymptotic (default 5.0); `limit_*`; `anchor_mode` DRAG_CENTER(1)/FIXED_TOP_LEFT(0); set `process_callback=CAMERA2D_PROCESS_PHYSICS(0)` for lockstep replay. Default limits = union AABB of bodies. |
| `background` | `CanvasLayer` | `layer<0` behind bodies; solid `fill`→full-viewport rect; `parallax>0`→`follow_viewport_enabled`+scale. HUD variant `layer>0` stays screen-fixed. |
| `tint` | `CanvasModulate` | `color` (default white=no-op) multiplies whole canvas; AT MOST ONE per canvas (multiples conflict); prerequisite for any visible 2D light. |
| `tile` (optional) | `TileMapLayer` | Needs a vendored `.tres` TileSet (not pure JSON). ⚠️ collision is pulled AUTOMATICALLY from the TileSet's physics layers — a decor tileset MUST declare none or it silently alters gameplay. Prefer `decor` unless real tiling is needed. |

**Fix `render.py` FollowCamera to match Godot:** (a) zoom is a MULTIPLIER, not a divisor; (b) replace
`CAM_SMOOTH=0.35` per-frame lerp with the asymptotic form `factor = 1 − exp(−speed·dt)` (dt=1/60):
default speed 5 ⇒ ≈0.080/frame, so our 0.35 is ~4× too snappy vs a stock Camera2D; (c) keep the
existing world-y-up→screen-y-down flip (correct vs Godot's native y-DOWN).

## 6. GOTCHAS TABLE (doc truth vs our likely assumption)

| # | Trap | Our naive assumption | Truth |
|---|---|---|---|
| 1 | Damping | bodies "eventually settle" | damping=0 AND can_sleep=false → coast forever; only friction/clamp stops them |
| 2 | Gravity dir | Godot default down = +Y | our world is `(0,-900)`, down = -Y; analyzer coded to +Y inverts every verdict |
| 3 | Overlap lists | reflect current positions | one-step-latent, PRE-move; empty until a step runs |
| 4 | CCD | on / walls solid | DISABLED; fast body tunnels thin walls in one 1/60 s tick |
| 5 | contact cap | all contacts reported | `max_contacts_reported=8` silently drops the rest |
| 6 | Camera zoom | divisor (Godot 3) | MULTIPLIER in 4.x; >1 zooms IN |
| 7 | `&&`/`\|\|` | usable in predicates | REJECTED (no `&`/`\|` in ALLOWED_OPS); use and/or/not |
| 8 | Expr `/` | float division | INTEGER when both operands int (`steps/2` floors); use `2.0` |
| 9 | Expr args | optionals may be omitted | ALL params required → short call → null → silently-false predicate |
| 10 | `const_calls_only` | free hardening | TRAP: QueryCtx methods aren't const → breaks all predicates |
| 11 | `_ready` | fires each tree-entry | ONCE per lifetime; node-reuse skips sensor ray respawn |
| 12 | `OS.set_exit_code` | exists | REMOVED 4.2.1; only `SceneTree.quit(code)` |
| 13 | `--headless` capture | pixels available | dummy rasterizer renders nothing; needs software Vulkan |
| 14 | import/export returncode | reliable pass/fail | GH #77508/#83449/#83042: spurious/0-on-error; verify artifacts |
| 15 | `time_scale`≠1 | scales tick count | scales sim-time PER tick, not count → desyncs state from tick index |
| 16 | poly verts | validated | fed raw to ConvexPolygonShape2D, no hull check → undefined collision |

## 7. CITATIONS

- **Physics semantics:** class_area2d.html (overlap "modified once during the physics step",
  monitoring, SpaceOverride), class_rigidbody2d.html (impulse time-independent vs force per-tick,
  `apply_torque_impulse` needs inertia, `inertia=0`=auto, `continuous_cd=0`, `can_sleep=true`,
  `contact_monitor=false`/`max_contacts_reported=0` defaults), class_rectangleshape2d.html (size=full
  extents), class_convexpolygonshape2d.html (points must be a convex hull), class_projectsettings.html
  (gravity/damp defaults), tutorials/physics/{physics_introduction, troubleshooting_physics_issues}.
- **Best-practices (12 pages, .../tutorials/best_practices/):** godot_notifications (lifecycle order),
  logic_preferences ("change values before adding to tree"; load vs preload), data_preferences
  (Dictionary insertion-order), autoloads_versus_regular_nodes (corrected slug), godot_interfaces
  (load→cached, `.new()`), project_organization (case-sensitive PCK/Linux), scenes_versus_scripts.
- **Scripting/Expression:** class_expression.html, tutorials/scripting/evaluating_expressions.html
  ("all Global Scope methods available", "parameters always required", `&&`/`||`/`!`, int division),
  core/math/expression.cpp (`base.callp` vs `base.call_const`), class_engine.html
  (`physics_jitter_fix` default 0.5, `get_physics_frames`), thread_safe_apis.html, using_servers.html,
  static_typing.html.
- **CLI/headless/determinism:** command_line_tutorial.html (`--headless`=`--display-driver headless
  --audio-driver Dummy`, `--fixed-fps` "disables real-time synchronization", `--import` implies
  `--editor`+`--quit`), class_scenetree.html (`quit(0–125)`, `physics_frame` before `_physics_process`,
  no `set_exit_code`), class_os.html (`read_string_from_stdin` one line ≤buffer, `get_cmdline_user_
  args` after `--`), physics_introduction.html ("not deterministic"), creating_movies.html; GH #77508,
  #83449, #83042, #90646.
- **2D rendering/dressing:** class_camera2d.html (zoom multiplier, smoothing 5.0 px/s), custom_drawing_
  in_2d.html (draw_* table), canvas_layers.html, class_canvasmodulate.html, class_tilemaplayer.html,
  viewports.html (`await RenderingServer.frame_post_draw` → `get_image()`), 2d_transforms.html
  (y-DOWN, rotated-AABB), particle_systems_2d.html. All under `docs.godotengine.org/en/4.7/`.
- **Local code cross-checked (via `~/gi` → `/home/enaha/GI/gi-harness`):** godotworld/runner.gd,
  project.godot, boot.gd, diag.gd, bench.py; harness/render.py; harness/designer/tools.py (inspect_world
  not yet in REGISTRY — these truths land before it freezes).

_Caveat: doc pages were summarized by WebFetch's small model; the "_ready once per tree-entry"
paraphrase conflicts with the authoritative Node rule (once per lifetime, reset via `request_ready()`)
and is corrected above. Sleep-threshold / 2D solver numeric defaults could not be re-verified from the
4.7 ProjectSettings page (it truncates) — moot for us (sleeping disabled) but don't hardcode them._
