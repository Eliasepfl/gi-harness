"""Tests for the tree-based G3 solver (harness/verify/treesolve.py).

Everything runs against a deterministic, physics-free ``FakeExecutor`` in the
spirit of tests/test_gameverify.py's FakeWorld and tests/test_statetree.py's
fake transition: a 1-D "trek" where ``fwd`` moves the controlled body +1, ``back``
-1 and ``idle`` nothing, with declared milestones at intermediate positions and a
success line further right. Because the transition is a pure function of the flat
action prefix, replays are bit-exact and the tree's identity-by-prefix holds
without any engine — so witnesses, dedup and determinism are all checkable.

Covered (the deliverable's test list): the solver SOLVES the chain game (batched
and streaming) with a replayable, non-trivial witness; dedup means a shared macro
prefix is never duplicated; determinism (two runs -> identical witness); the tick
budget is respected on an unsolvable game; the UNSOLVED progress diagnosis matches
the legacy shape; macro-edge expansion (name/flatten/tick) is correct; and the
G3_SOLVER selection (default "tree", HARNESS_G3_SOLVER override) dispatches.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.verify import gameverify as gv          # noqa: E402
from harness.verify import treesolve as ts           # noqa: E402
from harness.core.statetree import StateTree          # noqa: E402


# ====================================================================== #
# FakeExecutor — deterministic 1-D trek, faithful to run_episode's loop
# ====================================================================== #
ACTIONS = ["fwd", "back", "idle"]
_STEP = {"fwd": 1, "back": -1, "idle": 0}


class FakeExecutor:
    """Replays a flat action list on a 1-D world (x starts at 0).

    Milestones ``m_near`` (x >= NEAR) and ``m_far`` (x >= FAR); success at
    x >= WIN. Mirrors the runner: apply one action per tick up to
    ``min(max_ticks, len(actions))``, latch checkpoints, then check success;
    ``exhausted`` when the action list runs out before the tick budget. Records
    every flat action list it was asked to replay (for dedup assertions)."""

    NEAR, FAR, WIN = 3, 6, 8

    def __init__(self, batched=False, win=WIN):
        self.batched = batched
        self.win = win
        self.calls = 0
        self.seen: list = []            # every flat action list replayed

    def run_batch(self, game_source, episodes, max_ticks, frames_every=0,
                  escape_margin=None):
        out = []
        for ep in episodes:
            self.calls += 1
            actions = list(ep.get("actions", []))
            self.seen.append(tuple(actions))
            out.append(self._run(actions, max_ticks, frames_every))
        return out

    def _run(self, actions, max_ticks, frames_every):
        x = 0
        latches = {"m_near": None, "m_far": None}
        applied = 0
        result = "budget"
        limit = len(actions) if max_ticks is None else min(len(actions), max_ticks)
        frames = []
        for i in range(limit):
            x += _STEP.get(actions[i], 0)
            applied += 1
            if latches["m_near"] is None and x >= self.NEAR:
                latches["m_near"] = applied
            if latches["m_far"] is None and x >= self.FAR:
                latches["m_far"] = applied
            if frames_every and applied % frames_every == 0:
                frames.append({"tick": applied, "entities": {
                    "trek": {"pos": [float(x), 0.0], "bbox": [x - 1, -1, x + 1, 1],
                             "static": False, "sensor": False, "shape": "circle"}}})
            if x >= self.win:
                result = "success"
                break
        if result == "budget" and max_ticks is not None and len(actions) < max_ticks:
            result = "exhausted"
        snap = {"trek": {"pos": [float(x), 0.0], "vel": [0.0, 0.0], "angle": 0.0}}
        rec = {"result": result, "ticks": applied, "checkpoints": latches,
               "final_snapshot": snap, "actions": actions[:applied]}
        if frames_every:
            rec["frames"] = frames
        return rec


DECLARED = ["m_near", "m_far"]


@pytest.fixture()
def small_thresholds(monkeypatch):
    """Shrink the v2.3 anti-triviality/horizon bars for the tiny fake trek, exactly
    as tests/test_gameverify.py's ``legacy_thresholds`` does for its fixtures."""
    monkeypatch.setattr(gv, "TRIVIAL_TICKS", 5)
    monkeypatch.setattr(gv, "PROBE_HORIZON", 120)


