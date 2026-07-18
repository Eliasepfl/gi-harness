"""Offline tests for the exporter's MULTI-VIEW pixel channel: episode._write_package's
``views=[...]`` path (per-view layout ``frames/<id>/t%05d.png``, view 0 = the legacy
``frames/`` -- backward compatible), the per-view alignment rules (settle drop / surplus
drop / DEFICIT hard-error, exact ``n_png == T`` per view), the episode.json/manifest
provenance (``capture.views``), and the CLI ``--view`` spec parser.

No Godot, no display: the frame producer is INJECTED through _write_package's
``capture_fn`` seam (an interface extension, per the no-monkeypatching rule) and writes
synthetic PNG files with the capture host's ``frame_%05d.png`` naming. The GDScript side
(capture_host.gd / visual_dress.gd parametric camera) is exercised by the cluster
Validate lane, not here.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.export import episode as X                   # noqa: E402
from harness.export.loader import load_episode            # noqa: E402
from harness.cli import _parse_view_spec                  # noqa: E402


# --------------------------------------------------------------------------- #
# Synthetic trail + injected capture (no engine anywhere)
# --------------------------------------------------------------------------- #
def _trail(T, latch, result, dim="3D"):
    def pos(t):
        return [float(t), 0.0] if dim == "2D" else [float(t), 0.0, 0.0]
    frames = [{"tick": t, "entities": {"craft": {"pos": pos(t), "vel": pos(0),
                                                 "angle": 0.0, "controlled": True,
                                                 "static": False}}} for t in range(T + 1)]
    return {"frames": frames, "checkpoints": dict(latch), "result": result,
            "world_size": [800, 600], "ticks": T}


def _emit_frames(d, n):
    os.makedirs(d, exist_ok=True)
    for i in range(n):
        with open(os.path.join(d, "frame_%05d.png" % i), "wb") as fh:
            fh.write(b"PNG %d" % i)


def _fake_capture(n_primary, n_extra=None):
    """A ``capture_fn`` (same signature as episode._capture_frames) writing ``n_primary``
    raw frames for view 0 and ``n_extra`` (default: n_primary) for every extra view, and
    returning a capture_gif-shaped result incl. resolved per-view records."""
    def cap(game_path, out_gif, actions, seed, frames_dir, *, follow, width, height,
            fps, max_frames, cam_dist=None, views=None):
        _emit_frames(frames_dir, n_primary)
        with open(out_gif, "wb") as fh:
            fh.write(b"GIF")
        res = {"result": "success", "ticks": n_primary - 1,
               "n_frames": n_primary, "out_path": out_gif}
        if views is not None:
            recs = []
            for k, v in enumerate(views):
                if k > 0:
                    _emit_frames(v["frames_dir"], n_primary if n_extra is None else n_extra)
                recs.append({"id": v["id"], "follow": v.get("follow", follow),
                             "cam_dist": v.get("cam_dist", cam_dist),
                             "elevation": v.get("elevation"),
                             "azimuth": v.get("azimuth"), "fov": v.get("fov"),
                             "view_target": v.get("view_target"),
                             "result": "success", "ticks": n_primary - 1,
                             "n_pngs": n_primary, "frames_dir": v["frames_dir"]})
            res["views"] = recs
        return res
    return cap


def _export(tmp_path, T, *, views=None, capture_fn, latch=None):
    root = str(tmp_path)
    return X._write_package(
        os.path.join(root, "a_3d_ring_course", "game.gd"), root, "# src", "gdscript",
        actions=["thrust"] * T, seed=0, trail=_trail(T, latch or {"cp0": 2}, "success"),
        trajectory_kind="witness", witness_source="tree", witness_path=None,
        render_frames=True, views=views, capture_fn=capture_fn)


# --------------------------------------------------------------------------- #
# views=None -> the single-view package, laid out exactly as before
# --------------------------------------------------------------------------- #
def test_single_view_layout_unchanged(tmp_path):
    T = 5
    rec = _export(tmp_path, T, capture_fn=_fake_capture(T + 1))
    ep_dir = Path(rec["paths"]["dir"])
    names = sorted(p.name for p in (ep_dir / "frames").iterdir())
    assert names == [f"t{t:05d}.png" for t in range(1, T + 1)]   # ticks 1..T, no subdirs
    meta = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    assert meta["n_frames"] == T
    assert "views" not in meta["capture"]          # byte-identical meta shape
    assert "views" not in rec
    assert not list(ep_dir.glob("_frames_raw*"))   # raw dirs cleaned up


# --------------------------------------------------------------------------- #
# 2-view export: layout + provenance + loader compatibility
# --------------------------------------------------------------------------- #
def test_two_view_export_layout_and_provenance(tmp_path):
    T = 4
    views = [{}, {"id": "top", "elevation": 90.0, "fov": 45.0, "view_target": "craft"}]
    rec = _export(tmp_path, T, views=views, capture_fn=_fake_capture(T + 1))
    ep_dir = Path(rec["paths"]["dir"])

    # view 0 = today's path; the extra view in frames/<id>/ with the SAME tick naming
    assert len(list((ep_dir / "frames").glob("t*.png"))) == T
    assert sorted(p.name for p in (ep_dir / "frames" / "top").glob("t*.png")) \
        == [f"t{t:05d}.png" for t in range(1, T + 1)]
    assert not list(ep_dir.glob("_frames_raw*"))

    meta = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    cv = meta["capture"]["views"]
    assert [v["id"] for v in cv] == ["view0", "top"]
    assert cv[0]["path"] == "frames" and cv[1]["path"] == "frames/top"
    assert cv[1]["elevation"] == 90.0 and cv[1]["fov"] == 45.0
    assert cv[1]["view_target"] == "craft"
    assert all(v["n_frames"] == T for v in cv)
    assert meta["n_frames"] == T                   # n_frames stays the PRIMARY count
    assert rec["views"] == cv

    # the loader still validates the package (frames/ glob is non-recursive)
    ep = load_episode(rec["paths"]["dir"])
    assert ep.validate(require_frames=True)["ok"]


def test_two_view_surplus_dropped_per_view(tmp_path):
    # A non-terminating negative can render a small trailing surplus -- dropped per view.
    T = 3
    rec = _export(tmp_path, T, views=[{}, {"id": "side", "azimuth": 90.0}],
                  capture_fn=_fake_capture(T + 1 + 2))
    ep_dir = Path(rec["paths"]["dir"])
    assert len(list((ep_dir / "frames").glob("t*.png"))) == T
    assert len(list((ep_dir / "frames" / "side").glob("t*.png"))) == T
    meta = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    assert meta["capture"]["trailing_frames_dropped"] == 2
    assert [v["trailing_frames_dropped"] for v in meta["capture"]["views"]] == [2, 2]


def test_view_deficit_is_a_hard_error(tmp_path):
    # An extra view missing a tick's pixel must fail the WHOLE package, never misalign.
    T = 4
    with pytest.raises(ValueError, match="DEFICIT"):
        _export(tmp_path, T, views=[{}, {"id": "b"}],
                capture_fn=_fake_capture(T + 1, n_extra=T))   # extra view: one short


def test_primary_deficit_is_a_hard_error(tmp_path):
    T = 4
    with pytest.raises(ValueError, match="DEFICIT"):
        _export(tmp_path, T, capture_fn=_fake_capture(T))     # < n_state = T+1


# --------------------------------------------------------------------------- #
# _export_view_specs -- ids, dirs, rejection rules
# --------------------------------------------------------------------------- #
def test_export_view_specs_defaults_and_raw_dirs(tmp_path):
    ep_dir = Path(tmp_path)
    raw = ep_dir / "_frames_raw"
    specs = X._export_view_specs([{}, {"elevation": 10.0}, {"id": "top"}], ep_dir, raw)
    assert [s["id"] for s in specs] == ["view0", "view1", "top"]
    assert specs[0]["frames_dir"] == str(raw)
    assert specs[1]["frames_dir"] == str(ep_dir / "_frames_raw_view1")
    assert specs[2]["frames_dir"] == str(ep_dir / "_frames_raw_top")
    assert specs[1]["elevation"] == 10.0           # camera keys pass through untouched
    assert X._export_view_specs(None, ep_dir, raw) is None


def test_export_view_specs_rejects_bad_ids(tmp_path):
    ep_dir = Path(tmp_path)
    raw = ep_dir / "_frames_raw"
    with pytest.raises(ValueError):
        X._export_view_specs([], ep_dir, raw)
    with pytest.raises(ValueError):
        X._export_view_specs([{"id": "a"}, {"id": "a"}], ep_dir, raw)
    with pytest.raises(ValueError):
        X._export_view_specs([{"id": "../evil"}], ep_dir, raw)


# --------------------------------------------------------------------------- #
# manifest.jsonl carries the multi-view provenance (and legacy rows unchanged)
# --------------------------------------------------------------------------- #
def test_manifest_line_records_views(tmp_path):
    T = 3
    rec = _export(tmp_path, T, views=[{}, {"id": "top", "elevation": 90.0}],
                  capture_fn=_fake_capture(T + 1))
    X.append_manifest(str(tmp_path), rec)
    lines = [json.loads(x) for x in
             (Path(tmp_path) / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    row = lines[-1]
    assert [v["id"] for v in row["views"]] == ["view0", "top"]
    assert row["views"][1]["path"] == "frames/top"


def test_manifest_line_single_view_has_no_views_key(tmp_path):
    T = 3
    rec = _export(tmp_path, T, capture_fn=_fake_capture(T + 1))
    X.append_manifest(str(tmp_path), rec)
    lines = [json.loads(x) for x in
             (Path(tmp_path) / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "views" not in lines[-1]


# --------------------------------------------------------------------------- #
# CLI --view spec parser
# --------------------------------------------------------------------------- #
def test_parse_view_spec_default_and_full():
    assert _parse_view_spec("default") == {}
    assert _parse_view_spec("") == {}
    d = _parse_view_spec("id=top;elevation=90;fov=45;view_target=puck,goal_left")
    assert d == {"id": "top", "elevation": 90.0, "fov": 45.0,
                 "view_target": "puck,goal_left"}
    assert _parse_view_spec("follow=true;cam_dist=2.5;azimuth=-30") \
        == {"follow": True, "cam_dist": 2.5, "azimuth": -30.0}


def test_parse_view_spec_rejects_garbage():
    with pytest.raises(ValueError):
        _parse_view_spec("nonsense")
    with pytest.raises(ValueError):
        _parse_view_spec("bogus_key=1")
