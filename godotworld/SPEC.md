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
latch checkpoints, check failure then success), the same y-UP px world at `dt = 1/60`,
seeded determinism, and full-precision `%.17f` float output. Gravity is set per **view
mode** (§2b): the default `side` view keeps the historical `(0, -900)`; a `topdown`
view zeroes gravity for a plan-view arena.

---

## 1. Top-level shape

```jsonc
{
  "engine": "godot",                 // optional marker
  "world":  { ... },                 // optional: view (side|topdown) + linear_damp
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

## 2b. `world` — view mode (side vs top-down)

Optional top-level block selecting how gravity reads. **Omitting it is the default
`side` view**, and a spec with no `world` block is byte-for-byte identical to before
this field existed — the frozen runner touches no body property in side view.

| field | type | notes |
|---|---|---|
| `view` | `"side"` \| `"topdown"` | default `"side"` |
| `linear_damp` | number ≥ 0 | **top-down only**; the per-body linear-damping friction analog. Default `1.5`. Ignored in side view. |

- **`side`** (default): gravity `(0, -900)`, zero linear damping — the historical
  platformer/tower/pit world. Down is `-Y`; a released body falls and rests on a floor.
- **`topdown`**: gravity `(0, 0)` — the `x`/`y` plane is the **floor seen from above**
  (a plan-view arena: BallChase / AirHockey / CrossTheRoad / the steer family). No body
  falls; bodies **glide**. `world.linear_damp` (default `1.5`) is applied to **every
  dynamic body** as the friction analog, so a body pushed by `impulse`/`thrust` and then
  released **coasts to a stop** instead of drifting forever. Bound the arena on **all
  four sides** (there is no floor to catch a body). `grounded(...)` is meaningless
  (nothing is "below"); success is a reach / `contained(...)` / stillness read, not a
  landing.

Realisation (runner, per dynamic `RigidBody2D`, at world build): side view leaves
`gravity_scale = 1` and `linear_damp = 0` (engine defaults, untouched); top-down sets
`gravity_scale = 0` and `linear_damp = world.linear_damp` (`DAMP_MODE_REPLACE`). Static
and sensor bodies are unaffected. Both serve and batch modes honour the view.

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
{ "verb": "impulse" | "force" | "set_velocity" | "torque" | "thrust",
  "body": "<name>",
  "vec":  [x, y],         // impulse / force / set_velocity
  "magnitude": <number>,  // torque / thrust (signed scalar)
  "when": "<predicate>"   // optional gate (e.g. grounded-gated jump)
}
```

| verb | param | effect |
|---|---|---|
| `impulse` | `vec` | `apply_central_impulse` — instantaneous kick, once per decision tick. |
| `set_velocity` | `vec` | sets `linear_velocity`, once per decision tick. |
| `force` | `vec` | `apply_central_force`, **re-applied on each of the K=6 sub-steps** so it reads as a sustained push over the tick (engineering choice; documented). |
| `torque` | `magnitude` | `apply_torque_impulse(magnitude)` — a signed angular kick (`+` spins CCW, `−` CW), once per decision tick. Heading control for cars/ships/drills. |
| `thrust` | `magnitude` | `apply_central_impulse` of `(magnitude, 0)` **rotated by the body's CURRENT `rotation`** — pushes the body along the way it points, so `torque`+`thrust` steer-and-drive a heading-controlled body. |

- `when` gates the verb: it applies only when the predicate is true at act-time
  (contacts/grounded read the state from the *previous* tick's last step).

Worked example — a heading-controlled drive (steer with `torque`, drive with `thrust`):

```jsonc
"act": {
  "spin_left":  [{"verb": "torque",  "body": "car", "magnitude":  180}],
  "spin_right": [{"verb": "torque",  "body": "car", "magnitude": -180}],
  "drive":      [{"verb": "thrust",  "body": "car", "magnitude":  90}]
}
```

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
| `contained(a, b)` | true iff body `a`'s AABB is **fully inside** body `b`'s AABB (see below) |
| `dist(a, b)` | distance between the two bodies' centers |
| `flag(k)` | value of flag `k` (0 / false if unset) |

`contained(a, b)` **AABB semantics.** Both bodies are reduced to the same
axis-aligned bounding box the G0 init check uses (`_bbox`): a circle → `center ± r`;
a box/poly/segment → the extents of its **rotated** local vertices. Writing
`a=[aL,aB,aR,aT]` and `b=[bL,bB,bR,bT]` (left/bottom/right/top, y UP), containment is

```
aL >= bL  and  aB >= bB  and  aR <= bR  and  aT <= bT
```

i.e. every side of `a` lies within `b` — full containment, **not** the mere overlap
`contacts` reports. `b` is typically a `sensor` zone. Because a **rotated** box has a
larger AABB, a body spun by `torque` is harder to keep contained (the AABB is a
conservative over-approximation of the true shape). A removed or missing body is never
contained. This is the 2D-parking primitive: "the car is fully inside the slot".

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
