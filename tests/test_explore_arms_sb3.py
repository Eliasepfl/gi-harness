"""SB3-lane integration smokes for the two exploration arms (warmstart + RND).

Gated on stable_baselines3 (present in the `godot-rl` env and the certifier image). No
Godot: a minimal duck-typed fake PlanckEnv + a trivial gym env stand in for the engine, so
these run offline and catch WIRING bugs (wrapper API, PPO compatibility, byte-identity)
before any in-image budget is spent. The real end-to-end lands in the 50k in-image smoke.

Pins:
  * warmstart via ``wrap_gym(..., warmstart=cur)``: reset Backplay-replays the drawn prefix;
    a fully-annealed (frac 0) curriculum is byte-identical to the plain wrapper reset;
  * RND via ``rnd.wrap_venv``: the intrinsic bonus is ADDED to the extrinsic reward, PPO
    trains through it, and the un-wrapped venv is byte-identical (rnd off == vanilla).
"""

from __future__ import annotations

import os
import random
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("stable_baselines3")
import gymnasium as gym  # noqa: E402
from gymnasium import spaces  # noqa: E402
from stable_baselines3.common.vec_env import DummyVecEnv  # noqa: E402

from harness.rl import env as E  # noqa: E402
from harness.rl import rnd as rnd_mod  # noqa: E402
from harness.rl.env import wrap_gym  # noqa: E402
from harness.rl.warmstart import WarmstartCurriculum  # noqa: E402


# ---- a minimal duck-typed PlanckEnv (deterministic ladder) ---------------- #
class FakePlanck:
    """The subset of the PlanckEnv surface wrap_gym reads: spaces, actions, horizon, and a
    deterministic reset/step so a replayed prefix lands in a fixed, checkable state."""

    def __init__(self):
        self.observation_space = E.Box(-E.OBS_CLIP, E.OBS_CLIP, (1,))
        self.action_space = E.Discrete(3)
        self.actions = ["up", "down", "noop"]
        self.horizon = 50
        self.chord_mode = False
        self.allow_idle = False
        self.oppose_pairs = []
        self._x = 0.0

    def reset(self, seed: int = 0):
        self._x = float(int(seed) % 3)
        return np.array([self._x], dtype=np.float32), {"n_latched": 0, "result": None}

    def step(self, idx: int):
        self._x += {0: 1.0, 1: -1.0, 2: 0.0}[int(idx)]
        info = {"n_latched": 0, "result": None, "success": False, "tick": 1}
        return np.array([self._x], dtype=np.float32), 0.0, False, False, info

    def close(self):
        pass


def test_wrap_gym_warmstart_replays_prefix():
    cur = WarmstartCurriculum(["up"] * 20, start_frac=0.9, band_frac=0.0)  # len == cap
    genv = wrap_gym(FakePlanck(), warmstart=cur, ws_seed=0)
    obs, info = genv.reset(seed=0)
    assert info["warmstart_prefix_len"] == cur.cap_len() == 18
    assert float(obs[0]) == 18.0                    # start 0, +18 ups replayed


def test_wrap_gym_warmstart_frac0_byte_identical():
    cur = WarmstartCurriculum(["up"] * 20, start_frac=0.9)
    cur.frac = 0.0                                  # fully annealed -> empty prefix
    ws = wrap_gym(FakePlanck(), warmstart=cur, ws_seed=0)
    van = wrap_gym(FakePlanck())
    o_ws, _ = ws.reset(seed=2)
    o_van, _ = van.reset(seed=2)
    assert np.array_equal(o_ws, o_van)              # identical to vanilla wrapper


# ---- RND over a real DummyVecEnv + PPO ------------------------------------ #
class TinyGym(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self):
        super().__init__()
        self.observation_space = spaces.Box(-1.0, 1.0, (3,), dtype=np.float32)
        self.action_space = spaces.Discrete(2)
        self._t = 0

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self._t = 0
        return self.observation_space.sample(), {}

    def step(self, action):
        self._t += 1
        obs = self.observation_space.sample()
        return obs, 1.0, self._t >= 10, False, {}   # extrinsic reward is a flat 1.0


def test_rnd_wrapper_adds_bonus_and_is_offable():
    base = DummyVecEnv([lambda: TinyGym() for _ in range(2)])
    base.reset()
    # Vanilla step: extrinsic reward is flat 1.0.
    base.step_async(np.array([0, 1]))
    _o, r_vanilla, _d, _i = base.step_wait()
    assert np.allclose(r_vanilla, 1.0)

    wrapped = rnd_mod.wrap_venv(base, rnd_mod.RNDModel(3, seed=0),
                                total_steps=1000, int_coef=0.5)
    wrapped.reset()
    wrapped.step_async(np.array([0, 1]))
    _o, r_rnd, _d, infos = wrapped.step_wait()
    assert np.all(r_rnd >= r_vanilla - 1e-6)        # bonus is non-negative on top of extrinsic
    assert np.any(r_rnd > 1.0 + 1e-6)               # some novelty bonus was actually added
    assert all("rnd_intrinsic" in i and "extrinsic_reward" in i for i in infos)
    assert all(abs(i["extrinsic_reward"] - 1.0) < 1e-6 for i in infos)  # extrinsic untouched


def test_rnd_wrapper_ppo_learns_through_it():
    from stable_baselines3 import PPO
    venv = rnd_mod.wrap_venv(DummyVecEnv([lambda: TinyGym() for _ in range(2)]),
                             rnd_mod.RNDModel(3, seed=0), total_steps=2048, int_coef=0.5)
    model = PPO("MlpPolicy", venv, n_steps=64, batch_size=32, n_epochs=1,
                policy_kwargs=dict(net_arch=[16]), seed=0, device="cpu")
    model.learn(total_timesteps=256)                # runs through the wrapper without error
    assert model.num_timesteps >= 256
