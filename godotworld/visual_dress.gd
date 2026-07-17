# visual_dress.gd -- render-only, ZERO-CONTACT visual dresser for the capture lane.
#
# Turns a bare, certified GameAPI game (plain RigidBody/StaticBody/Area + CollisionShape
# nodes, NO visuals) into a demo-able scene WITHOUT regenerating it and WITHOUT ever
# touching the game's own tree or physics.
#
# THE SAFETY INVARIANT (Elias' hard rule):
#   The dresser NEVER adds a child to the game, never adds a physics node (no bodies,
#   no shapes, no joints), never mutates the game in any way. All visuals live in a
#   SEPARATE overlay subtree (this node), a SIBLING of the game root. Each rendered
#   frame we READ every body's global transform and MIRROR it onto a purely-visual
#   proxy (Polygon2D/Line2D in 2D, MeshInstance3D in 3D). Because the game tree is
#   only ever read, a DRESSED replay and an UNDRESSED replay produce byte-identical
#   state() trails by construction -- proven by the capture-lane identity test.
#
# 3D games may additionally have their proxies dressed with REAL low-poly bank assets
# (MISSION step 1): when route_assets mapped a body to an asset id, the primitive proxy
# is replaced by an AssetLoader-loaded, render-only model scaled to the body's collision
# AABB. The asset carries ZERO physics nodes (AssetLoader strips them) and lives in the
# overlay subtree exactly like the primitive it replaces -- the zero-contact contract and
# the state-trail identity are unchanged. A null asset load falls back to the primitive.
# 2D games are ALWAYS presented flat (no 2.5D): a 2D game stays 2D.
#
# Reused by BOTH the headless capture host (capture_host.gd) and the desktop local
# player (demo_player.gd). Pure engine nodes only -> works in headless-x11 software GL
# and in a normal desktop Godot window alike. No external assets required (the bank
# assets are an optional visual upgrade; the primitive path always renders).
#
# RESPECTING AUTHORSHIP (dress_mode, default "auto")
#   The dresser was written for a BARE game and used to treat every game as bare: it recoloured
#   every body from the 4-role palette, stamped the same procedural sky + ground + sun on every
#   3D scene, and make_current()'d its own camera -- so a game that had authored an arena floor,
#   glowing posts and a red guard rendered as identical orange/teal/blue-gray boxes under a
#   generic sky, framed by a camera its author never chose. 14 of the 22 certified games author
#   real fiction-specific visuals; all of it was being discarded or z-fought.
#
#   dress() now takes a read-only VISNODE CENSUS of the game tree first (_census_node, folded
#   into the existing _collect_shapes walk) and only fills in what the game did NOT bring:
#     * a body that draws its own visual keeps it     -> no proxy, no recolour
#     * a game that authored a Camera2D/Camera3D      -> we build no camera and steal no frame
#     * a game that authored a light / environment    -> we stamp neither over it
#     * a game that painted its own world (scene-level visual or its own env) -> no backdrop
#   A game that authored NOTHING takes exactly the old path, node for node -- the ~8 genuinely
#   bare games depend on it.
#
#   dress_mode: "auto" (default; the above) | "proxy" (legacy: dress everything, ignore
#   authorship) | "respect" (strict: draw nothing the game did not ask for). Also settable via
#   the HARNESS_DRESS_MODE env var (opts win), so any lane can force the old look.
#
# THE CENSUS IS READ-ONLY, like everything else here: it reads node classes and their draw
# properties and never writes. In particular it NEVER calls make_current() on the game's own
# camera -- the game's camera is already current by virtue of being the only one in the tree;
# we simply stop overriding it.
#
# API:
#   var stage := load("res://visual_dress.gd").new()
#   parent.add_child(stage)                 # a SIBLING of the game, never a child of it
#   stage.dress(game_root, {follow=false, assets={...}, manifest_path="...", dress_mode="auto"})
#   ... each rendered frame ...
#   stage.sync()                            # mirror transforms (read-only on the game)

extends Node

# ---- palette (distinct, pleasant, role-differentiated) -------------------- #
const COL_BG_2D := Color(0.106, 0.121, 0.165)          # deep slate backdrop
const COL_CONTROLLED := Color(0.98, 0.58, 0.16)        # warm orange -> the agent pops
const COL_DYNAMIC := Color(0.24, 0.78, 0.72)           # teal -> other movers
const COL_STATIC := Color(0.44, 0.49, 0.60)            # muted blue-gray -> walls/structure
const COL_STATIC_LINE := Color(0.64, 0.69, 0.80)       # brighter edge for thin walls
const COL_SENSOR := Color(0.36, 0.86, 0.48)            # green -> goals/checkpoints/gates
const COL_GROUND_3D := Color(0.28, 0.32, 0.38)         # 3D floor
const COL_OUTLINE := Color(0, 0, 0, 0.35)

const CIRCLE_SEGMENTS := 24
const ASSET_POS_TOL := 30.0         # t=0 body<->state position match tolerance (asset routing)
const ELEV_3D := 38.0               # 3D overview elevation above the play plane (deg; not top-down)

# ---- 3D follow-cam rig ----------------------------------------------------- #
# A chase cam trailing the controlled body along its TRAVEL direction, modelled on the
# godot_rl HovercraftRacing car.tscn rig (Camera3D at y=0.94 up, z=-3.37 back, pitch 12.6deg):
# ~1 body-height up, ~2.5-3.5 body-lengths back, ~12-15deg pitch down. Every distance scales
# off the CONTROLLED body's AABB, but a MINIMUM ABSOLUTE distance keeps a tiny body from
# gluing the camera to itself (the "on ne la voit pas trop" bug). The offset points BEHIND
# the travel direction -- the old rig sat on local +Z and faced backwards, hiding the path
# ahead entirely on a +Z-moving craft.
const FOLLOW_CAM_DIST := 3.0        # default chase multiplier (body-lengths back); --cam-dist overrides
const FOLLOW_MIN_BACK := 8.0        # absolute floor on the back distance (tiny-body guard)
const FOLLOW_MIN_UP := 3.0          # absolute floor on the rise
const FOLLOW_UP_FRAC := 0.30        # rise as a fraction of the back distance (keeps the pitch sane)
const FOLLOW_PITCH_DEG := 14.0      # aim pitched down (12-15deg band, per the car rig)
const FOLLOW_FOV := 65.0            # wider than the overview so the craft AND the path ahead read
const FOLLOW_CLAMP_MARGIN := 0.75   # keep the cam this far inside the flyable box (anti-ceiling-pop)
const WALL_LINE_WIDTH := 6.0
const MARGIN_FRAC := 0.12           # fit-to-scene padding (fraction of extent)
const SENSOR_ALPHA := 0.28
const Z_BG := -100
const Z_STATIC := -10
const Z_SENSOR := -5
const Z_DYNAMIC := 10

