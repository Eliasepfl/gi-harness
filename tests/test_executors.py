"""Tests for the episode-executor seam (harness/verify/executors.py).

Covers the frames-substrate document builder (``replay_frames_doc`` / ``_round_floats``)
and the Godot lane: the pure-python ``normalize_godot_record`` mapping plus the e2e
frames-doc / GIF render (skipped when the Godot binary is absent).
"""
from __future__ import annotations

import gzip
import json as _json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.verify.executors import _round_floats, replay_frames_doc  # noqa: E402


# ====================================================================== #
# Frames substrate (replay_frames_doc) — the web replayer's payload
# ====================================================================== #
def test_round_floats_recurses_and_preserves_types():
    src = {"pos": [1.23456, 2.0], "n": 3, "flag": True, "s": "x",
           "nested": [{"a": 9.87654}], "none": None}
    out = _round_floats(src, dp=2)
    assert out["pos"] == [1.23, 2.0]
    assert out["n"] == 3 and isinstance(out["n"], int)
    assert out["flag"] is True                      # bool not coerced to float
    assert out["s"] == "x" and out["none"] is None
    assert out["nested"][0]["a"] == 9.88


def _iter_floats(obj):
    if isinstance(obj, bool):
        return
    if isinstance(obj, float):
        yield obj
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_floats(v)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_floats(v)


def doc_ticks(frames):
    return max(fr["tick"] for fr in frames)


# ====================================================================== #
# Godot lane: normalization mapping (pure python) + e2e frames-doc / GIF
# ====================================================================== #
from harness.verify.executors import (  # noqa: E402
    find_godot_exe, normalize_godot_record, render_godot_replay,
)

GODOT_EXE = find_godot_exe()
requires_godot = pytest.mark.skipif(GODOT_EXE is None, reason="Godot binary not present")

_GODOT_SPEC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "tests", "fixtures", "godot_specs", "traverse.spec.json")


def _godot_spec_source():
    with open(_GODOT_SPEC, "r", encoding="utf-8") as fh:
        return fh.read()


# A fixture GodotExecutor episode record: the runner emits world_size but NOT
# title/prompt, and may append an `obs` array to a frame for observation specs.
_GODOT_FIXTURE_REC = {
    "result": "success", "ticks": 2,
    "checkpoints": {"reached": 2},
    "final_snapshot": {"marble": {"pos": [120.0, 90.0]}},
    "world_size": [1400, 700],
    "actions": ["run_right", "hop"],
    "frames": [
        {"tick": 0, "entities": {
            "marble": {"pos": [80.0, 66.0], "vel": [0.0, 0.0], "angle": 0.0,
                       "angular_vel": 0.0, "bbox": [64.0, 50.0, 96.0, 82.0],
                       "shape": "circle", "static": False, "sensor": False,
                       "controlled": True}}},
        # this frame carries an obs tail (sensor spec) that must be stripped
        {"tick": 1, "entities": {
            "marble": {"pos": [95.0, 80.0], "vel": [70.0, 20.0], "angle": 0.1,
                       "angular_vel": 0.0, "bbox": [79.0, 64.0, 111.0, 96.0],
                       "shape": "circle", "static": False, "sensor": False,
                       "controlled": True}},
         "obs": [1.0, 2.0, 3.0]},
    ],
}

_GODOT_FIXTURE_SRC = _json.dumps({
    "engine": "godot",
    "meta": {"title": "Quarry Shelves", "prompt": "Climb the shelves.",
             "world_size": [1400, 700]},
    "bodies": [], "act": {}, "predicates": {},
})


