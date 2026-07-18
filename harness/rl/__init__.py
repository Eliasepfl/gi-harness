"""harness.rl — the G3' (G3-prime) RL-learnability spike (Phase 0).

Proves the LLM_RL_SYSTEMS.md thesis on our real games: "solvable" (an existence
witness the Go-Explore tree finds) versus "learnable" (a small PPO policy climbs
to a high success rate). Three single-purpose modules:

    env.py      Shared obs/reward/space primitives + the gymnasium `wrap_gym`
                adapter for the Godot serve envs (one decision tick per step). The
                obs/action surface MIRRORS godot_rl_agents' AIController so the
                Godot lane can swap in later with zero retraining-code changes.
    ppo.py      A vendored, single-file CleanRL-style PPO (torch, CPU only): small
                2x64 MLP, categorical head, plateau early-stop, fixed seeds.
    certify.py  g3_prime(game_path, budget_steps) -> learnability verdict + the
                deterministic greedy RL witness, asserted to replay to success
                through the NORMAL batch-executor path.

Nothing here touches gameverify.py / treesolve.py / the executors' batch modes;
the RL envs run over the Godot serve hosts (runner.gd / serve_game.gd).
"""
