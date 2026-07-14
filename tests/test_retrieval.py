"""Tests for harness.gen.retrieval — deterministic Tier-1b parts retrieval.

No network, no physics: pure lexical BM25 over the pinned v1 bank. Covers the
canned prompt -> expected part mapping, the legend-only score-gate fallback,
determinism, and the per-engine menu rendering (py world.part usage vs js
world.add presets + the naming rule).
"""
from __future__ import annotations

import pytest

from harness.gen import retrieval as R
from harness.core import bank as BANK


# Five canned prompts, each expected to surface a specific bank part in its menu.
# The "volcano" -> lava_pool case exercises the query-synonym bridge (the prompt
# shares no literal token with lava_pool's name/tags/summary).
_CANNED = [
    ("volcano", "lava_pool"),
    ("swing a wrecking ball to smash a tower", "wrecking_ball"),
    ("push a crate onto a pressure plate to open the goal", "pressure_zone"),
    ("guide a puck across the ice onto the goal pad", "puck"),
    ("balance on a seesaw over a spike pit", "spike_pit"),
]

# Off-theme prompts: no confident lexical match -> legend-only fallback.
_LEGEND_ONLY = [
    "a game about abstract colours and rhythmic music",
    "steer a raft to the dock",
]


@pytest.mark.parametrize("prompt,expected", _CANNED)
def test_canned_prompts_retrieve_expected_part(prompt, expected):
    names = R.retrieve(prompt)
    assert expected in names, (prompt, expected, names)
    # A themed subset, never the whole catalog.
    assert 1 <= len(names) <= R.K_MAX


@pytest.mark.parametrize("prompt,expected", _CANNED)
def test_canned_prompts_expected_part_ranks_high(prompt, expected):
    ranked = R.score(prompt)
    top3 = [n for n, _ in ranked[:3]]
    # The expected part is one of the strongest lexical matches for its prompt...
    assert expected in top3, (prompt, ranked[:3])
    # ...well above the legend-only score gate.
    by_name = dict(ranked)
    assert by_name[expected] >= R.SCORE_THRESHOLD


@pytest.mark.parametrize("prompt", _LEGEND_ONLY)
def test_offtheme_prompts_fall_back_to_legend_only(prompt):
    assert R.retrieve(prompt) == []
    menu_text, mode, names = R.retrieve_menu(prompt)
    assert mode == "legend_only"
    assert menu_text is None
    assert names == []


def test_retrieval_is_deterministic():
    prompt = _CANNED[2][0]
    assert R.score(prompt) == R.score(prompt)
    assert R.retrieve(prompt) == R.retrieve(prompt)
    assert R.retrieve_menu(prompt, "py") == R.retrieve_menu(prompt, "py")


def test_score_covers_whole_bank_and_sorts_desc():
    ranked = R.score("crate")
    names = [n for n, _ in ranked]
    assert set(names) == set(BANK.load_bank("v1").parts)      # every part scored
    scores = [s for _, s in ranked]
    assert scores == sorted(scores, reverse=True)             # descending
    # ties break on name (deterministic, stable).
    for i in range(len(ranked) - 1):
        a, b = ranked[i], ranked[i + 1]
        if a[1] == b[1]:
            assert a[0] <= b[0]


# --- Menu rendering -----------------------------------------------------------

def test_py_menu_uses_world_part_and_is_advisory():
    menu_text, mode, names = R.retrieve_menu(
        "push a crate onto a pressure plate to open the goal", "py")
    assert mode == "menu" and names
    assert "world.part(" in menu_text
    # Each retrieved part appears with its category and override ranges.
    assert "pressure_zone (trigger)" in menu_text
    assert "overrides:" in menu_text
    assert "bodies:" in menu_text
    # Advisory framing (menu optional; world.add remains available).
    assert "not a requirement" in menu_text
    assert "world.add" in menu_text


def test_js_menu_inlines_presets_and_states_naming_rule():
    menu_text, mode, names = R.retrieve_menu(
        "guide a puck across the ice onto the goal pad", "js")
    assert mode == "menu" and "puck" in names
    # No world.part CALL in JS: the menu inlines the canonical world.add preset
    # instead (the usage line may mention world.part() to explain its absence).
    assert 'world.part("' not in menu_text
    assert 'world.add("puck", "circle"' in menu_text
    # ...with the calibrated physics params from the bank JSON...
    assert "friction: 0.2" in menu_text and "elasticity: 0.6" in menu_text
    # ...and THE NAMING RULE (bind sprites by the exact part name).
    assert "renderer binds sprites by name" in menu_text


def test_build_menu_empty_is_empty_string():
    assert R.build_menu([], "py") == ""
    assert R.build_menu(None, "js") == ""


def test_multibody_part_renders_all_bodies_note_in_js():
    # wrecking_ball is a 2-body subassembly; the js line must flag the extra body.
    menu_text = R.build_menu(["wrecking_ball"], "js")
    assert 'world.add("wrecking_ball"' in menu_text
    assert "more body/joint" in menu_text


def test_retrieve_menu_names_are_pinned_subset_of_bank():
    _, _, names = R.retrieve_menu("swing a wrecking ball to smash a tower", "py")
    bank_names = set(BANK.load_bank("v1").parts)
    assert names and set(names) <= bank_names
