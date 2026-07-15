"""Prompt tests - GDScript lane only.

The spec-lane prompt library was purged (Elias, 2026-07-15, commit 64e95b0);
its tests died with it. What survives is the gdscript engine section:
compose('gdscript') assembles the GameAPI contract prompt.
"""
from __future__ import annotations

import re

import pytest

from harness.gen import prompts as P


# api_gdscript.md is SELF-CONTAINED and, per Elias, examples-free: NO complete game
# ever appears (worked examples steer the small model into one attractor).

def _gdscript_blocks(sp):
    return re.findall(r"```gdscript\n(.*?)```", sp, re.DOTALL)


def test_all_sections_present_in_composed_gdscript():
    sp = P.compose("gdscript")
    # api_gdscript.md — the self-contained code contract + tables.
    assert "ONE GDScript file" in sp                # emit a .gd class, real code
    assert "extends GameBase" in sp                 # the base class it extends
    assert "DESIGN BEFORE YOU CODE" in sp           # design-before-code scaffold
    assert "DIVERSITY IS THE JOB" in sp             # the diversity mandate
    # The GameAPI contract tables: required methods + base-class services.
    for method in ("game_meta()", "build_world()", "on_action(action)",
                   "checkpoints()", "success()"):
        assert method in sp, method
    for service in ("add_body(name, opts)", "add_static(name, opts)",
                    "add_sensor(name, opts)", "control(name)", "set_gravity(vec)",
                    "impulse(name, vec)", "force(name, vec)", "set_velocity(name, vec)",
                    "torque(name, mag)", "thrust(name, mag)"):
        assert service in sp, service
    for query in ("grounded(name)", "contacts(a, b)", "contained(a, b)", "dist(a, b)"):
        assert query in sp, query
    assert "```gdscript" in sp                       # the placeholder-skeleton fence
    # design_block_gdscript.md — DESIGN output format, gdscript fence.
    assert "DESIGN" in sp and "Milestones:" in sp and "Parts used:" in sp
    assert "# Output format" in sp


def test_composed_gdscript_has_no_complete_game_examples():
    # Elias rejected worked examples as attractors: the guide carries ONLY a
    # placeholder FORM, never a copyable game. Grep-test that discipline.
    sp = P.compose("gdscript")
    blocks = _gdscript_blocks(sp)
    assert blocks, "expected at least the skeleton block"
    # The skeleton is a form, not a design: placeholder slots + pass bodies.
    skeleton = max(blocks, key=lambda b: b.count("func "))
    assert "<" in skeleton and "pass" in skeleton
    assert "shape to fill, not a game to copy" in sp
    # Every OTHER fenced gdscript block is a <=3-line syntax fragment.
    for b in blocks:
        if b is skeleton:
            continue
        nonblank = [ln for ln in b.splitlines() if ln.strip()]
        assert len(nonblank) <= 3, nonblank
    # No fenced block is a filled, copyable game (real construction + a concrete win).
    for b in blocks:
        filled_build = b.count("add_body(") + b.count("add_static(")
        concrete_win = ("func success" in b and "return <" not in b
                        and "return false" not in b)
        assert not (filled_build >= 2 and concrete_win), "a worked game leaked in"
    # The offline fixture's own game must never appear in the designer prompt.
    assert "Arm and Dock" not in sp


def test_gdscript_banned_list_states_determinism_and_sandbox_reasons():
    sp = P.compose("gdscript")
    assert "BANNED" in sp
    # The banned families, each named so the G0 scanner's finding is teachable.
    for banned in ("OS.", "FileAccess", "HTTPRequest", "StreamPeerTCP", "Thread",
                   "WorkerThreadPool", "Time.", "randi()", "randf()", "preload(",
                   "set_script", "Expression", "get_tree()"):
        assert banned in sp, banned
    # The WHY is the two hard rules, not style.
    flat = " ".join(sp.lower().split())
    assert "sandbox escape" in flat
    assert "nondeterministic" in flat or "nondeterminism" in flat
    assert "seeded" in flat            # use the host's seeded rng, not global randi


def test_gdscript_gravity_is_the_games_own_choice():
    # View guidance: gravity is set by the GAME in build_world, not chosen for it.
    sp = P.compose("gdscript")
    assert "set_gravity(Vector2(0, -900))" in sp       # side view anchor
    assert "set_gravity(Vector2.ZERO)" in sp           # topdown anchor
    flat = " ".join(sp.lower().split())
    assert "gravity is yours to set" in flat or "gravity choice you make" in flat


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
    assert sp.index("extends GameBase") < sp.index("GD_MENU_MARKER")
    assert sp.index("GD_MENU_MARKER") < sp.index("# Output format")
    assert "GD_MENU_MARKER" not in P.compose("gdscript")


def test_composed_gdscript_has_no_spec_or_pyjs_idioms_or_placeholders():
    sp = P.compose("gdscript")
    # The code lane must not leak the declarative-spec vocabulary...
    for spec_tok in ('"verb": "impulse"', "predicate DSL", "```json",
                     "whitelisted predicate", "on_step\":"):
        assert spec_tok not in sp, spec_tok
    # ...nor the py/js construction idioms.
    for pyjs in ("world.add", "world.part(", "world.control", "pymunk", "Planck.js"):
        assert pyjs not in sp, pyjs
    # Every {..} placeholder the shared substitution carries is resolved.
    for token in ("{lang}", "{fence}", "{artifact}", "{substrate}", "{false}",
                  "{import_rule}", "{rng_forbid}", "{dict_word}"):
        assert token not in sp, token


def test_gdscript_physics_and_failures_are_self_contained():
    sp = P.compose("gdscript")
    # Physics guidance (derive sizes, do not memorize) + the code-gate failure table.
    assert "Physics the host enforces" in sp
    assert "TUNNELLING" in sp
    assert "Common failures" in sp
    for row in ("G0 parse error", "G0 banned API", "G0 contract probe",
                "G1 containment escape", "G1 dead action", "G3 grounded-gated jump"):
        assert row in sp, row


def test_gdscript_integrity_freezes_its_prompt_sections():
    """The real repo: the gdscript section files are tracked base content, so a
    mid-run edit invalidates the run exactly like the other prompt sections."""
    root = INT.__file__.rsplit("harness", 1)[0].rstrip("\\/")
    snap = INT.snapshot(root)
    assert "harness/gen/prompts/api_gdscript.md" in snap
    assert "harness/gen/prompts/design_block_gdscript.md" in snap
