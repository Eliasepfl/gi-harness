"""GDScript VALUE-DEATH G4 attacker — the wiggle-proof softlock demonstration, end-to-end
on ``softlock_wiggle.gd``: a game that certifies G0-G3 via an intended up-and-over route
yet hides a reachable POCKET that CONFINES a body but keeps it MOVING (an aperiodic
rosette across many distinct positions). Elias's exact fear (2026-07-15): the body
WIGGLES, so the motion-based DETECT triggers (frozen + cycle) MISS it; only the motion-
INVARIANT value-death trigger (a collapsed critic V) catches it, and CONFIRM certifies it.

In-image (needs the Godot binary); the harness runs it under apptainer with
HARNESS_GODOT_EXE + HARNESS_GODOT_SPEEDUP=8.

  * softlock_wiggle.gd -> the anti-policy STEERS into the pocket, frozen+cycle MISS the
                          wiggle, value_death CATCHES it, and the G3 tree solver REFUTES
                          every continuation -> a CERTIFIED softlock (grade `open`).
  * mini_collect / losable -> no pocket at those coords -> the critic V never collapses
                          -> ZERO value_death candidates (no false positives).

Design: notes/adversarial/INVERSE_VALUE_G4.md (§VALUE-DEATH) + harness/rl/adversary.py
(detect_value_death) + tests/fixtures/gd_games/softlock_wiggle.gd.
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
from harness.rl.env import PER_BODY  # noqa: E402
from harness.rl.godot_env import GodotServeEnv  # noqa: E402
from harness.verify import g4  # noqa: E402
from harness.verify.gameverify import EFFICACY_EPS, verify_game  # noqa: E402
from harness.verify.godot_exec import find_godot_exe  # noqa: E402

GODOT_EXE = find_godot_exe()
requires_godot = pytest.mark.skipif(GODOT_EXE is None, reason="Godot binary not present")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIGGLE = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "softlock_wiggle.gd")
MINI = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "mini_collect.gd")
LOSABLE = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "losable.gd")

ACTIONS = ["up", "down", "left", "right"]
# An approximate up-and-over safe route (a backplay seed only; never asserted).
WITNESS = ["up"] * 26 + ["right"] * 44 + ["down"] * 10

# The pocket box (mirrors softlock_wiggle.gd POCKET_MIN/MAX) — the critic's collapse region.
# LOW (y in [360,560]), clear of the healthy games' y~300 straight-across lane.
POCKET_MIN = (280.0, 360.0)
POCKET_MAX = (480.0, 560.0)

# Fast, bounded sizing for the in-image funnel (tiny fuzz; small oracle budgets).
SMALL_FUZZ = dict(fuzz_random=6, fuzz_long=4, noop_heavy=4, alt_periods=(1, 2))
IV = dict(iv_seeds=2, iv_eps=0.0, iv_window=6, iv_max_ticks=70,
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


class WiggleCritic:
    """An INJECTED duck-typed critic standing in for a trained G3' policy on the wiggle
    game: (a) its lowest action probability is on ``right`` so the anti-policy ``argmin``
    steers RIGHT into the pocket, and (b) its VALUE COLLAPSES whenever the controlled body
    is inside the pocket box and is high outside — the wiggle-proof value signal a real
    critic would learn (a trap has no path to the goal -> low V no matter how the body
    jiggles). Soundness is critic-independent (CONFIRM certifies regardless of how the
    prefix was found); the trained-model A/B lives in the scripts-level bench."""

    source = "wiggle_critic(injected)"

    def __init__(self, world_size):
        self._w, self._h = float(world_size[0]), float(world_size[1])

    def action_probs(self, obs):
        return np.array([0.30, 0.30, 0.30, 0.10])   # order up/down/left/right -> argmin=right

    def _controlled_pos(self, obs):
        obs = np.asarray(obs, dtype=np.float64).reshape(-1)
        i = 0
        while i + PER_BODY <= obs.size:
            if obs[i] >= 0.5 and obs[i + 9] >= 0.5:            # present & controlled
                return obs[i + 1] * self._w, obs[i + 2] * self._h
            i += PER_BODY
        return None

    def value(self, obs):
        pos = self._controlled_pos(obs)
        if pos is None:
            return 1.0
        px, py = pos
        in_pocket = (POCKET_MIN[0] <= px <= POCKET_MAX[0]
                     and POCKET_MIN[1] <= py <= POCKET_MAX[1])
        return -4.0 if in_pocket else 1.0                     # collapsed in the pocket


def _gd_report(actions, witness_actions):
    return {
        "passed": True, "engine": "gdscript",
        "actions": list(actions),
        "witness": {"seed": 0, "actions": list(witness_actions),
                    "ticks": len(witness_actions), "checkpoints": {}},
        "layers": {"G0_static": {"checks": {"controlled": {"controlled": ["player"]}}}},
    }


def _probe_world_size(game_path):
    env = GodotServeEnv(game_path, port_base=_free_port())
    try:
        return tuple(env.world_size)
    finally:
        env.close()


# ====================================================================== #
# 0. The fixture certifies G0-G3 via the intended route (acceptance premise).
# ====================================================================== #
@requires_godot
def test_softlock_wiggle_certifies_g0_g3():
    """softlock_wiggle.gd passes the FULL G0-G3 funnel: the pocket is invisible to
    certification (geometry open, game solvable over the top) even though the value-death
    attacker certifies its wiggle-softlock (the test below)."""
    rep = verify_game(WIGGLE, sandboxed=False)
    assert rep["passed"] is True, rep
    for layer in ("G0_static", "G0_5_reach", "G1_rollout", "G2_goal", "G3_solve"):
        assert rep["layers"][layer]["passed"] is True, (layer, rep["layers"][layer])
    assert rep["witness"]["actions"], "a real replayable winning witness must exist"


# ====================================================================== #
# 1. The honest hole: frozen+cycle MISS the wiggle, value_death CATCHES it.
# ====================================================================== #
@requires_godot
def test_frozen_cycle_miss_but_value_death_catches_the_wiggle(monkeypatch):
    monkeypatch.setenv("GIP_PORT_BASE", str(_free_port()))
    world = _probe_world_size(WIGGLE)
    crit = WiggleCritic(world)

    # The anti-policy (argmin -> right) drives straight into the low pocket and stays,
    # wiggling on the confined rosette (mirrors the g4 search path).
    env = GodotServeEnv(WIGGLE, port_base=_free_port())
    try:
        roll = adversary.rollout(env, adversary.anti_policy_chooser(crit, eps=0.0),
                                 seed=0, critic=crit, max_ticks=70)
    finally:
        env.close()

    # The tail lands the body in the pocket and it keeps MOVING (wiggle, not a freeze):
    # consecutive tail states differ by more than EFFICACY_EPS.
    tail = roll["fps"][-8:]
    assert any(fp_delta(tail[0], s) > EFFICACY_EPS for s in tail[1:]), \
        "the trapped body must keep MOVING (a wiggle, not a pin)"

    # (i) the MOTION tests MISS it: not frozen (always moving) and not a short cycle
    #     (aperiodic rosette -> no eps-recurrence in a window).
    m_fired, _cut, m_info = adversary.detect_softlock_window(
        roll["fps"], roll["latched"], roll["terminal_tick"], window=6)
    assert m_fired is False, (m_info, "frozen/cycle must MISS the wiggle")

    # (ii) value_death CATCHES it: the critic V has collapsed in the pocket for a full
    #      window (motion-invariant).
    v_fired, v_cut, v_info = adversary.detect_value_death(
        roll["values"], roll["latched"], roll["terminal_tick"], window=6)
    assert v_fired is True and v_info["kind"] == "value_death"
    assert v_info["floor"] is not None and 1 <= v_cut < len(roll["actions"])


# ====================================================================== #
# 2. End-to-end: the attacker certifies the wiggle pocket as a `softlock` (open).
# ====================================================================== #
@requires_godot
def test_softlock_wiggle_certified_end_to_end(monkeypatch):
    monkeypatch.setenv("GIP_PORT_BASE", str(_free_port()))
    src = _src(WIGGLE)
    world = _probe_world_size(WIGGLE)
    out = g4.run_g4(src, _gd_report(ACTIONS, WITNESS), engine="gdscript",
                    slug="softlock_wiggle", game_path=WIGGLE,
                    iv_critic=WiggleCritic(world), tiers=(0,), **IV, **SMALL_FUZZ)

    iv = out["inverse_value"]
    assert iv["status"] == "run"
    assert iv["detected"] >= 1, "the anti-policy attacker must reach the wiggle pocket"
    assert iv["certified"] >= 1, "the tree solver must refute every continuation -> softlock"

    soft = [f for f in out["findings"] if f["outcome"] == "softlock"
            and f["tier"] == "inverse_value"]
    assert soft, "a certified inverse-value softlock finding must be present"
    f = soft[0]
    assert f["hard"] is True and out["grade"] == "open" and out["passed"] is False
    # The certified candidate was found by the MOTION-INVARIANT trigger (the whole point).
    prov = f["reproducer"]["provenance"]
    assert prov["kind"] == "value_death", prov
    assert prov["oracle"] == "inverse_value+tree_refute"

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
# 3. Negative controls: healthy games yield ZERO value_death candidates.
# ====================================================================== #
@requires_godot
@pytest.mark.parametrize("game_path,slug", [(MINI, "mini_collect"), (LOSABLE, "losable")])
def test_healthy_games_yield_no_value_death_candidates(monkeypatch, game_path, slug):
    monkeypatch.setenv("GIP_PORT_BASE", str(_free_port()))
    src = _src(game_path)
    world = _probe_world_size(game_path)
    out = g4.run_g4(src, _gd_report(ACTIONS, []), engine="gdscript", slug=slug,
                    game_path=game_path, iv_critic=WiggleCritic(world), tiers=(0,),
                    **IV, **SMALL_FUZZ)
    iv = out["inverse_value"]
    assert iv["status"] == "run"
    # No pocket at those coords -> the critic V never collapses -> the value-death trigger
    # never fires (no spread / no sub-floor window) -> ZERO value_death candidates.
    vd = [c for c in iv["candidates"] if (c.get("provenance") or {}).get("kind") == "value_death"]
    assert vd == [], f"{slug}: value_death must not fire on a healthy game"
    # And no inverse-value softlock is certified (any incidental motion firing is refuted).
    assert not [f for f in out["findings"]
                if f["outcome"] == "softlock" and f["tier"] == "inverse_value"]
