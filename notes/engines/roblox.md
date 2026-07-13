# Claude ↔ Roblox Studio — deep-dive on the feedback loop for our harness

> Analysis date: 2026-07-13. Every capability claim below is tagged with a primary
> source (official docs `create.roblox.com/docs`, GitHub repos, or Roblox staff posts on
> `devforum.roblox.com`). Where a marketing/README claim and the engine's actual behaviour
> differ, the **engine behaviour wins** and the gap is flagged. Framing question set by the
> mission: *how tight is the engine↔agent feedback loop, OUR way* — i.e. build-a-scene →
> step-deterministically → read-back-state-programmatically → inject-actions, at the cadence
> and rollout volume our G0–G3 oracles need (≈60 seeded rollouts per candidate, in seconds,
> reading STATE not pixels).

## TL;DR verdict

- **Two of the four loop legs are excellent, two are broken for us.** Building scenes
  programmatically (leg a) and reading back arbitrary DataModel state as JSON (leg c) are
  first-class and officially supported through both the built-in Studio MCP server and the
  Open Cloud Luau Execution API. **Stepping physics deterministically (leg b) and injecting
  actions at a fixed sub-frame cadence over MANY fast rollouts (leg d + our G1/G3) are where
  Roblox breaks down.**
- **The killer facts, all from primary sources:**
  1. **The official headless path runs NO physics.** Open Cloud Luau Execution loads the
     DataModel and runs Luau, but a Roblox staff answer in the beta thread confirms physics
     simulation does **not** run there ("Not currently — though we are thinking about how we
     could give more control over this").
  2. **The only programmatic physics-stepping API is Studio/plugin-only.**
     `WorldRoot:StepPhysics(dt, parts)` carries **Plugin Security** and "doesn't work during
     runtime". So a controlled `world.step(n)` analog exists — but only inside an *open Studio
     GUI*, one instance per machine.
  3. **Physics is not seed-deterministic.** It is at best *conditionally* reproducible
     (`Workspace.PhysicsSteppingMethod = Fixed`, un-throttled, same machine); there is no
     physics seed, floating-point cross-machine reproducibility is not guaranteed, and there
     are live 2026 bug reports of non-deterministic constraint/collision behaviour.
  4. **Play mode is strictly real-time.** No time-scaling API; an ~800-physics-step episode
     costs ~13 s of wall clock. Faster-than-real-time exists **only** via a StepPhysics loop
     in a Studio plugin (GUI-bound, single instance).
- **Recommendation: Roblox is a poor fit for our verification-heavy inner loop, but a
  plausible OUTPUT target.** Our harness's whole value is cheap, deterministic, headless,
  faster-than-real-time seeded rollouts (G1 determinism check; G3's 40+20 episodes × 120
  ticks). Roblox denies all four of those properties simultaneously in any *sanctioned,
  headless* configuration. It is a great **build-and-one-real-playtest** target with a
  best-in-class Claude MCP integration — not a substrate for our solvability probe. If we
  wanted G3-style probing on Roblox we would have to reimplement physics deterministically in
  Luau and ignore the native engine, which defeats the point of "use a real engine".

---

## 1. The official Roblox Studio MCP server(s)

Roblox actually shipped **two** things, and the standalone one is now deprecated in favour of
a built-in one. Both matter.

### 1.1 `Roblox/studio-rust-mcp-server` (the standalone repo — now archived)

Source: `github.com/Roblox/studio-rust-mcp-server` (README + release pages).

- **Status: ARCHIVED by Roblox on 2026-04-03, read-only.** README banner: *"We've shifted
  ongoing engineering investment to the built-in MCP Server included with Roblox Studio, which
  we recommend as the best way to connect external AI tools going forward."* Maturity at
  archival: 30 commits, 23 releases; languages Rust 48% / Luau 47% (the Luau half is the
  Studio-side plugin).
- **Architecture / transport:** two Rust components. An `rmcp` server speaks **MCP over stdio**
  to the client (Claude Desktop / Cursor). A separate web server built on **`axum`** exposes a
  local HTTP endpoint that a companion **Studio plugin long-polls** (Studio cannot accept
  inbound connections, so the plugin polls for work and posts results back). Build path is
  `cargo run`. Localhost only; requires an open Studio session.
- **Tools exposed (6):**
  - `run_code` — *"Runs a command in Roblox Studio and returns the printed output. Can be used
    to both make changes and retrieve information."* (This is the read/write/execute workhorse.)
  - `insert_model` — inserts a model from the Creator Store into the workspace.
  - `get_console_output` — reads the Studio console.
  - `start_stop_play` — starts/stops play mode (or runs the server).
  - `run_script_in_play_mode` — runs a script in play mode, auto-stops when it finishes/times out.
  - `get_studio_mode` — reports current Studio mode.
- **Limitations:** single local Studio instance, GUI must be open and logged in; no headless
  mode; stdio-only; superseded.

### 1.2 The BUILT-IN Studio MCP server (current, recommended)

Sources: `create.roblox.com/docs/studio/mcp` (official docs), devforum announcements
"Studio MCP Server Updates and External LLM Support for Assistant" and "Assistant Updates:
Studio Built-in MCP Server and Playtest Automation".

- **What it is:** an MCP server now embedded *inside* Studio, kept in lockstep with the Studio
  Assistant's own toolset (so external clients "always have the latest capabilities without
  pulling updates"). Runs as a **local process**; **stdio** transport.
- **Enable / connect:** Assistant → `…` → *Manage MCP Servers* → *Enable Studio as MCP server*.
  Connect via quick-connect GUI, a JSON config file, or a CLI command. **Quick-connect clients
  named in the docs include Claude Desktop, Claude Code, Cursor, VS Code, Codex CLI, Gemini CLI,
  Antigravity.** Requirements: latest Studio, Studio running and logged in.
- **Tools exposed (grouped, names quoted from the docs):**
  - *DataModel read:* `search_game_tree` ("explores the instance hierarchy as a flat JSON
    array"), `inspect_instance` (properties, attributes, child/descendant summaries).
  - *Scripts:* `script_read`, `script_search` (fuzzy, ≤10 results), `script_grep` (≤50 matches),
    `multi_edit` (apply many edits / create scripts).
  - *Execute:* **`execute_luau`** — *"Runs Luau code in Studio. Returns either the result or an
    error."* (This is our leg-a builder and leg-c reader.)
  - *Playtest:* `start_stop_play`, `get_studio_state` (incl. play state), `get_console_output`,
    `screen_capture` (viewport, optional camera), `character_navigation` (move the player
    character to a position — bypasses the input system).
  - *Assets:* `generate_mesh`, `generate_material`, `generate_procedural_model`, `search_asset`,
    `insert_asset`, `upload_image`.
  - *Multi-instance:* `list_roblox_studios`, `set_active_studio` (one client → several Studios).
- **Playtest automation (the interesting part for leg d):** the announcement says agents can
  *"plan, execute, and test … with minimal manual intervention"* — start/stop play, read console,
  **simulate mouse clicks/movement, simulate keyboard input ("like walking or jumping")**, and
  `character_navigation` for direct teleport-style movement. This is real-time input injection
  driving a live playtest, with per-session script auto-approval so it can run unattended.
- **Key architectural nuance for us:** `execute_luau` and the standalone server's `run_code`
  run in Studio's **command-bar / plugin security context**. That context is exactly the one
  that is *allowed to call `WorldRoot:StepPhysics`* (Plugin Security — see §3b). So the MCP is,
  in principle, a channel through which an agent could drive a manual physics-stepping loop in
  Studio edit mode. That is the single most important lever if one insisted on doing our loop on
  Roblox (see §6). It is undocumented as an MCP use-case and unverified end-to-end.

## 2. Community integrations

- **`boshyxd/robloxstudio-mcp`** (`github.com/boshyxd/robloxstudio-mcp`) — the most prominent
  community server. v2.7.0, ~483 stars, **archived 2026-06-06** (maintainer points to a fork).
  **43 tools** (read/inspect/write); an "Inspector Edition" trims to 31 read-only tools. Notably
  exposes `get_playtest_output`, `get_output_log`, `get_instance_properties`, `search_objects`,
  `capture_screenshot`. Transport is **HTTP** (needs "Allow HTTP Requests" enabled) via an NPX
  server ↔ Studio plugin. Superseded by the built-in server for most purposes, but its 43-tool
  surface is a good menu of what people actually want.
- **Other MCP servers:** `ZubeidHendricks/roblox-studio-mcp-claude-code`,
  `drgost1-robloxstudio-mcp` (LobeHub), plus forks of the Rust server
  (`takoyakisoft/studio-rust-mcp-server-wsl2` adds a WSL2/Linux build). All are thin wrappers
  around the same "plugin long-polls a local server, runs Luau, returns output" pattern.
- **Luau execution bridges / headless toolchain (this is the important community layer):**
  - **Lune** (`lune-org`) — a standalone Luau runtime for CI/CD, test runners, codegen. Runs
    Luau *outside* Roblox. **It has no Roblox engine**: no DataModel, no physics — Roblox APIs
    must be *mocked*. Useful for pure-logic unit tests, useless for physics rollouts.
  - **TestEZ** (`roblox.github.io/testez`) — Roblox's own BDD test framework, used internally
    for core scripts/plugins. Runs in Studio (real engine) or under Lune with API mocks.
    `lrockreal/testez-luau` is a Roblox-independent fork. This is the natural home for a G0/G2
    style assertion suite *if* we accept it running inside a Studio session.
  - **Rojo / Rokit / Wally / Selene / StyLua / luau-lsp** — the standard file↔Studio sync +
    package + lint/format toolchain. Rojo lets the agent keep the game as *files on disk*
    (git-friendly) and sync into Studio — a clean fit for our "generated artifact is a file"
    model.
  - **Grey-market headless (NOT usable):** `rsblox/local_rcc` (self-hosted RCC game servers) and
    `toastering/rbxsilent` (a "headless" client with rendering disabled but physics still
    running) rely on **patched/modified Roblox binaries**. RCCService is Roblox's *internal*
    server; there is no sanctioned public self-serve RCC. These violate the ToS and are out of
    scope for a legitimate research harness — but they are the *only* things that run real
    Roblox physics headlessly, which tells you how deliberately closed that door is.

## 3. The feedback-loop anatomy, OUR way

Mapping each leg to what Roblox actually offers.

### (a) Build a scene programmatically — STRONG (5/5)
`execute_luau` / Open Cloud Luau creates the entire DataModel from code: `Instance.new("Part")`,
set `.Size`/`.CFrame`/`.Anchored`/`.AssemblyLinearVelocity`, weld/constraint joints
(`WeldConstraint`, `HingeConstraint`, `SpringConstraint`, `PrismaticConstraint`), collision
groups, sensors (`.CanCollide=false` + `.Touched`). This is a superset of our `World.add/pin/
pivot/spring/sensor`. Rojo can hold the scene as files. No complaints here.

### (b) STEP / RUN the simulation deterministically — WEAK (2/5)
Three mutually exclusive modes, none of which is "headless + deterministic + fast":

| Mode | Physics runs? | Controlled step? | Headless? | Determinism |
|---|---|---|---|---|
| **Open Cloud Luau Execution** | **NO** (staff-confirmed) | n/a | Yes | n/a |
| **Studio play mode** (`start_stop_play`) | Yes | No — real-time loop only | No (GUI) | Conditional, real-time |
| **Studio edit mode + `StepPhysics`** | Yes | **Yes** (`StepPhysics(dt, parts)`, 1/60) | No (GUI, plugin) | Conditional |

- **Open Cloud Luau Execution** (`create.roblox.com/docs/cloud/reference/features/luau-execution`):
  `POST /cloud/v2/universes/{universe_id}/places/{place_id}/luau-execution-session-tasks` with a
  Luau script; poll `GET …/tasks/{task_id}` and `…/tasks/{task_id}/logs`; get the script's return
  values + logs. **Limits: 5-minute max task, 10 concurrent tasks/place** (raised from 30 s / 2).
  Full DataModel + engine Luau API, DataStore/HttpService now allowed. **But: place changes do
  NOT save** ("We're working on enabling this") and — decisively — **no physics simulation runs**.
  So this is a headless *DataModel query/compute* service, not a headless *simulator*. It can run
  our G0 static checks and G2 goal-purity checks; it cannot run a rollout.
- **`RunService` semantics** for the real-time loop: `PreSimulation`→physics→`PostSimulation`
  →`Heartbeat` each frame; default physics runs at **240 Hz (~4 world-steps per 60 fps frame)**.
  A Luau loop with `task.wait()` can read state every Heartbeat — but it advances only in
  real-time, at the engine's cadence, not on our command.
- **`WorldRoot:StepPhysics(dt: number, parts: Instances)`**
  (`create.roblox.com/docs/reference/engine/classes/WorldRoot#StepPhysics`): advances the world
  (or a parts subset) by `dt`. Backs the "Step Forward Physics" Studio button (graduated from
  beta Sep 2024), which steps at 1/60 while paused. **Plugin Security → not callable in a
  published game at runtime.** Called repeatedly in a plugin loop it *can* advance physics
  faster than real-time in edit mode — this is the one and only fast, controlled `world.step(n)`
  analog, and it is chained to an open Studio GUI, one instance, plugin context.

### (c) READ BACK state programmatically — STRONG (5/5)
Everything is engine state, no pixels required. Luau reads `part.Position`/`.CFrame`/
`.AssemblyLinearVelocity`/`.AssemblyAngularVelocity`, `Workspace:GetPartBoundsInBox` /
`GetPartsInPart` for overlaps, `part:GetTouchingParts()` and `.Touched`/`ProximityPrompt`/
`.Collided` events for contacts, raycasts for support/grounding. `execute_luau` returns the
result value and Open Cloud returns structured return values + logs — both machine-readable JSON.
This maps cleanly onto `World.query/contacts/touching/grounded/snapshot/events`. This is the leg
Roblox is *best* at and it aligns perfectly with our "read STATE, never pixels" rule.

### (d) Inject actions repeatedly at a fixed cadence — MIXED (3/5)
- *Sanctioned, real-time:* playtest automation simulates mouse/keyboard and `character_navigation`.
  This is UI-level, real-time, and cadence is whatever the running game does — fine for "does the
  menu work", wrong for a fixed decision-tick `act(world, action)` at the physics-step level.
- *Programmatic, in a loop:* inside a plugin/command-bar Luau loop you can apply impulses/set
  velocities/set flags on the controlled part between `StepPhysics` calls — this IS a faithful
  `act` + `step` interleave (our K=6 pattern). But again: Studio GUI, plugin security, one
  instance, no seed guarantee.

### Loop rating (per cycle)
- **Latency/cycle:** Open Cloud task create→poll→result is seconds of round-trip (HTTP + place
  load), so it is unsuitable as a per-tick channel; a single Luau task should do a whole batch of
  work internally. MCP `execute_luau` is local (ms–low-hundreds-ms) but still one Studio.
- **Machine-readability:** excellent (JSON everywhere; no pixels needed).
- **Determinism:** *conditional at best.* Community + staff consensus: not deterministic by
  default; `PhysicsSteppingMethod=Fixed` + un-throttled + same machine gets you *close*; there is
  **no physics seed** (only `Random.new(seed)` for game-logic RNG); floating-point cross-machine
  reproducibility is explicitly *not* guaranteed; live 2026 non-determinism bug reports exist.
  `BindToSimulation`/`UseFixedSimulation` (full release Apr 2026, freq Hz1…Hz60, default 30 Hz)
  target *server-authority* "repeatable behaviour across clients and server" — a networking/
  resimulation goal, **not** a seeded-reproducibility guarantee, and the announcement gives no
  floating-point/cross-machine guarantee. Contrast our pymunk substrate: fixed dt + seeded RNG
  gives **bit-exact** replay, which G1's determinism check and witness replay depend on.
- **Faster-than-real-time:** only via the StepPhysics plugin loop; impossible in play mode
  (no time-scaling API) and impossible in Open Cloud (no physics).

## 4. Verification transferability (could G0–G3 port?)

- **G0 static (sandbox the generated code):** *Portable in spirit.* Luau is a sandboxed language
  by design (Roblox runs untrusted user code), and Selene/luau-lsp give AST-level linting; a
  scan for forbidden globals is feasible. But our artifact would now be **Luau, not Python**, and
  "build runs / exactly one controlled dynamic body / ≥2 entities / no initial penetration / in
  bounds" all become Luau checks run via `execute_luau` or an Open Cloud task (both can construct
  the DataModel and query it). **Feasible.**
- **G2 goal well-formedness (pure predicates, false at t=0, purity):** *Portable.* `success`/
  `failure`/`checkpoints` become pure Luau functions reading the DataModel; purity = call twice,
  compare snapshots. Runnable headless in Open Cloud (no physics needed for t=0 evaluation).
  **Feasible.**
- **G1 rollout sanity + agency + action-efficacy + DETERMINISM:** *Barely portable, and only in
  Studio.* Needs an actual rollout (NaN/escape checks, noop-baseline vs action-held divergence)
  AND a determinism check (two identical seeded runs → identical final snapshot). Rollouts need
  physics ⇒ Studio only (Open Cloud can't). The **determinism sub-check is the one Roblox cannot
  honestly pass** — no seed, only conditional reproducibility, floating-point caveats. We'd have
  to *weaken* it to an approximate/tolerance comparison, losing the crisp guarantee.
- **G3 random-search solvability (E=40 × H=120 + E2=20 beam pass, first-success witness):**
  ***This is where it fails.*** G3 needs **~60 seeded rollouts × 120 ticks × 6 steps ≈ 43k
  physics steps per candidate, cheaply, deterministically, in parallel, in seconds.** On Roblox:
  play mode is real-time (60 episodes × ~13 s ≈ **13 min serial**, non-deterministic, one Studio);
  Open Cloud has no physics; the StepPhysics plugin loop is the only fast path but is single-
  Studio, GUI-bound, plugin-context, and still not seed-exact. **Many fast deterministic rollouts
  — the beating heart of our verifier — is not feasible at Roblox scale without abandoning the
  native engine.** The witness-replay-as-GIF also loses its "deterministic replay reproduces the
  exact certified trajectory" property once physics isn't seed-exact.

**Net:** G0 and G2 port; G1 ports only in a degraded (non-deterministic, Studio-bound) form;
**G3 does not port.** Since G3 is our solvability certificate, verification transferability is
**low**.

## 5. Constraints (Windows, accounts, ToS, licensing, headless)

- **Windows workflow:** *Good.* Studio is native Windows/Mac; the built-in MCP runs as a local
  process and quick-connects to Claude Code on Windows. Rust standalone server also builds on
  Windows (PowerShell scripts in-repo). No blocker.
- **Accounts / publishing:** a Roblox account logged into Studio is required for the MCP path.
  The **Open Cloud path additionally requires an API key and a *published* place** (you pass
  `universeId`/`placeId`) — so you must own/publish an experience and manage keys; least-
  privilege key policy applies. Assets from `insert_model`/Creator Store carry their own
  licensing.
- **ToS on automation:** *Developer-tooling automation is explicitly sanctioned* — Open Cloud
  ("build command-line automation tools", CI/CD) and the MCP/playtest automation are official
  products. Guardrails: HTTP 429 rate limits, per-owner key limits, and an Open Cloud policy
  update enforcing *principle of least privilege* (only necessary endpoints, Community Standards
  / Terms of Use for player/experience data). **What is NOT sanctioned:** patched clients /
  RCC self-hosting / synthetic input into the *player* client outside the dev tools — i.e. the
  only headless-physics routes are the ToS-violating ones.
- **What cannot be done headlessly (sanctioned):** run the **physics simulation** (Open Cloud
  = DataModel/compute only). Render/pixels only exist in Studio GUI (`screen_capture`). Save
  place edits from Open Cloud (not yet). So: **no headless simulator, no headless rollouts, no
  headless faster-than-real-time.**

## 6. Candidate framework sketch (IF we chose Roblox) + critical verdict

### Architecture (least-bad configuration)
The only configuration that satisfies build + controlled-step + state-read is a **Studio-in-the-
loop plugin driver**, with Open Cloud used for the parts that don't need physics.

```
  Harness runner (Python, our side)
        │  MCP (stdio)                         Open Cloud (HTTPS, headless, NO physics)
        ▼                                              │
  Built-in Studio MCP  ──►  execute_luau  ──►  Studio (edit mode, GUI, plugin ctx)
        ▲                        │                     ├─ G0 static: load DataModel, symbol/shape checks
        │  JSON results          ▼                     └─ G2 goal: purity + false@t0 (physics not needed)
        │            "Harness driver" Luau plugin loop:
        │              build() → world of Parts/Constraints
        │              repeat decision tick:
        │                 apply action (impulse/velocity/flag)      ← leg d
        │                 for K=6: WorldRoot:StepPhysics(1/60)      ← leg b (plugin-only!)
        │                 latch checkpoints(); eval failure/success ← leg c
        └──────────────  return episode dict (positions, contacts, checkpoints, verdict)
```

- **Where the harness's runner sits:** our Python runner stays authoritative and orchestrates,
  but the *actual `run_episode` inner loop moves into a Luau "harness driver" plugin* that calls
  `StepPhysics` in edit mode (the only place a controlled step exists). The generated game is a
  Luau module (`build/act/on_step/success/failure/checkpoints`) synced via Rojo. G0 and G2 can
  additionally run headless on Open Cloud for parallelism.
- **Data flow:** prompt → Claude generates Luau game module → Rojo sync / `multi_edit` into
  Studio → G0/G2 via Open Cloud + `execute_luau` → G1/G3 rollouts via the plugin StepPhysics loop
  → episode dicts returned as JSON → witness stored → replay via `screen_capture` in play mode.

### Critical verdict — is the loop tight enough?
**No, not for our purpose.** The loop is tight and delightful for a *human/agent co-development*
workflow (build a scene, tweak scripts, run one playtest, read the console, screenshot) — that is
exactly what Roblox built and it is genuinely best-in-class for Claude integration. **But our
harness is not a co-development tool; it is a verifier that lives on cheap, deterministic,
headless, faster-than-real-time seeded rollouts.** Roblox denies all four at once:

1. **Not headless for physics** — Open Cloud runs no simulation; physics needs the Studio GUI.
2. **Not deterministic to a seed** — conditional reproducibility only, no physics seed, FP/
   cross-machine caveats, live non-determinism bugs. Breaks G1's determinism check and witness-
   replay guarantees.
3. **Not faster-than-real-time** except through a GUI-bound, single-instance plugin StepPhysics
   loop. Breaks G3's ~60-rollout probe economically.
4. **Not parallel at rollout scale** — one physics-capable Studio per machine (Open Cloud's 10
   concurrent tasks have no physics).

**Where it breaks down, precisely:** at the transition from "verify the game *is well-formed*"
(G0/G2 — portable) to "verify the game *is solvable* by many fast random rollouts" (G1 determinism
+ G3 — not portable on the native engine). To force it, we'd reimplement a deterministic
fixed-step physics engine in Luau and *not use Roblox physics at all* — at which point Roblox is
just a renderer and we've thrown away the reason to use a real engine.

**Constructive alternative if Roblox becomes a goal anyway:** treat it as an **output/showcase
target, not a verification substrate** — certify games in our pymunk harness (keep G0–G3 there),
then *port certified designs* to Luau/DataModel and do a single real Studio playtest (via MCP
`start_stop_play` + `execute_luau` state reads) as a "runs on a real engine" demo. This keeps our
tight loop and buys the "real engine" credential without pretending Roblox can host the probe.

---

## Loop scorecard (1–5)

| Dimension | Score | One-line justification |
|---|---:|---|
| **State-read** | **5** | `execute_luau` / Open Cloud return arbitrary DataModel state (CFrame, velocity, contacts, events) as JSON — pure engine state, zero pixels, superset of `World.query`. |
| **Action-inject** | **3** | Impulse/velocity/flag writes per tick work in a plugin loop; but the sanctioned path is real-time synthetic input, and there's no clean fixed-cadence `act()` at step level outside a GUI-bound plugin. |
| **Determinism** | **2** | No physics seed; only *conditional* reproducibility (`PhysicsSteppingMethod=Fixed`, same machine); FP/cross-machine not guaranteed; live 2026 non-determinism bugs — far weaker than pymunk's bit-exact seeded replay. |
| **Speed** | **2** | Play mode is strictly real-time (no time-scaling); ~13 s per 800-step episode; faster-than-real-time only via a single GUI-bound StepPhysics plugin loop. |
| **Headless** | **2** | Open Cloud runs DataModel/Luau headless but **no physics**; physics/replay need the Studio GUI; official headless game-server (RCC) isn't public; the only headless-physics options are ToS-violating patched clients. |
| **Maturity** | **4** | Built-in Studio MCP + Open Cloud Luau Execution + StepPhysics are all real, documented, Roblox-supported and actively developed (2024–2026) — but built for dev-assist/CI, not RL rollouts. |

**Overall: legs (a) build and (c) read are 5/5; legs (b) step and (d) inject-at-scale collapse
for our G1/G3 needs. Verdict: excellent Claude-integrated build-and-playtest engine, wrong
substrate for our fast-deterministic-headless verification loop. Use pymunk for verification;
consider Roblox only as a downstream "real engine" showcase for already-certified designs.**

## Primary sources

- Official standalone server (archived): https://github.com/Roblox/studio-rust-mcp-server
- Built-in Studio MCP docs: https://create.roblox.com/docs/studio/mcp
- Built-in MCP + playtest automation announcement: https://devforum.roblox.com/t/assistant-updates-studio-built-in-mcp-server-and-playtest-automation/4474643
- Open Cloud Luau Execution docs: https://create.roblox.com/docs/cloud/reference/features/luau-execution
- Open Cloud Luau Execution beta thread (limits, "no physics" staff answer): https://devforum.roblox.com/t/beta-open-cloud-engine-api-for-executing-luau/3172185
- `WorldRoot:StepPhysics` reference (Plugin Security, not runtime): https://create.roblox.com/docs/reference/engine/classes/WorldRoot#StepPhysics
- Step Forward Physics (Studio-only) announcement: https://devforum.roblox.com/t/introducing-step-forward-physics/3094522
- New StepPhysics Plugin API: https://devforum.roblox.com/t/new-stepphysics-plugin-api/3093140
- "Making Roblox's engine deterministic somehow?" (conditional determinism, no seed): https://devforum.roblox.com/t/making-robloxs-engine-deterministic-somehow/3136010
- BindToSimulation & UseFixedSimulation full release: https://devforum.roblox.com/t/full-release-bindtosimulation-rolling-out-usefixedsimulation/4605516
- Live non-determinism bug (2026): https://devforum.roblox.com/t/recent-physics-change-causing-nondeterministic-constraintcollision-behavior-live-game-affected/4618232
- Community MCP (43 tools, archived): https://github.com/boshyxd/robloxstudio-mcp
- Lune standalone Luau runtime: https://github.com/lune-org/lune
- TestEZ docs: https://roblox.github.io/testez/
- Open Cloud rate-limit / API-key policy: https://github.com/Roblox/creator-docs/blob/main/content/en-us/cloud/reference/rate-limits.md , https://devforum.roblox.com/t/changes-to-open-cloud-api-usage-policies/3671058
