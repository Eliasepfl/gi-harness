# handsim_2d.gd -- the 2D twin of handsim_3d (GDScript lane test fixture, NOT a real game).
#
# "craft" is a shapeless kinematic variable (no CollisionShape2D) -> synthesized proxy driven
# by the trail; "wall" is a real shaped StaticBody2D that node-rides. Driven by
# tests/test_gd_dress_state.py.
extends Node2D

var _craft := Vector2.ZERO

func build(_world_seed: int) -> void:
	for c in get_children():
		c.free()
	_craft = Vector2(100.0, 100.0)
	var wall := StaticBody2D.new()
	wall.name = "wall"
	add_child(wall)
	var wcs := CollisionShape2D.new()
	var wr := RectangleShape2D.new()
	wr.size = Vector2(40.0, 40.0)
	wcs.shape = wr
	wall.add_child(wcs)
	wall.position = Vector2(300.0, 100.0)

func act(action: String) -> void:
	if action == "thrust":
		_craft += Vector2(10.0, 0.0)

func state() -> Dictionary:
	return {"bodies": [
		{"name": "craft", "pos": [_craft.x, _craft.y], "vel": [0.0, 0.0],
		 "angle": 0.0, "controlled": true, "static": false},
		{"name": "wall", "pos": [300.0, 100.0], "vel": [0.0, 0.0],
		 "angle": 0.0, "controlled": false, "static": true},
	]}

func checkpoints() -> Dictionary:
	return {"reached": false}

func is_success() -> bool:
	return false

func is_failure() -> bool:
	return false

func actions() -> Array:
	return ["thrust"]
