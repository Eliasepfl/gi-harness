"""Offline unit tests for the CHORD boundary (``harness/verify/chord.py``).

These never touch Godot -- they pin the single Python normalization boundary that turns
the wire union (``str | list[str]``) into one canonical, sorted, validated form:

* canonicalization: ``["a","b"]`` and ``["b","a"]`` collapse to the same tuple;
* legacy byte-identity: a single verb stays a plain ``str`` on the wire;
* typed rejection of duplicates / empties / non-``str`` / non-members;
* the noop sentinels (``None``, ``""``) pass a sequence through untouched.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.verify.chord import (  # noqa: E402
    ChordError,
    chord_from_mask,
    normalize_action,
    wire_action,
    wire_actions,
)


# ---------------------------------------------------------------- canonicalization
def test_canonicalization_order_independent():
    """A chord is a SET of simultaneous verbs: element order must not matter."""
    assert normalize_action(["a", "b"]) == ("a", "b")
    assert normalize_action(["b", "a"]) == ("a", "b")
    assert normalize_action(["a", "b"]) == normalize_action(["b", "a"])


def test_canonicalization_three_verbs():
    assert normalize_action(["thrust_up", "brake", "thrust_forward"]) == (
        "brake", "thrust_forward", "thrust_up")


def test_tuple_input_accepted():
    assert normalize_action(("b", "a")) == ("a", "b")


# ---------------------------------------------------------------- legacy byte-identity
def test_single_verb_str_is_unchanged():
    """The overwhelmingly common (legacy) case: a plain str -> a 1-tuple, and its wire
    form is the SAME plain str -> every existing single-verb witness is byte-identical."""
    assert normalize_action("thrust_up") == ("thrust_up",)
    assert wire_action("thrust_up") == "thrust_up"
    assert isinstance(wire_action("thrust_up"), str)


def test_singleton_chord_collapses_to_str():
    """A degenerate 1-verb chord canonicalizes to the single-verb wire form."""
    assert normalize_action(["thrust_up"]) == ("thrust_up",)
    assert wire_action(["thrust_up"]) == "thrust_up"


def test_wire_form_of_real_chord_is_sorted_list():
    assert wire_action(["thrust_up", "thrust_forward"]) == ["thrust_forward", "thrust_up"]
    assert wire_action(["b", "a"]) == ["a", "b"]
    assert isinstance(wire_action(["b", "a"]), list)


# ---------------------------------------------------------------- typed rejection
def test_duplicate_in_chord_is_typed_error():
    with pytest.raises(ChordError) as ei:
        normalize_action(["a", "a"])
    assert "duplicate" in str(ei.value).lower()
    # Same rejection through the wire helper.
    with pytest.raises(ChordError):
        wire_action(["thrust_up", "thrust_up"])


def test_empty_chord_rejected():
    with pytest.raises(ChordError):
        normalize_action([])


def test_empty_verb_rejected():
    with pytest.raises(ChordError):
        normalize_action("")
    with pytest.raises(ChordError):
        normalize_action(["a", ""])


def test_non_str_component_rejected():
    with pytest.raises(ChordError):
        normalize_action(["a", 3])


def test_non_sequence_rejected():
    with pytest.raises(ChordError):
        normalize_action(3)
    with pytest.raises(ChordError):
        normalize_action(None)


# ---------------------------------------------------------------- membership (valid=)
def test_membership_accepts_members():
    valid = ["up", "down", "left", "right"]
    assert normalize_action(["right", "up"], valid) == ("right", "up")
    assert normalize_action("up", valid) == ("up",)


def test_membership_rejects_non_member():
    valid = ["up", "down", "left", "right"]
    with pytest.raises(ChordError) as ei:
        normalize_action(["up", "jump"], valid)
    assert "jump" in str(ei.value)
    with pytest.raises(ChordError):
        normalize_action("jump", valid)


# ---------------------------------------------------------------- sequence + noops
def test_wire_actions_preserves_noops_and_single_verbs():
    """A legacy single-verb sequence (with a None noop) is byte-identical after
    wire_actions; a chord element canonicalizes in place."""
    seq = ["up", None, "right", ["down", "right"], ""]
    assert wire_actions(seq) == ["up", None, "right", ["down", "right"], ""]


def test_wire_actions_legacy_witness_is_identity():
    witness = ["up"] * 8 + ["right"] * 8 + ["down", "right"] * 8
    assert wire_actions(witness) == witness


# ============================================================ PHASE 2: allow_empty (idle)
def test_empty_chord_rejected_by_default_but_allowed_opt_in():
    """The empty chord is the Phase-2 IDLE tick: rejected unless allow_empty is set, so
    every legacy call site (default False) keeps rejecting it."""
    with pytest.raises(ChordError):
        normalize_action([])
    assert normalize_action([], allow_empty=True) == ()
    assert normalize_action((), allow_empty=True) == ()


def test_wire_action_idle_form_is_empty_list():
    """allow_empty -> the wire form of an all-off action is the empty list [] (distinct
    from the None / '' noop sentinels)."""
    assert wire_action([], allow_empty=True) == []
    with pytest.raises(ChordError):
        wire_action([])                       # default still rejects


def test_empty_verb_string_not_treated_as_idle():
    """An empty VERB '' is a malformed verb, not an idle tick — still rejected even with
    allow_empty (only an empty CHORD is idle)."""
    with pytest.raises(ChordError):
        normalize_action("", allow_empty=True)
    with pytest.raises(ChordError):
        normalize_action(["a", ""], allow_empty=True)


def test_wire_actions_threads_allow_empty_for_idle_demo():
    """A demo carrying idle ticks replays through the one boundary when allow_empty is on;
    the empty-list idle form coexists with the None/'' noop sentinels."""
    seq = ["up", [], None, ["down", "right"], ""]
    assert wire_actions(seq, allow_empty=True) == ["up", [], None, ["down", "right"], ""]
    with pytest.raises(ChordError):
        wire_actions(["up", []])              # default rejects the empty chord


# ============================================================ PHASE 2: chord_from_mask
ACTS = ["brake", "thrust_forward", "thrust_up"]   # a deliberately UNSORTED-relative set


def test_mask_singleton_collapses_to_plain_str():
    """A single set bit -> the plain verb STRING (the legacy singleton wire is preserved)."""
    assert chord_from_mask([0, 1, 0], ACTS) == "thrust_forward"
    assert isinstance(chord_from_mask([1, 0, 0], ACTS), str)


def test_mask_multi_bit_is_sorted_list():
    """Two+ set bits -> a lexicographically SORTED list (canonical, order-free)."""
    assert chord_from_mask([1, 0, 1], ACTS) == ["brake", "thrust_up"]
    assert chord_from_mask([1, 1, 1], ACTS) == ["brake", "thrust_forward", "thrust_up"]


def test_mask_all_zeros_rejected_unless_idle_enabled():
    """All-zeros is the IDLE tick: a ChordError unless allow_empty, then the empty list []."""
    with pytest.raises(ChordError):
        chord_from_mask([0, 0, 0], ACTS)
    assert chord_from_mask([0, 0, 0], ACTS, allow_empty=True) == []


def test_mask_length_mismatch_is_typed_error():
    with pytest.raises(ChordError):
        chord_from_mask([0, 1], ACTS)         # too short
    with pytest.raises(ChordError):
        chord_from_mask([0, 1, 0, 1], ACTS)   # too long


def test_mask_accepts_numpy_row():
    """A numpy int/bool row (SB3's MultiBinary action) works via truthiness."""
    np = pytest.importorskip("numpy")
    assert chord_from_mask(np.array([1, 0, 1], dtype=np.int8), ACTS) == ["brake", "thrust_up"]
    assert chord_from_mask(np.array([0, 1, 0], dtype=bool), ACTS) == "thrust_forward"


def test_mask_membership_is_enforced():
    """chord_from_mask validates through wire_action, so a pressed verb must be a member
    of the action list (it always is, since the mask is aligned) — the validation path is
    exercised (a non-member would raise, but by construction every verb is declared)."""
    # Every set bit maps to a declared action; the collapse is byte-identical to wire_action.
    assert chord_from_mask([1, 0, 1], ACTS) == wire_action(["brake", "thrust_up"], ACTS)
