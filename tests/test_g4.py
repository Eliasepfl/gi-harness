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
from harness.verify.executors import PyExecutor  # noqa: E402
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
def test_avoidance_catches_degenerate_goal():
    out = g4.run_g4(DEGENERATE, _report(["a"] * 3, 3, checkpoints={"tick_bumped": 1}),
                    engine="py", world_factory=factory(), tiers=(0,), **SMALL)
    assert out["grade"] == "open"
    assert out["passed"] is False
    assert out["tier0"]["avoidance"]["unintended_success"] > 0
    assert out["tier0"]["avoidance"]["passed"] is False
    hard = [f for f in out["findings"] if f["outcome"] == "unintended_success"]
    assert hard and all(f["hard"] for f in hard)


def test_certified_survivor_grades_hardened():
    out = g4.run_g4(SURVIVOR, _report(["charge"] * 8 + ["vent"], 9,
                                      controlled="cell", checkpoints={"charged": 7}),
                    engine="py", world_factory=factory(), tiers=(0,), **SMALL)
    # No HARD findings survive: no avoidance win, no escape/NaN.
    assert out["passed"] is True
    assert out["grade"] == "hardened"          # tier1 not run -> short of bulletproof
    assert out["tier0"]["counts"]["unintended_success"] == 0
    assert out["tier0"]["counts"]["escape"] == 0
    assert out["tier0"]["counts"]["nan"] == 0
    assert out["hard_findings"] == []


def test_single_action_win_is_flagged_with_tick_count():
    out = g4.run_g4(SINGLE, _report(["go"] * 10, 10, checkpoints={"near": 5}),
                    engine="py", world_factory=factory(), tiers=(0,), **SMALL)
    flags = out["tier0"]["single_action_win"]["flags"]
    actions = {f["action"] for f in flags}
    assert "go" in actions
    go = next(f for f in flags if f["action"] == "go")
    assert isinstance(go["ticks"], int) and go["ticks"] >= 1
    assert go["hard"] is False                 # a flag, not a hard fail
    assert go["reproducer"]["action_plan"]["pattern"] == "spam"


def test_fuzz_detects_escape_and_reproducer_replays():
    out = g4.run_g4(ESCAPE, _report(["blast"] * 6, 6, checkpoints={"moved": 1}),
                    engine="py", world_factory=factory(), tiers=(0,), **SMALL)
    escapes = [f for f in out["findings"] if f["outcome"] == "escape"]
    assert escapes and out["tier0"]["counts"]["escape"] > 0
    assert out["grade"] == "open"

    # The stored reproducer replays to the SAME escape on a fresh seeded world.
    ex = PyExecutor(world_factory=factory())
    rep = escapes[0]["reproducer"]
    ap = rep["action_plan"]
    plan = (ap["sequence"] if ap.get("kind") == "sequence"
            else g4._expand(ap["pattern"], ap["params"], ["blast", "wait"], g4.PROBE_HORIZON))
    ep = ex.run_batch(ESCAPE, [{"seed": rep["seed"], "actions": plan}],
                      g4.PROBE_HORIZON, escape_margin=g4.ESCAPE_MARGIN)[0]
    assert ep["oob"], "reproducer did not reproduce the escape"


def test_shortcut_beats_witness_is_soft():
    # Certified witness is (declared) slow (40 ticks); fuzz wins far faster.
    out = g4.run_g4(SINGLE, _report(["go"] * 40, 40, checkpoints={"near": 20}),
                    engine="py", world_factory=factory(), tiers=(0,), **SMALL)
    shortcuts = [f for f in out["findings"] if f["outcome"] == "shortcut_beats_witness"]
    assert shortcuts, "a fast win under a slow witness should be a shortcut"
    assert all(f["hard"] is False for f in shortcuts)


def test_fuzz_is_deterministic_under_seed():
    args = dict(engine="py", world_factory=factory(), tiers=(0,), seed=11, **SMALL)
    a = g4.run_g4(ESCAPE, _report(["blast"] * 6, 6), **args)
    b = g4.run_g4(ESCAPE, _report(["blast"] * 6, 6), **args)
    key = lambda o: json.dumps(o["findings"], sort_keys=True, default=str)
    assert key(a) == key(b)
    assert a["tier0"]["counts"] == b["tier0"]["counts"]


