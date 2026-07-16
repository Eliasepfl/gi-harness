"""stale_seek — the Godot end-to-end smoke (skipped when the Godot binary is absent).

Trains a REAL PPO stale-seeker on the ``softlock_pit.gd`` fixture over the batched
serve env, harvests candidates, and refutes them through the CONFIRM oracle — the honest
train -> harvest -> certify loop. Kept tiny (small budget, 2 instances, short horizon, a
short freeze window) so it runs in a couple of minutes under Slurm.

Also pins the EXPECTED ladder behaviour on a SIMPLE fixture (mission A/B prediction): the
cheap greedy anti-policy already certifies the pit, so ``attack_game(deep=True)`` correctly
SKIPS the expensive seeker — "the trained seeker loses on the simple fixture" is the point.
"""
from __future__ import annotations

import os
import socket
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.rl import stale_seek as ss  # noqa: E402
from harness.verify import g4  # noqa: E402
from harness.verify.executors import find_godot_exe  # noqa: E402
from harness.verify.gameverify import verify_game  # noqa: E402
from harness.verify.gd_exec import GdExecutor  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIT = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "softlock_pit.gd")

GODOT_EXE = find_godot_exe()
requires_godot = pytest.mark.skipif(GODOT_EXE is None, reason="Godot binary not present")

# Small, fast seeker sizing for the smoke (a real PPO run, just budget-bounded).
SMOKE = dict(budget_steps=3000, num_envs=2, seed=0, horizon=60, num_steps=64,
             patience=999)
SMOKE_PARAMS = ss.SeekParams(window=5, mobility_min=10.0, horizon=60)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(autouse=True)
def _disjoint_ports(monkeypatch):
    # One Slurm-task-style base; the seeker hands its probe/vec/eval envs disjoint offsets.
    monkeypatch.setenv("GIP_PORT_BASE", str(_free_port()))
    monkeypatch.setenv("HARNESS_GODOT_SPEEDUP", os.environ.get("HARNESS_GODOT_SPEEDUP", "8"))


@requires_godot
def test_softlock_pit_certifies_through_the_gd_funnel():
    report = verify_game(PIT, sandboxed=False)
    assert report.get("passed") is True, report.get("hint")
    assert report.get("engine") == "gdscript"


@requires_godot
def test_trained_seeker_finds_and_certifies_a_softlock():
    # Train the seeker; even an early policy spam-runs into the pit, so window-complete
    # candidates accumulate during training.
    trained = ss.train_stale_seeker(PIT, params=SMOKE_PARAMS, **SMOKE)
    candidates = list(trained["candidates"])

    # Harvest a few more with the trained policy (seed 0 == what CONFIRM replays at).
    def make_env():
        from harness.rl.godot_env import GodotServeEnv
        return GodotServeEnv(PIT, horizon=60)
    candidates += ss.harvest_candidates(make_env, trained["policy"], seeds=(0,),
                                        waypoints=(0,), params=SMOKE_PARAMS)
    assert len(candidates) >= 1, "the seeker discovered no stale candidates"

    # CONFIRM: at least one frozen prefix certifies as a softlock via the real oracle.
    ex = GdExecutor()
    try:
        res = ss.confirm_candidates(ex, open(PIT).read(), trained["actions"], candidates,
                                    H=30, budget=3000, engine="gdscript", top_m=6)
    finally:
        close = getattr(ex, "close", None)
        if callable(close):
            close()
    assert res["certified"] >= 1, res
    f = res["findings"][0]
    assert f["outcome"] == "softlock" and f["hard"] is True
    assert f["reproducer"]["provenance"]["discovered_by"] == "trained_ppo_seeker"


@requires_godot
def test_deep_attack_defers_to_the_cheap_tier_on_the_simple_pit():
    # On the SIMPLE pit the cheap greedy anti-policy already certifies, so the deep
    # seeker must be SKIPPED (no wasted PPO training) — the mission's expected result.
    out = g4.attack_game(PIT, tiers=(0,), sandboxed=False, deep=True,
                         stale_H=30, stale_budget=2500, stale_cand_budget=1500, top_m=6)
    assert out["grade"] == "open"                         # the softlock is certified
    assert out["stale"]["certified"] >= 1                 # by the CHEAP tier
    assert out["seeker"]["status"] == "skipped"           # deep tier correctly deferred
    assert "already certified" in out["seeker"]["reason"]
