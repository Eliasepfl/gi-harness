"""P0 gate tests for the two designer-cage integrity extensions (§4 P0).

1. The designer prompt seed (SYSTEM.md + skills/*.md + memory/*.md) is tracked
   base, so a mid-run mutation surfaces as a violation => verdict INVALIDATED.
2. The parts-bank content hash is folded into snapshot() under bank:<version>,
   so a mid-run bank swap is caught (but a cosmetic reformat is not).

Synthetic trees in tmp_path; the existing tests/test_integrity.py still guards
the base harness/**/*.py + CONTRACTS.md manifest unchanged.
"""
from __future__ import annotations

import pytest

import json
import os
import re

from harness.core import bank as BANK
from harness.core import integrity as INT

# Repo root = grandparent of this test file (tests/ sits at the root).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SKILLS_DIR = os.path.join(_REPO_ROOT, "designer", "skills")

# The frontmatter keys every reseeded skill must carry (DESIGNER_AGENT_PLAN.md §4
# artifact frontmatter + the HUB-routed `load_when` line).
_REQUIRED_FM_KEYS = {
    "id", "kind", "created_by", "run_id", "wave", "created_ts",
    "parent", "status", "load_when", "rationale", "provenance",
}
_KNOWN_KINDS = {"hub", "reference", "archetype"}


def _skill_files() -> list[str]:
    """Basenames of every ``*.md`` in the real designer/skills library."""
    return sorted(n for n in os.listdir(_SKILLS_DIR) if n.endswith(".md"))


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse the leading ``---`` fenced ``key: value`` block (flat, one per line).

    Values may themselves contain ``:`` (timestamps, provenance paths), so split on
    the FIRST colon only. Returns {} when no well-formed block is present.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            key, _, val = line.partition(":")
            out[key.strip()] = val.strip()
    return out


def _designer_tree(root):
    d = root / "designer"
    (d / "skills").mkdir(parents=True)
    (d / "memory").mkdir(parents=True)
    (d / "SYSTEM.md").write_text("SEED PROMPT\n", encoding="utf-8")
    (d / "skills" / "traps.md").write_text("trap craft\n", encoding="utf-8")
    (d / "skills" / "INDEX.md").write_text("router\n", encoding="utf-8")
    (d / "memory" / "LESSONS.md").write_text("lesson 1\n", encoding="utf-8")
    (root / "harness").mkdir()
    (root / "harness" / "a.py").write_text("A\n", encoding="utf-8")
    (root / "CONTRACTS.md").write_text("SPEC\n", encoding="utf-8")


def _bank_tree(root, data):
    p = root / "banks" / "parts" / "v1"
    p.mkdir(parents=True)
    (p / "parts.json").write_text(json.dumps(data), encoding="utf-8")


_DATA = {"schema_version": "1.0", "bank_version": "1.0.0",
         "parts": [{"name": "ground", "category": "terrain"}]}


# --------------------------------------------------------------------------- #
# Designer seed tracking
# --------------------------------------------------------------------------- #
def test_designer_seed_is_tracked(tmp_path):
    _designer_tree(tmp_path)
    snap = INT.snapshot(str(tmp_path))
    assert "designer/SYSTEM.md" in snap
    assert "designer/skills/traps.md" in snap
    assert "designer/skills/INDEX.md" in snap
    assert "designer/memory/LESSONS.md" in snap
    # Base harness + contracts still tracked alongside.
    assert "harness/a.py" in snap and "CONTRACTS.md" in snap


def test_midrun_skill_mutation_forces_invalidated(tmp_path):
    _designer_tree(tmp_path)
    before = INT.snapshot(str(tmp_path))                     # freeze (gamegen:901)
    (tmp_path / "designer" / "skills" / "traps.md").write_text(
        "MUTATED MID-RUN\n", encoding="utf-8")
    violated = INT.violations(before, str(tmp_path))         # gamegen:923
    assert "designer/skills/traps.md" in violated
    # This is exactly the branch gamegen turns into an INVALIDATED verdict.
    verdict = "INVALIDATED" if violated else "COMPLETED"
    assert verdict == "INVALIDATED"


