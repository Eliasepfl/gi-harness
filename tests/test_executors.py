"""Tests for the episode-executor seam (harness/verify/executors.py).

Covers: PyExecutor faithfulness (its dicts equal the in-process run_episode's),
cross-engine parity (the same drift game verifies COMPLETED in py AND js), the
JsExecutor golden witness from the spike, the shared episode-dict shape, the JS
render adapter, and the VERIFY_ERROR shape on node-missing / timeout / bad path.

Every JS test is skipped when `node` is unavailable; the pymunk-World tests are
skipped when the real World cannot be imported.
"""
from __future__ import annotations

import os
import random
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.verify.executors import (  # noqa: E402
    JsExecutor, PyExecutor, VerifyError, render_js_replay,
)
from harness.verify.gameverify import load_game, run_episode, verify_game  # noqa: E402
from harness.gen.gamegen import _DRIFT as DRIFT_PY, _DRIFT_JS as DRIFT_JS  # noqa: E402

NODE = shutil.which(os.environ.get("HARNESS_NODE", "node"))
requires_node = pytest.mark.skipif(NODE is None, reason="node not installed")

try:
    from harness.core.world import World
    _HAVE_WORLD = True
except Exception:  # noqa: BLE001
    _HAVE_WORLD = False
requires_world = pytest.mark.skipif(not _HAVE_WORLD, reason="pymunk World unavailable")

ACTIONS = ["left", "right", "up", "down"]


def _macro(seed, horizon=120):
    """Byte-for-byte the gameverify._macro_plan the probe uses."""
    rng = random.Random(seed)
    plan = []
    while len(plan) < horizon:
        plan.extend([rng.choice(ACTIONS)] * rng.randint(1, 4))
    return plan[:horizon]


# ====================================================================== #
# PyExecutor faithfulness
# ====================================================================== #
@requires_world
def test_pyexecutor_matches_run_episode():
    """PyExecutor.run_batch is a pure wrapper: its record equals run_episode's."""
    game = load_game(DRIFT_PY)
    plan = _macro(0)
    ex = PyExecutor(world_factory=lambda seed=0: World(seed=seed))
    [rec] = ex.run_batch(game, [{"seed": 0, "actions": plan}], 120)

    world = World(seed=0)
    game.build(world)
    direct = run_episode(game, world, iter(plan), 120)

    assert rec["result"] == direct["result"]
    assert rec["ticks"] == direct["ticks"]
    assert rec["checkpoints"] == direct["checkpoints"]
    assert rec["final_snapshot"] == direct["snapshot"]


@requires_world
def test_pyexecutor_g1_extras_present_with_margin():
    game = load_game(DRIFT_PY)
    ex = PyExecutor(world_factory=lambda seed=0: World(seed=seed))
    [rec] = ex.run_batch(game, [{"seed": 0, "actions": [None] * 20}], 20,
                         escape_margin=200.0)
    assert "nan" in rec and rec["nan"] is False
    assert "oob" in rec and isinstance(rec["oob"], list)
    # Without escape_margin the extras stay off (lean records).
    [lean] = ex.run_batch(game, [{"seed": 0, "actions": [None] * 5}], 5)
    assert "nan" not in lean and "oob" not in lean


# ====================================================================== #
# Cross-engine parity: the SAME drift game passes in py and js
# ====================================================================== #
@requires_world
@requires_node
def test_executor_parity_drift_both_complete(tmp_path):
    py_path = tmp_path / "drift.py"
    py_path.write_text(DRIFT_PY, encoding="utf-8")
    js_path = tmp_path / "drift.js"
    js_path.write_text(DRIFT_JS, encoding="utf-8")

    rep_py = verify_game(str(py_path), sandboxed=False)
    rep_js = verify_game(str(js_path), sandboxed=False)

    assert rep_py["passed"] is True, rep_py
    assert rep_js["passed"] is True, rep_js
    # engine is tagged on js reports; py reports stay byte-identical (no key).
    assert rep_js["engine"] == "js"
    assert "engine" not in rep_py
    # Both engines find a genuine (non-trivial) winning witness.
    assert rep_py["witness"]["ticks"] >= 5
    assert rep_js["witness"]["ticks"] >= 5
    # Same declared milestone set on both engines.
    assert set(rep_py["witness"]["checkpoints"]) == set(rep_js["witness"]["checkpoints"])


