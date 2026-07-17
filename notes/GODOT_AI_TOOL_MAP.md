# godot-ai tool map — DIAGNOSTICS · RUN/CONTROL · SCREENSHOT

Reference table for the three tool families the migration leans on, read off the **real
source** at `/home/enaha/GI/godot-ai` (v3.0.3), not the README summary. Every row cites the
tool file (`src/godot_ai/tools/*.py`) and, where the return shape matters, the handler
(`src/godot_ai/handlers/*.py`) / GDScript plugin that constructs it.

Author lane: INTEGRATION. Date: 2026-07-17. Companion to `MIGRATION_GODOT_AI.md` §10
(the editor-diagnostics feedback loop) — this file is the raw tool inventory that design
depends on.

## 0. How the surface is shaped (read this first)

The plugin registers **43 MCP tools**. High-traffic verbs are **top-level tools**
(`project_run`, `script_patch`, `logs_read`, `editor_screenshot`, `test_run`, …); the
long tail collapses into **`<domain>_manage`** rollups dispatched by an `op` enum +
`params` dict (`editor_manage(op="game_eval", params={...})`, `project_manage(op="stop")`).
So several capabilities the brief named as standalone tools are really an **op on a manage
tool** or a **field on a response** — the mapping below is the point of this document.

Return-shape convention: the Python handlers are thin — most just
`return await runtime.send_command(...)`, and `send_command` unwraps the plugin's
`{"data": {...}}` envelope, so the "return keys" listed are the keys **inside** that
`data` dict. Errors are **raised** as `GodotCommandError`
(`{"status":"error","error":{"code","message","data"}}`), not returned.

### Name-to-reality crosswalk (the brief's guessed names → what actually exists)

| Brief's name | Reality in godot-ai v3.0.3 |
|---|---|
| `editor_errors_hint` | **response field**, not a tool. Added to `logs_read(source="game")` (and stamped on any tool reply as `new_errors_hint`) pointing you at `logs_read(source="editor", include_details=true)`. |
| `editor_errors_count` | **response field** on `logs_read(source="game")`; also `new_errors_since_last_call` (int) as a one-shot doorbell on any reply. |
| `game_error_warn` | no such tool. Runtime `push_error`/`push_warning` arrive via `logs_read(source="game")`; boot/parse errors via `logs_read(source="editor")` and `project_run.recent_errors`. |
| `debugger_*` | no `debugger_*` tools. The Debugger dock's **Errors-tab rows** are merged into `logs_read(source="editor")`; clear them with `editor_manage(op="logs_clear", params={clear_debugger_errors:true})`. |
| `get_logs` / `logs_read` | **`logs_read`** (top-level tool). |
| `clear_debugger_errors` | **param** of `editor_manage(op="logs_clear")`, not a tool. |
| `project_run` / `run_project` | **`project_run`** (top-level tool). |
| `test_run` / `run_tests` | **`test_run`** (top-level tool). |
| `stop_project` | **`project_manage(op="stop")`**. |
| `game_status` | **response field/object** (not a tool) on `editor_state`, `project_run`, `logs_read`. |
| `is_playing` | **response field** on `editor_state` (raw editor play-state; use `game_status.status` for liveness). |
| `game_eval` | **`editor_manage(op="game_eval", params={code})`**. |
| `game_command` | no single tool; runtime drive = **`game_manage`** ops (`input_key`, `input_mouse`, `input_action`, `get_scene_tree`, …). |

---

## 1. DIAGNOSTICS / ERRORS — the feedback channel

This is the key surface for "give script/code errors back to the agent." There are **three
sub-channels**, each with line numbers where the engine can supply them:

### 1a. Write-time diagnostics (returned by the write tool itself)
`script_create` / `script_patch` (`tools/script.py`) validate the written `.gd` **before**
the editor import step and return per-write diagnostics in the same reply (`docs/TOOLS.md:133-143`):

