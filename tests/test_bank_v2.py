"""Tests for the v2 parts bank (ASSET_BANK_V2.md): the volume+role schema, the
mechanical v1->v2 migration, the deterministic parametric volume families, the
extended bank-CI (volume + physics_class floor + role_contract), the advisory
name|volume|role menu, and the integrity fold-in of the new catalog.

Run from the repo root: python -m pytest tests/test_bank_v2.py -q
"""

from __future__ import annotations

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.core import bank as bankmod  # noqa: E402
from harness.core.bank import (  # noqa: E402
    BankValidationError, PHYSICS_CLASSES, ROLES, ROLE_CONTRACT_TOKENS,
    bank_dir, content_hash, load_bank, validate_bank,
)
from harness.bank_tools import build_v2 as build  # noqa: E402
from harness.bank_tools.migrate import migrate_bank  # noqa: E402
from harness.bank_tools.parametric import generate_parametric  # noqa: E402
from harness.bank_ci import certify_bank, certify_entry  # noqa: E402
from harness.gen import retrieval as R  # noqa: E402
from harness.core import integrity  # noqa: E402


def _v2():
    return load_bank("v2", use_cache=False)


def _v1_data():
    return load_bank("v1", use_cache=False).data


def _migrated():
    return {e["name"]: e for e in migrate_bank(_v1_data())}


def _catalog(entry: dict) -> dict:
    return {"schema_version": "2.0", "bank_version": "2.0.0", "parts": [entry]}


# ====================================================================== #
# Schema validation
# ====================================================================== #
def test_v2_loads_and_validates():
    b = _v2()
    assert b.is_v2 and b.schema_version == "2.0" and b.bank_version == "2.0.0"
    assert len(b.parts) >= 200                     # hundreds of certified entries
    for name, p in b.parts.items():
        assert p["physics_class"] in PHYSICS_CLASSES, name
        assert p["role"] in ROLES, name
        assert "sprite" not in p, name             # sprite retired for render_binding
        fp = p["volume"]["footprint_2d"]
        assert fp["shape"] in bankmod.FOOTPRINT_SHAPES, name
        assert p["volume"]["glb"] is None, name    # Path B reserved but null
        assert p["render_binding"]["primitive_2d"]["shape"], name
        assert p["render_binding"]["tscn"] is None, name
        assert all(t in ROLE_CONTRACT_TOKENS for t in p["role_contract"]), name
        assert p["primary"] in {b_["role"] for b_ in p["assembly"]}, name


def test_v2_by_role_and_physics_class():
    b = _v2()
    assert set(b.by_role("gate")) == {n for n in b.names() if n.startswith("gate_")}
    assert b.by_role("collectible")                # coins + key_gem
    assert len(b.by_physics_class("terrain")) > len(b.by_physics_class("prop"))


@pytest.mark.parametrize("mutate", [
    lambda e: e.update(physics_class="floaty"),          # bad physics_class
    lambda e: e.update(role="bouncer"),                  # bad role
    lambda e: e.__setitem__("sprite", None),             # v1 sprite key resurrected
    lambda e: e["volume"]["footprint_2d"].__setitem__("size", [0, 20]),  # 0 dim
    lambda e: e["volume"].__setitem__("glb", {"ref": "x.glb"}),          # glb set
    lambda e: e["render_binding"].__setitem__("tscn", "scene.tscn"),     # tscn set
    lambda e: e.update(role_contract=["banana"]),        # unknown token
    lambda e: e.update(role_contract=["primary_dynamic"]),  # static box != dynamic
    lambda e: e.pop("render_binding"),                   # missing render_binding
    lambda e: e.pop("volume"),                           # missing volume
    lambda e: e["provenance"].pop("license"),            # provenance gap
])
def test_bad_v2_entry_rejected_at_validation(mutate):
    e = copy.deepcopy(_v2().parts["box_2x1"])
    mutate(e)
    with pytest.raises(BankValidationError):
        validate_bank(_catalog(e))


# ====================================================================== #
# bank-CI gates (a bad entry that PASSES schema still fails certification)
# ====================================================================== #
def test_bank_ci_rejects_wrong_volume():
    e = copy.deepcopy(_v2().parts["box_2x1"])
    e["volume"]["footprint_2d"]["size"] = [999, 20]   # positive but wrong -> valid...
    validate_bank(_catalog(e))                        # ...schema accepts it, but
    row = certify_entry(e)                            # the live AABB check must not
    assert not row["ok"]
    assert any("volume" in f for f in row["failed"]), row["failed"]


def test_bank_ci_rejects_miswired_collectible():
    # A collectible whose body is NOT a sensor: the exact mis-wiring class the note
    # keeps covered by verification. Floor + role_contract must both flag it.
    e = copy.deepcopy(_v2().parts["coin_r1"])
    e["assembly"][0]["sensor"] = False
    row = certify_entry(e)
    assert not row["ok"]
    assert any("sensor" in f for f in row["failed"]), row["failed"]


def test_bank_ci_certifies_all_v2():
    bank, rows = certify_bank("v2")
    failed = [r for r in rows if not r["ok"]]
    assert not failed, "v2 bank-CI failures: " + "; ".join(
        f"{r['name']}({', '.join(r['failed'])})" for r in failed)
    assert len(rows) >= 200
    assert bank.hash_ok


