"""Tests du SceneSDK (module A).

Couvre : construction, query/contacts/pénétration, gravité, settling,
teleport+reindex, on_contact, apply/jump conditionnel au sol, déterminisme.
Lancer depuis la racine : python -m pytest tests/test_sdk.py -q
"""

import math

import pytest

from harness.legacy.sdk import SceneSDK


# --------------------------------------------------------------------- #
# Construction & introspection
# --------------------------------------------------------------------- #
def test_construction_and_entities():
    s = SceneSDK(seed=7)
    assert s.seed == 7
    s.add_ground()
    s.spawn_agent((100, 40))
    s.add_box("box", (200, 100))
    ents = s.list_entities()
    assert set(ents) == {"ground", "agent", "box"}


def test_query_fields():
    s = SceneSDK()
    s.add_ground()
    s.spawn_agent((100, 40))
    q = s.query("agent")
    assert set(q) == {"pos", "vel", "angle", "angular_vel", "bbox", "body_type", "is_agent"}
    assert q["is_agent"] is True
    assert q["body_type"] == "dynamic"
    assert s.query("ground")["body_type"] == "static"
    assert q["pos"][0] == pytest.approx(100)


def test_agent_rotation_locked():
    """Moment infini => l'agent ne tourne pas même heurté."""
    s = SceneSDK()
    s.add_ground()
    s.spawn_agent((100, 40))
    s.apply({"type": "impulse", "target": "agent", "vector": [300, 0]})
    s.step(60)
    assert abs(s.query("agent")["angle"]) < 1e-6


# --------------------------------------------------------------------- #
# Contacts & pénétration
# --------------------------------------------------------------------- #
def test_contacts_and_no_penetration_when_apart():
    s = SceneSDK()
    s.add_ground()
    s.add_box("a", (100, 300))
    s.add_box("b", (400, 300))
    assert s.contacts("a", "b") is False
    assert s.penetration_depth("a", "b") == 0.0


def test_penetration_when_overlapping():
    s = SceneSDK()
    # deux boîtes statiques se chevauchant de 30 px
    s.add_box("a", (100, 100), size=(40, 40), body="static")
    s.add_box("b", (110, 100), size=(40, 40), body="static")
    assert s.contacts("a", "b") is True
    assert s.penetration_depth("a", "b") == pytest.approx(30.0, abs=1.0)


def test_box_rests_on_ground_contact():
    s = SceneSDK()
    s.add_ground()
    s.add_box("box", (400, 200))
    s.step(300)
    assert s.contacts("box", "ground") is True


# --------------------------------------------------------------------- #
# Gravité & settling
# --------------------------------------------------------------------- #
def test_gravity_makes_body_fall():
    s = SceneSDK()
    s.add_ground()
    s.add_box("box", (400, 400))
    y0 = s.query("box")["pos"][1]
    s.step(30)
    assert s.query("box")["pos"][1] < y0 - 10


def test_settling_kinetic_energy_to_zero():
    """Une boîte posée finit au repos (KE ~ 0) sans grand déplacement horizontal."""
    s = SceneSDK()
    s.add_ground()
    s.add_box("box", (400, 100), size=(40, 40))
    x0 = s.query("box")["pos"][0]
    s.step(300)
    assert s.total_kinetic_energy(["box"]) < 1e-2
    assert abs(s.query("box")["pos"][0] - x0) < 0.8  # 2% de 40 px


def test_no_nan_in_normal_scene():
    s = SceneSDK()
    s.add_ground()
    s.add_box("box", (400, 200))
    s.step(300)
    assert all(e["type"] != "nan_detected" for e in s.events())


# --------------------------------------------------------------------- #
# Teleport + reindex
# --------------------------------------------------------------------- #
def test_teleport_updates_position_and_bbox():
    s = SceneSDK()
    s.add_ground()
    s.add_box("box", (100, 100), size=(40, 40))
    s.teleport("box", (500, 300))
    q = s.query("box")
    assert q["pos"] == pytest.approx([500, 300])
    # bbox réindexée autour de la nouvelle position
    l, b, r, t = q["bbox"]
    assert l == pytest.approx(480, abs=1) and r == pytest.approx(520, abs=1)


