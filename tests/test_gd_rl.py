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
MINI_3D = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "mini_collect_3d.gd")
TUMBLE_3D = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "tumble_3d.gd")

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


# ====================================================================== #
# 4. TRUE-3D obs regression — the pos-unpack crash must be GONE
# ====================================================================== #
@requires_godot
def test_gd_serve_env_true_3d_loads_and_steps():
    """A true-3D `.gd` game (pos:[x,y,z]) loads through serve_game.gd and steps WITHOUT
    the `px, py = q.get('pos')` ValueError that crashed every 3D game before the
    dimension-aware obs. Pins env._dim == 3 and the 3D obs width."""
    from harness.rl.env import obs_dim_for
    env = GodotServeEnv(TUMBLE_3D, port_base=_free_port())
    try:
        assert env._dim == 3                             # pinned true-3D
        obs_dim = env.observation_space.shape[0]
        assert obs_dim == obs_dim_for(len(env._body_order), len(env._cp_keys), 3)
        obs, info = env.reset(seed=0)
        assert obs.shape == (obs_dim,) and obs.dtype == np.float32
        assert np.all(np.isfinite(obs))                  # no NaN leaks from 3D physics
        for _ in range(5):
            o, r, term, trunc, i = env.step(0)
            assert o.shape == (obs_dim,) and np.all(np.isfinite(o))
            if term or trunc:
                break
    finally:
        env.close()


@requires_godot
def test_g3_prime_true_3d_trains_without_obs_crash(monkeypatch):
    """The headline regression: g3_prime TRAINS a true-3D game end-to-end (the arm that
    used to crash at the obs builder before the first learning step). A tiny budget —
    we assert the pipeline RUNS and the dict is well-formed (not that it wins), which is
    exactly what "the crash arm now trains" means."""
    pytest.importorskip("stable_baselines3")
    from harness.rl.certify import g3_prime

    monkeypatch.setenv("GIP_PORT_BASE", str(_free_port()))
    res = g3_prime(MINI_3D, budget_steps=4000, trainer="sb3", seed=0,
                   n_eval=2, num_envs=2, num_steps=64, patience=999)

    for key in ("learnable", "steps_to_first_success", "checkpoints_curve",
                "final_success_rate", "rl_witness", "bridge_ok", "trained_steps"):
        assert key in res, key
    assert res["game_path"] == MINI_3D
    assert isinstance(res["learnable"], bool)
    assert res["trained_steps"] >= 4000 - 64 * 2
    assert res["bridge_ok"] in (None, True)              # never a broken bridge
    if res["rl_witness"] is not None:
        assert res["bridge_ok"] is True


# ====================================================================== #
# 5. Egocentric RAYCAST obs (opt-in) — in-image (direct_space_state.intersect_ray)
# ====================================================================== #
@requires_godot
def test_gd_rays_off_is_untouched_obs_width():
    """The default 'positions' profile carries NO ray tail — obs width is exactly the
    pre-rays width (the opt-in is truly zero-effect when not requested)."""
    from harness.rl.env import obs_dim_for
    env = GodotServeEnv(TUMBLE_3D, port_base=_free_port())      # default obs_profile
    try:
        assert env._obs_profile == "positions" and env._n_ray_floats == 0
        assert env.observation_space.shape[0] == \
            obs_dim_for(len(env._body_order), len(env._cp_keys), 3)
    finally:
        env.close()


@requires_godot
def test_gd_rays_3d_grid_casts_and_detects_geometry():
    """positions+rays on a true-3D game: the obs grows by the 25x5 depth-retina grid (×4
    with class bits), every value is finite, the ray tail is in [0,1], and AFTER a step
    rays HIT the enclosing canyon with the STATIC class bit set — the in-image proof that
    direct_space_state.intersect_ray + the class channel work (the feature hinges on this).
    The reset frame is intentionally all-clear (broadphase populated only after step 1)."""
    from harness.rl.env import obs_dim_for, rays_obs_width, n_rays_of, DEFAULT_RAYS
    rays = dict(DEFAULT_RAYS)
    rays["range"] = 200.0                                      # tumble_3d's canyon is small
    env = GodotServeEnv(TUMBLE_3D, port_base=_free_port(),
                        obs_profile="positions+rays", rays=rays)
    try:
        n_rays = n_rays_of(rays, 3)
        w = rays_obs_width(rays, 3)
        assert n_rays == 125 and w == 500                     # 25x5 rays × (dist + 3 class)
        base = obs_dim_for(len(env._body_order), len(env._cp_keys), 3)
        assert env.observation_space.shape[0] == base + w
        obs, _ = env.reset(seed=0)
        assert np.all(np.isfinite(obs))
        # reset frame: broadphase empty -> all-clear (distance 1.0, no class)
        reset_tail = obs[-w:]
        assert np.allclose(reset_tail.reshape(n_rays, 4)[:, 0], 1.0)
        # after a step the retina sees the canyon walls (STATIC class), distances < 1.0
        o, _r, _t, _tr, _i = env.step(0)
        tail = o[-w:].reshape(n_rays, 4)
        dists, cls = tail[:, 0], tail[:, 1:]
        assert dists.min() >= 0.0 and dists.max() <= 1.0
        assert dists.min() < 1.0, "no ray hit the canyon — intersect_ray broke"
        assert cls.max() <= 1.0 and cls.min() >= 0.0
        assert cls[:, 0].max() == 1.0, "canyon walls should read the STATIC class bit"
    finally:
        env.close()


