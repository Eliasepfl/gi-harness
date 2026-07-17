# serve_game.gd -- FROZEN, audited serve host for the GDScript (GameAPI) lane.
#
# The code twin of runner.gd's serve mode: where runner.gd interprets a DATA spec,
# this host loads a generated `.gd` game -- a PLAIN Node that IMPLEMENTS the method
# convention (build/act/state/checkpoints/is_success/is_failure/actions; NO base
# class -- godotworld/GAME_API.md) -- and drives it through the SAME framed serve
# protocol so the Python funnel (harness/verify/gd_exec.py -> the shared G0-G3
# layers) is engine-agnostic.
#
# Run (spawned by the Python side; NEVER hand-run against untrusted code outside
# the container):
#   godot --headless --fixed-fps 60 --path <godotworld> -s res://serve_game.gd \
#         -- --serve --port=<N> [--speedup=<K>]
#
# WIRE: identical to runner.gd -- Python binds/listens on 127.0.0.1:<N>, this host
# CONNECTS OUT and speaks 4-byte BIG-ENDIAN length-prefixed UTF-8 JSON frames (the
# §3 inversion so Godot's stdout log spam never corrupts the wire).
#
# VERBS (determinism-first; NO eval/script/name-dispatch verb):
#   check {source}                  -> G0/G2 FACTS: parse gate (in-memory compile),
#                                      contract probe (has_method), build + t=0
#                                      state()/checkpoints()/is_*() purity probes.
#   init  {source, seed, horizon}   -> compile + build(seed); handshake + first frame.
#   reset {seed}                    -> full free+rebuild+reseed (fresh episode).
#   act   {actions, n_ticks,        -> run <= n_ticks decision ticks SYNCHRONOUSLY,
#          escape_margin?}             each = act(action) + K=6 physics frames +
#                                      latch + terminal check; frame back.
#   close                           -> ack + quit.
#
# SECURITY: the generated game is compiled in-memory (GDScript.new + reload) and its
# methods are called, so this process runs UNTRUSTED code -- it therefore runs ONLY
# in-container, on a SCRUBBED environment (the Python spawner passes no keys), and
# ONLY after the Python-side static banned-API scanner (harness/verify/gd_gate.py)
# has passed. The host hands the game NOTHING but tree membership (its RigidBody2D
# children join the world's physics space) + build()'s seed; the game seeds its own
# RandomNumberGenerator. Every escape hatch (OS/FileAccess/net/threads/reflection/
# wall-clock/unseeded RNG) is a hard G0 scanner fail. This host itself uses NO such
# API on the game's behalf.

extends SceneTree

# Preloaded by PATH, not by global class_name: the class-name registry lives in the
# gitignored .godot cache, so a fresh checkout would fail to parse this host.
const ChordUtil = preload("res://chord_util.gd")

const K_STEPS := 6
const VMAX := 1.0e5
const DEFAULT_W := 800.0
const DEFAULT_H := 600.0
const SERVE_MAX_FRAME := 16777216       # 16 MiB frame cap (protocol sanity guard)
const SERVE_DEFAULT_HORIZON := 300      # decision-tick truncation budget
const SERVE_IDLE_TIMEOUT_MS := 120000   # self-quit after this long idle (orphan guard)
const SPEEDUP_MIN := 1
const SPEEDUP_MAX := 16
const PLAY_MARGIN := 400.0              # px padding past the level extent -> play-bounds
                                        # (Elias directive 2: a controlled body leaving
                                        # this TRUNCATES the episode, it is not a break)
# --- Egocentric raycast obs (OPT-IN; the examples' RaycastSensor pattern, no pixels) ---
# ZERO effect unless init carries a `rays:{n, fov_deg, range}` key: no cast, no wire
# byte, obs unchanged. When on, every frame gains a "rays" array of N normalized hit
# distances (1.0 = nothing within range) cast FROM the controlled body IN ITS LOCAL
# FRAME, via direct_space_state.intersect_ray (deterministic, read-only, excludes the
# body itself), computed at the SAME point in the tick as state() so replay parity holds.
const RAYS_MAX := 4096                  # sanity cap on total ray count (protocol guard) [eng.]
const RAYS_DIM_MAX := 128               # per-axis cap (n, n_h, n_v) [eng.]
const RAYS_HEADING_EPS := 1.0e-2        # speed below which heading holds its last value [eng.]

# The GameAPI contract methods a generated game MUST implement (GAME_API.md).
const REQUIRED_METHODS := ["build", "act", "state", "checkpoints",
	"is_success", "is_failure", "actions"]

# --- Live episode state (rebuilt per init/reset) --------------------------- #
var _source := ""                       # cached game source (for reset rebuild)
var _script: GDScript = null            # cached COMPILED game script (compile once,
                                        # instantiate per episode -> no reset recompile)
var _game: Node = null                  # the instantiated GameAPI game
var _horizon := SERVE_DEFAULT_HORIZON
var _applied := 0                       # decision ticks since the last (re)build
var _latches := {}                      # checkpoint name -> latch tick | null
var _result := ""                       # "" (running) | success | failure | error
var _done_term := false
var _done_trunc := false
var _frozen := false
var _nan := false
var _build_err := ""
var _world_w := DEFAULT_W
var _world_h := DEFAULT_H
var _actions_cache := []                # actions() captured at build (handshake)
var _speedup := 1
# Egocentric raycast config (opt-in). _rays_on stays false unless init passed a rays key,
# keeping every frame byte-identical to the pre-rays wire. 2D games cast a planar fan of
# _rays_n across _rays_fov_deg; 3D games cast a rectangular depth-retina grid of
# _rays_nh x _rays_nv across _rays_fov_h x _rays_fov_v. Range (world units) is shared.
var _rays_on := false
var _rays_n := 16                       # 2D planar fan ray count
var _rays_fov_deg := 180.0
var _rays_nh := 25                      # 3D grid: horizontal rays (reference wide sensor)
var _rays_nv := 5                       # 3D grid: vertical rays
var _rays_fov_h := 120.0
var _rays_fov_v := 60.0
var _rays_range := 80.0                 # world units; reference ray_length
var _rays_class_bits := true            # per-ray {static,dynamic,sensor} one-hot (semantic)
# Ray FRAME: "auto" (default) casts in the body's local frame while its rotation is LIVE,
# but switches to a HEADING frame (forward = velocity direction) once rotation is LOCKED --
# a lock_rotation body never turns to face its travel, so a body-local retina would stare at
# a FIXED world direction. "body" forces the body-local frame always. Fully sensor-side.
var _ray_frame := "auto"
# Per-instance last heading (instance index -> Vector2/Vector3), so a below-eps-speed frame
# holds its last direction. Reset on rebuild -> derived purely from the deterministic
# trajectory, so twin rollouts stay bit-exact.
var _ray_heading := {}
# Play-bounds (x,y) computed at (re)build from the world box + t=0 body positions +
# PLAY_MARGIN; the controlled body leaving it truncates the episode (Elias directive 2).
var _play_min_x := -PLAY_MARGIN
var _play_min_y := -PLAY_MARGIN
var _play_max_x := DEFAULT_W + PLAY_MARGIN
var _play_max_y := DEFAULT_H + PLAY_MARGIN

# --- Batched (in-scene) instances -- ONE socket serves N independent worlds ------ #
# Populated only when init carries n_instances > 1; single-instance mode leaves these
# empty and every op below takes the UNCHANGED, byte-identical legacy path. The N worlds
# live in ONE SceneTree (each under its OWN SubViewport world -> isolated physics space),
# so a single `await physics_frame` steps ALL N at once: the in-scene batching that
# amortises the per-tick engine loop and the socket round-trip across N worlds (the
# godot_rl_agents batching idea). Parallel arrays are indexed by instance 0..N-1.
var _batched := false                   # true once init carried an n_instances key
var _n_instances := 1                   # N worlds over one socket (>=1 when batched)
var _base_seed := 0                     # instance i is (re)built at seed base_seed + i
var _games := []                        # per-instance game Node
var _viewports := []                    # per-instance SubViewport (own physics world)
var _latches_arr := []                  # per-instance {checkpoint -> latch tick | null}
var _applied_arr := []                  # per-instance decision ticks since (re)build
var _result_arr := []                   # per-instance "" | success | failure | error
var _done_term_arr := []                # per-instance terminal reached
var _done_trunc_arr := []               # per-instance truncated at horizon
var _frozen_arr := []                   # per-instance NaN/exploded physics
var _nan_arr := []                      # per-instance NaN flag (frame diagnostic)
var _play_bounds_arr := []              # per-instance [min_x, min_y, max_x, max_y]


# =========================================================================== #
# Lifecycle -- determinism pins mirror runner.gd (paired physics/time scaling).
# =========================================================================== #
func _initialize() -> void:
	_speedup = _speedup_arg()
	Engine.physics_ticks_per_second = 60 * _speedup
	Engine.time_scale = float(_speedup)
	Engine.max_physics_steps_per_frame = 8
	Engine.physics_jitter_fix = 0.0
	_main()


func _speedup_arg() -> int:
	var sp := SPEEDUP_MIN
	for a in OS.get_cmdline_user_args():
		if a.begins_with("--speedup="):
			sp = a.substr(10).to_int()
	return clampi(sp, SPEEDUP_MIN, SPEEDUP_MAX)


