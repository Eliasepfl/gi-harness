"""A/B bench: random fuzz vs greedy anti-policy vs the TRAINED seeker on a softlock
fixture, counting candidates + certified per 1k SEARCH ticks with HONEST accounting
(the seeker's total INCLUDES its PPO training ticks).

Run inside the certifier image:
    HARNESS_GODOT_SPEEDUP=8 python scripts/stale_seek_ab.py [game.gd] [--budget N]

"Ticks" = simulation/env ticks spent DISCOVERING candidates (the search currency the
mission asks for). CONFIRM (one tree-solve per candidate) is a shared downstream cost.
The three searches run SEQUENTIALLY (each closes its Godot serve before the next) so
they never contend for a loopback port.
"""
import argparse
import os
import random
import socket
import sys


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# A free base BEFORE any executor/env imports read GIP_PORT_BASE.
os.environ.setdefault("GIP_PORT_BASE", str(_free_port()))
sys.path.insert(0, os.getcwd())

from harness.verify.gd_exec import GdExecutor              # noqa: E402
from harness.verify import g4, treesolve as ts             # noqa: E402
from harness.rl import stale_seek as ss                    # noqa: E402

ACTIONS_DEFAULT = ["run", "leap"]


def _detect(specs, episodes, initial):
    """Trip the SAME g4 DETECT triggers on replayed episodes -> suspect prefixes."""
    cands, seen = [], set()
    for plan, ep in zip(specs, episodes):
        if ep.get("result") != "budget":
            continue
        fa, ia = g4.trigger_state_cycling(ep.get("frames", []), ep.get("checkpoints", {}),
                                          int(ep.get("ticks", 0)))
        fb, _ = g4.trigger_entity_unreachable(ep, initial)
        if not (fa or fb):
            continue
        cut = ia["cycle_start"] if fa and ia["cycle_start"] is not None else g4._last_latch(
            ep.get("checkpoints", {}))
        cut = max(1, min(int(cut), len(ep.get("actions", plan))))
        pref = tuple(ep.get("actions", plan)[:cut])
        if pref and pref not in seen:
            seen.add(pref)
            cands.append(list(pref))
    return cands


def _certify(src, actions, cands, engine):
    ex = GdExecutor() if engine == "gdscript" else _py_exec()
    try:
        res = ss.confirm_candidates(ex, src, actions, [{"seed": 0, "prefix": p} for p in cands],
                                    H=30, budget=3000, engine=engine, top_m=8, probe=False)
    finally:
        c = getattr(ex, "close", None)
        if callable(c):
            c()
    return res["certified"]


def _py_exec():
    from harness.verify.executors import PyExecutor
    from test_gameverify import factory  # type: ignore
    return PyExecutor(world_factory=factory())


def bench(game_path, budget_steps=4000, horizon=40, n_fuzz=60, engine="gdscript"):
    src = open(game_path).read()
    actions = list(ACTIONS_DEFAULT)
    initial = {}
    rows = []

    # -- 1) RANDOM FUZZ ---------------------------------------------------
    rng = random.Random(0)
    specs = [{"seed": 0, "actions": [actions[rng.randrange(len(actions))]
                                     for _ in range(horizon)]} for _ in range(n_fuzz)]
    ex = GdExecutor() if engine == "gdscript" else _py_exec()
    try:
        eps = ex.run_batch(src, specs, horizon, frames_every=1)
        fuzz_ticks = sum(int(e.get("ticks", 0)) for e in eps)
        fuzz_cands = _detect(specs, eps, initial)
    finally:
        c = getattr(ex, "close", None)
        if callable(c):
            c()
    rows.append(["random_fuzz", fuzz_ticks, fuzz_cands])

    # -- 2) GREEDY ANTI-POLICY (spam+alternate coverage + inverted tree search) --
    plans = [[a] * horizon for a in actions]
    plans += [([a, b] * (horizon // 2 + 1))[:horizon] for a in actions for b in actions if a != b]
    ex = GdExecutor() if engine == "gdscript" else _py_exec()
    try:
        _, inv_eps, _, gtree = ts._tree_search(ex, src, actions, horizon,
                                               select=ts._select_leaves_inverted, budget=3000)
        for ep in inv_eps:
            if ep.get("actions"):
                plans.append(list(ep["actions"]))
        gspecs = [{"seed": 0, "actions": p} for p in plans]
        geps = ex.run_batch(src, gspecs, horizon, frames_every=1)
        greedy_ticks = sum(int(e.get("ticks", 0)) for e in geps) + int(gtree.ticks_simulated)
        greedy_cands = _detect(gspecs, geps, initial)
    finally:
        c = getattr(ex, "close", None)
        if callable(c):
            c()
    rows.append(["greedy_anti_policy", greedy_ticks, greedy_cands])

    # -- 3) TRAINED SEEKER (PPO) — HONEST: training ticks count -----------
    if engine == "gdscript":
        params = ss.SeekParams(window=5, mobility_min=10.0, horizon=horizon)
        trained = ss.train_stale_seeker(game_path, budget_steps=budget_steps, num_envs=2,
                                        seed=0, horizon=horizon, params=params,
                                        num_steps=64)
        train_ticks = int(trained["train_res"].get("global_steps", budget_steps))
        cands = [c["prefix"] for c in trained["candidates"]]

        def make_env():
            from harness.rl.godot_env import GodotServeEnv
            return GodotServeEnv(game_path, horizon=horizon)
        harvest = ss.harvest_candidates(make_env, trained["policy"], seeds=(0,),
                                        waypoints=(0,), params=params)
        harvest_ticks = sum(len(c["prefix"]) + params.window for c in harvest)
        cands += [c["prefix"] for c in harvest]
        uniq = [list(t) for t in {tuple(p) for p in cands if p}]
        rows.append(["trained_seeker(+train)", train_ticks + harvest_ticks, uniq])

    # CONFIRM each method's candidates (shared downstream cost, fresh executor).
    out = []
    for name, ticks, cands in rows:
        cert = _certify(src, actions, cands, engine)
        out.append((name, ticks, len(cands), cert))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game", nargs="?", default="tests/fixtures/gd_games/softlock_pit.gd")
    ap.add_argument("--budget", type=int, default=4000)
    ap.add_argument("--horizon", type=int, default=40)
    ap.add_argument("--engine", default="gdscript")
    args = ap.parse_args()
    rows = bench(args.game, budget_steps=args.budget, horizon=args.horizon, engine=args.engine)
    print(f"\nA/B on {os.path.basename(args.game)} (engine={args.engine}, "
          f"port_base={os.environ.get('GIP_PORT_BASE')})")
    print(f"{'method':24} {'ticks':>9} {'cands':>6} {'cert':>5} {'cand/1k':>8} {'cert/1k':>8}")
    for name, ticks, cands, cert in rows:
        k = max(1, ticks) / 1000.0
        print(f"{name:24} {ticks:>9} {cands:>6} {cert:>5} {cands / k:>8.2f} {cert / k:>8.2f}")


if __name__ == "__main__":
    main()
