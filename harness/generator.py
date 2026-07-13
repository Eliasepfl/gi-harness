"""v1 legacy — see gamegen.py for v2.

2D environment generation + repair loop (CONTRACTS §6).

Two backends:
- "anthropic": calls the Anthropic SDK (claude-opus-4-8, adaptive thinking).
- "template":  offline library (harness.templates), no network.
- "auto":      anthropic if the client is available, else fall back to templates.

The loop: writes the code, calls harness.verifier.verify_scene (lazy import),
and on failure re-generates using the JSON report as feedback, within budget
(OMNI-EPIC pattern: we discard rather than grind).
"""
from __future__ import annotations

import json
import os
import re

try:  # effectively a lazy import: the template backend must work without the package
    import anthropic
except ImportError:  # pragma: no cover - environment dependent
    anthropic = None

from harness import templates

_MODEL = "claude-opus-4-8"
_MAX_TOKENS = 16000
_COMPILE_CAP = 5  # max attempts for compile errors (L0 builds)


class _BackendUnavailable(Exception):
    """Signals that the anthropic backend is not usable -> fall back to templates."""


# --- Generation prompt --------------------------------------------------------

_SYSTEM_PROMPT = """You generate 2D physics scenes for an instrumented pymunk engine.
The code you produce SEES ONLY the `sdk` object (no access to pymunk, os, etc.).
World 800x600, y points UP, gravity (0,-900), ground = static segment at y=0.

# SceneSDK API (construction — used in build_scene)
- sdk.add_ground(friction=0.9) -> "ground"           # static segment at y=0
- sdk.add_wall(name, a, b, friction=0.9)              # static segment a->b
- sdk.add_box(name, pos, size=(40,40), mass=1.0, *, body="dynamic", friction=0.7, elasticity=0.1)
- sdk.add_ball(name, pos, radius=15.0, mass=1.0, *, body="dynamic", friction=0.7, elasticity=0.5)
- sdk.add_platform(name, pos, size=(120,12))          # static box
- sdk.spawn_agent(pos, size=(24,36), mass=1.0)        # dynamic box "agent", rotation locked
- sdk.add_zone(name, pos, size)                       # static sensor (no collision)
- sdk.on_contact(a, b, flag, once=True)               # set a flag on contact
- sdk.set_flag(key, value) / sdk.get_flag(key, default=None)

# SceneSDK API (reads — used in get_success, PURE, no side effects)
- sdk.query(name) -> {"pos":[x,y],"vel":[vx,vy],"angle":a,"angular_vel":w,
                      "bbox":[l,b,r,t],"body_type":"dynamic|static","is_agent":bool}
- sdk.contacts(a, b) -> bool
- sdk.get_flag(key, default=None)

# EXACT scene module format
SCENE_DESCRIPTION = "original command"
AVAILABLE_ACTIONS = ["left", "right", "jump", "noop"]

def build_scene(sdk):
    # populate the SDK; MUST call sdk.add_ground() and sdk.spawn_agent(...)

def get_success(sdk) -> bool:
    # PURE predicate: reads query/contacts/get_flag, mutates nothing

# Rules
- NO import, no open/exec/eval: only the `sdk` argument is used.
- get_success must be FALSE at t=0 (goal not already reached) and become
  reachable through the actions; it never modifies state.
- Suggested calibration: place objects AT REST on the ground surface, which is
  at y=1 (centre y = radius + 1 for a ball, y = height/2 + 1 for a box/agent) —
  an object that falls more than ~2 px while settling is REJECTED. Keep entities
  inside the 800x600 world and avoid any initial overlap.

# Complete example
```python
SCENE_DESCRIPTION = "push the ball into the zone"
AVAILABLE_ACTIONS = ["left", "right", "jump", "noop"]


def build_scene(sdk):
    sdk.add_ground()
    sdk.spawn_agent((90, 18))
    sdk.add_ball("ball", (390, 18), radius=18)
    sdk.add_zone("goal", (660, 45), (110, 90))


def get_success(sdk):
    b = sdk.query("ball")
    z = sdk.query("goal")
    cx = (b["bbox"][0] + b["bbox"][2]) / 2.0
    cy = (b["bbox"][1] + b["bbox"][3]) / 2.0
    return (z["bbox"][0] <= cx <= z["bbox"][2]) and (z["bbox"][1] <= cy <= z["bbox"][3])
```
Always answer with a single ```python block containing the complete module."""


def _first_user_msg(command):
    return (f'Command: "{command}"\n'
            "Generate the complete scene module, conforming to the format and rules, "
            "in a single ```python block.")


def _repair_user_msg(report):
    hint = report.get("hint", "") if isinstance(report, dict) else ""
    body = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    return ("The previous scene failed verification. Fix ONLY the scene code "
            "(same constraints, no imports).\n"
            f"Hint: {hint}\nJSON report:\n{body}\n"
            "Return the complete corrected module in a single ```python block.")


