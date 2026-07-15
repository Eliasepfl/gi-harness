# flyoff.gd -- a fly-off fixture for the play-bounds termination behaviour (directive 2).
#
# "fly" hurls BOTH the controlled player and a NON-controlled "debris" body far out of
# the play area in a single tick. The serve host must then:
#   * TRUNCATE the episode (done_trunc) because the CONTROLLED body left the play-bounds
#     -- a runaway is not a break, just a clean episode end; and
#   * still report the NON-controlled "debris" in oob (a required-containment escape),
#     while NEVER reporting the controlled body as an escape.
#
# Driven directly through GdExecutor.run_batch (single-instance), so it need not certify
# the full funnel -- it only exercises the stepping/oob semantics.

extends Node2D

const IMPULSE := 20000.0     # big enough to clear play-bounds in one tick, << VMAX (1e5)

var _rng := RandomNumberGenerator.new()
var _player: RigidBody2D = null
var _debris: RigidBody2D = null


func build(world_seed: int) -> void:
	_rng.seed = world_seed
	_player = _make_body(Vector2(400.0, 300.0))
	add_child(_player)
	_debris = _make_body(Vector2(400.0, 320.0))
	add_child(_debris)


func _make_body(pos: Vector2) -> RigidBody2D:
	var b := RigidBody2D.new()
	b.gravity_scale = 0.0
	b.lock_rotation = true
	b.can_sleep = false
	b.position = pos
	var col := CollisionShape2D.new()
	var circ := CircleShape2D.new()
	circ.radius = 16.0
	col.shape = circ
	b.add_child(col)
	return b


func act(action: String) -> void:
	if action == "fly":
		if _player != null:
			_player.apply_central_impulse(Vector2(IMPULSE, 0.0))
		if _debris != null:
			_debris.apply_central_impulse(Vector2(IMPULSE, 0.0))


func state() -> Dictionary:
	return {"bodies": [{
		"name": "player",
		"pos": [_player.position.x, _player.position.y],
		"vel": [_player.linear_velocity.x, _player.linear_velocity.y],
		"angle": 0.0, "controlled": true, "static": false,
	}, {
		"name": "debris",
		"pos": [_debris.position.x, _debris.position.y],
		"vel": [_debris.linear_velocity.x, _debris.linear_velocity.y],
		"angle": 0.0, "controlled": false, "static": false,
	}]}


func checkpoints() -> Dictionary:
	return {"launched": _debris != null and _debris.position.x > 440.0}


func is_success() -> bool:
	return false


func is_failure() -> bool:
	return false


func actions() -> Array:
	return ["fly", "wait"]
