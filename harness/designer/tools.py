"""The FROZEN tool layer v0 — the designer's read/oracle spine (§3 P1).

Three oracle-spine tools, human-authored and frozen (the agent may only PROPOSE
changes to this decomposition, never edit it):

* ``design(prompt_or_source, directive?, engine?, backend?)`` — wraps
  ``gamegen.generate_game`` (from scratch) / ``gamegen.revise_game`` (when a
  ``directive`` is present, treating ``prompt_or_source`` as the source). LLM cost.
* ``certify(game_path, depth=verify|harden|grade|full)`` — wraps the verifier
  funnel. ``verify`` = ``gameverify.verify_game`` (cheap, the default);
  ``harden`` adds ``g4.attack_game``; ``grade`` adds ``rl.certify.g3_prime``;
  ``full`` = all three. **Thresholds are NEVER a parameter** — the trust boundary
  is not agent-tunable.
* ``retrieve_parts(prompt, engine)`` — wraps ``retrieval.retrieve_menu``, a pure
  function of ``(prompt, bank_version)``. Free, deterministic.

Plus one FROZEN read-only STATIC-analysis tool (a fourth entry in the
``DESIGNER_AGENT_PLAN.md`` §3 tool table — noted here, the plan itself is untouched):

* ``inspect_world(spec_or_fragment)`` — engine-free placement feedback (v1): parse a
  (partial) Godot ``.spec.json`` — a full spec dict OR a bodies-only fragment the
  designer holds mid-composition — and return per-entity AABBs + bank roles + a
  placement-warning taxonomy (solid-static overlaps, out-of-bounds bodies, isolated
  sensors, floating statics, duplicate names). Geometry is computed analytically from
  the spec fields alone (``godotworld/SPEC.md`` shapes, mirroring ``runner.gd``
  ``_bbox``); NO Godot process spawns. Free, deterministic. An engine-backed v2
  (live ``GodotServeEnv`` state) is a FOLLOWUP, not built here.

Each tool has a JSON-schema'd input, a compact typed-dict output (the shapes from
the plan's §3 table) plus a verbose handle to the full underlying report, and is
exported in ``REGISTRY`` for OpenAI native function-calling. ``dispatch(name,
arguments)`` routes a function call by name. No write verb lives here: the spine
is read/oracle-only (``designer_write`` is the sole write path, §4).
"""
from __future__ import annotations

import json as _json
import math as _math
import re as _re
from typing import Any, Callable

# --------------------------------------------------------------------------- #
# design
# --------------------------------------------------------------------------- #
def design(prompt_or_source: str, directive: str | None = None,
           engine: str | None = None, backend: str = "auto", *,
           out_dir: str = "scenes/games", max_repairs: int = 4,
           use_bank: bool = True) -> dict:
    """Generate (or revise) a game and return a compact generation report.

    ``directive`` present ⇒ REVISE: ``prompt_or_source`` is treated as the
    certified source and the smallest edit applying ``directive`` is made.
    Otherwise ⇒ generate from scratch with ``prompt_or_source`` as the prompt.
    ``out_dir``/``max_repairs``/``use_bank`` are harness-side knobs, not part of
    the model-facing frozen schema.

    -> {"game_path", "verdict", "backend", "engine", "n_attempts", "integrity",
        "parts_used", "note"?, "report": <full generate/revise report>}
    """
    from harness.gen import gamegen

    if directive:
        report = gamegen.revise_game(prompt_or_source, directive, out_dir=out_dir,
                                     backend=backend, max_repairs=max_repairs,
                                     engine=engine, use_bank=use_bank)
    else:
        report = gamegen.generate_game(prompt_or_source, out_dir=out_dir,
                                       backend=backend, max_repairs=max_repairs,
                                       engine=engine, use_bank=use_bank)
    pipeline = report.get("pipeline") or {}
    out = {
        "game_path": report.get("game_path"),
        "verdict": report.get("verdict"),
        "backend": report.get("backend"),
        "engine": report.get("engine"),
        "n_attempts": len(report.get("attempts") or []),
        "integrity": report.get("integrity"),
        "parts_used": list(pipeline.get("parts_used") or []),
        "report": report,
    }
    if report.get("note"):
        out["note"] = report["note"]
    return out


# --------------------------------------------------------------------------- #
# certify
# --------------------------------------------------------------------------- #
_LAYER_STAGE = (("G0_static", "G0"), ("G1_rollout", "G1"),
                ("G2_goal", "G2"), ("G3_solve", "G3"))


def _is_verify_error(report: Any) -> bool:
    return isinstance(report, dict) and "error" in report and "layers" not in report


def _verdict_of(report: dict) -> str:
    """Map a verify_game report to a verdict (mirrors gamegen's mapping)."""
    if not isinstance(report, dict):
        return "VERIFY_ERROR"
    if _is_verify_error(report):
        return "VERIFY_ERROR"
    if report.get("passed"):
        return "COMPLETED"
    return report.get("failure_class") or "ENV_ERROR"


def _stage_of(report: dict) -> str | None:
    """The gate the report reached: first failing layer, or G3 if all passed."""
    if not isinstance(report, dict) or _is_verify_error(report):
        return None
    layers = report.get("layers") or {}
    last = None
    for key, label in _LAYER_STAGE:
        layer = layers.get(key) or {}
        if layer.get("passed"):
            last = label
        else:
            return label
    return last or "G3"


def certify(game_path: str, depth: str = "verify", *,
            sandboxed: bool = True) -> dict:
    """Run the verifier funnel at ``depth`` and return the compact certificate.

    ``depth``: ``verify`` (G0-G3, the cheap default) | ``harden`` (+G4 attacker) |
    ``grade`` (+RL learnability) | ``full`` (all three). Thresholds are NOT a
    parameter — they live in the frozen verifier. ``sandboxed`` is a harness-side
    knob (subprocess isolation), not part of the model-facing schema.

    -> {"verdict", "stage", "hint", "witness", "g4_grade", "learnable",
        "depth", "report": <full verify report>, "g4_report"?, "grade_report"?}
    """
    from harness.verify.gameverify import verify_game

    depth = depth if depth in ("verify", "harden", "grade", "full") else "verify"
    report = verify_game(game_path, sandboxed=sandboxed)

    out: dict[str, Any] = {
        "verdict": _verdict_of(report),
        "stage": _stage_of(report),
        "hint": report.get("hint") if isinstance(report, dict) else None,
        "witness": report.get("witness") if isinstance(report, dict) else None,
        "g4_grade": None,
        "learnable": None,
        "depth": depth,
        "report": report,
    }

    if depth in ("harden", "full"):
        from harness.verify.g4 import attack_game
        g4_report = attack_game(game_path, sandboxed=sandboxed)
        out["g4_grade"] = g4_report.get("grade")
        out["g4_report"] = g4_report

    if depth in ("grade", "full"):
        from harness.rl.certify import g3_prime
        grade_report = g3_prime(game_path)
        out["learnable"] = grade_report.get("learnable")
        out["grade_report"] = grade_report

    return out


