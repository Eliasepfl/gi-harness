"""Tests for the ``inspect_world`` static placement-feedback tool (TRACK INSPECT).

``inspect_world`` is a FROZEN, engine-free static analyzer (v1): it parses a full
Godot spec dict OR a bodies-only fragment and returns per-entity AABBs + a
placement-warning taxonomy, with NO Godot process. Geometry math mirrors the frozen
``runner.gd`` ``_bbox`` (SPEC.md §8 ``contained`` semantics: aabb = [left, bottom,
right, top], y UP). Coverage: registry exposure + JSON schema, per-shape AABB math,
overlap detection, fragment vs full-spec handling, the full warning taxonomy, bank
role matching, and a smoke on a shipped fixture spec.
"""
from __future__ import annotations

import json
import math
import os

import pytest

from harness.designer import tools as T

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "godot_specs")


# --------------------------------------------------------------------------- #
# Registry / schema exposure
# --------------------------------------------------------------------------- #
def test_inspect_world_in_registry_with_valid_schema():
    assert "inspect_world" in T.tool_names()
    entry = next(e for e in T.REGISTRY if e["function"]["name"] == "inspect_world")
    assert entry["type"] == "function"
    fn = entry["function"]
    assert set(fn) >= {"name", "description", "parameters"}
    params = fn["parameters"]
    assert params["type"] == "object"
    assert "spec_or_fragment" in params["properties"]
    assert params["required"] == ["spec_or_fragment"]
    assert params["additionalProperties"] is False


def test_inspect_world_dispatches_by_name():
    out = T.dispatch("inspect_world",
                     {"spec_or_fragment": {"bodies": [
                         {"name": "b", "shape": "circle", "pos": [10, 10], "radius": 5}]}})
    assert set(out) == {"entities", "warnings", "summary"}
    assert isinstance(out["entities"], list)
    assert isinstance(out["warnings"], list)
    assert isinstance(out["summary"], dict)


# --------------------------------------------------------------------------- #
# AABB math per shape type (mirrors runner.gd _bbox; aabb = [L, B, R, T], y UP)
# --------------------------------------------------------------------------- #
def _aabb(body):
    out = T.inspect_world({"bodies": [body]}, use_bank=False)
    return out["entities"][0]["aabb"]


def test_aabb_box_axis_aligned():
    assert _aabb({"name": "x", "shape": "box", "pos": [100, 100], "size": [20, 40]}) \
        == [90.0, 80.0, 110.0, 120.0]


def test_aabb_box_rotated_grows_the_box():
    # 90deg spin swaps the half-extents (a rotated box has a larger conservative AABB).
    got = _aabb({"name": "x", "shape": "box", "pos": [100, 100],
                 "size": [20, 40], "angle": math.pi / 2})
    assert got == pytest.approx([80.0, 90.0, 120.0, 110.0], abs=1e-9)


def test_aabb_circle():
    assert _aabb({"name": "c", "shape": "circle", "pos": [50, 50], "radius": 10}) \
        == [40.0, 40.0, 60.0, 60.0]


def test_aabb_segment():
    assert _aabb({"name": "s", "shape": "segment", "pos": [100, 100],
                  "a": [-30, 0], "b": [30, 0]}) == [70.0, 100.0, 130.0, 100.0]


def test_aabb_poly():
    assert _aabb({"name": "p", "shape": "poly", "pos": [10, 10],
                  "vertices": [[0, 0], [20, 0], [0, 30]]}) == [10.0, 10.0, 30.0, 40.0]


def test_aabb_malformed_shape_is_none_not_crash():
    out = T.inspect_world({"bodies": [{"name": "bad", "shape": "box", "pos": [0, 0]}]},
                          use_bank=False)
    assert out["entities"][0]["aabb"] is None


