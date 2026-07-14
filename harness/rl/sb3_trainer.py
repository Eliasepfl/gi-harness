"""SB3-backed G3' trainer — the [LF] library-first migration of the vendored
CleanRL-mirror PPO (`harness/rl/ppo.py`) onto stable-baselines3
(GODOT_RL_AGENTS_CAPABILITIES.md §6.7).

Only the LEARNER changes. This module drives the SAME PlanckEnv seam (through the
gymnasium adapter `harness/rl/env.wrap_gym`) and exposes the SAME train-and-eval
surface `certify.g3_prime` consumes — `train(...) -> {agent, curve_*, ...}` plus
`greedy_episode`/`sample_episode` returning the identical episode-dict shape as
`ppo.py`. The witness ORACLE (`certify._pick_witness`, `_bridge_replay`) is
untouched: greedy eval episodes come from `model.predict(deterministic=True)`,
sampled ones from `deterministic=False`, over the same n_eval / fixed eval seeds,
so a recorded (seed, action-string) pair still replays bit-exactly through the
batch executor.

Hyperparameters MIRROR the vendored loop (`ppo.DEFAULTS`) wherever SB3 exposes the
knob: learning rate (+ linear anneal), the 2x64 tanh separate actor/critic
net-arch (orthogonal init), n_steps, minibatch size, epochs, gamma/lambda, clip,
entropy/value coeffs, grad-norm. The smoothed plateau early-stop
(patience/window/min_delta) is reproduced in a training callback — the "declare
UNSOLVABLE-BY-RL and move on, never hang" rule.

stable-baselines3 is an OPTIONAL dependency (pinned `>=2.4,<3`, requirements.txt);
it is imported lazily inside `train()` so this module (and the eval helpers) stay
importable on the vendored lane, where SB3 is never installed.
"""

from __future__ import annotations

import time

import numpy as np
import torch

from harness.rl.env import wrap_gym
from harness.rl.ppo import DEFAULTS


def _build_callback_cls():
    """Lazily build the training callback (subclasses SB3's ``BaseCallback``, so
    it can only be defined once stable-baselines3 is importable)."""
    from stable_baselines3.common.callbacks import BaseCallback

    class _CurveCallback(BaseCallback):
        """Records the per-UPDATE curves the vendored trainer returns (mean episodic
        return / latched-checkpoints / success rate), tracks steps-to-first-success,
        and enforces the smoothed-return plateau + wall-clock early stops.

        SB3 fires ``_on_rollout_end`` after each rollout is collected (== one PPO
        update's worth of data), which is where we aggregate the episodes that
        finished during the rollout. Training can only be halted from ``_on_step``
        (SB3 ignores ``_on_rollout_end``'s return), so a plateau trip sets a flag
        that the next ``_on_step`` observes to return False."""

        def __init__(self, hp, log=None, wall_clock_budget_s=None):
            super().__init__()
            self.hp = hp
            self._log = log
            self.wall_clock_budget_s = wall_clock_budget_s
            self.curve_return: list[float] = []
            self.curve_latched: list[float] = []
            self.curve_success: list[float] = []
            self.steps_to_first_success = None
            self.updates = 0
            self.best_return = -1e9
            self.best_success = 0.0
            self.updates_since_best = 0
            self.stopped_early = False
            self._pending: list[dict] = []   # episodes finished since last rollout end
            self._stop = False
            self._t0 = 0.0

        def _on_training_start(self) -> None:
            self._t0 = time.time()

        def _on_step(self) -> bool:
            for info in self.locals.get("infos", []):
                ep = info.get("episode")
                if ep is not None:
                    self._pending.append(ep)
                    if ep.get("success") and self.steps_to_first_success is None:
                        self.steps_to_first_success = int(self.num_timesteps)
            return not self._stop

        def _on_rollout_end(self) -> None:
            self.updates += 1
            eps = self._pending
            self._pending = []
            if eps:
                mean_ret = float(np.mean([e["r"] for e in eps]))
                mean_lat = float(np.mean([e.get("n_latched", 0) for e in eps]))
                succ = float(np.mean([1.0 if e.get("success") else 0.0 for e in eps]))
            else:  # no episode ended this rollout — carry the last curve point
                mean_ret = self.curve_return[-1] if self.curve_return else 0.0
                mean_lat = self.curve_latched[-1] if self.curve_latched else 0.0
                succ = 0.0
            self.curve_return.append(round(mean_ret, 3))
            self.curve_latched.append(round(mean_lat, 3))
            self.curve_success.append(round(succ, 3))
            self.best_success = max(self.best_success, succ)

            # Plateau on the SMOOTHED mean return (rolling mean over plateau_window
            # updates) — a single lucky update no longer freezes `best` (== ppo.py).
            window = self.hp["plateau_window"]
            smoothed = float(np.mean(self.curve_return[-window:]))
            if smoothed > self.best_return + self.hp["min_delta"]:
                self.best_return = smoothed
                self.updates_since_best = 0
            else:
                self.updates_since_best += 1

            if self._log is not None:
                sps = int(self.num_timesteps / max(1e-6, time.time() - self._t0))
                self._log(
                    f"upd {self.updates} step {self.num_timesteps} "
                    f"ret {mean_ret:.2f} (sm {smoothed:.2f}) latched {mean_lat:.2f} "
                    f"succ {succ:.2f} sps {sps} "
                    f"plateau {self.updates_since_best}/{self.hp['patience']}")

            if self.updates_since_best >= self.hp["patience"]:
                self.stopped_early = True
                self._stop = True
            elif (self.wall_clock_budget_s is not None
                  and (time.time() - self._t0) > self.wall_clock_budget_s):
                self.stopped_early = True
                self._stop = True

    return _CurveCallback


