"""SB3-backed G3' trainer — the [LF] library-first migration of the vendored
CleanRL-mirror PPO (`harness/rl/ppo.py`) onto stable-baselines3
(GODOT_RL_AGENTS_CAPABILITIES.md §6.7).

Only the LEARNER changes. This module drives the SAME serve-env seam (through the
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

The learner is selected off a small ALGO registry (`method=`): ``ppo`` (default),
``a2c`` and ``dqn`` — BASE stable_baselines3 only. ppo/a2c are on-policy and share
the mirrored actor-critic net-arch + rollout knobs; dqn is off-policy and carries
its OWN small-budget hyperparameters (`DQN_DEFAULTS`) — PPO's n_steps / minibatch /
clip / gae knobs are never forced onto it. Every method emits the SAME eval/witness
surface (greedy = predict(deterministic=True), sampled = deterministic=False), so
`certify._pick_witness`/`_bridge_replay` stay untouched.

stable-baselines3 is an OPTIONAL dependency (pinned `>=2.4,<3`, requirements.txt);
it is imported lazily inside `train()` (and the registry helpers) so this module
(and the eval helpers) stay importable on the vendored lane, where SB3 is never
installed.
"""

from __future__ import annotations

import os
import time

import numpy as np
import torch

from harness.rl.env import wrap_gym
from harness.rl.ppo import DEFAULTS
from harness.verify.chord import chord_from_mask


# --- Algorithm registry ------------------------------------------------------
# Method name (the CLI/ledger token) -> the BASE stable_baselines3 class. BASE
# sb3 ONLY: sb3-contrib algos (RecurrentPPO, QRDQN, TRPO, ...) live in a separate
# `sb3-contrib` package the certifier image does not yet carry, so wiring them up
# is a FOLLOWUP that needs an image rebuild. Resolution is lazy (the import lives
# inside `_algo_registry`) so this module stays importable on the vendored lane.
ALGO_METHODS = ("ppo", "a2c", "dqn")
DEFAULT_METHOD = "ppo"

# BEST-CHECKPOINT eval cadence: how many PPO updates between greedy evals of the live policy
# (the best-by-eval-success snapshot the certifier reloads). One update ~= num_envs*num_steps
# env-steps; at the 8x128 default that is ~25k steps/eval, a few % overhead. [eng.]
EVAL_FREQ_UPDATES = 25


def _algo_registry() -> dict:
    """Map method name -> SB3 algorithm class (imported lazily; base sb3 only)."""
    from stable_baselines3 import A2C, DQN, PPO

    return {"ppo": PPO, "a2c": A2C, "dqn": DQN}


def _resolve_algo(method: str):
    """Return the SB3 algorithm class for ``method`` (one of ``ALGO_METHODS``).

    Raises ``ValueError`` on an unknown method — including sb3-contrib names such
    as ``recurrentppo``, which are a followup gated on an image rebuild."""
    reg = _algo_registry()
    try:
        return reg[method]
    except KeyError:
        raise ValueError(
            f"unknown method {method!r} (expected one of {tuple(reg)}); "
            f"sb3-contrib algos (e.g. RecurrentPPO) are a followup needing an "
            f"image rebuild") from None