func _serve_port() -> int:
	var has_serve := false
	var port := -1
	for a in OS.get_cmdline_user_args():
		if a == "--serve":
			has_serve = true
		elif a.begins_with("--port="):
			port = a.substr(7).to_int()
	return port if has_serve else -1


func _main() -> void:
	var port := _serve_port()
	if port < 0:
		quit(1)
		return
	var peer := StreamPeerTCP.new()
	if peer.connect_to_host("127.0.0.1", port) != OK:
		quit(1)
		return
	var guard := 0
	while true:
		peer.poll()
		var st := peer.get_status()
		if st == StreamPeerTCP.STATUS_CONNECTED:
			break
		if st == StreamPeerTCP.STATUS_ERROR or st == StreamPeerTCP.STATUS_NONE:
			quit(1)
			return
		guard += 1
		if guard > 1000000:
			quit(1)
			return
		await process_frame
	peer.set_no_delay(true)

	while true:
		var msg = await _read_frame(peer)
		if msg == null:
			break
		var op := str(msg.get("op", ""))
		if op == "close":
			_write_frame(peer, '{"ok":true,"error":null}')
			break
		var reply := ""
		match op:
			"check":
				reply = await _op_check(msg)
			"init":
				reply = await _op_init(msg)
			"reset":
				reply = await _op_reset(msg)
			"act":
				reply = await _op_act(msg)
			_:
				reply = '{"ok":false,"error":"unknown op: %s"}' % _esc(op)
		_write_frame(peer, reply)
	peer.disconnect_from_host()
	quit()


# =========================================================================== #
# Framed wire I/O (byte-identical to runner.gd's serve framing)
# =========================================================================== #
func _read_frame(peer: StreamPeerTCP):
	var header := await _read_n(peer, 4)
	if header.size() < 4:
		return null
	var length := (int(header[0]) << 24) | (int(header[1]) << 16) | (int(header[2]) << 8) | int(header[3])
	if length <= 0 or length > SERVE_MAX_FRAME:
		return null
	var body := await _read_n(peer, length)
	if body.size() < length:
		return null
	var parsed = JSON.parse_string(body.get_string_from_utf8())
	if typeof(parsed) != TYPE_DICTIONARY:
		return null
	return parsed


func _read_n(peer: StreamPeerTCP, n: int) -> PackedByteArray:
	# Busy-wait (poll + 1 ms sleep, NO yield) so the world is FROZEN between ops --
	# `act`'s N*K `await physics_frame` burst is the ONLY physics that runs, keeping
	# two serve sessions byte-identical. A short read signals a dead/idle peer.
	var buf := PackedByteArray()
	var idle_start := Time.get_ticks_msec()
	while buf.size() < n:
		peer.poll()
		var avail := peer.get_available_bytes()
		if avail > 0:
			var got = peer.get_partial_data(mini(avail, n - buf.size()))
			if got[0] != OK:
				return buf
			buf.append_array(got[1])
			idle_start = Time.get_ticks_msec()
		elif peer.get_status() != StreamPeerTCP.STATUS_CONNECTED:
			return buf
		elif Time.get_ticks_msec() - idle_start > SERVE_IDLE_TIMEOUT_MS:
			return buf
		else:
			OS.delay_msec(1)
	return buf


func _write_frame(peer: StreamPeerTCP, text: String) -> void:
	var body := text.to_utf8_buffer()
	var n := body.size()
	var header := PackedByteArray()
	header.append((n >> 24) & 0xFF)
	header.append((n >> 16) & 0xFF)
	header.append((n >> 8) & 0xFF)
	header.append(n & 0xFF)
	peer.put_data(header)
	peer.put_data(body)


# =========================================================================== #
# Game compile + build (the in-memory parse gate + contract probe)
# =========================================================================== #
func _compile_source(src: String) -> Dictionary:
	# In-memory compile so we can INSTANTIATE the game (compile ONCE; instantiate per
	# episode from the returned GDScript so reset never recompiles). The AUTHORITATIVE
	# parse gate is now the Python side's standalone `--check-only --script` (a duck-typed
	# plain-Node game has no base class to resolve, so it compiles standalone); this
	# reload() is the belt-and-braces twin that also yields the instantiable script.
	# Returns {"ok", "error", "script"}.
	var gd := GDScript.new()
	gd.source_code = src
	var err := gd.reload()
	if err != OK:
		return {"ok": false, "error": "parse/compile failed (Error %d)" % err, "script": null}
	if not gd.can_instantiate():
		return {"ok": false, "error": "script is not instantiable (must be a plain Node implementing the method convention)", "script": null}
	return {"ok": true, "error": "", "script": gd}


func _instantiate(gd: GDScript) -> Dictionary:
	var inst = gd.new()
	if inst == null:
		return {"ok": false, "error": "instantiation returned null", "instance": null}
	if not (inst is Node):
		return {"ok": false, "error": "game must be a Node (extends Node / Node2D / Node3D)", "instance": null}
	return {"ok": true, "error": "", "instance": inst}


func _missing_methods(inst) -> Array:
	var miss := []
	for m in REQUIRED_METHODS:
		if not inst.has_method(m):
			miss.append(m)
	return miss


func _teardown() -> void:
	if _game != null and is_instance_valid(_game):
		_game.queue_free()
	_game = null


func _rebuild(world_seed: int) -> String:
	# Full free + rebuild + reseed -> a fresh, deterministic episode, reusing the
	# CACHED compiled script (no recompile). Returns "" on success else an error.
	_teardown()
	await process_frame                     # flush the prior deferred free
	if _script == null:
		return "no compiled game script"
	var made := _instantiate(_script)
	if not made.ok:
		return made.error
	var inst = made.instance
	var missing := _missing_methods(inst)
	if not missing.is_empty():
		inst.free()                         # not in the tree yet
		return "missing contract method(s): " + ", ".join(missing)
	# 3D DETERMINISM PIN (notes/engines/DETERMINISM_3D.md). The single-instance path adds
	# the game straight under `root`, so a 3D game's RigidBody3D dynamics live in root's
	# World3D -- a physics space this path REUSES across every reset. GodotPhysics3D keeps
	# per-space solver state (contact/broadphase caches, freed-body RID reuse) that a body
	# free() does NOT scrub, so an episode rebuilt OVER the prior episode's stepped space
	# diverges from a clean build by a tiny, growing epsilon at the first collision. That
	# is exactly what fails the G1 two-run determinism gate on force-driven 3D games
	# (drone/car), whose trajectories never settle to rest to mask it -- measured
	# delta 5.9e-05 (drone) .. 0.046 (car). Two SEPARATE processes are byte-identical (each
	# builds over a clean space), which isolates the reused space as the sole cause. Hand
	# every 3D episode a FRESH World3D so ZERO state crosses the reset boundary. 2D is
	# untouched (root.world_2d left as-is) -> 2D replays stay byte-for-byte identical.
	if inst is Node3D:
		root.world_3d = World3D.new()
	root.add_child(inst)
	# The game is a plain Node implementing the method convention (no base class): it
	# seeds its OWN RandomNumberGenerator from build()'s seed -- the banned-API scan
	# forbids the unseeded global randi/randf/randomize, so a self-seeded generator is
	# the sanctioned path -- and its RigidBody2D children join the world's physics space
	# by tree membership. The host hands it nothing but that membership + the seed.
	# RUNTIME ERRORS: GDScript has NO catchable exceptions, so a build() that hits a
	# runtime fault (null deref, bad call) does NOT raise here -- the engine prints a
	# `SCRIPT ERROR: ... at: build (gdscript://...:LINE)` block to stderr and the call
	# just aborts, leaving a half-built world. We therefore CANNOT surface it as an
	# error frame in-process; the Python spawner mines the tee'd stderr DELTA per op
	# (harness/verify/gd_exec.parse_runtime_errors + read_stderr_delta) and attaches the
	# real cause python-side (run_check overrides build.ok, run_batch sets rec.error).
	inst.build(world_seed)
	_game = inst
	# Capture the declared action set + world size, and pre-register every checkpoint
	# key as unlatched so frames report the full milestone map from t=0.
	_actions_cache = _read_actions()
	_read_world_size()
	_compute_play_bounds()
	_latches = {}
	var cps = _safe_checkpoints()
	for k in cps.keys():
		_latches[str(k)] = null
	_applied = 0
	_result = ""
	_done_term = false
	_done_trunc = false
	_frozen = false
	_nan = false
	_ray_heading.erase(0)               # fresh heading for the (single-instance) episode
	return ""


func _read_actions() -> Array:
	var a = _game.actions()
	var out := []
	if typeof(a) == TYPE_ARRAY:
		for v in a:
			out.append(str(v))
	return out


func _read_world_size() -> void:
	_world_w = DEFAULT_W
	_world_h = DEFAULT_H
	var st = _safe_state()
	var ws = st.get("world_size", null)
	if typeof(ws) == TYPE_ARRAY and ws.size() == 2:
		_world_w = float(ws[0])
		_world_h = float(ws[1])


