"""Run-integrity manifest: freeze the harness base code during a generation run.

OBJECTIVES hard rule: the base code is FROZEN while a game is being generated.
Repairs may only touch the GENERATED game (its own sandbox dir). Any change to a
tracked base file mid-run invalidates that run.

This module provides a tiny content manifest over the tracked base files:

    tracked base = harness/**/*.py + CONTRACTS.md   (scenes/, tests/, env.py and
    any bytecode caches are deliberately EXCLUDED)

`snapshot(root)` returns {relative_path: sha256}. `violations(before, root)`
re-snapshots and lists every base file that changed, was added, or was removed
since `before`. `gamegen.generate_game` snapshots before a run and checks after;
a non-empty result forces the run's verdict to INVALIDATED.
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
    caches) plus ``CONTRACTS.md`` at the root. ``scenes/``, ``tests/`` and
    ``env.py`` are intentionally out of scope: generated games and tests live
    there, and secrets must never be hashed or surfaced.
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

    return sorted(out)


def snapshot(root: str = ".") -> dict[str, str]:
    """Content manifest {relative_path: sha256} of the tracked base files."""
    root = os.path.abspath(root)
    manifest: dict[str, str] = {}
    for path in tracked_files(root):
        try:
            manifest[_rel(path, root)] = _sha256(path)
        except OSError:
            # A file that vanished between listing and hashing is a change; it
            # will surface as "removed" against any earlier snapshot.
            continue
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
