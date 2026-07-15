# single_action_win.gd -- a DELIBERATELY BROKEN GameAPI fixture (GDScript lane).
#
# It certifies G0-G3 on its own terms: solvable, non-trivial (>=20 decision ticks),
# every milestone latches on the winning path. But it is winnable by SPAMMING ONE
# action ("right"): the lone gem sits straight to the right of the spawn, so holding
# "right" coasts into it while "up"/"down"/"left" only move away. This is exactly the
# BROKEN shape Elias directive 3 targets -- the single-action anti-triviality gate
# (harness/verify/gameverify.single_action_probe) must reject it with a GOAL_ERROR and
# the 'add a real obstacle/choice' repair hint, even though the G0-G3 funnel is happy.
#
# Modelled on mini_collect.gd (same duck-typed plain-Node2D convention, self-seeded rng,
# top-down zero-g coast) so it is a MINIMAL delta from a certifying game.

extends Node2D

const COLLECT_R := 40.0
const IMPULSE := 150.0
const DAMP := 3.0
const MAX_V := 130.0        # px/s cap -> ~13 px/tick, so the ~470 px run takes >20 ticks

var _rng := RandomNumberGenerator.new()
var _player: RigidBody2D = null
var _gem_pos := Vector2.ZERO
var _collected := false


func build(world_seed: int) -> void:
	_rng.seed = world_seed
	var jitter := _rng.randf_range(-3.0, 3.0)

	_player = RigidBody2D.new()
	_player.gravity_scale = 0.0
	_player.linear_damp_mode = RigidBody2D.DAMP_MODE_REPLACE
	_player.linear_damp = DAMP
	_player.lock_rotation = true
	_player.can_sleep = false
	_player.position = Vector2(150.0, 300.0 + jitter)
	var col := CollisionShape2D.new()
	var circ := CircleShape2D.new()
	circ.radius = 16.0
	col.shape = circ
	_player.add_child(col)
	add_child(_player)

	_gem_pos = Vector2(620.0, 300.0)                # straight right of the spawn
	_collected = false
	var marker := Node2D.new()
	marker.name = "gem"
	marker.position = _gem_pos
	add_child(marker)


func _physics_process(_delta: float) -> void:
	if _player == null:
		return
	if _player.linear_velocity.length() > MAX_V:
		_player.linear_velocity = _player.linear_velocity.limit_length(MAX_V)
	if not _collected and _player.position.distance_to(_gem_pos) < COLLECT_R:
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
	var bodies := [{
		"name": "player",
		"pos": [_player.position.x, _player.position.y],
		"vel": [_player.linear_velocity.x, _player.linear_velocity.y],
		"angle": _player.rotation,
		"controlled": true,
		"static": false,
	}, {
		"name": "gem",
		"pos": [_gem_pos.x, _gem_pos.y],
		"vel": [0.0, 0.0],
		"angle": 0.0,
		"controlled": false,
		"static": true,
	}]
	return {"bodies": bodies}


func checkpoints() -> Dictionary:
	var near := _player != null and _player.position.distance_to(_gem_pos) < 120.0
	return {"near_gem": near, "got_gem": _collected}


func is_success() -> bool:
	return _collected


func is_failure() -> bool:
	return false


func actions() -> Array:
	return ["up", "down", "left", "right"]
