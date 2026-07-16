# tumble_3d.gd -- a 3D determinism REGRESSION fixture (GDScript lane).
#
# Purpose: reproduce the 3D reset-state-leak that mini_collect_3d cannot. The 3D physics
# non-determinism the funnel hit (notes/engines/DETERMINISM_3D.md) needs a RigidBody3D whose
# motion is SOLVER-INTEGRATED (gravity + damping into a floor contact), enclosed by a dense
# field of large static bodies (so the broadphase carries many pairs), and SAMPLED right at
# that contact -- never a settling pile-up (which converges to a rest state that masks the
# leak) and never a zero-g floater (mini_collect_3d, which has no contacts at all). Under the
# single-instance reset path a 3D game's dynamics live in root's REUSED World3D; before the
# host pinned a fresh World3D per episode, the GodotPhysics3D solver/broadphase state left by
# episode N perturbed episode N+1's first floor contact, so two identical seeded rollouts
# diverged in the body's contact-tick velocity/angle (the G1 two-run gate FAILED).
#
# Shape (a faithful port of the wild drone that failed at delta 5.9e-05): a heavily-damped
# RigidBody3D dropped under gravity into a long enclosed "canyon" (floor + ceiling + four
# walls) onto its floor; the episode ENDS (is_failure) the instant the body touches down, so
# the final snapshot lands on the tiny contact TRANSIENT -- the reused-space residual -- before
# it converges to rest. Pinned as a twin-rollout regression (tests/test_gd_lane.py):
# byte-identical WITH the fresh-World3D pin, divergent WITHOUT it.
#
# The single 3D quirk (shared with every 3D game): build() calls PhysicsServer3D.set_active(
# true) -- headless serve leaves the 3D space inactive otherwise, so bodies never move.

extends Node3D

const HALF_W := 8.0
const LEN := 100.0
const CEIL_Y := 14.0
const WIND := 3.0
const TOUCH_Y := 0.45              # body center this low == touched the floor (top at y=0)

var _body: RigidBody3D = null
var _statics := []                 # [{name, pos}]
var _touched := false

func _add_static(nm: String, pos: Vector3, size: Vector3) -> void:
	# NB the registration order mirrors the wild drone: the body joins the tree (and the
	# physics space, at the origin) BEFORE its shape and final position are set, so it is
	# inserted into the broadphase at (0,0,0) then MOVED -- the very sequence whose leftover
	# broadphase structure leaked across a reused space.
	var b := StaticBody3D.new()
	b.name = nm
	add_child(b)
	var cs := CollisionShape3D.new()
	var box := BoxShape3D.new()
	box.size = size
	cs.shape = box
	b.add_child(cs)
	b.position = pos
	_statics.append({"name": nm, "pos": pos})

func build(world_seed: int) -> void:
	# THE 3D QUIRK: enable the (otherwise inactive) 3D physics space.
	PhysicsServer3D.set_active(true)
	var rng := RandomNumberGenerator.new()
	rng.seed = world_seed
	_touched = false
	_statics = []

	# An enclosed canyon: floor + ceiling + four walls, all large overlapping boxes -> the
	# broadphase holds many body pairs whose processing order after a reused-space teardown
	# is part of what leaks. Floor top surface at y = 0.
	_add_static("canyon_floor", Vector3(0, -0.5, LEN * 0.5), Vector3(HALF_W * 2 + 4, 1, LEN + 10))
	_add_static("canyon_ceiling", Vector3(0, CEIL_Y + 0.5, LEN * 0.5), Vector3(HALF_W * 2 + 4, 1, LEN + 10))
	_add_static("wall_left", Vector3(-HALF_W - 1, (CEIL_Y + 1) * 0.5, LEN * 0.5), Vector3(2, CEIL_Y + 2, LEN + 10))
	_add_static("wall_right", Vector3(HALF_W + 1, (CEIL_Y + 1) * 0.5, LEN * 0.5), Vector3(2, CEIL_Y + 2, LEN + 10))
	_add_static("wall_back", Vector3(0, (CEIL_Y + 1) * 0.5, -2), Vector3(HALF_W * 2 + 4, CEIL_Y + 2, 2))
	_add_static("wall_far", Vector3(0, (CEIL_Y + 1) * 0.5, LEN + 2), Vector3(HALF_W * 2 + 4, CEIL_Y + 2, 2))

	# A few jittered static capsule spires further down the canyon (extra broadphase pairs,
	# and the seed-jittered draws mirror the wild game's build).
	for i in range(6):
		var s := StaticBody3D.new()
		s.name = "spire%d" % i
		var scol := CollisionShape3D.new()
		var cap := CapsuleShape3D.new()
		cap.radius = 0.9
		cap.height = 10.0
		scol.shape = cap
		s.add_child(scol)
		var sx: float = clampf(rng.randf_range(-4.5, 4.5), -HALF_W + 1, HALF_W - 1)
		var sz: float = 14.0 + float(i) * 6.0 + rng.randf_range(-0.5, 0.5)
		s.position = Vector3(sx, 5.0, sz)
		add_child(s)
		_statics.append({"name": s.name, "pos": s.position})

	# One heavily-damped, non-sleeping RigidBody3D dropped at the wild drone's own spawn:
	# gravity + a gentle wind carry it into a near-flat touchdown against the canyon floor.
	_body = RigidBody3D.new()
	_body.name = "faller"
	_body.mass = 1.0
	_body.gravity_scale = 1.0
	_body.continuous_cd = false
	_body.contact_monitor = true
	_body.max_contacts_reported = 8
	_body.can_sleep = false
	_body.angular_damp = 3.0
	_body.linear_damp = 0.3
	var col := CollisionShape3D.new()
	var bx := BoxShape3D.new()
	bx.size = Vector3(0.9, 0.35, 0.9)
	col.shape = bx
	_body.add_child(col)
	_body.position = Vector3(0.0, 5.0, 3.0)
	add_child(_body)

func _physics_process(_delta: float) -> void:
	if _body == null or not is_instance_valid(_body):
		return
	# Position-dependent wind (matches the wild drone's wind term) -> deterministic input.
	var wx := sin(_body.position.z * 0.08) * WIND
	_body.apply_central_force(Vector3(wx, 0.0, 0.0))
	# Detect ACTUAL floor contact via the contact monitor (exactly how the wild drone's
	# crash fires): terminating here samples the resting contact TRANSIENT -- the moment the
	# reused-space warm-start residual shows -- not the free-fall before it.
	if not _touched and not _body.get_colliding_bodies().is_empty():
		_touched = true

func act(_action: String) -> void:
	pass

func state() -> Dictionary:
	var bodies := []
	var p: Vector3 = _body.position
	var lv: Vector3 = _body.linear_velocity
	bodies.append({
		"name": "faller",
		"pos": [p.x, p.y, p.z],
		"vel": [lv.x, lv.y, lv.z],
		"angle": _body.rotation.y,
		"controlled": true,
		"static": false,
	})
	for s in _statics:
		var sp: Vector3 = s.pos
		bodies.append({
			"name": s.name, "pos": [sp.x, sp.y, sp.z], "vel": [0.0, 0.0, 0.0],
			"angle": 0.0, "controlled": false, "static": true,
		})
	return {"bodies": bodies, "flags": {"touched": _touched}}

func checkpoints() -> Dictionary:
	return {"touched_down": _touched}

func is_success() -> bool:
	return false

func is_failure() -> bool:
	# End the episode the instant the body touches down -> the final snapshot is the tiny
	# contact TRANSIENT (mirrors the wild drone's crash-at-floor termination).
	return _touched

func actions() -> Array:
	return ["push"]
