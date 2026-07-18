"""Prompt tests - GDScript lane only.

The spec-lane prompt library was purged (Elias, 2026-07-15); its tests died with it.
What survives is the gdscript engine section: compose('gdscript') assembles the
duck-typed METHOD-CONVENTION prompt (a plain Node implementing build/act/state/...,
NO base class -- godotworld/GAME_API.md), examples-free.
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
    # api_gdscript.md — the self-contained code contract + tables.
    assert "ONE GDScript file" in sp                # emit a .gd node, real code
    assert "NO BASE CLASS" in sp                    # a PLAIN node, nothing to resolve
    assert "DESIGN BEFORE YOU CODE" in sp           # design-before-code scaffold
    assert "DIVERSITY IS THE JOB" in sp             # the diversity mandate
    # MATERIAL REALITY — the spatial-milestone non-vacuity rule (STAKES's twin): a goal in
    # space is a thing, not a coordinate. Two stable pins harden the wording (a reword is a
    # coupled prompt+test edit + a fresh integrity snapshot, never a mid-run tweak).
    assert "MATERIAL REALITY" in sp
    assert "never off a bare coordinate" in sp
    # The seven method SIGNATURES the has_method contract probe checks (taught by name,
    # not as a filled skeleton — reference, not a worked example).
    for sig in ("func build(world_seed: int) -> void", "func act(action: String) -> void",
                "func state() -> Dictionary", "func checkpoints() -> Dictionary",
                "func is_success() -> bool", "func is_failure() -> bool",
                "func actions() -> Array"):
        assert sig in sp, sig
    # The rng is self-seeded (a determinism RULE, not an anchoring node/value).
    assert "RandomNumberGenerator" in sp
    # The state() snapshot keys the funnel reads.
    for key in ('"controlled"', '"static"', '"bodies"'):
        assert key in sp, key
    # design_block_gdscript.md — DESIGN output format.
    assert "DESIGN" in sp and "Milestones:" in sp and "Parts used:" in sp
    assert "# Output format" in sp


def test_gdscript_material_reality_binds_only_spatial_milestones():
    # The rule binds a WHERE-milestone to a real shaped node, but explicitly EXEMPTS a
    # milestone that is not about a place (a time/motion condition) — so the generator is not
    # pushed to drop tick/velocity milestones to dodge the check. And it names the advisory
    # signal ("flips in empty space") so the wording matches the verifier's ANCHORING hint.
    flat = " ".join(P.compose("gdscript").lower().split())
    assert "milestones not defined by a place need no anchor" in flat   # the exemption
    assert "flips in empty space" in flat                               # the advisory signal


def test_composed_gdscript_is_reference_not_a_worked_game():
    # Elias's discipline: the guide carries SIGNATURES + slot descriptions, never a
    # filled skeleton or a copyable game — a worked example anchors the small model
    # (and a filled skeleton hardcodes a dimension/shape it must not).
    sp = P.compose("gdscript")
    assert "there is deliberately no skeleton" in sp
    # NO fenced gdscript block is a complete, playable game body.
    for b in _gdscript_blocks(sp):
        builds = b.count("RigidBody") + b.count("StaticBody") + b.count("add_child(")
        assert not (builds >= 2 and "func " in b), "a worked game leaked in"
    # The offline template's own game never appears in the designer prompt.
    assert "Arm and Dock" not in sp
    assert "air-hockey puck that must drift" not in sp
    assert "_puck" not in sp and "_pads" not in sp


def test_gdscript_grants_2d_and_3d_dimension_freedom():
    # Elias: BOTH 2D and 3D are first-class; the fiction chooses, neither is the default.
    sp = P.compose("gdscript")
    assert "Node2D" in sp and "Node3D" in sp             # both dimension families named
    assert "PhysicsServer3D.set_active(true)" in sp      # the one 3D quirk, taught
    # The controlled body's shape/type is a design choice, never a forced circle.
    assert "default to a circle" in sp
    flat = " ".join(sp.lower().split())
    assert "whatever the game is about" in flat


def test_gdscript_banned_list_states_determinism_and_sandbox_reasons():
    sp = P.compose("gdscript")
    assert "BANNED" in sp
    # The HARD banned families, each named so the G0 scanner's finding is teachable.
    for banned in ("OS.", "FileAccess", "ResourceSaver", "HTTPRequest",
                   "StreamPeerTCP", "Thread", "WorkerThreadPool", "Time.",
                   "set_script", "Expression", "get_tree()", "randomize()"):
        assert banned in sp, banned
    # Guardrails v2: res:// asset loads are ALLOWED (sandbox-contained reads)...
    assert "`load()`/`preload()` of `res://` resources are ALLOWED" in sp
    # ...and guardrails v2 round 2: the global RNG read family is ALLOWED and
    # deterministic because the host pins the global RNG from world_seed each reset;
    # only randomize() (wall-clock reseed) stays banned.
    assert "randi()" in sp and "randf()" in sp
    flat = " ".join(sp.lower().split())
    assert "pins the global rng" in flat or "seeds the global rng" in flat
    assert "randomize()" in sp                            # the one banned RNG call
    # The WHY is the two hard rules, not style.
    assert "sandbox escape" in flat
    assert "nondeterministic" in flat or "nondeterminism" in flat
    # A self-seeded RandomNumberGenerator is still an offered path.
    assert "randomnumbergenerator" in flat


def test_gdscript_names_godot4_runtime_and_bans_godot3_ghosts():
    # 2026-07-17 parser-friction lever: the free model half-remembers Godot 3 and invents
    # symbols the strict parser rejects. ONE runtime hard-rule line (anti-hallucination,
    # NOT a design menu) names the runtime as Godot 4.x and the real API surface, and calls
    # out the Godot-3 ghosts the A/B traces caught. Sits in the BANNED/determinism-adjacent
    # runtime area, BEFORE the "Common gate failures" section.
    sp = P.compose("gdscript")
    flat = " ".join(sp.lower().split())
    assert "runtime is godot 4" in flat                 # the runtime is named
    # The REAL Godot-4 API names the model must reach for.
    for real in ("apply_central_force", "apply_torque_impulse", "overlaps_body",
                 "limit_length", "CharacterBody2/3D"):
        assert real in sp, real
    # The Godot-3 GHOSTS, each named as non-existent so the model unlearns it.
    for ghost in ("add_central_force", "apply_angular_impulse", "has_overlapping_body",
                  "Vector2.limited", "KinematicBody", "FixedJoint3D", "PolygonShape2D",
                  "MODE_"):
        assert ghost in sp, ghost
    # It is a RUNTIME rule, not steering about the game: it lands before the gate-failure
    # reminders and after the BANNED table (the determinism/sandbox runtime area).
    assert sp.index("BANNED") < sp.index("RUNTIME IS GODOT 4")
    assert sp.index("RUNTIME IS GODOT 4") < sp.index("Common gate failures")


def test_gdscript_gravity_is_the_games_own_choice():
    # View guidance: gravity/view is the GAME's to set - and the contract offers NO menu.
    # (2026-07-16 de-bias: enumerating "side elevation or topdown" was itself a two-item
    # menu steering the frame choice; steering belongs to the user prompt, not the harness.)
    sp = P.compose("gdscript")
    flat = " ".join(sp.lower().split())
    assert "yours to set" in flat                       # gravity/orientation is the game's
    assert "topdown" not in flat                        # no closed frame menu
    assert "side elevation" not in flat
    # No fiction menu either: the controlled body must not be pre-cast into example kinds.
    assert "a ship, a car" not in flat


def test_gdscript_prompt_carries_no_anchoring_residue():
    # Elias's anti-anchoring principle: the surface carries signatures + hard rules only,
    # NOT hardcoded values, a world extent, prescribed (2D-only) node types, or a skeleton.
    sp = P.compose("gdscript")
    # No world-size field (pure spec-lane residue) and no hardcoded arena extent.
    for residue in ("world_size", "[800", "800, 600", "WORLD_SIZE"):
        assert residue not in sp, residue
    # No prescribed 2D-only node types (the game picks bodies for its dimension).
    for node2d in ("RigidBody2D", "StaticBody2D", "Area2D", "CollisionShape2D"):
        assert node2d not in sp, node2d
    # No filled skeleton / worked game.
    assert "there is deliberately no skeleton" in sp
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


def test_gdscript_common_failures_are_taught():
    sp = P.compose("gdscript")
    # The per-gate failure reminders (hard rules), no hardcoded physics numbers.
    assert "Common gate failures" in sp
    for row in ("G0 parse error", "G0 banned API", "G0 contract probe",
                "G1 containment escape", "G1 dead action", "G1 single-action win",
                "G2 predicate already true at t=0", "G3 goal never true"):
        assert row in sp, row
    # The tunnelling principle is folded into the containment fix (a rule, not a value).
    flat = " ".join(sp.lower().split())
    assert "clamp the controlled body" in flat


def test_gdscript_integrity_freezes_its_prompt_sections():
    """The real repo: the gdscript section files are tracked base content, so a
    mid-run edit invalidates the run exactly like the other prompt sections."""
    root = INT.__file__.rsplit("harness", 1)[0].rstrip("\\/")
    snap = INT.snapshot(root)
    assert "harness/gen/prompts/api_gdscript.md" in snap
    assert "harness/gen/prompts/design_block_gdscript.md" in snap
