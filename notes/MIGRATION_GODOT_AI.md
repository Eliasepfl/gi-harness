# Migration: generation on the `godot-ai` MCP stack

Status: **PoC-1 + PoC-2 PASSED — feasibility proven, real Hermes drives it.** Integration lane
assessment + plan.
Author lane: INTEGRATION (worktree `worktree-godot-ai-migration`).
Date: 2026-07-17.

---

## 0. TL;DR

- **The make-or-break PoC works.** A live Godot **editor** (`--editor`, full `4.7.stable`
  build) runs under Xvfb inside `gi-certifier.sif` on a compute node, the `godot_ai`
  plugin connects to a uv-run MCP server over WebSocket, and an MCP tool call
  (`node_create`) mutates the editor scene end-to-end. Verified by reading the mutation
  back (`scene_get_hierarchy`). Job `18183129`, `POC_RESULT: PASS`.
- **PoC-2 works too: the REAL Hermes agent drives the editor.** `hermes -z … -m tencent/hy3:free`
  (the free model) running on the compute node connected to the same MCP server and, on its own,
  called `get_editor_state → get_scene_tree → create_node(HermesWasHere) → save_scene →
  get_scene_tree` (an *unprompted* self-verify), persisting the node into `Main.tscn` with **no
  file edits**. Job `18184764`. (Verdict-script said PARTIAL only because it grepped the server log
  for the tool name; FastMCP's server logger names request *types* — the named op receipts are in
  the editor/plugin log. Adjudicated **PASS** on direct editor-log evidence.)
- **Layering recommendation: adapter, not host change.** Keep the entire certification
  stack frozen. `godot-ai` becomes a **generation front-end** (author + live run/test/see/fix
  loop). A **thin extract-and-verify adapter** pulls the authored GameAPI `.gd` source out of
  the editor project and feeds it into the *unchanged* `verify_game` funnel. Do **not** teach
  the serve host to load `.tscn` — it violates the filesystem-blind sandbox and the
  `no load()/preload()` ban.
- **Hermes Agent is the driver (Elias directive).** Use the real Hermes CLI from GitHub, not a
  bespoke client. It is installed + configured on this box (`~/.local/bin/hermes` v0.18.2, godot-ai
  registered in `~/.hermes/config.yaml`, OpenRouter key in `~/.hermes/.env`). PoC-2 proves it drives
  the editor end-to-end on the *free* model. (Any MCP client *could* also drive the endpoint —
  that remains true — but the pipeline standardizes on Hermes.) Watch the free tier's **429 volume
  cap**.
- **Next brick (approved): our funnel as an MCP server.** `harness/mcp_server.py` exposes
  `extract_game` / `verify_game` / `capture_demo` / `atlas_place` so one Hermes session builds with
  godot-ai tools *and* iterates against our certifier's typed hints live — while final certification
  always re-runs out-of-session on the frozen host (verifier stays out of the agent trust domain).
  See §9.
- **Assets reconcile cleanly.** `godot-ai` has no external-fetch tool; its dressing is
  procedural or instantiates in-project PackedScenes. Certification stays pixel-blind
  (`state()` only). Our existing two-lane split (asset-free cert vs. `.glb` demo dressing via
  `visual_dress.gd`) is untouched.

---

## 1. What `godot-ai` is (stack map)

**Repo:** `/home/enaha/GI/godot-ai` (github `hi-godot/godot-ai`, v3.0.3, MIT).
`requires-python>=3.11`, uv env synced at `/home/enaha/GI/godot-ai/.venv`.

**Architecture (README "How It Works"):**
```
MCP client ──HTTP /mcp:8000──► Python server (FastMCP) ──WS:9500──► Godot editor plugin ──► live editor
```
- **Server:** console entrypoint `godot-ai = godot_ai:main` (`src/godot_ai/server.py`).
  Launch: `uv run godot-ai --transport streamable-http --port 8000 --ws-port 9500`.
  Hosts the MCP HTTP endpoint on 8000 and the WS bridge on 9500; binds `127.0.0.1` by default.
- **Plugin:** `plugin/addons/godot_ai/` (GDScript `@tool` EditorPlugin). On enable it probes
  `GET 127.0.0.1:8000/godot-ai/status`; if a compatible server answers it **adopts** it,
  otherwise it **spawns** one (`.venv/bin/python -m godot_ai …`). It connects to WS 9500 as a
  client and dispatches ops onto `EditorInterface`/`SceneTree`. It **early-returns under
  `--headless`/`--display-driver headless`** (escape hatch `GODOT_AI_ALLOW_HEADLESS=1` for CI
  handler tests) — so a **real display is required**, which is why the PoC uses Xvfb + x11.

**Tool surface — 43 MCP tools (~120 ops), from the live server (`tools list`):**

| Domain | Tools |
|---|---|
| Session / editor | `session_manage` (list), `session_activate`, `editor_state`, `editor_manage` (state/selection/monitors/quit/logs_clear/game_eval), `editor_reload_plugin`, `editor_screenshot`, `logs_read` |
| Scene / node | `scene_manage` (create/save_as/get_roots), `scene_open`, `scene_save`, `scene_get_hierarchy`, `node_create`, `node_find`, `node_manage` (children/groups/delete/duplicate/rename/move/reparent/group), `node_get_properties`, `node_set_property` |
| Script | `script_create`, `script_attach`, `script_patch` (anchor-edit), `script_manage` (read/detach/find_symbols) |
| Signals | `signal_manage` |
| UI / theme | `ui_manage` (build_layout / draw_recipe — programmatic vector decoration), `theme_manage` |
| Materials / particles | `material_manage` (create/set_param/shader_param/assign/preset), `particle_manage` |
| Animation / audio / camera | `animation_create`, `animation_manage`, `audio_manage`, `camera_manage` |
| Resources / tiles / input / autoload | `resource_manage` (search/load/create + **procedural** gradient_texture_create / noise_texture_create / environment_create), `tilemap_manage`, `tileset_manage`, `input_map_manage`, `autoload_manage` |
| Run / test | `project_run`, `game_manage` (drive the running game), `project_manage` (stop/settings), `test_run`, `test_manage` |
| Meta | `batch_execute` (multi-op), `api_manage`, `client_manage` (auto-config MCP clients) |

**Session flow, end to end:**
1. Server up on 8000 (HTTP) + 9500 (WS).
2. Editor boots with the plugin enabled → plugin adopts/spawns server, opens WS to 9500,
   registers a session (`<project-slug>@<4hex>`, e.g. `project@bf2b`).