# ====================================================================== #
# v1 -> v2 migration fidelity
# ====================================================================== #
def test_migration_preserves_60_entries_and_physics_fields():
    v1 = _v1_data()
    migrated = migrate_bank(v1)
    assert len(migrated) == 60
    v1_by = {p["name"]: p for p in v1["parts"]}
    assert {m["name"] for m in migrated} == set(v1_by)      # exact set preserved
    for m in migrated:
        o = v1_by[m["name"]]
        # category is split into physics_class + role; nothing physical changes.
        assert m["physics_class"] == o["category"]
        assert m["role"] in ROLES
        assert m["assembly"] == o["assembly"]
        assert m["joints"] == o.get("joints", [])
        assert m["primary"] == o["primary"]
        assert m["control_candidate"] == o.get("control_candidate")
        assert m["overridable"] == o.get("overridable", {})
        assert m["behavior"] == o.get("behavior")
        assert m["invariants"] == o.get("invariants", [])
        assert m["cert"] == o.get("cert", {})
        # sprite is gone; category is gone; render_binding + volume arrive.
        assert "sprite" not in m and "category" not in m
        assert m["render_binding"]["primitive_2d"]
        assert m["volume"]["footprint_2d"]["shape"] in bankmod.FOOTPRINT_SHAPES


@pytest.mark.parametrize("name,role", [
    ("lava_pool", "hazard"), ("key_gem", "collectible"), ("goal_zone", "goal"),
    ("pressure_zone", "goal"), ("checkpoint_zone", "goal"), ("wall", "obstacle"),
    ("pillar", "obstacle"), ("door_slab", "obstacle"), ("ground", "platform"),
    ("crate_light", "movable"), ("seesaw", "mover"), ("bush", "decor"),
    ("ramp30", "platform"),
])
def test_migration_role_mapping(name, role):
    assert _migrated()[name]["role"] == role


def test_ramp_migration_declares_walkable_slope():
    assert "walkable_slope" in _migrated()["ramp30"]["role_contract"]


def test_migrated_volume_derived_from_geometry():
    m = _migrated()
    assert m["ground"]["volume"]["footprint_2d"] == {"shape": "box", "size": [800, 20]}
    assert m["ball_light"]["volume"]["footprint_2d"]["shape"] == "circle"
    assert m["ramp30"]["volume"]["footprint_2d"]["shape"] == "poly"
    # a jointed subassembly collapses to its bounding box
    assert m["wrecking_ball"]["volume"]["footprint_2d"]["shape"] == "box"


# ====================================================================== #
# Parametric volume families — determinism + stable names
# ====================================================================== #
def test_parametric_is_byte_identical_across_runs():
    a = generate_parametric()
    b = generate_parametric()
    assert a == b
    assert json.dumps(a, sort_keys=True).encode() == json.dumps(b, sort_keys=True).encode()
    assert len(a) >= 150                              # hundreds, zero external assets


def test_parametric_names_stable_and_unique():
    entries = generate_parametric()
    names = [e["name"] for e in entries]
    assert len(names) == len(set(names))              # no collisions
    assert "box_2x1" in names
    assert any(n.startswith("ramp_") and n.endswith("_4x2") for n in names)
    assert any(n.startswith("gate_") for n in names)
    families = {n.split("_")[0] for n in names}
    assert {"box", "platform", "crate", "ball", "coin", "capsule",
            "ramp", "arc", "gate"} <= families


def test_committed_v2_catalog_matches_fresh_build():
    res = build.check_v2()
    assert res["ok"], "banks/parts/v2/parts.json is stale; rerun build_v2"
    assert res["parts"] >= 200


# ====================================================================== #
# Advisory menu — name | volume | role
# ====================================================================== #
def test_v2_menu_renders_volume_and_role():
    b = _v2()
    menu = R.build_menu(["box_2x1", "coin_r1", "gate_6x4"], "godot", bank=b)
    assert "box_2x1 | volume: box 40x20 | role: obstacle" in menu
    assert "volume: circle r=12 | role: collectible" in menu
    assert "role: gate" in menu
    # advisory framing from the UNCHANGED template survives
    assert "not a requirement" in menu
    assert "world.add" in menu
    # v2 has no sprites, so the v1 sprite-naming rule must be absent
    assert "binds sprites by name" not in menu


def test_v2_retrieve_menu_uses_v2_lines():
    b = _v2()
    menu, mode, names = R.retrieve_menu("a volcano level with lava", "godot", bank=b)
    assert mode == "menu"
    assert "lava_pool" in names
    assert "role: hazard" in menu and "volume:" in menu


# ====================================================================== #
# Content hash / integrity (the v2 catalog folds into the freeze)
# ====================================================================== #
def test_v2_content_hash_stable_and_locked():
    b = _v2()
    assert content_hash(b.data) == b.content_hash
    assert b.lock is not None and b.lock["content_hash"] == b.content_hash
    assert b.hash_ok


def test_integrity_folds_v2_and_a_prev2_manifest_sees_it_change():
    root = os.path.dirname(os.path.dirname(os.path.dirname(bank_dir("v2"))))
    snap = integrity.snapshot(root)
    assert "bank:v2" in snap and "bank:v1" in snap
    assert snap["bank:v2"] != snap["bank:v1"]         # distinct catalogs
    # A snapshot taken before v2 landed (lacking the bank:v2 key) must see the new
    # catalog as an added base-content change — i.e. the hash CHANGES when v2 lands.
    before = {k: v for k, v in snap.items() if k != "bank:v2"}
    assert "bank:v2" in integrity.violations(before, root)
