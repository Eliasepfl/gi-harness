# mini_collect_3d.gd -- a duck-typed, 3D GameAPI-convention fixture game (GDScript lane).
#
# The 3D twin of mini_collect.gd: a PLAIN Node3D (NO base class, no class_name) that
# IMPLEMENTS the same method convention -- build/act/state/checkpoints/is_success/
# is_failure/actions -- and certifies G0-G3 through the SAME serve contract, proving
# 3D works THROUGH the pipeline (Node3D + RigidBody3D/StaticBody3D/Area3D +
# CollisionShape3D + Vector3 state), not just in a standalone script.
#
# The one 3D quirk: build() calls PhysicsServer3D.set_active(true) -- the headless
# script/serve mode leaves the 3D physics space INACTIVE otherwise, so bodies never
# move. This is the single PhysicsServer call the game makes.
#
# A top-down zero-g puck on a table: one controlled RigidBody3D locked to the z=0 play
# plane, a StaticBody3D table below it, and two Area3D goal pads -- one left, one right
# of the start, so a single held action reaches at most one (docking both forces a
# reversal). Collection is a proximity latch read purely in the predicates.
# Deterministic: the only randomness is a seed-stable jitter from an rng the game seeds
# itself from build()'s seed.

extends Node3D

const COLLECT_R := 45.0
const IMPULSE := 150.0
const DAMP := 3.0
const MAX_V := 130.0        # units/s cap -> ~2.2 units/step, bounded travel

var _rng := RandomNumberGenerator.new()
var _puck: RigidBody3D = null
var _table: StaticBody3D = null
var _goals := []            # [{name, node, pos: Vector3, collected}]


func build(world_seed: int) -> void:
	# THE 3D QUIRK: enable the 3D physics space (inactive by default in serve mode).
	PhysicsServer3D.set_active(true)
	_rng.seed = world_seed
	var jitter := _rng.randf_range(-5.0, 5.0)

	# A StaticBody3D "table" just behind the z=0 play plane (exercises StaticBody3D).
	_table = StaticBody3D.new()
	_table.position = Vector3(400.0, 300.0, -20.0)
	var tcol := CollisionShape3D.new()
	var tbox := BoxShape3D.new()
	tbox.size = Vector3(800.0, 600.0, 10.0)
	tcol.shape = tbox
	_table.add_child(tcol)
	add_child(_table)

	# One controlled RigidBody3D puck: zero-g top-down, locked to the z=0 plane so the
	# bounded x,y plane the funnel checks stays meaningful and the goal stays solvable.
	_puck = RigidBody3D.new()
	_puck.gravity_scale = 0.0
	_puck.linear_damp_mode = RigidBody3D.DAMP_MODE_REPLACE
	_puck.linear_damp = DAMP
	_puck.axis_lock_linear_z = true
	_puck.axis_lock_angular_x = true
	_puck.axis_lock_angular_y = true
	_puck.axis_lock_angular_z = true
	_puck.can_sleep = false
	_puck.position = Vector3(400.0, 300.0 + jitter, 0.0)
	var pcol := CollisionShape3D.new()
	var sph := SphereShape3D.new()
	sph.radius = 16.0
	pcol.shape = sph
	_puck.add_child(pcol)
	add_child(_puck)

	_goals = []
	_add_goal("goal_left", Vector3(280.0, 300.0, 0.0))    # ~120 units left
	_add_goal("goal_right", Vector3(560.0, 300.0, 0.0))   # ~160 units right -> a reversal


func _add_goal(goal_name: String, pos: Vector3) -> void:
	var area := Area3D.new()                                # exercises Area3D
	area.name = goal_name
	area.position = pos
	var acol := CollisionShape3D.new()
	var asph := SphereShape3D.new()
	asph.radius = COLLECT_R
	acol.shape = asph
	area.add_child(acol)
	add_child(area)
	_goals.append({"name": goal_name, "node": area, "pos": pos, "collected": false})


func _physics_process(_delta: float) -> void:
	if _puck == null:
		return
	if _puck.linear_velocity.length() > MAX_V:
		_puck.linear_velocity = _puck.linear_velocity.limit_length(MAX_V)
	for g in _goals:
		if not g.collected and _puck.position.distance_to(g.pos) < COLLECT_R:
			g.collected = true


func act(action: String) -> void:
	if _puck == null:
		return
	var v := Vector3.ZERO
	match action:
		"left":
			v = Vector3(-IMPULSE, 0.0, 0.0)
		"right":
			v = Vector3(IMPULSE, 0.0, 0.0)
		"up":
			v = Vector3(0.0, -IMPULSE, 0.0)
		"down":
			v = Vector3(0.0, IMPULSE, 0.0)
	_puck.apply_central_impulse(v)


func _count() -> int:
	var n := 0
	for g in _goals:
		if g.collected:
			n += 1
	return n


func state() -> Dictionary:
	var p := _puck.position
	var lv := _puck.linear_velocity
	var bodies := [{
		"name": "puck",
		"pos": [p.x, p.y, p.z],
		"vel": [lv.x, lv.y, lv.z],
		"angle": 0.0,
		"controlled": true,
		"static": false,
	}]
	for g in _goals:
		var gp: Vector3 = g.pos
		bodies.append({
			"name": g.name, "pos": [gp.x, gp.y, gp.z], "vel": [0.0, 0.0, 0.0],
			"angle": 0.0, "controlled": false, "static": true,
		})
	var tp := _table.position
	bodies.append({
		"name": "table", "pos": [tp.x, tp.y, tp.z], "vel": [0.0, 0.0, 0.0],
		"angle": 0.0, "controlled": false, "static": true,
	})
	return {
		"bodies": bodies,
		"flags": {"got_first": _count() >= 1, "got_both": _count() >= 2},
	}


func checkpoints() -> Dictionary:
	return {"reached_first": _count() >= 1, "reached_both": _count() >= 2}


func is_success() -> bool:
	return _count() >= 2


func is_failure() -> bool:
	return false


func actions() -> Array:
	return ["left", "right", "up", "down"]
