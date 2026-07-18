You write ONE GDScript `.gd` file — a plain node (`extends Node2D` / `Node3D`, no `class_name`, no custom base class) that a single frozen host loads and drives by calling the seven methods below by name (a `has_method()` probe rejects a missing one):

- `func build(world_seed: int) -> void:`
- `func act(action: String) -> void:`
- `func state() -> Dictionary:` — a PURE snapshot: a `"bodies"` list of entries `{"name", "pos", "vel", "angle", "controlled", "static"}` (report `pos`/`vel` as your dimension's vector — a `Vector2` as `[x, y]`, a `Vector3` as `[x, y, z]`); exactly one body is `"controlled"`, at least two bodies.
- `func checkpoints() -> Dictionary:` — ordered snake_case milestones -> `bool`, all false at t=0.
- `func is_success() -> bool:`
- `func is_failure() -> bool:`
- `func actions() -> Array:` — distinct verb strings, the whole move set.

`state()`, `checkpoints()`, `is_success()`, `is_failure()` are PURE: called repeatedly, they MUST NOT mutate the scene and MUST read false at t=0 (latch progress in `_physics_process`, never in these). The host runs `build(world_seed)` once, then each decision tick calls `act(action)`, advances physics a fixed number of steps, and reads `checkpoints()`, then `is_failure()`, then `is_success()`. In a 3D game call `PhysicsServer3D.set_active(true)` inside `build()`.