# ---- dress modes ----------------------------------------------------------- #
const MODE_AUTO := "auto"            # respect what the game authored, fill what's missing
const MODE_PROXY := "proxy"          # legacy: proxy/recolour everything, own the camera
const MODE_RESPECT := "respect"      # strict: the game presents itself, we add nothing

# ---- discovered state ----------------------------------------------------- #
var _pairs: Array = []               # [{src: Node(shape), proxy: Node}]
var _is_3d := false
# ---- visnode census (what the GAME authored for itself; read-only) --------- #
var _dress_mode := MODE_AUTO
var _authored_bodies := {}           # body instance-id -> true (this body draws its own visual)
var _authored_root_visual := false   # a scene-level visual: the game painted its own world
var _authored_camera := false        # the game brought its own Camera2D/Camera3D
var _authored_light := false         # the game brought its own Light3D
var _authored_env := false           # the game brought its own WorldEnvironment
var _controlled_ext_set := false
var _assets_norm: Dictionary = {}    # normalised-body-name -> bank asset id (from route_assets)
var _manifest_path := ""             # abs/res:// path to assets/manifest.json (AssetLoader source)
var _asset_by_body: Dictionary = {}  # body instance-id -> resolved asset id (t=0 matched)
var _follow := false
var _camera = null                   # Camera2D or Camera3D
var _controlled_body = null          # the controlled body node (follow target)
var _controlled_proxy = null         # its 3D proxy (the follow cam rides this, zero-contact)
var _controlled_ext := Vector3.ONE   # its collision half-extents (chase-offset scale)
var _stage2d: Node2D = null
var _stage3d: Node3D = null
# world-space bounds of the whole scene at t=0 (for fit-to-scene framing)
var _min := Vector3(INF, INF, INF)
var _max := Vector3(-INF, -INF, -INF)
var _base_zoom := 1.0
var _view_w := 960.0
var _view_h := 540.0
# camera-framing hints (opts, else capture.py's pre-scanned-trajectory env) -- render-only
var _cam_dist := FOLLOW_CAM_DIST
var _has_traj := false
var _traj_min := Vector3.ZERO
var _traj_max := Vector3.ZERO
var _has_fwd := false
var _traj_fwd := Vector3(0.0, 0.0, 1.0)   # controlled body's travel direction (fallback: +Z)
var _follow_offset := Vector3.ZERO        # world-space body->camera chase offset


# =========================================================================== #
# Public API
# =========================================================================== #
func dress(game_root: Node, opts := {}) -> void:
	_follow = bool(opts.get("follow", false))
	_view_w = float(opts.get("view_w", 960.0))
	_view_h = float(opts.get("view_h", 540.0))
	_is_3d = game_root is Node3D
	# Optional bank-asset dressing for the 3D path (cosmetic; see MISSION step 1). A 2D game
	# ignores these -- it is always presented flat.
	_manifest_path = str(opts.get("manifest_path", ""))
	_build_assets_norm(opts.get("assets", {}))
	_read_cam_opts(opts)
	_read_dress_mode(opts)

	# 1. Discover every collision shape in the game tree (READ-ONLY walk), taking the visnode
	#    census on the way: what did this game already author for itself?
	var shapes: Array = []
	_census_node(game_root)          # the root itself may be the scene's own visual
	_collect_shapes(game_root, shapes)

	# 2. Identify the controlled body by matching state()'s controlled entry to a body.
	var ctrl_pos = _controlled_pos_from_state(game_root)
	_assign_roles(shapes, ctrl_pos)

	# 2b. Resolve each body's bank asset (3D only), matching nodes to state() names by t=0 pos.
	if _is_3d:
		_resolve_assets(game_root, shapes)

	# 3. Build the overlay proxies + framing under a fresh stage subtree (a sibling).
	if _is_3d:
		_build_3d(shapes)
	else:
		_build_2d(shapes)
		_precompute_mirror_2d()

	# 4. Camera framing (fit-to-scene overview, or follow the controlled body).
	_setup_camera()
	# Prime proxy transforms so the very first captured frame is already aligned.
	sync()


func sync() -> void:
	# Mirror each body's current pose onto its visual proxy. READ-ONLY on the game tree -- the
	# whole zero-contact contract. Called once per captured frame by the host; never steps physics.
	#
	# 2D mirrors from the body's STORED .position/.rotation (via _mirror_xform_2d) rather than
	# reading the shape's .global_transform: a .global_transform read mid-physics forces a
	# transform-notification flush that perturbs a float-sensitive replay (a debris-threading game
	# docks at tick 275 but the flush diverged it into a crash at ~130). state()'s own reads are
	# .position/.rotation for exactly this reason. 3D keeps the direct .global_transform read (its
	# asset mounts carry a fit-scale the compose path would drop; the 3D follow-cam is unchanged).
	for p in _pairs:
		var proxy = p["proxy"]
		if not is_instance_valid(proxy):
			continue
		if _is_3d:
			var src = p["src"]
			if is_instance_valid(src):
				proxy.global_transform = src.global_transform
		else:
			proxy.global_transform = _mirror_xform_2d(p)
	if _follow and _camera != null and is_instance_valid(_camera) \
			and _controlled_body != null and is_instance_valid(_controlled_body):
		# The chase camera trails the controlled body at a FIXED WORLD offset (its orientation
		# was baked at setup). Read-only on the game tree, so the zero-contact contract holds.
		if _is_3d:
			_camera.global_position = _follow_pose()
		else:
			_camera.global_position = (_controlled_body as Node2D).position


func _precompute_mirror_2d() -> void:
	# Cache, per 2D pair, the data to reproduce the shape's world transform each frame from the
	# body's STORED .position/.rotation alone -- so sync() never reads .global_transform mid-physics.
	# The .global_transform reads HERE run ONCE at t=0 (pre-stepping), so they are safe. The
	# reconstruction proxy = parent_global(t0) * Transform2D(body.rotation, body.position) *
	# shape_rel is EXACT for a body whose ancestors are static (every GameAPI 2D game: bodies sit
	# under a fixed game root). A body with moving ancestors keeps its cache empty and falls back
	# to the direct read in _mirror_xform_2d.
	for p in _pairs:
		var src = p["src"]
		if not is_instance_valid(src) or not (src is Node2D):
			continue
		var body := _owning_body(src)
		if not (body is Node2D):
			continue
		var par = (body as Node2D).get_parent()
		var par_g := (par as Node2D).global_transform if (par is Node2D) else Transform2D()
		var body_g: Transform2D = par_g * Transform2D(body.rotation, body.position)
		p["m_body"] = body
		p["m_par"] = par_g
		p["m_rel"] = body_g.affine_inverse() * (src as Node2D).global_transform


func _mirror_xform_2d(p: Dictionary) -> Transform2D:
	# Non-perturbing 2D mirror: compose the shape's world transform from the body's stored
	# .position/.rotation and the t=0-cached parent + shape-relative transforms.
	var body = p.get("m_body", null)
	if body != null and is_instance_valid(body) and body is Node2D:
		return (p["m_par"] as Transform2D) \
			* Transform2D(body.rotation, body.position) * (p["m_rel"] as Transform2D)
	var src = p["src"]      # fallback: no cache (moving ancestors / malformed) -> direct read
	if is_instance_valid(src) and src is Node2D:
		return (src as Node2D).global_transform
	return Transform2D()


