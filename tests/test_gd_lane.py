"""End-to-end tests for the GDScript (GameAPI) lane (skipped without the Godot binary).

Driven through ``serve_game.gd`` + ``GdExecutor``:

* the ``mini_collect.gd`` fixture certifies G0-G3 (all four layers pass, a non-trivial
  replayable witness collects both gems, every milestone latches);
* the serve stepping is DETERMINISTIC — the same (seed, actions) replays byte-for-byte
  across two independent sessions (the G1 two-run drift gate);
* the parse gate rejects a syntax-broken game and the contract probe rejects a game
  missing a required method — both surface as a clean G0 ENV_ERROR;
* Godot starts and verifies correctly under the SCRUBBED env even when the parent
  process holds an OPENROUTER_API_KEY (the scrub does not break the lane).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.verify.executors import find_godot_exe  # noqa: E402
from harness.verify.gameverify import verify_game  # noqa: E402
from harness.verify.gd_exec import GdExecutor  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MINI = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "mini_collect.gd")

GODOT_EXE = find_godot_exe()
requires_godot = pytest.mark.skipif(GODOT_EXE is None, reason="Godot binary not present")

_REPORT_KEYS = {"passed", "failure_class", "layers", "hint", "warnings",
                "progress", "witness"}


def _free_port() -> int:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ====================================================================== #
# 1. Full G0-G3 certification of the fixture game
# ====================================================================== #
@requires_godot
def test_mini_collect_certifies_g0_g3():
    rep = verify_game(_MINI, sandboxed=False)
    assert rep["passed"] is True, rep
    assert rep["failure_class"] is None
    assert rep["engine"] == "gdscript"
    assert set(rep) == _REPORT_KEYS | {"engine"}
    for layer in ("G0_static", "G1_rollout", "G2_goal", "G3_solve"):
        assert rep["layers"][layer]["passed"], (layer, rep["layers"][layer])
    w = rep["witness"]
    assert w is not None and w["ticks"] >= 20, w        # a real, non-trivial play
    assert set(w["checkpoints"]) == {"got_first", "got_both"}
    assert all(t is not None for t in w["checkpoints"].values()), w["checkpoints"]


@requires_godot
def test_mini_collect_g0_contract_probe_facts():
    """G0 exposes the three code gates: banned-scan clean, parse gate passed, and the
    contract probe confirming every GameAPI method — plus one controlled dynamic body."""
    rep = verify_game(_MINI, sandboxed=False)
    checks = rep["layers"]["G0_static"]["checks"]
    assert checks["sandbox_scan"]["pass"] is True
    assert checks["loads"]["pass"] is True          # parse gate (compile-check)
    assert checks["symbols"]["pass"] is True        # contract probe (has_method)
    assert checks["controlled"]["controlled"] == ["player"]
    assert checks["counts"]["n"] == 3


# ====================================================================== #
# 2. Determinism — the G1 two-run drift gate, direct
# ====================================================================== #
@requires_godot
def test_gd_serve_two_run_drift_is_zero():
    """Two independent serve sessions, the SAME seed + action plan -> byte-identical
    final snapshots (the deterministic contract G1 gates)."""
    with open(_MINI, "r", encoding="utf-8") as fh:
        src = fh.read()
    plan = ["up"] * 12 + ["right"] * 20 + ["down"] * 8
    snaps = []
    for _ in range(2):
        ex = GdExecutor(port_base=_free_port())
        try:
            rec = ex.run_batch(src, [{"seed": 0, "actions": plan}], len(plan))[0]
            snaps.append(rec["final_snapshot"])
        finally:
            ex.close()
    assert snaps[0] == snaps[1], (snaps[0], snaps[1])


@requires_godot
def test_gd_serve_collect_mechanic_latches():
    """Driving straight up to gem_a latches the first milestone through the serve seam
    (the collect mechanic + per-step latching work). got_both is proven end-to-end by
    the G3 witness in test_mini_collect_certifies_g0_g3."""
    with open(_MINI, "r", encoding="utf-8") as fh:
        src = fh.read()
    ex = GdExecutor(port_base=_free_port())
    try:
        rec = ex.run_batch(src, [{"seed": 0, "actions": ["up"] * 14}], 14)[0]
    finally:
        ex.close()
    assert rec["checkpoints"]["got_first"] is not None, rec["checkpoints"]
    assert rec["checkpoints"]["got_both"] is None       # one gem only from a pure "up"


# ====================================================================== #
# 3. Parse gate + contract probe rejections
# ====================================================================== #
@requires_godot
def test_parse_gate_rejects_syntax_error(tmp_path):
    p = tmp_path / "broken.gd"
    p.write_text("extends GameAPI\nfunc build(world_seed):\n\tvar x = = =\n",
                 encoding="utf-8")
    rep = verify_game(str(p), sandboxed=False)
    assert rep["engine"] == "gdscript"
    assert rep["failure_class"] == "ENV_ERROR"
    assert rep["layers"]["G0_static"]["checks"]["loads"]["pass"] is False


@requires_godot
def test_contract_probe_rejects_missing_method(tmp_path):
    # A syntactically valid game that FORGETS state() -> the contract probe rejects it.
    p = tmp_path / "no_state.gd"
    p.write_text(
        "extends GameAPI\n"
        "func build(world_seed):\n"
        "\tvar b = RigidBody2D.new()\n"
        "\tadd_child(b)\n"
        "func act(action): pass\n"
        "func checkpoints(): return {\"done\": false}\n"
        "func is_success(): return false\n"
        "func is_failure(): return false\n"
        "func actions(): return [\"a\", \"b\"]\n",
        encoding="utf-8")
    rep = verify_game(str(p), sandboxed=False)
    assert rep["engine"] == "gdscript"
    assert rep["failure_class"] == "ENV_ERROR"
    symbols = rep["layers"]["G0_static"]["checks"]["symbols"]
    assert symbols["pass"] is False
    assert "state" in symbols["missing"], symbols


# ====================================================================== #
# 4. Env scrub does not break the lane (secret in the parent -> still certifies)
# ====================================================================== #
@requires_godot
def test_gd_lane_verifies_under_scrubbed_env(monkeypatch):
    """With OPENROUTER_API_KEY set in the PARENT, the game still certifies — Godot
    starts fine under the minimal scrubbed env, and (per the pure-python wiring test)
    the child never receives the key."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-parent-should-not-leak")
    rep = verify_game(_MINI, sandboxed=False)
    assert rep["passed"] is True, rep
    assert rep["engine"] == "gdscript"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
