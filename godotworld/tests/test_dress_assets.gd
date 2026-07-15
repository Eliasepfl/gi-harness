# test_dress_assets.gd -- in-image test for the 3D asset-dressing path of visual_dress.gd.
#
#   godot --display-driver x11 --rendering-driver opengl3 --path <godotworld> \
#         --script res://tests/test_dress_assets.gd -- <abs game.gd> <abs manifest.json> [logfile]
#
# Runs under the SAME software-GL X11 path as the capture host (NOT --headless: the overlay's
# render nodes stall the dummy display server). The dresser is added to root exactly as in
# capture_host.gd -- a SIBLING of the game, never a child. It proves, on a real 3D GameAPI
# game (mini_collect_3d):
#   1. ATTACH -- bodies routed to a valid asset id get an "AssetMount" proxy carrying render
#      meshes and ZERO physics nodes (physics provably stripped by AssetLoader).
#   2. FALLBACK -- an unmapped body OR a bogus/unknown asset id falls back to the primitive
#      MeshInstance3D proxy, so the demo always renders.
#   3. ZERO-CONTACT -- dressing never mutates the game subtree (identical descendant + physics
#      node counts before/after dress; the stage is a sibling, never a child of the game).
#   4. IDENTITY -- a DRESSED replay and an UNDRESSED replay produce a byte-identical state()
#      trail (the hard invariant): asset dressing is provably cosmetic.
extends SceneTree

const K_STEPS := 6
const PLAN := ["right", "right", "right", "right", "right", "right", "right", "right",
	"left", "left", "left", "left", "left", "left", "up", "down", "left", "right"]
# state-name -> asset id. car/goal-net are real ids (ATTACH); "__nope__" is a bogus id and
# goal_right/table are left unmapped, both of which must FALL BACK to a primitive proxy.
const ASSETS := {"puck": "car", "goal_left": "goal-net", "goal_right": "__nope__"}

var _logf := ""


func _log(s: String) -> void:
	print(s)
	if _logf != "":
		var f := FileAccess.open(_logf,
			FileAccess.READ_WRITE if FileAccess.file_exists(_logf) else FileAccess.WRITE)
		if f:
			f.seek_end()
			f.store_line(s)
			f.flush()


func _initialize() -> void:
	# Determinism pins -- identical across both passes (mirrors serve/capture host).
	Engine.physics_ticks_per_second = 60
	Engine.max_physics_steps_per_frame = 8
	Engine.physics_jitter_fix = 0.0
	_run()


