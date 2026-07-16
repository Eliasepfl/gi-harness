"""Tests for the ATLAS breeding experiment (gamegen breed builders/driver +
atlas placement/overlay), WITHOUT any network or engine.

The LLM dispatch is short-circuited by monkeypatching gamegen._dispatch, so the
driver's message building, arm routing, engine inference, slug identity and breed
metadata are all exercised offline; the placement/overlay helpers are pure math.
"""
from __future__ import annotations

import os

import pytest

from harness.atlas import breeding as BR
from harness.gen import gamegen as GG


# --- Fixtures -----------------------------------------------------------------

_GD_A = (
    'extends Node2D\n'
    '# parent A: fly through rings\n'
    'func build(world_seed: int) -> void:\n\tpass\n'
    'func is_success() -> bool:\n\treturn false\n'
)
_GD_B = (
    'extends Node2D\n'
    '# parent B: push the crate\n'
    'func build(world_seed: int) -> void:\n\tpass\n'
    'func is_success() -> bool:\n\treturn false\n'
)

_PROMPT_A = ("a 3D game: fly a small craft through a sequence of five floating "
             "rings hung at different heights")
_PROMPT_B = ("top-down maze: push a heavy crate through a stone labyrinth onto "
             "the exit pressure plate")


@pytest.fixture()
def parents(tmp_path):
    da = tmp_path / "parent_a_slug"
    db = tmp_path / "parent_b_slug"
    da.mkdir()
    db.mkdir()
    pa = da / "parent_a_slug.gd"
    pb = db / "parent_b_slug.gd"
    pa.write_text(_GD_A, encoding="utf-8")
    pb.write_text(_GD_B, encoding="utf-8")
    return str(pa), str(pb)


@pytest.fixture(autouse=True)
def _ledger_to_tmp(tmp_path_factory, monkeypatch):
    ledger_dir = tmp_path_factory.mktemp("ledger")
    monkeypatch.setattr(GG, "_LEDGER_PATH", str(ledger_dir / "test_ledger.jsonl"))


def _capture_dispatch(monkeypatch):
    """Replace _dispatch with a recorder returning a minimal COMPLETED result."""
    calls = {}

    def fake_dispatch(prompt, run_dir, backend, max_repairs, engine="py",
                      system=None, first_user=None):
        calls.update(prompt=prompt, run_dir=run_dir, backend=backend,
                     engine=engine, system=system, first_user=first_user)
        return {"game_path": None, "attempts": [], "verdict": "COMPLETED",
                "backend": "fake", "design": "DESIGN\nstub"}

    monkeypatch.setattr(GG, "_dispatch", fake_dispatch)
    return calls


# --- Arm A message builder ------------------------------------------------------

def test_breed_user_msg_carries_both_parents_and_contract():
    msg = GG._breed_user_msg(_GD_A, _GD_B, _PROMPT_A, _PROMPT_B, engine="gdscript")
    assert _GD_A in msg and _GD_B in msg
    assert _PROMPT_A in msg and _PROMPT_B in msg
    assert msg.count("```gdscript\n") == 2        # both parents fenced
    assert "exactly one ```gdscript" in msg       # the reply contract line
    assert "DESIGN" in msg
    assert "GDScript game class" in msg


def test_breed_user_msg_is_an_objective_search_not_a_recipe():
    msg = GG._breed_user_msg(_GD_A, _GD_B, _PROMPT_A, _PROMPT_B, engine="gdscript")
    low = msg.lower()
    # It must ask the model to FIND the child's win...
    assert "objective" in low and "neither parent" in low
    # ...and never prescribe which mechanic to inherit (anti-anchoring): the brief
    # leaves the inheritance choice to the model in so many words.
    assert "your design judgement" in low
    assert "mechanic of a" not in low and "pressure of b" not in low


# --- Arm B prompt fusion ----------------------------------------------------------

def test_fuse_prompts_quotes_both_seeds_and_is_deterministic():
    fused = GG.fuse_prompts(_PROMPT_A, _PROMPT_B)
    assert _PROMPT_A in fused and _PROMPT_B in fused
    assert fused == GG.fuse_prompts(_PROMPT_A, _PROMPT_B)


def test_fused_slug_never_collides_with_parent_slugs():
    # _slug truncates at 40 chars: a fused line starting with prompt_a verbatim
    # would inherit parent A's slug and overwrite its run dir. The template's
    # leading marker must prevent that, for BOTH arms' run prompts.
    fused_slug = GG._slug(GG.fuse_prompts(_PROMPT_A, _PROMPT_B))
    breed_slug = GG._slug(f"breed: {_PROMPT_A} || {_PROMPT_B}")
    for parent_prompt in (_PROMPT_A, _PROMPT_B):
        assert fused_slug != GG._slug(parent_prompt)
        assert breed_slug != GG._slug(parent_prompt)
    assert fused_slug != breed_slug                # arms get distinct child dirs


# --- breed_game driver -------------------------------------------------------------

def test_breed_game_arm_a_seeds_loop_with_both_sources(tmp_path, parents, monkeypatch):
    pa, pb = parents
    calls = _capture_dispatch(monkeypatch)
    res = GG.breed_game(pa, pb, arm="A", out_dir=str(tmp_path / "out"),
                        backend="openrouter", prompt_a=_PROMPT_A, prompt_b=_PROMPT_B,
                        use_bank=False)
    fu = calls["first_user"]
    assert fu is not None and _GD_A in fu and _GD_B in fu
    assert calls["engine"] == "gdscript"           # inferred from the .gd parent
    assert calls["prompt"].startswith("breed: ")
    breed = res["breed"]
    assert breed["arm"] == "A" and breed["fused_prompt"] is None
    assert breed["prompt_a"] == _PROMPT_A and breed["prompt_b"] == _PROMPT_B
    assert breed["parent_a"] == os.path.abspath(pa)


