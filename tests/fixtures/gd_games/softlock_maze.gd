# softlock_maze.gd -- a CERTIFIABLE (G0-G3) GDScript game hiding a MULTI-STEP-ROUTE
# softlock: the trap pocket is reachable ONLY by navigating RIGHT off the start column
# and THEN turning DOWN into it. Because every action applies a PURE-AXIS impulse
# (up/down = vertical only, left/right = horizontal only), NO single spammed action --
# and no greedy argmin-from-the-start anti-policy that collapses to one direction -- can
# compose that turn: "down" from the start stays in the start column (x too small to be
# over the pocket), "right" from the start stays on the start row (y too high to fall in).
# Only a policy that first TRAVELS right (competent navigation) and THEN descends enters
# it. That is exactly the S1.5 POLICY-GUIDED DESCENT attacker's edge over the S1 greedy
# anti-policy (notes/adversarial/STALE_SEEKING_PLAN.md §3.1); softlock_pit.gd is the
# SINGLE-STEP counterpart the greedy tier already trips.
#
# Modelled on softlock_pit.gd: one controlled RigidBody2D under zero gravity + linear
# damping, a LOGIC-trap pocket with NO collision footprint (so the G0.5 geometric flood-
# fill sees open space and does not reject it -- solvability is the tree solver's job).
#
# Why it passes the funnel:
#   * G0    -- banned-API-clean, standalone-parseable, one controlled dynamic body,
#              >=2 bodies, in bounds.
#   * G0.5  -- the pocket is a LOGIC trap (no half_extents), so the flood-fill sees the
#              goal reachable; the softlock is invisible to pure geometry.
#   * G1    -- deterministic (self-seeded jitter only); all four moves are live at the
#              start pose (outside the pocket); no success under noop.
#   * G2    -- every milestone is false at t=0 and a pure predicate read.
#   * G3    -- a budgeted solver reaches the goal via the up-and-across route that skirts
#              the low pocket entirely.
#
# Determinism: no wall clock, no global RNG -- the ONLY randomness is a tiny seed-stable
# jitter from an rng the game seeds ITSELF from build()'s seed (mirrors softlock_pit.gd).

extends Node2D

const COLLECT_R := 60.0
const IMPULSE := 150.0
const DAMP := 3.0
const MAX_V := 130.0        # px/s cap -> ~13 px/tick, bounded predictable travel

# The pocket box (a LOGIC trap, no collision body). Enter it -> pinned to PIT_ANCHOR.
# It sits BELOW-and-RIGHT of the start: x in [160,460] needs travelling right first,
# y in [380,560] needs falling after -- a two-leg route no single axis-pure move makes.
const PIT_MIN := Vector2(160.0, 380.0)
const PIT_MAX := Vector2(460.0, 560.0)
const PIT_ANCHOR := Vector2(300.0, 470.0)   # inside the box -> the freeze stays latched

# Start LEFT (x=120 < pocket x-min 160, so "down" alone stays left of the pocket) at
# mid-height (y=300 < pocket y-min 380, so "right" alone stays above it). The goal sits
# UP-and-RIGHT so the safe route (up, then across the top) skirts the low pocket.
const START_POS := Vector2(120.0, 300.0)
const GOAL_POS := Vector2(360.0, 140.0)

var _rng := RandomNumberGenerator.new()   # seeded from build()'s seed; host hands none
var _player: RigidBody2D = null
var _collected := false


func build(world_seed: int) -> void:
	_rng.seed = world_seed
	var jitter := _rng.randf_range(-2.0, 2.0)   # tiny, seed-stable; far from the pocket

	_player = RigidBody2D.new()
	_player.gravity_scale = 0.0                              # top-down: nothing to fall to
	_player.linear_damp_mode = RigidBody2D.DAMP_MODE_REPLACE
	_player.linear_damp = DAMP                               # coast-to-stop friction analog
	_player.lock_rotation = true
	_player.can_sleep = false
	_player.position = Vector2(START_POS.x, START_POS.y + jitter)  # start LEFT, above pocket
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
	# Outside the pocket: bounded speed + latch collection (a pure read elsewhere).
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
	# Milestones shape the SAFE up-and-across route ONLY -- the pocket route latches
	# NOTHING (it is low, y>=380, while every milestone needs the HIGH lane y<220). That
	# keeps neither the milestone-guided tree solver nor a checkpoint reward drawn into
	# the trap, and leaves the frozen pocket checkpoint-free so the attacker's DETECT
	# no-new-checkpoint guard reads clean.
	var p := _player
	return {
		"up_lane": p != null and p.position.y < 220.0,
		"across": p != null and p.position.y < 220.0 and p.position.x > 250.0,
		"near_goal": p != null and p.position.distance_to(GOAL_POS) < 120.0,
		"got_goal": _collected,
	}


func is_success() -> bool:
	return _collected


func is_failure() -> bool:
	return false


func actions() -> Array:
	return ["up", "down", "left", "right"]
