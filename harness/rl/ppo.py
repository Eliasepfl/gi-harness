"""Vendored single-file PPO — CleanRL-style, torch, CPU only (LLM_RL_SYSTEMS §4.1).

Adapted from CleanRL's `ppo.py` (arXiv 2111.08819, JMLR 2022) — vendored, not
imported, so the reward shape, the subprocess vector env, and the plateau
"declare UNSOLVABLE-BY-RL" early-stop live in one editable file (the note's
rationale for CleanRL over SB3). Small 2x64 MLP, categorical head over the game's
2-8 discrete actions, GAE, clipped surrogate. CPU is forced (`device="cpu"`); no
GPU assumptions. Training may be stochastic (action sampling) — that is OFFLINE
and irrelevant to the emitted certificate; the RL witness (certify.py) is a
greedy, fixed-seed rollout that replays bit-exactly.

Determinism: seeds are fixed (torch/numpy) for reproducibility; the eval + witness
rollouts are argmax and therefore deterministic given the env seed.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.distributions.categorical import Categorical


# --- Default hyperparameters ([eng.]) ---------------------------------------
DEFAULTS = dict(
    learning_rate=2.5e-4,
    num_envs=8,
    num_steps=128,          # rollout length per env -> batch = num_envs*num_steps
    anneal_lr=True,
    gamma=0.99,
    gae_lambda=0.95,
    num_minibatches=4,
    update_epochs=4,
    clip_coef=0.2,
    ent_coef=0.01,
    vf_coef=0.5,
    max_grad_norm=0.5,
    hidden=64,              # 2x64 MLP (task spec)
    patience=40,            # plateau: stop if no new best SMOOTHED return in N updates [eng.]
    plateau_window=10,      # updates averaged into the smoothed return (denoises) [eng.]
    min_delta=0.05,         # min smoothed-return improvement to count as a new best [eng.]
)


def layer_init(layer, std=np.sqrt(2), bias_const=0.0):
    torch.nn.init.orthogonal_(layer.weight, std)
    torch.nn.init.constant_(layer.bias, bias_const)
    return layer


class Agent(nn.Module):
    """Small 2x64 MLP actor-critic with a categorical policy head."""

    def __init__(self, obs_dim: int, n_actions: int, hidden: int = 64):
        super().__init__()
        self.critic = nn.Sequential(
            layer_init(nn.Linear(obs_dim, hidden)), nn.Tanh(),
            layer_init(nn.Linear(hidden, hidden)), nn.Tanh(),
            layer_init(nn.Linear(hidden, 1), std=1.0),
        )
        self.actor = nn.Sequential(
            layer_init(nn.Linear(obs_dim, hidden)), nn.Tanh(),
            layer_init(nn.Linear(hidden, hidden)), nn.Tanh(),
            layer_init(nn.Linear(hidden, n_actions), std=0.01),
        )

    def get_value(self, x):
        return self.critic(x)

    def get_action_and_value(self, x, action=None):
        logits = self.actor(x)
        probs = Categorical(logits=logits)
        if action is None:
            action = probs.sample()
        return action, probs.log_prob(action), probs.entropy(), self.critic(x)

    @torch.no_grad()
    def greedy_action(self, x):
        return torch.argmax(self.actor(x), dim=-1)


# ---------------------------------------------------------------------------
# Threaded subprocess vector env (one node process per env; steps run concurrently
# because each env blocks on Node physics, releasing the GIL on pipe I/O).
# ---------------------------------------------------------------------------
class VecEnv:
    def __init__(self, make_env, num_envs: int, base_seed: int = 0):
        self.envs = [make_env() for _ in range(num_envs)]
        self.n = num_envs
        self.seeds = [base_seed + i for i in range(num_envs)]
        self._pool = ThreadPoolExecutor(max_workers=num_envs)
        self._ret = [0.0] * num_envs
        self.observation_space = self.envs[0].observation_space
        self.action_space = self.envs[0].action_space

    def reset(self):
        obs = list(self._pool.map(lambda ie: ie[1].reset(seed=self.seeds[ie[0]])[0],
                                  list(enumerate(self.envs))))
        self._ret = [0.0] * self.n
        return np.stack(obs).astype(np.float32)

    def _step_one(self, i, action):
        env = self.envs[i]
        obs, reward, term, trunc, info = env.step(int(action))
        self._ret[i] += reward
        done = term or trunc
        ep = None
        if done:
            ep = {"r": self._ret[i], "l": info["tick"],
                  "success": bool(info["success"]), "n_latched": info["n_latched"]}
            self._ret[i] = 0.0
            obs, _ = env.reset(seed=self.seeds[i])  # autoreset (classic CleanRL)
        info["episode"] = ep
        return obs, reward, done, info

    def step(self, actions):
        results = list(self._pool.map(lambda ia: self._step_one(*ia),
                                      list(enumerate(actions))))
        obs = np.stack([r[0] for r in results]).astype(np.float32)
        rewards = np.array([r[1] for r in results], dtype=np.float32)
        dones = np.array([r[2] for r in results], dtype=np.float32)
        infos = [r[3] for r in results]
        return obs, rewards, dones, infos

    def close(self):
        for e in self.envs:
            e.close()
        self._pool.shutdown(wait=False)


def train(make_env, obs_dim: int, n_actions: int, *, total_steps: int,
          seed: int = 0, device: str = "cpu", log=None, wall_clock_budget_s=None,
          **overrides) -> dict:
    """Train PPO on `make_env` (a 0-arg PlanckEnv factory) for ~`total_steps`
    env-steps. Returns the trained agent + training curves + steps_to_first_success.

    Plateau early-stop: stop once no new best mean episodic return appears within
    `patience` updates (the "declare UNSOLVABLE-BY-RL and move on, never hang" rule).
    """
    hp = dict(DEFAULTS)
    hp.update(overrides)
    device = torch.device("cpu")  # CPU only — no GPU assumptions
    torch.manual_seed(seed)
    np.random.seed(seed)

    num_envs = hp["num_envs"]
    num_steps = hp["num_steps"]
    batch_size = num_envs * num_steps
    minibatch_size = batch_size // hp["num_minibatches"]
    num_updates = max(1, total_steps // batch_size)

    envs = VecEnv(make_env, num_envs, base_seed=seed)
    agent = Agent(obs_dim, n_actions, hidden=hp["hidden"]).to(device)
    optimizer = optim.Adam(agent.parameters(), lr=hp["learning_rate"], eps=1e-5)

    # Rollout storage
    obs = torch.zeros((num_steps, num_envs, obs_dim), device=device)
    actions = torch.zeros((num_steps, num_envs), dtype=torch.long, device=device)
    logprobs = torch.zeros((num_steps, num_envs), device=device)
    rewards = torch.zeros((num_steps, num_envs), device=device)
    dones = torch.zeros((num_steps, num_envs), device=device)
    values = torch.zeros((num_steps, num_envs), device=device)

    global_step = 0
    start = time.time()
    next_obs = torch.tensor(envs.reset(), device=device)
    next_done = torch.zeros(num_envs, device=device)

    # Curves + bookkeeping
    curve_return: list[float] = []
    curve_latched: list[float] = []
    curve_success: list[float] = []
    steps_to_first_success = None
    best_return = -1e9
    best_success = 0.0
    updates_since_best = 0
    stopped_early = False
    plateau_stopped = False   # True IFF the patience plateau tripped (== converged)

    for update in range(1, num_updates + 1):
        if hp["anneal_lr"]:
            frac = 1.0 - (update - 1.0) / num_updates
            optimizer.param_groups[0]["lr"] = frac * hp["learning_rate"]

        ep_returns: list[float] = []
        ep_latched: list[float] = []
        ep_success: list[float] = []

        for step in range(num_steps):
            global_step += num_envs
            obs[step] = next_obs
            dones[step] = next_done
            with torch.no_grad():
                action, logprob, _, value = agent.get_action_and_value(next_obs)
                values[step] = value.flatten()
            actions[step] = action
            logprobs[step] = logprob

            no, r, d, infos = envs.step(action.cpu().numpy())
            rewards[step] = torch.tensor(r, device=device)
            next_obs = torch.tensor(no, device=device)
            next_done = torch.tensor(d, device=device)

            for info in infos:
                ep = info.get("episode")
                if ep is not None:
                    ep_returns.append(ep["r"])
                    ep_latched.append(ep["n_latched"])
                    ep_success.append(1.0 if ep["success"] else 0.0)
                    if ep["success"] and steps_to_first_success is None:
                        steps_to_first_success = global_step

        # --- GAE ---
        with torch.no_grad():
            next_value = agent.get_value(next_obs).reshape(1, -1)
            advantages = torch.zeros_like(rewards, device=device)
            lastgaelam = 0
            for t in reversed(range(num_steps)):
                if t == num_steps - 1:
                    nextnonterminal = 1.0 - next_done
                    nextvalues = next_value
                else:
                    nextnonterminal = 1.0 - dones[t + 1]
                    nextvalues = values[t + 1]
                delta = rewards[t] + hp["gamma"] * nextvalues * nextnonterminal - values[t]
                advantages[t] = lastgaelam = (
                    delta + hp["gamma"] * hp["gae_lambda"] * nextnonterminal * lastgaelam)
            returns = advantages + values

        # --- flatten + optimize ---
        b_obs = obs.reshape((-1, obs_dim))
        b_logprobs = logprobs.reshape(-1)
        b_actions = actions.reshape(-1)
        b_advantages = advantages.reshape(-1)
        b_returns = returns.reshape(-1)
        b_values = values.reshape(-1)

        b_inds = np.arange(batch_size)
        for _epoch in range(hp["update_epochs"]):
            np.random.shuffle(b_inds)
            for start_i in range(0, batch_size, minibatch_size):
                mb = b_inds[start_i:start_i + minibatch_size]
                _, newlogprob, entropy, newvalue = agent.get_action_and_value(
                    b_obs[mb], b_actions[mb])
                logratio = newlogprob - b_logprobs[mb]
                ratio = logratio.exp()

                mb_adv = b_advantages[mb]
                mb_adv = (mb_adv - mb_adv.mean()) / (mb_adv.std() + 1e-8)

                pg_loss1 = -mb_adv * ratio
                pg_loss2 = -mb_adv * torch.clamp(
                    ratio, 1 - hp["clip_coef"], 1 + hp["clip_coef"])
                pg_loss = torch.max(pg_loss1, pg_loss2).mean()

                newvalue = newvalue.view(-1)
                v_loss = 0.5 * ((newvalue - b_returns[mb]) ** 2).mean()
                entropy_loss = entropy.mean()
                loss = pg_loss - hp["ent_coef"] * entropy_loss + hp["vf_coef"] * v_loss

                optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(agent.parameters(), hp["max_grad_norm"])
                optimizer.step()

        mean_ret = float(np.mean(ep_returns)) if ep_returns else curve_return[-1] if curve_return else 0.0
        mean_lat = float(np.mean(ep_latched)) if ep_latched else (curve_latched[-1] if curve_latched else 0.0)
        succ_rate = float(np.mean(ep_success)) if ep_success else 0.0
        curve_return.append(round(mean_ret, 3))
        curve_latched.append(round(mean_lat, 3))
        curve_success.append(round(succ_rate, 3))
        best_success = max(best_success, succ_rate)

        # Plateau early-stop on the SMOOTHED mean episodic return (a rolling mean
        # over the last `plateau_window` updates) — a single lucky update no longer
        # freezes `best`, so a still-climbing but noisy run is not cut prematurely.
        window = hp["plateau_window"]
        smoothed = float(np.mean(curve_return[-window:]))
        if smoothed > best_return + hp["min_delta"]:
            best_return = smoothed
            updates_since_best = 0
        else:
            updates_since_best += 1

        if log is not None:
            sps = int(global_step / max(1e-6, time.time() - start))
            log(f"upd {update}/{num_updates} step {global_step} "
                f"ret {mean_ret:.2f} (sm {smoothed:.2f}) latched {mean_lat:.2f} "
                f"succ {succ_rate:.2f} sps {sps} plateau {updates_since_best}/{hp['patience']}")

        if updates_since_best >= hp["patience"]:
            # Converged: no new smoothed-return best in `patience` updates. The only
            # stop that means the curve was NOT still improving (see still_improving).
            stopped_early = True
            plateau_stopped = True
            break
        if wall_clock_budget_s is not None and (time.time() - start) > wall_clock_budget_s:
            # Wall-clock BUDGET cut — not convergence; curve may still be climbing.
            stopped_early = True
            break

    envs.close()
    return {
        "agent": agent,
        "curve_return": curve_return,
        "curve_latched": curve_latched,
        "curve_success": curve_success,
        "steps_to_first_success": steps_to_first_success,
        "global_steps": global_step,
        "updates": update,
        "stopped_early": stopped_early,
        "plateau_stopped": plateau_stopped,
        "best_success_rate_train": round(best_success, 3),
        "train_wall_s": round(time.time() - start, 1),
        "hp": hp,
    }


@torch.no_grad()
def _rollout(env, agent, seed: int, *, greedy: bool, torch_seed=None) -> dict:
    """Roll out from a fresh `env.reset(seed)`, recording the action STRING sequence
    so the result replays bit-exactly through JsExecutor.run_batch. `greedy=True`
    uses argmax (deterministic); `greedy=False` samples the categorical policy
    (seed torch for reproducibility). Either way the recorded (seed, actions) pair
    IS the witness — the batch executor just replays the list."""
    dev = torch.device("cpu")
    if torch_seed is not None:
        torch.manual_seed(torch_seed)
    obs, _ = env.reset(seed=seed)
    action_strings: list[str] = []
    total = 0.0
    result = None
    latched = {}
    for _ in range(env.horizon):
        x = torch.tensor(obs, device=dev).unsqueeze(0)
        if greedy:
            a = int(agent.greedy_action(x).item())
        else:
            a = int(agent.get_action_and_value(x)[0].item())
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


def greedy_episode(env, agent, seed: int, device: str = "cpu") -> dict:
    """Deterministic argmax rollout (the RL witness's preferred form)."""
    return _rollout(env, agent, seed, greedy=True)


def sample_episode(env, agent, seed: int, torch_seed: int = 0) -> dict:
    """Stochastic (sampled) rollout — the graded learnability signal on
    fully-deterministic games, where every greedy episode is the SAME trajectory."""
    return _rollout(env, agent, seed, greedy=False, torch_seed=torch_seed)
