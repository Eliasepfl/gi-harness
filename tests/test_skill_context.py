"""Tests for harness.gen.skill_context — deterministic gd-agentic skill retrieval
and its injection into the gdscript generation prompt.

Pure-python, no network, no LLM. Every unit test runs against a tiny FIXTURE
skills library under tests/fixtures/gd_skills/ (2-3 fake SKILL.md + a fake
index), so CI never needs the real 19 MB clone. One test exercises the REAL
sibling library, skipped when it is not present.
"""
from __future__ import annotations

import json
import logging
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
    a = SC.select_skills("platformer jump physics", k=3, root=_FIX, use_llm=False)
    b = SC.select_skills("platformer jump physics", k=3, root=_FIX, use_llm=False)
    assert [s.name for s in a] == [s.name for s in b]
    # Same objects field-for-field (names, descriptions, bodies all reproduce).
    assert a == b
    # And the rendered block is byte-identical across calls.
    assert (SC.render_skill_context("platformer jump physics", k=3, root=_FIX, use_llm=False)
            == SC.render_skill_context("platformer jump physics", k=3, root=_FIX, use_llm=False))


# --------------------------------------------------------------------------- #
# Selection: relevance
# --------------------------------------------------------------------------- #
def test_platformer_prompt_ranks_relevant_skill_above_unrelated():
    names = [s.name for s in SC.select_skills("platformer jump physics", k=2, root=_FIX, use_llm=False)]
    # The platformer/physics skills are selected; the unrelated audio skill is not.
    assert "godot-genre-platformer" in names
    assert "godot-2d-physics" in names
    assert "godot-audio-mixing" not in names
    # The genre blueprint anchors the design, so it leads.
    assert names[0] == "godot-genre-platformer"


def test_single_pick_is_the_best_match():
    got = SC.select_skills("platformer jump physics", k=1, root=_FIX, use_llm=False)
    assert [s.name for s in got] == ["godot-genre-platformer"]


def test_genre_and_physics_are_preferred_together():
    # Even a pure-physics prompt keeps a physics/architecture skill in the mix.
    names = [s.name for s in SC.select_skills("collision rigidbody raycast", k=2, root=_FIX, use_llm=False)]
    assert "godot-2d-physics" in names


# --------------------------------------------------------------------------- #
# Rendering: attribution + graceful absence
# --------------------------------------------------------------------------- #
def test_render_carries_attribution_and_bodies():
    block = SC.render_skill_context("platformer jump physics", k=2, root=_FIX, use_llm=False)
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
    assert SC.select_skills("xyzzy quux frobnicate", k=3, root=_FIX, use_llm=False) == []
    assert SC.render_skill_context("xyzzy quux frobnicate", k=3, root=_FIX, use_llm=False) == ""


# --------------------------------------------------------------------------- #
# Rendering: token budget
# --------------------------------------------------------------------------- #
def test_token_budget_respected_and_oversized_skill_truncated():
    budget = 60
    block = SC.render_skill_context("megamechanic", k=1, max_tokens=budget, root=_FIX, use_llm=False)
    assert block
    # The oversized fixture body is far bigger than the budget, so it is truncated.
    assert "[truncated]" in block
    assert SC.estimate_tokens(block) <= budget
    # The attribution header always survives.
    assert "gd-agentic-skills" in block


def test_larger_budget_keeps_more_of_the_body():
    small = SC.render_skill_context("megamechanic", k=1, max_tokens=60, root=_FIX, use_llm=False)
    large = SC.render_skill_context("megamechanic", k=1, max_tokens=400, root=_FIX, use_llm=False)
    assert len(large) > len(small)


# --------------------------------------------------------------------------- #
# gamegen wiring: contract-only helper
# --------------------------------------------------------------------------- #
def test_gdscript_contract_is_minimal_and_contract_only():
    contract = P.gdscript_contract()
    assert "build(" in contract and "def act" in contract or "act(" in contract  # method convention, not a base class
    assert "state(" in contract and "checkpoints(" in contract  # the typed-state read methods
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
    assert "build(" in sysp and ("act(" in sysp or "def act" in sysp)  # contract method convention
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