func _compute_play_bounds() -> void:
	# Play area = union(world box, every t=0 body position) padded by PLAY_MARGIN. A
	# runaway CONTROLLED body that leaves it truncates the episode (Elias directive 2)
	# rather than being flagged an escape/break; an unbounded world is not a failure.
	var b := _play_bounds_from_state(_safe_state())
	_play_min_x = b[0]
	_play_min_y = b[1]
	_play_max_x = b[2]
	_play_max_y = b[3]


func _play_bounds_from_state(st: Dictionary) -> Array:
	var min_x := 0.0
	var min_y := 0.0
	var max_x := _world_w
	var max_y := _world_h
	var bodies = st.get("bodies", [])
	if typeof(bodies) == TYPE_ARRAY:
		for b in bodies:
			if typeof(b) != TYPE_DICTIONARY:
				continue
			var p = b.get("pos", [])
			if typeof(p) != TYPE_ARRAY or p.size() < 2:
				continue
			var px := float(p[0])
			var py := float(p[1])
			min_x = min(min_x, px)
			min_y = min(min_y, py)
			max_x = max(max_x, px)
			max_y = max(max_y, py)
	return [min_x - PLAY_MARGIN, min_y - PLAY_MARGIN, max_x + PLAY_MARGIN, max_y + PLAY_MARGIN]


func _controlled_out_of_bounds(st: Dictionary, min_x: float, min_y: float,
		max_x: float, max_y: float) -> bool:
	# True once the CONTROLLED body's x,y centre leaves [min..max] (the depth axis of a
	# 3D game is not boxed, matching the in-bounds plane). Only the controlled body
	# triggers truncation -- a NON-controlled body leaving is a containment escape,
	# reported via oob, never a truncation.
	var bodies = st.get("bodies", [])
	if typeof(bodies) != TYPE_ARRAY:
		return false
	for b in bodies:
		if typeof(b) != TYPE_DICTIONARY or not bool(b.get("controlled", false)):
			continue
		var p = b.get("pos", [])
		if typeof(p) != TYPE_ARRAY or p.size() < 2:
			continue
		var px := float(p[0])
		var py := float(p[1])
		if px < min_x or px > max_x or py < min_y or py > max_y:
			return true
	return false


func _left_play_bounds() -> bool:
	return _controlled_out_of_bounds(_safe_state(), _play_min_x, _play_min_y,
		_play_max_x, _play_max_y)


func _safe_state() -> Dictionary:
	var st = _game.state()
	return st if typeof(st) == TYPE_DICTIONARY else {}


func _safe_checkpoints() -> Dictionary:
	var c = _game.checkpoints()
	return c if typeof(c) == TYPE_DICTIONARY else {}


# =========================================================================== #
# Stepping (act) -- mirrors runner.gd's per-tick body: act + K=6 + latch + terminal
# =========================================================================== #
func _op_act(msg: Dictionary) -> String:
	if _batched:
		return await _op_act_batch(msg)
	if _game == null:
		return '{"ok":false,"error":"no game (call init first)"}'
	var actions_list = msg.get("actions", [])
	if typeof(actions_list) != TYPE_ARRAY:
		actions_list = []
	var n_ticks := int(msg.get("n_ticks", actions_list.size()))
	var margin := float(msg.get("escape_margin", 0.0))
	# frames_every>0 -> capture a per-tick {tick, entities:{...}} frame every N
	# decision ticks (plus t=0 and the terminal tick), mirroring the js/py
	# frame doc so the replay/render lanes read it unchanged. 0 (default) keeps
	# batch mode byte-identical: no frames captured, no "frames" key emitted.
	var frames_every := int(msg.get("frames_every", 0))
	var frames_out := PackedStringArray()
	await _do_ticks(actions_list, n_ticks, frames_every, frames_out)
	var frames_json := ""
	if frames_every > 0:
		frames_json = "[%s]" % ",".join(frames_out)
	return _frame_json(false, margin, frames_json)


func _do_ticks(actions_list: Array, n_ticks: int, frames_every: int,
		frames_out: PackedStringArray) -> void:
	if _done_term or _done_trunc:
		return
	var last_frame := -1
	if frames_every > 0:
		frames_out.append(_tick_frame_json(_applied))       # t=0 (post-reset) frame
		last_frame = _applied
	for i in range(n_ticks):
		var action = null
		if actions_list.size() > 0:
			action = actions_list[i] if i < actions_list.size() else actions_list[actions_list.size() - 1]
		if action != null:
			ChordUtil.apply(_game, action)      # String verb OR Array chord, canonical order
		_applied += 1
		for k in range(K_STEPS):
			await physics_frame
			if not _sane():
				_frozen = true
				_nan = true
				break
		_latch()
		if _frozen:
			# Broken physics: stop WITHOUT capturing the NaN frame (mirrors the
			# py executor's error path, which returns frames captured so far).
			_result = "error"
			_done_term = true
			break
		if frames_every > 0 and (_applied % frames_every) == 0:
			frames_out.append(_tick_frame_json(_applied))
			last_frame = _applied
		if _truthy(_game.is_failure()):
			_result = "failure"
			_done_term = true
			break
		if _truthy(_game.is_success()):
			_result = "success"
			_done_term = true
			break
		if _left_play_bounds():
			_done_trunc = true              # runaway -> clean truncation (directive 2)
			break
		if _applied >= _horizon:
			_done_trunc = true
			break
	# Ensure the terminal tick is represented (a period-N sampling can miss it).
	if frames_every > 0 and not _frozen and last_frame != _applied:
		frames_out.append(_tick_frame_json(_applied))


func _latch() -> void:
	var cps = _safe_checkpoints()
	for key in cps.keys():
		var k := str(key)
		if not _latches.has(k):
			_latches[k] = null
		if _latches[k] == null and _truthy(cps[key]):
			_latches[k] = _applied


func _sane() -> bool:
	var st = _safe_state()
	var bodies = st.get("bodies", [])
	if typeof(bodies) != TYPE_ARRAY:
		return true
	for b in bodies:
		if typeof(b) != TYPE_DICTIONARY or bool(b.get("static", false)):
			continue
		var p = b.get("pos", [])
		var v = b.get("vel", [])
		if typeof(p) != TYPE_ARRAY or typeof(v) != TYPE_ARRAY:
			continue
		# Every position component finite; full velocity magnitude under VMAX -- across
		# 2 OR 3 components, so a 3D explosion (large vz) is caught like a 2D one.
		for c in p:
			if not is_finite(float(c)):
				return false
		var sq := 0.0
		for c in v:
			var cv := float(c)
			if not is_finite(cv):
				return false
			sq += cv * cv
		if sqrt(sq) > VMAX:
			return false
	return true


# =========================================================================== #
# init / reset ops
# =========================================================================== #
func _op_init(msg: Dictionary) -> String:
	_source = str(msg.get("source", ""))
	_horizon = int(msg.get("horizon", SERVE_DEFAULT_HORIZON))
	if _source.strip_edges() == "":
		return '{"ok":false,"error":"empty game source"}'
	# Compile ONCE; every reset re-instantiates from the cached _script.
	var comp := _compile_source(_source)
	if not comp.ok:
		return '{"ok":false,"error":"%s"}' % _esc(comp.error)
	_script = comp.script
	_parse_rays(msg.get("rays", null))      # opt-in egocentric raycast obs (no-op if absent)
	# An explicit n_instances key (even ==1) selects the BATCHED array-frame path; its
	# ABSENCE keeps the legacy single-instance scalar frame byte-identical to before.
	_batched = msg.has("n_instances")
	if _batched:
		_n_instances = max(1, int(msg.get("n_instances", 1)))
		return await _batch_init(msg)
	_build_err = await _rebuild(int(msg.get("seed", 0)))
	if _build_err != "":
		return '{"ok":false,"error":"%s"}' % _esc("build failed: " + _build_err)
	return _frame_json(true, 0.0)


func _op_reset(msg: Dictionary) -> String:
	if _batched:
		return _op_reset_batch(msg)             # synchronous: no physics stepped on reset
	if _source == "":
		return '{"ok":false,"error":"no game loaded"}'
	_build_err = await _rebuild(int(msg.get("seed", 0)))
	if _build_err != "":
		return '{"ok":false,"error":"%s"}' % _esc("build failed: " + _build_err)
	return _frame_json(false, 0.0)


# =========================================================================== #
# Batched (in-scene) stepping -- N independent worlds over ONE socket.
#
# Each instance lives under its OWN SubViewport world (a fresh World2D -> isolated 2D
# physics space; own_world_3d -> isolated 3D space), so the N worlds never interact and
# instance i stays byte-identical to a lone single-instance run at seed base_seed + i
# (the determinism tests pin this). A single `await physics_frame` steps ALL N spaces at
# once, so the K-frame decision-tick burst and the socket round-trip are shared across N.
# The batched frame is ARRAYS (obs_state[N], checkpoints[N], done_term[N], ...); the
# single-instance frame above is untouched, so N=1 (no n_instances key) is byte-identical.
# =========================================================================== #
func _batch_new_viewport() -> SubViewport:
	# One PERSISTENT SubViewport per instance -> one isolated physics world (a fresh
	# World2D -> its own 2D space; own_world_3d -> its own 3D space). The viewport (and
	# its space) is reused across episodes -- exactly like the single-instance path reuses
	# root's world -- so a reset only rebuilds the GAME node inside it.
	var vp := SubViewport.new()
	vp.world_2d = World2D.new()                     # isolated 2D physics space
	vp.render_target_update_mode = SubViewport.UPDATE_DISABLED
	vp.size = Vector2i(1, 1)
	root.add_child(vp)
	return vp


