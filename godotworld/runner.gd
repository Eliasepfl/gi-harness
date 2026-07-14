# runner.gd -- FROZEN, audited game-spec interpreter for the Godot lane (rung-4).
#
# The GDScript twin of nodeworld/runner.js, generalised from the spike's fixed
# 3-body scene to a DECLARATIVE game-spec (godotworld/SPEC.md). The LLM emits DATA
# only; THIS file is the only code that ever runs in Godot. No untrusted GDScript,
# no eval of arbitrary code -- predicates go through a whitelisted token scan + the
# Expression class with a locked-down base instance (see _pred_error / QueryCtx).
#
# Run:
#   godot --headless --fixed-fps 60 --path <godotworld> -s res://runner.gd -- --job=<file>
#
# JOB (JSON, from --job=<file>, else stdin):
#   { "mode": "episodes"|"check",
#     "source": "<spec JSON string>",   # empty/absent -> DEFAULT_SPEC (bench.py compat)
#     "episodes": [ {"seed": int, "actions": [str|null,...]}, ... ],
#     "max_ticks": int, "frames_every": int|0, "escape_margin": number? }
#
# OUTPUT: framed between markers so the Python side slices past Godot's log noise:
#   __JSONL_BEGIN__
#   <one JSONL line per episode>            (episodes mode)
#   <one JSON facts object>                 (check mode)
#   __JSONL_END__
#
# DECISION-TICK SEMANTICS (CONTRACTS section 2, K=6): per tick, apply the action's
# verb list, then step physics 6x (each `await physics_frame` == one physics step),
# updating contacts + on_step behaviors after each step; then latch checkpoints,
# check failure, then success. Fresh scene per episode; y-UP px; gravity (0,-900).
#
# BYTE-DETERMINISM: episode floats are emitted with "%.17f" (full float64) -- JSON
# rounding would MASK low-bit drift. Check-mode facts (never byte-compared) use the
# built-in serializer for brevity.

extends SceneTree

const K_STEPS := 6
const VMAX := 1.0e5
const DEFAULT_WORLD := Vector2(800.0, 600.0)

# The spike's fixed 3-body scene, expressed as a spec -- used when a job omits
# "source" so godotworld/bench.py (the spike gate harness) keeps reproducing.
const DEFAULT_SPEC := {
	"meta": {"title": "spike-default", "prompt": "spike 3-body scene",
		"actions": ["left", "right", "up", "down"]},
	"bodies": [
		{"name": "floor", "shape": "box", "pos": [400.0, 30.0], "size": [760.0, 60.0], "static": true},
		{"name": "ball", "shape": "circle", "pos": [400.0, 400.0], "radius": 20.0, "control": true},
		{"name": "box", "shape": "box", "pos": [250.0, 300.0], "size": [40.0, 40.0]},
	],
	"act": {
		"left": [{"verb": "impulse", "body": "ball", "vec": [-200.0, 0.0]}],
		"right": [{"verb": "impulse", "body": "ball", "vec": [200.0, 0.0]}],
		"up": [{"verb": "impulse", "body": "ball", "vec": [0.0, 200.0]}],
		"down": [{"verb": "impulse", "body": "ball", "vec": [0.0, -200.0]}],
	},
	"predicates": {"checkpoints": {}},
}

# Whitelisted predicate identifiers (the security boundary -- an allow-list, so
# OS / FileAccess / load / preload / set_script and every other name is rejected).
const ALLOWED_IDENTS := {
	"pos_x": true, "pos_y": true, "vel_x": true, "vel_y": true, "speed": true,
	"angle": true, "grounded": true, "contacts": true, "dist": true, "flag": true,
	"steps": true,
	"abs": true, "min": true, "max": true, "clamp": true, "sqrt": true,
	"floor": true, "ceil": true, "sign": true,
	"and": true, "or": true, "not": true, "true": true, "false": true,
}
const ALLOWED_OPS := "+-*/%(),<>=!"

# spec-v2 sensors: a FIXED whitelist mapping a spec `type` to a vendored, audited
# sensor script (godotworld/addons/sensors/). The spec supplies DATA only (which
# type + params); no spec string is ever executed. Unlisted types are ignored.
const SENSOR_SCRIPTS := {
	"raycast2d": "res://addons/sensors/RaycastSensor2D.gd",
}

# --------------------------------------------------------------------------- #
# Parsed-spec state (set once by _load_spec)
# --------------------------------------------------------------------------- #
var _spec = null                    # the parsed spec Dictionary, or null on parse failure
var _spec_meta := {}
var _spec_bodies := []
var _spec_joints := []
var _spec_contacts := []
var _spec_act := {}
var _spec_on_step := []
var _spec_sensors := []             # spec-v2 sensor descriptors (DATA)
var _spec_predicates := {}
var _world_w := 800.0
var _world_h := 600.0
var _success_expr = null            # String or null
var _failure_expr = null            # String or null
var _checkpoint_keys := []          # ordered checkpoint names
var _checkpoint_exprs := {}         # name -> expression String

# --------------------------------------------------------------------------- #
# Live-scene state (rebuilt per episode)
# --------------------------------------------------------------------------- #
var _container: Node2D = null
var _bodies := {}                   # name -> {node, shape, verts, radius, static, sensor, controlled, removed}
var _order := []                    # entity insertion order (drives snapshot/frames)
var _node_ids := {}                 # instance_id -> name (reverse lookup)
var _flags := {}                    # key -> value
var _contact_rules := []            # [{a, b, flag, once, fired}]
var _steps := 0
var _frozen := false
var _nan := false
var _tick_forces := []              # [[RigidBody2D, Vector2], ...] re-applied each sub-step
var _sensors := []                  # [{node, n_rays}] in spec order -> obs tail

