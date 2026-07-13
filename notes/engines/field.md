# Field scan: real game engines for the agent↔engine feedback loop

> Scope: everything **beyond Roblox and Godot** (covered by two other agents). Written
> 2026-07-13. Sources are primary where possible (GitHub READMEs/docs, engine docs,
> arXiv, practitioner posts). Author: field-scan agent.

## Why this document exists

Our pymunk harness's entire value is the **loop**: text → generated game → verify by reading
engine **STATE** (never pixels) → MANY fast seeded rollouts for solvability → deterministic
witness replay. The "next rung" is real game engines. The only question that matters is:
**does a candidate engine preserve loop quality?** Concretely, seven dimensions:

| # | Dimension | What "5" means (our pymunk baseline) |
|---|---|---|
| SR | **State-read** | In-process, machine-readable snapshot of every body/flag, any tick, no plumbing |
| AI | **Action-inject** | Apply one action per decision tick programmatically, in-process, no UI |
| DET | **Determinism** | Same seed → identical trajectory, reproducibly, ideally cross-platform |
| SPD | **Speed** | Faster-than-realtime; 40×120-tick rollouts in ≈seconds (no renderer in the loop) |
| HL | **Headless** | Runs with zero display/GPU, trivially |
| SBX | **Sandboxing** | Generated code is untrusted; contain it cheaply (AST scan + isolated process) |
| CLA | **Claude-integration maturity** | Turnkey, well-trodden path to drive it from an LLM today |

The pymunk harness scores ~5 on SR/AI/DET/SPD/HL/SBX because it was **purpose-built** for this.
No general engine will match it; the exercise is finding which one gives up the least.

**One framing conclusion up front:** most "engine + LLM" tooling in the wild is an
**authoring/editor** loop ("make the agent build a scene, edit a script, run the test suite"),
NOT a **rollout** loop ("run 40 seeded episodes headless in 2 seconds and read terminal state").
Those are different products. Our harness needs the second; almost every MCP server ships the
first. Keep that distinction in mind for every scorecard below.

---

## 1. Web-native, PURE NODE — Planck.js / Matter.js (no browser)  ★ top pick

This is the natural next substrate and deserves top billing. **Planck.js** is a straight
JavaScript port of **Box2D** (algorithms unmodified); **Matter.js** is a native-JS 2D rigid-body
engine. Both run in plain Node with **no renderer at all** — you call `world.step(dt)` (Planck)
or `Engine.update(engine, dt)` (Matter) in a tight loop and read entity state directly from JS
objects. This is *the same architecture as our pymunk harness*, just in JS instead of Python.

- **Determinism.** Planck: "deterministic with fixed timestep… for the same input and same
  JavaScript runtime, Box2D/Planck.js will reproduce any simulation; does not use random numbers."
  That is a first-class, documented guarantee (same caveat as all Box2D lineage: same-binary,
  not bit-identical across CPUs). Matter.js moved to a **fixed deterministic timestep by default
  in v0.20.0** and *removed* non-fixed timestep — but Matter has a longer history of determinism
  complaints (open issues asking "how to make matter-js deterministic"), so **Planck is the
  stronger determinism choice**; Matter is fine if you drive `Engine.update` yourself with a
  fixed delta and avoid the Runner's wall-clock pacing.
- **Speed / headless.** No graphics dependency whatsoever; the loop runs as fast as the CPU
  allows. This is exactly what "MANY fast seeded rollouts" wants.
- **Sandboxing** is the one real downgrade vs Python. There is no AST-scan-plus-`math`-only
  equivalent out of the box; untrusted generated JS has more escape surface. But it is a solved
  problem in practice: run each episode in a **child process / worker with no network, `node:vm`
  or a locked-down global, `--disallow-code-generation-from-strings`, rlimits**, or use Deno's
  permission flags for deny-by-default FS/net. Costs engineering, not feasibility.

