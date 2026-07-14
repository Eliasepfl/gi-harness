# Godot game-spec v1

> The Godot lane's per-game artifact is a **declarative JSON spec**, not code. The
> LLM emits DATA (bodies / joints / sensors / actions / predicates); a single
> **FROZEN, audited `runner.gd`** interprets it. **No untrusted GDScript ever runs.**
> This is the safe-by-construction posture decided in
> `notes/engines/GODOT_MIGRATION.md §2.3` and `GODOT_SKILLS_WORLDGEN.md §3`.
>
> Machine-checkable schema: `godotworld/spec.schema.json` (JSON Schema draft-07).
> A spec file uses the `.spec.json` extension (the harness routes it to the Godot
> lane); it may also carry a top-level `"engine": "godot"` marker.

The spec mirrors `CONTRACTS.md §1-2` one-for-one: `bodies` are `World.add` kwargs,
`joints` are `World.pin/pivot/spring`, `act` is the per-action `world.impulse/force/
set_velocity`, `predicates` are `success/failure/checkpoints`. The runner enforces the
same decision-tick semantics as `nodeworld/runner.js` (act, then K=6 physics steps,
latch checkpoints, check failure then success), the same y-UP px world with gravity
`(0, -900)` at `dt = 1/60`, seeded determinism, and full-precision `%.17f` float output.

---

## 1. Top-level shape

```jsonc
{
  "engine": "godot",                 // optional marker
  "meta":   { ... },                 // title, prompt, world_size?, actions
  "bodies": [ ... ],                 // >= 2 bodies; exactly one "control": true
  "joints": [ ... ],                 // optional
  "on_contact": [ ... ],             // optional: latch a flag when two bodies touch
  "act":    { "<action>": [ verbs ] },
  "on_step":[ ... ],                 // optional: fixed behavior library
  "sensors":[ ... ],                 // optional (spec-v2): raycast obs fans -> obs tail
  "predicates": { "success": "...", "failure"?: "...", "checkpoints": { ... } }
}
```

## 2. `meta`

| field | type | notes |
|---|---|---|
| `title` | string | short game title |
| `prompt` | string | the originating user prompt |
| `world_size` | `[w, h]` | optional; width 800..2400, height 600..1600 (G0 validates); default `[800, 600]` |
| `actions` | `[str]` | 2..8 game-chosen action names (the move set) |

## 3. `bodies` — mirrors `World.add`

Each entry: `{ "name", "shape", "pos", ... }`. Exactly one **dynamic** body must set
`"control": true`.

- `shape`: `box` (needs `size:[w,h]`) · `circle` (needs `radius`) · `segment` (needs
  `a:[x,y]`, `b:[x,y]` local endpoints) · `poly` (needs `vertices:[[x,y],...]`, convex).
- common: `static` (bool) · `sensor` (bool, non-colliding overlap zone → `Area2D`) ·
  `mass` (>0) · `friction` (≥0, default 0.7) · `elasticity` (≥0, default 0.3) ·
  `locked_rotation` (bool) · `angle` (rad) · `velocity:[vx,vy]` · `control` (bool).

Realisation: static → `StaticBody2D`; sensor → `Area2D` (monitoring); else
`RigidBody2D` (contact-monitored, never sleeps). Density derives from `mass`/area.

## 4. `joints` — mirrors `World.pin/pivot/spring`

`{ "type": "pin"|"pivot"|"spring", "a", "b", ... }` — `pivot` takes `point:[x,y]`;
`pin` takes optional `anchor_a`/`anchor_b`; `spring` takes `rest_length`, `stiffness`,
`damping` (+ optional anchors). v1 mapping (Godot 2D has no distance joint): `pin`/
`pivot` → `PinJoint2D` at the anchor/point; `spring` → `DampedSpringJoint2D`. This is
approximate (documented in `GODOT_LANE.md`); joints are optional and none of the three
shipped example games needs one.

## 5. `on_contact` — the collect / trigger primitive

