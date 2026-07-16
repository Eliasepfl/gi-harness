# ghost_goal.gd -- a GameAPI fixture whose GOAL is a GHOST: the milestone and the win
# both latch on distance_to a HARDCODED coordinate (GOAL_POS) where NO node lives. It
# certifies G0-G3 (a real up-and-over route reaches the coordinate; a lethal hazard gives
# it stakes) yet the MATERIAL-REALITY gate (gameverify._anchoring_gate) flags it: at the
# flip the boat sits far from every REPORTED body (only the boat + a far buoy are on the
# wire), so the milestone reads as a bare coordinate threshold, not a physical event.
#
# The NEGATIVE control for anchored_goal.gd (same game, goal anchored to a real Area2D).
# Adapted from losable.gd (the PRESSURE positive control) so it certifies through the SAME
# funnel; the ONLY change is that the goal is arithmetic, not a node reported in state().
#
# Determinism: no wall clock, no global RNG -- the only randomness is a tiny seed-stable
# jitter from an rng the game seeds ITSELF from build()'s seed.

extends Node2D

const VISIT_R := 60.0
const IMPULSE := 150.0
const DAMP := 3.0
const MAX_V := 130.0

# The lethal hazard band (a LOGIC region, no collision body) -> STAKES (is_failure fires).
const HAZ_MIN := Vector2(280.0, 240.0)
const HAZ_MAX := Vector2(480.0, 560.0)

# The GHOST goal: a bare coordinate high on the far side. Reached only via the up-and-over
# detour (the straight route dies in the hazard), so no single action wins. NO node here.
const GOAL_POS := Vector2(620.0, 260.0)

var _rng := RandomNumberGenerator.new()
var _boat: RigidBody2D = null
var _buoy: StaticBody2D = null
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

	# A REAL anchored body, but FAR from GOAL_POS -- the only other body on the wire.
	_buoy = StaticBody2D.new()
	_buoy.position = Vector2(120.0, 520.0)
	var bcol := CollisionShape2D.new()
	var brect := RectangleShape2D.new()
	brect.size = Vector2(40.0, 40.0)
	bcol.shape = brect
	_buoy.add_child(bcol)
	add_child(_buoy)

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
	if not _reached and _boat.position.distance_to(GOAL_POS) < VISIT_R:
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
			"name": "buoy",
			"pos": [_buoy.position.x, _buoy.position.y],
			"vel": [0.0, 0.0],
			"angle": 0.0,
			"controlled": false,
			"static": true,
			"half_extents": [20.0, 20.0],
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
