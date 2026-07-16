"""Tests for THE ATLAS read-only MVP (harness.atlas): descriptor determinism, entropy
correctness, missing-artifact -> None, jsonl round-trip, and the coverage math.

All OFFLINE — fabricated verify reports + t=0 facts, no engine. The descriptor extractor
is pure aggregation, so these pin its contract exactly.
"""
from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from harness.atlas import descriptors as D  # noqa: E402
from harness.atlas.descriptors import DESCRIPTOR_KEYS, describe_game, slug_of  # noqa: E402
from harness.atlas import render as R  # noqa: E402
from harness.atlas import build as B  # noqa: E402
from harness.atlas import ghosts as G  # noqa: E402
from harness.atlas import frontier as F  # noqa: E402


# ---------------------------------------------------------------- fabricators
def fab_report(*, actions, witness_ticks=None, episodes=None, nodes=None,
               solver_ticks=None, checkpoints_n=None, pressure="has_pressure",
               has_fw=True, dead_ratio=None, dims=None, world_size=None,
               witness_checkpoints=None, passed=True):
    """A hand-built verify report in the real G0..G3 shape (only the fields the atlas reads)."""
    g3_checks = {}
    if episodes is not None or nodes is not None or solver_ticks is not None:
        g3_checks["episodes"] = {"pass": True, "run": episodes, "solver": "tree",
                                 "nodes": nodes, "ticks": solver_ticks}
    if pressure is not None:
        g3_checks["failure_witness"] = {"pass": True, "advisory": True,
                                        "has_failure_witness": has_fw, "outcome": pressure,
                                        "finding": {"outcome": pressure}}
    if dead_ratio is not None:
        g3_checks["dead_space"] = {"pass": True, "advisory": True,
                                   "dead_space": dead_ratio > 5.0,
                                   "linear_ratio": dead_ratio,
                                   "measure_ratio": dead_ratio ** (dims or 2),
                                   "threshold": 5.0, "dims": dims or 2}
    layers = {"G3_solve": {"passed": True, "checks": g3_checks}}
    if checkpoints_n is not None:
        layers["G2_goal"] = {"passed": True,
                             "checks": {"checkpoints_wellformed": {"pass": True,
                                                                   "n": checkpoints_n}}}
    if world_size is not None:
        layers["G0_static"] = {"passed": True,
                               "checks": {"world_size": {"pass": True,
                                                         "declared": list(world_size)}}}
    witness = None
    if actions is not None:
        witness = {"seed": 0, "actions": list(actions),
                   "ticks": witness_ticks if witness_ticks is not None else len(actions),
                   "checkpoints": witness_checkpoints or {}}
    return {"passed": passed, "failure_class": None if passed else "UNSOLVED",
            "layers": layers, "witness": witness, "warnings": [], "engine": "gdscript"}


def fab_facts(bodies, world_size=(800, 600)):
    return {"geometry": list(bodies), "world_size": {"declared": list(world_size)}}


def body(name, pos, *, controlled=False, static=False, sensor=False, half=None):
    d = {"name": name, "pos": list(pos), "controlled": controlled,
         "static": static, "sensor": sensor}
    if half is not None:
        d["half_extents"] = list(half)
    return d


# ---------------------------------------------------------------- entropy
def test_entropy_known_sequences():
    # two equiprobable symbols -> exactly 1 bit
    assert D.shannon_entropy(["a", "a", "b", "b"]) == pytest.approx(1.0)
    # constant sequence -> 0 bits
    assert D.shannon_entropy(["x", "x", "x", "x"]) == 0.0
    # four equiprobable symbols -> 2 bits
    assert D.shannon_entropy(["a", "b", "c", "d"]) == pytest.approx(2.0)
    # empty / None -> None (missing artifact)
    assert D.shannon_entropy([]) is None
    assert D.shannon_entropy(None) is None
    # a skewed distribution matches the closed form
    seq = ["a", "a", "a", "b"]  # p=3/4,1/4
    expect = -(0.75 * math.log2(0.75) + 0.25 * math.log2(0.25))
    assert D.shannon_entropy(seq) == pytest.approx(expect)