var _query_ctx = null               # QueryCtx (Expression base instance)
var _expr_cache := {}               # expr String -> Expression | null (parse/scan failed)
var _lines: PackedStringArray = []


# =========================================================================== #
# Expression base instance -- exposes ONLY the query DSL to the Expression class.
# Even so, the token scan (_pred_error) is the real boundary; this just narrows
# the surface. Every method delegates back into the runner's current world.
# =========================================================================== #
class QueryCtx extends RefCounted:
	var rt = null
	func pos_x(nm): return rt._q_pos(nm, 0)
	func pos_y(nm): return rt._q_pos(nm, 1)
	func vel_x(nm): return rt._q_vel(nm, 0)
	func vel_y(nm): return rt._q_vel(nm, 1)
	func speed(nm): return rt._q_speed(nm)
	func angle(nm): return rt._q_angle(nm)
	func grounded(nm): return rt._q_grounded(nm)
	func contacts(a, b): return rt._q_contacts(a, b)
	func dist(a, b): return rt._q_dist(a, b)
	func flag(k): return rt._q_flag(k)


# =========================================================================== #
# Lifecycle
# =========================================================================== #
func _initialize() -> void:
	Engine.physics_ticks_per_second = 60
	Engine.max_physics_steps_per_frame = 8
	Engine.physics_jitter_fix = 0.0
	_query_ctx = QueryCtx.new()
	_query_ctx.rt = self
	_main()  # coroutine; kept alive by the physics_frame/process_frame await chain


func _main() -> void:
	var job := _load_job()
	if job.is_empty():
		_emit_line(_err_line("no job / bad job JSON"))
		_finish()
		return

	var source = job.get("source", "")
	if job.get("mode", "episodes") == "check":
		await process_frame  # let the SceneTree root become available
		_emit_line(await _run_check(source))
		_finish()
		return

	# Episode mode.
	var load_err := _load_spec(source)
	var max_ticks := int(job.get("max_ticks", 120))
	var frames_every := int(job.get("frames_every", 0))
	var has_margin: bool = job.has("escape_margin")
	var margin := float(job.get("escape_margin", 0.0))
	var episodes: Array = job.get("episodes", [])

	for ep in episodes:
		if load_err != "":
			_emit_line(_err_line("spec load failed: " + load_err))
			continue
		var rec := await _run_episode(ep, max_ticks, frames_every, has_margin, margin)
		_emit_line(rec)
	_finish()


func _emit_line(line: String) -> void:
	_lines.append(line)


func _finish() -> void:
	var payload := "__JSONL_BEGIN__\n"
	payload += "\n".join(_lines)
	if _lines.size() > 0:
		payload += "\n"
	payload += "__JSONL_END__\n"
	printraw(payload)
	quit()


# =========================================================================== #
# Spec loading
# =========================================================================== #
func _load_spec(source) -> String:
	# Returns "" on success, else an error message. Populates the _spec_* fields.
	var spec = DEFAULT_SPEC
	if source != null and str(source).strip_edges() != "":
		var parsed = JSON.parse_string(str(source))
		if typeof(parsed) != TYPE_DICTIONARY:
			_spec = null
			return "spec is not a JSON object"
		spec = parsed
	_spec = spec
	_spec_meta = spec.get("meta", {}) if typeof(spec.get("meta", {})) == TYPE_DICTIONARY else {}
	_spec_bodies = spec.get("bodies", []) if typeof(spec.get("bodies", [])) == TYPE_ARRAY else []
	_spec_joints = spec.get("joints", []) if typeof(spec.get("joints", [])) == TYPE_ARRAY else []
	_spec_contacts = spec.get("on_contact", []) if typeof(spec.get("on_contact", [])) == TYPE_ARRAY else []
	_spec_act = spec.get("act", {}) if typeof(spec.get("act", {})) == TYPE_DICTIONARY else {}
	_spec_on_step = spec.get("on_step", []) if typeof(spec.get("on_step", [])) == TYPE_ARRAY else []
	_spec_sensors = spec.get("sensors", []) if typeof(spec.get("sensors", [])) == TYPE_ARRAY else []
	_spec_predicates = spec.get("predicates", {}) if typeof(spec.get("predicates", {})) == TYPE_DICTIONARY else {}

	var ws = _spec_meta.get("world_size", null)
	if typeof(ws) == TYPE_ARRAY and ws.size() == 2:
		_world_w = float(ws[0])
		_world_h = float(ws[1])
	else:
		_world_w = DEFAULT_WORLD.x
		_world_h = DEFAULT_WORLD.y

	_success_expr = _spec_predicates.get("success", null)
	_failure_expr = _spec_predicates.get("failure", null)
	_checkpoint_keys = []
	_checkpoint_exprs = {}
	var cps = _spec_predicates.get("checkpoints", {})
	if typeof(cps) == TYPE_DICTIONARY:
		for k in cps.keys():
			_checkpoint_keys.append(str(k))
			_checkpoint_exprs[str(k)] = cps[k]
	return ""


