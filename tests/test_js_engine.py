"""Tests for the JS engine path through verify_game + gamegen (rung-4 port).

A JS game is verified end-to-end by the Node runner (Planck.js): G0/G2 from the
runner's "check" facts, G1/G3 from batched episode jobs. These tests exercise the
happy path (COMPLETED + engine tag + schema), engine detection, the JS G0 failure
surfaces (missing checkpoints, forbidden require), determinism, the generator's
JS template path, and the VERIFY_ERROR shape when Node is unreachable.

Skipped wholesale when `node` is unavailable, except the engine-detection and
node-missing tests that need no runtime.
"""
from __future__ import annotations

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.verify.gameverify import detect_engine, verify_game  # noqa: E402
from harness.gen.gamegen import _DRIFT_JS as DRIFT_JS, generate_game  # noqa: E402

NODE = shutil.which(os.environ.get("HARNESS_NODE", "node"))
requires_node = pytest.mark.skipif(NODE is None, reason="node not installed")

_REPORT_KEYS = {"passed", "failure_class", "layers", "hint", "warnings",
                "progress", "witness"}


def _write(tmp_path, name, source):
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return str(p)


# A JS game missing ONLY the checkpoints symbol (drift, truncated before it).
GAME_NO_CHECKPOINTS_JS = DRIFT_JS.split("function checkpoints", 1)[0]

# A JS game that reaches for a forbidden capability -> rejected by the scan.
GAME_FORBIDDEN_REQUIRE_JS = 'const _fs = require("fs");\n' + DRIFT_JS


# ====================================================================== #
# Engine detection (no runtime needed)
# ====================================================================== #
def test_detect_engine_by_extension():
    assert detect_engine("game.js", "") == "js"
    assert detect_engine("game.py", "") == "py"
    assert detect_engine("game.txt", "") == "py"


def test_detect_engine_by_marker():
    assert detect_engine("g.txt", "// engine: js\nconst TITLE='x';") == "js"
    assert detect_engine("g.txt", "# engine: js\n") == "js"
    assert detect_engine("g.txt", "const TITLE='x';") == "py"


# ====================================================================== #
# Happy path: a JS game verifies COMPLETED with an engine tag
# ====================================================================== #
@requires_node
def test_js_game_verifies_completed(tmp_path):
    path = _write(tmp_path, "drift.js", DRIFT_JS)
    rep = verify_game(path, sandboxed=False)
    assert rep["passed"] is True, rep
    assert rep["failure_class"] is None
    assert rep["engine"] == "js"
    # The report schema is the standard set + the engine tag.
    assert set(rep) == _REPORT_KEYS | {"engine"}
    for layer in ("G0_static", "G1_rollout", "G2_goal", "G3_solve"):
        assert rep["layers"][layer]["passed"], (layer, rep["layers"][layer])
    w = rep["witness"]
    assert w is not None and w["ticks"] >= 5
    assert set(w["checkpoints"]) == {"moved_off_start", "crossed_midline",
                                     "entered_upper_half"}


@requires_node
def test_js_game_via_marker_on_non_js_extension(tmp_path):
    path = _write(tmp_path, "drift.txt", "// engine: js\n" + DRIFT_JS)
    rep = verify_game(path, sandboxed=False)
    assert rep.get("engine") == "js"
    assert rep["passed"] is True, rep


@requires_node
def test_js_verify_is_deterministic(tmp_path):
    path = _write(tmp_path, "drift.js", DRIFT_JS)
    a = verify_game(path, sandboxed=False)
    b = verify_game(path, sandboxed=False)
    # Same witness ticks + latch map across independent verifications.
    assert a["witness"]["ticks"] == b["witness"]["ticks"]
    assert a["witness"]["checkpoints"] == b["witness"]["checkpoints"]


# ====================================================================== #
# JS G0 failures surface correctly
# ====================================================================== #
@requires_node
def test_js_missing_checkpoints_fails_g0(tmp_path):
    path = _write(tmp_path, "nocp.js", GAME_NO_CHECKPOINTS_JS)
    rep = verify_game(path, sandboxed=False)
    assert rep["engine"] == "js"
    assert rep["failure_class"] == "ENV_ERROR"
    symbols = rep["layers"]["G0_static"]["checks"]["symbols"]
    assert symbols["pass"] is False
    assert "checkpoints" in symbols["missing"]
    assert "checkpoints" in rep["hint"]


@requires_node
def test_js_forbidden_require_fails_g0(tmp_path):
    path = _write(tmp_path, "bad.js", GAME_FORBIDDEN_REQUIRE_JS)
    rep = verify_game(path, sandboxed=False)
    assert rep["engine"] == "js"
    assert rep["failure_class"] == "ENV_ERROR"
    scan = rep["layers"]["G0_static"]["checks"]["sandbox_scan"]
    assert scan["pass"] is False
    assert "require" in scan["violations"]
    assert "sandbox" in rep["hint"]


# ====================================================================== #
# Generator: the JS template path produces a verifying .js game
# ====================================================================== #
@requires_node
def test_generate_js_template_completes(tmp_path):
    res = generate_game("a puck on ice", out_dir=str(tmp_path),
                        backend="template", engine="js", max_repairs=2)
    assert res["engine"] == "js"
    assert res["verdict"] == "COMPLETED"
    assert res["backend"] == "template"
    assert res["game_path"].endswith(".js")
    assert res["integrity"] == "ok"
    # Attempts written as .js files.
    slug = os.path.basename(os.path.dirname(res["game_path"]))
    assert (tmp_path / slug / "a1.js").is_file()


@requires_node
def test_generate_js_via_env_default(tmp_path, monkeypatch):
    # HARNESS_ENGINE=js selects the JS path when `engine` is not passed.
    monkeypatch.setenv("HARNESS_ENGINE", "js")
    res = generate_game("drift on ice", out_dir=str(tmp_path), backend="template",
                        max_repairs=1)
    assert res["engine"] == "js"
    assert res["game_path"].endswith(".js")


# ====================================================================== #
# VERIFY_ERROR shape when Node is unreachable
# ====================================================================== #
def test_verify_error_shape_when_node_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_NODE", "definitely-not-a-real-node-binary-xyz")
    path = _write(tmp_path, "drift.js", DRIFT_JS)
    rep = verify_game(path, sandboxed=False)
    # VERIFY_ERROR-shaped: an error record, no funnel layers -> repair loop stops.
    assert "error" in rep and "layers" not in rep
    assert rep["error"]["type"] == "node_missing"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
