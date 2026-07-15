"""P0 gate test for the designer-cage pre-commit hook (§4 layer 3).

The hook blocks a Tier-C edit in a designer-attributed commit and any skill that
overruns its line budget, while leaving human commits free to touch Tier-C.
Exercised over a throwaway ``git init`` repo in tmp_path — nothing is installed.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HOOK = os.path.join(_REPO_ROOT, "scripts", "hooks", "pre-commit")

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None
    or not os.path.isfile(_HOOK),
    reason="git/bash/hook not available")

_BUDGETS = ("```budgets\nskill_max_lines = 200\nskills_max_active = 25\n```\n")


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "designer" / "skills").mkdir(parents=True)
    (r / "designer" / "BUDGETS.md").write_text(_BUDGETS, encoding="utf-8")
    return r


def _run_hook(repo, session=None):
    env = dict(os.environ)
    env.pop("DESIGNER_SESSION", None)
    if session is not None:
        env["DESIGNER_SESSION"] = session
    return subprocess.run(["bash", _HOOK], cwd=str(repo), env=env,
                          capture_output=True, text=True)


def _write_add(repo, rel, content):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    _git(repo, "add", rel)


def test_hook_blocks_tier_c_in_designer_commit(repo):
    _write_add(repo, "harness/core/integrity.py", "x = 1\n")
    res = _run_hook(repo, session="sess-1")
    assert res.returncode != 0
    assert "Tier-C" in res.stderr


def test_hook_allows_tier_a_skill_in_designer_commit(repo):
    _write_add(repo, "designer/skills/ok.md", "# ok\nbody\n")
    res = _run_hook(repo, session="sess-1")
    assert res.returncode == 0, res.stderr


def test_hook_blocks_oversize_skill(repo):
    big = "\n".join(f"line {i}" for i in range(250)) + "\n"
    _write_add(repo, "designer/skills/huge.md", big)
    res = _run_hook(repo, session="sess-1")
    assert res.returncode != 0
    assert "budget" in res.stderr


def test_hook_lets_human_commit_touch_tier_c(repo):
    # No DESIGNER_SESSION -> a human commit may edit Tier-C freely.
    _write_add(repo, "harness/core/integrity.py", "x = 1\n")
    res = _run_hook(repo, session=None)
    assert res.returncode == 0, res.stderr
