"""chord.py -- the SINGLE Python boundary for the CHORD pivot (Phase 1).

A decision-tick action crosses the wire as either:
  * a plain ``str``  -- a single verb, e.g. ``"thrust_up"``. BYTE-IDENTICAL to every
    pre-chord witness / act message; single-verb traffic is entirely unchanged.
  * a JSON array (``list``/``tuple`` of ``str``) -- a CHORD, e.g.
    ``["thrust_forward", "thrust_up"]``: multiple keys pressed in the SAME decision
    tick (Elias-approved).

``normalize_action`` is the ONE place Python collapses that wire union into a single
canonical form -- a lexicographically sorted ``tuple[str, ...]`` -- rejecting empties,
duplicates, non-``str`` components, and (when the game's ``actions()`` is supplied)
non-members. Everything BEHIND this boundary handles the canonical tuple only: no
stringly-typed ``'+'`` parsing lives anywhere in the codebase.

``wire_action`` renders a canonical action back to its WIRE form for sending: a
single verb collapses to a plain ``str`` (preserving byte-identity of existing
single-verb traffic); a real chord is a sorted ``list[str]``. The GDScript host
re-sorts a local copy defensively, so the wire form is already canonical and the host
is idempotent on it (see ``godotworld/chord_util.gd`` and
``notes/engines/CHORD_PIVOT.md``).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Iterable, Optional


class ChordError(ValueError):
    """A malformed chord: empty action/verb, duplicate verbs, a non-``str`` component,
    or a component that is not a declared action. Typed (subclasses ``ValueError``) so
    callers and tests can catch it precisely rather than a bare ``ValueError``."""


def normalize_action(action, valid: Optional[Iterable[str]] = None) -> tuple[str, ...]:
    """Canonicalize one wire action into a lexicographically sorted tuple of verbs.

    ``action`` is either a ``str`` (single verb -- the common, legacy case) or a
    sequence of ``str`` (a chord). When ``valid`` is given (the game's ``actions()``),
    every component verb must be a member of it.

    Returns ``tuple[str, ...]`` sorted lexicographically. Raises :class:`ChordError`
    on an empty action, an empty/non-``str`` verb, duplicate verbs, or a non-member.
    """
    # A plain string is a single verb -- checked FIRST so a str never falls into the
    # Sequence branch (str is itself a Sequence of characters).
    if isinstance(action, str):
        verbs = [action]
    elif isinstance(action, Sequence) and not isinstance(action, (bytes, bytearray)):
        verbs = list(action)
    else:
        raise ChordError(
            f"action must be a str or a sequence of str, got {type(action).__name__}"
        )

    if not verbs:
        raise ChordError("empty chord: an action must have at least one verb")

    for v in verbs:
        if not isinstance(v, str):
            raise ChordError(
                f"chord verb must be a str, got {type(v).__name__}: {v!r}"
            )
        if v == "":
            raise ChordError("empty verb string in action")

    if len(set(verbs)) != len(verbs):
        dups = sorted({v for v in verbs if verbs.count(v) > 1})
        raise ChordError(f"duplicate verb(s) in chord: {dups}")

    if valid is not None:
        valid_set = set(valid)
        bad = [v for v in verbs if v not in valid_set]
        if bad:
            raise ChordError(f"action verb(s) not in actions(): {sorted(set(bad))}")

    return tuple(sorted(verbs))


def wire_action(action, valid: Optional[Iterable[str]] = None):
    """Canonical WIRE form of one action after validation via :func:`normalize_action`:
    a plain ``str`` for a single verb (byte-identical to legacy traffic), a sorted
    ``list[str]`` for a real (>=2 verb) chord."""
    canon = normalize_action(action, valid)
    return canon[0] if len(canon) == 1 else list(canon)


def wire_actions(actions: Iterable, valid: Optional[Iterable[str]] = None) -> list:
    """Wire form of a whole per-tick action SEQUENCE. Each item goes through
    :func:`wire_action`, EXCEPT the two noop sentinels -- ``None`` (serve/batch noop
    tick) and ``""`` (capture-witness noop tick) -- which pass through verbatim. This
    keeps the noop policy in exactly one place so no call site re-implements it."""
    out: list = []
    for a in actions:
        if a is None or a == "":
            out.append(a)
        else:
            out.append(wire_action(a, valid))
    return out
