"""L1 — settling layer: stability under simulation, without any input.

We simulate SETTLE_STEPS steps and check: no NaN, return to rest (low KE),
no notable displacement per dynamic object, no ongoing penetration,
and the agent effectively supported at the end.
"""

from __future__ import annotations

import math
from itertools import combinations

from .report import (
    ANGLE_TOL, DISP_FLOOR, DISP_FRAC, NAN_EVENT_TYPES, PEN_DURING_TOL,
    PEN_SAMPLE, REST_KE, SETTLE_STEPS, check,
)


def _char_size(bbox) -> float:
    """Characteristic size of an entity = largest dimension of its AABB."""
    left, bottom, right, top = bbox
    return max(abs(right - left), abs(top - bottom), 1.0)


def _finite_state(q) -> bool:
    vals = list(q["pos"]) + list(q["vel"]) + [q["angle"], q["angular_vel"]]
    return all(math.isfinite(v) for v in vals)


def _agent_supported(sdk) -> dict:
    """Does the agent have a contact with an entity located below it?"""
    if "agent" not in sdk.list_entities():
        return check(False, support=None)
    agent = sdk.query("agent")
    agent_cy = agent["pos"][1]
    for name in sdk.list_entities():
        if name == "agent":
            continue
        if sdk.contacts("agent", name) and sdk.query(name)["pos"][1] < agent_cy:
            return check(True, support=name)
    return check(False, support=None)


def run_l1(sdk):
    """Run the settling simulation and fill in the L1 checks."""
    layer = {"passed": False, "checks": {}}
    checks = layer["checks"]

    entities = list(sdk.list_entities())
    dynamic = [n for n in entities if sdk.query(n)["body_type"] == "dynamic"]
    start = {n: sdk.query(n) for n in dynamic}

    pen_max = 0.0
    pen_offenders = []
    stepped_nan = False

    for i in range(SETTLE_STEPS):
        try:
            sdk.step(1)
        except Exception:
            stepped_nan = True
            break
        if (i + 1) % PEN_SAMPLE == 0:
            for a, b in combinations(entities, 2):
                try:
                    depth = sdk.penetration_depth(a, b) or 0.0
                except Exception:
                    depth = 0.0
                if depth > pen_max:
                    pen_max = depth
                if depth > PEN_DURING_TOL and not any(o[0] == a and o[1] == b
                                                      for o in pen_offenders):
                    pen_offenders.append([a, b, round(float(depth), 3)])

    # --- no_nan: engine events + finite states ---
    event_nan = any(e.get("type") in NAN_EVENT_TYPES for e in sdk.events())
    finite = all(_finite_state(sdk.query(n)) for n in entities)
    checks["no_nan"] = check((not stepped_nan) and (not event_nan) and finite)

    # --- comes_to_rest ---
    ke = float(sdk.total_kinetic_energy())
    checks["comes_to_rest"] = check(ke < REST_KE, residual_KE=round(ke, 4))

    # --- no_displacement (per dynamic object) ---
    moved = []
    for n in dynamic:
        s = start[n]
        e = sdk.query(n)
        tol = max(DISP_FRAC * _char_size(s["bbox"]), DISP_FLOOR)
        dp = math.hypot(e["pos"][0] - s["pos"][0], e["pos"][1] - s["pos"][1])
        da = abs(e["angle"] - s["angle"])
        if dp > tol or da > ANGLE_TOL:
            moved.append([n, round(dp, 2)])
    checks["no_displacement"] = check(not moved, moved=moved)

    # --- no_penetration_during ---
    checks["no_penetration_during"] = check(not pen_offenders,
                                            offenders=pen_offenders,
                                            max=round(pen_max, 3))

    # --- agent_supported ---
    checks["agent_supported"] = _agent_supported(sdk)

    layer["passed"] = all(c["pass"] for c in checks.values())
    return layer