# =========================================================================== #
# Discovery (read-only walk of the game tree)
# =========================================================================== #
func _collect_shapes(node: Node, out: Array) -> void:
	# Depth-first: every CollisionShape2D/3D (with a real shape) or CollisionPolygon2D
	# whose owning body we can name. We record the SHAPE node (its global transform is
	# what moves with the body, honouring any per-shape offset) + its owning body.
	# The same walk takes the visnode census (read-only) -- one pass over the tree.
	for child in node.get_children():
		var body := _owning_body(child)
		if child is CollisionShape2D and child.shape != null:
			out.append({"shape": child, "body": body, "kind": "2d_shape"})
		elif child is CollisionPolygon2D and child.polygon.size() >= 3:
			out.append({"shape": child, "body": body, "kind": "2d_poly"})
		elif child is CollisionShape3D and child.shape != null:
			out.append({"shape": child, "body": body, "kind": "3d_shape"})
		_census_node(child)
		_collect_shapes(child, out)


func _owning_body(shape_node: Node) -> Node:
	var p := shape_node.get_parent()
	while p != null:
		if p is CollisionObject2D or p is CollisionObject3D:
			return p
		p = p.get_parent()
	return shape_node


# =========================================================================== #
# Visnode census -- what the GAME authored for itself (READ-ONLY)
# =========================================================================== #
func _census_node(n: Node) -> void:
	# Tally one node of the game tree into the census. Cameras and lights/environments are
	# their own buckets (they are not "visuals" to be proxied); everything that actually draws
	# is attributed to its owning body, or to the scene when it hangs at game-root level.
	if n is Camera2D or n is Camera3D:
		_authored_camera = true
		return
	if n is Light3D:
		_authored_light = true
		return
	if n is WorldEnvironment:
		_authored_env = true
		return
	if not _draws_something(n):
		return
	var body := _owning_body_or_null(n)
	if body != null:
		_authored_bodies[body.get_instance_id()] = true
	else:
		# A visual with no owning body: the game's own backdrop / terrain / decor.
		_authored_root_visual = true


func _draws_something(n: Node) -> bool:
	# Does this node put REAL pixels on screen? Deliberately STRICT: an empty MeshInstance3D or
	# a degenerate Polygon2D draws nothing, and must NOT suppress the proxy that is the only
	# thing making that body visible. Anything not listed here (particles, TileMap, ...) simply
	# reads as "not authored" and keeps the old proxy path -- a safe default, never a regression.
	if n is MeshInstance3D:
		return (n as MeshInstance3D).mesh != null
	if n is MultiMeshInstance3D:
		return (n as MultiMeshInstance3D).multimesh != null
	if n is CSGShape3D:
		return true
	if n is SpriteBase3D:            # Sprite3D / AnimatedSprite3D / Label3D
		return true
	if n is Polygon2D:
		return (n as Polygon2D).polygon.size() >= 3
	if n is Line2D:
		return (n as Line2D).points.size() >= 2
	if n is Sprite2D:
		return (n as Sprite2D).texture != null
	if n is AnimatedSprite2D:
		return (n as AnimatedSprite2D).sprite_frames != null
	if n is ColorRect or n is TextureRect:
		return true
	return false


func _owning_body_or_null(n: Node) -> Node:
	# The CollisionObject that OWNS this node (nearest body ancestor), or null when it hangs at
	# scene level. Distinct from _owning_body(), which returns the node itself as a fallback --
	# here the difference between "this body's art" and "the world's art" is the whole point.
	var p := n.get_parent()
	while p != null:
		if p is CollisionObject2D or p is CollisionObject3D:
			return p
		p = p.get_parent()
	return null


func _read_dress_mode(opts: Dictionary) -> void:
	# opts win over the env var; anything unrecognised falls back to "auto" (never crash a demo
	# over a typo'd knob).
	var m := str(opts.get("dress_mode", ""))
	if m == "":
		m = OS.get_environment("HARNESS_DRESS_MODE")
	if m != MODE_AUTO and m != MODE_PROXY and m != MODE_RESPECT:
		m = MODE_AUTO
	_dress_mode = m


func census() -> Dictionary:
	# Exposed for the host's logging + the dress tests: what the game brought of its own, and
	# therefore what we did NOT stamp over it.
	return {"mode": _dress_mode, "authored_bodies": _authored_bodies.size(),
		"root_visual": _authored_root_visual, "camera": _authored_camera,
		"light": _authored_light, "env": _authored_env}


# ---- census -> decisions (the ONLY places the census changes behaviour) ---- #
func _should_proxy(rec: Dictionary) -> bool:
	# A body that painted its own visual keeps it: the authored art IS the visual, and a
	# palette-coloured proxy on top would z-fight it and flatten the fiction.
	if _dress_mode == MODE_PROXY:
		return true
	if _dress_mode == MODE_RESPECT:
		return false
	var body = rec["body"]
	if body == null or not is_instance_valid(body):
		return true
	return not _authored_bodies.has(body.get_instance_id())


func _should_stamp_env() -> bool:
	if _dress_mode == MODE_PROXY:
		return true
	if _dress_mode == MODE_RESPECT:
		return false
	return not _authored_env


func _should_stamp_light() -> bool:
	if _dress_mode == MODE_PROXY:
		return true
	if _dress_mode == MODE_RESPECT:
		return false
	return not _authored_light


func _should_stamp_scene() -> bool:
	# The generic backdrop (2D slate rectangle / 3D ground quad). A game that painted its own
	# world -- scene-level visual, or its own environment/sky -- has chosen its background;
	# a game that only skinned its BODIES still wants a floor under them ("fill what's missing").
	if _dress_mode == MODE_PROXY:
		return true
	if _dress_mode == MODE_RESPECT:
		return false
	return not (_authored_root_visual or _authored_env)


func _should_own_camera() -> bool:
	# make_current() on our camera is what threw away every authored camera in the library.
	# When the game brought one, it is already the viewport's current camera; we build none and
	# leave theirs entirely alone (zero-contact).
	if _dress_mode == MODE_PROXY:
		return true
	if _dress_mode == MODE_RESPECT:
		return false
	return not _authored_camera


func _controlled_pos_from_state(game_root: Node):
	# Read the controlled body's t=0 position from the game's own state() (a PURE query
	# per the GameAPI contract). Returns a Vector3 (z=0 for 2D) or null. Used only to
	# pick which proxy is highlighted as the agent; never mutates anything.
	if not game_root.has_method("state"):
		return null
	var st = game_root.state()
	if typeof(st) != TYPE_DICTIONARY:
		return null
	var bodies = st.get("bodies", [])
	if typeof(bodies) != TYPE_ARRAY:
		return null
	for b in bodies:
		if typeof(b) != TYPE_DICTIONARY or not bool(b.get("controlled", false)):
			continue
		var pos = b.get("pos", [])
		if typeof(pos) == TYPE_ARRAY and pos.size() >= 2:
			var z := 0.0
			if pos.size() >= 3:
				z = float(pos[2])
			return Vector3(float(pos[0]), float(pos[1]), z)
	return null


