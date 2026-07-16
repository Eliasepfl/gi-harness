"""Deterministic behaviour descriptors for ONE certified game (ATLAS D1, read-only).

``describe_game(game_path, verify_report=None, extras=None) -> dict`` reads a game's
EXISTING certification artifacts and distils them into a flat dict of deterministic
descriptors — the axes the atlas map is drawn on. It NEVER runs the engine and NEVER
mutates anything: it is pure aggregation over

  * a verify ``report`` (the G0..G3 funnel output, or a whole ``gen_*.json`` around it),
  * ``extras["facts"]`` — the serve host's t=0 geometry facts (``run_check`` output:
    ``{geometry:[...], world_size:...}``), the only artifact that carries body/geometry
    counts and the raw span/playfield the space-utilisation ratio needs.

CONTRACT — a missing artifact yields ``None`` for that descriptor, NEVER a crash. Every
extractor is individually guarded, so a partial report (e.g. an ``UNSOLVED`` game with
no witness but real solver stats) still produces every descriptor it CAN.

DETERMINISM — no time, no randomness, no I/O beyond an optional read of the game's own
(fixed) source for a dimension fallback: the same inputs always yield the same dict.
"""

from __future__ import annotations

import math
import os

# The full descriptor key set, in a stable order. Shared by build/render/tests so the
# atlas schema has ONE source of truth. Every value is a JSON scalar or None.
DESCRIPTOR_KEYS = (
    "dimension",             # "2D" | "3D" | None
    "witness_ticks",         # decision ticks of the winning witness
    "witness_entropy",       # Shannon entropy (bits) over the witness action sequence
    "distinct_actions",      # # of distinct actions actually used in the witness
    "solver_episodes",       # G3 tree-solver episodes run
    "solver_expansions",     # G3 tree-solver node expansions (search effort)
    "solver_ticks",          # G3 tree-solver total ticks explored
    "space_util_linear_ratio",   # per-axis playfield/span ratio (HIGHER = emptier world)
    "space_util_measure_ratio",  # area (2D) / volume (3D) playfield/span ratio
    "dead_space",            # bool: linear ratio over the harness threshold
    "pressure_class",        # "has_pressure" | "no_pressure" | "failure_unreachable" | None
    "has_failure_witness",   # bool: a reachable failure was witnessed
    "n_checkpoints",         # declared, well-formed checkpoints
    "n_bodies",              # total t=0 bodies
    "n_controlled",          # controlled bodies
    "n_static",              # static (non-sensor) bodies
    "n_sensor",              # sensor bodies
    "n_dynamic",             # remaining (dynamic, non-static, non-sensor, non-controlled)
)


def slug_of(game_path) -> str:
    """The game's slug: its containing directory name (``scenes/games/<slug>/<slug>.gd``),
    falling back to the filename stem for a bare fixture path."""
    p = str(game_path)
    parent = os.path.basename(os.path.dirname(p))
    stem = os.path.splitext(os.path.basename(p))[0]
    # A game lives in a slug-named dir; a bare ``.../fixtures/mini_collect.gd`` has a
    # generic parent -> use the stem. Prefer the dir name only when it matches the stem
    # (the ``<slug>/<slug>.gd`` convention) or the stem looks like an attempt file.
    if parent and (parent == stem or stem in {"a1", "a2", "a3", "a4", "a5"}):
        return parent
    return stem or parent


# ======================================================================== #
# Report normalisation
# ======================================================================== #
def _norm_report(verify_report):
    """Return ``(report_dict | None, verdict | None)`` from any accepted shape:

      * a whole ``gen_*.json`` dict (``{attempts:[{report}], verdict}``) -> last attempt's
        report + the run verdict,
      * a bare report dict (``{layers, witness, ...}``) -> itself (verdict from the report),
      * ``None`` / anything unusable -> ``(None, None)``.
    """
    if not isinstance(verify_report, dict):
        return None, None
    if "attempts" in verify_report and isinstance(verify_report["attempts"], list):
        verdict = verify_report.get("verdict")
        atts = verify_report["attempts"]
        rep = atts[-1].get("report") if atts and isinstance(atts[-1], dict) else None
        return (rep if isinstance(rep, dict) else None), verdict
    if "layers" in verify_report or "witness" in verify_report:
        verdict = verify_report.get("verdict")
        if verdict is None and verify_report.get("passed") is True:
            verdict = "COMPLETED"
        return verify_report, verdict
    return None, None


def _g3_checks(report):
    return (((report or {}).get("layers") or {}).get("G3_solve") or {}).get("checks") or {}


