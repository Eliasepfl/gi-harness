# losable.gd -- a GameAPI fixture with REAL STAKES: it certifies G0-G3 AND has a
# REACHABLE failure, so the WAVE 1 failure-witness (PRESSURE) gate PASSES it
# (records outcome `has_pressure` with a failure witness). The positive control for
# no_pressure.gd (notes/engines/DEMO_GAP_ANALYSIS.md §Gap 1).
#
# A top-down crossing, adapted from softlock_pit.gd -- but where the pit there PINS
# the body into a frozen softlock, here the central band is a LETHAL HAZARD: enter it
# and the boat sinks (`_sunk`), is_failure() goes true, and the episode ends in a clean
# LOSS. The goal sits high on the far side, reachable ONLY by going OVER THE TOP (up,
# across, down); the straight route -- and any random flailing -- drives into the hazard
# and loses. So there are stakes: doing the wrong thing gets you killed.
#
# Why it PASSES the funnel AND the pressure gate:
#   * G0    -- banned-API-clean, standalone-parseable, one controlled dynamic body,
#              >=2 bodies, in bounds.
#   * G0.5  -- the hazard is a LOGIC region with no collision footprint -> open space to
#              the flood-fill; the goal stays geometrically reachable.
#   * G1    -- deterministic (self-seeded jitter only); all four moves live at the start
#              pose (outside the hazard); no success under noop.
#   * G2    -- milestones + is_failure all false at t=0 (the boat starts outside the hazard).
#   * G3    -- a budgeted solver reaches the goal via the up-and-over detour.
#   * G3.6  -- the PRESSURE gate: a broad adversarial sweep drives into the hazard, so
#              is_failure() FIRES from a reachable state -> outcome `has_pressure`, the
#              gate passes with a failure witness (no warning, no directive).
#
# Determinism: no wall clock, no global RNG -- the ONLY randomness is a tiny seed-stable
# jitter from an rng the game seeds ITSELF from build()'s seed (mirrors softlock_pit.gd).

extends Node2D

const VISIT_R := 60.0
const IMPULSE := 150.0
const DAMP := 3.0
const MAX_V := 130.0        # px/s cap -> ~13 px/tick, bounded predictable travel

# The lethal hazard band (a LOGIC region, no collision body). Enter it -> _sunk.
# It reaches from y=240 down toward the floor, so the safe way is over the top.
const HAZ_MIN := Vector2(280.0, 240.0)
const HAZ_MAX := Vector2(480.0, 560.0)

# The goal sits HIGH on the far side so the top-lane sweep reaches it IN PASSING,
# keeping G3 honestly solvable while the straight bottom route still dies in the hazard.
const GOAL_POS := Vector2(620.0, 260.0)

var _rng := RandomNumberGenerator.new()   # seeded from build()'s seed; host hands none
var _boat: RigidBody2D = null
var _collected := false
var _sunk := false


func build(world_seed: int) -> void:
	_rng.seed = world_seed
	var jitter := _rng.randf_range(-2.0, 2.0)   # tiny, seed-stable; far from the hazard

	_boat = RigidBody2D.new()
	_boat.gravity_scale = 0.0                                # top-down: nothing to fall to
	_boat.linear_damp_mode = RigidBody2D.DAMP_MODE_REPLACE
	_boat.linear_damp = DAMP                                 # coast-to-stop drift
	_boat.lock_rotation = true
	_boat.can_sleep = false
	_boat.position = Vector2(120.0, 300.0 + jitter)          # start LEFT, outside the hazard
	var col := CollisionShape2D.new()
	var circ := CircleShape2D.new()
	circ.radius = 16.0
	col.shape = circ
	_boat.add_child(col)
	add_child(_boat)

	_collected = false
	_sunk = false
	var goal := Node2D.new()                                 # bare marker -> target, not a wall
	goal.name = "goal"
	goal.position = GOAL_POS
	add_child(goal)


func _in_hazard(p: Vector2) -> bool:
	return p.x >= HAZ_MIN.x and p.x <= HAZ_MAX.x and p.y >= HAZ_MIN.y and p.y <= HAZ_MAX.y


func _physics_process(_delta: float) -> void:
	if _boat == null:
		return
	# STAKES: entering the hazard sinks the boat -- a latched, terminal LOSS (is_failure
	# below reads it). Once sunk, freeze the pose so a dead boat cannot drift to the goal
	# (is_failure is checked before is_success, so the loss stands cleanly).
	if _sunk:
		_boat.linear_velocity = Vector2.ZERO
		return
	if _in_hazard(_boat.position):
		_sunk = true
		_boat.linear_velocity = Vector2.ZERO
		return
	if _boat.linear_velocity.length() > MAX_V:
		_boat.linear_velocity = _boat.linear_velocity.limit_length(MAX_V)
	if not _collected and _boat.position.distance_to(GOAL_POS) < VISIT_R:
		_collected = true


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
			"pos": [GOAL_POS.x, GOAL_POS.y],
			"vel": [0.0, 0.0],
			"angle": 0.0,
			"controlled": false,
			"static": true,
		}],
		"flags": {"got_goal": _collected, "sunk": _sunk},
	}


func checkpoints() -> Dictionary:
	# Milestones shape the SAFE up-and-over route only (the hazard route latches nothing),
	# so neither the tree solver nor an RL checkpoint reward is drawn into the hazard.
	var p := _boat
	return {
		"up_lane": p != null and p.position.y < 230.0,
		"over_hazard": p != null and p.position.y < 230.0 and p.position.x > 380.0,
		"past_hazard": p != null and p.position.x > 500.0,
		"got_goal": _collected,
	}


func is_success() -> bool:
	return _collected


func is_failure() -> bool:
	# REAL STAKES: the boat sinks if it enters the hazard band. Reachable from the
	# start pose by driving straight in -- this is the losable condition the game needs.
	return _sunk


func actions() -> Array:
	return ["up", "down", "left", "right"]