# ====================================================================== #
# Macro-edge expansion correctness
# ====================================================================== #
def test_macro_names_cover_actions_x_holds():
    names = ts._macro_names(["fwd", "back"])
    # 2 actions x holds 1..MACRO_MAX
    assert len(names) == 2 * (gv.MACRO_MAX - gv.MACRO_MIN + 1)
    assert "fwd*1" in names and f"back*{gv.MACRO_MAX}" in names


def test_split_and_flatten_roundtrip():
    assert ts._split_macro("fwd*3") == ("fwd", 3)
    # base action containing the separator: split on the LAST one
    assert ts._split_macro("a*b*2") == ("a*b", 2)
    prefix = ("fwd*2", "back*1", "idle*3")
    assert ts._flatten(prefix) == ["fwd", "fwd", "back", "idle", "idle", "idle"]
    assert ts._macro_ticks(prefix) == 6
    assert ts._flatten(()) == [] and ts._macro_ticks(()) == 0


# ====================================================================== #
# Solves the chain game — streaming (Py) and batched (Js), replayable witness
# ====================================================================== #
@pytest.mark.parametrize("batched", [False, True])
def test_solver_finds_replayable_nontrivial_witness(small_thresholds, batched):
    ex = FakeExecutor(batched=batched)
    layer = ts.run_g3_tree(ex, "trek", ACTIONS, DECLARED)

    assert layer["passed"] is True, layer
    w = layer["witness"]
    assert w is not None
    assert w["seed"] == gv.WORLD_SEED                       # world seed, not a plan seed
    assert w["ticks"] >= gv.TRIVIAL_TICKS
    assert len(w["actions"]) == w["ticks"]
    # Every declared milestone latched on the winning path, in order.
    assert w["checkpoints"]["m_near"] is not None
    assert w["checkpoints"]["m_far"] is not None
    assert w["checkpoints"]["m_near"] <= w["checkpoints"]["m_far"] <= w["ticks"]

    # The witness replays EXACTLY to success on a fresh world.
    replay = ex._run(w["actions"], len(w["actions"]), 0)
    assert replay["result"] == "success"

    # Reported episode count is the number of executor replays, tagged solver=tree.
    ep = layer["checks"]["episodes"]
    assert ep["solver"] == "tree"
    assert ep["run"] >= 1


def test_checks_shape_matches_run_g3(small_thresholds):
    """The tree layer carries the SAME keys the legacy run_g3 layer does, so
    _finish_g3 and the hints stay engine/solver-agnostic."""
    ex = FakeExecutor()
    tree_layer = ts.run_g3_tree(ex, "trek", ACTIONS, DECLARED)
    rnd_layer = gv.run_g3(FakeExecutor(), "trek", ACTIONS, DECLARED)
    assert set(tree_layer) == set(rnd_layer)
    for name in ("episodes", "solvable", "non_trivial", "milestones_latched",
                 "replayable", "solidity"):
        assert name in tree_layer["checks"], name


# ====================================================================== #
# Determinism — same game + seed -> identical witness
# ====================================================================== #
def test_determinism_identical_witness(small_thresholds):
    w1 = ts.run_g3_tree(FakeExecutor(), "trek", ACTIONS, DECLARED)["witness"]
    w2 = ts.run_g3_tree(FakeExecutor(), "trek", ACTIONS, DECLARED)["witness"]
    assert w1 == w2 and w1 is not None


def test_determinism_batched_matches_itself(small_thresholds):
    a = ts.run_g3_tree(FakeExecutor(batched=True), "trek", ACTIONS, DECLARED)
    b = ts.run_g3_tree(FakeExecutor(batched=True), "trek", ACTIONS, DECLARED)
    assert a["witness"] == b["witness"]


