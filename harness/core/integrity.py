"""Run-integrity manifest: freeze the harness base code during a generation run.

OBJECTIVES hard rule: the base code is FROZEN while a game is being generated.
Repairs may only touch the GENERATED game (its own sandbox dir). Any change to a
tracked base file mid-run invalidates that run.

This module provides a tiny content manifest over the tracked base files:

    tracked base = harness/**/*.py + CONTRACTS.md
                 + the designer prompt seed (designer/SYSTEM.md,
                   designer/skills/*.md, designer/memory/*.md)
                 + the parts-bank CONTENT HASH (banks/parts/<v>/parts.json)
    (scenes/, tests/, env.py and any bytecode caches are deliberately EXCLUDED)

`snapshot(root)` returns {relative_path: sha256}. `violations(before, root)`
re-snapshots and lists every base file that changed, was added, or was removed
since `before`. `gamegen.generate_game` snapshots before a run and checks after;
a non-empty result forces the run's verdict to INVALIDATED.

Two designer-cage extensions (DESIGNER_AGENT_PLAN.md §4 P0):

* The designer's in-context prompt seed — ``designer/SYSTEM.md`` plus its
  agent-grown ``skills/*.md`` and ``memory/*.md`` — is tracked base too, so a
  mid-run skill/memory mutation INVALIDATES a run exactly like a code edit.
* The parts bank is DATA under ``banks/`` (outside the ``harness/**`` walk), so a
  mid-run bank swap used to be invisible. ``snapshot`` now folds
  ``bank.content_hash`` (the canonical-JSON hash, stable across reformatting)
  under a synthetic ``bank:<version>`` key, closing that gap.
"""

from __future__ import annotations

import hashlib
import os

# Directory / file names that are never part of the tracked base manifest.
_SKIP_DIRS = {"__pycache__"}
_CONTRACTS = "CONTRACTS.md"
_HARNESS = "harness"
# The generator's prompt SECTION FILES are base content too: a mid-run prompt
# edit must invalidate a run exactly like a code edit. They live only here.
_PROMPTS_DIR = os.path.join(_HARNESS, "gen", "prompts")
# The designer cage's human-seeded prompt + agent-grown skills/memory (§4). The
# bare SYSTEM.md file plus every markdown skill and memory file are tracked so a
# mid-run designer self-mod invalidates the run.
_DESIGNER = "designer"
_DESIGNER_SYSTEM = os.path.join(_DESIGNER, "SYSTEM.md")
_DESIGNER_MD_DIRS = (os.path.join(_DESIGNER, "skills"),
                     os.path.join(_DESIGNER, "memory"))
# Where the versioned parts-bank catalogs live (folded in as content hashes).
_BANK_PARTS = os.path.join("banks", "parts")
_BANK_CATALOG = "parts.json"


def _sha256(path: str) -> str:
    """Streaming SHA-256 of a file's raw bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(path: str, root: str) -> str:
    """Root-relative POSIX-style key (stable across OSes)."""
    return os.path.relpath(path, root).replace(os.sep, "/")


def tracked_files(root: str = ".") -> list[str]:
    """Absolute paths of every tracked base file under `root`.

    Tracked = every ``*.py`` under ``harness/`` (recursively, skipping bytecode
    caches) plus ``CONTRACTS.md`` at the root plus the designer prompt seed
    (``designer/SYSTEM.md`` and every ``*.md`` under ``designer/skills`` and
    ``designer/memory``). ``scenes/``, ``tests/`` and ``env.py`` are intentionally
    out of scope: generated games and tests live there, and secrets must never be
    hashed or surfaced. The parts-bank hash is added by ``snapshot`` (it is a
    content hash, not a file-byte hash), not here.
    """
    root = os.path.abspath(root)
    out: list[str] = []

    harness_dir = os.path.join(root, _HARNESS)
    prompts_dir = os.path.join(root, _PROMPTS_DIR)
    if os.path.isdir(harness_dir):
        for dirpath, dirnames, filenames in os.walk(harness_dir):
            # Prune bytecode/cache dirs in place so os.walk does not descend.
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            # In harness/gen/prompts/ the prompt section files (*.md, *.md.tmpl)
            # are tracked base content alongside every *.py.
            in_prompts = os.path.abspath(dirpath) == os.path.abspath(prompts_dir)
            for name in filenames:
                if name.endswith(".py") or (
                        in_prompts and (name.endswith(".md")
                                        or name.endswith(".md.tmpl"))):
                    out.append(os.path.join(dirpath, name))

    contracts = os.path.join(root, _CONTRACTS)
    if os.path.isfile(contracts):
        out.append(contracts)

    # Designer prompt seed: the bare SYSTEM.md, plus every *.md skill/memory file
    # (one directory deep — support dirs are gated separately by the write tool).
    system = os.path.join(root, _DESIGNER_SYSTEM)
    if os.path.isfile(system):
        out.append(system)
    for reldir in _DESIGNER_MD_DIRS:
        d = os.path.join(root, reldir)
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                if name.endswith(".md"):
                    p = os.path.join(d, name)
                    if os.path.isfile(p):
                        out.append(p)

    return sorted(out)


def _bank_hashes(root: str) -> dict[str, str]:
    """Synthetic ``bank:<version>`` -> content_hash entries for every catalog.

    Uses ``bank.content_hash`` (the canonical-JSON hash) rather than a file-byte
    hash, so a cosmetic reformat of ``parts.json`` is not a violation but any
    SEMANTIC change is. Scoped to ``banks/parts/`` at depth 1 (HPC etiquette: no
    broad traversal). Any unreadable/malformed catalog is skipped — a bank that
    cannot be parsed is a bank-CI problem, not an integrity signal here.
    """
    import json

    from harness.core import bank as _bank

    out: dict[str, str] = {}
    parts_dir = os.path.join(root, _BANK_PARTS)
    try:
        versions = sorted(os.listdir(parts_dir))
    except OSError:
        return out
    for ver in versions:
        catalog = os.path.join(parts_dir, ver, _BANK_CATALOG)
        if not os.path.isfile(catalog):
            continue
        try:
            with open(catalog, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            out[f"bank:{ver}"] = _bank.content_hash(data)
        except (OSError, ValueError):
            continue
    return out


def snapshot(root: str = ".") -> dict[str, str]:
    """Content manifest {relative_path: sha256} of the tracked base files.

    Includes ``bank:<version>`` synthetic keys folding the parts-bank content
    hash (closes the gap where a mid-run bank swap was invisible to the freeze).
    """
    root = os.path.abspath(root)
    manifest: dict[str, str] = {}
    for path in tracked_files(root):
        try:
            manifest[_rel(path, root)] = _sha256(path)
        except OSError:
            # A file that vanished between listing and hashing is a change; it
            # will surface as "removed" against any earlier snapshot.
            continue
    manifest.update(_bank_hashes(root))
    return manifest


def violations(before: dict[str, str], root: str = ".") -> list[str]:
    """Tracked base files that changed, were added, or were removed vs `before`.

    Returns a sorted list of relative paths (empty = base code untouched).
    """
    after = snapshot(root)
    changed: set[str] = set()
    for rel, digest in before.items():
        if rel not in after or after[rel] != digest:
            changed.add(rel)          # removed or mutated
    for rel in after:
        if rel not in before:
            changed.add(rel)          # newly added base file
    return sorted(changed)
