"""SceneSDK -- instrumented wrapper around pymunk.Space.

LLM-generated scene code talks ONLY to this API: it never imports pymunk
directly. Conventions: world 800x600, y UP, gravity (0, -900), ground = static
segment at y=0, dt = 1/60, deterministic seed. No pixels: everything is engine
state.
"""

from __future__ import annotations

import math
import random

import pymunk

# ---- World constants ----
GRAVITY = (0.0, -900.0)
DT = 1.0 / 60.0

# ---- Action calibration (mass-1 agent) ----
MOVE_IMPULSE = 45.0       # horizontal impulse per "left"/"right" step
MAX_MOVE_SPEED = 220.0    # capped horizontal speed (controllability)
JUMP_IMPULSE = 360.0      # vertical "jump" impulse (clearance ~65 px)

# ---- Numeric thresholds ----
CONTACT_TOL = 1.0         # px: distance below which two shapes count as touching
VMAX = 1.0e5             # beyond this: numerical explosion
GROUND_NORMAL_TOL = 0.5   # max |n.x| for a contact to count as "below" the agent


class SceneSDK:
    """Instrumented physics space. The only interface a scene sees."""

    def __init__(self, seed: int = 0, world: tuple[int, int] = (800, 600)):
        self.seed = seed
        self.world = world
        self._rng = random.Random(seed)
        self.space = pymunk.Space()
        self.space.gravity = GRAVITY

        self._bodies: dict[str, pymunk.Body] = {}
        self._shapes: dict[str, pymunk.Shape] = {}
        self._flags: dict[str, object] = {}
        self._events: list[dict] = []
        self._ct_of: dict[str, int] = {}   # name -> collision_type
        self._next_ct = 1
        self._step_count = 0
        self._agent_name: str | None = None
        self._exploded = False

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _register(self, name: str, body: pymunk.Body, shape: pymunk.Shape) -> str:
        """Register a body/shape pair under a unique name and add it to the space."""
        if name in self._bodies:
            raise ValueError(f"entity already exists: {name!r}")
        ct = self._next_ct
        self._next_ct += 1
        shape.collision_type = ct
        self._ct_of[name] = ct
        self._bodies[name] = body
        self._shapes[name] = shape
        self.space.add(body, shape)
        return name

    # ------------------------------------------------------------------ #
    # Construction API
    # ------------------------------------------------------------------ #
    def add_ground(self, friction: float = 0.9) -> str:
        """Horizontal static segment at y=0, named "ground"."""
        body = pymunk.Body(body_type=pymunk.Body.STATIC)
        w = self.world[0]
        shape = pymunk.Segment(body, (0, 0), (w, 0), 1.0)
        shape.friction = friction
        return self._register("ground", body, shape)

    def add_wall(self, name, a: tuple, b: tuple, friction: float = 0.9) -> str:
        """Arbitrary static segment (wall)."""
        body = pymunk.Body(body_type=pymunk.Body.STATIC)
        shape = pymunk.Segment(body, tuple(a), tuple(b), 1.0)
        shape.friction = friction
        return self._register(name, body, shape)

    def add_box(self, name, pos, size=(40, 40), mass=1.0, *, body="dynamic",
                friction=0.7, elasticity=0.1) -> str:
        """Box (dynamic by default, or static)."""
        if body == "static":
            b = pymunk.Body(body_type=pymunk.Body.STATIC)
        else:
            moment = pymunk.moment_for_box(mass, tuple(size))
            b = pymunk.Body(mass, moment)
        b.position = tuple(pos)
        shape = pymunk.Poly.create_box(b, tuple(size))
        shape.friction = friction
        shape.elasticity = elasticity
        return self._register(name, b, shape)

    def add_ball(self, name, pos, radius=15.0, mass=1.0, *, body="dynamic",
                 friction=0.7, elasticity=0.5) -> str:
        """Disk (dynamic by default, or static)."""
        if body == "static":
            b = pymunk.Body(body_type=pymunk.Body.STATIC)
        else:
            moment = pymunk.moment_for_circle(mass, 0, radius)
            b = pymunk.Body(mass, moment)
        b.position = tuple(pos)
        shape = pymunk.Circle(b, radius)
        shape.friction = friction
        shape.elasticity = elasticity
        return self._register(name, b, shape)

    def add_platform(self, name, pos, size=(120, 12)) -> str:
        """Platform: static box."""
        return self.add_box(name, pos, size=size, body="static", friction=0.9)

    def spawn_agent(self, pos, size=(24, 36), mass=1.0) -> str:
        """Agent: dynamic box named "agent", rotation locked (moment=inf)."""
        b = pymunk.Body(mass, float("inf"))   # infinite moment -> no rotation
        b.position = tuple(pos)
        shape = pymunk.Poly.create_box(b, tuple(size))
        shape.friction = 0.9
        shape.elasticity = 0.0
        name = self._register("agent", b, shape)
        self._agent_name = name
        return name

    def add_zone(self, name, pos, size) -> str:
        """Sensor zone: static sensor box (no physical collision)."""
        b = pymunk.Body(body_type=pymunk.Body.STATIC)
        b.position = tuple(pos)
        shape = pymunk.Poly.create_box(b, tuple(size))
        shape.sensor = True
        return self._register(name, b, shape)

    # ------------------------------------------------------------------ #
    # Flags & contacts
    # ------------------------------------------------------------------ #
    def on_contact(self, a: str, b: str, flag: str, once: bool = True) -> None:
        """Set flag `flag` to True when `a` and `b` start touching."""
        ct_a = self._ct_of[a]
        ct_b = self._ct_of[b]
        state = {"done": False}

        def begin(arbiter, space, data):
            if once and state["done"]:
                return
            state["done"] = True
            self.set_flag(flag, True)

        self.space.on_collision(ct_a, ct_b, begin=begin)

    def set_flag(self, key: str, value) -> None:
        """Set a flag and log the event."""
        self._flags[key] = value
        self._events.append({"type": "flag_set", "key": key, "step": self._step_count})

    def get_flag(self, key: str, default=None):
        return self._flags.get(key, default)

    # ------------------------------------------------------------------ #
    # Instrumentation
    # ------------------------------------------------------------------ #
    def list_entities(self) -> list[str]:
        return list(self._bodies.keys())

    def query(self, name) -> dict:
        """Full state of an entity (positions, velocities, bbox, type)."""
        body = self._bodies[name]
        shape = self._shapes[name]
        bb = shape.bb
        static = body.body_type == pymunk.Body.STATIC
        return {
            "pos": [body.position.x, body.position.y],
            "vel": [body.velocity.x, body.velocity.y],
            "angle": body.angle,
            "angular_vel": body.angular_velocity,
            "bbox": [bb.left, bb.bottom, bb.right, bb.top],
            "body_type": "static" if static else "dynamic",
            "is_agent": name == self._agent_name,
        }

    @staticmethod
    def _collide(shape_a: pymunk.Shape, shape_b: pymunk.Shape):
        """Return (points, normal) between two shapes, ([], None) if disjoint.

        Guards a pymunk quirk: shapes_collide raises AssertionError when the
        shapes are too far apart (contact count == 0). We pre-filter by AABB
        overlap.
        """
        if not shape_a.bb.intersects(shape_b.bb):
            return [], None
        try:
            cps = shape_a.shapes_collide(shape_b)
        except AssertionError:
            return [], None
        return list(cps.points), cps.normal

    def contacts(self, a: str, b: str) -> bool:
        """True if the two entities touch (distance ~<= 0)."""
        points, _ = self._collide(self._shapes[a], self._shapes[b])
        return any(p.distance <= CONTACT_TOL for p in points)

    def penetration_depth(self, a: str, b: str) -> float:
        """Interpenetration depth (>0 if the shapes overlap).

        Sensors (zones) do not collide physically: overlapping a sensor is never
        a penetration."""
        sa, sb = self._shapes[a], self._shapes[b]
        if sa.sensor or sb.sensor:
            return 0.0
        points, _ = self._collide(sa, sb)
        if not points:
            return 0.0
        deepest = min(p.distance for p in points)  # negative distance = overlap
        return max(0.0, -deepest)

    def in_bounds(self, name) -> bool:
        """True if the entity's AABB is contained in the world rectangle."""
        bb = self._shapes[name].bb
        w, h = self.world
        return bb.left >= 0 and bb.bottom >= 0 and bb.right <= w and bb.top <= h

    def total_kinetic_energy(self, names: list[str] | None = None) -> float:
        """Total kinetic energy of the dynamic bodies."""
        if names is None:
            names = list(self._bodies.keys())
        total = 0.0
        for n in names:
            body = self._bodies[n]
            if body.body_type != pymunk.Body.DYNAMIC:
                continue
            v = body.velocity
            total += 0.5 * body.mass * (v.x * v.x + v.y * v.y)
            if math.isfinite(body.moment):
                total += 0.5 * body.moment * body.angular_velocity ** 2
        return total

    def teleport(self, name, pos) -> None:
        """Move an entity then reindex its shapes (mandatory)."""
        body = self._bodies[name]
        body.position = tuple(pos)
        self.space.reindex_shapes_for_body(body)

    def set_state(self, name, *, pos=None, vel=None, angle=None, angular_vel=None) -> None:
        """Inject a partial state; reindex if the position changes."""
        body = self._bodies[name]
        if pos is not None:
            body.position = tuple(pos)
        if vel is not None:
            body.velocity = tuple(vel)
        if angle is not None:
            body.angle = angle
        if angular_vel is not None:
            body.angular_velocity = angular_vel
        if pos is not None or angle is not None:
            self.space.reindex_shapes_for_body(body)

    def events(self) -> list[dict]:
        return list(self._events)

    def snapshot(self) -> dict:
        """Positions and velocities of all bodies (to measure a settling delta)."""
        out = {}
        for name, body in self._bodies.items():
            out[name] = {
                "pos": [body.position.x, body.position.y],
                "vel": [body.velocity.x, body.velocity.y],
                "angle": body.angle,
                "angular_vel": body.angular_velocity,
            }
        return out

    # ------------------------------------------------------------------ #
    # Time & actions
    # ------------------------------------------------------------------ #
    def _check_sanity(self) -> bool:
        """Check for the absence of NaN/explosion on the dynamic bodies."""
        for body in self._bodies.values():
            if body.body_type != pymunk.Body.DYNAMIC:
                continue
            px, py = body.position
            vx, vy = body.velocity
            if not (math.isfinite(px) and math.isfinite(py)
                    and math.isfinite(vx) and math.isfinite(vy)):
                return False
            if math.hypot(vx, vy) > VMAX:
                return False
        return True

    def step(self, n: int = 1) -> None:
        """Advance the simulation by n steps; check NaN/explosion each step."""
        for _ in range(n):
            if self._exploded:
                return
            self.space.step(DT)
            self._step_count += 1
            if not self._check_sanity():
                self._exploded = True
                self._events.append({"type": "nan_detected", "step": self._step_count})
                return

    def _agent_grounded(self) -> bool:
        """True if there is a ~vertical contact below the agent (support to jump)."""
        if self._agent_name is None:
            return False
        agent = self._bodies[self._agent_name]
        ay = agent.position.y
        ashape = self._shapes[self._agent_name]
        for name, shape in self._shapes.items():
            if name == self._agent_name or shape.sensor:
                continue
            points, normal = self._collide(ashape, shape)
            if not points:
                continue
            # actual contact?
            if not any(p.distance <= CONTACT_TOL for p in points):
                continue
            # ~vertical normal?
            if abs(normal.x) > GROUND_NORMAL_TOL:
                continue
            # is the contact point BELOW the agent's center?
            contact_y = min(p.point_a.y for p in points)
            if contact_y < ay:
                return True
        return False

    def apply(self, action) -> None:
        """Apply an action to the agent (or a targeted impulse).

        action in {"left","right","jump","noop"} or
        {"type":"impulse","target":name,"vector":[x,y]}.
        """
        if isinstance(action, dict):
            if action.get("type") == "impulse":
                target = action["target"]
                vx, vy = action["vector"]
                body = self._bodies[target]
                body.apply_impulse_at_local_point((vx, vy), (0, 0))
            return

        if self._agent_name is None:
            return
        agent = self._bodies[self._agent_name]

        if action == "noop":
            return
        if action == "left":
            agent.apply_impulse_at_local_point((-MOVE_IMPULSE, 0), (0, 0))
            self._clamp_horizontal(agent)
        elif action == "right":
            agent.apply_impulse_at_local_point((MOVE_IMPULSE, 0), (0, 0))
            self._clamp_horizontal(agent)
        elif action == "jump":
            if self._agent_grounded():
                agent.apply_impulse_at_local_point((0, JUMP_IMPULSE), (0, 0))

    def _clamp_horizontal(self, body: pymunk.Body) -> None:
        """Cap horizontal velocity to keep the agent controllable."""
        v = body.velocity
        if abs(v.x) > MAX_MOVE_SPEED:
            body.velocity = (math.copysign(MAX_MOVE_SPEED, v.x), v.y)
