# dressed_arena_3d.gd -- a 3D GameAPI fixture that AUTHORS ITS OWN PRESENTATION.
#
# The counterpart to mini_collect_3d.gd (identical mechanics, deliberately: a bare game and an
# authored game the dresser must treat differently). It models the shape of the certified
# library's richest games (the arena shooter, the ring courses): the game brings its own
# camera, sun, sky and per-body meshes, and the dresser must add NOTHING over them.
#
# It is deliberately MIXED, because the real library is mixed:
#   * puck  (controlled RigidBody3D) -> authors an emissive MeshInstance3D  -> must NOT be proxied
#   * guard (StaticBody3D)           -> authors a red MeshInstance3D        -> must NOT be proxied
#   * pad   (Area3D)                 -> authors NOTHING                     -> MUST be proxied
#   * a root-level floor MeshInstance3D  -> the game painted its own world -> no generic ground
#   * Camera3D + DirectionalLight3D + WorldEnvironment -> none of ours stamped over them
#
# Physics/contract are a copy of mini_collect_3d.gd so the fixture is a REAL certifiable game,
# not a render mock: the visuals are the only difference.

extends Node3D

const COLLECT_R := 45.0
const IMPULSE := 150.0
const DAMP := 3.0
const MAX_V := 130.0

var _rng := RandomNumberGenerator.new()
var _puck: RigidBody3D = null
var _guard: StaticBody3D = null
var _pad: Area3D = null
var _pad_pos := Vector3(280.0, 300.0, 0.0)
var _collected := false


func build(world_seed: int) -> void:
	PhysicsServer3D.set_active(true)
	_rng.seed = world_seed
	var jitter := _rng.randf_range(-5.0, 5.0)

	# --- the game's OWN presentation: sky, sun, camera --------------------- #
	var we := WorldEnvironment.new()
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.05, 0.02, 0.09)      # a deliberate NIGHT arena, not our sky
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.3, 0.2, 0.4)
	we.environment = env
	add_child(we)

	var sun := DirectionalLight3D.new()
	sun.rotation = Vector3(deg_to_rad(-70.0), deg_to_rad(20.0), 0.0)
	sun.light_color = Color(0.8, 0.7, 1.0)
	add_child(sun)

	var cam := Camera3D.new()
	cam.name = "ArenaCam"
	cam.position = Vector3(400.0, 300.0, 700.0)
	add_child(cam)

	# --- the game's OWN world: an authored arena floor at scene level ------- #
	var floor_mi := MeshInstance3D.new()
	floor_mi.name = "ArenaFloor"
	var fm := BoxMesh.new()
	fm.size = Vector3(800.0, 600.0, 10.0)
	floor_mi.mesh = fm
	floor_mi.position = Vector3(400.0, 300.0, -20.0)
	var fmat := StandardMaterial3D.new()
	fmat.albedo_color = Color(0.16, 0.11, 0.22)
	floor_mi.set_surface_override_material(0, fmat)
	add_child(floor_mi)

	# --- puck: authors its own emissive mesh ------------------------------- #
	_puck = RigidBody3D.new()
	_puck.gravity_scale = 0.0
	_puck.linear_damp_mode = RigidBody3D.DAMP_MODE_REPLACE
	_puck.linear_damp = DAMP
	_puck.axis_lock_linear_z = true
	_puck.axis_lock_angular_x = true
	_puck.axis_lock_angular_y = true
	_puck.axis_lock_angular_z = true
	_puck.can_sleep = false
	_puck.position = Vector3(400.0, 300.0 + jitter, 0.0)
	var pcol := CollisionShape3D.new()
	var sph := SphereShape3D.new()
	sph.radius = 16.0
	pcol.shape = sph
	_puck.add_child(pcol)
	var pmesh := MeshInstance3D.new()
	pmesh.name = "PuckVisual"
	var psm := SphereMesh.new()
	psm.radius = 16.0
	psm.height = 32.0
	pmesh.mesh = psm
	var pmat := StandardMaterial3D.new()
	pmat.albedo_color = Color(0.2, 0.9, 1.0)
	pmat.emission_enabled = true
	pmat.emission = Color(0.2, 0.9, 1.0)
	pmesh.set_surface_override_material(0, pmat)
	_puck.add_child(pmesh)
	add_child(_puck)

	# --- guard: authors its own red mesh ----------------------------------- #
	_guard = StaticBody3D.new()
	_guard.position = Vector3(400.0, 480.0, 0.0)
	var gcol := CollisionShape3D.new()
	var gbox := BoxShape3D.new()
	gbox.size = Vector3(60.0, 60.0, 60.0)
	gcol.shape = gbox
	_guard.add_child(gcol)
	var gmesh := MeshInstance3D.new()
	gmesh.name = "GuardVisual"
	var gbm := BoxMesh.new()
	gbm.size = Vector3(60.0, 60.0, 60.0)
	gmesh.mesh = gbm
	var gmat := StandardMaterial3D.new()
	gmat.albedo_color = Color(1.0, 0.25, 0.25)
	gmesh.set_surface_override_material(0, gmat)
	_guard.add_child(gmesh)
	add_child(_guard)

	# --- pad: authors NOTHING -> the dresser must still make it visible ----- #
	_pad = Area3D.new()
	_pad.name = "pad"
	_pad.position = _pad_pos
	var acol := CollisionShape3D.new()
	var asph := SphereShape3D.new()
	asph.radius = COLLECT_R
	acol.shape = asph
	_pad.add_child(acol)
	add_child(_pad)


func _physics_process(_delta: float) -> void:
	if _puck == null:
		return
	if _puck.linear_velocity.length() > MAX_V:
		_puck.linear_velocity = _puck.linear_velocity.limit_length(MAX_V)
	if not _collected and _puck.position.distance_to(_pad_pos) < COLLECT_R:
		_collected = true


func act(action: String) -> void:
	if _puck == null:
		return
	var v := Vector3.ZERO
	match action:
		"left":
			v = Vector3(-IMPULSE, 0.0, 0.0)
		"right":
			v = Vector3(IMPULSE, 0.0, 0.0)
		"up":
			v = Vector3(0.0, -IMPULSE, 0.0)
		"down":
			v = Vector3(0.0, IMPULSE, 0.0)
	_puck.apply_central_impulse(v)


func state() -> Dictionary:
	var p := _puck.position
	var lv := _puck.linear_velocity
	var gp := _guard.position
	return {
		"bodies": [
			{"name": "puck", "pos": [p.x, p.y, p.z], "vel": [lv.x, lv.y, lv.z],
				"angle": 0.0, "controlled": true, "static": false},
			{"name": "pad", "pos": [_pad_pos.x, _pad_pos.y, _pad_pos.z],
				"vel": [0.0, 0.0, 0.0], "angle": 0.0, "controlled": false, "static": true},
			{"name": "guard", "pos": [gp.x, gp.y, gp.z], "vel": [0.0, 0.0, 0.0],
				"angle": 0.0, "controlled": false, "static": true},
		],
		"flags": {"docked": _collected},
	}


func checkpoints() -> Dictionary:
	return {"docked": _collected}


func is_success() -> bool:
	return _collected


func is_failure() -> bool:
	return false


func actions() -> Array:
	return ["left", "right", "up", "down"]
