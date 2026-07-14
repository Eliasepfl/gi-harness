# Godot migration — feasibility + integration architecture (rung-4 "real engine" lane)

> Author: research agent, 2026-07-14. Supersedes/refreshes `notes/engines/godot.md`
> (2026-07-13) with 2025-26 reality. Question: move the rung-4 "real engine" lane to
> Godot and start semi-open 2D worlds, using engine feedback (errors, state) to correct
> generated games. Key structural fact this note exploits: **our harness ALREADY has the
> executor seam** (`harness/verify/executors.py`). A `GodotExecutor` is simply the THIRD
> implementation of the same `run_batch` / `run_check` surface — `nodeworld/` proved the
> pattern once already; Godot repeats it. Every repo/stat/issue below was fetched live.

## VERDICT: GO-with-spike (today = spike + scaffolding, not full migration)

**Do not "point the harness at Godot" today — nothing about that is one-shot.** Godot is
not installed here; determinism (stock vs Rapier) and steady-state boot cost are unmeasured
and *undecidable from docs*; and the sandbox posture forces one architectural decision before
a line of runner.gd is worth writing. But the port is *structurally clean and genuinely
low-risk* because the seam already exists. So **today** splits cleanly:

- **AM — ½-day spike** (§8) behind hard pass/fail gates: install Godot, 60-line `runner.gd`,
  40 headless episodes of a 3-body scene in ONE process, measure boot + episodes/s + (decisive)
  **byte-identical final snapshot on two same-seed runs, stock Godot Physics 2D vs Rapier2D**.
- **PM — if gates pass: scaffold the port** — `GodotExecutor` shim + `runner.gd` skeleton +
  the declarative game-spec schema, exactly mirroring how `nodeworld/` graduated (spike →
  `JsExecutor` → `_verify_js` funnel → parity tests).

If a gate fails, we still finish today with a measured, decision-grade answer instead of a
half-built lane. **NO-GO only if** the spike shows *neither* stock nor Rapier gives byte-stable
same-seed snapshots (breaks G1/G3), which the evidence below makes unlikely.

---

## 0. What changed since the 2026-07-13 note (the refresh)

| Prior-note claim (2026-07-13) | 2026-07-14 update | Impact |
|---|---|---|
| Built-in 2D physics "officially non-deterministic", must swap to Rapier | **Issue #112976 is now CLOSED, milestone 4.7.** Root cause is narrow: 3+ colliding bodies **+ a scene reload timed from `_process`, not `_physics_process`**. Reporter: *"reload in `_physics_process` … the character ends up in the exact same position each time."* Reproduced on 4.5.1 & 4.3. | Stock physics **may** pass G1 **if our runner never resets from `_process`** and builds fresh deterministic worlds — a discipline we fully control. Rapier stays the safe default; the spike MEASURES stock. |
| Rapier "⭐948, active" | `appsinacup/godot-rapier-physics` **949★, MIT, v0.8.39 (2026-07-07)**, supports **api-4-4 … api-4-7**, drop-in `PhysicsServer2D` replacement, **two variants**: fast SIMD/parallel (locally deterministic) and slower **cross-platform deterministic** (IEEE-754), plus **serde state serialization**. | Determinism answer is production-grade and current. Cross-platform build is our witness-replay insurance. |
| MCP servers are all editor-authoring, none does a runtime loop | **NEW: `Erodenn/godot-mcp-runtime`** (47★, MIT, TS, v3.2.1 2026-07-11) injects a **transient `McpBridge` autoload over localhost TCP** for **input-simulation + screenshots + live GDScript against the running tree**, headless/background-capable. Still **not** typed per-tick state — but the first real *runtime* bridge. | Confirms the structural finding holds. Its "live GDScript in the running game → structured result" is directly reusable as a repair-loop probe primitive. |
| godot-box2d as a determinism option | `appsinacup/godot-box2d`: "Box2D is binary deterministic, **Godot Box2D should be too but no tests run yet**, **missing cross-platform determinism**", **maintenance-only, waiting on box2c**. | Rapier > Box2D for us today; Box2D is a watch-item, not a choice. |
| — | **`htdt/godogen`** (4713★, MIT, Python, pushed 2026-07-13) — autonomous Godot/Bevy/Babylon gen with Claude Code. Godot path = **C#/.NET with build-time SCENE generation** (data, not runtime free code), judged by **live game / recorded video, not logs or state**. | Reinforces our sandbox recommendation (build-time scene data) AND our differentiator (they prove via pixels; we prove via typed state + witness replay). |