func _run() -> void:
	var args := OS.get_cmdline_user_args()
	if args.size() < 2:
		push_error("test_dress_assets: need <game.gd> <manifest.json> args")
		quit(2)
		return
	var game_file: String = args[0]
	var manifest: String = args[1]
	if args.size() >= 3:
		_logf = args[2]

	var src := FileAccess.get_file_as_string(game_file)
	if src == "":
		_log("DRESSTEST_FAIL cannot read " + game_file)
		quit(2)
		return
	var gd := GDScript.new()
	gd.source_code = src
	if gd.reload() != OK or not gd.can_instantiate():
		_log("DRESSTEST_FAIL game failed to compile")
		quit(2)
		return

	var passed := 0
	var failed := 0
	_log("DRESST_MARK start")

	# ---- Pass A: UNDRESSED state trail ------------------------------------ #
	var inst_a = gd.new()
	root.add_child(inst_a)
	inst_a.build(0)
	await physics_frame
	var descendants_before := _count_all(inst_a)
	var phys_before := _count_phys(inst_a)
	var fp_a := await _play(inst_a, null)
	inst_a.free()                     # free BEFORE the next build (no physics overlap)
	await physics_frame
	_log("DRESST_MARK undressed-done")

	# ---- Pass B: DRESSED (asset overlay) state trail + proxy inspection --- #
	var inst_b = gd.new()
	root.add_child(inst_b)
	inst_b.build(0)
	await physics_frame
	var dresser = load("res://visual_dress.gd").new()
	root.add_child(dresser)   # a SIBLING of the game, exactly like capture_host.gd
	dresser.dress(inst_b, {"assets": ASSETS, "manifest_path": manifest,
		"view_w": 960.0, "view_h": 540.0})
	_log("DRESST_MARK dressed pairs=" + str(dresser._pairs.size()))

	# (3) ZERO-CONTACT: the game subtree is untouched by dressing.
	var descendants_after := _count_all(inst_b)
	var phys_after := _count_phys(inst_b)
	if descendants_after == descendants_before and phys_after == phys_before:
		_log("DRESSTEST_OK zero-contact descendants=" + str(descendants_after) +
			" phys=" + str(phys_after))
		passed += 1
	else:
		_log("DRESSTEST_FAIL game mutated: descendants " + str(descendants_before) + "->" +
			str(descendants_after) + " phys " + str(phys_before) + "->" + str(phys_after))
		failed += 1
	if dresser.get_parent() == inst_b:
		_log("DRESSTEST_FAIL dresser is a CHILD of the game")
		failed += 1

	# (1)+(2) ATTACH / FALLBACK: classify every proxy.
	var asset_proxies := 0
	var prim_proxies := 0
	var asset_ok := true
	for p in dresser._pairs:
		var proxy = p["proxy"]
		if proxy is MeshInstance3D:
			prim_proxies += 1
		else:
			asset_proxies += 1
			var meshes := _count_mesh(proxy)
			var phys := _count_phys(proxy)
			if meshes < 1 or phys != 0:
				asset_ok = false
				_log("DRESSTEST_FAIL asset proxy meshes=" + str(meshes) + " phys=" + str(phys))
	# ASSETS maps puck->car (valid) and goal_left->goal-net (valid) -> 2 asset mounts;
	# goal_right->bogus and table->unmapped -> 2 primitive fallbacks.
	if asset_proxies == 2 and asset_ok:
		_log("DRESSTEST_OK attach: " + str(asset_proxies) + " asset mounts, meshes>0 & 0 physics")
		passed += 1
	else:
		_log("DRESSTEST_FAIL attach: asset_proxies=" + str(asset_proxies) + " ok=" + str(asset_ok))
		failed += 1
	if prim_proxies == 2:
		_log("DRESSTEST_OK fallback: " + str(prim_proxies) + " primitive proxies (unmapped + bogus)")
		passed += 1
	else:
		_log("DRESSTEST_FAIL fallback: prim_proxies=" + str(prim_proxies) + " (expected 2)")
		failed += 1

	var fp_b := await _play(inst_b, dresser)
	inst_b.free()
	_log("DRESST_MARK dressed-done")

	# (4) IDENTITY: dressed vs undressed state trails are byte-identical.
	if fp_a == fp_b and fp_a != "":
		_log("DRESSTEST_OK identity: dressed==undressed state trail (" + str(fp_a.length()) + " bytes)")
		passed += 1
	else:
		_log("DRESSTEST_FAIL identity: trails differ")
		var la := fp_a.split("\n")
		var lb := fp_b.split("\n")
		for i in range(min(la.size(), lb.size())):
			if la[i] != lb[i]:
				_log("  first diff @tick " + str(i) + "\n   undressed=" + la[i] + "\n   dressed  =" + lb[i])
				break
		failed += 1

	_log("DRESST_DONE pass=" + str(passed) + " fail=" + str(failed))
	quit(1 if failed > 0 else 0)


func _play(inst, dresser) -> String:
	# Step the fixed PLAN with the serve discipline (act + K physics frames), returning the
	# per-tick state() fingerprint. If a dresser is given, sync() it each tick (read-only).
	var lines := PackedStringArray()
	lines.append(_fp(inst, 0))
	if dresser != null:
		dresser.sync()
	var applied := 0
	for a in PLAN:
		inst.act(a)
		applied += 1
		for k in range(K_STEPS):
			await physics_frame
		if dresser != null:
			dresser.sync()
		lines.append(_fp(inst, applied))
	return "\n".join(lines)


func _fp(inst, tick_no: int) -> String:
	# The SAME signature capture_host.gd writes (name:pos:vel:angle at %.17f).
	var parts := PackedStringArray()
	var st = inst.state()
	if typeof(st) == TYPE_DICTIONARY:
		var bodies = st.get("bodies", [])
		if typeof(bodies) == TYPE_ARRAY:
			for b in bodies:
				if typeof(b) != TYPE_DICTIONARY:
					continue
				parts.append("%s:%s:%s:%.17f" % [
					str(b.get("name", "")), _vecstr(b.get("pos", [])),
					_vecstr(b.get("vel", [])), float(b.get("angle", 0.0))])
	return "%d|%s" % [tick_no, ";".join(parts)]


func _vecstr(a) -> String:
	var parts := PackedStringArray()
	if typeof(a) == TYPE_ARRAY:
		for x in a:
			parts.append("%.17f" % float(x))
	return ",".join(parts)


func _count_all(node: Node) -> int:
	var n := node.get_child_count()
	for c in node.get_children():
		n += _count_all(c)
	return n


func _count_phys(node: Node) -> int:
	var n := 0
	if (node is CollisionObject3D) or (node is CollisionShape3D) \
			or (node is CollisionObject2D) or (node is CollisionShape2D):
		n += 1
	for c in node.get_children():
		n += _count_phys(c)
	return n


func _count_mesh(node: Node) -> int:
	var n := 0
	if node is MeshInstance3D and (node as MeshInstance3D).mesh != null:
		n += 1
	for c in node.get_children():
		n += _count_mesh(c)
	return n