# --- DQN small-budget hyperparameters ([eng.]) ------------------------------
# DQN is OFF-policy: PPO's rollout knobs (num_steps, num_minibatches,
# update_epochs, clip_coef, gae_lambda, ent_coef, vf_coef) DO NOT apply and are
# never forwarded. Only the SHARED knobs (gamma, hidden, num_envs, the plateau
# patience/window/min_delta) are read from `DEFAULTS`; everything below is DQN's
# own, sized for the small screening budget — SB3's stock defaults assume a ~1e6
# replay buffer / 1e4 target sync, both far too large for a 3-5k-step probe.
DQN_DEFAULTS = dict(
    learning_rate=1e-3,          # a touch above SB3's 1e-4 to move on tiny budgets
    buffer_size=50_000,          # replay capacity (vs SB3's 1e6 — memory-frugal)
    learning_starts=100,         # warm the buffer before the first gradient step
    batch_size=64,               # replay minibatch
    tau=1.0,                     # hard target update (DQN default)
    train_freq=4,                # one gradient step per 4 env-steps
    gradient_steps=1,
    target_update_interval=250,  # sync target net (vs SB3's 1e4)
    exploration_fraction=0.2,    # anneal epsilon over the first 20% of the budget
    exploration_initial_eps=1.0,
    exploration_final_eps=0.05,
)


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

        def __init__(self, hp, log=None, wall_clock_budget_s=None, best_model_path=None,
                     eval_fn=None, eval_freq_updates=EVAL_FREQ_UPDATES):
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
            self.best_latched = -1e9        # plateau also tracks checkpoint + success progress
            self.best_success_smoothed = -1e9  # (PBRS makes episodic RETURN flat — see below)
            self.best_success = 0.0
            self.updates_since_best = 0
            self.stopped_early = False
            self.plateau_stopped = False      # True IFF the patience plateau tripped
            self._pending: list[dict] = []   # episodes finished since last rollout end
            self._stop = False
            self._t0 = 0.0
            # BEST-CHECKPOINT (Elias, 2026-07-16). Godot-serve training is high variance / non
            # deterministic (a run can solve mid-training then DEGRADE to a do-nothing policy),
            # so evaluating the LAST policy under-reports the run. When `best_model_path` AND an
            # `eval_fn` are given, every `eval_freq_updates` we run a GREEDY EVAL of the current
            # policy and snapshot it whenever the (greedy_sr, stochastic_sr) score reaches a new
            # best. Keying on the EVAL (not rollout) success is deliberate: a near-random early
            # policy scores high ROLLOUT success by exploration luck but ~0 on a greedy eval, so
            # only a genuinely competent policy is ever saved. Default None -> no snapshot,
            # byte-identical to before.
            self.best_model_path = best_model_path
            self._eval_fn = eval_fn
            self._eval_freq_updates = max(1, int(eval_freq_updates))
            self.best_ckpt_score = (-1.0, -1.0)   # (greedy_sr, stochastic_sr)
            self.best_ckpt_greedy = None
            self.best_ckpt_stochastic = None
            self.best_ckpt_update = None
            self.best_ckpt_saved = False

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

            # Plateau on SMOOTHED PROGRESS (rolling mean over plateau_window updates). We treat
            # a new best in the episodic RETURN, the mean LATCHED-checkpoint count, OR the
            # success rate as "still improving" and reset the patience counter. Keying on return
            # ALONE breaks under POTENTIAL-BASED shaping: PBRS is discounted-neutral, so a
            # non-winning episode's return is ~0 and the return curve stays FLAT until a win —
            # which would trip the plateau (and stop training) while the policy is still climbing
            # the checkpoint ladder. Latched/success rise as the policy makes real progress, so
            # the combined signal keeps a genuinely-improving run alive; a truly stuck run (no
            # return, no new checkpoints, no wins) still plateaus and stops.
            window = self.hp["plateau_window"]
            min_delta = self.hp["min_delta"]
            smoothed = float(np.mean(self.curve_return[-window:]))
            smoothed_lat = float(np.mean(self.curve_latched[-window:]))
            smoothed_succ = float(np.mean(self.curve_success[-window:]))
            improved = False
            if smoothed > self.best_return + min_delta:
                self.best_return = smoothed
                improved = True
            if smoothed_lat > self.best_latched + min_delta:
                self.best_latched = smoothed_lat
                improved = True
            if smoothed_succ > self.best_success_smoothed + min_delta:
                self.best_success_smoothed = smoothed_succ
                improved = True
            if improved:
                self.updates_since_best = 0
            else:
                self.updates_since_best += 1

            # BEST-CHECKPOINT snapshot (EVAL-keyed): every eval_freq_updates, greedily evaluate
            # the current policy and keep the snapshot with the best (greedy_sr, stochastic_sr).
            # Greedy eval — not rollout success — so early exploration luck is never mistaken for
            # competence. A crash in eval must not kill training (skip, log).
            if self.best_model_path is not None and self._eval_fn is not None \
                    and self.updates % self._eval_freq_updates == 0:
                # Save/restore the global torch RNG around the eval: sample_episode reseeds
                # torch (for reproducible sampled rollouts), which would otherwise perturb the
                # trainer's own action sampling and change the run. This keeps the eval a pure
                # OBSERVER of training.
                _rng = torch.get_rng_state()
                try:
                    ev = self._eval_fn(self.model) or {}
                    g = float(ev.get("greedy_sr", 0.0))
                    s = float(ev.get("stochastic_sr", 0.0))
                except Exception as exc:  # noqa: BLE001
                    g = s = None
                    if self._log is not None:
                        self._log(f"  [best-ckpt] eval skipped: {type(exc).__name__}: {exc}")
                finally:
                    torch.set_rng_state(_rng)
                if g is not None:
                    new_best = (g, s) > self.best_ckpt_score and (g > 0.0 or s > 0.0)
                    if new_best:
                        self.best_ckpt_score = (g, s)
                        self.best_ckpt_greedy, self.best_ckpt_stochastic = g, s
                        self.best_ckpt_update = self.updates
                        self.model.save(self.best_model_path)   # SB3 .zip snapshot
                        self.best_ckpt_saved = True
                    if self._log is not None:
                        self._log(f"  [best-ckpt] upd {self.updates} eval greedy={g:.2f} "
                                  f"stochastic={s:.2f}" + (" -> SAVED new best" if new_best else ""))

            if self._log is not None:
                sps = int(self.num_timesteps / max(1e-6, time.time() - self._t0))
                self._log(
                    f"upd {self.updates} step {self.num_timesteps} "
                    f"ret {mean_ret:.2f} (sm {smoothed:.2f}) latched {mean_lat:.2f} "
                    f"succ {succ:.2f} sps {sps} "
                    f"plateau {self.updates_since_best}/{self.hp['patience']}")

            if self.updates_since_best >= self.hp["patience"]:
                # The learning curve has CONVERGED (no new smoothed-return best in
                # `patience` updates) — this is the "declare done and move on" stop, and
                # the ONLY stop that means the curve was NOT still improving.
                self.stopped_early = True
                self.plateau_stopped = True
                self._stop = True
            elif (self.wall_clock_budget_s is not None
                  and (time.time() - self._t0) > self.wall_clock_budget_s):
                # A wall-clock cut is a BUDGET limit, not convergence: the curve may
                # still have been improving, so this does NOT set plateau_stopped.
                self.stopped_early = True
                self._stop = True

    return _CurveCallback


