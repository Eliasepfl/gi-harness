# test_mesh_lib.gd -- in-image unit test for mesh_lib.gd + the insertion-2 exemplar game.
#
#   godot --headless --path <godotworld> --script res://tests/test_mesh_lib.gd -- <abs mesh_demo_game.gd>
#
# Proves, on the real engine:
#  1. MeshLib.car/spire/ring each produce a non-empty, normalled, vertex-coloured mesh whose
#     triangle count sits inside a LOW-POLY budget and whose AABB has the right silhouette.
#  2. The self-contained exemplar game (harness/demo/mesh_demo_game.gd) compiles in-memory,
#     builds, exposes exactly one controlled body, and dresses itself with >=4 MeshInstance3D
#     that carry ZERO physics nodes -- i.e. the meshes are render-only and cannot touch a verdict.
extends SceneTree

# Preload by path (not the `MeshLib` global) so this works on a fresh headless run whose
# global_script_class_cache has not yet indexed mesh_lib.gd.
const ML := preload("res://mesh_lib.gd")

# Per-shape triangle budgets (generous ceilings; actuals ~ car 72, spire 12, ring 196).
const BUDGET := {"car": 160, "spire": 48, "ring": 260}


func _initialize() -> void:
	var passed := 0
	var failed := 0

	# ---- 1. MeshLib shapes ----
	var car := ML.car()
	var spire := ML.spire(7)
	var ring := ML.ring()

	if _check_mesh("car", car, BUDGET["car"], Vector3(3.5, 1.2, 2.0), Vector3(4.5, 1.7, 2.8)):
		passed += 1
	else:
		failed += 1
	if _check_mesh("spire", spire, BUDGET["spire"], Vector3(1.5, 5.5, 1.5), Vector3(3.2, 6.5, 3.2)):
		passed += 1
	else:
		failed += 1
	if _check_mesh("ring", ring, BUDGET["ring"], Vector3(4.4, 0.6, 4.4), Vector3(5.2, 1.0, 5.2)):
		passed += 1
	else:
		failed += 1

	# spire is deterministic per seed (matters for byte-identical replay)
	var spire_b := ML.spire(7)
	if spire.get_aabb().is_equal_approx(spire_b.get_aabb()):
		print("MESHTEST_OK spire deterministic per seed")
		passed += 1
	else:
		print("MESHTEST_FAIL spire not deterministic")
		failed += 1

	# ---- 2. the self-contained exemplar game ----
	var args := OS.get_cmdline_user_args()
	if args.is_empty():
		print("MESHTEST_FAIL need <mesh_demo_game.gd> abs path arg")
		failed += 1
	elif _check_game(args[0]):
		passed += 1
	else:
		failed += 1

	print("MESHLIB_DONE pass=", passed, " fail=", failed)
	quit(1 if failed > 0 else 0)


func _check_mesh(label: String, mesh: ArrayMesh, tri_budget: int, lo: Vector3, hi: Vector3) -> bool:
	if mesh == null or mesh.get_surface_count() < 1:
		print("MESHTEST_FAIL ", label, " has no surface"); return false
	var arr := mesh.surface_get_arrays(0)
	var verts: PackedVector3Array = arr[Mesh.ARRAY_VERTEX]
	var idx = arr[Mesh.ARRAY_INDEX]
	var normals = arr[Mesh.ARRAY_NORMAL]
	var colors = arr[Mesh.ARRAY_COLOR]
	var tris: int = (idx.size() if (idx != null and idx.size() > 0) else verts.size()) / 3
	var ok := true
	if verts.size() < 9:
		print("MESHTEST_FAIL ", label, " too few verts=", verts.size()); ok = false
	if tris < 1 or tris > tri_budget:
		print("MESHTEST_FAIL ", label, " tris=", tris, " budget=", tri_budget); ok = false
	if normals == null or normals.size() == 0:
		print("MESHTEST_FAIL ", label, " no normals"); ok = false
	if colors == null or colors.size() == 0:
		print("MESHTEST_FAIL ", label, " no vertex colours"); ok = false
	var sz := mesh.get_aabb().size
	if sz.x < lo.x or sz.x > hi.x or sz.y < lo.y or sz.y > hi.y or sz.z < lo.z or sz.z > hi.z:
		print("MESHTEST_FAIL ", label, " AABB size=", sz, " not in [", lo, ",", hi, "]"); ok = false
	if ok:
		print("MESHTEST_OK ", label, " tris=", tris, " verts=", verts.size(),
			" aabb=(%.2f,%.2f,%.2f)" % [sz.x, sz.y, sz.z])
	return ok


func _check_game(game_abs: String) -> bool:
	var f := FileAccess.open(game_abs, FileAccess.READ)
	if f == null:
		print("MESHTEST_FAIL cannot read game ", game_abs); return false
	var gd := GDScript.new()
	gd.source_code = f.get_as_text()
	if gd.reload() != OK or not gd.can_instantiate():
		print("MESHTEST_FAIL game did not compile standalone"); return false
	var inst = gd.new()
	if inst == null or not (inst is Node):
		print("MESHTEST_FAIL game is not a Node"); return false
	root.add_child(inst)
	inst.build(0)

	# exactly one controlled body, >= 2 bodies
	var st = inst.state()
	var controlled := 0
	var nbodies := 0
	if typeof(st) == TYPE_DICTIONARY and typeof(st.get("bodies")) == TYPE_ARRAY:
		for b in st["bodies"]:
			nbodies += 1
			if bool(b.get("controlled", false)):
				controlled += 1
	var ok := true
	if controlled != 1 or nbodies < 2:
		print("MESHTEST_FAIL game bodies=", nbodies, " controlled=", controlled); ok = false

	# >= 4 render meshes, none carrying physics (pure cosmetic)
	var counts := {"mesh": 0, "phys_under_mesh": 0}
	_walk(inst, counts)
	if counts["mesh"] < 4:
		print("MESHTEST_FAIL game has only ", counts["mesh"], " MeshInstance3D"); ok = false
	if counts["phys_under_mesh"] != 0:
		print("MESHTEST_FAIL a render mesh carries ", counts["phys_under_mesh"], " physics nodes"); ok = false
	if ok:
		print("MESHTEST_OK game meshes=", counts["mesh"], " bodies=", nbodies, " controlled=", controlled)
	inst.free()
	return ok


func _walk(node: Node, counts: Dictionary) -> void:
	if node is MeshInstance3D:
		counts["mesh"] = int(counts["mesh"]) + 1
		var phys := {"n": 0}
		_count_phys(node, phys)
		counts["phys_under_mesh"] = int(counts["phys_under_mesh"]) + int(phys["n"])
	for ch in node.get_children():
		_walk(ch, counts)


func _count_phys(node: Node, phys: Dictionary) -> void:
	for ch in node.get_children():
		if (ch is CollisionObject3D) or (ch is CollisionShape3D):
			phys["n"] = int(phys["n"]) + 1
		_count_phys(ch, phys)