# --------------------------------------------------------------------------- #
# retrieve_parts
# --------------------------------------------------------------------------- #
def retrieve_parts(prompt: str, engine: str = "py") -> dict:
    """Deterministic Tier-1b menu for ``prompt`` (pure fn of prompt + bank).

    -> {"menu_text", "menu_mode", "names", "scores"} where ``scores`` is the list
    of retrieval scores parallel to ``names``. ``menu_text`` is None in
    legend-only mode. Two calls with the same args return an identical dict.
    """
    from harness.gen import retrieval

    menu_text, menu_mode, names = retrieval.retrieve_menu(prompt, engine)
    score_map = dict(retrieval.score(prompt))
    scores = [round(float(score_map.get(n, 0.0)), 6) for n in names]
    return {
        "menu_text": menu_text,
        "menu_mode": menu_mode,
        "names": list(names),
        "scores": scores,
    }


# --------------------------------------------------------------------------- #
# inspect_world — static placement feedback (engine-free)
# --------------------------------------------------------------------------- #
# Heuristic tolerances (px). Static analysis only; the designer verifies. [eng.]
_OVERLAP_EPS = 0.5      # ignore sub-pixel solid-static overlaps (float noise)
_BOUNDS_EPS = 0.5       # ignore sub-pixel out-of-bounds excursions
_SUPPORT_TOL = 4.0      # a static is "supported" if resting within this of a surface
_REACH_TOL = 40.0       # a sensor is "reachable" if a body is within this of it

# Engine invariants mirrored from the frozen runner.gd / project.godot so the analyzer
# reasons about EXACT engine semantics, not approximations (GODOT_DOCS_MINING.md §1).
_DEFAULT_GRAVITY = 900.0            # project.godot 2d/default_gravity
_DEFAULT_GRAVITY_VEC = (0.0, -1.0)  # 2d/default_gravity_vector — y-UP world, down = -Y
_DEFAULT_TOPDOWN_DAMP = 1.5        # runner.gd DEFAULT_TOPDOWN_DAMP (world.linear_damp default)
_TICK_DT = 1.0 / 60.0              # fixed physics dt (--fixed-fps 60); zero damping
_CONTACT_CAP = 8                   # RigidBody2D.max_contacts_reported — silently drops >8
_TUNNEL_THIN_PX = 6.0             # a static wall thinner than this can be tunnelled (CCD off)
_STILL_EPS = 30.0                 # speed below this reads as "at rest" for park detection
_FORECAST_HORIZON = 1200           # cap ballistic projection at 20 s (1200 physics ticks)

# The whitelisted predicate grammar, a static replica of runner.gd's `_pred_error`
# boundary (ALLOWED_IDENTS / ALLOWED_OPS) so the linter flags what the engine rejects.
_ALLOWED_IDENTS = frozenset({
    "pos_x", "pos_y", "vel_x", "vel_y", "speed", "angle", "grounded", "contacts",
    "contained", "dist", "flag", "steps",
    "abs", "min", "max", "clamp", "sqrt", "floor", "ceil", "sign",
    "and", "or", "not", "true", "false",
})
_ALLOWED_OPS = "+-*/%(),<>=!"
# Exact arity Godot's Expression demands (ALL params required — a short call is null,
# so the whole predicate is silently False).
_FN_ARITY = {
    "pos_x": 1, "pos_y": 1, "vel_x": 1, "vel_y": 1, "speed": 1, "angle": 1,
    "grounded": 1, "contacts": 2, "contained": 2, "dist": 2, "flag": 1,
    "abs": 1, "sqrt": 1, "floor": 1, "ceil": 1, "sign": 1,
    "min": 2, "max": 2, "clamp": 3,
}
# Which argument positions of a query fn are BODY names (checked against defined bodies);
# `flag(k)` takes a flag KEY, not a body, so it is deliberately absent here.
_FN_BODY_ARGS = {
    "pos_x": (0,), "pos_y": (0,), "vel_x": (0,), "vel_y": (0,), "speed": (0,),
    "angle": (0,), "grounded": (0,), "contacts": (0, 1), "contained": (0, 1),
    "dist": (0, 1),
}


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _is_vec2(x: Any) -> bool:
    return isinstance(x, (list, tuple)) and len(x) == 2 and all(_is_num(v) for v in x)


def _local_verts(body: dict, shape: str) -> list[tuple[float, float]] | None:
    """Local (unrotated, centered-at-pos) vertices for a non-circle shape, mirroring
    ``runner.gd``'s per-shape ``verts`` (box corners / segment endpoints / poly)."""
    if shape == "box":
        size = body.get("size")
        if not _is_vec2(size):
            return None
        w, h = float(size[0]), float(size[1])
        return [(-w * 0.5, -h * 0.5), (w * 0.5, -h * 0.5),
                (w * 0.5, h * 0.5), (-w * 0.5, h * 0.5)]
    if shape == "segment":
        a, b = body.get("a"), body.get("b")
        if not (_is_vec2(a) and _is_vec2(b)):
            return None
        return [(float(a[0]), float(a[1])), (float(b[0]), float(b[1]))]
    if shape == "poly":
        vs = body.get("vertices")
        if not isinstance(vs, list) or len(vs) < 3 or not all(_is_vec2(v) for v in vs):
            return None
        return [(float(v[0]), float(v[1])) for v in vs]
    return None