# =========================================================================== #
# TASK A.1 — load_index reconciliation against DISK TRUTH (loader robustness)
# =========================================================================== #
def _write_skill(root, name, description="body-desc", body="## Body\nreal content here.\n"):
    """Create skills/<name>/SKILL.md with a YAML frontmatter description under root."""
    d = os.path.join(root, "skills", name)
    os.makedirs(d, exist_ok=True)
    fm = f'---\nname: {name}\ndescription: "{description}"\n---\n\n{body}'
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write(fm)


def _make_lib(root, index_entries):
    """Write a fabricated library: skills_index.json = index_entries (bodies are
    created separately via _write_skill so an index entry can be a body-less ghost)."""
    with open(os.path.join(root, "skills_index.json"), "w", encoding="utf-8") as fh:
        json.dump(index_entries, fh)


def test_load_index_reconciles_dedup_supplement_and_ghost(tmp_path):
    root = str(tmp_path / "lib")
    os.makedirs(root)
    # index lists: a normal skill, the SAME name twice (exact duplicate), and an
    # index-only GHOST whose body file does not exist on disk.
    _make_lib(root, [
        {"name": "godot-real-one", "description": "one", "keywords": ["alpha"]},
        {"name": "godot-dup", "description": "dup", "keywords": ["beta"]},
        {"name": "godot-dup", "description": "dup", "keywords": ["beta"]},   # (b) duplicate
        {"name": "godot-ghost", "description": "ghost", "keywords": ["gamma"]},  # (c) no body
    ])
    _write_skill(root, "godot-real-one")
    _write_skill(root, "godot-dup")
    # (a) a DISK-ONLY skill: has a SKILL.md but is absent from the index entirely.
    _write_skill(root, "godot-disk-only",
                 description="Expert disk-only pathfinding blueprint for delta AI.")
    # godot-ghost has NO dir/body on disk.

    index = SC.load_index(root)
    names = [e["name"] for e in index]

    # (b) duplicate collapsed to a single entry
    assert names.count("godot-dup") == 1
    # (c) index-only ghost (no body file) skipped -> unroutable phantom removed
    assert "godot-ghost" not in names
    # (a) disk-only skill supplemented into the routable index, description from its
    # SKILL.md frontmatter
    assert "godot-disk-only" in names
    disk = next(e for e in index if e["name"] == "godot-disk-only")
    assert "pathfinding" in disk["description"]
    # and it is genuinely routable: BM25 can pick it from its own frontmatter terms
    picked = [s.name for s in SC.select_skills("delta pathfinding blueprint", k=3,
                                               root=root, use_llm=False)]
    assert "godot-disk-only" in picked
    # every reconciled entry resolves to a real body file (no dead references)
    for e in index:
        assert SC._skill_body(root, e["name"])


def _find_real_lib():
    cand = os.environ.get("GD_AGENTIC_SKILLS_DIR")
    if cand and SC.library_root(cand):
        return SC.library_root(cand)
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
        c = os.path.join(here, "gd-agentic-skills")
        if os.path.isfile(os.path.join(c, "skills_index.json")):
            return c
    return None


@pytest.mark.skipif(_find_real_lib() is None,
                    reason="real gd-agentic-skills library not locatable")
def test_real_library_recovers_unindexed_ai_navigation():
    # The real library's index forgets 'godot-ai-navigation' (its dir + SKILL.md
    # fully exist); the reconciled loader must recover it as a routable entry, and
    # collapse the duplicated 'godot-navigation-pathfinding' to a single entry.
    real = _find_real_lib()
    index = SC.load_index(real)
    names = [e["name"] for e in index]
    assert "godot-ai-navigation" in names
    assert names.count("godot-navigation-pathfinding") == 1
    assert len(names) == len(set(names))           # no duplicate names survive
    assert SC._skill_body(real, "godot-ai-navigation")   # body resolves


# =========================================================================== #
# TASK A.2 — the LLM-route pick ceiling (raised 10 -> 14, binding is observable)
# =========================================================================== #
def test_llm_route_ceiling_is_14():
    # A regression pin: the diagnosis found 10 truncated rich prompts; 14 clears the
    # observed 8-10 structural + genre/physics/camera/controls demand.
    assert SC._LLM_ROUTE_CEILING == 14


