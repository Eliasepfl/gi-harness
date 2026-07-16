"""A/B bench: S1 GREEDY anti-policy (adversary.search) vs S1.5 POLICY-GUIDED DESCENT
(adversary.descent_search) on a SINGLE-STEP softlock (softlock_pit.gd) AND a MULTI-STEP-
route softlock (softlock_maze.gd), counting candidates + CERTIFIED softlocks per 1k SEARCH
ticks at the SAME per-arm tick budget. CONFIRM (one tree-solve per candidate) is the
shared downstream cost.

The claim under test (STALE_SEEKING_PLAN.md §3.1): on the MULTI-STEP fixture the greedy
argmin-from-0 attacker cannot COMPOSE the pocket entry (it dives straight and misses),
while descent NAVIGATES to a low-V pocket-band waypoint and then alpha-ramps in. On the
SINGLE-STEP pit both trip it, so descent should roughly tie greedy there. Report honestly.

Both arms use the SAME injected scripted critic (a stand-in for a trained G3' policy —
soundness is critic-independent: DETECT+CONFIRM certify regardless of how the prefix was
found). Run inside the certifier image:

    HARNESS_GODOT_SPEEDUP=8 python scripts/descent_ab.py [--budget N]
"""
import argparse
import os
import socket
import sys

import numpy as np


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


os.environ.setdefault("GIP_PORT_BASE", str(_free_port()))
sys.path.insert(0, os.getcwd())

from harness.rl import adversary                      # noqa: E402
from harness.rl.godot_env import GodotServeEnv        # noqa: E402
from harness.verify import g4                         # noqa: E402
from harness.verify.gd_exec import GdExecutor         # noqa: E402

ROOT = os.getcwd()
PIT = os.path.join(ROOT, "tests", "fixtures", "gd_games", "softlock_pit.gd")
MAZE = os.path.join(ROOT, "tests", "fixtures", "gd_games", "softlock_maze.gd")
ACTIONS = ["up", "down", "left", "right"]


class PitCritic:
    """Single-step pit: the competent policy avoids driving straight RIGHT into the
    central pit, so argmin steers RIGHT — straight in."""
    source = "pit_critic"

    def action_probs(self, obs):
        return np.array([0.30, 0.30, 0.30, 0.10])     # up,down,left,right; argmin=right

    def value(self, obs):
        return 0.0


class MazeCritic:
    """Multi-step maze: competent policy TRAVELS right, argmin dives DOWN; V is low near
    the pocket BOX so low-V waypoints target the pocket band. See test_gd_descent.py."""
    source = "maze_critic"

    def action_probs(self, obs):
        return np.array([0.15, 0.05, 0.10, 0.70])     # up,down,left,right; argmin=down

    def value(self, obs):
        o = np.asarray(obs, dtype=float).reshape(-1)
        if o.size <= 2:
            return 0.0
        nx, ny = float(o[1]), float(o[2])
        dx = max(0.0, 0.20 - nx, nx - 0.575)
        dy = max(0.0, 0.633 - ny, ny - 0.933)
        return float(dx + dy)


def _confirm(src, candidates, top_m=8, H=30, budget=4000):
    """Refute each candidate prefix through the shared tree oracle; count certified."""
    ex = GdExecutor(port_base=_free_port())
    certified = 0
    seen = set()
    try:
        for c in candidates[:top_m]:
            pref = tuple(c["prefix"])
            if not pref or pref in seen:
                continue
            seen.add(pref)
            try:
                res = g4.refute_prefix(ex, src, ACTIONS, list(pref), H=H, budget=budget,
                                       engine="gdscript")
            except Exception:
                continue
            if res["certified"]:
                certified += 1
    finally:
        ex.close()
    return certified


def _greedy(game_path, critic, witness, budget):
    env = GodotServeEnv(game_path, port_base=_free_port())
    try:
        res = adversary.search(env, critic, seeds=list(range(8)), eps=0.0, window=6,
                               witness_actions=witness, max_ticks=60, budget_ticks=budget)
    finally:
        env.close()
    return res


def _descent(game_path, critic, witness, budget):
    env = GodotServeEnv(game_path, port_base=_free_port())
    try:
        res = adversary.descent_search(env, critic, witness_actions=witness,
                                       n_waypoints=8, descent_ticks=40, eps=0.0,
                                       window=6, max_ticks=60, budget_ticks=budget)
    finally:
        env.close()
    return res


def bench(name, game_path, critic, witness, budget):
    src = open(game_path).read()
    rows = []
    for arm, fn in (("S1_greedy", _greedy), ("S1.5_descent", _descent)):
        os.environ["GIP_PORT_BASE"] = str(_free_port())
        res = fn(game_path, critic, witness, budget)
        cands = res["candidates"]
        cert = _confirm(src, cands)
        rows.append((arm, res["ticks_simulated"], res["detections"], len(cands), cert))
    print(f"\nA/B on {name} ({os.path.basename(game_path)}, budget={budget} ticks/arm)")
    print(f"{'arm':16} {'ticks':>7} {'detect':>7} {'cands':>6} {'cert':>5} "
          f"{'det/1k':>7} {'cert/1k':>8}")
    for arm, ticks, det, nc, cert in rows:
        k = max(1, ticks) / 1000.0
        print(f"{arm:16} {ticks:>7} {det:>7} {nc:>6} {cert:>5} "
              f"{det / k:>7.2f} {cert / k:>8.2f}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=2400)
    args = ap.parse_args()
    pit_witness = ["up"] * 14 + ["right"] * 44 + ["down"] * 14
    maze_witness = ["up"] * 16 + ["right"] * 30
    bench("SINGLE-STEP pit", PIT, PitCritic(), pit_witness, args.budget)
    bench("MULTI-STEP maze", MAZE, MazeCritic(), maze_witness, args.budget)


if __name__ == "__main__":
    main()
