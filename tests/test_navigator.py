"""Navigator tests — FakeSDK + inline scene, no pymunk or real SDK.

Checks: the greedy policy emits valid actions, stops on simulated success,
respects max_steps; observe_text produces valid JSON; scene loading rejects a
source containing an import (if the sandbox is available, else skip).
"""

import json

import pytest

from harness.legacy import navigator
from harness.legacy.navigator import (
    _GreedyPolicy,
    _load_scene,
    _run_episode,
    navigate,
    observe_text,
)

VALID_ACTIONS = {"left", "right", "jump", "noop"}


# ==========================================================================
#  FakeSDK — 1D horizontal toy model, no engine dependency
# ==========================================================================
class FakeSDK:
    """Fake SDK: the agent slides left/right, the zone is fixed."""

    def __init__(self, agent_x=100.0, zone_x=600.0, with_ball=False):
        self._ents = {
            "ground": {"pos": [400.0, 0.0], "vel": [0.0, 0.0],
                       "body_type": "static", "is_agent": False,
                       "bbox": [0, -5, 800, 0]},
            "agent": {"pos": [agent_x, 40.0], "vel": [0.0, 0.0],
                      "body_type": "dynamic", "is_agent": True,
                      "bbox": [agent_x - 12, 22, agent_x + 12, 58]},
            "zone_goal": {"pos": [zone_x, 40.0], "vel": [0.0, 0.0],
                          "body_type": "static", "is_agent": False,
                          "bbox": [zone_x - 30, 10, zone_x + 30, 70]},
        }
        if with_ball:
            self._ents["ball0"] = {"pos": [300.0, 40.0], "vel": [0.0, 0.0],
                                   "body_type": "dynamic", "is_agent": False,
                                   "bbox": [285, 25, 315, 55]}
        self._flags = {}
        self.step_count = 0
        self.applied = []

    # ---- introspection ----
    def list_entities(self):
        return list(self._ents)

    def query(self, name):
        return dict(self._ents[name])

    def events(self):
        return [{"type": "flag_set", "key": k, "value": v, "step": self.step_count}
                for k, v in self._flags.items()]

    def get_flag(self, key, default=None):
        return self._flags.get(key, default)

    def set_flag(self, key, value):
        self._flags[key] = value

    # ---- dynamics ----
    def apply(self, action):
        self.applied.append(action)
        vx = {"left": -6.0, "right": 6.0}.get(action, 0.0)
        self._ents["agent"]["vel"][0] = vx

    def step(self, n=1):
        self.step_count += n
        a = self._ents["agent"]
        a["pos"][0] += a["vel"][0] * n
        half = 12
        a["bbox"] = [a["pos"][0] - half, 22, a["pos"][0] + half, 58]


def success_when_agent_right(threshold=590.0):
    """Inline get_success: the agent has reached the zone on the right."""
    def _pred(sdk):
        return sdk.query("agent")["pos"][0] >= threshold
    return _pred


# ==========================================================================
#  Greedy policy — valid actions
# ==========================================================================
def test_greedy_emits_only_valid_actions():
    sdk = FakeSDK(agent_x=100.0, zone_x=600.0)
    pol = _GreedyPolicy(seed=1)
    for _ in range(50):
        act = pol.decide(sdk)
        assert act in VALID_ACTIONS
        sdk.apply(act)
        sdk.step(6)


def test_greedy_moves_toward_zone_on_the_right():
    sdk = FakeSDK(agent_x=100.0, zone_x=600.0)
    pol = _GreedyPolicy(seed=0)
    # first decision: the agent is left of the zone -> must go right
    assert pol.decide(sdk) == "right"


def test_greedy_moves_left_when_zone_is_left():
    sdk = FakeSDK(agent_x=700.0, zone_x=100.0)
    pol = _GreedyPolicy(seed=0)
    assert pol.decide(sdk) == "left"


# ==========================================================================
#  Episode loop — success, timeout, actions
# ==========================================================================
def test_episode_stops_on_success():
    sdk = FakeSDK(agent_x=100.0, zone_x=600.0)
    res = _run_episode(sdk, success_when_agent_right(590.0),
                       policy="greedy", max_steps=1200)
    assert res["success"] is True
    assert res["reason"] == "goal"
    assert res["steps"] < 1200
    assert all(a in VALID_ACTIONS for a in res["actions"])