def test_midrun_memory_mutation_is_a_violation(tmp_path):
    _designer_tree(tmp_path)
    before = INT.snapshot(str(tmp_path))
    (tmp_path / "designer" / "memory" / "LESSONS.md").write_text(
        "lesson 1\nlesson 2\n", encoding="utf-8")
    assert "designer/memory/LESSONS.md" in INT.violations(before, str(tmp_path))


# --------------------------------------------------------------------------- #
# Bank content-hash fold
# --------------------------------------------------------------------------- #
def test_bank_content_hash_present_in_snapshot(tmp_path):
    _designer_tree(tmp_path)
    _bank_tree(tmp_path, _DATA)
    snap = INT.snapshot(str(tmp_path))
    assert "bank:v1" in snap
    assert snap["bank:v1"] == BANK.content_hash(_DATA)


def test_midrun_bank_change_is_a_violation(tmp_path):
    _designer_tree(tmp_path)
    _bank_tree(tmp_path, _DATA)
    before = INT.snapshot(str(tmp_path))
    changed = dict(_DATA)
    changed["parts"] = _DATA["parts"] + [{"name": "wall", "category": "terrain"}]
    (tmp_path / "banks" / "parts" / "v1" / "parts.json").write_text(
        json.dumps(changed), encoding="utf-8")
    assert "bank:v1" in INT.violations(before, str(tmp_path))


def test_bank_cosmetic_reformat_is_not_a_violation(tmp_path):
    """The canonical-JSON hash ignores whitespace/key-order — only semantics."""
    _designer_tree(tmp_path)
    _bank_tree(tmp_path, _DATA)
    before = INT.snapshot(str(tmp_path))
    # Same semantic content, different serialization (indented, sorted).
    (tmp_path / "banks" / "parts" / "v1" / "parts.json").write_text(
        json.dumps(_DATA, indent=4, sort_keys=True) + "\n", encoding="utf-8")
    assert "bank:v1" not in INT.violations(before, str(tmp_path))


def test_no_bank_dir_adds_no_bank_key(tmp_path):
    _designer_tree(tmp_path)  # no banks/ created
    snap = INT.snapshot(str(tmp_path))
    assert not any(k.startswith("bank:") for k in snap)


# --------------------------------------------------------------------------- #
# Real repo sanity
# --------------------------------------------------------------------------- #
@pytest.mark.skip(reason="designer content purged with the GDScript pivot (Elias, 2026-07-15); mechanism tests above still guard the cage")
def test_real_repo_snapshot_has_bank_and_designer_seed():
    # The wave-0 seed skills were deleted (they codified the pre-pivot perimeter,
    # commit 09d94a4); the reseed (TRACK RESEED, wave-1) has since landed, so the
    # RESTORED assertion below re-pins that the real repo's designer/skills/ is
    # tracked base — a mid-run mutation of any reseeded skill INVALIDATES a run,
    # exactly as the fixture-tree tests above prove for the mechanism.
    root = INT.__file__.rsplit("harness", 1)[0].rstrip("\\/")
    snap = INT.snapshot(root)
    assert "bank:v1" in snap
    # RESTORED (retired when the wave-0 seeds were deleted): the reseeded skills
    # are in the tracked-base snapshot, and the HUB orchestrator specifically.
    assert any(k.startswith("designer/skills/") for k in snap)
    assert "designer/skills/HUB.md" in snap


# --------------------------------------------------------------------------- #
# Reseeded skills library — HUB routing + frontmatter integrity (TRACK RESEED)
# --------------------------------------------------------------------------- #
@pytest.mark.skip(reason="designer content purged with the GDScript pivot (Elias, 2026-07-15); mechanism tests above still guard the cage")
def test_reseeded_library_present_and_hub_exists():
    """The wave-1 reseed landed: HUB.md + a spread of routed skills exist."""
    files = _skill_files()
    assert "HUB.md" in files, "the orchestrator hub is missing"
    assert "INDEX.md" not in files, "HUB.md replaces the retired INDEX.md router"
    # HUB + the reference skills + 6-10 archetype cards → a real library.
    assert len(files) >= 8


