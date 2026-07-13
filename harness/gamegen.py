"""Open-ended 2D game generator + repair loop (CONTRACTS §3, v2).

Where v1 (generator.py) filled parameters into a genre the harness had already
chosen, v2 hands the LLM a minimal physics substrate (`world`) and an OPEN
prompt: it designs a WHOLE game - its own actions, rules and win/lose - and the
harness only checks universal sanity + solvability.

Backends:
- "anthropic":  Anthropic SDK (claude-opus-4-8, adaptive thinking).
- "openrouter": OpenRouter chat-completions (free model, key in env.py) - the
                volume backend; same system prompt + repair loop as anthropic.
- "template":   two tiny built-in v2 games, for offline tests/demos (no network).
- "auto":       anthropic -> openrouter -> template, in that order; result["backend"]
                reflects what ran and result["note"] explains any fallback.

The loop writes the module, calls harness.gameverify.verify_game (lazy import),
and on failure re-generates with the full JSON report as feedback, within budget
(OMNI-EPIC pattern: on repeated compile errors we discard rather than grind).

Every run is written into its OWN sandbox dir (<out_dir>/<slug>/) and wrapped in
an integrity manifest check (harness.integrity): if any tracked base file mutates
mid-run the verdict is forced to INVALIDATED (OBJECTIVES hard rule).
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import time

from harness import integrity

try:  # lazily needed: the template backend must run without the package
    import anthropic
except ImportError:  # pragma: no cover - environment dependent
    anthropic = None

try:  # only the openrouter backend needs HTTP; template/anthropic run without it
    import requests
except ImportError:  # pragma: no cover - environment dependent
    requests = None

_MODEL = "claude-opus-4-8"
_MAX_TOKENS = 16000
_COMPILE_CAP = 5  # max attempts for env/compile errors (G0 load/build) -> discard

# OpenRouter backend ([eng.] = engineering choices)
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_OPENROUTER_MAX_TOKENS = 16000
_OPENROUTER_TIMEOUT = 300          # seconds, per request [eng.]
_OPENROUTER_MAX_RETRIES = 3        # extra attempts on 429/5xx before giving up [eng.]
_OPENROUTER_BACKOFF = 1.0          # initial backoff seconds, doubled each retry [eng.]
# Reasoning-token cap: without one, free reasoning models (e.g. hy3) burn the
# whole max_tokens budget thinking and return content=null. Override via the
# OPENROUTER_REASONING_MAX_TOKENS secret; "0" removes the field entirely
# (for non-reasoning models). [eng.]
_OPENROUTER_REASONING_DEFAULT = 4000

# Telemetry ledger (harness.telemetry) — one JSON line appended per run.
_LEDGER_PATH = "runs/ledger.jsonl"

_UNSOLVED_HINT = ("no random rollout reached success - make the goal easier to "
                  "reach or actions more effective")


class _BackendUnavailable(Exception):
    """An LLM backend is not usable -> fall back to the next backend/templates.

    Its message MUST NEVER contain secret material (the API key).
    """


# --- Secrets: os.environ first, then a gitignored env.py at the repo root ----
# The key is NEVER printed, logged, written, or embedded in any exception message.

def _repo_root() -> str:
    """Repo root = parent of the harness package directory."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env_module():
    """Lazily import <repo_root>/env.py; None if the file is absent/unloadable.

    Guarded on every failure mode so a missing env.py simply means "backend
    unavailable" rather than an error.
    """
    path = os.path.join(_repo_root(), "env.py")
    if not os.path.isfile(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location("_gi_env", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:  # noqa: BLE001 - any failure -> treat as no config
        return None


def _resolve_secret(name: str):
    """Resolve a secret by name: os.environ wins, then env.py; None if neither."""
    if name in os.environ:
        return os.environ[name]
    module = _load_env_module()
    if module is not None:
        return getattr(module, name, None)
    return None


def _redact(text: str, secret) -> str:
    """Defence in depth: never let the key appear in a surfaced string."""
    if secret and isinstance(text, str) and secret in text:
        return text.replace(secret, "***")
    return text


# --- THE OPEN PROMPT ---------------------------------------------------------
# This is the whole point of v2: teach the substrate and the format, then get
# out of the way. It must NOT decide the genre or hand over a worked design.

_SYSTEM_PROMPT = """You are a game designer and a physics programmer. From the user's prompt, design an ORIGINAL small 2D physics game and implement it as a single Python module. The prompt is a seed, not a spec - invent the mechanic and surprise us.

Your code runs against ONE object, `world`, a minimal 2D physics substrate (pymunk underneath). The world is 800x600, y points UP, default gravity is (0, -900), one physics step is 1/60 s. There are no pixels - everything is engine state.

# World API - the ONLY thing your code may touch

## Construction - from build(), and optionally on_step()
world.add(name, shape="box", *, pos, size=None, radius=None, a=None, b=None,
          vertices=None, mass=1.0, static=False, sensor=False, friction=0.7,
          elasticity=0.3, velocity=(0, 0), angle=0.0, locked_rotation=False) -> str
    # pos=(x,y) is REQUIRED for every shape. shape in {"box","circle","segment","poly"}.
    # box needs size=(w,h); circle needs radius; poly needs vertices=[(x,y),...];
    # segment needs a=(x,y), b=(x,y) given LOCAL to pos (use pos=(0,0) for absolute
    # endpoints). static=True -> immovable; sensor=True -> no collision but detectable.
world.remove(name)
world.pin(a, b, anchor_a=None, anchor_b=None)          # rigid PinJoint
world.pivot(a, b, point)                               # PivotJoint at a world point
world.spring(a, b, rest_length, stiffness, damping, anchor_a=None, anchor_b=None)
world.set_gravity(gx, gy)                              # any direction, or (0,0)
world.control(name)                                    # designate THE controlled body

## Dynamics - from act() and on_step()
world.impulse(name, vec)        # instantaneous momentum change
world.force(name, vec)          # continuous force for this step
world.set_velocity(name, vec)
world.set_flag(key, value)      # persistent game state
world.flag(key, default=None)
world.on_contact(a, b, flag, once=True)   # set `flag` when a and b touch
world.rng                       # a seeded random.Random - the ONLY randomness allowed
world.steps                     # int: physics steps elapsed (use for timers)

## Queries - PURE reads, for success()/failure()/on_step()
world.entities() -> list[str]
world.query(name) -> {"pos":[x,y], "vel":[vx,vy], "angle":a, "angular_vel":w,
                      "bbox":[left,bottom,right,top], "shape":str,
                      "static":bool, "sensor":bool, "controlled":bool}
world.contacts(a, b) -> bool
world.touching(name) -> list[str]        # non-sensor bodies in contact with name
world.grounded(name) -> bool             # supported from below
world.in_bounds(name, margin=0.0) -> bool
world.penetration_depth(a, b) -> float

That is the entire API. There is no step(), no snapshot, no rendering, no file
access, no imports. If it is not listed above, it does not exist for you.

# Module format - define EXACTLY these symbols (no imports; only `world` is used)

TITLE = "short title"
PROMPT = "the user's original prompt, verbatim"
ACTIONS = ["...", "..."]        # 2 to 8 short strings YOU choose - the whole move set

def build(world):
    \"\"\"Create every entity. MUST call world.control(<name>) on exactly one dynamic body.\"\"\"

def act(world, action):
    \"\"\"Apply ONE action's effect (impulse/force/set_velocity/set_flag). Once per decision tick.\"\"\"

def on_step(world):
    \"\"\"OPTIONAL. Runs once per physics step - timers, moving hazards, scoring, custom rules.\"\"\"

def success(world) -> bool:
    \"\"\"PURE win predicate. Reads state only, never mutates. MUST be False at t=0.\"\"\"

def failure(world) -> bool:
    \"\"\"OPTIONAL. PURE lose predicate.\"\"\"

def checkpoints(world) -> dict[str, bool]:
    \"\"\"REQUIRED. 1 to 6 ordered milestone predicates - dict insertion order is the
    intended progression toward success. Short snake_case keys. Pure like success;
    EVERY value MUST be False at t=0. Decompose YOUR OWN rules into stages.\"\"\"

Milestones are how the harness will tell you exactly where your game is stuck if it
fails - make them meaningful stages, not restatements of success. The harness latches
each milestone at the first tick it becomes True, so predicates may be instantaneous
reads (a ship that once touched the pad keeps that milestone) - never track state
yourself inside checkpoints. On the winning path every milestone must fire at or
before the win.

How it runs: each decision tick calls act(world, chosen_action), then advances the
physics 6 times (calling on_step after each), then checks failure() then success().
The action is picked by the player/solver; there is no built-in idle move unless you add one.

# Hard constraints (a game that breaks these is rejected)
- No imports whatsoever. Your only tool is `world`.
- At most 14 bodies total.
- Between 2 and 8 actions.
- Randomness ONLY through world.rng (never import random, never fake it with constants).
- Exactly one world.control(...) call, on a DYNAMIC (non-static) body.
- success(world) MUST be False at t=0 and stay pure (no side effects).
- Player agency is mandatory: doing nothing - or repeating one idle action forever - must NEVER win.
- The goal must be reachable within ~800 physics steps by SOME sequence of actions.
- Keep bodies inside the 800x600 world at rest; avoid initial overlaps.
- checkpoints(world) MUST return the same 1..6 snake_case keys on every call, all
  False at t=0, pure, and every milestone must be reachable on the way to success.

# Structure-only stub - shows the SHAPE of a module, NOT a design to copy.
# It is deliberately boring: do NOT imitate its mechanic, entities, or goal.
```python
TITLE = "poke"
PROMPT = "seed prompt"
ACTIONS = ["go", "wait"]
def build(world):
    world.add("dot", "circle", pos=(120, 40), radius=12); world.control("dot")
    world.add("marker", "box", pos=(680, 40), size=(50, 50), static=True, sensor=True)
def act(world, action):
    if action == "go": world.impulse("dot", (130, 0))
def success(world):
    return world.query("dot")["pos"][0] > 640
def checkpoints(world):
    return {"halfway": world.query("dot")["pos"][0] > 400}
```

# Invent a mechanic - do NOT default to a platformer with left/right/jump
Reach into the substrate: custom or flipping gravity (world.set_gravity); pin/pivot/spring
joints for pendulums, catapults, wrecking balls, tethers, ragdolls; sensors as triggers,
checkpoints, or hazards; timers and rhythm via world.steps; moving obstacles driven from
on_step; counters, combos, and multi-stage goals via flags. A slingshot, a gravity maze, a
juggling act, a falling-sand catcher, a swinging pendulum puzzle - anything but the obvious.
Make winning require deliberate play.

# Output format
First a DESIGN block of about six lines, then the code:

DESIGN
Theme: <one line>
Entities: <the bodies and their roles>
Mechanic twist: <what makes it original>
Actions: <each action and what it does>
Milestones: <the ordered checkpoints and what stage each marks>
Win / Lose: <success and, if any, failure>

Then EXACTLY ONE fenced ```python block with the complete module. Nothing after it."""


def _first_user_msg(prompt):
    return (f'User prompt: "{prompt}"\n'
            "Design an original 2D physics game for this prompt. Return the "
            "DESIGN block, then exactly one ```python module that follows the "
            "required format and every hard constraint.")


def _repair_user_msg(report):
    fc = report.get("failure_class") if isinstance(report, dict) else None
    hint = report.get("hint", "") if isinstance(report, dict) else ""
    progress = report.get("progress") if isinstance(report, dict) else None
    prefix = ""
    if progress:
        # Checkpoint diagnosis (v2.1): name the stuck boundary first.
        prefix = (f"Solvability diagnosis: {hint}. Focus the fix on the segment "
                  "between the named milestones.\n")
    if fc == "UNSOLVED":
        hint = _UNSOLVED_HINT + (f" ({hint})" if hint else "")
    body = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    return (prefix +
            "The previous game failed verification. Fix ONLY the game module "
            "(same format and constraints, no imports).\n"
            f"Hint: {hint}\nVerifier report (JSON):\n{body}\n"
            "Return the corrected DESIGN block and one ```python module.")


# --- Anthropic backend -------------------------------------------------------

def _make_client():
    """Zero-arg client (key via env or an `ant auth login` profile)."""
    if anthropic is None:
        raise _BackendUnavailable("anthropic package not installed")
    return anthropic.Anthropic()


def _llm_complete(client, system, messages):
    """One messages.create call; return the concatenated text blocks.

    Adaptive thinking is set explicitly (off by default on this model). NO
    temperature/top_p/prefill - they 400 on claude-opus-4-8.
    """
    resp = client.messages.create(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        thinking={"type": "adaptive"},
        system=system,
        messages=messages,
    )
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "\n".join(parts)


# --- OpenRouter backend ------------------------------------------------------

def _retry_after(resp, default: float) -> float:
    """Honour a Retry-After header (seconds) when present, else `default`."""
    try:
        raw = resp.headers.get("Retry-After")
    except Exception:  # noqa: BLE001 - header bag may be anything in a mock
        raw = None
    if raw:
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            pass
    return default


def _openrouter_error(resp, key) -> str:
    """A concise, key-free error string from a 4xx OpenRouter response."""
    msg = None
    try:
        data = resp.json()
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            msg = err.get("message")
        elif isinstance(err, str):
            msg = err
    except Exception:  # noqa: BLE001 - non-JSON body
        msg = None
    if not msg:
        try:
            msg = resp.text
        except Exception:  # noqa: BLE001
            msg = None
    msg = (msg or "request rejected").strip()
    if len(msg) > 200:  # a reasoning-model body can be huge; keep notes readable
        msg = msg[:200] + "..."
    status = getattr(resp, "status_code", "?")
    return _redact(f"OpenRouter HTTP {status}: {msg}", key)


def _openrouter_content(resp):
    """choices[0].message.content from a 200 body; None if malformed/empty.

    A null/blank content (reasoning models spending the whole budget thinking)
    counts as missing so the caller can attempt the cap-halving salvage.
    """
    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if isinstance(content, str) and content.strip():
        return content
    return None


def _reasoning_cap() -> int:
    """Resolve the reasoning-token cap (secret > default; 0 disables the field)."""
    raw = _resolve_secret("OPENROUTER_REASONING_MAX_TOKENS")
    if raw is None:
        return _OPENROUTER_REASONING_DEFAULT
    try:
        return max(0, int(str(raw).strip()))
    except (TypeError, ValueError):
        return _OPENROUTER_REASONING_DEFAULT


def _openrouter_payload(model, system, messages, cap: int) -> dict:
    payload = {
        "model": model,
        "max_tokens": _OPENROUTER_MAX_TOKENS,
        "messages": [{"role": "system", "content": system}] + list(messages),
    }
    if cap > 0:
        payload["reasoning"] = {"max_tokens": cap}
    return payload


def _openrouter_request(key, model, system, messages, cap: int):
    """Send one completion request; return the 200 response.

    Retries up to _OPENROUTER_MAX_RETRIES times on 429/5xx (and transient
    network errors) with exponential backoff, honouring Retry-After. 4xx
    auth/model errors are not retryable -> _BackendUnavailable carrying the
    API's (key-free) error message.
    """
    headers = {"Authorization": f"Bearer {key}"}
    payload = _openrouter_payload(model, system, messages, cap)

    backoff = _OPENROUTER_BACKOFF
    for attempt in range(_OPENROUTER_MAX_RETRIES + 1):
        try:
            resp = requests.post(_OPENROUTER_URL, headers=headers, json=payload,
                                 timeout=_OPENROUTER_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - requests.RequestException etc.
            # Transient network trouble: retry, then give up as unavailable.
            if attempt < _OPENROUTER_MAX_RETRIES:
                time.sleep(backoff)
                backoff *= 2
                continue
            raise _BackendUnavailable(
                _redact(f"OpenRouter unreachable: {type(exc).__name__}", key))

        status = getattr(resp, "status_code", 0)
        if status == 200:
            return resp

        if status == 429 or status >= 500:
            # Rate-limited / server-side: back off and retry within budget.
            if attempt < _OPENROUTER_MAX_RETRIES:
                time.sleep(_retry_after(resp, backoff))
                backoff *= 2
                continue
            raise _BackendUnavailable(
                f"OpenRouter rate-limited/unavailable (HTTP {status}) after "
                f"{_OPENROUTER_MAX_RETRIES} retries")

        # 4xx auth/model error: not retryable.
        raise _BackendUnavailable(_openrouter_error(resp, key))


def _openrouter_complete(system, messages):
    """One OpenRouter chat completion -> choices[0].message.content.

    The request carries a reasoning-token cap (see _reasoning_cap): without it
    free reasoning models can spend the entire max_tokens budget thinking and
    return content=null. If a 200 still comes back with null/blank content, we
    salvage ONCE by halving the cap (cheaper thinking leaves room for output)
    before declaring _BackendUnavailable. Missing config/`requests` ->
    _BackendUnavailable so `auto` falls through cleanly.
    """
    if requests is None:
        raise _BackendUnavailable("requests package not installed")
    key = _resolve_secret("OPENROUTER_API_KEY")
    model = _resolve_secret("OPENROUTER_MODEL")
    if not key or not model:
        raise _BackendUnavailable("OpenRouter API key or model not configured")

    cap = _reasoning_cap()
    salvage_left = 1
    while True:
        resp = _openrouter_request(key, model, system, messages, cap)
        content = _openrouter_content(resp)
        if content is not None:
            return content
        # 200 with null/blank content. With no cap to halve (cap disabled)
        # there is nothing to salvage; otherwise retry once at half the cap.
        if salvage_left > 0 and cap > 0:
            salvage_left -= 1
            cap = max(1, cap // 2)
            continue
        raise _BackendUnavailable(_openrouter_error(resp, key))


def _extract_code(text):
    """First ```python block (fallback: first fenced block, else raw text)."""
    m = re.search(r"```python\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1)
    return text


def _extract_design(text):
    """The DESIGN block: from 'DESIGN' up to the first code fence."""
    m = re.search(r"(?is)\bDESIGN\b(.*?)(?=```)", text)
    if m:
        return ("DESIGN" + m.group(1)).strip()
    return text.split("```", 1)[0].strip()


# --- Loop utilities ----------------------------------------------------------

def _slug(prompt):
    s = re.sub(r"[^a-z0-9]+", "_", (prompt or "").lower()).strip("_")
    return s[:40] or "game"


def _write_attempt(run_dir, attempt, code):
    """Write one attempt into the per-run sandbox dir as a{n}.py."""
    path = os.path.join(run_dir, f"a{attempt}.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return path


def _verify(game_path):
    """Lazy import of verify_game; None if module F does not exist yet.

    An error-shaped result ({"error": ...}: sandbox timeout, worker crash) is
    an INFRASTRUCTURE failure, not a game failure — retry once; if it persists,
    surface it so the loop can stop instead of repairing blind on an empty hint."""
    try:
        from harness.gameverify import verify_game
    except ImportError:
        return None
    report = verify_game(game_path)
    if isinstance(report, dict) and "error" in report and "layers" not in report:
        time.sleep(2.0)
        report = verify_game(game_path)
    return report


def _is_verify_error(report) -> bool:
    """True for error-shaped reports (no funnel layers, just an error record)."""
    return isinstance(report, dict) and "error" in report and "layers" not in report


def _repair_loop(run_dir, produce, backend_used, max_repairs, note):
    """Shared write -> verify -> repair loop for every backend.

    Attempts are written ONLY into `run_dir` (the per-run sandbox), one file
    per attempt (a1.py, a2.py, ...). The winning/final attempt is later promoted
    to <slug>.py by generate_game.
    """
    attempts = []
    feedback = None
    env_failures = 0
    game_path = None
    verdict = None
    design = ""
    max_attempts = max_repairs + 1  # 1 initial attempt + max_repairs repairs

    n = 0
    while True:
        n += 1
        code, design = produce(feedback)
        game_path = _write_attempt(run_dir, n, code)
        report = _verify(game_path)

        if report is None:
            attempts.append({"report": {
                "verdict": "PARTIAL",
                "note": "harness.gameverify.verify_game unavailable"}})
            verdict = "PARTIAL"
            break

        attempts.append({"report": report})

        if _is_verify_error(report):
            # Verification infrastructure failed twice on this code: stop the
            # run honestly (the game code may be fine) — never repair blind.
            verdict = "VERIFY_ERROR"
            note = (note + "; " if note else "") + \
                f"verification infrastructure failed: {report['error'].get('type', 'unknown')}"
            break

        if report.get("passed"):
            verdict = "COMPLETED"
            break

        if report.get("failure_class") == "ENV_ERROR":
            env_failures += 1
            if env_failures >= _COMPILE_CAP:
                verdict = "ENV_ERROR"  # OMNI-EPIC: discard, don't grind
                break

        if n >= max_attempts:
            verdict = report.get("failure_class") or "ENV_ERROR"
            break

        feedback = report

    result = {
        "game_path": game_path,
        "attempts": attempts,
        "verdict": verdict,
        "backend": backend_used,
        "design": design,
    }
    if note:
        result["note"] = note
    return result


def _run_template(prompt, run_dir, max_repairs, note):
    name = _select_template(prompt)
    code = _TEMPLATE_GAMES[name]
    design = _DESIGNS[name]
    return _repair_loop(run_dir, lambda feedback: (code, design),
                        "template", max_repairs, note)


def _run_anthropic(prompt, run_dir, max_repairs):
    if anthropic is None:
        raise _BackendUnavailable("anthropic package not installed")
    try:
        client = _make_client()
    except (anthropic.AuthenticationError, anthropic.APIConnectionError,
            anthropic.AnthropicError) as e:
        raise _BackendUnavailable(type(e).__name__)

    messages = [{"role": "user", "content": _first_user_msg(prompt)}]
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
        return _extract_code(text), _extract_design(text)

    return _repair_loop(run_dir, produce, "anthropic", max_repairs, None)


def _run_openrouter(prompt, run_dir, max_repairs):
    """OpenRouter backend: SAME system prompt + repair loop as anthropic.

    Availability (requests + configured key/model) is probed up front so that
    `auto` can fall through to the next backend without a wasted attempt. A
    _BackendUnavailable raised mid-loop (auth/rate-limit) propagates to
    generate_game, which then falls back to templates.
    """
    if requests is None:
        raise _BackendUnavailable("requests package not installed")
    if not _resolve_secret("OPENROUTER_API_KEY") or not _resolve_secret("OPENROUTER_MODEL"):
        raise _BackendUnavailable("OpenRouter API key or model not configured")

    messages = [{"role": "user", "content": _first_user_msg(prompt)}]

    def produce(feedback):
        if feedback is not None:
            messages.append({"role": "user", "content": _repair_user_msg(feedback)})
        text = _openrouter_complete(_SYSTEM_PROMPT, messages)
        messages.append({"role": "assistant", "content": text})
        return _extract_code(text), _extract_design(text)

    return _repair_loop(run_dir, produce, "openrouter", max_repairs, None)


# --- Public API --------------------------------------------------------------

# Ordered LLM backends tried under `auto`.
_LLM_RUNNERS = {"anthropic": _run_anthropic, "openrouter": _run_openrouter}


def _dispatch(prompt, run_dir, backend, max_repairs):
    """Pick and run a backend, honouring the auto fallback chain."""
    if backend == "template":
        return _run_template(prompt, run_dir, max_repairs, None)

    if backend == "auto":
        order = ["anthropic", "openrouter"]
    else:  # explicit "anthropic" or "openrouter": that one, then templates
        order = [backend]

    notes = []
    for name in order:
        try:
            return _LLM_RUNNERS[name](prompt, run_dir, max_repairs)
        except _BackendUnavailable as e:
            notes.append(f"{name} unavailable ({e})")
    note = "; ".join(notes) + "; falling back to templates" if notes else None
    return _run_template(prompt, run_dir, max_repairs, note)


def _finalize_game(run_dir, slug, result):
    """Promote the final attempt to <slug>.py inside the run dir; repoint path."""
    src = result.get("game_path")
    if src and os.path.isfile(src):
        final = os.path.join(run_dir, f"{slug}.py")
        if os.path.abspath(src) != os.path.abspath(final):
            shutil.copyfile(src, final)
        result["game_path"] = final
    return result


def _model_used(backend):
    """The actual model id behind a backend label (for the telemetry ledger)."""
    if backend == "anthropic":
        return _MODEL
    if backend == "openrouter":
        return _resolve_secret("OPENROUTER_MODEL")
    return backend  # "template" (or unknown)


def generate_game(prompt, out_dir="scenes/games", backend="auto", max_repairs=4):
    """Generate an original game for `prompt` and return the loop report.

    Each run gets its OWN sandbox dir `<out_dir>/<slug>/` (attempts a1.py, a2.py,
    ...; the final game promoted to <slug>.py). The run may write ONLY there. The
    whole run is bracketed by an integrity manifest check over the tracked base
    files: any base-code mutation mid-run forces verdict INVALIDATED. Every run
    is appended to the telemetry ledger (harness.telemetry, runs/ledger.jsonl);
    telemetry is best-effort and can never break a run.

    -> {"game_path": str|None, "attempts": [...], "verdict", "backend", "design",
        "integrity": "ok" | {"violated": [...]}, "note"?}
       verdict in COMPLETED | PARTIAL | ENV_ERROR | GOAL_ERROR | UNSOLVED |
       INVALIDATED
    """
    if backend not in ("auto", "anthropic", "openrouter", "template"):
        backend = "auto"
    os.makedirs(out_dir, exist_ok=True)

    slug = _slug(prompt)
    run_dir = os.path.join(out_dir, slug)
    os.makedirs(run_dir, exist_ok=True)

    # Freeze the base code for the duration of the run.
    root = _repo_root()
    before = integrity.snapshot(root)

    t0 = time.time()
    result = _dispatch(prompt, run_dir, backend, max_repairs)
    wall_s = time.time() - t0
    _finalize_game(run_dir, slug, result)

    # Base code must be untouched: a mutation invalidates the whole run.
    violated = integrity.violations(before, root)
    if violated:
        result["integrity"] = {"violated": violated}
        result["verdict"] = "INVALIDATED"
    else:
        result["integrity"] = "ok"

    # Telemetry: counting failures/repairs is a first-class statistic.
    try:
        from harness import telemetry
        telemetry.record_run(result, prompt, _model_used(result.get("backend")),
                             wall_s, path=_LEDGER_PATH)
    except Exception:  # noqa: BLE001 - telemetry must never break a run
        pass
    return result


# --- Built-in v2 games (offline test fixture - NOT a template library) --------
# Two tiny complete games in the §2 format, both random-solvable. They exist so
# the offline/template backend and the tests have real artifacts; do not grow
# this into a genre library - that is exactly what v2 exists to avoid.

def _select_template(prompt):
    p = (prompt or "").lower()
    if any(k in p for k in ("catch", "drop", "fall", "paddle")):
        return "drop"
    return "drift"


# "drift" - an air-hockey puck on frictionless ice; impulses in four directions
# nudge it onto a sensor pad. Zero gravity, bouncy walls; momentum carries.
_DRIFT = '''TITLE = "Drift"
PROMPT = "guide the puck across the ice onto the glowing pad"
ACTIONS = ["left", "right", "up", "down"]


def build(world):
    world.set_gravity(0.0, 0.0)
    world.add("puck", "circle", pos=(180.0, 150.0), radius=16.0,
              mass=1.0, friction=0.2, elasticity=0.6)
    world.control("puck")
    world.add("pad", "box", pos=(560.0, 430.0), size=(200.0, 200.0),
              static=True, sensor=True)
    world.add("w_left", "segment", pos=(0.0, 0.0), a=(8.0, 0.0), b=(8.0, 600.0),
              static=True, elasticity=0.9)
    world.add("w_right", "segment", pos=(0.0, 0.0), a=(792.0, 0.0),
              b=(792.0, 600.0), static=True, elasticity=0.9)
    world.add("w_bottom", "segment", pos=(0.0, 0.0), a=(0.0, 8.0), b=(800.0, 8.0),
              static=True, elasticity=0.9)
    world.add("w_top", "segment", pos=(0.0, 0.0), a=(0.0, 592.0),
              b=(800.0, 592.0), static=True, elasticity=0.9)


def act(world, action):
    j = 70.0
    if action == "left":
        world.impulse("puck", (-j, 0.0))
    elif action == "right":
        world.impulse("puck", (j, 0.0))
    elif action == "up":
        world.impulse("puck", (0.0, j))
    elif action == "down":
        world.impulse("puck", (0.0, -j))


def success(world):
    p = world.query("puck")
    z = world.query("pad")
    cx = (p["bbox"][0] + p["bbox"][2]) / 2.0
    cy = (p["bbox"][1] + p["bbox"][3]) / 2.0
    return (z["bbox"][0] <= cx <= z["bbox"][2]) and (z["bbox"][1] <= cy <= z["bbox"][3])


def checkpoints(world):
    p = world.query("puck")["pos"]
    dx = p[0] - 180.0
    dy = p[1] - 150.0
    return {
        "moved_off_start": (dx * dx + dy * dy) > 1600.0,
        "crossed_midline": p[0] > 400.0,
        "entered_upper_half": p[1] > 300.0,
    }
'''

# "drop" - catch a falling ball with a sliding paddle before it reaches the
# floor. Reduced gravity buys reaction time; the ball drops off-centre so a
# stationary paddle always loses. Has a failure() condition.
_DROP = '''TITLE = "Catch"
PROMPT = "slide the paddle to catch the falling ball before it hits the floor"
ACTIONS = ["left", "right"]


def build(world):
    world.set_gravity(0.0, -240.0)
    world.add("floor", "segment", pos=(0.0, 0.0), a=(0.0, 12.0), b=(800.0, 12.0),
              static=True, friction=0.6)
    world.add("w_left", "segment", pos=(0.0, 0.0), a=(6.0, 0.0), b=(6.0, 600.0),
              static=True)
    world.add("w_right", "segment", pos=(0.0, 0.0), a=(794.0, 0.0),
              b=(794.0, 600.0), static=True)
    world.add("paddle", "box", pos=(400.0, 24.0), size=(150.0, 22.0),
              mass=2.0, friction=0.5, locked_rotation=True)
    world.control("paddle")
    bx = world.rng.uniform(210.0, 300.0)
    world.add("ball", "circle", pos=(bx, 560.0), radius=15.0,
              mass=1.0, friction=0.4, elasticity=0.1)


def act(world, action):
    v = 260.0
    if action == "left":
        world.set_velocity("paddle", (-v, 0.0))
    elif action == "right":
        world.set_velocity("paddle", (v, 0.0))


def success(world):
    return "ball" in world.touching("paddle")


def failure(world):
    return world.query("ball")["bbox"][1] <= 14.0


def checkpoints(world):
    b = world.query("ball")
    p = world.query("paddle")
    overlap = b["bbox"][2] >= p["bbox"][0] and b["bbox"][0] <= p["bbox"][2]
    return {
        "paddle_under_ball": overlap and p["pos"][1] < b["pos"][1],
        "ball_in_lower_half": b["pos"][1] < 300.0,
    }
'''

_TEMPLATE_GAMES = {"drift": _DRIFT, "drop": _DROP}

_DESIGNS = {
    "drift": ("DESIGN\n"
              "Theme: an air-hockey puck adrift on frictionless ice.\n"
              "Entities: one controlled puck, one sensor pad, four bouncy walls.\n"
              "Mechanic twist: zero gravity - each action is a directional "
              "impulse and momentum carries.\n"
              "Actions: left/right/up/down each shove the puck along an axis.\n"
              "Milestones: moved_off_start -> crossed_midline -> "
              "entered_upper_half.\n"
              "Win / Lose: win when the puck's centre sits over the pad; no lose "
              "condition.\n"),
    "drop": ("DESIGN\n"
             "Theme: catch a falling ball on a sliding paddle.\n"
             "Entities: one controlled paddle, one falling ball, a floor and two "
             "walls.\n"
             "Mechanic twist: reduced gravity buys time and the ball drops "
             "off-centre, so standing still loses.\n"
             "Actions: left/right set the paddle's horizontal velocity.\n"
             "Milestones: paddle_under_ball -> ball_in_lower_half.\n"
             "Win / Lose: win when the ball rests on the paddle; lose if the "
             "ball reaches the floor.\n"),
}
