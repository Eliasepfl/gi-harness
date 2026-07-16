# softlock_wiggle.gd -- a GameAPI fixture that is CERTIFIABLE G0-G3 yet hides a reachable
# WIGGLE-POCKET SOFTLOCK: Elias's exact fear made concrete (2026-07-15). It is the
# motion-blind sibling of softlock_pit.gd. Where the pit PINS the body (its state FREEZES
# -> the frozen DETECT trigger catches it), this pocket CONFINES the body but keeps it
# MOVING: once inside, the body is trapped forever (position clamped + velocity reflected
# at the box walls, so the goal outside is unreachable -- genuinely unwinnable, TREE-
# REFUTABLE) yet it keeps WANDERING aperiodically over MANY distinct positions on a
# golden-angle "stir". Its fingerprint (which includes velocity) therefore CHANGES every
# tick -- never freezing, never closing a short cycle -- so it defeats BOTH motion-based
# DETECT triggers (harness.rl.adversary.detect_softlock_window: frozen AND cycle). Only
# the motion-INVARIANT VALUE-DEATH trigger (detect_value_death: a trained critic's V(s)
# stays COLLAPSED in the trap no matter how the body jiggles) catches it.
#
# A top-down collect game, modelled on softlock_pit.gd. One controlled RigidBody2D player
# coasts under zero gravity + linear damping toward a single goal gem on the far side. A
# central POCKET (a LOGIC trap, NOT a wall) spans the middle: the goal is reachable ONLY
# by going OVER THE TOP (up, across, down). The straight-ahead route -- and any anti-
# optimal steering toward it -- walks the body INTO the pocket, where it is CONFINED and
# STIRRED forever: is_failure stays false, is_success can never fire, and the state keeps
# changing. That is the wiggle-softlock the value-death attacker must find and CERTIFY.
#
# Why it passes the funnel (identical to softlock_pit.gd):
#   * G0    -- banned-API-clean, standalone-parseable, one controlled dynamic body,
#              >=2 bodies, in bounds.
#   * G0.5  -- the pocket is a LOGIC trap with no collision footprint (no half_extents), so
#              the geometric flood-fill sees open space and the goal stays reachable ->
#              the pre-filter (correctly) does NOT reject it; solvability is the tree
#              solver's job, and the softlock is invisible to pure geometry.
#   * G1    -- deterministic (self-seeded jitter + a pure frame-counter stir, no RNG); all
#              four moves are live at the start pose (outside the pocket); no success under noop.
#   * G2    -- every milestone is false at t=0 and a pure predicate read.
#   * G3    -- a budgeted solver reaches the goal via the up-and-over detour (the pocket
#              latches NO milestone, so the milestone-guided solver skirts it -- as in the pit).
#
# Determinism: no wall clock, no global RNG -- the ONLY randomness is a tiny seed-stable
# jitter from an rng the game seeds ITSELF from build()'s seed (mirrors softlock_pit.gd);
# the in-pocket stir is a PURE function of a monotonic physics-frame counter.

extends Node2D

const COLLECT_R := 60.0
const IMPULSE := 150.0
const DAMP := 3.0
const MAX_V := 130.0        # px/s cap -> ~13 px/tick, bounded predictable travel

# The pocket box (a LOGIC trap, no collision body). Enter it -> CONFINED + stirred FOREVER
# (a latched flag, so a body that drifted a hair past a wall stays trapped -- airtight).
# It sits LOW (y in [360,560]) -- BELOW the y~300 straight-across lane, so the safe route
# skirts OVER it AND (deliberately) the pocket coordinates lie clear of where a healthy
# top-down game's body travels at that height (the negative-control critic never collapses).
const POCKET_MIN := Vector2(280.0, 360.0)
const POCKET_MAX := Vector2(480.0, 560.0)

# The STIR: once trapped the body is kept MOVING as a CONFINED BILLIARD, steered by
# VELOCITY only (never by writing position -- a RigidBody2D ignores position writes at
# rest and fights them in flight, so both the pin and the teleport patterns fail here).
# Each frame we (a) keep the speed at STIR_SPEED, (b) advance the direction by a small
# GOLDEN-ANGLE-derived rotation so the billiard DRIFTS and never settles into a periodic
# orbit, and (c) REFLECT the velocity inward whenever the body comes within WALL_MARGIN of
# a wall while heading toward it -- so it turns BEFORE it can exit (STIR_SPEED*dt << the
# margin), staying airtight inside the box. The body thus visits MANY distinct positions
# (never freezing) with no short recurrence (never cycling), while the goal outside stays
# unreachable. That is what makes this trap MOVE where softlock_pit.gd's PIN freezes.
const STIR_SPEED := 90.0    # px/s billiard speed (below MAX_V) -> always moving
const STIR_ROT := 0.18      # per-frame velocity rotation (rad) -> aperiodic drift
const WALL_MARGIN := 26.0   # reflect this far from a wall (>> STIR_SPEED*dt) -> airtight
const GOLDEN := 2.399963    # golden angle (rad) -> equidistributed seed direction

