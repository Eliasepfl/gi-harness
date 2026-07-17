"""The capture lane's witness ROUTING: a demo shows the trained policy, not the tree solver.

OFFLINE + hermetic -- no Godot, no engine, no verify. ``_resolve_capture_witness`` takes an
injectable ``tree_witness`` resolver for exactly this reason, so the routing decision is
testable in isolation from the thing it routes to.

What is pinned here:
  * a g3'-exported demo_trajectory.json beside the game (or in the round's ``g3/`` sibling dir,
    where g3_prime ACTUALLY writes it -- next to the model artifact) is picked up BY DEFAULT and
    reported as witness_source="rl";
  * with no demo trajectory, the lane re-verifies for the tree witness exactly as it always has
    (witness_source="tree") -- the no-behaviour-change guarantee for the ~8 games with no
    trained policy;
  * an explicit --actions file beats both, and still reports its true provenance;
  * an unusable demo trajectory degrades to the tree witness instead of breaking the capture,
    while an unusable EXPLICIT --actions fails loudly (it must never silently play something
    else).
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import cli  # noqa: E402
from harness.rl.certify import export_demo_trajectory  # noqa: E402

TREE_WITNESS = {"seed": 3, "actions": ["left", "noop", "left", "right", "noop"], "ticks": 5}
DEMO_ACTIONS = ["up", "up", "up", "right", "right"]


def _tree(_game_path: str) -> dict:
    """Stand-in for a fresh verify's tree witness (the G3 solver's first solution)."""
    return dict(TREE_WITNESS)


def _exploding_tree(_game_path: str) -> dict:
    raise AssertionError("re-verified for a tree witness when a demo trajectory was available")


@pytest.fixture()
def game(tmp_path):
    """A harden-shaped round: <slug>/round_1/game/<slug>.gd + a sibling g3/ for the model."""
    game_dir = tmp_path / "slug" / "round_1" / "game"
    game_dir.mkdir(parents=True)
    g = game_dir / "slug.gd"
    g.write_text("extends Node2D\n", encoding="utf-8")
    return str(g)


def _write_demo(path, actions=DEMO_ACTIONS, seed=7):
    """Write a demo trajectory through certify.py's OWN exporter -- so this test breaks if the
    producer's payload shape ever drifts from what the consumer expects."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return export_demo_trajectory(
        {"seed": seed, "actions": actions, "ticks": len(actions), "greedy": True}, str(path))


# ====================================================================== #
# 1. The default: a trained-policy demo is picked up and named honestly
# ====================================================================== #
def test_demo_trajectory_in_g3_sibling_dir_is_default(game):
    """THE routing fix: g3_prime writes demo_trajectory.json beside the MODEL
    (<round>/g3/demo_trajectory.json), not beside the game. The capture lane must find it there
    with no --actions, and must not re-verify at all."""
    demo = os.path.join(os.path.dirname(os.path.dirname(game)), "g3", "demo_trajectory.json")
    _write_demo(demo)
    wit = cli._resolve_capture_witness(game, tree_witness=_exploding_tree)
    assert wit["witness_source"] == "rl"
    assert wit["actions"] == DEMO_ACTIONS
    assert wit["seed"] == 7                      # the demo's OWN seed, not the CLI default
    assert wit["witness_path"] == demo


def test_demo_trajectory_beside_the_game_is_picked_up(game):
    """A demo dropped straight beside the game works too (hand-placed, no harden tree)."""
    demo = os.path.join(os.path.dirname(game), "demo_trajectory.json")
    _write_demo(demo)
    wit = cli._resolve_capture_witness(game, tree_witness=_exploding_tree)
    assert wit["witness_source"] == "rl"
    assert wit["witness_path"] == demo


def test_beside_the_game_wins_over_the_g3_dir(game):
    """Nearest-first: an explicitly-placed demo beside the game beats the round's g3/ export."""
    beside = os.path.join(os.path.dirname(game), "demo_trajectory.json")
    g3dir = os.path.join(os.path.dirname(os.path.dirname(game)), "g3", "demo_trajectory.json")
    _write_demo(beside, actions=["up"], seed=1)
    _write_demo(g3dir, actions=["down"], seed=2)
    wit = cli._resolve_capture_witness(game, tree_witness=_exploding_tree)
    assert wit["actions"] == ["up"]
    assert wit["witness_path"] == beside


# ====================================================================== #
# 2. No demo trajectory -> the tree witness, exactly as before
# ====================================================================== #
def test_no_demo_trajectory_falls_back_to_tree_witness(game):
    """The no-behaviour-change guarantee: absent a demo, the lane re-verifies as it always did."""
    wit = cli._resolve_capture_witness(game, tree_witness=_tree)
    assert wit["witness_source"] == "tree"
    assert wit["actions"] == TREE_WITNESS["actions"]
    assert wit["seed"] == 3
    assert wit["witness_path"] is None           # a fresh verify's witness is memory-only