def test_witness_entropy_wired_through_report():
    rep = fab_report(actions=["a", "a", "b", "b"], witness_ticks=4)
    d = describe_game("scenes/games/x/x.gd", rep)
    assert d["witness_entropy"] == pytest.approx(1.0)
    assert d["distinct_actions"] == 2
    assert d["witness_ticks"] == 4


# ---------------------------------------------------------------- determinism
def test_descriptor_determinism():
    rep = fab_report(actions=["thrust", "turn_left", "thrust", "reverse"],
                     witness_ticks=161, episodes=50, nodes=3113, solver_ticks=7662,
                     checkpoints_n=6, pressure="has_pressure", has_fw=True,
                     world_size=(800, 600))
    facts = fab_facts([body("ship", (100, 100), controlled=True),
                       body("wall", (400, 300), static=True, half=(20, 200)),
                       body("goal", (700, 500), sensor=True)])
    a = describe_game("scenes/games/x/x.gd", rep, {"facts": facts})
    b = describe_game("scenes/games/x/x.gd", rep, {"facts": facts})
    assert a == b
    # a stable, ordered, JSON-round-trippable dict
    assert list(a.keys()) == list(DESCRIPTOR_KEYS)
    assert json.loads(json.dumps(a)) == a


def test_full_descriptor_extraction():
    rep = fab_report(actions=["thrust", "turn_left", "thrust", "reverse"],
                     witness_ticks=161, episodes=50, nodes=3113, solver_ticks=7662,
                     checkpoints_n=6, pressure="has_pressure", has_fw=True)
    facts = fab_facts([body("ship", (100, 100), controlled=True),
                       body("wall", (400, 300), static=True, half=(20, 200)),
                       body("goal", (700, 500), sensor=True),
                       body("crate", (300, 300))])
    d = describe_game("scenes/games/x/x.gd", rep, {"facts": facts})
    assert d["solver_episodes"] == 50
    assert d["solver_expansions"] == 3113
    assert d["solver_ticks"] == 7662
    assert d["n_checkpoints"] == 6
    assert d["pressure_class"] == "has_pressure"
    assert d["has_failure_witness"] is True
    assert d["n_bodies"] == 4
    assert d["n_controlled"] == 1
    assert d["n_sensor"] == 1
    assert d["n_static"] == 1
    assert d["n_dynamic"] == 1
    assert d["dimension"] == "2D"
    # space utilisation computed fresh from facts (reuse of reachability.space_utilization)
    assert isinstance(d["space_util_linear_ratio"], (int, float))
    assert d["space_util_linear_ratio"] >= 1.0


def test_dimension_3d_from_geometry():
    rep = fab_report(actions=["thrust"], witness_ticks=1)
    facts = fab_facts([body("drone", (100, 100, 100), controlled=True)],
                      world_size=(800, 600))
    d = describe_game("scenes/games/x/x.gd", rep, {"facts": facts})
    assert d["dimension"] == "3D"


def test_space_util_from_report_deadspace_when_no_facts():
    rep = fab_report(actions=["a", "b"], dead_ratio=7.5, dims=2)
    d = describe_game("scenes/games/x/x.gd", rep)   # no facts
    assert d["space_util_linear_ratio"] == 7.5
    assert d["dead_space"] is True


# ---------------------------------------------------------------- missing -> None
def test_missing_artifacts_yield_none_not_crash():
    # no report, no facts -> every descriptor None, no exception
    d = describe_game("scenes/games/x/x.gd", None, None)
    assert set(d.keys()) == set(DESCRIPTOR_KEYS)
    for k in DESCRIPTOR_KEYS:
        if k == "dimension":
            continue  # dimension may still be inferred from source; the rest must be None
        assert d[k] is None, f"{k} should be None with no artifacts"


