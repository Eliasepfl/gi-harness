# test_cam_rig.gd -- in-image UNIT test for visual_dress.gd's 3D follow-cam rig math + the
# trajectory-aware overview box. Pure math on the dresser's helper functions -- no render
# nodes, so it runs plain --headless (unlike the render-path tests):
#
#   godot --headless --path <godotworld> --script res://tests/test_cam_rig.gd -- [logfile]
#
# It locks the MISSION's rig invariants:
#   1. SCALE      -- the chase distance scales with the body's AABB and the --cam-dist mult.
#   2. MIN CLAMP  -- a tiny body is floored to an ABSOLUTE minimum (never glued to the cam).
#   3. BEHIND     -- the offset sits BEHIND the travel direction + risen (not ahead/backwards).
#   4. AIM        -- the look direction runs along travel, pitched DOWN (path ahead in frame).
#   5. TRAJ BOX   -- the overview frames the t=0 box UNION the witness trajectory box.
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
		_log("CAMRIG_OK " + name)
	else:
		_fail += 1
		_log("CAMRIG_FAIL " + name + "  " + detail)


func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	if args.size() >= 1:
		_logf = args[0]
	_log("CAMRIG_MARK start")

	var d = load("res://visual_dress.gd").new()

	# ---- 1. SCALE: a big body's chase distance is body_len * cam_dist (min not binding). --
	# ext=(4,2,4) -> body_len = 2*max = 8; cam_dist=3 -> 24 (>> FOLLOW_MIN_BACK).
	var big := Vector3(4.0, 2.0, 4.0)
	var back_big: float = d.follow_back_dist(big, 3.0)
	_check("scale_big", is_equal_approx(back_big, 24.0), "back=%f" % back_big)
	# doubling the multiplier doubles the distance (linear in cam_dist above the floor).
	var back_big6: float = d.follow_back_dist(big, 6.0)
	_check("scale_linear", is_equal_approx(back_big6, 48.0), "back6=%f" % back_big6)
	# a larger body pushes the camera further back at the same multiplier.
	var back_bigger: float = d.follow_back_dist(Vector3(8.0, 2.0, 8.0), 3.0)
	_check("scale_monotone", back_bigger > back_big, "%f !> %f" % [back_bigger, back_big])

	# ---- 2. MIN CLAMP: a tiny body (the glider: ext ~(0.55,1.2,0.55)) floors to the min. ----
	var tiny := Vector3(0.55, 1.2, 0.55)   # body_len = 2.4; *3 = 7.2 < FOLLOW_MIN_BACK(8)
	var back_tiny: float = d.follow_back_dist(tiny, 3.0)
	_check("min_clamp", is_equal_approx(back_tiny, d.FOLLOW_MIN_BACK),
		"back_tiny=%f min=%f" % [back_tiny, d.FOLLOW_MIN_BACK])
	_check("min_not_glued", back_tiny >= 8.0, "back_tiny=%f" % back_tiny)
	# the rise is also floored (never zero for a flat body).
	var up_tiny: float = d.follow_up_dist(tiny, back_tiny)
	_check("up_floored", up_tiny >= d.FOLLOW_MIN_UP, "up=%f" % up_tiny)

	# ---- 3. BEHIND: offset trails the travel dir (-fwd horizontally) and rises (+y). -------
	# Glider travels +Z; the camera must sit at -Z (behind) and +Y (above), NOT +Z (ahead).
	var off: Vector3 = d.follow_offset(tiny, 3.0, Vector3(0.0, 0.0, 1.0))
	_check("behind_z", off.z < 0.0, "off.z=%f (must be <0: behind a +Z craft)" % off.z)
	_check("behind_mag", is_equal_approx(off.z, -back_tiny), "off.z=%f" % off.z)
	_check("risen", off.y > 0.0, "off.y=%f" % off.y)
	_check("no_lateral", is_equal_approx(off.x, 0.0), "off.x=%f" % off.x)
	# travel -X -> camera trails at +X (generalises to any travel direction).
	var off_x: Vector3 = d.follow_offset(tiny, 3.0, Vector3(-1.0, 0.0, 0.0))
	_check("behind_x", off_x.x > 0.0, "off_x.x=%f" % off_x.x)

	# ---- 4. AIM: look along travel, pitched DOWN (negative y, positive forward comp). ------
	var look: Vector3 = d.follow_look_dir(Vector3(0.0, 0.0, 1.0))
	_check("aim_forward", look.z > 0.0, "look.z=%f" % look.z)
	_check("aim_down", look.y < 0.0, "look.y=%f (must be <0: pitched down)" % look.y)
	# pitch magnitude sits in the 12-15deg band (look.y = -sin(pitch)).
	var pitch_deg: float = rad_to_deg(asin(-look.y))
	_check("aim_pitch_band", pitch_deg >= 12.0 and pitch_deg <= 15.0, "pitch=%f" % pitch_deg)
	# a degenerate (vertical / zero) travel dir falls back to a valid horizontal aim.
	var look_deg: Vector3 = d.follow_look_dir(Vector3(0.0, 1.0, 0.0))
	_check("aim_degenerate_safe", look_deg.length() > 0.9 and look_deg.y < 0.0,
		"look_deg=%s" % str(look_deg))

	# ---- 5. TRAJ BOX: the overview frames t=0 UNION the trajectory. -----------------------
	d._min = Vector3(-5.0, 0.0, -5.0)
	d._max = Vector3(5.0, 3.0, 5.0)              # a small t=0 static box
	# no trajectory yet -> the overview box is exactly the t=0 box.
	var b0: Array = d._overview_box()
	_check("box_t0_only", (b0[0] as Vector3).is_equal_approx(d._min)
		and (b0[1] as Vector3).is_equal_approx(d._max), "b0=%s" % str(b0))
	# feed a trajectory that flies far past the t=0 box (a fly-through) via the opts path.
	d._read_cam_opts({
		"traj_min": Vector3(-5.0, 0.0, -5.0),
		"traj_max": Vector3(5.0, 12.0, 100.0),   # craft climbed to y=12, flew to z=100
		"cam_fwd": Vector3(0.0, 0.5, 1.0),
	})
	var b1: Array = d._overview_box()
	var lo: Vector3 = b1[0]
	var hi: Vector3 = b1[1]
	_check("box_union_z", is_equal_approx(hi.z, 100.0), "hi.z=%f (must reach the flight end)" % hi.z)
	_check("box_union_y", is_equal_approx(hi.y, 12.0), "hi.y=%f" % hi.y)
	_check("box_keeps_t0", is_equal_approx(lo.z, -5.0) and is_equal_approx(lo.x, -5.0),
		"lo=%s" % str(lo))
	_check("fwd_captured", d._has_fwd and d._traj_fwd.z > 0.0, "fwd=%s" % str(d._traj_fwd))

	# ---- 6. CLAMP: keep the cam inside the flyable box on lateral+vertical axes (anti-ceiling
	# pop), but leave the along-travel (chase) axis FREE. fwd ~ +Z (set in section 5 above).
	var m: float = d.FOLLOW_CLAMP_MARGIN
	var blo := Vector3(-5.0, 0.0, -5.0)
	var bhi := Vector3(5.0, 12.0, 100.0)
	var above: Vector3 = d.clamp_follow_pos(Vector3(0.0, 20.0, 50.0), blo, bhi)
	_check("clamp_ceiling", is_equal_approx(above.y, 12.0 + m), "y=%f (must clamp under ceiling)" % above.y)
	var behind: Vector3 = d.clamp_follow_pos(Vector3(0.0, 6.0, -40.0), blo, bhi)
	_check("clamp_chase_axis_free", is_equal_approx(behind.z, -40.0),
		"z=%f (the chase axis must NOT be clamped)" % behind.z)
	var wide: Vector3 = d.clamp_follow_pos(Vector3(50.0, 6.0, 10.0), blo, bhi)
	_check("clamp_lateral", is_equal_approx(wide.x, 5.0 + m), "x=%f" % wide.x)
	var inside: Vector3 = d.clamp_follow_pos(Vector3(2.0, 6.0, 10.0), blo, bhi)
	_check("clamp_noop_inside", inside.is_equal_approx(Vector3(2.0, 6.0, 10.0)), "inside=%s" % str(inside))

	# cam_dist default when nothing supplied (no opts key, no env) is the sane FOLLOW_CAM_DIST.
	var d2 = load("res://visual_dress.gd").new()
	d2._read_cam_opts({})
	_check("cam_dist_default", is_equal_approx(d2._cam_dist, d2.FOLLOW_CAM_DIST),
		"cam_dist=%f" % d2._cam_dist)
	# an explicit opts cam_dist wins.
	d2._read_cam_opts({"cam_dist": 2.5})
	_check("cam_dist_opts", is_equal_approx(d2._cam_dist, 2.5), "cam_dist=%f" % d2._cam_dist)

	_log("CAMRIG_DONE pass=%d fail=%d" % [_pass, _fail])
	quit(1 if _fail > 0 else 0)