# ====================================================================== #
# Dedup — a shared macro prefix is never duplicated
# ====================================================================== #
def test_shared_prefix_never_duplicated():
    # Insert the SAME rollout (same tail, same episode) twice: the second insert
    # must add NO new nodes — the tree dedups the shared prefix by construction.
    tree = StateTree(ts._macro_names(ACTIONS), world_seed=gv.WORLD_SEED, eps=0.0)
    ex = FakeExecutor()
    tree.init_root(ex.run_batch("trek", [{"seed": 0, "actions": []}], 0)[0])

    tail = ["fwd*2", "fwd*3", "fwd*2"]                      # 7 flat fwd ticks
    flat = ts._flatten(tuple(tail))
    ep = ex.run_batch("trek", [{"seed": 0, "actions": flat}], gv.PROBE_HORIZON)[0]

    ts._insert_rollout(tree, tree.root, tail, flat, 0, ep)
    n_after_first = len(tree)
    ts._insert_rollout(tree, tree.root, tail, flat, 0, ep)     # identical -> all dedup
    assert len(tree) == n_after_first
    # Every node has a unique prefix (dedup guarantee, structural).
    prefixes = [n.prefix for n in tree.nodes()]
    assert len(prefixes) == len(set(prefixes))


def test_search_merges_overlapping_rollouts(small_thresholds):
    # Across a whole search, rollouts that share a leading macro merge: the root
    # is the base of every rollout, so it is revisited many times yet exists once.
    ex = FakeExecutor()
    _, _, replays, tree = ts._tree_search(ex, "trek", ACTIONS, gv.PROBE_HORIZON)
    assert tree.root.visits >= 1
    # Far fewer nodes than the flat ticks replayed -> prefixes are shared.
    assert len(tree) <= tree.ticks_simulated + 1
    prefixes = [n.prefix for n in tree.nodes()]
    assert len(prefixes) == len(set(prefixes))


# ====================================================================== #
# Budget respected + UNSOLVED progress diagnosis
# ====================================================================== #
def test_budget_respected_on_unsolvable(small_thresholds, monkeypatch):
    # An unreachable success line (win far beyond any horizon walk) exhausts the
    # budget without a witness; simulated ticks stay within TICK_BUDGET.
    monkeypatch.setattr(ts, "TICK_BUDGET", 4000)
    ex = FakeExecutor(win=10_000)                          # unreachable
    witness, episodes, replays, tree = ts._tree_search(ex, "trek", ACTIONS,
                                                       gv.PROBE_HORIZON)
    assert witness is None
    assert tree.ticks_simulated <= ts.TICK_BUDGET + gv.PROBE_HORIZON  # soft cap
    assert replays == len(episodes) >= 1


def test_unsolved_progress_diagnosis(small_thresholds, monkeypatch):
    # m_near (x>=3) is reachable, m_far (x>=6) sits just below an unreachable win;
    # the UNSOLVED layer carries reach_counts + stuck_after (legacy shape).
    monkeypatch.setattr(ts, "TICK_BUDGET", 4000)
    ex = FakeExecutor(win=10_000)
    layer = ts.run_g3_tree(ex, "trek", ACTIONS, DECLARED)
    assert layer["checks"]["solvable"]["pass"] is False
    assert layer.get("witness") is None
    prog = layer["progress"]
    assert set(prog) == {"reach_counts", "stuck_after"}
    assert set(prog["reach_counts"]) == set(DECLARED)
    assert prog["reach_counts"]["m_near"] > 0
    assert prog["stuck_after"] in DECLARED
    # The legacy hint builder consumes this shape unchanged.
    hint = gv._hint_unsolved(layer["checks"], prog)
    assert isinstance(hint, str) and hint


# ====================================================================== #
# Solver selection: default "tree", HARNESS_G3_SOLVER override
# ====================================================================== #
def test_g3_solver_default_is_tree(monkeypatch):
    monkeypatch.delenv("HARNESS_G3_SOLVER", raising=False)
    assert gv.G3_SOLVER == "tree"
    assert gv._g3_solver() == "tree"


def test_g3_solver_env_override(monkeypatch):
    monkeypatch.setenv("HARNESS_G3_SOLVER", "random")
    assert gv._g3_solver() == "random"
    monkeypatch.setenv("HARNESS_G3_SOLVER", "TREE")        # case-insensitive
    assert gv._g3_solver() == "tree"