func _assign_roles(shapes: Array, ctrl_pos) -> void:
	# Tag each shape record with a role: sensor / static / dynamic / controlled.
	# controlled = the dynamic body whose t=0 position best matches state()'s controlled
	# entry (robust to name mismatches like "Cart" vs "cart").
	var best_body = null
	var best_d := INF
	for rec in shapes:
		var body = rec["body"]
		var role := _base_role(body)
		rec["role"] = role
		if role == "dynamic" and ctrl_pos != null and is_instance_valid(body):
			var d := _body_pos3(body).distance_to(ctrl_pos)
			if d < best_d:
				best_d = d
				best_body = body
	# Mark all shapes of the matched controlled body.
	if best_body != null:
		_controlled_body = best_body
		for rec in shapes:
			if rec["body"] == best_body:
				rec["role"] = "controlled"
	else:
		# Fallback: first dynamic body is the agent.
		for rec in shapes:
			if rec["role"] == "dynamic":
				_controlled_body = rec["body"]
				break


func _base_role(body: Node) -> String:
	if body is Area2D or body is Area3D:
		return "sensor"
	if body is StaticBody2D or body is StaticBody3D:
		return "static"
	return "dynamic"


func _body_pos3(body: Node) -> Vector3:
	if body is Node3D:
		var p: Vector3 = body.global_position
		return p
	if body is Node2D:
		var q: Vector2 = body.global_position
		return Vector3(q.x, q.y, 0.0)
	return Vector3.ZERO


# =========================================================================== #
# 2D build
# =========================================================================== #
func _build_2d(shapes: Array) -> void:
	_stage2d = Node2D.new()
	_stage2d.name = "DemoStage2D"
	add_child(_stage2d)

	# backdrop is added after bounds are known (below); collect proxies first.
	var ctrl_shape = null
	for rec in shapes:
		# Bounds come from COLLISION geometry and frame the whole scene, so they are taken for
		# every body -- including one we leave to its own authored art.
		_expand_bounds_2d(rec)
		if not _should_proxy(rec):
			continue
		var proxy = _make_2d_proxy(rec)
		if proxy == null:
			continue
		_stage2d.add_child(proxy)
		_pairs.append({"src": rec["shape"], "proxy": proxy})
		if rec["role"] == "controlled" and ctrl_shape == null:
			ctrl_shape = rec["shape"]

	if not _bounds_valid():
		_min = Vector3(0, 0, 0)
		_max = Vector3(_view_w, _view_h, 0)

	# Backdrop rectangle behind everything. Oversized well past the scene so it fills the
	# whole camera view (no gray letterbox band) in overview OR follow framing -- it is a
	# single flat polygon, so the size is free. Skipped for a game that painted its own world
	# (the lander's brown crater terrain, the platformers' ColorRect skies).
	if _should_stamp_scene():
		var bg := Polygon2D.new()
		bg.name = "Backdrop"
		var cx := (_min.x + _max.x) * 0.5
		var cy := (_min.y + _max.y) * 0.5
		var big: float = max(_max.x - _min.x, _max.y - _min.y) * 2.0 + max(_view_w, _view_h)
		bg.polygon = PackedVector2Array([
			Vector2(cx - big, cy - big), Vector2(cx + big, cy - big),
			Vector2(cx + big, cy + big), Vector2(cx - big, cy + big)])
		bg.color = COL_BG_2D
		bg.z_index = Z_BG
		_stage2d.add_child(bg)

	# A soft translucent halo tracking the agent so it stays legible in a wide overview
	# (the true-size body polygon is still drawn on top -- the halo never misrepresents
	# the collision size). Radius scales with the course, not the tiny body.
	# ctrl_shape is set only when the agent was PROXIED, so an agent the game drew itself (the
	# lander's yellow probe) keeps its own colour instead of wearing our orange halo.
	if ctrl_shape != null:
		var span2: float = max(_max.x - _min.x, _max.y - _min.y)
		var halo_r: float = max(span2 * 0.02, 22.0)
		var halo := Polygon2D.new()
		halo.polygon = _circle_pts(halo_r)
		halo.color = Color(COL_CONTROLLED.r, COL_CONTROLLED.g, COL_CONTROLLED.b, 0.22)
		halo.z_index = Z_DYNAMIC + 3
		_stage2d.add_child(halo)
		_pairs.append({"src": ctrl_shape, "proxy": halo})


func _make_2d_proxy(rec: Dictionary):
	var role: String = rec["role"]
	var fill := _role_fill(role)
	var shape_node = rec["shape"]

	if rec["kind"] == "2d_poly":
		var poly := Polygon2D.new()
		poly.polygon = shape_node.polygon
		poly.color = fill
		poly.z_index = _role_z(role)
		return poly

	var shape = shape_node.shape
	if shape is RectangleShape2D:
		var hs: Vector2 = shape.size * 0.5
		return _poly2d(PackedVector2Array([
			Vector2(-hs.x, -hs.y), Vector2(hs.x, -hs.y),
			Vector2(hs.x, hs.y), Vector2(-hs.x, hs.y)]), fill, role)
	elif shape is CircleShape2D:
		return _poly2d(_circle_pts(shape.radius), fill, role)
	elif shape is CapsuleShape2D:
		return _poly2d(_capsule_pts_2d(shape.radius, shape.height), fill, role)
	elif shape is SegmentShape2D:
		var line := Line2D.new()
		line.points = PackedVector2Array([shape.a, shape.b])
		line.width = WALL_LINE_WIDTH
		line.default_color = COL_STATIC_LINE if role == "static" else fill
		line.begin_cap_mode = Line2D.LINE_CAP_ROUND
		line.end_cap_mode = Line2D.LINE_CAP_ROUND
		line.z_index = _role_z(role)
		return line
	elif shape is WorldBoundaryShape2D:
		# An infinite floor line; draw a long segment along its normal-perpendicular.
		var n: Vector2 = shape.normal
		var tang := Vector2(-n.y, n.x)
		var d: float = shape.distance
		var c := n * d
		var line2 := Line2D.new()
		line2.points = PackedVector2Array([c - tang * 5000.0, c + tang * 5000.0])
		line2.width = WALL_LINE_WIDTH
		line2.default_color = COL_STATIC_LINE
		line2.z_index = _role_z(role)
		return line2
	elif shape is ConvexPolygonShape2D and shape.points.size() >= 3:
		return _poly2d(shape.points, fill, role)
	# Fallback: a small marker box.
	return _poly2d(PackedVector2Array([
		Vector2(-10, -10), Vector2(10, -10), Vector2(10, 10), Vector2(-10, 10)]),
		fill, role)


