# MCP feedback tools for the repair loop — verified report

> 2026-07-15. Question (Elias): can godot-mcp's "Project Analysis" and "Capture
> Debug Output" — or gd-agentic-skills' scene-builder MCP — give the designer
> model engine-level feedback the loop currently lacks (FEEDBACK_LOOP.md)?
>
> Method: 5 research lanes (source deep-read, hands-on probes on the real serve
> host, upstream archaeology, harness seam map, ecosystem sweep), each
> adversarially re-verified. Every empirical claim below was executed on the
> production stack: Godot 4.7.stable.official.5b4e0cb0f inside
> `/home/enaha/gi/gi-certifier.sif`. Skeptic refutations are folded in as
> corrections (§6). Probe artifacts: session scratchpad
> `/tmp/claude-101528/-home-enaha/b3a56d31-1997-4c5d-ac99-b63fa19ec67d/scratchpad/`
> (`godot-mcp/` clone, `debugprobe/`, `gdprobe/`, `lspprobe/`, `claimtest/`,
> `gdskills-history.git`) — session-scoped, disposable.

## 0. Verdict in one table

| Capability | Decision | How | Loop stage |
|---|---|---|---|
| Runtime SCRIPT ERROR capture ("Capture Debug Output" *concept*) | **ADOPT — mechanic, not the MCP server** | read the stderr tee we already own (`os.pread` deltas, 3 spawners) | feedback compiler → repair/revise directives; fixes two misleading hints |
| G0 parse capture (multi-error + line numbers) | **ADOPT — fix our own `_parse_error_line`** | keep `at: …:<line>` lines; keep all analyzer-error blocks | G0 hint |
| Engine-truth scene-tree geometry | **ADOPT — serve `check`-op extension** | pure-ADD `engine_geometry` block; re-audit frozen host | G0.5 walled-off directive |
| Full lint (warnings, all parse errors, structured) | **ADOPT LATER (2nd wave)** | headless GDScript LSP sidecar (Godot core, measured working) | G0 hint upgrade |
| godot-mcp `get_project_info` ("Project Analysis") | **SKIP** | it is file-extension counting; zero engine facts | — |
| godot-mcp server itself; any MCP server in-funnel | **SKIP** | broken headless as shipped; no Node.js on host or image; bypasses env scrub + determinism pins | — |
| Scene CRUD / `run_project` / screenshots / DAP | **SKIP** | no seam: scene-less one-file artifact; serve host is a strictly stronger run-project | — |
| gd-agentic-skills scene-builder MCP | **SKIP — it never existed as software** | prompt doc for a fabricated npm package; upstream deleted it | — |

---

## 1. Coding-Solo/godot-mcp — detailed report

