# runtime_crash.gd -- a GameAPI fixture that PARSES + builds clean (G0 green) but whose
# act() null-derefs at RUNTIME. It exists to exercise the stderr runtime-error capture:
# GDScript has no catchable exceptions, so the null deref does NOT raise -- the engine
# prints `SCRIPT ERROR: ... at: act (gdscript://<hash>.gd:LINE)` to the tee'd stderr and
# the call silently aborts (the impulse is never applied), so the action LOOKS dead. The
# funnel would report a misleading "dead action" hint; the capture surfaces the real
# crash site instead (harness/verify/gd_exec.parse_runtime_errors + gameverify hint).
#
# G0 stays green (parse gate + contract probe + build(0) + one controlled dynamic body +
# >=2 entities all pass -- the check op never calls act()); the crash first fires in G1's
# action-efficacy probe, where every action is inert because act() aborts.

extends Node2D

const IMPULSE := 150.0

var _rng := RandomNumberGenerator.new()
var _player: RigidBody2D = null
var _ghost: RigidBody2D = null      # DELIBERATELY never built -> act() null-derefs on it


func build(world_seed: int) -> void:
	_rng.seed = world_seed
	_player = RigidBody2D.new()
	_player.gravity_scale = 0.0
	_player.linear_damp_mode = RigidBody2D.DAMP_MODE_REPLACE
	_player.linear_damp = 3.0
	_player.lock_rotation = true
	_player.can_sleep = false
	_player.position = Vector2(300.0, 300.0)
	var col := CollisionShape2D.new()
	var circ := CircleShape2D.new()
	circ.radius = 16.0
	col.shape = circ
	_player.add_child(col)
	add_child(_player)
	_add_marker("goal", Vector2(300.0, 165.0))
	_add_marker("gate", Vector2(560.0, 340.0))


func _add_marker(marker_name: String, pos: Vector2) -> void:
	var m := Node2D.new()
	m.name = marker_name
	m.position = pos
	add_child(m)


func act(action: String) -> void:
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
	# RUNTIME CRASH: _ghost is null (never built) -> null dereference every tick. The
	# engine aborts the call here and prints the SCRIPT ERROR block naming THIS line.
	_ghost.apply_central_impulse(v)


func state() -> Dictionary:
	if _player == null:
		return {"bodies": [], "flags": {}}
	var bodies := []
	bodies.append({
		"name": "player",
		"pos": [_player.position.x, _player.position.y],
		"vel": [_player.linear_velocity.x, _player.linear_velocity.y],
		"angle": _player.rotation,
		"controlled": true,
		"static": false,
	})
	bodies.append({"name": "goal", "pos": [300.0, 165.0], "vel": [0.0, 0.0],
		"angle": 0.0, "controlled": false, "static": true})
	bodies.append({"name": "gate", "pos": [560.0, 340.0], "vel": [0.0, 0.0],
		"angle": 0.0, "controlled": false, "static": true})
	return {"bodies": bodies, "flags": {"reached_goal": false}}


func checkpoints() -> Dictionary:
	return {"reached_goal": false}


func is_success() -> bool:
	return false


func is_failure() -> bool:
	return false


func actions() -> Array:
	return ["up", "down", "left", "right"]