func _poly2d(pts: PackedVector2Array, fill: Color, role: String) -> Polygon2D:
	var poly := Polygon2D.new()
	poly.polygon = pts
	poly.color = fill
	poly.z_index = _role_z(role)
	# A subtle outline via a Line2D child (closes the loop).
	var outline := Line2D.new()
	var loop := PackedVector2Array(pts)
	loop.append(pts[0])
	outline.points = loop
	outline.width = 2.0
	outline.default_color = _role_outline(role)
	poly.add_child(outline)
	return poly


func _circle_pts(r: float) -> PackedVector2Array:
	var pts := PackedVector2Array()
	for i in range(CIRCLE_SEGMENTS):
		var a := TAU * float(i) / float(CIRCLE_SEGMENTS)
		pts.append(Vector2(cos(a), sin(a)) * r)
	return pts


func _capsule_pts_2d(r: float, h: float) -> PackedVector2Array:
	# A vertical capsule (Godot 2D capsule extends along local Y).
	var pts := PackedVector2Array()
	var half: float = max(0.0, h * 0.5 - r)
	var steps := CIRCLE_SEGMENTS / 2
	for i in range(steps + 1):
		var a := PI * float(i) / float(steps)
		pts.append(Vector2(cos(a) * r, -half - sin(a) * r))
	for i in range(steps + 1):
		var a2 := PI * float(i) / float(steps)
		pts.append(Vector2(-cos(a2) * r, half + sin(a2) * r))
	return pts


func _expand_bounds_2d(rec: Dictionary) -> void:
	var shape_node = rec["shape"]
	var xf: Transform2D = shape_node.global_transform
	var local_pts := _shape_extent_pts_2d(rec)
	for lp in local_pts:
		var wp: Vector2 = xf * lp
		_min.x = min(_min.x, wp.x)
		_min.y = min(_min.y, wp.y)
		_max.x = max(_max.x, wp.x)
		_max.y = max(_max.y, wp.y)


func _shape_extent_pts_2d(rec: Dictionary) -> Array:
	if rec["kind"] == "2d_poly":
		var arr := []
		for p in rec["shape"].polygon:
			arr.append(p)
		return arr
	var shape = rec["shape"].shape
	if shape is RectangleShape2D:
		var hs: Vector2 = shape.size * 0.5
		return [Vector2(-hs.x, -hs.y), Vector2(hs.x, hs.y),
				Vector2(hs.x, -hs.y), Vector2(-hs.x, hs.y)]
	elif shape is CircleShape2D:
		var r: float = shape.radius
		return [Vector2(-r, -r), Vector2(r, r)]
	elif shape is CapsuleShape2D:
		var rr: float = shape.radius
		var hh: float = shape.height * 0.5
		return [Vector2(-rr, -hh), Vector2(rr, hh)]
	elif shape is SegmentShape2D:
		return [shape.a, shape.b]
	elif shape is ConvexPolygonShape2D:
		var a2 := []
		for p in shape.points:
			a2.append(p)
		return a2
	return [Vector2(-10, -10), Vector2(10, 10)]


# =========================================================================== #
# 3D build
# =========================================================================== #
func _build_3d(shapes: Array) -> void:
	_stage3d = Node3D.new()
	_stage3d.name = "DemoStage3D"
	add_child(_stage3d)

	# Environment + sky + ambient (a clean, lit backdrop -- godot_rl examples look). Stamped
	# only over a game that brought no sky of its own: the arena shooter and the ring courses
	# author a WorldEnvironment + sun deliberately, and ours used to bury both.
	if _should_stamp_env():
		var we := WorldEnvironment.new()
		var env := Environment.new()
		env.background_mode = Environment.BG_SKY
		var sky := Sky.new()
		var sky_mat := ProceduralSkyMaterial.new()
		sky_mat.sky_top_color = Color(0.35, 0.52, 0.78)
		sky_mat.sky_horizon_color = Color(0.70, 0.78, 0.86)
		sky_mat.ground_bottom_color = Color(0.24, 0.26, 0.30)
		sky_mat.ground_horizon_color = Color(0.55, 0.60, 0.66)
		sky.sky_material = sky_mat
		env.sky = sky
		env.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
		env.ambient_light_energy = 0.6
		we.environment = env
		_stage3d.add_child(we)

	if _should_stamp_light():
		var sun := DirectionalLight3D.new()
		sun.rotation = Vector3(deg_to_rad(-55.0), deg_to_rad(-40.0), 0.0)
		sun.light_energy = 1.1
		sun.shadow_enabled = true
		_stage3d.add_child(sun)

	for rec in shapes:
		# Bounds/extents come from COLLISION geometry and frame the whole scene, so they are
		# taken for every body -- including one we leave to its own authored art.
		_expand_bounds_3d(rec)
		if rec["role"] == "controlled" and not _controlled_ext_set:
			_controlled_ext = _shape_half_extent_3d(rec["shape"].shape)
			_controlled_ext_set = true
		if not _should_proxy(rec):
			continue
		var proxy = _make_3d_proxy(rec)
		if proxy == null:
			continue
		_stage3d.add_child(proxy)
		_pairs.append({"src": rec["shape"], "proxy": proxy})
		if rec["role"] == "controlled" and _controlled_proxy == null:
			_controlled_proxy = proxy

	if not _bounds_valid():
		_min = Vector3(0, 0, 0)
		_max = Vector3(_view_w, _view_h, 100)

	# A backdrop plane at the FAR end of the scene's thinnest ("depth") axis -- it grounds
	# the scene without ever occluding the bodies (the camera views from the near side of
	# that axis). Orientation is derived from the AABB, so it works for any 3D layout.
	# Skipped for a game that painted its own world (the arena's floor + sky).
	if _should_stamp_scene():
		var span := _max - _min
		var center := (_min + _max) * 0.5
		var depth := _thin_axis_dir(span)
		var big3: float = span.length() + max(_view_w, _view_h)
		var floor_mi := MeshInstance3D.new()
		var qm := QuadMesh.new()
		qm.size = Vector2(big3, big3)
		floor_mi.mesh = qm
		var gmat := StandardMaterial3D.new()
		gmat.albedo_color = COL_GROUND_3D
		gmat.roughness = 0.95
		gmat.cull_mode = BaseMaterial3D.CULL_DISABLED
		floor_mi.mesh.surface_set_material(0, gmat)
		# QuadMesh faces +Z by default; aim its normal along +depth (toward the camera side).
		floor_mi.position = center - depth * (span.length() * 0.5 + 60.0)
		floor_mi.look_at_from_position(floor_mi.position, floor_mi.position + depth,
			_up_for(depth))
		_stage3d.add_child(floor_mi)


