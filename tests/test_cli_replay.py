"""Tests for `game replay` CLI dispatch (harness/cli.cmd_game_replay).

The replay verb dispatches by ENGINE: a .py game renders through render.replay_gif
(unchanged), a .js game through the executors' render_js_replay (previously no CLI
verb -> `game replay foo.js` crashed in the py-only loader). `--frames PATH`
persists the {meta, frames} substrate for both engines and, without an explicit
`--gif`, writes only that JSON.

JS cases are skipped when `node` is unavailable; py cases when pymunk is absent.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import cli  # noqa: E402
from harness.gen.gamegen import _DRIFT as DRIFT_PY, _DRIFT_JS as DRIFT_JS  # noqa: E402
from harness.verify.executors import find_godot_exe  # noqa: E402

NODE = shutil.which(os.environ.get("HARNESS_NODE", "node"))
requires_node = pytest.mark.skipif(NODE is None, reason="node not installed")

try:
    import pymunk  # noqa: F401
    _HAVE_WORLD = True
except Exception:  # noqa: BLE001
    _HAVE_WORLD = False
requires_world = pytest.mark.skipif(not _HAVE_WORLD, reason="pymunk unavailable")

GODOT_EXE = find_godot_exe()
requires_godot = pytest.mark.skipif(GODOT_EXE is None, reason="Godot binary not present")
_GODOT_SPEC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "godotworld", "examples", "traverse.spec.json")


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
@requires_world
def test_replay_py_dispatch_gif(tmp_path, capsys):
    game = tmp_path / "drift.py"
    game.write_text(DRIFT_PY, encoding="utf-8")
    gif = tmp_path / "out.gif"
    rc, out = _run(capsys, game_path=str(game), gif=str(gif))
    assert out["engine"] == "py"
    assert "gif" in out and out["gif"]["result"] in ("success", "failure", "timeout")
    assert gif.exists() and gif.stat().st_size > 0
    assert rc == 0


@requires_node
def test_replay_js_dispatch_gif_no_crash(tmp_path, capsys):
    # The whole point of the wiring fix: a .js game must NOT crash the py loader.
    game = tmp_path / "drift.js"
    game.write_text(DRIFT_JS, encoding="utf-8")
    gif = tmp_path / "out.gif"
    rc, out = _run(capsys, game_path=str(game), gif=str(gif))
    assert out["engine"] == "js"
    assert out["gif"]["result"] == "success"
    assert gif.exists() and gif.stat().st_size > 0
    assert rc == 0


@requires_node
def test_replay_js_marker_dispatch(tmp_path, capsys):
    # `// engine: js` on a non-.js extension still routes to the js path.
    game = tmp_path / "drift.txt"
    game.write_text("// engine: js\n" + DRIFT_JS, encoding="utf-8")
    rc, out = _run(capsys, game_path=str(game), frames=str(tmp_path / "f.json"))
    assert out["engine"] == "js"


# ====================================================================== #
# --frames substrate persistence + size reporting
# ====================================================================== #
@requires_node
def test_replay_frames_only_skips_gif(tmp_path, capsys):
    game = tmp_path / "drift.js"
    game.write_text(DRIFT_JS, encoding="utf-8")
    fj = tmp_path / "drift.frames.json"
    rc, out = _run(capsys, game_path=str(game), frames=str(fj))
    # frames-only: no GIF produced, only the JSON substrate.
    assert "gif" not in out
    assert out["frames"]["n_frames"] > 0
    assert out["frames"]["gzip_bytes"] < out["frames"]["raw_bytes"]
    assert fj.exists()
    doc = json.loads(fj.read_text(encoding="utf-8"))
    assert set(doc) == {"meta", "frames"}           # exactly the contract
    assert set(doc["meta"]) == {"title", "prompt", "world_size", "engine", "witness"}
    assert doc["frames"][0]["tick"] == 0
    assert rc == 0


@requires_node
def test_replay_frames_and_gif_together(tmp_path, capsys):
    game = tmp_path / "drift.js"
    game.write_text(DRIFT_JS, encoding="utf-8")
    gif = tmp_path / "out.gif"
    fj = tmp_path / "out.json"
    rc, out = _run(capsys, game_path=str(game), gif=str(gif), frames=str(fj))
    assert "gif" in out and "frames" in out          # explicit --gif -> both
    assert gif.exists() and fj.exists()


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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
