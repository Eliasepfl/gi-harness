"""Tests for harness.gen.skill_context — deterministic gd-agentic skill retrieval
and its injection into the gdscript generation prompt.

Pure-python, no network, no LLM. Every unit test runs against a tiny FIXTURE
skills library under tests/fixtures/gd_skills/ (2-3 fake SKILL.md + a fake
index), so CI never needs the real 19 MB clone. One test exercises the REAL
sibling library, skipped when it is not present.
"""
from __future__ import annotations

import json
import os
import types

import pytest

from harness.gen import skill_context as SC
from harness.gen import prompts as P
import harness.gen.gamegen as GG


_FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "gd_skills")
_ABSENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "no_such_lib")


# --------------------------------------------------------------------------- #
# Locating the library
# --------------------------------------------------------------------------- #
def test_fixture_library_resolves():
    assert SC.library_root(_FIX) == os.path.abspath(_FIX)
    idx = SC.load_index(_FIX)
    assert {e["name"] for e in idx} >= {"godot-genre-platformer", "godot-2d-physics"}


def test_env_var_locates_library(monkeypatch):
    monkeypatch.setenv("GD_AGENTIC_SKILLS_DIR", _FIX)
    assert SC.library_root() == os.path.abspath(_FIX)


def test_absent_library_is_none():
    assert SC.library_root(_ABSENT) is None
    assert SC.load_index(_ABSENT) == []


# --------------------------------------------------------------------------- #
# Selection: determinism
# --------------------------------------------------------------------------- #
def test_selection_is_deterministic():
    a = SC.select_skills("platformer jump physics", k=3, root=_FIX)
    b = SC.select_skills("platformer jump physics", k=3, root=_FIX)
    assert [s.name for s in a] == [s.name for s in b]
    # Same objects field-for-field (names, descriptions, bodies all reproduce).
    assert a == b
    # And the rendered block is byte-identical across calls.
    assert (SC.render_skill_context("platformer jump physics", k=3, root=_FIX)
            == SC.render_skill_context("platformer jump physics", k=3, root=_FIX))


# --------------------------------------------------------------------------- #
# Selection: relevance
# --------------------------------------------------------------------------- #
def test_platformer_prompt_ranks_relevant_skill_above_unrelated():
    names = [s.name for s in SC.select_skills("platformer jump physics", k=2, root=_FIX)]
    # The platformer/physics skills are selected; the unrelated audio skill is not.
    assert "godot-genre-platformer" in names
    assert "godot-2d-physics" in names
    assert "godot-audio-mixing" not in names
    # The genre blueprint anchors the design, so it leads.
    assert names[0] == "godot-genre-platformer"


def test_single_pick_is_the_best_match():
    got = SC.select_skills("platformer jump physics", k=1, root=_FIX)
    assert [s.name for s in got] == ["godot-genre-platformer"]


def test_genre_and_physics_are_preferred_together():
    # Even a pure-physics prompt keeps a physics/architecture skill in the mix.
    names = [s.name for s in SC.select_skills("collision rigidbody raycast", k=2, root=_FIX)]
    assert "godot-2d-physics" in names


# --------------------------------------------------------------------------- #
# Rendering: attribution + graceful absence
# --------------------------------------------------------------------------- #
def test_render_carries_attribution_and_bodies():
    block = SC.render_skill_context("platformer jump physics", k=2, root=_FIX)
    assert block
    assert "gd-agentic-skills" in block
    assert "LGPLv3" in block
    assert "pinned e9e20ff" in block
    assert "paraphrase, do not copy verbatim" in block
    # A selected skill's body content is present.
    assert "coyote time" in block or "Precision movement" in block


def test_absent_library_renders_empty_and_selects_nothing():
    assert SC.select_skills("platformer jump", k=3, root=_ABSENT) == []
    assert SC.render_skill_context("platformer jump", k=3, root=_ABSENT) == ""


def test_no_match_renders_empty():
    # A prompt sharing no lexical signal with any fixture skill -> no injection.
    assert SC.select_skills("xyzzy quux frobnicate", k=3, root=_FIX) == []
    assert SC.render_skill_context("xyzzy quux frobnicate", k=3, root=_FIX) == ""


# --------------------------------------------------------------------------- #
# Rendering: token budget
# --------------------------------------------------------------------------- #
def test_token_budget_respected_and_oversized_skill_truncated():
    budget = 60
    block = SC.render_skill_context("megamechanic", k=1, max_tokens=budget, root=_FIX)
    assert block
    # The oversized fixture body is far bigger than the budget, so it is truncated.
    assert "[truncated]" in block
    assert SC.estimate_tokens(block) <= budget
    # The attribution header always survives.
    assert "gd-agentic-skills" in block


def test_larger_budget_keeps_more_of_the_body():
    small = SC.render_skill_context("megamechanic", k=1, max_tokens=60, root=_FIX)
    large = SC.render_skill_context("megamechanic", k=1, max_tokens=400, root=_FIX)
    assert len(large) > len(small)


# --------------------------------------------------------------------------- #
# gamegen wiring: contract-only helper
# --------------------------------------------------------------------------- #
def test_gdscript_contract_is_minimal_and_contract_only():
    contract = P.gdscript_contract()
    assert "GameAPI" in contract
    assert "reset(seed" in contract
    # Contract only: it must NOT carry the advisory-knowledge framing itself.
    assert "Reference knowledge (advisory)" not in contract


