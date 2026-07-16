"""GodotShardVecEnv — M INDEPENDENT GodotBatchVecEnv shards stepped CONCURRENTLY so
ONE learner saturates MANY cores (Elias, 2026-07-16: "32 cores per run").

Two tiers:

* **Pure-python (needs only sb3+gymnasium, always run in-image):** a FAKE shard factory
  exercises the COMPOSITION contract with no Godot — port/seed derivation per shard, the
  M*K reset/step concatenation, the fan-out slicing (slot g's action reaches shard g//K
  instance g%K), the per-slot fixed-seed scheme, reseed, close-all, and the advisory
  ``plan_num_shards`` helper. This is the fast determinism/wiring pin.

* **End-to-end (skipped without the Godot binary):**
  - M==1 is BYTE-IDENTICAL to a bare GodotBatchVecEnv (the regression pin);
  - same (M,K,base_seed) -> byte-identical rollouts run-to-run (determinism guarantee);
  - shard i instance j (of M=2,K=3) matches a lone single-instance GodotServeEnv at seed
    i*K+j (the per-slot scheme extends across shards, worlds do not interfere);
  - g3_prime drives a num_shards=2 sharded train end-to-end and emits the standard dict.
"""
from __future__ import annotations

import importlib.util
import os
import socket
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

requires_sb3 = pytest.mark.skipif(
    importlib.util.find_spec("stable_baselines3") is None
    or importlib.util.find_spec("gymnasium") is None,
    reason="stable_baselines3 / gymnasium not present")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MINI = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "mini_collect.gd")

from harness.verify.executors import find_godot_exe  # noqa: E402

GODOT_EXE = find_godot_exe()
requires_godot_sb3 = pytest.mark.skipif(
    GODOT_EXE is None or importlib.util.find_spec("stable_baselines3") is None,
    reason="Godot binary and/or stable_baselines3 not present")

# A verified DETERMINISTIC winning rollout for mini_collect.gd (from tests/test_gd_rl.py).
WITNESS_ACTIONS = ["up"] * 8 + ["right"] * 8 + ["down", "right"] * 8


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ====================================================================== #
# Fake shard: mimics GodotBatchVecEnv's surface WITHOUT Godot. Its "game" is a
# per-instance counter seeded base_seed+j; the action bumps the counter so fan-out
# ordering is observable in the obs.
# ====================================================================== #
def _fake_shard_factory(records):
    """Return a factory that appends each constructed fake shard to ``records`` (so a
    test can inspect the per-shard port_offset / seed the shard env derived)."""
    from gymnasium import spaces

    class _FakeShard:
        def __init__(self, game_path, n_instances, *, port_base, port_offset, seed,
                     exe=None, project=None, horizon=100, timeout_s=60.0,
                     connect_timeout_s=60.0):
            self.game_path = game_path
            self.num_envs = int(n_instances)
            self._base_seed = int(seed)
            self.port_base = int(port_base)
            self.port_offset = int(port_offset)
            self.horizon = int(horizon)
            self.actions = ["a", "b", "c"]
            self.title = "fake"
            self.world_size = (100, 100)
            self.observation_space = spaces.Box(low=-1e9, high=1e9, shape=(2,),
                                                dtype=np.float32)
            self.action_space = spaces.Discrete(3)
            self._tick = [0] * self.num_envs
            self._pending = None
            self.last_actions = None
            self.closed = False
            records.append(self)

        def _obs(self):
            o = np.zeros((self.num_envs, 2), dtype=np.float32)
            for j in range(self.num_envs):
                o[j, 0] = self._base_seed + j     # per-instance seed (== base+i*K+j global)
                o[j, 1] = self._tick[j]
            return o

        def reset(self):
            self._tick = [0] * self.num_envs
            return self._obs()

        def step_async(self, actions):
            self._pending = np.asarray(actions).reshape(-1)
            self.last_actions = self._pending.copy()

        def step_wait(self):
            for j in range(self.num_envs):
                self._tick[j] += 1 + int(self._pending[j])   # action perturbs the counter
            rewards = np.array([float(self._base_seed + j) for j in range(self.num_envs)],
                               dtype=np.float32)
            dones = np.zeros(self.num_envs, dtype=bool)
            infos = [{"shard_seed": self._base_seed, "instance": j}
                     for j in range(self.num_envs)]
            return self._obs(), rewards, dones, infos

        def seed(self, seed=None):
            if seed is not None:
                self._base_seed = int(seed)
            return [self._base_seed + j for j in range(self.num_envs)]

        def close(self):
            self.closed = True

    return _FakeShard


