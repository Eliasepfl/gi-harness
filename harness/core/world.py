"""World -- instrumented minimal wrapper over pymunk (v2 substrate).

The LLM-generated GAME code talks ONLY to this object; it never imports pymunk
directly. Conventions: world 800x600, y UP, default gravity (0, -900),
dt = 1/60, deterministic seed. No pixels anywhere: everything is engine state.

This is the v2 successor of ``harness/sdk.py``: instead of a curated genre SDK,
``World`` is a forgiving physics substrate against which a whole game (its own
entities, actions, rules and win/lose conditions) is programmed.
"""

from __future__ import annotations

import math
import random

import pymunk

# ---- World constants ----
DT = 1.0 / 60.0

# ---- Numeric thresholds ----
CONTACT_TOL = 1.0          # px: distance below which two shapes count as touching
VMAX = 1.0e5               # beyond this |v|: numerical explosion
GROUND_NORMAL_TOL = 0.5    # max |n.x| for a contact to count as "below" a body
SEGMENT_RADIUS = 1.0       # default thickness for segment shapes


class World:
    """Instrumented minimal wrapper over pymunk. The ONLY object game code sees."""

    def __init__(self, seed: int = 0, size: tuple[int, int] = (800, 600),
                 gravity: tuple[float, float] = (0, -900)):
        self.seed = seed
        self.size = tuple(size)
        self._rng = random.Random(seed)
        self.space = pymunk.Space()
        self.space.gravity = tuple(gravity)

        self._bodies: dict[str, pymunk.Body] = {}
        self._shapes: dict[str, pymunk.Shape] = {}
        self._kind: dict[str, str] = {}                 # name -> shape kind
        self._flags: dict[str, object] = {}
        self._events: list[dict] = []
        self._ct_of: dict[str, int] = {}                # name -> collision_type int
        self._next_ct = 1
        # constraints, tracked so remove() can tear down attached joints:
        self._constraints: list[tuple[pymunk.Constraint, str, str]] = []
        self._step_count = 0
        self._controlled: str | None = None
        self._frozen = False                            # set once NaN/explosion seen
        # Parts bank (v2.2): lazily loaded + cached per World; see part().
        self._bank_version = "v1"
        self._bank_obj = None

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _register(self, name: str, body: pymunk.Body, shape: pymunk.Shape,
                  kind: str) -> str:
        """Register a body/shape pair under a unique name and add it to the space."""
        if name in self._bodies:
            raise ValueError(f"entity already exists: {name!r}")
        ct = self._next_ct
        self._next_ct += 1
        shape.collision_type = ct
        self._ct_of[name] = ct
        self._bodies[name] = body
        self._shapes[name] = shape
        self._kind[name] = kind
        self.space.add(body, shape)
        return name

    def _require(self, name: str) -> pymunk.Body:
        """Return the body for ``name`` or raise a clear error."""
        if name not in self._bodies:
            raise ValueError(f"unknown entity: {name!r}")
        return self._bodies[name]

    @staticmethod
    def _moment(kind: str, mass: float, *, size=None, radius=None,
                a=None, b=None, vertices=None) -> float:
        """Rotational moment for a dynamic body of the given shape."""
        if kind == "box":
            return pymunk.moment_for_box(mass, tuple(size))
        if kind == "circle":
            return pymunk.moment_for_circle(mass, 0, radius)
        if kind == "segment":
            return pymunk.moment_for_segment(mass, tuple(a), tuple(b), SEGMENT_RADIUS)
        if kind == "poly":
            return pymunk.moment_for_poly(mass, [tuple(v) for v in vertices])
        raise ValueError(f"unknown shape: {kind!r}")

    @staticmethod
    def _make_shape(kind: str, body: pymunk.Body, *, size=None, radius=None,
                    a=None, b=None, vertices=None) -> pymunk.Shape:
        """Build the pymunk shape for ``kind`` (geometry validated by the caller)."""
        if kind == "box":
            return pymunk.Poly.create_box(body, tuple(size))
        if kind == "circle":
            return pymunk.Circle(body, radius)
        if kind == "segment":
            return pymunk.Segment(body, tuple(a), tuple(b), SEGMENT_RADIUS)
        if kind == "poly":
            return pymunk.Poly(body, [tuple(v) for v in vertices])
        raise ValueError(f"unknown shape: {kind!r}")

    # ------------------------------------------------------------------ #
    # Construction (used by build / on_step)
    # ------------------------------------------------------------------ #
    def add(self, name: str, shape: str = "box", *, pos, size=None, radius=None,
            a=None, b=None, vertices=None, mass: float = 1.0, static: bool = False,
            sensor: bool = False, friction: float = 0.7, elasticity: float = 0.3,
            velocity=(0, 0), angle: float = 0.0, locked_rotation: bool = False) -> str:
        """Add an entity.

        ``shape`` in {"box","circle","segment","poly"}; box needs ``size=(w,h)``;
        circle needs ``radius``; segment needs ``a=(x,y), b=(x,y)`` (local to
        ``pos``); poly needs ``vertices``. ``static=True`` -> STATIC body;
        ``sensor=True`` -> no physical collision; ``locked_rotation=True`` ->
        infinite moment (no spin). Returns ``name``.
        """
        if shape not in ("box", "circle", "segment", "poly"):
            raise ValueError(
                f"unknown shape {shape!r}; expected one of box/circle/segment/poly")
        # Validate required geometry per shape.
        if shape == "box" and size is None:
            raise ValueError("box shape requires size=(w, h)")
        if shape == "circle" and radius is None:
            raise ValueError("circle shape requires radius")
        if shape == "segment" and (a is None or b is None):
            raise ValueError("segment shape requires a=(x, y) and b=(x, y)")
        if shape == "poly" and not vertices:
            raise ValueError("poly shape requires vertices=[(x, y), ...]")

        if static:
            body = pymunk.Body(body_type=pymunk.Body.STATIC)
        else:
            if locked_rotation:
                moment = float("inf")
            else:
                moment = self._moment(shape, mass, size=size, radius=radius,
                                      a=a, b=b, vertices=vertices)
            body = pymunk.Body(mass, moment)
        body.position = tuple(pos)
        body.angle = angle

        shp = self._make_shape(shape, body, size=size, radius=radius,
                               a=a, b=b, vertices=vertices)
        shp.friction = friction
        shp.elasticity = elasticity
        shp.sensor = sensor

        out = self._register(name, body, shp, shape)
        if not static:
            body.velocity = tuple(velocity)
        return out

    def remove(self, name: str) -> None:
        """Remove an entity and any constraints attached to it."""
        self._require(name)
        # Tear down attached constraints first (removing the body while a joint
        # still references it would corrupt the space).
        kept: list[tuple[pymunk.Constraint, str, str]] = []
        for con, na, nb in self._constraints:
            if na == name or nb == name:
                if con in self.space.constraints:
                    self.space.remove(con)
            else:
                kept.append((con, na, nb))
        self._constraints = kept

        body = self._bodies.pop(name)
        shape = self._shapes.pop(name)
        self.space.remove(body, shape)
        self._kind.pop(name, None)
        self._ct_of.pop(name, None)
        if self._controlled == name:
            self._controlled = None

    def pin(self, a: str, b: str, anchor_a=None, anchor_b=None) -> None:
        """Rigid PinJoint keeping a fixed distance between two anchor points."""
        ba, bb = self._require(a), self._require(b)
        con = pymunk.PinJoint(ba, bb, tuple(anchor_a or (0, 0)),
                              tuple(anchor_b or (0, 0)))
        self.space.add(con)
        self._constraints.append((con, a, b))

    def pivot(self, a: str, b: str, point) -> None:
        """PivotJoint pinning two bodies together at a world ``point``."""
        ba, bb = self._require(a), self._require(b)
        con = pymunk.PivotJoint(ba, bb, tuple(point))
        self.space.add(con)
        self._constraints.append((con, a, b))

    def spring(self, a: str, b: str, rest_length: float, stiffness: float,
               damping: float, anchor_a=None, anchor_b=None) -> None:
        """DampedSpring between two bodies."""
        ba, bb = self._require(a), self._require(b)
        con = pymunk.DampedSpring(ba, bb, tuple(anchor_a or (0, 0)),
                                  tuple(anchor_b or (0, 0)),
                                  rest_length, stiffness, damping)
        self.space.add(con)
        self._constraints.append((con, a, b))

    def set_gravity(self, gx: float, gy: float) -> None:
        """Set the world gravity vector."""
        self.space.gravity = (gx, gy)

    def control(self, name: str) -> None:
        """Designate THE controlled body (must be dynamic); last call wins."""
        body = self._require(name)
        if body.body_type != pymunk.Body.DYNAMIC:
            raise ValueError(f"controlled body must be dynamic: {name!r}")
        self._controlled = name

    def _bank(self):
        """Lazily load + cache the parts bank for this World (v2.2)."""
        if self._bank_obj is None:
            from harness.core.bank import load_bank
            self._bank_obj = load_bank(self._bank_version)
        return self._bank_obj

    def part(self, name: str, kind: str, *, pos, **overrides) -> str:
        """Instantiate a pre-certified bank part ``kind`` at ``pos`` under ``name``.

        The bank supplies a calibrated NOUN — a single body or a pre-jointed
        subassembly (e.g. a ``wrecking_ball``: anchor + ball + a correctly
        anchored pin). This one verb is the whole bank surface; every VERB
        (act/on_step/success/checkpoints) stays the game's own code, and
        ``world.add`` remains the escape hatch for anything the bank lacks.

        Sub-bodies register instance-prefixed: the PRIMARY sub-body under the
        bare ``name`` and every other under ``name.<role>`` (a ``wrecking_ball``
        called "wrecker" -> "wrecker" is the ball, "wrecker.anchor" the anchor).
        Returns the primary handle (``name``).

        ``overrides`` accepts ONLY keys in the entry's whitelist, each clamped to
        its declared range; an unknown key (e.g. ``density``) or an out-of-range
        value raises ValueError (surfaced by the verifier's G0 as ENV_ERROR).
        This method never calls ``control`` — the game keeps that choice.
        """
        resolved = self._bank().resolve(kind, name, pos, overrides)
        for body in resolved.bodies:
            self.add(body["name"], body["shape"], **body["kwargs"])
        for joint in resolved.joints:
            getattr(self, joint["verb"])(*joint["args"], **joint["kwargs"])
        return resolved.primary

    # ------------------------------------------------------------------ #
    # Game dynamics (used by act / on_step)
    # ------------------------------------------------------------------ #
    def impulse(self, name: str, vec) -> None:
        """Apply an instantaneous impulse at the body's center of mass."""
        self._require(name).apply_impulse_at_local_point(tuple(vec), (0, 0))

    def force(self, name: str, vec) -> None:
        """Apply a force at the center of mass (reset by pymunk each step)."""
        self._require(name).apply_force_at_local_point(tuple(vec), (0, 0))

    def set_velocity(self, name: str, vec) -> None:
        """Overwrite a body's linear velocity."""
        self._require(name).velocity = tuple(vec)

    def set_flag(self, key: str, value) -> None:
        """Set a flag and log the event (with the current step index)."""
        self._flags[key] = value
        self._events.append({"type": "flag_set", "key": key, "step": self._step_count})

    def flag(self, key: str, default=None):
        """Read a flag value."""
        return self._flags.get(key, default)

    def on_contact(self, a: str, b: str, flag: str, once: bool = True) -> None:
        """Set ``flag`` to True (via set_flag, so it is logged) when a and b touch."""
        ct_a = self._ct_of[a]
        ct_b = self._ct_of[b]
        state = {"done": False}

        def begin(arbiter, space, data):
            if once and state["done"]:
                return
            state["done"] = True
            self.set_flag(flag, True)

        self.space.on_collision(ct_a, ct_b, begin=begin)

    @property
    def rng(self) -> random.Random:
        """The ONLY allowed source of randomness (seeded)."""
        return self._rng

    @property
    def steps(self) -> int:
        """Number of physics steps elapsed."""
        return self._step_count

    # ------------------------------------------------------------------ #
    # Collision utility
    # ------------------------------------------------------------------ #
    @staticmethod
    def _collide(shape_a: pymunk.Shape, shape_b: pymunk.Shape):
        """Return (points, normal) between two shapes; ([], None) if disjoint.

        Guards a pymunk quirk: ``shapes_collide`` raises AssertionError when the
        shapes are far apart (contact count == 0). We pre-filter by AABB overlap.
        """
        if not shape_a.bb.intersects(shape_b.bb):
            return [], None
        try:
            cps = shape_a.shapes_collide(shape_b)
        except AssertionError:
            return [], None
        return list(cps.points), cps.normal

    # ------------------------------------------------------------------ #
    # Pure queries (used by success / failure / policies / verifier)
    # ------------------------------------------------------------------ #
    def entities(self) -> list[str]:
        """All entity names, in insertion order (deterministic)."""
        return list(self._bodies.keys())

    def query(self, name: str) -> dict:
        """Full state of an entity."""
        body = self._require(name)
        shape = self._shapes[name]
        bb = shape.bb
        static = body.body_type == pymunk.Body.STATIC
        out = {
            "pos": [body.position.x, body.position.y],
            "vel": [body.velocity.x, body.velocity.y],
            "angle": body.angle,
            "angular_vel": body.angular_velocity,
            "bbox": [bb.left, bb.bottom, bb.right, bb.top],
            "shape": self._kind[name],
            "static": static,
            "sensor": bool(shape.sensor),
            "controlled": name == self._controlled,
        }
        # World-space outline so renderers can draw ROTATED shapes truthfully
        # (an axis-aligned bbox turns a tilted plank into a bloated slab).
        if isinstance(shape, pymunk.Poly):
            out["verts"] = [[v.x, v.y] for v in
                            (body.local_to_world(lv) for lv in shape.get_vertices())]
        elif isinstance(shape, pymunk.Segment):
            a = body.local_to_world(shape.a)
            b = body.local_to_world(shape.b)
            out["verts"] = [[a.x, a.y], [b.x, b.y]]
            out["radius"] = float(shape.radius)
        elif isinstance(shape, pymunk.Circle):
            out["radius"] = float(shape.radius)
        return out

    def contacts(self, a: str, b: str) -> bool:
        """True if the two entities touch (distance ~<= 0)."""
        self._require(a)
        self._require(b)
        points, _ = self._collide(self._shapes[a], self._shapes[b])
        return any(p.distance <= CONTACT_TOL for p in points)

    def touching(self, name: str) -> list[str]:
        """Names of non-sensor entities in contact with ``name``."""
        self._require(name)
        shape = self._shapes[name]
        out = []
        for other, oshape in self._shapes.items():
            if other == name or oshape.sensor or shape.sensor:
                continue
            points, _ = self._collide(shape, oshape)
            if any(p.distance <= CONTACT_TOL for p in points):
                out.append(other)
        return out

    def grounded(self, name: str) -> bool:
        """True if ``name`` is supported from below (contact normal ~vertical)."""
        body = self._require(name)
        cy = body.position.y
        shape = self._shapes[name]
        for other, oshape in self._shapes.items():
            if other == name or oshape.sensor:
                continue
            points, normal = self._collide(shape, oshape)
            if not points or normal is None:
                continue
            if not any(p.distance <= CONTACT_TOL for p in points):
                continue
            if abs(normal.x) > GROUND_NORMAL_TOL:      # not a ~vertical normal
                continue
            contact_y = min(p.point_a.y for p in points)
            if contact_y < cy:                          # support is below center
                return True
        return False

    def in_bounds(self, name: str, margin: float = 0.0) -> bool:
        """True if the entity's AABB lies within the world rectangle (+margin)."""
        self._require(name)
        bb = self._shapes[name].bb
        w, h = self.size
        return (bb.left >= -margin and bb.bottom >= -margin
                and bb.right <= w + margin and bb.top <= h + margin)

    def penetration_depth(self, a: str, b: str) -> float:
        """Interpenetration depth (>0 if overlapping); 0.0 if either is a sensor."""
        self._require(a)
        self._require(b)
        sa, sb = self._shapes[a], self._shapes[b]
        if sa.sensor or sb.sensor:
            return 0.0
        points, _ = self._collide(sa, sb)
        if not points:
            return 0.0
        deepest = min(p.distance for p in points)       # negative distance = overlap
        return max(0.0, -deepest)

    # ------------------------------------------------------------------ #
    # Harness side (verifier / renderer)
    # ------------------------------------------------------------------ #
    def _sane(self) -> bool:
        """True if no dynamic body shows NaN/explosion."""
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
        """Advance the simulation ``n`` steps; freeze on NaN/explosion (logged once)."""
        for _ in range(n):
            if self._frozen:
                return
            self.space.step(DT)
            self._step_count += 1
            if not self._sane():
                self._frozen = True
                self._events.append({"type": "nan_detected", "step": self._step_count})
                return

    def snapshot(self) -> dict:
        """pos/vel/angle of all bodies, in deterministic (insertion) order."""
        out = {}
        for name, body in self._bodies.items():
            out[name] = {
                "pos": [body.position.x, body.position.y],
                "vel": [body.velocity.x, body.velocity.y],
                "angle": body.angle,
            }
        return out

    def events(self) -> list[dict]:
        """Recorded events (flag_set, nan_detected, ...)."""
        return list(self._events)

    def teleport(self, name: str, pos) -> None:
        """Move an entity then reindex its shapes (mandatory for query correctness)."""
        body = self._require(name)
        body.position = tuple(pos)
        self.space.reindex_shapes_for_body(body)

    def kinetic_energy(self, names=None) -> float:
        """Total kinetic energy of the given dynamic bodies (all by default)."""
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

    def controlled(self) -> str | None:
        """Name of the controlled body, or None."""
        return self._controlled
