"""harness.mcp_server -- the certification funnel as an MCP server (Migration Phase 3).

Puts *certification* in the same Hermes session as *building*: one agent loop can author
a game with godot-ai's editor tools **and** iterate against our certifier's typed hints in
real time, instead of the blind "generate -> hand off -> hope" pipeline. This server is the
SECOND MCP server a Hermes session connects to (alongside godot-ai's editor server); it
exposes four tools over FastMCP streamable-http.

    1. extract_game(project_path)   -- find the GameAPI .gd script in a godot-ai project;
                                       return its source + typed contract diagnostics.
    2. verify_game(game_source)     -- run the REAL frozen funnel on the source in a
                                       throwaway sandbox; return the typed report + HINTS.
                                       The certification lane is GameAPI GDScript compiled +
                                       run inside serve_game.gd's Godot host; the python/js
                                       lanes are FROZEN LEGACY (Godot-only since 2026-07-14).
    3. capture_demo(game_source)    -- solve + (optionally) render a demo; or a state-only
                                       replay summary when rendering is unavailable.
    4. atlas_place(game_source)     -- world x play descriptors + would-be atlas cell +
                                       nearest empty frontier cells (generation-targeting).

TRUST BOUNDARY (non-negotiable -- notes/MIGRATION_GODOT_AI.md sec.9)
------------------------------------------------------------------
  * Every tool invokes the FROZEN, fingerprinted serve host OUT-OF-PROCESS. `verify_game`
    calls the harness entry `verify_game(path, sandboxed=True)` -- the SAME entry the
    repair loop uses -- which spawns a fresh subprocess per call.
  * Every call runs in a FRESH throwaway sandbox directory (mkdtemp under /tmp or scratch),
    deleted when the call returns. No tool writes into the gi-harness repo or shares
    writable state with the agent.
  * No tool exposes a knob that can weaken a gate. The gate set + host fingerprint are
    fixed server-side; the advisory `level` parameter NEVER reconfigures or skips a gate.
  * In-session verdicts are ADVISORY fast feedback. FINAL certification always re-runs
    out-of-session on a clean host from the extracted artifact, keyed by the artifact hash.
    Every payload carries an ``advisory`` note saying so.

RESOURCE BOUNDS
---------------
  * ONE godot/verify at a time per server (a global lock serialises every engine spawn).
  * Serve hosts are pinned into the 54xxx loopback port band (``GIP_PORT_BASE``), out of
    the way of the MCP servers (godot-ai :8000, this server :8010).
  * Sane subprocess budgets (the funnel's own GAMEVERIFY_TIMEOUT_S) + sandbox cleanup.

RUNTIME ENV (REQUIRED)
----------------------
The server MUST run in an env that has the harness RUNTIME deps (numpy) as well as fastmcp.
The gd verify route lazy-imports ``harness.rl.godot_env`` (numpy) inside
``GdExecutor.__init__``, so a numpy-less env IMPORTS the server fine but crashes on the
FIRST gd verify. Run under EITHER the ``godot-rl`` conda env (``module load
miniforge/24.3.0-0 && conda activate godot-rl``; carries numpy + the harness deps, add
fastmcp) OR the godot-ai uv env with ``uv run --with numpy``. NEVER the bare godot-ai uv
env (it ships fastmcp but not numpy) -- that was the PoC-3 Step-A failure. A missing dep now
surfaces as a self-diagnosing ``error_cause`` (e.g. ``ModuleNotFoundError: No module named
'numpy'``), never an opaque "verify crashed".

RUN (compute node; the godot-ai uv env ships fastmcp 3.4.4 + python 3.12)
------------------------------------------------------------------------
The funnel needs BOTH fastmcp AND the harness runtime deps. The godot-ai uv env has
fastmcp but NOT numpy, and the gd verify route lazy-imports ``harness.rl.godot_env``
(numpy) inside ``GdExecutor.__init__`` -- so the server IMPORTS fine but the first gd
verify would crash without it. Bring numpy along with ``--with numpy``:

    PYTHONPATH=<gi-harness-worktree> \
    HARNESS_GODOT_EXE=<godot-console-binary-or-apptainer-wrapper> \
    GIP_PORT_BASE=54000 \
    ~/.local/bin/uv --project /home/enaha/GI/godot-ai run --no-sync --with numpy \
        python -m harness.mcp_server --host 127.0.0.1 --port 8010

Alternatively, under the harness's NATIVE env (``module load miniforge/24.3.0-0 &&
conda activate godot-rl``), which already carries numpy + the harness runtime deps, once
``fastmcp`` is installed there (sec.9 env discipline). Either way the gd verify route needs
numpy present -- a missing dep now surfaces as a self-diagnosing ``error_cause``/hint, not
an opaque "verify crashed".
Register with Hermes as ``harness-funnel`` -> ``http://127.0.0.1:8010/mcp``.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from contextlib import contextmanager

from fastmcp import FastMCP


def _log(msg: str) -> None:
    """Authoritative named tool receipts on the server's stderr. FastMCP's low-level logger
    only prints the request *type* (CallToolRequest), so PoC verdicts / tier detection read
    THESE lines, not the framework log. (See notes/MIGRATION_GODOT_AI.md sec.5b caveat.)"""
    sys.stderr.write(f"FUNNEL | {time.strftime('%H:%M:%S')} | {msg}\n")
    sys.stderr.flush()

# --------------------------------------------------------------------------- #
# Server identity + resource bounds
# --------------------------------------------------------------------------- #
SERVER_NAME = "harness-funnel"
DEFAULT_PORT = 8010                    # distinct from godot-ai's :8000
SERVE_PORT_BASE = "54000"              # pin gd serve hosts into the 54xxx band (sec.9)

# The GameAPI 7-method contract (godotworld/serve_game.gd:69 REQUIRED_METHODS) with the
# canonical arities read off the frozen host's call sites + the mini_collect.gd fixture:
#   build(world_seed)->void  act(action)->void  state()  checkpoints()  is_success()
#   is_failure()  actions()
REQUIRED_METHODS = ("build", "act", "state", "checkpoints",
                    "is_success", "is_failure", "actions")
EXPECTED_ARITY = {"build": 1, "act": 1, "state": 0, "checkpoints": 0,
                  "is_success": 0, "is_failure": 0, "actions": 0}

ADVISORY = ("in-session verdict; final certification re-runs out-of-session on a clean, "
            "fingerprinted host from the extracted artifact.")

# ONE engine spawn at a time per server. Guards verify/capture/atlas -- all of which may
# spawn godot -- so serve-host ports never collide and load stays bounded.
_ENGINE_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# Sandbox discipline
# --------------------------------------------------------------------------- #
def _sandbox_base() -> str:
    """Root under which every per-call throwaway dir is created (``/tmp`` or scratch)."""
    base = os.environ.get("HARNESS_MCP_SANDBOX") or os.path.join(
        tempfile.gettempdir(), "harness-funnel")
    os.makedirs(base, exist_ok=True)
    return base


@contextmanager
def _sandbox(prefix: str = "call-"):
    """A fresh throwaway directory, removed on exit. NOTHING the tools write survives a
    call, and nothing is written outside this directory (trust boundary)."""
    d = tempfile.mkdtemp(prefix=prefix, dir=_sandbox_base())
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _apply_serve_port_band() -> None:
    """Keep gd serve hosts in the 54xxx band unless the operator overrode it explicitly."""
    os.environ.setdefault("GIP_PORT_BASE", SERVE_PORT_BASE)


# --------------------------------------------------------------------------- #
# Source-engine detection (mirrors harness.verify.gameverify.detect_engine markers)
# --------------------------------------------------------------------------- #
_GD_FUNC_RE = re.compile(r"(?m)^\s*func\s+\w+\s*\(")
_GD_EXTENDS_RE = re.compile(r"(?m)^\s*extends\b")
_PY_DEF_RE = re.compile(r"(?m)^\s*def\s+\w+\s*\(")


def _guess_ext(source: str) -> str:
    """Choose the on-disk extension so ``detect_engine`` routes the source correctly.
    Godot-first: GDScript ``func``/``extends`` -> ``.gd``; python ``def`` -> ``.py``."""
    s = source or ""
    if re.search(r"#\s*engine:\s*gdscript", s):
        return ".gd"
    if re.search(r"#\s*engine:\s*js", s) or re.search(r"//\s*engine:\s*js", s):
        return ".js"
    if _GD_FUNC_RE.search(s) or _GD_EXTENDS_RE.search(s):
        return ".gd"
    if _PY_DEF_RE.search(s):
        return ".py"
    return ".gd"


# --------------------------------------------------------------------------- #
# Static GameAPI contract scan (extract_game) -- no engine spawn
# --------------------------------------------------------------------------- #
def _param_count(param_str: str) -> int:
    """Count declared parameters in a ``func name(<params>)`` header, tolerating type
    hints + defaults (``world_seed: int = 0``). Empty / whitespace -> 0."""
    inner = param_str.strip()
    if not inner:
        return 0
    # Split on top-level commas (params here never nest brackets in the contract).
    return len([p for p in inner.split(",") if p.strip()])


def _scan_gd_methods(source: str) -> dict:
    """Map each required method -> {"present": bool, "arity": int|None, "arity_ok": bool}
    by a static ``func`` scan (GDScript). Used to find the compliant script + typed misses
    WITHOUT compiling or running anything (mirrors serve_game.gd's has_method 7-probe)."""
    out = {}
    for m in REQUIRED_METHODS:
        rx = re.compile(r"(?m)^\s*func\s+" + re.escape(m) + r"\s*\(([^)]*)\)")
        hit = rx.search(source or "")
        if hit is None:
            out[m] = {"present": False, "arity": None, "arity_ok": False}
        else:
            n = _param_count(hit.group(1))
            out[m] = {"present": True, "arity": n, "arity_ok": (n == EXPECTED_ARITY[m])}
    return out


def _misses(methods: dict) -> list[dict]:
    """The typed contract violations for a scanned script (missing or wrong-arity)."""
    misses = []
    for m in REQUIRED_METHODS:
        info = methods[m]
        if not info["present"]:
            misses.append({"method": m, "problem": "missing",
                           "expected_arity": EXPECTED_ARITY[m]})
        elif not info["arity_ok"]:
            misses.append({"method": m, "problem": "wrong_arity",
                           "expected_arity": EXPECTED_ARITY[m],
                           "actual_arity": info["arity"]})
    return misses


_SKIP_DIRS = {".godot", ".git", ".import", "addons"}


def _iter_project_gd(project_path: str, cap: int = 400) -> list[str]:
    """Bounded walk of a godot project for ``.gd`` scripts, skipping the editor cache,
    the git dir, and vendored ``addons`` (the godot-ai plugin is not game code). Capped so
    a stray huge tree can never blow up the scan."""
    found = []
    root = os.path.abspath(project_path)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".gd"):
                found.append(os.path.join(dirpath, fn))
                if len(found) >= cap:
                    return sorted(found)
    return sorted(found)