def test_partial_report_no_witness():
    # an UNSOLVED game: solver ran (effort recorded) but there is no witness
    rep = fab_report(actions=None, episodes=200, nodes=999, passed=False)
    d = describe_game("scenes/games/x/x.gd", rep)
    assert d["witness_ticks"] is None
    assert d["witness_entropy"] is None
    assert d["distinct_actions"] is None
    assert d["solver_episodes"] == 200
    assert d["solver_expansions"] == 999


def test_n_bodies_from_report_g0_counts_when_no_facts():
    # No t=0 facts, but the G0 static report already counted the bodies -> honest fallback.
    rep = fab_report(actions=["a", "b"], passed=True)
    rep["layers"]["G0_static"] = {"passed": True, "checks": {
        "counts": {"pass": True, "n": 6},
        "controlled": {"pass": True, "controlled": ["car"]}}}
    d = describe_game("scenes/games/x/x.gd", rep)   # no facts
    assert d["n_bodies"] == 6
    assert d["n_controlled"] == 1
    # per-class splits remain None without geometry to classify
    assert d["n_static"] is None and d["n_sensor"] is None


def test_accepts_whole_gen_json_wrapper():
    inner = fab_report(actions=["a", "b", "a"], witness_ticks=3)
    gen = {"game_path": "scenes/games/x/x.gd", "verdict": "COMPLETED",
           "attempts": [{"report": {"passed": False}}, {"report": inner}]}
    d = describe_game("scenes/games/x/x.gd", gen)
    assert d["witness_ticks"] == 3
    assert d["distinct_actions"] == 2


def test_slug_of():
    assert slug_of("scenes/games/fly_a_craft/fly_a_craft.gd") == "fly_a_craft"
    assert slug_of("/a/b/tests/fixtures/gd_games/mini_collect.gd") == "mini_collect"
    assert slug_of("scenes/games/foo/a3.gd") == "foo"


# ---------------------------------------------------------------- jsonl round-trip
def test_jsonl_round_trip(tmp_path):
    rep = fab_report(actions=["a", "a", "b"], witness_ticks=3, episodes=10, nodes=42,
                     checkpoints_n=2)
    facts = fab_facts([body("p", (10, 10), controlled=True),
                       body("g", (700, 500), sensor=True)])
    d = describe_game("scenes/games/x/x.gd", rep, {"facts": facts})
    row = {"slug": "x", "game_path": "/abs/x.gd", "descriptors": d,
           "provenance": {"report_source": "runs/gen_0.json"}}
    p = tmp_path / "atlas.jsonl"
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    back = B.load_rows(str(p))
    assert len(back) == 1
    assert back[0]["descriptors"] == d
    assert back[0]["slug"] == "x"


# ---------------------------------------------------------------- coverage math
def _row(slug, **desc):
    full = {k: None for k in DESCRIPTOR_KEYS}
    full.update(desc)
    return {"slug": slug, "descriptors": full}


def test_coverage_math_simple_grid():
    # Two axes, points placed on a known 2x2 grid -> coverage = occupied/total.
    rows = [
        _row("a", witness_entropy=0.0, space_util_linear_ratio=1.0, dimension="2D"),
        _row("b", witness_entropy=0.0, space_util_linear_ratio=1.0, dimension="2D"),  # same cell
        _row("c", witness_entropy=2.0, space_util_linear_ratio=10.0, dimension="3D"),
    ]
    info = R.compute_grid(rows, "witness_entropy", "space_util_linear_ratio", n_bins=2)
    # 3 points, but a & b share a cell -> 2 distinct cells of 4 colonised
    assert info["n_cells"] == 4
    assert info["n_colonized"] == 2
    assert info["coverage"] == pytest.approx(0.5)
    assert info["n_placed"] == 3
    assert info["n_unplaced"] == 0


