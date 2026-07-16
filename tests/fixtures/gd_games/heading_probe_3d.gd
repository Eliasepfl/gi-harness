# heading_probe_3d.gd -- HEADING-FRAME raycast fixture (GDScript lane, serve/RL only).
#
# A lock_rotation RigidBody3D at IDENTITY (body-local forward = -Z) that translates +X toward
# a wall placed ahead in +X. Because rotation is locked, the body never turns to face its
# travel, so a BODY-LOCAL retina's centre ray stares down -Z (nothing there). Only the
# HEADING frame (forward = velocity = +X) points the centre ray at the wall. Used to pin that
# the "auto" ray frame switches to heading for a locked body. Not a real game (never wins).

extends Node3D

var _mover: RigidBody3D = null

func build(_world_seed: int) -> void:
	PhysicsServer3D.set_active(true)
	for c in get_children():
		c.free()
	# Wall ahead in +X (deep in y,z so the forward heading ray reliably hits it).
	var wall := StaticBody3D.new()
	wall.name = "wall"
	add_child(wall)
	var wcs := CollisionShape3D.new()
	var wbox := BoxShape3D.new()
	wbox.size = Vector3(2.0, 40.0, 40.0)
	wcs.shape = wbox
	wall.add_child(wcs)
	wall.position = Vector3(30.0, 0.0, 0.0)
	# Locked-rotation mover: facing -Z (identity), moving +X, no gravity/damping.
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
	_mover.linear_velocity = Vector3(20.0, 0.0, 0.0)

func act(_action: String) -> void:
	pass

func state() -> Dictionary:
	var p: Vector3 = _mover.position
	var v: Vector3 = _mover.linear_velocity
	return {"bodies": [
		{"name": "mover", "pos": [p.x, p.y, p.z], "vel": [v.x, v.y, v.z],
		 "angle": 0.0, "controlled": true, "static": false},
		{"name": "wall", "pos": [30.0, 0.0, 0.0], "vel": [0.0, 0.0, 0.0],
		 "angle": 0.0, "controlled": false, "static": true},
	]}

func checkpoints() -> Dictionary:
	return {"reached": _mover.position.x >= 25.0}

func is_success() -> bool:
	return false

func is_failure() -> bool:
	return false

func actions() -> Array:
	return ["noop"]
