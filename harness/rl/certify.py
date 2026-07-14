"""g3_prime — the RL-learnability certifier (LLM_RL_SYSTEMS §4.2, Phase 0).

`g3_prime(game_path, budget_steps)` trains a fresh PPO policy on the game via
PlanckEnv, evaluates it greedily over a fixed set of seeds, and emits a
learnability verdict plus the deterministic RL witness. The witness — a greedy,
fixed-seed argmax rollout reduced to an action-string sequence — MUST replay to
success through the NORMAL executor path (`JsExecutor.run_batch`); g3_prime
asserts that, which is the certificate bridge: RL slots into the determinism-first
harness with zero change to the replay/witness machinery (§4.1, risk #2).

`learnable` is TRUE when the greedy success rate over the eval seeds clears
LEARNABLE_SUCCESS_RATE. A negative (no success within budget, flat curve) is a
valid datapoint — recorded honestly, never hung on (the "declare UNSOLVABLE-BY-RL
and move on" rule lives in ppo.train's plateau early-stop).
"""

from __future__ import annotations

import time

from harness.rl.env import PlanckEnv

# --- Constants ([eng.]) ------------------------------------------------------
DEFAULT_BUDGET = 2_000_000       # env-steps per game (LLM_RL_SYSTEMS §4.1) [eng.]
N_EVAL = 32                      # greedy eval episodes (fixed seeds) [eng.]
LEARNABLE_SUCCESS_RATE = 0.5     # greedy success rate to call a game learnable [eng.]
TRAINERS = ("vendored", "sb3")   # RL trainer backends (vendored default; sb3 = [LF])


def _resolve_trainer(trainer: str):
    """Return the trainer module exposing ``train`` / ``greedy_episode`` /
    ``sample_episode``. ``vendored`` is the CleanRL-mirror PPO (`harness.rl.ppo`,
    the unchanged default); ``sb3`` is the library-first SB3 migration
    (`harness.rl.sb3_trainer`, GODOT_RL_AGENTS_CAPABILITIES.md §6.7). Imported
    lazily so the vendored lane never touches the optional stable-baselines3 dep."""
    if trainer == "vendored":
        from harness.rl import ppo
        return ppo
    if trainer == "sb3":
        from harness.rl import sb3_trainer
        return sb3_trainer
    raise ValueError(f"unknown trainer {trainer!r} (expected one of {TRAINERS})")


def _pick_witness(greedy_eps: list[dict], sampled_eps: list[dict]) -> dict | None:
    """Best witness = a successful GREEDY episode (fewest ticks) if one exists,
    else the shortest successful SAMPLED episode. Either is a concrete (seed,
    actions) pair that replays bit-exactly through the batch executor — the
    'greedy' preference keeps the determinism-first spirit, the sampled fallback
    lets us still emit a witness for a learnable-but-not-yet-sharp policy."""
    for pool in (greedy_eps, sampled_eps):
        wins = [e for e in pool if e["success"]]
        if wins:
            best = min(wins, key=lambda e: (e["ticks"], e["seed"]))
            return {"seed": best["seed"], "actions": list(best["actions"]),
                    "ticks": best["ticks"], "greedy": best.get("greedy", False)}
    return None


def _bridge_replay(game_source: str, witness: dict) -> dict:
    """Replay the RL witness through the NORMAL batch executor (JsExecutor) and
    return its record. This is the certificate bridge — the caller asserts success."""
    from harness.verify.executors import JsExecutor
    ex = JsExecutor()
    recs = ex.run_batch(
        game_source,
        [{"seed": witness["seed"], "actions": list(witness["actions"])}],
        max_ticks=len(witness["actions"]))
    return recs[0]