# =========================================================================== #
# Scene construction (fresh per episode / per check build)
# =========================================================================== #
func _build_scene() -> String:
	# Returns "" on success, else a build error message.
	_container = Node2D.new()
	_bodies = {}
	_order = []
	_node_ids = {}
	_flags = {}
	_contact_rules = []
	_steps = 0
	_frozen = false
	_nan = false
	_tick_forces = []
	_sensors = []

	for spec_body in _spec_bodies:
		var err := _add_body(spec_body)
		if err != "":
			root.add_child(_container)  # ensure teardown works
			return err

	root.add_child(_container)

	# Joints (added after all bodies exist).
	for j in _spec_joints:
		_add_joint(j)

	# on_contact rules.
	for r in _spec_contacts:
		if typeof(r) != TYPE_DICTIONARY:
			continue
		_contact_rules.append({"a": str(r.get("a", "")), "b": str(r.get("b", "")),
			"flag": str(r.get("flag", "")), "once": bool(r.get("once", true)),
			"fired": false})

	# rising_level flags start at their declared value so predicates read sanely at t=0.
	for beh in _spec_on_step:
		if typeof(beh) == TYPE_DICTIONARY and str(beh.get("kind", "")) == "rising_level":
			_flags[str(beh.get("flag", ""))] = float(beh.get("start", 0.0))

	# spec-v2 sensors: instantiate each vendored sensor under its named body (DATA
	# only -- no spec code runs). Added after all bodies exist so `attach_to` resolves.
	for s in _spec_sensors:
		_add_sensor(s)

	return ""


func _add_body(b) -> String:
	if typeof(b) != TYPE_DICTIONARY:
		return "body entry is not an object"
	var name := str(b.get("name", ""))
	if name == "":
		return "body missing name"
	if _bodies.has(name):
		return "duplicate body name: " + name
	var shape := str(b.get("shape", "box"))
	var pos_arr = b.get("pos", null)
	if typeof(pos_arr) != TYPE_ARRAY or pos_arr.size() != 2:
		return "body %s missing pos" % name
	var pos := Vector2(float(pos_arr[0]), float(pos_arr[1]))
	var is_static := bool(b.get("static", false))
	var is_sensor := bool(b.get("sensor", false))
	var controlled := bool(b.get("control", false))

	# Geometry -> a Godot Shape2D + local vertices/radius for bbox math.
	var col := CollisionShape2D.new()
	var verts := []
	var radius := 0.0
	match shape:
		"box":
			var sz = b.get("size", null)
			if typeof(sz) != TYPE_ARRAY or sz.size() != 2:
				return "box %s requires size=[w,h]" % name
			var w := float(sz[0])
			var h := float(sz[1])
			var rect := RectangleShape2D.new()
			rect.size = Vector2(w, h)
			col.shape = rect
			verts = [[-w * 0.5, -h * 0.5], [w * 0.5, -h * 0.5],
				[w * 0.5, h * 0.5], [-w * 0.5, h * 0.5]]
		"circle":
			var r = b.get("radius", null)
			if r == null:
				return "circle %s requires radius" % name
			radius = float(r)
			var circ := CircleShape2D.new()
			circ.radius = radius
			col.shape = circ
		"segment":
			var pa = b.get("a", null)
			var pb = b.get("b", null)
			if typeof(pa) != TYPE_ARRAY or typeof(pb) != TYPE_ARRAY:
				return "segment %s requires a=[x,y] and b=[x,y]" % name
			var seg := SegmentShape2D.new()
			seg.a = Vector2(float(pa[0]), float(pa[1]))
			seg.b = Vector2(float(pb[0]), float(pb[1]))
			col.shape = seg
			verts = [[float(pa[0]), float(pa[1])], [float(pb[0]), float(pb[1])]]
		"poly":
			var vs = b.get("vertices", null)
			if typeof(vs) != TYPE_ARRAY or vs.size() < 3:
				return "poly %s requires vertices=[[x,y],...]" % name
			var pts := PackedVector2Array()
			for v in vs:
				pts.append(Vector2(float(v[0]), float(v[1])))
				verts.append([float(v[0]), float(v[1])])
			var poly := ConvexPolygonShape2D.new()
			poly.points = pts
			col.shape = poly
		_:
			return "unknown shape %s on body %s" % [shape, name]

	var node: Node2D
	if is_sensor:
		var area := Area2D.new()
		area.monitoring = true
		area.monitorable = true
		node = area
	elif is_static:
		node = StaticBody2D.new()
	else:
		var rb := RigidBody2D.new()
		rb.can_sleep = false
		rb.contact_monitor = true
		rb.max_contacts_reported = 8
		rb.mass = float(b.get("mass", 1.0))
		rb.lock_rotation = bool(b.get("locked_rotation", false))
		var vel = b.get("velocity", null)
		if typeof(vel) == TYPE_ARRAY and vel.size() == 2:
			rb.linear_velocity = Vector2(float(vel[0]), float(vel[1]))
		node = rb

	# Friction / elasticity via a physics material (solid bodies only).
	if not is_sensor:
		var mat := PhysicsMaterial.new()
		mat.friction = float(b.get("friction", 0.7))
		mat.bounce = float(b.get("elasticity", 0.3))
		if node is RigidBody2D:
			(node as RigidBody2D).physics_material_override = mat
		elif node is StaticBody2D:
			(node as StaticBody2D).physics_material_override = mat

	node.position = pos
	node.rotation = float(b.get("angle", 0.0))
	node.add_child(col)
	_container.add_child(node)

	_bodies[name] = {"node": node, "shape": shape, "verts": verts, "radius": radius,
		"static": is_static, "sensor": is_sensor, "controlled": controlled,
		"removed": false}
	_order.append(name)
	_node_ids[node.get_instance_id()] = name
	return ""


