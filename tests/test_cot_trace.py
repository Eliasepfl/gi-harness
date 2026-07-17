"""CoT observability (Elias 2026-07-17): the OpenRouter backend must KEEP the
model's reasoning traces — a<N>.trace.json beside a<N>.gd — so the ambition
analysis can see WHY a design changed, not just WHAT changed, and which injected
instruction (repair hint) the model was reacting to."""
from __future__ import annotations

import json
import os

from harness.gen.gamegen import _reasoning_from, _write_trace


def _body(msg: dict) -> dict:
    return {"choices": [{"message": msg}]}


def test_reasoning_from_unified_field():
    assert _reasoning_from(_body({"content": "x", "reasoning": "I will add a timer"})) \
        == "I will add a timer"


def test_reasoning_from_reasoning_content_variant():
    assert _reasoning_from(_body({"content": "x", "reasoning_content": "plan..."})) \
        == "plan..."


def test_reasoning_from_details_blocks():
    msg = {"content": "x", "reasoning_details": [
        {"type": "text", "text": "step one"}, {"type": "text", "text": "step two"},
        {"type": "other"}]}
    assert _reasoning_from(_body(msg)) == "step one\nstep two"


def test_reasoning_from_absent_or_garbage_is_none():
    assert _reasoning_from(_body({"content": "x"})) is None
    assert _reasoning_from(_body({"content": "x", "reasoning": "   "})) is None
    assert _reasoning_from({}) is None
    assert _reasoning_from(None) is None


def test_write_trace_persists_beside_attempts(tmp_path):
    _write_trace(str(tmp_path), 2, "because the hint asked for reachability",
                 {"model": "m", "attempt": 2, "repair_hint": "make it reachable"})
    p = tmp_path / "a2.trace.json"
    data = json.loads(p.read_text())
    assert data["reasoning"].startswith("because")
    assert data["repair_hint"] == "make it reachable"
    assert data["attempt"] == 2


def test_write_trace_never_raises_on_bad_dir():
    _write_trace(os.path.join("/nonexistent", "nope"), 1, "r", {})  # must not raise
