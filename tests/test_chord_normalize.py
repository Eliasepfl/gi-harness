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