def train(make_env, obs_dim: int, n_actions: int, *, total_steps: int,
          seed: int = 0, device: str = "cpu", log=None, wall_clock_budget_s=None,
          **overrides) -> dict:
    """Train SB3 PPO on `make_env` (a 0-arg PlanckEnv factory) for ~`total_steps`
    env-steps and return the same dict shape as `ppo.train` (agent + curves +
    steps_to_first_success + bookkeeping). `obs_dim`/`n_actions` are accepted for
    surface parity with the vendored trainer; SB3 sizes the policy from the env's
    own spaces via the gymnasium adapter."""
    from stable_baselines3 import PPO
    from stable_baselines3.common.monitor import Monitor
    from stable_baselines3.common.vec_env import DummyVecEnv

    hp = dict(DEFAULTS)
    hp.update(overrides)

    num_envs = hp["num_envs"]
    num_steps = hp["num_steps"]
    batch_size = num_envs * num_steps
    minibatch_size = max(1, batch_size // hp["num_minibatches"])

    # One PlanckEnv per vec slot, wrapped as gymnasium.Env then Monitored so each
    # episode-end info carries r/l PLUS our success + n_latched (info_keywords) —
    # the callback reads those to rebuild the vendored curves.
    def _make_init():
        def _init():
            return Monitor(wrap_gym(make_env()),
                           info_keywords=("success", "n_latched"))
        return _init

    venv = DummyVecEnv([_make_init() for _ in range(num_envs)])
    # Seed each vec slot with base_seed+i, latched by the adapter and reused on
    # autoreset — the exact per-env fixed-seed scheme of the vendored VecEnv.
    venv.seed(seed)

    base_lr = hp["learning_rate"]
    # anneal_lr -> SB3 linear schedule (progress_remaining runs 1.0 -> 0.0), the
    # same shape as ppo.py's `frac = 1 - (update-1)/num_updates`.
    lr = (lambda progress_remaining: progress_remaining * base_lr) \
        if hp["anneal_lr"] else base_lr

    policy_kwargs = dict(
        net_arch=dict(pi=[hp["hidden"], hp["hidden"]],   # separate 2x64 actor/critic
                      vf=[hp["hidden"], hp["hidden"]]),
        activation_fn=torch.nn.Tanh,
        ortho_init=True,
    )
    model = PPO(
        "MlpPolicy", venv,
        learning_rate=lr,
        n_steps=num_steps,
        batch_size=minibatch_size,
        n_epochs=hp["update_epochs"],
        gamma=hp["gamma"],
        gae_lambda=hp["gae_lambda"],
        clip_range=hp["clip_coef"],
        ent_coef=hp["ent_coef"],
        vf_coef=hp["vf_coef"],
        max_grad_norm=hp["max_grad_norm"],
        policy_kwargs=policy_kwargs,
        seed=seed,
        device="cpu",                 # CPU only — no GPU assumptions (== ppo.py)
        verbose=0,
    )

    callback = _build_callback_cls()(hp, log=log,
                                     wall_clock_budget_s=wall_clock_budget_s)
    start = time.time()
    model.learn(total_timesteps=total_steps, callback=callback, progress_bar=False)
    train_wall = time.time() - start
    venv.close()

    return {
        "agent": model,
        "curve_return": callback.curve_return,
        "curve_latched": callback.curve_latched,
        "curve_success": callback.curve_success,
        "steps_to_first_success": callback.steps_to_first_success,
        "global_steps": int(model.num_timesteps),
        "updates": callback.updates,
        "stopped_early": callback.stopped_early,
        "best_success_rate_train": round(callback.best_success, 3),
        "train_wall_s": round(train_wall, 1),
        "hp": hp,
    }


def _rollout(env, model, seed: int, *, greedy: bool, torch_seed=None) -> dict:
    """Roll out an SB3 `model` on a fresh `env.reset(seed)`, recording the action
    STRING sequence so the result replays bit-exactly through JsExecutor.run_batch
    (identical shape to `ppo._rollout`). `greedy=True` -> `predict(deterministic=
    True)` (argmax); `greedy=False` -> sampled (seed torch for reproducibility).
    The recorded (seed, actions) pair IS the witness — the batch executor replays
    the list."""
    if torch_seed is not None:
        torch.manual_seed(torch_seed)
    obs, _ = env.reset(seed=seed)
    action_strings: list[str] = []
    total = 0.0
    result = None
    latched = {}
    for _ in range(env.horizon):
        action, _ = model.predict(np.asarray(obs, dtype=np.float32),
                                  deterministic=greedy)
        a = int(np.asarray(action).reshape(-1)[0])
        action_strings.append(env.actions[a])
        obs, r, term, trunc, info = env.step(a)
        total += r
        result = info["result"]
        latched = info["latched"]
        if term or trunc:
            break
    return {
        "seed": seed,
        "actions": action_strings,
        "ticks": len(action_strings),
        "success": result == "success",
        "result": result,
        "return": round(total, 3),
        "latched": latched,
        "greedy": greedy,
    }


def greedy_episode(env, model, seed: int, device: str = "cpu") -> dict:
    """Deterministic argmax rollout (the RL witness's preferred form)."""
    return _rollout(env, model, seed, greedy=True)


def sample_episode(env, model, seed: int, torch_seed: int = 0) -> dict:
    """Stochastic (sampled) rollout — the graded learnability signal on
    fully-deterministic games, where every greedy episode is the SAME trajectory."""
    return _rollout(env, model, seed, greedy=False, torch_seed=torch_seed)