**Scorecard** — SR 5 · AI 5 · DET 5 (Planck) / 4 (Matter) · SPD 5 · HL 5 · SBX 4 · CLA 3

**What we'd give up:** our Python `World` API and pymunk; we port the substrate to JS (a few days —
the substrate is small). Sandbox story is weaker and needs building.
**What we'd gain:** it *is* a real, mainstream game-engine family (Box2D lineage; Matter is the
physics engine Phaser ships), so it satisfies "produce environments in a real engine" honestly;
**free browser-deployable demos** for the site (same code runs in the page); a giant ecosystem;
and — crucially — **the loop stays essentially as tight as today's.** This is the lowest-regret
migration.

---

## 2. Web-native, IN BROWSER — Phaser/Kaplay + Matter, driven via CDP/Playwright  ★ strong #2

This is the **GameGen-Verifier pattern** from our literature, and it is not theoretical: two
real, recent systems run it.

- **GameGen-Verifier** (arXiv 2605.07442): decomposes a spec into keypoints, then for each
  keypoint **patches the runtime into a target state** (via the **Chrome DevTools Protocol
  runtime domain** — modify the JS objects for entities/positions/health/flags directly),
  runs a **bounded interaction** (a short, timeout-bounded action sequence, seconds not minutes),
  and checks an **assertion** over runtime state (or a VLM judgment on a screenshot when the
  postcondition is visual). Reported: **92.2% accuracy vs 58.8%** for an agent-plays-the-game
  baseline, **up to 16.6× faster**, parallelizable with per-unit browser isolation. Explicitly
  **web-stack only (JS/TS/HTML)**; the authors state Godot/Unity/Unreal are out of scope because
  they "lack mature LLM generation pipelines." This *is* our planned L3 state-injection rung,
  already validated on the web substrate.
- **Phaser Game Agent** (Phaser Studio, built on **Claude Managed Agents**): a QA subagent runs
  5 phases per step — build check, **runtime check in headless Chromium**, **gameplay verification
  (drive the client with game-specific actions)**, architecture validation, and **visual review
  via Playwright MCP screenshots**; a failing phase triggers an autofix subagent, up to 3 retries.
  This is our repair loop, in production, on the web substrate.

The trade vs pure Node: a browser is a natural sandbox (origin isolation, no FS) **but heavy** —
a full headless Chromium per verification unit throttles the "many fast rollouts" budget, and
`requestAnimationFrame` pacing must be bypassed (drive the step manually) or determinism/speed
both suffer. Good when you want the *whole* engine + free visual demos + a VLM perception channel;
worse than pure Node for cheap high-volume seeded rollouts.

**Scorecard** — SR 4 · AI 4 · DET 4 · SPD 3 · HL 4 · SBX 4 · CLA 4

**Give up:** the cheapest, tightest rollout loop (Chromium overhead). **Gain:** the exact
literature-blessed injection pattern, VLM-in-the-loop for perception, and demos are the live game.

---

## 3. Unity — mature MCP, but an AUTHORING loop, not a rollout loop

Two credible MCP servers dominate:
- **CoplayDev/unity-mcp** (a.k.a. justinpbarnett lineage): **12.4k★, MIT, very active** (v10.x,
  2026), **48 tools across 10 groups + 25 read-only "resources"**. Manage assets, control scenes,
  edit C#, **run tests**, profile, build. Unity 2021.3→6.x, Python 3.10+ bridge.
- **CoderGamester/mcp-unity**: Node/TS bridge; `run_tests` returns pass/fail counts and state
  (`returnWithLogs`); resources for read-only project data. Notes the real gotcha: **disable
  "Reload Domain"** in Enter-Play-Mode settings for PlayMode tests.

**Unity's own AI** (Unity 6.2, 2025): "Unity AI" folds the retired **Muse** and renamed **Sentis
→ Inference Engine** into Assistant + Generators. This is **asset generation and in-editor coding
help**, not an agent-controls-the-running-game API. Irrelevant to our loop.