func _batch_build_game(vp: SubViewport, inst_seed: int) -> Dictionary:
	# Instantiate + add the game under `vp` + build(seed). Returns {ok, error, game}. The
	# host grants the game nothing but tree membership (in vp's private world) + the seed.
	if _script == null:
		return {"ok": false, "error": "no compiled game script"}
	var made := _instantiate(_script)
	if not made.ok:
		return {"ok": false, "error": made.error}
	var inst = made.instance
	var missing := _missing_methods(inst)
	if not missing.is_empty():
		inst.free()
		return {"ok": false, "error": "missing contract method(s): " + ", ".join(missing)}
	# Isolate the 3D space ONLY for a 3D game -- a 2D game (the common case) would else
	# pay for an empty per-instance World3D space stepped every frame for nothing.
	vp.own_world_3d = inst is Node3D
	# 3D DETERMINISM PIN (notes/engines/DETERMINISM_3D.md). The batched path REUSES each
	# instance's viewport (its World3D) across episodes, so a 3D instance inherits the same
	# reused-space GodotPhysics3D state leak the single-instance path had -- hand every 3D
	# (re)build a FRESH World3D so no solver/broadphase residual crosses the reset boundary.
	# 2D (vp.world_2d, set once in _batch_new_viewport) is untouched -> 2D stays identical.
	if inst is Node3D:
		vp.world_3d = World3D.new()
	vp.add_child(inst)
	inst.build(inst_seed)
	return {"ok": true, "error": "", "game": inst}


func _batch_teardown() -> void:
	for vp in _viewports:
		if vp != null and is_instance_valid(vp):
			vp.queue_free()
	_viewports = []
	_games = []


func _batch_init(msg: Dictionary) -> String:
	# Build N INDEPENDENT copies of the game, instance i seeded base_seed + i. Handshake
	# rides on the first batched frame (actions/world size captured from instance 0, which
	# is shared -- same source -> same declared actions + world extent).
	_base_seed = int(msg.get("base_seed", msg.get("seed", 0)))
	_batch_teardown()
	await process_frame                             # flush any prior deferred free
	_games = []
	_viewports = []
	_latches_arr = []
	_applied_arr = []
	_result_arr = []
	_done_term_arr = []
	_done_trunc_arr = []
	_frozen_arr = []
	_nan_arr = []
	_play_bounds_arr = []
	for i in range(_n_instances):
		var vp := _batch_new_viewport()
		var r := _batch_build_game(vp, _base_seed + i)
		if not r.ok:
			return '{"ok":false,"error":"%s"}' % _esc(
				"build failed (instance %d): %s" % [i, str(r.error)])
		_viewports.append(vp)
		_games.append(r.game)
		_applied_arr.append(0)
		_result_arr.append("")
		_done_term_arr.append(false)
		_done_trunc_arr.append(false)
		_frozen_arr.append(false)
		_nan_arr.append(false)
		_play_bounds_arr.append([])         # filled after world size is known (below)
		var latches := {}
		for k in _safe_checkpoints_of(r.game).keys():
			latches[str(k)] = null
		_latches_arr.append(latches)
	_actions_cache = _read_actions_of(_games[0])
	_read_world_size_of(_games[0])
	for i in range(_n_instances):
		_play_bounds_arr[i] = _play_bounds_from_state(_safe_state_of(_games[i]))
	return _batch_frame_json(true)


func _op_reset_batch(msg: Dictionary) -> String:
	# Reset a SUBSET of instances (SB3 per-instance autoreset) or ALL (vec-env reset()).
	# Instance i is rebuilt at seed base_seed + i (its FIXED per-slot seed, like the
	# DummyVecEnv path), unless the op carries an explicit `seeds` list (test hook).
	if _games.is_empty():
		return '{"ok":false,"error":"no game loaded"}'
	var base := int(msg.get("base_seed", _base_seed))
	_base_seed = base
	var instances = msg.get("instances", null)
	var seeds = msg.get("seeds", null)
	var idx_list := []
	if typeof(instances) == TYPE_ARRAY:
		for x in instances:
			idx_list.append(int(x))
	else:
		for i in range(_n_instances):
			idx_list.append(i)
	for j in range(idx_list.size()):
		var i := int(idx_list[j])
		if i < 0 or i >= _n_instances:
			continue
		var inst_seed := base + i
		if typeof(seeds) == TYPE_ARRAY and j < seeds.size():
			inst_seed = int(seeds[j])
		var err := _batch_rebuild_instance(i, inst_seed)
		if err != "":
			return '{"ok":false,"error":"%s"}' % _esc(
				"build failed (instance %d): %s" % [i, str(err)])
	return _batch_frame_json(false)


func _batch_rebuild_instance(i: int, inst_seed: int) -> String:
	# Reuse the PERSISTENT viewport (its isolated space) and rebuild only the game node.
	# Immediate free() (NOT queue_free + await): an await here would step physics on the
	# OTHER (live) instances during a partial autoreset, desyncing them from a clean run.
	# free() is safe -- we are in the serve op handler BETWEEN frames (the busy-wait read
	# froze the world), never inside a physics callback.
	var old_game = _games[i]
	if old_game != null and is_instance_valid(old_game):
		old_game.free()
	var r := _batch_build_game(_viewports[i], inst_seed)
	if not r.ok:
		return str(r.error)
	_games[i] = r.game
	_applied_arr[i] = 0
	_result_arr[i] = ""
	_done_term_arr[i] = false
	_done_trunc_arr[i] = false
	_frozen_arr[i] = false
	_nan_arr[i] = false
	_ray_heading.erase(i)               # fresh heading for the reset instance
	_play_bounds_arr[i] = _play_bounds_from_state(_safe_state_of(r.game))
	var latches := {}
	for k in _safe_checkpoints_of(r.game).keys():
		latches[str(k)] = null
	_latches_arr[i] = latches
	return ""


func _op_act_batch(msg: Dictionary) -> String:
	if _games.is_empty():
		return '{"ok":false,"error":"no game (call init first)"}'
	var actions_list = msg.get("actions", [])
	if typeof(actions_list) != TYPE_ARRAY:
		actions_list = []
	var n_ticks := int(msg.get("n_ticks", 1))
	await _batch_do_ticks(actions_list, n_ticks)
	return _batch_frame_json(false)


func _batch_do_ticks(actions_list: Array, n_ticks: int) -> void:
	# One decision tick = act(one action per LIVE instance) + K=6 physics frames (shared
	# across all worlds) + per-instance latch + terminal. Mirrors the single-instance
	# _do_ticks body exactly, per instance, so instance i matches a lone run tick for tick.
	for _t in range(n_ticks):
		for i in range(_n_instances):
			if _done_term_arr[i] or _done_trunc_arr[i]:
				continue
			var action = null
			if i < actions_list.size():
				action = actions_list[i]
			if action != null:
				ChordUtil.apply(_games[i], action)   # String verb OR Array chord, canonical order
			_applied_arr[i] += 1
		for k in range(K_STEPS):
			await physics_frame                     # steps EVERY instance's space at once
		# NaN/explosion check ONCE after the K-frame burst (not per frame): the batched
		# loop can't early-break a single world anyway, and state() per frame per instance
		# is the batch's hot path. A NaN persists, so a single post-burst check still
		# catches an exploded world (the single-instance path checks per frame only to
		# break early, which a shared loop cannot do).
		for i in range(_n_instances):
			if _done_term_arr[i] or _done_trunc_arr[i]:
				continue
			if not _sane_of(_games[i]):
				_frozen_arr[i] = true
				_nan_arr[i] = true
		for i in range(_n_instances):
			if _done_term_arr[i] or _done_trunc_arr[i]:
				continue
			_latch_i(i)
			if _frozen_arr[i]:
				_result_arr[i] = "error"
				_done_term_arr[i] = true
				continue
			if _truthy(_games[i].is_failure()):
				_result_arr[i] = "failure"
				_done_term_arr[i] = true
				continue
			if _truthy(_games[i].is_success()):
				_result_arr[i] = "success"
				_done_term_arr[i] = true
				continue
			if _left_play_bounds_i(i):
				_done_trunc_arr[i] = true   # runaway -> clean truncation (directive 2)
				continue
			if _applied_arr[i] >= _horizon:
				_done_trunc_arr[i] = true


func _left_play_bounds_i(i: int) -> bool:
	var pb = _play_bounds_arr[i]
	if typeof(pb) != TYPE_ARRAY or pb.size() < 4:
		return false
	return _controlled_out_of_bounds(_safe_state_of(_games[i]),
		float(pb[0]), float(pb[1]), float(pb[2]), float(pb[3]))


func _latch_i(i: int) -> void:
	var cps = _safe_checkpoints_of(_games[i])
	var latches = _latches_arr[i]
	for key in cps.keys():
		var k := str(key)
		if not latches.has(k):
			latches[k] = null
		if latches[k] == null and _truthy(cps[key]):
			latches[k] = _applied_arr[i]


