"""Tests for the v2 World substrate (module E).

Covers construction (all shapes + sensor + static), errors, control semantics,
dynamics (gravity/impulse/force/velocity), joints (pin/pivot/spring), remove
with attached joints, on_contact + events, determinism, seeded rng, the NaN
sentinel, and pure queries.
Run from the repo root: python -m pytest tests/test_world.py -q
"""

import math

import pytest

from harness.world import World


# --------------------------------------------------------------------- #
# Construction & introspection
# --------------------------------------------------------------------- #
def test_add_all_shapes_sensor_and_static():
    w = World(seed=1)
    w.add("box", "box", pos=(100, 300), size=(40, 40))
    w.add("ball", "circle", pos=(200, 300), radius=15)
    w.add("ground", "segment", pos=(0, 0), a=(0, 0), b=(800, 0), static=True)
    w.add("wedge", "poly", pos=(300, 300), vertices=[(-20, -20), (20, -20), (0, 20)])
    w.add("zone", "box", pos=(500, 100), size=(60, 60), static=True, sensor=True)
    assert set(w.entities()) == {"box", "ball", "ground", "wedge", "zone"}

    assert w.query("box")["shape"] == "box"
    assert w.query("ball")["shape"] == "circle"
    assert w.query("ground")["shape"] == "segment"
    assert w.query("wedge")["shape"] == "poly"
    assert w.query("ground")["static"] is True
    assert w.query("box")["static"] is False
    assert w.query("zone")["sensor"] is True
    assert w.query("box")["sensor"] is False


def test_query_fields():
    w = World()
    w.add("box", "box", pos=(120, 340), size=(40, 40), velocity=(5, -3))
    q = w.query("box")
    assert set(q) == {"pos", "vel", "angle", "angular_vel", "bbox",
                      "shape", "static", "sensor", "controlled"}
    assert q["pos"] == pytest.approx([120, 340])
    assert q["vel"] == pytest.approx([5, -3])
    # bbox centered around the position
    l, b, r, t = q["bbox"]
    assert l == pytest.approx(100, abs=1) and r == pytest.approx(140, abs=1)


def test_locked_rotation_body_does_not_spin():
    w = World()
    w.add("ground", "segment", pos=(0, 0), a=(0, 0), b=(800, 0), static=True)
    w.add("agent", "box", pos=(100, 40), size=(24, 36), locked_rotation=True)
    w.impulse("agent", (300, 0))
    w.step(60)
    assert abs(w.query("agent")["angle"]) < 1e-6


# --------------------------------------------------------------------- #
# Construction errors
# --------------------------------------------------------------------- #
def test_duplicate_name_raises():
    w = World()
    w.add("dup", "box", pos=(100, 100), size=(20, 20))
    with pytest.raises(ValueError, match="already exists"):
        w.add("dup", "circle", pos=(200, 200), radius=10)


def test_unknown_shape_raises():
    w = World()
    with pytest.raises(ValueError, match="unknown shape"):
        w.add("x", "triangle", pos=(100, 100))


def test_missing_geometry_raises():
    w = World()
    with pytest.raises(ValueError, match="size"):
        w.add("b", "box", pos=(100, 100))
    with pytest.raises(ValueError, match="radius"):
        w.add("c", "circle", pos=(100, 100))
    with pytest.raises(ValueError, match="segment"):
        w.add("s", "segment", pos=(0, 0))
    with pytest.raises(ValueError, match="poly"):
        w.add("p", "poly", pos=(100, 100))


# --------------------------------------------------------------------- #
# control / controlled
# --------------------------------------------------------------------- #
def test_control_last_call_wins():
    w = World()
    w.add("a", "box", pos=(100, 100), size=(20, 20))
    w.add("b", "circle", pos=(200, 100), radius=10)
    assert w.controlled() is None
    w.control("a")
    assert w.controlled() == "a"
    w.control("b")                       # exactly-one semantics: last wins
    assert w.controlled() == "b"
    assert w.query("b")["controlled"] is True
    assert w.query("a")["controlled"] is False


def test_control_static_raises():
    w = World()
    w.add("wall", "box", pos=(100, 100), size=(20, 20), static=True)
    with pytest.raises(ValueError, match="dynamic"):
        w.control("wall")


# --------------------------------------------------------------------- #
# Dynamics
# --------------------------------------------------------------------- #
def test_gravity_makes_body_fall():
    w = World()
    w.add("box", "box", pos=(400, 400), size=(40, 40))
    y0 = w.query("box")["pos"][1]
    w.step(30)
    assert w.query("box")["pos"][1] < y0 - 10


def test_set_gravity_reverses_fall():
    w = World()
    w.add("box", "box", pos=(400, 300), size=(40, 40))
    w.set_gravity(0, 900)                # push up
    y0 = w.query("box")["pos"][1]
    w.step(30)
    assert w.query("box")["pos"][1] > y0 + 10