# --------------------------------------------------------------------------- #
# Entity kind discrimination
# --------------------------------------------------------------------------- #
def test_entity_kind_static_dynamic_sensor():
    out = T.inspect_world({"bodies": [
        {"name": "wall", "shape": "box", "pos": [10, 100], "size": [20, 200], "static": True},
        {"name": "zone", "shape": "box", "pos": [100, 100], "size": [40, 40],
         "static": True, "sensor": True},
        {"name": "ball", "shape": "circle", "pos": [200, 200], "radius": 8, "control": True},
    ]}, use_bank=False)
    kinds = {e["name"]: e["kind"] for e in out["entities"]}
    assert kinds == {"wall": "static", "zone": "sensor", "ball": "dynamic"}
    assert out["summary"]["counts"] == {"static": 1, "dynamic": 1, "sensor": 1, "total": 3}


# --------------------------------------------------------------------------- #
# Fragment vs full-spec handling
# --------------------------------------------------------------------------- #
def test_fragment_bodies_only_has_no_world_bounds():
    out = T.inspect_world({"bodies": [
        {"name": "a", "shape": "box", "pos": [50, 50], "size": [20, 20]}]},
        use_bank=False)
    assert out["summary"]["is_fragment"] is True
    assert out["summary"]["world_size"] is None
    # No out-of-bounds warning is possible without declared bounds.
    assert not any(w["kind"] == "out_of_bounds" for w in out["warnings"])


def test_bare_list_of_bodies_is_a_fragment():
    out = T.inspect_world([{"name": "a", "shape": "circle", "pos": [1, 1], "radius": 2}],
                          use_bank=False)
    assert out["summary"]["is_fragment"] is True
    assert len(out["entities"]) == 1


def test_json_string_input_is_parsed():
    frag = json.dumps({"bodies": [
        {"name": "a", "shape": "circle", "pos": [1, 1], "radius": 2}]})
    out = T.inspect_world(frag, use_bank=False)
    assert out["entities"][0]["name"] == "a"


def test_full_spec_is_not_a_fragment_and_carries_world_size():
    spec = {
        "meta": {"title": "t", "prompt": "p", "world_size": [1000, 800],
                 "actions": ["a"]},
        "bodies": [{"name": "g", "shape": "box", "pos": [500, 10], "size": [1000, 20],
                    "static": True},
                   {"name": "b", "shape": "circle", "pos": [50, 50], "radius": 8,
                    "control": True}],
        "act": {"a": []},
        "predicates": {"success": "false", "checkpoints": {}},
    }
    out = T.inspect_world(spec, use_bank=False)
    assert out["summary"]["is_fragment"] is False
    assert out["summary"]["world_size"] == [1000, 800]


# --------------------------------------------------------------------------- #
# Overlap detection (solid statics)
# --------------------------------------------------------------------------- #
def test_overlapping_solid_statics_are_warned():
    out = T.inspect_world({"bodies": [
        {"name": "s1", "shape": "box", "pos": [100, 100], "size": [40, 40], "static": True},
        {"name": "s2", "shape": "box", "pos": [120, 100], "size": [40, 40], "static": True},
    ]}, use_bank=False)
    ov = [w for w in out["warnings"] if w["kind"] == "overlap_solid_statics"]
    assert len(ov) == 1
    assert set(ov[0]["bodies"]) == {"s1", "s2"}
    assert ov[0]["penetration"] > 0


def test_edge_touching_statics_do_not_overlap():
    out = T.inspect_world({"bodies": [
        {"name": "s1", "shape": "box", "pos": [100, 100], "size": [40, 40], "static": True},
        {"name": "s2", "shape": "box", "pos": [140, 100], "size": [40, 40], "static": True},
    ]}, use_bank=False)
    assert not any(w["kind"] == "overlap_solid_statics" for w in out["warnings"])


def test_sensor_static_overlap_is_not_a_solid_overlap():
    # A sensor overlapping a solid static is a normal trigger placement, not a
    # solid-solid collision.
    out = T.inspect_world({"bodies": [
        {"name": "s1", "shape": "box", "pos": [100, 100], "size": [40, 40], "static": True},
        {"name": "z", "shape": "box", "pos": [110, 100], "size": [40, 40],
         "static": True, "sensor": True},
    ]}, use_bank=False)
    assert not any(w["kind"] == "overlap_solid_statics" for w in out["warnings"])


