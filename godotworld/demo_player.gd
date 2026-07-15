# demo_player.gd -- LOCAL, interactive player for a certified GDScript game (desktop Godot).
#
# The human-facing twin of capture_host.gd: same game load + same ZERO-CONTACT overlay
# (visual_dress.gd), but instead of writing PNGs it runs live in a desktop window so a
# person can WATCH a certified witness replay, or DRIVE the agent themselves with the
# number keys (each declared action() is bound to 1..9). This is NOT part of certification
# or capture -- it is a convenience for eyeballing a game on a machine with a real GPU.
#
# Run (needs a desktop GPU / display -- not the headless cluster):
#   godot --path godotworld -s res://demo_player.gd -- --game=<abs.gd> \
#         [--witness=<abs.json>]   # watch the certified replay
#   godot --path godotworld -s res://demo_player.gd -- --game=<abs.gd> --drive
#         # drive it yourself: keys 1..N = the actions() verbs, R = reset, Esc = quit
#
# It steps physics with the SAME discipline as the serve/capture hosts (act + K=6 frames),
# so a witness replay here matches the certified trajectory, and human play uses the same
# per-tick cadence. The game tree/physics are never mutated by the overlay.

extends SceneTree

const K_STEPS := 6
const REQUIRED_METHODS := ["build", "act", "state", "checkpoints",
	"is_success", "is_failure", "actions"]
const KEYS := [KEY_1, KEY_2, KEY_3, KEY_4, KEY_5, KEY_6, KEY_7, KEY_8, KEY_9]

var _game: Node = null
var _stage = null
var _script: GDScript = null        # compiled ONCE; re-instantiated per (re)build
var _actions_set: Array = []       # the game's declared actions()
var _witness: Array = []
var _seed := 0
var _drive := false
var _follow := false
var _applied := 0


func _initialize() -> void:
	Engine.physics_ticks_per_second = 60
	Engine.max_physics_steps_per_frame = 8
	Engine.physics_jitter_fix = 0.0
	_main()


func _main() -> void:
	var game_file := _str_arg("--game=", "")
	var witness_file := _str_arg("--witness=", "")
	_drive = _has_flag("--drive")
	_follow = _has_flag("--follow")
	if game_file == "":
		push_error("demo_player: --game=<path> is required")
		quit(2)
		return

	if witness_file != "":
		var wf := FileAccess.open(witness_file, FileAccess.READ)
		if wf != null:
			var parsed = JSON.parse_string(wf.get_as_text())
			if typeof(parsed) == TYPE_DICTIONARY:
				_seed = int(parsed.get("seed", 0))
				for v in parsed.get("actions", []):
					_witness.append(str(v))

	var gf := FileAccess.open(game_file, FileAccess.READ)
	if gf == null:
		push_error("demo_player: cannot read %s" % game_file)
		quit(2)
		return
	# Compile ONCE; every (re)build re-instantiates from this cached script.
	var gd := GDScript.new()
	gd.source_code = gf.get_as_text()
	if gd.reload() != OK or not gd.can_instantiate():
		push_error("demo_player: game failed to compile")
		quit(2)
		return
	_script = gd
	# Validate the contract on a throwaway instance (freed before it enters the tree).
	var probe = gd.new()
	if probe == null or not (probe is Node):
		push_error("demo_player: game is not a Node")
		quit(2)
		return
	for m in REQUIRED_METHODS:
		if not probe.has_method(m):
			push_error("demo_player: missing method %s" % m)
			probe.free()
			quit(2)
			return
	probe.free()

	root.title = "gi demo -- %s" % game_file.get_file()
	await _build()
	_print_controls()
	if _drive:
		await _drive_loop()
	else:
		await _replay_loop()
	quit(0)


func _build() -> void:
	if _stage != null and is_instance_valid(_stage):
		_stage.free()
		_stage = null
	if _game != null and is_instance_valid(_game):
		_game.free()
	_game = _script.new()
	root.add_child(_game)
	_game.build(_seed)
	_actions_set = _game.actions()
	# Settle t=0 and let the viewport initialise BEFORE dressing -- Camera2D/3D.make_current()
	# needs a ready viewport (mirrors capture_host.gd's pre-dress settle frame).
	await physics_frame
	var dress_script = load("res://visual_dress.gd")
	_stage = dress_script.new()
	root.add_child(_stage)
	_stage.dress(_game, {"follow": _follow})
	_applied = 0


func _print_controls() -> void:
	print("=== gi demo player ===")
	if _drive:
		print("DRIVE mode -- keys map to actions():")
		for i in range(min(_actions_set.size(), KEYS.size())):
			print("  [%d] %s" % [i + 1, str(_actions_set[i])])
		print("  [R] reset   [Esc] quit")
	else:
		print("REPLAY mode -- watching the certified witness (%d ticks). [Esc] quit" % _witness.size())


func _replay_loop() -> void:
	for i in range(_witness.size()):
		if Input.is_key_pressed(KEY_ESCAPE):
			return
		_game.act(str(_witness[i]))
		await _advance()
		if _terminal():
			break
	# hold on the final frame a moment so the end is visible
	for _h in range(90):
		if Input.is_key_pressed(KEY_ESCAPE):
			return
		await _advance_idle()


func _drive_loop() -> void:
	while true:
		if Input.is_key_pressed(KEY_ESCAPE):
			return
		if Input.is_key_pressed(KEY_R):
			await _build()
		var action := ""
		for i in range(min(_actions_set.size(), KEYS.size())):
			if Input.is_key_pressed(KEYS[i]):
				action = str(_actions_set[i])
				break
		if action != "":
			_game.act(action)
		await _advance()
		if _terminal():
			# brief pause on terminal, then auto-reset so play continues
			for _h in range(60):
				await _advance_idle()
			await _build()


func _advance() -> void:
	_applied += 1
	for k in range(K_STEPS):
		await physics_frame
	if _stage != null and is_instance_valid(_stage):
		_stage.sync()


func _advance_idle() -> void:
	for k in range(K_STEPS):
		await physics_frame
	if _stage != null and is_instance_valid(_stage):
		_stage.sync()


func _terminal() -> bool:
	if _truthy(_game.is_failure()):
		print("  -> FAILURE at tick %d" % _applied)
		return true
	if _truthy(_game.is_success()):
		print("  -> SUCCESS at tick %d" % _applied)
		return true
	return false


func _str_arg(prefix: String, dflt: String) -> String:
	for a in OS.get_cmdline_user_args():
		if a.begins_with(prefix):
			return a.substr(prefix.length())
	return dflt


func _has_flag(flag: String) -> bool:
	for a in OS.get_cmdline_user_args():
		if a == flag:
			return true
	return false


func _truthy(v) -> bool:
	if typeof(v) == TYPE_BOOL:
		return v
	if typeof(v) == TYPE_INT or typeof(v) == TYPE_FLOAT:
		return v != 0
	return v != null