# The goal sits HIGH on the far side so the top-lane sweep collects it IN PASSING
# (y=260 is within COLLECT_R of the y<230 cruise line) -- the win needs no precise final
# descent, keeping G3 honestly solvable while the straight low route still traps.
const GOAL_POS := Vector2(620.0, 260.0)
const START_POS := Vector2(120.0, 460.0)       # start LEFT, at the pocket's HEIGHT but clear of its x

var _rng := RandomNumberGenerator.new()   # seeded from build()'s seed; host hands none
var _player: RigidBody2D = null
var _collected := false
var _trapped := false                     # latched once the body enters the pocket
var _stir := 0                            # monotonic physics-frame counter (stir phase)


func build(world_seed: int) -> void:
	_rng.seed = world_seed
	var jitter := _rng.randf_range(-2.0, 2.0)   # tiny, seed-stable; far from the pocket

	_player = RigidBody2D.new()
	_player.gravity_scale = 0.0                              # top-down: nothing to fall to
	_player.linear_damp_mode = RigidBody2D.DAMP_MODE_REPLACE
	_player.linear_damp = DAMP                               # coast-to-stop friction analog
	_player.lock_rotation = true
	_player.can_sleep = false
	_player.position = Vector2(START_POS.x, START_POS.y + jitter)  # start LEFT, clear of the pocket x
	var col := CollisionShape2D.new()
	var circ := CircleShape2D.new()
	circ.radius = 16.0
	col.shape = circ
	_player.add_child(col)
	add_child(_player)

	_collected = false
	_trapped = false
	_stir = 0
	var goal := Node2D.new()                                 # bare marker -> target, not a wall
	goal.name = "goal"
	goal.position = GOAL_POS
	add_child(goal)


func _in_pocket(p: Vector2) -> bool:
	return p.x >= POCKET_MIN.x and p.x <= POCKET_MAX.x and p.y >= POCKET_MIN.y and p.y <= POCKET_MAX.y


func _physics_process(_delta: float) -> void:
	if _player == null:
		return
	_stir += 1
	# Latch the trap on first entry -- from here the body can NEVER leave (airtight even if
	# a frame's integration drifts it a hair past a wall).
	if not _trapped and _in_pocket(_player.position):
		_trapped = true
	if _trapped:
		# CONFINED WIGGLE (not a PIN): keep the body MOVING as a billiard steered by
		# VELOCITY only (physics integrates the position -- never write it), so the
		# fingerprint keeps changing (delta > EFFICACY_EPS) across many distinct positions
		# with no short recurrence -- defeating frozen AND cycle. It can NEVER leave the box
		# (reflected inward at every wall) so the goal outside is unreachable -> genuinely
		# unwinnable, TREE-REFUTABLE. Actions still arrive (act() applies impulses) but are
		# overwritten here, so no continuation can escape -- the softlock is absolute.
		var v := _player.linear_velocity
		if v.length() < 1.0:
			v = Vector2(STIR_SPEED, 0.0).rotated(float(_stir) * GOLDEN)   # seed a direction on entry
		v = v.rotated(STIR_ROT)                                            # slow drift -> aperiodic
		var p := _player.position
		if p.x < POCKET_MIN.x + WALL_MARGIN and v.x < 0.0:
			v.x = -v.x
		elif p.x > POCKET_MAX.x - WALL_MARGIN and v.x > 0.0:
			v.x = -v.x
		if p.y < POCKET_MIN.y + WALL_MARGIN and v.y < 0.0:
			v.y = -v.y
		elif p.y > POCKET_MAX.y - WALL_MARGIN and v.y > 0.0:
			v.y = -v.y
		_player.linear_velocity = v.normalized() * STIR_SPEED             # constant speed -> never rests
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
		"flags": {"got_goal": _collected, "trapped": _trapped},
	}


func checkpoints() -> Dictionary:
	# Milestones shape the SAFE up-and-over route ONLY -- the pocket route latches NOTHING.
	# That matters twice: (a) neither the milestone-guided tree solver nor the RL
	# checkpoint reward is drawn into the trap, and (b) the confined pocket stays
	# checkpoint-free, so the attacker's DETECT no-new-checkpoint guard reads clean.
	var p := _player
	return {
		"up_lane": p != null and p.position.y < 230.0,
		"over_pocket": p != null and p.position.y < 230.0 and p.position.x > 380.0,
		"past_pocket": p != null and p.position.x > 500.0,
		"got_goal": _collected,
	}


func is_success() -> bool:
	return _collected


func is_failure() -> bool:
	return false


func actions() -> Array:
	return ["up", "down", "left", "right"]