# --------------------------------------------------------------------------- #
# Warning taxonomy: bounds / floating / isolated sensor / duplicate names
# --------------------------------------------------------------------------- #
def test_out_of_bounds_body_is_warned():
    spec = {
        "meta": {"title": "t", "prompt": "p", "world_size": [200, 200], "actions": ["a"]},
        "bodies": [
            {"name": "g", "shape": "box", "pos": [100, 10], "size": [200, 20], "static": True},
            {"name": "esc", "shape": "circle", "pos": [260, 100], "radius": 10,
             "control": True},
        ],
        "act": {"a": []},
        "predicates": {"success": "false", "checkpoints": {}},
    }
    out = T.inspect_world(spec, use_bank=False)
    oob = [w for w in out["warnings"] if w["kind"] == "out_of_bounds"]
    assert len(oob) == 1 and oob[0]["bodies"] == ["esc"]


def test_floating_static_warned_but_grounded_static_is_not():
    out = T.inspect_world({"bodies": [
        {"name": "ground", "shape": "box", "pos": [400, 10], "size": [800, 20],
         "static": True},                       # bottom at y=0 -> supported by floor
        {"name": "ledge", "shape": "box", "pos": [400, 300], "size": [100, 20],
         "static": True},                       # nothing under it -> floating
    ]}, use_bank=False)
    floating = [w for w in out["warnings"] if w["kind"] == "floating_static"]
    assert [b for w in floating for b in w["bodies"]] == ["ledge"]


def test_static_resting_on_another_static_is_supported():
    out = T.inspect_world({"bodies": [
        {"name": "ground", "shape": "box", "pos": [400, 10], "size": [800, 20],
         "static": True},                       # y in [0,20]
        {"name": "crate", "shape": "box", "pos": [400, 40], "size": [40, 40],
         "static": True},                       # bottom at y=20, rests on ground top
    ]}, use_bank=False)
    assert not any(w["kind"] == "floating_static" for w in out["warnings"])


def test_isolated_sensor_warned_but_reachable_sensor_is_not():
    out = T.inspect_world({"bodies": [
        {"name": "ground", "shape": "box", "pos": [400, 10], "size": [800, 20],
         "static": True},
        {"name": "goal", "shape": "box", "pos": [400, 45], "size": [40, 40],
         "static": True, "sensor": True},        # sits just above the ground -> reachable
        {"name": "lonely", "shape": "box", "pos": [700, 500], "size": [40, 40],
         "static": True, "sensor": True},        # floats in the void -> isolated
        {"name": "ball", "shape": "circle", "pos": [50, 50], "radius": 8, "control": True},
    ]}, use_bank=False)
    iso = [w for w in out["warnings"] if w["kind"] == "isolated_sensor"]
    assert [b for w in iso for b in w["bodies"]] == ["lonely"]


def test_duplicate_names_are_warned():
    out = T.inspect_world({"bodies": [
        {"name": "dup", "shape": "circle", "pos": [10, 10], "radius": 5},
        {"name": "dup", "shape": "circle", "pos": [90, 90], "radius": 5},
        {"name": "unique", "shape": "circle", "pos": [50, 50], "radius": 5},
    ]}, use_bank=False)
    dups = [w for w in out["warnings"] if w["kind"] == "duplicate_name"]
    assert len(dups) == 1 and dups[0]["bodies"] == ["dup"]


# --------------------------------------------------------------------------- #
# Bank role matching (role_if_bank_matched)
# --------------------------------------------------------------------------- #
def test_bank_role_matches_names_exact_and_base():
    out = T.inspect_world({"bodies": [
        {"name": "ground", "shape": "box", "pos": [400, 10], "size": [800, 20], "static": True},
        {"name": "wall_2", "shape": "box", "pos": [10, 100], "size": [20, 200], "static": True},
        {"name": "marble", "shape": "circle", "pos": [50, 50], "radius": 8, "control": True},
        {"name": "zzz_unknown", "shape": "circle", "pos": [90, 90], "radius": 8},
    ]}, use_bank=True)
    role = {e["name"]: e["role_if_bank_matched"] for e in out["entities"]}
    assert role["ground"] == "terrain"       # exact bank hit
    assert role["wall_2"] == "terrain"        # base-name (wall) hit
    assert role["marble"] == "prop"           # exact bank hit (its catalog category)
    assert role["zzz_unknown"] is None


