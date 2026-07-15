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

from harness.core import integrity
from harness.gen import prompts
from harness.gen import retrieval
from harness.gen import skill_context
from harness.gen.prompts_js import SYSTEM_PROMPT_JS

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
    """Repo root = grandparent of this module's package dir (harness/gen/)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
#
# The prompt is no longer one giant literal: it is assembled from single-concern
# SECTION FILES under harness/gen/prompts/ (contract / api_py / api_js / rules /
# orientation / design_block / bank_menu) by prompts.compose(engine, menu_text).
# `_SYSTEM_PROMPT` (py, no menu) and prompts_js.SYSTEM_PROMPT_JS (js, no menu)
# stay as module-level shims so existing callers and tests keep their names; the
# per-run system prompt (optionally carrying a retrieved Tier-1b parts menu) is
# composed fresh inside generate_game. The section files are byte-frozen by the
# run-integrity manifest exactly like base code.
_SYSTEM_PROMPT = prompts.compose("py")


def _engine_lang(engine):
    """(language name, code-fence) for a given engine."""
    if engine == "js":
        return ("JavaScript", "javascript")
    if engine == "godot":
        # The godot artifact is a declarative JSON spec, not code.
        return ("JSON", "json")
    if engine == "gdscript":
        # The gdscript artifact is a real .gd game class (CODE, not a spec).
        return ("GDScript", "gdscript")
    return ("Python", "python")


# The godot lane's per-run system prompt (menu-free shim, byte-identical to
# compose("godot")); parallels _SYSTEM_PROMPT / SYSTEM_PROMPT_JS.
_SYSTEM_PROMPT_GODOT = prompts.compose("godot")
# The gdscript lane's per-run system prompt (menu-free shim, byte-identical to
# compose("gdscript")); parallels the godot/js/py shims.
_SYSTEM_PROMPT_GDSCRIPT = prompts.compose("gdscript")


def _system_prompt(engine, menu_text=None):
    """The open system prompt for the target engine (py default).

    With ``menu_text`` (a retrieved Tier-1b parts menu) the prompt is composed
    fresh so the menu is spliced in; without it the pre-composed, menu-free shim
    is returned (byte-identical to ``compose(engine)``).
    """
    if menu_text:
        return prompts.compose(engine, menu_text)
    if engine == "js":
        return SYSTEM_PROMPT_JS
    if engine == "godot":
        return _SYSTEM_PROMPT_GODOT
    if engine == "gdscript":
        return _SYSTEM_PROMPT_GDSCRIPT
    return _SYSTEM_PROMPT


# --- GDScript lane: contract + advisory skill knowledge ----------------------
# How many gd-agentic DOMAIN skills to retrieve alongside the godot-master
# orchestrator (a genre blueprint + a physics/architecture skill; see
# skill_context). The orchestrator (godot-master, ~12k tokens) leads the block.
_SKILL_K = 2
# Token budget for the whole injected block: the orchestrator's decision matrix
# is the high-value base, so give it room (~half) + the domain skills (~half).
_SKILL_MAX_TOKENS = 24000  # godot-master (~12k) UNtruncated + 2 domain skills (~12k)

# Advisory framing for the injected knowledge block: the CONTRACT above is
# binding; the reference knowledge is craft to draw on, never to copy.
_SKILL_ADVISORY_HEADER = (
    "## Reference knowledge (advisory)\n"
    "Craft guidance you MAY draw on; adapt, never copy; the CONTRACT above is "
    "binding, this is not.")


def _gdscript_system_prompt(prompt, k=_SKILL_K):
    """The gdscript system prompt: the GameAPI CONTRACT + an advisory skill block.

    The CONTRACT (api_gdscript.md) is sent unchanged; when the gd-agentic-skills
    library is present, a clearly delimited ``## Reference knowledge (advisory)``
    section carries the retrieved skill bodies (attributed, budget-bounded). When
    the library is absent, ``render_skill_context`` returns ``""`` and the prompt
    degrades cleanly to the contract alone.
    """
    contract = prompts.gdscript_contract()
    try:
        context = skill_context.render_skill_context(prompt, k=k, max_tokens=_SKILL_MAX_TOKENS)
    except Exception:  # noqa: BLE001 - a library hiccup must never break a run
        context = ""
    if not context:
        return contract
    return f"{contract}\n\n{_SKILL_ADVISORY_HEADER}\n\n{context}"


def _injected_skills(prompt, k=_SKILL_K):
    """The skill names injected into the gdscript prompt (for the ledger).

    Deterministic and identical to what ``_gdscript_system_prompt`` splices in;
    ``[]`` when the library is absent or nothing matches, so we can measure later
    whether the injected knowledge helped."""
    try:
        return [s.name for s in skill_context.select_skills(prompt, k=k)]
    except Exception:  # noqa: BLE001 - a library hiccup must never break a run
        return []


def _game_ext(engine):
    if engine == "js":
        return ".js"
    if engine == "godot":
        # The .spec.json extension is what detect_engine routes to the godot lane.
        return ".spec.json"
    if engine == "gdscript":
        # A plain .gd game class; detect_engine routes .gd to the gdscript lane.
        return ".gd"
    return ".py"


def _first_user_msg(prompt, engine="py"):
    _, fence = _engine_lang(engine)
    # The godot artifact is one JSON object (the spec); the gdscript artifact is
    # one .gd game class (code); py/js are a code module.
    if engine == "godot":
        artifact = "spec (one JSON object)"
    elif engine == "gdscript":
        artifact = "GDScript game class (one .gd file)"
    else:
        artifact = "module"
    return (f'User prompt: "{prompt}"\n'
            "Design an original 2D physics game for this prompt. Return the "
            f"DESIGN block, then exactly one ```{fence} {artifact} that follows the "
            "required format and every hard constraint.")


_PROMPT_PY_RE = re.compile(r"""(?m)^\s*PROMPT\s*=\s*(['"])(.*?)\1""")
_PROMPT_JS_RE = re.compile(r"""(?m)^\s*(?:const|let|var)\s+PROMPT\s*=\s*(['"])(.*?)\1""")


def _extract_prompt(source):
    """The game's declared PROMPT string (py or js), or None if not found."""
    for rx in (_PROMPT_PY_RE, _PROMPT_JS_RE):
        m = rx.search(source or "")
        if m:
            return m.group(2)
    return None


def _revise_user_msg(source, directive, engine="py"):
    """Seed the loop with a CERTIFIED game + a MINIMAL-EDIT task (revise mode).

    Unlike ``_first_user_msg`` (design a whole new game from a prompt) this hands
    the model the full current module and asks for the smallest edit that applies
    ``directive`` — keeping entities, actions, checkpoint names, the PROMPT line
    and every other stage intact. The model returns the FULL revised module, which
    then goes through the SAME verify->repair loop as a fresh generation.
    """
    _, fence = _engine_lang(engine)
    return (
        "This game is CERTIFIED — it already passes every verification oracle. "
        "Apply ONLY the following curriculum directive, as a MINIMAL EDIT: change "
        "as little as possible. KEEP every entity, the ACTIONS list, all "
        "checkpoint names, and every other stage exactly as they are; edit only "
        "what the directive asks for.\n\n"
        f"{directive}\n\n"
        "Current CERTIFIED game module:\n"
        f"```{fence}\n{source}\n```\n\n"
        f"Return the DESIGN block, then exactly one ```{fence} module: the FULL "
        "revised game (not a diff), same required format and every hard "
        "constraint. Preserve the PROMPT string verbatim (provenance); you may "
        "add a short version suffix to TITLE.")


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
        data = _openrouter_json(resp)
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


def _openrouter_json(resp):
    """Parse an OpenRouter body, tolerating keep-alive padding.

    On long non-streaming generations OpenRouter prepends anti-timeout padding
    (blank/comment lines) before the JSON document; a strict resp.json() then
    fails and a perfectly good completion looks unusable. Parse from the first
    '{' instead. (Diagnosed live: ~5 KB of padding on 800 s GLM generations.)
    """
    try:
        return resp.json()
    except ValueError:
        pass
    try:
        text = resp.text
        start = text.find("{")
        if start < 0:
            return None
        return json.loads(text[start:])
    except (ValueError, AttributeError):
        return None


def _openrouter_content(resp):
    """choices[0].message.content from a 200 body; None if malformed/empty.

    A null/blank content (reasoning models spending the whole budget thinking)
    counts as missing so the caller can attempt the cap-halving salvage.
    """
    data = _openrouter_json(resp)
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
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


def _extract_spec(text):
    """The godot spec JSON object from a model reply: first ``{`` .. last ``}``.

    The godot artifact is DATA, so we do not look for a code fence — we take the
    outermost brace slice, tolerating a DESIGN block before it, a ```json fence
    around it, keep-alive padding, or trailing prose after it (the same padding
    tolerance _openrouter_json applies to a raw body). Verification does the JSON
    parsing; this only isolates the object. Falls back to the whole text when no
    brace pair is present."""
    if not isinstance(text, str):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end < start:
        return text
    return text[start:end + 1]


def _extract_code(text, engine="py"):
    """First fenced code block for the engine (fallback: any fenced block, raw text)."""
    if engine == "godot":
        return _extract_spec(text)
    if engine == "gdscript":
        # The gdscript artifact is real code -> the same fence-extraction machinery
        # as py/js, keyed to a ```gdscript (or ```gd) fence.
        for lang in ("gdscript", "gd"):
            m = re.search(rf"```{lang}\s*\n(.*?)```", text, re.DOTALL)
            if m:
                return m.group(1)
    elif engine == "js":
        for lang in ("javascript", "js"):
            m = re.search(rf"```{lang}\s*\n(.*?)```", text, re.DOTALL)
            if m:
                return m.group(1)
    elif engine == "gdscript":
        for lang in ("gdscript", "gd"):
            m = re.search(rf"```{lang}\s*\n(.*?)```", text, re.DOTALL)
            if m:
                return m.group(1)
    else:
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


def _write_attempt(run_dir, attempt, code, ext=".py"):
    """Write one attempt into the per-run sandbox dir as a{n}<ext>."""
    path = os.path.join(run_dir, f"a{attempt}{ext}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return path


def _verify(game_path):
    """Lazy import of verify_game; None if module F does not exist yet.

    An error-shaped result ({"error": ...}: sandbox timeout, worker crash) is
    an INFRASTRUCTURE failure, not a game failure — retry once; if it persists,
    surface it so the loop can stop instead of repairing blind on an empty hint."""
    try:
        from harness.verify.gameverify import verify_game
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


def _repair_loop(run_dir, produce, backend_used, max_repairs, note, ext=".py"):
    """Shared write -> verify -> repair loop for every backend.

    Attempts are written ONLY into `run_dir` (the per-run sandbox), one file
    per attempt (a1<ext>, a2<ext>, ...). The winning/final attempt is later
    promoted to <slug><ext> by generate_game. verify_game routes by extension.
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
        game_path = _write_attempt(run_dir, n, code, ext)
        report = _verify(game_path)

        if report is None:
            attempts.append({"report": {
                "verdict": "PARTIAL",
                "note": "harness.verify.gameverify.verify_game unavailable"}})
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


def _run_template(prompt, run_dir, max_repairs, note, engine="py"):
    name = _select_template(prompt)
    if engine == "gdscript":
        # One built-in .gd fixture (mirrors the js/godot single-template shape); the
        # per-prompt keyword selection has no gdscript variants yet.
        code = _TEMPLATE_GAMES_GDSCRIPT.get(name, _DRIFT_GDSCRIPT)
        design = _DESIGNS_GDSCRIPT.get(name, _DESIGNS_GDSCRIPT["drift"])
    elif engine == "godot":
        # One certified spec fixture (mirrors the js single-template shape); the
        # per-prompt keyword selection has no godot variants yet.
        code = _TEMPLATE_GAMES_GODOT.get(name, _TRAVERSE_GODOT)
        design = _DESIGNS_GODOT.get(name, _DESIGNS_GODOT["traverse"])
    elif engine == "js":
        code = _TEMPLATE_GAMES_JS.get(name, _DRIFT_JS)
        design = _DESIGNS_JS.get(name, _DESIGNS_JS["drift"])
    else:
        code = _TEMPLATE_GAMES[name]
        design = _DESIGNS[name]
    return _repair_loop(run_dir, lambda feedback: (code, design),
                        "template", max_repairs, note, _game_ext(engine))


def _run_anthropic(prompt, run_dir, max_repairs, engine="py", system=None,
                   first_user=None):
    if anthropic is None:
        raise _BackendUnavailable("anthropic package not installed")
    try:
        client = _make_client()
    except (anthropic.AuthenticationError, anthropic.APIConnectionError,
            anthropic.AnthropicError) as e:
        raise _BackendUnavailable(type(e).__name__)

    if system is None:
        system = _system_prompt(engine)
    first = first_user if first_user is not None else _first_user_msg(prompt, engine)
    messages = [{"role": "user", "content": first}]
    state = {"first": True}

    def produce(feedback):
        if feedback is not None:
            messages.append({"role": "user", "content": _repair_user_msg(feedback)})
        try:
            text = _llm_complete(client, system, messages)
        except (anthropic.AuthenticationError, anthropic.APIConnectionError) as e:
            if state["first"]:
                raise _BackendUnavailable(type(e).__name__)
            raise
        state["first"] = False
        messages.append({"role": "assistant", "content": text})
        return _extract_code(text, engine), _extract_design(text)

    return _repair_loop(run_dir, produce, "anthropic", max_repairs, None,
                        _game_ext(engine))


def _run_openrouter(prompt, run_dir, max_repairs, engine="py", system=None,
                    first_user=None):
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

    if system is None:
        system = _system_prompt(engine)
    first = first_user if first_user is not None else _first_user_msg(prompt, engine)
    messages = [{"role": "user", "content": first}]

    def produce(feedback):
        if feedback is not None:
            messages.append({"role": "user", "content": _repair_user_msg(feedback)})
        text = _openrouter_complete(system, messages)
        messages.append({"role": "assistant", "content": text})
        return _extract_code(text, engine), _extract_design(text)

    return _repair_loop(run_dir, produce, "openrouter", max_repairs, None,
                        _game_ext(engine))


# --- Public API --------------------------------------------------------------

# Ordered LLM backends tried under `auto`.
_LLM_RUNNERS = {"anthropic": _run_anthropic, "openrouter": _run_openrouter}


def _dispatch(prompt, run_dir, backend, max_repairs, engine="py", system=None,
              first_user=None):
    """Pick and run a backend, honouring the auto fallback chain.

    ``system`` is the pre-composed system prompt (optionally carrying a retrieved
    Tier-1b parts menu); LLM backends reuse it for every attempt so the menu is
    PINNED for the whole run. The template backend ignores it. ``first_user``, when
    given, overrides the initial user message for the LLM backends (revise mode
    seeds the loop with the certified source + a minimal-edit task instead of the
    from-scratch design prompt); the template backend ignores it too.
    """
    if backend == "template":
        return _run_template(prompt, run_dir, max_repairs, None, engine)

    if backend == "auto":
        order = ["anthropic", "openrouter"]
    else:  # explicit "anthropic" or "openrouter": that one, then templates
        order = [backend]

    notes = []
    for name in order:
        try:
            return _LLM_RUNNERS[name](prompt, run_dir, max_repairs, engine, system,
                                      first_user)
        except _BackendUnavailable as e:
            notes.append(f"{name} unavailable ({e})")
    note = "; ".join(notes) + "; falling back to templates" if notes else None
    return _run_template(prompt, run_dir, max_repairs, note, engine)


def _resolve_engine(engine):
    """Target engine: explicit arg > HARNESS_ENGINE env > 'godot' default.

    'godot' (declarative spec, the default post-pivot), 'gdscript' (an agent-written
    .gd game class verified through the serve contract; see notes/engines/
    GDSCRIPT_LANE.md), 'js' (Planck) or 'py' (pymunk). The py/js lanes are frozen
    legacy: still fully selectable, but no longer the default. gdscript is selectable
    now but does NOT change the default (the lane switch is a later call, after the
    head-to-head)."""
    if engine is None:
        engine = os.environ.get("HARNESS_ENGINE", "godot")
    e = str(engine).lower()
    if e == "js":
        return "js"
    if e == "py":
        return "py"
    if e == "gdscript":
        return "gdscript"
    return "godot"


def _finalize_game(run_dir, slug, result, ext=".py"):
    """Promote the final attempt to <slug><ext> inside the run dir; repoint path."""
    src = result.get("game_path")
    if src and os.path.isfile(src):
        final = os.path.join(run_dir, f"{slug}{ext}")
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


# --- Parts-bank pipeline (Tier-1b retrieval + ledger telemetry) --------------

# py: the SECOND positional arg of world.part("<instance>", "<part>", ...) is the
# bank part KIND (the first is the caller's instance name). js: there is no
# world.part yet, so the bank part name appears as a world.add("<name>", ...)
# entity name (the naming rule the menu states).
_PART_PY_RE = re.compile(
    r"""world\.part\(\s*["'][^"']*["']\s*,\s*["']([A-Za-z0-9_]+)["']""")
_ADD_JS_RE = re.compile(r"""world\.add\(\s*["']([A-Za-z0-9_.]+)["']""")
# godot: the spec's body NAMES carry the part identity (skinned by name, like js);
# only bodies use a "name" key, so this reliably enumerates the declared entities.
_NAME_GODOT_RE = re.compile(r'"name"\s*:\s*"([A-Za-z0-9_.]+)"')
# gdscript: the .gd game names each body in its add_body/add_static/add_sensor call
# (skinned by name, like godot/js); the first string arg is the body identity.
_NAME_GDSCRIPT_RE = re.compile(
    r"""add_(?:body|static|sensor)\(\s*["']([A-Za-z0-9_.]+)["']""")


def _bank_names():
    """The set of bank part names (empty on any bank-load problem)."""
    try:
        return set(retrieval._bank.load_bank("v1").parts)
    except Exception:  # noqa: BLE001 - a bank problem must never break a run
        return set()


def _parse_parts_used(source, engine, bank_names):
    """Bank parts actually instantiated in the final game source (deduped, ordered).

    py -> the KIND arg of every ``world.part(...)`` call. js -> every
    ``world.add`` entity name that matches a known bank part name.
    """
    if not source:
        return []
    eng = str(engine).lower()
    if eng == "js":
        candidates = _ADD_JS_RE.findall(source)
        want = bank_names
    elif eng == "godot":
        # Spec body names matched against the bank (skinning parity with js).
        candidates = _NAME_GODOT_RE.findall(source)
        want = bank_names
    elif eng == "gdscript":
        # add_body/add_static/add_sensor names matched against the bank (skinning
        # parity with godot/js).
        candidates = _NAME_GDSCRIPT_RE.findall(source)
        want = bank_names
    else:
        candidates = _PART_PY_RE.findall(source)
        want = bank_names or None  # py: keep parsed kinds even if bank unavailable
    seen, out = set(), []
    for name in candidates:
        if want is not None and name not in want:
            continue
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _read_source(path):
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""
    return ""


def generate_game(prompt, out_dir="scenes/games", backend="auto", max_repairs=4,
                  engine=None, use_bank=True):
    """Generate an original game for `prompt` and return the loop report.

    `engine` picks the target language: "py" (pymunk, default) or "js" (Planck.js
    in Node). It defaults to the HARNESS_ENGINE env var, then "py". The JS path
    swaps the system prompt for the JS variant and writes .js attempt files;
    verify_game routes by extension automatically.

    `use_bank` (default True) turns on the Tier-1b parts pipeline (Option A): the
    harness retrieves a themed menu of pre-certified bank parts from the prompt
    BEFORE the call (deterministic lexical retrieval, no LLM, no network), splices
    it into the system prompt, and PINS it for the whole run — repairs reuse the
    same menu (a moving retrieved context destabilises the model; pipeline.md B.3).
    The menu is advisory: world.add stays the escape hatch, and below the score
    threshold the run falls back to a legend-only prompt. The template backend
    bypasses retrieval entirely (menu_mode "off").

    Each run gets its OWN sandbox dir `<out_dir>/<slug>/` (attempts a1<ext>,
    a2<ext>, ...; the final game promoted to <slug><ext>). The run may write ONLY
    there. The whole run is bracketed by an integrity manifest check over the
    tracked base files: any base-code mutation mid-run forces verdict INVALIDATED.
    Every run is appended to the telemetry ledger (harness.telemetry,
    runs/ledger.jsonl); telemetry is best-effort and can never break a run.

    -> {"game_path": str|None, "attempts": [...], "verdict", "backend", "design",
        "engine": "py"|"js", "integrity": "ok" | {"violated": [...]},
        "pipeline": {"retrieved": [...], "menu_mode": str, "parts_used": [...]},
        "note"?}
       verdict in COMPLETED | PARTIAL | ENV_ERROR | GOAL_ERROR | UNSOLVED |
       INVALIDATED
    """
    return _generate_core(prompt, out_dir=out_dir, backend=backend,
                          max_repairs=max_repairs, engine=engine,
                          use_bank=use_bank, first_user=None)


def revise_game(source, directive, out_dir="scenes/games", backend="auto",
                max_repairs=4, engine=None, use_bank=True):
    """Revise a CERTIFIED game by the SMALLEST edit that applies `directive`.

    This is generate_game's twin for the curriculum loop's *revise* mode: instead
    of designing a whole new game from a prompt, it seeds the SAME
    write->verify->repair loop with the certified `source` + a minimal-edit task
    block (``_revise_user_msg``, carrying `directive`). The model returns the FULL
    revised module, which is verified and repaired up to `max_repairs` exactly like
    a fresh generation — same oracles, same sandbox layout, same integrity check,
    same ledger telemetry, engine unchanged. The original PROMPT string is meant to
    be preserved (provenance; the task block asks for it), and the run is slugged /
    logged under that original prompt.

    Returns the same report dict as generate_game (game_path, attempts, verdict,
    backend, design, engine, integrity, pipeline, note?).
    """
    engine = _resolve_engine(engine)
    prompt = _extract_prompt(source) or "game"
    first_user = _revise_user_msg(source, directive, engine)
    return _generate_core(prompt, out_dir=out_dir, backend=backend,
                          max_repairs=max_repairs, engine=engine,
                          use_bank=use_bank, first_user=first_user)


def _generate_core(prompt, *, out_dir, backend, max_repairs, engine, use_bank,
                   first_user):
    """Shared body of generate_game / revise_game.

    ``first_user`` is the initial user message override: None -> the from-scratch
    design prompt (``generate_game``); a revise task block -> ``revise_game``. Every
    other step — sandbox layout, Tier-1b retrieval, integrity freeze, dispatch,
    finalize, pipeline telemetry, ledger record — is identical for both entries.
    """
    if backend not in ("auto", "anthropic", "openrouter", "template"):
        backend = "auto"
    engine = _resolve_engine(engine)
    os.makedirs(out_dir, exist_ok=True)

    slug = _slug(prompt)
    run_dir = os.path.join(out_dir, slug)
    os.makedirs(run_dir, exist_ok=True)

    # --- Pre-call context injection (harness-side, pinned for the run) ---------
    # Skip for the offline template backend (it ignores the system prompt) and
    # when use_bank is off.
    menu_text, menu_mode, retrieved = None, "off", []
    skills = []
    inject = use_bank and backend != "template"
    if engine == "gdscript":
        # The gdscript lane injects gd-agentic SKILL KNOWLEDGE (not the parts
        # bank): the GameAPI contract + an advisory reference-knowledge section.
        if inject:
            skills = _injected_skills(prompt)
        system = _gdscript_system_prompt(prompt) if inject else prompts.gdscript_contract()
    else:
        # Tier-1b parts retrieval. On any hiccup, degrade to legend-only so a
        # bank problem can never break generation.
        if inject:
            try:
                menu_text, menu_mode, retrieved = retrieval.retrieve_menu(prompt, engine)
            except Exception:  # noqa: BLE001 - retrieval must never break a run
                menu_text, menu_mode, retrieved = None, "legend_only", []
        system = _system_prompt(engine, menu_text)

    # Freeze the base code for the duration of the run.
    root = _repo_root()
    before = integrity.snapshot(root)

    t0 = time.time()
    result = _dispatch(prompt, run_dir, backend, max_repairs, engine, system,
                       first_user)
    wall_s = time.time() - t0
    result["engine"] = engine
    _finalize_game(run_dir, slug, result, _game_ext(engine))

    # If auto fell all the way back to templates, no injected context was
    # actually used — record the honest "off"/empty so the ledger is not misleading.
    if result.get("backend") == "template":
        menu_mode, retrieved, skills = "off", [], []

    # Pipeline telemetry: retrieved set (pinned), menu mode, the bank parts the
    # final game actually instantiated (parsed from its source), and — for the
    # gdscript lane — the gd-agentic skills injected into the prompt (so we can
    # later measure whether the injected knowledge helped).
    parts_used = _parse_parts_used(_read_source(result.get("game_path")),
                                   engine, _bank_names())
    result["pipeline"] = {"retrieved": list(retrieved), "menu_mode": menu_mode,
                          "parts_used": parts_used, "skills": list(skills)}

    # Base code must be untouched: a mutation invalidates the whole run.
    violated = integrity.violations(before, root)
    if violated:
        result["integrity"] = {"violated": violated}
        result["verdict"] = "INVALIDATED"
    else:
        result["integrity"] = "ok"

    # Telemetry: counting failures/repairs is a first-class statistic.
    try:
        from harness.core import telemetry
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


# --- Built-in v2 games, JS variant (Planck.js substrate) ----------------------
# The JS §2 module format: plain top-level `const`/`function` declarations, no
# require/import/exports, world.add takes an options object, checkpoints returns
# a plain object. Same "drift" design as the pymunk template, random-solvable by
# the seeded macro-action probe on the Node engine.
_DRIFT_JS = '''const TITLE = "Drift";
const PROMPT = "guide the puck across the ice onto the glowing pad";
const ACTIONS = ["left", "right", "up", "down"];

function build(world) {
  world.set_gravity(0.0, 0.0);
  world.add("puck", "circle", { pos: [180.0, 150.0], radius: 16.0, mass: 1.0, friction: 0.2, elasticity: 0.6 });
  world.control("puck");
  world.add("pad", "box", { pos: [560.0, 430.0], size: [200.0, 200.0], static: true, sensor: true });
  world.add("w_left", "segment", { pos: [0.0, 0.0], a: [8.0, 0.0], b: [8.0, 600.0], static: true, elasticity: 0.9 });
  world.add("w_right", "segment", { pos: [0.0, 0.0], a: [792.0, 0.0], b: [792.0, 600.0], static: true, elasticity: 0.9 });
  world.add("w_bottom", "segment", { pos: [0.0, 0.0], a: [0.0, 8.0], b: [800.0, 8.0], static: true, elasticity: 0.9 });
  world.add("w_top", "segment", { pos: [0.0, 0.0], a: [0.0, 592.0], b: [800.0, 592.0], static: true, elasticity: 0.9 });
}

function act(world, action) {
  const j = 70.0;
  if (action === "left") world.impulse("puck", [-j, 0.0]);
  else if (action === "right") world.impulse("puck", [j, 0.0]);
  else if (action === "up") world.impulse("puck", [0.0, j]);
  else if (action === "down") world.impulse("puck", [0.0, -j]);
}

function success(world) {
  const p = world.query("puck");
  const z = world.query("pad");
  const cx = (p.bbox[0] + p.bbox[2]) / 2.0;
  const cy = (p.bbox[1] + p.bbox[3]) / 2.0;
  return z.bbox[0] <= cx && cx <= z.bbox[2] && z.bbox[1] <= cy && cy <= z.bbox[3];
}

function checkpoints(world) {
  const p = world.query("puck").pos;
  const dx = p[0] - 180.0;
  const dy = p[1] - 150.0;
  return {
    moved_off_start: dx * dx + dy * dy > 1600.0,
    crossed_midline: p[0] > 400.0,
    entered_upper_half: p[1] > 300.0,
  };
}
'''

_TEMPLATE_GAMES_JS = {"drift": _DRIFT_JS}

_DESIGNS_JS = {"drift": _DESIGNS["drift"]}


# --- Built-in v2 game, Godot variant (declarative JSON spec) ------------------
# The godot lane's artifact is DATA (godotworld/SPEC.md), so the offline fixture
# is a spec string rather than code. This is the certified `traverse` example
# verbatim (a grounded-gated climb across three quarry shelves) — a known-good
# spec so the template backend and the prompt->spec->verify round-trip have a
# real, passing artifact. Do NOT grow this into a genre library.
_TRAVERSE_GODOT = '''{
  "engine": "godot",
  "spec_version": 1,
  "meta": {
    "title": "Quarry Shelves",
    "prompt": "Climb the three quarry shelves - hop over the spike strips between them and reach the beacon on the top shelf.",
    "world_size": [1400, 700],
    "actions": ["run_left", "run_right", "hop"]
  },
  "bodies": [
    {"name": "ground", "shape": "box", "pos": [700, 25], "size": [1400, 50], "static": true, "friction": 0.8},
    {"name": "ledge", "shape": "box", "pos": [430, 105], "size": [260, 30], "static": true, "friction": 0.8},
    {"name": "ledge_2", "shape": "box", "pos": [800, 185], "size": [260, 30], "static": true, "friction": 0.8},
    {"name": "ledge_3", "shape": "box", "pos": [1170, 265], "size": [260, 30], "static": true, "friction": 0.8},
    {"name": "wall", "shape": "box", "pos": [10, 350], "size": [20, 700], "static": true, "friction": 0.2},
    {"name": "wall_2", "shape": "box", "pos": [1390, 350], "size": [20, 700], "static": true, "friction": 0.2},
    {"name": "spike", "shape": "box", "pos": [615, 60], "size": [90, 24], "static": true, "sensor": true},
    {"name": "spike_2", "shape": "box", "pos": [985, 60], "size": [90, 24], "static": true, "sensor": true},
    {"name": "tree", "shape": "box", "pos": [180, 115], "size": [70, 130], "static": true, "sensor": true},
    {"name": "bush", "shape": "box", "pos": [1310, 72], "size": [60, 45], "static": true, "sensor": true},
    {"name": "goal_zone", "shape": "box", "pos": [1200, 335], "size": [130, 110], "static": true, "sensor": true},
    {"name": "marble", "shape": "circle", "pos": [80, 66], "radius": 16, "mass": 1.0, "friction": 0.6, "elasticity": 0.05, "control": true}
  ],
  "act": {
    "run_right": [{"verb": "impulse", "body": "marble", "vec": [70, 0]}],
    "run_left": [{"verb": "impulse", "body": "marble", "vec": [-70, 0]}],
    "hop": [{"verb": "impulse", "body": "marble", "vec": [0, 430], "when": "grounded(\\"marble\\")"}]
  },
  "on_step": [
    {"kind": "velocity_clamp", "body": "marble", "vx_max": 250, "vy_min": -900, "vy_max": 520}
  ],
  "predicates": {
    "success": "contacts(\\"marble\\", \\"goal_zone\\")",
    "failure": "contacts(\\"marble\\", \\"spike\\") or contacts(\\"marble\\", \\"spike_2\\")",
    "checkpoints": {
      "on_first_shelf": "pos_y(\\"marble\\") > 130 and pos_x(\\"marble\\") > 290",
      "past_spikes": "pos_x(\\"marble\\") > 680",
      "on_mid_shelf": "pos_y(\\"marble\\") > 210 and pos_x(\\"marble\\") > 660",
      "on_top_shelf": "pos_y(\\"marble\\") > 290 and pos_x(\\"marble\\") > 1030",
      "at_beacon": "contacts(\\"marble\\", \\"goal_zone\\")"
    }
  }
}
'''

_TEMPLATE_GAMES_GODOT = {"traverse": _TRAVERSE_GODOT}

_DESIGNS_GODOT = {
    "traverse": ("DESIGN\n"
                 "Theme: a marble climbing three quarry shelves to a beacon.\n"
                 "Entities: one controlled marble, three static ledges, two spike "
                 "sensors, perimeter walls, decor (tree/bush), a goal sensor.\n"
                 "Mechanic twist: grounded-gated hops - the marble may only jump "
                 "when it is resting on a surface.\n"
                 "Actions: run_left/run_right impulse the marble sideways; hop "
                 "kicks it upward, gated on grounded.\n"
                 "Milestones: on_first_shelf -> past_spikes -> on_mid_shelf -> "
                 "on_top_shelf -> at_beacon.\n"
                 "Win / Lose: win when the marble reaches the beacon zone; lose if "
                 "it touches either spike strip.\n"),
}


# --- Built-in v2 game, GDScript variant (a real .gd game class) ---------------
# The gdscript lane's artifact is CODE: a class extending the frozen host's GameBase
# and implementing the GameAPI contract (notes/engines/GDSCRIPT_LANE.md). This offline
# fixture is a topdown "arm then dock" drift - touch the far switch to arm the pad, then
# coast the puck home. A required reversal means no single held action wins. It exercises
# the whole contract (set_gravity / add_static / add_sensor / add_body / control / impulse
# / on_step clamp + flag latch / contacts / contained / checkpoints / success). Indented
# with TABS, matching godotworld/runner.gd. Do NOT grow this into a genre library, and it
# is deliberately NOT shown to the designer (the prompt is examples-free by design).
_DRIFT_GDSCRIPT = '''extends Node2D
# A duck-typed plain-Node game (no base class, no class_name): a topdown air-hockey
# puck that must drift onto BOTH glowing pads -- one to the LEFT, one to the RIGHT of
# the start -- so a single held action never wins (a reversal is required). Docking is
# a proximity latch read purely in the predicates. Deterministic: the only randomness
# is a seed-stable jitter from an rng the game seeds itself from build()'s seed.

const DOCK_R := 40.0
const IMPULSE := 150.0
const DAMP := 3.0
const MAX_V := 130.0        # px/s cap -> ~13 px/tick, bounded travel (containment + non-trivial)

var _rng := RandomNumberGenerator.new()
var _puck: RigidBody2D = null
var _pads := []             # [{name, pos, docked}]

func build(world_seed: int) -> void:
	_rng.seed = world_seed
	var jitter := _rng.randf_range(-5.0, 5.0)
	_puck = RigidBody2D.new()
	_puck.gravity_scale = 0.0                        # topdown: no "down" to fall toward
	_puck.linear_damp_mode = RigidBody2D.DAMP_MODE_REPLACE
	_puck.linear_damp = DAMP
	_puck.lock_rotation = true
	_puck.can_sleep = false
	_puck.position = Vector2(400.0, 300.0 + jitter)
	var col := CollisionShape2D.new()
	var circ := CircleShape2D.new()
	circ.radius = 16.0
	col.shape = circ
	_puck.add_child(col)
	add_child(_puck)
	_pads = []
	_add_pad("pad_left", Vector2(200.0, 300.0))      # ~200 px left of start
	_add_pad("pad_right", Vector2(620.0, 300.0))     # ~420 px right -> forces a reversal

func _add_pad(pad_name: String, pos: Vector2) -> void:
	var marker := Node2D.new()
	marker.name = pad_name
	marker.position = pos
	add_child(marker)
	_pads.append({"name": pad_name, "pos": pos, "docked": false})

func _physics_process(_delta: float) -> void:
	if _puck == null:
		return
	if _puck.linear_velocity.length() > MAX_V:
		_puck.linear_velocity = _puck.linear_velocity.limit_length(MAX_V)
	for p in _pads:
		if not p.docked and _puck.position.distance_to(p.pos) < DOCK_R:
			p.docked = true

func act(action: String) -> void:
	if _puck == null:
		return
	var v := Vector2.ZERO
	match action:
		"left": v = Vector2(-IMPULSE, 0.0)
		"right": v = Vector2(IMPULSE, 0.0)
		"up": v = Vector2(0.0, -IMPULSE)
		"down": v = Vector2(0.0, IMPULSE)
	_puck.apply_central_impulse(v)

func _count() -> int:
	var n := 0
	for p in _pads:
		if p.docked:
			n += 1
	return n

func state() -> Dictionary:
	var bodies := [{
		"name": "puck", "pos": [_puck.position.x, _puck.position.y],
		"vel": [_puck.linear_velocity.x, _puck.linear_velocity.y],
		"angle": _puck.rotation, "controlled": true, "static": false,
	}]
	for p in _pads:
		bodies.append({
			"name": p.name, "pos": [p.pos.x, p.pos.y], "vel": [0.0, 0.0],
			"angle": 0.0, "controlled": false, "static": true,
		})
	return {"bodies": bodies,
		"flags": {"one": _count() >= 1, "both": _count() >= 2}}

func checkpoints() -> Dictionary:
	return {"docked_first": _count() >= 1, "docked_both": _count() >= 2}

func is_success() -> bool:
	return _count() >= 2

func is_failure() -> bool:
	return false

func actions() -> Array:
	return ["left", "right", "up", "down"]
'''

_TEMPLATE_GAMES_GDSCRIPT = {"drift": _DRIFT_GDSCRIPT}

_DESIGNS_GDSCRIPT = {
    "drift": ("DESIGN\n"
              "Theme: a topdown air-hockey puck that must drift onto two glowing "
              "pads on opposite sides of the rink.\n"
              "Entities: one controlled puck plus two static pad markers - one to "
              "the left of the start, one to the right.\n"
              "Mechanic twist: the pads straddle the start, so a single held action "
              "reaches at most one - docking both forces a reversal.\n"
              "Actions: left/right/up/down impulse the puck across frictionless ice.\n"
              "Milestones: docked_first -> docked_both.\n"
              "Win / Lose: win when the puck has docked both pads; no lose "
              "condition.\n"),
}
