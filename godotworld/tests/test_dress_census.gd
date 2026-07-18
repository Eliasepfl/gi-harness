# test_dress_census.gd -- in-image test for visual_dress.gd's VISNODE CENSUS / authorship respect.
#
#   godot --headless --path <godotworld> --script res://tests/test_dress_census.gd \
#         -- <bare_3d.gd> <authored_3d.gd> <authored_2d.gd> [logfile]
#
# Driven by tests/test_gd_dress_census.py. The dresser is added to root exactly as capture_host.gd
# does it -- a SIBLING of the game, never a child. It proves, on real GameAPI fixtures:
#
#   1. RESPECT (3D) -- a game that authored its own meshes/camera/sun/sky keeps ALL of it: its
#      authored bodies get no proxy, its Camera3D stays the viewport's current camera, and no
#      generic sky/sun/ground is stamped over it.
#   2. RESPECT (2D) -- ditto for Polygon2D art + Camera2D, and the agent halo is not painted over
#      an authored probe.
#   3. FILL WHAT'S MISSING -- a body the game did NOT skin still gets its proxy in the same scene.
#   4. FALLBACK UNCHANGED -- a fully bare game dresses IDENTICALLY under the new default ("auto")
#      and the legacy path ("proxy"): same stage tree, same pair count, same camera ownership.
#      The ~8 bare library games depend on this being a no-op.
#   5. THE KNOB -- dress_mode="proxy" restores the old behaviour on an AUTHORED game (everything
#      proxied, our camera current), so the old look is one flag away.
#   6. AUTHORED, NO CAMERA -- a game that authored its meshes/sun/sky but NO Camera3D (the
#      KNOCKDOWN shape): the dresser owns the overview camera AND frames the GAMEPLAY CONTENT, not
#      the world-bounds ground slab -- the fix for the "authored game renders gray" symptom, where
#      nothing was hidden but the play area was dwarfed by a fit-everything overview of the floor.
#
# Everything is loaded BY PATH (load("res://visual_dress.gd"), GDScript.new() + source_code):
# no class_name global registration, which resolves in a warm worktree and fails in a fresh
# checkout (.godot/global_script_class_cache.cfg is gitignored).
extends SceneTree

var _logf := ""
var _passed := 0
var _failed := 0


func _log(s: String) -> void:
	print(s)
	if _logf != "":
		var f := FileAccess.open(_logf,
			FileAccess.READ_WRITE if FileAccess.file_exists(_logf) else FileAccess.WRITE)
		if f:
			f.seek_end()
			f.store_line(s)
			f.flush()


func _ok(msg: String) -> void:
	_passed += 1
	_log("CENSUS_OK " + msg)


func _fail(msg: String) -> void:
	_failed += 1
	_log("CENSUS_FAIL " + msg)


func _check(cond: bool, msg: String) -> void:
	if cond:
		_ok(msg)
	else:
		_fail(msg)


func _initialize() -> void:
	Engine.physics_ticks_per_second = 60
	Engine.max_physics_steps_per_frame = 8
	Engine.physics_jitter_fix = 0.0
	_run()


func _run() -> void:
	var args := OS.get_cmdline_user_args()
	if args.size() < 4:
		push_error("test_dress_census: need <bare_3d.gd> <authored_3d.gd> <authored_2d.gd>"
			+ " <authored_nocam_3d.gd>")
		quit(2)
		return
	var bare_3d: String = args[0]
	var authored_3d: String = args[1]
	var authored_2d: String = args[2]
	var authored_nocam_3d: String = args[3]
	if args.size() >= 5:
		_logf = args[4]

	_log("CENSUS_MARK start")
	await _test_authored_3d(authored_3d)
	await _test_authored_2d(authored_2d)
	await _test_authored_no_camera_frames_content(authored_nocam_3d)
	await _test_bare_fallback_identical(bare_3d)
	await _test_proxy_knob_restores_legacy(authored_3d)
	_log("CENSUS_DONE pass=" + str(_passed) + " fail=" + str(_failed))
	quit(1 if _failed > 0 else 0)