def test_coverage_counts_unplaced_off_map():
    rows = [
        _row("a", witness_entropy=0.0, space_util_linear_ratio=1.0),
        _row("b", witness_entropy=None, space_util_linear_ratio=1.0),  # missing x -> off map
    ]
    info = R.compute_grid(rows, "witness_entropy", "space_util_linear_ratio", n_bins=3)
    assert info["n_placed"] == 1
    assert info["n_unplaced"] == 1


def test_select_axes_picks_discriminating_pair():
    # entropy + emptiness both vary; a constant axis must NOT be chosen.
    rows = []
    for i in range(8):
        rows.append(_row(f"g{i}", witness_entropy=float(i) / 2.0,
                         space_util_linear_ratio=1.0 + i,
                         n_checkpoints=3,  # constant -> ineligible
                         dimension="2D"))
    x, y, size, scores = R.select_axes(rows, n_bins=4)
    assert {x, y} == {"witness_entropy", "space_util_linear_ratio"}
    # the constant axis scores zero (ineligible)
    assert scores["n_checkpoints"]["score"] == 0.0


def test_render_atlas_end_to_end_writes_svg(tmp_path):
    rows = []
    for i in range(8):
        rows.append(_row(f"g{i}", witness_entropy=float(i) / 3.0,
                         space_util_linear_ratio=1.0 + i * 2,
                         n_checkpoints=i % 4, dimension="2D" if i % 2 else "3D"))
    out = tmp_path / "atlas.svg"
    summary = R.render_atlas(rows, str(out), n_bins=4)
    assert out.exists()
    svg = out.read_text(encoding="utf-8")
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "COVERAGE" in svg
    assert 0.0 <= summary["coverage"] <= 1.0
    assert summary["axes"][0] and summary["axes"][1]


def test_resolve_games_skips_harden_and_attempts(tmp_path):
    # a fake game tree: one real game dir + a harden sandbox that must be skipped
    g = tmp_path / "scenes" / "games" / "myslug"
    g.mkdir(parents=True)
    (g / "myslug.gd").write_text("extends Node\n", encoding="utf-8")
    (g / "a1.gd").write_text("extends Node\n", encoding="utf-8")
    h = tmp_path / "scenes" / "games" / "harden" / "sandbox"
    h.mkdir(parents=True)
    (h / "sandbox.gd").write_text("extends Node\n", encoding="utf-8")
    resolved = B.resolve_games([str(tmp_path / "scenes" / "games" / "*")])
    slugs = [slug_of(p) for p in resolved]
    assert "myslug" in slugs
    assert "sandbox" not in slugs  # harden/ sandbox skipped
    # the resolved game is the slug-named module, not an attempt file
    assert any(p.endswith("myslug/myslug.gd") for p in resolved)


# ================================================================ GHOSTS
# A fabricated .tscn snippet in the real Godot scene-file shape (only the fields the
# ghost extractor reads): a root Node3D, typed physics bodies, an instanced sub-scene
# (counts as a node, NOT a typed body), and a decorative light.
GHOST_TSCN = """[gd_scene load_steps=3 format=3 uid="uid://abc"]

[ext_resource type="PackedScene" path="res://car.tscn" id="1_car"]

[node name="GameScene" type="Node3D"]

[node name="Ground" type="StaticBody3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)

[node name="Wall" type="StaticBody3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 40, 0, 0)

[node name="GreenSpace" type="Area3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 20)

[node name="Crate" type="RigidBody3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 10, 5, 0)

[node name="Car" parent="." instance=ExtResource("1_car")]

[node name="DirectionalLight3D" type="DirectionalLight3D" parent="."]
"""


def test_ghost_descriptor_extraction_from_tscn():
    d = G.describe_ghost_tscn(GHOST_TSCN)
    # geometry-only schema, all keys present
    assert set(d.keys()) == set(G.GHOST_KEYS)
    assert d["dimension"] == "3D"
    # 7 authored nodes: GameScene, Ground, Wall, GreenSpace, Crate, Car(instance), Light
    assert d["n_nodes"] == 7
    assert d["n_scenes"] == 1
    # 4 physics bodies: 2 static + 1 sensor(Area) + 1 dynamic(Rigid); Car(instance) & Light excluded
    assert d["n_bodies"] == 4
    assert d["n_static"] == 2
    assert d["n_sensor"] == 1
    assert d["n_dynamic"] == 1
    # world extent: x spans 0..40 -> 40 is the largest per-axis span
    assert d["world_extent"] == pytest.approx(40.0)
    # proportion derivable (>1 nonzero axis span) and >= 1
    assert d["proportion"] is not None and d["proportion"] >= 1.0


