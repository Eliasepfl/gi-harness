# softlock_pit.gd -- a GameAPI-convention fixture with a GENUINE softlock (GDScript lane).
#
# The GDScript twin of the py-DSL momentum-pit (tests/test_g4.py SOFTLOCK), rebuilt so
# the trap FREEZES rather than cycles -- the shape the TRAINED stale-seeker rewards
# (a per-tick frozen fingerprint) AND the g4 stale tier / CONFIRM oracle certify.
#
# A left-to-right dash to a goal across a gap. "run" advances one cell and builds speed
# (which DECAYS one per decision tick); only a RUNNING LEAP (two runs to build speed,
# then leap at the lip, cell 2) clears the gap (cells 3-5). A slow/idle approach STEPS
# INTO the gap and is TRAPPED FOREVER: once trapped, every action pins the body to one
# fixed spot with zero velocity -- a truly frozen state, no way out. Success = reach the
# far cell (>= GOAL_CELL). The decay makes it avoidance-safe (sparse play can never build
# the speed to cross), so the ONLY hard finding is the certified softlock.
#
# A PLAIN Node2D implementing the method convention (build/act/state/checkpoints/
# is_success/is_failure/actions) -- no base class, compiles standalone. All game state
# lives in act() (the serve loop runs act() then K physics frames per decision tick, so
# state logic must NOT ride _physics_process); physics is cosmetic (a zero-gravity,
# heavily-damped body we position directly), exactly as the certified py-DSL twin.

extends Node2D

const CELL := 25.0          # px per progression cell
const ORIGIN_X := 60.0      # cell 0 world-x
const LANE_Y := 300.0
const GOAL_CELL := 24       # success cell (far side of the gap) -> the win needs >= 20
                            # decision ticks (3 to leap the gap + 18 more), clearing the
                            # funnel's anti-trivial-goal gate; a slow approach still traps.
const LEAP_LIP := 2         # the only cell a running leap launches from
const LEAP_SPEED := 5       # min speed for the leap to clear the gap
const RUN_GAIN := 10        # speed gained per run
const TRAP_POS := Vector2(240.0, 380.0)   # the fixed frozen spot once trapped

var _rng := RandomNumberGenerator.new()   # seeded from build() -- the game owns its rng
var _player: RigidBody2D = null
var _pos := 0               # progression cell
var _speed := 0             # momentum (decays each decision tick)
var _trapped := false


func build(world_seed: int) -> void:
	_rng.seed = world_seed
	# A tiny seed-stable jitter -- exercises the self-owned rng (the banned-API scan
	# forbids the unseeded global randi/randf) without perturbing determinism.
	var jitter := _rng.randf_range(-1.0, 1.0)

	_pos = 0
	_speed = 0
	_trapped = false

	_player = RigidBody2D.new()
	_player.gravity_scale = 0.0
	_player.linear_damp_mode = RigidBody2D.DAMP_MODE_REPLACE
	_player.linear_damp = 30.0                 # kill any residual drift -> a still body
	_player.lock_rotation = true
	_player.can_sleep = false
	_player.position = Vector2(ORIGIN_X + jitter, LANE_Y)
	var col := CollisionShape2D.new()
	var circ := CircleShape2D.new()
	circ.radius = 12.0
	col.shape = circ
	_player.add_child(col)
	add_child(_player)

	# A static goal marker at the far cell -> >= 2 bodies, and a real target region.
	var goal := Node2D.new()
	goal.name = "goal"
	goal.position = Vector2(ORIGIN_X + GOAL_CELL * CELL, LANE_Y)
	add_child(goal)


func _physics_process(_delta: float) -> void:
	# Physics is cosmetic: keep the body still so state() reads a clean frozen/holding
	# position. ALL game logic is in act() (decision-tick granularity).
	if _player != null:
		_player.linear_velocity = Vector2.ZERO


func _place() -> void:
	_player.position = Vector2(ORIGIN_X + _pos * CELL, LANE_Y)
	_player.linear_velocity = Vector2.ZERO


func act(action: String) -> void:
	if _player == null:
		return
	# Once trapped, EVERY action pins the body to the one fixed spot -> frozen forever.
	if _trapped:
		_player.position = TRAP_POS
		_player.linear_velocity = Vector2.ZERO
		return

	if action == "run":
		_pos += 1
		_speed += RUN_GAIN
	elif action == "leap":
		if _pos == LEAP_LIP and _speed >= LEAP_SPEED:
			_pos = LEAP_LIP + 4                # a running leap clears the gap (cells 3-5)
			_speed = 0
		else:
			_pos += 1                          # a slow leap just steps forward (into the gap)

	if _pos == 3 or _pos == 4 or _pos == 5:    # anywhere in the gap -> fall in, no way out
		_trapped = true
		_player.position = TRAP_POS
		_player.linear_velocity = Vector2.ZERO
	else:
		_place()

	_speed = max(0, _speed - 1)                 # momentum decays one per decision tick


func state() -> Dictionary:
	return {
		"bodies": [{
			"name": "player",
			"pos": [_player.position.x, _player.position.y],
			"vel": [_player.linear_velocity.x, _player.linear_velocity.y],
			"angle": _player.rotation,
			"controlled": true,
			"static": false,
		}, {
			"name": "goal",
			"pos": [ORIGIN_X + GOAL_CELL * CELL, LANE_Y],
			"vel": [0.0, 0.0],
			"angle": 0.0,
			"controlled": false,
			"static": true,
		}],
		"flags": {"lip": _pos >= LEAP_LIP, "crossed": _pos >= 6},
	}


func checkpoints() -> Dictionary:
	return {"lip": _pos >= LEAP_LIP, "crossed": _pos >= 6}


func is_success() -> bool:
	return _pos >= GOAL_CELL


func is_failure() -> bool:
	return false


func actions() -> Array:
	return ["run", "leap"]
