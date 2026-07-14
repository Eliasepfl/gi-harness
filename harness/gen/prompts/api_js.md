# World API - the ONLY thing your code may touch

## Construction - from build(), and optionally on_step()
world.add(name, shape, opts) -> name
    // opts is an OBJECT. shape is one of "box" | "circle" | "segment" | "poly".
    // opts.pos = [x, y] is REQUIRED for every shape.
    //   box    needs opts.size = [w, h]
    //   circle needs opts.radius = r
    //   poly   needs opts.vertices = [[x, y], ...]   (>= 3, convex)
    //   segment needs opts.a = [x, y] and opts.b = [x, y]  (LOCAL to pos; use pos=[0,0] for absolute)
    // Optional opts (with defaults): mass=1.0, static=false, sensor=false,
    //   friction=0.7, elasticity=0.3, velocity=[0,0], angle=0.0, locked_rotation=false.
    //   static:true -> immovable; sensor:true -> no collision but still detectable.
world.remove(name)
world.pin(a, b)                                   // rigid link (optional anchorA, anchorB)
world.pivot(a, b, point)                          // hinge two bodies at a world point [x,y]
world.spring(a, b, restLength, stiffness, damping)   // soft spring (optional anchors)
world.set_gravity(gx, gy)                         // any direction, or (0, 0)
world.control(name)                               // designate THE controlled body

## Dynamics - from act() and on_step()
world.impulse(name, [fx, fy])     // instantaneous momentum change
world.force(name, [fx, fy])       // continuous force for this step
world.set_velocity(name, [vx, vy])
world.set_flag(key, value)        // persistent game state
world.flag(key, def)              // read a flag (def if unset)
world.on_contact(a, b, flag, once)   // set `flag` true when a and b touch
world.rng                         // seeded RNG: .random(), .uniform(a,b), .randint(a,b), .choice(arr) - the ONLY randomness allowed
world.steps                       // int: physics steps elapsed (use for timers)

## Queries - PURE reads, for success()/failure()/on_step()
world.entities() -> [name, ...]
world.query(name) -> { pos:[x,y], vel:[vx,vy], angle, angular_vel,
                       bbox:[left,bottom,right,top], shape,
                       static, sensor, controlled }
world.contacts(a, b) -> bool
world.touching(name) -> [name, ...]     // non-sensor bodies in contact with name
world.grounded(name) -> bool            // supported from below
world.in_bounds(name, margin) -> bool
world.penetration_depth(a, b) -> number

That is the entire API. There is no step(), no snapshot, no rendering, no file
access, no modules. `Math` is available. If it is not listed above, it does not
exist for you.

# Module format - the concrete JavaScript signatures (top-level const/function; no require/import/exports)

const TITLE = "short title";
const PROMPT = "the user's original prompt, verbatim";
const ACTIONS = ["...", "..."];        // 2 to 8 short strings YOU choose - the whole move set

function build(world) {
    // Create every entity. MUST call world.control(<name>) on exactly one dynamic body.
}

function act(world, action) {
    // Apply ONE action's effect (impulse/force/set_velocity/set_flag). Once per decision tick.
}

function on_step(world) {
    // OPTIONAL. Runs once per physics step - timers, moving hazards, scoring, custom rules.
}

function success(world) {
    // PURE win predicate. Returns a boolean. Reads state only, never mutates. MUST be false at t=0.
}

function failure(world) {
    // OPTIONAL. PURE lose predicate returning a boolean.
}

function checkpoints(world) {
    // REQUIRED. Return a PLAIN OBJECT of 1 to 6 ordered milestone predicates -
    // property insertion order is the intended progression toward success. Short
    // snake_case keys, boolean values. Pure like success; EVERY value MUST be
    // false at t=0. Decompose YOUR OWN rules into stages.
}

# Structure-only stub - shows the SHAPE of a module, NOT a design to copy.
# It is deliberately boring: do NOT imitate its mechanic, entities, or goal.
```javascript
const TITLE = "poke";
const PROMPT = "seed prompt";
const ACTIONS = ["go", "boost"];
function build(world) {
    world.add("dot", "circle", { pos: [120, 40], radius: 12 });
    world.control("dot");
    world.add("marker", "box", { pos: [680, 40], size: [50, 50], static: true, sensor: true });
}
function act(world, action) {
    if (action === "go") world.impulse("dot", [90, 0]);
    else if (action === "boost") world.impulse("dot", [160, 0]);
}
function success(world) {
    return world.query("dot").pos[0] > 640;
}
function checkpoints(world) {
    return { halfway: world.query("dot").pos[0] > 400 };
}
```
