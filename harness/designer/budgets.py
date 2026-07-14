"""Numeric caps for the designer cage, parsed from ``designer/BUDGETS.md``.

``BUDGETS.md`` is human-authored (Tier-C): its fenced ```budgets``` block is the
machine-readable source of truth. This module parses that block and overlays it
on ``DEFAULTS`` (which mirror DESIGNER_AGENT_PLAN.md §4). A missing file or a
missing key silently falls back to the default, so the code is robust even if
the note is absent — but the block and the defaults are kept in agreement.

Only ``key = <int>`` lines *inside* the ```budgets``` fence are honoured; prose
elsewhere in the file (which happens to contain numbers) is ignored.
"""
from __future__ import annotations

import math
import os
import re

# Mirror of the DESIGNER_AGENT_PLAN.md §4 caps. Kept in sync with
# designer/BUDGETS.md's fenced block; the file wins when it disagrees.
DEFAULTS: dict[str, int] = {
    "skill_max_lines": 200,
    "skill_max_tokens": 1500,
    "skills_max_active": 25,
    "skills_total_tokens": 5000,
    "prompt_max_lines": 120,
    "memory_delta_max_lines": 5,
    "proposals_per_wave": 3,
    "proposal_max_sections": 2,
    "proposal_section_max_added_lines": 15,
    "wave_net_max_lines": 40,
    "repair_iters_per_proposal": 2,
    "waves_per_day": 1,
}

_KV = re.compile(r"^\s*([a-z_]+)\s*=\s*(\d+)\s*$")
_FENCE_OPEN = re.compile(r"^\s*```+\s*budgets\s*$")
_FENCE_CLOSE = re.compile(r"^\s*```+\s*$")


def _repo_root() -> str:
    """Repo root = grandparent of this module's package dir (harness/designer/)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def budgets_path(root: str | None = None) -> str:
    """Path to BUDGETS.md: explicit ``root`` > ``DESIGNER_BUDGETS`` env > repo."""
    env = os.environ.get("DESIGNER_BUDGETS")
    if env:
        return env
    root = root or _repo_root()
    return os.path.join(root, "designer", "BUDGETS.md")


def _parse_block(text: str) -> dict[str, int]:
    """Extract ``key = int`` pairs from the first ```budgets``` fenced block."""
    out: dict[str, int] = {}
    in_block = False
    for line in text.splitlines():
        if not in_block:
            if _FENCE_OPEN.match(line):
                in_block = True
            continue
        if _FENCE_CLOSE.match(line):
            break
        m = _KV.match(line)
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


def load_budgets(root: str | None = None) -> dict[str, int]:
    """Return the effective caps: DEFAULTS overlaid with BUDGETS.md's block."""
    caps = dict(DEFAULTS)
    try:
        with open(budgets_path(root), "r", encoding="utf-8") as fh:
            caps.update(_parse_block(fh.read()))
    except OSError:
        pass  # no file -> plan defaults
    return caps


def estimate_tokens(content: str) -> int:
    """Cheap, deterministic token estimate (~4 chars/token, the GPT rule of thumb).

    Used only for cap enforcement, so a conservative over-count is fine: it errs
    toward rejecting a borderline-oversize artifact.
    """
    return math.ceil(len(content) / 4)


def count_lines(content: str) -> int:
    """Line count as authors see it (a trailing newline is not a blank line)."""
    return len(content.splitlines())
