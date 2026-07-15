---
id: engine-truths
kind: reference
created_by: human-seed wave-1
run_id: reseed-2026-07-14
wave: 1
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
load_when: Layout and Predicates phases — before writing any settle/park goal, any contained() goal, any predicate, or any sensor fan
rationale: The docs-mined engine facts a JSON spec must respect or it silently mis-behaves. These are the gaps between a naive assumption and Godot 2D's actual semantics; ignoring one produces a spec that certifies wrong or never latches.
provenance: notes/engines/GODOT_DOCS_MINING.md §1/§5/§6 (Godot 4.7 official-docs mining) cross-checked against godotworld/SPEC.md §6-8 and the frozen runner.gd; godotengine docs, paraphrased. Some NEVER-lines re-verified against thedivergentai/gd-agentic-skills (LGPLv3), paraphrased.
---

# Engine truths the spec must respect

Doc-mined facts where a naive assumption breaks a spec. Each is a design
constraint, not trivia.

## Stillness & settling (park / dock / land goals)

- **Bodies coast FOREVER.** Damping is 0 and sleeping is disabled, so a free body
  never auto-settles. A "come to rest / park / land" success predicate is
  **unsatisfiable** unless you give it a way to stop: an `on_step` `velocity_clamp`
  on the controlled body, or a friction surface it presses into. Assert this before
  you write any `speed(b) < ε` clause.
- **Design a stillness WINDOW, not a stillness instant.** Because motion only stops
  via clamp/friction, gate success on `speed < small` AND a pose/containment clause
  together — a compound latch the agent must actively hold, not a value it drifts
  through for one tick.

## Containment (`contained()` for parking / docking)

- **`contained(a,b)` is AABB-in-AABB, conservative for rotated bodies.** A tilted
  box/poly has an INFLATED axis-aligned box, so "fully inside the slot" can read
  FALSE when the body is geometrically inside. If the body is spun by `torque`,
  either make the zone generous or add `locked_rotation` where the theme allows.
- **A removed or missing body is never contained** (reads pos=vel=0). Don't gate
  success on containing a body a `remove_when` deleted.

## Overlap / contact timing

- **Overlap is one-step-latent.** Sensor overlap lists reflect PRE-move positions
  and are empty until a step runs; a freshly added body registers only after one
  step. Never design a goal that must fire at tick 0 from an overlap — it can't.
- **`max_contacts_reported = 8`** silently drops extra contacts: a goal expecting a
  body to touch >8 others (dense pile-ups) may read "not grounded" falsely. Keep
  contact-counting goals sparse.
- **CCD is off; fast bodies tunnel thin walls** in one 1/60 s tick. Make bounding
  walls THICK relative to the top speed, or `contacts()/contained()/grounded()` may
  never fire against them.

## Coordinate & sign facts

- **Down is -Y.** Gravity is `(0,-900)`; y is UP everywhere in predicates. A
  "below the floor / falling" clause is `pos_y(b) < threshold`, not `>`.
- **Escape = failure.** A dynamic body beyond the world bound + margin is an escape
  (G1). Bound the arena and expect the edge to be lethal.

## Predicate grammar (the DSL is `Expression`, not Python)

- **Boolean words only: `and` `or` `not`.** `&&` `||` `&` `|` are REJECTED at
  spec-load.
- **`/` is INTEGER division when both operands are int.** `steps / 2` floors; write
  `steps / 2.0` when you want a fraction.
- **Every query arg is REQUIRED.** A short call (missing arg) evaluates to null →
  the predicate is silently False forever. Pass all args; body/flag names are string
  literals.
- **No attribute access or indexing.** `.` `[` `]` are rejected — only the
  whitelisted query fns (`pos_x/pos_y/vel_x/vel_y/speed/angle/grounded/contacts/
  contained/dist/flag`), the variable `steps`, math builtins
  (`abs min max clamp sqrt floor ceil`), comparisons, and `and/or/not`.
- **`success`/`failure` must be pure bools, False at t=0** (G2). Checkpoints are an
  ordered 1-6 map of `snake_case` bools, each False at t=0, latching in order.

## Sensors (the raycast fan)

- **Proximity convention:** each ray reports `(ray_length - hit_dist)/ray_length` ∈
  [0,1] — **0 means no hit; larger means nearer.** A ray never sees its own host.
  Design the world so the fan discriminates the thing that matters.
- **Sensor specs settle one uncounted physics frame before the first read** (a
  RayCast2D isn't in the space until a step elapses). Harmless, but don't expect a
  meaningful sensor read at literal tick 0.
