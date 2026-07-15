"""Tests for the Godot serve lane (runner.gd serve mode + harness/rl/godot_env).

Two tiers, mirroring tests/test_godot_exec.py:

* **Pure-python (always run):** the 4-byte-BE length-prefixed JSON framing round-trips
  over a socketpair; the per-op STALE deadline raises a TYPED error instead of hanging;
  a closed peer is a typed error; and the port-safety bind-check raises ``port_in_use``
  when the derived port is already bound (no Godot needed — the listener binds before
  the process spawns).

* **End-to-end (skipped when the Godot binary is absent):** a serve round-trip over
  ``traverse.spec.json`` (init/reset/act/close, stable obs length), same-seed
  determinism across two independent serve sessions, the terminated/truncated split,
  serve/batch PARITY (a serve-recorded action sequence replays identically through
  ``GodotExecutor.run_batch`` — the certificate bridge), and a tiny-budget ``g3_prime``
  sb3 smoke that runs the whole learnability pipeline end-to-end.
"""
from __future__ import annotations

import os
import socket
import sys
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.rl.godot_env import (  # noqa: E402
    GodotServeError, GodotServeEnv, _recv_frame, _send_frame,
)
from harness.verify.executors import GodotExecutor, find_godot_exe  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXAMPLES = os.path.join(_ROOT, "tests", "fixtures", "godot_specs")
TRAVERSE = os.path.join(_EXAMPLES, "traverse.spec.json")

GODOT_EXE = find_godot_exe()
requires_godot = pytest.mark.skipif(GODOT_EXE is None, reason="Godot binary not present")


