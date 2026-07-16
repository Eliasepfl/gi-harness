# mesh_demo_game.gd -- a SELF-CONTAINED GDScript game that dresses ITSELF with recognizable
# low-poly meshes, built entirely in code. This is the insertion-point-2 exemplar for
# notes/engines/ASSET_CREATION_3D.md: exactly the shape a generated game would take.
#
# It obeys every sandbox rule a real generated game must obey:
#   * ONE file, NO class_name, NO `extends SomeGameBase` (extends the engine's Node3D root).
#   * NO load()/preload(), NO external assets -- every mesh is emitted from SurfaceTool.
#   * compiles STANDALONE:  godot --check-only --script harness/demo/mesh_demo_game.gd
# The three private mesh builders (_mesh_car/_mesh_spire/_mesh_ring) are the INLINED twin of
# godotworld/mesh_lib.gd -- a hermetic game cannot share a library, so it carries its own.
#
# The meshes are RENDER-ONLY children of the physics bodies (a MeshInstance3D per the
# contract's own example, api_gdscript.md:46). They carry no collision and never touch
# state(); certification is pixel-blind, so they cannot change any verdict.
#
# The game: drive the red car (+X = forward) across the yard and through the green ring gate
# without falling off the far edge.
extends Node3D

const DRIVE := 42.0
const STEER := 26.0
const BOUND := 15.0

var _car: RigidBody3D
var _gate_c: Vector3
var _passed := false
var _fell := false


func build(world_seed: int) -> void:
	PhysicsServer3D.set_active(true)
	var rng := RandomNumberGenerator.new()
	rng.seed = world_seed

	# ground (static, finite -- the edge is a real stake)
	var ground := StaticBody3D.new()
	ground.name = "yard"
	var gcs := CollisionShape3D.new()
	var gbox := BoxShape3D.new()
	gbox.size = Vector3(2 * BOUND, 1.0, 2 * BOUND)
	gcs.shape = gbox
	ground.add_child(gcs)
	ground.position = Vector3(0, -0.5, 0)
	add_child(ground)
	_decor(ground, _ground_mesh(), Vector3.ZERO)

	# the controlled car (dynamic), dressed with the inline low-poly car mesh
	_car = RigidBody3D.new()
	_car.name = "car"
	var ccs := CollisionShape3D.new()
	var cbox := BoxShape3D.new()
	cbox.size = Vector3(4.0, 1.4, 1.9)
	ccs.shape = cbox
	ccs.position = Vector3(0, 0.7, 0)
	_car.add_child(ccs)
	_car.position = Vector3(-9.0, 1.0, 0.0)
	_car.axis_lock_angular_x = true
	_car.axis_lock_angular_z = true
	add_child(_car)
	_decor(_car, _mesh_car(), Vector3.ZERO)

	# a rock spire obstacle (static)
	var spire := StaticBody3D.new()
	spire.name = "spire"
	var scs := CollisionShape3D.new()
	var scyl := CylinderShape3D.new()
	scyl.height = 6.0
	scyl.radius = 1.3
	scs.shape = scyl
	scs.position = Vector3(0, 3.0, 0)
	spire.add_child(scs)
	spire.position = Vector3(-1.0, 0.0, -4.5)
	add_child(spire)
	_decor(spire, _mesh_spire(world_seed), Vector3.ZERO)

	# the ring gate (Area3D sensor goal), dressed with the inline torus mesh
	var gate := Area3D.new()
	gate.name = "ring_gate"
	var acs := CollisionShape3D.new()
	var acyl := CylinderShape3D.new()
	acyl.height = 0.8
	acyl.radius = 2.0
	acs.shape = acyl
	acs.rotation_degrees = Vector3(0, 0, 90)   # ring lies across the +X driving line
	gate.add_child(acs)
	_gate_c = Vector3(9.0, 2.4, 0.0)
	gate.position = _gate_c
	add_child(gate)
	var ring_dec := _decor(gate, _mesh_ring(), Vector3.ZERO)
	ring_dec.rotation_degrees = Vector3(0, 0, 90)

	# render-only camera + light so the game frames itself for the demo lane
	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-52, -120, 0)
	sun.light_energy = 1.3
	add_child(sun)
	var cam := Camera3D.new()
	cam.fov = 52.0
	add_child(cam)   # must be in-tree before look_at (global transform)
	cam.look_at_from_position(Vector3(6.0, 8.0, 15.0), Vector3(0, 1.5, 0), Vector3.UP)


func act(action: String) -> void:
	if _car == null:
		return
	match action:
		"forward":
			_car.apply_central_force(Vector3(DRIVE, 0, 0))
		"left":
			_car.apply_central_force(Vector3(0, 0, -STEER))
		"right":
			_car.apply_central_force(Vector3(0, 0, STEER))
		"brake":
			_car.linear_velocity *= 0.80


func _physics_process(_delta: float) -> void:
	if _car == null:
		return
	if _car.global_position.y < -3.0 or abs(_car.global_position.z) > BOUND:
		_fell = true
	if _car.global_position.distance_to(_gate_c) < 2.2:
		_passed = true


func state() -> Dictionary:
	var bodies := []
	bodies.append(_body_entry(_car, true, false))
	for n in ["spire", "ring_gate"]:
		var node := get_node_or_null(NodePath(n))
		if node != null:
			bodies.append(_body_entry(node, false, true))
	return {"bodies": bodies}


func checkpoints() -> Dictionary:
	return {
		"left_start": _car != null and _car.global_position.x > -6.0,
		"passed_gate": _passed,
	}


func is_success() -> bool:
	return _passed


