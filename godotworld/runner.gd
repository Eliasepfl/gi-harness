# runner.gd -- episode executor for the Godot spike (rung-4 step 2).
#
# GDScript twin of nodeworld/runner.js, minimal. Runs as a MainLoop via
#   godot --headless --fixed-fps 60 --path <proj> -s res://runner.gd -- --job=<file>
# and mirrors the JSON-job-in / JSONL-out contract:
#
#   JOB (from --job=<file>, else stdin):
#     { "episodes": [ { "seed": int, "actions": [str|null, ...] }, ... ],
#       "max_ticks": int, "frames_every": int|0 }
#
#   OUTPUT: one JSONL line per episode (in order) on stdout, framed between
#   markers so the Python side can slice cleanly out of Godot's own log noise:
#     __JSONL_BEGIN__
#     {"result":...,"ticks":...,"final_snapshot":{...},"frames":[...],"error":null}
#     __JSONL_END__
#
# DECISION-TICK SEMANTICS (CONTRACTS section 2, K=6): per tick, apply the action's
# impulse, then step physics 6x (each `await physics_frame` == one physics step),
# optionally sample a frame. Fresh scene per episode (floor + ball + box).
#
# BYTE-DETERMINISM: floats are emitted with "%.17f" (full float64 precision) --
# Godot's JSON.stringify rounds (0.1+0.2 -> "0.3") and would MASK low-bit drift,
# so it is deliberately NOT used for the numeric payload.

extends SceneTree

const K_STEPS := 6
const WORLD_W := 800.0
const WORLD_H := 600.0

# Scene geometry (y-UP, px; gravity (0,-900) set in project.godot -- mirrors nodeworld).
const FLOOR_CY := 30.0
const FLOOR_W := 760.0
const FLOOR_H := 60.0
const FLOOR_TOP := FLOOR_CY + FLOOR_H * 0.5      # 60.0 : top surface of the floor
const BALL_R := 20.0
const BALL_START := Vector2(400.0, 400.0)
const BOX_S := 40.0
const BOX_START := Vector2(250.0, 300.0)
const IMPULSE := 200.0

const ENTITY_ORDER := ["floor", "ball", "box"]

var _lines: PackedStringArray = []


func _initialize() -> void:
	# Determinism knobs (belt-and-suspenders on top of project.godot).
	Engine.physics_ticks_per_second = 60
	Engine.max_physics_steps_per_frame = 8
	Engine.physics_jitter_fix = 0.0
	_main()  # coroutine; kept alive by the physics_frame await chain


func _main() -> void:
	var job := _load_job()
	if job.is_empty():
		_lines.append(_err_line("no job / bad job JSON"))
		_finish()
		return
	var max_ticks := int(job.get("max_ticks", 120))
	var frames_every := int(job.get("frames_every", 0))
	var episodes: Array = job.get("episodes", [])
	for ep in episodes:
		var rec := await _run_episode(ep, max_ticks, frames_every)
		_lines.append(rec)
	_finish()


func _finish() -> void:
	var payload := "__JSONL_BEGIN__\n"
	payload += "\n".join(_lines)
	if _lines.size() > 0:
		payload += "\n"
	payload += "__JSONL_END__\n"
	printraw(payload)
	quit()


# --------------------------------------------------------------------------- #
# Scene build (fresh per episode)
# --------------------------------------------------------------------------- #
func _build_scene() -> Dictionary:
	var container := Node2D.new()

	var floor_body := StaticBody2D.new()
	floor_body.position = Vector2(WORLD_W * 0.5, FLOOR_CY)
	var fcs := CollisionShape2D.new()
	var frect := RectangleShape2D.new()
	frect.size = Vector2(FLOOR_W, FLOOR_H)
	fcs.shape = frect
	floor_body.add_child(fcs)
	container.add_child(floor_body)

	var ball := RigidBody2D.new()
	ball.position = BALL_START
	ball.can_sleep = false
	var bcs := CollisionShape2D.new()
	var circ := CircleShape2D.new()
	circ.radius = BALL_R
	bcs.shape = circ
	ball.add_child(bcs)
	container.add_child(ball)

	var box := RigidBody2D.new()
	box.position = BOX_START
	box.can_sleep = false
	var xcs := CollisionShape2D.new()
	var xrect := RectangleShape2D.new()
	xrect.size = Vector2(BOX_S, BOX_S)
	xcs.shape = xrect
	box.add_child(xcs)
	container.add_child(box)

	root.add_child(container)
	return {"container": container, "floor": floor_body, "ball": ball, "box": box}


