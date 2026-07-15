"""Tests for the Godot lane (declarative game-spec -> frozen runner.gd).

Two tiers:

* **Pure-python (always run):** the game-spec JSON Schema validates the three
  shipped examples and rejects malformed specs; engine detection routes
  `.spec.json` / `"engine":"godot"` to the Godot lane; the GodotExecutor raises the
  VERIFY_ERROR shape when the binary/runner is missing; and canned "check" facts
  (the exact shape runner.gd emits) flow correctly through the SHARED
  `run_g0_js`/`run_g2_js` layers — proving the Godot check contract without Godot.

* **End-to-end (skipped when the Godot binary is absent):** each example verifies
  COMPLETED with `engine == "godot"`, verification is byte-deterministic across three
  independent runs, a spec missing `checkpoints` fails G0, and a predicate reaching
  for a forbidden token is rejected by the whitelist scan (surfaces at G2).
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.verify.executors import (  # noqa: E402
    GodotExecutor, VerifyError, default_godot_project, find_godot_exe,
)
from harness.verify.gameverify import (  # noqa: E402
    detect_engine, run_g0_js, run_g2_js, verify_game,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXAMPLES = os.path.join(_ROOT, "tests", "fixtures", "godot_specs")
_SCHEMA = os.path.join(_ROOT, "godotworld", "spec.schema.json")
_SPEC_NAMES = ["traverse", "collect2", "escape"]

GODOT_EXE = find_godot_exe()
requires_godot = pytest.mark.skipif(GODOT_EXE is None, reason="Godot binary not present")

_REPORT_KEYS = {"passed", "failure_class", "layers", "hint", "warnings",
                "progress", "witness"}


def _example_path(name: str) -> str:
    return os.path.join(_EXAMPLES, f"{name}.spec.json")


def _load_spec(name: str) -> dict:
    with open(_example_path(name), "r", encoding="utf-8") as fh:
        return json.load(fh)


# ====================================================================== #
# Spec JSON Schema (pure python, always run)
# ====================================================================== #
def test_schema_is_valid_json():
    with open(_SCHEMA, "r", encoding="utf-8") as fh:
        schema = json.load(fh)
    assert schema["title"] == "Godot game-spec v1"
    assert "meta" in schema["required"]


@pytest.mark.parametrize("name", _SPEC_NAMES)
def test_example_specs_match_schema(name):
    import jsonschema
    with open(_SCHEMA, "r", encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.validate(_load_spec(name), schema)  # raises on non-conformance


@pytest.mark.parametrize("name", _SPEC_NAMES)
def test_example_specs_structural_invariants(name):
    spec = _load_spec(name)
    # exactly one controlled dynamic body
    controlled = [b for b in spec["bodies"]
                  if b.get("control") and not b.get("static")]
    assert len(controlled) == 1, name
    # 2..8 declared actions, all bound in `act`
    actions = spec["meta"]["actions"]
    assert 2 <= len(actions) <= 8
    for a in actions:
        assert a in spec["act"], f"{name}: action {a} unbound"
    # 1..6 checkpoints, snake_case keys
    cps = spec["predicates"]["checkpoints"]
    assert 1 <= len(cps) <= 6
    assert all(k.replace("_", "a").isalnum() for k in cps)


def test_schema_rejects_malformed_specs():
    import jsonschema
    with open(_SCHEMA, "r", encoding="utf-8") as fh:
        schema = json.load(fh)
    base = _load_spec("traverse")

    no_meta = {k: v for k, v in base.items() if k != "meta"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(no_meta, schema)

    too_few_actions = json.loads(json.dumps(base))
    too_few_actions["meta"]["actions"] = ["only_one"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(too_few_actions, schema)

    bad_shape = json.loads(json.dumps(base))
    bad_shape["bodies"][0]["shape"] = "triangle"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad_shape, schema)


# ---- heading-control verbs (torque / thrust) + contained() ------------------
def _schema() -> dict:
    with open(_SCHEMA, "r", encoding="utf-8") as fh:
        return json.load(fh)


def test_schema_accepts_torque_and_thrust_verbs():
    import jsonschema
    schema = _schema()
    base = _load_spec("traverse")
    body = base["bodies"][-1]["name"]  # the controlled body
    spec = json.loads(json.dumps(base))
    spec["meta"]["actions"] = ["torque_it", "thrust_it"]
    spec["act"] = {
        "torque_it": [{"verb": "torque", "body": body, "magnitude": 180}],
        # thrust with a signed magnitude + an optional gate is still data-only.
        "thrust_it": [{"verb": "thrust", "body": body, "magnitude": -90,
                       "when": "grounded(\"%s\")" % body}],
    }
    jsonschema.validate(spec, schema)  # raises on non-conformance


def test_schema_rejects_unknown_verb_and_stray_field():
    import jsonschema
    schema = _schema()
    base = _load_spec("traverse")
    body = base["bodies"][-1]["name"]

    unknown = json.loads(json.dumps(base))
    unknown["act"] = {a: [{"verb": "warp", "body": body, "magnitude": 1}]
                      for a in unknown["meta"]["actions"]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(unknown, schema)

    stray = json.loads(json.dumps(base))
    stray["act"] = {a: [{"verb": "thrust", "body": body, "magnitude": 1,
                         "bogus": True}] for a in stray["meta"]["actions"]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(stray, schema)  # additionalProperties: false


def test_schema_accepts_contained_predicate_string():
    # Predicate strings are opaque to the schema (the whitelist scan lives in the
    # frozen runner) — contained(...) must at least validate structurally.
    import jsonschema
    schema = _schema()
    spec = json.loads(json.dumps(_load_spec("traverse")))
    spec["predicates"]["success"] = "contained(\"marble\", \"goal_zone\")"
    spec["predicates"]["checkpoints"] = {
        "parked": "contained(\"marble\", \"goal_zone\")"}
    jsonschema.validate(spec, schema)


# ====================================================================== #
# Engine detection (pure python)
# ====================================================================== #
def test_detect_engine_by_extension():
    assert detect_engine("game.spec.json", "") == "godot"
    assert detect_engine("game.js", "") == "js"
    assert detect_engine("game.py", "") == "py"


def test_detect_engine_by_marker():
    assert detect_engine("g.txt", '{"engine": "godot", "meta": {}}') == "godot"
    assert detect_engine("g.json", '{ "engine":"godot" }') == "godot"
    assert detect_engine("g.txt", "// engine: js\n") == "js"
    assert detect_engine("g.txt", "{}") == "py"


# ====================================================================== #
# GodotExecutor infra failures -> VERIFY_ERROR shape (pure python)
# ====================================================================== #
def test_godot_missing_is_verify_error():
    ex = GodotExecutor(exe="definitely-not-a-real-godot-binary-xyz.exe")
    with pytest.raises(VerifyError) as ei:
        ex.run_check('{"meta": {}}')
    assert ei.value.kind == "godot_missing"
    report = ei.value.as_report()
    assert "error" in report and "layers" not in report  # VERIFY_ERROR shape
    assert report["error"]["type"] == "godot_missing"


def test_godot_runner_missing_is_verify_error(tmp_path):
    # A valid-looking exe path but an empty project dir (no runner.gd).
    fake_exe = tmp_path / "godot.exe"
    fake_exe.write_text("", encoding="utf-8")
    ex = GodotExecutor(exe=str(fake_exe), project=str(tmp_path))
    with pytest.raises(VerifyError) as ei:
        ex.run_check('{"meta": {}}')
    assert ei.value.kind == "godot_runner_missing"
    assert "layers" not in ei.value.as_report()


def test_find_godot_exe_env_override(monkeypatch, tmp_path):
    fake = tmp_path / "my_godot.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setenv("HARNESS_GODOT_EXE", str(fake))
    assert find_godot_exe() == str(fake)
    monkeypatch.setenv("HARNESS_GODOT_EXE", str(tmp_path / "nope.exe"))
    assert find_godot_exe() is None


def test_default_project_has_runner():
    # The lane's frozen interpreter ships in the repo (independent of the binary).
    assert os.path.isfile(os.path.join(default_godot_project(), "runner.gd"))


# ====================================================================== #
# Godot "check" facts flow through the SHARED G0/G2 layers (pure python)
# ====================================================================== #
def _wellformed_facts() -> dict:
    """The exact fact shape runner.gd's check mode emits for a valid 2-body spec."""
    return {
        "mode": "check",
        "scan": [],
        "load": {"ok": True, "error": None},
        "symbols": {
            "defined": {"TITLE": True, "PROMPT": True, "ACTIONS": True,
                        "build": True, "act": True, "success": True,
                        "checkpoints": True},
            "callable": {"build": True, "act": True, "success": True,
                         "checkpoints": True},
        },
        "actions": {"is_list": True, "length": 2, "all_str": True,
                    "values": ["push_left", "push_right"]},
        "world_size": {"declared": [1000.0, 600.0], "effective": [1000.0, 600.0]},
        "build": {"ok": True, "error": None},
        "entities": ["floor", "player"],
        "queries": {
            "floor": {"static": True, "sensor": False, "controlled": False, "in_bounds": True},
            "player": {"static": False, "sensor": False, "controlled": True, "in_bounds": True},
        },
        "penetration": [],
        "g2": {
            "success": {"is_bool": True, "value": False, "deterministic": True,
                        "state_unchanged": True, "error": None},
            "failure": None,
            "checkpoints": {"is_dict": True, "keys": ["moved"], "n": 1,
                            "non_bool_keys": [], "true_keys": [],
                            "deterministic": True, "state_unchanged": True, "error": None},
        },
    }