def _layer_checks(report, layer):
    return (((report or {}).get("layers") or {}).get(layer) or {}).get("checks") or {}


# ======================================================================== #
# Witness descriptors
# ======================================================================== #
def _witness(report):
    w = (report or {}).get("witness")
    return w if isinstance(w, dict) else None


def shannon_entropy(actions) -> float | None:
    """Shannon entropy (in BITS) of an action sequence: ``-sum p_i log2 p_i`` over the
    empirical action distribution. ``['a','a','b','b'] -> 1.0``; a constant sequence
    -> ``0.0``; ``None`` for an empty/absent sequence."""
    if not actions:
        return None
    counts: dict = {}
    n = 0
    for a in actions:
        counts[a] = counts.get(a, 0) + 1
        n += 1
    if n == 0:
        return None
    h = 0.0
    for c in counts.values():
        p = c / n
        h -= p * math.log2(p)
    # Clamp a -0.0 artefact to 0.0 for a stable, round-trippable value.
    return round(h, 6) if h else 0.0


def _witness_ticks(report):
    w = _witness(report)
    if w and isinstance(w.get("ticks"), (int, float)):
        return int(w["ticks"])
    return None


def _witness_entropy(report):
    w = _witness(report)
    acts = w.get("actions") if w else None
    if isinstance(acts, list) and acts:
        return shannon_entropy(acts)
    return None


def _distinct_actions(report):
    w = _witness(report)
    acts = w.get("actions") if w else None
    if isinstance(acts, list) and acts:
        return len(set(acts))
    return None


# ======================================================================== #
# Solver effort (G3 tree solve)
# ======================================================================== #
def _solver(report, key):
    epi = _g3_checks(report).get("episodes")
    if isinstance(epi, dict) and isinstance(epi.get(key), (int, float)):
        return int(epi[key])
    return None


# ======================================================================== #
# Pressure class (failure-witness gate)
# ======================================================================== #
def _pressure_class(report):
    fw = _g3_checks(report).get("failure_witness")
    if isinstance(fw, dict):
        out = fw.get("outcome") or (fw.get("finding") or {}).get("outcome")
        if out:
            return str(out)
    return None


def _has_failure_witness(report):
    fw = _g3_checks(report).get("failure_witness")
    if isinstance(fw, dict) and isinstance(fw.get("has_failure_witness"), bool):
        return bool(fw["has_failure_witness"])
    return None


# ======================================================================== #
# Checkpoint count
# ======================================================================== #
def _n_checkpoints(report):
    cw = _layer_checks(report, "G2_goal").get("checkpoints_wellformed")
    if isinstance(cw, dict) and isinstance(cw.get("n"), (int, float)):
        return int(cw["n"])
    # Fallback: the witness records one entry per latched checkpoint.
    w = _witness(report)
    cps = w.get("checkpoints") if w else None
    if isinstance(cps, dict):
        return len(cps)
    return None


# ======================================================================== #
# Geometry: body counts + space utilisation (from t=0 facts, else the report)
# ======================================================================== #
def _facts_geometry(facts):
    if isinstance(facts, dict):
        g = facts.get("geometry")
        if isinstance(g, list):
            return g
    return None


def _world_size_list(facts, report):
    """A plain ``[w, h(, d)]`` from facts (``{declared:...}`` or a bare list) or, failing
    that, the G0 static ``world_size.declared`` in the report."""
    if isinstance(facts, dict):
        ws = facts.get("world_size")
        if isinstance(ws, dict) and isinstance(ws.get("declared"), (list, tuple)):
            return list(ws["declared"])
        if isinstance(ws, (list, tuple)):
            return list(ws)
    wsc = _layer_checks(report, "G0_static").get("world_size")
    if isinstance(wsc, dict) and isinstance(wsc.get("declared"), (list, tuple)):
        return list(wsc["declared"])
    return None


def _body_counts(facts):
    """Classify the t=0 bodies into controlled / sensor / static / dynamic by a fixed
    precedence (controlled > sensor > static > dynamic), returning a counts dict or
    ``None`` when there is no geometry to count."""
    geom = _facts_geometry(facts)
    if geom is None:
        return None
    n_ctrl = n_sensor = n_static = n_dyn = 0
    for b in geom:
        if not isinstance(b, dict):
            continue
        if b.get("controlled"):
            n_ctrl += 1
        elif b.get("sensor"):
            n_sensor += 1
        elif b.get("static"):
            n_static += 1
        else:
            n_dyn += 1
    total = n_ctrl + n_sensor + n_static + n_dyn
    return {"n_bodies": total, "n_controlled": n_ctrl, "n_sensor": n_sensor,
            "n_static": n_static, "n_dynamic": n_dyn}


