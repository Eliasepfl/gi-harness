"""Offline unit tests for CHORD Phase 2 (MultiBinary PPO) that never touch Godot.

They pin the parts of the Phase-2 seam that are pure Python:
  * the gymnasium adapter re-exports MultiBinary(n) in chord mode / Discrete(n) otherwise,
    and passes the raw action through in chord mode vs int()-casting it for Discrete
    (byte-identical to the pre-chord wrapper);
  * the MultiBinary duck-typed space samples an n-bit 0/1 vector;
  * certify.action_histogram's CHORD path (per-key press frequency + the 0/1/2/3+ chord-size
    distribution) and the byte-identical Discrete path;
  * export_demo_trajectory routes a chord/idle demo through wire_actions (never str(a));
  * GdExecutor auto-detects an idle demo (empty-chord tick) for the allow_idle replay.

The live wire mapping + the empty-chord protocol guard are covered in-image
(tests/test_gd_chord.py / test_gd_rl.py), which require a Godot binary.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.rl import certify as C  # noqa: E402
from harness.rl.chord_probe import antiparallel_pairs  # noqa: E402
from harness.rl.env import Discrete, MultiBinary, wrap_gym  # noqa: E402
from harness.verify.chord import chord_from_mask, project_opposition  # noqa: E402
from harness.verify.gd_exec import GdExecutor  # noqa: E402


# ---------------------------------------------------------------- fake env (no Godot)
class _Space:
    def __init__(self, n, shape=None):
        self.n = n
        if shape is not None:
            self.shape = shape


class _FakeEnv:
    """A duck-typed stand-in for GodotServeEnv exposing exactly what wrap_gym reads."""

    def __init__(self, n_actions, *, chord_mode=False, allow_idle=False):
        import numpy as np
        self._np = np
        self.observation_space = _Space(0, shape=(5,))
        self.action_space = _Space(n_actions)
        self.chord_mode = chord_mode
        self.allow_idle = allow_idle
        self.actions = [f"a{i}" for i in range(n_actions)]
        self.horizon = 10
        self.last_action = "UNSET"

    def reset(self, seed=0):
        return self._np.zeros(5, dtype=self._np.float32), {}

    def step(self, action):
        self.last_action = action
        return (self._np.zeros(5, dtype=self._np.float32), 0.0, False, False,
                {"result": None, "latched": {}})

    def close(self):
        pass


# ---------------------------------------------------------------- MultiBinary space
def test_multibinary_space_samples_bit_vector():
    np = pytest.importorskip("numpy")
    mb = MultiBinary(4)
    assert mb.n == 4
    s = mb.sample(np.random.default_rng(0))
    assert s.shape == (4,)
    assert set(np.unique(s)).issubset({0, 1})


# ---------------------------------------------------------------- gym adapter, chord mode
def test_wrapper_exposes_multibinary_in_chord_mode():
    spaces = pytest.importorskip("gymnasium.spaces")
    w = wrap_gym(_FakeEnv(3, chord_mode=True, allow_idle=True))
    assert isinstance(w.action_space, spaces.MultiBinary)
    assert w.action_space.n == 3
    assert w.chord_mode is True and w.allow_idle is True


def test_wrapper_exposes_discrete_by_default():
    spaces = pytest.importorskip("gymnasium.spaces")
    w = wrap_gym(_FakeEnv(3))                 # chord_mode default off
    assert isinstance(w.action_space, spaces.Discrete)
    assert w.action_space.n == 3
    assert w.chord_mode is False


def test_wrapper_step_passes_mask_through_in_chord_mode():
    """Chord: the MultiBinary vector reaches the wrapped env UNCHANGED (the wrapped env owns
    the vector->wire mapping). Discrete: the action is int()-cast, byte-identical to before."""
    pytest.importorskip("gymnasium")
    np = pytest.importorskip("numpy")
    inner = _FakeEnv(3, chord_mode=True)
    w = wrap_gym(inner)
    mask = np.array([1, 0, 1], dtype=np.int8)
    w.step(mask)
    assert list(inner.last_action) == [1, 0, 1]      # passed through, not int()-cast

    inner_d = _FakeEnv(3)
    wd = wrap_gym(inner_d)
    wd.step(np.int64(2))
    assert inner_d.last_action == 2 and isinstance(inner_d.last_action, int)


# ---------------------------------------------------------------- action histogram (chord)
def test_action_histogram_chord_counts_keys_and_sizes():
    eps = [{"actions": ["a0", ["a0", "a1"], [], "a1", ["a0", "a1", "a2"]]}]
    h = C.action_histogram(eps, ["a0", "a1", "a2"], chord=True)
    assert h["per_action"] == {"a0": 3, "a1": 3, "a2": 1}     # per-KEY press counts
    assert h["chord_size"] == {"0": 1, "1": 2, "2": 1, "3+": 1}
    assert h["total_ticks"] == 5
    assert h["total_key_presses"] == 7
    assert h["mean_chord_size"] == pytest.approx(1.4)
    assert h["chord_size_frac"]["0"] == pytest.approx(0.2)


def test_action_histogram_discrete_path_byte_identical():
    """chord=False keeps the exact pre-Phase-2 shape (per_action sums to total_ticks)."""
    hd = C.action_histogram([{"actions": ["up", "up", "right"]}], ["up", "right"])
    assert hd == {"per_action": {"up": 2, "right": 1}, "total_ticks": 3}


def test_action_histogram_chord_with_axes_aggregates_keys():
    eps = [{"actions": [["thrust_up", "thrust_forward"], "thrust_up"]}]
    h = C.action_histogram(eps, ["thrust_up", "thrust_forward"], with_axes=True, chord=True)
    # 2 thrust_up presses (vertical) + 1 thrust_forward (forward_brake); axes sum to key presses
    assert h["per_axis"]["vertical"] == 2
    assert h["per_axis"]["forward_brake"] == 1
    assert sum(h["per_axis"].values()) == h["total_key_presses"] == 3


# ---------------------------------------------------------------- demo export (chord/idle)
def test_export_demo_trajectory_preserves_chords_and_idle():
    p = os.path.join(tempfile.mkdtemp(), "demo.json")
    C.export_demo_trajectory(
        {"seed": 7, "actions": ["up", ["right", "up"], []], "ticks": 3, "greedy": True}, p)
    d = json.load(open(p, encoding="utf-8"))
    # chord sorted, singleton stays a str, idle is [] — NOT flattened by str(a).
    assert d["actions"] == ["up", ["right", "up"], []]
    assert d["seed"] == 7 and d["source"] == "g3_demo"


def test_export_demo_trajectory_legacy_singleton_unchanged():
    p = os.path.join(tempfile.mkdtemp(), "demo.json")
    C.export_demo_trajectory(
        {"seed": 0, "actions": ["up", "right", "up"], "ticks": 3, "greedy": True}, p)
    d = json.load(open(p, encoding="utf-8"))
    assert d["actions"] == ["up", "right", "up"]


# ---------------------------------------------------------------- gd_exec idle auto-detect
def test_gdexecutor_detects_idle_demo():
    assert GdExecutor._episodes_have_idle([{"actions": ["a", [], "b"]}]) is True
    assert GdExecutor._episodes_have_idle([{"actions": ["a", ["b", "c"]]}]) is False
    assert GdExecutor._episodes_have_idle([]) is False


# ============================================================ CONTRADICTORY-CHORD projection
# Effect vectors modelling a 4-verb 2D mover: up/down are antiparallel, left/right are
# antiparallel; up vs left/right are orthogonal (not opposed). Actions ordered [up,down,left,right].
_UP, _DOWN, _LEFT, _RIGHT = (0, 1, 2, 3)
_MOVER_VECS = [(0.0, 1.0), (0.0, -1.0), (-1.0, 0.0), (1.0, 0.0)]


def test_antiparallel_pairs_discovers_opposites_mechanically():
    """Near-antiparallel effect vectors (cosine < -0.9, comparable magnitude) are discovered;
    orthogonal ones are not. Derived from the VECTORS only — no action names involved."""
    pairs = antiparallel_pairs(_MOVER_VECS)
    assert (_UP, _DOWN) in pairs and (_LEFT, _RIGHT) in pairs
    # up/left, up/right, down/left, down/right are orthogonal -> never paired
    assert (_UP, _LEFT) not in pairs and (_UP, _RIGHT) not in pairs
    assert len(pairs) == 2


def test_antiparallel_pairs_rejects_incomparable_magnitude():
    """A strong action is NOT paired with a weak near-opposite one (magnitude ratio guard)."""
    vecs = [(0.0, 10.0), (0.0, -1.0)]      # antiparallel but 10x magnitude gap
    assert antiparallel_pairs(vecs, mag_ratio=3.0) == []
    # within the ratio, the same direction pair IS discovered
    assert antiparallel_pairs([(0.0, 2.0), (0.0, -1.0)], mag_ratio=3.0) == [(0, 1)]


def test_antiparallel_pairs_excludes_zero_effect_actions():
    """An action with ~no measured effect (a non-mover) is never contradictory."""
    vecs = [(0.0, 0.0), (0.0, 1.0), (0.0, -1.0)]
    assert antiparallel_pairs(vecs) == [(1, 2)]


def test_project_opposition_drops_both_when_both_pressed():
    pairs = [(_UP, _DOWN), (_LEFT, _RIGHT)]
    # up+down pressed -> both dropped; left survives
    assert project_opposition([1, 1, 1, 0], pairs) == [0, 0, 1, 0]
    # only one of a pair pressed -> untouched
    assert project_opposition([1, 0, 0, 1], pairs) == [1, 0, 0, 1]
    # both pairs pressed -> all four drop (the mb degenerate 'left+right+up+down' collapses)
    assert project_opposition([1, 1, 1, 1], pairs) == [0, 0, 0, 0]
    # a non-opposing multi-key chord is untouched
    assert project_opposition([1, 0, 0, 1], [(_UP, _DOWN)]) == [1, 0, 0, 1]


def test_chord_from_mask_applies_projection():
    acts = ["up", "down", "left", "right"]
    pairs = [(_UP, _DOWN), (_LEFT, _RIGHT)]
    # left+right (the mb degenerate) -> both drop -> nothing pressed -> idle [] (allow_empty)
    assert chord_from_mask([0, 0, 1, 1], acts, allow_empty=True, oppose_pairs=pairs) == []
    # up + left+right -> the opposing pair drops, 'up' survives as a singleton str
    assert chord_from_mask([1, 0, 1, 1], acts, allow_empty=True, oppose_pairs=pairs) == "up"
    # up + right (non-opposing) -> a real 2-key chord, sorted
    assert chord_from_mask([1, 0, 0, 1], acts, oppose_pairs=pairs) == ["right", "up"]
    # no pairs given -> byte-identical to the un-projected mapping
    assert chord_from_mask([0, 0, 1, 1], acts, oppose_pairs=None) == ["left", "right"]
