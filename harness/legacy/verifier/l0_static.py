"""L0 — static layer: sandbox + scene graph (before any simulation).

Checks: sandbox scan, build_scene runs, agent + ground present, counts > 1,
no initial penetration, everything in bounds.
"""

from __future__ import annotations

import traceback
from itertools import combinations

from harness.core.sandbox import SandboxViolation, load_scene_namespace, scan_source

from .report import PEN_INIT_TOL, check


def run_l0(sdk_factory, source):
    """Run L0 on `source`, building an SDK via `sdk_factory()`.

    Returns (layer, ctx) where ctx = {"sdk", "get_success", "actions", "description"}.
    A None `sdk` in ctx means no downstream simulation is possible.
    """
    layer = {"passed": False, "checks": {}}
    ctx = {"sdk": None, "get_success": None, "actions": None, "description": None}
    checks = layer["checks"]

    # --- Sandbox scan (re-checkable even if already done upstream) ---
    violations = scan_source(source)
    checks["sandbox_scan"] = check(not violations, violations=violations)
    if violations:
        return layer, ctx

    # --- Load into restricted namespace ---
    try:
        ns = load_scene_namespace(source, scan=False)
    except SandboxViolation as exc:
        checks["sandbox_scan"] = check(False, violations=exc.violations)
        return layer, ctx
    except Exception:
        checks["builds"] = check(False, error=traceback.format_exc(limit=3))
        return layer, ctx

    build_scene = ns.get("build_scene")
    ctx["get_success"] = ns.get("get_success")
    ctx["actions"] = ns.get("AVAILABLE_ACTIONS")
    ctx["description"] = ns.get("SCENE_DESCRIPTION")

    if not callable(build_scene):
        checks["builds"] = check(False, error="build_scene missing or not callable")
        return layer, ctx

    # --- Scene construction ---
    sdk = sdk_factory()
    try:
        build_scene(sdk)
    except Exception:
        checks["builds"] = check(False, error=traceback.format_exc(limit=3))
        return layer, ctx
    checks["builds"] = check(True)
    ctx["sdk"] = sdk

    entities = list(sdk.list_entities())

    # --- Agent + ground present ---
    has_agent = "agent" in entities
    has_ground = "ground" in entities
    checks["has_agent"] = check(has_agent and has_ground,
                                agent=has_agent, ground=has_ground)

    # --- Counts ---
    checks["counts"] = check(len(entities) > 1, n=len(entities))

    # --- No initial penetration ---
    # Static-static pairs (a wall resting on the ground...) are intended geometry,
    # never resolved by the solver: we only test pairs involving at least one
    # dynamic body. Sensors are excluded by the SDK.
    body_type = {n: sdk.query(n)["body_type"] for n in entities}
    offenders = []
    for a, b in combinations(entities, 2):
        if body_type[a] == "static" and body_type[b] == "static":
            continue
        try:
            depth = sdk.penetration_depth(a, b)
        except Exception:
            depth = 0.0
        if depth and depth > PEN_INIT_TOL:
            offenders.append([a, b, round(float(depth), 3)])
    checks["no_penetration"] = check(not offenders, offenders=offenders)

    # --- In bounds (dynamic only) ---
    # Static geometry (ground/walls = segments with non-zero radius) overflows the
    # world rectangle by construction; only moving bodies are constrained.
    dynamic = [n for n in entities if sdk.query(n)["body_type"] == "dynamic"]
    out_of_bounds = [n for n in dynamic if not sdk.in_bounds(n)]
    checks["in_bounds"] = check(not out_of_bounds, offenders=out_of_bounds)

    layer["passed"] = all(c["pass"] for c in checks.values())
    return layer, ctx
