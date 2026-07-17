# chord_probe_2d.gd -- CHORD-PIVOT test probe (2D). NOT a production/contract game: a
# minimal RigidBody2D whose per-axis impulse verbs compose PERFECTLY LINEARLY, so a
# chord's post-tick velocity is the EXACT (bit-for-bit) sum of its component impulses.
#
# WHY A DEDICATED PROBE. The shipped collect fixtures cap speed (MAX_V) via an isotropic
# limit_length(), which couples the axes non-linearly -- fine for gameplay, useless for a
# clean composition assertion. Here: gravity_scale=0, linear_damp=0, NO speed cap, and no
# _physics_process, so the velocity that act() sets PERSISTS unchanged through the tick's
# physics frames. chord ["vx","vy"] therefore reads exactly (IMPULSE, IMPULSE) ==
# ["vx"] velocity + ["vy"] velocity, with no floating-point slop to reason around.
extends Node2D

const IMPULSE := 100.0

var _body: RigidBody2D = null


func build(_world_seed: int) -> void:
	_body = RigidBody2D.new()
	_body.gravity_scale = 0.0           # no gravity
	_body.linear_damp_mode = RigidBody2D.DAMP_MODE_REPLACE
	_body.linear_damp = 0.0             # no damping -> velocity persists exactly
	_body.lock_rotation = true
	_body.can_sleep = false
	_body.position = Vector2(300.0, 300.0)
	var col := CollisionShape2D.new()
	var circ := CircleShape2D.new()
	circ.radius = 16.0
	col.shape = circ
	_body.add_child(col)
	add_child(_body)

	# A static marker: keeps the world at >=2 entities (contract shape parity).
	var marker := Node2D.new()
	marker.name = "marker"
	marker.position = Vector2(300.0, 100.0)
	add_child(marker)


func act(action: String) -> void:
	if _body == null:
		return
	var v := Vector2.ZERO
	match action:
		"vx":
			v = Vector2(IMPULSE, 0.0)   # pure +x
		"vy":
			v = Vector2(0.0, IMPULSE)   # pure +y
	_body.apply_central_impulse(v)


func state() -> Dictionary:
	return {
		"bodies": [
			{
				"name": "body",
				"pos": [_body.position.x, _body.position.y],
				"vel": [_body.linear_velocity.x, _body.linear_velocity.y],
				"angle": _body.rotation,
				"controlled": true,
				"static": false,
			},
			{
				"name": "marker",
				"pos": [300.0, 100.0],
				"vel": [0.0, 0.0],
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
	return ["vx", "vy"]
