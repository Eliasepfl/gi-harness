# dressed_ground_3d.gd -- a 3D GameAPI fixture that AUTHORS ITS OWN VISUALS but NO CAMERA.
#
# The shape of the certified KNOCKDOWN game (and the class of game the "authored game renders
# gray" bug was traced to): the author brought per-body meshes, a WorldEnvironment sky and a
# DirectionalLight sun, PLUS a WORLD-BOUNDS ground slab (80x0.2x80) -- but did NOT author a
# Camera3D. Because there is no authored camera, the dresser builds its OWN overview camera; the
# defect was that that camera framed the ENTIRE collision AABB (the 80-wide ground), zooming out
# until the ~a-few-unit gameplay near the origin was a distant speck under the game's own sky.
#
# This fixture pins the FIX: the dresser must frame on the GAMEPLAY CONTENT (blocks + platform +
# zone near the origin), dropping the outsized static ground from the framing box (it still
# RENDERS -- the census keeps its authored mesh; it just no longer drives the zoom).
#
#   * Ground   (StaticBody3D, 80x0.2x80) -> authored green mesh -> world floor: DROPPED from framing
#   * Platform (StaticBody3D, 3x0.5x3)   -> authored gray mesh  -> normal static: KEPT in framing
#   * block_a  (controlled RigidBody3D)  -> authored mesh       -> gameplay: KEPT (a mover)
#   * block_b/c(RigidBody3D)             -> authored meshes      -> gameplay: KEPT (movers)
#   * zone     (Area3D)                  -> authors NOTHING      -> sensor: KEPT + proxied
#   * WorldEnvironment + DirectionalLight3D, and NO Camera3D
#
# A real certifiable-shaped GameAPI game (build/act/state/checkpoints/is_success/is_failure/
# actions), not a render mock: only a plain Node3D, duck-typed like the other gd_games fixtures.

extends Node3D

const WORLD_HALF := 40.0            # the ground spans +-WORLD_HALF on x and z (an 80-wide floor)
const IMPULSE := 6.0

var _rng := RandomNumberGenerator.new()
var _blocks := []                   # [{name, node, controlled}]
var _zone: Area3D = null
var _zone_pos := Vector3(2.5, 0.6, 0.0)
var _knocked := false


func build(world_seed: int) -> void:
	PhysicsServer3D.set_active(true)
	_rng.seed = world_seed
	var jitter := _rng.randf_range(-0.05, 0.05)

	# --- the game's OWN presentation: sky + sun (but NO camera) ------------- #
	var we := WorldEnvironment.new()
	var env := Environment.new()
	env.background_mode = Environment.BG_SKY
	var sky := Sky.new()
	var sky_mat := ProceduralSkyMaterial.new()
	sky_mat.sky_top_color = Color(0.25, 0.55, 0.85)
	sky_mat.ground_bottom_color = Color(0.20, 0.22, 0.25)
	sky.sky_material = sky_mat
	env.sky = sky
	env.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	we.environment = env
	add_child(we)

	var sun := DirectionalLight3D.new()
	sun.rotation = Vector3(deg_to_rad(-55.0), deg_to_rad(-40.0), 0.0)
	add_child(sun)

	# --- Ground: an authored WORLD-BOUNDS slab (drives the "zoomed to a speck" bug) --- #
	var ground := StaticBody3D.new()
	ground.name = "Ground"
	ground.position = Vector3(0.0, 0.0, 0.0)
	var gcol := CollisionShape3D.new()
	var gbox := BoxShape3D.new()
	gbox.size = Vector3(WORLD_HALF * 2.0, 0.2, WORLD_HALF * 2.0)
	gcol.shape = gbox
	ground.add_child(gcol)
	var gmesh := MeshInstance3D.new()
	gmesh.name = "GroundVisual"
	var gbm := BoxMesh.new()
	gbm.size = gbox.size
	gmesh.mesh = gbm
	var gmat := StandardMaterial3D.new()
	gmat.albedo_color = Color(0.28, 0.55, 0.30)
	gmesh.set_surface_override_material(0, gmat)
	ground.add_child(gmesh)
	add_child(ground)

	# --- Platform: a normal-scale authored static the framing must KEEP ------ #
	var platform := StaticBody3D.new()
	platform.name = "Platform"
	platform.position = Vector3(0.0, 0.35, 0.0)
	var pcol := CollisionShape3D.new()
	var pbox := BoxShape3D.new()
	pbox.size = Vector3(3.0, 0.5, 3.0)
	pcol.shape = pbox
	platform.add_child(pcol)
	var pmesh := MeshInstance3D.new()
	pmesh.name = "PlatformVisual"
	var pbm := BoxMesh.new()
	pbm.size = pbox.size
	pmesh.mesh = pbm
	var pmat := StandardMaterial3D.new()
	pmat.albedo_color = Color(0.55, 0.57, 0.62)
	pmesh.set_surface_override_material(0, pmat)
	platform.add_child(pmesh)
	add_child(platform)

	# --- blocks: the gameplay -- small authored movers near the origin ------- #
	_blocks = []
	_add_block("block_a", Vector3(0.0, 1.05 + jitter, 0.0), Color(0.95, 0.55, 0.15), true)
	_add_block("block_b", Vector3(0.0, 1.95 + jitter, 0.0), Color(0.24, 0.78, 0.72), false)
	_add_block("block_c", Vector3(1.2, 1.05 + jitter, 0.0), Color(0.85, 0.30, 0.35), false)

	# --- zone: authors NOTHING -> the dresser proxies it, and it stays in frame --- #
	_zone = Area3D.new()
	_zone.name = "zone"
	_zone.position = _zone_pos
	var zcol := CollisionShape3D.new()
	var zsph := SphereShape3D.new()
	zsph.radius = 1.2
	zcol.shape = zsph
	_zone.add_child(zcol)
	add_child(_zone)


