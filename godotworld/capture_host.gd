# capture_host.gd -- the CAPTURE lane's render host. A SEPARATE scene from the frozen
# certification host (serve_game.gd); it NEVER touches certification behaviour.
#
# What it is: a SceneTree script that REPLAYS an already-certified witness (seed +
# actions) through a RENDERED run and writes one PNG per decision tick. It steps physics
# with the EXACT same discipline serve_game.gd uses -- act(action) + K=6 physics frames +
# latch + terminal check, same physics pins + optional speedup -- so the trajectory it
# draws is byte-for-byte the certified witness. "The demo IS the witness."
#
# It is NOT run with --headless (that is the pixel-blind dummy rasterizer). It runs under
# a real display + software GL (x11 + opengl3 + llvmpipe, or a desktop GPU), reads the
# main viewport back each frame, and saves it. Rendering is decoupled from stepping via
# RenderingServer.force_draw() (a synchronous draw that advances NO physics), so the
# captured cadence is exactly one frame per decision tick and the physics trail is
# untouched.
#
# Visuals come from visual_dress.gd -- a ZERO-CONTACT overlay (proxies in a sibling
# subtree, transforms mirrored read-only). The game tree and its physics are never
# mutated, so a dressed replay and an undressed replay share an identical state() trail.
#
# Run (spawned by the Python capture executor; see harness/verify/capture.py):
#   godot --display-driver x11 --rendering-driver opengl3 --fixed-fps 60 \
#         --path godotworld -s res://capture_host.gd -- \
#         --capture --game-file=<abs.gd> --actions-file=<abs.json> \
#         --out=<dir> [--follow] [--width=960] [--height=540] [--speedup=K] \
#         [--every=1] [--max-frames=400]
#
# SECURITY: like serve_game.gd this compiles + runs UNTRUSTED game code, so it runs ONLY
# in-container on a SCRUBBED env, and only on games that already PASSED the G0 scanner
# (it is the certified witness being replayed). The host itself uses FileAccess only to
# read the (trusted) Python-provided game/actions/out paths -- never on the game's behalf.

extends SceneTree

const K_STEPS := 6
const VMAX := 1.0e5
const REQUIRED_METHODS := ["build", "act", "state", "checkpoints",
	"is_success", "is_failure", "actions"]
const SPEEDUP_MIN := 1
const SPEEDUP_MAX := 16

var _game: Node = null
var _stage: Node = null
var _out_dir := ""
var _actions: Array = []
var _seed := 0
var _follow := false
var _width := 960
var _height := 540
var _every := 1
var _max_frames := 400
var _speedup := 1
var _applied := 0
var _frames_written := 0
var _result := ""
var _no_dress := false
var _no_frames := false
var _fp_path := ""
var _fp_lines: PackedStringArray = PackedStringArray()


func _initialize() -> void:
	# Determinism pins -- IDENTICAL to serve_game.gd so the render trail matches the
	# certified witness (paired physics-rate/time-scale scaling under speedup).
	_speedup = _clamp_speedup(_int_arg("--speedup=", 1))
	Engine.physics_ticks_per_second = 60 * _speedup
	Engine.time_scale = float(_speedup)
	Engine.max_physics_steps_per_frame = 8
	Engine.physics_jitter_fix = 0.0
	_run()


func _run() -> void:
	var game_file := _str_arg("--game-file=", "")
	var actions_file := _str_arg("--actions-file=", "")
	_out_dir = _str_arg("--out=", "")
	_follow = _has_flag("--follow")
	_width = _int_arg("--width=", 960)
	_height = _int_arg("--height=", 540)
	_every = max(1, _int_arg("--every=", 1))
	_max_frames = max(1, _int_arg("--max-frames=", 400))
	_no_dress = _has_flag("--no-dress")     # identity-test lane (no overlay at all)
	_no_frames = _has_flag("--no-frames")   # step + fingerprint only, write no PNGs
	_fp_path = _str_arg("--fingerprint=", "")

	if game_file == "" or _out_dir == "":
		push_error("capture_host: --game-file and --out are required")
		quit(2)
		return

	# Load the witness (seed + actions).
	if actions_file != "":
		var af := FileAccess.open(actions_file, FileAccess.READ)
		if af != null:
			var parsed = JSON.parse_string(af.get_as_text())
			if typeof(parsed) == TYPE_DICTIONARY:
				_seed = int(parsed.get("seed", 0))
				var a = parsed.get("actions", [])
				if typeof(a) == TYPE_ARRAY:
					for v in a:
						_actions.append(str(v))

	# Load + compile the game source (in-memory, like serve_game.gd).
	var gf := FileAccess.open(game_file, FileAccess.READ)
	if gf == null:
		push_error("capture_host: cannot read game file %s" % game_file)
		quit(2)
		return
	var src := gf.get_as_text()
	var gd := GDScript.new()
	gd.source_code = src
	if gd.reload() != OK or not gd.can_instantiate():
		push_error("capture_host: game failed to compile")
		quit(2)
		return
	var inst = gd.new()
	if inst == null or not (inst is Node):
		push_error("capture_host: game is not a Node")
		quit(2)
		return
	for m in REQUIRED_METHODS:
		if not inst.has_method(m):
			push_error("capture_host: missing contract method %s" % m)
			inst.free()
			quit(2)
			return

	# Window/viewport size -> the captured image size.
	root.size = Vector2i(_width, _height)

	# Build the game under root (its bodies join root's physics space -- same as serve).
	root.add_child(inst)
	inst.build(_seed)
	_game = inst
	await physics_frame  # settle t=0

	# Dress: a ZERO-CONTACT overlay stage, SIBLING of the game (added to root, never to
	# the game). It reads the game tree read-only; the game is never mutated. The
	# identity test runs this host with --no-dress to prove the overlay is inert.
	if not _no_dress:
		var dress_script = load("res://visual_dress.gd")
		_stage = dress_script.new()
		root.add_child(_stage)
		_stage.dress(_game, {"follow": _follow, "view_w": float(_width), "view_h": float(_height)})

	# Ensure the out dir exists.
	DirAccess.make_dir_recursive_absolute(_out_dir)

	# t=0 frame + fingerprint.
	_fingerprint(0)
	await _grab(0)

	# Replay the witness with the serve stepping discipline.
	await _replay()

	_write_meta()
	quit(0)