def _shape_aabb(body: dict) -> list[float] | None:
    """Analytic axis-aligned bounding box ``[left, bottom, right, top]`` (y UP), the
    SAME box the frozen ``runner.gd`` ``_bbox`` computes for the G0 init check and
    ``contained()`` (SPEC.md §8): circle → center ± r; box/segment/poly → the extents
    of the ROTATED local vertices. Returns None for a shape with missing geometry."""
    pos = body.get("pos")
    if not _is_vec2(pos):
        return None
    px, py = float(pos[0]), float(pos[1])
    shape = body.get("shape")
    if shape == "circle":
        r = body.get("radius")
        if not _is_num(r):
            return None
        r = float(r)
        return [px - r, py - r, px + r, py + r]
    verts = _local_verts(body, shape)
    if not verts:
        return None
    ang = float(body.get("angle", 0.0) or 0.0)
    ca, sa = _math.cos(ang), _math.sin(ang)
    left = bottom = _math.inf
    right = top = -_math.inf
    for vx, vy in verts:
        wx = px + ca * vx - sa * vy
        wy = py + sa * vx + ca * vy
        left, right = min(left, wx), max(right, wx)
        bottom, top = min(bottom, wy), max(top, wy)
    return [left, bottom, right, top]


def _aabb_penetration(a: list[float], b: list[float]) -> float:
    """Min-axis overlap depth of two AABBs (0 if separated); mirrors runner.gd."""
    ox = min(a[2], b[2]) - max(a[0], b[0])
    oy = min(a[3], b[3]) - max(a[1], b[1])
    if ox <= 0.0 or oy <= 0.0:
        return 0.0
    return min(ox, oy)


def _aabb_gap(a: list[float], b: list[float]) -> float:
    """Axis-max separation between two AABBs (0 if they overlap/touch)."""
    dx = max(0.0, a[0] - b[2], b[0] - a[2])
    dy = max(0.0, a[1] - b[3], b[1] - a[3])
    return max(dx, dy)


def _base_name(name: str) -> str:
    """Strip a trailing ``_<digits>`` instance suffix (``wall_2`` -> ``wall``)."""
    i = name.rfind("_")
    if i > 0 and name[i + 1:].isdigit():
        return name[:i]
    return name


def _kind_of(body: dict) -> str:
    if body.get("sensor"):
        return "sensor"
    if body.get("static"):
        return "static"
    return "dynamic"


def _round(x: float) -> float:
    return round(float(x), 6)


def _gravity_of(meta: dict) -> list:
    """The world gravity vector ``[gx, gy]`` (px/s²), y-UP so default down = -Y (§1.1).
    Reads ``meta.gravity`` (magnitude) + ``meta.gravity_vector`` (direction) when a spec
    carries them, else the project.godot defaults ``(0,-1)·900 = (0,-900)`` — the exact
    vector the runner's floating/OOB verdicts must reference or they invert sign.

    NOTE: only the SIDE view has gravity; ``inspect_world`` overrides this to ``[0, 0]``
    in a top-down world (see ``_view_of`` / SPEC.md §2b)."""
    mag = _DEFAULT_GRAVITY
    vx, vy = _DEFAULT_GRAVITY_VEC
    if isinstance(meta, dict):
        g = meta.get("gravity")
        if _is_num(g):
            mag = float(g)
        gv = meta.get("gravity_vector")
        if _is_vec2(gv):
            length = _math.hypot(float(gv[0]), float(gv[1]))
            if length > 0:
                vx, vy = float(gv[0]) / length, float(gv[1]) / length
    return [vx * mag, vy * mag]


def _world_block(spec: Any) -> dict:
    """The optional top-level ``world`` block (SPEC.md §2b), or ``{}`` when absent."""
    if isinstance(spec, dict):
        wb = spec.get("world")
        if isinstance(wb, dict):
            return wb
    return {}


def _view_of(spec: Any) -> str:
    """The world view mode: ``"topdown"`` when the ``world`` block selects it, else the
    default ``"side"``. Top-down zeroes gravity (SPEC.md §2b), which flips several
    placement oracles (floating statics / ballistic forecasts become meaningless; a
    ``linear_damp`` makes a park-at-rest goal satisfiable)."""
    return "topdown" if _world_block(spec).get("view") == "topdown" else "side"


def _topdown_linear_damp(spec: Any) -> float:
    """The top-down friction analog (``world.linear_damp``, default 1.5) — the per-body
    damping the runner applies to every dynamic body in a top-down world. ``0.0`` in the
    side view (where linear_damp is ignored and bodies coast forever)."""
    if _view_of(spec) != "topdown":
        return 0.0
    ld = _world_block(spec).get("linear_damp")
    return float(ld) if _is_num(ld) else _DEFAULT_TOPDOWN_DAMP


