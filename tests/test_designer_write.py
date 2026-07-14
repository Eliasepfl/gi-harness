"""P0 gate tests for the designer cage's SOLE write path (DESIGNER_AGENT_PLAN §4).

Covers the section-5 gates: path-traversal / symlink / Tier-C rejection,
append-only enforcement, budget caps (oversize skill rejected), the
flag-off => no-write-path kill-switch, and ledger lines for BOTH accept and
reject. Everything runs over a synthetic repo tree in tmp_path; no network.
"""
from __future__ import annotations

import json
import os

import pytest

from harness.designer import write as W


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A minimal repo tree with the designer scaffold + a BUDGETS.md."""
    (tmp_path / "designer" / "skills").mkdir(parents=True)
    (tmp_path / "designer" / "memory").mkdir(parents=True)
    (tmp_path / "harness" / "gen" / "prompts").mkdir(parents=True)
    (tmp_path / "harness" / "gen" / "prompts" / "rules.md").write_text(
        "LIVE RULES\n", encoding="utf-8")
    (tmp_path / "harness" / "core").mkdir(parents=True)
    (tmp_path / "harness" / "core" / "integrity.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "CONTRACTS.md").write_text("SPEC\n", encoding="utf-8")
    (tmp_path / "designer" / "BUDGETS.md").write_text(
        "```budgets\nskill_max_lines = 200\nskill_max_tokens = 1500\n"
        "skills_max_active = 25\nprompt_max_lines = 120\n```\n", encoding="utf-8")
    # Enable the write path and point the ledger inside the tree.
    monkeypatch.setenv("DESIGNER_WRITE_ENABLED", "1")
    monkeypatch.setenv("DESIGNER_WAVE", "wave-7")
    monkeypatch.delenv("DESIGNER_LEDGER", raising=False)
    monkeypatch.delenv("DESIGNER_BUDGETS", raising=False)
    return tmp_path


def _ledger(root):
    path = os.path.join(root, "designer", "ledger", "designer.jsonl")
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# --------------------------------------------------------------------------- #
# Happy path + ledger
# --------------------------------------------------------------------------- #
def test_tier_a_skill_write_accepted_and_logged(repo):
    res = W.designer_write("designer/skills/traps.md", "# Traps\nbody\n",
                           mode="w", root=str(repo))
    assert res["accepted"] is True
    assert res["tier"] == "A"
    assert res["path"] == "designer/skills/traps.md"
    assert (repo / "designer" / "skills" / "traps.md").read_text() == "# Traps\nbody\n"
    lines = _ledger(repo)
    assert len(lines) == 1
    assert lines[0]["accepted"] is True and lines[0]["wave"] == "wave-7"


def test_ledger_records_accept_and_reject(repo):
    W.designer_write("designer/skills/ok.md", "hi\n", root=str(repo))
    W.designer_write("harness/core/integrity.py", "evil\n", root=str(repo))  # Tier-C
    lines = _ledger(repo)
    assert len(lines) == 2
    assert lines[0]["accepted"] is True
    assert lines[1]["accepted"] is False and lines[1]["tier"] == "C"
    assert lines[1]["reason"]  # a non-empty reason is logged


# --------------------------------------------------------------------------- #
# Tier-C hard reject
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", [
    "harness/core/integrity.py",   # any *.py
    "CONTRACTS.md",
    "designer/BUDGETS.md",
    "scripts/hooks/pre-commit",
    "harness/designer/write.py",   # the cage cannot rewrite itself
])
def test_tier_c_paths_hard_rejected(repo, path):
    res = W.designer_write(path, "payload\n", root=str(repo))
    assert res["accepted"] is False
    assert res["tier"] == "C"
    # And nothing was written to any pre-existing Tier-C file.
    if (repo / path).exists():
        assert "payload" not in (repo / path).read_text()


# --------------------------------------------------------------------------- #
# Path traversal / symlink / outside-repo
# --------------------------------------------------------------------------- #
def test_path_traversal_rejected(repo):
    res = W.designer_write("designer/skills/../../etc/passwd", "x\n", root=str(repo))
    assert res["accepted"] is False
    assert "traversal" in res["reason"]


def test_outside_repo_rejected(repo, tmp_path):
    outside = tmp_path.parent / "outside.md"
    res = W.designer_write(str(outside), "x\n", root=str(repo))
    assert res["accepted"] is False


def test_symlink_target_rejected(repo):
    link = repo / "designer" / "skills" / "sneaky.md"
    link.symlink_to("/etc/passwd")
    res = W.designer_write("designer/skills/sneaky.md", "x\n", root=str(repo))
    assert res["accepted"] is False
    assert "symlink" in res["reason"]


def test_symlink_ancestor_rejected(repo, tmp_path):
    # A symlinked directory in the path is rejected even if it stays in-repo.
    real = repo / "designer" / "real_sk"
    real.mkdir()
    (repo / "designer" / "linkdir").symlink_to(real)
    res = W.designer_write("designer/linkdir/x.md", "x\n", root=str(repo))
    assert res["accepted"] is False
    assert "symlink" in res["reason"]


# --------------------------------------------------------------------------- #
# Tier-B redirect
# --------------------------------------------------------------------------- #
def test_tier_b_prompt_redirected_to_proposals(repo):
    res = W.designer_write("harness/gen/prompts/rules.md", "PROPOSED DELTA\n",
                           root=str(repo))
    assert res["accepted"] is True
    assert res["tier"] == "B"
    assert res["path"] == "designer/proposals/wave-7/rules.md"
    # The LIVE prompt is untouched...
    assert (repo / "harness" / "gen" / "prompts" / "rules.md").read_text() == "LIVE RULES\n"
    # ...and the proposal was staged.
    assert (repo / "designer" / "proposals" / "wave-7" / "rules.md").read_text() \
        == "PROPOSED DELTA\n"


# --------------------------------------------------------------------------- #
# Append-only memory
# --------------------------------------------------------------------------- #
def test_memory_append_accepted(repo):
    W.designer_write("designer/memory/LESSONS.md", "lesson 1\n", mode="a",
                     root=str(repo))
    W.designer_write("designer/memory/LESSONS.md", "lesson 2\n", mode="a",
                     root=str(repo))
    assert (repo / "designer" / "memory" / "LESSONS.md").read_text() \
        == "lesson 1\nlesson 2\n"


def test_memory_overwrite_rejected(repo):
    res = W.designer_write("designer/memory/LESSONS.md", "wipe\n", mode="w",
                           root=str(repo))
    assert res["accepted"] is False
    assert "append-only" in res["reason"]
    assert not (repo / "designer" / "memory" / "LESSONS.md").exists()


# --------------------------------------------------------------------------- #
# Budget caps
# --------------------------------------------------------------------------- #
def test_oversize_skill_rejected(repo):
    big = "\n".join(f"line {i}" for i in range(250)) + "\n"  # 250 > 200 cap
    res = W.designer_write("designer/skills/huge.md", big, root=str(repo))
    assert res["accepted"] is False
    assert "line" in res["reason"]
    assert not (repo / "designer" / "skills" / "huge.md").exists()


def test_skills_count_cap_rejects_26th(repo, monkeypatch):
    monkeypatch.setenv("DESIGNER_BUDGETS", str(repo / "designer" / "BUDGETS.md"))
    (repo / "designer" / "BUDGETS.md").write_text(
        "```budgets\nskill_max_lines = 200\nskills_max_active = 3\n```\n",
        encoding="utf-8")
    for i in range(3):
        r = W.designer_write(f"designer/skills/s{i}.md", "x\n", root=str(repo))
        assert r["accepted"] is True
    r = W.designer_write("designer/skills/s3.md", "x\n", root=str(repo))
    assert r["accepted"] is False
    assert "full" in r["reason"]
    # Editing an EXISTING skill is still allowed at the cap.
    r = W.designer_write("designer/skills/s0.md", "edited\n", root=str(repo))
    assert r["accepted"] is True


# --------------------------------------------------------------------------- #
# Kill-switch
# --------------------------------------------------------------------------- #
def test_flag_off_raises_and_writes_nothing(repo, monkeypatch):
    monkeypatch.delenv("DESIGNER_WRITE_ENABLED", raising=False)
    with pytest.raises(W.DesignerWriteDisabled):
        W.designer_write("designer/skills/x.md", "x\n", root=str(repo))
    assert not (repo / "designer" / "skills" / "x.md").exists()
    # No ledger either — there is no write path at all.
    assert not (repo / "designer" / "ledger" / "designer.jsonl").exists()