def test_breed_game_arm_b_rides_the_normal_path(tmp_path, parents, monkeypatch):
    pa, pb = parents
    calls = _capture_dispatch(monkeypatch)
    res = GG.breed_game(pa, pb, arm="b", out_dir=str(tmp_path / "out"),
                        backend="openrouter", prompt_a=_PROMPT_A, prompt_b=_PROMPT_B,
                        use_bank=False)
    assert calls["first_user"] is None             # normal generation path
    assert calls["prompt"] == GG.fuse_prompts(_PROMPT_A, _PROMPT_B)
    assert res["breed"]["arm"] == "B"
    assert res["breed"]["fused_prompt"] == calls["prompt"]


def test_breed_game_prompt_fallback_humanizes_slug(tmp_path, parents, monkeypatch):
    pa, pb = parents
    calls = _capture_dispatch(monkeypatch)
    res = GG.breed_game(pa, pb, arm="A", out_dir=str(tmp_path / "out"),
                        backend="openrouter", use_bank=False)
    # .gd sources carry no PROMPT line -> the humanized stem is the fallback.
    assert res["breed"]["prompt_a"] == "parent a slug"
    assert "parent a slug" in calls["first_user"]


def test_breed_game_rejects_bad_arm_and_missing_parent(tmp_path, parents):
    pa, pb = parents
    with pytest.raises(ValueError):
        GG.breed_game(pa, pb, arm="C", out_dir=str(tmp_path / "out"))
    with pytest.raises(FileNotFoundError):
        GG.breed_game(pa, str(tmp_path / "nope.gd"), arm="A",
                      out_dir=str(tmp_path / "out"))


def test_engine_from_path():
    assert GG._engine_from_path("x/y/game.gd") == "gdscript"
    assert GG._engine_from_path("x/y/game.spec.json") == "godot"
    assert GG._engine_from_path("x/y/game.js") == "js"
    assert GG._engine_from_path("x/y/game.py") == "py"
    assert GG._engine_from_path("x/y/game.txt") is None


# --- Atlas placement ---------------------------------------------------------------

def _row(slug, expansions, entropy, dim="2D"):
    return {"slug": slug, "descriptors": {"solver_expansions": expansions,
                                          "witness_entropy": entropy,
                                          "dimension": dim}}


def test_placement_between():
    a, b = _row("a", 0, 0.0), _row("b", 1000, 2.0)
    c = _row("c", 500, 1.0)
    out = BR.placement(c, a, b)
    assert out["label"] == "between"
    assert 0.45 <= out["t"] <= 0.55 and out["d_perp"] < 1e-6


def test_placement_collapsed_onto_parent():
    a, b = _row("a", 0, 0.0), _row("b", 1000, 2.0)
    c = _row("c", 10, 0.02)
    out = BR.placement(c, a, b)
    assert out["label"] == "collapsed_onto_parent_a"
    c2 = _row("c2", 990, 1.99)
    assert BR.placement(c2, a, b)["label"] == "collapsed_onto_parent_b"


def test_placement_beyond():
    a, b = _row("a", 100, 1.0), _row("b", 400, 1.2)
    c = _row("c", 2000, 2.0)                      # far past B -> new territory
    out = BR.placement(c, a, b)
    assert out["label"] == "beyond"
    assert out["t"] > 1.0


def test_placement_off_map_on_missing_descriptor():
    a, b = _row("a", 0, 0.0), _row("b", 1000, 2.0)
    c = _row("c", None, 1.0)
    assert BR.placement(c, a, b)["label"] == "off_map"


def test_placement_uses_library_bounds_when_rows_given():
    a, b = _row("a", 0, 0.0), _row("b", 100, 0.2)
    c = _row("c", 55, 0.1)
    rows = [a, b, c, _row("big", 10000, 3.0)]      # library stretches the bounds
    out = BR.placement(c, a, b, rows=rows)
    # In library units the parents nearly coincide -> the mid child collapses.
    assert out["d_parents"] < 0.08
    assert out["label"].startswith("collapsed") or out["label"] == "between"
    assert out["x_bounds"][1] == 10000


# --- Overlay SVG ---------------------------------------------------------------------

def test_render_breeding_svg_draws_triangles(tmp_path):
    rows = [_row("pa", 100, 0.5), _row("pb", 900, 1.5, dim="3D"),
            _row("kid_a", 500, 1.0), _row("kid_b", 1200, 0.2),
            _row("bystander", 300, 2.0)]
    triads = [{"child": "kid_a", "parent_a": "pa", "parent_b": "pb", "arm": "A"},
              {"child": "kid_b", "parent_a": "pa", "parent_b": "pb", "arm": "B"}]
    out = tmp_path / "triangles.svg"
    svg = BR.render_breeding_svg(rows, triads, str(out))
    assert out.is_file() and svg.startswith("<svg")
    assert "kid_a" in svg and "kid_b" in svg and "stroke-dasharray" in svg
    assert BR.ARM_COLORS["A"] in svg and BR.ARM_COLORS["B"] in svg


def test_render_breeding_svg_skips_off_map_members(tmp_path):
    rows = [_row("pa", 100, 0.5), _row("pb", 900, 1.5),
            _row("ghost", None, None)]
    triads = [{"child": "ghost", "parent_a": "pa", "parent_b": "pb", "arm": "A"}]
    svg = BR.render_breeding_svg(rows, triads, str(tmp_path / "t.svg"))
    assert svg.startswith("<svg")                  # no crash; ghost simply not drawn