| Return key | Meaning |
|---|---|
| `diagnostics` | array of structured editor-style entries for the file just written (`{level, text, line, …}`). |
| `diagnostics_scope` | `"this_file"`. |
| `diagnostics_status` | `"checked"` or `"partial"` (scoped validation log window overflowed). |
| `diagnostics_detail` | `"log_capture"` (real Logger parse diagnostics), `"fallback"` (validation failed, Logger detail unavailable — line is best-effort in `details.fallback_line`), or `"none"` (no diagnostics). |

> This means the agent gets a **line-numbered parse error on the file it just wrote,
> before ever running the game** — the tightest possible loop.

### 1b. Editor-log diagnostics — `logs_read` (top-level, `tools/editor.py:88`)
```
logs_read(count=50, offset=0, source="plugin", since_run_id="",
          since_cursor=None, include_details=False, session_id="") -> dict
```
`source` ∈ `{"plugin","game","editor","all"}`. **`source="editor"`** is the parse/reload
error channel: parse errors, GDScript reload warnings, `@tool`/EditorPlugin runtime errors,
`push_error`/`push_warning`, and the Debugger dock's visible Errors-tab rows. Filtered to
`.gd`/`.cs` in the user project; `addons/godot_ai/` dropped.

- **Per-entry shape** (source=editor/game/all; `surfaced_error_tracker.gd:409-417`):
  `{source, level ("error"|"warn"|"info"), text, path, line, function, details}` — `details`
  is stripped unless `include_details=true`.
- **Envelope keys**: `source, lines, total_count, returned_count, offset, limit, has_more,
  run_id, is_running, dropped_count, stale_run_id`; plus (game/all) `current_run_id,
  helper_live, session_active, game_status`, and on game reads `editor_errors_count` +
  `editor_errors_hint`; plus cursor keys `cursor, oldest_cursor, next_cursor,
  appended_total, truncated`. Poll editor logs incrementally with `since_cursor=next_cursor`.
- **Doorbell**: any tool reply may carry `new_errors_since_last_call` (int) +
  `new_errors_hint` (and `new_warnings_*`) — delivered **exactly once**, "treat it as a
  doorbell, then read the logs" (`docs/TOOLS.md:121-131`).
- `editor_errors_hint` literal (`editor_handler.gd:203-206`): *"N editor-side error(s) from
  this run (first: …) missing from the game log — boot-time parse/load errors occur before
  the game helper's logger attaches. Read logs_read(source='editor', include_details=true)."*

### 1c. Runtime diagnostics — `project_run` + `game_eval`
- **`project_run`** returns `recent_errors` (list of the same `{source,level,text,path,line,
  function,details}` entries) plus a `game_status` object; a boot-time GDScript parse/load
  error freezes the game in a debugger **break** before any logger attaches — `project_run`
  synthesizes the record, names the failing script, and sets
  `game_status.break = {reason, can_debug, pre_live}` (see §2).
- **`game_eval`** (`editor_manage(op="game_eval", params={code})`, `tools/editor.py:40-51,270`):
  run GDScript in the running game. Success → `{result, source:"game"}`. Errors are raised
  with a code: `EVAL_COMPILE_ERROR` (syntax/parse — **message only, no line**; the parse text
  lives in the editor Output/Debugger panel, not capturable from the running game),
  `EVAL_RUNTIME_ERROR` (`"Game eval raised a runtime error: <msg>"` — message only),
  `EVAL_GAME_NOT_READY`, `EVAL_HUNG`, `EVAL_RESULT_TOO_LARGE`. Structured `line`/`path` live
  only on **surfaced editor errors** (§1b), not on eval errors — so for line numbers on a
  parse error, use §1a/§1b, not `game_eval`.

### 1d. Clearing
`editor_manage(op="logs_clear", params={clear_debugger_errors=false})` → `{cleared_count}`
(+ `debugger_errors_cleared` when `clear_debugger_errors=true`). Default leaves the
user-facing Errors panel untouched.

---

## 2. RUN / CONTROL — launch the game and read its runtime state