def test_llm_route_returns_up_to_ceiling_and_logs_when_binding(monkeypatch, caplog):
    # A model that names MANY relevant skills: the ceiling caps the return AND the
    # binding is logged (never a silent truncation).
    index = [{"name": f"godot-skill-{i:02d}", "description": f"desc {i}"}
             for i in range(20)]
    monkeypatch.setattr(GG, "_openrouter_complete",
                        lambda system, messages: "\n".join(e["name"] for e in index))
    with caplog.at_level(logging.INFO, logger="harness.gen.skill_context"):
        picked = SC._llm_route("a rich structural prompt", index, SC._LLM_ROUTE_CEILING)
    assert picked == [e["name"] for e in index][:14]     # exactly the ceiling, in order
    assert any("ceiling" in r.getMessage().lower() for r in caplog.records)


def test_llm_route_no_log_when_under_ceiling(monkeypatch, caplog):
    index = [{"name": f"godot-skill-{i:02d}", "description": f"desc {i}"}
             for i in range(6)]
    monkeypatch.setattr(GG, "_openrouter_complete",
                        lambda system, messages: "\n".join(e["name"] for e in index[:5]))
    with caplog.at_level(logging.INFO, logger="harness.gen.skill_context"):
        picked = SC._llm_route("a prompt", index, SC._LLM_ROUTE_CEILING)
    assert len(picked) == 5
    assert not any("ceiling" in r.getMessage().lower() for r in caplog.records)


# =========================================================================== #
# TASK A.3 — BM25 fallback degradation is OBSERVABLE (logged + returned reason)
# =========================================================================== #
def test_bm25_intended_offline_path_is_not_degraded():
    # use_llm=False is the DELIBERATE offline path; a healthy match is NOT a degradation.
    sk = SC.select_skills("platformer jump physics", k=3, root=_FIX, use_llm=False)
    assert sk
    diag = SC.last_route_diagnosis()
    assert diag["route"] == "bm25"
    assert diag["reason"] == SC.ROUTE_BM25
    assert diag["degraded"] is False


def test_silent_llm_to_bm25_fallback_is_flagged(monkeypatch, caplog):
    # use_llm=True but the LLM router yields nothing -> we fall back to BM25. This is
    # the "silent degradation" the audit flagged; it must now be logged + returned.
    monkeypatch.setattr(SC, "_llm_route", lambda prompt, index, k: [])
    with caplog.at_level(logging.WARNING, logger="harness.gen.skill_context"):
        sk = SC.select_skills("platformer jump physics", k=3, root=_FIX, use_llm=True)
    assert sk                                        # BM25 still produced picks
    diag = SC.last_route_diagnosis()
    assert diag["degraded"] is True
    assert diag["reason"] in (SC.ROUTE_BM25_FALLBACK, SC.ROUTE_BM25_WEAK)
    assert diag["route"] == "bm25"
    assert any("DEGRADED" in r.getMessage() for r in caplog.records)


def test_bm25_empty_match_is_flagged(caplog):
    # A prompt with no lexical overlap -> BM25 matches nothing -> observable empty route.
    with caplog.at_level(logging.WARNING, logger="harness.gen.skill_context"):
        sk = SC.select_skills("xyzzy quux frobnicate zzz", k=3, root=_FIX, use_llm=False)
    assert sk == []
    diag = SC.last_route_diagnosis()
    assert diag["reason"] == SC.ROUTE_BM25_EMPTY
    assert diag["degraded"] is True
    assert any("DEGRADED" in r.getMessage() for r in caplog.records)


def test_llm_success_route_is_healthy(monkeypatch):
    # When the LLM route succeeds, the recorded route is the healthy primary path.
    monkeypatch.setattr(SC, "_llm_route",
                        lambda prompt, index, k: ["godot-genre-platformer"])
    sk = SC.select_skills("platformer jump physics", k=3, root=_FIX, use_llm=True)
    assert [s.name for s in sk] == ["godot-genre-platformer"]
    diag = SC.last_route_diagnosis()
    assert diag["reason"] == SC.ROUTE_LLM
    assert diag["degraded"] is False