def test_gdscript_system_prompt_injects_advisory_section_with_attribution(monkeypatch):
    monkeypatch.setenv("GD_AGENTIC_SKILLS_DIR", _FIX)
    sysp = GG._gdscript_system_prompt("platformer jump physics")
    # Contract first (binding), then the clearly delimited advisory section.
    assert sysp.startswith(P.gdscript_contract())
    assert "## Reference knowledge (advisory)" in sysp
    assert "the CONTRACT above is binding, this is not" in sysp
    assert "gd-agentic-skills, LGPLv3, pinned e9e20ff" in sysp


def test_gdscript_system_prompt_degrades_to_contract_only_when_absent(monkeypatch):
    monkeypatch.setenv("GD_AGENTIC_SKILLS_DIR", _ABSENT)
    sysp = GG._gdscript_system_prompt("platformer jump physics")
    assert sysp == P.gdscript_contract()
    assert "Reference knowledge (advisory)" not in sysp


# --------------------------------------------------------------------------- #
# gamegen wiring: end-to-end injection + ledger record
# --------------------------------------------------------------------------- #
_GD_GAME = ("extends Node\n"
            "const ACTIONS = [\"left\", \"right\"]\n"
            "func init(): pass\n"
            "func reset(seed): return {}\n"
            "func act(action, n_ticks): return {}\n")


def _fake_anthropic(monkeypatch):
    # Make the anthropic backend usable without the real SDK: _make_client and
    # _llm_complete are stubbed, so the exception tuples are never evaluated.
    if GG.anthropic is None:
        monkeypatch.setattr(GG, "anthropic", types.SimpleNamespace(
            AuthenticationError=Exception, APIConnectionError=Exception,
            AnthropicError=Exception))
    monkeypatch.setattr(GG, "_make_client", lambda: object())


def _install_gameverify_pass(monkeypatch):
    import sys
    mod = types.ModuleType("harness.verify.gameverify")
    mod.verify_game = lambda p: {"passed": True, "failure_class": None,
                                 "hint": "", "witness": {}}
    monkeypatch.setitem(sys.modules, "harness.verify.gameverify", mod)


def test_generate_gdscript_injects_skills_and_records_them(tmp_path, monkeypatch):
    monkeypatch.setenv("GD_AGENTIC_SKILLS_DIR", _FIX)
    monkeypatch.setattr(GG, "_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    _fake_anthropic(monkeypatch)
    _install_gameverify_pass(monkeypatch)

    seen = {"systems": []}

    def fake_complete(client, system, messages):
        seen["systems"].append(system)
        return "DESIGN\nTheme: hop\n```gdscript\n" + _GD_GAME + "```\n"

    monkeypatch.setattr(GG, "_llm_complete", fake_complete)

    res = GG.generate_game("a platformer where you jump across pits",
                           out_dir=str(tmp_path / "out"), backend="anthropic",
                           engine="gdscript", max_repairs=2)

    assert res["verdict"] == "COMPLETED"
    assert res["engine"] == "gdscript"
    # The composed system prompt carried the CONTRACT + the attributed advisory
    # knowledge section (injection actually reached the model).
    sysp = seen["systems"][0]
    assert "GameAPI" in sysp
    assert "## Reference knowledge (advisory)" in sysp
    assert "gd-agentic-skills, LGPLv3, pinned e9e20ff" in sysp
    assert "godot-genre-platformer" in sysp
    # The ledger records which skills were injected.
    skills = res["pipeline"]["skills"]
    assert "godot-genre-platformer" in skills
    ledger = json.loads((tmp_path / "ledger.jsonl").read_text().strip().splitlines()[0])
    assert ledger["pipeline"]["skills"] == skills


def test_generate_gdscript_still_works_when_library_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("GD_AGENTIC_SKILLS_DIR", _ABSENT)
    monkeypatch.setattr(GG, "_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    _fake_anthropic(monkeypatch)
    _install_gameverify_pass(monkeypatch)

    seen = {"systems": []}

    def fake_complete(client, system, messages):
        seen["systems"].append(system)
        return "DESIGN\nTheme: hop\n```gdscript\n" + _GD_GAME + "```\n"

    monkeypatch.setattr(GG, "_llm_complete", fake_complete)

    res = GG.generate_game("a platformer where you jump across pits",
                           out_dir=str(tmp_path / "out"), backend="anthropic",
                           engine="gdscript", max_repairs=2)

    assert res["verdict"] == "COMPLETED"
    # No library -> the prompt is the contract alone, and nothing is recorded.
    assert "Reference knowledge (advisory)" not in seen["systems"][0]
    assert res["pipeline"]["skills"] == []


# --------------------------------------------------------------------------- #
# The REAL sibling library (skipped when it is not present)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(SC.library_root() is None,
                    reason="real gd-agentic-skills sibling library not present")
def test_real_library_loads_and_renders():
    index = SC.load_index()
    assert len(index) > 50                       # the real library has ~96 skills
    block = SC.render_skill_context(
        "a top-down game where you herd sheep into a pen", k=2)
    assert block
    assert "gd-agentic-skills, LGPLv3, pinned e9e20ff" in block
    # It named at least one real, plausible skill.
    names = [s.name for s in SC.select_skills(
        "a top-down game where you herd sheep into a pen", k=2)]
    assert names and all(n.startswith("godot-") for n in names)
