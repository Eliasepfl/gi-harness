# walled_goal.gd -- a DELIBERATELY UNSOLVABLE GameAPI fixture (GDScript lane).
#
# The goal gem is SEALED inside a box of four StaticBody2D walls; the controlled player
# spawns OUTSIDE the box and can never reach the gem, so success is geometrically
# unreachable. The game is otherwise well-formed -- it passes G0 (one controlled body,
# >=2 bodies, in bounds), G1 (deterministic, all four moves live, no noop success), and
# G2 (well-formed milestones false at t=0) -- so it is the G0.5 geometric reachability
# pre-filter (Elias directive 1) that must reject it FAST with a 'walled off / unreachable'
# hint, BEFORE the expensive G3 tree solve wastes its budget proving the obvious.
#
# The walls report their footprints (half_extents) in state() so the python flood-fill
# has real occupancy to flood over; the gem is a bare marker (no footprint -> a target
# region, never a wall). Modelled on mini_collect.gd otherwise.

extends Node2D

const COLLECT_R := 40.0
const IMPULSE := 150.0
const DAMP := 3.0
const MAX_V := 130.0

var _rng := RandomNumberGenerator.new()
var _player: RigidBody2D = null
var _gem_pos := Vector2(400.0, 300.0)
var _collected := false
var _walls := []            # [{name, pos: Vector2, half: Vector2}]


func build(world_seed: int) -> void:
	_rng.seed = world_seed
	var jitter := _rng.randf_range(-3.0, 3.0)

	_player = RigidBody2D.new()
	_player.gravity_scale = 0.0
	_player.linear_damp_mode = RigidBody2D.DAMP_MODE_REPLACE
	_player.linear_damp = DAMP
	_player.lock_rotation = true
	_player.can_sleep = false
	_player.position = Vector2(100.0, 300.0 + jitter)
	var col := CollisionShape2D.new()
	var circ := CircleShape2D.new()
	circ.radius = 16.0
	col.shape = circ
	_player.add_child(col)
	add_child(_player)

	# The goal gem -- a bare marker, SEALED inside the wall box.
	_gem_pos = Vector2(400.0, 300.0)
	_collected = false
	var marker := Node2D.new()
	marker.name = "gem"
	marker.position = _gem_pos
	add_child(marker)

	# Four real StaticBody2D walls forming a sealed box around the gem.
	_walls = []
	_add_wall("wall_top", Vector2(400.0, 240.0), Vector2(68.0, 8.0))
	_add_wall("wall_bottom", Vector2(400.0, 360.0), Vector2(68.0, 8.0))
	_add_wall("wall_left", Vector2(340.0, 300.0), Vector2(8.0, 68.0))
	_add_wall("wall_right", Vector2(460.0, 300.0), Vector2(8.0, 68.0))


func _add_wall(wall_name: String, pos: Vector2, half: Vector2) -> void:
	var body := StaticBody2D.new()
	body.name = wall_name
	body.position = pos
	var col := CollisionShape2D.new()
	var rect := RectangleShape2D.new()
	rect.size = half * 2.0
	col.shape = rect
	body.add_child(col)
	add_child(body)
	_walls.append({"name": wall_name, "pos": pos, "half": half})


func _physics_process(_delta: float) -> void:
	if _player == null:
		return
	if _player.linear_velocity.length() > MAX_V:
		_player.linear_velocity = _player.linear_velocity.limit_length(MAX_V)
	if not _collected and _player.position.distance_to(_gem_pos) < COLLECT_R:
		_collected = true


func act(action: String) -> void:
	if _player == null:
		return
	var v := Vector2.ZERO
	match action:
		"up":
			v = Vector2(0.0, -IMPULSE)
		"down":
			v = Vector2(0.0, IMPULSE)
		"left":
			v = Vector2(-IMPULSE, 0.0)
		"right":
			v = Vector2(IMPULSE, 0.0)
	_player.apply_central_impulse(v)


func state() -> Dictionary:
	var bodies := [{
		"name": "player",
		"pos": [_player.position.x, _player.position.y],
		"vel": [_player.linear_velocity.x, _player.linear_velocity.y],
		"angle": _player.rotation,
		"controlled": true,
		"static": false,
	}, {
		"name": "gem",
		"pos": [_gem_pos.x, _gem_pos.y],
		"vel": [0.0, 0.0],
		"angle": 0.0,
		"controlled": false,
		"static": true,
	}]
	for w in _walls:
		var p: Vector2 = w.pos
		var h: Vector2 = w.half
		bodies.append({
			"name": w.name,
			"pos": [p.x, p.y],
			"vel": [0.0, 0.0],
			"angle": 0.0,
			"controlled": false,
			"static": true,
			"half_extents": [h.x, h.y],
		})
	return {"bodies": bodies}


func checkpoints() -> Dictionary:
	var near := _player != null and _player.position.distance_to(_gem_pos) < 120.0
	return {"near_gem": near, "got_gem": _collected}


func is_success() -> bool:
	return _collected


func is_failure() -> bool:
	return false


func actions() -> Array:
	return ["up", "down", "left", "right"]
