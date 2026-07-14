# ISensor2D.gd -- vendored sensor base for the Godot lane (spec-v2 sensors).
#
# VENDORED from edbeeching/godot_rl_agents (examples pin d659636), file
# addons/godot_rl_agents/sensors/sensors_2d/ISensor2D.gd. MIT-licensed:
#
#   MIT License. Copyright (c) 2022 Edward Beeching.
#   Permission is hereby granted, free of charge, to any person obtaining a copy
#   of this software and associated documentation files (the "Software"), to deal
#   in the Software without restriction ... The above copyright notice and this
#   permission notice shall be included in all copies. THE SOFTWARE IS PROVIDED
#   "AS IS", WITHOUT WARRANTY OF ANY KIND. (Full text: godot_rl_agents_examples/LICENSE.)
#
# LOCAL CHANGES: verbatim node interface; kept as a plain instantiable Node2D so
# runner.gd can `load(...).new()` it (no AGENT/group coupling, no editor branch).

extends Node2D
class_name ISensor2D

var _obs: Array = []
var _active := false


func get_observation():
	pass


func activate():
	_active = true


func deactivate():
	_active = false


func _update_observation():
	pass


func reset():
	pass
