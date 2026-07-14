"""Unit tests for the shared state-action tree (harness/core/statetree.py).

Everything runs against a FAKE executor: a deterministic, dict-based transition
function with NO physics. A "player" starts at (0, 0); movement actions shift it
by one unit; every other action is a genuine no-op (zero state change) — exactly
the "action with no effect = probably stuck" case the design targets. Because the
transition is a pure function of the action prefix, replaying the same prefix
always yields the same state, so the tree's identity-by-prefix holds bit-exactly
without any engine.

Covered: dedup guarantee, no-effect edges never spawn children, terminal_stuck
after K (default and custom), streak reset on effect, frontier selection
strategies, restart-from-leaf, claim atomicity (two workers -> one wins),
release/commit lifecycle, budget accounting, and JSON round-trip.
"""

from __future__ import annotations

import json
import random
import threading

import pytest

from harness.core import statetree as st
from harness.core.statetree import Expansion, Node, StateTree, StateTreeError


# ========================================================================== #
# Fake executor — deterministic dict transition, no physics
# ========================================================================== #
_MOVE = {"right": (1, 0), "left": (-1, 0), "up": (0, 1), "down": (0, -1)}
# Eight distinct idle (no-effect) actions, enough to trip the default K_STUCK=8.
_IDLE = tuple(f"idle{i}" for i in range(8))
ALL_ACTIONS = ("right", "left", "up", "down", *_IDLE)


class FakeExecutor:
    """Replays an action prefix to a final snapshot, faithfully to run_episode's
    decision-tick loop (latch checkpoints, then check failure, then success).

    Checkpoints: ``reach_3`` (x >= 3), ``reach_6`` (x >= 6).
    Failure: y <= -3 (fell into the pit). Success: x >= 6 and y >= 1.
    """

    batched = False

    def __init__(self):
        self.calls = 0                        # how many episodes it was asked to run

    def run_batch(self, game_source, episodes, max_ticks, frames_every=0,
                  escape_margin=None):
        out = []
        for ep in episodes:
            self.calls += 1
            out.append(self._run(list(ep.get("actions", [])), max_ticks))
        return out

    @staticmethod
    def _run(actions, max_ticks):
        x = y = 0
        latches = {"reach_3": None, "reach_6": None}
        applied = 0
        result = "budget"
        limit = len(actions) if max_ticks is None else min(len(actions), max_ticks)
        for i in range(limit):
            dx, dy = _MOVE.get(actions[i], (0, 0))
            x += dx
            y += dy
            applied += 1
            if latches["reach_3"] is None and x >= 3:
                latches["reach_3"] = applied
            if latches["reach_6"] is None and x >= 6:
                latches["reach_6"] = applied
            if y <= -3:
                result = "failure"
                break
            if x >= 6 and y >= 1:
                result = "success"
                break
        snap = {"player": {"pos": [float(x), float(y)], "vel": [0.0, 0.0],
                           "angle": 0.0}}
        return {"result": result, "ticks": applied, "checkpoints": latches,
                "final_snapshot": snap, "actions": actions[:applied]}


def new_tree(actions=ALL_ACTIONS, **kw) -> StateTree:
    tree = StateTree(actions, game_hash="fake", **kw)
    tree.init_root(FakeExecutor().run_batch("g", [{"seed": 0, "actions": []}], 0)[0])
    return tree


def driven_tree(actions=ALL_ACTIONS, **kw):
    """A tree wired to a FakeExecutor, root initialised via that executor."""
    ex = FakeExecutor()
    tree = StateTree(actions, game_hash="fake", **kw)
    tree.init_root(executor=ex, game_source="g")
    return tree, ex


# ========================================================================== #
# Fingerprint helpers
# ========================================================================== #
def test_fingerprint_is_hashable_and_rounded():
    fp = st.fingerprint({"b": {"pos": [1.23456789, 2.0], "vel": [0, 0], "angle": 0}},
                        decimals=3)
    assert isinstance(hash(fp), int)                 # hashable
    assert fp == (("b", 1.235, 2.0, 0.0, 0.0, 0.0),)  # one entry, rounded to 3 dp