# Game-parameterised twins of the single-instance _safe_state/_safe_checkpoints/
# _read_actions/_read_world_size/_sane helpers (which read the _game member). Kept
# separate so the single-instance functions above stay byte-identical.
func _safe_state_of(game: Node) -> Dictionary:
	var st = game.state()
	return st if typeof(st) == TYPE_DICTIONARY else {}


func _safe_checkpoints_of(game: Node) -> Dictionary:
	var c = game.checkpoints()
	return c if typeof(c) == TYPE_DICTIONARY else {}


func _read_actions_of(game: Node) -> Array:
	var a = game.actions()
	var out := []
	if typeof(a) == TYPE_ARRAY:
		for v in a:
			out.append(str(v))
	return out


func _read_world_size_of(game: Node) -> void:
	_world_w = DEFAULT_W
	_world_h = DEFAULT_H
	var st = _safe_state_of(game)
	var ws = st.get("world_size", null)
	if typeof(ws) == TYPE_ARRAY and ws.size() == 2:
		_world_w = float(ws[0])
		_world_h = float(ws[1])


func _sane_of(game: Node) -> bool:
	var st = _safe_state_of(game)
	var bodies = st.get("bodies", [])
	if typeof(bodies) != TYPE_ARRAY:
		return true
	for b in bodies:
		if typeof(b) != TYPE_DICTIONARY or bool(b.get("static", false)):
			continue
		var p = b.get("pos", [])
		var v = b.get("vel", [])
		if typeof(p) != TYPE_ARRAY or typeof(v) != TYPE_ARRAY:
			continue
		for c in p:
			if not is_finite(float(c)):
				return false
		var sq := 0.0
		for c in v:
			var cv := float(c)
			if not is_finite(cv):
				return false
			sq += cv * cv
		if sqrt(sq) > VMAX:
			return false
	return true


func _entities_json_of(game: Node) -> String:
	var st = _safe_state_of(game)
	var bodies = st.get("bodies", [])
	var parts := PackedStringArray()
	if typeof(bodies) == TYPE_ARRAY:
		for b in bodies:
			if typeof(b) != TYPE_DICTIONARY:
				continue
			parts.append('"%s":%s' % [_esc(str(b.get("name", ""))), _body_obs_json(b)])
	return "{%s}" % ",".join(parts)


func _checkpoints_json_i(i: int) -> String:
	var parts := PackedStringArray()
	var latches = _latches_arr[i]
	for key in latches.keys():
		var t = latches[key]
		var val := "null" if t == null else str(int(t))
		parts.append('"%s":%s' % [_esc(str(key)), val])
	return "{%s}" % ",".join(parts)


func _oob_json_of(game: Node, margin: float) -> String:
	var out := PackedStringArray()
	var st = _safe_state_of(game)
	var bodies = st.get("bodies", [])
	if typeof(bodies) == TYPE_ARRAY:
		for b in bodies:
			# Controlled body leaving = truncation, not escape (directive 2); only a
			# NON-controlled body escaping its containment is reported.
			if typeof(b) != TYPE_DICTIONARY or bool(b.get("static", false)) \
					or bool(b.get("controlled", false)):
				continue
			var p = b.get("pos", [0.0, 0.0])
			if typeof(p) != TYPE_ARRAY or p.size() < 2:
				continue
			var px := float(p[0]); var py := float(p[1])
			if px < -margin or py < -margin or px > _world_w + margin or py > _world_h + margin:
				out.append('"%s"' % _esc(str(b.get("name", ""))))
	return ",".join(out)


func _batch_frame_json(with_handshake: bool) -> String:
	# ARRAY frame: every field is a length-N list indexed by instance. world_size + actions
	# are shared (same source). n_instances marks the batched frame for the Python reader.
	var obs_parts := PackedStringArray()
	var cp_parts := PackedStringArray()
	var tick_parts := PackedStringArray()
	var result_parts := PackedStringArray()
	var term_parts := PackedStringArray()
	var trunc_parts := PackedStringArray()
	var nan_parts := PackedStringArray()
	var oob_parts := PackedStringArray()
	var rays_parts := PackedStringArray()
	for i in range(_n_instances):
		obs_parts.append(_entities_json_of(_games[i]))
		cp_parts.append(_checkpoints_json_i(i))
		tick_parts.append("%d" % int(_applied_arr[i]))
		var res := "null"
		if str(_result_arr[i]) != "":
			res = '"%s"' % str(_result_arr[i])
		result_parts.append(res)
		term_parts.append(_b(bool(_done_term_arr[i])))
		trunc_parts.append(_b(bool(_done_trunc_arr[i])))
		nan_parts.append(_b(bool(_nan_arr[i])))
		oob_parts.append("[%s]" % _oob_json_of(_games[i], 0.0))
		if _rays_on:
			rays_parts.append(_rays_json_of(_games[i], i))
	var head := ""
	if with_handshake:
		head = '"ok":true,"actions":%s,' % _actions_json()
	# rays_part is "" unless init opted in (byte-identical batched wire when off); when
	# on it appends a per-instance array-of-arrays after "oob" so leading keys never shift.
	var rays_part := ""
	if _rays_on:
		rays_part = ',"rays":[%s]' % ",".join(rays_parts)
	return ('{%s"n_instances":%d,"obs_state":[%s],"checkpoints":[%s],"tick":[%s],'
		+ '"result":[%s],"done_term":[%s],"done_trunc":[%s],'
		+ '"world_size":[%s,%s],"nan":[%s],"oob":[%s]%s,"error":null}') % [
		head, _n_instances, ",".join(obs_parts), ",".join(cp_parts),
		",".join(tick_parts), ",".join(result_parts), ",".join(term_parts),
		",".join(trunc_parts), _num(_world_w), _num(_world_h),
		",".join(nan_parts), ",".join(oob_parts), rays_part]


# =========================================================================== #
# Egocentric raycasts (OPT-IN). A deterministic fan cast FROM the controlled body IN
# ITS LOCAL FRAME via direct_space_state.intersect_ray -- read-only, so it steps no
# physics and mutates no state (a twin rollout is byte-identical WITH rays on). Cast at
# the SAME state() sampling instant the frame is built. The body itself is excluded, and
# a missing body / unavailable space degrades to all-1.0 (nothing seen), never an error.
#
# LAYOUT (documented for the obs consumer) -- standardized on the godot_rl_agents FPS
# reference sensor (examples player.tscn WideRaycastSensor + ExtendedRaycastSensor.gd):
#   2D -- a planar fan of `n` rays across `fov_deg`, centered on the body's facing (local
#         +X, transform.x -- the direction the reported `angle` points). The world IS a plane.
#   3D -- a WIDE DEPTH RETINA: a rectangular `n_h x n_v` (default 25x5) grid across
#         `fov_h x fov_v` centered on forward (azimuth about the body's local up +Y basis,
#         pitch about local right +X basis; forward is local -Z, Godot's forward). Row-major,
#         vertical rows outer, exactly n_h*n_v rays. A single horizontal fan is vertically
#         blind (obstacles above/below) -- the grid sees both.
# PER-RAY FLOATS (the wire is a FLAT, interleaved array): the normalized distance (1.0 =
# nothing within `range`, else hit_distance/range in [0,1]) PLUS, when class_bits is on
# (default, matching the reference class channel), a {static, dynamic, sensor} one-hot from
# the collider type (all-zero on no hit). So each ray is 1 float, or 1+3=4 with class bits.
# Areas ARE hit (collide_with_areas=true) so goal/sensor pads read as the sensor class -- the
# SEMANTIC retina. FIRST-FRAME NOTE: the reset/init frame reads all-clear because the physics
# broadphase is only populated after the first step; every stepped frame is faithful.
# FRAME (ray_frame, default "auto"): while the body's rotation is LIVE the fan/grid is cast in
# its LOCAL frame (correct FPS facing). Once rotation is LOCKED (lock_rotation / all angular
# axis locks) the body never turns to face its travel, so a body-local retina would stare at a
# FIXED world direction -- "auto" then casts in a HEADING frame (forward = velocity direction,
# holding its last value below RAYS_HEADING_EPS speed, initialized to the body's facing; 3D up =
# world up, right = orthonormal cross, degenerate forward|up -> keep body-local). Per-instance
# last-heading is derived purely from the deterministic trajectory (twin rollouts stay bit-exact)
# and reset on rebuild. "body" forces the body-local frame. Sensor-side only -- no game change.
# NARROW/FOVEA second tier (a denser tighter-fov grid, reference NarrowRaycastSensor 25x25):
# a documented FOLLOW-UP -- the same machinery casts a second grid and concatenates; deferred
# to keep this change bounded (it threads a second config block through host + obs sizing).
# =========================================================================== #
func _parse_rays(r) -> void:
	# Opt-in egocentric raycast config. Anything but a well-formed dict leaves rays OFF ->
	# the wire stays byte-identical to the pre-rays frame. Carries BOTH the 2D-fan params
	# (n, fov_deg) and the 3D-grid params (n_h, n_v, fov_h, fov_v); the host picks per the
	# controlled body's dimension. Range (world units) is shared.
	_rays_on = false
	if typeof(r) != TYPE_DICTIONARY:
		return
	_rays_n = clampi(int(r.get("n", 16)), 0, RAYS_DIM_MAX)
	_rays_fov_deg = clampf(float(r.get("fov_deg", 180.0)), 1.0, 360.0)
	_rays_nh = clampi(int(r.get("n_h", 25)), 0, RAYS_DIM_MAX)
	_rays_nv = clampi(int(r.get("n_v", 5)), 0, RAYS_DIM_MAX)
	_rays_fov_h = clampf(float(r.get("fov_h", 120.0)), 1.0, 360.0)
	_rays_fov_v = clampf(float(r.get("fov_v", 60.0)), 1.0, 179.0)
	_rays_range = maxf(1.0, float(r.get("range", 80.0)))
	_rays_class_bits = bool(r.get("class_bits", true))
	_ray_frame = "body" if str(r.get("ray_frame", "auto")) == "body" else "auto"
	_ray_heading = {}                       # fresh sensor state per init
	# On only if SOME axis yields rays (2D fan n>0 OR 3D grid nh*nv>0).
	_rays_on = _rays_n > 0 or (_rays_nh > 0 and _rays_nv > 0)