func _make_3d_proxy(rec: Dictionary):
	var role: String = rec["role"]
	# 1. If this body routed to a bank asset, dress its PROXY with the render-only model
	#    (scaled to the collision AABB, base-anchored so it grounds on the body's floor).
	#    The asset lives under a mount whose transform is mirrored each frame; the mount stays
	#    orthonormal so the asset's own fit-scale is preserved by sync()'s global_transform.
	var aid := _asset_id_for_rec(rec)
	if aid != "":
		var ext := _shape_half_extent_3d(rec["shape"].shape)
		var asset = _load_asset(aid, ext * 2.0, "base")
		if asset != null:
			var mount := Node3D.new()
			mount.name = "AssetMount"
			asset.position.y = -ext.y   # asset base at the shape's bottom (mount at centre)
			mount.add_child(asset)
			return mount

	# 2. Fallback: the primitive proxy mirroring the collision shape.
	var shape = rec["shape"].shape
	var mesh: Mesh = null
	if shape is BoxShape3D:
		var bm := BoxMesh.new()
		bm.size = shape.size
		mesh = bm
	elif shape is SphereShape3D:
		var sm := SphereMesh.new()
		sm.radius = shape.radius
		sm.height = shape.radius * 2.0
		mesh = sm
	elif shape is CapsuleShape3D:
		var cm := CapsuleMesh.new()
		cm.radius = shape.radius
		cm.height = shape.height
		mesh = cm
	elif shape is CylinderShape3D:
		var cy := CylinderMesh.new()
		cy.top_radius = shape.radius
		cy.bottom_radius = shape.radius
		cy.height = shape.height
		mesh = cy
	else:
		var fb := BoxMesh.new()
		fb.size = Vector3(20, 20, 20)
		mesh = fb
	var mi := MeshInstance3D.new()
	mi.mesh = mesh
	mi.mesh.surface_set_material(0, _mesh_material(role))
	return mi


func _mesh_material(role: String) -> StandardMaterial3D:
	# Role-differentiated PBR material for a primitive 3D proxy.
	var mat := StandardMaterial3D.new()
	var fill := _role_fill(role)
	mat.albedo_color = fill
	mat.roughness = 0.6
	if role == "sensor":
		mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		mat.albedo_color.a = SENSOR_ALPHA
	elif role == "controlled":
		# A gentle glow so the agent reads at a glance in the lit 3D scene.
		mat.emission_enabled = true
		mat.emission = fill
		mat.emission_energy_multiplier = 0.35
	return mat


func _expand_bounds_3d(rec: Dictionary) -> void:
	var shape_node = rec["shape"]
	var xf: Transform3D = shape_node.global_transform
	var ext := _shape_half_extent_3d(rec["shape"].shape)
	for sx in [-1.0, 1.0]:
		for sy in [-1.0, 1.0]:
			for sz in [-1.0, 1.0]:
				var wp: Vector3 = xf * Vector3(ext.x * sx, ext.y * sy, ext.z * sz)
				_min.x = min(_min.x, wp.x); _min.y = min(_min.y, wp.y); _min.z = min(_min.z, wp.z)
				_max.x = max(_max.x, wp.x); _max.y = max(_max.y, wp.y); _max.z = max(_max.z, wp.z)


func _shape_half_extent_3d(shape) -> Vector3:
	if shape is BoxShape3D:
		return shape.size * 0.5
	elif shape is SphereShape3D:
		return Vector3(shape.radius, shape.radius, shape.radius)
	elif shape is CapsuleShape3D:
		var r: float = shape.radius
		return Vector3(r, shape.height * 0.5, r)
	elif shape is CylinderShape3D:
		var rr: float = shape.radius
		return Vector3(rr, shape.height * 0.5, rr)
	return Vector3(10, 10, 10)


# =========================================================================== #
# Asset routing consumption (MISSION step 1) -- render-only, physics-free
# =========================================================================== #
func _build_assets_norm(assets) -> void:
	# Index the route_assets mapping ({state-body-name: asset_id|null}) by a normalised name
	# so it survives case/underscore differences between state() names and node names
	# (e.g. state "puck" vs node "Puck", "goal_a" vs "GoalA").
	_assets_norm.clear()
	if typeof(assets) != TYPE_DICTIONARY:
		return
	for k in assets.keys():
		var v = assets[k]
		if typeof(v) == TYPE_STRING and String(v) != "":
			_assets_norm[_norm_name(String(k))] = String(v)


func _norm_name(name: String) -> String:
	var out := ""
	for ch in name.to_lower():
		if (ch >= "a" and ch <= "z") or (ch >= "0" and ch <= "9"):
			out += ch
	return out


func _resolve_assets(game_root: Node, shapes: Array) -> void:
	# Build body-instance-id -> asset-id, once, before proxies. Bodies are matched to state()
	# names by t=0 POSITION (node names need not equal state names -- e.g. an unnamed
	# RigidBody3D whose state name is "puck"), with a node-name fallback.
	_asset_by_body.clear()
	if _assets_norm.is_empty() or _manifest_path == "":
		return
	var state_bodies := _state_bodies(game_root)
	var seen := {}
	for rec in shapes:
		var body = rec["body"]
		if body == null or not is_instance_valid(body):
			continue
		var bid: int = body.get_instance_id()
		if seen.has(bid):
			continue
		seen[bid] = true
		var aid: String = _asset_for_body(body, state_bodies)
		if aid != "":
			_asset_by_body[bid] = aid


func _state_bodies(game_root: Node) -> Array:
	# [{name: String, pos: Vector3}] from the game's own t=0 state() (a PURE query). Empty on
	# any deviation -- routing then falls back to node-name matching.
	var out := []
	if not game_root.has_method("state"):
		return out
	var st = game_root.state()
	if typeof(st) != TYPE_DICTIONARY:
		return out
	var bodies = st.get("bodies", [])
	if typeof(bodies) != TYPE_ARRAY:
		return out
	for b in bodies:
		if typeof(b) != TYPE_DICTIONARY:
			continue
		var v := Vector3.ZERO
		var pos = b.get("pos", [])
		if typeof(pos) == TYPE_ARRAY and pos.size() >= 2:
			v.x = float(pos[0]); v.y = float(pos[1])
			if pos.size() >= 3:
				v.z = float(pos[2])
		out.append({"name": String(b.get("name", "")), "pos": v})
	return out


func _asset_for_body(body: Node, state_bodies: Array) -> String:
	# Prefer a t=0 position match to a state() body; fall back to the node's own name.
	var bpos := _body_pos3(body)
	var best_name := ""
	var best_d := INF
	for sb in state_bodies:
		var d: float = (sb["pos"] as Vector3).distance_to(bpos)
		if d < best_d:
			best_d = d
			best_name = String(sb["name"])
	if best_name != "" and best_d <= ASSET_POS_TOL:
		var aid := String(_assets_norm.get(_norm_name(best_name), ""))
		if aid != "":
			return aid
	return String(_assets_norm.get(_norm_name(String(body.name)), ""))


func _asset_id_for_rec(rec: Dictionary) -> String:
	var body = rec["body"]
	if body == null or not is_instance_valid(body):
		return ""
	return String(_asset_by_body.get(body.get_instance_id(), ""))


