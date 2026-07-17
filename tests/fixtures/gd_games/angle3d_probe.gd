# angle3d_probe -- minimal 3D fixture whose bodies report `angle` as the NATURAL
# [x, y, z] Euler vector (rotation_degrees), the shape that crashed the pre-fix
# frame builder (float(Array) SCRIPT ERROR -> truncated frame -> VERIFY_ERROR).
# Twin-determinism + parse tests drive it; it must certify nothing fancy, just
# serve frames and win by reaching the pad.
extends Node

var _probe: RigidBody3D
var _pad_hit := false
var _ticks := 0

func build(world_seed: int) -> void:
	PhysicsServer3D.set_active(true)
	var rng := RandomNumberGenerator.new()
	rng.seed = world_seed

	_probe = RigidBody3D.new()
	_probe.name = "probe"
	var pc := CollisionShape3D.new()
	var ps := BoxShape3D.new()
	ps.size = Vector3(1, 1, 1)
	pc.shape = ps
	_probe.add_child(pc)
	_probe.position = Vector3(0, 4, 0)
	# A visible spin so the [x,y,z] angle actually varies across ticks.
	_probe.angular_velocity = Vector3(0.3, 1.1, 0.2)
	add_child(_probe)

	var floor_body := StaticBody3D.new()
	floor_body.name = "floor"
	var fc := CollisionShape3D.new()
	var fs := BoxShape3D.new()
	fs.size = Vector3(40, 1, 40)
	fc.shape = fs
	floor_body.add_child(fc)
	floor_body.position = Vector3(0, -0.5, 0)
	add_child(floor_body)

	var pad := Area3D.new()
	pad.name = "pad"
	var ac := CollisionShape3D.new()
	var as_ := BoxShape3D.new()
	as_.size = Vector3(3, 2, 3)
	ac.shape = as_
	pad.add_child(ac)
	pad.position = Vector3(6, 1, 0)
	add_child(pad)

	_pad_hit = false
	_ticks = 0

func act(action: String) -> void:
	_ticks += 1
	if _probe == null:
		return
	match action:
		"push_x":
			_probe.apply_central_impulse(Vector3(2.0, 0, 0))
		"push_up":
			_probe.apply_central_impulse(Vector3(0, 2.5, 0))
		"brake":
			_probe.linear_velocity *= 0.5

func _physics_process(_delta: float) -> void:
	if _probe == null or _pad_hit:
		return
	var pad := get_node_or_null("pad")
	if pad != null and (_probe.position - pad.position).length() < 2.0:
		_pad_hit = true

func state() -> Dictionary:
	var bodies := []
	for b in get_children():
		if not (b is Node3D):
			continue
		var vel := Vector3.ZERO
		if b is RigidBody3D:
			vel = b.linear_velocity
		var ang: Vector3 = b.rotation_degrees
		bodies.append({
			"name": b.name,
			"pos": [b.position.x, b.position.y, b.position.z],
			"vel": [vel.x, vel.y, vel.z],
			# THE POINT OF THIS FIXTURE: angle as the natural 3-vector.
			"angle": [ang.x, ang.y, ang.z],
			"controlled": b == _probe,
			"static": not (b is RigidBody3D),
		})
	return {"bodies": bodies}

func checkpoints() -> Dictionary:
	return {"pad_reached": _pad_hit}

func is_success() -> bool:
	return _pad_hit

func is_failure() -> bool:
	return _ticks > 0 and _probe != null and _probe.position.y < -5.0

func actions() -> Array:
	return ["push_x", "push_up", "brake"]