def _make_fake_shard_env(num_shards, num_envs, *, base_seed=0, port_base=50000,
                         port_offset_base=0, port_stride=None):
    from harness.rl.godot_shard_env import GodotShardVecEnv, PORT_STRIDE
    records = []
    kw = dict(base_seed=base_seed, port_base=port_base,
              port_offset_base=port_offset_base,
              shard_factory=_fake_shard_factory(records))
    if port_stride is not None:
        kw["port_stride"] = port_stride
    env = GodotShardVecEnv(MINI, num_shards, num_envs, **kw)
    return env, records, PORT_STRIDE


# ====================================================================== #
# 1. Advisory auto-sizing helper (pure)
# ====================================================================== #
@requires_sb3
def test_plan_num_shards_formula():
    from harness.rl.godot_shard_env import plan_num_shards
    # (cpus-2)//cores_per_shard caps the request; reserves 2 cores.
    assert plan_num_shards(4, cpus=32, cores_per_shard=8) == 3    # (30)//8 = 3
    assert plan_num_shards(4, cpus=34, cores_per_shard=8) == 4    # (32)//8 = 4, req met
    assert plan_num_shards(2, cpus=34, cores_per_shard=8) == 2    # request below the cap
    assert plan_num_shards(4, cpus=8, cores_per_shard=8) == 1     # (6)//8 = 0 -> floor 1
    assert plan_num_shards(4, cpus=4, cores_per_shard=2) == 1     # (2)//2 = 1


@requires_sb3
def test_core_slices_disjoint_and_cover():
    """The affinity plan (the M=4 collapse fix): split the allocated cores into M DISJOINT
    contiguous slices; return None when there are fewer cores than shards."""
    from harness.rl.godot_shard_env import _core_slices
    sl = _core_slices(list(range(32)), 4)
    assert [sorted(s) for s in sl] == [list(range(0, 8)), list(range(8, 16)),
                                       list(range(16, 24)), list(range(24, 32))]
    # disjoint + full cover
    union = set().union(*sl)
    assert union == set(range(32)) and sum(len(s) for s in sl) == 32
    # last slice takes the remainder (30 cores / 4 -> 7,7,7,9)
    sl2 = _core_slices(list(range(30)), 4)
    assert [len(s) for s in sl2] == [7, 7, 7, 9]
    assert set().union(*sl2) == set(range(30))
    # fewer cores than shards -> cannot pin disjointly -> None (caller runs unpinned)
    assert _core_slices([0, 1], 4) is None


# ====================================================================== #
# 2. Bad counts rejected (pure)
# ====================================================================== #
@requires_sb3
def test_shard_rejects_bad_counts():
    from harness.rl.godot_shard_env import GodotShardVecEnv
    with pytest.raises(ValueError):
        GodotShardVecEnv(MINI, 0, 4, shard_factory=_fake_shard_factory([]))
    with pytest.raises(ValueError):
        GodotShardVecEnv(MINI, 2, 0, shard_factory=_fake_shard_factory([]))


# ====================================================================== #
# 3. Construction: per-shard ports (i*STRIDE) + seeds (base + i*K), M*K envs
# ====================================================================== #
@requires_sb3
def test_shard_construction_ports_and_seeds():
    M, K, base = 3, 4, 10
    env, shards, STRIDE = _make_fake_shard_env(M, K, base_seed=base, port_base=51000)
    try:
        assert env.num_envs == M * K
        assert len(shards) == M
        for i, sh in enumerate(shards):
            assert sh.port_base == 51000
            assert sh.port_offset == i * STRIDE, "shard i binds i*PORT_STRIDE"
            assert sh._base_seed == base + i * K, "shard i seeded base + i*K"
            assert sh.num_envs == K
    finally:
        env.close()


# ====================================================================== #
# 4. reset: concatenated obs realise the per-slot fixed-seed scheme
# ====================================================================== #
@requires_sb3
def test_shard_reset_concat_seed_scheme():
    M, K, base = 3, 4, 100
    env, _shards, _ = _make_fake_shard_env(M, K, base_seed=base)
    try:
        obs = env.reset()
        assert obs.shape == (M * K, 2)
        # global slot g -> seed base + g (shard g//K instance g%K, seed base+i*K+j).
        assert np.array_equal(obs[:, 0], np.array([base + g for g in range(M * K)],
                                                  dtype=np.float32))
        assert np.array_equal(obs[:, 1], np.zeros(M * K, dtype=np.float32))
    finally:
        env.close()


