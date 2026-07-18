"""Tests for harness.integrity — the run-integrity base-code manifest.

No network, no physics: everything runs over a synthetic file tree in tmp_path.
The tracked base is exactly ``harness/**/*.py`` + ``CONTRACTS.md``; ``scenes/``,
``tests/``, ``env.py`` and bytecode caches are out of scope (so generated games
and secrets may change freely without invalidating a run).
"""
from __future__ import annotations

from harness.core import integrity as INT


def _make_tree(root):
    """Build a minimal repo-shaped tree under `root`."""
    harness = root / "harness"
    (harness / "sub").mkdir(parents=True)
    (harness / "a.py").write_text("A", encoding="utf-8")
    (harness / "sub" / "b.py").write_text("B", encoding="utf-8")
    # A non-.py file inside harness/ is NOT tracked (only *.py).
    (harness / "notes.txt").write_text("notes", encoding="utf-8")
    # Bytecode cache: must be ignored.
    (harness / "__pycache__").mkdir()
    (harness / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\x00\x01")
    # Root CONTRACTS.md is tracked.
    (root / "CONTRACTS.md").write_text("CONTRACTS", encoding="utf-8")
    # Excluded areas.
    (root / "scenes" / "games").mkdir(parents=True)
    (root / "scenes" / "games" / "g.py").write_text("G", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "t.py").write_text("T", encoding="utf-8")
    (root / "env.py").write_text("SECRET = 1", encoding="utf-8")


def test_snapshot_tracks_only_base_files(tmp_path):
    _make_tree(tmp_path)
    snap = INT.snapshot(str(tmp_path))
    assert set(snap) == {"harness/a.py", "harness/sub/b.py", "CONTRACTS.md"}
    # Excluded: secrets, generated scenes, tests, bytecode, non-.py files.
    assert "env.py" not in snap
    assert not any(k.startswith("scenes/") for k in snap)
    assert not any(k.startswith("tests/") for k in snap)
    assert not any(k.endswith(".pyc") for k in snap)
    assert "harness/notes.txt" not in snap
    # Values are hex sha256 digests.
    for digest in snap.values():
        assert len(digest) == 64
        int(digest, 16)  # parses as hex


def test_violations_empty_when_unchanged(tmp_path):
    _make_tree(tmp_path)
    before = INT.snapshot(str(tmp_path))
    assert INT.violations(before, str(tmp_path)) == []


def test_violations_detect_modification(tmp_path):
    _make_tree(tmp_path)
    before = INT.snapshot(str(tmp_path))
    (tmp_path / "harness" / "a.py").write_text("MUTATED", encoding="utf-8")
    assert INT.violations(before, str(tmp_path)) == ["harness/a.py"]


def test_violations_detect_add_and_remove(tmp_path):
    _make_tree(tmp_path)
    before = INT.snapshot(str(tmp_path))
    (tmp_path / "harness" / "c.py").write_text("NEW", encoding="utf-8")   # added
    (tmp_path / "harness" / "a.py").unlink()                             # removed
    assert INT.violations(before, str(tmp_path)) == ["harness/a.py", "harness/c.py"]


def test_contracts_change_is_a_violation(tmp_path):
    _make_tree(tmp_path)
    before = INT.snapshot(str(tmp_path))
    (tmp_path / "CONTRACTS.md").write_text("edited spec", encoding="utf-8")
    assert INT.violations(before, str(tmp_path)) == ["CONTRACTS.md"]


def test_generated_and_secret_changes_do_not_violate(tmp_path):
    """The hard-rule guarantee: repairs to generated games and edits to env.py
    are allowed mid-run; only base code is frozen."""
    _make_tree(tmp_path)
    before = INT.snapshot(str(tmp_path))
    (tmp_path / "scenes" / "games" / "g.py").write_text("repaired game", encoding="utf-8")
    (tmp_path / "scenes" / "games" / "new.py").write_text("attempt 2", encoding="utf-8")
    (tmp_path / "env.py").write_text("SECRET = 'rotated'", encoding="utf-8")
    (tmp_path / "tests" / "t.py").write_text("changed test", encoding="utf-8")
    assert INT.violations(before, str(tmp_path)) == []


def test_bytecode_cache_churn_does_not_violate(tmp_path):
    _make_tree(tmp_path)
    before = INT.snapshot(str(tmp_path))
    # A freshly compiled .pyc must not register as a base-code change.
    (tmp_path / "harness" / "__pycache__" / "b.cpython-312.pyc").write_bytes(b"\x02")
    assert INT.violations(before, str(tmp_path)) == []


def test_snapshot_of_real_repo_includes_gamegen():
    """Sanity check against the real repo layout."""
    root = INT.__file__.rsplit("harness", 1)[0].rstrip("\\/")
    snap = INT.snapshot(root)
    assert "harness/gen/gamegen.py" in snap
    assert "harness/core/integrity.py" in snap
    assert "env.py" not in snap
