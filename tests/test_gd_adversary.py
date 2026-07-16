"""GDScript inverse-value G4 attacker — the REAL serve-env SEARCH+DETECT wired to the
tree-refutation CONFIRM, end-to-end on a certifiable fixture that hides a reachable
softlock pocket (Elias's smart tier). In-image (needs the Godot binary); the harness
runs it under apptainer with HARNESS_GODOT_EXE + HARNESS_GODOT_SPEEDUP=8.

  * softlock_pit.gd  -> the attacker STEERS into the frozen pocket, DETECT fires, and the
                        G3 tree solver REFUTES every continuation -> a CERTIFIED softlock
                        with a replayable {seed, actions} witness (grade `open`).
  * mini_collect.gd  -> no pocket exists -> ZERO certified softlocks (the negative control).
  * prefix-seeding    -> replaying a witness prefix through the lockstep serve is
                        bit-identical run-to-run at speedup 8, and the handoff state IS
                        the frozen pocket (determinism req + Go-Explore/Backplay handoff).

Design: notes/adversarial/INVERSE_VALUE_G4.md + notes/adversarial/FEASIBILITY_LITERATURE.md
(§4 the Engine.time_scale determinism caveat: replay rides LOCKSTEP frame-stepping).
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
SOFTLOCK_PIT = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "softlock_pit.gd")
MINI = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "mini_collect.gd")

ACTIONS = ["up", "down", "left", "right"]
# An approximate up-and-over safe route (used only as a backplay seed; not asserted).
WITNESS = ["up"] * 14 + ["right"] * 44 + ["down"] * 14

# Fast, bounded sizing for the in-image funnel (tiny fuzz; small oracle budgets).
SMALL_FUZZ = dict(fuzz_random=6, fuzz_long=4, noop_heavy=4, alt_periods=(1, 2))
IV = dict(iv_seeds=2, iv_eps=0.0, iv_window=6, iv_max_ticks=60,
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


class PitCritic:
    """An INJECTED duck-typed critic standing in for a trained G3' policy: a competent
    policy AVOIDS driving straight ``right`` into the central pit, so its lowest action
    probability is on ``right`` — and the anti-policy ``argmin`` therefore steers RIGHT,
    into the freeze pocket. Soundness is critic-independent (DETECT+CONFIRM certify the
    softlock regardless of how the prefix was found); the trained-model A/B lives in the
    scripts-level bench."""

    source = "pit_critic(injected)"

    def action_probs(self, obs):
        return np.array([0.30, 0.30, 0.30, 0.10])   # order up/down/left/right -> argmin=right

    def value(self, obs):
        return 0.0


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
def test_softlock_pit_certifies_g0_g3():
    """softlock_pit.gd passes the FULL G0-G3 funnel (the acceptance premise: the pocket
    is invisible to certification — geometry open, game solvable over the top) even
    though the inverse-value attacker can certify its softlock (the test below)."""
    rep = verify_game(SOFTLOCK_PIT, sandboxed=False)
    assert rep["passed"] is True, rep
    for layer in ("G0_static", "G0_5_reach", "G1_rollout", "G2_goal", "G3_solve"):
        assert rep["layers"][layer]["passed"] is True, (layer, rep["layers"][layer])
    assert rep["witness"]["actions"], "a real replayable winning witness must exist"


# ====================================================================== #
# 1. End-to-end: the attacker certifies the pit as a `softlock` (grade open)
# ====================================================================== #
@requires_godot
def test_softlock_pit_certified_end_to_end(monkeypatch):
    monkeypatch.setenv("GIP_PORT_BASE", str(_free_port()))
    src = _src(SOFTLOCK_PIT)
    out = g4.run_g4(src, _gd_report(ACTIONS, WITNESS), engine="gdscript",
                    slug="softlock_pit", game_path=SOFTLOCK_PIT,
                    iv_critic=PitCritic(), tiers=(0,), **IV, **SMALL_FUZZ)

    iv = out["inverse_value"]
    assert iv["status"] == "run"
    assert iv["critic_source"] == "pit_critic(injected)"
    assert iv["detected"] >= 1, "the anti-policy attacker must reach the frozen pocket"
    assert iv["certified"] >= 1, "the tree solver must refute every continuation -> softlock"

    soft = [f for f in out["findings"] if f["outcome"] == "softlock"
            and f["tier"] == "inverse_value"]
    assert soft, "a certified inverse-value softlock finding must be present"
    f = soft[0]
    assert f["hard"] is True and f["family"] == "inverse_value+tree_refute"
    assert out["grade"] == "open" and out["passed"] is False
    # The PRIMARY smart tier leads the ladder.
    assert out["findings"][0]["tier"] == "inverse_value"

    prov = f["reproducer"]["provenance"]
    assert prov["oracle"] == "inverse_value+tree_refute"
    assert prov["critic_source"] == "pit_critic(injected)"
    assert prov["engine"] == "gdscript" and "subtree_status" in prov
    # The repair hint names the last checkpoint latched before the freeze (or None-safe).
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
def test_mini_collect_yields_no_certified_softlock(monkeypatch):
    monkeypatch.setenv("GIP_PORT_BASE", str(_free_port()))
    src = _src(MINI)
    out = g4.run_g4(src, _gd_report(ACTIONS, []), engine="gdscript", slug="mini_collect",
                    game_path=MINI, iv_critic=PitCritic(), tiers=(0,), **IV, **SMALL_FUZZ)
    iv = out["inverse_value"]
    assert iv["status"] == "run"
    # Any incidental DETECT firing is REFUTED by the oracle (a win exists) -> 0 certified.
    assert iv["certified"] == 0, "mini_collect has no pocket -> no certified softlock"
    assert not [f for f in out["findings"]
                if f["outcome"] == "softlock" and f["tier"] == "inverse_value"]


# ====================================================================== #
# 3. Prefix-seeding handoff + lockstep determinism (req 6)
# ====================================================================== #
@requires_godot
def test_prefix_seeding_handoff_is_deterministic_and_lands_in_pocket():
    src_actions = ACTIONS
    pit_prefix = ["right"] * 22             # straight into the central pit from the start

    def _replay():
        env = GodotServeEnv(SOFTLOCK_PIT, port_base=_free_port())
        try:
            roll = adversary.rollout(env, adversary.random_chooser(), seed=0,
                                     prefix=pit_prefix, max_ticks=len(pit_prefix))
        finally:
            env.close()
        return roll

    roll1 = _replay()
    roll2 = _replay()

    # Control handed off exactly at the end of the replayed prefix.
    assert roll1["handoff_tick"] == len(pit_prefix)
    fp1 = roll1["fps"][roll1["handoff_tick"]]
    fp2 = roll2["fps"][roll2["handoff_tick"]]

    # Determinism: two independent serve sessions replay the prefix BIT-IDENTICALLY
    # through the lockstep frame-stepping at HARNESS_GODOT_SPEEDUP (never time_scale).
    assert fp1 == fp2, "witness-prefix replay must be bit-identical across serve sessions"

    # The handoff state IS the frozen pocket: the tail of the replay does not move.
    tail = roll1["fps"][-6:]
    assert all(fp_delta(tail[0], s) < EFFICACY_EPS for s in tail[1:]), \
        "the seeded prefix must land the body in the frozen pocket"

    # And DETECT agrees this is a softlock window (frozen, no checkpoint churn, alive).
    fired, _cut, info = adversary.detect_softlock_window(
        roll1["fps"], roll1["latched"], roll1["terminal_tick"], window=6)
    assert fired is True and info["kind"] == "frozen"


# ====================================================================== #
# 4. The certified finding carries the ENGINE-TRUTH frozen state (Elias directive):
#    the real serve host reports the player pinned inside the pit box, the goal named
#    among the nearest bodies, and the enrichment feeds the repair directive.
# ====================================================================== #
@requires_godot
def test_softlock_pit_finding_carries_frozen_state(monkeypatch):
    monkeypatch.setenv("GIP_PORT_BASE", str(_free_port()))
    src = _src(SOFTLOCK_PIT)
    out = g4.run_g4(src, _gd_report(ACTIONS, WITNESS), engine="gdscript",
                    slug="softlock_pit", game_path=SOFTLOCK_PIT,
                    iv_critic=PitCritic(), tiers=(0,), **IV, **SMALL_FUZZ)

    soft = [f for f in out["findings"] if f["outcome"] == "softlock"
            and f["tier"] == "inverse_value"]
    assert soft, "a certified inverse-value softlock finding must be present"
    fs = soft[0]["frozen_state"]
    # The engine froze the player INSIDE the central pit box (PIT_MIN..PIT_MAX in the fixture).
    assert fs["controlled"]["name"] == "player"
    px, py = fs["controlled"]["pos"]
    assert 280.0 <= px <= 480.0 and 240.0 <= py <= 560.0, fs["controlled"]
    assert fs["controlled"]["vel"] is not None
    assert fs["dimension"] == 2
    assert isinstance(fs["ticks_elapsed"], int) and fs["ticks_elapsed"] >= 1
    # The goal marker is named among the nearest OTHER bodies.
    assert "goal" in [b["name"] for b in fs["nearby"]]
    # The pit is a LOGIC trap with no collision footprint -> no enclosing geometry (graceful).
    assert fs["enclosing"] == []

    # The repair directive rendered from this finding RAISES the engine facts.
    from harness.gen import feedback as F
    d = [x for x in F.compile_directives({"g4": out}) if x.source == "softlock"][0]
    assert str(int(px)) in d.text and "goal" in d.text
    assert "PROVED" in d.text.upper() or "no continuation" in d.text.lower()
    assert d.detail.get("frozen_state") == fs