def test_witness_tree_forces_the_tree_witness_even_with_a_demo(game):
    """--witness tree: the escape hatch back to the old demo, for A/B and debugging."""
    _write_demo(os.path.join(os.path.dirname(game), "demo_trajectory.json"))
    wit = cli._resolve_capture_witness(game, auto_demo=False, tree_witness=_tree)
    assert wit["witness_source"] == "tree"
    assert wit["actions"] == TREE_WITNESS["actions"]


def test_a_demo_for_a_DIFFERENT_game_is_not_picked_up(tmp_path, game):
    """Routing is per-game: a demo trajectory under some other round never leaks into this one."""
    other = tmp_path / "other" / "round_1" / "g3" / "demo_trajectory.json"
    _write_demo(str(other))
    wit = cli._resolve_capture_witness(game, tree_witness=_tree)
    assert wit["witness_source"] == "tree"


# ====================================================================== #
# 3. Explicit --actions still wins
# ====================================================================== #
def test_explicit_actions_beats_the_demo_trajectory(tmp_path, game):
    _write_demo(os.path.join(os.path.dirname(game), "demo_trajectory.json"))
    explicit = tmp_path / "hand.json"
    explicit.write_text(json.dumps({"seed": 11, "actions": ["noop", "noop"]}), encoding="utf-8")
    wit = cli._resolve_capture_witness(game, actions_arg=str(explicit),
                                       tree_witness=_exploding_tree)
    assert wit["actions"] == ["noop", "noop"]
    assert wit["seed"] == 11
    assert wit["witness_path"] == str(explicit)
    # A hand-rolled witness is not the trained policy, and must not claim to be.
    assert wit["witness_source"] == "tree"


def test_explicit_actions_of_a_demo_file_reports_rl(tmp_path, game):
    """--actions <demo_trajectory.json> (the pre-existing manual workflow) still reports rl:
    the payload self-declares its provenance, so the source never depends on HOW it was passed."""
    demo = _write_demo(str(tmp_path / "exported.json"))
    wit = cli._resolve_capture_witness(game, actions_arg=demo, tree_witness=_exploding_tree)
    assert wit["witness_source"] == "rl"
    assert wit["actions"] == DEMO_ACTIONS


# ====================================================================== #
# 4. Degradation: a broken demo must not break a capture; a broken --actions must shout
# ====================================================================== #
@pytest.mark.parametrize("payload", [
    "",                                  # empty file
    "{not json",                         # malformed
    "[]",                                # a bare list, not a witness object
    '{"seed": 1}',                       # no actions
    '{"seed": 1, "actions": []}',        # empty actions
])
def test_unusable_demo_trajectory_degrades_to_tree(game, payload):
    demo = os.path.join(os.path.dirname(game), "demo_trajectory.json")
    with open(demo, "w", encoding="utf-8") as fh:
        fh.write(payload)
    wit = cli._resolve_capture_witness(game, tree_witness=_tree)
    assert wit["witness_source"] == "tree"
    assert wit["actions"] == TREE_WITNESS["actions"]


@pytest.mark.parametrize("payload", ["", "{not json", "[]", '{"seed": 1, "actions": []}'])
def test_unusable_explicit_actions_raises(tmp_path, game, payload):
    p = tmp_path / "bad.json"
    p.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError, match="unusable --actions"):
        cli._resolve_capture_witness(game, actions_arg=str(p), tree_witness=_tree)


def test_missing_explicit_actions_file_raises(tmp_path, game):
    with pytest.raises(ValueError, match="unusable --actions"):
        cli._resolve_capture_witness(game, actions_arg=str(tmp_path / "nope.json"),
                                     tree_witness=_tree)


# ====================================================================== #
# 5. Producer/consumer contract: certify.py's export IS what the lane replays
# ====================================================================== #
def test_exported_demo_round_trips_through_the_router(tmp_path, game):
    """The seam that was never enforced: what export_demo_trajectory writes, the capture lane
    reads back verbatim -- same seed, same actions, and recognised as rl."""
    trajectory = {"seed": 42, "actions": ["thrust", "thrust", "left"], "ticks": 3, "greedy": True}
    demo = export_demo_trajectory(
        trajectory, os.path.join(os.path.dirname(game), "demo_trajectory.json"))
    on_disk = json.loads(open(demo, encoding="utf-8").read())
    assert on_disk["source"] == cli.DEMO_SOURCE_TOKEN     # the token the router keys off
    wit = cli._resolve_capture_witness(game, tree_witness=_exploding_tree)
    assert (wit["seed"], wit["actions"]) == (42, ["thrust", "thrust", "left"])
    assert wit["witness_source"] == "rl"
