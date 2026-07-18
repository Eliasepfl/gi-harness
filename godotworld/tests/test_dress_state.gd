# test_dress_state.gd -- in-image test for visual_dress.gd's DRESSER-FOLLOWS-STATE path.
#
#   godot --headless --path <godotworld> --script res://tests/test_dress_state.gd \
#         -- <handsim_3d.gd> <handsim_2d.gd> [logfile]
#
# Driven by tests/test_gd_dress_state.py. Proves, on real GameAPI hand-sim fixtures:
#
#   A. PURE MAP    -- trail_pose_3d/2d turn a state() (pos, angle) into the right world pose
#                     (scalar yaw about +Y, [x,y,z] Euler, 2D scalar rotation).
#   B. SYNTHESIS   -- a SHAPELESS controlled body (no CollisionShape, only in state()) gets a
#                     synthesized proxy (src == null) keyed by its state() name.
#   C. FOLLOWS     -- sync(positions) drives that synthesized proxy from the per-tick trail
#                     (2D and 3D), so a 0-shape hand-sim finally animates.
#   D. NODE-RIDE   -- a shape-backed body node-rides by DEFAULT: a bogus trail entry for it is
#                     ignored (byte-identical to legacy sync()).
#   E. FALLBACK    -- sync({}) with no trail never crashes; the synthesized proxy holds its last
#                     pose and the shape-backed proxy node-rides.
#   F. UNIFIED     -- state_follow="all" opts a shape-backed proxy INTO the trail (via its
#                     cached t=0 rel transform).
#
# Loaded BY PATH (no class_name global registration -- resolves in a fresh checkout too).
extends SceneTree

var _logf := ""
var _pass := 0
var _fail := 0


func _log(s: String) -> void:
	print(s)
	if _logf != "":
		var f := FileAccess.open(_logf,
			FileAccess.READ_WRITE if FileAccess.file_exists(_logf) else FileAccess.WRITE)
		if f:
			f.seek_end()
			f.store_line(s)
			f.flush()


func _check(name: String, cond: bool, detail: String = "") -> void:
	if cond:
		_pass += 1
		_log("DSTATE_OK " + name)
	else:
		_fail += 1
		_log("DSTATE_FAIL " + name + "  " + detail)


func _initialize() -> void:
	Engine.physics_ticks_per_second = 60
	Engine.max_physics_steps_per_frame = 8
	Engine.physics_jitter_fix = 0.0
	_run()


func _run() -> void:
	var args := OS.get_cmdline_user_args()
	if args.size() < 2:
		push_error("test_dress_state: need <handsim_3d.gd> <handsim_2d.gd>")
		quit(2)
		return
	var handsim_3d: String = args[0]
	var handsim_2d: String = args[1]
	if args.size() >= 3:
		_logf = args[2]

	_log("DSTATE_MARK start")
	_test_pure_math()
	await _test_synth_3d(handsim_3d)
	await _test_unified_3d(handsim_3d)
	await _test_synth_2d(handsim_2d)
	_log("DSTATE_DONE pass=" + str(_pass) + " fail=" + str(_fail))
	quit(1 if _fail > 0 else 0)


# =========================================================================== #
# A. PURE trail-pose mapping (no scene)
# =========================================================================== #
func _test_pure_math() -> void:
	var d = load("res://visual_dress.gd").new()
	var t: Transform3D = d.trail_pose_3d([1.0, 2.0, 3.0], 0.0)
	_check("pure_3d_pos", t.origin.is_equal_approx(Vector3(1, 2, 3)), str(t.origin))
	# scalar angle == yaw about +Y: +X rotates toward -Z.
	var yaw: Transform3D = d.trail_pose_3d([0.0, 0.0, 0.0], PI * 0.5)
	_check("pure_3d_yaw", (yaw.basis * Vector3(1, 0, 0)).is_equal_approx(Vector3(0, 0, -1)),
		str(yaw.basis * Vector3(1, 0, 0)))
	# [x,y,z] Euler angle -> natural basis (yaw component here matches the scalar case).
	var eul: Transform3D = d.trail_pose_3d([0.0, 0.0, 0.0], [0.0, PI * 0.5, 0.0])
	_check("pure_3d_euler", (eul.basis * Vector3(1, 0, 0)).is_equal_approx(Vector3(0, 0, -1)),
		str(eul.basis * Vector3(1, 0, 0)))
	var u: Transform2D = d.trail_pose_2d([5.0, 6.0], 0.0)
	_check("pure_2d_pos", u.origin.is_equal_approx(Vector2(5, 6)), str(u.origin))
	var u2: Transform2D = d.trail_pose_2d([0.0, 0.0], PI * 0.5)
	_check("pure_2d_rot", u2.basis_xform(Vector2(1, 0)).is_equal_approx(Vector2(0, 1)),
		str(u2.basis_xform(Vector2(1, 0))))
	d.free()


