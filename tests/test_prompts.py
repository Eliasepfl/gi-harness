"""Prompt tests - GDScript lane only.

The spec-lane prompt library was purged (Elias, 2026-07-15); its tests died with it.
What survives is the gdscript engine section: compose('gdscript') assembles the
duck-typed METHOD-CONVENTION prompt (a plain Node implementing build/act/state/...,
NO base class -- godotworld/GAME_API.md), examples-free.

2026-07-18 (Elias): api_gdscript.md was shrunk TO THE BONE — every design-coaching
section (dimension, controlled body, stakes, MATERIAL REALITY, visuals, Godot-4
runtime ghosts, the banned-API table, the common-gate-failure cheat sheet) was
DELETED. Only the hard INTERFACE (the seven method signatures + the state() shape
the funnel reads) and the DETERMINISM rule remain; everything else the model now
learns from verify_game's typed hints via the feedback loop. Tests for the deleted
sections were removed with them.
"""
from __future__ import annotations

import re

import pytest

from harness.core import integrity as INT
from harness.gen import prompts as P


# api_gdscript.md is SELF-CONTAINED and, per Elias, examples-free: NO complete game
# ever appears (worked examples steer the small model into one attractor).

def _gdscript_blocks(sp):
    return re.findall(r"```gdscript\n(.*?)```", sp, re.DOTALL)


def test_all_sections_present_in_composed_gdscript():
    sp = P.compose("gdscript")
    # api_gdscript.md — the shrunk interface + determinism contract.
    assert "extends" in sp and "Node3D" in sp       # extend a plain node type
    assert "custom base class" in sp                # no custom base class (the host needs a Node, not RefCounted)
    # The seven method SIGNATURES the has_method contract probe checks (taught by name,
    # the ONLY interface guidance that survives — the funnel hard-depends on them).
    for sig in ("func build(world_seed: int) -> void", "func act(action: String) -> void",
                "func state() -> Dictionary", "func checkpoints() -> Dictionary",
                "func is_success() -> bool", "func is_failure() -> bool",
                "func actions() -> Array"):
        assert sig in sp, sig
    # The state() snapshot keys the funnel reads (structural interface, not coaching).
    for key in ('"controlled"', '"static"', '"bodies"'):
        assert key in sp, key
    # design_block_gdscript.md — DESIGN output format (unchanged by the shrink).
    assert "DESIGN" in sp and "Milestones:" in sp and "Parts used:" in sp
    assert "# Output format" in sp


def test_gdscript_prompt_carries_no_determinism_lecture():
    # 2026-07-18 (Elias): the whole Determinism section was DELETED. Determinism is now
    # ENFORCED, not requested — the host pins the global RNG (seed(world_seed) each reset)
    # and the strengthened G1 gate twins a real-action rollout, so any nondeterminism fails
    # certification regardless of what the prompt says. The prompt must NOT re-grow a
    # determinism lecture (verify, don't instruct).
    sp = P.compose("gdscript")
    assert "byte-for-byte" not in sp
    assert "RandomNumberGenerator" not in sp
    assert "# Determinism" not in sp


def test_composed_gdscript_is_reference_not_a_worked_game():
    # Elias's discipline: the guide carries SIGNATURES only, never a filled skeleton or a
    # copyable game — a worked example anchors the small model.
    sp = P.compose("gdscript")
    # NO fenced gdscript block is a complete, playable game body.
    for b in _gdscript_blocks(sp):
        builds = b.count("RigidBody") + b.count("StaticBody") + b.count("add_child(")
        assert not (builds >= 2 and "func " in b), "a worked game leaked in"
    # The offline template's own game never appears in the designer prompt.
    assert "Arm and Dock" not in sp
    assert "air-hockey puck that must drift" not in sp
    assert "_puck" not in sp and "_pads" not in sp


def test_gdscript_prompt_carries_no_anchoring_residue():
    # Elias's anti-anchoring principle: the surface carries signatures + the determinism
    # rule only, NOT hardcoded values, a world extent, prescribed (2D-only) node types.
    sp = P.compose("gdscript")
    # No hardcoded arena extent and no spec-lane WORLD_SIZE constant. NB the lowercase
    # `world_size` key IS legitimately documented now (the host reads it from state() to
    # enlarge the default 800x600 arena — a contract option, not anchoring residue).
    for residue in ("[800", "800, 600", "WORLD_SIZE"):
        assert residue not in sp, residue
    # No prescribed 2D-only node types (the game picks bodies for its dimension).
    for node2d in ("RigidBody2D", "StaticBody2D", "Area2D", "CollisionShape2D"):
        assert node2d not in sp, node2d
    # No filled skeleton / worked game in any fenced block.
    for b in _gdscript_blocks(sp):
        assert "func " not in b, "a code skeleton leaked in"


def test_compose_gdscript_deterministic_and_distinct():
    assert P.compose("gdscript") == P.compose("gdscript")
    assert P.compose("gdscript") != P.compose("godot")
    assert P.compose("gdscript") != P.compose("py")
    assert P.compose("gdscript") != P.compose("js")


def test_engine_key_maps_gdscript():
    assert P._engine_key("gdscript") == "gdscript"
    assert P._engine_key("GDSCRIPT") == "gdscript"


def test_menu_splices_into_gdscript_before_design_block():
    menu = "# Parts available for this prompt (optional menu)\nGD_MENU_MARKER\n"
    sp = P.compose("gdscript", menu)
    assert "GD_MENU_MARKER" in sp
    # The menu comes AFTER the api contract and BEFORE the DESIGN output format.
    assert sp.index("func build(world_seed") < sp.index("GD_MENU_MARKER")
    assert sp.index("GD_MENU_MARKER") < sp.index("# Output format")
    assert "GD_MENU_MARKER" not in P.compose("gdscript")


def test_composed_gdscript_has_no_spec_or_pyjs_idioms_or_placeholders():
    sp = P.compose("gdscript")
    # The code lane must not leak the declarative-spec vocabulary...
    for spec_tok in ('"verb": "impulse"', "predicate DSL", "```json",
                     "whitelisted predicate", "on_step\":"):
        assert spec_tok not in sp, spec_tok
    # ...nor the py/js construction idioms, nor the retired GameBase service API.
    for stale in ("world.add", "world.part(", "world.control", "pymunk", "Planck.js",
                  "extends GameBase", "add_body(", "set_gravity("):
        assert stale not in sp, stale
    # Every {..} placeholder the shared substitution carries is resolved.
    for token in ("{lang}", "{fence}", "{artifact}", "{substrate}", "{false}",
                  "{import_rule}", "{rng_forbid}", "{dict_word}"):
        assert token not in sp, token


def test_gdscript_integrity_freezes_its_prompt_sections():
    """The real repo: the gdscript section files are tracked base content, so a
    mid-run edit invalidates the run exactly like the other prompt sections."""
    root = INT.__file__.rsplit("harness", 1)[0].rstrip("\\/")
    snap = INT.snapshot(root)
    assert "harness/gen/prompts/api_gdscript.md" in snap
    assert "harness/gen/prompts/design_block_gdscript.md" in snap