func _add_joint(j) -> void:
	if typeof(j) != TYPE_DICTIONARY:
		return
	var a := str(j.get("a", ""))
	var b := str(j.get("b", ""))
	if not _bodies.has(a) or not _bodies.has(b):
		return
	var na: Node2D = _bodies[a].node
	var nb: Node2D = _bodies[b].node
	var jtype := str(j.get("type", "pin"))
	if jtype == "spring":
		var spring := DampedSpringJoint2D.new()
		var pa := na.position
		var pb := nb.position
		spring.position = (pa + pb) * 0.5
		spring.rotation = (pb - pa).angle() + PI * 0.5
		spring.length = float(j.get("rest_length", pa.distance_to(pb)))
		spring.rest_length = float(j.get("rest_length", pa.distance_to(pb)))
		spring.stiffness = float(j.get("stiffness", 20.0))
		spring.damping = float(j.get("damping", 1.0))
		spring.node_a = spring.get_path_to(na)
		spring.node_b = spring.get_path_to(nb)
		_container.add_child(spring)
	else:
		# pin and pivot both -> PinJoint2D (Godot 2D has no distance joint).
		var pin := PinJoint2D.new()
		var point = j.get("point", null)
		if jtype == "pivot" and typeof(point) == TYPE_ARRAY and point.size() == 2:
			pin.position = Vector2(float(point[0]), float(point[1]))
		else:
			pin.position = (na.position + nb.position) * 0.5
		pin.node_a = pin.get_path_to(na)
		pin.node_b = pin.get_path_to(nb)
		_container.add_child(pin)


func _add_sensor(s) -> void:
	# spec-v2: attach a vendored obs sensor under a named body. `type` selects a
	# FIXED whitelisted script; every param is DATA. Params are set BEFORE add_child
	# so the sensor's `_ready` spawns its rays with the spec values (its setters do
	# not re-spawn at runtime -- the editor-only path was stripped when vendored).
	if typeof(s) != TYPE_DICTIONARY:
		return
	var stype := str(s.get("type", ""))
	if not SENSOR_SCRIPTS.has(stype):
		return
	var rec = _bodies.get(str(s.get("attach_to", "")), null)
	if rec == null or rec.removed:
		return
	var script = load(SENSOR_SCRIPTS[stype])
	if script == null:
		return
	var node = script.new()
	if s.has("n_rays"):
		node.n_rays = float(s.get("n_rays"))
	if s.has("ray_length"):
		node.ray_length = float(s.get("ray_length"))
	if s.has("cone_width_deg"):
		node.cone_width = float(s.get("cone_width_deg"))
	if s.has("collision_mask"):
		node.collision_mask = int(s.get("collision_mask"))
	rec.node.add_child(node)
	_sensors.append({"node": node, "n_rays": int(node.n_rays)})


func _teardown_scene() -> void:
	if _container != null:
		_container.queue_free()
		_container = null
	_bodies = {}
	_order = []
	_node_ids = {}
	_sensors = []


# =========================================================================== #
# Episode
# =========================================================================== #
func _run_episode(ep: Dictionary, max_ticks: int, frames_every: int,
		has_margin: bool, margin: float) -> String:
	var build_err := _build_scene()
	var actions: Array = ep.get("actions", [])
	var frames := PackedStringArray()
	var latches := {}
	var applied := 0
	var result := ""

	if build_err != "":
		_teardown_scene()
		await process_frame
		return _err_line("build failed: " + build_err)

	# Sensor guard (#95359): a RayCast2D added this frame does not register with the
	# physics space until one physics step elapses, so `force_raycast_update` would
	# read empty on the first tick. Settle ONE frame here (sensor specs only, so
	# sensor-free specs stay byte-for-byte unchanged). Not counted in `_steps`.
	if not _sensors.is_empty():
		await physics_frame

	if frames_every > 0:
		frames.append(_frame_json(0))

	var budget: int = min(max_ticks, actions.size())
	for t in range(budget):
		_apply_action(actions[t])
		applied += 1
		for k in range(K_STEPS):
			_apply_tick_forces()
			await physics_frame
			_steps += 1
			_update_contacts()
			_run_on_step()
			if not _sane():
				_frozen = true
				_nan = true
				break
		_latch(latches, applied)
		if _frozen:
			break
		if _failure_expr != null and _eval_bool(_failure_expr):
			result = "failure"
			break
		if _eval_bool(_success_expr):
			result = "success"
			break
		if frames_every > 0 and applied % frames_every == 0:
			frames.append(_frame_json(applied))

	if result == "":
		result = "exhausted" if actions.size() < max_ticks else "budget"

	var snap := _snapshot_json()
	var oob_json := ""
	if has_margin:
		oob_json = _oob_json(margin)
	# Read the sensor obs tail at the final settled state (before teardown frees them).
	var has_obs := not _sensors.is_empty()
	var obs_json := ""
	if has_obs:
		obs_json = _obs_json()

	_teardown_scene()  # clears _sensors -> capture has_obs above, not after
	await process_frame  # flush the deferred free before the next build

	var line := '{"result":"%s","ticks":%d,"checkpoints":%s,"final_snapshot":%s,"world_size":[%s,%s]' % [
		result, applied, _checkpoints_json(latches), snap, _num(_world_w), _num(_world_h)]
	if has_obs:
		line += ',"obs":[%s]' % obs_json
	if frames_every > 0:
		line += ',"frames":[%s]' % ",".join(frames)
	if has_margin:
		line += ',"nan":%s,"oob":[%s]' % [_b(_nan), oob_json]
	line += ',"error":null}'
	return line


