"""Offline unit tests for the exporter's NEGATIVE / behaviorally-diverse sources
(harness/export/rollouts.py + the trajectory_kind / episode_key plumbing in
harness/export/episode.py + loader.filter_by_kind). No Godot, no display: the two policy
generators are pure, and _write_package is exercised on synthetic code-state trails with
render_frames=False (state-only packages), so a losing frame's reward label is still checked
byte-identical to the RL training signal.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.rl import env as E                          # noqa: E402
from harness.export import episode as X                  # noqa: E402
from harness.export.rollouts import (                    # noqa: E402
    perturb_actions, random_actions,
)
from harness.export.loader import EpisodeDataset, load_episode  # noqa: E402

H = E.HORIZON
VERBS = ["tilt_up", "tilt_down", "tilt_left", "tilt_right", "tilt_reset", "tilt_hold"]


# --------------------------------------------------------------------------- #
# random_actions -- deterministic random policy over the declared verbs
# --------------------------------------------------------------------------- #
def test_random_actions_deterministic_and_in_vocab():
    a = random_actions(VERBS, seed=7, horizon=50)
    b = random_actions(VERBS, seed=7, horizon=50)
    c = random_actions(VERBS, seed=8, horizon=50)
    assert a == b                                  # same seed -> same sequence
    assert a != c                                  # different seed -> (almost surely) different
    assert len(a) == 50
    assert set(a).issubset(set(VERBS))             # only declared verbs
    # it actually exercises more than one verb (not a constant policy)
    assert len(set(a)) >= 2


def test_random_actions_empty_verbs_raises():
    try:
        random_actions([], 0, 10)
    except ValueError:
        return
    assert False, "random_actions must reject an empty verb set"


# --------------------------------------------------------------------------- #
# perturb_actions -- seeded near-miss corruptions of the winning witness
# --------------------------------------------------------------------------- #
BASE = ["tilt_up", "tilt_up", "tilt_right", "tilt_hold", "tilt_left", "tilt_down",
        "tilt_reset", "tilt_up"]


def test_perturb_deterministic_and_changes_plan():
    p1, ops1 = perturb_actions(BASE, VERBS, seed=3, n_corruptions=2)
    p2, ops2 = perturb_actions(BASE, VERBS, seed=3, n_corruptions=2)
    assert p1 == p2 and ops1 == ops2               # deterministic in seed
    assert p1 != BASE                              # a corruption actually landed
    assert len(ops1) == 2
    assert all(o["op"] in ("swap", "drop", "replace") for o in ops1)


def test_perturb_op_semantics():
    # force each op kind by scanning seeds, then check the op did what it claims
    seen = set()
    for s in range(200):
        p, ops = perturb_actions(BASE, VERBS, seed=s, n_corruptions=1)
        if not ops:
            continue
        op = ops[0]
        seen.add(op["op"])
        if op["op"] == "drop":
            assert len(p) == len(BASE) - 1
        elif op["op"] == "replace":
            assert len(p) == len(BASE)
            assert p[op["pos"]] == op["to"] and op["to"] != op["from"]
        elif op["op"] == "swap":
            assert len(p) == len(BASE)
    assert {"swap", "drop", "replace"}.issubset(seen), f"only saw {seen}"


def test_perturb_single_verb_game_falls_back_to_drop():
    # a 1-verb game can't "replace"-differ; perturb must still change the plan (via drop)
    base = ["go"] * 6
    p, ops = perturb_actions(base, ["go"], seed=1, n_corruptions=3)
    assert p != base
    assert all(o["op"] in ("swap", "drop") for o in ops)   # never a no-op replace


def test_perturb_short_plan():
    # length-1 plan: swap/drop are unsafe -> replace lands. A single corruption differs;
    # (an even number of replaces on a 2-verb single-position plan can cancel -- honest, and
    # a non-issue for real multi-action witnesses). Assert clean handling + a lands-once change.
    p, ops = perturb_actions(["a"], ["a", "b"], seed=0, n_corruptions=1)
    assert len(p) == 1 and p == ["b"] and ops[0]["op"] == "replace"


# --------------------------------------------------------------------------- #
# trajectory_kind mapping
# --------------------------------------------------------------------------- #
def test_kind_for_witness_source():
    assert X.kind_for_witness_source("rl") == "demo"
    assert X.kind_for_witness_source("tree") == "witness"


# --------------------------------------------------------------------------- #
# _write_package on a synthetic trail (state-only, offline) -- round-trip + labels
# --------------------------------------------------------------------------- #
def _trail(T, latch, result, dim="2D"):
    def pos(t):
        return [float(t), 0.0] if dim == "2D" else [float(t), 0.0, 0.0]
    frames = [{"tick": t, "entities": {"ball": {"pos": pos(t), "vel": pos(0),
                                                "angle": 0.0, "controlled": True,
                                                "static": False}}} for t in range(T + 1)]
    return {"frames": frames, "checkpoints": dict(latch), "result": result,
            "world_size": [800, 600], "ticks": T}


def _export_synth(root, slug, seed, kind, *, T, latch, result, key=None, extra=None):
    game_path = os.path.join(root, slug, "game.gd")     # need not exist (render_frames=False)
    ws = "rl" if kind == "demo" else ("tree" if kind == "witness" else kind)
    return X._write_package(
        game_path, root, "# src", "gdscript",
        actions=["tilt_up"] * T, seed=seed, trail=_trail(T, latch, result),
        trajectory_kind=kind, witness_source=ws, witness_path=None,
        episode_key=(key or str(seed)), render_frames=False, extra_meta=extra)


def test_write_package_state_only_roundtrip_and_labels(tmp_path):
    root = str(tmp_path)
    rec = _export_synth(root, "a_2d_game", 0, "witness",
                        T=5, latch={"cp0": 2, "cp1": 5}, result="success")
    assert rec["trajectory_kind"] == "witness"
    assert rec["n_frames"] == 0 and rec["outcome"] == "success"
    ep = load_episode(rec["paths"]["dir"])
    assert ep.trajectory_kind == "witness"
    # state-only package validates WITHOUT frames, and the win dominates every prefix
    report = ep.validate(require_frames=False)
    assert report["ok"] and report["n_frames"] == 0
    totals = [s["reward"]["total"] for s in ep.steps]
    assert all(sum(totals) > sum(totals[:k]) for k in range(1, len(totals)))
    # every label is byte-identical to env.step_reward with reconstructed latch counts
    latch = ep.meta["checkpoints_latch"]
    ncp = ep.meta["reward_scheme"]["n_checkpoints"]
    for s in ep.steps:
        cb = X._latched_count(latch, s["t"] - 1)
        ca = X._latched_count(latch, s["t"])
        res = "success" if s["done"] else None
        assert abs(s["reward"]["total"] - E.step_reward(cb, ca, ncp, res, s["t"], H)) < 1e-9


def test_write_package_degenerate_trail_raises(tmp_path):
    # a trail with only the build frame (< 1 decision tick) carries no signal -> refuse
    try:
        _export_synth(str(tmp_path), "g", 0, "random", T=0, latch={}, result="failure")
    except ValueError:
        return
    assert False, "expected ValueError on a < 1 tick trajectory"


def test_failure_and_timeout_returns_below_win(tmp_path):
    root = str(tmp_path)
    win = _export_synth(root, "a_2d_game", 0, "witness",
                        T=6, latch={"cp0": 2, "cp1": 4, "cp2": 6}, result="success")
    fail = _export_synth(root, "a_2d_game", 1, "random",
                         T=3, latch={"cp0": 2, "cp1": None, "cp2": None}, result="failure",
                         key="random-1")
    timeout = _export_synth(root, "a_2d_game", 2, "random",
                            T=5, latch={"cp0": 2, "cp1": None, "cp2": None}, result="budget",
                            key="random-2")
    assert win["outcome"] == "success"
    assert fail["outcome"] == "failure" and timeout["outcome"] == "timeout"
    # NEGATIVES strictly below the win -- mechanical (bounded shaping, dominant terminal payoff)
    assert fail["episode_return"] < win["episode_return"]
    assert timeout["episode_return"] < win["episode_return"]


# --------------------------------------------------------------------------- #
# manifest keying -- many episodes of the SAME game/seed coexist by episode_key
# --------------------------------------------------------------------------- #
def test_manifest_keyed_by_episode_key(tmp_path):
    root = str(tmp_path)
    for kind, key, seed in [("witness", "0", 0), ("random", "random-0", 0),
                            ("perturbed", "perturbed-0-0", 0)]:
        rec = _export_synth(root, "a_2d_game", seed, kind,
                            T=4, latch={"cp0": 2, "cp1": 4}, result="success", key=key)
        X.append_manifest(root, rec)
    lines = (tmp_path / "manifest.jsonl").read_text().strip().splitlines()
    assert len(lines) == 3, f"expected 3 coexisting episodes, got {len(lines)}"
    # re-append one -> replaced in place, still 3 (idempotent per (slug, episode_key))
    rec = _export_synth(root, "a_2d_game", 0, "random",
                        T=4, latch={"cp0": 2, "cp1": 4}, result="success", key="random-0")
    X.append_manifest(root, rec)
    lines = (tmp_path / "manifest.jsonl").read_text().strip().splitlines()
    assert len(lines) == 3


# --------------------------------------------------------------------------- #
# loader.filter_by_kind / kinds -- the behavioral-diversity filter
# --------------------------------------------------------------------------- #
def test_loader_filter_by_kind(tmp_path):
    root = str(tmp_path)
    plan = [("a_game_2d", "witness", "0", 0, "success"),
            ("a_game_2d", "random", "random-0", 0, "budget"),
            ("a_game_2d", "random", "random-1", 1, "failure"),
            ("a_game_2d", "perturbed", "perturbed-0-0", 0, "failure"),
            ("a_game_3d", "witness", "0", 0, "success"),
            ("a_game_3d", "random", "random-0", 0, "failure")]
    for slug, kind, key, seed, result in plan:
        rec = _export_synth(root, slug, seed, kind,
                            T=4, latch={"cp0": 2, "cp1": 4 if result == "success" else None},
                            result=result, key=key)
        X.append_manifest(root, rec)

    ds = EpisodeDataset(root)
    assert set(ds.kinds()) == {"witness", "random", "perturbed"}
    wins = list(ds.filter_by_kind(("demo", "witness")))
    negs = list(ds.filter_by_kind(("random", "perturbed")))
    assert len(wins) == 2 and all(e.trajectory_kind == "witness" for e in wins)
    assert len(negs) == 4 and all(e.trajectory_kind in ("random", "perturbed") for e in negs)
    # combine with a slug filter (kinds + slugs are orthogonal)
    negs_2d = list(ds.filter_by_kind(("random", "perturbed"), slugs=["a_game_2d"]))
    assert len(negs_2d) == 3
    # episodes(kinds=...) mirrors the filter; split_by_game is unchanged and disjoint
    assert len(list(ds.episodes(kinds="random"))) == 3
    train, test = ds.split_by_game(frac=0.5, seed=0)
    assert set(train).isdisjoint(set(test)) and len(train) + len(test) == 2