# --------------------------------------------------------------------------- #
# Report shaping helpers (verify_game)
# --------------------------------------------------------------------------- #
def _verdict_of(report: dict) -> str:
    """Human verdict label for a funnel report. ``CERTIFIED`` == COMPLETED (advisory)."""
    if report.get("passed"):
        return "CERTIFIED"
    fc = report.get("failure_class")
    if fc:
        return fc
    if report.get("error"):
        return "VERIFY_ERROR"
    return "UNKNOWN"


def _per_gate(report: dict) -> dict:
    """Compact per-gate pass/fail summary from ``report["layers"]``."""
    gates = {}
    for name, layer in (report.get("layers") or {}).items():
        if not isinstance(layer, dict):
            continue
        checks = layer.get("checks") or {}
        failing = [k for k, v in checks.items()
                   if isinstance(v, dict) and v.get("pass") is False]
        gates[name] = {"passed": bool(layer.get("passed")),
                       "failing_checks": failing}
    return gates


def _witness_summary(report: dict) -> dict | None:
    """Small, safe summary of the solved-witness (never the full frame trail)."""
    w = report.get("witness")
    if not isinstance(w, dict):
        return None
    plan = w.get("plan") or w.get("actions") or []
    summary = {"ticks": w.get("ticks"), "seed": w.get("seed"),
               "outcome": w.get("outcome") or ("success" if report.get("passed") else None),
               "plan_len": len(plan) if isinstance(plan, (list, tuple)) else None}
    if isinstance(plan, (list, tuple)):
        summary["plan_preview"] = list(plan[:24])
    return summary


