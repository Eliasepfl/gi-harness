# The GDScript-lane game contract (method convention, no base class)

> The code twin of `SPEC.md`. Where the spec lane emits DATA a frozen interpreter
> reads, the GDScript lane ships ONE `.gd` file: a **plain node** (a `Node2D`-family
> root for a 2D game, a `Node3D`-family root for a 3D game — the game's choice) that
> **IMPLEMENTS the methods below** — NO base class, no `class_name` to resolve. It is
> verified through the SAME G0–G4 funnel via the serve seam, so the certificate stays
> ours while the medium becomes a language models actually know
> (`notes/engines/GDSCRIPT_LANE.md`, `ASYMMETRY_ANALYSIS.md`).

This is a CONTRACT, not a template: it carries the method **signatures** and the hard
**rules** only. Sizes, body counts, node types, a world extent, a worked skeleton — none
of those live here; the game builds whatever scene its design needs under `self`.

## The shape

The root node picks the dimension (both are fully supported; neither is a default):

- a **2D** root (the `Node2D` family) — a 2D world, `pos`/`vel` reported as `Vector2` → `[x, y]`.
- a **3D** root (the `Node3D` family) — a 3D world, `pos`/`vel` reported as `Vector3` → `[x, y, z]`. The game MUST call `PhysicsServer3D.set_active(true)` inside `build()` (the headless serve host leaves the 3D space inactive otherwise).

## Methods (the convention the contract probe checks with `has_method`)

| method | returns | contract |
|---|---|---|
| `build(world_seed: int)` | `void` | Construct the whole scene UNDER `self` (`add_child` the bodies, areas, and collision shapes the game needs — the `Node2D`- or `Node3D`-family that matches its dimension). Draw ANY randomness from an rng seeded with `world_seed`. In 3D, call `PhysicsServer3D.set_active(true)`. Runs once at start and again on every reset; deterministic given the seed. |
| `act(action: String)` | `void` | Apply ONE decision-tick input. `action` ∈ `actions()`; the host steps physics a fixed number of frames after each `act`. |
| `state()` | `Dictionary` | Typed, PURE snapshot (below). |
| `checkpoints()` | `Dictionary` | Ordered snake_case milestones → `bool`, ALL false at t=0. PURE. |
| `is_success()` | `bool` | Pure terminal predicate; false at t=0. |
| `is_failure()` | `bool` | Pure terminal predicate; false at t=0. |
| `actions()` | `Array[String]` | Distinct verb strings, the whole move set. |

### `state()` schema

A dictionary with a `"bodies"` list of named entries; each carries typed values — a
position and velocity reported as the game's dimension vector (`Vector2` → `[x, y]`, or
`Vector3` → `[x, y, z]`), an `angle`, and `controlled`/`static` flags — plus optional
`"flags"` (named bools) and any custom scalars. Exactly one body is
`{"controlled": true, "static": false}`; there are at least two bodies.

### Purity

`state()`, `checkpoints()`, `is_success()`, `is_failure()` are called REPEATEDLY by the
host and MUST NOT mutate the scene (G2 verifies a two-call + snapshot invariance). Track
progress in per-step latch flags updated inside `_physics_process`, never by mutating in
a predicate.

## Determinism + randomness (a hard rule)

Physics is pinned (a fixed step, single thread, jitter-fix off). Randomness is
deterministic: the serve host seeds the global RNG from `world_seed` before every
`build()` (each reset, both the single-instance and batched/vec paths), so the global
`randi`/`randf`/`randi_range`/`randf_range`/`randfn` and bare `seed()` draw a fixed
stream keyed by the seed and replay identically. (A game may also seed its own
`RandomNumberGenerator` from `world_seed`.) `randomize()` is BANNED — it reseeds the
global RNG from the wall clock and defeats the host pin. G1 gates a two-run drift
check per game — determinism is tested, not assumed.

## Bans (G0 static scanner, `harness/verify/gd_gate.py`)

HARD (a hit fails G0 with a line number): `OS.*`, `FileAccess`, `DirAccess`,
`ResourceSaver`, `HTTP*`, `TCPServer`, `StreamPeer*`, `PacketPeer*`, `Thread`, `Mutex`,
`Engine.get_singleton`, `ClassDB`, `Expression`, `GDScript`, `set_script`, `Time.*`
(wall clock), `randomize()` (reseeds the global RNG from the wall clock, defeating the
host's `seed(world_seed)` pin), `get_tree().quit`.

ALLOWED (guardrails v2 round 2): the global `randi`/`randf`/`randi_range`/`randf_range`/
`randfn` family and bare `seed()` — the serve host pins the global RNG with
`seed(world_seed)` before every `build()`, so they are deterministic (only `randomize()`,
which reseeds from the wall clock, stays banned). The ADVISORY severity remains in the
scanner for future use, but no rule currently uses it.

ALLOWED (guardrails v2): `load()`/`preload()`/`ResourceLoader` — reads confined to
`res://` (the sandboxed project) + an empty `user://`; no network backend, no OS
paths, deterministic. Generated code additionally runs ONLY in-container, in a process
whose environment is SCRUBBED of every credential. `PhysicsServer3D.set_active(true)`
in a 3D `build()` is the one allowed PhysicsServer call.

## How it is verified

`detect_engine` routes a `.gd` path to `engine == "gdscript"`. `verify_game` then runs:

- **G0** — the static banned-API scan (python) + a **standalone parse gate**
  (`godot --headless --check-only --script <file>`, no `--path` — a plain-node game has
  no base class to resolve, so it compiles standalone) + a contract probe (instantiate,
  assert every method exists with `has_method`, `state()`/`checkpoints()` return sane
  shapes) + the shared structural checks (one controlled body, ≥2 bodies, in bounds).
- **G1–G3** — driven through the serve host (`serve_game.gd`): a deterministic two-run
  drift check, per-action efficacy, a well-formed-goal probe, and a budgeted solver that
  finds a replayable witness. Byte-for-byte the machinery the spec and JS lanes use —
  only the executor differs, and the snapshot vectors are dimension-agnostic (2D or 3D).
