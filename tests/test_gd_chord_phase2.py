"""CHORD Phase 2 (MultiBinary PPO) — the IN-IMAGE trust suite (needs a Godot binary).

The offline twin is ``tests/test_chord_phase2.py`` (pure vector->wire mapping, histograms,
demo export). Here we drive the LIVE serve host (``serve_game.gd``) to pin the parts that
only a running engine can prove:

* the chord env exposes a MultiBinary(n) space and steps a multi-key + idle vector without
  crashing;
* the ALL-ZEROS idle tick is applied as ZERO act() calls when the ``allow_idle`` capability
  is on, and REJECTED as a protocol error at the serve boundary when it is off (the GDScript
  guard — tested at the raw wire, bypassing Python's own allow_empty rejection);
* a recorded chord+idle WIRE-action list replays bit-exactly through ``GdExecutor.run_batch``
  (the witness/demo export -> batch-host certificate bridge, now carrying chords and idle);
* a tiny-budget MultiBinary ``g3_prime`` run drives the whole learnability pipeline and emits
  the chord-size action histogram.
"""
from __future__ import annotations

import os
import socket
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.rl.env import MultiBinary  # noqa: E402
from harness.rl.godot_env import GodotServeEnv  # noqa: E402
from harness.verify.chord import ChordError, chord_from_mask  # noqa: E402
from harness.verify.executors import find_godot_exe  # noqa: E402
from harness.verify.gd_exec import GdExecutor  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MINI = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "mini_collect.gd")

GODOT_EXE = find_godot_exe()
requires_godot = pytest.mark.skipif(GODOT_EXE is None, reason="Godot binary not present")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _mask(actions, *pressed):
    """A 0/1 vector over `actions` with the named verbs set (order-free)."""
    v = np.zeros(len(actions), dtype=np.int8)
    for name in pressed:
        v[actions.index(name)] = 1
    return v


# ====================================================================== #
# 1. MultiBinary action space + stepping a chord / idle vector
# ====================================================================== #
@requires_godot
def test_chord_env_exposes_multibinary_and_steps():
    """chord_mode -> a MultiBinary(n) space; stepping a multi-key vector runs a real tick."""
    env = GodotServeEnv(MINI, port_base=_free_port(), chord_mode=True)
    try:
        assert isinstance(env.action_space, MultiBinary)
        assert env.action_space.n == len(env.actions) == 4
        assert env.chord_mode is True and env.allow_idle is True   # idle ON by default
        obs_dim = env.observation_space.shape[0]
        obs, info = env.reset(seed=0)
        assert obs.shape == (obs_dim,)
        # a real 2-key chord (down+right = a diagonal) steps without crashing
        o, r, term, trunc, i = env.step(_mask(env.actions, "down", "right"))
        assert o.shape == (obs_dim,)
        assert isinstance(r, float)
        assert i["tick"] == 1
    finally:
        env.close()


@requires_godot
def test_chord_env_idle_tick_applies_zero_acts():
    """All-zeros with allow_idle ON = an IDLE tick: the tick advances (physics steps) with
    ZERO act() calls, and nothing crashes."""
    env = GodotServeEnv(MINI, port_base=_free_port(), chord_mode=True)  # allow_idle default ON
    try:
        env.reset(seed=0)
        o, r, term, trunc, i = env.step(np.zeros(len(env.actions), dtype=np.int8))
        assert i["tick"] == 1          # a physics tick ran (idle is a real, losing tick)
        assert i["result"] in (None, "success", "failure", "error")
    finally:
        env.close()


# ====================================================================== #
# 2. The empty-chord capability guard (GDScript side, at the raw wire)
# ====================================================================== #
@requires_godot
def test_empty_chord_rejected_at_serve_boundary_when_capability_off():
    """With allow_idle OFF, an empty chord [] on the wire is a PROTOCOL ERROR at the serve
    host — tested by sending the raw frame (bypassing Python's own allow_empty rejection),
    so this pins the GDScript guard in serve_game.gd, not just the chord.py guard."""
    # chord_mode with allow_idle explicitly OFF -> init carries no allow_idle capability.
    env = GodotServeEnv(MINI, port_base=_free_port(), chord_mode=True, allow_idle=False)
    try:
        assert env.allow_idle is False
        env.reset(seed=0)
        reply = env._exchange({"op": "act", "actions": [[]], "n_ticks": 1})
        assert reply.get("error"), reply           # protocol error, not a silent no-op
        assert "empty chord" in str(reply.get("error")).lower()
    finally:
        env.close()

    # And the Python bridge refuses to even produce that wire form without allow_empty:
    with pytest.raises(ChordError):
        chord_from_mask(np.zeros(4, dtype=np.int8), ["up", "down", "left", "right"])