def _space_util(report, facts):
    """The space-utilisation ratio, preferring a FRESH compute from t=0 facts (reusing
    the on-main ``reachability.space_utilization``) and falling back to the report's
    ``dead_space`` sub-check. Returns ``(linear_ratio, measure_ratio, dead_space, dims)``
    with any element ``None`` when unavailable."""
    geom = _facts_geometry(facts)
    if geom is not None:
        ws = _world_size_list(facts, report) or [800, 600]
        try:
            from harness.verify.reachability import space_utilization
            su = space_utilization(geom, ws)
        except Exception:
            su = None
        if isinstance(su, dict):
            return (su.get("linear_ratio"), su.get("measure_ratio"),
                    bool(su.get("dead_space")), su.get("dims"))
    ds = _g3_checks(report).get("dead_space")
    if isinstance(ds, dict):
        return (ds.get("linear_ratio"), ds.get("measure_ratio"),
                (bool(ds["dead_space"]) if "dead_space" in ds else None), ds.get("dims"))
    return (None, None, None, None)


# ======================================================================== #
# Dimension
# ======================================================================== #
def _dim_from_geometry(facts):
    geom = _facts_geometry(facts)
    if not geom:
        return None
    # Prefer the controlled body's position width; else the first body with a position.
    for prefer_ctrl in (True, False):
        for b in geom:
            if not isinstance(b, dict):
                continue
            if prefer_ctrl and not b.get("controlled"):
                continue
            pos = b.get("pos")
            if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                return "3D" if len(pos) >= 3 else "2D"
    return None


def _dim_from_source(game_path):
    """Last-resort dimension read from the game's OWN (fixed) source: a 3D game uses
    ``Vector3`` / ``Node3D``. Deterministic (file content is immutable per game)."""
    try:
        with open(game_path, "r", encoding="utf-8") as fh:
            src = fh.read(20000)
    except OSError:
        return None
    if "Vector3" in src or "Node3D" in src or "Camera3D" in src:
        return "3D"
    if "Vector2" in src or "Node2D" in src or "Camera2D" in src:
        return "2D"
    return None


def _dimension(report, facts, game_path, su_dims):
    d = _dim_from_geometry(facts)
    if d:
        return d
    if su_dims in (2, 3):
        return "2D" if su_dims == 2 else "3D"
    return _dim_from_source(game_path)


# ======================================================================== #
# The public entry point
# ======================================================================== #
def describe_game(game_path, verify_report=None, extras=None) -> dict:
    """Compute the deterministic descriptor row for ONE game from existing artifacts.

    ``game_path``     : the game's ``.gd`` path (used for the dimension fallback only).
    ``verify_report`` : a verify report dict, a whole ``gen_*.json`` around one, or None.
    ``extras``        : optional ``{"facts": <run_check t=0 facts>}`` (geometry + world
                        size) — the only source of body counts / a fresh space ratio.

    Returns a flat dict over :data:`DESCRIPTOR_KEYS`; a descriptor with no backing
    artifact is ``None``. Pure and deterministic (same inputs -> same dict)."""
    extras = extras or {}
    report, _verdict = _norm_report(verify_report)
    facts = extras.get("facts")

    lin, meas, dead, su_dims = _space_util(report, facts)
    counts = _body_counts(facts) or {}

    row = {
        "dimension": _dimension(report, facts, game_path, su_dims),
        "witness_ticks": _witness_ticks(report),
        "witness_entropy": _witness_entropy(report),
        "distinct_actions": _distinct_actions(report),
        "solver_episodes": _solver(report, "run"),
        "solver_expansions": _solver(report, "nodes"),
        "solver_ticks": _solver(report, "ticks"),
        "space_util_linear_ratio": lin,
        "space_util_measure_ratio": meas,
        "dead_space": dead,
        "pressure_class": _pressure_class(report),
        "has_failure_witness": _has_failure_witness(report),
        "n_checkpoints": _n_checkpoints(report),
        "n_bodies": counts.get("n_bodies"),
        "n_controlled": counts.get("n_controlled"),
        "n_static": counts.get("n_static"),
        "n_sensor": counts.get("n_sensor"),
        "n_dynamic": counts.get("n_dynamic"),
    }
    # Guarantee the full, ordered key set (a robustness belt over the explicit dict above).
    return {k: row.get(k) for k in DESCRIPTOR_KEYS}
