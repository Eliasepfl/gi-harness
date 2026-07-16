"""GDScript S1.5 POLICY-GUIDED DESCENT attacker — the REAL serve-env descent search wired
to the tree-refutation CONFIRM, end-to-end on a MULTI-STEP-route fixture whose softlock
pocket a greedy argmin-from-0 attacker cannot compose but the descent attacker can.
In-image (needs the Godot binary); the harness runs it under apptainer with
HARNESS_GODOT_EXE + HARNESS_GODOT_SPEEDUP=8.

  * softlock_maze.gd -> the pocket is off BOTH axes from the start (needs travel RIGHT
                        then turn DOWN); descent NAVIGATES to a low-V waypoint and then
                        alpha-ramps in -> a CERTIFIED softlock (grade `open`). The
                        greedy inverse-value tier (S1) largely misses it (measured A/B in
                        scripts/descent_ab.py).
  * mini_collect.gd  -> no pocket exists -> ZERO certified softlocks (negative control).
  * return determinism -> replaying the travel-then-turn prefix is bit-identical run to
                        run at speedup 8 and the handoff state IS the frozen pocket.

Design: notes/adversarial/STALE_SEEKING_PLAN.md §3.1 (S1.5) + INVERSE_VALUE_G4.md.
"""

from __future__ import annotations

import os
import socket
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness.core.statetree import fingerprint, fp_delta  # noqa: E402
from harness.rl import adversary  # noqa: E402
from harness.rl.godot_env import GodotServeEnv  # noqa: E402
from harness.verify import g4  # noqa: E402
from harness.verify.gameverify import EFFICACY_EPS, verify_game  # noqa: E402
from harness.verify.godot_exec import find_godot_exe  # noqa: E402

GODOT_EXE = find_godot_exe()
requires_godot = pytest.mark.skipif(GODOT_EXE is None, reason="Godot binary not present")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAZE = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "softlock_maze.gd")
MINI = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "mini_collect.gd")

ACTIONS = ["up", "down", "left", "right"]
# An approximate up-and-across safe route (backplay seed only; not asserted).
WITNESS = ["up"] * 16 + ["right"] * 30

