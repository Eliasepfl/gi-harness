# measure_aabb.gd -- headless AABB measurement for the demo asset bank.
#
# Runtime GLTF loading works headless (no editor import). For each asset listed
# in <bank>/manifest.json this loads the mesh with GLTFDocument.append_from_file,
# unions every MeshInstance3D AABB in the asset's own root space, and writes
# <bank>/aabb.json  { id: {aabb_min, aabb_max, size, center, mesh_count} }.
#
# This is offline host tooling (NOT sandboxed game code): FileAccess/absolute
# paths are fine here.
#
#   godot --headless --script measure_aabb.gd -- <abs-bank-dir> [<abs-out.json>]
extends SceneTree


func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	if args.is_empty():
		push_error("measure_aabb: need <bank-dir> arg")
		quit(2)
		return
	var bank_dir: String = args[0].trim_suffix("/")
	var out_path: String = args[1] if args.size() > 1 else bank_dir + "/aabb.json"
	var manifest_path := bank_dir + "/manifest.json"

	var mf := FileAccess.open(manifest_path, FileAccess.READ)
	if mf == null:
		push_error("measure_aabb: cannot open " + manifest_path)
		quit(2)
		return
	var manifest = JSON.parse_string(mf.get_as_text())
	mf.close()
	if typeof(manifest) != TYPE_DICTIONARY or not manifest.has("assets"):
		push_error("measure_aabb: bad manifest")
		quit(2)
		return

	var results := {}
	var ok := 0
	var failed := 0
	for entry in manifest["assets"]:
		var id: String = entry["id"]
		var path: String = bank_dir + "/" + String(entry["file"])
		var measured := _measure_one(path)
		if measured.is_empty():
			failed += 1
			print("MEASURE_FAIL ", id, " <- ", path)
			continue
		results[id] = measured
		ok += 1
		var s: Array = measured["size"]
		print("MEASURE_OK ", id, " size=(%.3f, %.3f, %.3f)" % [s[0], s[1], s[2]],
			" meshes=", measured["mesh_count"])

	var of := FileAccess.open(out_path, FileAccess.WRITE)
	if of == null:
		push_error("measure_aabb: cannot write " + out_path)
		quit(2)
		return
	of.store_string(JSON.stringify(results, "  "))
	of.close()
	print("MEASURE_DONE ok=", ok, " failed=", failed, " -> ", out_path)
	quit(1 if failed > 0 else 0)


func _measure_one(path: String) -> Dictionary:
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	var err := doc.append_from_file(path, state)
	if err != OK:
		print("  append_err=", err, " path=", path)
		return {}
	var scene := doc.generate_scene(state)
	if scene == null:
		print("  generate_scene=null path=", path)
		return {}
	var acc := {"box": AABB(), "has": false, "mesh": 0}
	_collect(scene, Transform3D.IDENTITY, acc)
	if not bool(acc["has"]):
		return {}
	var b: AABB = acc["box"]
	var mn := b.position
	var mx := b.position + b.size
	var center := b.position + b.size * 0.5
	return {
		"aabb_min": [mn.x, mn.y, mn.z],
		"aabb_max": [mx.x, mx.y, mx.z],
		"size": [b.size.x, b.size.y, b.size.z],
		"center": [center.x, center.y, center.z],
		"mesh_count": acc["mesh"],
	}


# Union every MeshInstance3D AABB, transformed into the scene root's space.
func _collect(node: Node, xform: Transform3D, acc: Dictionary) -> void:
	var here := xform
	if node is Node3D:
		here = xform * (node as Node3D).transform
	if node is MeshInstance3D:
		var mi := node as MeshInstance3D
		if mi.mesh != null:
			acc["mesh"] = int(acc["mesh"]) + 1
			var world: AABB = here * mi.mesh.get_aabb()
			if not bool(acc["has"]):
				acc["box"] = world
				acc["has"] = true
			else:
				acc["box"] = (acc["box"] as AABB).merge(world)
	for c in node.get_children():
		_collect(c, here, acc)