3. MCP client connects `http://127.0.0.1:8000/mcp` (streamable-http), `tools/list` → 43 tools.
4. `session_manage(list)` shows connected editors; `session_activate` pins one.
5. A write tool (`node_create`, `script_patch`, …) → HTTP → server → WS frame → plugin →
   `EditorInterface` mutation (undoable) → structured reply back up the chain.
6. Read tools (`scene_get_hierarchy`, `node_get_properties`, `editor_screenshot`,
   `logs_read`) observe the result. `project_run`/`game_manage` play + drive the game live.

---

## 2. HPC FEASIBILITY — the PoC (PASSED)

**Question:** can the plugin's hard requirement — a *live editor*, not `--script`/headless — be
satisfied on a display-less compute node, and can an MCP tool call mutate that editor?

**Answer: yes.** Reused the proven `scripts/capture_demo.sh` pattern (host Xvfb + host-staged
x11 client libs bound into the pixel-free `gi-certifier.sif`, software GL via llvmpipe), but
with `--editor` instead of a game runtime, plus the uv MCP server on the host.

**Precheck (login node):**
- `/opt/godot/godot` in the sif = **full editor build**: `4.7.stable.official`, `--help` lists
  `-e, --editor` (editor-only `E` feature), `--path`, `--display-driver x11`. Not a template.
- Container has Python 3.12.13 and reaches host `~/.local/bin/uv` (HOME bound) — server can run
  host-side or in-container.
- Host has `uv 0.11.29`, `Xvfb`, `curl`; **no host godot** (so the editor must run in the sif).

**Artifacts (all on scratch, reproducible):**
- Project: `/orcd/scratch/orcd/008/enaha/gi/poc-godot-ai/project/` (copy of
  `plugin/addons/godot_ai` + `project.godot` enabling the plugin + minimal `Main.tscn`).
- MCP client: `/orcd/scratch/orcd/008/enaha/gi/poc-godot-ai/poc_client.py` (fastmcp `Client`).
- Job: `/orcd/scratch/orcd/008/enaha/gi/poc-godot-ai/poc.sbatch`
  (`mit_preemptable`, 4 cpu / 12G / 35 min).
- Logs: `/orcd/scratch/orcd/008/enaha/gi/logs/gai-poc-18183129.*`.

**The chain, exact commands (as run inside the job on node1460):**
```bash
# 0) pre-import project assets (headless; plugin self-disables, just builds .godot cache)
apptainer exec -B /orcd -B "$PROJ" gi-certifier.sif \
  /opt/godot/godot --headless --import --path "$PROJ"            # import rc=0

# 1) MCP server on the host uv env  (HTTP :8000 + WS :9500)
cd /home/enaha/GI/godot-ai && \
  uv run --no-sync godot-ai --transport streamable-http --port 8000 --ws-port 9500 &
#   -> server :8000 up=1

# 2) LIVE EDITOR in the sif, under Xvfb, x11 + software GL; plugin adopts the running server
apptainer exec -B /orcd -B /tmp/.X11-unix -B "$PROJ" -B "$X11LIB" -B /home/enaha/GI/godot-ai \
  gi-certifier.sif bash -lc '
    export DISPLAY=:NN LIBGL_ALWAYS_SOFTWARE=1 LD_LIBRARY_PATH=$X11LIB:$LD_LIBRARY_PATH
    exec /opt/godot/godot --editor --path "$PROJ" --display-driver x11 --rendering-driver opengl3' &

# 3) MCP client: wait for the editor session, then create + verify a node
cd /home/enaha/GI/godot-ai && uv run --no-sync python poc_client.py
```

**Result (`gai-poc-18183129.out` + `.client.log`):**
- MCP streamable-http session created; `tools/list` → **43 tools**.
- Editor connected over WS: session `project@bf2b`, `godot_version 4.7-stable (official)`,
  `readiness: ready`, `server_launch_mode: uvx` (it adopted the host server).
- **`node_create(type=Sprite2D, name=PoCNode, parent_path="")` → `/Main/PoCNode`.** Server log:
  `MCP | [recv] create_node({"name":"PoCNode",...})` — the call traversed
  client → HTTP:8000 → WS:9500 → live editor.
- **`scene_get_hierarchy` read it back:** `Main (Node2D)` → `PoCNode (Sprite2D)`, total 2.
- `POC_RESULT: PASS`, client rc=0.