def _free_port() -> int:
    """Grab an ephemeral loopback port and release it (tests derive a base from it)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ====================================================================== #
# 1. Framed wire protocol + STALE deadline (pure python, always run)
# ====================================================================== #
def test_frame_roundtrip_over_socketpair():
    a, b = socket.socketpair()
    try:
        _send_frame(a, {"op": "act", "actions": ["run_right"], "n_ticks": 1})
        got = _recv_frame(b, time.monotonic() + 5.0)
        assert got == {"op": "act", "actions": ["run_right"], "n_ticks": 1}
        # A large-ish frame (multi-chunk) still reassembles.
        big = {"blob": "x" * 200000}
        _send_frame(b, big)
        assert _recv_frame(a, time.monotonic() + 5.0) == big
    finally:
        a.close()
        b.close()


def test_recv_frame_stale_raises_typed_error():
    # Peer never sends -> the per-op deadline must raise a typed STALE error, not hang.
    a, b = socket.socketpair()
    try:
        t0 = time.monotonic()
        with pytest.raises(GodotServeError) as ei:
            _recv_frame(a, time.monotonic() + 0.2)
        assert ei.value.kind == "stale"
        assert time.monotonic() - t0 < 5.0  # returned promptly, did not hang
    finally:
        a.close()
        b.close()


def test_recv_frame_closed_peer_is_typed_error():
    a, b = socket.socketpair()
    b.close()  # peer gone before any bytes
    try:
        with pytest.raises(GodotServeError) as ei:
            _recv_frame(a, time.monotonic() + 2.0)
        assert ei.value.kind in ("closed", "stale")
    finally:
        a.close()


def test_port_collision_raises_typed_error():
    # Occupy a port, then a serve env deriving the SAME port must fail the bind-check
    # BEFORE spawning anything (so this needs no Godot binary).
    port = _free_port()
    occupier = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupier.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupier.bind(("127.0.0.1", port))
    occupier.listen(1)
    try:
        with pytest.raises(GodotServeError) as ei:
            GodotServeEnv(TRAVERSE, port_base=port, port_offset=0)
        assert ei.value.kind == "port_in_use"
        assert str(port) in str(ei.value)
    finally:
        occupier.close()


def test_serve_argv_pins_fixed_fps():
    # The serve seam spawns through the SAME shared builder as the batch executor, so
    # --fixed-fps 60 is guaranteed here too (GODOT_DOCS_MINING.md section 3).
    from harness.verify.godot_exec import stepping_argv
    argv = stepping_argv("/opt/godot", "/proj", "res://runner.gd",
                         ["--serve", "--port=47000"])
    assert argv[argv.index("--fixed-fps") + 1] == "60"
    assert argv[argv.index("--") + 1:] == ["--serve", "--port=47000"]


def test_port_base_and_offset_compose():
    # port = base + offset; the collision message reports the derived port.
    base = _free_port()
    occupier = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupier.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupier.bind(("127.0.0.1", base + 3))
    occupier.listen(1)
    try:
        with pytest.raises(GodotServeError) as ei:
            GodotServeEnv(TRAVERSE, port_base=base, port_offset=3)
        assert ei.value.kind == "port_in_use"
        assert str(base + 3) in str(ei.value)
    finally:
        occupier.close()


# ====================================================================== #
# 2. Serve round-trip + determinism (skipped without the Godot binary)
# ====================================================================== #
@requires_godot
def test_serve_roundtrip_and_stable_obs():
    env = GodotServeEnv(TRAVERSE, port_base=_free_port())
    try:
        # Handshake fields flow from the spec's meta.
        assert env.actions == ["run_left", "run_right", "hop"]
        assert env.title == "Quarry Shelves"
        assert tuple(env.world_size) == (1400, 700)
        assert env.action_space.n == 3
        obs_dim = env.observation_space.shape[0]

        obs, info = env.reset(seed=0)
        assert obs.shape == (obs_dim,)          # obs length stable across reset
        assert obs.dtype == np.float32
        assert set(info["latched"]) == {
            "on_first_shelf", "past_spikes", "on_mid_shelf", "on_top_shelf", "at_beacon"}
        assert all(v is None for v in info["latched"].values())  # nothing latched at t0

        ri = env.actions.index("run_right")
        for _ in range(5):
            o, r, term, trunc, i = env.step(ri)
            assert o.shape == (obs_dim,)         # obs length stays fixed every step
            assert isinstance(r, float)
            assert set(i) == {"result", "tick", "latched", "n_latched", "success"}
    finally:
        env.close()


@requires_godot
def test_serve_same_seed_determinism_across_sessions():
    """Two independent serve sessions, same act sequence -> identical state vectors."""
    ri = None
    seqs = []
    for _ in range(2):
        env = GodotServeEnv(TRAVERSE, port_base=_free_port())
        try:
            if ri is None:
                ri = env.actions.index("run_right")
            env.reset(seed=0)
            vecs = []
            for _ in range(15):
                o, r, term, trunc, i = env.step(ri)
                vecs.append(o.copy())
                if term or trunc:
                    break
            seqs.append(vecs)
        finally:
            env.close()
    assert len(seqs[0]) == len(seqs[1])
    assert all(np.array_equal(a, b) for a, b in zip(*seqs)), "serve must be deterministic"


@requires_godot
def test_serve_terminated_vs_truncated_split():
    # Terminated: roll right into a spike (the failure predicate) -> done_term.
    env = GodotServeEnv(TRAVERSE, port_base=_free_port(), horizon=120)
    try:
        ri = env.actions.index("run_right")
        term = trunc = False
        info = None
        for _ in range(env.horizon):
            _o, _r, term, trunc, info = env.step(ri)
            if term or trunc:
                break
        assert term and not trunc
        assert info["result"] in ("failure", "error")
    finally:
        env.close()

    # Truncated: roll left into the wall, never terminates -> done_trunc at the horizon.
    env = GodotServeEnv(TRAVERSE, port_base=_free_port(), horizon=30)
    try:
        li = env.actions.index("run_left")
        term = trunc = False
        info = None
        for _ in range(env.horizon):
            _o, _r, term, trunc, info = env.step(li)
            if term or trunc:
                break
        assert trunc and not term
        assert info["tick"] == 30 and info["result"] is None
    finally:
        env.close()


@requires_godot
def test_serve_batch_parity_is_the_bridge():
    """A serve-recorded (seed, actions) pair MUST replay identically through the batch
    executor -- the RL certificate bridge. Record an action sequence to its terminal in
    serve, then replay the SAME actions through GodotExecutor.run_batch and require the
    terminal result, tick and latched checkpoints to agree."""
    env = GodotServeEnv(TRAVERSE, port_base=_free_port(), horizon=200)
    name2idx = {a: i for i, a in enumerate(env.actions)}
    plan = (["hop", "run_right", "run_right", "run_right"] * 20)
    applied: list[str] = []
    info = None
    try:
        env.reset(seed=0)
        for a in plan:
            applied.append(a)
            _o, _r, term, trunc, info = env.step(name2idx[a])
            if term or trunc:
                break
    finally:
        env.close()

    with open(TRAVERSE, "r", encoding="utf-8") as fh:
        src = fh.read()
    rec = GodotExecutor().run_batch(
        src, [{"seed": 0, "actions": applied}], max_ticks=len(applied))[0]

    assert info["result"] == rec["result"]
    assert info["tick"] == rec["ticks"]
    serve_latch = {k: v for k, v in info["latched"].items() if v is not None}
    batch_latch = {k: v for k, v in rec["checkpoints"].items() if v is not None}
    assert serve_latch == batch_latch


# ====================================================================== #
# 3. g3_prime learnability smoke over the serve env (needs Godot + sb3)
# ====================================================================== #
@requires_godot
def test_g3_prime_serve_sb3_smoke(monkeypatch):
    """End-to-end: g3_prime routes the .spec.json to GodotServeEnv, trains a tiny sb3
    PPO, evaluates, extracts+bridges any witness, and emits the standard result dict.

    Asserts the pipeline runs and the dict is well-formed; `bridge_ok in (None, True)`
    (NEVER False -- a False would be a broken serve/batch bridge). Traverse is not
    RL-solvable in a tiny budget, so no witness is expected here (bridge_ok is None);
    the serve/batch bridge itself is proven byte-for-byte by the parity test above.
    """
    pytest.importorskip("stable_baselines3")
    from harness.rl.certify import g3_prime

    # One Slurm-task-style base; g3_prime hands its vec/eval envs increasing offsets.
    monkeypatch.setenv("GIP_PORT_BASE", str(_free_port()))

    res = g3_prime(TRAVERSE, budget_steps=4000, trainer="sb3", seed=0,
                   n_eval=2, num_envs=1, num_steps=64, patience=999)

    for key in ("learnable", "steps_to_first_success", "checkpoints_curve",
                "final_success_rate", "rl_witness", "wall_clock_s", "trainer",
                "bridge_ok", "bridge_result", "throughput_sps", "trained_steps"):
        assert key in res, key
    assert res["trainer"] == "sb3"
    assert isinstance(res["learnable"], bool)
    assert isinstance(res["checkpoints_curve"], list)
    assert res["trained_steps"] >= 4000 - 64
    assert res["bridge_ok"] in (None, True)     # never a broken bridge
    if res["rl_witness"] is not None:
        assert res["bridge_ok"] is True         # any witness must replay to success


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