# ====================================================================== #
# 5. step fan-out: slot g's action reaches shard g//K instance g%K
# ====================================================================== #
@requires_sb3
def test_shard_step_fanout_ordering():
    M, K = 3, 4
    env, shards, _ = _make_fake_shard_env(M, K, base_seed=0)
    try:
        env.reset()
        actions = np.array([g % 3 for g in range(M * K)])   # distinct-ish per slot
        obs, rewards, dones, infos = env.step(actions)
        # fake tick after one step == 1 + action; so obs[:,1] pins the fan-out ordering.
        assert np.array_equal(obs[:, 1],
                              np.array([1 + (g % 3) for g in range(M * K)], dtype=np.float32))
        # each shard received exactly its contiguous K-slice, in order.
        for i, sh in enumerate(shards):
            expect = actions[i * K:(i + 1) * K]
            assert np.array_equal(sh.last_actions, expect)
        assert not dones.any()
        assert len(infos) == M * K
        # infos concatenated in shard order (instance index cycles 0..K-1 per shard).
        assert [inf["instance"] for inf in infos] == [j for _ in range(M) for j in range(K)]
    finally:
        env.close()


# ====================================================================== #
# 6. determinism at the composition level: same inputs -> byte-identical
# ====================================================================== #
@requires_sb3
def test_shard_composition_determinism():
    M, K, base = 2, 3, 7

    def roll():
        env, _sh, _ = _make_fake_shard_env(M, K, base_seed=base)
        traj = [env.reset().copy()]
        for t in range(5):
            a = np.array([(g + t) % 3 for g in range(M * K)])
            obs, _r, _d, _i = env.step(a)
            traj.append(obs.copy())
        env.close()
        return traj

    a, b = roll(), roll()
    for t, (x, y) in enumerate(zip(a, b)):
        assert np.array_equal(x, y), f"non-deterministic at step {t}"


# ====================================================================== #
# 7. M==1 shard env == a lone shard (composition-level pin; Godot byte pin below)
# ====================================================================== #
@requires_sb3
def test_shard_m1_matches_lone_shard_composition():
    K, base = 4, 3
    recs_env = []
    from harness.rl.godot_shard_env import GodotShardVecEnv
    env = GodotShardVecEnv(MINI, 1, K, base_seed=base, port_base=52000,
                           shard_factory=_fake_shard_factory(recs_env))
    lone = _fake_shard_factory([])(MINI, K, port_base=52000, port_offset=0, seed=base)
    try:
        assert env.num_envs == K
        eo, lo = env.reset(), lone.reset()
        assert np.array_equal(eo, lo)
        for t in range(4):
            a = np.array([(g + t) % 3 for g in range(K)])
            eobs, er, ed, _ = env.step(a)
            lone.step_async(a)
            lobs, lr, ld, _ = lone.step_wait()
            assert np.array_equal(eobs, lobs)
            assert np.array_equal(er, lr)
            assert np.array_equal(ed, ld)
    finally:
        env.close()
        lone.close()


# ====================================================================== #
# 8. reseed propagates base + i*K; close() closes every shard
# ====================================================================== #
@requires_sb3
def test_shard_seed_and_close():
    M, K = 3, 2
    env, shards, _ = _make_fake_shard_env(M, K, base_seed=0)
    got = env.seed(20)
    assert got == [20 + g for g in range(M * K)]
    for i, sh in enumerate(shards):
        assert sh._base_seed == 20 + i * K
    env.close()
    assert all(sh.closed for sh in shards)


