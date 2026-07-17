# warning_inference.gd -- a VALID GameAPI-convention game whose ONLY parse-time fault is a
# benign type-inference/Variant WARNING (Godot 4.7 `inference_on_variant`, which the strict
# --check-only promotes to an error). It is byte-identical to mini_collect.gd except build()
# draws one untyped `:=` from a Variant-returning `Dictionary.get(...)` -- the exact Class-A
# construct the 2026-07-17 parser-friction probe captured. It MUST pass the G0 load: the
# value is simply typed Variant and the game runs deterministically. (Contrast: an array
# subscript `arr[0]` off an untyped array is a GENUINE hard error, "Cannot infer ... doesn't
# have a set type", which stays fatal.)

extends Node2D

const COLLECT_R := 40.0
const IMPULSE := 150.0
const DAMP := 3.0
const MAX_V := 130.0

var _rng := RandomNumberGenerator.new()
var _player: RigidBody2D = null
var _gems := []


func build(world_seed: int) -> void:
	_rng.seed = world_seed
	# THE CLASS-A CONSTRUCT: `mode` is inferred from a Variant value (Dictionary.get), so
	# Godot warns "inferred from a Variant value ... (Warning treated as error.)". Inert here
	# (opts is empty -> mode is null); it changes no physics, only the parse-time diagnostic.
	var opts := {}
	var mode := opts.get("mode")
	if mode == null:
		pass
	var jitter := _rng.randf_range(-5.0, 5.0)

	_player = RigidBody2D.new()
	_player.gravity_scale = 0.0
	_player.linear_damp_mode = RigidBody2D.DAMP_MODE_REPLACE
	_player.linear_damp = DAMP
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
	_add_gem("gem_a", Vector2(300.0, 165.0))
	_add_gem("gem_b", Vector2(560.0, 340.0))


func _add_gem(gem_name: String, pos: Vector2) -> void:
	var marker := Node2D.new()
	marker.name = gem_name
	marker.position = pos
	add_child(marker)
	_gems.append({"name": gem_name, "node": marker, "pos": pos, "collected": false})


func _physics_process(_delta: float) -> void:
	if _player == null:
		return
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