def inspect_world(spec_or_fragment: Any, *, use_bank: bool = True,
                  bank_version: str = "v1") -> dict:
    """Static, engine-free placement feedback for a Godot spec (or bodies fragment).

    Accepts a full spec dict, a bodies-only fragment (``{"bodies": [...]}`` or a bare
    list of bodies), or a JSON string of either — so the designer can call it
    mid-composition. Geometry is derived analytically from the spec fields alone (no
    Godot process); ``use_bank``/``bank_version`` are harness-side knobs, not part of
    the model-facing frozen schema.

    -> {"entities": [{"name", "role_if_bank_matched", "position", "aabb", "shape",
            "kind": static|dynamic|sensor}],
        "warnings": [{"kind", "bodies", ...}],  # overlap_solid_statics | out_of_bounds
            | isolated_sensor | floating_static | duplicate_name
        "summary": {"counts", "by_shape", "world_size", "world_bbox", "is_fragment",
            "n_entities", "n_warnings"}}
    """
    if isinstance(spec_or_fragment, str):
        try:
            spec_or_fragment = _json.loads(spec_or_fragment)
        except ValueError as exc:
            raise ValueError(f"inspect_world: input is not valid JSON: {exc}") from exc

    if isinstance(spec_or_fragment, list):
        spec: dict = {"bodies": spec_or_fragment}
    elif isinstance(spec_or_fragment, dict):
        spec = spec_or_fragment
    else:
        raise TypeError("inspect_world: expected a spec dict, bodies list, or JSON string")

    raw_bodies = spec.get("bodies")
    bodies = [b for b in raw_bodies if isinstance(b, dict)] if isinstance(raw_bodies, list) else []

    # A full spec carries the whole contract (SPEC.md §1); anything less is a fragment
    # the designer is still assembling.
    is_fragment = not all(k in spec for k in ("meta", "act", "predicates"))
    meta = spec.get("meta") if isinstance(spec.get("meta"), dict) else {}
    ws = meta.get("world_size")
    world_size = list(ws) if _is_vec2(ws) else (None if is_fragment else [800, 600])
    # View mode (SPEC.md §2b): a top-down world has ZERO gravity, so its gravity-dependent
    # oracles (floating statics, ballistic forecast, park-at-rest) are branched below.
    view = _view_of(spec)
    gravity = [0.0, 0.0] if view == "topdown" else _gravity_of(meta)

    roles = _bank_roles([b.get("name") for b in bodies], use_bank, bank_version)

    # -- entities ----------------------------------------------------------
    entities: list[dict] = []
    recs: list[dict] = []  # parallel geometry records for the warning passes
    for body in bodies:
        name = body.get("name")
        aabb = _shape_aabb(body)
        pos = body.get("pos")
        kind = _kind_of(body)
        entities.append({
            "name": name,
            "role_if_bank_matched": roles.get(name) if isinstance(name, str) else None,
            "position": [_round(pos[0]), _round(pos[1])] if _is_vec2(pos) else None,
            "aabb": [_round(v) for v in aabb] if aabb is not None else None,
            "shape": body.get("shape"),
            "kind": kind,
        })
        recs.append({"name": name, "aabb": aabb, "kind": kind,
                     "shape": body.get("shape"), "body": body})

    warnings = _collect_warnings(recs, world_size, spec, gravity)

    # -- summary -----------------------------------------------------------
    counts = {"static": 0, "dynamic": 0, "sensor": 0, "total": len(entities)}
    by_shape: dict[str, int] = {}
    for e in entities:
        counts[e["kind"]] += 1
        by_shape[str(e["shape"])] = by_shape.get(str(e["shape"]), 0) + 1

    boxes = [r["aabb"] for r in recs if r["aabb"] is not None]
    world_bbox = None
    if boxes:
        world_bbox = [_round(min(b[0] for b in boxes)), _round(min(b[1] for b in boxes)),
                      _round(max(b[2] for b in boxes)), _round(max(b[3] for b in boxes))]

    return {
        "entities": entities,
        "warnings": warnings,
        "summary": {
            "counts": counts,
            "by_shape": by_shape,
            "world_size": world_size,
            "world_bbox": world_bbox,
            "view": view,
            "gravity": [_round(gravity[0]), _round(gravity[1])],
            # A jump/ballistic forecast is meaningless with no gravity (top-down).
            "ballistic": None if view == "topdown" else _ballistic_summary(recs, spec, gravity),
            "is_fragment": is_fragment,
            "n_entities": len(entities),
            "n_warnings": len(warnings),
        },
    }


def _bank_roles(names: list, use_bank: bool, bank_version: str) -> dict[str, str]:
    """Map each body name to its bank part CATEGORY (the semantic role), matching by
    exact name then base name (``wall_2`` -> ``wall``). Empty when the bank is off or
    unavailable — a static analyzer must never fail because the bank is missing."""
    if not use_bank:
        return {}
    try:
        from harness.core import bank as _bank
        catalog = _bank.load_bank(bank_version)
    except Exception:  # noqa: BLE001 — bank is optional context, never fatal here
        return {}
    parts = catalog.parts
    out: dict[str, str] = {}
    for name in names:
        if not isinstance(name, str):
            continue
        part = parts.get(name) or parts.get(_base_name(name))
        if part is not None:
            out[name] = part.get("category")
    return out