def _directives(report: dict) -> tuple[list[dict], str]:
    """Compile the harness's typed repair directives (the anti-laundering + telemetry
    payload) from a funnel report -> (structured list, one combined instruction block).
    Best-effort: a builder that does not apply simply contributes nothing."""
    try:
        from harness.gen.feedback import (
            compile_directives, combined_directive_text,
            pressure_finding, dead_space_finding, anchoring_finding, runtime_error_finding,
        )
    except Exception:
        return [], ""
    oracle = {}
    for key, fn in (("runtime_error", runtime_error_finding),
                    ("pressure", pressure_finding),
                    ("anchoring", anchoring_finding),
                    ("dead_space", dead_space_finding)):
        try:
            finding = fn(report)
        except Exception:
            finding = None
        if finding:
            oracle[key] = finding
    if not oracle:
        return [], ""
    try:
        directives = compile_directives(oracle)
    except Exception:
        return [], ""
    structured = []
    for d in directives:
        structured.append({
            "source": getattr(d, "source", None),
            "origin": getattr(d, "origin", None),
            "checkpoint_keys": list(getattr(d, "checkpoint_keys", []) or []),
            "text": getattr(d, "text", None),
            "detail": getattr(d, "detail", None),
        })
    try:
        text = combined_directive_text(directives)
    except Exception:
        text = ""
    return structured, text


