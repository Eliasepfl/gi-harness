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