# Fast, bounded sizing for the in-image funnel (tiny fuzz; small oracle budgets).
SMALL_FUZZ = dict(fuzz_random=4, fuzz_long=2, noop_heavy=2, alt_periods=(1,))
DESC = dict(descent_ticks=40, descent_waypoints=6, iv_eps=0.0,
            stale_H=30, stale_budget=4000, top_m=4)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _src(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


class MazeCritic:
    """An INJECTED duck-typed critic standing in for a trained G3' policy. It TRAVELS
    right (a competent navigator that keeps the body alive + mobile) and rarely goes
    DOWN, so ``argmin`` (the anti-policy) dives DOWN — which from the start column misses
    the pocket (x too small) but from a travelled-right / pocket-band waypoint drops
    straight in. V is LOW near the pocket BOX (what a real critic assigns a can't-win dead
    region), so the low-V waypoint selection targets the pocket band and descent's
    competent-right phase then walks in. Soundness is critic-independent (DETECT+CONFIRM
    certify regardless); the trained-model A/B lives in the scripts-level bench.

    Obs layout (build_obs_vector): [present, px/W, py/H, ...] per body, W×H = 800×600."""

    source = "maze_critic(injected)"

    def action_probs(self, obs):
        return np.array([0.15, 0.05, 0.10, 0.70])   # up, down, left, right; argmin=down

    def value(self, obs):
        o = np.asarray(obs, dtype=float).reshape(-1)
        if o.size <= 2:
            return 0.0
        nx, ny = float(o[1]), float(o[2])            # normalized player pos
        # Pocket box x in [160,460], y in [380,560] -> normalized against 800x600.
        dx = max(0.0, 0.20 - nx, nx - 0.575)
        dy = max(0.0, 0.633 - ny, ny - 0.933)
        return float(dx + dy)                        # 0 (lowest) inside the pocket band


def _gd_report(actions, witness_actions):
    return {
        "passed": True, "engine": "gdscript",
        "actions": list(actions),
        "witness": {"seed": 0, "actions": list(witness_actions),
                    "ticks": len(witness_actions), "checkpoints": {}},
        "layers": {"G0_static": {"checks": {"controlled": {"controlled": ["player"]}}}},
    }


# ====================================================================== #
# 0. The fixture's two-sided guarantee: CERTIFIABLE yet ATTACKABLE
# ====================================================================== #
@requires_godot
def test_softlock_maze_certifies_g0_g3():
    """softlock_maze.gd passes the FULL G0-G3 funnel (the pocket is invisible to
    certification — geometry open, game solvable up-and-across) even though the descent
    attacker can certify its softlock (the test below)."""
    rep = verify_game(MAZE, sandboxed=False)
    assert rep["passed"] is True, rep
    for layer in ("G0_static", "G0_5_reach", "G1_rollout", "G2_goal", "G3_solve"):
        assert rep["layers"][layer]["passed"] is True, (layer, rep["layers"][layer])
    assert rep["witness"]["actions"], "a real replayable winning witness must exist"


# ====================================================================== #
# 1. End-to-end: the descent attacker certifies the pocket as a `softlock`
# ====================================================================== #
@requires_godot
def test_softlock_maze_descent_certified_end_to_end(monkeypatch):
    monkeypatch.setenv("GIP_PORT_BASE", str(_free_port()))
    src = _src(MAZE)
    out = g4.run_g4(src, _gd_report(ACTIONS, WITNESS), engine="gdscript",
                    slug="softlock_maze", game_path=MAZE,
                    descent_critic=MazeCritic(), tiers=(0,), **DESC, **SMALL_FUZZ)

    desc = out["descent"]
    assert desc["status"] == "run"
    assert desc["critic_source"] == "maze_critic(injected)"
    assert desc["detected"] >= 1, "the descent attacker must reach the frozen pocket"
    assert desc["certified"] >= 1, "the tree solver must refute every continuation -> softlock"

    soft = [f for f in out["findings"] if f["outcome"] == "softlock"
            and f["tier"] == "descent"]
    assert soft, "a certified descent softlock finding must be present"
    f = soft[0]
    assert f["hard"] is True and f["family"] == "policy_descent+tree_refute"
    assert out["grade"] == "open" and out["passed"] is False

    prov = f["reproducer"]["provenance"]
    assert prov["oracle"] == "policy_descent+tree_refute"
    assert prov["critic_source"] == "maze_critic(injected)"
    assert prov["engine"] == "gdscript" and "subtree_status" in prov
    assert isinstance(f["repair_hint"], str) and f["repair_hint"]

    # Replayable witness: the persisted reproducer re-certifies on a FRESH executor.
    from harness.verify.gd_exec import GdExecutor
    ap = f["reproducer"]["action_plan"]
    assert ap["kind"] == "sequence" and ap["sequence"]
    ex = GdExecutor(port_base=_free_port())
    try:
        recheck = g4.refute_prefix(ex, src, out["actions"], ap["sequence"],
                                   H=30, budget=4000, engine="gdscript")
    finally:
        ex.close()
    assert recheck["certified"] is True, "the reproducer must re-certify deterministically"


# ====================================================================== #
# 2. Negative control: a solvable game yields ZERO certified softlocks
# ====================================================================== #
@requires_godot
def test_mini_collect_descent_no_false_positive(monkeypatch):
    monkeypatch.setenv("GIP_PORT_BASE", str(_free_port()))
    src = _src(MINI)
    out = g4.run_g4(src, _gd_report(ACTIONS, []), engine="gdscript", slug="mini_collect",
                    game_path=MINI, descent_critic=MazeCritic(), tiers=(0,),
                    **DESC, **SMALL_FUZZ)
    desc = out["descent"]
    assert desc["status"] == "run"
    # Any incidental DETECT firing is REFUTED by the oracle (a win exists) -> 0 certified.
    assert desc["certified"] == 0, "mini_collect has no pocket -> no certified softlock"
    assert not [f for f in out["findings"]
                if f["outcome"] == "softlock" and f["tier"] == "descent"]


# ====================================================================== #
# 3. Return-phase prefix replay: deterministic + lands in the frozen pocket (req)
# ====================================================================== #
@requires_godot
def test_return_prefix_replay_is_deterministic_and_lands_in_pocket():
    # A travel-right-then-turn-down prefix reaches the multi-step pocket.
    pocket_prefix = ["right"] * 8 + ["down"] * 14

    def _replay():
        env = GodotServeEnv(MAZE, port_base=_free_port())
        try:
            roll = adversary.rollout(env, adversary.random_chooser(), seed=0,
                                     prefix=pocket_prefix, max_ticks=len(pocket_prefix))
        finally:
            env.close()
        return roll

    roll1 = _replay()
    roll2 = _replay()

    assert roll1["handoff_tick"] == len(pocket_prefix)
    fp1 = roll1["fps"][roll1["handoff_tick"]]
    fp2 = roll2["fps"][roll2["handoff_tick"]]
    # Determinism: two independent serve sessions replay the prefix BIT-IDENTICALLY
    # through the lockstep frame-stepping at HARNESS_GODOT_SPEEDUP (never time_scale).
    assert fp1 == fp2, "travel-then-turn prefix replay must be bit-identical across sessions"

    # The handoff state IS the frozen pocket: the tail of the replay does not move.
    tail = roll1["fps"][-6:]
    assert all(fp_delta(tail[0], s) < EFFICACY_EPS for s in tail[1:]), \
        "the travel-then-turn prefix must land the body in the frozen pocket"

    # DETECT agrees this is a softlock window (frozen, no checkpoint churn, alive).
    fired, _cut, info = adversary.detect_softlock_window(
        roll1["fps"], roll1["latched"], roll1["terminal_tick"], window=6)
    assert fired is True and info["kind"] == "frozen"
