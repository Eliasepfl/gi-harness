"""Offline unit tests for the exporter's reward labelling + step assembly
(harness/export/episode.py). No Godot, no display: these pin that a ``steps.jsonl``
reward label is byte-identical to the RL training signal (env.step_reward is the ONE
source of truth) and that the per-tick latched counts are reconstructed exactly.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.rl import env as E                       # noqa: E402
from harness.export import episode as X               # noqa: E402

H = E.HORIZON


# --------------------------------------------------------------------------- #
# reward: step_reward_parts total is byte-identical to env.step_reward
# --------------------------------------------------------------------------- #
def test_parts_total_matches_env_step_reward():
    cases = [
        (0, 0, 3, None, 10, H),
        (0, 1, 3, None, 5, H),
        (1, 2, 3, None, 20, H),
        (2, 3, 3, "success", 34, H),
        (0, 0, 3, "failure", 12, H),
        (0, 0, 1, "success", 0, H),         # instant win -> full R_SUCCESS
        (0, 0, 0, None, 7, H),              # no declared checkpoints
    ]
    for (cb, ca, ncp, res, t, h) in cases:
        parts = X.step_reward_parts(cb, ca, ncp, res, t, h)
        expect = E.step_reward(cb, ca, ncp, res, t, h)
        assert abs(parts["total"] - expect) < 1e-9, (cb, ca, ncp, res, t, parts, expect)
        # decomposition is exact: shaping + terminal == total
        assert abs(parts["shaping"] + parts["terminal"] - parts["total"]) < 1e-9


def test_terminal_component_isolation():
    # result=None -> no terminal payoff; success -> the decayed success payoff; failure -> R_FAILURE.
    running = X.step_reward_parts(1, 2, 3, None, 15, H)
    assert running["terminal"] == 0.0
    win = X.step_reward_parts(2, 3, 3, "success", 15, H)
    assert abs(win["terminal"] - E.success_payoff(15, H)) < 1e-9
    lose = X.step_reward_parts(0, 0, 3, "failure", 15, H)
    assert abs(lose["terminal"] - E.R_FAILURE) < 1e-9


# --------------------------------------------------------------------------- #
# latched reconstruction from the latch-tick map
# --------------------------------------------------------------------------- #
def test_latched_count_and_map():
    latch = {"a": 2, "b": 3, "c": None}       # c never latches
    assert X._latched_count(latch, 0) == 0
    assert X._latched_count(latch, 1) == 0
    assert X._latched_count(latch, 2) == 1
    assert X._latched_count(latch, 3) == 2
    assert X._latched_count(latch, 99) == 2
    m3 = X._latched_map(latch, 3)
    assert m3 == {"a": True, "b": True, "c": False}


# --------------------------------------------------------------------------- #
# build_steps: end-to-end labelling of a synthetic success episode
# --------------------------------------------------------------------------- #
def _frames(T):
    # tick 0..T, minimal entities (a single controlled body)
    return [{"tick": t, "entities": {"body": {"pos": [float(t), 0.0], "vel": [0.0, 0.0],
                                              "angle": 0.0, "controlled": True,
                                              "static": False}}} for t in range(T + 1)]


def test_build_steps_success_episode():
    T = 5
    latch = {"cp0": 2, "cp1": 4, "cp2": 5}     # all three latch, last at the terminal tick
    n_cp = 3
    actions = ["a", "b", "c", "d", "e"]
    steps, ep_return, got_T = X.build_steps(_frames(T), latch, n_cp, actions, "success", H)

    assert got_T == T and len(steps) == T
    # 1-based monotone ticks; action alignment
    assert [s["t"] for s in steps] == [1, 2, 3, 4, 5]
    assert [s["action"] for s in steps] == actions
    # n_latched is monotone non-decreasing and correct
    assert [s["n_latched"] for s in steps] == [0, 1, 1, 2, 3]
    # done only on the terminal tick
    assert [s["done"] for s in steps] == [False, False, False, False, True]
    # per-tick total == env.step_reward with the reconstructed counts
    for s in steps:
        cb = X._latched_count(latch, s["t"] - 1)
        ca = X._latched_count(latch, s["t"])
        res = "success" if s["t"] == T else None
        assert abs(s["reward"]["total"] - E.step_reward(cb, ca, n_cp, res, s["t"], H)) < 1e-9
    # episode_return is the sum of totals
    assert abs(ep_return - sum(s["reward"]["total"] for s in steps)) < 1e-6


def test_reward_telescoping_and_terminal_dominance():
    # The validation's "success episode total > any prefix": a certified success return
    # must strictly dominate every prefix (bounded shaping mass, dominant terminal payoff).
    T = 5
    latch = {"cp0": 2, "cp1": 4, "cp2": 5}
    steps, ep_return, _ = X.build_steps(_frames(T), latch, 3, ["a"] * T, "success", H)
    totals = [s["reward"]["total"] for s in steps]
    prefixes = [sum(totals[:k]) for k in range(1, T)]      # every strict prefix
    full = sum(totals)
    assert all(full > p for p in prefixes), (full, prefixes)
    # the shaping mass alone is bounded by SHAPING_MASS; the terminal tick carries the win
    assert steps[-1]["reward"]["terminal"] >= E.R_SUCCESS * E.SUCCESS_TIME_FLOOR
    assert sum(s["reward"]["shaping"] for s in steps) <= E.SHAPING_MASS + 1e-9


# --------------------------------------------------------------------------- #
# small pure helpers
# --------------------------------------------------------------------------- #
def test_deslug_and_outcome():
    assert X.deslug("a_2d_window_washer_winch_your_platform_u") == \
        "a 2d window washer winch your platform u"
    assert X._outcome("success") == "success"
    assert X._outcome("failure") == "failure"
    assert X._outcome("error") == "failure"
    assert X._outcome("exhausted") == "timeout"
    assert X._outcome(None) == "timeout"


def test_append_manifest_idempotent(tmp_path):
    out = str(tmp_path)
    rec = {"slug": "g", "seed": 0, "dim": "2D", "outcome": "success", "ticks": 3,
           "n_frames": 3, "witness_source": "tree", "episode_return": 1.5}
    X.append_manifest(out, rec)
    X.append_manifest(out, rec)                 # re-export same episode -> replaces line
    lines = (tmp_path / "manifest.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1                       # not duplicated
    import json as _j
    row = _j.loads(lines[0])
    assert row["slug"] == "g" and row["paths"]["episode"] == "g/0/episode.json"
    # a different episode adds a second line
    X.append_manifest(out, dict(rec, slug="h"))
    assert len((tmp_path / "manifest.jsonl").read_text().strip().splitlines()) == 2
