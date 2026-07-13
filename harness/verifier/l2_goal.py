"""L2 — goal layer: `get_success` well-formed.

Checks: callable and returns a bool; not true after settling (goal already
reached = degenerate scene); pure (2 calls -> same result, snapshot unchanged).
"""

from __future__ import annotations

import traceback

from .report import check


def run_l2(sdk, get_success):
    """Run L2 on an already-settled scene."""
    layer = {"passed": False, "checks": {}}
    checks = layer["checks"]

    if not callable(get_success):
        checks["callable_bool"] = check(False, error="get_success not callable")
        return layer

    # --- Purity: two calls bracketed by snapshots ---
    snap_before = sdk.snapshot()
    try:
        r1 = get_success(sdk)
        r2 = get_success(sdk)
    except Exception:
        checks["callable_bool"] = check(False, error=traceback.format_exc(limit=3))
        return layer
    snap_after = sdk.snapshot()

    is_bool = isinstance(r1, bool) and isinstance(r2, bool)
    checks["callable_bool"] = check(is_bool, value=bool(r1) if is_bool else None)
    if not is_bool:
        return layer

    # --- Not trivially true (after settling) ---
    checks["not_trivially_true"] = check(not r1, value=bool(r1))

    # --- Purity ---
    pure = (r1 == r2) and (snap_before == snap_after)
    checks["pure"] = check(pure, deterministic=(r1 == r2),
                           state_unchanged=(snap_before == snap_after))

    layer["passed"] = all(c["pass"] for c in checks.values())
    return layer
