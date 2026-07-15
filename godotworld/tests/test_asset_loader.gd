# test_asset_loader.gd -- in-image unit test for AssetLoader (render-only bank loader).
#
#   godot --headless --path <godotworld> --script res://tests/test_asset_loader.gd -- <abs-bank-dir>
#
# Proves, on real bank assets, that load_asset(): loads a GLB/GLTF at runtime,
# yields a Node3D with renderable meshes and ZERO physics nodes, and scales to a
# requested target AABB (stretch = exact; fit = largest axis touches the target).
extends SceneTree

const AssetLoaderScript := preload("res://asset_loader.gd")
const TOL := 0.06


func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	if args.is_empty():
		push_error("test_asset_loader: need <bank-dir> arg")
		quit(2)
		return
	var manifest_path: String = args[0].trim_suffix("/") + "/manifest.json"

	var cases := [
		{"id": "car", "target": Vector3(4, 4, 4), "mode": "fit"},
		{"id": "robot", "target": Vector3(2, 2, 2), "mode": "stretch"},
		{"id": "chest", "target": Vector3(1, 1, 1), "mode": "fit"},
	]
	var passed := 0
	var failed := 0
	for c in cases:
		if _run_case(c, manifest_path):
			passed += 1
		else:
			failed += 1

	# Negative: an unknown id must return null, not crash.
	var none_node := AssetLoaderScript.load_asset("no-such-asset", manifest_path)
	if none_node == null:
		print("LOADERTEST_OK unknown-id -> null")
		passed += 1
	else:
		print("LOADERTEST_FAIL unknown-id returned a node")
		failed += 1

	print("LOADER_DONE pass=", passed, " fail=", failed)
	quit(1 if failed > 0 else 0)


func _run_case(c: Dictionary, manifest_path: String) -> bool:
	var id: String = c["id"]
	var target: Vector3 = c["target"]
	var node: Node3D = AssetLoaderScript.load_asset(id, manifest_path, target, c["mode"])
	if node == null:
		print("LOADERTEST_FAIL ", id, " returned null")
		return false

	var counts := {"mesh": 0, "phys": 0}
	var box := {"box": AABB(), "has": false}
	_inspect(node, Transform3D.IDENTITY, counts, box)

	var ok := true
	if counts["mesh"] < 1:
		print("LOADERTEST_FAIL ", id, " has no MeshInstance3D"); ok = false
	if counts["phys"] != 0:
		print("LOADERTEST_FAIL ", id, " has ", counts["phys"], " physics nodes"); ok = false
	if not bool(box["has"]):
		print("LOADERTEST_FAIL ", id, " produced no AABB"); ok = false
		node.free()
		return false

	var size: Vector3 = (box["box"] as AABB).size
	if c["mode"] == "stretch":
		if not (_close(size.x, target.x) and _close(size.y, target.y) and _close(size.z, target.z)):
			print("LOADERTEST_FAIL ", id, " stretch size=", size, " != ", target); ok = false
	else:  # fit: fits inside target, largest axis touches it
		var ratio: float = max(size.x / target.x, max(size.y / target.y, size.z / target.z))
		if size.x > target.x * (1 + TOL) or size.y > target.y * (1 + TOL) or size.z > target.z * (1 + TOL):
			print("LOADERTEST_FAIL ", id, " fit exceeds target size=", size); ok = false
		if abs(ratio - 1.0) > TOL:
			print("LOADERTEST_FAIL ", id, " fit largest axis ratio=", ratio); ok = false

	if ok:
		print("LOADERTEST_OK ", id, " meshes=", counts["mesh"], " phys=", counts["phys"],
			" size=(%.2f, %.2f, %.2f)" % [size.x, size.y, size.z])
	node.free()
	return ok


func _inspect(node: Node, xform: Transform3D, counts: Dictionary, box: Dictionary) -> void:
	var here := xform
	if node is Node3D:
		here = xform * (node as Node3D).transform
	if node is MeshInstance3D and (node as MeshInstance3D).mesh != null:
		counts["mesh"] = int(counts["mesh"]) + 1
		var world: AABB = here * (node as MeshInstance3D).mesh.get_aabb()
		if not bool(box["has"]):
			box["box"] = world
			box["has"] = true
		else:
			box["box"] = (box["box"] as AABB).merge(world)
	if (node is CollisionObject3D) or (node is CollisionShape3D):
		counts["phys"] = int(counts["phys"]) + 1
	for ch in node.get_children():
		_inspect(ch, here, counts, box)


func _close(a: float, b: float) -> bool:
	return abs(a - b) <= max(TOL, abs(b) * TOL)
