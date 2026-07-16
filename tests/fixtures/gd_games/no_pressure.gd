# no_pressure.gd -- a GameAPI fixture that CERTIFIES G0-G3 yet has NO STAKES: the
# canonical UNFAILABLE game the WAVE 1 failure-witness (PRESSURE) gate must flag
# (notes/engines/DEMO_GAP_ANALYSIS.md §Gap 1 -- "idling is free").
#
# A leisurely top-down sightseeing drift: one controlled RigidBody2D boat visits two
# buoys in an open, hazard-free bay. There is NO current, NO clock, NO out-of-bounds
# that bites, NO capsize -- `is_failure()` is a hardcoded `return false`. Doing nothing
# forever is indistinguishable from playing: the episode simply never ends until the
# host truncates it. Modelled on mini_collect.gd so it is a MINIMAL delta from a
# certifying game -- the ONLY thing "wrong" with it is the absence of a way to lose.
#
# Why it PASSES the funnel (and why that is exactly the point):
#   * G0    -- banned-API-clean, standalone-parseable, one controlled dynamic body,
#              >=2 bodies, in bounds.
#   * G0.5  -- two bare markers, no occupancy -> nothing to wall off; reachable.
#   * G1    -- deterministic (self-seeded jitter only); all four moves live; no noop win.
#   * G2    -- both milestones false at t=0; is_failure false at t=0 (it is false ALWAYS).
#   * G3    -- a budgeted solver visits both buoys via a real, non-trivial, two-axis play
#              (buoy_b is off the buoy_a axis, so no single held action wins the tour).
#
# What the PRESSURE gate does with it: is_failure() is statically constant-false, so the
# gate records outcome `no_pressure` + a repair directive ("add a real failure condition
# so play has stakes") as a WARNING -- it does NOT block certification (the gate is
# advisory; the revise loop drives the fix). This is the fixture that proves the gate
# fires on an otherwise-clean game.
#
# Determinism: no wall clock, no global RNG -- the ONLY randomness is a tiny seed-stable
# jitter from an rng the game seeds ITSELF from build()'s seed (mirrors mini_collect.gd).

extends Node2D

const VISIT_R := 40.0
const IMPULSE := 150.0
const DAMP := 3.0
const MAX_V := 130.0        # px/s cap -> ~13 px/tick, bounded predictable travel so the
                            # two-buoy tour needs REAL play (non-trivial) yet stays solvable

var _rng := RandomNumberGenerator.new()   # seeded from build()'s seed; the host hands none
var _boat: RigidBody2D = null
var _buoys := []            # [{name, pos, visited}]


func build(world_seed: int) -> void:
	_rng.seed = world_seed
	var jitter := _rng.randf_range(-5.0, 5.0)   # tiny, seed-stable; never near a buoy

	_boat = RigidBody2D.new()
	_boat.gravity_scale = 0.0                                # top-down: calm water, no fall
	_boat.linear_damp_mode = RigidBody2D.DAMP_MODE_REPLACE
	_boat.linear_damp = DAMP                                 # coast-to-stop drift
	_boat.lock_rotation = true
	_boat.can_sleep = false
	_boat.position = Vector2(300.0, 300.0 + jitter)
	var col := CollisionShape2D.new()
	var circ := CircleShape2D.new()
	circ.radius = 16.0
	col.shape = circ
	_boat.add_child(col)
	add_child(_boat)

	_buoys = []
	_add_buoy("buoy_a", Vector2(300.0, 165.0))               # ~135 px "up" (-y)
	_add_buoy("buoy_b", Vector2(560.0, 340.0))               # ~260 px right, 40 down (off-axis)


func _add_buoy(buoy_name: String, pos: Vector2) -> void:
	var marker := Node2D.new()
	marker.name = buoy_name
	marker.position = pos
	add_child(marker)
	_buoys.append({"name": buoy_name, "pos": pos, "visited": false})


func _physics_process(_delta: float) -> void:
	# Visits are detected during stepping and LATCHED here (a sticky flag), so the
	# predicates below stay pure reads -- never mutate state in state()/checkpoints().
	if _boat == null:
		return
	if _boat.linear_velocity.length() > MAX_V:
		_boat.linear_velocity = _boat.linear_velocity.limit_length(MAX_V)
	for b in _buoys:
		if not b.visited and _boat.position.distance_to(b.pos) < VISIT_R:
			b.visited = true


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


func _count() -> int:
	var n := 0
	for b in _buoys:
		if b.visited:
			n += 1
	return n


func state() -> Dictionary:
	var bodies := []
	bodies.append({
		"name": "boat",
		"pos": [_boat.position.x, _boat.position.y],
		"vel": [_boat.linear_velocity.x, _boat.linear_velocity.y],
		"angle": _boat.rotation,
		"controlled": true,
		"static": false,
	})
	for b in _buoys:
		bodies.append({
			"name": b.name,
			"pos": [b.pos.x, b.pos.y],
			"vel": [0.0, 0.0],
			"angle": 0.0,
			"controlled": false,
			"static": true,
		})
	return {
		"bodies": bodies,
		"flags": {"seen_first": _count() >= 1, "seen_both": _count() >= 2},
	}


func checkpoints() -> Dictionary:
	return {"seen_first": _count() >= 1, "seen_both": _count() >= 2}


func is_success() -> bool:
	return _count() >= 2


func is_failure() -> bool:
	# NO STAKES: the boat can never capsize, run aground, or run out of time. This
	# hardcoded false is the whole point of the fixture -- the PRESSURE gate flags it.
	return false


func actions() -> Array:
	return ["up", "down", "left", "right"]