def test_fp_delta_infinite_on_topology_change():
    a = st.fingerprint({"x": {"pos": [0, 0]}})
    b = st.fingerprint({"x": {"pos": [0, 0]}, "y": {"pos": [0, 0]}})
    assert st.fp_delta(a, b) == float("inf")         # different body sets
    assert st.fp_delta(None, a) == float("inf")      # unknown parent
    assert st.fp_delta(a, a) == 0.0


# ========================================================================== #
# Root / basic structure
# ========================================================================== #
def test_root_exists_and_needs_init_before_expand():
    tree = StateTree(ALL_ACTIONS)
    assert tree.root.is_root and len(tree) == 1
    # Expanding before the root has a fingerprint is a misuse.
    with pytest.raises(StateTreeError):
        tree.record(tree.root, "right",
                    {"result": "budget", "ticks": 1, "checkpoints": {},
                     "final_snapshot": {"player": {"pos": [1, 0]}}})


def test_expand_creates_child_with_prefix_identity():
    tree, ex = driven_tree()
    res = tree.expand(tree.root, "right", executor=ex, game_source="g")
    assert res.outcome == "created"
    child = res.child
    assert child.prefix == ("right",)
    assert child.parent == () and child.action_from_parent == "right"
    assert child.depth == 1
    assert tree.get(("right",)) is child
    assert tree.child(tree.root, "right") is child


# ========================================================================== #
# Dedup guarantee — never explore the same action-combo twice
# ========================================================================== #
def test_dedup_same_edge_returns_existing_no_replay():
    tree, ex = driven_tree()
    first = tree.expand(tree.root, "right", executor=ex, game_source="g")
    calls_after_first = ex.calls
    second = tree.expand(tree.root, "right", executor=ex, game_source="g")
    assert second.outcome == "existing"
    assert second.child is first.child               # same node object
    assert ex.calls == calls_after_first             # no second replay happened
    assert len(tree.get(())._claims) == 0


def test_dedup_via_record_directly():
    tree = new_tree()
    ep = {"result": "budget", "ticks": 1, "checkpoints": {},
          "final_snapshot": {"player": {"pos": [1, 0]}}}
    a = tree.record(tree.root, "right", ep)
    b = tree.record(tree.root, "right", ep)
    assert a.outcome == "created" and b.outcome == "existing"
    assert b.child is a.child
    assert tree.budget()["episodes"] == 2            # root init + one real record only


# ========================================================================== #
# No-effect rule — an edge fact, never a child
# ========================================================================== #
def test_no_effect_edge_creates_no_child():
    tree, ex = driven_tree()
    before = len(tree)
    res = tree.expand(tree.root, "idle0", executor=ex, game_source="g")
    assert res.outcome == "no_effect"
    assert res.child is None
    assert len(tree) == before                       # no node was added
    assert tree.root.edges["idle0"].outcome == "no_effect"
    assert tree.get(("idle0",)) is None              # no node exists for the prefix


def test_no_effect_repeat_is_deduped_not_recreated():
    tree, ex = driven_tree()
    tree.expand(tree.root, "idle0", executor=ex, game_source="g")
    again = tree.expand(tree.root, "idle0", executor=ex, game_source="g")
    assert again.outcome == "existing" and again.child is None


# ========================================================================== #
# Stuck rule — K consecutive no-effect expansions -> terminal_stuck
# ========================================================================== #
def test_terminal_stuck_after_default_k():
    tree, ex = driven_tree()
    root = tree.root
    for i, idle in enumerate(_IDLE):                 # 8 distinct no-effect actions
        res = tree.expand(root, idle, executor=ex, game_source="g")
        assert res.outcome == "no_effect"
        if i < st.K_STUCK_DEFAULT - 1:
            assert root.status == st.OPEN
            assert res.stuck is False
    assert root.status == st.TERMINAL_STUCK          # flipped on the 8th
    assert res.stuck is True
    assert root.no_effect_streak == st.K_STUCK_DEFAULT


