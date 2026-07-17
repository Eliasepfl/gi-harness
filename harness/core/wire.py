"""Wire-value helpers shared by every lane that reads a game's ``state()`` fields.

THE ANGLE CONTRACT (2026-07-17). A body's ``angle`` is its rotation reported in the
game's own dimension: a SCALAR in 2D (the one rotation DOF), and in 3D either a
scalar yaw (legacy, what every pre-fix game emitted) or an ``[x, y, z]`` Euler
vector (the natural reading — ``rotation_degrees``/``rotation`` is a Vector3).

Before this module existed, every consumer did ``float(q.get("angle", 0.0))`` and a
3D game reporting the natural vector CRASHED the serve host mid-frame ("unparseable
frame", VERIFY_ERROR) — the 2026-07-17 ambition probe lost all three of its 3D games
to exactly this. The contract prompt never said "scalar"; the harness was punishing
a compliant reading. One boundary, both shapes, everywhere.

Scalar semantics are BYTE-IDENTICAL to the old behaviour; only the vector form is
new. ``None``/malformed values degrade to 0.0 exactly as ``float(q.get(...) or 0.0)``
did — never an exception on wire data.
"""
from __future__ import annotations

from collections.abc import Sequence


def angle_components(a) -> tuple[float, ...]:
    """``angle`` as a tuple of floats: scalar -> (a,), vector -> tuple(a).

    Malformed entries degrade to 0.0 (wire data must never raise)."""
    if isinstance(a, Sequence) and not isinstance(a, (str, bytes)):
        out = []
        for x in a:
            try:
                out.append(float(x))
            except (TypeError, ValueError):
                out.append(0.0)
        return tuple(out) if out else (0.0,)
    try:
        return (float(a),)
    except (TypeError, ValueError):
        return (0.0,)


def angle_yaw(a) -> float:
    """The single rotation the planar/yaw consumers want, from either shape.

    Scalar -> itself (byte-identical to the old ``float(angle)`` path).
    ``[x, y, z]`` Euler -> the Y component (rotation about the world up axis —
    the same axis the obs builder's scalar-yaw fallback spins about).
    Other vector lengths -> first component (best planar guess, never a crash)."""
    comps = angle_components(a)
    if len(comps) >= 3:
        return comps[1]
    return comps[0]


def angle_delta(a, b) -> float:
    """Max componentwise |a-b| between two angle values of the same game (twin
    rollouts always share a shape; mismatched lengths compare the overlap and
    count any extra component as its own magnitude — a shape change IS a delta)."""
    ca, cb = angle_components(a), angle_components(b)
    n = min(len(ca), len(cb))
    worst = 0.0
    for i in range(n):
        worst = max(worst, abs(ca[i] - cb[i]))
    for x in ca[n:] + cb[n:]:
        worst = max(worst, abs(x))
    return worst