@requires_godot
def test_gd_rays_determinism_byte_identical_across_sessions():
    """Two independent serve sessions with rays ON must produce BYTE-identical obs on the
    same seed+actions — the ray cast is read-only + deterministic, so twin-rollout parity
    holds WITH rays (the §3 determinism guarantee extended to the raycast tail)."""
    from harness.rl.env import DEFAULT_RAYS
    rays = dict(DEFAULT_RAYS); rays["range"] = 200.0
    seqs = []
    for _ in range(2):
        env = GodotServeEnv(TUMBLE_3D, port_base=_free_port(),
                            obs_profile="positions+rays", rays=rays)
        try:
            obs, _ = env.reset(seed=0)
            trail = [obs.copy()]
            for _ in range(6):
                o, _r, term, trunc, _i = env.step(0)
                trail.append(o.copy())
                if term or trunc:
                    break
            seqs.append(trail)
        finally:
            env.close()
    assert len(seqs[0]) == len(seqs[1])
    assert all(a.tobytes() == b.tobytes() for a, b in zip(*seqs)), \
        "rays broke twin-rollout byte-identity"


@requires_godot
def test_gd_pure_rays_profile_is_proprioception_plus_grid():
    """The pure 'rays' profile: obs = proprioception (7 in 3D) + cp one-hot + tick + the
    ray-float tail — NO global positions. Loads, steps finite, and stays deterministic."""
    from harness.rl.env import PROPRIO_3D, rays_obs_width, DEFAULT_RAYS
    rays = dict(DEFAULT_RAYS); rays["range"] = 200.0
    env = GodotServeEnv(TUMBLE_3D, port_base=_free_port(),
                        obs_profile="rays", rays=rays)
    try:
        w = rays_obs_width(rays, 3)
        want = PROPRIO_3D + len(env._cp_keys) + 1 + w
        assert env.observation_space.shape[0] == want
        obs, _ = env.reset(seed=0)
        assert obs.shape == (want,) and np.all(np.isfinite(obs))
        for _ in range(3):
            o, _r, term, trunc, _i = env.step(0)
            assert np.all(np.isfinite(o))
            if term or trunc:
                break
    finally:
        env.close()


@requires_godot
def test_gd_rays_class_bits_off_is_one_float_per_ray():
    """class_bits=False -> the tail is exactly n_rays floats (distance only)."""
    from harness.rl.env import obs_dim_for, rays_obs_width, n_rays_of, DEFAULT_RAYS
    rays = dict(DEFAULT_RAYS); rays["range"] = 200.0; rays["class_bits"] = False
    env = GodotServeEnv(TUMBLE_3D, port_base=_free_port(),
                        obs_profile="positions+rays", rays=rays)
    try:
        w = rays_obs_width(rays, 3)
        assert w == n_rays_of(rays, 3) == 125                 # 1 float/ray, no class
        base = obs_dim_for(len(env._body_order), len(env._cp_keys), 3)
        assert env.observation_space.shape[0] == base + w
    finally:
        env.close()


@requires_godot
def test_gd_rays_2d_planar_fan():
    """A 2D `.gd` game gets the planar fan (n rays), NOT the 3D grid — obs grows by the
    fan's ray-float width."""
    from harness.rl.env import obs_dim_for, rays_obs_width, n_rays_of, DEFAULT_RAYS
    rays = dict(DEFAULT_RAYS); rays["range"] = 400.0
    env = GodotServeEnv(MINI, port_base=_free_port(),
                        obs_profile="positions+rays", rays=rays)
    try:
        assert env._dim == 2
        assert n_rays_of(rays, 2) == DEFAULT_RAYS["n"] == 16
        w = rays_obs_width(rays, 2)                            # 16 rays × 4 = 64
        base = obs_dim_for(len(env._body_order), len(env._cp_keys), 2)
        assert env.observation_space.shape[0] == base + w
        obs, _ = env.reset(seed=0)
        assert np.all(np.isfinite(obs))
        assert obs[-w:].min() >= 0.0 and obs[-w:].max() <= 1.0
    finally:
        env.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
