# Game engine integrations — synthesis (2026-07-13)

> Exploratory research for pyramid rung 4 ("real 2D game engines"). Detailed,
> source-verified analyses live in `notes/engines/` (youtube_scan.md, roblox.md,
> godot.md, field.md — each with primary sources and per-engine scorecards).
> Evaluation criterion (Elias): the ENGINE ↔ AGENT FEEDBACK LOOP — machine-readable
> state out, actions in, deterministic, fast, headless. The tighter the link, the
> easier the harness port.

## The structural finding (changes how we read everything else)

**The entire "Claude + game engine" ecosystem is an AUTHORING layer, not an
environment-loop layer.** Every existing integration (official Roblox Studio MCP,
CoplayDev unity-mcp 12.4k★, godot-mcp 4.7k★, Epic's experimental UE 5.8 MCP)
helps an LLM write game code and drive an editor. **None ships a typed,
deterministic, per-tick state→action→state loop over a running game.** Their
feedback degrades: structured play-mode output → compile/test results → console
stdout → screenshots → nothing.

Consequences: (1) porting to any engine means BUILDING the loop ourselves on top
of it; (2) our harness — which already has that loop, plus universal oracles and
witness replay — sits a full layer below this crowd. That is the differentiator
to state plainly in the GI submission.

## The matrix

| Loop dimension (1-5) | pymunk (now) | **Node: Planck/Matter** | **Godot (+Rapier)** | Roblox | Unity | Unreal |
|---|---|---|---|---|---|---|
| State-read (no pixels) | 5 | 5 | 5 | **5** | 3 | 2 |
| Action-inject per tick | 5 | 5 | 5 | 3 | 2 | 2 |
| Determinism (seeded) | 5 | 5 | 2 stock → 4 Rapier | 2 | 2 | 2 |
| Speed (≫ realtime) | 5 | 5 | 3 (boot tax; batch!) | 2 (realtime only) | 2 | 2 |
| Headless | 5 | 5 | 5 (first-class) | 2 (**no physics headless** — staff-confirmed) | 3 | 3 |
| Sandboxing generated code | 5 (AST+subprocess) | 4 (locked child process) | 2 (**open problem** for GDScript) | 3 (Luau is sandboxed by design, but env is Studio) | 1 | 1 |
| Claude-integration maturity | n/a (ours) | 3 | 4 | 4 | 4 | 3 |
| "Real engine" credential | low | **medium-high** (Matter = Phaser's physics) | high | high | high | high |

Sleepers/fallbacks: **Defold** (official headless, deterministic Box2D, Lua — zero
Claude glue today); **Bevy+Rapier** (only bit-exact cross-platform determinism —
pays rustc-in-the-loop). Skip for the loop: PICO-8/TIC-80, LÖVE, Kaboom, PixiJS.

## The recommendation ladder

1. **Now (base-of-games campaign): stay on pymunk.** No engine gives the campaign
   anything it lacks, and all cost loop quality or effort.
2. **Rung-4 step 1 — Planck.js (or Matter.js) in pure Node.** The only "real
   engine family" step that keeps the loop AT PARITY with today (5s across the
   board): `world.step()` in a tight loop, JS-object state, trivially headless,
   faster than realtime, fixed-timestep determinism documented. Bonus: a second
   generation LANGUAGE (JS) tests the harness's generality, and the browser/
   Phaser+CDP pattern (GameGen-Verifier's validated architecture) becomes a free
   on-ramp for visual demos.
3. **Rung-4 step 2 — Godot, deliberately, WITH Rapier from day one.** Richer
   engine, first-class headless, MIT everywhere, structurally clean port
   (`_physics_process` ↔ our runner; oracles stay in Python eating JSONL).
   Three taxes: stock-physics non-determinism (open issue #112976 → Rapier swap),
   per-boot latency (batch all G3 episodes in ONE `--script` invocation, never
   per-episode), and no turn-key sandbox for generated GDScript (OS-level
   isolation needed). Gate the decision with the ½-day spike: measure boot time,
   episodes/sec, and same-seed snapshot equality stock vs Rapier. ~3-5 weeks to parity.
4. **Roblox — downstream SHOWCASE, not substrate.** Best-in-class state-read and
   scene-build via official tooling, but physics never runs headless, is not
   seed-deterministic, and play-mode is realtime-locked: G3 (≈60 seeded rollouts
   in seconds) would become ~13 serial minutes, non-deterministic, GUI-bound.
   Right use: certify games on our substrate, then port certified designs to
   Luau/DataModel for a single real Studio playtest — the "runs on a real engine"
   credential without pretending Roblox can host the probe.
5. **Unity / Unreal: skip for the loop.** Mature authoring MCPs, but play-mode
   control is roadmap-"not planned" (Unity) / editor-bound (Unreal), physics
   non-deterministic, code unsandboxed. Watch item: UE builds can host an MCP
   server in shipping games (`IModelContextProtocolModule::StartServer()`) — the
   one first-party seam pointing toward running-game agent loops.

## Cross-cutting lessons (apply to OUR harness now)

- Practitioner systems that work converge on exactly our choices: binary
  pass/fail (not scores), read state not pixels, state-based waits (not sleeps),
  and inject-state + bounded-interaction + assert instead of making the agent
  play the whole game. Keep these; say so in the submission.
- **Small, verb-shaped APIs beat big ones** (Phaser Studio's agent-design cue):
  a large tool surface degrades the model. Bears directly on `World`'s surface
  and the generator prompt — resist API growth during the lessons-harvest phase.
- Star counts measure authoring popularity, not loop quality. Evaluate engines
  by the four loop legs (build / step / read / inject), never by ecosystem size.