| Tool (signature) | Source | Returns (key fields) |
|---|---|---|
| **`project_run(mode="main", scene="", autosave=True, session_id="")`** | `tools/project.py:39` | `mode, scene, autosave, was_already_running, undoable, reason, game_status, helper_live, session_active, recent_errors, recent_errors_scope, recent_errors_may_predate_run, recent_errors_truncated, current_run_errors, retained_errors`. Idempotent (`was_already_running=true` if already playing). `autosave=false` keeps in-memory MCP edits off disk (smoke tests). |
| **`project_manage(op="stop")`** | `tools/project.py:96` | `{stopped, was_running, undoable, reason, readiness_after?}`. Idempotent (`was_running=false` if not running). This is `stop_project`. |
| `project_manage(op="settings_get"/"settings_set", params={key,value?})` | `tools/project.py:97-98` | read/write a `project.godot` ProjectSettings key. |
| **`editor_state(session_id="")`** | `tools/editor.py:56` | `godot_version, project_name, current_scene, is_playing, readiness, game_capture_ready, game_status, helper_live, session_active`. `is_playing` is raw play-state; use `game_status.status` for liveness. Also `editor_manage(op="state")`. **Recovery role**: calling it re-syncs the readiness cache after an `EDITOR_NOT_READY (state=playing)` false-positive. |
| **`game_manage(op=…, params=…)`** (drive the running game) | `tools/game.py:43` | ops: `get_scene_tree(depth,root_path)`→`{root,nodes[{name,type,path,children_count}],total_count}`; `get_node_info(path,include_properties)`→`{path,name,type,children_count,groups,found,properties?}`; `get_ui_elements(...)`; `input_key(key,pressed,echo)`→`{sent,key,pressed}`; `input_mouse(event,position,button,pressed)`; `input_gamepad(...)`; `input_action(action,pressed,strength)`; `input_state(actions)`. Targets the running game via Godot's EngineDebugger bridge. This covers `game_command`. |
| **`test_run(suite="", test_name="", exclude_test_name="", verbose=False, session_id="")`** | `tools/testing.py:29` | discovers `test_*.gd` in `res://tests/`, runs `test_*` methods → `{passed, failed, skipped, total, duration_ms, suites_run[], suite_count, failures[{suite,test,passed,skipped?,message}], results?(verbose), load_errors?, edited_scene, scene_warning?}`. This is `run_tests`. |
| `test_manage(op="results_get", params={verbose})` | `tools/testing.py:73` | re-fetch last `test_run` payload, no re-exec. |

**`game_status.status` enum** (`mcp_debugger_plugin.gd:444-459`), the authoritative liveness
field (used by both `project_run` and `editor_state`):

| status | meaning | `helper_live` / `session_active` |
|---|---|---|
| `live` | `_mcp_game_helper` checked in | true / true |
| `launching` | soft "not live yet"; may reconcile on `editor_state` poll | false / true |
| `not_live` | launched but never became live in the ready window | false / false |
| `no_helper` | no `_mcp_game_helper` autoload (headless/custom-main-loop) | false / true |
| `break` | **parked in a remote-debugger break** — at boot this is a GDScript **parse/load error**; carries `break={reason,can_debug,pre_live}`. Won't resume on its own → `project_manage(op="stop")`, fix, relaunch. | false / true |
| `stopped` | playback stopped / never became active | false / false |

`game_status` also carries `run_token, active, ready, helper_expected, run_started_msec,
elapsed_msec, ready_wait_msec, editor_log_cursor`. `is_running` is a back-compat alias of
`session_active` (no longer raw play-state).

**Launch → observe sequence**: `project_run(mode="main")` → poll `editor_state` until
`game_capture_ready=true` / `game_status.status=="live"` → drive with `game_manage` inputs →
read state with `game_manage(get_scene_tree/get_node_info)` and errors with
`logs_read(source="game")` → `project_manage(op="stop")`.

---