func _rays_json_of(game, inst_idx: int) -> String:
	# JSON array of the flat per-ray floats (distance [+ class]) at the obs %.17f precision.
	var parts := PackedStringArray()
	for v in _cast_rays_of(game, inst_idx):
		parts.append(_f(float(v)))
	return "[%s]" % ",".join(parts)


func _append_ray(out: Array, dist_norm: float, collider) -> void:
	# One ray's obs floats: the normalized distance, then (when class_bits on) a
	# {static, dynamic, sensor} one-hot from the collider type. No hit -> all-zero class.
	out.append(dist_norm)
	if not _rays_class_bits:
		return
	var is_static := 0.0
	var is_dynamic := 0.0
	var is_sensor := 0.0
	if collider != null:
		if collider is Area2D or collider is Area3D:
			is_sensor = 1.0
		elif collider is StaticBody2D or collider is StaticBody3D:
			is_static = 1.0
		else:
			is_dynamic = 1.0        # RigidBody/CharacterBody/AnimatableBody/etc.
	out.append(is_static)
	out.append(is_dynamic)
	out.append(is_sensor)


func _all_clear_rays(n_rays: int) -> Array:
	# n_rays rays, each all-clear (distance 1.0, no class) -> n_rays * ray_stride floats.
	var out := []
	for i in range(n_rays):
		_append_ray(out, 1.0, null)
	return out


func _game_dim_of(game) -> int:
	# The game's dimension from the first body's pos arity in state() (>=3 -> 3D), so the
	# ray COUNT (2D fan vs 3D grid) is known even when no controlled node is found.
	for b in _safe_state_of(game).get("bodies", []):
		if typeof(b) != TYPE_DICTIONARY:
			continue
		var p = b.get("pos", [])
		if typeof(p) == TYPE_ARRAY and p.size() > 0:
			return 3 if p.size() >= 3 else 2
	return 2


func _expected_ray_count(game) -> int:
	return (_rays_nh * _rays_nv) if _game_dim_of(game) == 3 else _rays_n


func _cast_rays_of(game, inst_idx: int) -> Array:
	# The deterministic fan/grid -> Array[float] in [0,1]. Length: 2D fan _rays_n, 3D grid
	# _rays_nh*_rays_nv (the depth retina). A missing body -> all-1.0 at the right count.
	# inst_idx keys the per-instance heading state (single-instance path uses 0).
	if not _rays_on:
		return []
	if game == null or not is_instance_valid(game):
		return _all_clear_rays(_expected_ray_count(game))
	var nm := _controlled_name_of_game(game)
	var node := _controlled_body_node(game, nm)
	if node == null or not is_instance_valid(node):
		return _all_clear_rays(_expected_ray_count(game))   # no body to cast from -> clear
	if node is CollisionObject3D:
		return _cast_rays_3d(node, inst_idx)
	if node is CollisionObject2D:
		return _cast_rays_2d(node, inst_idx)
	return _all_clear_rays(_expected_ray_count(game))


func _rotation_locked(node) -> bool:
	# True when the body's rotation is LOCKED (so its facing is fixed and a body-local retina
	# would stare at a fixed world direction). 3D: RigidBody3D.lock_rotation OR all three
	# axis_lock_angular_* set. 2D: RigidBody2D.lock_rotation. Missing props (CharacterBody,
	# etc.) -> not locked (rotation is game-driven, so body-local facing is meaningful).
	var lr = node.get("lock_rotation")
	if typeof(lr) == TYPE_BOOL and lr:
		return true
	if node is CollisionObject3D:
		var ax = node.get("axis_lock_angular_x")
		var ay = node.get("axis_lock_angular_y")
		var az = node.get("axis_lock_angular_z")
		if typeof(ax) == TYPE_BOOL and typeof(ay) == TYPE_BOOL and typeof(az) == TYPE_BOOL:
			return ax and ay and az
	return false


func _body_velocity_3d(node) -> Vector3:
	var v = node.get("linear_velocity")     # RigidBody3D
	if typeof(v) == TYPE_VECTOR3:
		return v
	v = node.get("velocity")                # CharacterBody3D
	if typeof(v) == TYPE_VECTOR3:
		return v
	return Vector3.ZERO


func _body_velocity_2d(node) -> Vector2:
	var v = node.get("linear_velocity")     # RigidBody2D
	if typeof(v) == TYPE_VECTOR2:
		return v
	v = node.get("velocity")                # CharacterBody2D
	if typeof(v) == TYPE_VECTOR2:
		return v
	return Vector2.ZERO


func _controlled_name_of_game(game) -> String:
	for b in _safe_state_of(game).get("bodies", []):
		if typeof(b) == TYPE_DICTIONARY and bool(b.get("controlled", false)):
			return str(b.get("name", ""))
	return ""


func _controlled_pos_of_game(game) -> Array:
	for b in _safe_state_of(game).get("bodies", []):
		if typeof(b) == TYPE_DICTIONARY and bool(b.get("controlled", false)):
			var p = b.get("pos", [])
			if typeof(p) == TYPE_ARRAY:
				return p
	return []


func _collect_collision_objects(node: Node, out: Array) -> void:
	for c in node.get_children():
		if c is CollisionObject2D or c is CollisionObject3D:
			out.append(c)
		_collect_collision_objects(c, out)


func _controlled_body_node(game, nm: String) -> Node:
	# The controlled body's physics node. Fast path: a CollisionObject descendant whose
	# node name == the state-reported controlled name (true for our games, which set
	# node.name to that string). Fallback: the CollisionObject nearest the reported pos --
	# so a game that names its node differently still casts from the right place.
	var cands := []
	_collect_collision_objects(game, cands)
	if cands.is_empty():
		return null
	if nm != "":
		for c in cands:
			if str(c.name) == nm:
				return c
	var target := _controlled_pos_of_game(game)
	if target.is_empty():
		return cands[0]
	var best: Node = null
	var best_d := INF
	for c in cands:
		var gp := _node_global_pos_arr(c)
		if gp.is_empty():
			continue
		var d := _pos_dist2(gp, target)
		if d < best_d:
			best_d = d
			best = c
	return best if best != null else cands[0]


func _node_global_pos_arr(c) -> Array:
	if c is Node3D:
		var v: Vector3 = c.global_transform.origin
		return [v.x, v.y, v.z]
	if c is Node2D:
		var v2: Vector2 = c.global_position
		return [v2.x, v2.y]
	return []


func _pos_dist2(a: Array, b: Array) -> float:
	var n := mini(a.size(), b.size())
	var s := 0.0
	for i in range(n):
		var d := float(a[i]) - float(b[i])
		s += d * d
	return s


func _fan_azimuths_2d() -> Array:
	# N azimuth offsets (deg) evenly across fov, centered on forward (the RaycastSensor fan).
	var out := []
	var step := _rays_fov_deg / float(_rays_n)
	var start := step * 0.5 - _rays_fov_deg * 0.5
	for i in range(_rays_n):
		out.append(start + float(i) * step)
	return out


func _fan_dirs_3d() -> Array:
	# The DEPTH RETINA: a rectangular _rays_nh x _rays_nv grid of (azimuth, pitch) pairs
	# covering _rays_fov_h x _rays_fov_v centered on forward. Row-major (vertical rows
	# outer, horizontal columns inner), exactly _rays_nh*_rays_nv rays. Deterministic order.
	var out := []
	var az_step := _rays_fov_h / float(maxi(1, _rays_nh))
	var az_start := az_step * 0.5 - _rays_fov_h * 0.5
	var pt_step := _rays_fov_v / float(maxi(1, _rays_nv))
	var pt_start := pt_step * 0.5 - _rays_fov_v * 0.5
	for iv in range(_rays_nv):
		var pitch := pt_start + float(iv) * pt_step
		for ih in range(_rays_nh):
			out.append([az_start + float(ih) * az_step, pitch])
	return out


