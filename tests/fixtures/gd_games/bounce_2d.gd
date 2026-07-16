# bounce_2d.gd -- a FLOAT-SENSITIVE 2D GameAPI fixture for the capture-lane zero-contact guard.
#
# A RigidBody2D ball free-falls under gravity and ricochets among a lattice of static circle pegs
# inside a bouncy box (pachinko). The bounce cascade is chaotic: a sub-ULP perturbation early on
# changes which side of a peg the ball passes and diverges the whole trajectory within ~50 ticks.
# It is NOT meant to certify -- it exists so a test can prove the visual dresser's per-tick sync()
# never perturbs the physics (reading a shape's .global_transform mid-step used to force a
# transform-notification flush that diverged exactly this kind of replay). act() is a no-op, so
# serve and a dressed capture replay the SAME fixed action list and MUST share a byte-identical
# state() trail.
extends Node2D

var _rng := RandomNumberGenerator.new()
var ball: RigidBody2D
var _tick := 0

const W := 480.0
const H := 640.0

func build(world_seed: int) -> void:
	_rng.seed = world_seed
	_tick = 0
	for c in get_children():
		remove_child(c)
		c.free()
	_wall("floor", Vector2(W * 0.5, H), Vector2(W, 24))
	_wall("ceil", Vector2(W * 0.5, 0), Vector2(W, 24))
	_wall("left", Vector2(0, H * 0.5), Vector2(24, H))
	_wall("right", Vector2(W, H * 0.5), Vector2(24, H))
	# A fixed peg lattice (deterministic; not seed-dependent so the fixture is stable).
	var rows := 5
	var cols := 4
	for r in range(rows):
		for col in range(cols):
			var off := 30.0 if (r % 2 == 0) else 0.0
			var px := 70.0 + off + float(col) * 100.0
			var py := 150.0 + float(r) * 80.0
			_peg("peg_%d_%d" % [r, col], Vector2(px, py), 14.0)
	# The ball: slight initial sideways lean so it engages the lattice asymmetrically.
	ball = RigidBody2D.new()
	ball.name = "ball"
	ball.position = Vector2(W * 0.5 + 7.0, 60.0)
	ball.gravity_scale = 1.0
	ball.linear_velocity = Vector2(23.0, 0.0)
	ball.contact_monitor = true
	ball.max_contacts_reported = 16
	var pm := PhysicsMaterial.new()
	pm.bounce = 0.9
	pm.friction = 0.0
	ball.physics_material_override = pm
	var cs := CollisionShape2D.new()
	var cir := CircleShape2D.new()
	cir.radius = 10.0
	cs.shape = cir
	ball.add_child(cs)
	add_child(ball)

func _wall(n: String, pos: Vector2, sz: Vector2) -> void:
	var b := StaticBody2D.new()
	b.name = n
	b.position = pos
	var cs := CollisionShape2D.new()
	var rs := RectangleShape2D.new()
	rs.size = sz
	cs.shape = rs
	b.add_child(cs)
	var pm := PhysicsMaterial.new()
	pm.bounce = 0.9
	pm.friction = 0.0
	b.physics_material_override = pm
	add_child(b)

func _peg(n: String, pos: Vector2, r: float) -> void:
	var b := StaticBody2D.new()
	b.name = n
	b.position = pos
	var cs := CollisionShape2D.new()
	var cir := CircleShape2D.new()
	cir.radius = r
	cs.shape = cir
	b.add_child(cs)
	var pm := PhysicsMaterial.new()
	pm.bounce = 0.9
	pm.friction = 0.0
	b.physics_material_override = pm
	add_child(b)

func act(_action: String) -> void:
	pass

func state() -> Dictionary:
	var bodies := []
	if ball != null and is_instance_valid(ball):
		bodies.append({
			"name": "ball",
			"pos": [ball.position.x, ball.position.y],
			"vel": [ball.linear_velocity.x, ball.linear_velocity.y],
			"angle": ball.rotation,
			"controlled": true,
			"static": false
		})
	return {"bodies": bodies, "tick": _tick}

func _physics_process(_delta: float) -> void:
	if ball == null or not is_instance_valid(ball):
		return
	_tick += 1
	# A steady sideways "draft" impulse + a per-tick position clamp: the game WRITES the body
	# transform every tick (like the certified debris-docking game). Reading the shape's
	# .global_transform mid-step flushes that pending write at a different point than serve does,
	# which is exactly what perturbed the certified replay -- so this makes the fixture bite.
	ball.apply_central_impulse(Vector2(1.3, 0.0))
	ball.position.x = clampf(ball.position.x, 20.0, W - 20.0)
	ball.position.y = clampf(ball.position.y, 20.0, H - 20.0)

func checkpoints() -> Dictionary:
	return {}

func is_success() -> bool:
	return false

func is_failure() -> bool:
	return false

func actions() -> Array:
	return ["noop"]