func is_failure() -> bool:
	return _fell


func actions() -> Array:
	return ["forward", "left", "right", "brake"]


# ---- state() helper --------------------------------------------------------- #
func _body_entry(node: Node3D, controlled: bool, is_static: bool) -> Dictionary:
	var p: Vector3 = node.global_position if node != null else Vector3.ZERO
	var v := Vector3.ZERO
	if node is RigidBody3D:
		v = (node as RigidBody3D).linear_velocity
	return {
		"name": node.name if node != null else "",
		"pos": [p.x, p.y, p.z],
		"vel": [v.x, v.y, v.z],
		"angle": node.rotation.y if node != null else 0.0,
		"controlled": controlled,
		"static": is_static,
	}


# ---- render-only dressing helper -------------------------------------------- #
func _decor(body: Node, mesh: ArrayMesh, offset: Vector3) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	mi.mesh = mesh
	mi.position = offset
	var m := StandardMaterial3D.new()
	m.vertex_color_use_as_albedo = true
	m.roughness = 0.92
	m.cull_mode = BaseMaterial3D.CULL_DISABLED
	mi.material_override = m
	body.add_child(mi)
	return mi


func _ground_mesh() -> ArrayMesh:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	_box(st, Vector3(0, 0, 0), Vector3(2 * BOUND, 1.0, 2 * BOUND), Color(0.30, 0.33, 0.30))
	st.generate_normals()
	return st.commit()


# ==== INLINED TWIN of godotworld/mesh_lib.gd (a hermetic game carries its own) ==== #
func _mesh_car() -> ArrayMesh:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	_box(st, Vector3(0.0, 0.45, 0.0), Vector3(4.0, 0.7, 1.9), Color(0.86, 0.22, 0.20))
	_box(st, Vector3(-0.25, 1.05, 0.0), Vector3(2.1, 0.7, 1.7), Color(0.14, 0.17, 0.24))
	for wx in [1.35, -1.35]:
		for wz in [1.0, -1.0]:
			_box(st, Vector3(wx, 0.35, wz), Vector3(0.7, 0.7, 0.5), Color(0.05, 0.05, 0.06))
	st.generate_normals()
	return st.commit()


func _mesh_spire(world_seed: int) -> ArrayMesh:
	var rng := RandomNumberGenerator.new()
	rng.seed = world_seed
	var sides := 6
	var apex := Vector3(0.0, 6.0, 0.0)
	var rim := []
	for i in range(sides):
		var a := TAU * float(i) / float(sides)
		var r := 1.5 * rng.randf_range(0.72, 1.0)
		rim.append(Vector3(cos(a) * r, 0.0, sin(a) * r))
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	for i in range(sides):
		var p0: Vector3 = rim[i]
		var p1: Vector3 = rim[(i + 1) % sides]
		_tri(st, p0, p1, apex, Color(0.42, 0.40, 0.44), Color(0.42, 0.40, 0.44), Color(0.60, 0.58, 0.62))
		_tri(st, Vector3.ZERO, p1, p0, Color(0.42, 0.40, 0.44), Color(0.42, 0.40, 0.44), Color(0.42, 0.40, 0.44))
	st.generate_normals()
	return st.commit()


func _mesh_ring() -> ArrayMesh:
	var nu := 14
	var nv := 7
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	for i in range(nu):
		for j in range(nv):
			_quad(st, _torus_pt(2.0, 0.4, i, j, nu, nv), _torus_pt(2.0, 0.4, i + 1, j, nu, nv),
				_torus_pt(2.0, 0.4, i + 1, j + 1, nu, nv), _torus_pt(2.0, 0.4, i, j + 1, nu, nv),
				Color(0.20, 0.78, 0.55))
	st.generate_normals()
	return st.commit()


func _torus_pt(rr: float, tr: float, i: int, j: int, nu: int, nv: int) -> Vector3:
	var u := TAU * float(i) / float(nu)
	var v := TAU * float(j) / float(nv)
	var ring_r := rr + tr * cos(v)
	return Vector3(cos(u) * ring_r, tr * sin(v), sin(u) * ring_r)


func _tri(st: SurfaceTool, a: Vector3, b: Vector3, c: Vector3, ca: Color, cb: Color, cc: Color) -> void:
	st.set_color(ca); st.add_vertex(a)
	st.set_color(cb); st.add_vertex(b)
	st.set_color(cc); st.add_vertex(c)


func _quad(st: SurfaceTool, a: Vector3, b: Vector3, c: Vector3, d: Vector3, col: Color) -> void:
	_tri(st, a, b, c, col, col, col)
	_tri(st, a, c, d, col, col, col)


func _box(st: SurfaceTool, c: Vector3, s: Vector3, col: Color) -> void:
	var h := s * 0.5
	var p000 := c + Vector3(-h.x, -h.y, -h.z)
	var p100 := c + Vector3(h.x, -h.y, -h.z)
	var p110 := c + Vector3(h.x, h.y, -h.z)
	var p010 := c + Vector3(-h.x, h.y, -h.z)
	var p001 := c + Vector3(-h.x, -h.y, h.z)
	var p101 := c + Vector3(h.x, -h.y, h.z)
	var p111 := c + Vector3(h.x, h.y, h.z)
	var p011 := c + Vector3(-h.x, h.y, h.z)
	_quad(st, p001, p101, p111, p011, col)
	_quad(st, p100, p000, p010, p110, col)
	_quad(st, p000, p001, p011, p010, col)
	_quad(st, p101, p100, p110, p111, col)
	_quad(st, p011, p111, p110, p010, col)
	_quad(st, p000, p100, p101, p001, col)