func _apply_action(action) -> void:
	_tick_forces = []
	if action == null:
		return
	var binds = _spec_act.get(str(action), [])
	if typeof(binds) != TYPE_ARRAY:
		return
	for vc in binds:
		if typeof(vc) != TYPE_DICTIONARY:
			continue
		var when = vc.get("when", null)
		if when != null and not _eval_bool(when):
			continue
		var bname := str(vc.get("body", ""))
		var rec = _bodies.get(bname, null)
		if rec == null or rec.removed:
			continue
		var node = rec.node
		if not (node is RigidBody2D):
			continue
		var vec = vc.get("vec", [0.0, 0.0])
		var v := Vector2(float(vec[0]), float(vec[1]))
		match str(vc.get("verb", "")):
			"impulse":
				(node as RigidBody2D).apply_central_impulse(v)
			"set_velocity":
				(node as RigidBody2D).linear_velocity = v
			"force":
				_tick_forces.append([node, v])


func _apply_tick_forces() -> void:
	for pair in _tick_forces:
		(pair[0] as RigidBody2D).apply_central_force(pair[1])


func _update_contacts() -> void:
	for rule in _contact_rules:
		if rule.once and rule.fired:
			continue
		if _q_contacts(rule.a, rule.b):
			_flags[rule.flag] = true
			rule.fired = true


func _run_on_step() -> void:
	for beh in _spec_on_step:
		if typeof(beh) != TYPE_DICTIONARY:
			continue
		match str(beh.get("kind", "")):
			"velocity_clamp":
				_do_velocity_clamp(beh)
			"timer_flag":
				if _steps >= int(beh.get("after_steps", 0)):
					_flags[str(beh.get("flag", ""))] = true
			"rising_level":
				var start := float(beh.get("start", 0.0))
				var rate := float(beh.get("rate", 0.0))
				_flags[str(beh.get("flag", ""))] = start + rate * float(_steps)
			"remove_when":
				if _truthy(_flags.get(str(beh.get("flag", "")), false)):
					_remove_body(str(beh.get("body", "")))


func _do_velocity_clamp(beh: Dictionary) -> void:
	var rec = _bodies.get(str(beh.get("body", "")), null)
	if rec == null or rec.removed or not (rec.node is RigidBody2D):
		return
	var node := rec.node as RigidBody2D
	var v := node.linear_velocity
	if beh.has("vx_max"):
		var vx_max := absf(float(beh["vx_max"]))
		v.x = clamp(v.x, -vx_max, vx_max)
	var vy_min := float(beh.get("vy_min", -INF))
	var vy_max := float(beh.get("vy_max", INF))
	v.y = clamp(v.y, vy_min, vy_max)
	node.linear_velocity = v


func _remove_body(name: String) -> void:
	var rec = _bodies.get(name, null)
	if rec == null or rec.removed:
		return
	rec.removed = true
	_node_ids.erase(rec.node.get_instance_id())
	_order.erase(name)
	rec.node.queue_free()


func _latch(latches: Dictionary, applied: int) -> void:
	for key in _checkpoint_keys:
		if not latches.has(key):
			latches[key] = null
		if latches[key] == null and _eval_bool(_checkpoint_exprs.get(key, null)):
			latches[key] = applied


func _sane() -> bool:
	for name in _order:
		var rec = _bodies[name]
		if rec.static or rec.sensor or rec.removed:
			continue
		var node = rec.node
		if not (node is RigidBody2D):
			continue
		var p: Vector2 = node.position
		var v := (node as RigidBody2D).linear_velocity
		if not (is_finite(p.x) and is_finite(p.y) and is_finite(v.x) and is_finite(v.y)):
			return false
		if v.length() > VMAX:
			return false
	return true


# =========================================================================== #
# Predicate DSL (whitelist scan + Expression over the QueryCtx base instance)
# =========================================================================== #
func _eval_bool(expr) -> bool:
	var v = _eval_raw(expr)
	return typeof(v) == TYPE_BOOL and v


func _eval_raw(expr):
	if expr == null or typeof(expr) != TYPE_STRING:
		return null
	var e = _make_expr(expr)
	if e == null:
		return null
	var r = e.execute([_steps], _query_ctx, false)
	if e.has_execute_failed():
		return null
	return r


func _make_expr(expr: String):
	if _expr_cache.has(expr):
		return _expr_cache[expr]
	if _pred_error(expr) != "":
		_expr_cache[expr] = null
		return null
	var e := Expression.new()
	var err := e.parse(expr, PackedStringArray(["steps"]))
	if err != OK:
		_expr_cache[expr] = null
		return null
	_expr_cache[expr] = e
	return e