def test_ghost_dimension_2d_and_no_bodies():
    tscn = ('[node name="Root" type="Node2D"]\n'
            '[node name="Sprite" type="Sprite2D" parent="."]\n'
            'position = Vector2(10, 20)\n')
    d = G.describe_ghost_tscn(tscn)
    assert d["dimension"] == "2D"
    assert d["n_bodies"] == 0
    # a single distinct origin -> extent not derivable
    assert d["world_extent"] is None


def test_ghost_parse_handles_instanced_and_untyped(tmp_path):
    # build_ghosts over a fabricated example dir yields one ghost row
    gd = tmp_path / "MyDemo"
    (gd / "scenes").mkdir(parents=True)
    (gd / "scenes" / "main.tscn").write_text(GHOST_TSCN, encoding="utf-8")
    rows = G.build_ghosts([str(tmp_path / "*")])
    assert len(rows) == 1
    assert rows[0]["slug"] == "MyDemo"
    assert rows[0]["kind"] == "ghost"
    assert rows[0]["descriptors"]["n_bodies"] == 4


# ================================================================ FRONTIER RING
def _gen_json(slug, verdict, reach_counts, *, stuck_after=None, episodes=None):
    """A minimal gen_*.json in the real funnel shape carrying a progress block."""
    report = {"passed": verdict == "COMPLETED",
              "failure_class": None if verdict == "COMPLETED" else verdict,
              "layers": {"G3_solve": {"passed": True,
                                      "checks": {"episodes": {"pass": True, "run": episodes,
                                                              "solver": "tree", "nodes": 10,
                                                              "ticks": 20}}}},
              "witness": None, "engine": "gdscript"}
    if reach_counts is not None:
        report["progress"] = {"reach_counts": dict(reach_counts),
                              "stuck_after": stuck_after}
    return {"game_path": f"scenes/games/{slug}/{slug}.gd", "verdict": verdict,
            "attempts": [{"report": report}]}


def test_frontier_progress_normalisation():
    rep = _gen_json("d", "UNSOLVED",
                    {"spire_field_cleared": 2466, "arch_passed": 0, "helipad": 0},
                    stuck_after="spire_field_cleared", episodes=3360)["attempts"][0]["report"]
    prog = F.report_progress(rep)
    assert prog["milestones_total"] == 3
    assert prog["milestones_reached"] == 1
    assert prog["best_milestone"] == "spire_field_cleared"
    assert prog["best_count"] == 2466
    assert F.is_progressing(prog) is True
    label = F.frontier_label("d", prog, rep)
    assert "2466/3360" in label and "spire_field_cleared" in label