def test_godot_check_facts_pass_shared_g0_g2():
    facts = _wellformed_facts()
    g0 = run_g0_js(facts)
    assert g0["passed"] is True, g0
    assert g0["checks"]["controlled"]["controlled"] == ["player"]
    g2 = run_g2_js(facts["g2"])
    assert g2["passed"] is True, g2


def test_godot_check_facts_catch_two_controlled():
    facts = _wellformed_facts()
    facts["queries"]["floor"]["controlled"] = True  # two controlled -> G0 fails
    g0 = run_g0_js(facts)
    assert g0["passed"] is False
    assert g0["checks"]["controlled"]["pass"] is False


def test_godot_check_facts_catch_bad_predicate():
    facts = _wellformed_facts()
    facts["g2"]["success"] = {"is_bool": False, "value": None, "deterministic": False,
                              "state_unchanged": False, "error": "identifier not allowed: 'OS'"}
    g2 = run_g2_js(facts["g2"])
    assert g2["passed"] is False
    assert g2["checks"]["success_callable_bool"]["pass"] is False


# ====================================================================== #
# End-to-end verification (skipped without the Godot binary)
# ====================================================================== #
@requires_godot
@pytest.mark.parametrize("name", _SPEC_NAMES)
def test_example_verifies_completed(name):
    rep = verify_game(_example_path(name), sandboxed=False)
    assert rep["passed"] is True, rep
    assert rep["failure_class"] is None
    assert rep["engine"] == "godot"
    assert set(rep) == _REPORT_KEYS | {"engine"}
    for layer in ("G0_static", "G1_rollout", "G2_goal", "G3_solve"):
        assert rep["layers"][layer]["passed"], (name, layer, rep["layers"][layer])
    w = rep["witness"]
    assert w is not None and w["ticks"] >= 20  # non-trivial
    # every declared checkpoint latched on the winning path
    assert all(t is not None for t in w["checkpoints"].values()), w["checkpoints"]