func _add_block(block_name: String, pos: Vector3, col: Color, controlled: bool) -> void:
	var body := RigidBody3D.new()
	body.name = block_name
	body.can_sleep = false
	body.position = pos
	var bcol := CollisionShape3D.new()
	var bbox := BoxShape3D.new()
	bbox.size = Vector3(0.9, 0.9, 0.9)
	bcol.shape = bbox
	body.add_child(bcol)
	var bmesh := MeshInstance3D.new()
	bmesh.name = block_name + "Visual"
	var bm := BoxMesh.new()
	bm.size = bbox.size
	bmesh.mesh = bm
	var bmat := StandardMaterial3D.new()
	bmat.albedo_color = col
	bmesh.set_surface_override_material(0, bmat)
	body.add_child(bmesh)
	add_child(body)
	_blocks.append({"name": block_name, "node": body, "controlled": controlled})


func _controlled_block():
	for b in _blocks:
		if b["controlled"]:
			return b["node"]
	return null


func _physics_process(_delta: float) -> void:
	var ctrl = _controlled_block()
	if ctrl != null and not _knocked and ctrl.position.distance_to(_zone_pos) < 1.4:
		_knocked = true


func act(action: String) -> void:
	var ctrl = _controlled_block()
	if ctrl == null:
		return
	var v := Vector3.ZERO
	match action:
		"left":
			v = Vector3(-IMPULSE, 0.0, 0.0)
		"right":
			v = Vector3(IMPULSE, 0.0, 0.0)
		"forward":
			v = Vector3(0.0, 0.0, -IMPULSE)
		"back":
			v = Vector3(0.0, 0.0, IMPULSE)
	ctrl.apply_central_impulse(v)


func state() -> Dictionary:
	var bodies := []
	for b in _blocks:
		var node: RigidBody3D = b["node"]
		var p := node.position
		var lv := node.linear_velocity
		bodies.append({
			"name": b["name"], "pos": [p.x, p.y, p.z], "vel": [lv.x, lv.y, lv.z],
			"angle": 0.0, "controlled": bool(b["controlled"]), "static": false,
		})
	bodies.append({
		"name": "platform", "pos": [0.0, 0.35, 0.0], "vel": [0.0, 0.0, 0.0],
		"angle": 0.0, "controlled": false, "static": true,
	})
	bodies.append({
		"name": "ground", "pos": [0.0, 0.0, 0.0], "vel": [0.0, 0.0, 0.0],
		"angle": 0.0, "controlled": false, "static": true,
	})
	bodies.append({
		"name": "zone", "pos": [_zone_pos.x, _zone_pos.y, _zone_pos.z],
		"vel": [0.0, 0.0, 0.0], "angle": 0.0, "controlled": false, "static": true,
	})
	return {"bodies": bodies, "flags": {"knocked": _knocked}}


func checkpoints() -> Dictionary:
	return {"reached_zone": _knocked}


func is_success() -> bool:
	return _knocked


func is_failure() -> bool:
	return false


func actions() -> Array:
	return ["left", "right", "forward", "back"]