# =========================================================================== #
# 1 + 3. A 3D game that authored its own presentation keeps ALL of it
# =========================================================================== #
func _test_authored_3d(path: String) -> void:
	var inst = _instantiate(path)
	if inst == null:
		return
	root.add_child(inst)
	inst.build(0)
	await physics_frame
	var game_cam := _find_first(inst, "Camera3D")
	var dresser = _dress(inst, {})

	var c: Dictionary = dresser.census()
	_check(bool(c["camera"]) and bool(c["light"]) and bool(c["env"])
		and bool(c["root_visual"]) and int(c["authored_bodies"]) == 2,
		"census sees the authored stack: " + str(c))

	# The authored bodies (puck, guard) keep their own meshes -> no proxy over them.
	_check(_pairs_for_body(dresser, inst, "puck") == 0, "authored puck is NOT proxied")
	_check(_pairs_for_body(dresser, inst, "guard") == 0, "authored guard is NOT proxied")
	# ... but the pad, which the game did not skin, still gets one (fill what's missing).
	_check(_pairs_for_body(dresser, inst, "pad") == 1, "un-authored pad IS still proxied")

	# The game's camera frames the demo: ours is never built, theirs stays current.
	_check(dresser._camera == null, "dresser built no camera over the authored one")
	_check(game_cam != null and root.get_camera_3d() == game_cam,
		"the game's OWN Camera3D is still the current camera")

	# No generic sky / sun / ground stamped over the authored night arena.
	_check(_count_class(dresser, "WorldEnvironment") == 0, "no generic sky over authored env")
	_check(_count_class(dresser, "DirectionalLight3D") == 0, "no generic sun over authored light")
	_check(_stage_mesh_names(dresser).find("Backdrop") == -1,
		"no generic ground over the authored arena floor")

	_free(inst, dresser)
	await physics_frame


# =========================================================================== #
# 2. The 2D twin: authored Polygon2D art + Camera2D, no backdrop, no halo
# =========================================================================== #
func _test_authored_2d(path: String) -> void:
	var inst = _instantiate(path)
	if inst == null:
		return
	root.add_child(inst)
	inst.build(0)
	await physics_frame
	var game_cam := _find_first(inst, "Camera2D")
	var dresser = _dress(inst, {})

	var c: Dictionary = dresser.census()
	_check(bool(c["camera"]) and bool(c["root_visual"]) and int(c["authored_bodies"]) == 1
		and not bool(c["light"]) and not bool(c["env"]),
		"2D census sees authored art + camera: " + str(c))

	# The probe is the CONTROLLED body: authored -> neither a proxy NOR an orange halo.
	_check(_pairs_for_body(dresser, inst, "probe") == 0,
		"authored probe is NOT proxied and NOT haloed")
	_check(_pairs_for_body(dresser, inst, "pad") == 1, "un-authored pad IS still proxied")
	_check(dresser._camera == null, "dresser built no Camera2D over the authored one")
	_check(game_cam != null and root.get_camera_2d() == game_cam,
		"the game's OWN Camera2D is still the current camera")
	_check(_stage_mesh_names(dresser).find("Backdrop") == -1,
		"no slate backdrop over the authored terrain")

	_free(inst, dresser)
	await physics_frame


# =========================================================================== #
# 4. A BARE game dresses identically under "auto" and the legacy "proxy"
# =========================================================================== #
func _test_bare_fallback_identical(path: String) -> void:
	var sig_auto := await _dress_signature(path, {})
	var sig_proxy := await _dress_signature(path, {"dress_mode": "proxy"})
	if sig_auto == sig_proxy and sig_auto != "":
		_ok("bare game: auto == proxy (" + str(sig_auto.split("\n").size()) + " stage nodes)")
	else:
		_fail("bare game: auto != proxy\n  auto =" + sig_auto + "\n  proxy=" + sig_proxy)


