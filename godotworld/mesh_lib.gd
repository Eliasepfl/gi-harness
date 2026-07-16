# mesh_lib.gd -- pure-GDScript procedural low-poly mesh factory (class_name MeshLib).
#
# WHY THIS EXISTS. Generated games are ONE self-contained .gd file with load()/preload()
# and external assets BANNED (api_gdscript.md:46,64) -- so a game can never IMPORT a .glb.
# But the same contract line 46 explicitly permits, and points at, building meshes in code:
#   "no external assets and no load()/preload() (both banned - construct any mesh/material
#    in code)."
# This library is the reference implementation of that clause: recognizable low-poly
# geometry (car / rock-spire / ring gate) emitted from SurfaceTool into an ArrayMesh, with
# per-vertex colour baked in. NOTHING here reads a file, so it is legal both (a) inlined
# verbatim into a generated game's build() [insertion point 2], and (b) called host-side by
# the capture dresser to replace naked-primitive proxies for free [the recommended first
# experiment -- see notes/engines/ASSET_CREATION_3D.md].
#
# ZERO CERTIFICATION IMPACT. Every function returns a render-only Mesh resource. It has no
# collision, no physics, no state(); certification is pixel-blind (reads only state()), so a
# richer mesh can neither help nor hurt a verdict. Meshes are LOW-POLY BY DESIGN -- see the
# per-function tri budgets asserted in tests/test_mesh_lib.gd.
#
# Colours are baked as vertex colours; render them with a StandardMaterial3D whose
# `vertex_color_use_as_albedo = true` (the caller owns the material, as the contract requires).

class_name MeshLib
extends RefCounted

# Palette -- role-differentiated, reads at a glance under flat/low light.
const BODY := Color(0.86, 0.22, 0.20)      # car body: warm red
const CABIN := Color(0.14, 0.17, 0.24)     # cabin glass: near-black slate
const TYRE := Color(0.05, 0.05, 0.06)      # wheels: black
const ROCK := Color(0.42, 0.40, 0.44)      # spire: cool grey stone
const ROCK_TOP := Color(0.60, 0.58, 0.62)  # spire highlight near apex
const GATE := Color(0.20, 0.78, 0.55)      # ring gate: bright green (a "pass-through" goal)


# ---- a recognizable low-poly CAR: chassis + cabin + 4 wheels (6 boxes) ------- #
static func car() -> ArrayMesh:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	# chassis (long, low) and cabin (shorter, raised, set back)
	_box(st, Vector3(0.0, 0.45, 0.0), Vector3(4.0, 0.7, 1.9), BODY)
	_box(st, Vector3(-0.25, 1.05, 0.0), Vector3(2.1, 0.7, 1.7), CABIN)
	# four wheels, just proud of the body sides
	var wy := 0.35
	var wr := Vector3(0.7, 0.7, 0.5)
	for wx in [1.35, -1.35]:
		for wz in [1.0, -1.0]:
			_box(st, Vector3(wx, wy, wz), wr, TYRE)
	st.generate_normals()
	return st.commit()


# ---- a recognizable low-poly ROCK SPIRE: an irregular tapered N-gon needle ---- #
static func spire(world_seed: int = 0, height: float = 6.0, base_r: float = 1.5) -> ArrayMesh:
	var rng := RandomNumberGenerator.new()
	rng.seed = world_seed
	var sides := 6
	var apex := Vector3(0.0, height, 0.0)
	var rim := []
	for i in range(sides):
		var a := TAU * float(i) / float(sides)
		var r := base_r * rng.randf_range(0.72, 1.0)   # jitter -> reads as rock, not a cone
		rim.append(Vector3(cos(a) * r, 0.0, sin(a) * r))
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	for i in range(sides):
		var p0: Vector3 = rim[i]
		var p1: Vector3 = rim[(i + 1) % sides]
		_tri(st, p0, p1, apex, ROCK, ROCK, ROCK_TOP)     # a face climbing to the apex
		_tri(st, Vector3.ZERO, p1, p0, ROCK, ROCK, ROCK)  # base cap (wound downward)
	st.generate_normals()
	return st.commit()


# ---- a recognizable low-poly RING GATE: a faceted torus ---------------------- #
static func ring(major_r: float = 2.0, tube_r: float = 0.4) -> ArrayMesh:
	var nu := 14   # segments around the ring
	var nv := 7    # segments around the tube
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	for i in range(nu):
		for j in range(nv):
			_quad(st,
				_torus_pt(major_r, tube_r, i, j, nu, nv),
				_torus_pt(major_r, tube_r, i + 1, j, nu, nv),
				_torus_pt(major_r, tube_r, i + 1, j + 1, nu, nv),
				_torus_pt(major_r, tube_r, i, j + 1, nu, nv),
				GATE)
	st.generate_normals()
	return st.commit()


# ---- shared geometry helpers (not counted against the per-shape line budgets) - #
static func _torus_pt(rr: float, tr: float, i: int, j: int, nu: int, nv: int) -> Vector3:
	var u := TAU * float(i) / float(nu)
	var v := TAU * float(j) / float(nv)
	var ring_r := rr + tr * cos(v)
	return Vector3(cos(u) * ring_r, tr * sin(v), sin(u) * ring_r)


static func _tri(st: SurfaceTool, a: Vector3, b: Vector3, c: Vector3,
		ca: Color, cb: Color, cc: Color) -> void:
	st.set_color(ca); st.add_vertex(a)
	st.set_color(cb); st.add_vertex(b)
	st.set_color(cc); st.add_vertex(c)


static func _quad(st: SurfaceTool, a: Vector3, b: Vector3, c: Vector3, d: Vector3,
		col: Color) -> void:
	_tri(st, a, b, c, col, col, col)
	_tri(st, a, c, d, col, col, col)


static func _box(st: SurfaceTool, c: Vector3, s: Vector3, col: Color) -> void:
	var h := s * 0.5
	var p000 := c + Vector3(-h.x, -h.y, -h.z)
	var p100 := c + Vector3(h.x, -h.y, -h.z)
	var p110 := c + Vector3(h.x, h.y, -h.z)
	var p010 := c + Vector3(-h.x, h.y, -h.z)
	var p001 := c + Vector3(-h.x, -h.y, h.z)
	var p101 := c + Vector3(h.x, -h.y, h.z)
	var p111 := c + Vector3(h.x, h.y, h.z)
	var p011 := c + Vector3(-h.x, h.y, h.z)
	_quad(st, p001, p101, p111, p011, col)  # +Z front
	_quad(st, p100, p000, p010, p110, col)  # -Z back
	_quad(st, p000, p001, p011, p010, col)  # -X left
	_quad(st, p101, p100, p110, p111, col)  # +X right
	_quad(st, p011, p111, p110, p010, col)  # +Y top
	_quad(st, p000, p100, p101, p001, col)  # -Y bottom