def test_terminal_stuck_custom_k():
    tree, ex = driven_tree(k_stuck=3)
    root = tree.root
    for idle in _IDLE[:2]:
        assert tree.expand(root, idle, executor=ex, game_source="g").outcome == "no_effect"
        assert root.status == st.OPEN
    res = tree.expand(root, _IDLE[2], executor=ex, game_source="g")
    assert res.stuck is True and root.status == st.TERMINAL_STUCK


def test_effect_resets_no_effect_streak():
    tree, ex = driven_tree(k_stuck=3)
    root = tree.root
    tree.expand(root, _IDLE[0], executor=ex, game_source="g")   # streak 1
    tree.expand(root, _IDLE[1], executor=ex, game_source="g")   # streak 2
    assert root.no_effect_streak == 2
    tree.expand(root, "right", executor=ex, game_source="g")    # effect -> reset
    assert root.no_effect_streak == 0 and root.status == st.OPEN
    # Two more idles no longer reach the (now-reset) threshold of 3.
    tree.expand(root, _IDLE[2], executor=ex, game_source="g")
    tree.expand(root, _IDLE[3], executor=ex, game_source="g")
    assert root.status == st.OPEN and root.no_effect_streak == 2


def test_stuck_node_leaves_the_frontier():
    tree, ex = driven_tree(k_stuck=3)
    root = tree.root
    for idle in _IDLE[:3]:
        tree.expand(root, idle, executor=ex, game_source="g")
    assert root.status == st.TERMINAL_STUCK
    assert tree.is_frontier(root) is False
    assert root not in tree.frontier()


# ========================================================================== #
# Terminal success / failure statuses
# ========================================================================== #
def test_child_status_success_and_failure():
    tree = new_tree()
    win = {"result": "success", "ticks": 7, "checkpoints": {"reach_6": 7},
           "final_snapshot": {"player": {"pos": [6, 1]}}}
    res = tree.record(tree.root, "right", win)
    assert res.child.status == st.TERMINAL_SUCCESS and res.child.is_terminal

    lose = {"result": "failure", "ticks": 3, "checkpoints": {},
            "final_snapshot": {"player": {"pos": [0, -3]}}}
    res2 = tree.record(tree.root, "down", lose)
    assert res2.child.status == st.TERMINAL_FAILURE and res2.child.is_terminal
    # Terminal children are not frontier nodes.
    assert tree.frontier() == [n for n in tree.frontier() if n.status == st.OPEN]


# ========================================================================== #
# Exhaustion — every action tried, none left
# ========================================================================== #
def test_node_exhausted_when_all_actions_tried():
    tree, ex = driven_tree(actions=("right", "idle0"))
    root = tree.root
    tree.expand(root, "right", executor=ex, game_source="g")   # child
    assert root.status == st.OPEN                                # idle0 still untried
    tree.expand(root, "idle0", executor=ex, game_source="g")    # no_effect
    assert tree.untried(root) == []
    assert root.status == st.EXHAUSTED
    assert tree.is_frontier(root) is False


# ========================================================================== #
# Frontier selection (restart-from-leaf)
# ========================================================================== #
def _grow_chain(tree, ex, actions):
    """Expand a straight-line chain of actions from the root; return the leaf node."""
    node = tree.root
    for a in actions:
        node = tree.expand(node, a, executor=ex, game_source="g").child
    return node


def test_frontier_lists_only_open_nodes_with_untried_actions():
    tree, ex = driven_tree(actions=("right", "up"))
    leaf = _grow_chain(tree, ex, ["right", "right"])
    fr = tree.frontier()
    # root (up untried), ("right",) (up untried), ("right","right") (both untried).
    assert leaf in fr and tree.root in fr
    assert all(n.status == st.OPEN and tree.untried(n) for n in fr)