@requires_godot
def test_verify_is_deterministic_x3():
    path = _example_path("traverse")
    reps = [verify_game(path, sandboxed=False) for _ in range(3)]
    ticks = {r["witness"]["ticks"] for r in reps}
    latches = {json.dumps(r["witness"]["checkpoints"], sort_keys=True) for r in reps}
    assert len(ticks) == 1, ticks
    assert len(latches) == 1, latches


@requires_godot
def test_batch_bytes_identical_x3():
    ex = GodotExecutor()
    src = open(_example_path("collect2"), encoding="utf-8").read()
    specs = [{"seed": 0, "actions": ["push_right", "push_right", "push_left"]}]
    runs = [json.dumps(ex.run_batch(src, specs, 3)) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]


@requires_godot
def test_missing_checkpoints_fails_g0(tmp_path):
    spec = _load_spec("traverse")
    del spec["predicates"]["checkpoints"]
    p = tmp_path / "nocp.spec.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    rep = verify_game(str(p), sandboxed=False)
    assert rep["engine"] == "godot"
    assert rep["failure_class"] == "ENV_ERROR"
    symbols = rep["layers"]["G0_static"]["checks"]["symbols"]
    assert symbols["pass"] is False
    assert "checkpoints" in symbols["missing"]


@requires_godot
def test_forbidden_predicate_rejected_by_whitelist(tmp_path):
    # A predicate reaching for a singleton / attribute access must be rejected by the
    # runner's whitelist scan (never evaluated) -> surfaces as a G2 goal error.
    spec = _load_spec("traverse")
    spec["predicates"]["success"] = "OS.get_name() == \"Windows\""
    p = tmp_path / "evil.spec.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    rep = verify_game(str(p), sandboxed=False)
    assert rep["engine"] == "godot"
    assert rep["failure_class"] == "GOAL_ERROR"
    assert rep["layers"]["G2_goal"]["checks"]["success_callable_bool"]["pass"] is False


# ====================================================================== #
# Heading-control verbs + contained() — end-to-end (skipped without Godot)
# ====================================================================== #

