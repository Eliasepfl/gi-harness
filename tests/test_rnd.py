"""Unit tests for RND intrinsic reward (``harness.rl.rnd``).

Uses torch (present in the offline `reve` env and the certifier image) but NOT
stable_baselines3 — the RNDModel + normalizer are engine/SB3-free, so they run offline. The
``VecEnvWrapper`` integration + the "disabled == byte-identical" trainer claim are pinned by
the in-image smoke (a run with rnd=None applies NO wrapper, so its graph is vanilla); here we
pin the model-level properties the task requires:
  * the predictor LEARNS -> intrinsic DECAYS on repeated observations;
  * a held-out (novel) observation keeps a HIGHER intrinsic than a trained one (the novelty
    signal is real, not a global collapse);
  * the intrinsic is BOUNDED and the coefficient anneals to its floor (invariant respect);
  * the running normalizer matches numpy, and the model is deterministic under its seed.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("torch")

from harness.rl.rnd import (  # noqa: E402
    INTRINSIC_CLIP,
    RNDModel,
    RunningMeanStd,
    coef_at,
)


# ====================================================================== #
# 1) Predictor learns -> intrinsic decays on repeated obs
# ====================================================================== #
def test_intrinsic_decays_on_repeated_obs():
    rng = np.random.default_rng(0)
    obs = rng.standard_normal((16, 8)).astype(np.float32)
    m = RNDModel(obs_dim=8, seed=0, lr=1e-2)
    before = float(m.intrinsic(obs).mean())
    for _ in range(200):
        m.update(obs)
    after = float(m.intrinsic(obs).mean())
    assert after < before * 0.5                 # error on the repeatedly-seen obs collapses
    assert after >= 0.0


def test_novel_obs_stays_more_intrinsic_than_trained():
    rng = np.random.default_rng(1)
    trained = rng.standard_normal((16, 8)).astype(np.float32)
    # A held-out region the predictor never sees (shifted far away).
    novel = (rng.standard_normal((16, 8)) + 12.0).astype(np.float32)
    m = RNDModel(obs_dim=8, seed=1, lr=1e-2)
    # Prime the obs normalizer on BOTH regions so whitening isn't what separates them, then
    # train the predictor ONLY on `trained`.
    m.update(np.concatenate([trained, novel], axis=0))
    for _ in range(300):
        m.update(trained)
    err_trained = float(m.intrinsic(trained).mean())
    err_novel = float(m.intrinsic(novel).mean())
    assert err_novel > err_trained              # novelty signal survives training


# ====================================================================== #
# 2) Boundedness + coefficient anneal (reward-invariant respect)
# ====================================================================== #
def test_normalized_intrinsic_is_bounded_and_nonneg():
    rng = np.random.default_rng(2)
    m = RNDModel(obs_dim=6, seed=2)
    for _ in range(10):
        obs = rng.standard_normal((32, 6)).astype(np.float32)
        norm = m.normalized_intrinsic(obs)
        assert norm.shape == (32,)
        assert np.all(norm >= 0.0) and np.all(norm <= INTRINSIC_CLIP)
        assert np.all(np.isfinite(norm))


def test_coef_anneals_to_floor():
    # Linear int_coef -> int_coef_final over total_steps; at/after the end it sits at the
    # floor (default 0) -> the extrinsic terminal strictly dominates by run end.
    assert coef_at(0, 1000, int_coef=0.5, int_coef_final=0.0) == pytest.approx(0.5)
    assert coef_at(500, 1000, int_coef=0.5, int_coef_final=0.0) == pytest.approx(0.25)
    assert coef_at(1000, 1000, int_coef=0.5, int_coef_final=0.0) == pytest.approx(0.0)
    assert coef_at(5000, 1000, int_coef=0.5, int_coef_final=0.0) == pytest.approx(0.0)


# ====================================================================== #
# 3) Determinism + normalizer correctness
# ====================================================================== #
def test_model_is_deterministic_under_seed():
    rng = np.random.default_rng(3)
    obs = rng.standard_normal((10, 5)).astype(np.float32)
    a = RNDModel(obs_dim=5, seed=42).intrinsic(obs)
    b = RNDModel(obs_dim=5, seed=42).intrinsic(obs)
    assert np.allclose(a, b)
    # A different seed gives a different target -> a different novelty landscape.
    c = RNDModel(obs_dim=5, seed=43).intrinsic(obs)
    assert not np.allclose(a, c)


def test_running_mean_std_matches_numpy():
    rng = np.random.default_rng(4)
    data = rng.standard_normal((500, 3))
    rms = RunningMeanStd(shape=(3,))
    for i in range(0, 500, 50):
        rms.update(data[i:i + 50])
    assert np.allclose(rms.mean, data.mean(axis=0), atol=1e-6)
    assert np.allclose(rms.var, data.var(axis=0), atol=1e-6)


def test_scalar_running_mean_std():
    rng = np.random.default_rng(5)
    data = rng.standard_normal(400)
    rms = RunningMeanStd(shape=())
    for i in range(0, 400, 40):
        rms.update(data[i:i + 40])
    assert np.isclose(rms.mean, data.mean(), atol=1e-6)
    assert np.isclose(rms.var, data.var(), atol=1e-6)
