"""harness.rl — the G3' (G3-prime) RL-learnability spike (Phase 0).

Proves the LLM_RL_SYSTEMS.md thesis on our real games: "solvable" (an existence
witness the Go-Explore tree finds) versus "learnable" (a small PPO policy climbs
to a high success rate). Three single-purpose modules:

    env.py      PlanckEnv — a Gymnasium-style Env over nodeworld/runner.js "serve"
                mode (one decision tick per step). Its obs/action surface MIRRORS
                godot_rl_agents' AIController so the Godot lane can swap in later
                with zero retraining-code changes (see the docstring + the note).
    ppo.py      A vendored, single-file CleanRL-style PPO (torch, CPU only): small
                2x64 MLP, categorical head, plateau early-stop, fixed seeds.
    certify.py  g3_prime(game_path, budget_steps) -> learnability verdict + the
                deterministic greedy RL witness, asserted to replay to success
                through the NORMAL executor path (JsExecutor.run_batch).

Nothing here touches gameverify.py / treesolve.py / the executors' batch modes;
the only engine change is the additive "serve" mode in nodeworld/runner.js.
"""