func _pred_error(expr) -> String:
	# Strict allow-list token scan -- the real security boundary. Rejects any
	# identifier outside ALLOWED_IDENTS, attribute access ('.'), indexing, and
	# every character outside the tiny predicate grammar.
	if typeof(expr) != TYPE_STRING:
		return "predicate must be a string"
	var s: String = expr
	if s.strip_edges() == "":
		return "empty predicate"
	var n := s.length()
	var i := 0
	while i < n:
		var c := s[i]
		if c == '"' or c == "'":
			var q := c
			i += 1
			while i < n and s[i] != q:
				if s[i] == "\\":
					return "backslash not allowed in string literal"
				i += 1
			if i >= n:
				return "unterminated string literal"
			i += 1
			continue
		if c == " " or c == "\t" or c == "\r" or c == "\n":
			i += 1
			continue
		if _is_alpha(c):
			var j := i
			while j < n and _is_ident_char(s[j]):
				j += 1
			var word := s.substr(i, j - i)
			if not ALLOWED_IDENTS.has(word):
				return "identifier not allowed: '%s'" % word
			i = j
			continue
		if _is_digit(c):
			var j := i
			while j < n and (_is_digit(s[j]) or s[j] == "."):
				j += 1
			i = j
			continue
		if ALLOWED_OPS.contains(c):
			i += 1
			continue
		return "character not allowed: '%s'" % c
	return ""


func _is_digit(c: String) -> bool:
	var o := c.unicode_at(0)
	return o >= 48 and o <= 57


func _is_alpha(c: String) -> bool:
	var o := c.unicode_at(0)
	return (o >= 65 and o <= 90) or (o >= 97 and o <= 122) or o == 95


func _is_ident_char(c: String) -> bool:
	return _is_alpha(c) or _is_digit(c)


# =========================================================================== #
# Query context (used by predicates AND state readback)
# =========================================================================== #
func _q_pos(nm, axis: int) -> float:
	var rec = _bodies.get(str(nm), null)
	if rec == null or rec.removed:
		return 0.0
	return rec.node.position.x if axis == 0 else rec.node.position.y


func _q_vel(nm, axis: int) -> float:
	var rec = _bodies.get(str(nm), null)
	if rec == null or rec.removed or not (rec.node is RigidBody2D):
		return 0.0
	var v := (rec.node as RigidBody2D).linear_velocity
	return v.x if axis == 0 else v.y


func _q_speed(nm) -> float:
	var rec = _bodies.get(str(nm), null)
	if rec == null or rec.removed or not (rec.node is RigidBody2D):
		return 0.0
	return (rec.node as RigidBody2D).linear_velocity.length()


func _q_angle(nm) -> float:
	var rec = _bodies.get(str(nm), null)
	if rec == null or rec.removed:
		return 0.0
	return rec.node.rotation


func _q_dist(a, b) -> float:
	var ra = _bodies.get(str(a), null)
	var rb = _bodies.get(str(b), null)
	if ra == null or rb == null or ra.removed or rb.removed:
		return INF
	return ra.node.position.distance_to(rb.node.position)


func _q_flag(k):
	return _flags.get(str(k), false)


func _q_contacts(a, b) -> bool:
	var ra = _bodies.get(str(a), null)
	var rb = _bodies.get(str(b), null)
	if ra == null or rb == null or ra.removed or rb.removed:
		return false
	var na = ra.node
	var nb = rb.node
	if na is Area2D:
		if (na as Area2D).get_overlapping_bodies().has(nb):
			return true
		if nb is Area2D and (na as Area2D).get_overlapping_areas().has(nb):
			return true
		return false
	if nb is Area2D:
		return (nb as Area2D).get_overlapping_bodies().has(na)
	if na is RigidBody2D:
		return (na as RigidBody2D).get_colliding_bodies().has(nb)
	if nb is RigidBody2D:
		return (nb as RigidBody2D).get_colliding_bodies().has(na)
	return false


func _q_grounded(nm) -> bool:
	var rec = _bodies.get(str(nm), null)
	if rec == null or rec.removed or not (rec.node is RigidBody2D):
		return false
	var node := rec.node as RigidBody2D
	var cy := node.position.y
	for other in node.get_colliding_bodies():
		# y-UP: a supporting body sits BELOW (smaller y) than this body's center.
		if other != null and other.position.y < cy - 0.5:
			return true
	return false


# =========================================================================== #
# Geometry / bounds
# =========================================================================== #
func _bbox(rec) -> Array:
	var node = rec.node
	var px: float = node.position.x
	var py: float = node.position.y
	if rec.shape == "circle":
		var r: float = rec.radius
		return [px - r, py - r, px + r, py + r]
	var ang: float = node.rotation
	var ca := cos(ang)
	var sa := sin(ang)
	var left := INF
	var bottom := INF
	var right := -INF
	var top := -INF
	for v in rec.verts:
		var wx: float = px + ca * float(v[0]) - sa * float(v[1])
		var wy: float = py + sa * float(v[0]) + ca * float(v[1])
		left = min(left, wx)
		right = max(right, wx)
		bottom = min(bottom, wy)
		top = max(top, wy)
	return [left, bottom, right, top]


func _in_bounds(rec, margin: float) -> bool:
	var bb := _bbox(rec)
	return (bb[0] >= -margin and bb[1] >= -margin
		and bb[2] <= _world_w + margin and bb[3] <= _world_h + margin)


func _oob_json(margin: float) -> String:
	var out := PackedStringArray()
	for name in _order:
		var rec = _bodies[name]
		if rec.static or rec.sensor or rec.removed:
			continue
		if not _in_bounds(rec, margin):
			out.append('"%s"' % _esc(name))
	return ",".join(out)


