"""Tests for the G4 adversarial suite (harness.verify.g4).

Everything runs offline and deterministically against the same tiny deterministic
`FakeWorld` the gameverify tests use (imported here), injected through the
executor via `world_factory`. Games are inline source strings crafted to trip a
specific probe. The Tier-1 OpenRouter lane is exercised with a MOCKED network seam
(`g4._attacker_complete`) and a mocked key check (`g4._have_key`) — no network,
ever.

FakeWorld is a frictionless pure integrator: a single impulse carries forever.
Assertions therefore target the SPECIFIC probe signal (a named finding / flag),
not the overall grade, wherever the frictionless physics adds incidental findings.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

# Make `harness` AND the sibling test modules importable regardless of rootdir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness.verify import g4  # noqa: E402
from test_gameverify import GAME_VALID, FakeWorld, factory  # noqa: E402

# Small, fast fuzz sizing for the tests (the defaults run ~600 episodes/game).
SMALL = dict(fuzz_random=25, fuzz_long=10, noop_heavy=8, alt_periods=(1, 2, 3))


def _report(actions_witness, ticks, controlled="player", checkpoints=None):
    """A minimal certified-report stand-in carrying what run_g4 reads."""
    return {
        "passed": True, "engine": "py",
        "witness": {"seed": 0, "actions": list(actions_witness), "ticks": ticks,
                    "checkpoints": checkpoints or {}},
        "layers": {"G0_static": {"checks": {"controlled": {"controlled": [controlled]}}}},
    }


def _write(tmp_path, name, source):
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return str(p)


# ====================================================================== #
# Inline fixtures (each trips ONE probe)
# ====================================================================== #
# Time-based win: success fires on its own after enough steps, no matter what the
# player does. G1's 100-tick noop misses it; the 120-tick avoidance probe catches
# it -> unintended_success (degenerate/unavoidable goal).
DEGENERATE = '''
TITLE = "Timer"
PROMPT = "wins on its own"
ACTIONS = ["a", "b"]

def build(world):
    world.add("ground", shape="box", pos=(400, 10), size=(800, 20), static=True)
    world.add("player", shape="box", pos=(100, 60), size=(20, 20))
    world.control("player")

def act(world, action):
    if action == "a":
        world.impulse("player", (60, 0))
    elif action == "b":
        world.impulse("player", (-60, 0))

def success(world):
    return world.steps >= 18

def checkpoints(world):
    return {"tick_bumped": world.steps >= 6}
'''

# Combo-lock: ~8 consecutive "charge" (which leaks each step) then a "vent" opens
# it. One action alone never wins; avoidance never wins; the body never moves.
# -> tier 0 finds nothing hard -> grade "hardened".
SURVIVOR = '''
TITLE = "Lock"
PROMPT = "charge then release"
ACTIONS = ["charge", "vent"]

def build(world):
    world.add("ground", shape="box", pos=(400, 10), size=(800, 20), static=True)
    world.add("cell", shape="box", pos=(100, 60), size=(20, 20))
    world.control("cell")

def act(world, action):
    c = world.flag("charge", 0)
    if action == "charge":
        world.set_flag("charge", c + 10)
    elif action == "vent":
        if c >= 30:
            world.set_flag("opened", 1)
        else:
            world.set_flag("charge", 0)

def on_step(world):
    c = world.flag("charge", 0)
    if c > 0:
        world.set_flag("charge", max(0, c - 1))

def success(world):
    return world.flag("opened", 0) == 1

def checkpoints(world):
    return {"charged": world.flag("charge", 0) >= 25}
'''

# "blast" hurls the block far right; with no wall it leaves the world -> escape.
ESCAPE = '''
TITLE = "Blast"
PROMPT = "blast the block"
ACTIONS = ["blast", "wait"]

def build(world):
    world.add("ground", shape="box", pos=(400, 10), size=(800, 20), static=True)
    world.add("player", shape="box", pos=(100, 300), size=(20, 20))
    world.control("player")

def act(world, action):
    if action == "blast":
        world.impulse("player", (4000, 0))

def success(world):
    return world.query("player")["pos"][0] > 700

def checkpoints(world):
    return {"moved": world.query("player")["pos"][0] > 300}
'''

# Broken gating: success (reach the "door", x>500) does NOT actually require the
# declared "got_key" checkpoint (rise to y<150). Spamming "door" wins while skipping
# the key band -> a win that bypasses a declared gate -> broken_gating (HARD).
GATING = '''
TITLE = "KeyDoor"
PROMPT = "grab the key up high, then reach the door"
ACTIONS = ["key", "door"]

def build(world):
    world.add("ground", shape="box", pos=(400, 10), size=(800, 20), static=True)
    world.add("player", shape="box", pos=(100, 400), size=(20, 20))
    world.control("player")

def act(world, action):
    if action == "key":
        world.impulse("player", (0, -50))
    elif action == "door":
        world.impulse("player", (50, 0))

def success(world):
    return world.query("player")["pos"][0] > 500

def checkpoints(world):
    return {"got_key": world.query("player")["pos"][1] < 150}
'''

# One repeated action wins quickly on its own.
SINGLE = '''
TITLE = "Go"
PROMPT = "go right"
ACTIONS = ["go", "stay"]

def build(world):
    world.add("ground", shape="box", pos=(400, 10), size=(800, 20), static=True)
    world.add("player", shape="box", pos=(100, 60), size=(20, 20))
    world.control("player")

def act(world, action):
    if action == "go":
        world.impulse("player", (40, 0))

def success(world):
    return world.query("player")["pos"][0] > 130

def checkpoints(world):
    return {"near": world.query("player")["pos"][0] > 115}
'''


def _canned(proposals):
    """A mocked attacker completion returning a JSON array (fenced, to exercise
    the fence-tolerant parser)."""
    def _complete(system, messages, model):
        return "```json\n" + json.dumps(proposals) + "\n```"
    return _complete


# ====================================================================== #
# Expander / vocabulary unit tests
# ====================================================================== #
def test_expand_spam_fills_horizon():
    plan = g4._expand("spam", {"action": "go"}, ["go", "stay"], 12)
    assert plan == ["go"] * 12


def test_expand_alternate_blocks():
    plan = g4._expand("alternate", {"a": "go", "b": "stay", "period": 2},
                      ["go", "stay"], 8)
    assert plan == ["go", "go", "stay", "stay", "go", "go", "stay", "stay"]


def test_expand_rejects_out_of_vocabulary_token():
    with pytest.raises(g4._InvalidPlan):
        g4._expand("spam", {"action": "JUMP"}, ["go", "stay"], 5)
    with pytest.raises(g4._InvalidPlan):
        g4._expand("sequence", {"sequence": ["go", "NOPE"]}, ["go", "stay"], 5)


def test_expand_rejects_unknown_pattern():
    with pytest.raises(g4._InvalidPlan):
        g4._expand("teleport_hack", {}, ["go", "stay"], 5)


def test_noop_and_avoid_are_deterministic():
    assert g4._expand("noop", {}, ["a", "b"], 6) == [None] * 6
    p1 = g4._expand("avoid", {"seed": 3}, ["a", "b"], 30)
    p2 = g4._expand("avoid", {"seed": 3}, ["a", "b"], 30)
    assert p1 == p2 and p1.count(None) > 0        # noop-heavy: mostly idle


# ====================================================================== #
# Referee (classify) unit tests — no physics, hand-built episode dicts
# ====================================================================== #
def _ep(result="budget", ticks=120, checkpoints=None, snapshot=None, nan=False, oob=None):
    return {"result": result, "ticks": ticks, "checkpoints": checkpoints or {},
            "final_snapshot": snapshot or {}, "nan": nan, "oob": oob or []}


def test_classify_nan_and_escape_are_findings():
    o, _ = g4.classify(_ep(result="error", nan=True), "py", avoidance=False,
                       witness_ticks=None, controlled="player", initial_snapshot={})
    assert o == "nan"
    o, ev = g4.classify(_ep(oob=["player"]), "py", avoidance=False,
                        witness_ticks=None, controlled="player", initial_snapshot={})
    assert o == "escape" and ev["escape"] == ["player"]


def test_classify_avoidance_win_is_unintended_success():
    o, _ = g4.classify(_ep(result="success", ticks=40), "py", avoidance=True,
                       witness_ticks=30, controlled="player", initial_snapshot={})
    assert o == "unintended_success"


def test_classify_shortcut_vs_intended():
    fast, _ = g4.classify(_ep(result="success", ticks=3), "py", avoidance=False,
                          witness_ticks=40, controlled="player", initial_snapshot={})
    slow, _ = g4.classify(_ep(result="success", ticks=35), "py", avoidance=False,
                          witness_ticks=40, controlled="player", initial_snapshot={})
    assert fast == "shortcut_beats_witness"
    assert slow == "intended_success"


def test_classify_broken_gating_vs_informational_shortcut():
    # A win that SKIPS a required (declared) checkpoint -> broken_gating (HARD).
    o, ev = g4.classify(
        _ep(result="success", ticks=3, checkpoints={"got_key": None, "at_door": 2}),
        "py", avoidance=False, witness_ticks=40, controlled="player",
        initial_snapshot={}, required_checkpoints=["got_key", "at_door"])
    assert o == "broken_gating"
    assert ev["skipped_checkpoints"] == ["got_key"]
    assert "broken_gating" in g4._HARD_OUTCOMES
    # A fast win that still latches EVERY required gate -> informational shortcut (SOFT).
    o2, _ = g4.classify(
        _ep(result="success", ticks=3, checkpoints={"got_key": 1, "at_door": 2}),
        "py", avoidance=False, witness_ticks=40, controlled="player",
        initial_snapshot={}, required_checkpoints=["got_key", "at_door"])
    assert o2 == "shortcut_beats_witness"
    # No declared gates -> the old shortcut/intended split is unchanged.
    o3, _ = g4.classify(_ep(result="success", ticks=3), "py", avoidance=False,
                        witness_ticks=40, controlled="player", initial_snapshot={})
    assert o3 == "shortcut_beats_witness"


def test_classify_stuck_requires_travel_then_immobility():
    start = {"player": {"pos": [100, 60], "vel": [0, 0]}}
    parked = {"player": {"pos": [300, 60], "vel": [0.0, 0.0]}}
    o, _ = g4.classify(_ep(result="budget", ticks=120, checkpoints={"m": 5},
                           snapshot=parked), "py", avoidance=False, witness_ticks=None,
                       controlled="player", initial_snapshot=start)
    assert o == "stuck"
    # A body that never moved is NOT a soft-lock (non-starter, not a G4 finding).
    still = {"player": {"pos": [100, 60], "vel": [0.0, 0.0]}}
    o2, _ = g4.classify(_ep(result="budget", ticks=120, snapshot=still), "py",
                        avoidance=False, witness_ticks=None, controlled="player",
                        initial_snapshot=start)
    assert o2 == "nothing"


# ====================================================================== #
# Tier 0 — integration probes
# ====================================================================== #
# ====================================================================== #
# Tier 1 — mocked OpenRouter lane (no network)
# ====================================================================== #
# ====================================================================== #
# attack_game — verify-then-attack wiring + certification gate
# ====================================================================== #
def test_attack_game_missing_file():
    out = g4.attack_game("does_not_exist_zzz.py", tiers=(0,), sandboxed=False)
    assert out["grade"] == "error"
    assert out["passed"] is False


# ====================================================================== #
# Finding -> repair-report adapter
# ====================================================================== #
# ---------------------------------------------------------------------------
# B3 smoke — attack a certified Godot spec end to end (skipped without Godot).
# This exercises the SAME wiring the py/js lanes use: detect_engine routes the
# .spec.json to the godot lane, attack_game verifies then hammers it, and the
# router must hand tier 0 a GodotExecutor (not fall through to PyExecutor). The
# fuzz sizing is deliberately tiny to keep the in-image Godot spawns fast.
# ---------------------------------------------------------------------------
from harness.verify.executors import find_godot_exe  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TRAVERSE_SPEC = os.path.join(_ROOT, "tests", "fixtures", "godot_specs", "traverse.spec.json")
requires_godot = pytest.mark.skipif(
    find_godot_exe() is None, reason="Godot binary not present")


def test_make_executor_routes_godot_to_godot_executor():
    # Pure-python guard on the router fix (no Godot needed): a godot engine must
    # get a GodotExecutor.
    from harness.verify.executors import GodotExecutor
    assert isinstance(g4._make_executor("godot", None), GodotExecutor)


def test_make_executor_routes_gdscript_to_gd_executor():
    # Pure-python guard on the gdscript router branch (no Godot spawn): a .gd game
    # must get a GdExecutor over the serve host, never fall to the pymunk default
    # (which would error "invalid syntax" parsing GDScript as Python).
    from harness.verify.gd_exec import GdExecutor
    ex = g4._make_executor("gdscript", None)
    try:
        assert isinstance(ex, GdExecutor)
    finally:
        ex.close()


_GD_MINI = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "mini_collect.gd")
_GD_MINI_3D = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "mini_collect_3d.gd")


@requires_godot
@pytest.mark.parametrize("path", [_GD_MINI, _GD_MINI_3D],
                         ids=["mini_collect_2d", "mini_collect_3d"])
def test_attack_game_routes_and_hardens_a_gd_game(path):
    # The whole point of Track G4-gdscript: a .gd routes through detect_engine to
    # the serve-host executor, attack_game verifies then hammers it, and tier 0
    # returns a REAL grade (not the "invalid syntax" pymunk error). Both the 2D and
    # 3D fixtures must flow through the same engine-agnostic G4 machinery.
    out = g4.attack_game(path, tiers=(0,), sandboxed=False, seed=0,
                         horizon=40, fuzz_random=6, fuzz_long=3, noop_heavy=3,
                         alt_periods=(1, 2), anti_variants=1)
    assert out["engine"] == "gdscript", out
    assert "error" not in out, out
    assert out.get("grade") in ("bulletproof", "hardened", "open"), out
    # ACTIONS recovered from the G1 efficacy report (the shared js/gdscript path).
    assert sorted(out["actions"]) == ["down", "left", "right", "up"], out
    assert out["tier0"]["episodes"] > 0


@requires_godot
def test_attack_game_routes_and_hardens_a_godot_spec():
    out = g4.attack_game(_TRAVERSE_SPEC, tiers=(0,), sandboxed=False, seed=0,
                         horizon=40, fuzz_random=6, fuzz_long=3, noop_heavy=3,
                         alt_periods=(1, 2), anti_variants=1)
    # Routed to the godot lane (not "uncertified"/"error") and actually attacked.
    assert out["engine"] == "godot", out
    assert "error" not in out, out
    assert out.get("grade") in ("bulletproof", "hardened", "open"), out
    # ACTIONS were recovered from the G1 efficacy report (the js/godot path).
    assert out["actions"] == ["run_left", "run_right", "hop"]
    assert out["tier0"]["episodes"] > 0
# ====================================================================== #
# STALE-STATE TIER — softlock triggers (1a/1b) + tree-refutation oracle (1c)
# ====================================================================== #
# Genuine softlock: a momentum pit. "run" builds speed (which DECAYS each step),
# and only a running leap (two consecutive runs, then leap at the lip) clears the
# gap. A slow/idle approach steps into the gap (pos 3-5) and is trapped forever —
# the body then churns in a period-2 cycle and the goal (pos>=12) stays unreached.
# The decay makes it avoidance-safe: sparse/idle play can never build the speed to
# cross, so the ONLY hard finding is the certified softlock.
SOFTLOCK = '''
TITLE = "Pit"
PROMPT = "build speed and leap the gap; a slow step falls in"
ACTIONS = ["run", "leap"]

def build(world):
    world.add("ground", shape="box", pos=(400, 10), size=(800, 20), static=True)
    world.add("player", shape="box", pos=(60, 300), size=(20, 20))
    world.control("player")

def act(world, action):
    dt = world.flag("dt", 0) + 1
    world.set_flag("dt", dt)
    if world.flag("trapped", 0):
        world.teleport("player", (140 if dt % 2 else 120, 340))
        world.set_velocity("player", (0, 0))
        return
    pos = world.flag("pos", 0)
    speed = world.flag("speed", 0)
    if action == "run":
        pos += 1
        speed += 10
    elif action == "leap":
        if pos == 2 and speed >= 8:
            pos = 6                 # a running leap clears the gap (pos 3-5)
            speed = 0
        else:
            pos += 1                # a slow leap just steps forward (into the gap)
    world.set_flag("pos", pos)
    world.set_flag("speed", speed)
    if pos in (3, 4, 5):            # anywhere in the gap -> fall in, no way out
        world.set_flag("trapped", 1)
    world.teleport("player", (60 + pos * 30, 300))
    world.set_velocity("player", (0, 0))

def on_step(world):
    world.set_flag("speed", max(0, world.flag("speed", 0) - 1))   # momentum decays

def success(world):
    return world.flag("pos", 0) >= 12

def checkpoints(world):
    return {"lip": world.flag("pos", 0) >= 2,
            "crossed": world.flag("pos", 0) >= 6}
'''

# Healthy control: a climb with the SAME decay-and-cycle shape but NO dead end.
# "push" adds height, which slides back each step ("coast"), so only sustained
# pushing reaches the top (pos>=48) — yet from ANY state a run of pushes still
# wins. Alternating push/coast makes a period-2 cycle (the 1a trigger fires), but
# the oracle always finds a winning continuation -> refuted, never a softlock.
CONTROL = '''
TITLE = "Climb"
PROMPT = "keep pushing to the top; coasting slides back"
ACTIONS = ["push", "coast"]

def build(world):
    world.add("ground", shape="box", pos=(400, 10), size=(800, 20), static=True)
    world.add("player", shape="box", pos=(60, 300), size=(20, 20))
    world.control("player")

def act(world, action):
    if action == "push":
        world.set_flag("pos", world.flag("pos", 0) + 12)

def on_step(world):
    pos = max(0, world.flag("pos", 0) - 1)
    world.set_flag("pos", pos)
    world.teleport("player", (60 + pos, 300))
    world.set_velocity("player", (0, 0))

def success(world):
    return world.flag("pos", 0) >= 48

def checkpoints(world):
    return {"halfway": world.flag("pos", 0) >= 24}
'''

# Small, fast stale-tier sizing for the tests (bounded oracle + candidate search).
STALE = dict(stale_H=30, stale_budget=2500, stale_cand_budget=1500, top_m=6)


def _frame(tick, x, y=300.0):
    return {"tick": tick,
            "entities": {"player": {"pos": [float(x), float(y)],
                                    "vel": [0.0, 0.0], "angle": 0.0}}}


# -- Triggers (1a / 1b) — pure-function unit tests, no physics ------------- #
def test_trigger_1a_fires_on_a_state_cycle():
    # Last latch at tick 2, then a period-2 oscillation over the no-progress tail.
    frames = [_frame(0, 60), _frame(1, 100), _frame(2, 140)]
    frames += [_frame(t, 120 if t % 2 == 0 else 140, 340) for t in range(3, 40)]
    fired, info = g4.trigger_state_cycling(frames, {"lip": 2}, 39)
    assert fired is True
    assert info["no_recent_latch"] and info["cycle"]
    assert info["cycle_start"] >= 2 and info["cycle_period"] == 2


def test_trigger_1a_silent_while_progressing():
    # A checkpoint latched near the end -> still progressing -> not stale.
    frames = [_frame(t, 60 + t) for t in range(40)]
    fired, info = g4.trigger_state_cycling(frames, {"lip": 38}, 39)
    assert fired is False and info["no_recent_latch"] is False


def test_trigger_1a_silent_on_monotone_drift():
    # Travels forever without repeating a state -> no cycle, no trigger.
    frames = [_frame(t, 60 + 5 * t) for t in range(40)]
    fired, info = g4.trigger_state_cycling(frames, {"lip": None}, 39)
    assert info["no_recent_latch"] is True
    assert fired is False and info["cycle"] is False


def test_trigger_1b_missing_entity_or_escape():
    gone, info = g4.trigger_entity_unreachable(
        {"final_snapshot": {"player": {}}, "oob": []}, {"player": {}, "key": {}})
    assert gone is True and info["missing"] == ["key"]
    esc, info2 = g4.trigger_entity_unreachable(
        {"final_snapshot": {"player": {}, "key": {}}, "oob": ["ball"]},
        {"player": {}, "key": {}})
    assert esc is True and info2["escaped"] == ["ball"]
    ok, _ = g4.trigger_entity_unreachable(
        {"final_snapshot": {"player": {}}, "oob": []}, {"player": {}})
    assert ok is False


# -- Oracle 1c — bounded tree-refutation --------------------------------- #
# -- Grading + registry wiring ------------------------------------------- #
def test_softlock_is_a_hard_outcome_and_maps_to_repair():
    assert "softlock" in g4._HARD_OUTCOMES
    finding = {"outcome": "softlock", "family": "tree_refute", "hard": True,
               "detail": "d", "evidence": {"result": "budget"},
               "reproducer": {"engine": "py", "seed": 0, "action_plan":
                              {"kind": "sequence", "sequence": ["run", "run"]}}}
    rr = g4.to_repair_report(finding)
    assert rr["passed"] is False and rr["failure_class"] == "G4_FINDING"
    assert rr["outcome"] == "softlock" and rr["hint"]
    assert rr["g4_reproducer"]["action_plan"]["sequence"] == ["run", "run"]


def test_stale_seek_registered_but_not_attacker_emittable():
    # Wired into the strategy registry, but hidden from the Tier-1 attacker menu
    # (it names a search the harness drives, not a flat plan an attacker emits).
    assert "stale_seek" in g4.STRATEGY_VOCAB
    assert "stale_seek" in g4._ORACLE_STRATEGIES
    assert "stale_seek" not in g4._vocab_text()
    with pytest.raises(g4._InvalidPlan):
        g4._expand("stale_seek", {}, ["run", "leap"], 5)


# -- End to end through the g4 entry point ------------------------------- #
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
