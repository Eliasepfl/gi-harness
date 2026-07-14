"""Tests for the parts bank (v2.2 step 1): loader/validator, content hash,
World.part() spawning across categories (incl. the wrecking_ball subassembly),
the bounded-override whitelist, a hand-written mini game that clears the full
verify_game funnel end-to-end, and the whole-bank bank-CI pass.

Run from the repo root: python -m pytest tests/test_bank.py -q
"""

from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.core import bank as bankmod  # noqa: E402
from harness.core.bank import (  # noqa: E402
    BankError, BankOverrideError, Bank, content_hash, load_bank, resolve_part,
)
from harness.bank_ci import certify_bank  # noqa: E402
from harness.core.world import World  # noqa: E402

CATEGORIES = ("terrain", "prop", "hazard", "mobile", "trigger")


# ====================================================================== #
# Loading & validation
# ====================================================================== #
def test_bank_loads_and_validates():
    bank = load_bank("v1", use_cache=False)
    assert isinstance(bank, Bank)
    assert bank.bank_version == "1.0.0"
    assert len(bank.parts) == 60
    # Every declared category is populated.
    for cat in CATEGORIES:
        assert bank.by_category(cat), f"no parts in category {cat}"
    # Every entry carries the mandatory fields (schema contract).
    for name, part in bank.parts.items():
        assert part["category"] in bankmod.CATEGORIES
        assert part["summary"] and isinstance(part["summary"], str)
        assert part["sprite"] is None                 # lazy: sprites come later
        assert part["primary"] in {b["role"] for b in part["assembly"]}
        prov = part["provenance"]
        assert prov["license"] == "CC0-1.0"
        assert all(k in prov for k in ("author", "source"))


def test_category_counts():
    bank = load_bank("v1", use_cache=False)
    counts = {c: len(bank.by_category(c)) for c in CATEGORIES}
    assert counts == {"terrain": 15, "prop": 16, "hazard": 7, "mobile": 8, "trigger": 8}


def test_unknown_part_raises():
    bank = load_bank("v1", use_cache=False)
    with pytest.raises(BankError):
        bank.get("nonexistent_part")


# ====================================================================== #
# Content hash / lock pinning
# ====================================================================== #
def test_content_hash_stable_and_matches_lock():
    bank = load_bank("v1", use_cache=False)
    # Recomputing over the same data is stable.
    assert content_hash(bank.data) == bank.content_hash
    # bank.lock exists and pins exactly this hash.
    assert bank.lock is not None
    assert bank.lock["content_hash"] == bank.content_hash
    assert bank.hash_ok is True


def test_verify_hash_ok():
    # verify_hash must not raise when the lock matches.
    bank = load_bank("v1", verify_hash=True, use_cache=False)
    assert bank.hash_ok


# ====================================================================== #
# World.part() — spawning across every category
# ====================================================================== #
def _spawn(entry):
    """Spawn one entry via World.part() the way bank-CI would; return (world, name)."""
    cat = entry["category"]
    cert = entry.get("cert", {})
    ground = cert.get("ground", cat == "prop")
    world = World(seed=0)
    if ground:
        world.add("g", "box", pos=(400, 10), size=(1000, 20), static=True, friction=0.9)
    pos = cert.get("pos") or [400, 140]
    name = world.part("it", entry["name"], pos=pos)
    return world, name


def test_part_spawns_each_category():
    bank = load_bank("v1", use_cache=False)
    for cat in CATEGORIES:
        entry = bank.get(bank.by_category(cat)[0])
        world, name = _spawn(entry)
        assert name == "it"                       # primary registered under bare name
        assert name in world.entities()
        world.step(120)
        assert not any(e["type"] == "nan_detected" for e in world.events()), cat
        q = world.query(name)
        if cat == "terrain":
            assert q["static"] is True
        elif cat == "prop":
            assert q["static"] is False
        elif cat in ("hazard", "trigger"):
            assert q["sensor"] is True


def test_part_returns_primary_and_does_not_control():
    world = World(seed=0)
    world.add("g", "box", pos=(400, 10), size=(800, 20), static=True)
    name = world.part("crate", "crate_light", pos=(400, 120))
    assert name == "crate"
    # part() must NOT designate a controlled body — that stays the game's choice.
    assert world.controlled() is None


# ====================================================================== #
# The wrecking_ball subassembly (the ledger-motivated part)
# ====================================================================== #
def test_wrecking_ball_subassembly_swings_without_nan():
    world = World(seed=0)
    world.add("g", "box", pos=(400, 10), size=(800, 20), static=True)
    primary = world.part("wrecker", "wrecking_ball", pos=(400, 230), chain=250, mass=12)
    # Sub-body namespacing: primary is the bare name; anchor is name.anchor.
    assert primary == "wrecker"
    assert "wrecker" in world.entities()
    assert "wrecker.anchor" in world.entities()
    world.control("wrecker")

    ax, ay = world.query("wrecker.anchor")["pos"]
    bx0, by0 = world.query("wrecker")["pos"]
    start_dist = math.hypot(ax - bx0, ay - by0)
    assert start_dist == pytest.approx(250.0, abs=1.0)   # chain override honoured

    # Pump the ball: it must SWING (deviate horizontally) and the rigid chain must
    # hold its length — the exact failure the campaign's hand-built joint hit (NaN).
    world.impulse("wrecker", (900, 0))
    max_dev = 0.0
    for _ in range(90):
        world.step(1)
        bx, by = world.query("wrecker")["pos"]
        max_dev = max(max_dev, abs(bx - bx0))
        assert math.hypot(ax - bx, ay - by) == pytest.approx(250.0, abs=6.0)
    assert max_dev > 20.0                                  # genuinely swung
    assert not any(e["type"] == "nan_detected" for e in world.events())