def test_teleport_then_contact_detects_new_position():
    """Sans reindex, contacts échouerait ; teleport doit réindexer."""
    s = SceneSDK()
    s.add_ground()
    s.add_box("box", (400, 400), size=(40, 40))
    assert s.contacts("box", "ground") is False
    s.teleport("box", (400, 20))  # posée sur le sol (repos ~ y=20)
    assert s.contacts("box", "ground") is True


# --------------------------------------------------------------------- #
# on_contact / flags / events
# --------------------------------------------------------------------- #
def test_on_contact_sets_flag_via_sensor_zone():
    s = SceneSDK()
    s.add_ground()
    s.add_ball("ball", (100, 200), radius=15)
    s.add_zone("zone", (100, 60), (60, 120))
    s.on_contact("ball", "zone", "in_zone")
    assert s.get_flag("in_zone") is None
    s.step(120)  # la balle tombe dans la zone capteur
    assert s.get_flag("in_zone") is True
    assert any(e["type"] == "flag_set" and e["key"] == "in_zone" for e in s.events())


def test_flag_set_and_get():
    s = SceneSDK()
    s.set_flag("k", 42)
    assert s.get_flag("k") == 42
    assert s.get_flag("absent", "def") == "def"


# --------------------------------------------------------------------- #
# apply / jump conditionnel
# --------------------------------------------------------------------- #
def test_move_right_increases_x():
    s = SceneSDK()
    s.add_ground()
    s.spawn_agent((100, 40))
    s.step(20)
    x0 = s.query("agent")["pos"][0]
    for _ in range(60):
        s.apply("right")
        s.step()
    assert s.query("agent")["pos"][0] > x0 + 10


def test_jump_only_when_grounded():
    s = SceneSDK()
    s.add_ground()
    s.spawn_agent((400, 40))
    s.step(40)  # settle au sol
    assert s._agent_grounded() is True
    y_ground = s.query("agent")["pos"][1]
    s.apply("jump")
    ys = []
    for _ in range(40):
        s.step()
        ys.append(s.query("agent")["pos"][1])
    assert max(ys) > y_ground + 30  # le saut décolle bien

    # en l'air : jump ne fait rien
    s.teleport("agent", (400, 400))
    s.step()
    assert s._agent_grounded() is False
    vy_before = s.query("agent")["vel"][1]
    s.apply("jump")
    assert s.query("agent")["vel"][1] == pytest.approx(vy_before)


def test_noop_does_nothing():
    s = SceneSDK()
    s.add_ground()
    s.spawn_agent((100, 40))
    s.step(40)
    v0 = s.query("agent")["vel"]
    s.apply("noop")
    assert s.query("agent")["vel"] == pytest.approx(v0)


# --------------------------------------------------------------------- #
# Déterminisme
# --------------------------------------------------------------------- #
def test_determinism_same_seed_same_positions():
    def run():
        s = SceneSDK(seed=123)
        s.add_ground()
        s.spawn_agent((100, 40))
        s.add_ball("ball", (300, 200), radius=15, mass=0.5)
        for i in range(200):
            s.apply("right")
            s.step()
        return s.query("agent")["pos"], s.query("ball")["pos"]

    a1, b1 = run()
    a2, b2 = run()
    assert a1 == a2
    assert b1 == b2


# --------------------------------------------------------------------- #
# snapshot / in_bounds / total_kinetic_energy
# --------------------------------------------------------------------- #
def test_snapshot_structure():
    s = SceneSDK()
    s.add_ground()
    s.spawn_agent((100, 40))
    snap = s.snapshot()
    assert "agent" in snap and "ground" in snap
    assert set(snap["agent"]) == {"pos", "vel", "angle", "angular_vel"}


def test_in_bounds():
    s = SceneSDK(world=(800, 600))
    s.add_ground()
    s.add_box("inside", (400, 300), size=(40, 40))
    assert s.in_bounds("inside") is True
    s.teleport("inside", (790, 300))  # déborde à droite
    assert s.in_bounds("inside") is False


# --------------------------------------------------------------------- #
# Scènes d'exemple (fixtures)
# --------------------------------------------------------------------- #
# RETIRED (Elias, 2026-07-15): the example-scene solvability tests are deleted with
# scenes/examples/ (push_ball_to_zone / climb_platforms / broken_floating). The
# SceneSDK mechanism itself (still imported by harness.core.sandbox's verify job) is
# covered by the direct-API tests above.
