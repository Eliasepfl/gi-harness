"""RND — Random Network Distillation intrinsic reward (the ANTI-CAMPING arm).

Burda, Edwards, Storkey, Klimov (2018), "Exploration by Random Network Distillation"
(arXiv:1810.12894). A FIXED randomly-initialized *target* network maps each observation
to a feature vector; a *predictor* network is trained to regress that target. The
prediction error is HIGH on novel observations (the predictor has not been trained near
them) and LOW on familiar ones, so it is a state-novelty bonus that needs no density
model, no counts, and no extra episode bookkeeping — a single forward pass per step over
the same MlpPolicy observations PPO already collects.

WHY this arm. Vanilla PPO on the farm's sparse-terminal games collapses to the greedy-0
policy: with no gradient toward a win it never stumbles into, it camps. RND pays the agent
to VISIT states its predictor cannot yet explain, pushing it off the do-nothing basin and
out along the state manifold until it trips the terminal — a demonstration-free complement
to the witness-warmstart arm.

REWARD INVARIANTS (harness/rl/env.py §REWARD). The extrinsic reward has four
terminal-dominance invariants (success payoff >= 5.0 dwarfs the <= 1.0 shaping mass;
earlier success > later; any success > any no-success; failure < timeout). The intrinsic
bonus is EXPLORATION-ONLY and must not invert them at convergence. It respects them by
construction, LOOSELY during training and EXACTLY in the limit:
  * it is added at the VecEnv boundary (a wrapper), never inside ``step_reward`` — the
    unshaped ``success`` certificate and the extrinsic reward function are byte-identical;
  * it is BOUNDED — normalized by a running std then clipped to ``[0, clip]``;
  * it DECAYS two ways: (1) RND's own mechanism — as the predictor learns, prediction
    error -> 0 on every state the policy actually revisits, so a converged policy earns
    ~0 intrinsic; (2) the coefficient anneals linearly ``int_coef -> int_coef_final``
    (default 0) over training, a belt-and-suspenders guarantee that the terminal payoff
    strictly dominates by end-of-training even on residual OOD novelty.
Thus at convergence the intrinsic term vanishes and the pure extrinsic invariants hold.

CLEAN + OPT-IN. Nothing here is wired on by default. :func:`wrap_venv` composes an SB3
``VecEnvWrapper`` around the training venv (all three lanes converge on one venv object),
so when RND is off no wrapper is applied and the training graph is byte-identical to
vanilla. No monkeypatching: the trainer inserts the wrapper as an explicit dependency.

Torch is imported at module top (present in the certifier image and the offline `reve`
env); ``stable_baselines3`` is imported LAZILY inside :func:`wrap_venv` so this module
imports for the pure RNDModel unit tests without SB3.
"""

from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn

# --- Defaults ([eng.] = calibrated engineering choice) -------------------- #
FEAT_DIM = 64            # target/predictor output feature width [eng.]
HIDDEN = 128             # hidden width of both MLPs [eng.]
PREDICTOR_LR = 1e-3      # predictor Adam LR [eng.]
INT_COEF = 0.5           # starting intrinsic coefficient (fixed-small; annealed below) [eng.]
INT_COEF_FINAL = 0.0     # end coefficient — terminal payoff strictly dominates by run end [eng.]
INTRINSIC_CLIP = 5.0     # clip normalized intrinsic into [0, clip] (bound outliers) [eng.]
OBS_CLIP = 5.0           # clip whitened observations into [-clip, clip] (Burda et al.) [eng.]
RMS_EPS = 1e-8


class RunningMeanStd:
    """Numpy running mean / variance (Welford's parallel form, Chan et al.). Used to
    whiten observations before the RND nets and to normalize the intrinsic reward by its
    running std — both as in Burda et al. 2018. ``shape=()`` tracks a scalar stream."""

    def __init__(self, shape=()):
        self.mean = np.zeros(shape, dtype=np.float64)
        self.var = np.ones(shape, dtype=np.float64)
        self.count = float(RMS_EPS)

    def update(self, x: np.ndarray) -> None:
        x = np.asarray(x, dtype=np.float64)
        if x.ndim == (1 if self.mean.ndim else 0):
            x = x.reshape((-1,) + self.mean.shape)
        batch_mean = x.mean(axis=0)
        batch_var = x.var(axis=0)
        batch_count = x.shape[0]
        self._update_from_moments(batch_mean, batch_var, batch_count)

    def _update_from_moments(self, batch_mean, batch_var, batch_count) -> None:
        delta = batch_mean - self.mean
        tot = self.count + batch_count
        self.mean = self.mean + delta * batch_count / tot
        m_a = self.var * self.count
        m_b = batch_var * batch_count
        m2 = m_a + m_b + np.square(delta) * self.count * batch_count / tot
        self.var = m2 / tot
        self.count = tot

    @property
    def std(self) -> np.ndarray:
        return np.sqrt(self.var + RMS_EPS)