def test_episode_respects_max_steps_on_impossible_goal():
    sdk = FakeSDK(agent_x=100.0, zone_x=600.0)
    res = _run_episode(sdk, lambda s: False, policy="greedy", max_steps=120)
    assert res["success"] is False
    assert res["reason"] == "timeout"
    assert res["steps"] <= 120


def test_episode_reports_error_on_step_explosion():
    sdk = FakeSDK()

    def boom(n=1):
        raise FloatingPointError("NaN in the state")

    sdk.step = boom
    res = _run_episode(sdk, lambda s: False, policy="greedy", max_steps=120)
    assert res["success"] is False
    assert res["reason"] == "error"


def test_push_task_is_recognised_and_runs():
    sdk = FakeSDK(agent_x=100.0, zone_x=600.0, with_ball=True)
    # simply: the loop runs without raising and respects max_steps
    res = _run_episode(sdk, lambda s: False, policy="greedy", max_steps=60)
    assert res["reason"] == "timeout"
    assert all(a in VALID_ACTIONS for a in res["actions"])


# ==========================================================================
#  llm policy — stub
# ==========================================================================
def test_llm_policy_is_stub():
    with pytest.raises(NotImplementedError):
        _run_episode(FakeSDK(), lambda s: False, policy="llm")
    with pytest.raises(NotImplementedError):
        navigate("whatever.py", policy="llm")


def test_unknown_policy_raises():
    with pytest.raises(ValueError):
        _run_episode(FakeSDK(), lambda s: False, policy="zigzag")


# ==========================================================================
#  observe_text — valid, structured JSON
# ==========================================================================
def test_observe_text_is_valid_json():
    sdk = FakeSDK()
    sdk.set_flag("touched", True)
    txt = observe_text(sdk)
    data = json.loads(txt)
    assert set(data) == {"entities", "flags", "step"}
    assert "agent" in data["entities"]
    agent = data["entities"]["agent"]
    assert set(agent) == {"pos", "vel", "is_agent", "body_type"}
    assert agent["is_agent"] is True
    assert isinstance(agent["pos"], list) and len(agent["pos"]) == 2
    assert data["flags"].get("touched") is True


def test_observe_text_is_compact():
    txt = observe_text(FakeSDK())
    assert ", " not in txt  # compact separators


# ==========================================================================
#  Scene loading
# ==========================================================================
INLINE_SCENE = '''
SCENE_DESCRIPTION = "reach the zone"
AVAILABLE_ACTIONS = ["left", "right", "jump", "noop"]

def build_scene(sdk):
    sdk.add_ground()
    sdk.spawn_agent((100, 40))
    sdk.add_zone("zone_goal", (600, 40), (60, 60))

def get_success(sdk):
    return sdk.query("agent")["pos"][0] >= 590
'''

BAD_SCENE = '''
import os
def build_scene(sdk):
    pass
def get_success(sdk):
    return True
'''


def test_load_scene_valid(tmp_path):
    p = tmp_path / "scene_ok.py"
    p.write_text(INLINE_SCENE, encoding="utf-8")
    mod = _load_scene(str(p))
    assert callable(mod.build_scene)
    assert callable(mod.get_success)
    assert mod.SCENE_DESCRIPTION == "reach the zone"


def test_load_scene_missing_function(tmp_path):
    p = tmp_path / "scene_bad.py"
    p.write_text("def build_scene(sdk):\n    pass\n", encoding="utf-8")
    with pytest.raises(navigator.SceneError):
        _load_scene(str(p))


def test_load_scene_rejects_import(tmp_path):
    try:
        import harness.core.sandbox  # noqa: F401
    except Exception:
        pytest.skip("harness.core.sandbox absent (module B in progress) — import rejection not testable")
    p = tmp_path / "scene_import.py"
    p.write_text(BAD_SCENE, encoding="utf-8")
    with pytest.raises(navigator.SceneError):
        _load_scene(str(p))