**Feedback-loop reality.** The MCPs are **editor/asset-centric**. You *can* read runtime state
and inject actions, but the idiom is: write a C#/PlayMode test that scripts the actions and
asserts on state, enter play mode, read the XML result — an **authoring round-trip**, not a
per-tick agent channel over 40 fast episodes. And:
- **Headless:** `-batchmode -nographics` works, but by default the player is **still frame-limited
  to realtime (~60 FPS)** — you must raise `Time.timeScale` / use `Time.captureDeltaTime` to go
  faster, and editor/process startup is heavy.
- **Determinism:** deterministic on the **same machine** with a fixed timestep; **not cross-platform**
  (PhysX float divergence). DOTS/Unity Physics adds a `FixedStepSimulationSystemGroup` (default
  1/60), but cross-platform bit-determinism needs the community **soft-float** fork.
- **Sandboxing:** generated **C# compiles into the project and runs with full process privileges** —
  no built-in isolation; you'd need OS-level containers, and a C# static-scan is more work than our
  Python AST gate.
- **Licensing friction:** batchmode needs a license activation (`-serial -username -password`), and
  **Unity Personal can't use manual/CLI activation** — you pre-activate via Unity Hub, harvest the
  `.ulf`, and feed it to CI. Real per-machine friction for an automated pipeline.

**Scorecard** — SR 3 · AI 3 · DET 3 · SPD 2 · HL 3 · SBX 2 · CLA 4 (mature, *for authoring*)

**Give up:** fast seeded rollouts, cheap sandboxing, cheap determinism, zero-friction CI.
**Gain:** genuine 3D, real-engine credibility, huge asset ecosystem, the most mature MCP tooling —
if the deliverable were "agent builds a Unity scene," Unity wins; for "agent verifies solvability
over thousands of rollouts," it fights us.

---

## 4. Unreal — heaviest, weakest determinism, Python-not-in-PIE

MCP landscape is fragmented:
- **chongdashu/unreal-mcp** (~2k★, MIT, "EXPERIMENTAL"): actor/blueprint/editor control.
  **Editor-only — no runtime state read, no play-mode control, no tests.**
- **remiphilippe/mcp-unreal** (58★, Apache-2.0, ~4 commits, early): the most loop-relevant — 
  **headless** `build_project`/`run_tests`/`cook` via `UnrealEditor-Cmd`, and **during PIE**
  reads state as **structured JSON** through the **Remote Control API** (`get_level_actors`,
  `get_property`, `call_function`, `player_control`). Immature but the right shape.
- **Epic's official** Unreal MCP (UE 5.8): a Toolset Registry (SceneTools, ActorTools, …) with
  Python/C++ toolsets exposed as agent tools — official backing, but again editor-authoring.

**The killer nuance for our loop:** Unreal **Python is editor-only — it does NOT run during Play
In Editor.** So the clean "read gameplay state in Python each tick" path does not exist; runtime
state must come through the **Remote Control HTTP API** (or C++/Blueprint), which is plumbing-heavy
and per-call, not a tight in-process snapshot.

- **Determinism:** **Chaos physics is not deterministic by default** — real-time, timing-sensitive.
  Reproducibility needs enhanced-determinism settings, substepping, or **caching** (record-and-replay
  transforms). Worst determinism story of any candidate here.
- **Headless:** `-nullrhi` / `UnrealEditor-Cmd` headless builds/tests work, but everything is heavy.

**SimWorld Studio reuse** (arXiv 2605.09423): its **SimCoder** writes engine-level UE5 code and
revises it from **rich verifier feedback — compilation errors, collision reports, physics checks,
VLM critiques** — and **autonomously authors reusable skills** into its own library. That design
is a near-mirror of our repair loop + OMNI-EPIC skills, and worth reading as validation of the
approach. But it targets **3D embodied worlds with real-time pixel streaming**, not a fast
deterministic state loop. **Verdict on reuse: the *ideas* transfer (verifier-driven repair,
skill accretion); the *code* is UE5-specific and not a drop-in for our loop.**

