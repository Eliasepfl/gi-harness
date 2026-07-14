# World API - the ONLY thing your code may touch

## Construction - from build(), and optionally on_step()
world.add(name, shape="box", *, pos, size=None, radius=None, a=None, b=None,
          vertices=None, mass=1.0, static=False, sensor=False, friction=0.7,
          elasticity=0.3, velocity=(0, 0), angle=0.0, locked_rotation=False) -> str
    # pos=(x,y) is REQUIRED for every shape. shape in {"box","circle","segment","poly"}.
    # box needs size=(w,h); circle needs radius; poly needs vertices=[(x,y),...];
    # segment needs a=(x,y), b=(x,y) given LOCAL to pos (use pos=(0,0) for absolute
    # endpoints). static=True -> immovable; sensor=True -> no collision but detectable.
world.remove(name)
world.pin(a, b, anchor_a=None, anchor_b=None)          # rigid PinJoint
world.pivot(a, b, point)                               # PivotJoint at a world point
world.spring(a, b, rest_length, stiffness, damping, anchor_a=None, anchor_b=None)
world.set_gravity(gx, gy)                              # any direction, or (0,0)
world.control(name)                                    # designate THE controlled body

## Dynamics - from act() and on_step()
world.impulse(name, vec)        # instantaneous momentum change
world.force(name, vec)          # continuous force for this step
world.set_velocity(name, vec)
world.set_flag(key, value)      # persistent game state
world.flag(key, default=None)
world.on_contact(a, b, flag, once=True)   # set `flag` when a and b touch
world.rng                       # a seeded random.Random - the ONLY randomness allowed
world.steps                     # int: physics steps elapsed (use for timers)

## Queries - PURE reads, for success()/failure()/on_step()
world.entities() -> list[str]
world.query(name) -> {"pos":[x,y], "vel":[vx,vy], "angle":a, "angular_vel":w,
                      "bbox":[left,bottom,right,top], "shape":str,
                      "static":bool, "sensor":bool, "controlled":bool}
world.contacts(a, b) -> bool
world.touching(name) -> list[str]        # non-sensor bodies in contact with name
world.grounded(name) -> bool             # supported from below
world.in_bounds(name, margin=0.0) -> bool
world.penetration_depth(a, b) -> float

That is the entire API. There is no step(), no snapshot, no rendering, no file
access, no imports. If it is not listed above, it does not exist for you.

# Module format - the concrete Python signatures

TITLE = "short title"
PROMPT = "the user's original prompt, verbatim"
ACTIONS = ["...", "..."]        # 2 to 8 short strings YOU choose - the whole move set

def build(world):
    """Create every entity. MUST call world.control(<name>) on exactly one dynamic body."""

def act(world, action):
    """Apply ONE action's effect (impulse/force/set_velocity/set_flag). Once per decision tick."""

def on_step(world):
    """OPTIONAL. Runs once per physics step - timers, moving hazards, scoring, custom rules."""

def success(world) -> bool:
    """PURE win predicate. Reads state only, never mutates. MUST be False at t=0."""

def failure(world) -> bool:
    """OPTIONAL. PURE lose predicate."""

def checkpoints(world) -> dict[str, bool]:
    """REQUIRED. 1 to 6 ordered milestone predicates - dict insertion order is the
    intended progression toward success. Short snake_case keys. Pure like success;
    EVERY value MUST be False at t=0. Decompose YOUR OWN rules into stages."""

# Structure-only stub - shows the SHAPE of a module, NOT a design to copy.
# It is deliberately boring: do NOT imitate its mechanic, entities, or goal.
```python
TITLE = "poke"
PROMPT = "seed prompt"
ACTIONS = ["go", "boost"]
def build(world):
    world.add("dot", "circle", pos=(120, 40), radius=12); world.control("dot")
    world.add("marker", "box", pos=(680, 40), size=(50, 50), static=True, sensor=True)
def act(world, action):
    if action == "go": world.impulse("dot", (90, 0))
    elif action == "boost": world.impulse("dot", (160, 0))
def success(world):
    return world.query("dot")["pos"][0] > 640
def checkpoints(world):
    return {"halfway": world.query("dot")["pos"][0] > 400}
```

## Optional world size (module-level constant, next to TITLE)
WORLD_SIZE = (w, h)   # width 800..2400, height 600..1600; omit for the 800x600 default.
    # The world rectangle spans x in [0, w], y in [0, h], y UP, gravity (0, -900).
    # The renderer follows the controlled body with a camera - design multi-screen levels.
