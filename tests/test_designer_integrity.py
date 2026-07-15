"""P0 gate tests for the two designer-cage integrity extensions (§4 P0).

1. The designer prompt seed (SYSTEM.md + skills/*.md + memory/*.md) is tracked
   base, so a mid-run mutation surfaces as a violation => verdict INVALIDATED.
2. The parts-bank content hash is folded into snapshot() under bank:<version>,
   so a mid-run bank swap is caught (but a cosmetic reformat is not).

Synthetic trees in tmp_path; the existing tests/test_integrity.py still guards
the base harness/**/*.py + CONTRACTS.md manifest unchanged.
"""
from __future__ import annotations

import json

from harness.core import bank as BANK
from harness.core import integrity as INT


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
def test_real_repo_snapshot_has_bank_and_designer_seed():
    root = INT.__file__.rsplit("harness", 1)[0].rstrip("\\/")
    snap = INT.snapshot(root)
    assert "bank:v1" in snap
    assert any(k.startswith("designer/skills/") for k in snap)
