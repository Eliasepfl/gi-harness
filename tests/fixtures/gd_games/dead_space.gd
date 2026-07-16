# dead_space.gd -- a GameAPI fixture that CERTIFIES G0-G3 cleanly yet is DEAD SPACE:
# a tiny controlled body and its two goals huddle in one corner of a huge declared
# world, so the playfield dwarfs the region the action actually uses. The positive
# control for the WAVE-2 PROPORTION gate (notes/engines/DEMO_GAP_ANALYSIS.md §Gap 3):
# gameverify._dead_space_gate flags it `dead_space` with a repair directive, but the
# gate is ADVISORY, so the game still certifies (passed == True).
#
# It is mini_collect.gd's mechanic (a top-down zero-g collector; two gems needing a
# reversal so no single action wins) transplanted into a 2000x1400 world with the whole
# scene packed into a ~220x180 px sliver near (200, 200). The action-span / playfield
# ratio comes out well past the harness-side dead-space threshold while every G-gate
# stays green: one controlled dynamic body, >=2 bodies, in bounds, deterministic, all
# four moves live, milestones false at t=0, and a budgeted solver collects both gems in
# a non-trivial (>= 20 tick) replayable witness.
#
# Determinism: no wall clock, no global RNG -- the ONLY randomness is a tiny seed-stable
# jitter from an rng the game seeds ITSELF from build()'s seed (mirrors mini_collect.gd).

extends Node2D

const WORLD_SIZE := Vector2(2000.0, 1400.0)   # a deliberately HUGE arena...
const START := Vector2(200.0, 200.0)          # ...with the whole scene in one corner
const COLLECT_R := 40.0
const IMPULSE := 150.0
const DAMP := 3.0
const MAX_V := 130.0        # px/s cap -> ~13 px/tick, bounded predictable travel

var _rng := RandomNumberGenerator.new()   # seeded from build()'s seed; host hands none
var _mote: RigidBody2D = null
var _gems := []            # [{name, node, pos, collected}]


func build(world_seed: int) -> void:
	_rng.seed = world_seed
	var jitter := _rng.randf_range(-3.0, 3.0)   # tiny, seed-stable

	_mote = RigidBody2D.new()
	_mote.gravity_scale = 0.0                                 # top-down: nothing to fall to
	_mote.linear_damp_mode = RigidBody2D.DAMP_MODE_REPLACE
	_mote.linear_damp = DAMP                                  # coast-to-stop drift
	_mote.lock_rotation = true
	_mote.can_sleep = false
	_mote.position = START + Vector2(0.0, jitter)
	var col := CollisionShape2D.new()
	var circ := CircleShape2D.new()
	circ.radius = 16.0
	col.shape = circ
	_mote.add_child(col)
	add_child(_mote)

	_gems = []
	_add_gem("gem_down", START + Vector2(0.0, 180.0))         # straight down
	_add_gem("gem_right", START + Vector2(220.0, 0.0))        # straight right -> a reversal


func _add_gem(gem_name: String, pos: Vector2) -> void:
	var marker := Node2D.new()
	marker.name = gem_name
	marker.position = pos
	add_child(marker)
	_gems.append({"name": gem_name, "node": marker, "pos": pos, "collected": false})


func _physics_process(_delta: float) -> void:
	if _mote == null:
		return
	if _mote.linear_velocity.length() > MAX_V:
		_mote.linear_velocity = _mote.linear_velocity.limit_length(MAX_V)
	for g in _gems:
		if not g.collected and _mote.position.distance_to(g.pos) < COLLECT_R:
			g.collected = true


func act(action: String) -> void:
	if _mote == null:
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
	_mote.apply_central_impulse(v)


func _count() -> int:
	var n := 0
	for g in _gems:
		if g.collected:
			n += 1
	return n


func state() -> Dictionary:
	var bodies := []
	bodies.append({
		"name": "mote",
		"pos": [_mote.position.x, _mote.position.y],
		"vel": [_mote.linear_velocity.x, _mote.linear_velocity.y],
		"angle": _mote.rotation,
		"controlled": true,
		"static": false,
	})
	for g in _gems:
		bodies.append({
			"name": g.name,
			"pos": [g.pos.x, g.pos.y],
			"vel": [0.0, 0.0],
			"angle": 0.0,
			"controlled": false,
			"static": true,
		})
	return {
		"world_size": [WORLD_SIZE.x, WORLD_SIZE.y],
		"bodies": bodies,
		"flags": {"got_first": _count() >= 1, "got_both": _count() >= 2},
	}


func checkpoints() -> Dictionary:
	return {"got_first": _count() >= 1, "got_both": _count() >= 2}


func is_success() -> bool:
	return _count() >= 2


func is_failure() -> bool:
	return false


func actions() -> Array:
	return ["up", "down", "left", "right"]