# --------------------------------------------------------------------------- #
# The core (sync) tool bodies -- run under the engine lock, off the event loop
# --------------------------------------------------------------------------- #
def _do_extract(project_path: str) -> dict:
    if not os.path.isdir(project_path):
        return {"error": f"project_path is not a directory: {project_path}",
                "game_source": None, "script_path": None, "diagnostics": {}}
    candidates = _iter_project_gd(project_path)
    scanned = []
    best = None  # (n_present, is_compliant, path, source, methods)
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            continue
        methods = _scan_gd_methods(src)
        n_present = sum(1 for m in REQUIRED_METHODS if methods[m]["present"])
        compliant = all(methods[m]["present"] and methods[m]["arity_ok"]
                        for m in REQUIRED_METHODS)
        rel = os.path.relpath(path, project_path)
        scanned.append({"script_path": path, "rel": rel,
                        "methods_present": n_present, "compliant": compliant})
        rank = (n_present, 1 if compliant else 0)
        if best is None or rank > best[0]:
            best = (rank, compliant, path, src, methods)

    if best is None:
        return {"game_source": None, "script_path": None,
                "diagnostics": {"reason": "no .gd scripts found in project",
                                "scanned": scanned, "n_scripts": 0}}

    _rank, compliant, path, src, methods = best
    diagnostics = {
        "n_scripts": len(scanned),
        "scanned": scanned,
        "chosen": path,
        "compliant": compliant,
        "methods": methods,
        "misses": _misses(methods),
        "required_methods": list(REQUIRED_METHODS),
        "expected_arity": EXPECTED_ARITY,
    }
    if compliant:
        return {"game_source": src, "script_path": path, "diagnostics": diagnostics,
                "advisory": ADVISORY}
    # No compliant script: still hand back the closest source so the agent can fix it,
    # plus the typed misses telling it exactly which methods to write/repair.
    diagnostics["reason"] = ("no fully-compliant GameAPI script; closest candidate has "
                             "unmet contract methods (see misses)")
    return {"game_source": src, "script_path": path, "diagnostics": diagnostics,
            "advisory": ADVISORY}