def test_run_g3_dispatches_by_solver(small_thresholds, monkeypatch):
    # tree solver tags the episodes check solver="tree"; random does not.
    monkeypatch.setenv("HARNESS_G3_SOLVER", "tree")
    tree_layer = gv._run_g3(FakeExecutor(), "trek", ACTIONS, DECLARED)
    assert tree_layer["checks"]["episodes"].get("solver") == "tree"

    monkeypatch.setenv("HARNESS_G3_SOLVER", "random")
    rnd_layer = gv._run_g3(FakeExecutor(), "trek", ACTIONS, DECLARED)
    assert "solver" not in rnd_layer["checks"]["episodes"]


# ====================================================================== #
# Inverted-objective frontier selector (stale-state tier fork) + search seam
# ====================================================================== #
def _seed_two_leaves():
    """A tree with two open frontier leaves: a PRODUCTIVE one (a milestone latched,
    shallow) and a STALE one (no milestone, deeper). Returns (tree, productive,
    stale) so the two selectors can be compared head to head."""
    tree = StateTree(ts._macro_names(ACTIONS), world_seed=gv.WORLD_SEED, eps=0.0)
    ex = FakeExecutor()
    tree.init_root(ex.run_batch("trek", [{"seed": 0, "actions": []}], 0)[0])
    # fwd*3 -> x=3 latches m_near (productive, depth 1, 3 ticks).
    fwd = ex.run_batch("trek", [{"seed": 0, "actions": ["fwd"] * 3}], gv.PROBE_HORIZON)[0]
    ts._insert_rollout(tree, tree.root, ["fwd*3"], ["fwd"] * 3, 0, fwd)
    # back*3 then idle*1 -> x=-3, no milestone (stale, deeper, 4 ticks).
    stale_flat = ["back"] * 3 + ["idle"]
    stl = ex.run_batch("trek", [{"seed": 0, "actions": stale_flat}], gv.PROBE_HORIZON)[0]
    ts._insert_rollout(tree, tree.root, ["back*3", "idle*1"], stale_flat, 0, stl)
    productive = tree.get(("fwd*3",))
    stale = tree.get(("back*3", "idle*1"))
    assert productive is not None and stale is not None
    return tree, productive, stale


def test_inverted_selector_targets_stalest_leaf(monkeypatch):
    monkeypatch.setattr(ts, "EPSILON", 0.0)          # pure exploit -> deterministic pick
    tree, productive, stale = _seed_two_leaves()
    rng = ts.random.Random(0)
    # The G3 solver exploits the PRODUCTIVE boundary (most milestones latched)...
    greedy = ts._select_leaves(tree, rng, 4, gv.PROBE_HORIZON, {})
    assert all(nd.prefix == productive.prefix for nd in greedy)
    # ...the inverted fork exploits the STALEST leaf (fewest milestones, deepest).
    inv = ts._select_leaves_inverted(tree, rng, 4, gv.PROBE_HORIZON, {})
    assert all(nd.prefix == stale.prefix for nd in inv)


def test_inverted_selector_prefers_more_cycling(monkeypatch):
    # Between two equally-stale leaves, the one that stalled MORE (deaths ~= cycling)
    # is exploited.
    monkeypatch.setattr(ts, "EPSILON", 0.0)
    tree, _productive, stale = _seed_two_leaves()
    root = tree.root
    inv = ts._select_leaves_inverted(tree, ts.random.Random(0), 1, gv.PROBE_HORIZON,
                                     {stale.prefix: 5})       # stale leaf has cycled a lot
    assert inv and inv[0].prefix == stale.prefix


def test_tree_search_select_and_budget_seam(small_thresholds):
    # The stale-oracle seam: a custom budget caps the search and the inverted
    # selector drops into the SAME solver with no other change.
    ex = FakeExecutor(win=10_000)                    # unreachable -> no witness
    w, eps, replays, tree = ts._tree_search(
        ex, "trek", ACTIONS, gv.PROBE_HORIZON,
        select=ts._select_leaves_inverted, budget=800)
    assert w is None
    assert tree.ticks_simulated <= 800 + gv.PROBE_HORIZON
    assert replays == len(eps) >= 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
