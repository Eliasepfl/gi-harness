"""``designer_write`` — the SOLE write capability of the designer cage (§4 P0).

DESIGNER_AGENT_PLAN.md §4, enforcement layer (1): one write tool, no general
fs/bash write. Every call is realpath-resolved and classified into three tiers:

* **Tier A — DIRECT:** ``designer/skills/*.md`` (direct), ``designer/memory/*.md``
  (append-only), ``designer/proposals/**``, ``designer/workspace/<run_id>/**``.
* **Tier B — PROPOSAL only:** the ``harness/gen/prompts/*`` sections + the bank
  catalog + ``designer/SYSTEM.md`` — the live file is NEVER touched; the write is
  redirected into ``designer/proposals/<wave>/`` as a staged packet.
* **Tier C — HARD REJECT:** everything else (default-deny), which by the plan's
  mechanical v0 rule covers every ``*.py``, ``CONTRACTS.md``/``BUDGETS.md``,
  CI/hooks, and this cage itself.

Path guards reject ``..`` traversal, symlinks (target or any ancestor), and any
path resolving outside the repo. Size/line caps come from ``designer/BUDGETS.md``
(§4). Every call — accepted AND rejected, with the reason — is appended as one
JSON line to ``designer/ledger/designer.jsonl``.

Global kill-switch: this module is import-flag-gated by the ``DESIGNER_WRITE_ENABLED``
env var. When it is off, ``designer_write`` raises ``DesignerWriteDisabled`` and
NOTHING is written (not even the ledger) — there is no write path at all.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re

from harness.designer import budgets as _budgets

# --------------------------------------------------------------------------- #
# Kill-switch (§4 global kill-switch). Snapshotted at import for inspection;
# _write_enabled() re-reads the env live so the flag is the effective gate.
# --------------------------------------------------------------------------- #
_TRUTHY = {"1", "true", "yes", "on"}
DESIGNER_WRITE_ENABLED = os.environ.get("DESIGNER_WRITE_ENABLED", "").strip().lower() in _TRUTHY


def _write_enabled() -> bool:
    return os.environ.get("DESIGNER_WRITE_ENABLED", "").strip().lower() in _TRUTHY


class DesignerWriteDisabled(RuntimeError):
    """Raised by ``designer_write`` when ``DESIGNER_WRITE_ENABLED`` is off."""


# --------------------------------------------------------------------------- #
# Tier allowlists (compiled once). Patterns are matched against the repo-relative
# POSIX path AFTER realpath resolution + containment.
# --------------------------------------------------------------------------- #
_A_SKILL = re.compile(r"^designer/skills/[^/]+\.md$")
_A_MEMORY = re.compile(r"^designer/memory/[^/]+\.md$")
_A_PROPOSAL = re.compile(r"^designer/proposals/.+")
_A_WORKSPACE = re.compile(r"^designer/workspace/[^/]+/.+")

# Tier B: the exact live surfaces that may only be PROPOSED (redirected).
_B_PATHS = frozenset({
    "harness/gen/prompts/rules.md",
    "harness/gen/prompts/orientation.md",
    "harness/gen/prompts/api_godot.md",
    "harness/gen/prompts/api_py.md",
    "harness/gen/prompts/api_js.md",
    "harness/gen/prompts/design_block.md",
    "harness/gen/prompts/contract.md",
    "harness/gen/prompts/bank_menu.md.tmpl",
    "banks/parts/v1/parts.json",
    "designer/SYSTEM.md",
})


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def ledger_path(root: str | None = None) -> str:
    """Ledger path: explicit ``root`` > ``DESIGNER_LEDGER`` env > repo default."""
    env = os.environ.get("DESIGNER_LEDGER")
    if env:
        return env
    root = root or _repo_root()
    return os.path.join(root, "designer", "ledger", "designer.jsonl")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# Path safety
# --------------------------------------------------------------------------- #
def _has_symlink_ancestor(cand: str) -> bool:
    """True if the target or any ancestor is a symlink (islink is False for a
    not-yet-created tail, so a normal new file under a real dir is fine)."""
    p = cand
    while True:
        if os.path.islink(p):
            return True
        parent = os.path.dirname(p)
        if parent == p:
            return False
        p = parent


def _resolve(path: str, root: str) -> tuple[str | None, str]:
    """Realpath-resolve + containment. Returns (repo_relative_posix | None, reason).

    reason is "" on success, else the rejection cause.
    """
    if path is None or path == "":
        return None, "empty path"
    # Literal traversal in the requested path is rejected before any resolution.
    parts = re.split(r"[\\/]+", path)
    if ".." in parts:
        return None, "path-traversal (`..` component)"

    cand = path if os.path.isabs(path) else os.path.join(root, path)
    if _has_symlink_ancestor(cand):
        return None, "symlink in path (target or ancestor)"

    real = os.path.realpath(cand)
    root_real = os.path.realpath(root)
    if real != root_real and not real.startswith(root_real + os.sep):
        return None, "resolves outside the repo"

    rel = os.path.relpath(real, root_real).replace(os.sep, "/")
    if rel == ".":
        return None, "cannot write the repo root itself"
    return rel, ""


# --------------------------------------------------------------------------- #
# Tier classification + cap enforcement
# --------------------------------------------------------------------------- #
def classify(rel: str) -> str:
    """Tier of a repo-relative path: 'A', 'B', or 'C' (default-deny)."""
    if _A_SKILL.match(rel) or _A_MEMORY.match(rel) or \
            _A_PROPOSAL.match(rel) or _A_WORKSPACE.match(rel):
        return "A"
    if rel in _B_PATHS:
        return "B"
    return "C"


def _tier_c_reason(rel: str) -> str:
    if rel.endswith(".py"):
        return "Tier-C: harness code (*.py) is never directly writable"
    if rel in ("CONTRACTS.md", "OBJECTIVES.md", "SPEC_VERIFIER.md"):
        return "Tier-C: frozen contract/spec doc"
    if rel == "designer/BUDGETS.md":
        return "Tier-C: BUDGETS.md is human-authored"
    if rel.startswith("scripts/hooks/") or rel.startswith(".github/"):
        return "Tier-C: CI/hook file"
    if rel.startswith("harness/designer/"):
        return "Tier-C: the cage cannot rewrite itself"
    return "Tier-C: not in the Tier-A/Tier-B allowlist (default-deny)"


def _check_caps(rel: str, content: str, mode: str, root: str,
                caps: dict[str, int]) -> str:
    """Return "" if within budget, else the cap-violation reason. Tier-A only."""
    lines = _budgets.count_lines(content)
    if _A_SKILL.match(rel):
        if os.path.basename(rel) == "INDEX.md":
            return ""  # the router index is exempt from the per-skill caps
        if lines > caps["skill_max_lines"]:
            return f"skill exceeds {caps['skill_max_lines']} lines ({lines})"
        toks = _budgets.estimate_tokens(content)
        if toks > caps["skill_max_tokens"]:
            return f"skill exceeds ~{caps['skill_max_tokens']} tokens (~{toks})"
        target = os.path.join(root, rel)
        if not os.path.exists(target):
            active = _active_skill_count(root)
            if active >= caps["skills_max_active"]:
                return (f"skills library full: {active} active "
                        f">= cap {caps['skills_max_active']} (evict one first)")
    elif _A_MEMORY.match(rel):
        if mode != "a":
            return "memory is append-only (mode must be 'a'); rewrite rejected (ACE)"
    return ""


def _active_skill_count(root: str) -> int:
    """Count active skill files (excluding the INDEX router)."""
    skills_dir = os.path.join(root, "designer", "skills")
    try:
        return sum(1 for n in os.listdir(skills_dir)
                   if n.endswith(".md") and n != "INDEX.md")
    except OSError:
        return 0


# --------------------------------------------------------------------------- #
# Ledger
# --------------------------------------------------------------------------- #
def _log(record: dict, root: str) -> None:
    path = ledger_path(root)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


# --------------------------------------------------------------------------- #
# The one write tool
# --------------------------------------------------------------------------- #
def designer_write(path: str, content: str, mode: str = "w", *,
                   wave: str | None = None, run_id: str | None = None,
                   root: str | None = None) -> dict:
    """Write ``content`` to ``path`` iff the cage permits it; log every attempt.

    ``mode`` is "w" (overwrite) or "a" (append). Tier-A paths are written
    directly (memory only in append mode). Tier-B paths are REDIRECTED into
    ``designer/proposals/<wave>/`` — the live file is never touched. Everything
    else is a Tier-C hard-reject. ``..``/symlink/out-of-repo paths and
    budget-overruns are rejected. Returns the ledger record; raises
    ``DesignerWriteDisabled`` when ``DESIGNER_WRITE_ENABLED`` is off (no write of
    any kind, including the ledger).
    """
    if not _write_enabled():
        raise DesignerWriteDisabled(
            "designer_write is disabled: set DESIGNER_WRITE_ENABLED=1 to enable "
            "the (audited) write path")

    root = os.path.abspath(root or _repo_root())
    wave = wave or os.environ.get("DESIGNER_WAVE") or "wave-0"
    run_id = run_id or os.environ.get("DESIGNER_RUN_ID")
    if mode not in ("w", "a"):
        mode = "w"

    content = "" if content is None else str(content)
    record = {
        "ts": _now(),
        "requested_path": path,
        "path": None,
        "tier": None,
        "mode": mode,
        "accepted": False,
        "reason": "",
        "bytes": len(content.encode("utf-8")),
        "lines": _budgets.count_lines(content),
        "wave": wave,
        "run_id": run_id,
    }

    rel, reason = _resolve(path, root)
    if rel is None:
        record["reason"] = reason
        _log(record, root)
        return record

    tier = classify(rel)
    record["tier"] = tier

    if tier == "C":
        record["reason"] = _tier_c_reason(rel)
        _log(record, root)
        return record

    caps = _budgets.load_budgets(root)

    if tier == "B":
        # Redirect into the proposals staging area; re-validate the target as a
        # Tier-A proposal path (defence in depth) and cap it like a prompt section.
        target_rel = f"designer/proposals/{wave}/{os.path.basename(rel)}"
        cap_reason = ""
        plines = _budgets.count_lines(content)
        if plines > caps["prompt_max_lines"]:
            cap_reason = (f"proposal exceeds {caps['prompt_max_lines']} lines "
                          f"({plines})")
        if cap_reason:
            record["reason"] = cap_reason
            _log(record, root)
            return record
        _write_file(root, target_rel, content, "w")
        record["path"] = target_rel
        record["accepted"] = True
        record["reason"] = f"Tier-B: staged as proposal (live {rel} untouched)"
        _log(record, root)
        return record

    # tier == "A"
    cap_reason = _check_caps(rel, content, mode, root, caps)
    if cap_reason:
        record["reason"] = cap_reason
        _log(record, root)
        return record

    _write_file(root, rel, content, mode)
    record["path"] = rel
    record["accepted"] = True
    record["reason"] = "Tier-A: written"
    _log(record, root)
    return record


def _write_file(root: str, rel: str, content: str, mode: str) -> None:
    target = os.path.join(root, rel)
    directory = os.path.dirname(target)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(target, mode, encoding="utf-8") as fh:
        fh.write(content)