Source: clone at `<scratchpad>/godot-mcp/`, HEAD `1209744` (2026-04-16, merge of
PR #99 `fix/rce-arbitrary-script-instantiation`). The entire server is **one
TypeScript file** (`src/index.ts`, 2,221 lines) plus **one bundled GDScript
bridge** (`src/scripts/godot_operations.gd`, 1,186 lines, `extends SceneTree`).
No editor plugin, no socket protocol, never talks to a running editor.

### 1.1 Repo facts

- MIT license. ~4,728 stars. npm: **scoped** `@coding-solo/godot-mcp` 0.1.1
  (2026-02-03) — the *unscoped* npm `godot-mcp` is an unrelated project
  (craigsteyn), do not confuse them.
- Last push 2026-04-16; **58 open issues+PRs** (GitHub's counter includes PRs);
  issue #119 "Is this project still active?" unanswered since 2026-06-21.
  Effectively unmaintained.
- Deps: `@modelcontextprotocol/sdk` pinned 0.6.0 (very old), `axios` never
  imported (dead dep). Issue #118: CVEs on both.
- **Hard blocker for us regardless of merit: no Node.js exists on the login
  node or inside gi-certifier.sif** (verified `which node` fails in both). Any
  Node-based MCP server requires adding a toolchain first.

### 1.2 Architecture — three mechanisms

1. **Pure Node `fs`** (engine never runs): `list_projects`, the structure part
   of `get_project_info`, existence pre-checks.
2. **One-shot CLI** via `execFileAsync` (`executeOperation`, index.ts:484-543):
   `godot --headless --path <proj> --script godot_operations.gd <op> <json>`.
   No timeout, 1 MB `maxBuffer` default. The bridge is fully standalone
   (verified: invoked it in-image with no MCP server; `create_scene` worked on
   4.7, `.tscn` written).
3. **Long-lived `spawn`**: `launch_editor` (`-e`) and `run_project`
   (`-d --path`, **no `--headless`**), stdout/stderr line-split into in-memory
   arrays on a single global `activeProcess` (one game per server instance —
   incompatible with our parallel G1–G3 gates).

### 1.3 Full tool inventory (14 tools)

| # | Tool | Mechanism | Headless-viable in our env? | Value for our loop |
|---|---|---|---|---|
| 1 | `launch_editor` | spawn `godot -e` | No (GUI; also fire-and-forget — reports success even if it dies, #106) | None |
| 2 | `run_project` | spawn `godot -d --path` (index.ts:1090) | **Broken as shipped** — no `--headless`; verified instant `Unable to create DisplayServer` death | None; serve host is strictly stronger (seeded `build`, action injection, `--fixed-fps 60`, typed frames) |
| 3 | `get_debug_output` | read in-memory buffers of `activeProcess` | Only while the process is alive (§1.5) | Concept yes, implementation no |
| 4 | `stop_project` | `activeProcess.kill()` | Same caveat | None |
| 5 | `get_godot_version` | `execFile --version` | Yes (verified) | Trivial |
| 6 | `list_projects` | Node `fs` scan | Yes (no engine) | None (we know our one project) |
| 7 | `get_project_info` | Node `fs` walk + `--version` | Yes, but shallow (§1.4) | **~Zero** (§1.4) |
| 8 | `create_scene` | bridge (B) | Yes (verified; benign noisy `set_owner` ERROR on 4.7) | None — our lane has no scenes |
| 9 | `add_node` | bridge (B) | Yes | None; also has a false-success defect (§1.6) |
| 10 | `load_sprite` | bridge (B) | Conditional — texture needs prior editor import (#103); pure headless projects never ran the importer | None |
| 11 | `export_mesh_library` | bridge (B) | Yes | None |
| 12 | `save_scene` | bridge (B) | Yes | None |
| 13 | `get_uid` | bridge (B); gated ≥4.4 | Yes; the only op that prints structured JSON | Template pattern only |
| 14 | `update_project_uids` | bridge (B) | **Broken** (#102, source-confirmed): TS passes an absolute path, GD prepends `res://` → `res:///home/...` → finds nothing, reports success | None |

### 1.4 Deep dive — "Project Analysis" (`get_project_info`, index.ts:1381-1477)

**It is `find | wc` plus `godot --version`.** Three steps, engine never opens
the project:

1. `godot --version` — the only engine call (index.ts:1430; no `--path`).
2. A recursive `readdirSync` walk that **counts files by extension** into
   `{scenes, scripts, assets, other}` (index.ts:1321-1377). Counts only — no
   file names, node trees, autoloads, settings, or dependencies. A
   syntactically broken `.gd` still counts as `scripts+=1`.
3. Project-name extraction is **dead code in the shipped build**: `const fs =
   require('fs')` (index.ts:1438) inside an ESM package (`"type":"module"`) →
   `ReferenceError` → swallowed by try/catch → name always falls back to
   `basename(projectPath)`. Verified empirically (equivalent `.mjs` reproduced
   `ReferenceError: require is not defined` + silent fallback).

It also refuses to run without a `project.godot`. Our generated artifact is one
scene-less, resource-less `.gd` compiled **in-memory** over TCP
(`serve_game.gd:535`, `_compile_source` :245) — it never exists inside any
project. The only real project is the frozen host `godotworld/`. So
`get_project_info` could only ever describe the host, in four integers.

**Correction carried from verification:** its outputs (version string,
extension counts, dir name) are *disjoint-and-irrelevant* to our serve `check`
facts, not a "subset" — but nothing in them feeds any loop stage either way.

### 1.5 Deep dive — "Capture Debug Output" (`run_project` → `get_debug_output`)

Mechanism: spawn `godot -d --path <proj>` with `stdio:'pipe'`; push line-split
chunks into `output[]`/`errors[]`; return raw text as JSON. No parsing, no
error classification, no exit code, **no size cap**.

Three fatal defects in our environment, all verified:

1. **No `--headless` on the spawn** (index.ts:1090). In our display-less
   container the process dies in ~2 s at `ERROR: Unable to create
   DisplayServer, all display drivers failed.` The flagship feature is a
   guaranteed dead-end without patching the source.
2. **Output destroyed on exit** (index.ts:1117-1122): the `exit` handler nulls
   `activeProcess`; the buffers are reachable only through it. Any crash or
   quick completion — including the DisplayServer death above — makes the
   captured output permanently unretrievable. The agent can't even read the
   error that killed it.
3. **Hardcoded `-d` → stdin-blocking debugger REPL.** Verified on 4.7 (with
   `--headless` patched in): every parse *and* runtime error drops into a
   `debug>` prompt that blocks on stdin; Node's pipe holds stdin open, so the
   process hangs frozen until killed. Perversely this is the only reason the
   buffers survive — but the game never advances and an unattended loop stalls.

**Corrected stderr taxonomy** (skeptic refutation applied — the original claim
that parse errors hit stderr "either way" was wrong):

| Mode | Parse error | Runtime error |
|---|---|---|
| `-d` (godot-mcp's hardcoded mode) | **stderr is empty (0 bytes)**; only diagnostic is stdout `Debugger Break, Reason: 'Parser Error: …'` + `debug>` hang | stderr gets `SCRIPT ERROR: …` + `GDScript backtrace`, then `debug>` hang |
| no `-d` (what we should use) | stderr: `SCRIPT ERROR: Parse Error: … at: GDScript::reload (res://file.gd:LINE)` + `ERROR: Failed to load script` | stderr: `SCRIPT ERROR: <msg>` + `GDScript backtrace`; execution continues |

Any monitor keyed on stderr `SCRIPT ERROR` would miss parse errors precisely in
the mode this server hardcodes. For headless automation `-d` is strictly
counterproductive.

### 1.6 Reliability defects (why not to trust it even patched)

- Success detection for all bridge ops is the string test
  `stderr.includes('Failed to')` (7 sites). Verified false-success: `add_node`
  with a missing parent prints `Parent node not found` + a SCRIPT ERROR, adds
  nothing, and the tool reports "added successfully". **Worse than originally
  reported: the process also exits 0** (the bridge's `quit(1)` only flags the
  SceneTree; execution falls through and aborts) — even a correct exit-code
  check would report success.
- `validatePath` only rejects `..` — no root confinement. The last commit was
  an RCE fix (arbitrary script instantiation). Exec surfaces of this class must
  never point at generated content (consistent with
  `GODOT_AI_TOOLING_AUDIT.md`).

### 1.7 Reuse without MCP

Nothing in the operations depends on the MCP SDK. Each is ~30 lines of
validation around `execFile(godot, ['--headless','--path',proj,'--script',
godot_operations.gd, op, jsonParams])` — the exact invocation family our
harness already implements (`harness/verify/godot_exec.py` `stepping_argv`,
`harness/verify/capture.py`; `gd_exec.py` is the `--check-only` variant). MIT
permits vendoring the `.gd` bridge, but its operations (scene CRUD, sprites,
mesh libraries) target interactive editor workflows we don't have. The durable
takeaways are **patterns**: the headless `--script` bridge idiom (we already
own it) and the stderr taxonomy above.

---

## 2. gd-agentic-skills "scene-builder MCP" — archaeology

Pinned clone: `/home/enaha/GI/gd-agentic-skills` @ `e9e20ff` (v0.0.8). Full
history recovered via blobless clone (`<scratchpad>/gdskills-history.git`,
37 commits).

### 2.1 What it actually was

**A prompt document, not software.** `godot-mcp-scene-builder` (deleted;
verbatim survivor: `skills/godot-master/references/mcp-scene-builder.md`) was a
4-tool orchestration recipe (`mcp_godot_create_scene` → `add_node` →
`load_sprite` → verify via `run_project`), a 5-rule NEVER-list, and two inert
GDScript assets (`skills/godot-master/scripts/mcp_scene_builder_*.gd` — a
declarative manifest Resource whose own comment admits creation is
"Pseudo-code", and an EditorScript scaffolder). Its sibling `godot-mcp-setup`
(survivor: `references/mcp-setup.md`) was Claude Desktop install instructions.
Neither contains a line of server code (removal commit `6cb0843`
`--name-status` confirms: SKILL.md + mcp_reference.md + 4 `.gd` files, nothing
else).

### 2.2 The server it assumed: a hallucination wearing Coding-Solo's tool names

- **What it told users to install:** `npx @modelcontextprotocol/server-godot`,
  linking `modelcontextprotocol/servers/tree/main/src/godot`. **Both
  fabricated** — npm returns 404 (re-verified), and neither the official
  servers repo nor `servers-archived` ever contained a Godot server.
- **What the tool names match:** Coding-Solo/godot-mcp's 14 tools and camelCase
  params, by *name*. Precision correction from verification: the match is
  names-only — the skill's param tables drop required params (`projectPath`
  everywhere, `add_node`'s `nodeName`), i.e. it was written from a lossy
  description, not against the real server. And `godot-master/SKILL.md:281`
  (still live, marked MANDATORY) requires `mcp_godot_get_scene_tree` — **not a
  Coding-Solo tool** (exists in addon-style servers like ee0pdt/Godot-MCP).
  Plus a fictional `MCP_ACCESS_KEY` env var in the diagnostic script.
- GitHub search for `godot-mcp-scene-builder`: 0 repos. **There is no server by
  that name anywhere. Nothing exists to adopt.**

### 2.3 Why it was removed (timeline)

| Date | Event |
|---|---|
| 2026-02-07 | v0.0.1: both MCP skills present from day one |
| 2026-05-20 | `6cb0843` v0.0.7: **both skill dirs deleted** in a single 762-file release commit (single parent; "squashed" is an inference). Same commit adds trimmed NEVER-list stubs under `godot-auditor/references/categories/mcp-*.md` and the persona skills |
| 2026-06-13 | Issue #2 (still open, 0 comments): user hits the npm 404 *via the surviving godot-master mirrors* — cleanup was incomplete; Workflow 11 still routes to the dead flow at pinned HEAD. (Issue #1 is a 404 — a deleted earlier report can't be ruled out) |
| 2026-07-07 | v0.0.8: 4.7 sweep didn't touch the dead mirrors |

Only stated rationale: CHANGELOG v0.0.7 — "Retired the **defunct** MCP Builder
and MCP Setup skills in favor of direct construction and compilation using the
Godot CLI." Read with the npm 404: the skills were removed because **they never
worked — the server they installed doesn't exist** — and the maintainers
pivoted to what they could execute and test.

### 2.4 What replaced it: the direct-CLI pattern

`skills/godot-builder/scripts/` reimplements **all 14 Coding-Solo tool names as
local Python scripts** (a superset: +~11 asset/CI tools). Mechanism
(`base.py`): build `[godot, --headless, --path <proj>, …]` via
`subprocess.run(capture_output=True)`; for scene ops, write a temp
`extends SceneTree` worker `.gd` and run `godot -s <worker>`. Verified live
in-image: `create_scene.py` works on 4.7 (functional, not byte-identical —
quirk: unnamed root node). Their `get_project_info.py` is 11 lines (parse
`project.godot`); `get_debug_output.py` is 12 lines (`godot -v --log-file`).
Quality caveats: naive f-string GDScript codegen (no escaping), raw prints, no
timeouts.

The audit half (`godot-auditor`) never needed MCP: 13 static-analysis scripts +
a notable idiom — **zero-touch scene introspection via
`PackedScene.get_state()`** (no `@tool` side effects during audit).

**Relevance:** their trajectory — MCP wrapper (broken) → headless CLI + temp
workers + captured stdout/stderr — converges on what
`harness/verify/godot_exec.py`/`capture.py` + the serve host already do, and
gi-harness is further along (length-prefixed JSON over TCP vs raw prints;
determinism/solvability gates vs "run it and look"). This is independent
validation of our architecture, not a source of new capability.

---

## 3. Delta vs what the harness already captures

### 3.1 The load-bearing facts about today's pipeline

- **The stderr channel already exists and is thrown away.** All **three**
  spawners tee the host's stdout+stderr into an anonymous tempfile:
  `gd_exec.py:118-120`, `godot_env.py:306-308`, **and
  `godot_vec_env.py:100-101`** (the third was missed in the first pass).
  `_read_log()` is called only on fatal aborts (spawn stall, `gd_dead`/
  `gd_stale`, write failure), returns only the **last 2,000 bytes**, and the
  file is closed unread on every healthy run. Even the fatal-path tail only
  reaches a `VERIFY_ERROR` report that `_repair_loop` deliberately never
  repairs from ("never repair blind").
- **The wire cannot carry runtime errors.** `"error":null` is hardcoded in both
  frame serializers (`serve_game.gd:929`, `:957`); the `check` op **hardcodes**
  `build: {ok:true, error:null}` (`serve_game.gd:1075-1077`); the comment at
  `:306-307` claiming a bad build "surfaces as an error frame" is false for
  runtime errors — GDScript has no exceptions; the function silently aborts.
- **Verified consequences on the real serve host** (probes in
  `<scratchpad>/debugprobe/`, re-run by the skeptic):
  - Null deref in `act()` (line 93): every wire frame healthy; G0 fully green;
    first tripping gate is G1 with the *symptom* hint `dead action(s) with no
    effect on the world: up` (`gameverify.py:921` at HEAD 9c24f7e). The tee'd
    stderr holds the *root cause*: `SCRIPT ERROR: Invalid access to property or
    key 'position' on a base object of type 'Nil'. at: act (…:93)` + full
    backtrace. Line numbers in the synthetic `gdscript://<hash>.gd` name map
    1:1 to the generated source — the compiler can quote the exact line.
  - Runtime error in `build()` (line 42): `init` returns `ok:true` with empty
    state; G0's hint is the **misleading** `exactly one controlled dynamic body
    required (found none)` (`gameverify.py:890`) — a design directive for a
    crash defect. Stderr's FIRST error block names `build:…:42` exactly;
    4–8 cascade errors follow. Design rule: surface the **first N** blocks,
    deduped, never the tail (the current 2,000-byte tail is dominated by
    cascade/warning spam — measured ~1.66 KB/tick with one `push_warning` per
    physics frame; `print`/`push_warning` are not banned by `gd_gate.py`).
- **G0 parse capture drops information.** `_parse_error_line`
  (`gd_exec.py:36-51`) keeps only the first matching line and discards the
  companion `at: … (res://file.gd:LINE)` line — the hint has no line number.
  Corrected scope (skeptic): `--check-only` reports only the first error for
  tokenizer/parser *panic* errors, but emits **all analyzer-stage errors at
  once** (still labeled "Parse Error", each with its own `at:` line) — e.g. two
  undeclared identifiers → four blocks. It **does** emit analyzer errors; it
  never emits warnings. So the fix is multi-block capture, not just keeping one
  `at:` line.
- **G0.5 occupancy is entirely self-reported.** `_op_check` geometry copies the
  game's own `aabb`/`half_extents`/`radius` declarations verbatim; grep for
  `PhysicsServer`/`CollisionShape`/`get_shape` in `serve_game.gd`: zero hits;
  `penetration` is an empty stub. A game with real walls that omits footprints
  gets a vacuous G0.5 pass; mis-declaration is undetectable
  (`reachability.py:196-219`).

### 3.2 DELTA TABLE — MCP capability → loop stage → have / new / cheapest wiring

| MCP capability | Feeds stage (FEEDBACK_LOOP.md) | Already captured | Genuinely NEW | Cheapest wiring | Flags |
|---|---|---|---|---|---|
| **Debug-output capture** (`run_project`+`get_debug_output` idiom) | feedback compiler: new runtime-error directive class; enriches in-loop repair + post-cert revise context | The tee exists (3 spawners) but is read only on fatal aborts, tail-2000, then deleted; wire `error` hardcoded null | **YES — the one real gap.** Per-episode runtime `SCRIPT ERROR` text with exact line + backtrace, currently invisible | **Not MCP — read the tee we own.** `os.pread` offset-deltas per op/episode in all 3 spawners (dup'd fd shares offset with the child — `seek/read` races); parse `SCRIPT ERROR` blocks, keep first N deduped by (message,line) + counts, drop/count WARNING spam; attach `rec["console"]`/facts → `_repair_user_msg`/`_revise_user_msg`; let `run_g0_gd.builds` consume build-scoped errors | Determinism safe (read-only, Python-side). Batched RL mode (N SubViewports, one process): interleaved text → run-level diagnostics only; per-episode attribution in single-instance verify mode |
| **Project analysis** (`get_project_info`) | none | Structure facts already arrive typed from `_op_check` (contract methods, actions, entities, geometry, world_size); artifact has no project, scenes, or resources (`load`/`preload` banned, `gd_gate.py:88-91`) | Essentially nothing (its actual outputs are irrelevant to any stage) | None. At most a dev-shell one-off against the frozen host | — |
| **Scene-tree inspection** (addon-server idiom, not in Coding-Solo) | G0.5 walled-off directive ("enclosed by static bodies <AABBs/names>") + new declared-vs-actual mismatch fact | Only self-reported `state()` geometry | **YES — engine-truth geometry** (`_game.get_children()` walk, CollisionShape2D extents / PhysicsServer AABBs) | Serve `check`-op extension: `engine_geometry` beside `geometry`, following the exact "Pure ADD to the check reply" precedent (`serve_game.gd:1122-1123`); `_run_reachability` prefers engine truth | `serve_game.gd` is FROZEN → re-audit required; check-op only, never the act/episode frames (G1 byte-identity) |
| **Live scene editing** (`create_scene`/`add_node`/…) | none | N/A — no scenes in the lane; code is the single source of truth | Nothing compatible: world edits desync source from certification, break G1 | Reject; repairs round-trip as source edits via `_revise_user_msg` (existing design) | Exec surfaces are RCE-class near generated content (`GODOT_AI_TOOLING_AUDIT.md`) |
| **Run-project** | none | Serve host IS run-project with structure: seeded build, action injection, K=6 physics frames/tick, `--fixed-fps 60` asserted, byte-identical reruns | Nothing — MCP variant is wall-clock, unseeded, untyped; its one extra (console) is row 1 | None; spawn ownership stays in `GdExecutor`/`GodotServeEnv`/`GodotVecEnv` | An external MCP server spawning Godot bypasses `scrubbed_env` (default-deny allowlist, `godot_exec.py:89-119`, applied only at our 3 spawn sites) and the fixed-fps pin. Keys are env-borne by design (`_resolve_secret`: os.environ wins) — the leak path is live where it matters |
| **Screenshots** | at most revise-context vision | `render.py` GIF from typed state; "No pixels are read from the engine" is the moat | True engine pixels — but no oracle consumes pixels, by design | If ever wanted: render the existing `frames_every` trail offline; real screenshots need a rasterizer inside apptainer — costliest wiring, least oracle value | Serve runs `--headless`; no framebuffer |

---

## 4. Ecosystem alternatives, ranked

Context blocker restated: **no Node.js on the login node or in
gi-certifier.sif** — every Node-based MCP server (Coding-Solo, tugcantopaloglu,
bradypp, erodenn) cannot run on this stack without adding a toolchain. The only
surveyed server runnable on the production image as-is is the pure-GDScript
yurineko73 one (young, v1.0.7-pre1).

**#1 — Headless GDScript LSP sidecar (Godot core, non-MCP). MEASURED working.**
`godot --headless --editor --path <proj> --lsp-port <high-port>` on our exact
binary/image. Reproduced independently with a fresh HOME (default settings):
`didOpen` of a broken file → **5 structured parse-error diagnostics where
`--check-only` reports 1** (parser error-recovery); **warnings** (`UNUSED_*`,
with fix hints) that no CLI flag ever emits; analyzer errors as structured
`{line, severity, message}`. Corrected claim: analyzer *errors* also surface in
`--check-only` — the LSP's unique adds are warnings, full parse-error recovery,
and structure; it is a strict superset of **G0's capture** (first line only).
Persistent connection + `didChange` per repair revision verified — same
lifecycle as the serve host, slots into `GdExecutor` idiomatically (scrubbed
env, loopback, high port). Operational gotcha (corrected): Godot's bind on an
occupied port **fails silently** (verified — zero error lines, client reaches
the squatter); the 601x loopback range is sshd X11-forwarding territory (6/10
occupied at measurement), and the failure mode is accept-then-**hang** or an
X11-proxy error reply, not a fast close. Use high random loopback ports —
harness practice already (`DEFAULT_PORT_BASE = 47000`, `godot_env.py:78`). Cost:
one headless-editor process per funnel worker (RAM unmeasured, expect a few
hundred MB); ~15-30 s startup. Godot core, MIT; proposal #11056 (`--gdscript-lsp`)
makes this path better over time. Seed client: `<scratchpad>/lsp_probe.py`.

**#2 — Serve-host stderr parsing (row 1 of the delta table).** Highest
value-per-effort in the entire sweep; zero new processes, ports, deps. Honest
sizing (corrected from "~90% built"): the capture exists, but per-op
attribution needs new offset bookkeeping (`os.pread`) in three spawners, and
`_read_log`'s 2,000-char tail cannot be the read path.

**#3 — Native structure facts for the funnel:** `--doctool <out>
--gdscript-docs res://` (measured: full typed contract per script — methods
with typed params/returns, members with defaults, signals, constants; skips
broken files gracefully) and/or a ~20-line reflection extension to the `check`
op (`get_script_method_list()` etc., measured). Both cover the legitimate core
of "Project Analysis" — does the file expose the GameAPI contract with the
right shapes — without any server.

**MCP servers, for a future *interactive* authoring lane only (never the
funnel):**

| Server | Notes |
|---|---|
| tugcantopaloglu/godot-mcp (157 tools, v3.1.0 2026-07-13, tested on 4.7, MIT) | Best-featured: headless ops + autoload TCP bridge (a cousin of `serve_game.gd`), `validate_scripts`, `game_get_errors`/`game_get_logs`. Node-based → cannot run here today; young fork, unaudited |
| @satelliteoflove/godot-mcp (v4.1.0, 2026-06-20) | The differentiated one: input injection, deterministic game-time control, live runtime state for agent playtesting — the only MCP capabilities overlapping territory we don't already have in code form (G4-adjacent) |
| yurineko73 Godot MCP Native (v1.0.7-pre1) | Pure GDScript, no Node — the only one runnable on our image as-is; very young |
| ee0pdt/Godot-MCP | Requires the editor GUI running with a plugin — no |
| erodenn/godot-mcp-runtime | Transient autoload bridge pattern worth remembering; Node-based |
| gdUnit4 / GUT | Healthy, maintained, wrong value proposition (assertion frameworks duplicate our oracle) |

If MCP framing is ever wanted so a designer model can call tools
mid-conversation, **wrap our own gates as MCP tools**; do not mount third-party
servers into the loop.

---

## 5. Integration verdict

### ADOPT (extract the mechanic; no MCP server anywhere)

1. **Runtime stderr capture → new feedback-compiler directive class.**
   Wiring: in `GdExecutor` (`harness/verify/gd_exec.py`), `GodotServeEnv`
   (`harness/rl/godot_env.py`), and `GodotVecEnv`
   (`harness/rl/godot_vec_env.py`): record the log offset before each
   op/episode, `os.pread` the delta after (dup'd fd shares the write offset
   with the child — never `seek/read`), extract `SCRIPT ERROR:` blocks with
   game-line numbers (`gdscript://[^:]+:(\d+)`), keep the **first N** deduped
   by (message, line) with occurrence counts, count-don't-keep WARNING spam.
   Attach as `rec["console"]` / `runtime_errors` facts. Consume in three
   places: (a) new directive — *"runtime script error during act('up') at line
   93: Invalid access … — the action aborted every time it fired"* — into
   `_repair_user_msg`/`_revise_user_msg`; (b) `run_g0_gd.builds` consumes
   build-scoped errors so a crashed `build()` stops masquerading as "no
   controlled body"; (c) fatal-abort reports keep first-N blocks instead of
   tail-2000. Attribution: per-episode in single-instance verify mode;
   run-level in batched RL mode. Loop stage: in-loop repair + post-cert revise
   entry (FEEDBACK_LOOP.md §The full loop); this is the concrete "TO BUILD"
   compiler input for the runtime-defect class.
2. **G0 parse hint upgrade** (`_parse_error_line`, `gd_exec.py:36-51`): keep
   the `at: …:<line>` companion lines and **all** analyzer-error blocks (they
   arrive en masse; only panic errors stop at the first). Loop stage: G0 hint.
3. **Engine-truth geometry** in `_op_check` (`godotworld/serve_game.gd`):
   `engine_geometry` pure-ADD block (child walk + CollisionShape2D extents);
   `_run_reachability` prefers it; declared-vs-actual mismatch becomes a new
   structural fact. Respect dimension-awareness (FEEDBACK_LOOP.md §Dimension).
   Precondition: re-audit of the frozen host; check-op only, frames untouched.
   Loop stage: G0.5 walled-off directive.
4. **Second wave (after 1–3 land): LSP sidecar** as `harness/verify/gd_lsp.py`
   (seed: `<scratchpad>/lsp_probe.py`) for the full warning system + parse
   error-recovery in G0. Optional because 1–2 already close the *error* gap;
   this adds *lint*. Scrubbed env, loopback, 47xxx port, one process per
   worker, kill with the funnel.
5. **Dev-time only:** `--doctool --gdscript-docs` contract dump; reflection
   facts in `check`. Not loop stages.

### SKIP (with reasons)

- **Coding-Solo/godot-mcp, the server**: flagship feature broken headless as
  shipped (no `--headless`, verified death), output destroyed on exit, `-d`
  REPL hangs, false-success detection (survives even exit-code checks),
  single global process, unmaintained (~3 months, 58 open issues+PRs, CVE
  report), MCP SDK 0.6.0, **and no Node.js on this stack**. Every useful idea
  in it is a pattern we already own.
- **`get_project_info` ("Project Analysis")**: file-extension counting with a
  dead-code name regex; our artifact has no project; `_op_check` + doctool/
  reflection strictly dominate. Elias's hypothesis is DISCONFIRMED for this
  half — the *adjacent* real gap is engine-truth geometry (adopted as #3).
- **"Capture Debug Output" as a tool**: hypothesis CONFIRMED (the missing
  engine-level runtime feedback is real), tool NOT needed — we already hold
  the stream; adopting the server would trade `scrubbed_env` and the
  `--fixed-fps 60` pin for a weaker copy of it.
- **Scene CRUD / live editing**: no scenes in the lane (`load`/`preload`
  banned); world edits desync source from certification and break G1.
- **`run_project`**: strictly weaker than the serve host.
- **gd-agentic-skills MCP skills**: never software; the install target never
  existed; upstream deleted them as "defunct". Mine `godot-builder/scripts/`
  and the auditor's `PackedScene.get_state()` idiom at most.
- **DAP, screenshots, test runners**: heavier protocol / pixel moat / duplicate
  oracle — no seam.

### Hard flags (unchanged from prior audits, re-verified)

- Spawn ownership stays with our three executors: they alone apply
  `scrubbed_env` (default-deny; env-borne API keys are the primary secret path
  per `_resolve_secret`) and the `--fixed-fps 60` determinism pin.
- Exec-class tool surfaces never point at generated content
  (`GODOT_AI_TOOLING_AUDIT.md`: "mine-technique", "reject wholesale").

## 6. Corrections ledger (skeptic refutations folded in above — do not resurrect)

1. Parse errors under `-d` emit **nothing on stderr**; the
   `SCRIPT ERROR: Parse Error … GDScript::reload` stderr signature is a
   no-`-d` phenomenon (§1.5 table).
2. godot-mcp `add_node` false success also exits 0 — exit-code checks don't
   save it (§1.6).
3. "58 open issues" = issues **plus PRs** (GitHub counter).
4. `--check-only` emits **all analyzer errors** (not just the first); only
   panic parse errors stop early; it never emits warnings. G0's *capture* keeps
   only the first line — that's our bug, not the CLI's (§3.1).
5. `get_project_info` outputs are disjoint-and-irrelevant to check facts, not
   a "subset" (§1.4).
6. **Three** tee'd spawners, not two — `godot_vec_env.py:100-101` included in
   the capture plan (§5.1).
7. Delta reads require `os.pread` (child shares the dup'd fd offset) (§5.1).
8. LSP port gotcha: silent bind failure + client hang/junk-reply (not
   accept-then-close); 601x = X11-forward territory, 6/10 occupied at
   measurement (§4 #1).
9. "Only tugcantopaloglu fits headless" refuted: erodenn/yurineko73 also
   advertise headless, and **no Node-based server can run here at all** (§4).
10. mcp_reference.md matches Coding-Solo by *names only* (drops required
    params); `mcp_godot_get_scene_tree` is foreign/invented (§2.2).
11. The `-s` worker-pattern analog in gi-harness is
    `godot_exec.py`/`capture.py`; `gd_exec.py` is the `--check-only` gate
    (§1.7).
12. `gameverify.py` hint lines at HEAD 9c24f7e: `_hint_g0` :890, dead-action
    :921 (older citations were pre-move).
13. Coding-Solo stars 4,728; npm identity is the **scoped**
    `@coding-solo/godot-mcp` (unscoped `godot-mcp` is unrelated).

## Appendix — evidence & operational note

- Harness files cited: `harness/verify/gd_exec.py`, `harness/verify/gd_gate.py`,
  `harness/verify/godot_exec.py`, `harness/verify/gameverify.py`,
  `harness/verify/reachability.py`, `harness/rl/godot_env.py`,
  `harness/rl/godot_vec_env.py`, `godotworld/serve_game.gd`,
  `harness/gen/gamegen.py`, `harness/render.py` (all under
  `/home/enaha/GI/gi-harness/`), at HEAD 9c24f7e unless noted.
- gd-agentic-skills pinned clone: `/home/enaha/GI/gd-agentic-skills` @ e9e20ff.
- Scratchpad evidence (session-scoped, will vanish): godot-mcp clone @ 1209744,
  `debugprobe/` (serve-host probes + captures), `gdprobe/`+`lspprobe/` (LSP/
  doctool/reflection probes), `claimtest/` (skeptic re-runs),
  `gdskills-history.git` (removal commit `6cb0843`, recovery ref `6cb0843^`).
- **Incident during research (needs action):** a probe-cleanup
  `pkill -f "[/]opt/godot/godot"` on the login node matched a sibling agent's
  srun test step (worktree `.claude/worktrees/agent-abeca7bd44a1c2da9`,
  `mit_quicktest`, pytest on test_feedback/test_harden/test_sb3_trainer).
  `sacct` shows 4 cancelled steps 15:05–15:39 (attribution unprovable — shared
  uid). Concrete outstanding item: a full `tests/test_sb3_trainer.py` run from
  that worktree never completed and should be relaunched.