func _load_asset(asset_id: String, target_size: Vector3, anchor: String):
	# Render-only bank model via AssetLoader (physics provably stripped). Returns null on any
	# failure -> the caller falls back to the primitive proxy, so the demo always renders.
	if asset_id == "" or _manifest_path == "":
		return null
	var loader = load("res://asset_loader.gd")
	if loader == null:
		return null
	return loader.load_asset(asset_id, _manifest_path, target_size, "fit", anchor)


# =========================================================================== #
# Camera framing
# =========================================================================== #
func _setup_camera() -> void:
	# The game's OWN camera frames the demo when it brought one: we build none, and never touch
	# theirs (it is already the viewport's current camera -- there is nothing to make current).
	# This is the make_current() theft that used to discard the framing 7 of the 22 certified
	# games authored for themselves. _camera stays null -> sync()'s follow block is inert.
	if not _should_own_camera():
		return
	if _is_3d:
		_setup_camera_3d()
	else:
		_setup_camera_2d()


func _setup_camera_2d() -> void:
	var cam := Camera2D.new()
	cam.name = "DemoCamera2D"
	cam.anchor_mode = Camera2D.ANCHOR_MODE_DRAG_CENTER
	cam.position_smoothing_enabled = false
	var center := (_min + _max) * 0.5
	cam.global_position = Vector2(center.x, center.y)
	var pad := _pad_amount()
	var scene_w := (_max.x - _min.x) + pad * 2.0
	var scene_h := (_max.y - _min.y) + pad * 2.0
	scene_w = max(scene_w, 1.0)
	scene_h = max(scene_h, 1.0)
	# Camera2D.zoom is a MULTIPLIER (<1 zooms OUT / shows more). Fit both axes.
	var zx := _view_w / scene_w
	var zy := _view_h / scene_h
	var z: float = min(zx, zy)
	if _follow:
		# In follow mode, keep a comfortable window around the agent (don't zoom out
		# to the whole course); cap so the agent stays ~1/4 of the view.
		z = clamp(z * 3.0, 0.35, 1.4)
		if _controlled_body != null and is_instance_valid(_controlled_body):
			cam.global_position = _controlled_body.global_position
	else:
		z = min(z, 1.4)
	_base_zoom = z
	cam.zoom = Vector2(z, z)
	cam.enabled = true
	_stage2d.add_child(cam)
	cam.make_current()
	_camera = cam


func _setup_camera_3d() -> void:
	# --follow = a chase cam trailing the controlled body along its travel direction; default
	# is an elevated, TILTED overview framed on the whole scene UNION the witness trajectory
	# (godot_rl-examples arena look, but never losing a fly-through craft off-frame).
	if _follow and _controlled_body != null and is_instance_valid(_controlled_body):
		_setup_follow_cam_3d()
	else:
		_setup_overview_cam_3d()


func _overview_box() -> Array:
	# The framing box for the elevated overview: the t=0 static AABB UNION the witness
	# trajectory's box, so a craft that flies well past its start frame stays on screen (the
	# fly-through fix -- the old t=0-only box lost the craft after ~2 frames). No trajectory
	# supplied (e.g. the desktop player) -> the t=0 box, unchanged.
	var lo := _min
	var hi := _max
	if _traj_valid():
		lo = Vector3(minf(lo.x, _traj_min.x), minf(lo.y, _traj_min.y), minf(lo.z, _traj_min.z))
		hi = Vector3(maxf(hi.x, _traj_max.x), maxf(hi.y, _traj_max.y), maxf(hi.z, _traj_max.z))
	return [lo, hi]


func _traj_valid() -> bool:
	return _has_traj and _traj_min.x <= _traj_max.x \
		and is_finite(_traj_min.x) and is_finite(_traj_max.x)


func _setup_overview_cam_3d() -> void:
	# An elevated, tilted fit-to-scene overview (~ELEV_3D above the play plane, NOT straight
	# top-down): the scene's large axes read as a ground plane with the bodies standing on it,
	# like ScoreTheGoal's arena shots. All distances scale off the AABB (no absolute values).
	var cam := Camera3D.new()
	cam.name = "DemoCamera3D"
	var box := _overview_box()
	var bmin: Vector3 = box[0]
	var bmax: Vector3 = box[1]
	var center := (bmin + bmax) * 0.5
	var span := bmax - bmin
	var radius: float = max(span.length() * 0.5, 1.0)
	cam.projection = Camera3D.PROJECTION_PERSPECTIVE
	cam.fov = 50.0
	# The thin axis is the scene's "up" (out of the play plane); pull back along an in-plane
	# axis and rise by the elevation angle so we look DOWN onto the plane at a tilt.
	var normal := _thin_axis_dir(span)
	var back := _up_for(normal)
	var el := deg_to_rad(ELEV_3D)
	var dir := (back * cos(el) + normal * sin(el)).normalized()
	var d := radius / tan(deg_to_rad(cam.fov * 0.5)) * (1.0 + MARGIN_FRAC) * 1.2
	cam.near = 0.5
	cam.far = d * 4.0 + 2000.0
	_stage3d.add_child(cam)                # in-tree BEFORE look_at (needs a global transform)
	cam.global_position = center + dir * d
	cam.look_at(center, normal)            # world-up = play-plane normal -> arena framing
	cam.make_current()
	_camera = cam


func _setup_follow_cam_3d() -> void:
	# A chase camera trailing the controlled body along its TRAVEL direction (from the
	# pre-scanned witness trajectory; falls back to the body's facing). Its world offset +
	# orientation are baked here from the AABB-scaled rig; sync() re-poses the position each
	# frame (read-only -> the zero-contact contract holds, as when it was proxy-parented).
	var fwd := _traj_fwd
	if not _has_fwd:
		var bf: Vector3 = -_controlled_body.global_transform.basis.z
		if Vector3(bf.x, 0.0, bf.z).length() > 1.0e-3:
			fwd = bf
	_follow_offset = follow_offset(_controlled_ext, _cam_dist, fwd)
	var cam := Camera3D.new()
	cam.name = "DemoFollowCam3D"
	cam.projection = Camera3D.PROJECTION_PERSPECTIVE
	cam.fov = FOLLOW_FOV
	cam.near = 0.5
	var span_len: float = (_overview_box()[1] - _overview_box()[0]).length()
	cam.far = maxf(span_len * 2.0 + _follow_offset.length() * 2.0, 2000.0)
	_stage3d.add_child(cam)              # in-tree BEFORE global_transform (needs a scenario)
	cam.global_transform = Transform3D(
		Basis.looking_at(follow_look_dir(fwd), Vector3.UP), _follow_pose())
	cam.make_current()
	_camera = cam


# ---- follow-rig math (PURE: reads no member state -> unit-testable in isolation) ---- #
func follow_back_dist(ext: Vector3, cam_dist: float) -> float:
	# Chase distance = a generous body-length (the largest full AABB extent) * the multiplier,
	# floored by an ABSOLUTE minimum so a tiny body never glues the camera to itself.
	var body_len: float = 2.0 * maxf(ext.x, maxf(ext.y, ext.z))
	return maxf(body_len * cam_dist, FOLLOW_MIN_BACK)


