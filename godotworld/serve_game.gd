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

const K_STEPS := 6
const VMAX := 1.0e5
const DEFAULT_W := 800.0
const DEFAULT_H := 600.0
const SERVE_MAX_FRAME := 16777216       # 16 MiB frame cap (protocol sanity guard)
const SERVE_DEFAULT_HORIZON := 300      # decision-tick truncation budget
const SERVE_IDLE_TIMEOUT_MS := 120000   # self-quit after this long idle (orphan guard)
const SPEEDUP_MIN := 1
const SPEEDUP_MAX := 16

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
	root.add_child(inst)
	# The game is a plain Node implementing the method convention (no base class): it
	# seeds its OWN RandomNumberGenerator from build()'s seed -- the banned-API scan
	# forbids the unseeded global randi/randf/randomize, so a self-seeded generator is
	# the sanctioned path -- and its RigidBody2D children join the world's physics space
	# by tree membership. The host hands it nothing but that membership + the seed.
	# build() may raise inside generated code; a bad build surfaces as an error frame
	# rather than crashing the host.
	inst.build(world_seed)
	_game = inst
	# Capture the declared action set + world size, and pre-register every checkpoint
	# key as unlatched so frames report the full milestone map from t=0.
	_actions_cache = _read_actions()
	_read_world_size()
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
			_game.act(str(action))
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
	_build_err = await _rebuild(int(msg.get("seed", 0)))
	if _build_err != "":
		return '{"ok":false,"error":"%s"}' % _esc("build failed: " + _build_err)
	return _frame_json(true, 0.0)


func _op_reset(msg: Dictionary) -> String:
	if _source == "":
		return '{"ok":false,"error":"no game loaded"}'
	_build_err = await _rebuild(int(msg.get("seed", 0)))
	if _build_err != "":
		return '{"ok":false,"error":"%s"}' % _esc("build failed: " + _build_err)
	return _frame_json(false, 0.0)


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
	return ('{%s"obs_state":%s,"checkpoints":%s,"tick":%d,"result":%s,'
		+ '"done_term":%s,"done_trunc":%s,"world_size":[%s,%s],'
		+ '"nan":%s,"oob":[%s]%s,"error":null}') % [
		head, obs, _checkpoints_json(), _applied, res_str,
		_b(_done_term), _b(_done_trunc), _num(_world_w), _num(_world_h),
		_b(_nan), oob, frames_part]


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
		_f(float(b.get("angle", 0.0))),
		_b(bool(b.get("controlled", false))), _b(bool(b.get("static", false)))]


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
			if typeof(b) != TYPE_DICTIONARY or bool(b.get("static", false)):
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

	# 6. Goal probes on the fresh t=0 world (predicates are pure/read-only).
	out["g2"] = {
		"success": _probe_bool("is_success"),
		"failure": _probe_bool("is_failure"),
		"checkpoints": _probe_checkpoints(),
	}

	_teardown()
	await process_frame
	return JSON.stringify(out)


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
	return "%.17f" % x


func _num(x: float) -> String:
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
