"""Physics verifier — funnel L0 static -> L1 settling -> L2 goal.

100% assertional: it only queries the engine state (no VLM, no pixels).
Stops at the first failure; classifies the failure (ENV_ERROR / GOAL_ERROR) and
produces an English `hint` naming the offending entities, for the repair loop.
"""

from __future__ import annotations

from .l0_static import run_l0
from .l1_settling import run_l1
from .l2_goal import run_l2
from .report import make_report

__all__ = ["verify_scene", "run_l0", "run_l1", "run_l2", "make_report"]


def _default_factory():
    """Build a real SDK (module A). Imported lazily (depends on pymunk)."""
    from harness.sdk import SceneSDK
    return SceneSDK()


# --- Natural-language hint generation ------------------------------------ #
def _hint_l0(layer: dict) -> str:
    c = layer["checks"]
    if not c.get("sandbox_scan", {}).get("pass", True):
        v = c["sandbox_scan"].get("violations") or ["non-compliant code"]
        return f"code rejected by the sandbox: {v[0]}"
    if not c.get("builds", {}).get("pass", True):
        return f"build_scene failed: {c['builds'].get('error', 'unknown error')}"
    if not c.get("has_agent", {}).get("pass", True):
        missing = [k for k in ("agent", "ground") if not c["has_agent"].get(k)]
        return f"required entity/entities missing: {', '.join(missing)}"
    if not c.get("counts", {}).get("pass", True):
        return "near-empty scene (one entity or fewer)"
    if not c.get("no_penetration", {}).get("pass", True):
        a, b, d = c["no_penetration"]["offenders"][0]
        return f"initial interpenetration between {a} and {b} ({d}px)"
    if not c.get("in_bounds", {}).get("pass", True):
        return f"out of world bounds: {', '.join(c['in_bounds']['offenders'])}"
    return "static failure (L0)"


def _hint_l1(layer: dict) -> str:
    c = layer["checks"]
    parts = []
    if not c.get("no_nan", {}).get("pass", True):
        parts.append("numerical explosion (NaN) during settling")
    if not c.get("no_displacement", {}).get("pass", True):
        name, delta = c["no_displacement"]["moved"][0]
        parts.append(f"{name} moves {delta}px at settling (unstable or floating object)")
    if not c.get("agent_supported", {}).get("pass", True):
        parts.append("the agent floats with no support contact at the end of settling")
    if not c.get("comes_to_rest", {}).get("pass", True):
        parts.append(f"the scene does not settle (residual KE "
                     f"{c['comes_to_rest'].get('residual_KE')})")
    if not c.get("no_penetration_during", {}).get("pass", True):
        offs = c["no_penetration_during"].get("offenders")
        if offs:
            a, b, d = offs[0]
            parts.append(f"ongoing interpenetration between {a} and {b} ({d}px)")
    return " ; ".join(parts) if parts else "stability failure (L1)"


def _hint_l2(layer: dict) -> str:
    c = layer["checks"]
    if not c.get("callable_bool", {}).get("pass", True):
        return (f"get_success malformed: "
                f"{c['callable_bool'].get('error', 'does not return a bool')}")
    if not c.get("not_trivially_true", {}).get("pass", True):
        return "get_success is already true after settling (degenerate goal / trivial scene)"
    if not c.get("pure", {}).get("pass", True):
        detail = ("non-deterministic result"
                  if not c["pure"].get("deterministic")
                  else "get_success mutates the scene state")
        return f"get_success is not pure: {detail}"
    return "goal failure (L2)"


def verify_scene(scene_path: str, sandboxed: bool = True, *, sdk_factory=None) -> dict:
    """Orchestrate the L0 -> L1 -> L2 funnel on scene `scene_path`.

    sandboxed=True  : run the verification in an isolated subprocess (prod default).
    sandboxed=False : in-process execution (tests / call from the sandbox worker).
    sdk_factory     : SDK factory (default = harness.sdk.SceneSDK); injectable for tests.
    """
    if sandboxed:
        from harness.sandbox import run_sandboxed
        return run_sandboxed(scene_path, "verify")

    report = make_report()
    try:
        with open(scene_path, "r", encoding="utf-8") as fh:
            source = fh.read()
    except OSError as exc:
        report["failure_class"] = "ENV_ERROR"
        report["hint"] = f"scene unreadable: {exc}"
        return report

    factory = sdk_factory or _default_factory

    # L0
    l0, ctx = run_l0(factory, source)
    report["layers"]["L0_static"] = l0
    if not l0["passed"]:
        report["failure_class"] = "ENV_ERROR"
        report["hint"] = _hint_l0(l0)
        return report

    # L1
    l1 = run_l1(ctx["sdk"])
    report["layers"]["L1_settling"] = l1
    if not l1["passed"]:
        report["failure_class"] = "ENV_ERROR"
        report["hint"] = _hint_l1(l1)
        return report

    # L2
    l2 = run_l2(ctx["sdk"], ctx["get_success"])
    report["layers"]["L2_goal"] = l2
    if not l2["passed"]:
        report["failure_class"] = "GOAL_ERROR"
        report["hint"] = _hint_l2(l2)
        return report

    report["passed"] = True
    report["failure_class"] = None
    report["hint"] = "valid scene: static correct, stable at settling, goal well-formed."
    return report