`{ "a", "b", "flag", "once"?: true }`. Once `a` and `b` overlap, `flag` latches to
`true` (and stays true even after they separate — mirrors `World.on_contact`). This is
how a collectible is "picked up": pair it with a `remove_when` behavior and read the
flag in a predicate.

## 6. `act` — per-action verb lists (fixed vocabulary)

`"act": { "<action_name>": [ verb_call, ... ] }`. Each `verb_call`:

```jsonc
{ "verb": "impulse" | "force" | "set_velocity",
  "body": "<name>",
  "vec":  [x, y],
  "when": "<predicate>"   // optional gate (e.g. grounded-gated jump)
}
```

- `impulse` → `apply_central_impulse` (instantaneous kick, once per decision tick).
- `set_velocity` → sets `linear_velocity` (once per decision tick).
- `force` → `apply_central_force`, **re-applied on each of the K=6 sub-steps** so it
  reads as a sustained push over the tick (engineering choice; documented).
- `when` gates the verb: it applies only when the predicate is true at act-time
  (contacts/grounded read the state from the *previous* tick's last step).

An action absent from `act`, or bound to `[]`, is a no-op — it will fail G1's
dead-action efficacy check, so bind every declared action to something with an effect.

## 7. `on_step` — fixed behavior library (v1)

Evaluated once per physics step (after the step, before the terminal checks), in list
order. `kind` selects the behavior:

| kind | fields | effect |
|---|---|---|
| `velocity_clamp` | `body`, `vx_max`?, `vy_min`?, `vy_max`? | clamp `|vx| ≤ vx_max` and `vy ∈ [vy_min, vy_max]` (each optional) |
| `timer_flag` | `flag`, `after_steps` | set `flag = true` once `steps ≥ after_steps` |
| `remove_when` | `flag`, `body` | remove `body` from the world when `flag` is truthy |
| `rising_level` | `flag`, `rate`, `start`? | set numeric `flag = start + rate·steps` (a rising water/lava line to compare against) |

These four suffice for the shipped archetypes; new behaviors are a follow-up
(`GODOT_LANE.md`).

## 7b. `sensors` — raycast observation fans (spec-v2, optional)

> Not to be confused with a body's `sensor: true` flag (§3), which makes that body a
> non-colliding Area2D overlap zone. `sensors` here is a **top-level list of RL-style
> observation probes** — audited nodes vendored from `godot_rl_agents` (MIT,
> `godotworld/addons/sensors/`), attached under a named body and read as an **obs tail**.

Each entry describes a proximity fan riding on one body:

```jsonc
{ "type": "raycast2d",     // the only sensor type in spec-v2
  "attach_to": "<body>",   // name of the body the fan rides on (required)
  "n_rays": 16,            // ray count = obs length; 1..64 (default 16)
  "ray_length": 200,       // max reach in px (default 200)
  "cone_width_deg": 360,   // fan spread in degrees (default 360)
  "collision_mask": 1 }    // 2D physics layers the rays hit (default 1)
```

`n_rays` `RayCast2D` are fanned evenly over `cone_width_deg` (centered on the body's
local +x). Each ray reports a **normalized proximity** `(ray_length − hit_dist)/ray_length`
∈ `[0, 1]` — **`0` = no hit; larger = the hit point is nearer** (the vendored convention;
a ray never sees its own host body). The runner concatenates every sensor's
`get_observation()` (in spec order) into a flat float array and appends it as an `"obs"`
tail on **each per-tick frame** and on the **episode record** (the final settled read).
Specs with no `sensors` block emit no `"obs"` key at all — the frozen runner's existing
output is byte-for-byte unchanged.

Sensors are pure **DATA** (a fixed `type` selects a fixed vendored script — no spec string
is ever executed), so they add no ambient authority. Determinism holds: identical jobs
yield byte-identical obs. One subtlety (Godot issue #95359): a `RayCast2D` added at build
time does not register with the physics space until a step elapses, so for sensor specs the
runner settles **one** physics frame after build (uncounted in `steps`) before the first
read — sensor-free specs skip this and are unaffected.

## 8. `predicates` — the whitelisted expression DSL

`success` and (optional) `failure` are single expression strings. `checkpoints` is an
**ordered** map of 1..6 `snake_case` milestone expressions (insertion order = intended
progression). Every expression must evaluate to a **bool** and be **False at t=0**
(G2 enforces). Checkpoints latch runner-side (first tick each becomes true).

Expressions are evaluated by Godot's `Expression` class over a **whitelisted binding**
— never `eval` of arbitrary code. Before any evaluation, each expression is scanned:
every identifier must be in the allow-list below, and attribute access (`.`), indexing
(`[]`), and every other token outside the grammar are **rejected** at spec-load. This
is the real security boundary (an `OS.execute(...)` / `set_script(...)` string can never
reach the interpreter).

**Query functions** (argument names are body/flag string literals):

| call | returns |
|---|---|
| `pos_x(b)`, `pos_y(b)` | body position component (y is UP) |
| `vel_x(b)`, `vel_y(b)` | body velocity component |
| `speed(b)` | `|velocity|` |
| `angle(b)` | rotation (radians) |
| `grounded(b)` | true if supported from below (a non-sensor contact under the body) |
| `contacts(a, b)` | true if `a` and `b` are touching / overlapping |
| `dist(a, b)` | distance between the two bodies' centers |
| `flag(k)` | value of flag `k` (0 / false if unset) |

**Variable:** `steps` (physics steps elapsed this episode).

**Operators / builtins:** `and or not`, `== != < > <= >=`, `+ - * / %`, numeric
literals, and the math builtins `abs min max clamp sqrt floor ceil`. Removed bodies
read as `pos=vel=0`, `grounded=false`, `contacts=false`.

Example:

```jsonc
"predicates": {
  "success":  "flag(\"got_a\") and flag(\"got_b\")",
  "failure":  "pos_y(\"player\") < flag(\"water\")",
  "checkpoints": {
    "first_gem": "flag(\"got_a\") or flag(\"got_b\")",
    "both_gems": "flag(\"got_a\") and flag(\"got_b\")"
  }
}
```

---

## 9. What the lane verifies (G0-G3)

A `.spec.json` runs the **same universal funnel** as the Py/JS lanes
(`harness/verify/gameverify.py`), routed through a `GodotExecutor`:

- **G0** — spec well-formedness (the load/symbols analog: required sections, body
  shapes, action list, one controlled dynamic body, ≥2 bodies), `world_size` bounds,
  no initial interpenetration (analytic AABB), dynamic bodies in bounds. The
  code-scan is trivially empty (pure data).
- **G1** — noop rollout: no NaN/explosion, no escape, no success under noop (agency),
  byte-determinism across two seeded runs, per-action efficacy (dead-action check).
- **G2** — `success`/`failure` are pure bools False at t=0; `checkpoints` is 1..6
  snake_case bools all False at t=0.
- **G3** — the Go-Explore tree solver searches for a replayable witness (unchanged;
  it only needs `run_batch`), then checks anti-triviality, dead milestones, replay,
  and solidity on the winning path.

Run one: `python -m harness game verify godotworld/examples/traverse.spec.json --json`.

## 10. Determinism & limits (v1)

- Byte-identical JSONL for identical jobs (stock Godot Physics 2D, per
  `SPIKE_REPORT.md` gate (c)). Cross-*machine* replay would use the Rapier
  cross-platform build (follow-up).
- v1 expresses: traversal / reach-a-zone, timed survival, collect-N (via
  `on_contact`+`remove_when`+flags), rising-hazard avoidance, grounded-gated jumping,
  velocity-capped movement. Not yet: continuously-translating kinematic platforms,
  per-body custom scripts, spawning, counters beyond flags, navmesh pathing. See the
  archetype table in `notes/engines/GODOT_LANE.md`.