# Analytic AABB penetration depth (no physics step needed; used by G0 init check).
func _aabb_penetration(ra, rb) -> float:
	var ba := _bbox(ra)
	var bb := _bbox(rb)
	var ox: float = min(ba[2], bb[2]) - max(ba[0], bb[0])
	var oy: float = min(ba[3], bb[3]) - max(ba[1], bb[1])
	if ox <= 0.0 or oy <= 0.0:
		return 0.0
	return min(ox, oy)


# =========================================================================== #
# State readback / JSON (episode: manual %.17f floats)
# =========================================================================== #
func _f(x: float) -> String:
	return "%.17f" % x


func _num(x: float) -> String:
	if x == floor(x) and abs(x) < 1.0e15:
		return "%d" % int(x)
	return "%.17f" % x


func _b(v: bool) -> String:
	return "true" if v else "false"


func _esc(s: String) -> String:
	return s.replace("\\", "\\\\").replace('"', '\\"')


func _truthy(v) -> bool:
	if typeof(v) == TYPE_BOOL:
		return v
	if typeof(v) == TYPE_INT or typeof(v) == TYPE_FLOAT:
		return v != 0
	return v != null


func _body_snap_json(rec) -> String:
	var node = rec.node
	var vel := Vector2.ZERO
	if node is RigidBody2D:
		vel = (node as RigidBody2D).linear_velocity
	return '{"pos":[%s,%s],"vel":[%s,%s],"angle":%s}' % [
		_f(node.position.x), _f(node.position.y),
		_f(vel.x), _f(vel.y), _f(node.rotation)]


func _snapshot_json() -> String:
	var parts := PackedStringArray()
	for name in _order:
		parts.append('"%s":%s' % [_esc(name), _body_snap_json(_bodies[name])])
	return "{%s}" % ",".join(parts)


func _query_json(rec) -> String:
	var node = rec.node
	var vel := Vector2.ZERO
	var avel := 0.0
	if node is RigidBody2D:
		vel = (node as RigidBody2D).linear_velocity
		avel = (node as RigidBody2D).angular_velocity
	var bb := _bbox(rec)
	return ('{"pos":[%s,%s],"vel":[%s,%s],"angle":%s,"angular_vel":%s,'
		+ '"bbox":[%s,%s,%s,%s],"shape":"%s","static":%s,"sensor":%s,"controlled":%s}') % [
		_f(node.position.x), _f(node.position.y), _f(vel.x), _f(vel.y),
		_f(node.rotation), _f(avel),
		_f(bb[0]), _f(bb[1]), _f(bb[2]), _f(bb[3]),
		rec.shape, _b(rec.static), _b(rec.sensor), _b(rec.controlled)]


func _read_obs() -> Array:
	# Concatenate every attached sensor's get_observation() in spec order -> the
	# flat obs tail (floats). A sensor whose host body was removed reads as skipped.
	var obs := []
	for s in _sensors:
		var node = s.node
		if node == null or not is_instance_valid(node):
			continue
		var vals = node.get_observation()
		if typeof(vals) == TYPE_ARRAY:
			for v in vals:
				obs.append(float(v))
	return obs


func _obs_json() -> String:
	var parts := PackedStringArray()
	for v in _read_obs():
		parts.append(_f(float(v)))
	return ",".join(parts)


func _frame_json(tick: int) -> String:
	var parts := PackedStringArray()
	for name in _order:
		parts.append('"%s":%s' % [_esc(name), _query_json(_bodies[name])])
	if not _sensors.is_empty():
		return '{"tick":%d,"entities":{%s},"obs":[%s]}' % [tick, ",".join(parts), _obs_json()]
	return '{"tick":%d,"entities":{%s}}' % [tick, ",".join(parts)]


func _checkpoints_json(latches: Dictionary) -> String:
	var parts := PackedStringArray()
	for key in _checkpoint_keys:
		var t = latches.get(key, null)
		var val := "null" if t == null else str(int(t))
		parts.append('"%s":%s' % [_esc(key), val])
	return "{%s}" % ",".join(parts)