def test_impulse_changes_velocity():
    w = World()
    w.add("box", "box", pos=(400, 300), size=(40, 40), mass=1.0)
    w.impulse("box", (50, 0))            # mass 1 -> ~+50 in vx
    assert w.query("box")["vel"][0] == pytest.approx(50, abs=1e-3)


def test_force_accelerates_body():
    w = World()
    w.add("box", "box", pos=(400, 300), size=(40, 40))
    w.set_gravity(0, 0)                  # isolate the force
    vx0 = w.query("box")["vel"][0]
    for _ in range(10):
        w.force("box", (100, 0))         # re-applied each step (pymunk resets force)
        w.step(1)
    assert w.query("box")["vel"][0] > vx0 + 5


def test_set_velocity():
    w = World()
    w.add("box", "box", pos=(400, 300), size=(40, 40))
    w.set_velocity("box", (10, 20))
    assert w.query("box")["vel"] == pytest.approx([10, 20])


# --------------------------------------------------------------------- #
# Joints
# --------------------------------------------------------------------- #
def test_pin_pendulum_swings():
    """A bob pinned to a static anchor, released off-vertical, changes x."""
    w = World()
    w.add("anchor", "box", pos=(400, 500), size=(10, 10), static=True)
    w.add("bob", "circle", pos=(500, 500), radius=12)   # to the right, same height
    w.pin("anchor", "bob")
    x0 = w.query("bob")["pos"][0]
    w.step(60)
    assert abs(w.query("bob")["pos"][0] - x0) > 5        # swung under gravity


def test_pivot_holds_bodies_together():
    w = World()
    w.add("anchor", "box", pos=(400, 500), size=(10, 10), static=True)
    w.add("arm", "box", pos=(430, 500), size=(60, 8))
    w.pivot("anchor", "arm", (400, 500))
    w.step(60)
    # the pivot keeps the arm's pinned corner near the world point
    q = w.query("arm")
    # arm rotates about the pivot but stays attached (does not free-fall away)
    assert q["pos"][1] > 300


def test_spring_pulls_body_back():
    """A body hung on a damped spring settles below rest, not free-falling forever."""
    w = World()
    w.add("anchor", "box", pos=(400, 550), size=(10, 10), static=True)
    w.add("weight", "circle", pos=(400, 450), radius=10, mass=1.0)
    w.spring("anchor", "weight", rest_length=60, stiffness=400, damping=8)
    w.step(300)
    y = w.query("weight")["pos"][1]
    # equilibrium hangs below the anchor but the spring prevents an endless fall
    assert 350 < y < 500


def test_remove_with_joint_attached():
    """Removing a jointed body tears down the constraint without crashing."""
    w = World()
    w.add("anchor", "box", pos=(400, 500), size=(10, 10), static=True)
    w.add("bob", "circle", pos=(500, 500), radius=12)
    w.pin("anchor", "bob")
    assert len(w.space.constraints) == 1
    w.remove("bob")
    assert len(w.space.constraints) == 0
    assert "bob" not in w.entities()
    w.step(10)                          # space still steps cleanly


def test_remove_controlled_clears_controlled():
    w = World()
    w.add("a", "box", pos=(100, 100), size=(20, 20))
    w.control("a")
    w.remove("a")
    assert w.controlled() is None


# --------------------------------------------------------------------- #
# on_contact / flags / events
# --------------------------------------------------------------------- #
def test_on_contact_sets_flag_and_logs_event():
    w = World()
    w.add("ball", "circle", pos=(100, 200), radius=15)
    w.add("zone", "box", pos=(100, 60), size=(60, 120), static=True, sensor=True)
    w.on_contact("ball", "zone", "in_zone")
    assert w.flag("in_zone") is None
    w.step(120)                         # the ball falls into the sensor zone
    assert w.flag("in_zone") is True
    assert any(e["type"] == "flag_set" and e["key"] == "in_zone"
               for e in w.events())


def test_set_flag_records_step_index():
    w = World()
    w.add("box", "box", pos=(400, 300), size=(20, 20))
    w.step(5)
    w.set_flag("k", 42)
    assert w.flag("k") == 42
    assert w.flag("absent", "def") == "def"
    ev = [e for e in w.events() if e.get("key") == "k"][0]
    assert ev["type"] == "flag_set" and ev["step"] == 5


# --------------------------------------------------------------------- #
# Determinism & rng
# --------------------------------------------------------------------- #
def _build(w):
    w.add("ground", "segment", pos=(0, 0), a=(0, 0), b=(800, 0), static=True)
    w.add("agent", "box", pos=(100, 40), size=(24, 36))
    w.add("ball", "circle", pos=(300, 200), radius=15, mass=0.5)
    w.control("agent")