@pytest.mark.skip(reason="designer content purged with the GDScript pivot (Elias, 2026-07-15); mechanism tests above still guard the cage")
def test_hub_routes_have_no_dangling_targets():
    """Every skill the HUB routes to must exist — no dangling routes."""
    hub = os.path.join(_SKILLS_DIR, "HUB.md")
    with open(hub, "r", encoding="utf-8") as fh:
        body = fh.read()
    present = set(_skill_files())
    # Skills are referenced in the HUB as inline-code filenames, e.g. `steer-to-pose.md`.
    referenced = {m for m in re.findall(r"`([a-z0-9-]+\.md)`", body)}
    assert referenced, "the HUB references no skills — routing is empty"
    dangling = sorted(r for r in referenced if r not in present)
    assert not dangling, f"HUB routes to non-existent skills: {dangling}"


@pytest.mark.skip(reason="designer content purged with the GDScript pivot (Elias, 2026-07-15); mechanism tests above still guard the cage")
def test_every_skill_is_routed_by_hub_no_orphans():
    """No orphan skills: every non-HUB skill file is reachable from the HUB."""
    hub = os.path.join(_SKILLS_DIR, "HUB.md")
    with open(hub, "r", encoding="utf-8") as fh:
        body = fh.read()
    referenced = {m for m in re.findall(r"`([a-z0-9-]+\.md)`", body)}
    orphans = sorted(
        n for n in _skill_files() if n != "HUB.md" and n not in referenced
    )
    assert not orphans, f"skills exist but the HUB never routes to them: {orphans}"


@pytest.mark.skip(reason="designer content purged with the GDScript pivot (Elias, 2026-07-15); mechanism tests above still guard the cage")
def test_at_least_six_archetype_cards():
    """The library ships 6-10 archetype cards (the differentiator families)."""
    archetypes = []
    for name in _skill_files():
        with open(os.path.join(_SKILLS_DIR, name), "r", encoding="utf-8") as fh:
            fm = _parse_frontmatter(fh.read())
        if fm.get("kind") == "archetype":
            archetypes.append(name)
    assert 6 <= len(archetypes) <= 10, f"expected 6-10 archetype cards, got {archetypes}"


@pytest.mark.skip(reason="designer content purged with the GDScript pivot (Elias, 2026-07-15); mechanism tests above still guard the cage")
def test_every_skill_has_valid_frontmatter():
    """Frontmatter validation for ALL skill files (DESIGNER_AGENT_PLAN.md §4)."""
    for name in _skill_files():
        path = os.path.join(_SKILLS_DIR, name)
        with open(path, "r", encoding="utf-8") as fh:
            fm = _parse_frontmatter(fh.read())
        assert fm, f"{name}: missing/malformed frontmatter block"
        missing = sorted(_REQUIRED_FM_KEYS - fm.keys())
        assert not missing, f"{name}: frontmatter missing keys {missing}"
        # Every reseeded skill is an active, human-seeded wave-1 artifact with a
        # non-empty provenance and a load_when line the HUB can route on.
        assert fm["status"] == "active", f"{name}: status must be active"
        assert fm["created_by"].startswith("human-seed"), \
            f"{name}: created_by must be the human-seed attribution"
        assert fm["kind"] in _KNOWN_KINDS, f"{name}: unknown kind {fm['kind']!r}"
        assert fm["load_when"], f"{name}: load_when must be non-empty (HUB routes on it)"
        assert fm["provenance"], f"{name}: provenance must be non-empty"


@pytest.mark.skip(reason="designer content purged with the GDScript pivot (Elias, 2026-07-15); mechanism tests above still guard the cage")
def test_quarried_skills_attribute_the_lgpl_source():
    """No verbatim copying (LGPL): any skill mining the quarry must attribute it."""
    for name in _skill_files():
        path = os.path.join(_SKILLS_DIR, name)
        with open(path, "r", encoding="utf-8") as fh:
            fm = _parse_frontmatter(fh.read())
        prov = fm.get("provenance", "")
        if "gd-agentic-skills" in prov:
            assert "LGPL" in prov and "paraphrased" in prov, (
                f"{name}: quarried provenance must cite LGPL + 'paraphrased'")
