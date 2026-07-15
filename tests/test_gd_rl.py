"""GDScript RL lane — GodotServeEnv drives a `.gd` GameAPI game (through
``godotworld/serve_game.gd``) and ``g3_prime`` certifies/grades it via the sb3
trainer, with the RL witness bridging through ``GdExecutor.run_batch``.

The gdscript twin of ``tests/test_godot_serve.py`` (which exercises the
``.spec.json`` / ``runner.gd`` dialect). Two tiers:

* **Pure-python (always run):** ``detect_engine`` classifies the ``.gd`` fixture as
  ``gdscript``, and the ``.gd`` construction path raises the SAME typed port-collision
  error before any spawn (the listener binds first).

* **End-to-end (skipped when the Godot binary is absent):** GodotServeEnv loads and
  steps ``mini_collect.gd`` through ``serve_game.gd`` (routing: engine=gdscript ->
  serve host + scrubbed env); same-seed determinism across two independent serve
  sessions; a winning ``{seed, actions}`` witness replays to SUCCESS through the
  gdscript batch executor (the certificate bridge); and a tiny-budget parallel
  ``g3_prime`` sb3 run drives the whole learnability pipeline end-to-end.
"""
from __future__ import annotations

import os
import socket
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.rl.godot_env import GodotServeError, GodotServeEnv  # noqa: E402
from harness.verify.executors import find_godot_exe  # noqa: E402
from harness.verify.gameverify import detect_engine  # noqa: E402
from harness.verify.gd_exec import GdExecutor  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MINI = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "mini_collect.gd")

GODOT_EXE = find_godot_exe()
requires_godot = pytest.mark.skipif(GODOT_EXE is None, reason="Godot binary not present")

# A verified DETERMINISTIC winning rollout for mini_collect.gd at seed 0: steer the
# controlled body up to gem_a, then right/down to gem_b (collects both -> got_first@8,
# got_both@32). This is a concrete {seed, argmax-actions} pair — exactly the shape of
# the RL greedy witness — and it wins in BOTH the serve env and GdExecutor.run_batch.
WITNESS_SEED = 0
WITNESS_ACTIONS = ["up"] * 8 + ["right"] * 8 + ["down", "right"] * 8