# The 2D-parking archetype: a dynamic car, driven by thrust along its heading and
# steered by torque, must end FULLY inside the slot sensor (contained, not merely
# touching). Tuned to be tree-solvable in a few dozen decision ticks.
_PARKING_SPEC = {
    "engine": "godot",
    "spec_version": 1,
    "meta": {
        "title": "Pull Into The Slot",
        "prompt": "Drive the car fully inside the parking slot.",
        "world_size": [1000, 600],
        "actions": ["drive", "spin_left", "spin_right"],
    },
    "bodies": [
        {"name": "floor", "shape": "box", "pos": [500, 25], "size": [1000, 50],
         "static": True, "friction": 0.6},
        {"name": "wall_left", "shape": "box", "pos": [10, 300], "size": [20, 600],
         "static": True},
        {"name": "wall_right", "shape": "box", "pos": [990, 300], "size": [20, 600],
         "static": True},
        # The slot is a generous sensor zone; a level car (60x30) fully fits.
        {"name": "slot", "shape": "box", "pos": [820, 90], "size": [170, 130],
         "static": True, "sensor": True},
        {"name": "car", "shape": "box", "pos": [130, 68], "size": [60, 30],
         "mass": 1.0, "friction": 0.5, "control": True},
    ],
    "act": {
        "drive": [{"verb": "thrust", "body": "car", "magnitude": 95}],
        "spin_left": [{"verb": "torque", "body": "car", "magnitude": 220}],
        "spin_right": [{"verb": "torque", "body": "car", "magnitude": -220}],
    },
    "on_step": [
        {"kind": "velocity_clamp", "body": "car", "vx_max": 210,
         "vy_min": -600, "vy_max": 400},
    ],
    "predicates": {
        "success": "contained(\"car\", \"slot\")",
        "checkpoints": {
            "rolling": "pos_x(\"car\") > 260",
            "approaching": "pos_x(\"car\") > 560",
            "parked": "contained(\"car\", \"slot\")",
        },
    },
}


@requires_godot
def test_parking_spec_verifies_end_to_end(tmp_path):
    """thrust + torque + contained() drive the full G0-G3 funnel: the tree solver
    finds a replayable witness that parks the car fully inside the slot."""
    p = tmp_path / "parking.spec.json"
    p.write_text(json.dumps(_PARKING_SPEC), encoding="utf-8")
    rep = verify_game(str(p), sandboxed=False)
    assert rep["passed"] is True, rep
    assert rep["failure_class"] is None
    assert rep["engine"] == "godot"
    for layer in ("G0_static", "G1_rollout", "G2_goal", "G3_solve"):
        assert rep["layers"][layer]["passed"], (layer, rep["layers"][layer])
    w = rep["witness"]
    assert w is not None and w["ticks"] >= 20                 # non-trivial park
    assert all(t is not None for t in w["checkpoints"].values()), w["checkpoints"]


@requires_godot
def test_contained_is_stricter_than_contacts():
    """contained(a, b) is FULL AABB containment, not the overlap contacts() reports.
    Same scene: the wide car OVERLAPS the narrow slot (contacts latches) yet never
    fits inside it (contained never latches), while a chip fully inside the slot
    IS contained. Driven directly through the executor (no G0/G2 false-at-t0 gate)."""
    ex = GodotExecutor()
    spec = {
        "engine": "godot",
        "meta": {"title": "strict", "prompt": "strict",
                 "actions": ["wait", "nudge"]},
        "bodies": [
            {"name": "floor", "shape": "box", "pos": [400, 50], "size": [800, 100],
             "static": True},
            # zone: a NARROW sensor (40 wide) — the car cannot fit across it.
            {"name": "zone", "shape": "box", "pos": [400, 200], "size": [40, 240],
             "static": True, "sensor": True},
            # chip: small static box parked fully inside the zone.
            {"name": "chip", "shape": "box", "pos": [400, 200], "size": [10, 10],
             "static": True},
            # car: WIDER (80) than the zone, resting on the floor, overlapping it.
            {"name": "car", "shape": "box", "pos": [400, 120], "size": [80, 40],
             "mass": 1.0, "locked_rotation": True, "control": True},
        ],
        "act": {
            "wait": [{"verb": "impulse", "body": "car", "vec": [0, 0]}],
            "nudge": [{"verb": "impulse", "body": "car", "vec": [10, 0]}],
        },
        "predicates": {
            "success": "contained(\"car\", \"chip\")",  # never (car is huge)
            "checkpoints": {
                "touch": "contacts(\"car\", \"zone\")",       # overlap -> latches
                "chip_inside": "contained(\"chip\", \"zone\")",  # fully in -> latches
                "car_inside": "contained(\"car\", \"zone\")",    # pokes out -> never
            },
        },
    }
    rec = ex.run_batch(json.dumps(spec), [{"seed": 0, "actions": [None, None, None]}], 3)[0]
    cps = rec["checkpoints"]
    assert cps["touch"] is not None, cps        # the car DOES overlap the zone
    assert cps["chip_inside"] is not None, cps  # a body fully inside IS contained
    assert cps["car_inside"] is None, cps       # overlapping-but-not-contained -> false


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
