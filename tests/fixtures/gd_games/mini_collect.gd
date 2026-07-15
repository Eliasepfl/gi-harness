# mini_collect.gd -- a duck-typed GameAPI-convention fixture game (GDScript lane).
#
# A PLAIN Node2D that IMPLEMENTS the method convention -- NO base class, no
# `class_name` dependency (godotworld/GAME_API.md): build/act/state/checkpoints/
# is_success/is_failure/actions. It compiles STANDALONE (godot --headless
# --check-only --script mini_collect.gd, no --path) and certifies G0-G3 through the
# serve contract (godotworld/serve_game.gd + harness/verify/gd_exec.py).
#
# A top-down 3-body collect game: one controlled RigidBody2D player + two gem
# markers. The player coasts under zero gravity + linear damping (the top-down
# friction analog) and collects a gem by moving within COLLECT_R of it. Success =
# both gems collected. Deterministic: no wall clock, no global RNG -- the ONLY
# randomness is a tiny seed-stable jitter drawn from an rng the game creates ITSELF
# from build()'s seed (the banned-API scan forbids the unseeded global randi/randf/
# randomize, so a self-seeded RandomNumberGenerator is the sanctioned path).
#
# Passes the GDScript lane funnel: G0 (banned-API scan + standalone parse gate +
# contract probe + one controlled dynamic body + >=2 bodies), G1 (deterministic
# two-run drift + each of the 4 moves is live + no success under noop), G2 (both
# milestones false at t=0, pure predicates), G3 (a budgeted solver collects both gems).

extends Node2D

const COLLECT_R := 40.0
const IMPULSE := 150.0
const DAMP := 3.0
const MAX_V := 130.0        # px/s speed cap -> bounded, predictable per-tick travel
                            # (~13 px/tick) so the goal needs REAL play (non-trivial)
                            # while staying solvable within the budget

var _rng := RandomNumberGenerator.new()   # seeded from build()'s seed -- the game owns
                                           # its randomness; the host hands it none
var _player: RigidBody2D = null
var _gems := []            # [{name, node, pos, collected}]


func build(world_seed: int) -> void:
	# Seed OUR OWN generator from the world seed -> the same seed yields the same
	# stream (two builds at the same seed match, which G1's drift gate checks).
	_rng.seed = world_seed
	# A tiny deterministic jitter (same for a given seed) -- exercises the rng without
	# perturbing solvability or determinism (both runs share seed 0).
	var jitter := _rng.randf_range(-5.0, 5.0)

	_player = RigidBody2D.new()
	_player.gravity_scale = 0.0                              # top-down: no floor to fall to
	_player.linear_damp_mode = RigidBody2D.DAMP_MODE_REPLACE
	_player.linear_damp = DAMP                               # coast-to-stop friction analog
	_player.lock_rotation = true
	_player.can_sleep = false
	_player.position = Vector2(300.0, 300.0 + jitter)
	var col := CollisionShape2D.new()
	var circ := CircleShape2D.new()
	circ.radius = 16.0
	col.shape = circ
	_player.add_child(col)
	add_child(_player)

	_gems = []
	_add_gem("gem_a", Vector2(300.0, 165.0))                 # ~135 px "up" (-y)
	_add_gem("gem_b", Vector2(560.0, 340.0))                 # ~260 px right, 40 down


func _add_gem(gem_name: String, pos: Vector2) -> void:
	var marker := Node2D.new()
	marker.name = gem_name
	marker.position = pos
	add_child(marker)
	_gems.append({"name": gem_name, "node": marker, "pos": pos, "collected": false})


func _physics_process(_delta: float) -> void:
	# Collection is detected during physics stepping and LATCHED here (a sticky flag),
	# so the predicates below stay pure reads -- never mutate state in state()/
	# checkpoints()/is_success().
	if _player == null:
		return
	# Cap speed so per-tick travel is bounded and the tick count is predictable
	# (the goal stays non-trivial without the player rocketing across the arena).
	if _player.linear_velocity.length() > MAX_V:
		_player.linear_velocity = _player.linear_velocity.limit_length(MAX_V)
	for g in _gems:
		if not g.collected and _player.position.distance_to(g.pos) < COLLECT_R:
			g.collected = true


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


func _count() -> int:
	var n := 0
	for g in _gems:
		if g.collected:
			n += 1
	return n


func state() -> Dictionary:
	var bodies := []
	bodies.append({
		"name": "player",
		"pos": [_player.position.x, _player.position.y],
		"vel": [_player.linear_velocity.x, _player.linear_velocity.y],
		"angle": _player.rotation,
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