def test_use_bank_false_yields_no_roles():
    out = T.inspect_world({"bodies": [
        {"name": "ground", "shape": "box", "pos": [400, 10], "size": [800, 20], "static": True}]},
        use_bank=False)
    assert out["entities"][0]["role_if_bank_matched"] is None


# --------------------------------------------------------------------------- #
# Smoke on a shipped fixture spec
# --------------------------------------------------------------------------- #
def test_smoke_on_fixture_collect2_spec():
    with open(os.path.join(_FIXTURES, "collect2.spec.json"), encoding="utf-8") as fh:
        spec = json.load(fh)
    out = T.inspect_world(spec)
    assert out["summary"]["is_fragment"] is False
    assert out["summary"]["world_size"] == [1200, 650]
    assert len(out["entities"]) == len(spec["bodies"]) == 12
    # every body got an AABB and a kind.
    for e in out["entities"]:
        assert e["aabb"] is not None and len(e["aabb"]) == 4
        assert e["kind"] in ("static", "dynamic", "sensor")
    # exactly one dynamic (the controlled marble).
    assert sum(1 for e in out["entities"] if e["kind"] == "dynamic") == 1
    assert isinstance(out["warnings"], list)
    # the union world_bbox spans all entities.
    assert out["summary"]["world_bbox"] is not None and len(out["summary"]["world_bbox"]) == 4


# =========================================================================== #
# PRECISION UPGRADES (GODOT_DOCS_MINING.md section 1, all 12 items)
# =========================================================================== #
def _full(bodies, success="false", *, checkpoints=None, failure=None,
          world_size=(1000, 600), **extra):
    """A minimal FULL spec (meta/act/predicates present -> not a fragment) so the
    predicate-driven warning passes run. Two no-op actions keep it structurally sane."""
    preds = {"success": success, "checkpoints": checkpoints or {}}
    if failure is not None:
        preds["failure"] = failure
    spec = {
        "meta": {"title": "t", "prompt": "p", "world_size": list(world_size),
                 "actions": ["a", "b"]},
        "bodies": bodies,
        "act": {"a": [], "b": []},
        "predicates": preds,
    }
    spec.update(extra)
    return spec


def _kinds(out, kind):
    return [w for w in out["warnings"] if w["kind"] == kind]


_FLOOR = {"name": "floor", "shape": "box", "pos": [500, 10], "size": [1000, 20],
          "static": True}


# ---- Item 1: gravity convention (y-UP, down = -Y) --------------------------
def test_summary_carries_gravity_vector_default_down_is_negative_y():
    out = T.inspect_world(_full([_FLOOR,
        {"name": "ball", "shape": "circle", "pos": [100, 100], "radius": 8,
         "control": True}]), use_bank=False)
    assert out["summary"]["gravity"] == [0.0, -900.0]  # (0,-1)*900, down = -Y


def test_summary_gravity_reads_meta_override():
    spec = _full([_FLOOR,
        {"name": "ball", "shape": "circle", "pos": [100, 100], "radius": 8,
         "control": True}])
    spec["meta"]["gravity"] = 500.0
    spec["meta"]["gravity_vector"] = [0, -1]
    out = T.inspect_world(spec, use_bank=False)
    assert out["summary"]["gravity"] == [0.0, -500.0]


# ---- Items 2 & 3: byte-identical AABB parity + exact rotated-box formula ----
def test_aabb_adds_no_safe_margin():
    # 2D collision shapes get NO grow/safe margin (unlike 3D CharacterBody) — a unit
    # box's AABB is exactly its extents, not inflated.
    assert _aabb({"name": "u", "shape": "box", "pos": [0, 0], "size": [1, 1]}) \
        == [-0.5, -0.5, 0.5, 0.5]


