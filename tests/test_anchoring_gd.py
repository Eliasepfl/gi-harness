"""End-to-end MATERIAL-REALITY gate on the GDScript lane (skipped without the Godot binary).

Runs the whole G0-G3 funnel through ``serve_game.gd`` + ``GdExecutor`` on two fixtures that
differ ONLY in whether the goal is a real reported node:

* ``ghost_goal.gd``    — milestone + win latch on distance to a bare hardcoded coordinate;
                         certifies (advisory gate) but the anchoring gate FLAGS it, stashes
                         ``report["anchoring"]``, warns once, and compiles the typed hint.
* ``anchored_goal.gd`` — same game with the goal built as a real Area2D + CircleShape2D and
                         reported in state(); certifies AND the gate leaves it pristine.

The pure decision logic + hint text are covered engine-free in ``test_anchoring.py``; this
file is the container-gated end-to-end net (the coordinator's Godot image runs it).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.gen import feedback as F  # noqa: E402
from harness.verify.executors import find_godot_exe  # noqa: E402
from harness.verify.gameverify import verify_game  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GD = os.path.join(_ROOT, "tests", "fixtures", "gd_games")
_GHOST = os.path.join(_GD, "ghost_goal.gd")
_ANCHORED = os.path.join(_GD, "anchored_goal.gd")

requires_godot = pytest.mark.skipif(find_godot_exe() is None, reason="Godot binary not present")


@requires_godot
def test_ghost_goal_certifies_but_anchoring_flags_it():
    rep = verify_game(_GHOST, sandboxed=False)
    assert rep["passed"] is True, rep                    # ADVISORY: non-gating, still certifies
    assert rep["engine"] == "gdscript"

    sub = rep["layers"]["G3_solve"]["checks"]["material_anchoring"]
    assert sub["advisory"] is True and sub["pass"] is True and sub["anchored"] is False, sub

    stash = rep["anchoring"]
    assert stash["outcome"] == "unanchored", stash
    keys = [m["milestone"] for m in stash["milestones"]]
    assert "reached_goal" in keys or "is_success" in keys, stash
    assert len([w for w in rep["warnings"] if w.startswith("ANCHORING: ")]) == 1, rep["warnings"]

    ds = F.compile_directives({"anchoring": F.anchoring_finding(rep)})
    assert ds and ds[0].source == "unanchored_milestone", ds
    assert ds[0].text.startswith("UNANCHORED MILESTONE"), ds[0].text
    assert "if it does not mark a place" in ds[0].text.lower(), ds[0].text


@requires_godot
def test_anchored_goal_certifies_and_is_not_flagged():
    rep = verify_game(_ANCHORED, sandboxed=False)
    assert rep["passed"] is True, rep
    assert rep["engine"] == "gdscript"

    sub = rep["layers"]["G3_solve"]["checks"]["material_anchoring"]
    assert sub["anchored"] is True and sub["pass"] is True, sub
    assert "anchoring" not in rep, rep.get("anchoring")
    assert not [w for w in rep["warnings"] if w.startswith("ANCHORING: ")], rep["warnings"]