def _mlp(in_dim: int, out_dim: int, hidden: int, *, depth: int) -> nn.Sequential:
    layers: list = []
    d = in_dim
    for _ in range(depth):
        layers += [nn.Linear(d, hidden), nn.ReLU()]
        d = hidden
    layers += [nn.Linear(d, out_dim)]
    return nn.Sequential(*layers)


class RNDModel:
    """Fixed random target + trained predictor over observations.

    ``intrinsic(obs)`` returns the per-sample prediction error (mean-squared over the
    feature dim) with NO gradient — the raw novelty signal. ``update(obs)`` takes one Adam
    step regressing the predictor onto the (frozen) target on ``obs`` and returns the loss;
    repeated updates on the same observations drive their intrinsic toward 0 (the decay the
    unit test pins). ``normalized_intrinsic(obs)`` divides the raw error by its running std
    and clips to ``[0, INTRINSIC_CLIP]`` — the bounded bonus the wrapper adds to reward.

    Observations are whitened by a running mean/std (clipped to ``+/-OBS_CLIP``) before both
    nets, as in Burda et al.; the target has a DEEPER trunk than the predictor (the standard
    asymmetry that keeps the target hard to fit). Deterministic under ``seed``."""

    def __init__(self, obs_dim: int, *, feat_dim: int = FEAT_DIM, hidden: int = HIDDEN,
                 lr: float = PREDICTOR_LR, device: str = "cpu", seed: int = 0):
        self.obs_dim = int(obs_dim)
        self.device = torch.device(device)
        gen = torch.Generator().manual_seed(int(seed))
        with torch.no_grad():
            torch.manual_seed(int(seed))
            self.target = _mlp(obs_dim, feat_dim, hidden, depth=2).to(self.device)
            torch.manual_seed(int(seed) + 1)
            self.predictor = _mlp(obs_dim, feat_dim, hidden, depth=1).to(self.device)
        for p in self.target.parameters():
            p.requires_grad_(False)
        self.target.eval()
        self.optim = torch.optim.Adam(self.predictor.parameters(), lr=lr)
        self.obs_rms = RunningMeanStd(shape=(self.obs_dim,))
        self.int_rms = RunningMeanStd(shape=())
        self._gen = gen

    # -- obs whitening ----------------------------------------------------- #
    def _whiten(self, obs: np.ndarray, *, update: bool) -> torch.Tensor:
        arr = np.asarray(obs, dtype=np.float64).reshape(-1, self.obs_dim)
        if update:
            self.obs_rms.update(arr)
        w = (arr - self.obs_rms.mean) / self.obs_rms.std
        w = np.clip(w, -OBS_CLIP, OBS_CLIP)
        return torch.as_tensor(w, dtype=torch.float32, device=self.device)

    # -- novelty ----------------------------------------------------------- #
    def _raw_error(self, obs_t: torch.Tensor) -> torch.Tensor:
        """Per-sample MSE between predictor and (frozen) target features."""
        tgt = self.target(obs_t)
        pred = self.predictor(obs_t)
        return ((pred - tgt) ** 2).mean(dim=1)

    def intrinsic(self, obs: np.ndarray) -> np.ndarray:
        """Raw per-sample prediction error (no grad, no normalization)."""
        with torch.no_grad():
            obs_t = self._whiten(obs, update=False)
            err = self._raw_error(obs_t)
        return err.detach().cpu().numpy().reshape(-1)

    def normalized_intrinsic(self, obs: np.ndarray) -> np.ndarray:
        """Bounded intrinsic bonus: raw error divided by its running std then clipped to
        ``[0, INTRINSIC_CLIP]``. Updates the intrinsic running std (so the scale adapts to
        the game's own novelty range). This is what the wrapper scales by the coefficient
        and adds to the extrinsic reward."""
        err = self.intrinsic(obs)
        self.int_rms.update(err)
        norm = err / float(self.int_rms.std)
        return np.clip(norm, 0.0, INTRINSIC_CLIP).astype(np.float32)

    def update(self, obs: np.ndarray) -> float:
        """One Adam step regressing the predictor onto the target on ``obs`` (also updates
        the obs running mean/std). Returns the scalar loss. Repeated calls on the same
        observations drive their intrinsic toward 0."""
        obs_t = self._whiten(obs, update=True)
        tgt = self.target(obs_t).detach()
        pred = self.predictor(obs_t)
        loss = ((pred - tgt) ** 2).mean()
        self.optim.zero_grad()
        loss.backward()
        self.optim.step()
        return float(loss.detach().cpu())