def test_rotated_box_matches_abs_sum_basis_formula():
    # The exact rotated-box half-extents are |hx*cos|+|hy*sin|, |hx*sin|+|hy*cos|
    # (abs-sum of the rotated Transform2D basis columns) — not a heuristic.
    hx, hy, ang = 10.0, 20.0, math.radians(30)
    ex = abs(hx * math.cos(ang)) + abs(hy * math.sin(ang))
    ey = abs(hx * math.sin(ang)) + abs(hy * math.cos(ang))
    got = _aabb({"name": "r", "shape": "box", "pos": [100, 100],
                 "size": [2 * hx, 2 * hy], "angle": ang})
    # inspect_world rounds its output to 6 decimals, so compare at that resolution.
    assert got == pytest.approx([100 - ex, 100 - ey, 100 + ex, 100 + ey], abs=1e-6)


# ---- Item 4: rotatable-body containment goal -------------------------------
def _car_spec(success='contained("car", "slot")', **car_extra):
    car = {"name": "car", "shape": "box", "pos": [130, 68], "size": [60, 30],
           "control": True}
    car.update(car_extra)
    return _full([_FLOOR,
        {"name": "slot", "shape": "box", "pos": [820, 90], "size": [170, 130],
         "static": True, "sensor": True}, car], success=success)


def test_rotatable_containment_goal_warns():
    out = T.inspect_world(_car_spec(), use_bank=False)
    w = _kinds(out, "rotatable_containment")
    assert len(w) == 1 and w[0]["bodies"] == ["car", "slot"]


def test_locked_rotation_containment_is_not_warned():
    out = T.inspect_world(_car_spec(locked_rotation=True), use_bank=False)
    assert _kinds(out, "rotatable_containment") == []


def test_circle_containment_is_not_rotatable():
    # A circle's AABB is rotation-invariant, so a containment goal on it is fine.
    out = T.inspect_world(_car_spec(shape="circle", radius=20), use_bank=False)
    assert _kinds(out, "rotatable_containment") == []


# ---- Item 5: one-step-latent annotation on overlap warnings ----------------
def test_overlap_warning_annotated_one_step_latent():
    out = T.inspect_world({"bodies": [
        {"name": "s1", "shape": "box", "pos": [100, 100], "size": [40, 40], "static": True},
        {"name": "s2", "shape": "box", "pos": [120, 100], "size": [40, 40], "static": True},
    ]}, use_bank=False)
    ov = _kinds(out, "overlap_solid_statics")
    assert len(ov) == 1
    assert "one-step-latent" in ov[0]["note"]


# ---- Item 6: unsatisfiable park (rest-at-goal with no damping/clamp) --------
def test_unsatisfiable_park_warns_without_clamp():
    out = T.inspect_world(_full([_FLOOR,
        {"name": "ball", "shape": "circle", "pos": [100, 100], "radius": 8,
         "control": True}], success='speed("ball") < 5'), use_bank=False)
    w = _kinds(out, "unsatisfiable_park")
    assert len(w) == 1 and w[0]["bodies"] == ["ball"]


def test_park_with_velocity_clamp_is_satisfiable():
    spec = _full([_FLOOR,
        {"name": "ball", "shape": "circle", "pos": [100, 100], "radius": 8,
         "control": True}], success='speed("ball") < 5',
        on_step=[{"kind": "velocity_clamp", "body": "ball", "vx_max": 0}])
    out = T.inspect_world(spec, use_bank=False)
    assert _kinds(out, "unsatisfiable_park") == []


def test_fast_speed_threshold_is_not_a_park_goal():
    # speed("ball") < 5000 is a plausible speed CAP, not a rest condition -> no warning.
    out = T.inspect_world(_full([_FLOOR,
        {"name": "ball", "shape": "circle", "pos": [100, 100], "radius": 8,
         "control": True}], success='speed("ball") < 5000'), use_bank=False)
    assert _kinds(out, "unsatisfiable_park") == []


# ---- Item 7: closed-form ballistic forecast --------------------------------
def test_ballistic_forecast_is_exact_semi_implicit_euler():
    # v += g*dt then x += v*dt, dt=1/60, g=(0,-900): tick1 vy=-15 -> y=-0.25;
    # tick2 vy=-30 -> y=-0.75.
    path = T.ballistic_forecast(0, 0, 0, 0, 0, -900, 3)
    assert path[0] == pytest.approx((0.0, -0.25))
    assert path[1] == pytest.approx((0.0, -0.75))
    assert path[2] == pytest.approx((0.0, -1.5))


