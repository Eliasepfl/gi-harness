# GameAPI — the GDScript-lane game contract

> The code twin of `SPEC.md`. Where the spec lane emits DATA a frozen interpreter
> reads, the GDScript lane ships ONE `.gd` file that `extends GameAPI` and
> implements the contract below. It is verified through the SAME G0–G4 funnel via
> the serve seam, so the certificate (deterministic, provably-solvable,
> G4-hardened) stays ours while the medium becomes a language models actually
> know (`notes/engines/GDSCRIPT_LANE.md`, `ASYMMETRY_ANALYSIS.md`).

## The shape

```gdscript
extends GameAPI          # the frozen, trusted base (godotworld/game_api.gd)

func build(world_seed: int) -> void: ...
func act(action: String) -> void: ...
func state() -> Dictionary: ...
func checkpoints() -> Dictionary: ...
func is_success() -> bool: ...
func is_failure() -> bool: ...
func actions() -> Array: ...
```

The base provides `self.rng` (a `RandomNumberGenerator` the harness seeds before
`build`), `get_space()` (the physics-space RID), **and nothing else** — no OS, no
file, no net.

## Methods (signatures are exact; the contract probe rejects a missing one)

| method | returns | contract |
|---|---|---|
| `build(world_seed:int)` | `void` | Build the scene UNDER `self` (add_child bodies + CollisionShape2D). Draw ANY randomness from `self.rng`. Called once per episode after a full free+rebuild; idempotent given the seed. |
| `act(action:String)` | `void` | Apply ONE decision-tick input. `action` ∈ `actions()`; the host steps physics K=6 frames after each `act`. |
| `state()` | `Dictionary` | Typed snapshot (below). Pure — no scene mutation. |
| `checkpoints()` | `Dictionary` | 1..6 snake_case milestones → `bool`, ALL false at t=0. Pure. |
| `is_success()` | `bool` | Pure terminal predicate; false at t=0. |
| `is_failure()` | `bool` | Pure terminal predicate; false at t=0. |
| `actions()` | `Array[String]` | 2..8 distinct verb strings. |

### `state()` schema

```gdscript
{
  "bodies": [
    {"name": "player", "pos": [x, y], "vel": [vx, vy], "angle": a,
     "controlled": true,  "static": false},
    {"name": "gem_a",  "pos": [x, y], "vel": [0, 0],   "angle": 0,
     "controlled": false, "static": false},
    ...
  ],
  "flags":      {"got_first": false, ...},   # optional, cosmetic
  "world_size": [800, 600],                  # optional; default [800, 600]
}
```

Invariants the funnel enforces (G0): **exactly one** body is
`{"controlled": true, "static": false}`; **≥ 2** bodies; every dynamic body's
centre is in bounds.

### Purity

`state()`, `checkpoints()`, `is_success()`, `is_failure()` are called repeatedly
by the host and MUST NOT mutate the scene (G2 verifies a two-call + snapshot
invariance). Track progress in per-step latch flags updated inside `build`-time
bodies' `_physics_process` (or by distance checks recomputed each call), never by
mutating in a predicate.

## Determinism

Physics runs under a pinned 1/60 fixed step, single thread, jitter-fix off
(`project.godot` + the runner's preflight). The ONLY nondeterminism sources are
banned (below) or must route through `self.rng`. G1 gates a two-run drift check
per game — determinism is tested, not assumed.

## Bans (hard G0 fail — static scanner, `harness/verify/gd_gate.py`)

`OS.*`, `FileAccess`, `DirAccess`, `HTTP*`, `TCPServer`, `StreamPeer*`,
`PacketPeer*`, `Thread`, `Mutex`, `Engine.get_singleton`, `ClassDB`,
`Expression`, `load()` / `preload()`, `Time.*` (wall clock),
`randi/randf/randomize` (use `self.rng` instead), `get_tree().quit`.

The scanner is a HARD fail with line numbers. Generated code additionally runs
ONLY in-container on compute nodes, in a process whose environment is SCRUBBED of
every credential (`scrubbed_env`, no `OPENROUTER_*`/`ANTHROPIC_*`) — the game
process can never read a key, and the scanner blocks `OS.get_environment` anyway.

## How it is verified

`detect_engine` routes a `.gd` path to `engine == "gdscript"`.
`verify_game` then runs:

- **G0** — the static banned-API scan (python) + a parse gate (headless
  compile-check) + a contract probe (instantiate, assert every method exists,
  `state()`/`checkpoints()` return sane shapes) + the shared structural checks
  (one controlled body, ≥2 bodies, actions 2..8, in bounds).
- **G1–G3** — driven through the serve host (`serve_game.gd`): a deterministic
  two-run drift check, per-action efficacy, a well-formed-goal probe, and a
  budgeted solver that finds a replayable witness. Byte-for-byte the machinery
  the spec and JS lanes use — only the executor differs.