def test_report_schema_shape():
    out = g4.run_g4(SURVIVOR, _report(["charge"] * 8 + ["vent"], 9, controlled="cell"),
                    engine="py", world_factory=factory(), tiers=(0,), **SMALL)
    for key in ("schema", "game", "engine", "grade", "passed", "tiers_run",
                "tier0", "tier1", "findings", "hard_findings"):
        assert key in out, key
    assert out["schema"] == g4.SCHEMA
    for key in ("avoidance", "single_action_win", "breaker", "shortcut",
                "findings", "families", "counts", "episodes"):
        assert key in out["tier0"], key


# ====================================================================== #
# Tier 1 — mocked OpenRouter lane (no network)
# ====================================================================== #
def test_tier1_mocked_lane_classifies_and_persists(monkeypatch):
    proposals = [
        {"hypothesis": "blast flies out",
         "action_plan": {"pattern": "spam", "params": {"action": "blast"}}},   # hit: escape
        {"hypothesis": "idle does nothing",
         "action_plan": {"pattern": "spam", "params": {"action": "wait"}}},     # misconception
        {"hypothesis": "unknown move",
         "action_plan": {"pattern": "spam", "params": {"action": "JUMP"}}},     # incomprehension
        {"hypothesis": "explicit seq",
         "action_plan": {"kind": "sequence", "sequence": ["blast", "wait"]}},   # hit: escape
    ]
    monkeypatch.setattr(g4, "_have_key", lambda: True)
    monkeypatch.setattr(g4, "_attacker_complete", _canned(proposals))

    out = g4.run_g4(ESCAPE, _report(["blast"] * 6, 6), engine="py",
                    world_factory=factory(), tiers=(0, 1),
                    models=["qwen/qwen-2.5-coder:free"], **SMALL)
    t1 = out["tier1"]
    assert t1["status"] == "run"
    assert t1["models"] == ["qwen/qwen-2.5-coder:free"]

    # Every proposal + outcome is persisted (traceability).
    assert len(t1["records"]) == 4
    classes = sorted(r["failure_class"] for r in t1["records"])
    assert classes == ["hit", "hit", "incomprehension", "misconception"]
    incomp = next(r for r in t1["records"] if r["failure_class"] == "incomprehension")
    assert "JUMP" in incomp["reason"] and incomp["outcome"] == "incomprehension"

    # Attacker leaderboard stats add up.
    a = t1["attackers"][0]
    assert a["attacker_id"] == "qwen-2.5-coder#lane0"
    assert a["attempts"] == 4
    assert a["findings"] == 2 and a["incomprehension"] == 1 and a["misconception"] == 1

    # Findings surfaced at the top level, tagged tier 1 with an attacker id.
    t1_findings = [f for f in out["findings"] if f["tier"] == 1]
    assert t1_findings and all(f["attacker_id"] == "qwen-2.5-coder#lane0"
                               for f in t1_findings)


def test_tier1_multiple_lanes(monkeypatch):
    monkeypatch.setattr(g4, "_have_key", lambda: True)
    monkeypatch.setattr(g4, "_attacker_complete", _canned(
        [{"action_plan": {"pattern": "spam", "params": {"action": "wait"}}}]))
    out = g4.run_g4(ESCAPE, _report(["blast"] * 6, 6), engine="py",
                    world_factory=factory(), tiers=(0, 1),
                    models=["m/one:free", "m/two:free"], **SMALL)
    ids = [a["attacker_id"] for a in out["tier1"]["attackers"]]
    assert ids == ["one#lane0", "two#lane1"]


def test_tier1_skipped_without_key(monkeypatch):
    # Force "no key": tier 1 must skip cleanly while tier 0 still runs.
    monkeypatch.setattr(g4, "_have_key", lambda: False)

    def _boom(*a, **k):  # network must never be touched when there is no key
        raise AssertionError("attacker completion called without a key")
    monkeypatch.setattr(g4, "_attacker_complete", _boom)

    out = g4.run_g4(ESCAPE, _report(["blast"] * 6, 6), engine="py",
                    world_factory=factory(), tiers=(0, 1), **SMALL)
    assert out["tier1"]["status"] == "skipped_no_key"
    assert out["tier1"]["reason"]
    assert out["tier0"]["episodes"] > 0        # tier 0 stood alone


def test_tier1_not_requested_is_skipped():
    out = g4.run_g4(SURVIVOR, _report(["charge"] * 8 + ["vent"], 9, controlled="cell"),
                    engine="py", world_factory=factory(), tiers=(0,), **SMALL)
    assert out["tier1"]["status"] == "skipped_not_requested"
    # A clean tier 0 without a tier-1 pass is "hardened", never "bulletproof".
    assert out["grade"] == "hardened"