def test_wrecking_ball_counts_two_bodies():
    world = World(seed=0)
    world.part("w", "wrecking_ball", pos=(400, 230))
    assert set(world.entities()) == {"w", "w.anchor"}     # 2 bodies toward the cap


# ====================================================================== #
# Bounded-override whitelist
# ====================================================================== #
def test_scale_override_resizes_geometry():
    small = World(seed=0)
    small.part("c", "crate_light", pos=(400, 300), scale=0.5)
    big = World(seed=0)
    big.part("c", "crate_light", pos=(400, 300), scale=2.0)
    ws = small.query("c")["bbox"]
    wb = big.query("c")["bbox"]
    width_s = ws[2] - ws[0]
    width_b = wb[2] - wb[0]
    assert width_b == pytest.approx(width_s * 4.0, rel=0.02)   # 2.0 / 0.5 = 4x


def test_density_override_rejected():
    world = World(seed=0)
    with pytest.raises(ValueError, match="unknown override 'density'"):
        world.part("c", "crate_light", pos=(400, 120), density=5.0)


def test_out_of_range_override_rejected():
    world = World(seed=0)
    with pytest.raises(BankOverrideError, match="out of range"):
        world.part("c", "crate_light", pos=(400, 120), mass=99.0)


def test_override_error_is_valuerror():
    # G0 surfaces build-time ValueErrors as ENV_ERROR, so the whitelist errors
    # must be ValueError subclasses.
    assert issubclass(BankOverrideError, ValueError)


def test_resolver_scales_pin_anchors_and_offsets():
    bank = load_bank("v1", use_cache=False)
    entry = bank.get("wrecking_ball")
    resolved = resolve_part(entry, "w", (400, 200), {"scale": 1.4})
    # anchor offset [0,270] scaled by 1.4 -> y = 200 + 378 = 578.
    anchor = next(b for b in resolved.bodies if b["name"] == "w.anchor")
    assert anchor["kwargs"]["pos"][1] == pytest.approx(578.0)
    assert resolved.primary == "w"
    assert resolved.roles == {"anchor": "w.anchor", "ball": "w"}
    assert [j["verb"] for j in resolved.joints] == ["pin"]


# ====================================================================== #
# Hand-written mini game -> full verify_game funnel (end-to-end)
# ====================================================================== #
_MINIGAME = '''TITLE = "Roll to the Goal"
PROMPT = "roll the ball across the yard into the goal zone"
ACTIONS = ["push_right", "push_left", "hop"]


def build(world):
    world.part("ground", "ground", pos=(400, 10))
    world.part("left_wall", "wall", pos=(20, 170))
    world.part("right_wall", "wall", pos=(788, 170))
    world.part("ball", "ball_light", pos=(140, 60))
    world.control("ball")
    world.part("goal", "goal_zone", pos=(580, 170), scale=2.6)


def act(world, action):
    if action == "push_right":
        world.impulse("ball", (90, 0))
    elif action == "push_left":
        world.impulse("ball", (-90, 0))
    elif action == "hop":
        world.impulse("ball", (0, 120))


def _in_goal(world):
    b = world.query("ball")["pos"]
    g = world.query("goal")["bbox"]
    return g[0] <= b[0] <= g[2] and g[1] <= b[1] <= g[3]


def success(world):
    return _in_goal(world)


def checkpoints(world):
    x = world.query("ball")["pos"][0]
    return {"started": x > 200, "crossed_mid": x > 320, "near_goal": x > 420}
'''


def test_minigame_passes_full_verify_funnel(tmp_path):
    from harness.verify.gameverify import verify_game

    path = tmp_path / "minigame.py"
    path.write_text(_MINIGAME, encoding="utf-8")
    report = verify_game(str(path), sandboxed=False)
    assert report["passed"], report["hint"]
    assert report["failure_class"] is None
    for layer in ("G0_static", "G1_rollout", "G2_goal", "G3_solve"):
        assert report["layers"][layer]["passed"], layer
    # The witness must be a real, multi-stage play (parts drove a valid game).
    assert report["witness"]["ticks"] >= 5


# ====================================================================== #
# Whole-bank certification
# ====================================================================== #
def test_bank_ci_passes_on_v1():
    bank, rows = certify_bank("v1")
    failed = [r for r in rows if not r["ok"]]
    assert not failed, "bank-CI failures: " + "; ".join(
        f"{r['name']}({', '.join(r['failed'])})" for r in failed)
    assert len(rows) == 60
    assert bank.hash_ok