# ====================================================================== #
# JsExecutor: golden witness (SPIKE_REPORT.md) + dict shape + determinism
# ====================================================================== #
@requires_node
def test_jsexecutor_drift_golden_witness():
    ex = JsExecutor()
    [rec] = ex.run_batch(DRIFT_JS, [{"seed": 0, "actions": _macro(0)}], 120)
    # SPIKE_REPORT.md: sample_drift solves at episode 0 in 61 ticks with these
    # latch ticks. This pins the Node engine's determinism to a known value.
    assert rec["result"] == "success"
    assert rec["ticks"] == 61
    assert rec["checkpoints"] == {"moved_off_start": 3, "crossed_midline": 29,
                                  "entered_upper_half": 20}


@requires_node
def test_jsexecutor_dict_shape():
    ex = JsExecutor()
    [rec] = ex.run_batch(DRIFT_JS, [{"seed": 0, "actions": ["up", "right"]}], 2)
    for key in ("result", "ticks", "checkpoints", "final_snapshot", "actions"):
        assert key in rec, key
    assert rec["actions"] == ["up", "right"][:rec["ticks"]]


@requires_node
def test_jsexecutor_batch_is_deterministic():
    ex = JsExecutor()
    specs = [{"seed": 0, "actions": _macro(e)} for e in range(8)]
    a = ex.run_batch(DRIFT_JS, specs, 120)
    b = ex.run_batch(DRIFT_JS, specs, 120)
    assert a == b  # two independent node processes -> identical records


# ====================================================================== #
# JS render adapter (frames path) -> a real GIF, render.py untouched
# ====================================================================== #
@requires_node
def test_render_js_replay_produces_gif(tmp_path):
    out = tmp_path / "drift.gif"
    res = render_js_replay(DRIFT_JS, str(out), actions=_macro(0), seed=0,
                           label="Drift", every=4)
    assert res["result"] == "success"
    assert out.exists() and out.stat().st_size > 0
    assert res["frames"] > 0


# ====================================================================== #
# VERIFY_ERROR shape on infra failure
# ====================================================================== #
def test_jsexecutor_node_missing_is_verify_error():
    ex = JsExecutor(node="definitely-not-a-real-node-binary-xyz")
    with pytest.raises(VerifyError) as ei:
        ex.run_batch(DRIFT_JS, [{"seed": 0, "actions": ["up"]}], 1)
    assert ei.value.kind == "node_missing"
    report = ei.value.as_report()
    assert "error" in report and "layers" not in report  # VERIFY_ERROR shape
    assert report["error"]["type"] == "node_missing"


def test_jsexecutor_missing_runner_is_verify_error(tmp_path):
    ex = JsExecutor(runner_path=str(tmp_path / "no_such_runner.js"))
    with pytest.raises(VerifyError) as ei:
        ex.run_check(DRIFT_JS)
    assert ei.value.kind == "node_runner_missing"
    assert "layers" not in ei.value.as_report()


@requires_node
def test_jsexecutor_timeout_is_verify_error():
    # An absurdly small budget (< node cold start) forces a kill+timeout.
    ex = JsExecutor(timeout_s=0.001)
    with pytest.raises(VerifyError) as ei:
        ex.run_batch(DRIFT_JS, [{"seed": 0, "actions": _macro(0)}], 120)
    assert ei.value.kind == "js_timeout"
    report = ei.value.as_report()
    assert report["error"]["type"] == "js_timeout" and "layers" not in report


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