Current stable is **Godot 4.6** (early 2026); 2D still defaults to Godot Physics 2D; **4.5
merged TileMapLayer cell shapes into larger collision bodies** (big win for large worlds).

---

## 1. Feasibility, leg by leg (the four loop organs)

Our loop = **build · step deterministically & fast · read state · inject actions**. Godot
scores 5/5/5 on build/read/inject; the whole risk is concentrated in *step-determinism* and
*boot cost*, both spike-measurable.

- **Build** — `RigidBody2D.new()` + `add_child()` (runtime nodes) or `PhysicsServer2D`
  RID-level (`body_create`/`rectangle_shape_create`/`body_set_state`), the pymunk-closest
  route. `.tscn` text/`PackedScene` is the third route, best for a certified parts bank.
- **Step** — headless is first-class (`--headless` = dummy display+audio, *"useful with
  --script"*); `--fixed-fps N` *"disables real-time synchronization"* → the main loop runs
  flat-out at constant dt. **No user-callable `space_step`**: stepping is driven by the main
  loop, so the runner is a `MainLoop`/`SceneTree` script whose `_physics_process` is one step
  and a tick counter gates the K=6 decision cadence. Determinism → §2.4.
- **Read** — `PhysicsServer2D.body_get_state` (RID) or node props; contacts via
  `contact_monitor=true`+`get_colliding_bodies()`; `Area2D` `body_entered/exited` signals =
  our `on_contact`/sensors. All state, zero pixels. Emit **one JSON line per episode to
  stdout** (JSONL) — the Python side reads the pipe, no TCP for batch verification.
- **Inject** — `apply_central_impulse`/`apply_central_force`/`body_set_state` map 1:1 onto
  `world.impulse`/`force`/`set_velocity`. Pure physics mutation, no `Input` subsystem
  (deterministic). Matches `nodeworld/world.js` exactly.

---

## 2. Integration architecture — `GodotExecutor`, the third seam

### 2.1 The seam is already the contract

`executors.py` defines one surface; `PyExecutor` (in-proc pymunk) and `JsExecutor` (Node
subprocess) implement it; the funnel routes by `detect_engine()`. **A `GodotExecutor` is a
copy of `JsExecutor` with `godot` swapped for `node`:**

```
GodotExecutor(batched=True):
  run_batch(game_source, episodes, max_ticks, frames_every=0, escape_margin=None)
      -> list[episode_dict]   # keys: result, ticks, checkpoints, final_snapshot,
                              #       actions, world_size (+ frames / nan / oob)
  run_check(game_source) -> dict   # raw G0/G2 facts (scan/load/symbols/actions/
                                   # world_size/build/entities/queries/penetration/g2)
  # raises VerifyError(kind,msg) on infra failure -> .as_report() = VERIFY_ERROR shape
```

Then add one funnel: `detect_engine(...) == "gd"` → `_verify_godot(source, report)`, a
line-for-line twin of `_verify_js` (§ `gameverify.py:1152`): ONE `check` job feeds G0+G2;
G1/G3 batch through `run_batch`. **Oracles, hints, witness, report schema stay byte-identical
across all three engines** — they never learn physics ran in GDScript. This is the whole point
of the seam and why the port is low-risk.

### 2.2 Process protocol (mirror `nodeworld/runner.js` exactly)

`runner.gd` is the GDScript twin of `runner.js`: read ONE JSON job, run it, emit JSONL, `quit()`.

```
godot --headless --fixed-fps 60 -s res://runner.gd -- \
      --job=<tempfile.json> --physics=rapier2d
```

- **Job in via TEMP FILE, not stdin** (the one honest deviation from `JsExecutor`): Godot's
  headless stdin (`OS.read_string_from_stdin`) is line-buffered and historically flaky; a
  temp-file job path passed after `--` is robust cross-platform. Python writes the job, spawns
  the process, reads stdout. (Spike also tries stdin; temp-file is the fallback that always works.)
- **Two modes** identical to `runner.js`: `"episodes"` (JSONL, one record/episode, in order)
  and `"check"` (one object of raw facts). Decision-tick semantics **identical**: `act` then
  `6 × [step; on_step]`, latch checkpoints BEFORE terminal checks, then `failure` then `success`.
- **Batch the whole layer in ONE process** (`batched=True`) — the boot tax is paid once per
  layer, never per episode. Non-negotiable (per-episode relaunch = 10-40× slower, §5).

### 2.3 Scene-generation format + sandbox decision (the load-bearing call)

**GDScript has NO sandbox and this is worse than the prior note implied.** GodLoader malware
(17,000+ machines since 2024) is *literally* untrusted GDScript with `OS.execute`/`FileAccess`
ambient authority; `register_singleton()` **won't** strip singletons from the GDScript env
(issue #77611: "Godot is actively fighting against making more secure environments without
C++"). `libriscv/godot-sandbox` sandboxes compiled C++/Rust guests, **not GDScript**. There is
no `ast`-grade tool (`--check-only` gives syntax, not a capability report).

**Decision: ship the Godot lane on a DECLARATIVE GAME SPEC + a single FROZEN, audited
`runner.gd` interpreter — no untrusted code executes in-engine.** Rationale:

- **Safe by construction** — the LLM emits DATA (bodies/joints/sensors/actions/predicates),
  never callable GDScript. Nothing to sandbox in-engine.
- **Maps 1:1 onto the parts bank** (`CONTRACTS §9`): each bank noun → a **certified `.tscn`
  template** with proven physics; `world.part(kind, pos, **overrides)` → instantiate template.
  This is exactly the parts-bank angle Elias asked about, and exactly what `godogen`'s
  "build-time scene generation" does in practice.
- **Predicates stay pure & bounded** — `success`/`failure`/`checkpoints` are expressions over
  an allowlisted query DSL (`pos`, `vel`, `grounded`, `contacts`, `in_bounds`, `flag`, `steps`,
  arithmetic/boolean), evaluated by Godot's **`Expression` class** (parse/execute with an
  explicit variable set — it cannot reach `OS`/`FileAccess` unless you hand it those). Covers
  the vast majority of win/lose logic; anything it can't express is a prompt constraint, not a
  security hole.
- **Cost: expressiveness.** This is nearer v1's `SceneSDK` than v2's "LLM writes whole game".
  Accept it for the Godot lane specifically — Py/JS lanes keep full free-code expressiveness;
  Godot buys us "real engine + rendering + semi-open worlds" and pays with a constrained DSL.

**Stretch goal (later, gated on a real Windows Job Object / AppContainer harness):** free
GDScript games + OS-level process isolation (temp-only FS, no network, kill-on-timeout) + our
own token allowlist (reject `OS`/`FileAccess`/`load`/`preload`/`ResourceLoader`/`GDExtension`).
More expressive, materially more work, and the token scan is brittle without an AST. Not today.

### 2.4 Determinism choice (with evidence)

**Default: Rapier2D, "fast SIMD/parallel" variant** (locally deterministic — run-to-run
byte-stable on the same machine, which is exactly our G1/G3 requirement). Same-machine is all
witness replay needs. Keep the **cross-platform-deterministic** variant as CI insurance if
witnesses must replay across dev boxes. Rapier's **serde state serialization** is a free gift
for the future L3 state-injection rung.

**But test stock first.** #112976's closure narrows stock non-determinism to `_process`-timed
resets with 3+ colliding bodies — a footgun our runner structurally avoids (fresh deterministic
world per episode, all mutation in `_physics_process`). If the spike shows stock Godot Physics 2D
gives byte-identical same-seed snapshots under our reset discipline, we save a dependency. The
spike's gate (c) decides this empirically; **Rapier is the guaranteed fallback either way.**

### 2.5 World verb-API → Godot mapping (the `GodotWorld` class inside `runner.gd`)

| World verb (CONTRACTS §1) | Godot realization (Rapier/stock, same API) |
|---|---|
| `add(shape,pos,size/radius/…,static,sensor,mass,…)` | `RigidBody2D`/`StaticBody2D`/`Area2D`(sensor) + `CollisionShape2D`; density from mass/area |
| `pin` / `pivot` / `spring` | `PinJoint2D` / `PinJoint2D`@point / `DampedSpringJoint2D` |
| `impulse`/`force`/`set_velocity` | `apply_central_impulse`/`apply_central_force`/`linear_velocity=` |
| `on_contact(a,b,flag)` | `Area2D.body_entered` signal → set flag (once) |
| `query` (pos/vel/angle/bbox/shape/static/sensor/controlled) | node transform + `linear_velocity` + shape metadata; bbox from shape+xform |
| `contacts`/`touching`/`grounded` | `contact_monitor`+`get_colliding_bodies()`; grounded = contact normal ~vertical |
| `in_bounds`/`penetration_depth` | bbox vs world rect; manifold separation (Rapier contact API) |
| `step(n)` | n × main-loop `_physics_process` at fixed dt; NaN/`|v|>VMAX` sentinel → freeze |
| `snapshot`/`events`/`rng` | dict of {pos,vel,angle}; event list; seeded RNG (port mulberry32 for cross-engine parity) |

---

## 3. Tooling / skills inventory (verified live 2026-07-14)

**None of these is our substrate** — all are AUTHORING or visual-proof layers; we build the
headless state↔action runner ourselves. Value column = usefulness to *our* loop.

| Name | Link | Maintained? | What it automates | Useful to us? |
|---|---|---|---|---|
| **godot-rapier-physics** | github.com/appsinacup/godot-rapier-physics | ✅ 949★ MIT, v0.8.39 2026-07-07, 4.4-4.7 | Drop-in deterministic `PhysicsServer2D`; serde state serialize | **YES — core dependency** (determinism + state I/O) |
| **godot_rl_agents** | github.com/edbeeching/godot_rl_agents | ✅ 1532★ MIT, 2026-07-10 | Python↔Godot RL bridge: `Sync` TCP node, `get_obs`/`set_action`/`reset`, ≤8× speed-up, headless, ONNX | **Pattern to copy** for the L3/L4 *live-agent* rung (Shape B TCP); not for batch verify |
| **Erodenn/godot-mcp-runtime** | github.com/Erodenn/godot-mcp-runtime | ✅ 47★ MIT, v3.2.1 2026-07-11 | Transient `McpBridge` autoload/TCP: input-sim, screenshots, **live GDScript in running game**, headless bg | Partial — its "run probe script in live game → structured result" is reusable for a **repair-loop probe**; feedback is pixels/input, not typed state |
| **htdt/godogen** | github.com/htdt/godogen | ✅ 4713★ MIT, 2026-07-13 | Autonomous Godot/Bevy/Babylon gen w/ Claude Code; Godot = C#/.NET **build-time scene gen**; proof via live URL / recorded clip | Reference — validates our **declarative-scene** sandbox choice; proves via video (we prove via state) |
| **Coding-Solo/godot-mcp** | github.com/Coding-Solo/godot-mcp | ✅ ~4.7k★ MIT | Launch editor, run project (debug), **capture debug/console output**, scene/node authoring | Its **run→read-stdout/errors** is the pattern for our repair loop's error feedback |
| **hi-godot/godot-ai** | github.com/hi-godot/godot-ai | ✅ MIT, v2.9 | 150+ editor ops; feedback via **"smart screenshots"** | No — pixel feedback, opposite of our no-pixels design |
| **Randroids-Dojo/Godot-Claude-Skills** | github.com/Randroids-Dojo/Godot-Claude-Skills | ⚠️ 35★ MIT, 2026-01-19 | Claude Code skill: GdUnit4 tests, PlayGodot automation, exports, CI/CD | Minor — **GdUnit4 headless test invocation** is a reusable harness-CI idea |
| **"Godot Games" skill** | `claude skill add godot-games` (jonathansblog.co.uk writeup) | ✅ active | Generates complete `.tscn`+`.gd` projects from English; **one-way, no error loop** | No — authoring only, no feedback |
| **gamedev-skills/awesome-gamedev-agent-skills** | github.com/gamedev-skills/awesome-gamedev-agent-skills | ✅ 264★ Apache-2.0, 2026-07-13 | 66 version-pinned SKILL.md authoring playbooks + router | No feedback loop; a good **authoring prompt** source for the Godot generator |
| appsinacup/godot-box2d | github.com/appsinacup/godot-box2d | ⚠️ maintenance-only | Box2D `PhysicsServer2D`; determinism untested, no cross-platform | Watch-item, not a choice |

**Reusable error-feedback pattern (directly applicable to our repair loop):** the
Coding-Solo/runtime-MCP "run → capture stdout/compile errors → feed back to the LLM" loop is
precisely what our `gamegen` repair loop already does with the gameverify JSON report. For the
Godot lane, `runner.gd` must surface **parse/build/runtime errors as structured in-band JSON**
(same as `runner.js` reports per-episode `error`), so the repair loop needs zero new machinery.

---

## 4. Semi-open 2D worlds — what Godot buys us beyond Planck

This is the *only* thing that justifies the migration for this rung (Planck already matches us
on the four loop legs). Godot adds authorability of *big, navigable, watchable* worlds:

- **TileMapLayer** (4.4+, cell-shape merging in 4.5) → large tiled worlds with cheap merged
  collision; `WORLD_SIZE` (up to 2400×1600, `CONTRACTS §2`) becomes a tilemap extent instead of
  a hand-placed segment soup. Our checkpoint latching maps unchanged — milestones are still
  pure predicates over body state.
- **Camera2D** → `WORLD_SIZE`→camera is native: a `Camera2D` follows the controlled body with
  `limit_*` = world rect. Replaces `render.FollowCamera` (executors.py:342) with an engine
  primitive, and gives real demo video instead of PIL GIFs.
- **NavigationRegion2D + NavigationAgent2D** → baked navmesh from TileMapLayer (4.4 tutorials
  confirm) → moving hazards/patrols that *path*, not just bob on joints (recall the parts-bank
  v1 deviation §9.7 where moving platforms had to be jointed for lack of a per-step driver —
  Godot's nav/kinematic drivers remove that limitation).
- **Area2D triggers** = our sensors/`on_contact`, first-class with enter/exit signals.
- **Scene composition = the parts bank made physical**: each certified noun ships as a `.tscn`
  with certified physics; bank-CI (`CONTRACTS §9.5`) certifies the `.tscn` once, offline.
- **Particles/visual polish** → demo GIF/video quality for the GI site, free.

---

## 5. Throughput estimates (must be spike-confirmed)

- **JS lane baseline:** ~70 ms node cold start/batch; sim negligible.
- **Godot lane (Shape A, batched):** per-game cost = **engine boot + ~29k physics steps**
  (G3 = 40 ep × 120 ticks × 6). Headless physics for ≤14 bodies is cheap (~10k-100k+ steps/s →
  sim ~0.3-3 s). **Boot is the tax**: the scary 30 s+ reports are **first-run only**
  (Defender/AMD-driver specific, "can't repro on Linux", #112425); steady-state minimal-headless
  boot after a one-time `--headless --import --quit` warm-up is order **~0.5-2 s**.
  → **whole-game verify ≈ 1-5 s → ~10-40 games/min single-core**, ~order-of-magnitude parity
  with the JS lane *only because we batch*, and it scales linearly across cores.
- **Per-episode relaunch = 40 × boot ≈ tens of s/game (10-40× worse). Never do it.**
- **G4 fuzzing** (thousands of episodes): batch *hundreds* of episodes per process, parallelize
  across cores; target thousands of episodes/min on 8 cores. The Godot boot tax makes the
  "one process, many episodes" discipline even more load-bearing than in the JS lane.

---

## 6. Verification parity (G0-G4) — nothing structurally blocks the oracles

- **G0 static facts** — `check`-mode run builds the world once, reports entities/queries/
  penetration/goal-probes (mirror `runCheck`). In spec-mode, "static scan" is trivially safe
  (no code). **Parity: clean.**
- **G1 rollout / NaN / escape** — `run_batch(escape_margin=…)`; NaN sentinel = `is_finite`
  on transform/velocity each physics frame, explosion = `|v|>VMAX`, freeze on trip (mirror
  `world.js:_sane`). **Parity: clean.**
- **G2 purity** — predicates run twice on a fresh world, snapshot-unchanged. In spec-mode
  predicates are pure by construction (`Expression` over queries). **Parity: clean, even easier.**
- **G3 witness replay** — **the determinism-critical oracle**: the witness must replay
  byte-identically → Rapier (or stock-if-spike-passes). Solidity/penetration check needs
  contact-manifold read (contact_monitor / Rapier contacts). **Parity: gated on §2.4 only.**
- **G4 fuzzing at speed** — needs fast batched headless throughput (§5); no oracle-logic
  blocker, purely a throughput/boot concern. **Parity: clean if batched.**

---

## 7. Top 5 risks + mitigations

1. **Determinism doesn't hold (breaks G1/G3).** *Mit:* Rapier fast-local-deterministic variant
   is the default (byte-stable same-machine, confirmed by maintainers); reset only in
   `_physics_process`; fresh deterministic world per episode; cross-platform variant if replay
   must cross machines. Spike gate (c) is a hard go/no-go on this.
2. **Untrusted GDScript = full ambient authority (GodLoader-class).** *Mit:* declarative
   game-spec + frozen audited interpreter — no untrusted code in-engine (§2.3). Free GDScript is
   a later, OS-Job-Object-gated stretch goal, never shipped without isolation.
3. **Boot latency kills throughput.** *Mit:* batch the whole layer in ONE process
   (`batched=True`), warm the import cache once (`--import --quit`), never relaunch per episode,
   parallelize across cores. Spike gate (a)/(b) quantifies it.
4. **Semantic drift from pymunk/Planck** (joint mapping, grounded normal tol, bbox skin,
   contact separation) silently changes G0/G3 verdicts. *Mit:* port the exact numeric constants
   (`CONTACT_TOL`, `GROUND_NORMAL_TOL`, `VMAX`, K=6, mulberry32 RNG) from `world.js`; add a
   cross-engine parity test that runs a fixed certified game and diffs the episode dict shape
   (not exact floats — result/ticks/checkpoints/dead-action/order), exactly as the JS lane did.
5. **Windows first-run friction** (Defender/SmartScreen scan; `res://` vs `user://`; the
   `--quit-after 1` skips-import footgun #77508). *Mit:* Defender exclusion for the Godot binary
   + project dir; call `quit()` from script after the budget (not `--quit-after 1`); temp-file
   job path; forward-slash `res://` discipline. All one-time setup.

---

## 8. DAY PLAN (today)

### AM — ½-day spike, HARD gates (do not proceed to PM if any gate fails)

Mirrors `godot.md`'s recommended spike, now with concrete pass/fail thresholds.

- [ ] **Install** Godot 4.6 stable (portable `.exe`) + Rapier2D addon (fast variant); add
      Defender exclusion; one-time `godot --headless --import --quit` to warm the cache.
- [ ] **`runner.gd`** (~60 lines, `extends SceneTree`): build a fixed 3-body scene
      (ground + 2 stacked crates + a controlled ball), `_physics_process` steps, tick counter
      gates K=6, apply a scripted impulse every decision tick, print one JSONL line/episode,
      `quit()` after 40 episodes. Launch `--headless --fixed-fps 60 -s runner.gd -- --job=…`.
- [ ] **Gate (a) — boot:** steady-state process boot **< 2 s** (after warm import). *Fail → NO-GO
      today, escalate: investigate `--import` caching / disable unused servers.*
- [ ] **Gate (b) — throughput:** 40-episode batch (≈29k steps) completes **< 5 s** total
      (boot+sim). *Fail → revisit batch size / body count; still likely GO with tuning.*
- [ ] **Gate (c) — DETERMINISM (decisive):** two identical-seed 40-episode runs → **byte-identical
      final-snapshot JSONL**, measured **stock Godot Physics 2D AND Rapier2D**. Pass with Rapier =
      **GO**. Pass with stock too = **GO + save a dependency**. **Fail on both = NO-GO** (breaks
      G1/G3 witness replay — the one true blocker).
- [ ] **Gate (d) — stdin vs temp-file:** confirm the job-in path that reliably delivers JSON to
      headless Godot (temp-file expected to win). Records the protocol detail for the shim.

### PM — if gates pass: scaffold the port (mirror how `nodeworld/` graduated)

`nodeworld/` went spike → `world.js` (substrate) → `runner.js` (episode CLI) → `JsExecutor`
(executors.py) → `_verify_js` funnel (gameverify.py) → cross-engine parity tests. Repeat:

1. **`godotworld/` dir** (sibling of `nodeworld/`): `runner.gd` (episodes+check modes, JSONL,
   the FROZEN interpreter) + a `World`-equivalent GDScript class (§2.5 mapping) + the game-spec
   schema doc. Port the exact numeric constants and mulberry32 RNG from `world.js`.
2. **`GodotExecutor`** in `harness/verify/executors.py` — copy `JsExecutor`, swap `node`→`godot`,
   add the `--headless --fixed-fps -s runner.gd -- --job=<tempfile> --physics=rapier2d` argv and
   temp-file job write; keep `batched=True`, same `VerifyError` shapes.
3. **`_verify_godot`** in `gameverify.py` + `detect_engine → "gd"` — line-for-line twin of
   `_verify_js`; `report["engine"]="gd"`. Oracles/hints/witness untouched.
4. **Parity test** `tests/test_godot.py` — one certified spec-game run through the Godot funnel;
   assert the report *shape* matches (passed/failure_class/layers/dead-action/checkpoint-order),
   `skip` if `godot` not on PATH (exactly how JS-lane tests skip on missing `node`).
5. **Declarative-spec generator prompt** (later wave, not today): teach `gamegen` to emit the
   Godot game-spec + `Parts used:` (bank nouns → `.tscn`); today just hand-write one spec to
   drive the parity test.

**Definition of done for TODAY:** either (i) a measured NO-GO with the exact failing gate, or
(ii) `runner.gd` running 40 headless deterministic episodes + a `GodotExecutor` skeleton that
returns one parsed episode dict through `run_batch` — the same "first green pixel" `nodeworld/`
hit on its spike day.

---

## Sources (fetched live 2026-07-14)

- Determinism issue #112976 (CLOSED, milestone 4.7; repro 4.5.1/4.3; `_process`-reset root cause) — https://github.com/godotengine/godot/issues/112976
- godot-rapier-physics (949★, MIT, v0.8.39, 4.4-4.7, two determinism variants, serde) — https://github.com/appsinacup/godot-rapier-physics ; determinism doc https://godot.rapier.rs/docs/documentation/determinism/ ; asset-store fast https://godotengine.org/asset-library/asset/2267 , cross-platform https://godotengine.org/asset-library/asset/2815
- godot-box2d (binary-det untested, no cross-platform, maintenance-only) — https://github.com/appsinacup/godot-box2d ; issue #21 https://github.com/appsinacup/godot-box2d/issues/21
- Erodenn/godot-mcp-runtime (47★, MIT, v3.2.1, McpBridge TCP runtime bridge) — https://github.com/Erodenn/godot-mcp-runtime
- htdt/godogen (4713★, MIT, autonomous Godot/Bevy/Babylon, build-time scene gen, video proof) — https://github.com/htdt/godogen
- godot_rl_agents (1532★, MIT, Sync/TCP, speed-up, headless, ONNX) — https://github.com/edbeeching/godot_rl_agents
- Coding-Solo/godot-mcp (~4.7k★, run+capture-stdout) — https://github.com/Coding-Solo/godot-mcp ; hi-godot/godot-ai (screenshots) — https://github.com/hi-godot/godot-ai
- Randroids-Dojo/Godot-Claude-Skills (35★, GdUnit4/PlayGodot) — https://github.com/Randroids-Dojo/Godot-Claude-Skills ; gamedev-skills (264★, 66 SKILL.md) — https://github.com/gamedev-skills/awesome-gamedev-agent-skills ; "Godot Games" skill writeup — https://jonathansblog.co.uk/the-godot-games-claude-code-skill-build-complete-godot-games-with-ai
- GDScript sandbox reality: GodLoader malware — https://www.helpnetsecurity.com/2024/11/27/godot-engine-malware-loader-godloader/ ; secure-env issue #77611 — https://github.com/godotengine/godot/issues/77611
- Godot CLI/headless/--fixed-fps — https://docs.godotengine.org/en/stable/tutorials/editor/command_line_tutorial.html ; PhysicsServer2D (no space_step) — https://docs.godotengine.org/en/stable/classes/class_physicsserver2d.html
- Godot 4.5 (TileMapLayer cell-shape merge) — https://godotengine.org/releases/4.5/ ; NavigationRegion2D from TileMapLayer — https://docs.godotengine.org/en/stable/classes/class_navigationregion2d.html ; headless boot #112425 https://github.com/godotengine/godot/issues/112425 , import footgun #77508 https://github.com/godotengine/godot/issues/77508
- Local seam: `harness/verify/executors.py`, `harness/verify/gameverify.py` (`_verify_js`), `nodeworld/runner.js`, `nodeworld/world.js`, `CONTRACTS.md` §1-2/§9