## 3. SCREENSHOT — `editor_screenshot` (top-level, `tools/editor.py:171`)
```
editor_screenshot(source="viewport", max_resolution=640, include_image=True,
                  view_target="", coverage=False, elevation=None, azimuth=None,
                  fov=None, session_id="")
```
| Param | Meaning |
|---|---|
| `source` | `"viewport"` (editor 3D viewport; needs Node3D content or returns `EDITOR_NOT_READY {editor_state:"viewport_not_3d", scene_root_type}`), `"viewport_2d"` (2D scenes; incompatible with view_target/coverage/camera args), `"cinematic"` (render through the scene's active `Camera3D`, no gizmos), **`"game"` (the RUNNING game's framebuffer — only while playing)**. |
| `max_resolution` | longest-edge px (default 640; `0` = full res). |
| `include_image` | `True` → returns an MCP `ImageContent` block; `False` → metadata dict only. |
| `view_target` | comma-separated Node3D scene path(s) to **reframe the editor camera** on; AABB metadata always returned. |
| `coverage` | with `view_target`, also capture an orthographic top-down reference (perspective + ortho). |
| `elevation` / `azimuth` / `fov` | **programmatic camera control** (degrees): elevation 0=level/90=overhead; azimuth 0=front/90=right; fov 20-30=zoom, 60-75=context. |

**Output** (`handlers/editor.py:72-180`):
- `include_image=False` → the `metadata` dict.
- `include_image=True` → a **list**: `[TextContent(json.dumps(metadata)), McpImage(...)]`
  (single) or `[TextContent, McpImage, McpImage, …]` (coverage).
- **Single-image metadata**: `source, width, height, original_width, original_height,
  format` (+ optional `view_target, view_target_count, view_target_not_found, elevation,
  azimuth, fov, aabb_center[x,y,z], aabb_size[x,y,z], aabb_longest_ground_axis("x"|"z"),
  camera_path`).
- **Coverage metadata**: `source, view_target, coverage:true, image_count, images[{label,
  elevation, azimuth, fov, width, height, ortho?}]` (+ AABB keys).
- **Not-ready** (raised, not returned): `EDITOR_NOT_READY` with sub-codes
  `EDITOR_GAME_NOT_RUNNING` (source="game" while not playing — start the project first),
  `EDITOR_VIEWPORT_NOT_3D`, `EDITOR_VIEWPORT_EMPTY`, `EDITOR_NO_SCENE`,
  `EDITOR_VIEWPORT_UNAVAILABLE`, each with an actionable `error.message`.

**Answers to the brief's screenshot questions:** yes, it can screenshot the **running game**
(`source="game"`), and yes, the editor camera is **controllable programmatically** for
viewport/cinematic shots via `elevation`/`azimuth`/`fov`/`view_target` (the running-game
`source="game"` shot uses the game's own camera, not these knobs).

---

## 4. Supporting tools referenced by the loop

| Tool (signature) | Source | Returns |
|---|---|---|
| `session_manage(op="list")` | `tools/session.py:57` | connected editors: `session_id, name, godot_version, project_path, plugin_version, server_version, editor_pid, server_launch_mode, current_scene, play_state, readiness, connected_at, last_seen, is_active`. |
| `session_activate(session_id)` | `tools/session.py:35` | pin the active editor (exact id `<slug>@<4hex>` or a unique substring hint). |
| `script_create(path, content="", session_id="")` | `tools/script.py:33` | writes a `.gd`; returns write-time `diagnostics*` (§1a) + `data.cleanup.rm`. |
| `script_patch(path, old_text, new_text, replace_all=False, session_id="")` | `tools/script.py:54` | exact anchor replace; write-time `diagnostics*` (§1a). Not Ctrl-Z undoable. |
| `script_attach(path, script_path, session_id="")` | `tools/script.py:86` | attach a `.gd` to a scene node (undoable). |
| `script_manage(op="read"/"find_symbols"/"detach", params={path})` | `tools/script.py:108` | `read`→`{source,line_count,file_size}`; `find_symbols`→outline (class_name, extends, funcs, signals, @export). Used by the funnel's `extract_game`. |
| `camera_manage(op=…)` | `tools/camera.py:54` | Camera2D/3D authoring (`create/configure/apply_preset/get/list/follow_2d/…`) — scene-authoring, distinct from `editor_screenshot`'s transient camera reframing. |