func follow_up_dist(ext: Vector3, back: float) -> float:
	# Rise ~1 body-height, but tied to the back distance (stable framing) and floored.
	return maxf(2.0 * ext.y, maxf(back * FOLLOW_UP_FRAC, FOLLOW_MIN_UP))


func _horiz_fwd(fwd: Vector3) -> Vector3:
	# Travel direction flattened onto the play plane; a safe default when degenerate.
	var f := Vector3(fwd.x, 0.0, fwd.z)
	if f.length() < 1.0e-4:
		return Vector3(0.0, 0.0, 1.0)
	return f.normalized()


func follow_offset(ext: Vector3, cam_dist: float, fwd: Vector3) -> Vector3:
	# World-space body->camera offset: BEHIND the travel direction + risen.
	var f := _horiz_fwd(fwd)
	var back := follow_back_dist(ext, cam_dist)
	var up := follow_up_dist(ext, back)
	return -f * back + Vector3(0.0, up, 0.0)


func follow_look_dir(fwd: Vector3) -> Vector3:
	# Camera aim: along travel, pitched down FOLLOW_PITCH_DEG so the craft sits low in frame
	# and the path ahead (the rings!) fills the rest.
	var f := _horiz_fwd(fwd)
	var pitch := deg_to_rad(FOLLOW_PITCH_DEG)
	return (f * cos(pitch) - Vector3.UP * sin(pitch)).normalized()


func clamp_follow_pos(pos: Vector3, lo: Vector3, hi: Vector3) -> Vector3:
	# Keep the chase camera INSIDE the flyable volume (lo..hi + a margin) on the lateral +
	# vertical axes, so it never pops out through the ceiling/side walls when the craft hugs a
	# boundary -- the craft then rides high/wide in-frame instead of vanishing into empty sky.
	# The chase (depth) axis is deliberately NOT clamped: the camera MUST sit behind the craft,
	# often past the box's near face. PURE -> unit-testable.
	var m := FOLLOW_CLAMP_MARGIN
	var f := _horiz_fwd(_traj_fwd)
	var out := pos
	# clamp only the axes ACROSS travel (|component| small); leave the along-travel axis free.
	if absf(f.x) < 0.5:
		out.x = clampf(pos.x, lo.x - m, hi.x + m)
	if absf(f.z) < 0.5:
		out.z = clampf(pos.z, lo.z - m, hi.z + m)
	out.y = clampf(pos.y, lo.y - m, hi.y + m)   # vertical: always (the ceiling-pop fix)
	return out


func _clamp_box() -> Array:
	# The volume the follow camera is kept within: the WITNESS TRAJECTORY box (where the craft
	# actually flew -- guaranteed inside the course) when known, else the t=0 static AABB.
	if _traj_valid():
		return [_traj_min, _traj_max]
	return [_min, _max]


func _follow_pose() -> Vector3:
	# The chase camera's world position for the controlled body's current pose, clamped.
	var box := _clamp_box()
	return clamp_follow_pos(_controlled_body.global_position + _follow_offset, box[0], box[1])


func _thin_axis_dir(span: Vector3) -> Vector3:
	# Unit vector along the AABB's shortest extent -- the scene's "depth" / face normal.
	var ax := absf(span.x)
	var ay := absf(span.y)
	var az := absf(span.z)
	if az <= ax and az <= ay:
		return Vector3(0, 0, 1)
	if ay <= ax and ay <= az:
		return Vector3(0, 1, 0)
	return Vector3(1, 0, 0)


func _up_for(depth: Vector3) -> Vector3:
	# A stable in-plane axis perpendicular to the depth axis (-Y for a z-depth layout so the
	# view matches the 2D y-down convention these games share).
	if absf(depth.z) > 0.5:
		return Vector3(0, -1, 0)
	if absf(depth.y) > 0.5:
		return Vector3(0, 0, -1)
	return Vector3(0, -1, 0)


# =========================================================================== #
# Small helpers
# =========================================================================== #
func _role_fill(role: String) -> Color:
	match role:
		"controlled":
			return COL_CONTROLLED
		"sensor":
			var c := COL_SENSOR
			c.a = SENSOR_ALPHA
			return c
		"static":
			return COL_STATIC
		_:
			return COL_DYNAMIC


func _role_outline(role: String) -> Color:
	if role == "sensor":
		return Color(COL_SENSOR.r, COL_SENSOR.g, COL_SENSOR.b, 0.9)
	return COL_OUTLINE


func _role_z(role: String) -> int:
	match role:
		"controlled":
			return Z_DYNAMIC + 5
		"sensor":
			return Z_SENSOR
		"static":
			return Z_STATIC
		_:
			return Z_DYNAMIC


func _pad_amount() -> float:
	var span_x := _max.x - _min.x
	var span_y := _max.y - _min.y
	return max(span_x, span_y) * MARGIN_FRAC + 20.0


func _bounds_valid() -> bool:
	return _min.x <= _max.x and _min.y <= _max.y and is_finite(_min.x) and is_finite(_max.x)


func bounds() -> Dictionary:
	# Exposed for the host's logging/framing sanity.
	return {"min": _min, "max": _max, "is_3d": _is_3d}


func _read_cam_opts(opts: Dictionary) -> void:
	# Camera-framing hints, from opts (unit tests / desktop player) or the capture driver's
	# env (capture.py pre-scans the witness trajectory headless and exports these). ALL are
	# render-only: they move the camera and never touch the game tree or physics, so the
	# capture host stays untouched and the dressed==undressed state trail is unaffected.
	_cam_dist = float(opts.get("cam_dist", _env_float("HARNESS_CAM_DIST", FOLLOW_CAM_DIST)))
	if _cam_dist <= 0.0:
		_cam_dist = FOLLOW_CAM_DIST
	var tmin = opts.get("traj_min", _env_vec3("HARNESS_CAM_TRAJ_MIN"))
	var tmax = opts.get("traj_max", _env_vec3("HARNESS_CAM_TRAJ_MAX"))
	if tmin != null and tmax != null:
		_traj_min = tmin
		_traj_max = tmax
		_has_traj = true
	var fwd = opts.get("cam_fwd", _env_vec3("HARNESS_CAM_FWD"))
	if fwd != null:
		_traj_fwd = fwd
		_has_fwd = true


func _env_float(name: String, dflt: float) -> float:
	var s := OS.get_environment(name)
	return s.to_float() if s != "" else dflt


func _env_vec3(name: String):
	# Parse "x,y,z" -> Vector3, else null (so an unset var falls back to the t=0 defaults).
	var s := OS.get_environment(name)
	if s == "":
		return null
	var parts := s.split(",", false)
	if parts.size() < 3:
		return null
	return Vector3(parts[0].to_float(), parts[1].to_float(), parts[2].to_float())