# =========================================================================== #
# B..E. 3D: synthesize the shapeless craft, follow the trail, node-ride the gate
# =========================================================================== #
func _test_synth_3d(path: String) -> void:
	var inst = _instantiate(path)
	if inst == null:
		return
	root.add_child(inst)
	inst.build(0)
	await physics_frame
	var dr = _dress(inst, {})

	var craft = _pair_named(dr, "craft")
	var gate = _pair_named(dr, "gate")
	_check("synth_craft_is_synthesized",
		craft != null and bool(craft.get("synth", false)) and craft["src"] == null,
		str(craft))
	_check("shape_gate_is_shape_backed",
		gate != null and gate["src"] != null and not bool(gate.get("synth", false)),
		str(gate))
	if craft == null or gate == null:
		_free(inst, dr)
		await physics_frame
		return
	var cp = craft["proxy"]
	var gp = gate["proxy"]
	_check("synth_craft_t0_at_origin",
		(cp as Node3D).global_transform.origin.is_equal_approx(Vector3.ZERO),
		str((cp as Node3D).global_transform.origin))

	# Drive the trail: craft moves to (0,0,-15); the gate carries a BOGUS entry that DEFAULT
	# mode must ignore (the gate node-rides its real transform at z=-40).
	dr.sync({"craft": {"pos": [0.0, 0.0, -15.0], "angle": 0.0},
		"gate": {"pos": [999.0, 999.0, 999.0], "angle": 0.0}})
	_check("synth_craft_follows_trail",
		(cp as Node3D).global_transform.origin.is_equal_approx(Vector3(0, 0, -15)),
		str((cp as Node3D).global_transform.origin))
	_check("shape_gate_node_rides_by_default",
		(gp as Node3D).global_transform.origin.is_equal_approx(Vector3(0, 0, -40)),
		str((gp as Node3D).global_transform.origin))

	# sync({}) with no trail: no crash, synthesized proxy holds, shape proxy node-rides.
	dr.sync({})
	_check("empty_pos_synth_holds",
		(cp as Node3D).global_transform.origin.is_equal_approx(Vector3(0, 0, -15)),
		str((cp as Node3D).global_transform.origin))
	_check("empty_pos_shape_node_rides",
		(gp as Node3D).global_transform.origin.is_equal_approx(Vector3(0, 0, -40)),
		str((gp as Node3D).global_transform.origin))

	_free(inst, dr)
	await physics_frame


# =========================================================================== #
# F. 3D UNIFIED: state_follow="all" drives the shape-backed gate from the trail too
# =========================================================================== #
func _test_unified_3d(path: String) -> void:
	var inst = _instantiate(path)
	if inst == null:
		return
	root.add_child(inst)
	inst.build(0)
	await physics_frame
	var dr = _dress(inst, {"state_follow": "all"})
	var gate = _pair_named(dr, "gate")
	if gate == null:
		_check("unified_gate_present", false, "no gate pair")
		_free(inst, dr)
		await physics_frame
		return
	var gp = gate["proxy"]
	# gate rel3d == identity (shape at body origin; state pose == shape world), so the proxy
	# tracks the trail entry exactly.
	dr.sync({"craft": {"pos": [0.0, 0.0, 0.0], "angle": 0.0},
		"gate": {"pos": [0.0, 10.0, -40.0], "angle": 0.0}})
	_check("unified_gate_follows_trail",
		(gp as Node3D).global_transform.origin.is_equal_approx(Vector3(0, 10, -40)),
		str((gp as Node3D).global_transform.origin))
	_free(inst, dr)
	await physics_frame


# =========================================================================== #
# B..E. 2D twin: synthesize the shapeless craft, follow the trail, node-ride the wall
# =========================================================================== #
func _test_synth_2d(path: String) -> void:
	var inst = _instantiate(path)
	if inst == null:
		return
	root.add_child(inst)
	inst.build(0)
	await physics_frame
	var dr = _dress(inst, {})

	var craft = _pair_named(dr, "craft")
	var wall = _pair_named(dr, "wall")
	_check("synth2d_craft_is_synthesized",
		craft != null and bool(craft.get("synth", false)) and craft["src"] == null, str(craft))
	_check("shape2d_wall_is_shape_backed",
		wall != null and wall["src"] != null and not bool(wall.get("synth", false)), str(wall))
	if craft == null or wall == null:
		_free(inst, dr)
		await physics_frame
		return
	var cp = craft["proxy"]
	var wp = wall["proxy"]
	_check("synth2d_craft_t0",
		(cp as Node2D).global_position.is_equal_approx(Vector2(100, 100)),
		str((cp as Node2D).global_position))

	dr.sync({"craft": {"pos": [160.0, 100.0], "angle": 0.0},
		"wall": {"pos": [999.0, 999.0], "angle": 0.0}})
	_check("synth2d_craft_follows_trail",
		(cp as Node2D).global_position.is_equal_approx(Vector2(160, 100)),
		str((cp as Node2D).global_position))
	_check("shape2d_wall_node_rides_by_default",
		(wp as Node2D).global_position.is_equal_approx(Vector2(300, 100)),
		str((wp as Node2D).global_position))

	_free(inst, dr)
	await physics_frame


# =========================================================================== #
# helpers
# =========================================================================== #
func _instantiate(path: String):
	var src := FileAccess.get_file_as_string(path)
	if src == "":
		_check("read_fixture", false, path)
		return null
	var gd := GDScript.new()
	gd.source_code = src
	if gd.reload() != OK or not gd.can_instantiate():
		_check("compile_fixture", false, path)
		return null
	return gd.new()


func _dress(inst, opts: Dictionary):
	var dresser = load("res://visual_dress.gd").new()
	root.add_child(dresser)          # a SIBLING of the game, exactly like capture_host.gd
	var o := {"view_w": 960.0, "view_h": 540.0}
	for k in opts:
		o[k] = opts[k]
	dresser.dress(inst, o)
	return dresser


func _pair_named(dresser, sname: String):
	for p in dresser._pairs:
		if String(p.get("state_name", "")) == sname:
			return p
	return null


func _free(inst, dresser) -> void:
	if dresser != null and is_instance_valid(dresser):
		dresser.free()
	if inst != null and is_instance_valid(inst):
		inst.free()
