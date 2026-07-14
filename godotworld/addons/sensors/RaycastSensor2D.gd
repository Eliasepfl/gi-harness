# RaycastSensor2D.gd -- vendored raycast obs sensor for the Godot lane (spec-v2).
#
# VENDORED from edbeeching/godot_rl_agents (examples pin d659636), file
# addons/godot_rl_agents/sensors/sensors_2d/RaycastSensor2D.gd. MIT-licensed:
#
#   MIT License. Copyright (c) 2022 Edward Beeching.
#   Permission is hereby granted, free of charge, to any person obtaining a copy
#   of this software and associated documentation files (the "Software"), to deal
#   in the Software without restriction ... The above copyright notice and this
#   permission notice shall be included in all copies. THE SOFTWARE IS PROVIDED
#   "AS IS", WITHOUT WARRANTY OF ANY KIND. (Full text: godot_rl_agents_examples/LICENSE.)
#
# A plain instantiable node: a fan of `n_rays` RayCast2D spread over `cone_width`
# degrees; `get_observation()` returns one normalized proximity per ray in [0,1]
# (0 = no hit; -> 1 as the hit point nears the sensor origin). Stateless, headless,
# no pixels -- runner.gd `load(...).new()`s it under a named body and appends the
# observation as an obs tail (godotworld/SPEC.md sec. sensors).
#
# LOCAL CHANGES vs upstream:
#   * dropped the editor tool-mode annotation and the editor-only debug_draw /
#     live-preview branch (the editor-hint guard) -- this is a headless-only path;
#   * `extends` the base by resource PATH (not the `ISensor2D` global class name) so
#     load()-by-path works without depending on the global class cache;
#   * otherwise the ray fan + proximity maths are copied verbatim.

extends "res://addons/sensors/ISensor2D.gd"
class_name RaycastSensor2D

@export_flags_2d_physics var collision_mask := 1
@export var collide_with_areas := false
@export var collide_with_bodies := true
@export var n_rays := 16.0
@export_range(5, 3000, 5.0) var ray_length := 200
@export_range(5, 360, 5.0) var cone_width := 360.0

var _angles = []
var rays := []


func _ready() -> void:
	_spawn_nodes()


func _spawn_nodes():
	for ray in rays:
		ray.queue_free()
	rays = []

	_angles = []
	var step = cone_width / (n_rays)
	var start = step / 2 - cone_width / 2

	for i in n_rays:
		var angle = start + i * step
		var ray = RayCast2D.new()
		ray.set_target_position(
			Vector2(ray_length * cos(deg_to_rad(angle)), ray_length * sin(deg_to_rad(angle)))
		)
		ray.set_name("node_" + str(i))
		ray.enabled = false
		ray.collide_with_areas = collide_with_areas
		ray.collide_with_bodies = collide_with_bodies
		ray.collision_mask = collision_mask
		add_child(ray)
		rays.append(ray)

		_angles.append(start + i * step)


func get_observation() -> Array:
	return self.calculate_raycasts()


func calculate_raycasts() -> Array:
	var result = []
	for ray in rays:
		ray.enabled = true
		ray.force_raycast_update()
		var distance = _get_raycast_distance(ray)
		result.append(distance)
		ray.enabled = false
	return result


func _get_raycast_distance(ray: RayCast2D) -> float:
	if !ray.is_colliding():
		return 0.0

	var distance = (global_position - ray.get_collision_point()).length()
	distance = clamp(distance, 0.0, ray_length)
	return (ray_length - distance) / ray_length
