# chord_probe_3d.gd -- CHORD-PIVOT test probe (3D). The true-3D twin of
# chord_probe_2d.gd: a minimal RigidBody3D whose per-axis impulse verbs compose exactly,
# so a chord's post-tick velocity is the bit-for-bit sum of its component impulses.
#
# gravity_scale=0, linear_damp=0, no speed cap, no _physics_process: the velocity act()
# sets persists unchanged through the tick's physics frames. chord ["vx","vy"] reads
# exactly (IMPULSE, IMPULSE, 0) == ["vx"] + ["vy"]. Nothing is axis-locked, so all three
# axes are live (a chord could exercise any pair). Used for the 3D twin-determinism run
# and a 3D composition check.
extends Node3D

const IMPULSE := 100.0

var _body: RigidBody3D = null


func build(_world_seed: int) -> void:
	_body = RigidBody3D.new()
	_body.gravity_scale = 0.0           # no gravity
	_body.linear_damp_mode = RigidBody3D.DAMP_MODE_REPLACE
	_body.linear_damp = 0.0             # no damping -> velocity persists exactly
	_body.can_sleep = false
	_body.axis_lock_angular_x = true
	_body.axis_lock_angular_y = true
	_body.axis_lock_angular_z = true
	_body.position = Vector3(300.0, 300.0, 0.0)
	var col := CollisionShape3D.new()
	var sph := SphereShape3D.new()
	sph.radius = 16.0
	col.shape = sph
	_body.add_child(col)
	add_child(_body)

	# A static marker: keeps the world at >=2 entities (contract shape parity).
	var marker := Node3D.new()
	marker.name = "marker"
	marker.position = Vector3(300.0, 100.0, 0.0)
	add_child(marker)


func act(action: String) -> void:
	if _body == null:
		return
	var v := Vector3.ZERO
	match action:
		"vx":
			v = Vector3(IMPULSE, 0.0, 0.0)   # pure +x
		"vy":
			v = Vector3(0.0, IMPULSE, 0.0)   # pure +y
		"vz":
			v = Vector3(0.0, 0.0, IMPULSE)   # pure +z
	_body.apply_central_impulse(v)


func state() -> Dictionary:
	var p := _body.position
	var lv := _body.linear_velocity
	return {
		"bodies": [
			{
				"name": "body",
				"pos": [p.x, p.y, p.z],
				"vel": [lv.x, lv.y, lv.z],
				"angle": 0.0,
				"controlled": true,
				"static": false,
			},
			{
				"name": "marker",
				"pos": [300.0, 100.0, 0.0],
				"vel": [0.0, 0.0, 0.0],
				"angle": 0.0,
				"controlled": false,
				"static": true,
			},
		],
		"flags": {},
	}


func checkpoints() -> Dictionary:
	return {}


func is_success() -> bool:
	return false


func is_failure() -> bool:
	return false


func actions() -> Array:
	return ["vx", "vy", "vz"]
