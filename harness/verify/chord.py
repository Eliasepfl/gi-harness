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

PHASE 2 -- the IDLE tick (an OPT-IN empty chord). By default an action must have >=1
verb: ``normalize_action``/``wire_action`` REJECT an empty chord, so every legacy call
site is unchanged (empties can never reach the wire). The MultiBinary RL policy
(Phase 2) can emit an all-keys-off vector, so those helpers take an explicit
``allow_empty`` flag (default ``False``): when ``True`` an empty chord canonicalizes to
the empty tuple ``()`` and its wire form is the empty list ``[]`` -- an IDLE tick the
serve host applies as ZERO ``act()`` calls (guarded by its own ``allow_idle`` init
capability; STAKES/game-pressure, not the action-space shape, is what punishes idling).
``chord_from_mask`` is the single MultiBinary bridge: a 0/1 mask aligned with the
game's ``actions()`` -> the wire form, routed through ``wire_action`` so the
canonicalization lives in exactly ONE place (no duplicated sort/collapse logic).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Iterable, Optional


class ChordError(ValueError):
    """A malformed chord: empty action/verb, duplicate verbs, a non-``str`` component,
    or a component that is not a declared action. Typed (subclasses ``ValueError``) so
    callers and tests can catch it precisely rather than a bare ``ValueError``."""


def normalize_action(action, valid: Optional[Iterable[str]] = None, *,
                     allow_empty: bool = False) -> tuple[str, ...]:
    """Canonicalize one wire action into a lexicographically sorted tuple of verbs.

    ``action`` is either a ``str`` (single verb -- the common, legacy case) or a
    sequence of ``str`` (a chord). When ``valid`` is given (the game's ``actions()``),
    every component verb must be a member of it.

    Returns ``tuple[str, ...]`` sorted lexicographically. Raises :class:`ChordError`
    on an empty action, an empty/non-``str`` verb, duplicate verbs, or a non-member.

    ``allow_empty`` (default ``False``, so every existing call site keeps rejecting
    empties) permits the Phase-2 IDLE tick: an empty chord (``[]``/``()``) then returns
    the empty tuple ``()`` instead of raising. A plain ``str`` is never "empty" in this
    sense -- an empty verb string is still rejected (that is a malformed verb, not idle).
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
        if allow_empty:
            return ()                       # Phase-2 idle tick (opt-in): press nothing
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


def wire_action(action, valid: Optional[Iterable[str]] = None, *,
                allow_empty: bool = False):
    """Canonical WIRE form of one action after validation via :func:`normalize_action`:
    a plain ``str`` for a single verb (byte-identical to legacy traffic), a sorted
    ``list[str]`` for a real (>=2 verb) chord, and -- only when ``allow_empty`` -- the
    empty list ``[]`` for the Phase-2 IDLE tick (press nothing)."""
    canon = normalize_action(action, valid, allow_empty=allow_empty)
    if len(canon) == 0:
        return []                           # idle tick wire form (allow_empty only)
    return canon[0] if len(canon) == 1 else list(canon)


def wire_actions(actions: Iterable, valid: Optional[Iterable[str]] = None, *,
                 allow_empty: bool = False) -> list:
    """Wire form of a whole per-tick action SEQUENCE. Each item goes through
    :func:`wire_action`, EXCEPT the two noop sentinels -- ``None`` (serve/batch noop
    tick) and ``""`` (capture-witness noop tick) -- which pass through verbatim. This
    keeps the noop policy in exactly one place so no call site re-implements it.

    ``allow_empty`` (default ``False``) threads to :func:`wire_action` so a Phase-2 demo
    that carries IDLE ticks (empty chords ``[]``) replays through the same one boundary;
    the empty-list idle form is distinct from the ``None``/``""`` noop sentinels above."""
    out: list = []
    for a in actions:
        if a is None or (isinstance(a, str) and a == ""):
            out.append(a)
        else:
            out.append(wire_action(a, valid, allow_empty=allow_empty))
    return out


def chord_from_mask(mask, actions: Sequence, *, allow_empty: bool = False):
    """The SINGLE MultiBinary->wire bridge (Phase 2). Map a binary ``mask`` (an iterable
    of 0/1, one bit per verb, positionally aligned with the game's ordered ``actions``)
    to the canonical wire form via :func:`wire_action` -- so a single pressed key collapses
    to a plain ``str`` (byte-identical legacy singleton traffic), two-or-more pressed keys
    become a sorted ``list[str]`` chord, and (only when ``allow_empty``) all-keys-off
    becomes the empty list ``[]`` idle tick. Reuses ``wire_action`` for the canonicalization
    so the sort/collapse/validate rules live in exactly one place.

    ``mask`` length must equal ``len(actions)`` (a shape mismatch is a caller bug, raised
    as :class:`ChordError`). Truthiness of each bit is used, so a numpy int/bool row works.
    """
    actions = list(actions)
    mask = list(mask)
    if len(mask) != len(actions):
        raise ChordError(
            f"mask length {len(mask)} != n_actions {len(actions)}")
    pressed = [a for a, bit in zip(actions, mask) if bit]
    return wire_action(pressed, actions, allow_empty=allow_empty)