def _do_verify(game_source: str, level: int | None) -> dict:
    _apply_serve_port_band()
    from harness.verify.gameverify import verify_game as _verify
    ext = _guess_ext(game_source)
    with _ENGINE_LOCK:
        with _sandbox("verify-") as d:
            game_path = os.path.join(d, "game" + ext)
            with open(game_path, "w", encoding="utf-8") as fh:
                fh.write(game_source)
            report = _verify(game_path, sandboxed=True)

    if "error" in report and "layers" not in report:
        # VERIFY_ERROR shape (engine missing / timeout / crash): still typed feedback.
        return {"verdict": "VERIFY_ERROR", "passed": False,
                "failure_class": "VERIFY_ERROR", "error": report.get("error"),
                "error_cause": _error_cause(report.get("error")),
                "per_gate": {}, "hint": _verify_error_hint(report.get("error")),
                "directives": [], "directive_text": "", "warnings": [],
                "witness_summary": None, "progress": None,
                "engine": None, "level": level, "level_note": _LEVEL_NOTE,
                "advisory": ADVISORY}

    directives, directive_text = _directives(report)
    lane = _lane_of(report.get("engine"))
    return {
        "verdict": _verdict_of(report),
        "passed": bool(report.get("passed")),
        "failure_class": report.get("failure_class"),
        "engine": report.get("engine"),
        "certification_lane": lane["name"],
        "lane_note": lane["note"],
        "legacy_lane": lane["legacy"],
        "per_gate": _per_gate(report),
        "hint": report.get("hint") or "",
        "directives": directives,
        "directive_text": directive_text,
        "warnings": list(report.get("warnings") or []),
        "witness_summary": _witness_summary(report),
        "progress": report.get("progress"),
        "level": level,
        "level_note": _LEVEL_NOTE,
        "advisory": ADVISORY,
    }


# The certification lane. The GDScript GameAPI lane -- source compiled + run inside the
# FROZEN serve_game.gd Godot host -- is the ONLY lane that certifies for real. The
# python/js lanes are FROZEN LEGACY (gi-harness went Godot-only 2026-07-14); the funnel
# still routes them for back-compat but they are NOT a certification path.
def _lane_of(engine) -> dict:
    if engine == "gdscript":
        return {"name": "gdscript-godot", "legacy": False,
                "note": ("GDScript GameAPI compiled + run inside the frozen serve_game.gd "
                         "Godot host -- the real certification lane.")}
    if engine in ("godot",):
        return {"name": "godot-spec", "legacy": False,
                "note": "declarative Godot spec on the frozen runner.gd host."}
    if engine in ("py", "js", None):
        return {"name": (engine or "py") + "-legacy", "legacy": True,
                "note": ("FROZEN LEGACY lane (gi-harness is Godot-only since 2026-07-14); "
                         "routed for back-compat / plumbing only, NOT a certification path. "
                         "Ship GameAPI GDScript for a real verdict.")}
    return {"name": str(engine), "legacy": False, "note": ""}


_LEVEL_NOTE = ("`level` is advisory metadata only: the frozen funnel always runs its full "
              "G0-G3 schedule; no parameter can weaken, skip, or reconfigure a gate.")


def _error_cause(error) -> str | None:
    """The single most informative line of a VERIFY_ERROR: the final traceback line (the
    real exception), else the message. Surfacing this turns an opaque 'verify crashed' into
    a self-diagnosing failure (e.g. a missing runtime dep in the server's env)."""
    if not isinstance(error, dict):
        return None
    tb = error.get("traceback")
    if tb:
        lines = [ln for ln in str(tb).splitlines() if ln.strip()]
        if lines:
            return lines[-1].strip()
    return error.get("message")


def _verify_error_hint(error) -> str:
    if isinstance(error, dict):
        t = error.get("type")
        cause = _error_cause(error)
        if t == "timeout":
            return "verify timed out -- the game may loop or be too slow to solve."
        if t in ("crash", "exception"):
            # A ModuleNotFoundError here is a SERVER-ENV problem (the funnel needs the
            # harness runtime deps, e.g. numpy), NOT a bug in the game -- name it so.
            if cause and "ModuleNotFoundError" in cause:
                return (f"verify could not run: {cause}. The funnel server's env is missing "
                        "a harness runtime dependency -- start it with numpy available "
                        "(see harness.mcp_server run instructions). Not a game error.")
            if cause:
                return f"verify crashed before producing a report: {cause}"
            return "verify crashed before producing a report; check the script for a build/act error."
        if cause:
            return f"verify environment error: {str(cause)[:200]}"
    return "verify could not run (engine unavailable or environment error)."


