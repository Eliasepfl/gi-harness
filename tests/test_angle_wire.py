"""The angle wire contract: scalar in 2D, scalar-yaw OR [x,y,z] Euler in 3D.

Before harness/core/wire.py, every consumer coerced ``float(angle)`` and a 3D game
reporting the NATURAL vector (``rotation_degrees`` is a Vector3) crashed the serve
host mid-frame — a truncated, unparseable frame surfacing as VERIFY_ERROR. The
2026-07-17 ambition probe lost all three of its 3D prompts (cargo port, stacking,
submarine) to exactly this while every 2D prompt ran fine. The contract prompt never
said "scalar"; the harness punished a compliant reading.

Offline half: the wire helpers. Godot half: the fixed host serves an [x,y,z]-angle
game (parse + twin determinism) and scalar games stay byte-identical.
"""
from __future__ import annotations

import os
import socket

import pytest

from harness.core.wire import angle_components, angle_delta, angle_yaw
from harness.verify.executors import find_godot_exe
from harness.verify.gd_exec import GdExecutor

GODOT_EXE = find_godot_exe()
requires_godot = pytest.mark.skipif(GODOT_EXE is None, reason="Godot binary not present")

_HERE = os.path.dirname(os.path.abspath(__file__))
_FIX = os.path.join(_HERE, "fixtures", "gd_games")


def _src(name: str) -> str:
    with open(os.path.join(_FIX, name)) as f:
        return f.read()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --- offline: the helpers -----------------------------------------------------

def test_angle_components_scalar_and_vector():
    assert angle_components(1.5) == (1.5,)
    assert angle_components([1.0, 2.0, 3.0]) == (1.0, 2.0, 3.0)
    assert angle_components((0.5, 0.25)) == (0.5, 0.25)


def test_angle_components_never_raises_on_wire_garbage():
    assert angle_components(None) == (0.0,)
    assert angle_components("12deg") == (0.0,)
    assert angle_components([]) == (0.0,)
    assert angle_components([1.0, None, "x"]) == (1.0, 0.0, 0.0)


def test_angle_yaw_scalar_is_byte_identical_semantics():
    # Scalar path must behave exactly like the old float(angle) coercion.
    assert angle_yaw(0.75) == 0.75
    assert angle_yaw(0) == 0.0


def test_angle_yaw_euler_takes_y_component():
    # [x, y, z] Euler -> rotation about the world up axis, the same axis the
    # obs builder's scalar-yaw fallback spins about.
    assert angle_yaw([10.0, 90.0, 5.0]) == 90.0
    # 2-vectors (no defined yaw axis) fall back to the first component.
    assert angle_yaw([7.0, 3.0]) == 7.0


def test_angle_delta_componentwise_and_shape_change_is_a_delta():
    assert angle_delta(1.0, 1.0) == 0.0
    assert angle_delta(1.0, 3.5) == 2.5
    assert angle_delta([1, 2, 3], [1, 2, 6]) == 3.0
    # A shape change counts the extra component as its own magnitude.
    assert angle_delta(1.0, [1.0, 4.0]) == 4.0


def test_obs_builder_accepts_vector_angle():
    # env.build_obs_vector must not crash on a 3-vector angle (the pre-fix
    # float() coercion raised TypeError).
    from harness.rl.env import build_obs_vector
    obs_state = {
        "probe": {"pos": [1.0, 2.0], "vel": [0.0, 0.0],
                  "angle": [10.0, 45.0, 0.0], "controlled": True, "static": False},
        "floor": {"pos": [0.0, 0.0], "vel": [0.0, 0.0],
                  "angle": 0.0, "controlled": False, "static": True},
    }
    vec = build_obs_vector(obs_state, {}, ["probe", "floor"], [],
                           (800.0, 600.0), 0, 300)
    assert vec is not None and len(vec) > 0


def test_solver_fingerprint_accepts_vector_angle():
    # statetree.fingerprint crashed on float([x,y,z]) — the tree solver's own
    # angle trap (found by the A/B analyst: subm3D only certified after the game
    # fell back to scalar yaw). Every component must join the fingerprint.
    from harness.core.statetree import fingerprint, fp_delta
    snap_v = {"probe": {"pos": [1, 2], "vel": [0, 0], "angle": [10.0, 45.0, 5.0]}}
    snap_s = {"floor": {"pos": [0, 0], "vel": [0, 0], "angle": 0.25}}
    fv, fs = fingerprint(snap_v), fingerprint(snap_s)
    assert fv[0][-3:] == (10.0, 45.0, 5.0)
    assert fs[0][-1] == 0.25
    # A rotation-only change IS a state change (delta > 0).
    snap_v2 = {"probe": {"pos": [1, 2], "vel": [0, 0], "angle": [10.0, 46.0, 5.0]}}
    assert fp_delta(fv, fingerprint(snap_v2)) == 1.0


# --- Godot: the fixed host serves the natural 3D reading ----------------------

@requires_godot
def test_vector_angle_game_serves_parseable_frames():
    """The exact pre-fix crash: an [x,y,z]-angle game's init frame was truncated
    ('unparseable frame'). With _angle_json it parses and steps."""
    ex = GdExecutor(port_base=_free_port())
    try:
        rec = ex.run_batch(_src("angle3d_probe.gd"),
                           [{"seed": 0, "actions": ["push_x"] * 12}], 12)[0]
    finally:
        ex.close()
    assert rec.get("error") in (None, ""), rec.get("error")
    snap = rec.get("final_snapshot") or {}
    ang = (snap.get("probe") or {}).get("angle")
    assert isinstance(ang, list) and len(ang) == 3, ang


@requires_godot
def test_vector_angle_twin_determinism():
    """Two identical rollouts of the [x,y,z]-angle game are byte-equal on the
    angle stream (the spinning probe makes the vector actually vary)."""
    runs = []
    for _ in range(2):
        ex = GdExecutor(port_base=_free_port())
        try:
            rec = ex.run_batch(_src("angle3d_probe.gd"),
                               [{"seed": 3, "actions": ["push_x", "push_up"] * 8}],
                               16)[0]
        finally:
            ex.close()
        snap = rec.get("final_snapshot") or {}
        runs.append((snap.get("probe") or {}).get("angle"))
    assert runs[0] == runs[1], (runs[0], runs[1])
    assert any(abs(c) > 1e-9 for c in runs[0]), "probe never rotated; test is vacuous"
