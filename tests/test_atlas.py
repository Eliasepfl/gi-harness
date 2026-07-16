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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