def _do_capture(game_source: str, actions: list | None) -> dict:
    """Solve the game (funnel witness) and, if rendering is available, capture a GIF;
    otherwise return a state-only replay summary. Rendering needs a Godot binary + an X
    display (``HARNESS_GODOT_EXE`` + ``DISPLAY``/Xvfb); absent either, we stay state-only."""
    _apply_serve_port_band()
    from harness.verify.gameverify import verify_game as _verify
    ext = _guess_ext(game_source)

    with _ENGINE_LOCK:
        with _sandbox("capture-") as d:
            game_path = os.path.join(d, "game" + ext)
            with open(game_path, "w", encoding="utf-8") as fh:
                fh.write(game_source)
            # 1) Always establish outcome/ticks via the funnel witness (state channel).
            report = _verify(game_path, sandboxed=True)
            witness = _witness_summary(report)
            plan = actions
            if plan is None and isinstance(report.get("witness"), dict):
                plan = report["witness"].get("plan") or report["witness"].get("actions")

            can_render = (ext == ".gd" and bool(os.environ.get("DISPLAY"))
                          and _godot_available())
            if not can_render or not report.get("passed") or not plan:
                return {
                    "mode": "state_only",
                    "outcome": (witness or {}).get("outcome")
                    or ("success" if report.get("passed") else _verdict_of(report)),
                    "ticks": (witness or {}).get("ticks"),
                    "gif_path": None,
                    "state_summary": witness,
                    "render_skipped_reason": _render_skip_reason(ext, report, plan),
                    "advisory": ADVISORY,
                }
            # 2) Rendering available + a solved plan -> capture a GIF.
            out_gif = os.path.join(d, "demo.gif")
            try:
                from harness.verify.capture import capture_gif
                res = capture_gif(game_path, out_gif, actions=plan)
            except Exception as exc:  # noqa: BLE001
                return {
                    "mode": "state_only",
                    "outcome": (witness or {}).get("outcome") or "success",
                    "ticks": (witness or {}).get("ticks"),
                    "gif_path": None,
                    "state_summary": witness,
                    "render_skipped_reason": f"capture failed: {str(exc)[:180]}",
                    "advisory": ADVISORY,
                }
            # Persist the GIF outside the (about-to-be-deleted) sandbox.
            persist = _persist_artifact(out_gif, suffix=".gif")
            return {
                "mode": "rendered",
                "outcome": res.get("result"),
                "ticks": res.get("ticks"),
                "n_frames": res.get("n_frames"),
                "gif_path": persist,
                "state_summary": witness,
                "advisory": ADVISORY,
            }


def _render_skip_reason(ext: str, report: dict, plan) -> str:
    if ext != ".gd":
        return "rendering supported for GDScript games only; returned state-only summary."
    if not report.get("passed"):
        return "game not solved by the funnel; nothing to render -- returned state-only summary."
    if not plan:
        return "no winning action plan available; returned state-only summary."
    if not os.environ.get("DISPLAY"):
        return "no X display (DISPLAY unset / no Xvfb); returned state-only summary."
    if not _godot_available():
        return "Godot binary not found (set HARNESS_GODOT_EXE); returned state-only summary."
    return "returned state-only summary."


def _godot_available() -> bool:
    try:
        from harness.verify.godot_exec import find_godot_exe
        return bool(find_godot_exe())
    except Exception:
        return False


def _persist_artifact(path: str, suffix: str = "") -> str | None:
    """Copy a sandbox artifact into a durable per-server dir so its path survives the
    sandbox cleanup. Still outside the gi-harness repo (trust boundary)."""
    if not os.path.isfile(path):
        return None
    outdir = os.environ.get("HARNESS_MCP_ARTIFACTS") or os.path.join(
        _sandbox_base(), "artifacts")
    os.makedirs(outdir, exist_ok=True)
    dest = tempfile.mkstemp(prefix="demo-", suffix=suffix, dir=outdir)[1]
    shutil.copyfile(path, dest)
    return dest


def _do_atlas(game_source: str) -> dict:
    _apply_serve_port_band()
    from harness.verify.gameverify import verify_game as _verify
    from harness.atlas.build import fetch_facts
    from harness.atlas.descriptors import describe_game, DESCRIPTOR_KEYS
    ext = _guess_ext(game_source)

    with _ENGINE_LOCK:
        with _sandbox("atlas-") as d:
            game_path = os.path.join(d, "game" + ext)
            with open(game_path, "w", encoding="utf-8") as fh:
                fh.write(game_source)
            report = _verify(game_path, sandboxed=True)
            facts = fetch_facts(game_path, source=game_source)
            row = describe_game(game_path, report, {"facts": facts})

    certified = bool(report.get("passed"))
    placement = _place_in_library(row)
    out = {
        "descriptors": row,
        "descriptor_keys": list(DESCRIPTOR_KEYS),
        "certified": certified,
        "verdict": _verdict_of(report),
        "advisory": ("atlas placement is a generation-targeting SIGNAL only; it confers no "
                     "certification authority. " + ADVISORY),
    }
    out.update(placement)
    return out


