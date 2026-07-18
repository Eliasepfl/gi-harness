# handsim_3d.gd -- a SHAPELESS hand-sim body (GDScript lane test fixture, NOT a real game).
#
# "craft" is a pure kinematic variable: it has NO CollisionShape node, so the dresser's
# collision walk finds nothing to ride -- it exists ONLY in state(). "gate" is a real shaped
# StaticBody3D. Exercises visual_dress.gd's dresser-follows-state SYNTHESIS path: craft must
# get a trail-driven synthesized proxy while gate node-rides. Driven by
# tests/test_gd_dress_state.py.
extends Node3D

var _craft := Vector3.ZERO

func build(_world_seed: int) -> void:
	for c in get_children():
		c.free()
	_craft = Vector3(0.0, 0.0, 0.0)
	# A real shaped static gate ahead on -Z.
	var gate := StaticBody3D.new()
	gate.name = "gate"
	add_child(gate)
	var gcs := CollisionShape3D.new()
	var gbox := BoxShape3D.new()
	gbox.size = Vector3(4.0, 4.0, 4.0)
	gcs.shape = gbox
	gate.add_child(gcs)
	gate.position = Vector3(0.0, 0.0, -40.0)

func act(action: String) -> void:
	if action == "thrust":
		_craft += Vector3(0.0, 0.0, -5.0)

func state() -> Dictionary:
	return {"bodies": [
		{"name": "craft", "pos": [_craft.x, _craft.y, _craft.z],
		 "vel": [0.0, 0.0, 0.0], "angle": 0.0, "controlled": true, "static": false},
		{"name": "gate", "pos": [0.0, 0.0, -40.0], "vel": [0.0, 0.0, 0.0],
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
