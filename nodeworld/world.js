"use strict";
/*
 * world.js -- instrumented minimal substrate over Planck.js (Box2D), the JS
 * mirror of harness/world.py (CONTRACTS §1). The LLM-generated GAME code talks
 * ONLY to this object; it never touches planck directly.
 *
 * Conventions (identical to the pymunk substrate): world 800x600, y UP, default
 * gravity (0, -900), dt = 1/60 fixed, deterministic seed. No pixels anywhere in
 * verification -- everything reads engine state.
 *
 * PIXEL-SCALE NOTE (honest spike engineering): Box2D is tuned for MKS (meters),
 * with internal length limits (maxTranslation = 2 m/step, linearSlop = 0.005 m)
 * that would clamp a pixel-scale world where a puck moves several px/step. We
 * keep the EXTERNAL API in pixels (to mirror world.py exactly) and retune the two
 * length knobs Planck exposes for exactly this: `lengthUnitsPerMeter` scales the
 * length-based tolerances, and `maxTranslation` lifts the per-step travel cap.
 * A production port would instead pick an explicit px<->m scale; this is called
 * out in SPIKE_REPORT.md as an API-parity note, not a silent hack.
 */

const pl = require("planck");

// ---- Pixel-scale retuning (must happen before any World is built) ----
pl.Settings.lengthUnitsPerMeter = 50; // treat ~50 px as 1 "Box2D meter" of tolerance
// 200 px per 1/60s substep (= 12000 px/s) is far above any sane gameplay speed
// but low enough that the solver keeps contacts coherent; 1000 allowed bodies to
// cross a crate-sized obstacle in one substep (measured 15px interpenetration).
pl.Settings.maxTranslation = 200;

const { World: PlWorld, Vec2, Circle, Box, Edge, Polygon } = pl;

// ---- World constants (fixed; mirror world.py) ----
const DT = 1.0 / 60.0;
const VELOCITY_ITERATIONS = 8; // Box2D solver velocity passes [eng., fixed]
const POSITION_ITERATIONS = 3; // Box2D solver position passes [eng., fixed]

// ---- Numeric thresholds (mirror world.py) ----
const CONTACT_TOL = 1.0; // px: separation below which two shapes count as touching
const VMAX = 1.0e5; // beyond this |v|: numerical explosion
const GROUND_NORMAL_TOL = 0.5; // max |n.x| for a contact to count as "below" a body