def _collect_warnings(recs: list[dict], world_size: list | None,
                      spec: dict, gravity: list) -> list[dict]:
    """The placement-warning taxonomy (GODOT_DOCS_MINING.md §1): overlapping solid
    statics, out-of-bounds bodies, isolated sensors, floating statics, duplicate names,
    PLUS the precision upgrades — non-convex polys, tunnelling-thin walls, contact-cap
    pile-ups, sensor layer/mask mismatch, rotatable-body containment goals, unsatisfiable
    'park at rest' goals, ballistic out-of-bounds forecasts, and a predicate linter.
    Deterministic, appended in a fixed pass order."""
    warnings: list[dict] = []
    solids = [r for r in recs if r["kind"] != "sensor" and r["aabb"] is not None]
    statics = [r for r in recs if r["kind"] == "static" and r["aabb"] is not None]
    dynamics = [r for r in recs if r["kind"] == "dynamic" and r["aabb"] is not None]
    known_bodies = {r["name"] for r in recs if isinstance(r["name"], str)}
    topdown = _view_of(spec) == "topdown"  # zero gravity flips the gravity-dependent passes

    # duplicate names (across ALL bodies, geometry or not).
    seen: dict[Any, int] = {}
    for r in recs:
        seen[r["name"]] = seen.get(r["name"], 0) + 1
    for name, n in seen.items():
        if n > 1 and name is not None:
            warnings.append({"kind": "duplicate_name", "bodies": [name], "count": n,
                             "detail": f"{n} bodies share the name {name!r}"})

    # overlapping solid statics (non-sensor statics with real AABB penetration).
    solid_statics = [r for r in statics if r["kind"] == "static"]
    for i in range(len(solid_statics)):
        for j in range(i + 1, len(solid_statics)):
            pen = _aabb_penetration(solid_statics[i]["aabb"], solid_statics[j]["aabb"])
            if pen > _OVERLAP_EPS:
                warnings.append({
                    "kind": "overlap_solid_statics",
                    "bodies": [solid_statics[i]["name"], solid_statics[j]["name"]],
                    "penetration": _round(pen),
                    "detail": "two solid static bodies overlap by "
                              f"{pen:.1f}px (AABB)",
                    # (§1.5) this is a tick-0 GEOMETRY fact; Area2D overlap lists/signals
                    # are one-step-latent (they reflect pre-move positions and first
                    # update only AFTER a physics step elapses), so the engine will not
                    # SIGNAL this until stepped.
                    "note": "tick-0 geometry; overlap signals are one-step-latent",
                })

    # bodies out of world bounds (only when bounds are known). Down is the gravity
    # direction (§1.1): with gravity_vector (0,-1) the floor is the low-Y world edge.
    if _is_vec2(world_size):
        wx, wy = float(world_size[0]), float(world_size[1])
        for r in recs:
            bb = r["aabb"]
            if bb is None:
                continue
            if (bb[0] < -_BOUNDS_EPS or bb[1] < -_BOUNDS_EPS
                    or bb[2] > wx + _BOUNDS_EPS or bb[3] > wy + _BOUNDS_EPS):
                warnings.append({
                    "kind": "out_of_bounds", "bodies": [r["name"]],
                    "aabb": [_round(v) for v in bb],
                    "detail": f"AABB extends outside the [0,0,{wx:g},{wy:g}] world",
                })

    # floating statics (no support: not near the world floor, not resting on / touching
    # another solid). A heuristic — free-floating platforms are often intentional. The
    # "floor" is the low-Y edge because gravity points -Y (§1.1). MEANINGLESS in a
    # top-down world (no gravity, so nothing "falls" or needs support) — suppressed.
    for r in ([] if topdown else statics):
        bb = r["aabb"]
        if bb[1] <= _SUPPORT_TOL:  # bottom near world floor (y=0)
            continue
        supported = False
        for other in solids:
            if other is r:
                continue
            ob = other["aabb"]
            if _aabb_penetration(bb, ob) > 0.0:  # touching / connected to structure
                supported = True
                break
            x_over = min(bb[2], ob[2]) - max(bb[0], ob[0])
            if x_over > 0.0 and 0.0 <= bb[1] - ob[3] <= _SUPPORT_TOL:  # resting on top
                supported = True
                break
        if not supported:
            warnings.append({
                "kind": "floating_static", "bodies": [r["name"]],
                "detail": "static body has no support below it (floating heuristic)",
            })

    # sensors overlapping nothing reachable (no body within reach of the zone).
    for r in recs:
        if r["kind"] != "sensor" or r["aabb"] is None:
            continue
        reachable = False
        for other in recs:
            if other is r or other["aabb"] is None:
                continue
            if _aabb_gap(r["aabb"], other["aabb"]) <= _REACH_TOL:
                reachable = True
                break
        if not reachable:
            warnings.append({
                "kind": "isolated_sensor", "bodies": [r["name"]],
                "detail": "sensor zone overlaps nothing reachable (no body within "
                          f"{_REACH_TOL:g}px)",
            })

    # (§1.8) non-convex poly shapes: verts go straight into ConvexPolygonShape2D with NO
    # hull repair, so a concave outline collides as UNDEFINED while _bbox still looks fine.
    for r in recs:
        if r["shape"] != "poly":
            continue
        verts = _local_verts(r["body"], "poly")
        if verts is not None and not _is_convex(verts):
            warnings.append({
                "kind": "nonconvex_poly", "bodies": [r["name"]],
                "detail": "poly vertices are not convex; ConvexPolygonShape2D does no hull "
                          "repair, so collision is undefined (make the outline convex)",
            })

    # (§1.10) tunnelling: CCD is DISABLED and VMAX=1e5 px/s is allowed, so a static wall
    # thinner than a body's per-step displacement can be passed through in one 1/60 s tick.
    if dynamics:
        for r in statics:
            body, bb = r["body"], r["aabb"]
            thin = min(bb[2] - bb[0], bb[3] - bb[1])
            if r["shape"] == "segment" or thin < _TUNNEL_THIN_PX:
                warnings.append({
                    "kind": "tunneling", "bodies": [r["name"]],
                    "thinnest_px": _round(thin),
                    "detail": f"thin static wall (thinnest dim {thin:.1f}px) with CCD off — a "
                              "fast body can tunnel it in one tick; thicken it, cap speed, "
                              "or enable CCD",
                })

    # (§1.9) contact-cap pile-ups: max_contacts_reported=8 silently drops the rest, so a
    # body touching >8 others may read "not grounded"/miss contacts.
    for r in dynamics:
        touch = sum(1 for o in recs if o is not r and o["aabb"] is not None
                    and _aabb_gap(r["aabb"], o["aabb"]) <= 0.0)
        if touch > _CONTACT_CAP:
            warnings.append({
                "kind": "contact_cap", "bodies": [r["name"]], "contacts": touch,
                "detail": f"body touches {touch} others but max_contacts_reported=8 drops "
                          "the excess — contacts()/grounded() can read false",
                "note": "one-step-latent: contact lists update after a physics step",
            })

    # (§1.11) sensor layer/mask mismatch: all bodies share collision_layer=1, so a raycast
    # sensor whose collision_mask excludes bit 1 sees NOTHING.
    sensors = spec.get("sensors")
    if isinstance(sensors, list):
        for s in sensors:
            if not isinstance(s, dict):
                continue
            mask = s.get("collision_mask", 1)
            if _is_num(mask) and (int(mask) & 1) == 0:
                warnings.append({
                    "kind": "layer_mask_mismatch", "bodies": [s.get("attach_to")],
                    "collision_mask": int(mask),
                    "detail": f"sensor collision_mask={int(mask)} excludes layer 1, where "
                              "ALL bodies live — its rays will hit nothing",
                })

    # (§1.4 / §1.6 / §1.12) predicate-driven passes: rotatable containment goals,
    # unsatisfiable park goals, and the predicate linter.
    warnings.extend(_predicate_warnings(recs, spec, known_bodies))

    # (§1.7) ballistic out-of-bounds forecast: a dynamic body with an initial velocity,
    # under zero damping + fixed dt, is projected forward; flag if it leaves the world.
    # Suppressed in a top-down world: the zero-damping ballistic projection is wrong there
    # (linear_damp>0 makes bodies coast to a stop), so the forecast is meaningless.
    if _is_vec2(world_size) and not topdown:
        warnings.extend(_forecast_oob(dynamics, world_size, gravity))

    return warnings


def _is_convex(verts: list[tuple[float, float]]) -> bool:
    """Whether a polygon (CCW or CW) is convex: every consecutive edge turns the same
    way (the cross products share a sign; collinear zeros are tolerated). A degenerate
    all-collinear outline is treated as non-convex (undefined as a 2D collision hull)."""
    n = len(verts)
    if n < 3:
        return False
    sign = 0
    for i in range(n):
        ax, ay = verts[i]
        bx, by = verts[(i + 1) % n]
        cx, cy = verts[(i + 2) % n]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if cross > 1e-9:
            if sign < 0:
                return False
            sign = 1
        elif cross < -1e-9:
            if sign > 0:
                return False
            sign = -1
    return sign != 0