def _build_onpolicy(method: str, algo_cls, venv, hp: dict, seed: int):
    """Construct an ON-policy SB3 model (PPO or A2C) mirroring the vendored loop's
    knobs: the separate 2x64 tanh actor/critic net-arch (orthogonal init), the
    learning rate (+ linear anneal), n_steps, gamma/lambda, entropy/value coeffs,
    grad-norm. PPO keeps the full minibatch/epoch/clip machinery; A2C is the same
    actor-critic net + rollout but a single full-batch update per rollout, so the
    PPO-only knobs (num_minibatches/update_epochs/clip_coef) are simply NOT
    forwarded — SB3's A2C has no such parameters."""
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
    common = dict(
        learning_rate=lr,
        n_steps=hp["num_steps"],
        gamma=hp["gamma"],
        gae_lambda=hp["gae_lambda"],
        ent_coef=hp["ent_coef"],
        vf_coef=hp["vf_coef"],
        max_grad_norm=hp["max_grad_norm"],
        policy_kwargs=policy_kwargs,
        seed=seed,
        device="cpu",                 # CPU only — no GPU assumptions (== ppo.py)
        verbose=0,
    )
    if method == "ppo":
        batch_size = hp["num_envs"] * hp["num_steps"]
        minibatch_size = max(1, batch_size // hp["num_minibatches"])
        return algo_cls(
            "MlpPolicy", venv,
            batch_size=minibatch_size,
            n_epochs=hp["update_epochs"],
            clip_range=hp["clip_coef"],
            **common)
    return algo_cls("MlpPolicy", venv, **common)   # a2c


def _build_dqn(algo_cls, venv, hp: dict, seed: int):
    """Construct an OFF-policy DQN model from DQN's OWN small-budget knobs
    (`DQN_DEFAULTS`). Discrete actions only — the serve-env action space IS
    Discrete, so DQN applies. NONE of PPO's rollout hyperparameters are forwarded;
    epsilon-greedy exploration (not an LR anneal) drives exploration. A single Q
    net-arch (no actor/critic split, no ortho_init — DQN's MlpPolicy has neither)."""
    policy_kwargs = dict(
        net_arch=[hp["hidden"], hp["hidden"]],   # one Q-net (no pi/vf split)
        activation_fn=torch.nn.Tanh,
    )
    return algo_cls(
        "MlpPolicy", venv,
        learning_rate=hp["learning_rate"],
        buffer_size=hp["buffer_size"],
        learning_starts=hp["learning_starts"],
        batch_size=hp["batch_size"],
        tau=hp["tau"],
        gamma=hp["gamma"],
        train_freq=hp["train_freq"],
        gradient_steps=hp["gradient_steps"],
        target_update_interval=hp["target_update_interval"],
        exploration_fraction=hp["exploration_fraction"],
        exploration_initial_eps=hp["exploration_initial_eps"],
        exploration_final_eps=hp["exploration_final_eps"],
        policy_kwargs=policy_kwargs,
        seed=seed,
        device="cpu",                 # CPU only — no GPU assumptions (== ppo.py)
        verbose=0,
    )


# When sharding, the M Godot processes own the cores for COLLECTION and the CPU learner is a
# tiny 2x64 MLP whose backward pass is FASTER on few threads. PyTorch otherwise defaults its
# intra-op pool to the whole `-c` cgroup (e.g. 32 threads), which both wastes cycles on the
# small net AND fights the Godot shard procs for cores — instrumented: torch=32 collapsed 4x8
# to 503 sps, torch=2 gave 10502 (20x), while the sharded ENV itself scaled 4868->15202 sps
# (M=1->4). So the sharded path caps torch to this small default (overridable). [eng.]
SHARD_TORCH_THREADS = 2


def train(make_env, obs_dim: int, n_actions: int, *, total_steps: int,
          seed: int = 0, device: str = "cpu", log=None, wall_clock_budget_s=None,
          method: str = DEFAULT_METHOD, make_batch_venv=None, num_shards: int = 1,
          make_shard_venv=None, torch_num_threads: int | None = None,
          best_model_path: str | None = None, eval_fn=None,
          eval_freq_updates: int = EVAL_FREQ_UPDATES,
          warmstart=None, rnd=None, **overrides) -> dict:
    """Train an SB3 learner on `make_env` (a 0-arg serve-env factory) for
    ~`total_steps` env-steps and return the same dict shape as `ppo.train` (agent +
    curves + steps_to_first_success + bookkeeping, plus the resolved `method`).
    `obs_dim`/`n_actions` are accepted for surface parity with the vendored trainer;
    SB3 sizes the policy from the env's own spaces via the gymnasium adapter.

    `method` selects the algorithm off the registry (`ALGO_METHODS`: ``ppo``
    default / ``a2c`` / ``dqn``). ppo/a2c share the mirrored on-policy net-arch and
    rollout knobs; dqn is off-policy and gets its own `DQN_DEFAULTS` — the PPO
    rollout knobs are never forced onto it. The callback (curves + plateau/wall-clock
    early stop) and the greedy/sampled eval surface are identical for every method.

    SHARDING (Elias, 2026-07-16: "32 cores per run"). `num_shards` (default 1 =
    today's behavior, byte-identical) fans the rollout across M INDEPENDENT
    `GodotBatchVecEnv` shards stepped concurrently — total logical envs =
    ``num_shards * num_envs`` (M*K). It engages ONLY when a `make_shard_venv(M, K)`
    factory is supplied (the sb3/gdscript lane, `certify.g3_prime`) AND
    ``num_shards > 1`` AND ``HARNESS_VECENV != "dummy"``; otherwise the single-shard
    batch (or DummyVecEnv) path is untouched. When it engages the PPO minibatch is
    sized off the TRUE rollout width (M*K) so `num_minibatches` still divides the
    batch exactly as on the single-shard path, and torch intra-op threads are capped
    (``SHARD_TORCH_THREADS``) so the tiny-MLP learner does not starve the M Godot
    collectors — the instrumented single-game-throughput fix (torch=32 -> 503 sps,
    torch=2 -> 10502 sps at 4x8). ``torch_num_threads`` overrides the cap on any path."""
    algo_cls = _resolve_algo(method)   # validates method (+ lazily imports SB3)
    from stable_baselines3.common.monitor import Monitor
    from stable_baselines3.common.vec_env import DummyVecEnv

    hp = dict(DEFAULTS)
    if method == "dqn":
        hp.update(DQN_DEFAULTS)       # off-policy knobs (no PPO rollout params)
    hp.update(overrides)

    num_envs = hp["num_envs"]

    # One serve env per vec slot, wrapped as gymnasium.Env then Monitored so each
    # episode-end info carries r/l PLUS our success + n_latched (info_keywords) —
    # the callback reads those to rebuild the vendored curves.
    def _make_init(slot: int = 0):
        def _init():
            # WITNESS-WARMSTART (OPT-IN): thread the shared curriculum + a per-slot rng seed
            # into the gym adapter so each slot Backplay-replays an independently-drawn
            # witness prefix on reset. `warmstart=None` -> wrap_gym byte-identical to before.
            genv = wrap_gym(make_env(), warmstart=warmstart,
                            ws_seed=(int(seed) * 1000 + slot))
            return Monitor(genv, info_keywords=("success", "n_latched"))
        return _init

    # MULTI-CPU PER GAME (Elias, 2026-07-15). SB3's SubprocVecEnv does NOT fit: each
    # GodotServeEnv already spawns its OWN Godot serve subprocess + TCP socket, and
    # forking a Python worker around that (fork/spawn) breaks the sockets (BrokenPipe).
    # The library-aligned fix is IN-SCENE batching: when the caller supplies a
    # `make_batch_venv` factory (godot/gdscript lane, num_envs>1) we run ONE Godot
    # process with N in-scene instances over ONE socket (GodotBatchVecEnv), stepped
    # together in the engine tick loop — vs DummyVecEnv, which steps its N slots
    # SEQUENTIALLY (one Godot proc busy at a time, N round-trips per vec-step). The
    # single-serve-env lane and num_envs==1 keep DummyVecEnv; HARNESS_VECENV=dummy forces
    # the old path everywhere (debug). Farm-level parallelism (1 game/array task) is
    # orthogonal and still live.
    num_shards = int(num_shards)
    # WITNESS-WARMSTART lives in the gym adapter's reset (Backplay prefix replay via the
    # single-instance serve stepping); the batched serve_game.gd host steps all N instances
    # together and has no per-slot prefix seam. So warmstart FORCES the sequential
    # DummyVecEnv lane (N independent GodotServeEnv slots, each replay-capable) — the clean
    # reuse of the adversary idiom with zero engine changes. Documented, opt-in.
    force_dummy = (os.environ.get("HARNESS_VECENV") == "dummy") or (warmstart is not None)
    # SHARDING first (M*K logical envs over M concurrent Godot processes), then the
    # single-process batch, then the sequential DummyVecEnv. num_shards==1 never takes
    # the shard path -> the batch/dummy behavior stays byte-identical to before.
    use_shard = (num_shards > 1 and make_shard_venv is not None and num_envs > 1
                 and not force_dummy)
    use_batch = (not use_shard and make_batch_venv is not None and num_envs > 1
                 and not force_dummy)
    if use_shard:
        venv = make_shard_venv(num_shards, num_envs)
        # M*K logical envs now feed ONE rollout; size the PPO minibatch off the TRUE
        # rollout width so num_minibatches divides the batch exactly as on the single
        # batch path (SB3 reads the buffer width from venv.num_envs itself).
        hp["num_envs"] = venv.num_envs
    elif use_batch:
        venv = make_batch_venv(num_envs)
    else:
        venv = DummyVecEnv([_make_init(i) for i in range(num_envs)])

    # RND (OPT-IN, anti-camping): compose a VecEnvWrapper around the ONE venv all three
    # lanes converge on, adding the bounded/decaying intrinsic bonus in step_wait and
    # training the predictor on the collected obs. `rnd=None` -> no wrapper -> byte-identical
    # to vanilla PPO. Reward invariants are respected (see harness.rl.rnd): the wrapper never
    # touches step_reward or the `success` certificate, the bonus is bounded and anneals to
    # `int_coef_final` while RND's own novelty decay drives it to ~0 at convergence.
    rnd_wrapper = None
    if rnd is not None:
        from harness.rl import rnd as rnd_mod
        rcfg = dict(rnd) if isinstance(rnd, dict) else {}
        model_kw = {k: rcfg[k] for k in ("feat_dim", "hidden", "lr") if k in rcfg}
        rnd_model = rnd_mod.RNDModel(obs_dim, seed=seed, device=device, **model_kw)
        wrap_kw = {k: rcfg[k] for k in ("int_coef", "int_coef_final", "update_predictor")
                   if k in rcfg}
        venv = rnd_mod.wrap_venv(venv, rnd_model, total_steps=total_steps, **wrap_kw)
        rnd_wrapper = venv

    # CPU-learner thread policy (the sharded single-game-throughput fix — see
    # SHARD_TORCH_THREADS). Explicit `torch_num_threads` always wins; else the SHARDED path
    # caps torch to the small default so the tiny MLP's threads do not starve the M Godot
    # collectors; the non-shard path is left exactly as before (byte/behaviour-identical).
    if torch_num_threads is not None:
        torch.set_num_threads(max(1, int(torch_num_threads)))
    elif use_shard:
        torch.set_num_threads(max(1, min(torch.get_num_threads(), SHARD_TORCH_THREADS)))

    # Seed each vec slot with base_seed+i, latched by the adapter and reused on
    # autoreset — the exact per-env fixed-seed scheme of the vendored VecEnv.
    venv.seed(seed)

    model = (_build_dqn(algo_cls, venv, hp, seed) if method == "dqn"
             else _build_onpolicy(method, algo_cls, venv, hp, seed))

    curve_cb = _build_callback_cls()(hp, log=log,
                                     wall_clock_budget_s=wall_clock_budget_s,
                                     best_model_path=best_model_path, eval_fn=eval_fn,
                                     eval_freq_updates=eval_freq_updates)
    callback = curve_cb
    # WITNESS-WARMSTART (OPT-IN): append the curriculum-annealing callback so finished
    # episodes step the prefix down. Composed via CallbackList so the curve/plateau/best-ckpt
    # callback (`curve_cb`, whose attributes the return dict reads) is untouched; only built
    # when warmstart is enabled (vanilla path unchanged).
    if warmstart is not None:
        from stable_baselines3.common.callbacks import CallbackList
        from harness.rl.warmstart import build_warmstart_callback
        callback = CallbackList([curve_cb, build_warmstart_callback(warmstart, log=log)])
    start = time.time()
    model.learn(total_timesteps=total_steps, callback=callback, progress_bar=False)
    train_wall = time.time() - start
    venv.close()

    return {
        "agent": model,
        "method": method,
        "curve_return": curve_cb.curve_return,
        "curve_latched": curve_cb.curve_latched,
        "curve_success": curve_cb.curve_success,
        "steps_to_first_success": curve_cb.steps_to_first_success,
        "global_steps": int(model.num_timesteps),
        "updates": curve_cb.updates,
        "stopped_early": curve_cb.stopped_early,
        "plateau_stopped": curve_cb.plateau_stopped,
        "best_success_rate_train": round(curve_cb.best_success, 3),
        # BEST-CHECKPOINT provenance (eval-keyed; None-safe when no snapshot was saved).
        "best_model_path": (best_model_path if curve_cb.best_ckpt_saved else None),
        "best_ckpt_update": curve_cb.best_ckpt_update,
        "best_ckpt_greedy_sr": curve_cb.best_ckpt_greedy,
        "best_ckpt_stochastic_sr": curve_cb.best_ckpt_stochastic,
        "train_wall_s": round(train_wall, 1),
        "hp": hp,
        # EXPLORATION-ARM diagnostics (present only when the arm is enabled).
        "warmstart_summary": (warmstart.summary() if warmstart is not None else None),
        "rnd_mean_intrinsic": (round(float(rnd_wrapper.mean_intrinsic), 5)
                               if rnd_wrapper is not None else None),
        "rnd_final_coef": (round(float(rnd_wrapper.last_coef), 5)
                           if rnd_wrapper is not None else None),
    }


def _rollout(env, model, seed: int, *, greedy: bool, torch_seed=None) -> dict:
    """Roll out an SB3 `model` on a fresh `env.reset(seed)`, recording the WIRE-action
    sequence so the result replays bit-exactly through the batch executor (identical
    shape to `ppo._rollout`). `greedy=True` -> `predict(deterministic=True)`; `greedy=
    False` -> sampled (seed torch for reproducibility). The recorded (seed, actions) pair
    IS the witness — the batch executor replays the list.

    DISCRETE: each recorded action is the single verb STRING ``env.actions[a]`` (byte-
    identical to the pre-chord witness). CHORD (MultiBinary): ``predict`` returns a per-key
    0/1 vector (deterministic -> per-key argmax, prob>0.5); it is reduced through
    :func:`chord_from_mask` to the WIRE form — a plain str for a lone key (legacy singleton),
    a sorted list for a real chord, or ``[]`` for an all-off IDLE tick (only when the env's
    ``allow_idle`` is on). Phase-1 capture/replay already accepts arrays, so the witness list
    replays unchanged through GdExecutor.run_batch."""
    if torch_seed is not None:
        torch.manual_seed(torch_seed)
    chord = bool(getattr(env, "chord_mode", False))
    allow_idle = bool(getattr(env, "allow_idle", False))
    oppose_pairs = getattr(env, "oppose_pairs", None)   # measured contradictory-chord pairs
    obs, _ = env.reset(seed=seed)
    action_strings: list = []
    total = 0.0
    result = None
    latched = {}
    for _ in range(env.horizon):
        action, _ = model.predict(np.asarray(obs, dtype=np.float32),
                                  deterministic=greedy)
        if chord:
            mask = np.asarray(action).reshape(-1)
            action_strings.append(
                chord_from_mask(mask, env.actions, allow_empty=allow_idle,
                                oppose_pairs=oppose_pairs))
            step_action = mask
        else:
            a = int(np.asarray(action).reshape(-1)[0])
            action_strings.append(env.actions[a])
            step_action = a
        obs, r, term, trunc, info = env.step(step_action)
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