func _dress_signature(path: String, opts: Dictionary) -> String:
	# A structural fingerprint of what the dresser built: the stage subtree's classes + names,
	# the pair count, and whether the dresser owns the camera. Instance-id free, so two
	# independent passes are comparable.
	var inst = _instantiate(path)
	if inst == null:
		return ""
	root.add_child(inst)
	inst.build(0)
	await physics_frame
	var dresser = _dress(inst, opts)
	var lines := PackedStringArray()
	lines.append("pairs=" + str(dresser._pairs.size()))
	lines.append("owns_camera=" + str(dresser._camera != null))
	_sig_walk(dresser, lines, "")
	var out := "\n".join(lines)
	_free(inst, dresser)
	await physics_frame
	return out


func _sig_walk(node: Node, lines: PackedStringArray, indent: String) -> void:
	# The class + tree shape is the structural fingerprint. Godot auto-numbers unnamed nodes
	# ("@MeshInstance3D@44") with a GLOBAL counter that advances between the two passes, so those
	# names are recorded as just their class -- otherwise two identical trees never match. Only
	# the dresser's OWN deliberate names (DemoStage3D, Backdrop, DemoCamera3D) are kept.
	for c in node.get_children():
		var nm := str(c.name)
		var tag := c.get_class() if nm.begins_with("@") else c.get_class() + ":" + nm
		lines.append(indent + tag)
		_sig_walk(c, lines, indent + "  ")


# =========================================================================== #
# 5. dress_mode="proxy" forces the legacy look back on an AUTHORED game
# =========================================================================== #
func _test_proxy_knob_restores_legacy(path: String) -> void:
	var inst = _instantiate(path)
	if inst == null:
		return
	root.add_child(inst)
	inst.build(0)
	await physics_frame
	var dresser = _dress(inst, {"dress_mode": "proxy"})

	_check(_pairs_for_body(dresser, inst, "puck") == 1, "proxy mode: authored puck IS proxied")
	_check(_pairs_for_body(dresser, inst, "guard") == 1, "proxy mode: authored guard IS proxied")
	_check(_count_class(dresser, "WorldEnvironment") == 1, "proxy mode: generic sky stamped")
	_check(_count_class(dresser, "DirectionalLight3D") == 1, "proxy mode: generic sun stamped")
	_check(dresser._camera != null and root.get_camera_3d() == dresser._camera,
		"proxy mode: the dresser's camera is current again")

	_free(inst, dresser)
	await physics_frame


