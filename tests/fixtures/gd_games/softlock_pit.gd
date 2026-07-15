# softlock_pit.gd -- a GameAPI fixture that is CERTIFIABLE G0-G3 yet hides a reachable
# SOFTLOCK POCKET (GDScript lane). The target of the inverse-value G4 attacker
# (harness.rl.adversary + harness.verify.g4 inverse-value tier).
#
# A top-down collect game, modelled on mini_collect.gd. One controlled RigidBody2D
# player coasts under zero gravity + linear damping toward a single goal gem on the far
# side of the arena. A deep central PIT (a logic trap, NOT a wall) spans the middle
# from y=240 down to the floor: the goal is reachable ONLY by going OVER THE TOP
# (up, across, down). The straight-ahead route -- and any anti-optimal steering toward
# it -- walks the body INTO the pit, where _physics_process PINS its pose forever:
# the state FREEZES while the game keeps accepting actions, is_failure stays false, and
# is_success can never fire. That is the softlock the attacker must find and CERTIFY.
#
# Why it passes the funnel:
#   * G0    -- banned-API-clean, standalone-parseable, one controlled dynamic body,
#              >=2 bodies, in bounds.
#   * G0.5  -- the pit is a LOGIC trap with no collision footprint (no half_extents), so
#              the geometric flood-fill sees open space and the goal stays reachable ->
#              the pre-filter (correctly) does NOT reject it; solvability is the tree
#              solver's job, and the softlock is invisible to pure geometry.
#   * G1    -- deterministic (self-seeded jitter only); all four moves are live at the
#              start pose (outside the pit); no success under noop.
#   * G2    -- every milestone is false at t=0 and a pure predicate read.
#   * G3    -- a budgeted solver reaches the goal via the up-and-over detour.
#
# Determinism: no wall clock, no global RNG -- the ONLY randomness is a tiny seed-stable
# jitter from an rng the game seeds ITSELF from build()'s seed (mirrors mini_collect.gd).

extends Node2D

const COLLECT_R := 60.0
const IMPULSE := 150.0
const DAMP := 3.0
const MAX_V := 130.0        # px/s cap -> ~13 px/tick, bounded predictable travel

# The pit box (a LOGIC trap, no collision body). Enter it -> pinned to PIT_ANCHOR.
# It reaches from y=240 down toward the floor, so the way to the goal is over the top.
const PIT_MIN := Vector2(280.0, 240.0)
const PIT_MAX := Vector2(480.0, 560.0)
const PIT_ANCHOR := Vector2(380.0, 400.0)   # inside the box -> the freeze stays latched

# The goal sits HIGH on the far side so the top-lane sweep collects it IN PASSING
# (y=260 is within COLLECT_R of the y<230 cruise line) -- the win needs no precise
# final descent, keeping G3 honestly solvable while the straight bottom route still
# freezes in the pit.
const GOAL_POS := Vector2(620.0, 260.0)

var _rng := RandomNumberGenerator.new()   # seeded from build()'s seed; host hands none
var _player: RigidBody2D = null
var _collected := false


func build(world_seed: int) -> void:
	_rng.seed = world_seed
	var jitter := _rng.randf_range(-2.0, 2.0)   # tiny, seed-stable; far from the pit

	_player = RigidBody2D.new()
	_player.gravity_scale = 0.0                              # top-down: nothing to fall to
	_player.linear_damp_mode = RigidBody2D.DAMP_MODE_REPLACE
	_player.linear_damp = DAMP                               # coast-to-stop friction analog
	_player.lock_rotation = true
	_player.can_sleep = false
	_player.position = Vector2(120.0, 300.0 + jitter)        # start LEFT, outside the pit
	var col := CollisionShape2D.new()
	var circ := CircleShape2D.new()
	circ.radius = 16.0
	col.shape = circ
	_player.add_child(col)
	add_child(_player)

	_collected = false
	var goal := Node2D.new()                                 # bare marker -> target, not a wall
	goal.name = "goal"
	goal.position = GOAL_POS
	add_child(goal)


func _in_pit(p: Vector2) -> bool:
	return p.x >= PIT_MIN.x and p.x <= PIT_MAX.x and p.y >= PIT_MIN.y and p.y <= PIT_MAX.y


func _physics_process(_delta: float) -> void:
	if _player == null:
		return
	# SOFTLOCK: once the body is inside the trap box, PIN its pose every physics frame.
	# Actions keep arriving (act() still applies impulses) but they are overwritten here,
	# so the fingerprinted state FREEZES -- no failure, no success, no escape.
	if _in_pit(_player.position):
		_player.position = PIT_ANCHOR
		_player.linear_velocity = Vector2.ZERO
		return
	# Outside the pit: bounded speed + latch collection (a pure read elsewhere).
	if _player.linear_velocity.length() > MAX_V:
		_player.linear_velocity = _player.linear_velocity.limit_length(MAX_V)
	if not _collected and _player.position.distance_to(GOAL_POS) < COLLECT_R:
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
	return {
		"bodies": [{
			"name": "player",
			"pos": [_player.position.x, _player.position.y],
			"vel": [_player.linear_velocity.x, _player.linear_velocity.y],
			"angle": _player.rotation,
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
		"flags": {"got_goal": _collected},
	}


func checkpoints() -> Dictionary:
	# Milestones shape the SAFE up-and-over route ONLY -- the pit route latches NOTHING.
	# That matters twice: (a) neither the milestone-guided tree solver nor the RL
	# checkpoint reward is drawn into the trap (a checkpoint on the pit route makes the
	# dive a local optimum and stalls both), and (b) the frozen pocket stays checkpoint-
	# free, so the attacker's DETECT no-new-checkpoint guard reads clean.
	var p := _player
	return {
		"up_lane": p != null and p.position.y < 230.0,
		"over_pit": p != null and p.position.y < 230.0 and p.position.x > 380.0,
		"past_pit": p != null and p.position.x > 500.0,
		"got_goal": _collected,
	}


func is_success() -> bool:
	return _collected


func is_failure() -> bool:
	return false


func actions() -> Array:
	return ["up", "down", "left", "right"]