def _free_port() -> int:
    """Grab an ephemeral loopback port and release it (tests derive a base from it)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ====================================================================== #
# 1. Routing (pure python, always run)
# ====================================================================== #
def test_gdscript_engine_detected():
    with open(MINI, "r", encoding="utf-8") as fh:
        src = fh.read()
    assert detect_engine(MINI, src) == "gdscript"


def test_gd_serve_port_collision_raises_typed_error():
    """The `.gd` construction path binds the listener FIRST, so a busy port surfaces a
    TYPED ``port_in_use`` before any Godot spawn (no binary needed)."""
    port = _free_port()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    try:
        with pytest.raises(GodotServeError) as ei:
            GodotServeEnv(MINI, port_base=port, port_offset=0)
        assert ei.value.kind == "port_in_use"
    finally:
        blocker.close()


# ====================================================================== #
# 2. Serve round-trip + determinism (skipped without the Godot binary)
# ====================================================================== #
@requires_godot
def test_gd_serve_env_loads_and_steps():
    """GodotServeEnv routes a `.gd` game to serve_game.gd (scrubbed child env) and
    exposes the PlanckEnv surface: discrete actions, a stable-length obs, typed info."""
    env = GodotServeEnv(MINI, port_base=_free_port())
    try:
        assert env.engine == "gdscript"
        assert env._host_rel == "res://serve_game.gd"
        assert env._init_key == "source" and env._scrub is True
        assert env.actions == ["up", "down", "left", "right"]
        assert env.action_space.n == 4
        obs_dim = env.observation_space.shape[0]

        obs, info = env.reset(seed=0)
        assert obs.shape == (obs_dim,)
        assert obs.dtype == np.float32
        assert set(info["latched"]) == {"got_first", "got_both"}
        assert all(v is None for v in info["latched"].values())

        ui = env.actions.index("up")
        for _ in range(5):
            o, r, term, trunc, i = env.step(ui)
            assert o.shape == (obs_dim,)
            assert isinstance(r, float)
            assert set(i) == {"result", "tick", "latched", "n_latched", "success"}
    finally:
        env.close()


@requires_godot
def test_gd_serve_same_seed_determinism_across_sessions():
    """Two independent serve sessions, same seed + action sequence -> identical obs
    vectors every step (the G1 drift gate / witness replay both rely on this)."""
    plan = WITNESS_ACTIONS[:15]
    seqs = []
    for _ in range(2):
        env = GodotServeEnv(MINI, port_base=_free_port())
        try:
            env.reset(seed=WITNESS_SEED)
            vecs = []
            for a in plan:
                o, r, term, trunc, i = env.step(env.actions.index(a))
                vecs.append(o.copy())
                if term or trunc:
                    break
            seqs.append(vecs)
        finally:
            env.close()
    assert len(seqs[0]) == len(seqs[1])
    assert all(np.array_equal(a, b) for a, b in zip(*seqs)), "serve must be deterministic"


@requires_godot
def test_gd_witness_replays_to_success_through_executor():
    """The certificate bridge for gdscript: a winning {seed, actions} witness recorded
    in the serve env MUST replay to SUCCESS through GdExecutor.run_batch, with the
    terminal result / tick / latched checkpoints agreeing byte-for-byte."""
    env = GodotServeEnv(MINI, port_base=_free_port(), horizon=200)
    info = None
    try:
        env.reset(seed=WITNESS_SEED)
        for a in WITNESS_ACTIONS:
            _o, _r, term, trunc, info = env.step(env.actions.index(a))
            if term or trunc:
                break
    finally:
        env.close()
    assert info is not None and info["result"] == "success", info

    with open(MINI, "r", encoding="utf-8") as fh:
        src = fh.read()
    ex = GdExecutor(port_base=_free_port())
    try:
        rec = ex.run_batch(
            src, [{"seed": WITNESS_SEED, "actions": WITNESS_ACTIONS}],
            max_ticks=len(WITNESS_ACTIONS))[0]
    finally:
        ex.close()

    assert rec["result"] == "success"
    assert rec["ticks"] == info["tick"]
    serve_latch = {k: v for k, v in info["latched"].items() if v is not None}
    batch_latch = {k: v for k, v in rec["checkpoints"].items() if v is not None}
    assert serve_latch == batch_latch


# ====================================================================== #
# 3. g3_prime learnability pipeline over the gdscript serve env (needs sb3)
# ====================================================================== #
@requires_godot
def test_g3_prime_gdscript_sb3_smoke(monkeypatch):
    """End-to-end: g3_prime routes the `.gd` game to GodotServeEnv, trains a tiny sb3
    PPO over a PARALLEL vec env (num_envs=2, disjoint port offsets off GIP_PORT_BASE),
    evaluates greedily/stochastically, extracts + bridges any witness through
    GdExecutor, and emits the standard result dict.

    Asserts the pipeline runs and the dict is well-formed; ``bridge_ok in (None, True)``
    (NEVER False — a False would be a broken serve/batch bridge). If a witness is found
    it MUST have bridged to success. The bridge itself is proven byte-for-byte by
    ``test_gd_witness_replays_to_success_through_executor`` above.
    """
    pytest.importorskip("stable_baselines3")
    from harness.rl.certify import g3_prime

    # One Slurm-task-style base; g3_prime hands its probe/vec/eval envs disjoint offsets.
    monkeypatch.setenv("GIP_PORT_BASE", str(_free_port()))

    res = g3_prime(MINI, budget_steps=4000, trainer="sb3", seed=0,
                   n_eval=2, num_envs=2, num_steps=64, patience=999)

    for key in ("learnable", "steps_to_first_success", "checkpoints_curve",
                "final_success_rate", "rl_witness", "wall_clock_s", "trainer",
                "stochastic_success_rate", "bridge_ok", "bridge_result",
                "throughput_sps", "trained_steps"):
        assert key in res, key
    assert res["trainer"] == "sb3"
    assert res["title"] and res["game_path"] == MINI
    assert isinstance(res["learnable"], bool)
    assert isinstance(res["checkpoints_curve"], list)
    assert res["trained_steps"] >= 4000 - 64 * 2
    assert res["bridge_ok"] in (None, True)          # never a broken bridge
    assert res["stochastic_success_rate"] is not None
    if res["rl_witness"] is not None:
        assert res["bridge_ok"] is True              # any witness must replay to success


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