def test_determinism_same_seed_same_snapshots():
    w1 = World(seed=42)
    _build(w1)
    w2 = World(seed=42)
    _build(w2)
    for _ in range(300):
        w1.impulse("agent", (5, 0))
        w2.impulse("agent", (5, 0))
        w1.step(1)
        w2.step(1)
    assert w1.snapshot() == w2.snapshot()


def test_rng_is_seeded():
    w1 = World(seed=7)
    w2 = World(seed=7)
    seq1 = [w1.rng.random() for _ in range(10)]
    seq2 = [w2.rng.random() for _ in range(10)]
    assert seq1 == seq2
    w3 = World(seed=8)
    assert [w3.rng.random() for _ in range(10)] != seq1


# --------------------------------------------------------------------- #
# NaN / explosion sentinel
# --------------------------------------------------------------------- #
def test_nan_sentinel_freezes_and_logs():
    w = World()
    w.add("box", "box", pos=(400, 300), size=(40, 40))
    w.set_velocity("box", (1e6, 0))     # explosive velocity
    w.step(5)                           # should detect, log once, freeze
    assert any(e["type"] == "nan_detected" for e in w.events())
    steps_after = w.steps
    w.step(50)                          # frozen: no further stepping
    assert w.steps == steps_after
    # exactly one nan event
    assert sum(1 for e in w.events() if e["type"] == "nan_detected") == 1


# --------------------------------------------------------------------- #
# Pure queries
# --------------------------------------------------------------------- #
def test_contacts_and_touching():
    w = World()
    w.add("ground", "segment", pos=(0, 0), a=(0, 0), b=(800, 0), static=True)
    w.add("box", "box", pos=(400, 200), size=(40, 40))
    assert w.contacts("box", "ground") is False
    w.step(200)
    assert w.contacts("box", "ground") is True
    assert "ground" in w.touching("box")


def test_touching_excludes_sensors():
    w = World()
    w.add("box", "box", pos=(400, 100), size=(40, 40), static=True)
    w.add("zone", "box", pos=(400, 100), size=(80, 80), static=True, sensor=True)
    # zone overlaps box but is a sensor -> not reported as touching
    assert "zone" not in w.touching("box")


def test_grounded():
    w = World()
    w.add("ground", "segment", pos=(0, 0), a=(0, 0), b=(800, 0), static=True)
    w.add("agent", "box", pos=(400, 40), size=(24, 36), locked_rotation=True)
    w.step(60)                          # settle onto the ground
    assert w.grounded("agent") is True
    w.teleport("agent", (400, 400))     # lift into the air
    w.step(1)
    assert w.grounded("agent") is False


def test_in_bounds():
    w = World(size=(800, 600))
    w.add("box", "box", pos=(400, 300), size=(40, 40))
    assert w.in_bounds("box") is True
    w.teleport("box", (795, 300))       # spills off the right edge
    assert w.in_bounds("box") is False
    assert w.in_bounds("box", margin=50) is True


def test_penetration_depth():
    w = World()
    w.add("a", "box", pos=(100, 100), size=(40, 40), static=True)
    w.add("b", "box", pos=(110, 100), size=(40, 40), static=True)
    assert w.penetration_depth("a", "b") == pytest.approx(30.0, abs=1.0)
    w.add("far", "box", pos=(500, 100), size=(40, 40), static=True)
    assert w.penetration_depth("a", "far") == 0.0


def test_penetration_depth_sensor_is_zero():
    w = World()
    w.add("a", "box", pos=(100, 100), size=(40, 40), static=True)
    w.add("z", "box", pos=(110, 100), size=(40, 40), static=True, sensor=True)
    assert w.penetration_depth("a", "z") == 0.0    # sensor -> never a penetration


def test_teleport_reindexes():
    w = World()
    w.add("ground", "segment", pos=(0, 0), a=(0, 0), b=(800, 0), static=True)
    w.add("box", "box", pos=(400, 400), size=(40, 40))
    assert w.contacts("box", "ground") is False
    w.teleport("box", (400, 20))        # rest on the ground
    assert w.contacts("box", "ground") is True


def test_kinetic_energy():
    w = World()
    w.add("box", "box", pos=(400, 300), size=(40, 40))
    assert w.kinetic_energy() == pytest.approx(0.0)
    w.set_velocity("box", (10, 0))
    assert w.kinetic_energy(["box"]) == pytest.approx(50.0, abs=1e-6)


def test_snapshot_structure_and_order():
    w = World()
    w.add("a", "box", pos=(100, 100), size=(20, 20))
    w.add("b", "circle", pos=(200, 100), radius=10)
    snap = w.snapshot()
    assert list(snap.keys()) == ["a", "b"]           # insertion order
    assert set(snap["a"]) == {"pos", "vel", "angle"}


def test_steps_property_counts():
    w = World()
    w.add("box", "box", pos=(400, 300), size=(20, 20))
    assert w.steps == 0
    w.step(7)
    assert w.steps == 7