func _apply_action(ball: RigidBody2D, action) -> void:
	if action == null:
		return
	match str(action):
		"left":
			ball.apply_central_impulse(Vector2(-IMPULSE, 0.0))
		"right":
			ball.apply_central_impulse(Vector2(IMPULSE, 0.0))
		"up":
			ball.apply_central_impulse(Vector2(0.0, IMPULSE))
		"down":
			ball.apply_central_impulse(Vector2(0.0, -IMPULSE))
		_:
			pass  # "none" / unknown -> no-op


# --------------------------------------------------------------------------- #
# Episode
# --------------------------------------------------------------------------- #
func _run_episode(ep: Dictionary, max_ticks: int, frames_every: int) -> String:
	var actions: Array = ep.get("actions", [])
	var scene := _build_scene()
	var ball: RigidBody2D = scene.ball

	var frames := PackedStringArray()
	if frames_every > 0:
		frames.append(_frame_json(0, scene))

	var budget: int = min(max_ticks, actions.size())
	var applied := 0
	for t in range(budget):
		_apply_action(ball, actions[t])
		applied += 1
		for k in range(K_STEPS):
			await physics_frame
		if frames_every > 0 and applied % frames_every == 0:
			frames.append(_frame_json(applied, scene))

	var result := "exhausted" if actions.size() < max_ticks else "budget"
	var snap := _snapshot_json(scene)

	scene.container.queue_free()
	await process_frame  # let the deferred free flush before the next build

	var line := '{"result":"%s","ticks":%d,"final_snapshot":%s' % [result, applied, snap]
	if frames_every > 0:
		line += ',"frames":[%s]' % ",".join(frames)
	line += ',"error":null}'
	return line


# --------------------------------------------------------------------------- #
# State readback / JSON (manual, full-precision floats)
# --------------------------------------------------------------------------- #
func _f(x: float) -> String:
	return "%.17f" % x


func _body_json(node: Node2D) -> String:
	var vel := Vector2.ZERO
	var ang := node.rotation
	if node is RigidBody2D:
		vel = (node as RigidBody2D).linear_velocity
	return '{"pos":[%s,%s],"vel":[%s,%s],"angle":%s}' % [
		_f(node.position.x), _f(node.position.y),
		_f(vel.x), _f(vel.y), _f(ang)]


func _snapshot_json(scene: Dictionary) -> String:
	var parts := PackedStringArray()
	for name in ENTITY_ORDER:
		parts.append('"%s":%s' % [name, _body_json(scene[name])])
	return "{%s}" % ",".join(parts)


func _frame_json(tick: int, scene: Dictionary) -> String:
	return '{"tick":%d,"entities":%s}' % [tick, _snapshot_json(scene)]


# --------------------------------------------------------------------------- #
# Job input (temp-file --job= preferred; stdin fallback, tested for the report)
# --------------------------------------------------------------------------- #
func _load_job() -> Dictionary:
	var raw := ""
	var job_path := ""
	for a in OS.get_cmdline_user_args():
		if a.begins_with("--job="):
			job_path = a.substr(6)
	if job_path != "":
		var f := FileAccess.open(job_path, FileAccess.READ)
		if f == null:
			return {}
		raw = f.get_as_text()
		f.close()
	else:
		raw = _read_all_stdin()
	var parsed = JSON.parse_string(raw)
	if typeof(parsed) != TYPE_DICTIONARY:
		return {}
	return parsed


func _read_all_stdin() -> String:
	var buf := ""
	while true:
		var chunk := OS.read_string_from_stdin(65536)
		if chunk == "":
			break
		buf += chunk
	return buf


func _err_line(msg: String) -> String:
	return '{"result":"error","ticks":0,"final_snapshot":{},"error":"%s"}' % msg