def _body_is_rotatable(body: dict) -> bool:
    """A dynamic, non-circle body free to spin (torque/collision) — its AABB inflates as
    it tilts, so a containment goal on it can read false even when geometrically inside."""
    if body.get("static") or body.get("sensor") or body.get("locked_rotation"):
        return False
    return body.get("shape") in ("box", "poly", "segment")


def _predicate_warnings(recs: list[dict], spec: dict,
                        known_bodies: set) -> list[dict]:
    """Warnings derived from the predicate strings: rotatable-body containment goals
    (§1.4), unsatisfiable park-at-rest goals (§1.6), and the full predicate linter
    (§1.12: &&/||, integer division, arity, undefined body refs, illegal tokens)."""
    out: list[dict] = []
    body_by_name = {r["name"]: r["body"] for r in recs if isinstance(r["name"], str)}
    has_clamp = _has_velocity_clamp(spec)
    # A top-down world with linear_damp>0 brings a free body to rest on its own (the
    # friction analog), so a park-at-rest goal IS satisfiable there without a clamp.
    damped_topdown = _view_of(spec) == "topdown" and _topdown_linear_damp(spec) > 0.0

    for label, expr in _iter_predicates(spec):
        # -- linter -------------------------------------------------------
        for problem, detail in _lint_predicate(expr, known_bodies):
            out.append({"kind": "predicate_lint", "bodies": [], "predicate": label,
                        "problem": problem, "detail": f"[{label}] {detail}"})
        calls = _find_calls(expr)
        # -- rotatable containment (§1.4) --------------------------------
        for name, args in calls:
            if name == "contained" and len(args) >= 2 \
                    and _is_str_literal(args[0]) and _is_str_literal(args[1]):
                a, b = args[0][1:-1], args[1][1:-1]
                body = body_by_name.get(a)
                if body is not None and _body_is_rotatable(body):
                    out.append({
                        "kind": "rotatable_containment", "bodies": [a, b],
                        "predicate": label,
                        "detail": f"[{label}] contained('{a}','{b}') on a rotatable body — a "
                                  "tilted box/poly has an inflated AABB, so containment can "
                                  "read false when it is geometrically inside; lock rotation "
                                  "or widen the zone",
                    })
        # -- unsatisfiable park (§1.6) -----------------------------------
        if label == "success":
            park_body = _park_target(expr)
            if park_body is not None and not has_clamp and not damped_topdown:
                out.append({
                    "kind": "unsatisfiable_park", "bodies": [park_body],
                    "predicate": label,
                    "detail": f"success requires '{park_body}' to come to rest (speed near 0) "
                              "but damping=0 and sleeping is disabled, so a free body coasts "
                              "forever — add an on_step velocity_clamp or a friction surface, "
                              "or use an explicit stillness-window predicate",
                })
    return out


def _has_velocity_clamp(spec: dict) -> bool:
    on_step = spec.get("on_step")
    if not isinstance(on_step, list):
        return False
    return any(isinstance(b, dict) and b.get("kind") == "velocity_clamp" for b in on_step)


def _park_target(expr: str):
    """If ``expr`` demands a body be (nearly) at rest — ``speed("b") < c`` / ``<= c`` /
    ``== 0`` with a small ``c`` — return that body name, else None."""
    if not isinstance(expr, str):
        return None
    m = _re.search(r"""speed\(\s*["']([^"']+)["']\s*\)\s*(<=|<|==)\s*([0-9]+(?:\.[0-9]+)?)""",
                   expr)
    if m and float(m.group(3)) <= _STILL_EPS:
        return m.group(1)
    return None


def _iter_predicates(spec: dict):
    preds = spec.get("predicates")
    if not isinstance(preds, dict):
        return
    for key in ("success", "failure"):
        v = preds.get(key)
        if isinstance(v, str):
            yield (key, v)
    cps = preds.get("checkpoints")
    if isinstance(cps, dict):
        for k, v in cps.items():
            if isinstance(v, str):
                yield (f"checkpoint:{k}", v)


def _is_str_literal(tok: str) -> bool:
    return (len(tok) >= 2 and tok[0] in "\"'" and tok[-1] == tok[0])


def _read_arglist(expr: str, open_idx: int):
    """Return (arg_substrings, close_idx) for the call whose '(' is at ``open_idx``,
    splitting on top-level commas and respecting string literals + nested parens."""
    args: list[str] = []
    cur: list[str] = []
    depth = 0
    i, n = open_idx, len(expr)
    while i < n:
        c = expr[i]
        if c in "\"'":
            q = c
            cur.append(c)
            i += 1
            while i < n and expr[i] != q:
                cur.append(expr[i])
                if expr[i] == "\\" and i + 1 < n:
                    i += 1
                    cur.append(expr[i])
                i += 1
            if i < n:
                cur.append(expr[i])
            i += 1
            continue
        if c == "(":
            depth += 1
            if depth > 1:
                cur.append(c)
            i += 1
            continue
        if c == ")":
            depth -= 1
            if depth == 0:
                arg = "".join(cur).strip()
                if arg != "" or args:
                    args.append(arg)
                return args, i
            cur.append(c)
            i += 1
            continue
        if c == "," and depth == 1:
            args.append("".join(cur).strip())
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    args.append("".join(cur).strip())
    return args, n


def _find_calls(expr: str) -> list[tuple[str, list[str]]]:
    """Every ``name(...)`` call in ``expr`` (including nested), as (name, arg_substrings)."""
    calls: list[tuple[str, list[str]]] = []
    if not isinstance(expr, str):
        return calls
    n = len(expr)
    i = 0
    while i < n:
        c = expr[i]
        if c in "\"'":
            q = c
            i += 1
            while i < n and expr[i] != q:
                if expr[i] == "\\":
                    i += 1
                i += 1
            i += 1
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (expr[j].isalnum() or expr[j] == "_"):
                j += 1
            k = j
            while k < n and expr[k] in " \t":
                k += 1
            if k < n and expr[k] == "(":
                args, _end = _read_arglist(expr, k)
                calls.append((expr[i:j], args))
                i = k + 1  # keep scanning inside the args for nested calls
                continue
            i = j
            continue
        i += 1
    return calls