# =========================================================================== #
# 6. A game that authored its VISUALS but NO camera: the dresser owns the overview
#    camera, and it frames the GAMEPLAY CONTENT, not the world-bounds ground slab.
#    (The KNOCKDOWN shape -- the "authored game renders gray" regression: nothing was
#    hidden, the play area was just dwarfed by a fit-everything overview of the 80-wide floor.)
# =========================================================================== #
func _test_authored_no_camera_frames_content(path: String) -> void:
	var inst = _instantiate(path)
	if inst == null:
		return
	root.add_child(inst)
	inst.build(0)
	await physics_frame
	var dresser = _dress(inst, {})

	var c: Dictionary = dresser.census()
	# Authored per-body meshes + sun + sky, but NO Camera3D -- exactly what triggers our own cam.
	_check(bool(c["light"]) and bool(c["env"]) and not bool(c["camera"])
		and int(c["authored_bodies"]) == 5,
		"no-camera census: authored art+light+env, camera absent: " + str(c))

	# Authored bodies keep their own meshes; the un-authored zone still gets its proxy.
	_check(_pairs_for_body(dresser, inst, "block_a") == 0, "authored block is NOT proxied")
	_check(_pairs_for_body(dresser, inst, "zone") == 1, "un-authored zone IS still proxied")
	_check(_count_class(dresser, "WorldEnvironment") == 0,
		"no generic sky over the authored env (no-camera game)")
	_check(_count_class(dresser, "DirectionalLight3D") == 0,
		"no generic sun over the authored light (no-camera game)")

	# The game authored NO camera -> the dresser builds its OWN overview camera and it is current.
	_check(dresser._camera != null and root.get_camera_3d() == dresser._camera,
		"dresser owns the overview camera when the game authored none")

	# ... and that camera frames the GAMEPLAY CONTENT: the overview box is strictly tighter than the
	# full collision AABB, i.e. the 80-wide static ground was dropped from the framing (it still
	# renders). Without the fix the two spans are equal and the gameplay is a distant speck.
	var b: Dictionary = dresser.bounds()
	var full_span: float = ((b["max"] as Vector3) - (b["min"] as Vector3)).length()
	var ob: Array = dresser._overview_box()
	var ov_span: float = ((ob[1] as Vector3) - (ob[0] as Vector3)).length()
	_check(full_span > 0.0 and ov_span < full_span * 0.6,
		"overview frames content span=%.2f, not the full ground box span=%.2f" % [ov_span, full_span])

	_free(inst, dresser)
	await physics_frame


# =========================================================================== #
# helpers
# =========================================================================== #
func _instantiate(path: String):
	var src := FileAccess.get_file_as_string(path)
	if src == "":
		_fail("cannot read fixture " + path)
		return null
	var gd := GDScript.new()
	gd.source_code = src
	if gd.reload() != OK or not gd.can_instantiate():
		_fail("fixture failed to compile: " + path)
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


func _free(inst, dresser) -> void:
	if dresser != null and is_instance_valid(dresser):
		dresser.free()
	if inst != null and is_instance_valid(inst):
		inst.free()


func _body_named(inst, body_name: String) -> Node:
	# The game's body node whose state() name is `body_name`. The fixtures name their Area/Static
	# nodes directly; the puck/probe is the game's controlled RigidBody.
	for c in inst.get_children():
		if str(c.name) == body_name:
			return c
	# Fall back to the class-shaped guess used by the fixtures (an unnamed RigidBody).
	for c in inst.get_children():
		if (c is RigidBody3D or c is RigidBody2D) and body_name in ["puck", "probe"]:
			return c
		if (c is StaticBody3D or c is StaticBody2D) and body_name == "guard":
			return c
	return null


func _pairs_for_body(dresser, inst, body_name: String) -> int:
	# How many overlay proxies the dresser mirrors onto this body (0 = the game's own art is
	# the only thing drawn for it). Counts the halo too, which shares the agent's src shape.
	var body := _body_named(inst, body_name)
	if body == null:
		_fail("fixture has no body named " + body_name)
		return -1
	var n := 0
	for p in dresser._pairs:
		var src = p["src"]
		if is_instance_valid(src) and _owner_of(src) == body:
			n += 1
	return n


func _owner_of(node: Node) -> Node:
	var p := node.get_parent()
	while p != null:
		if p is CollisionObject2D or p is CollisionObject3D:
			return p
		p = p.get_parent()
	return null


func _find_first(node: Node, cls: String) -> Node:
	for c in node.get_children():
		if c.get_class() == cls:
			return c
		var found := _find_first(c, cls)
		if found != null:
			return found
	return null


func _count_class(node: Node, cls: String) -> int:
	var n := 0
	for c in node.get_children():
		if c.get_class() == cls:
			n += 1
		n += _count_class(c, cls)
	return n


func _stage_mesh_names(dresser) -> String:
	var names := PackedStringArray()
	_collect_names(dresser, names)
	return " ".join(names)


func _collect_names(node: Node, out: PackedStringArray) -> void:
	for c in node.get_children():
		out.append(str(c.name))
		_collect_names(c, out)