def test_ballistic_forecast_horizontal_carry():
    # constant vx, no gravity -> pure translation of vx*dt per tick.
    path = T.ballistic_forecast(100, 50, 600, 0, 0, 0, 1)
    assert path[0] == pytest.approx((110.0, 50.0))


def test_forecast_oob_flags_a_launched_body():
    out = T.inspect_world(_full([_FLOOR,
        {"name": "rocket", "shape": "circle", "pos": [500, 300], "radius": 8,
         "velocity": [0, 900], "control": True}]), use_bank=False)
    w = _kinds(out, "forecast_oob")
    assert len(w) == 1 and w[0]["bodies"] == ["rocket"]
    assert w[0]["tick"] > 0  # a concrete tick at which it leaves the world


def test_no_forecast_for_a_body_at_rest():
    out = T.inspect_world(_full([_FLOOR,
        {"name": "ball", "shape": "circle", "pos": [500, 300], "radius": 8,
         "control": True}]), use_bank=False)
    assert _kinds(out, "forecast_oob") == []


def test_ballistic_summary_reports_jump_kinematics():
    spec = _full([_FLOOR,
        {"name": "ball", "shape": "circle", "pos": [100, 100], "radius": 8,
         "mass": 1.0, "control": True}])
    spec["act"] = {"a": [{"verb": "impulse", "body": "ball", "vec": [0, 460]}], "b": []}
    b = T.inspect_world(spec, use_bank=False)["summary"]["ballistic"]
    assert b["body"] == "ball"
    assert b["launch_dv"] == [0.0, 460.0]
    assert b["apex_px"] == pytest.approx(460 * 460 / (2 * 900), abs=1e-3)


# ---- Item 8: convexity check on poly shapes --------------------------------
def test_nonconvex_poly_is_warned():
    out = T.inspect_world(_full([_FLOOR,
        {"name": "el", "shape": "poly", "pos": [300, 300], "static": True,
         "vertices": [[0, 0], [40, 0], [40, 10], [10, 10], [10, 40], [0, 40]]},
        {"name": "ball", "shape": "circle", "pos": [100, 100], "radius": 8,
         "control": True}]), use_bank=False)
    w = _kinds(out, "nonconvex_poly")
    assert len(w) == 1 and w[0]["bodies"] == ["el"]


def test_convex_poly_is_not_warned():
    out = T.inspect_world(_full([_FLOOR,
        {"name": "tri", "shape": "poly", "pos": [300, 300], "static": True,
         "vertices": [[0, 0], [20, 0], [0, 30]]},
        {"name": "ball", "shape": "circle", "pos": [100, 100], "radius": 8,
         "control": True}]), use_bank=False)
    assert _kinds(out, "nonconvex_poly") == []


# ---- Item 9: contact-cap pile-up -------------------------------------------
def test_contact_cap_warns_over_eight_contacts():
    bodies = [{"name": "ball", "shape": "circle", "pos": [500, 300], "radius": 30,
               "control": True}]
    for i in range(9):  # nine tiny statics all inside the ball's AABB
        bodies.append({"name": f"s{i}", "shape": "box", "pos": [500, 300],
                       "size": [4, 4], "static": True})
    out = T.inspect_world(_full(bodies), use_bank=False)
    w = _kinds(out, "contact_cap")
    assert len(w) == 1 and w[0]["bodies"] == ["ball"] and w[0]["contacts"] == 9


def test_few_contacts_do_not_trip_the_cap():
    out = T.inspect_world(_full([_FLOOR,
        {"name": "ball", "shape": "circle", "pos": [500, 25], "radius": 8,
         "control": True}]), use_bank=False)  # ball touches only the floor
    assert _kinds(out, "contact_cap") == []


