# asset_loader.gd -- runtime, RENDER-ONLY loader for the demo asset bank.
#
# The visual dresser calls AssetLoader.load_asset(id, manifest_path, target_size)
# to get a plain Node3D of meshes for a game body. It is COSMETIC: every physics
# node in the source asset is stripped, so it can never touch collision/gameplay.
# The game keeps its own bodies; this node is added as a child purely to be seen.
#
# Runtime GLTF loading works headless, but the GLTF import subsystem must be
# initialised, which happens when Godot boots a real project (run these tools
# with `--path <godotworld>`; a bare `--script` run has append_from_file fail).
#
# This is host/engine code, NOT a generated game, so preload/FileAccess/absolute
# paths are all fine here (the G0 banned-API scan governs game code only).
class_name AssetLoader
extends RefCounted


# Load bank asset `asset_id` (looked up in the manifest at `manifest_path`) and
# return a render-only Node3D, or null on any failure.
#
#   asset_id      : bank id, e.g. "car"
#   manifest_path : path to manifest.json (absolute or res://). Asset files are
#                   resolved relative to the manifest's own directory.
#   target_size   : desired world AABB size (Vector3). Vector3.ZERO = native size.
#   scale_mode    : "fit" (uniform, fit inside target; default), "fill" (uniform,
#                   cover target), or "stretch" (per-axis exact match).
#   anchor        : "center" (asset centre at origin; default) or "base"
#                   (asset centred in X/Z with its min-Y at origin, for ground).
static func load_asset(asset_id: String, manifest_path: String,
		target_size: Vector3 = Vector3.ZERO,
		scale_mode: String = "fit", anchor: String = "center") -> Node3D:
	var manifest := read_manifest(manifest_path)
	if manifest.is_empty():
		push_error("AssetLoader: cannot read manifest " + manifest_path)
		return null
	var entry := _find(manifest, asset_id)
	if entry.is_empty():
		push_error("AssetLoader: unknown asset id '" + asset_id + "'")
		return null

	var bank_dir := manifest_path.get_base_dir()
	var asset_path := bank_dir.path_join(String(entry["file"]))
	var scene := _load_scene(asset_path)
	if scene == null:
		push_error("AssetLoader: failed to load " + asset_path)
		return null

	# Flatten to render-only meshes (drops physics, lights, cameras, empties).
	var meshes: Array = []
	_gather_meshes(scene, Transform3D.IDENTITY, meshes)
	scene.free()
	if meshes.is_empty():
		push_error("AssetLoader: no meshes in " + asset_path)
		return null

	var render_root := Node3D.new()
	render_root.name = "AssetDress_" + asset_id
	var pivot := Node3D.new()
	pivot.name = "pivot"
	render_root.add_child(pivot)

	var box := AABB()
	var has := false
	for m in meshes:
		var mi := MeshInstance3D.new()
		mi.mesh = m["mesh"]
		mi.transform = m["xform"]
		pivot.add_child(mi)
		var world: AABB = (m["xform"] as Transform3D) * (m["mesh"] as Mesh).get_aabb()
		if not has:
			box = world
			has = true
		else:
			box = box.merge(world)

	# Recentre via the pivot so scaling (on render_root) stays about the anchor.
	var offset := -(box.position + box.size * 0.5)
	if anchor == "base":
		offset.y = -box.position.y
	pivot.position = offset

	if target_size != Vector3.ZERO:
		render_root.scale = _scale_for(box.size, target_size, scale_mode)
	return render_root


# Read + JSON-parse a manifest (absolute or res://). {} on any failure.
static func read_manifest(manifest_path: String) -> Dictionary:
	var f := FileAccess.open(manifest_path, FileAccess.READ)
	if f == null:
		return {}
	var parsed = JSON.parse_string(f.get_as_text())
	f.close()
	if typeof(parsed) != TYPE_DICTIONARY or not parsed.has("assets"):
		return {}
	return parsed


static func _find(manifest: Dictionary, asset_id: String) -> Dictionary:
	for a in manifest["assets"]:
		if String(a.get("id", "")) == asset_id:
			return a
	return {}


static func _load_scene(path: String) -> Node:
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	if doc.append_from_file(path, state) != OK:
		return null
	return doc.generate_scene(state)


# Collect (mesh, transform-relative-to-scene-root) for every MeshInstance3D.
# Non-mesh nodes -- crucially any CollisionObject3D / CollisionShape3D / Area3D --
# are simply never copied, so the result is provably physics-free.
static func _gather_meshes(node: Node, xform: Transform3D, out: Array) -> void:
	var here := xform
	if node is Node3D:
		here = xform * (node as Node3D).transform
	if node is MeshInstance3D:
		var mi := node as MeshInstance3D
		if mi.mesh != null:
			out.append({"mesh": mi.mesh, "xform": here})
	for c in node.get_children():
		_gather_meshes(c, here, out)


static func _scale_for(native: Vector3, target: Vector3, mode: String) -> Vector3:
	var nx: float = max(native.x, 1e-6)
	var ny: float = max(native.y, 1e-6)
	var nz: float = max(native.z, 1e-6)
	if mode == "stretch":
		return Vector3(target.x / nx, target.y / ny, target.z / nz)
	var rx := target.x / nx
	var ry := target.y / ny
	var rz := target.z / nz
	var s: float = max(rx, max(ry, rz)) if mode == "fill" else min(rx, min(ry, rz))
	return Vector3(s, s, s)