// ---------------------------------------------------------------------------
// Deterministic RNG (mulberry32) -- determinism matters more than quality.
// Mirrors the surface of Python's random.Random that game code uses.
// ---------------------------------------------------------------------------
class SeededRandom {
  constructor(seed) {
    this._state = (seed >>> 0) || 1;
  }
  random() {
    // mulberry32
    let t = (this._state += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  uniform(a, b) {
    return a + (b - a) * this.random();
  }
  randint(a, b) {
    // inclusive [a, b], mirroring Python's random.randint
    return a + Math.floor(this.random() * (b - a + 1));
  }
  choice(arr) {
    return arr[Math.floor(this.random() * arr.length)];
  }
}

// ---------------------------------------------------------------------------
// World
// ---------------------------------------------------------------------------
class World {
  constructor(seed = 0, size = [800, 600], gravity = [0, -900]) {
    this.seed = seed;
    this.size = [size[0], size[1]];
    this._rng = new SeededRandom(seed);

    this._world = new PlWorld(new Vec2(gravity[0], gravity[1]));
    this._world.setAllowSleeping(false); // mirror pymunk: bodies never sleep

    // Insertion-ordered registries (Map preserves insertion order deterministically).
    this._bodies = new Map(); // name -> planck Body
    this._fixtures = new Map(); // name -> planck Fixture (the primary fixture)
    this._geom = new Map(); // name -> {kind, ...local geometry} for bbox math
    this._flags = new Map(); // key -> value
    this._events = []; // [{type, ...}]
    this._joints = []; // [{joint, a, b}] so remove() can tear down
    this._contactRules = []; // [{a, b, flag, once, fired}]

    this._stepCount = 0;
    this._controlled = null;
    this._frozen = false;

    // Single begin-contact listener drives on_contact rules.
    this._world.on("begin-contact", (contact) => this._onBeginContact(contact));
  }

  // ------------------------------------------------------------------ //
  // Internal helpers
  // ------------------------------------------------------------------ //
  _require(name) {
    const body = this._bodies.get(name);
    if (body === undefined) throw new Error(`unknown entity: ${name}`);
    return body;
  }

  _makeShape(kind, opts) {
    switch (kind) {
      case "box": {
        const [w, h] = opts.size;
        return new Box(w / 2, h / 2); // Planck Box takes HALF extents
      }
      case "circle":
        return new Circle(opts.radius);
      case "segment":
        return new Edge(new Vec2(opts.a[0], opts.a[1]), new Vec2(opts.b[0], opts.b[1]));
      case "poly":
        return new Polygon(opts.vertices.map((v) => new Vec2(v[0], v[1])));
      default:
        throw new Error(`unknown shape: ${kind}`);
    }
  }

  // Local vertices/geometry kept for deterministic tight-bbox computation.
  _storeGeom(name, kind, opts) {
    if (kind === "box") {
      const w = opts.size[0] / 2;
      const h = opts.size[1] / 2;
      this._geom.set(name, {
        kind,
        verts: [
          [-w, -h],
          [w, -h],
          [w, h],
          [-w, h],
        ],
      });
    } else if (kind === "circle") {
      this._geom.set(name, { kind, radius: opts.radius });
    } else if (kind === "segment") {
      this._geom.set(name, { kind, verts: [opts.a.slice(), opts.b.slice()] });
    } else if (kind === "poly") {
      this._geom.set(name, { kind, verts: opts.vertices.map((v) => v.slice()) });
    }
  }

  static _shapeArea(kind, opts) {
    if (kind === "box") return opts.size[0] * opts.size[1];
    if (kind === "circle") return Math.PI * opts.radius * opts.radius;
    if (kind === "poly") {
      const v = opts.vertices;
      let a = 0;
      for (let i = 0; i < v.length; i++) {
        const j = (i + 1) % v.length;
        a += v[i][0] * v[j][1] - v[j][0] * v[i][1];
      }
      return Math.abs(a) / 2;
    }
    return 0; // segment: zero area
  }

  // world-space point of a local point on `name`'s body (deterministic, no skin)
  _toWorld(body, lx, ly) {
    const p = body.getPosition();
    const ang = body.getAngle();
    const c = Math.cos(ang);
    const s = Math.sin(ang);
    return [p.x + c * lx - s * ly, p.y + s * lx + c * ly];
  }

  _bbox(name) {
    const body = this._require(name);
    const g = this._geom.get(name);
    if (g.kind === "circle") {
      const [cx, cy] = this._toWorld(body, 0, 0);
      const r = g.radius;
      return [cx - r, cy - r, cx + r, cy + r];
    }
    let left = Infinity;
    let bottom = Infinity;
    let right = -Infinity;
    let top = -Infinity;
    for (const [lx, ly] of g.verts) {
      const [wx, wy] = this._toWorld(body, lx, ly);
      if (wx < left) left = wx;
      if (wx > right) right = wx;
      if (wy < bottom) bottom = wy;
      if (wy > top) top = wy;
    }
    return [left, bottom, right, top];
  }

  _onBeginContact(contact) {
    const na = contact.getFixtureA().getBody().getUserData();
    const nb = contact.getFixtureB().getBody().getUserData();
    for (const rule of this._contactRules) {
      if (rule.once && rule.fired) continue;
      const match =
        (rule.a === na && rule.b === nb) || (rule.a === nb && rule.b === na);
      if (match) {
        rule.fired = true;
        this.setFlag(rule.flag, true);
      }
    }
  }

  // ------------------------------------------------------------------ //
  // Construction (used by build / on_step)
  // ------------------------------------------------------------------ //
  add(name, shape = "box", opts = {}) {
    if (this._bodies.has(name)) throw new Error(`entity already exists: ${name}`);
    if (!["box", "circle", "segment", "poly"].includes(shape)) {
      throw new Error(`unknown shape ${shape}; expected box/circle/segment/poly`);
    }
    // Validate required geometry per shape (mirror world.py).
    if (shape === "box" && opts.size == null) throw new Error("box shape requires size=[w,h]");
    if (shape === "circle" && opts.radius == null) throw new Error("circle shape requires radius");
    if (shape === "segment" && (opts.a == null || opts.b == null)) {
      throw new Error("segment shape requires a=[x,y] and b=[x,y]");
    }
    if (shape === "poly" && (!opts.vertices || opts.vertices.length < 3)) {
      throw new Error("poly shape requires vertices=[[x,y],...]");
    }

    const pos = opts.pos;
    if (pos == null) throw new Error(`entity ${name} requires pos`);
    const isStatic = !!opts.static;
    const mass = opts.mass == null ? 1.0 : opts.mass;
    const friction = opts.friction == null ? 0.7 : opts.friction;
    const elasticity = opts.elasticity == null ? 0.3 : opts.elasticity;
    const angle = opts.angle == null ? 0.0 : opts.angle;
    const lockedRotation = !!opts.locked_rotation;

    const body = this._world.createBody({
      type: isStatic ? "static" : "dynamic",
      position: new Vec2(pos[0], pos[1]),
      angle,
      fixedRotation: lockedRotation, // mirror pymunk moment=inf
      bullet: !isStatic, // CCD for every dynamic body: gameplay speeds routinely
      // exceed one body-width per substep, and dynamic-vs-dynamic tunneling was
      // observed (player half-inside a crate). Body counts are tiny (<=14).
      userData: name,
    });

    const shp = this._makeShape(shape, opts);
    let density = 1.0;
    if (!isStatic) {
      const area = World._shapeArea(shape, opts);
      density = area > 0 ? mass / area : 1.0; // density * area = mass
    }
    const fixture = body.createFixture({
      shape: shp,
      density,
      friction,
      restitution: elasticity,
      isSensor: !!opts.sensor,
    });

    // Zero-area dynamic bodies (e.g. a dynamic segment) get no mass from density;
    // force the requested mass explicitly so it behaves.
    if (!isStatic && body.getMass() === 0) {
      body.setMassData({ mass, center: new Vec2(0, 0), I: lockedRotation ? 0 : mass });
    }

    if (!isStatic && opts.velocity) {
      body.setLinearVelocity(new Vec2(opts.velocity[0], opts.velocity[1]));
    }

    this._bodies.set(name, body);
    this._fixtures.set(name, fixture);
    this._storeGeom(name, shape, opts);
    return name;
  }

  remove(name) {
    const body = this._require(name);
    // Planck destroys attached joints/fixtures with the body; just drop tracking.
    this._joints = this._joints.filter((j) => j.a !== name && j.b !== name);
    this._world.destroyBody(body);
    this._bodies.delete(name);
    this._fixtures.delete(name);
    this._geom.delete(name);
    if (this._controlled === name) this._controlled = null;
  }

  pin(a, b, anchorA = null, anchorB = null) {
    // Rigid fixed-distance link -> Planck DistanceJoint (stiff).
    const ba = this._require(a);
    const bb = this._require(b);
    const pa = anchorA ? ba.getWorldPoint(new Vec2(anchorA[0], anchorA[1])) : ba.getWorldCenter();
    const pb = anchorB ? bb.getWorldPoint(new Vec2(anchorB[0], anchorB[1])) : bb.getWorldCenter();
    const joint = pl.DistanceJoint({ frequencyHz: 0, dampingRatio: 0 }, ba, bb, pa, pb);
    this._world.createJoint(joint);
    this._joints.push({ joint, a, b });
  }

  pivot(a, b, point) {
    // Pin two bodies together at a world point -> Planck RevoluteJoint.
    const ba = this._require(a);
    const bb = this._require(b);
    const joint = pl.RevoluteJoint({}, ba, bb, new Vec2(point[0], point[1]));
    this._world.createJoint(joint);
    this._joints.push({ joint, a, b });
  }

  spring(a, b, restLength, stiffness, damping, anchorA = null, anchorB = null) {
    // Soft spring -> Planck DistanceJoint with frequency/damping.
    const ba = this._require(a);
    const bb = this._require(b);
    const pa = anchorA ? ba.getWorldPoint(new Vec2(anchorA[0], anchorA[1])) : ba.getWorldCenter();
    const pb = anchorB ? bb.getWorldPoint(new Vec2(anchorB[0], anchorB[1])) : bb.getWorldCenter();
    // Map pymunk (stiffness, damping) to Box2D (frequencyHz, dampingRatio) heuristically.
    const freqHz = Math.max(0.01, Math.sqrt(Math.max(stiffness, 0)) / (2 * Math.PI));
    const dampingRatio = Math.min(1.0, Math.max(0, damping / 100));
    const joint = pl.DistanceJoint(
      { frequencyHz: freqHz, dampingRatio, length: restLength },
      ba,
      bb,
      pa,
      pb
    );
    this._world.createJoint(joint);
    this._joints.push({ joint, a, b });
  }

  set_gravity(gx, gy) {
    this._world.setGravity(new Vec2(gx, gy));
  }

  control(name) {
    const body = this._require(name);
    if (body.isStatic()) throw new Error(`controlled body must be dynamic: ${name}`);
    this._controlled = name;
  }

  // ------------------------------------------------------------------ //
  // Game dynamics (used by act / on_step)
  // ------------------------------------------------------------------ //
  impulse(name, vec) {
    const body = this._require(name);
    body.applyLinearImpulse(new Vec2(vec[0], vec[1]), body.getWorldCenter(), true);
  }

  force(name, vec) {
    const body = this._require(name);
    body.applyForceToCenter(new Vec2(vec[0], vec[1]), true);
  }

  set_velocity(name, vec) {
    const body = this._require(name);
    body.setLinearVelocity(new Vec2(vec[0], vec[1]));
  }

  set_flag(key, value) {
    this._flags.set(key, value);
    this._events.push({ type: "flag_set", key, step: this._stepCount });
  }
  // camelCase alias used internally by the contact listener
  setFlag(key, value) {
    this.set_flag(key, value);
  }

  flag(key, def = null) {
    return this._flags.has(key) ? this._flags.get(key) : def;
  }

  on_contact(a, b, flag, once = true) {
    this._require(a);
    this._require(b);
    this._contactRules.push({ a, b, flag, once, fired: false });
  }

  get rng() {
    return this._rng;
  }

  get steps() {
    return this._stepCount;
  }

  // ------------------------------------------------------------------ //
  // Pure queries (used by success / failure / policies / verifier)
  // ------------------------------------------------------------------ //
  entities() {
    return Array.from(this._bodies.keys());
  }

  query(name) {
    const body = this._require(name);
    const fixture = this._fixtures.get(name);
    const p = body.getPosition();
    const v = body.getLinearVelocity();
    return {
      pos: [p.x, p.y],
      vel: [v.x, v.y],
      angle: body.getAngle(),
      angular_vel: body.getAngularVelocity(),
      bbox: this._bbox(name),
      shape: this._geom.get(name).kind,
      static: body.isStatic(),
      sensor: fixture.isSensor(),
      controlled: name === this._controlled,
    };
  }

  // Iterate every touching contact that involves `name`.
  _contactsOf(name) {
    const out = [];
    for (let ce = this._require(name).getContactList(); ce; ce = ce.next) {
      const contact = ce.contact;
      if (!contact.isTouching()) continue;
      const other = ce.other.getUserData();
      out.push({ contact, other });
    }
    return out;
  }

  contacts(a, b) {
    this._require(a);
    this._require(b);
    for (const { contact, other } of this._contactsOf(a)) {
      if (other === b) {
        const wm = contact.getWorldManifold();
        if (!wm) return true; // touching but no manifold -> count it
        const seps = wm.separations || [];
        if (seps.length === 0) return true;
        if (seps.some((s) => s <= CONTACT_TOL)) return true;
      }
    }
    return false;
  }

  touching(name) {
    this._require(name);
    const myFixtureSensor = this._fixtures.get(name).isSensor();
    if (myFixtureSensor) return [];
    const out = [];
    for (const { other } of this._contactsOf(name)) {
      const of = this._fixtures.get(other);
      if (of && of.isSensor()) continue; // non-sensor only
      if (!out.includes(other)) out.push(other);
    }
    return out;
  }

  grounded(name) {
    const body = this._require(name);
    const cy = body.getPosition().y;
    for (const { contact, other } of this._contactsOf(name)) {
      const of = this._fixtures.get(other);
      if (of && of.isSensor()) continue;
      const wm = contact.getWorldManifold();
      if (!wm) continue;
      // Box2D normal points from A to B; orient it so it points away from `name`.
      let nx = wm.normal.x;
      let ny = wm.normal.y;
      const aName = contact.getFixtureA().getBody().getUserData();
      if (aName !== name) {
        nx = -nx;
        ny = -ny;
      }
      if (Math.abs(nx) > GROUND_NORMAL_TOL) continue; // not ~vertical
      // support below the body center: contact point y < center y
      const pts = wm.points || [];
      if (pts.some((pt) => pt.y < cy)) return true;
      // fall back to normal orientation if no points reported
      if (pts.length === 0 && ny > 0) return true;
    }
    return false;
  }

  in_bounds(name, margin = 0.0) {
    const [left, bottom, right, top] = this._bbox(name);
    const [w, h] = this.size;
    return left >= -margin && bottom >= -margin && right <= w + margin && top <= h + margin;
  }

  penetration_depth(a, b) {
    this._require(a);
    this._require(b);
    if (this._fixtures.get(a).isSensor() || this._fixtures.get(b).isSensor()) return 0.0;
    for (const { contact, other } of this._contactsOf(a)) {
      if (other !== b) continue;
      const wm = contact.getWorldManifold();
      if (!wm) continue;
      const seps = wm.separations || [];
      if (seps.length === 0) continue;
      const deepest = Math.min(...seps); // negative separation = overlap
      return Math.max(0.0, -deepest);
    }
    return 0.0;
  }

  // ------------------------------------------------------------------ //
  // Harness side (verifier / renderer)
  // ------------------------------------------------------------------ //
  _sane() {
    for (const body of this._bodies.values()) {
      if (body.isStatic()) continue;
      const p = body.getPosition();
      const v = body.getLinearVelocity();
      if (!(Number.isFinite(p.x) && Number.isFinite(p.y) && Number.isFinite(v.x) && Number.isFinite(v.y))) {
        return false;
      }
      if (Math.hypot(v.x, v.y) > VMAX) return false;
    }
    return true;
  }

  step(n = 1) {
    for (let i = 0; i < n; i++) {
      if (this._frozen) return;
      this._world.step(DT, VELOCITY_ITERATIONS, POSITION_ITERATIONS);
      this._world.clearForces();
      this._stepCount += 1;
      if (!this._sane()) {
        this._frozen = true;
        this._events.push({ type: "nan_detected", step: this._stepCount });
        return;
      }
    }
  }

  snapshot() {
    // deterministic (insertion) key order via Map iteration
    const out = {};
    for (const [name, body] of this._bodies) {
      const p = body.getPosition();
      const v = body.getLinearVelocity();
      out[name] = { pos: [p.x, p.y], vel: [v.x, v.y], angle: body.getAngle() };
    }
    return out;
  }

  events() {
    return this._events.slice();
  }

  teleport(name, pos) {
    const body = this._require(name);
    body.setTransform(new Vec2(pos[0], pos[1]), body.getAngle());
  }

  kinetic_energy(names = null) {
    const list = names == null ? Array.from(this._bodies.keys()) : names;
    let total = 0.0;
    for (const n of list) {
      const body = this._bodies.get(n);
      if (!body || body.isStatic()) continue;
      const v = body.getLinearVelocity();
      total += 0.5 * body.getMass() * (v.x * v.x + v.y * v.y);
      const I = body.getInertia();
      const w = body.getAngularVelocity();
      if (Number.isFinite(I)) total += 0.5 * I * w * w;
    }
    return total;
  }

  controlled() {
    return this._controlled;
  }
}

module.exports = { World, SeededRandom, DT, VELOCITY_ITERATIONS, POSITION_ITERATIONS };