# ---- Item 10: tunneling (thin/fast) ----------------------------------------
def test_thin_wall_and_segment_warn_tunneling_but_thick_wall_does_not():
    out = T.inspect_world(_full([
        {"name": "floor", "shape": "box", "pos": [500, 20], "size": [1000, 40],
         "static": True},                                     # thick -> no warn
        {"name": "thinwall", "shape": "box", "pos": [500, 300], "size": [4, 200],
         "static": True},                                     # 4px thin -> warn
        {"name": "seg", "shape": "segment", "pos": [200, 300], "a": [-50, 0],
         "b": [50, 0], "static": True},                       # zero-thickness -> warn
        {"name": "ball", "shape": "circle", "pos": [100, 100], "radius": 8,
         "control": True}]), use_bank=False)
    hit = {b for w in _kinds(out, "tunneling") for b in w["bodies"]}
    assert hit == {"thinwall", "seg"}
    assert "floor" not in hit


def test_no_tunneling_warning_without_a_dynamic_body():
    out = T.inspect_world({"bodies": [
        {"name": "seg", "shape": "segment", "pos": [200, 300], "a": [-50, 0],
         "b": [50, 0], "static": True}]}, use_bank=False)
    assert _kinds(out, "tunneling") == []  # nothing to tunnel it


# ---- Item 11: sensor layer/mask mismatch -----------------------------------
def _sensor_spec(mask):
    return _full([_FLOOR,
        {"name": "ball", "shape": "circle", "pos": [100, 100], "radius": 8,
         "control": True}], spec_version=2,
        sensors=[{"type": "raycast2d", "attach_to": "ball", "collision_mask": mask}])


def test_sensor_mask_excluding_layer_one_warns():
    out = T.inspect_world(_sensor_spec(2), use_bank=False)  # bit 0 unset
    w = _kinds(out, "layer_mask_mismatch")
    assert len(w) == 1 and w[0]["bodies"] == ["ball"] and w[0]["collision_mask"] == 2


def test_sensor_mask_including_layer_one_is_ok():
    assert _kinds(T.inspect_world(_sensor_spec(1), use_bank=False),
                  "layer_mask_mismatch") == []
    assert _kinds(T.inspect_world(_sensor_spec(3), use_bank=False),
                  "layer_mask_mismatch") == []  # bit 0 set


# ---- Item 12: predicate linter ---------------------------------------------
def _lint_problems(success):
    out = T.inspect_world(_full([_FLOOR,
        {"name": "ball", "shape": "circle", "pos": [100, 100], "radius": 8,
         "control": True},
        {"name": "goal", "shape": "box", "pos": [900, 100], "size": [40, 40],
         "static": True, "sensor": True}], success=success), use_bank=False)
    return {w["problem"] for w in _kinds(out, "predicate_lint")}


def test_lint_flags_c_style_logical_operators():
    assert "logical_operator" in _lint_problems('grounded("ball") && pos_x("ball") > 0')
    assert "logical_operator" in _lint_problems('grounded("ball") || pos_x("ball") > 0')


def test_lint_flags_integer_division_trap():
    assert "integer_division" in _lint_problems("steps / 2 > 5")
    assert "integer_division" not in _lint_problems("steps / 2.0 > 5")


def test_lint_flags_wrong_arity():
    assert "bad_arity" in _lint_problems("pos_x() > 0")
    assert "bad_arity" in _lint_problems('dist("ball") > 0')  # dist needs 2


def test_lint_flags_undefined_body_reference():
    assert "undefined_body" in _lint_problems('contacts("ball", "ghost")')
    assert "undefined_body" not in _lint_problems('contacts("ball", "goal")')


def test_lint_flags_forbidden_identifier_and_attribute_access():
    probs = _lint_problems('OS.get_name() == "x"')
    assert "bad_identifier" in probs   # OS / get_name not in the allow-list
    assert "bad_char" in probs          # the '.' attribute access


def test_clean_predicate_has_no_lint_warnings():
    assert _lint_problems('contacts("ball", "goal") and pos_x("ball") > 100.0') == set()


# =========================================================================== #
# TOP-DOWN world mode (SPEC.md §2b): zero gravity flips the gravity-dependent
# oracles — floating statics + ballistic forecast are meaningless (suppressed),
# and a linear_damp makes a park-at-rest goal satisfiable.
# =========================================================================== #
_BALL = {"name": "ball", "shape": "circle", "pos": [100, 100], "radius": 8,
         "control": True}


