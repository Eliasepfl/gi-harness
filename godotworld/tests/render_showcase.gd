# render_showcase.gd -- PROOF render for mesh_lib.gd (Track A prototype).
#
# Renders the three MeshLib shapes (car / rock-spire / ring gate) arranged on a ground
# plane to a single PNG, using the EXACT software-GL mechanics the certified capture lane
# uses (x11 + opengl3 + llvmpipe, RenderingServer.force_draw, viewport read-back, save_png
# -- see godotworld/capture_host.gd). This is deliberately NOT the capture host: it carries
# no game contract and no dresser, so what you see is purely MeshLib's output.
#
# Run (inside gi-certifier.sif, under Xvfb -- see harness/demo/render_meshes.sh):
#   godot --display-driver x11 --rendering-driver opengl3 --path godotworld \
#         -s res://tests/render_showcase.gd -- --out=<abs.png> [--width=1100] [--height=680]
extends SceneTree

# Preload by path (not the `MeshLib` global) so this works on a fresh headless run whose
# global_script_class_cache has not yet indexed mesh_lib.gd.
const ML := preload("res://mesh_lib.gd")

var _out := ""
var _width := 1100
var _height := 680
var _seed := 7


func _initialize() -> void:
	for a in OS.get_cmdline_user_args():
		if a.begins_with("--out="):
			_out = a.substr(6)
		elif a.begins_with("--width="):
			_width = int(a.substr(8))
		elif a.begins_with("--height="):
			_height = int(a.substr(9))
		elif a.begins_with("--seed="):
			_seed = int(a.substr(7))
	if _out == "":
		push_error("render_showcase: --out=<png> required")
		quit(2)
		return
	_run()


func _run() -> void:
	root.size = Vector2i(_width, _height)

	# --- lighting + sky so no face is ever pure black (robust to flat normals) ---
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.52, 0.62, 0.74)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.62, 0.66, 0.72)
	env.ambient_light_energy = 0.55
	var we := WorldEnvironment.new()
	we.environment = env
	root.add_child(we)

	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-52, -128, 0)
	sun.light_energy = 1.35
	root.add_child(sun)

	# --- ground plane ---
	var ground := MeshInstance3D.new()
	var pm := PlaneMesh.new()
	pm.size = Vector2(34, 34)
	ground.mesh = pm
	ground.material_override = _mat(Color(0.30, 0.33, 0.30), false)
	root.add_child(ground)

	# --- the three MeshLib shapes, arranged for a clean read ---
	_place(ML.car(), Vector3(0.0, 0.0, 2.6), 0.0)
	_place(ML.spire(_seed), Vector3(-6.0, 0.0, -4.5), 0.0)
	_place(ML.ring(), Vector3(6.0, 2.4, -4.0), 90.0)   # stood upright, like a fly-through gate

	# --- camera framing all three ---
	var cam := Camera3D.new()
	cam.fov = 48.0
	root.add_child(cam)   # must be in-tree before look_at (global transform)
	cam.look_at_from_position(Vector3(11.5, 7.5, 13.5), Vector3(0.0, 1.4, -1.0), Vector3.UP)
	cam.make_current()

	# settle a couple of frames, then one synchronous draw + read-back
	await process_frame
	await process_frame
	RenderingServer.force_draw(false)
	var img := root.get_texture().get_image()
	if img == null:
		push_error("render_showcase: null viewport image")
		quit(1)
		return
	DirAccess.make_dir_recursive_absolute(_out.get_base_dir())
	var err := img.save_png(_out)
	if err != OK:
		push_error("render_showcase: save_png failed err=%d" % err)
		quit(1)
		return
	print("SHOWCASE_OK wrote ", _out, " (", _width, "x", _height, ")")
	quit(0)


func _place(mesh: ArrayMesh, pos: Vector3, yaw_deg: float) -> void:
	var mi := MeshInstance3D.new()
	mi.mesh = mesh
	mi.material_override = _mat(Color.WHITE, true)
	mi.position = pos
	mi.rotation_degrees = Vector3(0.0, yaw_deg, 0.0)
	root.add_child(mi)


static func _mat(albedo: Color, use_vertex_color: bool) -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.albedo_color = albedo
	m.vertex_color_use_as_albedo = use_vertex_color
	m.roughness = 0.92
	m.cull_mode = BaseMaterial3D.CULL_DISABLED   # winding-safe: never drop a face
	return m
