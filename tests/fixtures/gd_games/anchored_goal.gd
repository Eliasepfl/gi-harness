# anchored_goal.gd -- the POSITIVE control for ghost_goal.gd: the SAME crossing game, but
# the goal is a REAL node with a collision shape (an Area2D + CircleShape2D) built in
# build() and REPORTED in state()'s bodies. The milestone/win latch off the boat's arrival
# AT that node's position, so the MATERIAL-REALITY gate reads it as anchored (a real
# reported body sits at the flip) -> no warning, no directive, `anchored: True`.
#
# Certifies through the SAME funnel as losable.gd / ghost_goal.gd (up-and-over route past a
# lethal hazard; no single action wins). Determinism: self-seeded jitter only.

extends Node2D

const VISIT_R := 60.0
const GOAL_R := 40.0
const IMPULSE := 150.0
const DAMP := 3.0
const MAX_V := 130.0

const HAZ_MIN := Vector2(280.0, 240.0)
const HAZ_MAX := Vector2(480.0, 560.0)
const GOAL_POS := Vector2(620.0, 260.0)

var _rng := RandomNumberGenerator.new()
var _boat: RigidBody2D = null
var _goal: Area2D = null
var _reached := false
var _sunk := false


func build(world_seed: int) -> void:
	_rng.seed = world_seed
	var jitter := _rng.randf_range(-2.0, 2.0)

	_boat = RigidBody2D.new()
	_boat.gravity_scale = 0.0
	_boat.linear_damp_mode = RigidBody2D.DAMP_MODE_REPLACE
	_boat.linear_damp = DAMP
	_boat.lock_rotation = true
	_boat.can_sleep = false
	_boat.position = Vector2(120.0, 300.0 + jitter)
	var col := CollisionShape2D.new()
	var circ := CircleShape2D.new()
	circ.radius = 16.0
	col.shape = circ
	_boat.add_child(col)
	add_child(_boat)

	# The goal is a REAL area with a collision shape, anchored at GOAL_POS and reported in
	# state() -- the thing the milestone latches off (its overlap/position).
	_goal = Area2D.new()
	_goal.position = GOAL_POS
	var gcol := CollisionShape2D.new()
	var gcirc := CircleShape2D.new()
	gcirc.radius = GOAL_R
	gcol.shape = gcirc
	_goal.add_child(gcol)
	add_child(_goal)

	_reached = false
	_sunk = false


func _in_hazard(p: Vector2) -> bool:
	return p.x >= HAZ_MIN.x and p.x <= HAZ_MAX.x and p.y >= HAZ_MIN.y and p.y <= HAZ_MAX.y


func _physics_process(_delta: float) -> void:
	if _boat == null:
		return
	if _sunk:
		_boat.linear_velocity = Vector2.ZERO
		return
	if _in_hazard(_boat.position):
		_sunk = true
		_boat.linear_velocity = Vector2.ZERO
		return
	if _boat.linear_velocity.length() > MAX_V:
		_boat.linear_velocity = _boat.linear_velocity.limit_length(MAX_V)
	# Latch off arrival AT the real goal node (its position) -- an anchored, physical event.
	if not _reached and _boat.position.distance_to(_goal.position) < VISIT_R:
		_reached = true


func act(action: String) -> void:
	if _boat == null:
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
	_boat.apply_central_impulse(v)


func state() -> Dictionary:
	return {
		"bodies": [{
			"name": "boat",
			"pos": [_boat.position.x, _boat.position.y],
			"vel": [_boat.linear_velocity.x, _boat.linear_velocity.y],
			"angle": _boat.rotation,
			"controlled": true,
			"static": false,
		}, {
			"name": "goal",
			"pos": [_goal.position.x, _goal.position.y],
			"vel": [0.0, 0.0],
			"angle": 0.0,
			"controlled": false,
			"static": false,
			"sensor": true,
			"radius": GOAL_R,
		}],
		"flags": {"reached": _reached, "sunk": _sunk},
	}


func checkpoints() -> Dictionary:
	return {"reached_goal": _reached}


func is_success() -> bool:
	return _reached


func is_failure() -> bool:
	return _sunk


func actions() -> Array:
	return ["up", "down", "left", "right"]