def _lint_predicate(expr, known_bodies: set) -> list[tuple[str, str]]:
    """Static replica of runner.gd's `_pred_error`, but COLLECTING every problem (not
    stopping at the first): illegal tokens/identifiers, &&/|| logical operators, integer
    division traps, wrong call arity, and undefined body references. Returns
    (problem, detail) pairs — empty when the predicate is clean."""
    if not isinstance(expr, str):
        return [("bad_type", "predicate is not a string")]
    issues = _lint_scan(expr) + _lint_int_division(expr)
    for name, args in _find_calls(expr):
        if name in _FN_ARITY and len(args) != _FN_ARITY[name]:
            issues.append(("bad_arity",
                           f"{name}() needs {_FN_ARITY[name]} arg(s), got {len(args)} — "
                           "Expression requires ALL params, so a short call is silently false"))
        for p in _FN_BODY_ARGS.get(name, ()):
            if p < len(args) and _is_str_literal(args[p]):
                ref = args[p][1:-1]
                if ref not in known_bodies:
                    issues.append(("undefined_body",
                                   f"{name}() references body '{ref}', which is not defined"))
    return issues


def _lint_scan(expr: str) -> list[tuple[str, str]]:
    """Char scan mirroring `_pred_error`: reject identifiers outside the allow-list, `.`/
    `[`/`]`/`\\` and other stray characters, and &&/||/&/| (rewrite to and/or/not)."""
    issues: list[tuple[str, str]] = []
    n = len(expr)
    i = 0
    while i < n:
        c = expr[i]
        if c in "\"'":
            q = c
            i += 1
            while i < n and expr[i] != q:
                if expr[i] == "\\":
                    issues.append(("bad_char", "backslash not allowed in a string literal"))
                i += 1
            if i >= n:
                issues.append(("bad_char", "unterminated string literal"))
                break
            i += 1
            continue
        if c in " \t\r\n":
            i += 1
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (expr[j].isalnum() or expr[j] == "_"):
                j += 1
            word = expr[i:j]
            if word not in _ALLOWED_IDENTS:
                issues.append(("bad_identifier", f"identifier not allowed: '{word}'"))
            i = j
            continue
        if c.isdigit():
            j = i
            while j < n and (expr[j].isdigit() or expr[j] == "."):
                j += 1
            i = j
            continue
        if expr[i:i + 2] in ("&&", "||"):
            issues.append(("logical_operator",
                           f"'{expr[i:i + 2]}' is rejected; use 'and'/'or'"))
            i += 2
            continue
        if c in "&|":
            issues.append(("logical_operator",
                           f"'{c}' is rejected; use 'and'/'or'/'not'"))
            i += 1
            continue
        if c in _ALLOWED_OPS:
            i += 1
            continue
        issues.append(("bad_char", f"character not allowed: '{c}'"))
        i += 1
    return issues


def _lint_int_division(expr: str) -> list[tuple[str, str]]:
    """Flag ``/`` whose right operand is an INTEGER literal — Godot's Expression floors
    int/int, so ``steps / 2`` silently truncates; suggest a float literal."""
    issues: list[tuple[str, str]] = []
    n = len(expr)
    i = 0
    while i < n:
        c = expr[i]
        if c in "\"'":
            q = c
            i += 1
            while i < n and expr[i] != q:
                if expr[i] == "\\":
                    i += 1
                i += 1
            i += 1
            continue
        if c == "/":
            k = i + 1
            while k < n and expr[k] in " \t":
                k += 1
            if k < n and expr[k].isdigit():
                m = k
                while m < n and expr[m].isdigit():
                    m += 1
                if not (m < n and expr[m] == "."):
                    lit = expr[k:m]
                    issues.append(("integer_division",
                                   f"'/ {lit}' floors (integer division); write '{lit}.0' "
                                   "for a real fraction"))
        i += 1
    return issues


# --------------------------------------------------------------------------- #
# Ballistic forecast (§1.7): zero damping + fixed dt ⇒ exact per-tick projection.
# --------------------------------------------------------------------------- #
def ballistic_forecast(x0: float, y0: float, vx: float, vy: float,
                       gx: float, gy: float, n_ticks: int,
                       dt: float = _TICK_DT) -> list[tuple[float, float]]:
    """Project a free body's centre N physics ticks forward, EXACTLY as the engine
    integrates it under zero damping: per tick ``v += g·dt`` then ``x += v·dt`` (the
    semi-implicit Euler the runner steps at a fixed dt=1/60). Returns the per-tick
    positions."""
    out: list[tuple[float, float]] = []
    x, y, vX, vY = float(x0), float(y0), float(vx), float(vy)
    for _ in range(int(n_ticks)):
        vX += gx * dt
        vY += gy * dt
        x += vX * dt
        y += vY * dt
        out.append((x, y))
    return out


def _controlled_body(recs: list[dict]):
    for r in recs:
        b = r["body"]
        if b.get("control") and not b.get("static"):
            return b
    return None


def _launch_dv(spec: dict, name, mass: float) -> tuple[float, float]:
    """The strongest instantaneous launch (|Δvx|, |Δvy|) the action set imparts to the
    named body via impulse/set_velocity (impulse Δv = J/mass). Heading-dependent verbs
    (thrust/force/torque) are excluded — their direction is not known statically."""
    max_vx = max_vy = 0.0
    act = spec.get("act")
    if not isinstance(act, dict):
        return (0.0, 0.0)
    for binds in act.values():
        if not isinstance(binds, list):
            continue
        for vc in binds:
            if not isinstance(vc, dict) or vc.get("body") != name:
                continue
            verb = vc.get("verb")
            vec = vc.get("vec")
            if verb in ("impulse", "set_velocity") and _is_vec2(vec):
                vx, vy = float(vec[0]), float(vec[1])
                if verb == "impulse" and mass > 0:
                    vx /= mass
                    vy /= mass
                max_vx = max(max_vx, abs(vx))
                max_vy = max(max_vy, abs(vy))
    return (max_vx, max_vy)