func _replay() -> void:
	var n := _actions.size()
	# Subsample cadence so a very long witness still fits _max_frames.
	var stride := _every
	var est_frames := int(ceil(float(n) / float(stride))) + 2
	if est_frames > _max_frames:
		stride = max(1, int(ceil(float(n) / float(_max_frames - 2))))

	for i in range(n):
		var action := str(_actions[i]) if i < _actions.size() else ""
		if action != "":
			_game.act(action)
		_applied += 1
		var frozen := false
		for k in range(K_STEPS):
			await physics_frame
			if not _sane():
				frozen = true
				break
		_fingerprint(_applied)
		if frozen:
			_result = "error"
			break
		# terminal checks (mirror serve ordering: failure before success)
		if _truthy(_game.is_failure()):
			_result = "failure"
			await _grab(_applied)
			break
		if _truthy(_game.is_success()):
			_result = "success"
			await _grab(_applied)
			break
		if (_applied % stride) == 0:
			await _grab(_applied)
	if _result == "":
		_result = "exhausted"
		# make sure the final state is represented
		await _grab(_applied)
	_write_fingerprint()


func _fingerprint(tick_no: int) -> void:
	# Append one tick's state() signature (name:px,py[,pz],vx,vy[,vz],angle at %.17f) --
	# the SAME precision serve_game.gd frames use. Written to --fingerprint=<file>; the
	# dressed-vs-undressed identity test asserts these files are byte-identical.
	if _fp_path == "":
		return
	var st = _game.state()
	var parts := PackedStringArray()
	if typeof(st) == TYPE_DICTIONARY:
		var bodies = st.get("bodies", [])
		if typeof(bodies) == TYPE_ARRAY:
			for b in bodies:
				if typeof(b) != TYPE_DICTIONARY:
					continue
				parts.append("%s:%s:%s:%.17f" % [
					str(b.get("name", "")), _vecstr(b.get("pos", [])),
					_vecstr(b.get("vel", [])), float(b.get("angle", 0.0))])
	_fp_lines.append("%d|%s" % [tick_no, ";".join(parts)])


func _vecstr(a) -> String:
	var parts := PackedStringArray()
	if typeof(a) == TYPE_ARRAY:
		for x in a:
			parts.append("%.17f" % float(x))
	return ",".join(parts)


func _write_fingerprint() -> void:
	if _fp_path == "":
		return
	var f := FileAccess.open(_fp_path, FileAccess.WRITE)
	if f != null:
		f.store_string("\n".join(_fp_lines))
		f.flush()


func _grab(tick_no: int) -> void:
	# Mirror body transforms onto the visual proxies (read-only), render ONE synchronous
	# frame (no physics stepped), read the viewport back, and save a PNG. force_draw
	# decouples the render from stepping so the physics trail stays byte-identical.
	if _no_frames:
		return
	if _stage != null and is_instance_valid(_stage):
		_stage.sync()
	RenderingServer.force_draw(false)
	var tex := root.get_texture()
	if tex == null:
		return
	var img := tex.get_image()
	if img == null:
		return
	var path := "%s/frame_%05d.png" % [_out_dir, _frames_written]
	img.save_png(path)
	_frames_written += 1


func _sane() -> bool:
	var st = _game.state()
	if typeof(st) != TYPE_DICTIONARY:
		return true
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


func _write_meta() -> void:
	var meta := {
		"result": _result,
		"ticks": _applied,
		"frames": _frames_written,
		"seed": _seed,
		"width": _width,
		"height": _height,
		"follow": _follow,
		"speedup": _speedup,
	}
	var f := FileAccess.open("%s/meta.json" % _out_dir, FileAccess.WRITE)
	if f != null:
		f.store_string(JSON.stringify(meta))
		f.flush()


# ---- cmdline arg helpers -------------------------------------------------- #
func _str_arg(prefix: String, dflt: String) -> String:
	for a in OS.get_cmdline_user_args():
		if a.begins_with(prefix):
			return a.substr(prefix.length())
	return dflt


func _int_arg(prefix: String, dflt: int) -> int:
	var s := _str_arg(prefix, "")
	return int(s) if s != "" else dflt


func _has_flag(flag: String) -> bool:
	for a in OS.get_cmdline_user_args():
		if a == flag:
			return true
	return false


func _clamp_speedup(v: int) -> int:
	return clampi(v, SPEEDUP_MIN, SPEEDUP_MAX)


func _truthy(v) -> bool:
	if typeof(v) == TYPE_BOOL:
		return v
	if typeof(v) == TYPE_INT or typeof(v) == TYPE_FLOAT:
		return v != 0
	return v != null