func _cast_rays_3d(node, inst_idx: int) -> Array:
	var n_grid := _rays_nh * _rays_nv
	var vp: Viewport = node.get_viewport()
	if vp == null:
		return _all_clear_rays(n_grid)
	var world: World3D = vp.find_world_3d()
	if world == null:
		return _all_clear_rays(n_grid)
	var space: PhysicsDirectSpaceState3D = world.direct_space_state
	if space == null:
		return _all_clear_rays(n_grid)
	var xform: Transform3D = node.global_transform
	var basis := xform.basis.orthonormalized()
	var origin: Vector3 = xform.origin
	# Body-local frame (Godot forward = -Z). In "auto" mode a LOCKED-rotation body switches
	# to a HEADING frame (forward = velocity), so its retina looks down-travel, not at a fixed
	# world axis its non-rotating basis happens to point at.
	var fwd: Vector3 = -basis.z
	var up: Vector3 = basis.y
	var right: Vector3 = basis.x
	if _ray_frame == "auto" and _rotation_locked(node):
		var last: Vector3 = _ray_heading.get(inst_idx, fwd)     # init to body facing
		var vel := _body_velocity_3d(node)
		var hf := last
		if vel.length() > RAYS_HEADING_EPS:
			hf = vel.normalized()
		_ray_heading[inst_idx] = hf
		# Orthonormal frame about world up; degenerate (hf ~parallel to up) -> keep body-local.
		var r := hf.cross(Vector3.UP)
		if r.length() > 1.0e-4:
			right = r.normalized()
			fwd = hf
			up = right.cross(fwd).normalized()
	var exclude: Array[RID] = [node.get_rid()]
	var out := []
	for pd in _fan_dirs_3d():
		var dir: Vector3 = fwd.rotated(right, deg_to_rad(pd[1])).rotated(up, deg_to_rad(pd[0]))
		var q := PhysicsRayQueryParameters3D.create(origin, origin + dir * _rays_range)
		q.collide_with_areas = true         # so goal/sensor AREAs are seen (class channel)
		q.collide_with_bodies = true
		q.exclude = exclude
		var hit := space.intersect_ray(q)
		if hit.is_empty():
			_append_ray(out, 1.0, null)
		else:
			var d := clampf(origin.distance_to(hit["position"]) / _rays_range, 0.0, 1.0)
			_append_ray(out, d, hit.get("collider"))
	return out


func _cast_rays_2d(node, inst_idx: int) -> Array:
	var vp: Viewport = node.get_viewport()
	if vp == null:
		return _all_clear_rays(_rays_n)
	var world: World2D = vp.find_world_2d()
	if world == null:
		return _all_clear_rays(_rays_n)
	var space: PhysicsDirectSpaceState2D = world.direct_space_state
	if space == null:
		return _all_clear_rays(_rays_n)
	var xform: Transform2D = node.global_transform
	var origin: Vector2 = xform.origin
	var fwd: Vector2 = xform.x.normalized()   # local +X (the direction `angle` points)
	if fwd.length() < 0.5:
		fwd = Vector2.RIGHT
	# "auto" + LOCKED rotation -> heading frame (forward = velocity), so a non-rotating 2D
	# mover's retina looks down-travel rather than at its fixed facing.
	if _ray_frame == "auto" and _rotation_locked(node):
		var last: Vector2 = _ray_heading.get(inst_idx, fwd)     # init to body facing
		var vel := _body_velocity_2d(node)
		if vel.length() > RAYS_HEADING_EPS:
			fwd = vel.normalized()
		else:
			fwd = last
		_ray_heading[inst_idx] = fwd
	var exclude: Array[RID] = [node.get_rid()]
	var out := []
	for az in _fan_azimuths_2d():
		var dir: Vector2 = fwd.rotated(deg_to_rad(az))
		var q := PhysicsRayQueryParameters2D.create(origin, origin + dir * _rays_range)
		q.collide_with_areas = true         # so goal/sensor AREAs are seen (class channel)
		q.collide_with_bodies = true
		q.exclude = exclude
		var hit := space.intersect_ray(q)
		if hit.is_empty():
			_append_ray(out, 1.0, null)
		else:
			var d := clampf(origin.distance_to(hit["position"]) / _rays_range, 0.0, 1.0)
			_append_ray(out, d, hit.get("collider"))
	return out


# =========================================================================== #
# Frame JSON (obs_state at full %.17f precision so two serve sessions match byte
# for byte; checkpoints/nan/oob mirror runner.gd's serve frame).
# =========================================================================== #
func _frame_json(with_handshake: bool, margin: float, frames_json := "") -> String:
	var obs := _entities_json()
	var res_str := "null"
	if _result != "":
		res_str = '"%s"' % _result
	var head := ""
	if with_handshake:
		head = '"ok":true,"actions":%s,' % _actions_json()
	var oob := _oob_json(margin)
	# frames_json is "" for every batch/init/reset/check call (byte-identical to
	# the pre-frames wire) and ',"frames":[...]' only when act captured per-tick
	# frames. It rides after "oob" so the leading keys never shift.
	var frames_part := ""
	if frames_json != "":
		frames_part = ',"frames":%s' % frames_json
	# rays_part is "" unless init opted in (byte-identical wire when off); when on it
	# rides AFTER frames so the leading keys never shift. Computed here, at the same
	# state() sampling instant as obs, so a twin rollout is byte-identical WITH rays on.
	var rays_part := ""
	if _rays_on:
		rays_part = ',"rays":%s' % _rays_json_of(_game, 0)
	# "error":null is hardcoded: a runtime SCRIPT ERROR inside the generated act()
	# aborts the call without raising, so it is undetectable in-process. The Python
	# executor attaches the real cause per-episode from the tee'd stderr delta
	# (harness/verify/gd_exec: run_batch sets rec.error). Keeping the wire byte stable
	# preserves single-instance byte-identity on clean runs.
	return ('{%s"obs_state":%s,"checkpoints":%s,"tick":%d,"result":%s,'
		+ '"done_term":%s,"done_trunc":%s,"world_size":[%s,%s],'
		+ '"nan":%s,"oob":[%s]%s%s,"error":null}') % [
		head, obs, _checkpoints_json(), _applied, res_str,
		_b(_done_term), _b(_done_trunc), _num(_world_w), _num(_world_h),
		_b(_nan), oob, frames_part, rays_part]


func _entities_json() -> String:
	# The '{name: body_obs}' map shared by the final obs_state and every per-tick
	# frame (byte-identical to the obs the pre-frames _frame_json built inline).
	var st = _safe_state()
	var bodies = st.get("bodies", [])
	var parts := PackedStringArray()
	if typeof(bodies) == TYPE_ARRAY:
		for b in bodies:
			if typeof(b) != TYPE_DICTIONARY:
				continue
			parts.append('"%s":%s' % [_esc(str(b.get("name", ""))), _body_obs_json(b)])
	return "{%s}" % ",".join(parts)


func _tick_frame_json(tick_no: int) -> String:
	# One replay frame: {tick, entities:{name:{pos,vel,angle,controlled,static}}} --
	# the js/py frame doc shape (harness/verify/executors.py::replay_frames_doc).
	return '{"tick":%d,"entities":%s}' % [tick_no, _entities_json()]


func _body_obs_json(b: Dictionary) -> String:
	return ('{"pos":%s,"vel":%s,"angle":%s,"controlled":%s,"static":%s}') % [
		_vec_json(b.get("pos", [])), _vec_json(b.get("vel", [])),
		_angle_json(b.get("angle", 0.0)),
		_b(bool(b.get("controlled", false))), _b(bool(b.get("static", false)))]


func _angle_json(a) -> String:
	# `angle` is the body's rotation in the game's own dimension: a scalar in 2D,
	# and in 3D either a scalar yaw or the natural [x,y,z] Euler vector. The old
	# `float(angle)` coercion CRASHED the frame builder on a vector (SCRIPT ERROR
	# mid-string -> truncated frame -> "unparseable" VERIFY_ERROR; it cost the
	# 2026-07-17 ambition probe all three of its 3D games). Scalar output is
	# byte-identical to the old path.
	if typeof(a) == TYPE_ARRAY:
		return _vec_json(a)
	return _f(float(a))


func _vec_json(a) -> String:
	# Emit a pos/vel vector at full %.17f precision, however many components the game
	# reports -- 2D ([x,y]) OR 3D ([x,y,z]). The obs is thus dimension-agnostic; the
	# Python-side snapshot deltas zip componentwise, so both lanes read it unchanged.
	# Byte-identical to the old "[%s,%s]" for a 2-vector.
	var parts := PackedStringArray()
	if typeof(a) == TYPE_ARRAY:
		for x in a:
			parts.append(_f(float(x)))
	return "[%s]" % ",".join(parts)


func _actions_json() -> String:
	var parts := PackedStringArray()
	for a in _actions_cache:
		parts.append('"%s"' % _esc(str(a)))
	return "[%s]" % ",".join(parts)


func _checkpoints_json() -> String:
	var parts := PackedStringArray()
	for key in _latches.keys():
		var t = _latches[key]
		var val := "null" if t == null else str(int(t))
		parts.append('"%s":%s' % [_esc(str(key)), val])
	return "{%s}" % ",".join(parts)


