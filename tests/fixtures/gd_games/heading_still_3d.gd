# heading_still_3d.gd -- zero-velocity HEADING-FRAME fixture (GDScript lane, serve/RL only).
#
# A lock_rotation RigidBody3D that DOES NOT MOVE (zero velocity, no gravity). With speed below
# the heading epsilon, the heading frame must fall back to the body's INITIAL FACING (-Z at
# identity) rather than normalizing a zero vector (which would be NaN). A wall is placed at -Z
# (the facing direction), so the centre ray hits it iff the fallback used the facing. Pins the
# zero-velocity path: heading = initial facing, NO NaN. Not a real game.

extends Node3D

var _mover: RigidBody3D = null

func build(_world_seed: int) -> void:
	PhysicsServer3D.set_active(true)
	for c in get_children():
		c.free()
	# Wall behind, in the -Z facing direction.
	var wall := StaticBody3D.new()
	wall.name = "wall"
	add_child(wall)
	var wcs := CollisionShape3D.new()
	var wbox := BoxShape3D.new()
	wbox.size = Vector3(40.0, 40.0, 2.0)
	wcs.shape = wbox
	wall.add_child(wcs)
	wall.position = Vector3(0.0, 0.0, -30.0)
	# Stationary locked-rotation mover at identity (facing -Z), zero velocity.
	_mover = RigidBody3D.new()
	_mover.name = "mover"
	_mover.gravity_scale = 0.0
	_mover.lock_rotation = true
	_mover.can_sleep = false
	var mcs := CollisionShape3D.new()
	var mbox := BoxShape3D.new()
	mbox.size = Vector3(1.0, 1.0, 1.0)
	mcs.shape = mbox
	_mover.add_child(mcs)
	add_child(_mover)
	_mover.position = Vector3(0.0, 0.0, 0.0)
	_mover.linear_velocity = Vector3.ZERO

func act(_action: String) -> void:
	pass

func state() -> Dictionary:
	var p: Vector3 = _mover.position
	var v: Vector3 = _mover.linear_velocity
	return {"bodies": [
		{"name": "mover", "pos": [p.x, p.y, p.z], "vel": [v.x, v.y, v.z],
		 "angle": 0.0, "controlled": true, "static": false},
		{"name": "wall", "pos": [0.0, 0.0, -30.0], "vel": [0.0, 0.0, 0.0],
		 "angle": 0.0, "controlled": false, "static": true},
	]}

func checkpoints() -> Dictionary:
	return {"still": false}

func is_success() -> bool:
	return false

func is_failure() -> bool:
	return false

func actions() -> Array:
	return ["noop"]