def test_select_frontier_deepest():
    tree, ex = driven_tree(actions=("right", "up"))
    _grow_chain(tree, ex, ["right", "right", "right"])
    chosen = tree.select_frontier("deepest")
    assert chosen.depth == 3 and chosen.prefix == ("right", "right", "right")


def test_select_frontier_most_checkpoints():
    tree, ex = driven_tree(actions=("right", "up"))
    # A right-chain latches reach_3 at depth 3; an up-chain latches nothing.
    _grow_chain(tree, ex, ["right", "right", "right"])
    _grow_chain(tree, ex, ["up", "up", "up", "up"])
    chosen = tree.select_frontier("most_checkpoints")
    assert chosen.n_latched() == 1
    assert chosen.prefix == ("right", "right", "right")
    # Alias resolves the same way.
    assert tree.select_frontier("most-checkpoints-latched") is chosen


def test_select_frontier_uniform_deterministic_without_rng_and_seeded_with():
    tree, ex = driven_tree(actions=("right", "up"))
    _grow_chain(tree, ex, ["right"])
    # No rng -> stable first-in-order pick (deterministic across calls).
    assert tree.select_frontier("uniform") is tree.select_frontier("uniform")
    # Seeded rng -> reproducible choice.
    pick_a = tree.select_frontier("uniform", rng=random.Random(42))
    pick_b = tree.select_frontier("uniform", rng=random.Random(42))
    assert pick_a is pick_b


def test_select_frontier_none_when_saturated():
    tree, ex = driven_tree(actions=("idle0",))
    tree.expand(tree.root, "idle0", executor=ex, game_source="g")   # only action -> no_effect
    assert tree.untried(tree.root) == []
    assert tree.select_frontier("deepest") is None
    assert tree.frontier() == []


def test_unknown_frontier_strategy_raises():
    tree = new_tree()
    with pytest.raises(StateTreeError):
        tree.select_frontier("bogus")


# ========================================================================== #
# Async edge claiming — atomicity, release, commit
# ========================================================================== #
def test_claim_is_atomic_one_winner_sequential():
    tree = new_tree()
    assert tree.claim(tree.root, "right", lane="A") is True
    assert tree.claim(tree.root, "right", lane="B") is False   # already claimed
    assert tree.claimed(tree.root, "right") == "A"
    # A claimed action is not offered as untried / frontier work.
    assert "right" not in tree.untried(tree.root)


