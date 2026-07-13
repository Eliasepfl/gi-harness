# Engine deep-dive: Claude ↔ Godot for the verification harness

> Research note (exploratory, no code changes). Author: research agent, 2026-07-13.
> Question asked: can Godot replace/augment our pymunk substrate as the *verification*
> engine, and how good is the **feedback loop quality (engine ↔ agent)**?
> Every claim below is tied to a primary source (official docs, GitHub repo, or issue);
> repo maturity numbers were pulled live from the GitHub API on 2026-07-13.

**TL;DR.** Godot is a genuinely strong *simulation substrate* for our loop — headless is
first-class, `_physics_process` maps 1:1 onto our `act → K steps → on_step → check` runner,
and `PhysicsServer2D` gives pymunk-grade programmatic state read/write. But three real taxes
separate it from pymunk: (1) **stock 2D physics is officially non-deterministic**, which breaks
our G1 determinism oracle — you must swap in the Rapier GDExtension; (2) **per-episode process
boot** is a latency cost that only becomes acceptable if you batch all G3 episodes inside a
single `--script` invocation; (3) **there is no turn-key sandbox for untrusted generated
GDScript** — the celebrated `godot-sandbox` sandboxes compiled C++/Rust, not GDScript.
The MCP servers everyone links to are **editor-authoring assistants, not verification
substrates** — largely irrelevant to our loop except as a "how Claude edits a Godot project"
reference. Verdict at the bottom: **viable and higher-fidelity than pymunk, but a 2–4 week
engineering tax and a physics-engine swap to reach determinism parity. Keep pymunk as the
default; Godot earns its place at the "real engines" rung (OBJECTIVES step 4), not before.**

---

## 1. Existing integrations (godot-mcp servers, editor plugins)

**Headline finding: none of these are built for what we need.** Every mature godot-mcp is an
*editor co-pilot* — it lets an LLM create/edit scenes and scripts inside the running Godot
**editor** and read back console output. None exposes a headless, deterministic,
state-emitting *verification* loop. They matter to us only for the **build phase** (how Claude
could author a Godot project) and as evidence that "Claude drives Godot" is a well-trodden path.

Metadata pulled live from the GitHub API, 2026-07-13:

| Repo | Stars | License | Lang | Last push | What it actually exposes | For us |
|---|---|---|---|---|---|---|
| **Coding-Solo/godot-mcp** | 4688 | MIT | JS | 2026-04-16 | `launch_editor`, `run_project` (debug mode), `get_debug_output`, `stop_project`, `get_godot_version`, `list_projects`, `get_project_info`, `create_scene`, `add_node`, `load_sprite`, `save_scene`, `export_mesh_library`, `get_uid`. Hybrid: simple ops via Godot CLI, complex ops via a **bundled `godot_operations.gd`** fed JSON params. | The canonical/most-starred. **Cannot read runtime node state**; captures stdout only. Build-phase authoring + run + read-console. |
| **ee0pdt/Godot-MCP** | 593 | MIT | GDScript | **2025-03-19** | Editor **addon** (`addons/godot_mcp`) + Node MCP server over **stdio**. Commands: `get-scene-tree`, `get-node-properties`, `create-node`, `modify-node`, `delete-node`, `read/modify/create-script`, `create/read/save-scene`, `get-project-settings`, `run-project`, `stop-project`, `get-editor-state`. | Richer scene introspection **at edit time** (reads the *editor* tree, not a headless sim). **Stale — no push in ~16 months.** |
| **youichi-uda/godot-mcp-pro** | 490 | proprietary ($15) | GDScript | 2026-06-24 | 162 tools incl. **physics, input simulation, runtime analysis, testing**. | The only one advertising *runtime analysis + input simulation* — closest in spirit — but **paid/proprietary**, so unusable as an OSS substrate. |
| **tomyud1/godot-mcp** | 389 | MIT | GDScript | 2026-04-21 | MCP server + Godot plugin, AI-assisted dev. | Editor-authoring. |
| **tugcantopaloglu/godot-mcp** | 334 | MIT | JS | **2026-07-13** | "157 tools for full Godot 4.x control (GDScript + C#/.NET), tested on Godot 4.7." | Most *actively* maintained (pushed today); broad editor control; still authoring-oriented. |
| **IvanMurzak/Godot-MCP** | 171 | Apache-2.0 | C# | 2026-07-13 | C# editor tools, cloud link to ai-game.dev. | Active; C#/.NET flavour. |
| **3ddelano/gdai-mcp-plugin-godot** | 94 | **none** | GDScript | 2026-03-30 | Create scenes/resources/scripts, read errors. | **No license = do not depend on it.** |
| **bradypp/godot-mcp** | 86 | MIT | TS | 2025-05-31 | General MCP interaction. | Stale. |
| **mkdevkit/godot-mcp** | 5 | MIT | GDScript | 2026-06-09 | Editor control. | New/tiny. |
| **alexmeckes/godot-mcp** | 23 | none | TS | 2026-03-24 | Fork-scale. | Ignore. |

