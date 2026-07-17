# syntax_error.gd -- a GENUINE GDScript syntax error (a missing ':' after the build()
# signature) that Godot reports as `Parse Error: Unexpected "Indent" in class body.` with NO
# "(Warning treated as error.)" tag. It MUST stay a FATAL G0 load failure: the warning-only
# reclassification (item 1) may never launder a real parse error into a pass.

extends Node2D

var _player: RigidBody2D = null


func build(world_seed: int) -> void
	_player = RigidBody2D.new()
	add_child(_player)


func act(action: String) -> void:
	pass


func state() -> Dictionary:
	return {"bodies": [{"name": "player", "pos": [0.0, 0.0], "vel": [0.0, 0.0],
		"angle": 0.0, "controlled": true, "static": false}]}


func checkpoints() -> Dictionary:
	return {"started": false}


func is_success() -> bool:
	return false


func is_failure() -> bool:
	return false


func actions() -> Array:
	return ["a", "b"]