def test_normalize_godot_record_maps_meta_and_frames():
    """The Godot normalization harvests title/prompt from the spec `meta` (the
    runner omits them) and strips every frame to the shared {tick, entities}
    shape — dropping the sensor `obs` tail — so the doc is engine-agnostic."""
    title, prompt, frames = normalize_godot_record(_GODOT_FIXTURE_REC,
                                                    _GODOT_FIXTURE_SRC)
    assert title == "Quarry Shelves"
    assert prompt == "Climb the shelves."
    assert len(frames) == 2
    for fr in frames:
        assert set(fr) == {"tick", "entities"}     # obs tail dropped
    assert frames[0]["tick"] == 0 and frames[1]["tick"] == 1
    # entity query dicts are preserved verbatim (bbox/shape/controlled/...)
    m0 = frames[0]["entities"]["marble"]
    assert m0["shape"] == "circle" and m0["controlled"] is True
    assert m0["bbox"] == [64.0, 50.0, 96.0, 82.0]


def test_normalize_godot_record_tolerates_missing_meta_and_frames():
    """No `meta` block -> title/prompt None; unparseable source is swallowed
    (meta is cosmetic); a record without frames yields an empty frame list."""
    t, p, frames = normalize_godot_record({"result": "budget"},
                                          _json.dumps({"engine": "godot"}))
    assert t is None and p is None and frames == []
    # garbage source never raises
    t2, p2, f2 = normalize_godot_record({"frames": []}, "not json at all")
    assert t2 is None and p2 is None and f2 == []


@requires_godot
def test_replay_frames_doc_godot_schema(tmp_path):
    """The godot substrate obeys the {meta, frames} contract: meta carries
    title/prompt/world_size + engine=='godot'; every frame is {tick, entities}
    with monotone ticks starting at 0 and non-empty entities; floats are rounded
    to <= 2 decimals and the payload gzips small.

    Uses a fixed (non-solving) plan — the schema never depends on the outcome —
    so this stays fast and needs no witness search."""
    src = _godot_spec_source()
    doc = replay_frames_doc(src, engine="godot",
                            actions=(["run_right"] * 20 + ["hop"] * 5),
                            witness={"ticks": 25, "checkpoints": {}}, seed=0)
    assert doc["result"] in ("success", "failure", "exhausted", "budget")

    meta = doc["meta"]
    assert set(meta) == {"title", "prompt", "world_size", "engine", "witness"}
    assert meta["engine"] == "godot"
    assert meta["title"] == "Quarry Shelves" and meta["prompt"]
    assert meta["world_size"] == [1400, 700]

    frames = doc["frames"]
    assert frames and frames[0]["tick"] == 0
    assert frames[-1]["tick"] == doc_ticks(frames)      # monotone, ends at last
    for i in range(1, len(frames)):                      # strictly increasing
        assert frames[i]["tick"] > frames[i - 1]["tick"]
    for fr in frames:
        assert set(fr) == {"tick", "entities"}
        assert fr["entities"]                            # non-empty scene
    # Godot circles/boxes carry bbox + shape (renderer's fallback fields).
    marble = frames[0]["entities"]["marble"]
    assert marble["shape"] == "circle" and "bbox" in marble
    for v in _iter_floats(frames):
        assert round(v, 2) == v

    text = _json.dumps({"meta": meta, "frames": frames}, separators=(",", ":"))
    raw = len(text.encode("utf-8"))
    gz = len(gzip.compress(text.encode("utf-8")))
    assert 0 < gz < raw                                  # compresses
    assert gz < 60_000                                   # inline budget (audit §7)


@requires_godot
def test_render_godot_replay_produces_gif(tmp_path):
    """render_godot_replay draws the Godot frames path to a real GIF via
    render._render_frame (bbox fallback for the omitted verts/radius)."""
    from PIL import Image
    out = tmp_path / "traverse.gif"
    res = render_godot_replay(_godot_spec_source(), str(out),
                              actions=(["run_right"] * 20 + ["hop"] * 5),
                              seed=0, label="Traverse", every=2)
    assert res["result"] in ("success", "failure", "exhausted", "budget")
    assert res["frames"] > 0
    assert out.exists() and out.stat().st_size > 0
    with Image.open(str(out)) as im:
        assert im.n_frames > 0                           # a real multi-frame GIF


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