def g3_prime(game_path: str, budget_steps: int = DEFAULT_BUDGET, *,
             n_eval: int = N_EVAL, seed: int = 0, log=None,
             wall_clock_budget_s=None, trainer: str = "vendored",
             **train_kwargs) -> dict:
    """Train, greedily evaluate, and emit the learnability certificate for one game.

    `trainer` selects the RL backend: ``"vendored"`` (default, the CleanRL-mirror
    PPO in `harness.rl.ppo`) or ``"sb3"`` (the library-first SB3 PPO migration,
    GODOT_RL_AGENTS_CAPABILITIES.md §6.7). BOTH drive the same PlanckEnv seam and
    the same greedy/sampled eval-episode emission, so the witness ORACLE below
    (`_pick_witness`/`_bridge_replay`) is identical regardless of trainer.

    Returns (task-required keys + provenance extras):
        learnable, steps_to_first_success, checkpoints_curve (per-update mean
        latches), final_success_rate (over n_eval greedy episodes),
        rl_witness ({seed, actions, ticks} | None), wall_clock_s.
    """
    t0 = time.time()
    trainer_mod = _resolve_trainer(trainer)

    # Probe the game once to size the policy (spaces are frozen at construction).
    probe = PlanckEnv(game_path)
    obs_dim = probe.observation_space.shape[0]
    n_actions = probe.action_space.n
    title = probe.title
    n_bodies = len(probe._body_order)
    cp_keys = list(probe._cp_keys)
    probe.close()

    def make_env():
        return PlanckEnv(game_path)

    # --- Train ---
    train_res = trainer_mod.train(make_env, obs_dim, n_actions,
                                  total_steps=budget_steps, seed=seed, log=log,
                                  wall_clock_budget_s=wall_clock_budget_s,
                                  **train_kwargs)
    agent = train_res["agent"]

    # --- Evaluation over fixed seeds ---
    # NB: the showcase games use no world.rng, so they are fully DETERMINISTIC —
    # every greedy episode is the SAME trajectory, making the greedy success rate
    # binary (0 or 1). The graded learnability signal therefore comes from the
    # STOCHASTIC (sampled) rollouts; greedy is reported too (it is the witness's
    # preferred form and the determinism-first certificate).
    eval_env = PlanckEnv(game_path)
    greedy_eps = [trainer_mod.greedy_episode(eval_env, agent, seed=s)
                  for s in range(n_eval)]
    sampled_eps = [trainer_mod.sample_episode(eval_env, agent, seed=s,
                                              torch_seed=1000 + s)
                   for s in range(n_eval)]
    eval_env.close()
    n_greedy = sum(1 for e in greedy_eps if e["success"])
    n_sampled = sum(1 for e in sampled_eps if e["success"])
    final_success_rate = round(n_greedy / float(n_eval), 3)     # greedy (task key)
    stochastic_success_rate = round(n_sampled / float(n_eval), 3)  # graded signal

    # --- RL witness + the certificate bridge (assert it replays via JsExecutor) ---
    witness = _pick_witness(greedy_eps, sampled_eps)
    bridge_ok = None
    bridge_result = None
    if witness is not None:
        with open(game_path, "r", encoding="utf-8") as fh:
            game_source = fh.read()
        rec = _bridge_replay(game_source, witness)
        bridge_result = rec.get("result")
        bridge_ok = bridge_result == "success"
        # The bridge is the whole point: a greedy witness recorded in serve mode
        # MUST win through the batch executor (identical semantics). Fail loud.
        assert bridge_ok, (
            f"RL witness failed to replay to success via JsExecutor "
            f"(got {bridge_result!r}) — serve/batch determinism broken")

    # Learnability is judged on the GRADED (stochastic) success rate — robust to
    # the deterministic-env degeneracy above — OR a clean greedy solve.
    learnable = (stochastic_success_rate >= LEARNABLE_SUCCESS_RATE
                 or final_success_rate >= LEARNABLE_SUCCESS_RATE)

    return {
        # --- task-required keys ---
        "learnable": bool(learnable),
        "steps_to_first_success": train_res["steps_to_first_success"],
        "checkpoints_curve": train_res["curve_latched"],
        "final_success_rate": final_success_rate,             # greedy (deterministic)
        "rl_witness": witness,
        "wall_clock_s": round(time.time() - t0, 1),
        # --- provenance / diagnostics ---
        "title": title,
        "game_path": game_path,
        "trainer": trainer,
        "stochastic_success_rate": stochastic_success_rate,   # graded learnability
        "budget_steps": budget_steps,
        "trained_steps": train_res["global_steps"],
        "updates": train_res["updates"],
        "stopped_early": train_res["stopped_early"],
        "curve_return": train_res["curve_return"],
        "curve_success": train_res["curve_success"],
        "greedy_success_count": n_greedy,
        "sampled_success_count": n_sampled,
        "n_eval": n_eval,
        "witness_greedy": None if witness is None else witness.get("greedy"),
        "bridge_ok": bridge_ok,
        "bridge_result": bridge_result,
        "n_bodies": n_bodies,
        "n_actions": n_actions,
        "obs_dim": obs_dim,
        "checkpoint_keys": cp_keys,
        "throughput_sps": int(train_res["global_steps"] / max(1e-6, train_res["train_wall_s"])),
    }
