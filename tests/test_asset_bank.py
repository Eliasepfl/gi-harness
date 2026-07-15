"""Tests for harness.demo.asset_bank -- the render-only asset matcher.

Offline-first: the LLM route is exercised with an injected fake completion fn,
so nothing here touches the network (mirrors tests/test_skill_context.py).
"""

import json

import pytest

from harness.demo import asset_bank as AB

# A tiny hermetic manifest so logic tests don't couple to the real bank contents.
FIX = {
    "assets": [
        {"id": "car", "description": "a small car", "tags": ["vehicle", "car"],
         "archetype": "vehicle", "dimensions": {"size": [1.0, 1.0, 2.0]},
         "collision": {"primitive": "box"}},
        {"id": "tree", "description": "a leafy tree", "tags": ["tree", "plant"],
         "archetype": "tree", "dimensions": {"size": [2.0, 4.0, 2.0]},
         "collision": {"primitive": "box"}},
        {"id": "robot", "description": "a walking robot", "tags": ["robot", "bot"],
         "archetype": "robot", "dimensions": {"size": [1.0, 1.5, 1.0]},
         "collision": {"primitive": "capsule"}},
        {"id": "chest", "description": "a crate box", "tags": ["crate", "box"],
         "archetype": "crate", "dimensions": {"size": [1.0, 1.0, 1.0]},
         "collision": {"primitive": "box"}},
        {"id": "rock", "description": "a round boulder", "tags": ["ball", "round"],
         "archetype": "ball", "dimensions": {"size": [1.0, 1.0, 1.0]},
         "collision": {"primitive": "sphere"}},
    ]
}


# --------------------------------------------------------------------------- #
# Offline fallback (trivial, no taxonomy)
# --------------------------------------------------------------------------- #
def test_offline_exact_id_names_map():
    assert AB.match("car", manifest=FIX) == "car"
    assert AB.match("tree", manifest=FIX) == "tree"
    assert AB.match("robot", manifest=FIX) == "robot"
    assert AB.match("chest", manifest=FIX) == "chest"


def test_offline_junk_is_none():
    assert AB.match("xyzzy", manifest=FIX) is None
    assert AB.match("frobnicate", manifest=FIX) is None
    assert AB.match("", manifest=FIX) is None


def test_offline_camel_and_separator_names():
    # a token equals an id
    assert AB.match("player_robot", manifest=FIX) == "robot"
    assert AB.match("EnemyCar", manifest=FIX) == "car"
    # substring: "racecar" contains "car"
    assert AB.match("racecar", manifest=FIX) == "car"


def test_offline_is_deterministic():
    a = AB.match("player_robot", manifest=FIX)
    b = AB.match("player_robot", manifest=FIX)
    assert a == b == "robot"


def test_offline_ignores_shape_info_gracefully():
    assert AB.match("car", {"shape": "sphere"}, manifest=FIX) == "car"
    assert AB.match("car", None, manifest=FIX) == "car"


# --------------------------------------------------------------------------- #
# route_assets: offline batch
# --------------------------------------------------------------------------- #
def test_route_offline_batch_maps_and_nones():
    out = AB.route_assets("", ["car", "tree", "xyzzy"], FIX, use_llm=False)
    assert out == {"car": "car", "tree": "tree", "xyzzy": None}


def test_route_accepts_body_dicts():
    bodies = [{"name": "car", "shape": "box", "size": [1, 1, 2]}, {"name": "nope"}]
    out = AB.route_assets("", bodies, FIX, use_llm=False)
    assert out == {"car": "car", "nope": None}


# --------------------------------------------------------------------------- #
# route_assets: LLM route with an injected fake completion fn (no network)
# --------------------------------------------------------------------------- #
def test_route_llm_mapping_validates_ids():
    def fake(system, messages):
        # blob -> unknown id (must drop to None); wall -> explicit null
        return json.dumps({"hero": "car", "enemy": "robot",
                           "blob": "does-not-exist", "wall": None})

    bodies = ["hero", "enemy", "blob", "wall"]
    out = AB.route_assets("a chase game", bodies, FIX, use_llm=True, complete_fn=fake)
    assert out == {"hero": "car", "enemy": "robot", "blob": None, "wall": None}


def test_route_llm_tolerates_code_fences_and_prose():
    def fake(system, messages):
        return "Sure!\n```json\n{\"a\": \"tree\"}\n```\n"

    out = AB.route_assets("g", ["a"], FIX, use_llm=True, complete_fn=fake)
    assert out == {"a": "tree"}


def test_route_llm_failure_falls_back_offline():
    def boom(system, messages):
        raise RuntimeError("backend down")

    out = AB.route_assets("g", ["car", "xyzzy"], FIX, use_llm=True, complete_fn=boom)
    assert out == {"car": "car", "xyzzy": None}  # offline fallback fills in


def test_route_cache_is_reproducible_without_recall(tmp_path):
    calls = {"n": 0}

    def fake(system, messages):
        calls["n"] += 1
        return json.dumps({"hero": "car"})

    cache = tmp_path / "game.assets.json"
    first = AB.route_assets("g", ["hero"], FIX, use_llm=True,
                            complete_fn=fake, cache_path=str(cache))
    assert first == {"hero": "car"}
    assert cache.is_file()
    assert calls["n"] == 1

    # Second call hits the cache: no new completion call.
    second = AB.route_assets("g", ["hero"], FIX, use_llm=True,
                             complete_fn=fake, cache_path=str(cache))
    assert second == {"hero": "car"}
    assert calls["n"] == 1


def test_match_single_body_via_llm_mock():
    def fake(system, messages):
        return json.dumps({"speeder": "car"})

    got = AB.match("speeder", {"shape": "box"}, manifest=FIX,
                   use_llm=True, game_context="race", complete_fn=fake)
    assert got == "car"


# --------------------------------------------------------------------------- #
# Real committed manifest smoke
# --------------------------------------------------------------------------- #
def test_real_manifest_loads_and_covers_archetypes():
    mf = AB.load_manifest()
    assert mf["count"] >= 8
    ids = [a["id"] for a in mf["assets"]]
    assert len(ids) == len(set(ids)), "asset ids must be unique"
    archetypes = {a["archetype"] for a in mf["assets"]}
    for need in ("vehicle", "tree", "crate", "ball", "robot"):
        assert need in archetypes, f"missing archetype {need}"


def test_real_manifest_assets_have_measured_properties():
    mf = AB.load_manifest()
    for a in mf["assets"]:
        dims = a["dimensions"]
        assert dims is not None and len(dims["size"]) == 3
        assert all(v > 0 for v in dims["size"])
        assert a["collision"]["primitive"] in ("box", "sphere", "capsule")
        assert "license" in a and "source" in a


def test_real_manifest_offline_match_examples():
    mf = AB.load_manifest()
    assert AB.match("car", manifest=mf) == "car"
    assert AB.match("robot", manifest=mf) == "robot"
    assert AB.match("qwertyuiop", manifest=mf) is None