def _ballistic_summary(recs: list[dict], spec: dict, gravity: list):
    """A closed-form jump forecast for the controlled body (§1.7): apex height, airtime,
    and horizontal range at its strongest launch. None when there is no controllable jump."""
    body = _controlled_body(recs)
    if body is None:
        return None
    name = body.get("name")
    mass = float(body.get("mass", 1.0) or 1.0)
    vx, vy = _launch_dv(spec, name, mass)
    g = abs(gravity[1]) if gravity and gravity[1] != 0 else _DEFAULT_GRAVITY
    if vy <= 0 or g <= 0:
        return None
    apex = vy * vy / (2.0 * g)          # peak height above launch
    airtime_s = 2.0 * vy / g            # up-and-back to the launch height
    return {
        "body": name,
        "gravity": [_round(gravity[0]), _round(gravity[1])],
        "launch_dv": [_round(vx), _round(vy)],
        "apex_px": _round(apex),
        "airtime_ticks": int(round(airtime_s / _TICK_DT)),
        "horizontal_range_px": _round(vx * airtime_s),
    }


def _forecast_oob(dynamics: list[dict], world_size: list, gravity: list) -> list[dict]:
    """For each dynamic body with an explicit initial velocity, project its centre forward
    (zero damping, fixed dt) and flag the tick at which it first leaves the world — the
    §1.7 quantitative complement to the static out-of-bounds check."""
    out: list[dict] = []
    wx, wy = float(world_size[0]), float(world_size[1])
    gx, gy = float(gravity[0]), float(gravity[1])
    for r in dynamics:
        body = r["body"]
        vel = body.get("velocity")
        pos = body.get("pos")
        if not (_is_vec2(vel) and _is_vec2(pos)):
            continue
        if float(vel[0]) == 0.0 and float(vel[1]) == 0.0:
            continue
        path = ballistic_forecast(pos[0], pos[1], vel[0], vel[1], gx, gy, _FORECAST_HORIZON)
        for tick, (x, y) in enumerate(path, start=1):
            if x < 0.0 or y < 0.0 or x > wx or y > wy:
                out.append({
                    "kind": "forecast_oob", "bodies": [r["name"]], "tick": tick,
                    "position": [_round(x), _round(y)],
                    "detail": f"initial velocity carries '{r['name']}' out of the "
                              f"[0,0,{wx:g},{wy:g}] world by tick {tick} "
                              f"(≈{tick / 60.0:.2f}s), ballistic under gravity",
                })
                break
    return out


# --------------------------------------------------------------------------- #
# Frozen JSON schemas + registry (OpenAI native function-calling shape)
# --------------------------------------------------------------------------- #
DESIGN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "design",
        "description": ("Generate a new game from a prompt, or REVISE a certified "
                        "game when 'directive' is given (then 'prompt_or_source' is "
                        "the source). Returns a compact generation report."),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt_or_source": {
                    "type": "string",
                    "description": ("A design prompt (generate) or a certified game "
                                    "source string (revise, when 'directive' set)."),
                },
                "directive": {
                    "type": "string",
                    "description": ("If present, revise the source by the SMALLEST "
                                    "edit applying this directive."),
                },
                "engine": {
                    "type": "string",
                    "enum": ["py", "js", "godot"],
                    "description": "Target engine; defaults to the harness default.",
                },
                "backend": {
                    "type": "string",
                    "enum": ["auto", "anthropic", "openrouter", "template"],
                    "description": "Generation backend; 'auto' by default.",
                },
            },
            "required": ["prompt_or_source"],
            "additionalProperties": False,
        },
    },
}

CERTIFY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "certify",
        "description": ("Run the frozen verifier funnel on a game. depth=verify "
                        "(G0-G3, default) | harden (+G4) | grade (+RL) | full. "
                        "Thresholds are fixed and NOT tunable."),
        "parameters": {
            "type": "object",
            "properties": {
                "game_path": {
                    "type": "string",
                    "description": "Path to the generated game file to certify.",
                },
                "depth": {
                    "type": "string",
                    "enum": ["verify", "harden", "grade", "full"],
                    "default": "verify",
                    "description": ("Funnel depth. Default 'verify' is cheap; "
                                    "'harden'/'grade'/'full' are expensive (budgeted)."),
                },
            },
            "required": ["game_path"],
            "additionalProperties": False,
        },
    },
}

RETRIEVE_PARTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "retrieve_parts",
        "description": ("Retrieve the themed menu of pre-certified bank parts for a "
                        "prompt. Deterministic, free; a pure function of the prompt "
                        "and the pinned bank version."),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The design prompt to retrieve parts for.",
                },
                "engine": {
                    "type": "string",
                    "enum": ["py", "js", "godot"],
                    "default": "py",
                    "description": "Engine the menu is rendered for.",
                },
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
    },
}

INSPECT_WORLD_SCHEMA = {
    "type": "function",
    "function": {
        "name": "inspect_world",
        "description": ("Statically inspect a Godot game-spec (or a bodies-only "
                        "fragment mid-composition) WITHOUT running the engine: returns "
                        "each entity's AABB, bank role, and kind (static/dynamic/"
                        "sensor), plus placement warnings (solid-static overlaps, "
                        "out-of-bounds bodies, isolated sensors, floating statics, "
                        "duplicate names). Free and deterministic."),
        "parameters": {
            "type": "object",
            "properties": {
                "spec_or_fragment": {
                    "type": "object",
                    "description": ("A full Godot spec dict (meta/bodies/act/predicates) "
                                    "OR a bodies-only fragment ({\"bodies\": [...]}). "
                                    "Analyzed from spec fields alone; no engine runs."),
                },
            },
            "required": ["spec_or_fragment"],
            "additionalProperties": False,
        },
    },
}

# The frozen tool spine + the static-analysis tool, in OpenAI `tools=[...]` order.
REGISTRY: list[dict] = [DESIGN_SCHEMA, CERTIFY_SCHEMA, RETRIEVE_PARTS_SCHEMA,
                        INSPECT_WORLD_SCHEMA]

# name -> callable, for dispatching a native function call.
_DISPATCH: dict[str, Callable[..., dict]] = {
    "design": design,
    "certify": certify,
    "retrieve_parts": retrieve_parts,
    "inspect_world": inspect_world,
}


def tool_names() -> list[str]:
    """The frozen tool names, in registry order."""
    return [t["function"]["name"] for t in REGISTRY]


def dispatch(name: str, arguments: dict | None = None) -> dict:
    """Invoke a frozen tool by name with a JSON-decoded ``arguments`` dict."""
    if name not in _DISPATCH:
        raise KeyError(f"unknown tool {name!r}; known: {sorted(_DISPATCH)}")
    return _DISPATCH[name](**(arguments or {}))
