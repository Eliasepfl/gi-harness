"""Tests for the Godot lane's spec-v2 raycast obs sensors.

Two tiers, mirroring ``test_godot_exec.py``:

* **Pure-python (always run):** the ``sensors`` block validates against the
  game-spec JSON Schema; malformed sensor entries (bad ``type``, unknown field,
  missing ``attach_to``, over-max ``n_rays``) are rejected; ``spec_version: 2`` is
  accepted; and the vendored sensor scripts are present, MIT-attributed and
  stripped of the editor-only / AGENT-group coupling.

* **End-to-end (skipped when the Godot binary is absent):** a minimal spec with a
  raycast fan on a ball that falls toward the floor. The runner appends a
  ``get_observation()`` obs tail to each per-tick frame AND the episode record; the
  tail has ``n_rays`` entries in [0, 1]; a frame with the ball NEARER the floor
  reads a LARGER proximity (the vendored ``(len-dist)/len`` convention, asserted);
  the tail is byte-deterministic across two same-seed runs; and a sensor-free spec
  emits no ``obs`` key at all (non-regression on the frozen runner's output).
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.verify.executors import GodotExecutor, find_godot_exe  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXAMPLES = os.path.join(_ROOT, "tests", "fixtures", "godot_specs")
_SCHEMA = os.path.join(_ROOT, "godotworld", "spec.schema.json")
_SENSORS_DIR = os.path.join(_ROOT, "godotworld", "addons", "sensors")
_RUNNER = os.path.join(_ROOT, "godotworld", "runner.gd")

GODOT_EXE = find_godot_exe()
requires_godot = pytest.mark.skipif(GODOT_EXE is None, reason="Godot binary not present")


def _schema() -> dict:
    with open(_SCHEMA, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_example(name: str) -> dict:
    with open(os.path.join(_EXAMPLES, f"{name}.spec.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _valid_sensor() -> dict:
    return {"type": "raycast2d", "attach_to": "marble", "n_rays": 12,
            "ray_length": 250, "cone_width_deg": 180, "collision_mask": 1}


# ====================================================================== #
# Schema — the sensors block (pure python, always run)
# ====================================================================== #
def test_schema_defines_sensor():
    schema = _schema()
    assert "sensor" in schema["definitions"]
    assert "sensors" in schema["properties"]
    assert schema["definitions"]["sensor"]["additionalProperties"] is False


def test_schema_accepts_sensors_block():
    import jsonschema
    spec = _load_example("traverse")
    spec["spec_version"] = 2
    spec["sensors"] = [_valid_sensor()]
    jsonschema.validate(spec, _schema())  # raises on non-conformance


def test_schema_accepts_spec_version_2():
    import jsonschema
    spec = _load_example("traverse")
    spec["spec_version"] = 2
    jsonschema.validate(spec, _schema())


def test_schema_rejects_bad_sensor_type():
    import jsonschema
    spec = _load_example("traverse")
    sensor = _valid_sensor()
    sensor["type"] = "lidar"  # not in the enum
    spec["sensors"] = [sensor]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(spec, _schema())


def test_schema_rejects_unknown_sensor_field():
    import jsonschema
    spec = _load_example("traverse")
    sensor = _valid_sensor()
    sensor["hit_from_inside"] = True  # additionalProperties: false
    spec["sensors"] = [sensor]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(spec, _schema())


def test_schema_rejects_sensor_missing_attach_to():
    import jsonschema
    spec = _load_example("traverse")
    sensor = _valid_sensor()
    del sensor["attach_to"]
    spec["sensors"] = [sensor]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(spec, _schema())


def test_schema_rejects_n_rays_over_max():
    import jsonschema
    spec = _load_example("traverse")
    sensor = _valid_sensor()
    sensor["n_rays"] = 128  # maximum is 64
    spec["sensors"] = [sensor]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(spec, _schema())


# ====================================================================== #
# Vendored sensor scripts — provenance & de-coupling (pure python)
# ====================================================================== #
def test_vendored_sensor_files_present():
    for fn in ("ISensor2D.gd", "RaycastSensor2D.gd"):
        assert os.path.isfile(os.path.join(_SENSORS_DIR, fn)), fn


def test_vendored_raycast_is_decoupled_and_attributed():
    with open(os.path.join(_SENSORS_DIR, "RaycastSensor2D.gd"), "r", encoding="utf-8") as fh:
        src = fh.read()
    # MIT attribution kept (edbeeching/godot_rl_agents, examples pin d659636).
    assert "MIT" in src
    assert "Edward Beeching" in src
    assert "d659636" in src
    # Editor-only branches and AGENT/group coupling stripped -> plain node.
    assert "@tool" not in src
    assert "is_editor_hint" not in src
    assert "AGENT" not in src and "add_to_group" not in src
    # Extends the base by resource path (load()-by-path, no global-class-cache dep).
    assert 'extends "res://addons/sensors/ISensor2D.gd"' in src
    # The proximity convention we assert in the e2e: (ray_length - distance)/ray_length.
    assert "(ray_length - distance) / ray_length" in src


def test_runner_preloads_sensor_scripts():
    # GODOT_DOCS_MINING.md section 2/3: the finite sensor whitelist is PRELOADED (a
    # compile-time const table) so there is no load() hitch in the physics-sensitive
    # rebuild path and a bad path fails fast at boot instead of silently dropping a sensor.
    with open(_RUNNER, "r", encoding="utf-8") as fh:
        src = fh.read()
    assert 'preload("res://addons/sensors/RaycastSensor2D.gd")' in src
    # No runtime load()-by-string of the whitelist survives in the rebuild path.
    assert "load(SENSOR_SCRIPTS" not in src
    # _add_sensor resolves the preloaded Script from the const table.
    assert "SENSOR_SCRIPTS.get(stype" in src


# ====================================================================== #
# End-to-end — obs tail on a real Godot episode (skipped without the binary)
# ====================================================================== #
_N_RAYS = 8


def _falling_ball_spec(n_rays: int = _N_RAYS, ray_length: int = 300) -> dict:
    """A ball dropped above a full-width floor with a 360deg raycast fan on it.

    Sensor-free semantics are deliberately trivial (noop actions); the ONLY point
    of interest is the obs tail as the ball nears the floor under gravity."""
    return {
        "engine": "godot",
        "spec_version": 2,
        "meta": {
            "title": "raycast probe",
            "prompt": "a ball falls toward the floor while a raycast fan reads proximity",
            "world_size": [400, 400],
            "actions": ["noop", "noop2"],
        },
        "bodies": [
            {"name": "floor", "shape": "box", "pos": [200, 20], "size": [400, 40],
             "static": True, "friction": 0.7},
            {"name": "ball", "shape": "circle", "pos": [200, 320], "radius": 15,
             "mass": 1.0, "control": True},
        ],
        "act": {"noop": [], "noop2": []},
        "sensors": [
            {"type": "raycast2d", "attach_to": "ball", "n_rays": n_rays,
             "ray_length": ray_length, "cone_width_deg": 360, "collision_mask": 1},
        ],
        "predicates": {
            "success": "flag(\"never\")",
            "checkpoints": {"descended": "pos_y(\"ball\") < 200"},
        },
    }


@requires_godot
def test_raycast_obs_tail_shape_and_proximity():
    ex = GodotExecutor()
    src = json.dumps(_falling_ball_spec())
    episodes = [{"seed": 0, "actions": [None] * 16}]
    recs = ex.run_batch(src, episodes, 16, frames_every=1)
    assert len(recs) == 1
    ep = recs[0]
    assert ep.get("error") is None, ep

    # Episode-level obs tail: n_rays entries, all normalized to [0, 1].
    assert "obs" in ep, ep
    assert len(ep["obs"]) == _N_RAYS
    assert all(0.0 <= v <= 1.0 for v in ep["obs"]), ep["obs"]

    # Per-tick frames each carry the same-length obs tail.
    frames = ep["frames"]
    assert len(frames) >= 3
    for fr in frames:
        assert len(fr["obs"]) == _N_RAYS
        assert all(0.0 <= v <= 1.0 for v in fr["obs"]), fr["obs"]

    # Vendored convention: proximity = (ray_length - distance)/ray_length, so a body
    # NEARER the floor reads a LARGER value. Compare the closest vs the farthest frame.
    by_y = sorted(frames, key=lambda fr: fr["entities"]["ball"]["pos"][1])
    nearest, farthest = by_y[0], by_y[-1]
    assert nearest["entities"]["ball"]["pos"][1] < farthest["entities"]["ball"]["pos"][1]
    assert max(nearest["obs"]) > max(farthest["obs"])  # nearer => larger proximity
    assert max(nearest["obs"]) > 0.0                    # the floor is actually detected


@requires_godot
def test_raycast_obs_deterministic_same_seed():
    ex = GodotExecutor()
    src = json.dumps(_falling_ball_spec())
    episodes = [{"seed": 0, "actions": [None] * 12}]
    run_a = ex.run_batch(src, episodes, 12, frames_every=1)[0]
    run_b = ex.run_batch(src, episodes, 12, frames_every=1)[0]
    assert run_a["obs"] == run_b["obs"]
    assert [fr["obs"] for fr in run_a["frames"]] == [fr["obs"] for fr in run_b["frames"]]


@requires_godot
def test_sensorless_spec_emits_no_obs():
    # Non-regression: a spec with no `sensors` block is byte-for-byte unchanged --
    # neither the episode record nor its frames gain an `obs` key.
    ex = GodotExecutor()
    src = open(os.path.join(_EXAMPLES, "traverse.spec.json"), encoding="utf-8").read()
    episodes = [{"seed": 0, "actions": ["run_right", "run_right", "hop"]}]
    ep = ex.run_batch(src, episodes, 3, frames_every=1)[0]
    assert "obs" not in ep
    assert all("obs" not in fr for fr in ep["frames"])


@requires_godot
def test_bad_attach_target_is_ignored():
    # A sensor naming a non-existent body attaches nothing -> no obs tail, no crash.
    ex = GodotExecutor()
    spec = _falling_ball_spec()
    spec["sensors"][0]["attach_to"] = "no_such_body"
    ep = ex.run_batch(json.dumps(spec), [{"seed": 0, "actions": [None] * 4}], 4)[0]
    assert ep.get("error") is None, ep
    assert "obs" not in ep


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