def _topdown(bodies, **extra):
    extra.setdefault("world", {"view": "topdown", "linear_damp": 1.5})
    return _full(bodies, **extra)


def test_topdown_summary_reports_view_and_zero_gravity():
    out = T.inspect_world(_topdown([_FLOOR, _BALL]), use_bank=False)
    assert out["summary"]["view"] == "topdown"
    assert out["summary"]["gravity"] == [0.0, 0.0]
    assert out["summary"]["ballistic"] is None       # no gravity -> no jump forecast


def test_side_view_is_the_default_with_gravity():
    out = T.inspect_world(_full([_FLOOR, _BALL]), use_bank=False)
    assert out["summary"]["view"] == "side"
    assert out["summary"]["gravity"] == [0.0, -900.0]


def test_topdown_suppresses_floating_static_but_side_warns():
    ledge = {"name": "ledge", "shape": "box", "pos": [400, 300], "size": [100, 20],
             "static": True}                          # nothing under it
    side = T.inspect_world(_full([_FLOOR, ledge, _BALL]), use_bank=False)
    assert [b for w in _kinds(side, "floating_static") for b in w["bodies"]] == ["ledge"]
    top = T.inspect_world(_topdown([_FLOOR, ledge, _BALL]), use_bank=False)
    assert _kinds(top, "floating_static") == []      # no "floor" in a plan view


def test_topdown_suppresses_ballistic_forecast_oob():
    rocket = {"name": "rocket", "shape": "circle", "pos": [500, 300], "radius": 8,
              "velocity": [0, 900], "control": True}
    side = T.inspect_world(_full([_FLOOR, rocket]), use_bank=False)
    assert len(_kinds(side, "forecast_oob")) == 1
    top = T.inspect_world(_topdown([_FLOOR, rocket]), use_bank=False)
    assert _kinds(top, "forecast_oob") == []         # damped coast, not ballistic


def test_topdown_with_damping_makes_park_satisfiable():
    # side view: a rest-at-goal with no clamp is unsatisfiable (bodies coast forever).
    side = T.inspect_world(_full([_FLOOR, _BALL], success='speed("ball") < 5'),
                           use_bank=False)
    assert len(_kinds(side, "unsatisfiable_park")) == 1
    # top-down with linear_damp>0: friction brings it to rest -> satisfiable, no warning.
    top = T.inspect_world(_topdown([_FLOOR, _BALL], success='speed("ball") < 5'),
                          use_bank=False)
    assert _kinds(top, "unsatisfiable_park") == []


def test_topdown_zero_damp_still_warns_unsatisfiable_park():
    # linear_damp explicitly 0 -> a top-down body still coasts forever, so the park
    # goal is unsatisfiable and the warning must fire.
    spec = _full([_FLOOR, _BALL], success='speed("ball") < 5',
                 world={"view": "topdown", "linear_damp": 0})
    out = T.inspect_world(spec, use_bank=False)
    assert len(_kinds(out, "unsatisfiable_park")) == 1


def test_topdown_still_flags_out_of_bounds():
    # View-independent geometry checks (bounds, duplicates, overlaps) are unaffected.
    spec = _topdown([
        {"name": "g", "shape": "box", "pos": [100, 100], "size": [40, 40], "static": True},
        {"name": "esc", "shape": "circle", "pos": [1180, 100], "radius": 10, "control": True},
    ], world_size=(200, 200))
    out = T.inspect_world(spec, use_bank=False)
    oob = [w for w in out["warnings"] if w["kind"] == "out_of_bounds"]
    assert oob and oob[0]["bodies"] == ["esc"]


def test_smoke_on_topdown_slalom_fixture():
    with open(os.path.join(_FIXTURES, "topdown_slalom.spec.json"), encoding="utf-8") as fh:
        spec = json.load(fh)
    out = T.inspect_world(spec)
    assert out["summary"]["view"] == "topdown"
    assert out["summary"]["gravity"] == [0.0, 0.0]
    # the cart parks via contained()+speed on a rotatable box, but the damped top-down
    # world means it is NOT flagged unsatisfiable, and there is no floating-static noise.
    assert _kinds(out, "unsatisfiable_park") == []
    assert _kinds(out, "floating_static") == []