**Warnings / gotchas hit (all benign, fixes noted):**
- **venv interpreter drift (the coordinator's flag):** the client log printed
  `Using incompatible environment (.venv) due to --no-sync (interpreter … 3.12.1 vs created-with
  3.12.13)`. The `.venv` was created (login node) with a uv-managed 3.12.13; at run time on the
  compute node uv resolved 3.12.1. `--no-sync` forced reuse and **it worked** — warning only.
  **Clean fix:** pick one — (a) `uv run --python 3.12.13 …` / `uv python pin 3.12.13` to pin the
  interpreter, (b) `uv sync` once per node (or inside the sif, which ships 3.12.13) to repair the
  venv to the local interpreter, or (c) run the server **inside** the sif where 3.12.13 is
  guaranteed. Recommend pin + a per-node sync check in the launcher.
- **`session_activate("poc_project")` errored** — the session name is the project *folder*
  basename `project`, not `poc_project`. Harmless (single session = default active). Fix: pin by
  the returned `session_id` (`project@bf2b`), not a guessed slug.
- Editor needs the **x11** driver + software GL (`LIBGL_ALWAYS_SOFTWARE=1`, `--rendering-driver
  opengl3`); `--headless` disables the plugin. Xvfb + staged x11 libs (14 libs, already cached
  at `~/.cache/gi-capture/x11libs` from the capture lane) satisfy this with zero sif rebuild.

**Verdict: FEASIBLE.** The one hard risk (editor-not-headless on a display-less node) is retired.

---

## 3. Stack layering — recommendation

**Two options assessed:**

- **(a) Adapter:** the agent authors in a `godot-ai` project; a converter emits our single-file
  GameAPI `.gd`; certification runs unchanged.
- **(b) Host change:** teach `serve_game.gd` + the wire to load a `.tscn` + attached script.

### Recommendation: **(a), and specifically "author the script live, ship the script."** Reject (b).

**Why (b) is wrong — it breaks the sandbox's core invariant.** `godotworld/serve_game.gd` is
FROZEN/audited and is **filesystem-blind**: the `init`/`check` wire frame carries the game's
**source text**, and `_compile_source` does `GDScript.new(); gd.source_code = src; gd.reload()`
— the host never opens a file. A `.tscn` is a filesystem resource; loading it needs
`ResourceLoader`/`load()`/PackedScene instantiation — **the exact APIs hard-banned at G0**
(`harness/verify/gd_gate.py`, BANNED table `api_gdscript.md:56-71`). Option (b) would require a
wire-protocol change, replacing in-memory compile with scene instantiation, and re-auditing the
frozen host and every cert gate. High risk to the crown jewels for no certification benefit
(certification is pixel-blind — it never needed a scene).

**Why (a) is right.**
1. Preserves the entire certification pipeline byte-for-byte: serve host, G0–G4, tree witnesses
   (`treesolve.py`), RL demo_ready (`rl/certify.py`), exporter (`export_episode`), Atlas, and the
   demo/capture dressing lane. **Zero risk to certified invariants.**
2. The certified artifact stays a single self-contained `.gd` (no `load`/`preload`, no external
   assets) — the contract holds trivially.
3. `godot-ai` owns **generation**, which is exactly the gap ("blind single-file generation from a
   weak model → structureless ugly games"). Its killer feature is the **live edit → run → see →
   fix loop** (`project_run` + `game_manage` + `editor_screenshot` + `logs_read` + `test_run`) —
   the antidote to blind generation.

**Critical nuance that shapes the adapter.** The harness world is **seed-procedural**:
`build(world_seed)` must construct the world in code and re-run deterministically for every seed.
A `godot-ai` `.tscn` is a **static, hand-placed** tree — the wrong shape (same every seed → fails
determinism/diversity). Therefore **we ship the authored *script*, not the scene.** Scene
authoring in the editor is at most a spatial scratchpad; it is never the shipped artifact.

### Adapter sketch (thin extract-and-verify — the primary path)

```
 godot-ai project (live editor, compute node)            gi-harness (UNCHANGED)
 --------------------------------------------            ----------------------
 Node "Game"
   └─ game.gd   (attached; implements the 7 methods,   ── extract ──►  game.gd SOURCE STRING
      build(world_seed) constructs the world in code)                        │
   agent loop drives it via MCP:                                             ▼
     script_create / script_patch   (write logic)                     verify_game(source)
     project_run + game_manage        (play it)                        · G0 gd_gate banned-scan
     editor_screenshot / logs_read    (see + read errors)              · godot --check-only parse
     test_run                         (contract probe)                 · has_method 7-probe
                                                                       · G0.5 reachability
   on cert failure → feedback.py directives fed back                   · G1 determinism twins
   to the agent as MCP revisions (loop the harness                     · G2 goal well-formed
   never had before)                                                   · G3 tree/RL witness
                                                                       · G4 harden ladder
                                                                             │ certified?
                                                                             ▼
                                                             export_episode · Atlas · capture-dress
```

The adapter is small and stateless:
- **extract**: read the attached script's source — either over MCP (`script_manage op=read`) or
  straight off disk (`res://game.gd`; the project lives on scratch). Assert plain `Node`, no
  `class_name`, no `extends <base>`, 7 methods present (mirror `serve_game.gd:69`
  `REQUIRED_METHODS` + a local `--check-only`).
- **verify**: hand the source string to the existing `verify_game` funnel. **No host change.**
- **repair**: on failure, surface the harness's existing typed directives (`harness/gen/feedback.py`,
  `harness/gen/harden.py`) back to the agent as MCP-driven edits; the agent fixes in the editor and
  re-runs. This closes a loop the current blind-regen pipeline lacks.

**Optional later enhancement — A1 scene→code flattener** (`scene_get_hierarchy` +
`node_get_properties` → codegen a `build()` skeleton). Explicitly a *scratchpad aid*: it emits a
**seed-independent** skeleton the agent must then parameterize by `world_seed` by hand. Never a
direct artifact path (a static tree can't satisfy `build(world_seed)`). Ship only if agents prove
they lay worlds out visually more effectively than they code them.

**Security boundary (non-negotiable):** the `godot-ai` editor runs untrusted agent scripts in a
`@tool`/editor context that is *more* privileged than the frozen serve host. **Never treat the
editor as a certifier.** Certification is *always* re-run on the extracted source through the
audited, filesystem-blind `serve_game.gd`. The editor is authoring only.

---

## 4. Assets & the certification rules

**How assets flow in `godot-ai`:** there is **no external/network asset-fetch tool** (verified —
no `download`/`http`/`ResourceLoader`-fetch tool in `src/godot_ai/tools/`; the two grep hits are
docstrings). Visual dressing is either (i) **procedural** — `ui_manage draw_recipe`,
`material_manage`, `particle_manage`, `resource_manage` gradient/noise-texture + environment
creation (this is how their cyberpunk-HUD demo was "zero image gen, all programmatically drawn"),
or (ii) **instantiation of PackedScenes already vendored into the project**
(`node_create(scene_path=…)`). "Compatible with ready-made asset libraries" means *a project can
contain asset packs the agent places*, not that the plugin pulls from the internet. The plugin is
itself *distributed* via the Godot Asset Library (asset 5050) — orthogonal to game content.

**Reconciliation with our rules — no conflict, because generation and certification are already
two lanes:**
- **Certification is pixel-blind** and reads only `state()` (`README.md:6-7`;
  `api_gdscript.md:50`). Nothing visual can help or hurt the verdict.
- **The game's own visuals** must be built **in code from primitives — `no external assets and no
  `load()`/`preload()`** (`api_gdscript.md:50`, statically enforced at G0 by `gd_gate.py`). This
  is unchanged: whatever the agent authored in the editor, the **extracted script** must still pass
  the banned-API scan. Procedural dressing (godot-ai's default) is compatible; any
  `load()/preload()`/`.glb` import the agent added is caught at G0 and sent back as a repair
  directive. **So the editor cannot smuggle assets past certification.**
- **MATERIAL REALITY** (`api_gdscript.md:44`, `notes/engines/MATERIAL_REALITY.md`, advisory gate
  `gameverify._anchoring_gate`): a place-based milestone must be anchored to a **real node with a
  collision shape** `add_child`-ed in `build()`, latched off its overlap/contact — never a bare
  coordinate. This is a *logic* rule, independent of visuals. `godot-ai`'s `node_create` for
  Area2D/3D + CollisionShape actually *helps* the agent build real anchors — but the check still
  runs on the extracted script.
- **Bank `.glb` dressing** stays exactly where it is: the **demo/capture lane only**, via the
  zero-contact `godotworld/visual_dress.gd` overlay (dressed and undressed replays produce
  byte-identical `state()` by construction), routed by `harness/demo/asset_bank.py::route_assets`
  and cached in `<game>.assets.json`. Untouched by this migration.

**Net rule:** assets/dressing are allowed at **generation** time only insofar as the extracted
script survives G0; certification still validates deterministic `state()` on an asset-free,
self-contained `.gd`; the `.glb` demo lane is unchanged.

---

## 5. Hermes Agent — the driver (Elias override)

> **Decision reversal.** An earlier draft recommended skipping Hermes for a bespoke MCP client.
> **Elias overrode this: use the real Hermes from GitHub.** PoC-2 (§5b) then proved it works — so the
> pipeline standardizes on Hermes as the agent that drives godot-ai.

**What it is:** NousResearch's **Hermes Agent** (`github.com/NousResearch/hermes-agent`, v0.18.2 here)
— an open-source, model-agnostic *agentic CLI* that speaks MCP; config at `~/.hermes/config.yaml`,
secrets in `~/.hermes/.env`. One-shot mode is `hermes -z "<task>" -m <model> --provider <p> --yolo
--cli`; `hermes mcp add/list` manages MCP servers (stdio + http, incl. OAuth 2.1). It is **installed
and configured on this box**: `hermes mcp list` shows `godot-ai → http://127.0.0.1:8000/mcp ✓ enabled`
(config lines 1511-1513), default model `anthropic/claude-opus-4.6` over OpenRouter
(`base_url https://openrouter.ai/api/v1`, key `OPENROUTER_API_KEY` in `.env`).

**How it fits our stack:** Hermes runs on the **compute node inside the job** (egress confirmed —
see §5b), reads `~/.hermes` off shared home, connects to the node-local godot-ai server over
loopback, and calls OpenRouter for inference. Its **MCP tools = godot-ai's 43 editor tools**; adding
our funnel as a second MCP server (§9) puts *build* and *certify* tools in the same session.

**Model + cost:** the OpenRouter key is **free-tier**, so pin a `:free` model — PoC-2 used
`tencent/hy3:free` and it drove the tools reliably (including an unprompted self-verify). No paid
fallback is available on this key (a paid model would just error). **Risk: free-tier 429 volume
cap** — the same wall the night waves hit; at pipeline scale, stagger Hermes sessions, add a second
key, or budget a paid model. For a *strong* driver on hard prompts, `anthropic/claude-opus-4.6`
(the configured default) is one `-m` flag away once a funded key is present.

**Still true:** godot-ai is a standard FastMCP streamable-http server, so *any* MCP client can drive
it (PoC-1 used a `fastmcp.Client`). We standardize on Hermes per the directive; the endpoint stays
client-agnostic underneath.

---

## 5b. PoC-2 — real Hermes drives the editor (PASSED)

**Question:** can the *actual* Hermes binary (not a bespoke client) drive the live editor
end-to-end on a compute node, on the free model?

**Setup** (`poc2.sbatch`, job `18184764`, node5005): PoC-1 chain (Xvfb + editor in sif + uv server),
then in-job `hermes -z "<task>" -m tencent/hy3:free --provider openrouter --yolo --cli`. Task:
*"Using the godot-ai MCP tools … create a Node2D named HermesWasHere under the current scene's root,
then save the scene … do not edit files directly."*

**Egress:** a 1-min probe (`egress_probe.sbatch`, job `18184455`, node5005) confirmed
`mit_preemptable` compute nodes reach `https://openrouter.ai` directly (`http=200`, 0.13 s). So
Hermes runs **in-job** (Architecture A); no tunnel. A no-egress fallback (login-node Hermes + `ssh
-L` tunnel to the compute server, `poc2b_holdjob.sbatch`) is written and documented but unused.

**Result — PASS.** Editor/plugin log (the authoritative op receipts):
```
MCP | [recv] get_editor_state({})
MCP | [recv] get_scene_tree({"depth":1.0,"limit":100.0,"offset":0.0})
MCP | [recv] create_node({"name":"HermesWasHere","parent_path":"/Main","type":"Node2D"})
MCP | [send] create_node -> ok
MCP | [recv] save_scene({})
MCP | [send] save_scene -> ok
MCP | [recv] get_scene_tree({"depth":2.0,"limit":100.0,"offset":0.0})   # unprompted self-verify
```
`Main.tscn` now contains `[node name="HermesWasHere" type="Node2D" parent="."]`; server saw 5
`CallToolRequest`s (matching the 5 named ops) + 1 `ListToolsRequest`; the session connected and
disconnected cleanly. **Hermes chose the right tools, made no file edits, and self-verified** — on a
free model.

**Logger caveat (fixed understanding):** the verdict script initially reported PARTIAL because it
grepped the **server** log for `create_node`, but FastMCP's `mcp.server.lowlevel` logger only prints
the request **type** (`Processing request of type CallToolRequest`). Named op receipts
(`MCP | [recv] <tool>(args)`) come from the **Godot plugin dispatcher** and land in the *editor* log.
Fix for future automated verdicts: assert against the editor log (or the tool result payloads the
MCP client receives), not the server's low-level type logger.

---

## 6. Migration plan

### What STAYS (frozen crown jewels — do not touch)
- `godotworld/serve_game.gd` deterministic host + wire (source-text, in-memory compile) and
  `harness/verify/gd_exec.py` driver.
- The 7-method GameAPI contract (`harness/gen/prompts/api_gdscript.md`, `godotworld/GAME_API.md`).
- The full verify funnel G0–G4: `gd_gate.py` (G0 banned scan), `reachability.py` (G0.5),
  determinism twins (G1), G2, `treesolve.py`/`statetree.py` (G3), `rl/certify.py` g3′ RL demo_ready,
  `g4.py`/`rl/adversary.py` (G4).
- Exporter `harness/export/episode.py::export_episode` + episode dataset format; `rollouts.py`
  negatives; Atlas `harness/atlas/build.py`.
- Demo/capture lane: `visual_dress.gd`, `asset_loader.gd`, `asset_bank.py`, `assets/manifest.json`,
  and **`scripts/capture_demo.sh`** (whose Xvfb + staged-x11 + sif pattern the PoC reuses verbatim).
- MATERIAL REALITY advisory gate.

### What CHANGES (generation only)
- Add a **`godot-ai` authoring backend** alongside the current blind-LLM backend in
  `harness/gen/gamegen.py` (`generate_game`/`_generate_core`). New package, e.g.
  `harness/gen/godotai/`:
  - **session launcher** — sbatch recipe from the PoC (editor in sif under Xvfb + uv server on
    the node, per compute node) + per-game project scaffolding + teardown. (PoC-1/PoC-2 scaffolding.)
  - **agent driver = Hermes** (Elias directive; PoC-2 proven) — `hermes -z/--yolo --cli` on the node,
    with `~/.hermes` configured for the godot-ai MCP server (and the funnel server, §9). Hand it
    `api_gdscript.md` as the compile target; it authors via `script_create`/`script_patch`, plays via
    `project_run`/`game_manage`, observes via `editor_screenshot`/`logs_read`/`test_run`. Model: a
    `:free` OpenRouter model for breadth, a funded strong model (`anthropic/claude-opus-4.6`) for hard
    prompts.
  - **extract-and-verify adapter** (§3) — pull the script source, run the *unchanged* `verify_game`
    (exposed in-session via the funnel MCP server, §9; certificate of record still re-run out-of-session).
  - **repair wiring** — the funnel's `verify_game` returns `feedback.py`/`harden.py` directives into
    the same Hermes session for live repair.
- Keep the blind-LLM backend for breadth/cost; select per game (`backend="godotai"` vs today's
  `"auto"`).

### Phases
- **Phase 0 — PoC (DONE).** PoC-1: editor+plugin+server+one tool call on a compute node
  (`18183129` PASS). PoC-2: real Hermes (free model) drives the editor end-to-end (`18184764` PASS).
- **Phase 1 — Session harness.** Productionize the launcher: warm editor per node, deterministic
  teardown, port/env hygiene, the venv-interpreter pin, per-game project reset
  (`scene_manage create` or fresh project) to prevent cross-game contamination. Deliver a
  `harness … godotai-session` smoke that boots an editor + a configured Hermes and returns a handle.
- **Phase 2 — Author-and-verify one game.** Hermes writes one GameAPI `.gd` live (with
  `project_run`/`test_run` feedback), extract it, pass the **full existing** funnel to CERTIFIED.
  Compare quality/structure against a blind-LLM baseline on the same prompt.
- **Phase 3 — Funnel MCP server + repair loop (§9).** Ship `harness/mcp_server.py`
  (`extract_game`/`verify_game`/`capture_demo`/`atlas_place`), register it in Hermes alongside
  godot-ai, and close the guided-repair loop (typed directives → live fix). Certificate of record
  still re-runs out-of-session. Measure repair convergence vs. blind regen.
- **Phase 4 — Throughput + routing.** Measure editor boot + per-game wall time; decide the split
  (godot-ai/Hermes for high-value/hard prompts, blind-LLM for breadth). Batch across nodes; manage
  the free-tier 429 cap (stagger / 2nd key / funded model). Wire into the wave runner.
- **Phase 5 — Guardrails v2 (§8), optional A1 flattener.** Build the resource-graph audit +
  hash-into-fingerprint machinery; only then relax the lexical bans to allow audited in-project
  resource loading. A1 scene→code flattener as a spatial scratchpad if it demonstrably helps.

### Risks & mitigations
1. **Throughput/cost.** A live editor per game (boot seconds–minutes, llvmpipe software GL, ~one
   node/game) is far heavier than parallel LLM calls. → Warm-reuse an editor across sequential
   games per node; reserve godot-ai for high-value prompts; keep blind-LLM for breadth. Measure in
   Phase 4 before committing wave volume.
2. **Static scene ≠ seed-procedural world.** → Ship the *script*, not the `.tscn`; keep
   `build(world_seed)` procedural (§3). A1 flattener is scratchpad-only.
3. **Sandbox trust.** The editor is more privileged than the serve host. → *Always* re-verify the
   extracted source through the frozen host; never certify from the editor (§3).
4. **venv/interpreter drift.** (Hit in the PoC.) → Pin `uv --python 3.12.13` / `uv python pin`, or
   `uv sync` per node, or run the server inside the sif (3.12.13). Bake into the launcher.
5. **Session/port hygiene.** 8000/9500 are node-local; one editor+server/node. On warm-reuse, reset
   scene state between games; pin sessions by returned `session_id`, not a guessed slug.
6. **Godot version pin.** godot-ai targets 4.5+/4.7; sif + serve host are 4.7-stable. Keep both
   lanes on the same engine; validate any 4.x bump on both.
7. **Cert-freeze discipline** (`notes` memory): never merge/push `main` while cert jobs run in
   `~/gi`; the integrity tripwire invalidates in-flight certs. This lane works in a worktree and
   touches only generation code.
8. **Free-tier 429 volume cap.** The OpenRouter key is free-tier; a `:free` model (PoC-2:
   `tencent/hy3:free`) works but the free tier throttles at volume — the same wall the night A-Z
   waves hit. → Stagger Hermes sessions, add a second key, or budget a funded model
   (`anthropic/claude-opus-4.6` is one `-m` flag away). No paid fallback exists on the current key.
9. **Funnel-server trust leakage (§9).** In-session `verify_game` results are *advisory* — the agent
   must never be able to weaken a gate, mutate the host, or mint the certificate of record. → Tools
   invoke the frozen fingerprinted host in throwaway sandboxes; final certification always re-runs
   out-of-session keyed by artifact hash; the verifier stays outside the agent trust domain.

---

## 7. PoC reproduction (exact)

```bash
# from a login node
sbatch /orcd/scratch/orcd/008/enaha/gi/poc-godot-ai/poc.sbatch
# watch
tail -f /orcd/scratch/orcd/008/enaha/gi/logs/gai-poc-<JOBID>.out   # expect: POC_RESULT: PASS
```
Key files: `poc.sbatch` (orchestration), `poc_client.py` (fastmcp client: waits for the editor
session, `node_create`, verifies via `scene_get_hierarchy`), `project/` (plugin-enabled project).
Env knobs baked in: `GODOT_AI_DISABLE_TELEMETRY=true`, `UV_OFFLINE=1`, x11 libs staged from
`~/.cache/gi-capture/x11libs`, Xvfb `:NN` at 1400x1000x24.

**PoC-2 (real Hermes):**
```bash
sbatch /orcd/scratch/orcd/008/enaha/gi/poc-godot-ai/poc2.sbatch          # in-job Hermes (egress OK)
grep POC2_RESULT /orcd/scratch/orcd/008/enaha/gi/logs/gai-poc2-<JOBID>.out
grep '\[recv\]' /orcd/scratch/orcd/008/enaha/gi/logs/gai-poc2-<JOBID>.editor.log   # named MCP ops
```
`egress_probe.sbatch` = 1-min compute-node egress check; `poc2b_holdjob.sbatch` = no-egress fallback
(login-node Hermes + `ssh -L` tunnel). Verdict caveat: assert MCP calls against the **editor** log,
not the server's low-level type logger (§5b).

---

## 8. Guardrails v2 — "ban less, verify more" (Elias doctrine)

> Elias: *"Maybe it's time to make our indication even smaller if the library gets even better. But
> then we have to be smarter with our guardrails."*

As godot-ai and ready-made asset libraries improve, the harness's hand-written **contract can
shrink** — but only if the guardrails get **smarter**. Today's contract leans on **lexical bans**
(`no load()/preload()`, `no class_name/extends`, `no external assets`, "construct every mesh/material
in code"). Those bans are **proxies**, not the real invariant: we ban `load()` because the
filesystem-blind serve host **cannot verify what a `load()` would pull in** (it could be
network-backed, `user://`-writable, or nondeterministic).

**The TRUE invariants certification protects** (keep these absolute):
1. **Replay-determinism** — the verifier replays the run and reproduces it byte-for-byte.
2. **`state()`-truth** — `state()` is the single, pure, complete source of truth; no hidden mutation.
3. **Sandbox safety** — untrusted code, in-container: no escape, no network, no clock/entropy, no
   unbounded resource use.

Lexical bans are proxies for #1 and #3. **If the verifier can audit what gets loaded, the proxy can
be relaxed** without weakening the invariant.

**What SHRINKS in the contract (what we can then allow):**
- **In-project resource loading.** Permit `load()`/`preload()` of `res://` paths that resolve inside
  the game's own bundle — a mesh/material/PackedScene from a vendored, ready-made asset pack the
  agent placed (godot-ai's `node_create(scene_path=…)`, `resource_manage load`). This lets games
  ship richer, real assets without hand-coding every primitive.
- The "construct every mesh/material in code" clause weakens to **"any loaded resource must be
  project-local and audited."** `no external assets` narrows to `no *unvendored/network* assets`.

**The SMARTER checks that replace the ban (the price of relaxing):**
1. **Resource-graph audit (static, pre-serve).** Walk the game's transitive resource closure
   (script + every `load()`/`preload()` target + PackedScene sub-resources). **Whitelist:** each
   path resolves under the game bundle root (`res://<bundle>/…`). **Forbid:** `user://`, absolute
   host paths, and any URL scheme (`http(s)`/`ftp`) — no network, no user-writable dirs. Reject
   `@tool`/`_init` side effects that touch FS/clock/network.
2. **Integrity fingerprint (hash into the witness).** SHA-256 every loaded resource's bytes and fold
   those hashes into the game's certification fingerprint / witness. Extends today's "witness
   replays bit-for-bit" from the *script alone* to the *script + its whole resource closure*: change
   any byte of any resource → fingerprint changes → cert invalid.
3. **Determinism re-verification with resources loaded.** G1's twin-rollout replays both twins with
   the identical audited, hashed resource set; a resource that injects nondeterminism (time-driven
   shader, `randi()` in a sub-script) fails G1 exactly as a script would today.
4. **`state()`-truth unchanged.** Certification still reads only `state()`; a loaded PackedScene that
   adds a physics body must surface it in `state()` like any code-built body, and **MATERIAL
   REALITY** still requires place-milestones anchored to real, reported nodes.
5. **Sandbox unchanged / strengthened.** Still no runtime network, no `user://`, no host FS; the host
   can pre-stage the audited set **read-only** and refuse any load outside it.

**Reconciliation with §3.** This is exactly the principled, verify-gated path by which the earlier
**rejected option (b)** (host loads `.tscn`/resources) becomes acceptable *later*: not by trusting a
`.tscn`, but by auditing + hashing its resource closure into the fingerprint. **Until that machinery
exists and is itself tested against adversarial resources** (a resource that mutates state, a symlink
escaping `res://`, a `user://` path, a network URL), the **default stays strict** — the §3 adapter
(single self-contained `.gd`) remains the shipping path. Guardrails v2 is a **Phase-5+** item, gated
on the resource-graph audit + fingerprint landing first; it is the one place the "single
self-contained `.gd`" invariant is *deliberately* widened, and only with the audit in place.

---

## 9. Next brick — our funnel as an MCP server (`harness/mcp_server.py`) [approved]

**Goal.** Put *certification* in the same Hermes session as *building*, so one agent loop can build
with godot-ai's editor tools **and** iterate against our certifier's typed hints in real time —
instead of the current blind "generate → hand off → hope". Expose the funnel as a **second MCP
server** Hermes connects to alongside godot-ai (`hermes mcp add`), giving the session both toolsets.

**Trust boundary (non-negotiable).** The verifier must stay **out of the agent's trust domain**. The
MCP tools are a *thin, read-mostly* facade that **invoke the frozen, fingerprinted serve host
out-of-process**; they never let the agent mutate gates, seeds, or the host. **Final certification
always re-runs out-of-session** on a clean host from the extracted artifact — the in-session tool
results are *advisory* (fast feedback), never the certificate of record. This mirrors §3's rule:
the editor/agent authors; the audited host certifies.

**Tool contracts (sketch):**

| Tool | Input | Output | Notes |
|---|---|---|---|
| `extract_game` | `session_id?`, `node_path?` (default the Game node) | `{ source: str, sha256, warnings[] }` | Pull the attached GameAPI `.gd` source out of the *live* godot-ai editor session (via `script_manage read` / disk). Local `--check-only` + `has_method` 7-probe; returns violations as `warnings`, does not certify. |
| `verify_game` | `source: str` **or** `game_ref`, `stage?` (`g0`\|`g0.5`\|`g1`\|`g2`\|`g3`\|`full`), `seed?` | `{ verdict, stage_results[], directives[] }` | Runs the **unchanged** `verify_game` funnel on the frozen host in a throwaway sandbox. `directives[]` = the existing typed `feedback.py`/`harden.py` repair hints, surfaced to the agent so it fixes in the editor and re-calls. Idempotent, read-only w.r.t. repo. |
| `capture_demo` | `game_ref`, `seed?`, `dress?=true` | `{ gif_path, frames, state_digest }` | Wraps `scripts/capture_demo.sh` (Xvfb + sif) → returns a demo GIF + the `state()` digest so the agent can *see* its game. Cosmetic lane; never gates. |
| `atlas_place` | `game_ref` (CERTIFIED only) | `{ atlas_cell, neighbors[], world_coord }` | Read-mostly Atlas placement/preview for a certified game; aggregation only, no cert authority. |

**Design constraints:**
- **Stateless, sandboxed tools.** Each `verify_game` spawns the frozen host on a fresh temp copy;
  no shared writable state with the agent. The agent cannot pass flags that weaken a gate (the tool
  fixes the gate set + host fingerprint server-side).
- **Env discipline** (per repo memory): the funnel server runs under
  `module load miniforge/24.3.0-0 && conda activate godot-rl` (never `reve`); godot invoked through
  the pinned sif. Respect the **cert-merge freeze** — never touch `main`/`~/gi` certs while waves run.
- **Same transport as godot-ai** (FastMCP streamable-http on a *distinct* node-local port, e.g.
  8001) so `hermes mcp add funnel http://127.0.0.1:8001/mcp` composes cleanly with the editor server.
- **Certificate of record** is emitted only by the out-of-session batch verify (the existing
  pipeline), keyed by the artifact hash — the in-session `verify_game` verdict is labeled
  *advisory* in its payload to prevent trust leakage.

**Why it's the right next brick.** It closes the generation feedback loop the harness never had
(blind regen → guided repair) while keeping the certification invariants and trust boundary exactly
as strong. Sequenced **after** Phase 2 (author-and-verify one game) and **before** wide waves.

---

## 10. The editor-diagnostics feedback loop [Elias: "give script/code errors and feedback to Hermes"]

> Elias flagged this as *one of the most interesting points*. The harness's blind single-file
> generator never sees why a script fails — it regenerates from scratch. The overnight A/B showed
> the concrete cost: a **Godot-3-ghost parse error** (a removed-in-4 API — `PoolIntArray`,
> `PolygonShape2D`, `yield`, Godot-3 signal syntax) silently sank **5–9 attempts/game**, because a
> parse failure returns nothing the blind generator can act on. godot-ai turns that black hole into a
> **line-numbered, in-editor repair loop**.

The loop is **two tiers**, in strict order. Tier 1 fixes *code that won't parse/run* using the
editor's own diagnostics; only code that survives Tier 1 is worth spending Tier 2 (certification)
compute on. Exact tool names/returns live in **`notes/GODOT_AI_TOOL_MAP.md`**.

### Tier 1 — editor diagnostics (godot-ai): make it parse, make it run

Three sub-channels, each cheaper than the last to reach, all carrying line numbers where the engine
can supply them (see TOOL_MAP §1):

1. **Write-time** — `script_create`/`script_patch` validate the `.gd` *before* the import step and
   return `diagnostics[]` + `diagnostics_detail` (`log_capture` = real parse diagnostics with a
   line; `fallback` = best-effort `details.fallback_line`) **in the same reply**. The agent gets the
   parse error on the file it just wrote, before running anything. This is where a Godot-3 ghost dies
   in one round trip instead of nine.
2. **Editor log** — `logs_read(source="editor", include_details=true)` returns
   `{source, level, text, path, line, function}` entries: parse errors, GDScript reload warnings,
   `@tool` runtime errors, `push_error`/`push_warning`, and the Debugger dock's Errors-tab rows. A
   one-shot **doorbell** (`new_errors_since_last_call` / `new_errors_hint`, or `editor_errors_hint`
   on a game read) is stamped on any tool reply when new errors appear — read the log, don't poll.
3. **Runtime** — `project_run` returns `game_status` + `recent_errors`; a boot-time parse/load error
   freezes the game in a debugger **break** and `game_status.break = {reason, can_debug, pre_live}`
   names the failing script. `game_manage(get_scene_tree/get_node_info)` reads live state;
   `editor_manage(op="game_eval")` probes the running game (its `EVAL_RUNTIME_ERROR` gives a message
   but **no line** — for line numbers stay in channels 1–2). `editor_screenshot(source="game")` lets
   the agent *see* the running frame.

**Tier-1 driver sequence** (per authored/patched script):
```
script_create(res://game.gd, <source>)          # or script_patch for an edit
  └─ read reply.diagnostics / diagnostics_detail  ── parse error? → fix text → re-write ─┐
     (loop until diagnostics_detail == "none")                                            │
script_attach(/root/Game, res://game.gd)  ◄──────────────────────────────────────────────┘
project_run(mode="main", autosave=false)
  └─ game_status.status == "break"/"not_live"?  → logs_read(source="editor"/"game",
                                                     include_details=true) → fix → repeat
  └─ status == "live"?  → game_manage inputs + get_scene_tree to sanity-check behaviour
project_manage(op="stop")
```
Only when Tier 1 yields a script that parses, attaches, and runs does the driver hand off to Tier 2.

### Tier 2 — the certification funnel (`harness-funnel`, §9): determinism, solvability, cert

`extract_game(project_path)` pulls the GameAPI `.gd` (7-method static scan) →
`verify_game(game_source)` runs the **frozen** funnel out-of-process and returns `verdict`,
`per_gate`, `hint`, and the typed `directives[]`/`directive_text` (the `feedback.py`/`harden.py`
repair payload). The agent applies each directive as a `script_patch` **in the editor** (re-entering
Tier 1 to keep it parsing), then re-calls `verify_game` until the verdict improves. `capture_demo`
lets it see a solved GIF; `atlas_place` aims the next game at an empty world×play cell. **The
in-session verdict is advisory** — the certificate of record always re-runs out-of-session on a
clean fingerprinted host (§9 trust boundary).

### Why the two tiers must stay separate

Tier 1 is the **editor's** truth (does this GDScript parse/run in a live Godot?) and Tier 2 is the
**funnel's** truth (does it certify deterministic + solvable through the frozen host?). A
Godot-3-ghost parse error is invisible to Tier 2 in a useful way — `verify_game` would just report
`VERIFY_ERROR` "verify crashed before producing a report" (exactly what the funnel returns for a
build/act error), which is *far* less actionable than the editor's `line: N, "Could not find type
PoolIntArray"`. Spending a Godot-host cert spawn to discover "your script doesn't parse" is the
waste Tier 1 eliminates. Tier 1 is also **cheaper** (no serve-host spawn, no port, no engine lock)
and **tighter** (write-tool reply, no second call). Keep the cheap, precise editor loop in front of
the expensive, authoritative cert loop.

### Tier-1 proof (compute-node test)

`poc-tier1.sbatch` + `tier1_client.py` (in `poc-godot-ai/`) stand up the proven PoC-1 chain (uv MCP
server ↔ live editor in the sif under Xvfb) and, over MCP: (1) `script_create` a script carrying the
Godot-3 ghosts `PoolIntArray`/`PoolVector2Array`, asserting the reply hands back a **line-numbered**
diagnostic; (2) `logs_read(source="editor", include_details=true)` shows the same error with
`{path, line, function, text}`; (3) `script_patch` fixes `Pool*`→`Packed*` and the diagnostics
clear — the full write→diagnose→fix cycle. Gates: `G1` ghost surfaces a diagnostic, `G2` it carries
a line number, `G3` the fix clears it.

**Result — PASS** (job `18190344`, `logs/gai-tier1-18190344.out`). `script_create`/`script_patch`
returned inline, line-numbered diagnostics (`diagnostics_detail=log_capture`):
```
line 3: Parse Error: Could not find type "PoolIntArray" in the current scope.
line 3: Parse Error: Function "PoolIntArray()" not found in base self.
        Did you mean to use "PackedInt32Array()"?
line 6: Parse Error: Could not find type "PoolVector2Array" in the current scope.
line 6:  … Did you mean to use "PackedVector2Array()"?
```
(Godot even names the Godot-4 replacement.) The editor process log carried the same as
`SCRIPT ERROR: Parse Error … at: GDScript::reload (res://ghost_game.gd:6)`. After the
`Pool*`→`Packed*` `script_patch`, the reply came back `diagnostics_detail=none`, `diagnostics: []`
— fixed. `G1/G2/G3` all PASS → `TIER1_RESULT: PASS`.

**Field note that sharpens the design.** In this run `logs_read(source="editor")` returned an *empty*
ring at the moment of the read, while the **write-tool's inline `diagnostics`** (channel 1a) carried
the full line-numbered error every time. Lesson: **prefer the write-tool reply (channel 1a) as the
primary parse-error signal** — it's synchronous with the edit and immune to ring-buffer timing;
treat `logs_read(source="editor")` (channel 2) as the corroborating/aggregate view and for
*runtime* errors that no write tool produced.

---

## 11. Full agent autonomy inside a disposable sandbox [Elias directive]

> **Direction (Elias, explicit — supersedes the earlier "kill --yolo" draft).** Hermes should be
> free to run **pretty much any tools, code, and MCP** — full autonomy. `--yolo` (and broad
> tool/MCP access) is the **sanctioned** mode, not something to eliminate. The certificate's
> integrity does **not** come from restricting what the agent may do in-session; it comes from
> **where** the agent runs (a throwaway sandbox that can't reach the real repo, `main`, or the
> frozen host) and from **re-verifying** its output out-of-session on the frozen fingerprinted host.
> **Trust from re-verification, not from restricting tools.**

### Why full autonomy is safe to sanction (what `--yolo` actually does)

Source-checked against the installed Hermes v0.18.2 (`~/.hermes/hermes-agent/`), so the grant is
understood, not assumed:

- `--yolo` sets `HERMES_YOLO_MODE=1` (frozen at import, `tools/approval.py:35`) and **bypasses the
  dangerous-*shell-command* approval gate** on the `terminal` tool (after the unbypassable floors).
- **Hermes has no per-*tool* approval gate at all.** MCP tools — our `harness-funnel` + the godot-ai
  editor tools — are **never** gated regardless of `--yolo`. `hermes -z`/`--oneshot` also force-sets
  yolo unconditionally (`oneshot.py:220-221`).
- **Still unbypassable even under `--yolo`** (the only floors that remain, and they are the sane
  default): the hardline blocklist (`rm -rf /`, `mkfs`, `dd` to a raw device, fork bombs —
  `approval.py:2896`), the sudo-stdin guard, and the user's `approvals.deny` globs
  (`approval.py:3212`). Nothing here restricts *building games*; they only stop host-destroying
  shell commands.

So "full autonomy" costs nothing we rely on: the agent can use the editor tools, the funnel tools,
shell, filesystem, and web freely. The sanctioned invocation is simply the existing one —
```
hermes -z "<task>" -m <model> --provider openrouter --yolo --cli   # broad tools, all MCP, code exec
```
(any of `hermes chat -q … --yolo`, extra `hermes mcp add` servers, etc. are equally fine). **The
safety does not come from that line.** It comes from the two boundaries below.

### The trust boundary: a disposable sandbox + out-of-session re-verification

These protect Elias's work and certification integrity **without** touching the agent's build
freedom. They are the sane default (he can drop them too, but they cost the agent nothing):

1. **Run Hermes only inside a disposable, wall-clock-bounded sbatch job.** Full-autonomy code
   execution is fenced by *what the job can reach*, not by what the agent is allowed to call:
   - The editor project is a **throwaway copy on `/orcd/scratch`** (`cp -r … project-<jobid>`,
     `rm -rf` on exit), bound into the sif. The real `gi-harness` repo is **not** checked out in
     the job; the harness is reachable only **read-only via `PYTHONPATH`** to a worktree, and the
     funnel's `verify_game` writes only into per-call `mkdtemp` sandboxes. So the agent cannot
     mutate the real repo, **cannot push to `main`**, and cannot edit the frozen host — those paths
     simply aren't writable from inside the job.
   - `sbatch --time=…` bounds the blast radius in wall-clock; `trap cleanup EXIT` tears down the
     scratch project, sandbox, editor, and servers.
   - Loopback-only servers (godot-ai `:8000`, funnel `:8010`, serve hosts pinned to the 54xxx band)
     — node-local, nothing exposed.
   - **Cert-merge-freeze respected** (repo memory): never merge/push `main` while cert jobs run in
     `~/gi`; this lane lives in a worktree and its jobs run on scratch. *Within* that fence, Hermes
     does anything it likes.

2. **Final certification always re-runs out-of-session on the frozen fingerprinted host** (§9,
   already the design). In-session `verify_game` verdicts are **advisory** fast feedback; the
   certificate of record is minted by the existing out-of-session batch verify, keyed by the
   artifact hash, on a clean host the agent never touched. The verifier stays **outside** the
   agent's trust domain. A fully-autonomous agent cannot launder a bad game into a certificate,
   because the certificate isn't its to mint — it's re-derived from the extracted `.gd` afterwards.

> **Production pattern, one line:** *full agent autonomy inside a disposable sandbox; trust comes
> from re-verification of the extracted artifact on the frozen host, not from restricting the
> agent's tools.* This is the same trust boundary §3 and §9 already state — the editor/agent
> authors with maximum freedom; the audited host certifies.

### Re-run evidence (already on record)

The **existing** `poc3.sbatch` *is* the sanctioned pattern and it already ran — full-autonomy Hermes
(`-z … --yolo --cli`) connected to **both** MCP servers inside a throwaway scratch sandbox, with the
frozen serve host invoked out-of-process. Job `18189027` funnel receipts show the autonomous agent
authoring `ball_to_goal.gd` (a 3754-byte GameAPI script) and driving `verify_game` end-to-end:
```
FUNNEL | verify_game(len=310, …)   -> verdict=ENV_ERROR  (G0 correctly rejected randomize())
FUNNEL | extract_game(project_path=…poc-godot-ai)
FUNNEL | verify_game(len=3754, …)  -> verdict=VERIFY_ERROR (Hermes-authored ball_to_goal.gd)
```
(The `VERIFY_ERROR` verdicts trace to a *separate* Godot-host-verify environment issue — the host
crashes before emitting a report even for the known-good `mini_collect.gd` fixture, STEP A — **not**
to the autonomy or the sandbox; the certificate of record re-runs out-of-session regardless, so this
is an infra bug to fix in the funnel's host launcher, tracked apart from this section.) No restricted
allowlist is needed or wanted; the sandbox + out-of-session re-cert are the whole guarantee.