def coef_at(step: int, total_steps: int, *, int_coef: float = INT_COEF,
            int_coef_final: float = INT_COEF_FINAL) -> float:
    """Linear coefficient anneal ``int_coef -> int_coef_final`` over ``total_steps`` env
    steps, clamped. At ``step >= total_steps`` it sits at ``int_coef_final`` (default 0),
    the belt-and-suspenders guarantee the extrinsic terminal dominates by run end."""
    if total_steps <= 0:
        return float(int_coef_final)
    frac = min(1.0, max(0.0, float(step) / float(total_steps)))
    return float(int_coef) + (float(int_coef_final) - float(int_coef)) * frac


def wrap_venv(venv, rnd_model: RNDModel, *, total_steps: int, int_coef: float = INT_COEF,
              int_coef_final: float = INT_COEF_FINAL, update_predictor: bool = True):
    """Compose an SB3 ``VecEnvWrapper`` that adds the RND intrinsic bonus to the reward of
    every ``step_wait`` (computed from the RETURNED next-state obs), trains the predictor
    on those obs, and anneals the coefficient toward ``int_coef_final`` over ``total_steps``.

    OPT-IN only: the trainer calls this exclusively when RND is enabled, so the un-wrapped
    path is byte-identical to vanilla PPO. ``stable_baselines3`` is imported here (lazily)
    so :class:`RNDModel` unit-tests without SB3. The wrapper exposes ``rnd`` (the model) and
    ``mean_intrinsic`` (a rolling scalar) for the trainer's logging / diagnostics."""
    from stable_baselines3.common.vec_env import VecEnvWrapper

    class _RNDVecEnv(VecEnvWrapper):
        def __init__(self, wrapped):
            super().__init__(wrapped)
            self.rnd = rnd_model
            self._t = 0
            self._total = int(total_steps)
            self._int_coef = float(int_coef)
            self._int_coef_final = float(int_coef_final)
            self._update_predictor = bool(update_predictor)
            self.mean_intrinsic = 0.0
            self.last_coef = float(int_coef)

        def reset(self):
            return self.venv.reset()

        def step_async(self, actions):
            self.venv.step_async(actions)

        def step_wait(self):
            obs, rewards, dones, infos = self.venv.step_wait()
            intr = self.rnd.normalized_intrinsic(obs)          # bounded per-sample novelty
            if self._update_predictor:
                self.rnd.update(obs)                           # predictor learns -> intrinsic decays
            coef = coef_at(self._t, self._total, int_coef=self._int_coef,
                           int_coef_final=self._int_coef_final)
            self.last_coef = coef
            self._t += self.num_envs
            bonus = coef * intr
            new_rewards = np.asarray(rewards, dtype=np.float32) + bonus.astype(np.float32)
            self.mean_intrinsic = float(np.mean(intr)) if intr.size else 0.0
            # Expose the (pre-bonus) extrinsic reward + intrinsic for honest diagnostics;
            # never touch the unshaped `success` flag the certificate reads.
            for k, info in enumerate(infos):
                if info is not None:
                    info["rnd_intrinsic"] = float(intr[k]) if k < intr.size else 0.0
                    info["extrinsic_reward"] = float(rewards[k])
            return obs, new_rewards, dones, infos

    return _RNDVecEnv(venv)