def test_bulletproof_requires_clean_tier1(monkeypatch):
    # A survivor whose tier-1 attackers all whiff -> bulletproof.
    monkeypatch.setattr(g4, "_have_key", lambda: True)
    monkeypatch.setattr(g4, "_attacker_complete", _canned(
        [{"action_plan": {"pattern": "spam", "params": {"action": "vent"}}}]))
    out = g4.run_g4(SURVIVOR, _report(["charge"] * 8 + ["vent"], 9, controlled="cell"),
                    engine="py", world_factory=factory(), tiers=(0, 1),
                    models=["m/x:free"], **SMALL)
    assert out["tier1"]["status"] == "run"
    assert not out["tier1"]["findings"]
    assert out["grade"] == "bulletproof"
    assert out["passed"] is True


# ====================================================================== #
# attack_game — verify-then-attack wiring + certification gate
# ====================================================================== #
def test_attack_game_end_to_end(tmp_path, monkeypatch):
    # GAME_VALID is a legacy-scale fast-win fixture; pin the v2.2 G3 thresholds
    # (like test_gameverify.legacy_thresholds) so certification passes and this
    # test exercises the G4 wiring, not the v2.3 duration bar.
    from harness.verify import gameverify as gv
    monkeypatch.setattr(gv, "TRIVIAL_TICKS", 5)
    monkeypatch.setattr(gv, "PROBE_HORIZON", 120)
    path = _write(tmp_path, "valid.py", GAME_VALID)
    out = g4.attack_game(path, tiers=(0,), sandboxed=False, world_factory=factory(),
                         **SMALL)
    assert out["schema"] == g4.SCHEMA
    assert out["engine"] == "py"
    assert out["grade"] in ("open", "hardened", "bulletproof")
    assert out["tier0"]["episodes"] > 0
    assert set(out["actions"]) == {"right", "left"}


def test_attack_game_refuses_uncertified(tmp_path):
    # A game missing `success` fails G0 -> not certified -> G4 refuses to attack.
    broken = GAME_VALID.replace("def success(world):", "def not_success(world):")
    path = _write(tmp_path, "broken.py", broken)
    out = g4.attack_game(path, tiers=(0,), sandboxed=False, world_factory=factory())
    assert out["grade"] == "uncertified"
    assert out["passed"] is False
    assert "findings" in out and out["findings"] == []


def test_attack_game_missing_file():
    out = g4.attack_game("does_not_exist_zzz.py", tiers=(0,), sandboxed=False)
    assert out["grade"] == "error"
    assert out["passed"] is False


# ====================================================================== #
# Finding -> repair-report adapter
# ====================================================================== #
def test_to_repair_report_shape():
    finding = {"outcome": "escape", "family": "spam", "hard": True,
               "detail": "drove player out", "reproducer": {"engine": "py"},
               "evidence": {"result": "budget"}}
    rr = g4.to_repair_report(finding)
    assert rr["passed"] is False
    assert rr["failure_class"] == "G4_FINDING"
    assert rr["outcome"] == "escape"
    assert rr["hint"] and rr["g4_reproducer"] == {"engine": "py"}


# ---------------------------------------------------------------------------
# B3 smoke — attack a certified Godot spec end to end (skipped without Godot).
# This exercises the SAME wiring the py/js lanes use: detect_engine routes the
# .spec.json to the godot lane, attack_game verifies then hammers it, and the
# router must hand tier 0 a GodotExecutor (not fall through to PyExecutor). The
# fuzz sizing is deliberately tiny to keep the in-image Godot spawns fast.
# ---------------------------------------------------------------------------
from harness.verify.executors import find_godot_exe  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TRAVERSE_SPEC = os.path.join(_ROOT, "godotworld", "examples", "traverse.spec.json")
requires_godot = pytest.mark.skipif(
    find_godot_exe() is None, reason="Godot binary not present")


def test_make_executor_routes_godot_to_godot_executor():
    # Pure-python guard on the router fix (no Godot needed): a godot engine must
    # get a GodotExecutor, never the pymunk default.
    from harness.verify.executors import GodotExecutor, PyExecutor
    assert isinstance(g4._make_executor("godot", None), GodotExecutor)
    assert isinstance(g4._make_executor("py", None), PyExecutor)


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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