def test_frontier_selection_logic(tmp_path):
    # a mix of runs; only UNSOLVED + progressing + existing-dir games make the ring
    runs = tmp_path / "runs"
    (runs / "r1").mkdir(parents=True)
    (runs / "r2").mkdir(parents=True)
    games = tmp_path / "games"
    for s in ("drone", "herding", "flat", "ghosttown"):
        (games / s).mkdir(parents=True)
    # drone: UNSOLVED + progressing (older + newer report -> newer wins)
    (runs / "r1" / "gen_0.json").write_text(json.dumps(
        _gen_json("drone", "UNSOLVED", {"m1": 100, "m2": 0}, stuck_after="m1", episodes=500)))
    (runs / "r2" / "gen_9.json").write_text(json.dumps(
        _gen_json("drone", "UNSOLVED", {"m1": 300, "m2": 0}, stuck_after="m1", episodes=800)))
    # herding: UNSOLVED + progressing
    (runs / "r1" / "gen_1.json").write_text(json.dumps(
        _gen_json("herding", "UNSOLVED", {"endangered": 178}, stuck_after="endangered",
                  episodes=216)))
    # flat: UNSOLVED but reached NOTHING -> excluded
    (runs / "r1" / "gen_2.json").write_text(json.dumps(
        _gen_json("flat", "UNSOLVED", {"m1": 0, "m2": 0}, stuck_after=None, episodes=400)))
    # completed: solved -> excluded
    (runs / "r1" / "gen_3.json").write_text(json.dumps(
        _gen_json("drone", "COMPLETED", {"m1": 500}, episodes=500)))
    # archived: UNSOLVED + progressing but NO game dir -> excluded
    (runs / "r1" / "gen_4.json").write_text(json.dumps(
        _gen_json("archived_old", "UNSOLVED", {"m1": 5}, stuck_after="m1", episodes=9)))
    # give r2 a newer mtime so the newer drone report wins deterministically
    os.utime(str(runs / "r2" / "gen_9.json"), (2_000_000_000, 2_000_000_000))
    os.utime(str(runs / "r1" / "gen_0.json"), (1_000_000_000, 1_000_000_000))

    existing = F.library_slugs(str(games))
    assert existing == {"drone", "herding", "flat", "ghosttown"}
    ring = F.scan_frontier([str(runs / "*" / "gen_*.json")], existing_slugs=existing)
    slugs = [r["slug"] for r in ring]
    assert slugs == ["drone", "herding"]          # sorted; flat + archived excluded
    drone = next(r for r in ring if r["slug"] == "drone")
    # last-known (newer) report pinned: best_count 300, episodes 800
    assert drone["progress"]["best_count"] == 300
    assert "300/800" in drone["label"]
    assert drone["kind"] == "frontier"
    # partial descriptors are present (solver effort survives even with no witness)
    assert drone["descriptors"]["solver_expansions"] == 10


# ================================================================ RENDER: both classes
def test_render_includes_both_legend_classes(tmp_path):
    rows = []
    for i in range(8):
        rows.append(_row(f"g{i}", witness_entropy=float(i) / 3.0,
                         space_util_linear_ratio=1.0 + i * 2,
                         n_bodies=3 + i % 3, n_checkpoints=i % 4,
                         dimension="2D" if i % 2 else "3D"))
    ghosts = [{"slug": "3DCarParking", "kind": "ghost",
               "descriptors": {k: None for k in G.GHOST_KEYS}}]
    ghosts[0]["descriptors"].update(dimension="3D", n_nodes=120, n_scenes=6,
                                    n_bodies=40, n_static=20, n_sensor=8, n_dynamic=12,
                                    world_extent=180.0, proportion=2.4)
    frontier = [{"slug": "drone", "kind": "frontier",
                 "descriptors": {k: None for k in DESCRIPTOR_KEYS},
                 "progress": {"milestones_total": 5, "milestones_reached": 1,
                              "best_milestone": "spire", "best_count": 2466,
                              "reached_fraction": 0.2, "stuck_after": "spire"},
                 "label": "2466/3360 -> spire"}]
    frontier[0]["descriptors"].update(dimension="3D", solver_expansions=5856,
                                      space_util_linear_ratio=3.0)
    out = tmp_path / "atlas.svg"
    summary = R.render_atlas(rows, str(out), n_bins=4, ghosts=ghosts, frontier=frontier)
    svg = out.read_text(encoding="utf-8")
    # both distinct classes appear in the legend
    assert "reference (geometry only)" in svg
    assert "over budget frontier" in svg
    # the ghost slug and frontier label surface on the map
    assert "3DCarParking" in svg
    assert "spire" in svg
    # coverage math still computed over the CERTIFIED rows only
    assert summary["n_placed"] == 8
    assert summary.get("n_ghosts") == 1
    assert summary.get("n_frontier") == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
