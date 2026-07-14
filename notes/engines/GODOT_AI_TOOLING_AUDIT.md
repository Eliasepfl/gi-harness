# Godot AI tooling — HANDS-ON audit (the layer the surveys lacked)

> **hands-on audit, Fable orchestrator + Opus agents, 2026-07-14.** Extends the same-day surveys
> `GODOT_SKILLS_WORLDGEN.md` / `CLAUDE_GAMEGEN_SKILLS.md` (which graded repos from the GitHub API +
> source pages) by CLONING seven and reading them at file:line to answer Elias's question: *can the
> Godot lane (`GODOT_RL_BENCH_AND_PIPELINE.md` parts A+B) be made cleaner with these tools?* Clones
> live external at `~/orcd/scratch/gi/tool-audit/*` and `~/GI/godot_rl_agents_examples` (pinned
> d659636). Nothing was run; static review only.

---

## 1. Adoption table

| tool | fit | one-line why | security note |
|---|---|---|---|
| **godot_rl_agents_examples** @d659636 | **adopt-now** | the 5 start-set envs ARE the RL smoke-bench (part A); pure-GDScript AIControllers over Sync TCP v0.7 | LOW-MOD: full-capability 3rd-party GDScript; grep clean (no OS.execute/eval); keep external, run in Apptainer, C#-strip to avoid native ONNX |
| **godogen** (htdt) | mine-technique | port its serialize + **node-count pre-save gate** to a frozen GDScript `build_part.gd` for bank-CI | NEGLIGIBLE for the port (our DATA-only builder, no net/keys); HIGH if run as-is (autonomous agent runs LLM C#) |
| **awesome-gamedev-skills** | mine-technique | accurate Godot-4 2D-physics prose → seed `api_godot.md`; headless-export recipes | very low: 147 .md + 1 CI linter (`validate-skills.py:108-162`, no subprocess/eval); Apache-2.0, attribute |
| **Coding-Solo/godot-mcp** | mine-technique | proves headless `--script <ops.gd> <op> <json>` .tscn authoring works; two-layer class allowlist | LOW-MOD: execFile argv arrays (no shell); `run_project`/`launch_editor` = RCE, never aim at generated content |
| **godot-mcp-runtime** (Erodenn) | mine-technique | clean length-prefixed TCP command loop → reference for `runner.gd` serve mode | HIGH if run: `run_script` = GDScript RCE, gate is Node-side + port-bypassable; **anti-pattern by contrast** |
| **tugcantopaloglu/godot-mcp** | mine-technique | mine `validate_script.gd` compile-checker + headless-export/Dockerfile invocation shapes | HIGH by design: `game_eval` RCE, no-auth TCP :9090; reject wholesale, mine 2 standalone files |
| **randroids-godot-skills** | mine-technique | `parse_results.py` (JUnit→JSON) + GdUnit4 headless + `validate_project.py` import/parse gate | LOW-MOD; **self-DEPRECATED**; do NOT adopt `run_tests.py`/PlayGodot/`ci.yml` (unpinned fork binary + `--dangerously-skip-permissions`) |

**One-line synthesis:** exactly ONE tool is adopt-now (the examples, as the external bench); the
other six give recipes/prose/patterns only. The certified-menu + typed-state verify moat stays
100% ours — every tool here proves via pixels, raw code, or human, none via typed state.

---

## 2. Per-tool hands-on findings (load-bearing citations kept)

**godot_rl_agents_examples @d659636 (adopt-now).** 5 envs load-safe across the 4.1→4.5 span:
all `.tscn` are `format=3` (BallChase.tscn:1), standard 4.x APIs, no removed nodes. All share
Sync protocol **0.7** (`sync.gd:16-17` MAJOR="0"/MINOR="7") → Python must be `godot_rl_agents`
from a git commit speaking 0.7, not the year-stale pip tag. Sync is a TCP client
(`sync.gd:377` connect_to_host) reading actions via safe `JSON.parse_string` (`sync.gd:346`,
applied by dict lookup, never eval'd); only FileAccess is demo-record (`sync.gd:532`) — no
OS.execute/eval/HTTP anywhere in plugin+game grep. C# is ONNX-inference-only
(`BallChase.csproj` OnnxRuntime 1.15.1; `ai_controller_2d.gd:20` typed member, gated behind
`ControlModes.ONNX_INFERENCE` at `sync.gd:100`) → TRAINING never touches it. See §4 for the
per-env prep blockers.

**godogen (mine-technique).** Not a library — a ~360KB doc renderer (`publish.sh:1-155`;
`render_dir.py:28-31` pure str.replace, no eval → repo surface safe). The only scene-builder is
a ~10-line C# `PackAndSave` sketch + prose at `engines/godot.md:14-39`. The load-bearing
technique: build graph headless → set Owner=root on every descendant **skipping instantiated
sub-scenes** (`godot.md:20`) → **count nodes, Pack, Instantiate, recount, gate Save on match**
to catch silent node-drop (`godot.md:21`, sketch :26-37); build seq `--import` then `--quit`
(:10). Their quirks DB (`godot.md:41-50`) is mostly 3D/C#. Port target = a frozen GDScript
`godotworld/bank/build_part.gd` (DATA-only part spec in, .tscn out, NO scripts attached) +
`certify_part.gd` asserting `CONTRACTS §9.5` on typed state (mass>0, shape!=null, joint anchors
resolve, no AABB interpenetration) — replacing their dropped-Gemini pixel QA. GDScript tax on
the port: never `:=` on `instantiate()`/math builtins (`gdscript-vs-csharp.md:5`); their worst C#
quirk (`SetScript()` disposal, :18) vanishes in GDScript. Live risk only if RUN: holds cloud keys
and downloads model files from API-returned URLs (`tripo3d.py:37-43,125`).

**awesome-gamedev-skills (mine-technique).** 15 Godot SKILL.md, all `Godot 4.3+`, zero
hallucinations on hands-on check of 4 skills. Extractables for `api_godot.md`: the body cheat
sheet (`godot-physics/references/bodies-and-queries.md:6-14`, agrees with SPEC §3), the
RigidBody control block (`:28-37` apply_central_impulse/force/linear_velocity ↔ SPEC §6 verbs),
`contact_monitor`+max_contacts (`:47-48` ↔ SPEC §3), raycast via PhysicsRayQueryParameters2D
(`:52-67` → grounded()/sensor ref), joints (`:82-89` ↔ SPEC §4), and the pitfalls corpus
(`SKILL.md:114-131` layer-vs-mask, tunneling, raycast staleness; `godot-gdscript:120`
int-division). Headless-export recipe verbatim-usable: `godot-export/SKILL.md:49-84`
(`--export-pack`, `--quit-after N` frames, `OS.has_feature("dedicated_server")||
DisplayServer.get_name()=="headless"` guard). Paraphrase (Apache-2.0), do NOT `npx skills add`.

**Coding-Solo/godot-mcp (mine-technique).** Two files. Scene-authoring IS headless:
`executeOperation` (`index.ts:511-528`) runs `['--headless','--path',proj,'--script',ops.gd,
op,paramsJson]` via execFileAsync (argv array, no shell); `godot_operations.gd:1-78` is the
exact frozen-runner dispatch skeleton (parse cmdline → JSON.parse one arg → match(operation)),
`:238-347` create_scene (instantiate→Pack→ResourceSaver.save), `:481-556` add_node. Class
allowlist worth copying verbatim: TS regex `^[A-Za-z_][A-Za-z0-9_]*$` (`index.ts:222-225`) +
registry-only lookup (`godot_operations.gd:91-121`, never a raw path). NOT headless & unsafe:
`run_project` `['-d','--path']` no `--headless` (`index.ts:1090-1097`), `launch_editor` `-e`
(`:1020`) — both execute the whole project (RCE); capture is unstructured `split('\n')` buffers
(`:1101-1115`). Gaps: one Godot process PER op (batch nodes into one call); add_node can't build
inline sub-resources — pre-author shapes as `.tres`, reference by res:// (auto-load
`godot_operations.gd:521-524`). No serve/step protocol.

**godot-mcp-runtime (Erodenn, mine-technique).** McpBridge autoload opens a loopback TCP loop
INTO a running game — the mineable asset is the framing/dispatch pattern for our serve mode:
`TCPServer.listen(port,'127.0.0.1')` (`mcp_bridge.gd:40`), 4-byte-BE length + UTF-8 JSON, 16MiB
cap (`:6-19`), per-peer state machine + `handling` one-in-flight gate (`:89-118`), token check
then `match command` dispatch (`:146-179`), Node mirror (`bridge-protocol.ts:22-99`),
inject/cleanup lifecycle (`bridge-manager.ts:52-97`). Two decisions transfer (TCP side-channel
to dodge Godot's stdout log spam; per-session random token) and one instructs by contrast:
`_handle_run_script` compiles arbitrary GDScript with NO in-bridge scan (`:514-553`) — full RCE,
gate lives Node-side so a direct port talker bypasses it (`docs/security.md:145-147`). NOT
headless: `runProject` throws with no DISPLAY (`godot-runner.ts:434-439`), windowed spawn
(`:452`), screenshot needs a live viewport (`mcp_bridge.gd:184-191`); worse, `act` is real-time
`_process` (`:34-75`) — non-deterministic, wrong model for RL. Editing path IS headless
(`godot-runner.ts:356-365`).

**tugcantopaloglu/godot-mcp (mine-technique).** Genuinely large fork (148 tools/case handlers,
`index.ts`), NOT boilerplate, but the architectural antithesis of our lane: `game_eval` →
`_cmd_eval` compiles MCP-provided code and runs it (`mcp_interaction_server.gd:554-581`, full
RCE), a second `set_script` path (`:3040-3049`), no-auth `TCPServer.listen(9090,'127.0.0.1')`
(`:13,18-27`) — on a shared host any local user could drive eval while a session is live.
Runtime lane needs a display; reject wholesale. Mine two standalone MIT files (attribute Tugcan
Topaloglu): (a) **`validate_script.gd`** — headless SceneTree compile-checker via
`ResourceLoader.load(target,'GDScript',CACHE_MODE_IGNORE)` with a documented workaround for the
`--check-only` autoload-ordering bug (invoked `index.ts:6338-6349`) → copy to
`godotworld/tools/validate_gd.gd` for bank-CI + runner dev; (b) invocation shapes:
headless op-dispatch (`index.ts:669-688`), export `--export-release` (`:5688`), and the
`manage_docker_export` Dockerfile that installs Godot+templates (`:6978`, writes only) for the
.sif. Node hygiene is good (execFile argv, `git -C` at `:6396-6397`). NB: `git clone` redirects
to Coding-Solo's upstream — fetch via codeload.

**randroids-godot-skills (mine-technique).** **Self-DEPRECATED** (`README.md:1`, commit
335df5d "DEPRECATED" 2026-01-19). Mine 3 things: `parse_results.py:52-225` (stdlib-only
JUnit-XML→json/markdown, copy verbatim into `harness/`); the GdUnit4 headless invocation for
runner.gd/spec-v2 GDScript tests; `validate_project.py:42-57` (`--import` + error-grep) and
`:80-90` (`--check-only --script` syntax gate) for bank-CI. Do NOT adopt: `run_tests.py`
default runs ZERO tests (passes `--run-tests`, not a real flag, omits `-a`; `:82-91`);
`export_build.py:47-61` template-version check is broken for 4.x and lacks an `--import`
pre-step (`:136-143`); PlayGodot needs a custom Godot fork + xvfb (`SKILL.md:187,199`,
`ci-integration.md:419`); `ci.yml` curl-downloads an UNPINNED prebuilt fork binary
(`:249-255`) and runs `claude --dangerously-skip-permissions` with a live key (`:462`), pins
stale GdUnit4 v4.4.0 (`:92`) — for 4.7 vendor GdUnit4 v5.0.4 from godot-gdunit-labs instead.

---

## 3. THE ANSWER — can parts A+B get cleaner? (delta vs GODOT_RL_BENCH_AND_PIPELINE.md)

Yes, but only at the edges: no tool replaces a moat step; several unblock or de-risk the plumbing.

**Part A (RL smoke-bench):**
- **A1 clone d659636 — STAYS ours.** Confirmed load-safe (format=3, protocol 0.7 `sync.gd:16-17`).
- **A2 headless export — UNBLOCKED by the examples audit + GAINS export recipes.** The plan's
  `--export-debug "Linux/X11"` fails as written: NO `export_presets.cfg` is committed (repo
  `.gitignore` ignores it; `build_examples.sh:2` points at an author-local mono binary). Fix =
  author presets + resolve the C# flag + `--import` first (§4). Borrow the export invocation
  from `godot-export/SKILL.md:49-84` and the `.sif` Dockerfile from tugcan `index.ts:6978`; the
  randroids `export_build.py:136-143` CLI shape is right only after you add its missing
  `--import` pre-step.
- **A3 wire godot_rl_agents (Sync TCP), A4 smoke, A5 bench — STAY ours.** Protocol-0.7 git-pin
  requirement is now confirmed at file:line, not inferred.
- **A6 risks — DE-RISKED.** Version drift is LOW (format=3 stable); the mono/.NET question is
  resolved to "strip C# from `config/features`, drop onnx/csharp .cs so the standard 4.7 image
  suffices" (TRAINING never instantiates ONNXModel — verified statically, smoke-test the load).

**Part B (pipeline):**
- **B1 prompts / `api_godot.md` — GAINS prose from awesome-gamedev-skills.** Paraphrase the body
  cheat sheet + verb mapping + pitfalls (`bodies-and-queries.md:6-14,28-37`; `SKILL.md:114-131`)
  into the SPEC-derived exemplars. Bank menu + `rules/orientation` STAY ours.
- **B2 gamegen engine=godot — STAYS 100% ours.** Coding-Solo's JSON-dispatch shape validates the
  design (`godot_operations.gd:1-78`) but there is nothing to wire in; spec extraction is ours.
- **B3 G4 attack_game — STAYS ours.**
- **B4 G3' serve mode on `runner.gd` — GAINS a TCP framing PATTERN + a settled design decision.**
  Lift the shape (not code) from `mcp_bridge.gd:34-118` (loopback bind, 4-byte-BE length frame,
  16MiB cap, `handling` one-in-flight gate) and the inject/token lifecycle from tugcan's bridge.
  CRITICAL adaptations, both from the anti-patterns: (i) replace real-time `_process` stepping
  (`mcp_bridge.gd:34-75`) with SYNCHRONOUS N-step-per-`act` inside the command handler
  (determinism for RL); (ii) NO `run_script`/eval verb — the fixed {init,reset,act,close}
  vocabulary over our whitelisted Expression erases their entire RCE + Node-side-gate attack
  class. Decision the runtime tool settles: Godot's stdout log spam makes stdio JSON fragile
  (they scrape it) → **prototype serve over TCP**, or route Godot logs off the stdio channel.
- **B5 acceptance run — STAYS ours.**
- **B6 tests — GAINS a CI harness.** GdUnit4 headless + `parse_results.py` (randroids) for
  runner.gd/spec-v2 GDScript tests; `validate_script.gd` (tugcan) or `validate_project.py`
  (randroids) as the parse/compile gate.

**Bonus — bank build item (1), certified `.tscn` authoring — GAINS the whole technique.** Port
godogen's serialize + node-count gate to a frozen `build_part.gd`/`certify_part.gd`, using
Coding-Solo's `--headless --script <ops.gd> <op> <json>` invocation + class allowlist, with
`validate_script.gd` as the compile pre-gate. This is where the tools help most.

---

## 4. examples-4.7-compat — per-env blockers for the ORCD smoke bench

Three shared prep chores gate EVERY env (none loads/exports as-is): (P) author
`export_presets.cfg` named exactly `"Linux/X11"` (none committed), (C) strip C# from
`config/features` + drop onnx/csharp `.cs`/`.csproj` (or ship a mono 4.7 build + dotnet +
OnnxRuntime), (I) `godot --headless --path <env> --import` first to rebuild the gitignored cache
and bump `config/features` → 4.7. All action spaces verified hands-on.

| env | dims | authored ver | action space | per-env note |
|---|---|---|---|---|
| **BallChase** | 2D | 4.1 (`project.godot:15`) | move cont size2 (`AIController2D.gd:47`) | RaycastSensor2D vendored (`BallChase.tscn:4`); cheapest smoke start |
| **SimpleMemoryTest** | true 2D | 4.5 | answer disc (`recall_answer_controller.gd:72`) | scene nodes = Node2D only; genuine 2D |
| **CrossTheRoad** | flat-3D | 4.3 | movement disc5 (`robot_ai_controller.gd:87`) | Completed/ subdir is the training scene |
| **DownFall** | flat-3D | 4.2 | jump/move/turn cont 1/2/2 (`ai_controller.gd:40-42`) | — |
| **JumperHard** | flat-3D | 4.1 | jump/move/turn cont 1/1/1 (`AIController3D.gd:54-56`) | the ordered-gating reference env |

All 5: P+C+I, then `--export-debug "Linux/X11" bin/<env>.x86_64`; Python = godot_rl_agents
git-pinned to Sync 0.7, `speed_up 8`, `n_parallel 4`, per-Slurm-job TCP port offset. Copy env dirs
to a WRITABLE location (import + version bump write in-place). Does NOT touch our runner.gd/serve
path — this is the OUTER/TCP rung for envs that already ship AIControllers.

---

## 5. REJECTED (do not wire in)

- **tugcantopaloglu/godot-mcp runtime lane** — `game_eval` RCE + no-auth TCP :9090
  (`mcp_interaction_server.gd:554-581,13,18-27`); GUI-bound. Mine 2 files only.
- **godot-mcp-runtime AS A PRODUCT** — `run_script` RCE, port-bypassable gate, needs a display,
  real-time non-deterministic `act`. Mine the TCP framing pattern only.
- **Coding-Solo `run_project`/`launch_editor`** — execute the whole project (RCE), not headless
  (`index.ts:1090-1097,1020`). Mine the headless authoring invocation only.
- **godogen as a tool** — autonomous agent runs LLM-authored C#, holds cloud keys, downloads from
  API URLs (`tripo3d.py:37-43,125`); C#/3D/pixel-proof. Port the technique only.
- **randroids `run_tests.py`** (runs zero tests, `:82-91`), **PlayGodot** (custom fork + xvfb),
  **`ci.yml`** (unpinned fork-binary download `:249-255` + `--dangerously-skip-permissions`
  `:462`). Repo is self-deprecated. Mine `parse_results.py`/`validate_project.py` only.
- **`npx skills add` / marketplace installs** (awesome-gamedev-skills, randroids) — run 3rd-party
  CLIs that fetch+copy into the agent skills dir. Hand-copy the 2-3 .md we paraphrase.
- **examples mono/.NET envs** — native OnnxRuntime nuget restore; excluded per the C-strip
  decision. Never point any runner at generated/untrusted Godot projects (RCE, SPEC §8).