**Verdict on MCP servers:** use one (Coding-Solo, MIT, most stars; or tugcantopaloglu for
Godot 4.7 freshness) *only* if we want Claude to author a Godot project interactively in the
editor. **They are the wrong tool for verification** — an MCP round-trip through a live editor
is the opposite of a fast headless subprocess. Our verification substrate must be a **direct
`godot --headless --script` runner we write ourselves** (Section 3), not an MCP server.

The far more relevant prior art is not an MCP server at all:

- **edbeeching/godot_rl_agents** (⭐1530, MIT, Python, pushed 2026-07-10) + its
  **godot_rl_agents_plugin** (⭐167, MIT, GDScript). This is the *actual* analog of our
  loop: a Python↔Godot RL bridge. A **`Sync` node** talks to Python over **TCP**; an
  `AIController` exposes `get_obs()` / `set_action()` / `get_reward()` / `reset()`; a
  **"Speed Up" property (up to 8×)** runs multiple physics steps per rendered frame; headless
  training works by dropping the visualization flag; ONNX export lets a trained policy run
  *inside* the engine. It keeps **one persistent Godot process and resets in-process** — it
  does **not** relaunch per episode. This is the pattern to copy for the *agent-in-the-loop*
  future (RL/eval), and its architecture directly informs Section 3.
  Sources: [README](https://github.com/edbeeching/godot_rl_agents),
  [CUSTOM_ENV.md](https://github.com/edbeeching/godot_rl_agents/blob/main/docs/CUSTOM_ENV.md).
- **lupoglaz/GodotAIGym** — older OpenAI-Gym-style bridge using **shared memory + semaphores**
  and the `Engine.time_scale` / `iterations_per_second` trick to run faster than realtime.
  Useful reference for the speedup mechanics and its explicit warning that time-scaling must be
  unit-tested against physics fidelity.
  [Speedup tutorial](https://lupoglaz.github.io/GodotAIGym/tutorial_speedup.html).

---

## 2. Feedback-loop anatomy, our way

Our loop has four organs: **(a) build the world, (b) run deterministically & fast, (c) read
state programmatically, (d) inject actions per tick.** Godot supports all four; the sharp edge
is (b)-determinism.

### (a) Build scenes programmatically — three viable routes, all real

1. **Runtime node creation (recommended).** `RigidBody2D.new()`, set shape/mass/position,
   `add_child()`. Fully programmatic, no `.tscn` files. This is what a generated `build()`
   maps onto.
2. **`PhysicsServer2D` server-direct (pymunk-closest).** Confirmed by the class docs:
   *"Physics objects in PhysicsServer2D may be created and manipulated independently; they do
   not have to be tied to nodes in the scene tree."* Methods: `space_create()`, `body_create()`,
   `rectangle_shape_create()` / `circle_shape_create()` / `convex_polygon_shape_create()`,
   `body_set_state()` / `body_get_state()`. This is the RID-level API and is the tightest
   analog to `pymunk.Space` + bodies/shapes.
   [PhysicsServer2D docs](https://docs.godotengine.org/en/stable/classes/class_physicsserver2d.html).
3. **`.tscn` text synthesis.** `.tscn` is a documented text format; you can emit it as text or
   build a `PackedScene` and `instantiate()`. Fine for authoring, unnecessary for a per-episode
   runner (route 1 or 2 is faster and needs no disk round-trip).

> **Caveat on server-direct:** `PhysicsServer2D` exposes creation/state/impulse but **no
> `space_step()`** — stepping is driven by the engine main loop, not user code (verified: the
> class lists no `space_step`/`space_flush_queries`). So even the RID-level route still advances
> via `_physics_process` + `--fixed-fps` (Section (b)); you don't get pymunk's "call
> `space.step(dt)` yourself" control. In practice you attach bodies to the SceneTree's default
> `World2D` space and let the tree step it.

### (b) Run deterministically and fast — the crux

**Headless: first-class. 2D physics DOES run without rendering.** The CLI docs define
`--headless` as *"Enable headless mode (`--display-driver headless --audio-driver Dummy`).
Useful for servers and with `--script`."* Physics lives in `PhysicsServer2D`, a **separate
server from rendering**; dedicated servers, CI test suites (GUT), and godot_rl_agents all run
physics headless routinely. `-s/--script <script>` runs a script that **inherits `SceneTree`
or `MainLoop`** — no window, no project boot into a game.
[Command-line tutorial](https://docs.godotengine.org/en/stable/tutorials/editor/command_line_tutorial.html).

**Fixed timestep / faster-than-realtime.** Two mechanisms:
- `--fixed-fps <fps>`: *"Force a fixed number of frames per second. This setting disables
  real-time synchronization."* Combined with `--headless` (no vsync, no render), the main loop
  runs **flat-out at a constant dt** — this is the clean faster-than-realtime path and keeps dt
  constant (so it doesn't perturb the sim the way time-scaling can).
- `Engine.physics_ticks_per_second` (default 60) sets the fixed physics dt;
  `Engine.time_scale` + `Engine.max_physics_steps_per_frame` can also accelerate, but
  time-scaling changes how many steps run per frame and **must be validated against physics
  fidelity** (GodotAIGym explicitly wrote unit tests for exactly this). Prefer `--fixed-fps`.
- `--quit-after <N>` cleanly terminates after N iterations (mind
  [issue #77508](https://github.com/godotengine/godot/issues/77508): `--quit`/`--quit-after 1`
  can skip resource import in headless — a known footgun for one-shot headless runs; call
  `quit()` from script after your budget instead, or warm the import cache first).

**Determinism — the red flag.** Our G1 oracle requires *two identical seeded runs → identical
final snapshot*. Stock Godot physics cannot guarantee this:
- **Built-in 2D physics is officially non-deterministic**, even run-to-run *on the same machine
  and same instance*. [Issue #112976](https://github.com/godotengine/godot/issues/112976)
  (open, milestone 4.7): with ≥3 simultaneously-colliding moving bodies and a scene reload not
  synced to physics ticks, *"the printed position of the player character is different every
  time, despite everything being deterministic."* Confirmed reproducible; disappears only when
  reloads are physics-synced or bodies are fewer. Our episodes routinely have several dynamic
  bodies, so **we cannot rely on stock physics for G1.**
- **Godot Rapier Physics** (`appsinacup/godot-rapier-physics`, ⭐948, **MIT**, Rust, pushed
  2026-07-12 — very active) is the fix. It's a **drop-in physics server** (Advanced Settings →
  Physics → 2D → `Rapier2D`), *"1-to-1 compatible with Godot Physics"* (RigidBodies, Areas,
  Shapes, Joints, Character Controller), with **run-to-run determinism in all variants (even
  parallel)** and a **cross-platform-deterministic build** (fixed-point-ish, IEEE-754,
  "slower version") plus **binary/JSON state serialization** (serde) for save/restore.
  Its determinism doc: *"if the exact same initial conditions are met, the Physics State will
  be exactly the same."* Caveat: node-cached state can lag physics state; input/animation/script
  logic can still inject non-determinism if uncontrolled.
  [Determinism doc](https://godot.rapier.rs/docs/documentation/determinism/),
  [repo](https://github.com/appsinacup/godot-rapier-physics).
- **SG Physics 2D** (Snopek Games, MIT) — fully deterministic **fixed-point** (64-bit int, 16
  fractional bits), built for rollback netcode. But it's a **separate node API** (you rewrite
  against it, not a drop-in) and **Godot 3.x-era / low maintenance**. Rapier is the better
  Godot-4 choice.
  [SG Physics 2D intro](https://www.snopekgames.com/tutorial/2021/getting-started-sg-physics-2d-and-deterministic-physics-godot/).

**Bottom line on (b):** headless + `--fixed-fps` gives fast, fixed-dt simulation; **determinism
requires adopting Rapier** (and disabling any `_process`-driven, non-physics-synced state
mutation). That is a real dependency, but a permissively-licensed, actively-maintained one.

### (c) Read state programmatically — strong

- **Server-direct:** `PhysicsServer2D.body_get_state(body, state)` returns transform / linear
  velocity / angular velocity by RID — the pymunk-equivalent read.
- **Node-level:** `node.global_position`, `RigidBody2D.linear_velocity` /
  `angular_velocity`. Contacts: set `contact_monitor = true` + `max_contacts_reported`, then
  `get_contact_count()` / `get_colliding_bodies()`; or `Area2D` `body_entered`/`body_exited`
  **signals** for sensor-zone semantics (our `on_contact`). Godot RL reads exactly this kind of
  data ("ball's position and velocity in the paddle's frame").
- **Stream it out:** the runner `print()`s **one JSON line per tick (JSONL) to stdout**; the
  Python harness reads the subprocess pipe. No TCP needed for the batch-verification case. (TCP,
  à la godot_rl's `Sync` node, is only needed when a *live external policy* — e.g. Claude — must
  act each tick.) One documented gotcha: Godot's node-cached view of physics state can be one
  frame stale vs the server state (Rapier docs warn about this) — read from the server or read
  in `_physics_process` after the step, not from `_process`.

### (d) Inject actions per tick — clean, and deterministic if you use physics APIs

- **Preferred (deterministic, matches `world.impulse`/`world.force`):** apply directly —
  `RigidBody2D.apply_central_impulse()` / `apply_impulse()` / `apply_central_force()`, or
  server-direct `PhysicsServer2D.body_apply_impulse()` /
  `body_apply_central_impulse()` / `body_add_constant_force()`. This is a pure physics mutation,
  no input subsystem, and maps 1:1 onto our `act()`.
- **Alternative (`Input.action_press`/`action_release`):** emulates human input; godot_rl uses a
  `heuristic == "human"` switch to toggle between keyboard and policy action. Unnecessary and
  slightly less direct for our purposes — **use direct impulse/force application.**

---

## 3. Harness-fit sketch — does the whole pattern port?

**Yes, structurally the port is clean.** Our runner is
`act(world, a) → K×[world.step(1); on_step(world)] → latch checkpoints → failure? → success?`.
In Godot this becomes a **custom `SceneTree` (or `MainLoop`) script** whose `_physics_process`
is the physics step and where a tick counter gates the decision cadence:

```
# runner.gd  (extends SceneTree)   — sketch, not tested
# launched:  godot --headless --fixed-fps 60 -s res://runner.gd -- \
#              --game=res://games/g0007.gd --seed=42 --episodes=40 --horizon=120
func _initialize():        # build the world for episode 0 (game.build)
func _physics_process(dt): # one physics step
    tick += 1
    if tick % K == 0:      # decision boundary
        game.act(world, next_action())      # seeded RNG or witness prefix
    game.on_step(world)                     # per-step rules
    latch_checkpoints(game.checkpoints(world))
    if game.failure(world) or game.success(world) or tick >= budget:
        emit_jsonl(episode_result); advance_or_finish()
```

**Two execution shapes, and the choice decides the speed:**

- **Shape A — one process, all episodes (recommended for G0–G3 verification).** A single
  `godot --headless --script probe.gd` runs the **entire E=40 × H=120 solvability probe
  internally**: the "policy" is just our seeded macro-action sampler (Section CONTRACTS §4),
  which is pure GDScript — **no Python round-trip per tick at all.** The runner loops episodes,
  resets the world **physics-synced** (per issue #112976, never reset from `_process`) or
  re-`_initialize`s, and prints one JSONL record per episode; the witness is the first success.
  **Boot cost is paid once and amortized over 40 episodes.** This is the natural home for
  G0/G1/G2/G3 — they need *many fast seeded rollouts*, not a live agent.
- **Shape B — persistent process + TCP (for the agent-in-the-loop future).** Exactly the
  godot_rl_agents model: keep Godot alive, `Sync` node over TCP, `set_action`/`get_obs` each
  tick, in-process `reset()`. Use this only when an *external* policy (Claude, or a trained
  net) must choose actions live — i.e. the L3/L4 eval rungs, not base verification.

**Where `run_episode` sits:** it moves *into GDScript* (the runner loop above), because that's
where the physics lives. The Python side shrinks to: (i) write the game module + a manifest,
(ii) `subprocess.run(["godot","--headless",...])`, (iii) parse JSONL from stdout into the same
episode dict our oracles already consume. The **universal oracles (G0–G3) and witness replay
stay in Python** and operate on the returned JSONL — they never need to know physics ran in
Godot. So the *contract* (`run_episode → {result, ticks, checkpoints, witness}`) ports; only
its *implementation substrate* changes.

**Cycle-speed estimate (the honest part).**
- *pymunk today:* fully in-process. An episode ≈ 120 ticks × 6 steps = 720 steps; G3 ≈ 40
  episodes ≈ 29k steps. Pymunk does tens of thousands of simple steps/sec, so **G3 ≈ sub-second
  to a few seconds; whole-game verify ≈ ~1 s** (plus one Python subprocess spawn for the AST
  sandbox, ~100–300 ms).
- *Godot, Shape A:* per-game cost = **engine boot + 29k physics steps**. Headless physics for
  ≤14 bodies is cheap (flat-out likely 10k–100k+ steps/s), so the *sim* is ~0.3–3 s. **Boot is
  the unknown** — I could not find a cited steady-state number and Godot is not installed here to
  measure. Evidence: the scary "30+ s startup" reports are **first-run only** (Windows Defender /
  a specific AMD driver 25.10.2; *"can't reproduce on Linux"* —
  [issue #112425](https://github.com/godotengine/godot/issues/112425)); CI practice is a one-time
  `--headless --import --quit` warm-up then fast runs. Realistic steady-state minimal-headless
  boot is order **~0.3–2 s**. So **Shape A whole-game verify ≈ 1–5 s → ~0.2–1 game/s**, i.e.
  same order of magnitude as pymunk *if you batch*. **THE thing to measure empirically before
  committing.**
- *Godot, Shape B or "relaunch per episode":* 40 × boot ≈ **tens of seconds per game — 10–40×
  worse.** Never relaunch per episode for verification.

---

## 4. Sandboxing, Windows, licensing

**Sandboxing untrusted generated GDScript — the weakest link.** GDScript has **no built-in
sandbox**: generated code can call `OS.execute()`, `FileAccess`, `OS.get_environment()`, load
resources, etc. — full ambient authority. Our pymunk harness relies on `harness/sandbox.py`
(AST allowlist, only `math` importable, subprocess worker); Godot offers no equivalent for
GDScript out of the box.

- **`libriscv/godot-sandbox`** (⭐458, **BSD-3**, C++, active) is often cited but **does not
  solve this**: it sandboxes **compiled C++/Rust guests** (RISC-V ELF, memory-safe, opt-in
  API allowlisting, JIT/interpreter). *"GDScript is the host language, not a guest."* Its
  design doc: sandboxed code has *"access to the entire public Godot API"* unless you explicitly
  lock it down via Callables. A GDScript-to-RISC-V compiler is **WIP/experimental**, not
  production-ready. So it can't sandbox our LLM-authored GDScript today.
  [Design doc](https://libriscv.no/docs/host_langs/godot_integration/godot_docs/design/).
- **Practical isolation options for us:**
  1. **OS-level sandbox around the headless subprocess** (we already spawn one per game): on
     Windows a restricted **Job Object / AppContainer** (no network, temp-only filesystem,
     killed on timeout); on Linux a container/namespace + seccomp. This is more robust than any
     in-engine attempt and composes with Shape A. **Recommended.**
  2. **Our own GDScript static allowlist** (analog to `sandbox.py`): reject `OS`, `FileAccess`,
     `GDExtension`, `load`/`ResourceLoader`, `preload` of scripts, etc. Harder than Python —
     GDScript's AST isn't exposed to external tools the way Python's `ast` is (`godot
     --check-only --script x.gd` validates syntax but gives no capability report), so you'd
     hand-roll a lexer/regex pass. Brittle; use only as defense-in-depth on top of (1).
  3. **Sidestep it entirely — declarative game spec.** Have the LLM emit a **JSON/DSL game
     description** (bodies, joints, sensors, a fixed action vocabulary, win/lose predicates over
     an allowlisted query set) that a **single, audited, trusted GDScript interpreter** runs.
     This is closer to v1's `SceneSDK` than v2's free-code philosophy — less expressive, but
     **no untrusted code executes in-engine at all.** A clean middle path if free GDScript
     sandboxing proves too costly; worth weighing against the v2 "LLM writes whole game" goal.

**Windows workflow.** Godot ships as a **single portable `.exe`** (`Godot_v4.x-stable_win64.exe`),
no install; `--headless` works on Windows. Two frictions: (i) the **first-run Defender/SmartScreen
scan** adds one-time latency (add a Defender exclusion for the Godot binary + project dir on the
dev box / CI); (ii) path handling (`res://` vs `user://`, and forward/back slashes). Otherwise the
`subprocess.run` + JSONL-over-stdout pattern is OS-agnostic.

**Licensing — all green.** Godot engine: **MIT**. Rapier plugin: **MIT**. godot_rl_agents &
plugin: **MIT**. Coding-Solo/godot-mcp: **MIT**. godot-sandbox: **BSD-3**. The only non-free
pieces are youichi-uda/godot-mcp-pro (proprietary, $15) and the license-less repos
(3ddelano, alexmeckes) — none of which we'd depend on. **No licensing obstacle.**

---

## 5. Candidate framework: architecture, effort, critical verdict

### Architecture sketch (Shape A, verification-first)

```
                    Python harness (our existing oracles, unchanged in spirit)
  gamegen ─► game.gd (LLM-authored GDScript OR declarative spec)
                    │
                    ├─ manifest + integrity check (base runner.gd is FROZEN & audited)
                    ▼
  gameverify.run_episode(...)  ──►  subprocess:
      godot --headless --fixed-fps 60 -s res://runner.gd --
            --game=game.gd --seed=S --episodes=40 --horizon=120 --physics=rapier2d
                    │                                   (inside a Job Object / container)
                    ▼
      runner.gd (TRUSTED): builds world via PhysicsServer2D/RigidBody2D,
                           loops E×H, seeded macro-actions, latches checkpoints,
                           prints JSONL per episode, quit() at budget
                    │  stdout (JSONL)
                    ▼
  Python parses JSONL ─► same episode dict ─► G0/G1/G2/G3 oracles + witness ─► replay GIF
```

- **`run_episode`** conceptually lives in `runner.gd`; the Python `run_episode` becomes a thin
  subprocess-launch + JSONL-parse shim returning the identical dict our oracles consume.
- **Determinism** provided by `--physics=rapier2d` (Advanced Settings override) + `--fixed-fps`
  + physics-synced resets + direct impulse actions (no `Input`, no `_process` state mutation).
- **G1's determinism check** (two seeded runs identical) becomes a real, passable test *with
  Rapier* — and Rapier's serde state serialization even lets us snapshot/restore for the future
  L3 state-injection rung.

### Effort estimate (from a working pymunk harness)

| Piece | Effort |
|---|---|
| `runner.gd` (SceneTree loop, JSONL emit, seeded macro-actions, checkpoint latch) | ~3–5 days |
| Godot substrate `World`-equivalent (build/query/impulse mapping onto PhysicsServer2D) | ~3–5 days |
| Rapier integration + determinism validation (reproduce G1 green N times) | ~2–4 days |
| Python subprocess shim + JSONL schema + integrity/manifest for the frozen runner | ~2–3 days |
| Sandboxing (Job Object/container harness + GDScript allowlist *or* declarative-spec interpreter) | ~4–8 days |
| Empirical speed/latency benchmarking + tuning batch size | ~2 days |
| **Total** | **~3–5 weeks for one engineer** to reach pymunk-parity fidelity |

### Critical verdict — where the loop breaks down vs pymunk

Ranked by how much they hurt:

1. **Determinism (biggest).** pymunk is deterministic in-process for free; our G1 oracle just
   works. Godot's stock 2D physics is **officially non-deterministic run-to-run**
   (issue #112976), so G1 would flake with false-negatives. **Mitigation exists and is good
   (Rapier), but it's a hard dependency and its cross-platform-deterministic variant is slower.**
   This alone means "just point the harness at Godot" is false — you must swap the physics engine.
2. **Startup latency / IPC (medium).** pymunk pays ~0 per episode (in-process) + one Python
   spawn per game. Godot pays **engine boot per `--script` invocation**. Batching all G3
   episodes into one process (Shape A) makes it *acceptable* (~1–5 s/game, same order as pymunk)
   but never *free*, and any design that relaunches per episode is 10–40× worse. JSONL-over-pipe
   IPC is cheap; the cost is boot, not marshalling.
3. **Sandboxing (medium, and philosophically awkward).** pymunk + our AST sandbox already
   isolate untrusted Python cleanly. Godot has **no GDScript sandbox**; we inherit OS-level
   isolation work *or* must retreat from "LLM writes free GDScript" toward a declarative spec —
   which partially undoes v2's whole premise. This is the least-solved gap.
4. **Determinism-adjacent footguns (small but real).** node-cached state lagging server state;
   `--quit`/`--quit-after 1` skipping imports; `_process` vs `_physics_process` timing; time-scale
   perturbing physics. All avoidable with discipline (read server state in `_physics_process`,
   warm the import cache, use `--fixed-fps` not `time_scale`), but each is a place a naive port
   silently goes wrong.

**Where the loop is actually *better* than pymunk:** richer, first-class state (contacts,
areas/signals, joints, character controllers) without us hand-rolling instrumentation; a real
renderer for demo GIFs (we currently draw with PIL); an ecosystem (godot_rl_agents) that already
solved the agent-in-the-loop TCP story for the L3/L4 rungs; Rapier's state serialization as a
gift for state-injection; and — the strategic point — it satisfies the assignment's literal
"produce playable environments **in a game or physics engine**" at a fidelity pymunk can't
(rendering, tooling, human-playability of the same artifact).

---

## Loop scorecard (1–5, one-line justification)

| Dimension | Score | Justification |
|---|---|---|
| **State-read** | **5** | `PhysicsServer2D.body_get_state` (RID-level, pymunk-equivalent) + node props + contact monitor + `Area2D` signals; everything read from engine STATE, zero pixels. |
| **Action-inject** | **5** | `body_apply_impulse`/`apply_central_force`/`body_set_state` map 1:1 onto `world.impulse`/`force`/`set_velocity`; pure physics mutation, deterministic, no input subsystem needed. |
| **Determinism** | **2 stock → 4 with Rapier** | Built-in 2D physics is officially non-deterministic run-to-run (issue #112976, open); Rapier GDExtension (MIT, active) delivers run-to-run + optional cross-platform determinism + state serialization — but it's a third-party swap and the cross-platform build is slower. |
| **Speed** | **3** | Headless physics is fast for ≤14 bodies, but per-invocation engine boot (~0.3–2 s, unmeasured here) is a real tax; acceptable ONLY by batching all G3 episodes in one `--script` run (Shape A). Order-of-magnitude parity with pymunk, not better. |
| **Headless** | **5** | First-class: `--headless` = dummy display+audio, *"useful with --script"*; 2D physics runs without rendering; CI/dedicated-server/RL-proven. |
| **Maturity** | **4** | Godot MIT + huge; CLI/headless & `PhysicsServer2D` mature; active loop ecosystem (godot_rl_agents ⭐1530, Rapier ⭐948, both pushed within days). Minus one: no off-the-shelf *verification* harness — we build the runner + sandbox ourselves; MCP servers are authoring tools, not substrates. |

**Composite feedback-loop quality: strong on the four "does the signal flow" axes (state,
action, headless, maturity), gated by determinism (needs Rapier) and dinged on speed (needs
batching).**

---

## Recommendation

**Keep pymunk as the default verification substrate. Adopt Godot deliberately at the
"real 2D game engines" rung (OBJECTIVES step 4), not before — and when we do, adopt it *with
Rapier from day one*.**

Rationale: the feedback loop Godot offers is genuinely high-quality — arguably higher-fidelity
than pymunk on state richness and demo rendering, and it's the honest fulfillment of the
assignment's "game or physics engine" wording. But it does **not** give us anything the current
loop lacks *for the base-of-games campaign*, while it **does** impose three costs pymunk doesn't
have: a mandatory physics-engine swap for determinism, per-process boot latency (survivable only
via batching), and an unsolved untrusted-GDScript sandboxing problem. Spending 3–5 weeks now
would slow the base-of-games campaign (OBJECTIVES step 1) for fidelity we don't yet need.

**Concrete next steps when the rung comes up (cheap, decision-gating):**
1. **Spike (½ day):** install Godot, write a 50-line `runner.gd`, run 40 headless episodes of a
   3-body scene in one `--script` invocation, and **measure**: (a) steady-state boot time, (b)
   episodes/sec, (c) — decisively — whether two identical-seed runs produce identical final
   snapshots **with stock physics vs with Rapier2D**. That single experiment resolves the two
   biggest unknowns (speed + determinism) that I could not settle from docs alone.
2. If determinism holds under Rapier and batched boot is ≤~2 s: prototype the Python subprocess
   shim + JSONL schema and port one certified pymunk game to prove the oracle stack is
   substrate-agnostic.
3. Decide sandboxing posture up front: **declarative-spec interpreter** (safe, less expressive)
   vs **free GDScript + OS-level Job Object/container isolation** (expressive, more work). Do not
   ship LLM-authored free GDScript without one of these.

---

### Sources (primary, verified 2026-07-13)

- Godot CLI/headless flags — https://docs.godotengine.org/en/stable/tutorials/editor/command_line_tutorial.html
- PhysicsServer2D class (server-direct, `body_get_state`/`body_apply_impulse`, no `space_step`) — https://docs.godotengine.org/en/stable/classes/class_physicsserver2d.html
- Built-in 2D physics non-determinism (open, milestone 4.7) — https://github.com/godotengine/godot/issues/112976
- Godot Rapier Physics determinism — https://godot.rapier.rs/docs/documentation/determinism/ ; repo https://github.com/appsinacup/godot-rapier-physics
- SG Physics 2D (fixed-point, MIT, Godot 3.x) — https://www.snopekgames.com/tutorial/2021/getting-started-sg-physics-2d-and-deterministic-physics-godot/
- godot_rl_agents (Sync/TCP, speed-up, headless, ONNX) — https://github.com/edbeeching/godot_rl_agents ; CUSTOM_ENV https://github.com/edbeeching/godot_rl_agents/blob/main/docs/CUSTOM_ENV.md
- GodotAIGym speedup (time_scale/shared-mem, physics-fidelity warning) — https://lupoglaz.github.io/GodotAIGym/tutorial_speedup.html
- godot-sandbox (compiled guests only, GDScript is host) — https://libriscv.no/docs/host_langs/godot_integration/godot_docs/design/
- Coding-Solo/godot-mcp (bundled `godot_operations.gd`, MIT) — https://github.com/Coding-Solo/godot-mcp
- ee0pdt/Godot-MCP (editor addon + stdio, MIT) — https://github.com/ee0pdt/Godot-MCP
- Headless `--quit`/import footgun — https://github.com/godotengine/godot/issues/77508 ; first-run 30 s (Windows/AMD, Linux OK) — https://github.com/godotengine/godot/issues/112425
- Repo maturity metadata (stars/license/last-push) — GitHub REST API, fetched 2026-07-13.