# =========================================================================== #
# CHECK MODE -- raw G0/G2 facts mirroring nodeworld/runner.js runCheck. The scan
# is trivially empty (pure data); SPEC WELL-FORMEDNESS is the load/symbols analog.
# Facts are never byte-compared, so the built-in serializer is fine here.
# =========================================================================== #
func _run_check(source) -> String:
	var out := {"mode": "check", "scan": []}

	var load_err := _load_spec(source)
	if _spec == null:
		out["load"] = {"ok": false, "error": load_err}
		return JSON.stringify(out)
	out["load"] = {"ok": true, "error": null}

	# 2. Required "symbols" = required spec sections (well-formedness analog).
	var title = _spec_meta.get("title", null)
	var defined := {
		"TITLE": typeof(title) == TYPE_STRING and title != "",
		"PROMPT": _spec_meta.has("prompt"),
		"ACTIONS": _spec_meta.has("actions"),
		"build": _spec.has("bodies"),
		"act": _spec.has("act"),
		"success": _spec_predicates.has("success"),
		"checkpoints": _spec_predicates.has("checkpoints"),
	}
	var callable_map := {
		"build": typeof(_spec.get("bodies", null)) == TYPE_ARRAY,
		"act": typeof(_spec.get("act", null)) == TYPE_DICTIONARY,
		"success": typeof(_spec_predicates.get("success", null)) == TYPE_STRING,
		"checkpoints": typeof(_spec_predicates.get("checkpoints", null)) == TYPE_DICTIONARY,
	}
	out["symbols"] = {"defined": defined, "callable": callable_map}
	var required := ["TITLE", "PROMPT", "ACTIONS", "build", "act", "success", "checkpoints"]
	var missing := []
	for s in required:
		if not defined.get(s, false):
			missing.append(s)
	var not_callable := []
	for s in ["build", "act", "success", "checkpoints"]:
		if defined.get(s, false) and not callable_map.get(s, false):
			not_callable.append(s)
	if missing.size() > 0 or not_callable.size() > 0:
		return JSON.stringify(out)

	# 3. ACTIONS well-formedness.
	var actions = _spec_meta.get("actions", null)
	var is_list := typeof(actions) == TYPE_ARRAY
	var all_str := is_list
	if is_list:
		for a in actions:
			if typeof(a) != TYPE_STRING:
				all_str = false
				break
	out["actions"] = {
		"is_list": is_list,
		"length": actions.size() if is_list else null,
		"all_str": all_str,
		"values": actions if is_list else null,
	}
	if not (is_list and actions.size() >= 2 and actions.size() <= 8 and all_str):
		return JSON.stringify(out)

	# 4. Declared world size (bounds validated Python-side).
	var declared_ws = _spec_meta.get("world_size", null)
	out["world_size"] = {"declared": declared_ws, "effective": [_world_w, _world_h]}

	# 5. Build the scene.
	await process_frame
	var build_err := _build_scene()
	if build_err != "":
		out["build"] = {"ok": false, "error": build_err}
		_teardown_scene()
		return JSON.stringify(out)
	out["build"] = {"ok": true, "error": null}

	# 6. Post-build facts.
	out["entities"] = _order.duplicate()
	var queries := {}
	for name in _order:
		var rec = _bodies[name]
		queries[name] = {
			"static": rec.static,
			"sensor": rec.sensor,
			"controlled": rec.controlled,
			"in_bounds": _in_bounds(rec, 0.0),
		}
	out["queries"] = queries

	var pen := []
	for i in range(_order.size()):
		for k in range(i + 1, _order.size()):
			var a: String = _order[i]
			var b: String = _order[k]
			var ra = _bodies[a]
			var rb = _bodies[b]
			if ra.static and rb.static:
				continue
			if ra.sensor or rb.sensor:
				continue
			var d := _aabb_penetration(ra, rb)
			if d > 0.0:
				pen.append([a, b, d])
	out["penetration"] = pen

	# 7. Goal probes on the fresh t=0 world (predicates are pure/read-only, so all
	# three share one built world -- state_unchanged/deterministic hold by design).
	out["g2"] = {
		"success": _probe_predicate(_spec_predicates.get("success", null)),
		"failure": _probe_predicate(_spec_predicates.get("failure", null)) if _spec_predicates.has("failure") else null,
		"checkpoints": _probe_checkpoints(),
	}

	_teardown_scene()
	return JSON.stringify(out)


func _probe_predicate(expr) -> Dictionary:
	var scan_err := _pred_error(expr)
	if scan_err != "":
		return {"is_bool": false, "value": null, "deterministic": false,
			"state_unchanged": false, "error": scan_err}
	var before := _snapshot_json()
	var r1 = _eval_raw(expr)
	var r2 = _eval_raw(expr)
	var after := _snapshot_json()
	var is_bool := typeof(r1) == TYPE_BOOL and typeof(r2) == TYPE_BOOL
	return {
		"is_bool": is_bool,
		"value": (r1 if is_bool else null),
		"deterministic": r1 == r2,
		"state_unchanged": before == after,
		"error": null,
	}


func _probe_checkpoints() -> Dictionary:
	var cps = _spec_predicates.get("checkpoints", null)
	if typeof(cps) != TYPE_DICTIONARY:
		return {"is_dict": false, "keys": [], "n": null, "non_bool_keys": [],
			"true_keys": [], "deterministic": false, "state_unchanged": false, "error": null}
	var keys := []
	var non_bool := []
	var true_keys := []
	var before := _snapshot_json()
	var first := {}
	for k in cps.keys():
		keys.append(str(k))
		var v1 = _eval_raw(cps[k])
		if typeof(v1) != TYPE_BOOL:
			non_bool.append(str(k))
		elif v1:
			true_keys.append(str(k))
		first[str(k)] = v1
	# determinism: evaluate a second time
	var deterministic := true
	for k in cps.keys():
		var v2 = _eval_raw(cps[k])
		if v2 != first[str(k)]:
			deterministic = false
	var after := _snapshot_json()
	return {
		"is_dict": true,
		"keys": keys,
		"n": keys.size(),
		"non_bool_keys": non_bool,
		"true_keys": true_keys,
		"deterministic": deterministic,
		"state_unchanged": before == after,
		"error": null,
	}


# =========================================================================== #
# Job input (temp-file --job= preferred; stdin fallback)
# =========================================================================== #
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
	return '{"result":"error","ticks":0,"checkpoints":{},"final_snapshot":{},"world_size":[%s,%s],"error":"%s"}' % [
		_num(_world_w), _num(_world_h), _esc(msg)]
