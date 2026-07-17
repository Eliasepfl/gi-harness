# dressed_lander_2d.gd -- a 2D GameAPI fixture that AUTHORS ITS OWN PRESENTATION.
#
# The 2D twin of dressed_arena_3d.gd, shaped like the certified lander (brown crater terrain,
# green pad, yellow probe) and the platformers: 11 of the library's 14 visual-authoring games
# are 2D, so the 2D respect path needs its own fixture.
#
# Deliberately MIXED:
#   * probe (controlled RigidBody2D) -> authors a yellow Polygon2D -> NOT proxied, and NOT
#     haloed either (an orange halo over an authored yellow probe is exactly the flattening)
#   * pad   (Area2D)                 -> authors NOTHING            -> MUST be proxied
#   * a root-level terrain Polygon2D -> the game painted its own world -> no slate backdrop
#   * Camera2D -> ours must not steal the frame
#
# A featherweight lander: thrust up against gravity, drift onto the pad.

extends Node2D

const PAD_R := 40.0
const THRUST := 260.0

var _rng := RandomNumberGenerator.new()
var _probe: RigidBody2D = null
var _pad: Area2D = null
var _pad_pos := Vector2(300.0, 460.0)
var _landed := false


func build(world_seed: int) -> void:
	_rng.seed = world_seed
	var jitter := _rng.randf_range(-4.0, 4.0)

	# --- the game's OWN world: brown crater terrain at scene level ---------- #
	var terrain := Polygon2D.new()
	terrain.name = "Terrain"
	terrain.polygon = PackedVector2Array([
		Vector2(0.0, 500.0), Vector2(600.0, 500.0),
		Vector2(600.0, 540.0), Vector2(0.0, 540.0)])
	terrain.color = Color(0.27, 0.17, 0.10)
	add_child(terrain)

	# --- the game's OWN camera --------------------------------------------- #
	var cam := Camera2D.new()
	cam.name = "LanderCam"
	cam.position = Vector2(300.0, 270.0)
	add_child(cam)

	# --- probe: authors its own yellow hull --------------------------------- #
	_probe = RigidBody2D.new()
	_probe.gravity_scale = 0.4
	_probe.position = Vector2(300.0 + jitter, 90.0)
	var pcol := CollisionShape2D.new()
	var prect := RectangleShape2D.new()
	prect.size = Vector2(24.0, 24.0)
	pcol.shape = prect
	_probe.add_child(pcol)
	var hull := Polygon2D.new()
	hull.name = "ProbeHull"
	hull.polygon = PackedVector2Array([
		Vector2(0.0, -14.0), Vector2(12.0, 12.0), Vector2(-12.0, 12.0)])
	hull.color = Color(0.95, 0.85, 0.2)
	_probe.add_child(hull)
	add_child(_probe)

	# --- pad: authors NOTHING -> the dresser must still make it visible ----- #
	_pad = Area2D.new()
	_pad.name = "pad"
	_pad.position = _pad_pos
	var acol := CollisionShape2D.new()
	var acirc := CircleShape2D.new()
	acirc.radius = PAD_R
	acol.shape = acirc
	_pad.add_child(acol)
	add_child(_pad)


func _physics_process(_delta: float) -> void:
	if _probe == null:
		return
	if not _landed and _probe.position.distance_to(_pad_pos) < PAD_R:
		_landed = true


func act(action: String) -> void:
	if _probe == null:
		return
	match action:
		"thrust":
			_probe.apply_central_impulse(Vector2(0.0, -THRUST))
		"left":
			_probe.apply_central_impulse(Vector2(-THRUST * 0.4, 0.0))
		"right":
			_probe.apply_central_impulse(Vector2(THRUST * 0.4, 0.0))


func state() -> Dictionary:
	var p := _probe.position
	var lv := _probe.linear_velocity
	return {
		"bodies": [
			{"name": "probe", "pos": [p.x, p.y], "vel": [lv.x, lv.y],
				"angle": _probe.rotation, "controlled": true, "static": false},
			{"name": "pad", "pos": [_pad_pos.x, _pad_pos.y], "vel": [0.0, 0.0],
				"angle": 0.0, "controlled": false, "static": true},
		],
		"flags": {"landed": _landed},
	}


func checkpoints() -> Dictionary:
	return {"landed": _landed}


func is_success() -> bool:
	return _landed


func is_failure() -> bool:
	return false


func actions() -> Array:
	return ["thrust", "left", "right", "noop"]