# --- Anthropic backend --------------------------------------------------------

def _make_client():
    """Zero-arg client (key via env or an `ant auth login` profile)."""
    if anthropic is None:
        raise _BackendUnavailable("anthropic package not installed")
    return anthropic.Anthropic()


def _llm_complete(client, system, messages):
    """One messages.create call; return the concatenated text blocks."""
    resp = client.messages.create(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        thinking={"type": "adaptive"},
        system=system,
        messages=messages,
    )
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "\n".join(parts)


def _extract_code(text):
    """First ```python block of the response (fallback: first block, else raw)."""
    m = re.search(r"```python\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1)
    return text


# --- Loop utilities -----------------------------------------------------------

def _slug(command):
    s = re.sub(r"[^a-z0-9]+", "_", (command or "").lower()).strip("_")
    return s[:40] or "scene"


def _write_scene(out_dir, command, attempt, code):
    path = os.path.join(out_dir, f"{_slug(command)}_a{attempt}.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return path


def _verify(scene_path):
    """Lazy import of verify_scene; None if module B does not exist."""
    try:
        from harness.verifier import verify_scene
    except ImportError:
        return None
    return verify_scene(scene_path)


def _is_build_error(report):
    try:
        return report["layers"]["L0_static"]["checks"]["builds"]["pass"] is False
    except (KeyError, TypeError):
        return False


def _repair_loop(command, out_dir, produce, backend_used, max_repairs, note):
    """Write -> verify -> repair loop, shared by both backends."""
    attempts = []
    feedback = None
    compile_failures = 0
    scene_path = None
    verdict = None
    max_attempts = max_repairs + 1  # 1 initial attempt + max_repairs repairs

    n = 0
    while True:
        n += 1
        code = produce(feedback)
        scene_path = _write_scene(out_dir, command, n, code)
        report = _verify(scene_path)

        if report is None:
            attempts.append({"report": {"verdict": "PARTIAL",
                                        "note": "harness.verifier.verify_scene unavailable"}})
            verdict = "PARTIAL"
            break

        attempts.append({"report": report})

        if report.get("passed"):
            verdict = "COMPLETED"
            break

        if _is_build_error(report):
            compile_failures += 1
            if compile_failures >= _COMPILE_CAP:
                verdict = "ENV_ERROR"  # OMNI-EPIC: discard the scene
                break

        if n >= max_attempts:
            verdict = report.get("failure_class") or "ENV_ERROR"
            break

        feedback = report

    result = {
        "scene_path": scene_path,
        "attempts": attempts,
        "verdict": verdict,
        "backend": backend_used,
    }
    if note:
        result["note"] = note
    return result


def _run_template(command, out_dir, max_repairs, note):
    code = templates.build_scene_source(command)
    return _repair_loop(command, out_dir, lambda feedback: code,
                        "template", max_repairs, note)


def _run_llm(command, out_dir, max_repairs):
    if anthropic is None:
        raise _BackendUnavailable("anthropic package not installed")
    try:
        client = _make_client()
    except (anthropic.AuthenticationError, anthropic.APIConnectionError,
            anthropic.AnthropicError) as e:
        raise _BackendUnavailable(type(e).__name__)

    messages = [{"role": "user", "content": _first_user_msg(command)}]
    state = {"first": True}

    def produce(feedback):
        if feedback is not None:
            messages.append({"role": "user", "content": _repair_user_msg(feedback)})
        try:
            text = _llm_complete(client, _SYSTEM_PROMPT, messages)
        except (anthropic.AuthenticationError, anthropic.APIConnectionError) as e:
            if state["first"]:
                raise _BackendUnavailable(type(e).__name__)
            raise
        state["first"] = False
        messages.append({"role": "assistant", "content": text})
        return _extract_code(text)

    return _repair_loop(command, out_dir, produce, "anthropic", max_repairs, None)


# --- Public API ---------------------------------------------------------------

def generate(command, out_dir="scenes/generated", backend="auto", max_repairs=4):
    """Generate a scene for `command` and return the loop report.

    -> {"scene_path", "attempts", "verdict", "backend", "note"?}
      verdict in COMPLETED | PARTIAL | ENV_ERROR | GOAL_ERROR
    """
    if backend not in ("auto", "anthropic", "template"):
        backend = "auto"
    os.makedirs(out_dir, exist_ok=True)

    note = None
    if backend in ("auto", "anthropic"):
        try:
            return _run_llm(command, out_dir, max_repairs)
        except _BackendUnavailable as e:
            note = f"anthropic unavailable ({e}); falling back to templates"
    return _run_template(command, out_dir, max_repairs, note)