def _place_in_library(row: dict) -> dict:
    """Locate the candidate's would-be cell + nearest empty frontier cells RELATIVE TO the
    existing atlas library (``HARNESS_ATLAS_JSONL`` -> an ``atlas.jsonl`` of descriptor
    rows). Degrades gracefully to descriptors-only when no library is configured."""
    jsonl = os.environ.get("HARNESS_ATLAS_JSONL")
    if not jsonl or not os.path.isfile(jsonl):
        return {"would_be_cell": None, "nearest_empty_cells": [], "axes": None,
                "placement_note": ("no atlas library configured (set HARNESS_ATLAS_JSONL "
                                   "to an atlas.jsonl); returned descriptors only.")}
    try:
        import json
        rows = []
        with open(jsonl, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                rows.append(obj if "descriptors" in obj else {"descriptors": obj})
        from harness.atlas.render import select_axes, compute_grid, _bin_index
        from harness.atlas.composites import AxisSpace
        cand = {"descriptors": row}
        all_rows = rows + [cand]
        x_key, y_key, size_key, _scores = select_axes(all_rows)
        grid = compute_grid(all_rows, x_key, y_key)
        space = AxisSpace(all_rows)
        i_cand = len(all_rows) - 1
        xv = space.value(i_cand, x_key)
        yv = space.value(i_cand, y_key)
        n_bins = grid.get("n_bins", 6)
        xb = grid.get("xbounds") or [0, 1]
        yb = grid.get("ybounds") or [0, 1]
        ix = _bin_index(xv, xb[0], xb[1], n_bins) if xv is not None else None
        iy = _bin_index(yv, yb[0], yb[1], n_bins) if yv is not None else None
        empty = list(grid.get("empty_cells") or [])
        if ix is not None and iy is not None:
            empty = sorted(empty, key=lambda c: max(abs(c.get("ix", 0) - ix),
                                                     abs(c.get("iy", 0) - iy)))
        return {
            "would_be_cell": ({"ix": ix, "iy": iy, "x_value": xv, "y_value": yv}
                              if ix is not None and iy is not None else None),
            "nearest_empty_cells": empty[:5],
            "axes": {"x": x_key, "y": y_key, "size": size_key,
                     "xbounds": xb, "ybounds": yb, "n_bins": n_bins},
            "placement_note": f"placed against {len(rows)} library rows.",
        }
    except Exception as exc:  # noqa: BLE001
        return {"would_be_cell": None, "nearest_empty_cells": [], "axes": None,
                "placement_note": f"placement unavailable: {str(exc)[:180]}"}


# --------------------------------------------------------------------------- #
# FastMCP tool surface (async wrappers -> blocking bodies off the event loop)
# --------------------------------------------------------------------------- #
mcp = FastMCP(
    SERVER_NAME,
    instructions=(
        "The gi-harness certification funnel. Build a game with the godot-ai editor "
        "tools, then call verify_game on the script's source and fix what it reports "
        "until the verdict improves. Verdicts here are ADVISORY fast feedback; final "
        "certification re-runs out-of-session."),
)


async def _run(fn, *args):
    import anyio
    return await anyio.to_thread.run_sync(fn, *args)


@mcp.tool
async def extract_game(project_path: str) -> dict:
    """Locate the GameAPI GDScript in a godot-ai-built editor project and return its source.

    Statically scans the project's ``.gd`` scripts (skipping the editor cache + the
    godot-ai plugin) for the single script implementing the 7-method GameAPI contract
    (build/act/state/checkpoints/is_success/is_failure/actions). Returns the chosen
    ``game_source`` + ``script_path`` + ``diagnostics``. If NO compliant script exists,
    ``diagnostics.misses`` lists exactly which methods are missing or have the wrong arity
    so you know what to write. Read-only; spawns no engine.

    Args:
        project_path: absolute path to the godot project root (the dir with project.godot).
    """
    _log(f"extract_game(project_path={project_path!r})")
    out = await _run(_do_extract, project_path)
    diag = out.get("diagnostics", {})
    _log(f"extract_game -> compliant={diag.get('compliant')} "
         f"script={out.get('script_path')} misses={len(diag.get('misses', []))}")
    return out


@mcp.tool
async def verify_game(game_source: str, level: int | None = None) -> dict:
    """Run the REAL certification funnel on a GameAPI GDScript and return the typed report.

    Ship GameAPI **GDScript**: the source is compiled + run inside the frozen, fingerprinted
    ``serve_game.gd`` Godot host (engine ``"gdscript"``) -- the ONLY lane that certifies for
    real. (The python/js lanes are FROZEN LEGACY since gi-harness went Godot-only on
    2026-07-14; the funnel still routes them for back-compat, flagged ``legacy_lane: true``,
    but they are not a certification path.)

    Writes the source into a throwaway sandbox and runs the host out-of-process (the same
    ``verify_game`` entry the repair loop uses). Returns the verdict, per-gate results, the
    funnel ``hint``, and the typed repair ``directives`` (the anti-laundering + telemetry
    payload) -- fix what it reports and re-call to watch the verdict improve. Verdict
    ``CERTIFIED`` == a passing (COMPLETED) run through the Godot host. G0 runs the harness's
    own sandbox scanner (rejects ``OS.*``/``FileAccess``/``ResourceSaver``/network etc.)
    before anything is compiled; ``load``/``preload`` of ``res://`` resources are ALLOWED,
    and the unseeded ``randomize``/``randi`` family is ADVISORY-only (surfaced in
    ``warnings`` -- the two-run replay gate still fails real drift).

    Args:
        game_source: the full GameAPI GDScript implementing the 7 methods (build/act/state/
                     checkpoints/is_success/is_failure/actions).
        level: advisory metadata only -- it CANNOT weaken, skip, or reconfigure any gate;
               the funnel always runs its full G0-G3 schedule.
    """
    _log(f"verify_game(len={len(game_source or '')}, level={level})")
    out = await _run(_do_verify, game_source, level)
    _log(f"verify_game -> verdict={out.get('verdict')} engine={out.get('engine')} "
         f"lane={out.get('certification_lane')} hint={ (out.get('hint') or '')[:100]!r}")
    return out


@mcp.tool
async def capture_demo(game_source: str, actions: list | None = None) -> dict:
    """Capture a quick demo of the game: a rendered GIF when a display is available, else
    a state-only replay summary.

    Solves the game via the funnel to get the outcome + tick count (state channel). If a
    Godot binary and an X display are present it also renders a GIF and returns its path;
    otherwise it returns the state-only summary (outcome + ticks). Cosmetic lane -- never
    gates certification.

    Args:
        game_source: the full game source.
        actions: optional explicit action plan; when omitted the funnel's winning plan is used.
    """
    _log(f"capture_demo(len={len(game_source or '')}, actions={'given' if actions else 'auto'})")
    out = await _run(_do_capture, game_source, actions)
    _log(f"capture_demo -> mode={out.get('mode')} outcome={out.get('outcome')} "
         f"ticks={out.get('ticks')} gif={out.get('gif_path')}")
    return out


@mcp.tool
async def atlas_place(game_source: str) -> dict:
    """Compute the candidate's world x play descriptors + its would-be atlas cell + nearest
    empty frontier cells (the generation-targeting signal).

    Runs the cheap facts channel + funnel to derive the descriptor row, then -- if an atlas
    library is configured (``HARNESS_ATLAS_JSONL``) -- bins the candidate onto the world x
    play grid and returns its cell plus the nearest empty cells to aim generation at.
    Read-mostly aggregation; confers no certification authority.

    Args:
        game_source: the full game source.
    """
    _log(f"atlas_place(len={len(game_source or '')})")
    out = await _run(_do_atlas, game_source)
    _log(f"atlas_place -> certified={out.get('certified')} cell={out.get('would_be_cell')}")
    return out


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="gi-harness funnel MCP server (Phase 3)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--transport", default="http",
                    help="fastmcp transport (http|streamable-http|stdio|sse)")
    args = ap.parse_args(argv)
    _apply_serve_port_band()
    if args.transport in ("http", "streamable-http", "sse"):
        mcp.run(transport=args.transport, host=args.host, port=args.port)
    else:
        mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
