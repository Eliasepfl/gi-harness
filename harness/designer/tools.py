"""The FROZEN tool layer v0 — the designer's read/oracle spine (§3 P1).

Three tools, human-authored and frozen (the agent may only PROPOSE changes to
this decomposition, never edit it):

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

Each tool has a JSON-schema'd input, a compact typed-dict output (the shapes from
the plan's §3 table) plus a verbose handle to the full underlying report, and is
exported in ``REGISTRY`` for OpenAI native function-calling. ``dispatch(name,
arguments)`` routes a function call by name. No write verb lives here: the spine
is read/oracle-only (``designer_write`` is the sole write path, §4).
"""
from __future__ import annotations

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

# The frozen tool spine, in OpenAI `tools=[...]` order.
REGISTRY: list[dict] = [DESIGN_SCHEMA, CERTIFY_SCHEMA, RETRIEVE_PARTS_SCHEMA]

# name -> callable, for dispatching a native function call.
_DISPATCH: dict[str, Callable[..., dict]] = {
    "design": design,
    "certify": certify,
    "retrieve_parts": retrieve_parts,
}


def tool_names() -> list[str]:
    """The frozen tool names, in registry order."""
    return [t["function"]["name"] for t in REGISTRY]


def dispatch(name: str, arguments: dict | None = None) -> dict:
    """Invoke a frozen tool by name with a JSON-decoded ``arguments`` dict."""
    if name not in _DISPATCH:
        raise KeyError(f"unknown tool {name!r}; known: {sorted(_DISPATCH)}")
    return _DISPATCH[name](**(arguments or {}))