func _oob_json(margin: float) -> String:
	var out := PackedStringArray()
	var st = _safe_state()
	var bodies = st.get("bodies", [])
	if typeof(bodies) == TYPE_ARRAY:
		for b in bodies:
			# The CONTROLLED body leaving is a play-bounds TRUNCATION, not an escape
			# (Elias directive 2); only a NON-controlled dynamic body escaping its
			# required containment is reported here.
			if typeof(b) != TYPE_DICTIONARY or bool(b.get("static", false)) \
					or bool(b.get("controlled", false)):
				continue
			var p = b.get("pos", [0.0, 0.0])
			if typeof(p) != TYPE_ARRAY or p.size() < 2:
				continue
			var px := float(p[0]); var py := float(p[1])
			if px < -margin or py < -margin or px > _world_w + margin or py > _world_h + margin:
				out.append('"%s"' % _esc(str(b.get("name", ""))))
	return ",".join(out)


# =========================================================================== #
# CHECK op -- G0/G2 facts (parse gate + contract probe + t=0 purity probes),
# emitted in the SAME shape runner.js/runner.gd check emits so the shared Python
# run_g0_gd / run_g2_js layers consume it unchanged.
# =========================================================================== #
func _op_check(msg: Dictionary) -> String:
	var out := {"mode": "check", "scan": []}
	_source = str(msg.get("source", ""))

	# 1. Parse gate: in-memory compile.
	var comp := _compile_source(_source)
	if not comp.ok:
		out["load"] = {"ok": false, "error": comp.error}
		return JSON.stringify(out)
	out["load"] = {"ok": true, "error": null}
	_script = comp.script

	# 2. Contract probe: every required GameAPI method present.
	var made := _instantiate(_script)
	if not made.ok:
		out["load"] = {"ok": false, "error": made.error}
		return JSON.stringify(out)
	var inst = made.instance
	var methods := {}
	for m in REQUIRED_METHODS:
		methods[m] = inst.has_method(m)
	out["contract"] = {"methods": methods}
	var missing := _missing_methods(inst)
	if not missing.is_empty():
		inst.free()
		return JSON.stringify(out)

	# 3. Build the scene (fresh, seed 0), then probe t=0. The game self-seeds from
	# build()'s seed; the host only grants tree membership.
	# NB: build.ok is reported optimistically here -- a runtime crash inside build()
	# does not raise in GDScript, so this line runs regardless. The AUTHORITATIVE
	# build-ok is set python-side: run_check reads the stderr delta for a `build`-scoped
	# SCRIPT ERROR and overrides this to {ok:false} so a crashed build() stops
	# masquerading as a downstream "no controlled body" symptom.
	await process_frame
	root.add_child(inst)
	inst.build(0)
	_game = inst
	out["build"] = {"ok": true, "error": null}

	# 4. actions() well-formedness.
	var acts := _read_actions()
	var all_str := true
	var raw = inst.actions()
	if typeof(raw) == TYPE_ARRAY:
		for a in raw:
			if typeof(a) != TYPE_STRING:
				all_str = false
	else:
		all_str = false
	out["actions"] = {
		"is_list": typeof(raw) == TYPE_ARRAY,
		"length": acts.size() if typeof(raw) == TYPE_ARRAY else null,
		"all_str": all_str,
		"values": acts,
	}

	# 5. world size + entities/queries from state().
	_read_world_size()
	out["world_size"] = {"declared": [_world_w, _world_h], "effective": [_world_w, _world_h]}
	var st := _safe_state()
	var bodies = st.get("bodies", [])
	var entities := []
	var queries := {}
	if typeof(bodies) == TYPE_ARRAY:
		for b in bodies:
			if typeof(b) != TYPE_DICTIONARY:
				continue
			var name := str(b.get("name", ""))
			entities.append(name)
			queries[name] = {
				"static": bool(b.get("static", false)),
				"sensor": bool(b.get("sensor", false)),
				"controlled": bool(b.get("controlled", false)),
				"in_bounds": _center_in_bounds(b),
			}
	out["entities"] = entities
	out["queries"] = queries
	out["penetration"] = []             # no shape info in the GameAPI lane -> no probe

	# t=0 GEOMETRY for the G0.5 reachability pre-filter (Elias directive 1): each body's
	# pos + static/sensor/controlled flags + any footprint the game reports (aabb /
	# half_extents / radius). A body WITHOUT a footprint is a bare marker (never a wall);
	# the python side floods over the static footprints. Pure ADD to the check reply --
	# it never touches the act/episode frames, so serve determinism is unchanged.
	var geometry := []
	if typeof(bodies) == TYPE_ARRAY:
		for b in bodies:
			if typeof(b) == TYPE_DICTIONARY:
				geometry.append(_geometry_of(b))
	out["geometry"] = geometry

	# 6. Goal probes on the fresh t=0 world (predicates are pure/read-only).
	out["g2"] = {
		"success": _probe_bool("is_success"),
		"failure": _probe_bool("is_failure"),
		"checkpoints": _probe_checkpoints(),
	}

	_teardown()
	await process_frame
	return JSON.stringify(out)


func _geometry_of(b: Dictionary) -> Dictionary:
	# One body's t=0 geometry fact for the G0.5 reachability pre-filter: position +
	# static/sensor/controlled flags + whatever footprint the game reports (an explicit
	# `aabb`/`half_extents` or a `radius`). Footprint keys are optional; a body reporting
	# none is treated as a bare marker (a target region, never a wall) by the python side.
	var g := {
		"name": str(b.get("name", "")),
		"pos": b.get("pos", []),
		"static": bool(b.get("static", false)),
		"sensor": bool(b.get("sensor", false)),
		"controlled": bool(b.get("controlled", false)),
	}
	if b.has("aabb"):
		g["aabb"] = b.get("aabb")
	if b.has("half_extents"):
		g["half_extents"] = b.get("half_extents")
	if b.has("radius"):
		g["radius"] = b.get("radius")
	return g


func _center_in_bounds(b: Dictionary) -> bool:
	# Bounds are the [0,w]x[0,h] plane of the FIRST TWO position components (x, y);
	# a 3D game keeps that plane in bounds (extra components -- depth/height -- are not
	# boxed here, only checked finite by _sane). Accept >= 2 components (2D or 3D).
	var p = b.get("pos", [0.0, 0.0])
	if typeof(p) != TYPE_ARRAY or p.size() < 2:
		return false
	var px := float(p[0]); var py := float(p[1])
	return px >= 0.0 and py >= 0.0 and px <= _world_w and py <= _world_h


func _state_sig() -> String:
	# A signature of the mutable scene (pos/vel/angle) -> detects a predicate that
	# mutates the world (breaks purity).
	var st := _safe_state()
	var bodies = st.get("bodies", [])
	var parts := PackedStringArray()
	if typeof(bodies) == TYPE_ARRAY:
		for b in bodies:
			if typeof(b) != TYPE_DICTIONARY:
				continue
			parts.append('%s:%s' % [str(b.get("name", "")), _body_obs_json(b)])
	return "|".join(parts)


func _probe_bool(method_name: String) -> Dictionary:
	var before := _state_sig()
	var r1 = _game.call(method_name)
	var r2 = _game.call(method_name)
	var after := _state_sig()
	var is_bool := typeof(r1) == TYPE_BOOL and typeof(r2) == TYPE_BOOL
	return {
		"is_bool": is_bool,
		"value": (r1 if is_bool else null),
		"deterministic": r1 == r2,
		"state_unchanged": before == after,
		"error": null,
	}


func _probe_checkpoints() -> Dictionary:
	var before := _state_sig()
	var c1 = _game.checkpoints()
	var c2 = _game.checkpoints()
	var after := _state_sig()
	if typeof(c1) != TYPE_DICTIONARY or typeof(c2) != TYPE_DICTIONARY:
		return {"is_dict": false, "keys": [], "n": null, "non_bool_keys": [],
			"true_keys": [], "deterministic": false, "state_unchanged": false, "error": null}
	var keys := []
	var non_bool := []
	var true_keys := []
	for k in c1.keys():
		keys.append(str(k))
		var v = c1[k]
		if typeof(v) != TYPE_BOOL:
			non_bool.append(str(k))
		elif v:
			true_keys.append(str(k))
	return {
		"is_dict": true,
		"keys": keys,
		"n": keys.size(),
		"non_bool_keys": non_bool,
		"true_keys": true_keys,
		"deterministic": c1 == c2,
		"state_unchanged": before == after,
		"error": null,
	}


# =========================================================================== #
# Formatting helpers (byte-identical to runner.gd)
# =========================================================================== #
func _f(x: float) -> String:
	# Non-finite floats print as "inf"/"nan" via "%f" — INVALID JSON that corrupts
	# the wire (first hit 2026-07-16: real 3D games read transforms before
	# entering the tree -> Transform3D() warnings + non-finite pos/vel ->
	# "unparseable frame"). Serialize them as null: the python side already
	# treats a null component as a fact (and the NaN oracle catches the game).
	if not is_finite(x):
		return "null"
	return "%.17f" % x


func _num(x: float) -> String:
	if not is_finite(x):
		return "null"
	if x == floor(x) and abs(x) < 1.0e15:
		return "%d" % int(x)
	return "%.17f" % x


func _b(v: bool) -> String:
	return "true" if v else "false"


func _esc(s: String) -> String:
	return s.replace("\\", "\\\\").replace('"', '\\"')


func _truthy(v) -> bool:
	if typeof(v) == TYPE_BOOL:
		return v
	if typeof(v) == TYPE_INT or typeof(v) == TYPE_FLOAT:
		return v != 0
	return v != null