**Scorecard** — SR 3 (via Remote Control) · AI 2 · DET 2 · SPD 2 · HL 3 · SBX 2 · CLA 3

**Give up:** basically everything that makes our loop cheap and reproducible.
**Gain:** top-tier 3D and Epic's official tooling — only justified if UE-specific 3D is a hard
requirement, which for a 2D-physics verification harness it is not.

---

## 5. Bevy + Rapier (Rust) — best determinism, worst LLM ergonomics

Bevy (Rust ECS engine) + **Rapier** (dimforge's Rust physics) is the **determinism champion**:
Rapier's **`enhanced-determinism` feature gives bit-level cross-platform reproducibility** across
all IEEE-754-strict platforms — "serialized physics states producing identical byte arrays." No
other candidate offers *cross-platform* bit-determinism. In-process ECS queries give perfect
state-read/action-inject, and Bevy runs trivially headless (`MinimalPlugins`, no render).

The catches are severe for *our* loop:
- **`enhanced-determinism` cannot combine with `parallel`/SIMD** → you trade the speed you'd want.
- **Feedback-loop tax:** generated **Rust must compile every iteration** (`rustc` latency), and
  LLM first-try success on Rust is lower than on Python/JS/Lua → more repair cycles, each slow.
- **Sandboxing:** native Rust runs unsandboxed; a real sandbox means targeting **wasm + wasmtime**
  (doable, adds a layer, slower), and that path is not well-trodden for game-gen.
- **Claude integration: essentially none** — no notable Bevy MCP or LLM game-gen pipeline.

**Scorecard** — SR 5 · AI 5 · DET 5 · SPD 4 · HL 5 · SBX 3 (wasm path) · CLA 2

**Give up:** LLM ergonomics and any ready integration; accept compile-in-the-loop latency.
**Gain:** unmatched determinism + native speed — the pick *only* if cross-platform bit-determinism
becomes a hard requirement (e.g., distributed rollout workers that must agree bit-for-bit).

---

## 6. Defold — the sleeper: excellent headless, Lua, deterministic Box2D

Defold ships an **official headless engine variant** (no graphics/sound): `java -jar bob.jar
--variant=headless` builds and runs; **DefTest** provides unit testing; `--#IF HEADLESS`
preprocessor markup toggles code paths; CI recipes exist. Physics is **Box2D** with a fixed
update step → deterministic in the Box2D sense. Game logic is **Lua**, which is (a) LLM-friendly
and (b) one of the more genuinely **sandboxable** languages (restrict the global environment,
strip `os`/`io`). State-read/action-inject are in-process (`go.get`/`msg.post`).

The gap is purely ecosystem: **no known Defold MCP or LLM game-gen pipeline** — you'd build the
Claude integration from scratch. But the *substrate* is arguably better-suited to our loop than
Unity/Unreal: lightweight C engine, real headless mode, deterministic physics, sandboxable
scripting.

**Scorecard** — SR 4 · AI 4 · DET 4 · SPD 4 · HL 5 · SBX 4 · CLA 2

**Give up:** a ready Claude path (build it yourself). **Gain:** a lean, genuinely-headless, 
deterministic engine with a sandboxable scripting language — the best "real engine" fit *after*
the web-native family, if you're willing to build the glue.

---

## 7. Others — brief, and mostly "skip for the loop"

- **LÖVE (love2d).** Physics is Box2D (deterministic-ish); Lua (sandboxable). But **headless is
  broken-by-design**: `t.window=false` runs, yet `love.graphics` is disabled and the engine
  **crashes if you touch a canvas**; true offscreen needs patching LÖVE's C source. No Claude
  ecosystem. The headless story alone disqualifies it for high-volume rollouts. *Skip.*
- **PICO-8 / TIC-80.** Fantasy consoles. PICO-8's `-x cart.p8` runs a cart "headless then quits"
  (experimental, poor error reporting); no external **state-out API** for a per-tick agent loop —
  they're designed for humans at a console, not machine-readable rollouts. Charming as a
  *constrained generation target* (tiny API, forces creativity), useless as a *verification
  substrate*. **SR/AI ≈ 1.** *Skip for the loop; maybe revisit as an exotic-prompt target.*
- **Kaboom/KAPLAY.** KAPLAY is the maintained fork of the abandoned Kaboom.js — a **web** JS/TS
  game *library* with a deliberately friendly **verb-based API** (which happens to match the
  Phaser-Agent "design for the agent" lesson). But its physics is simple AABB, **not** full
  rigid-body, so for a *physics* verification harness it's weaker than Matter/Planck. It inherits
  the browser-CDP loop of §2. *Subsumed by the web-native family; pick Matter/Planck for physics.*
- **PixiJS.** Pure **renderer**, no physics at all. Relevant only as the draw layer you'd bolt
  onto Matter/Planck for demos. Not an engine choice on its own.

---

## Blog / write-up digest — the recurring lessons

Nine lessons recur across practitioner posts, the Phaser Studio write-up, the StraySpark
playtesting-agents post, GameGen-Verifier, SimWorld, and Claude-agent-loop material. They form
a de-facto spec for "a workable LLM↔engine loop," and our harness already embodies most of them:

1. **Binary pass/fail beats scores.** "You can't build a feedback loop from a score of 7/10; you
   can from pass/fail checks that tell you exactly which properties held." → validates our
   **unshaped binary `success` certificate** + checkpoints for structure.
2. **State-based waiting beats brittle timing.** The single most-cited reliability fix: replace
   "wait 2 seconds for the level" with "loop until `is_level_loaded()` is true." Eliminates
   flakiness; is inherently deterministic. → our runner reads state each tick, never sleeps.
3. **Read STATE, not pixels.** "MCP agents test in PIE by reading properties and variables, not
   analyzing pixels." The flip side is explicit — "visual bugs that don't affect game state are
   invisible to state-based testing" — which is *fine* for mechanics/solvability and exactly the
   trade we already chose. → "no pixels in verification" is the industry-converged position.
4. **Inject state + bounded interaction + assert > agent-plays-the-whole-game.** GameGen-Verifier's
   92.2% vs 58.8% and up-to-16.6× speedup come from **not** requiring the agent to *reach* states by
   playing; you *patch* the state, run a few ticks, assert. → this is precisely our planned L3
   keypoint-injection rung, now with external validation on the web substrate.
5. **Design the API for the agent, not the human.** Phaser rebuilt its engine "around how an agent
   reasons — in verbs and intentions," and warns: "a big API doesn't just slow an agent down, it
   *degrades* it: too many functions, too little signal, hallucinated lookalikes," and "docs are
   what the model actually reads" (docs written *into* the API surface). → directly informs how we
   shape the generated-game `World` API and the generator prompt: **fewer, verb-shaped tools.**
6. **Grow components/skills, not the framework.** "Don't expand the framework to infinity — expand
   the components." Accumulate *verified reusable mechanics* rather than bloating the core. Mirrors
   SimWorld's autonomous skill library and OMNI-EPIC. → supports our "skills may emerge" plan while
   keeping the base API small and the prompt free of game-specific hardcoding.
7. **Keep a perception channel (human or VLM) OUT of the reward but IN QA.** "Automated checks
   prove a meter wraps correctly; they can't tell you the landscape scrolls in ugly chunky steps,"
   and "AI agents have no concept of fun." → our GIF replay + (future) optional VLM check is the
   right place for perception; it must never enter the binary certificate.
8. **Determinism is engineered, not free.** Fixed timestep, no wall-clock pacing, only seeded RNG.
   Real 3D engines are the cautionary tale: **Unity PhysX and Unreal Chaos are not cross-platform
   deterministic by default**; the reproducible substrates are **Box2D-lineage web (Planck)** and
   **Rapier-enhanced (Bevy)**. → our fixed-dt, seeded, snapshot-diff determinism gate is the norm
   the good systems converge to.
9. **Editor round-trips are authoring loops, not rollout loops.** Every heavyweight MCP (Unity,
   Unreal) optimizes "make/edit/test in the editor," which is a *different* loop from "run 40
   seeded episodes headless in seconds and read terminal state." Do not be seduced by star counts:
   a 12k★ editor-automation MCP does not give us a fast solvability probe.

---

## Ranked shortlist — "if we go engine X, the loop looks like…"

| Rank | Engine family | SR | AI | DET | SPD | HL | SBX | CLA | One-line loop shape |
|---|---|---|---|---|---|---|---|---|---|
| **1** | **Pure-Node web (Planck.js / Matter.js)** | 5 | 5 | 5/4 | 5 | 5 | 4 | 3 | Port the `World` substrate to JS; `world.step()` in a Node tight loop; state from JS objects; sandbox per-episode in a locked child process |
| **2** | **Browser web (Phaser+Matter via CDP/Playwright)** | 4 | 4 | 4 | 3 | 4 | 4 | 4 | GameGen-Verifier pattern: CDP injects state, bounded action sequence, assert on state (+VLM for visuals); demos are the live game |
| **3** | **Defold (headless variant)** | 4 | 4 | 4 | 4 | 5 | 4 | 2 | `bob --variant=headless` + DefTest; Lua game code (sandboxable); Box2D fixed-step; build the Claude glue yourself |
| **4** | **Bevy + Rapier (Rust)** | 5 | 5 | 5 | 4 | 5 | 3 | 2 | In-process ECS; `enhanced-determinism` for bit-exact cross-platform; pay rustc compile-in-the-loop + wasm sandbox; no ready MCP |
| **5** | **Unity (unity-mcp / mcp-unity)** | 3 | 3 | 3 | 2 | 3 | 2 | 4 | Author PlayMode tests that script actions + assert state; `-batchmode -nographics` (raise `timeScale`); license/`.ulf` friction |
| **6** | **Unreal (mcp-unreal / Epic MCP)** | 3 | 2 | 2 | 2 | 3 | 2 | 3 | Remote-Control HTTP for PIE state (Python not in PIE); Chaos non-deterministic; reuse SimWorld *ideas* not code; only for hard-3D needs |
| — | **PICO-8 / TIC-80 / LÖVE / Kaboom / PixiJS** | 1–2 | 1–2 | – | – | 1–3 | – | 1 | Skip for the loop (no machine-readable state channel / broken headless / no rigid-body physics). PICO-8 only as an exotic *generation* target |

### The call

**Go pure-Node web-native (Planck.js primary, Matter.js as the ships-with-Phaser alternative).**
It is the only "real engine" move that keeps our loop essentially as tight as today's: in-process
state-read and action-inject, documented fixed-timestep determinism, faster-than-realtime headless
rollouts, and — as a bonus — **free browser demos** for the site and a **direct on-ramp to the
GameGen-Verifier browser pattern (#2)** when we want VLM-in-the-loop perception or the full engine.
The only thing we truly give up versus pymunk is the clean AST+subprocess sandbox, which is
replaceable with process isolation. Everything heavier (Unity/Unreal) trades away exactly the
properties the harness exists to provide, in exchange for 3D and editor-authoring maturity we
don't need for a 2D-physics verification loop.

**If cross-platform bit-determinism ever becomes mandatory** (e.g. distributed rollout workers that
must agree byte-for-byte), that is the one scenario that would promote **Bevy + Rapier** above the
web option — at the cost of Rust compile latency and building the LLM integration ourselves.
