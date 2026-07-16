# build_crash.gd -- a GameAPI fixture that PARSES clean (G0 parse gate + contract probe
# pass) but whose build() null-derefs at RUNTIME on its very first statement. It exercises
# the check-op build-ok override: GDScript has no catchable exceptions, so serve_game.gd's
# `inst.build(0)` aborts silently and the wire still reports `build: {ok:true}` -- which
# then masquerades downstream as "no controlled body". The Python executor reads the tee'd
# stderr delta, sees a `build`-scoped SCRIPT ERROR, and overrides build.ok to false so the
# G0 builds gate names the real crash (harness/verify/gd_exec.run_check).
#
# state()/predicates guard the null _player so the check op yields a well-formed reply
# (empty world) rather than cascading; the FIRST SCRIPT ERROR block is the build crash.

extends Node2D

const IMPULSE := 150.0

var _player: RigidBody2D = null
var _ghost: RigidBody2D = null      # DELIBERATELY never built -> build() null-derefs on it


func build(world_seed: int) -> void:
	# RUNTIME CRASH on the first line: _ghost is null -> null dereference. The engine
	# aborts build() here (nothing below runs) and prints the SCRIPT ERROR naming THIS
	# line; _player stays null so the world comes up empty.
	var here := _ghost.position
	_player = RigidBody2D.new()
	_player.position = Vector2(300.0, 300.0)
	add_child(_player)


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
	if _player == null:
		return {"bodies": [], "flags": {}}
	return {"bodies": [{
		"name": "player",
		"pos": [_player.position.x, _player.position.y],
		"vel": [_player.linear_velocity.x, _player.linear_velocity.y],
		"angle": _player.rotation,
		"controlled": true,
		"static": false,
	}], "flags": {}}


func checkpoints() -> Dictionary:
	return {"done": false}


func is_success() -> bool:
	return false


func is_failure() -> bool:
	return false


func actions() -> Array:
	return ["up", "down", "left", "right"]
