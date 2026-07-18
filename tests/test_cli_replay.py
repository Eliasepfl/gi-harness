"""Tests for `game replay` CLI dispatch (harness/cli.cmd_game_replay).

The replay verb dispatches by ENGINE: godot (.spec.json) renders through
render_godot_replay and gdscript (.gd) through render_gdscript_replay, driving the
serve host. `--frames PATH` persists the {meta, frames} substrate and, without an
explicit `--gif`, writes only that JSON.

Godot/gdscript cases are skipped when the Godot binary is unavailable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import cli  # noqa: E402
from harness.verify.executors import find_godot_exe  # noqa: E402


GODOT_EXE = find_godot_exe()
requires_godot = pytest.mark.skipif(GODOT_EXE is None, reason="Godot binary not present")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GODOT_SPEC = os.path.join(_ROOT, "tests", "fixtures", "godot_specs", "traverse.spec.json")
_GD_MINI = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "mini_collect.gd")
_GD_MINI_3D = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "mini_collect_3d.gd")


def _args(**kw):
    base = dict(game_path=None, gif=None, frames=None, seed=0, json=True)
    base.update(kw)
    return argparse.Namespace(**base)


def _run(capsys, **kw):
    rc = cli.cmd_game_replay(_args(**kw))
    return rc, json.loads(capsys.readouterr().out)


# ====================================================================== #
# Extension dispatch
# ====================================================================== #
# ====================================================================== #
# --frames substrate persistence + size reporting
# ====================================================================== #
# ====================================================================== #
# Godot lane e2e: the whole point of Track G — `game replay foo.spec.json`
# no longer crashes the py-only loader; --frames + --gif both work end-to-end.
# ====================================================================== #
@requires_godot
def test_replay_godot_frames_and_gif_e2e(tmp_path, capsys):
    """A `.spec.json` routes to the Godot lane: the witness is re-derived via
    verify (same as js/py), the frames substrate validates against the shared
    {meta, frames} schema, and the GIF renders to a real multi-frame file.

    One cmd call so the (slow) witness search runs ONCE for both outputs."""
    from PIL import Image
    gif = tmp_path / "traverse.gif"
    fj = tmp_path / "traverse.frames.json"
    rc, out = _run(capsys, game_path=_GODOT_SPEC, gif=str(gif), frames=str(fj))
    assert rc == 0
    assert out["engine"] == "godot"

    # --- frames substrate: same schema the js/py lanes emit ---
    assert out["frames"]["n_frames"] > 0
    assert out["frames"]["gzip_bytes"] < out["frames"]["raw_bytes"]
    doc = json.loads(fj.read_text(encoding="utf-8"))
    assert set(doc) == {"meta", "frames"}                    # exactly the contract
    assert set(doc["meta"]) == {"title", "prompt", "world_size", "engine", "witness"}
    assert doc["meta"]["engine"] == "godot"
    assert doc["meta"]["world_size"] == [1400, 700]          # meta has world size
    frames = doc["frames"]
    assert frames[0]["tick"] == 0
    assert frames[-1]["tick"] == max(fr["tick"] for fr in frames)   # monotone
    for fr in frames:
        assert set(fr) == {"tick", "entities"}
        assert fr["entities"]                                # non-empty scene

    # --- GIF smoke: a real multi-frame file ---
    assert "gif" in out and gif.exists() and gif.stat().st_size > 0
    with Image.open(str(gif)) as im:
        assert im.n_frames > 0


# ====================================================================== #
# GDScript lane e2e: `game replay foo.gd` drives the serve host — --frames +
# --gif both work end-to-end (positional recording proving the .gd plays).
# ====================================================================== #
@requires_godot
@pytest.mark.parametrize("path", [_GD_MINI, _GD_MINI_3D],
                         ids=["mini_collect_2d", "mini_collect_3d"])
def test_replay_gdscript_frames_and_gif_e2e(tmp_path, capsys, path):
    """A `.gd` routes to the gdscript lane: the witness is re-derived via verify,
    the frames substrate validates against the shared {meta, frames} schema (tick
    monotone, entities non-empty), and the GIF renders to a real multi-frame file.

    One cmd call so the (slow) witness search runs ONCE for both outputs."""
    from PIL import Image
    gif = tmp_path / "mini.gif"
    fj = tmp_path / "mini.frames.json"
    rc, out = _run(capsys, game_path=path, gif=str(gif), frames=str(fj))
    assert rc == 0
    assert out["engine"] == "gdscript"

    # --- frames substrate: same schema the js/py/godot lanes emit ---
    assert out["frames"]["n_frames"] > 0
    assert out["frames"]["gzip_bytes"] < out["frames"]["raw_bytes"]
    doc = json.loads(fj.read_text(encoding="utf-8"))
    assert set(doc) == {"meta", "frames"}                    # exactly the contract
    assert set(doc["meta"]) == {"title", "prompt", "world_size", "engine", "witness"}
    assert doc["meta"]["engine"] == "gdscript"
    frames = doc["frames"]
    assert frames[0]["tick"] == 0
    assert frames[-1]["tick"] == max(fr["tick"] for fr in frames)   # monotone
    for fr in frames:
        assert set(fr) == {"tick", "entities"}
        assert fr["entities"]                                # non-empty scene

    # --- GIF smoke: a real multi-frame file ---
    assert "gif" in out and gif.exists() and gif.stat().st_size > 0
    with Image.open(str(gif)) as im:
        assert im.n_frames > 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