def test_claim_atomic_under_thread_contention():
    tree = new_tree()
    winners = []
    barrier = threading.Barrier(16)

    def worker(i):
        barrier.wait()                                   # maximise the race window
        if tree.claim(tree.root, "right", lane=f"L{i}"):
            winners.append(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(winners) == 1                             # exactly one lane won

    # The winner commits; late losers see the committed edge as existing (dedup).
    lane = f"L{winners[0]}"
    ep = {"result": "budget", "ticks": 1, "checkpoints": {},
          "final_snapshot": {"player": {"pos": [1, 0]}}}
    res = tree.commit(tree.root, "right", ep, lane=lane)
    assert res.outcome == "created"
    assert tree.commit(tree.root, "right", ep, lane="stale").outcome == "existing"


def test_release_frees_a_claim():
    tree = new_tree()
    assert tree.claim(tree.root, "right", lane="A") is True
    assert tree.release(tree.root, "right", lane="B") is False   # not the owner
    assert tree.release(tree.root, "right", lane="A") is True
    assert tree.claimed(tree.root, "right") is None
    assert tree.claim(tree.root, "right", lane="B") is True      # re-claimable


def test_commit_without_claim_raises():
    tree = new_tree()
    ep = {"result": "budget", "ticks": 1, "checkpoints": {},
          "final_snapshot": {"player": {"pos": [1, 0]}}}
    with pytest.raises(StateTreeError):
        tree.commit(tree.root, "right", ep, lane="A")


def test_expand_reports_conflict_when_claimed_by_another_lane():
    tree = new_tree()
    tree.claim(tree.root, "right", lane="A")
    res = tree.expand(tree.root, "right", episode={"result": "budget", "ticks": 1,
                      "checkpoints": {}, "final_snapshot": {"player": {"pos": [1, 0]}}},
                      lane="B")
    assert res.outcome == "conflict" and res.child is None


# ========================================================================== #
# Budget accounting
# ========================================================================== #
def test_budget_counts_replays_and_ticks_not_dedup():
    tree, ex = driven_tree(actions=("right", "up"))     # root init already counted 1
    base = tree.budget()
    assert base["episodes"] == 1 and base["ticks"] == 0
    tree.expand(tree.root, "right", executor=ex, game_source="g")   # +1 ep, +1 tick
    tree.expand(tree.root, "up", executor=ex, game_source="g")      # +1 ep, +1 tick
    tree.expand(tree.root, "right", executor=ex, game_source="g")   # dedup -> no change
    b = tree.budget()
    assert b["episodes"] == 3                            # root + 2 real replays
    assert b["ticks"] == 2
    assert b["nodes"] == len(tree)


# ========================================================================== #
# JSON round-trip
# ========================================================================== #
def _grow_sample_tree():
    tree, ex = driven_tree(actions=("right", "up", "idle0"))
    _grow_chain(tree, ex, ["right", "right", "right"])   # latches reach_3
    tree.expand(tree.root, "idle0", executor=ex, game_source="g")   # a no_effect edge
    tree.expand(tree.root, "up", executor=ex, game_source="g")
    return tree


def test_json_round_trip_is_faithful_and_stable():
    tree = _grow_sample_tree()
    text = tree.to_json()
    # Stable: serialising twice yields byte-identical JSON.
    assert tree.to_json() == text
    restored = StateTree.from_json(text)
    # Structural equality via the canonical dict.
    assert restored.to_dict() == tree.to_dict()
    # Re-serialisation of the restored tree is identical too.
    assert restored.to_json() == text


def test_json_round_trip_preserves_semantics():
    tree = _grow_sample_tree()
    restored = StateTree.from_json(tree.to_json())
    assert restored.actions == tree.actions
    assert restored.budget() == tree.budget()
    assert len(restored) == len(tree)
    # A rebuilt node keeps identity, fingerprint, checkpoints, edges.
    node = restored.get(("right", "right", "right"))
    assert isinstance(node, Node) and node.n_latched() == 1
    assert node.checkpoints.get("reach_3") == 3
    assert restored.root.edges["idle0"].outcome == "no_effect"
    # And it keeps working: dedup still holds, the frontier is recomputed.
    dup = restored.expand(restored.root, "right",
                          episode={"result": "budget", "ticks": 1, "checkpoints": {},
                                   "final_snapshot": {"player": {"pos": [1, 0]}}})
    assert dup.outcome == "existing"
    assert restored.get(("right",)) in restored.frontier()


def test_save_and_load_file(tmp_path):
    tree = _grow_sample_tree()
    path = tmp_path / "tree.json"
    tree.save(str(path))
    loaded = StateTree.load(str(path))
    assert loaded.to_dict() == tree.to_dict()
    # File is valid, schema-versioned JSON.
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == st.SCHEMA_VERSION
    assert data["game_hash"] == "fake"


def test_load_rejects_unknown_schema_version():
    tree = new_tree()
    data = tree.to_dict()
    data["schema_version"] = 999
    with pytest.raises(StateTreeError):
        StateTree.from_dict(data)


def test_claims_are_not_serialised():
    tree = new_tree()
    tree.claim(tree.root, "right", lane="A")             # in-flight, runtime-only
    restored = StateTree.from_json(tree.to_json())
    # A reload is a fresh coordination epoch: the claim is gone, edge re-claimable.
    assert restored.claimed(restored.root, "right") is None
    assert restored.claim(restored.root, "right", lane="B") is True
