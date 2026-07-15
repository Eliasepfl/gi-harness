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
        recs.append({"name": name, "aabb": aabb, "kind": kind})

    warnings = _collect_warnings(recs, world_size)

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


def _collect_warnings(recs: list[dict], world_size: list | None) -> list[dict]:
    """The warning taxonomy: overlapping solid statics, out-of-bounds bodies, isolated
    sensors, floating statics, duplicate names — deterministic, in declaration order."""
    warnings: list[dict] = []
    solids = [r for r in recs if r["kind"] != "sensor" and r["aabb"] is not None]
    statics = [r for r in recs if r["kind"] == "static" and r["aabb"] is not None]

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
                })

    # bodies out of world bounds (only when bounds are known).
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
    # another solid). A heuristic — free-floating platforms are often intentional.
    for r in statics:
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

    return warnings


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