# ====================================================================== #
# 9. END-TO-END: M==1 is BYTE-IDENTICAL to a bare GodotBatchVecEnv (regression pin)
# ====================================================================== #
@requires_godot_sb3
def test_shard_m1_byte_identical_to_bare_batch():
    from harness.rl.godot_shard_env import GodotShardVecEnv
    from harness.rl.godot_vec_env import GodotBatchVecEnv
    K, T = 4, 12
    up = 0

    shard = GodotShardVecEnv(MINI, 1, K, base_seed=0, port_base=_free_port())
    shard_traj = []
    try:
        shard_traj.append(shard.reset().copy())
        for _ in range(T):
            obs, r, d, _ = shard.step(np.array([up] * K))
            assert not d.any()
            shard_traj.append((obs.copy(), r.copy(), d.copy()))
    finally:
        shard.close()

    bare = GodotBatchVecEnv(MINI, K, port_base=_free_port(), seed=0)
    bare_traj = []
    try:
        bare_traj.append(bare.reset().copy())
        for _ in range(T):
            obs, r, d, _ = bare.step(np.array([up] * K))
            bare_traj.append((obs.copy(), r.copy(), d.copy()))
    finally:
        bare.close()

    assert np.array_equal(shard_traj[0], bare_traj[0]), "reset obs must match bare batch"
    for t in range(1, T + 1):
        so, sr, sd = shard_traj[t]
        bo, br, bd = bare_traj[t]
        assert np.array_equal(so, bo), f"obs diverged at step {t}"
        assert np.array_equal(sr, br), f"reward diverged at step {t}"
        assert np.array_equal(sd, bd), f"done diverged at step {t}"


# ====================================================================== #
# 10. END-TO-END: same (M,K,base_seed) -> byte-identical rollouts run-to-run
# ====================================================================== #
@requires_godot_sb3
def test_shard_determinism_run_to_run():
    from harness.rl.godot_shard_env import GodotShardVecEnv
    M, K, T = 2, 3, 12
    plan = [(0 if t % 2 else 3) for t in range(T)]   # alternate up/right, no early done

    def roll():
        env = GodotShardVecEnv(MINI, M, K, base_seed=0, port_base=_free_port())
        traj = [env.reset().copy()]
        try:
            for a in plan:
                obs, r, d, _ = env.step(np.array([a] * (M * K)))
                assert not d.any()
                traj.append((obs.copy(), r.copy()))
        finally:
            env.close()
        return traj

    a, b = roll(), roll()
    assert np.array_equal(a[0], b[0])
    for t in range(1, T + 1):
        assert np.array_equal(a[t][0], b[t][0]), f"obs non-deterministic at step {t}"
        assert np.array_equal(a[t][1], b[t][1]), f"reward non-deterministic at step {t}"


# ====================================================================== #
# 11. END-TO-END: shard i instance j == lone single-instance at seed i*K+j
# ====================================================================== #
@requires_godot_sb3
def test_shard_instance_matches_single_instance_seed():
    from harness.rl.godot_env import GodotServeEnv
    from harness.rl.godot_shard_env import GodotShardVecEnv
    M, K, T = 2, 3, 12
    up = 0

    env = GodotShardVecEnv(MINI, M, K, base_seed=0, port_base=_free_port())
    traj = [[] for _ in range(M * K)]
    try:
        obs = env.reset()
        for g in range(M * K):
            traj[g].append(obs[g].copy())
        for _ in range(T):
            obs, _r, d, _ = env.step(np.array([up] * (M * K)))
            assert not d.any()
            for g in range(M * K):
                traj[g].append(obs[g].copy())
    finally:
        env.close()

    for g in range(M * K):   # global slot g <-> single-instance seed g (== i*K+j)
        single = GodotServeEnv(MINI, port_base=_free_port())
        ref = []
        try:
            o, _ = single.reset(seed=g)
            ref.append(o.copy())
            for _ in range(T):
                o, _r, term, trunc, _ = single.step(up)
                assert not (term or trunc)
                ref.append(o.copy())
        finally:
            single.close()
        for t, (a, b) in enumerate(zip(traj[g], ref)):
            assert np.array_equal(a, b), f"slot {g} diverged from single seed {g} at step {t}"


# ====================================================================== #
# 12. END-TO-END: g3_prime drives a num_shards=2 sharded train
# ====================================================================== #
@requires_godot_sb3
def test_g3_prime_gdscript_sharded(monkeypatch):
    from harness.rl.certify import g3_prime

    monkeypatch.setenv("GIP_PORT_BASE", str(_free_port()))
    res = g3_prime(MINI, budget_steps=4000, trainer="sb3", seed=0,
                   n_eval=2, num_envs=4, num_shards=2, num_steps=64, patience=999)

    for key in ("learnable", "checkpoints_curve", "final_success_rate", "rl_witness",
                "bridge_ok", "throughput_sps", "trained_steps"):
        assert key in res, key
    assert res["trainer"] == "sb3"
    # 2 shards x 4 envs = 8 logical envs; the rollout advances 8 per vec-step.
    assert res["trained_steps"] >= 4000 - 64 * 8
    assert res["bridge_ok"] in (None, True)
    if res["rl_witness"] is not None:
        assert res["bridge_ok"] is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-s"]))
