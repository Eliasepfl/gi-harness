# heading_probe_2d.gd -- HEADING-FRAME raycast fixture, 2D twin of heading_probe_3d.
#
# A lock_rotation RigidBody2D FACING +Y (rotation = PI/2) that translates +X toward a wall in
# +X. Body-local forward is +Y (its fixed facing), so a body-local centre ray points straight
# up (misses the +X wall); only the HEADING frame (forward = velocity = +X) hits it. Not a game.

extends Node2D

var _mover: RigidBody2D = null

func build(_world_seed: int) -> void:
	for c in get_children():
		c.free()
	var wall := StaticBody2D.new()
	wall.name = "wall"
	add_child(wall)
	var wcs := CollisionShape2D.new()
	var wrect := RectangleShape2D.new()
	wrect.size = Vector2(20.0, 400.0)
	wcs.shape = wrect
	wall.add_child(wcs)
	wall.position = Vector2(300.0, 0.0)
	_mover = RigidBody2D.new()
	_mover.name = "mover"
	_mover.gravity_scale = 0.0
	_mover.lock_rotation = true
	_mover.can_sleep = false
	_mover.rotation = PI / 2.0           # facing +Y, but travels +X
	var mcs := CollisionShape2D.new()
	var mrect := RectangleShape2D.new()
	mrect.size = Vector2(10.0, 10.0)
	mcs.shape = mrect
	_mover.add_child(mcs)
	add_child(_mover)
	_mover.position = Vector2(0.0, 0.0)
	_mover.linear_velocity = Vector2(200.0, 0.0)

func act(_action: String) -> void:
	pass

func state() -> Dictionary:
	var p: Vector2 = _mover.position
	var v: Vector2 = _mover.linear_velocity
	return {"bodies": [
		{"name": "mover", "pos": [p.x, p.y], "vel": [v.x, v.y],
		 "angle": _mover.rotation, "controlled": true, "static": false},
		{"name": "wall", "pos": [300.0, 0.0], "vel": [0.0, 0.0],
		 "angle": 0.0, "controlled": false, "static": true},
	]}

func checkpoints() -> Dictionary:
	return {"reached": _mover.position.x >= 250.0}

func is_success() -> bool:
	return false

func is_failure() -> bool:
	return false

func actions() -> Array:
	return ["noop"]
