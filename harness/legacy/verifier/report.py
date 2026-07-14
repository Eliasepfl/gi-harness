"""Verifier report and calibration constants.

Report schema (CONTRACTS §4): mutable dict filled by the L0/L1/L2 layers.
Every constant marked [eng.] is an engineering choice to calibrate
(see SPEC_VERIFIER.md, summary table).
"""

from __future__ import annotations

# --- L0 constants -------------------------------------------------------- #
PEN_INIT_TOL = 1.5          # px: max tolerated initial penetration (C1) [eng.]
                            # > ground segment radius (1 px): an object resting
                            # exactly on the surface must not be rejected.

# --- L1 constants -------------------------------------------------------- #
SETTLE_STEPS = 300          # simulation steps with no input (~5 s) [eng.]
PEN_SAMPLE = 30             # sampling of ongoing penetration (every N steps)
PEN_DURING_TOL = 5.0        # px: max tolerated penetration during the rollout [eng.]
REST_KE = 5.0               # total kinetic energy below which we consider rest [eng.]
DISP_FRAC = 0.02            # tolerated Δpos = 2% of the characteristic size [eng.]
DISP_FLOOR = 2.0            # px: absolute floor of the tolerated Δpos (absorbs solver jitter) [eng.]
ANGLE_TOL = 0.15            # rad: tolerated Δangle at settling [eng.]

# Event types signalling a numerical explosion (cf. sdk.step).
NAN_EVENT_TYPES = {"nan_detected", "nan", "explosion"}


def make_report() -> dict:
    """Blank report structure, filled in-place by the layers."""
    return {
        "passed": False,
        "failure_class": None,   # None | "ENV_ERROR" | "GOAL_ERROR"
        "layers": {
            "L0_static": _empty_layer(),
            "L1_settling": _empty_layer(),
            "L2_goal": _empty_layer(),
        },
        "hint": "",
    }


def _empty_layer() -> dict:
    return {"passed": False, "checks": {}}


def check(passed: bool, **extra) -> dict:
    """Build a check dict: {"pass": bool, ...extra}."""
    out = {"pass": bool(passed)}
    out.update(extra)
    return out