@requires_godot
def test_empty_chord_accepted_at_serve_boundary_when_capability_on():
    """With allow_idle ON, the same empty chord [] is a legal idle tick (no error)."""
    env = GodotServeEnv(MINI, port_base=_free_port(), chord_mode=True, allow_idle=True)
    try:
        env.reset(seed=0)
        reply = env._exchange({"op": "act", "actions": [[]], "n_ticks": 1})
        assert not reply.get("error"), reply       # accepted as an idle tick
    finally:
        env.close()


# ====================================================================== #
# 3. Witness/demo export (chords + idle) replays bit-exactly via GdExecutor
# ====================================================================== #
@requires_godot
def test_chord_and_idle_wire_actions_replay_through_executor():
    """A recorded WIRE-action list carrying a real chord AND an idle [] tick MUST replay
    to the SAME terminal result / ticks / latched checkpoints through GdExecutor.run_batch
    (the serve<->batch determinism the demo/witness bridge depends on). No win required —
    this pins the round-trip of the Phase-2 wire, including the idle tick."""
    # A short, deterministic mixed sequence: a diagonal chord, an idle, singletons.
    env = GodotServeEnv(MINI, port_base=_free_port(), chord_mode=True, horizon=200)
    plan = [
        _mask(env.actions, "up"),
        _mask(env.actions, "up", "right"),          # a real 2-key chord -> ["right","up"]
        np.zeros(len(env.actions), dtype=np.int8),  # idle []
        _mask(env.actions, "right"),
        _mask(env.actions, "down", "right"),        # another chord
    ]
    wire_actions_rec: list = []
    info = None
    try:
        env.reset(seed=0)
        for m in plan:
            wire_actions_rec.append(
                chord_from_mask(m, env.actions, allow_empty=env.allow_idle))
            _o, _r, term, trunc, info = env.step(m)
            if term or trunc:
                break
    finally:
        env.close()
    assert info is not None
    # The recorded list carries BOTH a chord (a list) and an idle ([]) — the Phase-2 shapes.
    assert any(isinstance(a, list) and len(a) >= 2 for a in wire_actions_rec)
    assert any(isinstance(a, list) and len(a) == 0 for a in wire_actions_rec)

    with open(MINI, "r", encoding="utf-8") as fh:
        src = fh.read()
    ex = GdExecutor(port_base=_free_port())
    try:
        # run_batch AUTO-detects the idle tick and inits the host with allow_idle.
        rec = ex.run_batch(src, [{"seed": 0, "actions": wire_actions_rec}],
                           max_ticks=len(wire_actions_rec))[0]
    finally:
        ex.close()

    # The determinism guarantee is on the STATE the replay reproduces: the tick count and the
    # latched checkpoints must match bit-for-bit. The terminal-RESULT string is normalized —
    # the serve env reports "" / None for a still-running tick while the batch executor reports
    # "budget" for reaching max_ticks without a terminal; both mean "did not terminate". A real
    # terminal (success / failure / error), if reached, must agree exactly.
    def _terminal(r):
        return r if r in ("success", "failure", "error") else None
    assert _terminal(rec.get("result")) == _terminal(info["result"]), \
        (rec.get("result"), info["result"])
    assert rec.get("ticks") == info["tick"]
    serve_latch = {k: v for k, v in info["latched"].items() if v is not None}
    batch_latch = {k: v for k, v in (rec.get("checkpoints") or {}).items() if v is not None}
    assert serve_latch == batch_latch


# ====================================================================== #
# 4. MultiBinary g3_prime learnability pipeline (needs sb3) + chord histogram
# ====================================================================== #
@requires_godot
def test_g3_prime_chord_smoke_emits_chord_histogram(monkeypatch):
    """End-to-end tiny MultiBinary run: g3_prime with chord_mode trains a Bernoulli-head PPO
    over the batched serve env, evaluates, and emits the standard dict PLUS the chord-size
    action histogram. Asserts the pipeline runs, chord_mode is recorded, the histogram has the
    0/1/2/3+ chord-size distribution, and the bridge is never broken."""
    pytest.importorskip("stable_baselines3")
    from harness.rl.certify import g3_prime

    monkeypatch.setenv("GIP_PORT_BASE", str(_free_port()))
    res = g3_prime(MINI, budget_steps=4000, trainer="sb3", seed=0,
                   n_eval=2, num_envs=2, num_steps=64, patience=999,
                   chord_mode=True)

    assert res["chord_mode"] is True
    assert res["bridge_ok"] in (None, True)          # never a broken serve/batch bridge
    assert res["trained_steps"] >= 4000 - 64 * 2
    hist = res["action_histogram"]["greedy"]
    assert set(hist["chord_size"]) == {"0", "1", "2", "3+"}
    assert "mean_chord_size" in hist and "total_key_presses" in hist
    # per_action is per-KEY press frequency, seeded for every declared verb (n_actions keys),
    # and sums to total_key_presses (a 2-key tick counts two keys).
    assert len(hist["per_action"]) == res["n_actions"] == 4
    assert sum(hist["per_action"].values()) == hist["total_key_presses"]
    if res["rl_witness"] is not None:
        assert res["bridge_ok"] is True              # any witness must replay to success
