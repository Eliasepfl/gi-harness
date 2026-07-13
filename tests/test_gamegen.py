"""Tests for module G (gamegen: open-ended v2 generator), WITHOUT any network.

`harness.gameverify.verify_game` is mocked (via sys.modules) to simulate
pass/fail reports; the anthropic backend is short-circuited by monkeypatch.
"""
from __future__ import annotations

import ast
import re
import sys
import types

import httpx
import anthropic
import pytest

from harness import gamegen as GG


# --- Helpers ------------------------------------------------------------------

def _no_import(source):
    """No import / from ... import statement in the emitted game code."""
    return re.search(r"(?m)^\s*(import|from)\s", source) is None


def _exec_module(source):
    ns = {}
    exec(compile(source, "<game>", "exec"), ns)
    return ns


def _install_gameverify(monkeypatch, fn):
    mod = types.ModuleType("harness.gameverify")
    mod.verify_game = fn
    monkeypatch.setitem(sys.modules, "harness.gameverify", mod)


def _remove_gameverify(monkeypatch):
    monkeypatch.delitem(sys.modules, "harness.gameverify", raising=False)


_REQUIRED = ("TITLE", "PROMPT", "ACTIONS", "build", "act", "success", "checkpoints")


class FakeWorld:
    """Minimal stand-in for harness.world.World: records add() calls and answers
    query()/touching() from that static t=0 state. Enough to run build() and the
    pure predicates without physics (self-contained; does not depend on module E)."""

    def __init__(self, seed=0):
        import random
        self._rng = random.Random(seed)
        self._ent = {}
        self._controlled = None
        self._flags = {}
        self.gravity = (0.0, -900.0)

    # construction
    def add(self, name, shape="box", *, pos, size=None, radius=None, a=None,
            b=None, vertices=None, mass=1.0, static=False, sensor=False,
            friction=0.7, elasticity=0.3, velocity=(0, 0), angle=0.0,
            locked_rotation=False):
        x, y = float(pos[0]), float(pos[1])
        if shape == "box" and size is not None:
            hw, hh = size[0] / 2.0, size[1] / 2.0
            bbox = [x - hw, y - hh, x + hw, y + hh]
        elif shape == "circle" and radius is not None:
            r = float(radius)
            bbox = [x - r, y - r, x + r, y + r]
        elif shape == "segment" and a is not None and b is not None:
            xs, ys = (a[0], b[0]), (a[1], b[1])
            bbox = [x + min(xs), y + min(ys), x + max(xs), y + max(ys)]
        else:  # poly or missing geometry: zero extent is enough for these tests
            bbox = [x, y, x, y]
        self._ent[name] = {
            "pos": [x, y], "bbox": bbox, "shape": shape, "static": static,
            "sensor": sensor, "vel": [float(velocity[0]), float(velocity[1])],
            "angle": angle, "angular_vel": 0.0,
        }
        return name

    def remove(self, name):
        self._ent.pop(name, None)

    def pin(self, a, b, anchor_a=None, anchor_b=None):
        pass

    def pivot(self, a, b, point):
        pass

    def spring(self, a, b, rest_length, stiffness, damping,
               anchor_a=None, anchor_b=None):
        pass

    def set_gravity(self, gx, gy):
        self.gravity = (gx, gy)

    def control(self, name):
        self._controlled = name

    # dynamics
    def impulse(self, name, vec):
        pass

    def force(self, name, vec):
        pass

    def set_velocity(self, name, vec):
        self._ent[name]["vel"] = [float(vec[0]), float(vec[1])]

    def set_flag(self, key, value):
        self._flags[key] = value

    def flag(self, key, default=None):
        return self._flags.get(key, default)

    def on_contact(self, a, b, flag, once=True):
        pass

    @property
    def rng(self):
        return self._rng

    @property
    def steps(self):
        return 0

    # queries
    def entities(self):
        return list(self._ent)

    def query(self, name):
        e = self._ent[name]
        return dict(e, controlled=(name == self._controlled))

    def contacts(self, a, b):
        return False

    def touching(self, name):
        return []

    def grounded(self, name):
        return False

    def in_bounds(self, name, margin=0.0):
        return True

    def penetration_depth(self, a, b):
        return 0.0


# --- Built-in v2 games: shape and validity -----------------------------------

@pytest.mark.parametrize("name", ["drift", "drop"])
def test_builtin_game_is_valid_module(name):
    src = GG._TEMPLATE_GAMES[name]
    # Parses without error.
    ast.parse(src)
    # No imports whatsoever (only `world` is used).
    assert _no_import(src)
    ns = _exec_module(src)
    # Required §2 symbols present.
    for sym in _REQUIRED:
        assert sym in ns, (name, sym)
    assert callable(ns["build"]) and callable(ns["act"]) and callable(ns["success"])
    # ACTIONS is a list of 2..8 short strings.
    actions = ns["ACTIONS"]
    assert isinstance(actions, list) and 2 <= len(actions) <= 8
    assert all(isinstance(a, str) for a in actions)
    # Exactly one world.control(...) call in the source.
    assert src.count("world.control(") == 1


def test_builtin_games_are_two_and_distinct():
    assert set(GG._TEMPLATE_GAMES) == {"drift", "drop"}
    assert GG._DRIFT != GG._DROP
    # "drop" declares a lose condition; "drift" does not.
    assert "def failure" in GG._DROP
    assert "def failure" not in GG._DRIFT


def test_template_selection_keywords():
    assert GG._select_template("catch the falling ball") == "drop"
    assert GG._select_template("a puck on ice") == "drift"
    assert GG._select_template("") == "drift"


@pytest.mark.parametrize("name", ["drift", "drop"])
def test_builtin_checkpoints_conform_at_t0(name):
    # v2.1: checkpoints() is a required symbol — 1..6 ordered snake_case
    # milestones, pure, ALL False at t=0. Exercised on a stub world.
    ns = _exec_module(GG._TEMPLATE_GAMES[name])
    fw = FakeWorld(seed=0)
    ns["build"](fw)
    cps = ns["checkpoints"](fw)
    assert isinstance(cps, dict)
    assert 1 <= len(cps) <= 6
    for key, val in cps.items():
        assert re.fullmatch(r"[a-z][a-z0-9_]*", key), key   # snake_case
        assert val is False, (name, key)                     # all False at t=0
    # Pure: calling twice gives the same answer, same keys, same order.
    again = ns["checkpoints"](fw)
    assert list(again) == list(cps)
    assert again == cps
    # success is also False at t=0 on the stub world.
    assert ns["success"](fw) is False


def test_builtin_checkpoints_are_not_success_restatements():
    # Milestones must be stages, not copies of the win predicate: at least one
    # game state exists where a milestone holds but success does not.
    ns = _exec_module(GG._TEMPLATE_GAMES["drift"])
    fw = FakeWorld(seed=0)
    ns["build"](fw)
    # Move the puck past the midline but far from the pad.
    fw._ent["puck"]["pos"] = [420.0, 150.0]
    fw._ent["puck"]["bbox"] = [404.0, 134.0, 436.0, 166.0]
    cps = ns["checkpoints"](fw)
    assert cps["moved_off_start"] is True
    assert cps["crossed_midline"] is True
    assert ns["success"](fw) is False


# --- The open prompt ----------------------------------------------------------

def test_prompt_teaches_the_world_api():
    sp = GG._SYSTEM_PROMPT
    # Construction / dynamics / queries are all referenced.
    for tok in ("world.add", "world.control", "world.impulse", "world.force",
                "world.set_gravity", "world.rng", "world.steps", "world.query",
                "world.touching", "world.spring", "world.pivot"):
        assert tok in sp, tok
    # The §2 module format is taught.
    for tok in ("TITLE", "PROMPT", "ACTIONS", "def build", "def act",
                "def on_step", "def success", "def failure", "def checkpoints"):
        assert tok in sp, tok


def test_prompt_requires_checkpoints():
    sp = GG._SYSTEM_PROMPT
    # v2.1: the checkpoints contract is taught in full.
    assert "checkpoints(world) -> dict[str, bool]" in sp
    assert "1 to 6" in sp
    assert "snake_case" in sp
    assert "False at t=0" in sp
    # The motivating line is present (whitespace-normalised: the prompt wraps).
    flat = " ".join(sp.lower().split())
    assert ("milestones are how the harness will tell you exactly where your "
            "game is stuck if it fails") in flat
    assert "not restatements of success" in flat
    # The DESIGN block lists the milestones.
    assert "Milestones:" in sp
    # The stub shows a one-line checkpoints example.
    m = re.search(r"```python\s*\n(.*?)```", sp, re.DOTALL)
    assert m, "stub missing"
    assert "def checkpoints" in m.group(1)
    assert '"halfway"' in m.group(1)


def test_prompt_is_open_not_a_v1_worked_example():
    sp = GG._SYSTEM_PROMPT
    # No trace of the v1 closed prompt (SceneSDK genre-deciding vocabulary).
    for v1 in ("SceneSDK", "add_ground", "spawn_agent", "add_zone",
               "add_platform", "SCENE_DESCRIPTION", "AVAILABLE_ACTIONS",
               "get_success", "build_scene"):
        assert v1 not in sp, ("leaked v1 token", v1)
    # The stub is explicitly marked structure-only, not a design to copy.
    assert "Structure-only" in sp
    assert "do NOT imitate" in sp
    # Variety is invited, not a fixed genre.
    assert "do NOT default to a platformer" in sp
    # Does NOT expose harness-side methods.
    for hidden in ("world.step(", "world.snapshot", "world.teleport",
                   "world.kinetic_energy", "world.events"):
        assert hidden not in sp, ("exposed harness method", hidden)


# --- Extraction ---------------------------------------------------------------

def test_extract_code_first_python_block():
    text = "prose\n```python\nA = 1\n```\nmore\n```python\nB = 2\n```"
    assert GG._extract_code(text).strip() == "A = 1"


def test_extract_design_block():
    text = ("DESIGN\nTheme: puck\nEntities: a, b\nActions: go\n"
            "Win / Lose: reach pad\n```python\nTITLE = 'x'\n```")
    design = GG._extract_design(text)
    assert design.startswith("DESIGN")
    assert "Theme: puck" in design
    assert "```" not in design
    assert "TITLE" not in design


# --- Repair loop (verify_game mocked) ----------------------------------------

def test_repair_fail_then_pass_completed(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_verify(path):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"passed": False, "failure_class": "GOAL_ERROR",
                    "hint": "goal never reached", "witness": None}
        return {"passed": True, "failure_class": None, "hint": "",
                "witness": {"seed": 1, "actions": ["left"], "ticks": 7}}

    _install_gameverify(monkeypatch, fake_verify)

    res = GG.generate_game("a game about ice", out_dir=str(tmp_path),
                           backend="template", max_repairs=4)

    assert res["verdict"] == "COMPLETED"
    assert res["backend"] == "template"
    assert len(res["attempts"]) == 2
    assert calls["n"] == 2
    assert res["game_path"] is not None
    assert res["design"].startswith("DESIGN")


def test_repair_permanent_goal_error(tmp_path, monkeypatch):
    def fake_verify(path):
        return {"passed": False, "failure_class": "GOAL_ERROR",
                "hint": "success true at t=0", "witness": None}

    _install_gameverify(monkeypatch, fake_verify)

    res = GG.generate_game("catch the ball", out_dir=str(tmp_path),
                           backend="template", max_repairs=3)

    assert res["verdict"] == "GOAL_ERROR"
    # 1 initial attempt + 3 repairs.
    assert len(res["attempts"]) == 4


def test_env_errors_capped_and_discarded(tmp_path, monkeypatch):
    # ENV_ERROR (G0 load/build failures): hard cap at 5, regardless of max_repairs.
    def fake_verify(path):
        return {"passed": False, "failure_class": "ENV_ERROR",
                "hint": "module failed to load", "witness": None}

    _install_gameverify(monkeypatch, fake_verify)

    res = GG.generate_game("a puck on ice", out_dir=str(tmp_path),
                           backend="template", max_repairs=20)

    assert res["verdict"] == "ENV_ERROR"
    assert len(res["attempts"]) == 5


def test_unsolved_verdict(tmp_path, monkeypatch):
    def fake_verify(path):
        return {"passed": False, "failure_class": "UNSOLVED",
                "hint": "0/40 episodes reached success", "witness": None}

    _install_gameverify(monkeypatch, fake_verify)

    res = GG.generate_game("a puck on ice", out_dir=str(tmp_path),
                           backend="template", max_repairs=2)

    assert res["verdict"] == "UNSOLVED"
    assert len(res["attempts"]) == 3


def test_verification_unavailable_partial(tmp_path, monkeypatch):
    _remove_gameverify(monkeypatch)
    monkeypatch.setattr(GG, "_verify", lambda p: None)

    res = GG.generate_game("a puck on ice", out_dir=str(tmp_path),
                           backend="template", max_repairs=4)

    assert res["verdict"] == "PARTIAL"
    assert len(res["attempts"]) == 1
    assert res["game_path"] is not None


# --- Repair message: UNSOLVED hint injection ---------------------------------

def test_unsolved_hint_in_repair_message():
    msg = GG._repair_user_msg({"failure_class": "UNSOLVED", "hint": "0/40"})
    assert "no random rollout reached success" in msg
    assert "0/40" in msg


def test_progress_diagnosis_prepended_to_repair_message():
    # v2.1: when the report carries a checkpoint diagnosis, the stuck-boundary
    # sentence leads the repair message and the full JSON stays attached.
    report = {
        "failure_class": "UNSOLVED",
        "hint": ("34/40 episodes reached 'crossed_midline', none reached "
                 "'entered_upper_half' - the game is stuck between "
                 "crossed_midline and entered_upper_half"),
        "progress": {"reach_counts": {"moved_off_start": 40,
                                      "crossed_midline": 34,
                                      "entered_upper_half": 0},
                     "stuck_after": "crossed_midline"},
    }
    msg = GG._repair_user_msg(report)
    assert msg.startswith("Solvability diagnosis: 34/40 episodes reached")
    assert "Focus the fix on the segment between the named milestones." in msg
    # The UNSOLVED hint and full report are still attached.
    assert "no random rollout reached success" in msg
    assert "reach_counts" in msg and "stuck_after" in msg


def test_no_diagnosis_without_progress():
    msg = GG._repair_user_msg({"failure_class": "GOAL_ERROR",
                               "hint": "success true at t=0", "progress": None})
    assert "Solvability diagnosis" not in msg
    assert msg.startswith("The previous game failed verification.")


def test_unsolved_hint_injected_into_llm_conversation(tmp_path, monkeypatch):
    # Fake LLM records every messages list it is handed.
    seen = {"first_user": None, "repair_user": None}
    verify_calls = {"n": 0}
    code = "```python\n" + GG._DRIFT + "\n```"

    def fake_complete(client, system, messages):
        # Latest user message content, per call.
        last_user = [m for m in messages if m["role"] == "user"][-1]["content"]
        if seen["first_user"] is None:
            seen["first_user"] = last_user
        else:
            seen["repair_user"] = last_user
        return "DESIGN\nTheme: t\n" + code

    def fake_verify(path):
        verify_calls["n"] += 1
        if verify_calls["n"] == 1:
            return {"passed": False, "failure_class": "UNSOLVED",
                    "hint": "0/40 episodes", "witness": None,
                    "progress": {"reach_counts": {"moved_off_start": 12},
                                 "stuck_after": "moved_off_start"}}
        return {"passed": True, "failure_class": None, "hint": "", "witness": {}}

    monkeypatch.setattr(GG, "_make_client", lambda: object())
    monkeypatch.setattr(GG, "_llm_complete", fake_complete)
    _install_gameverify(monkeypatch, fake_verify)

    res = GG.generate_game("invent something", out_dir=str(tmp_path),
                           backend="anthropic", max_repairs=4)

    assert res["backend"] == "anthropic"
    assert res["verdict"] == "COMPLETED"
    assert len(res["attempts"]) == 2
    # The repair turn carried the UNSOLVED hint + the JSON report back to the model.
    assert seen["repair_user"] is not None
    assert "no random rollout reached success" in seen["repair_user"]
    assert "UNSOLVED" in seen["repair_user"]
    # v2.1: the checkpoint diagnosis leads the repair turn.
    assert seen["repair_user"].startswith("Solvability diagnosis:")
    assert "stuck_after" in seen["repair_user"]


# --- Backend selection --------------------------------------------------------

def test_auto_falls_back_to_template_when_anthropic_missing(tmp_path, monkeypatch):
    # Simulate the ImportError path: the package is not present.
    monkeypatch.setattr(GG, "anthropic", None)
    _install_gameverify(monkeypatch, lambda p: {"passed": True,
                                                "failure_class": None, "hint": "",
                                                "witness": {}})

    res = GG.generate_game("a puck on ice", out_dir=str(tmp_path),
                           backend="auto", max_repairs=4)

    assert res["backend"] == "template"
    assert res["verdict"] == "COMPLETED"
    assert "note" in res and "anthropic unavailable" in res["note"]


def test_auto_falls_back_on_connection_error(tmp_path, monkeypatch):
    def boom():
        raise anthropic.APIConnectionError(
            request=httpx.Request("GET", "http://localhost"))

    monkeypatch.setattr(GG, "_make_client", boom)
    _install_gameverify(monkeypatch, lambda p: {"passed": True,
                                                "failure_class": None, "hint": "",
                                                "witness": {}})

    res = GG.generate_game("catch the ball", out_dir=str(tmp_path),
                           backend="auto", max_repairs=4)

    assert res["backend"] == "template"
    assert res["verdict"] == "COMPLETED"


def test_anthropic_backend_uses_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(GG, "_make_client", lambda: object())
    monkeypatch.setattr(
        GG, "_llm_complete",
        lambda client, system, messages:
            "DESIGN\nTheme: t\nActions: go\n```python\n" + GG._DROP + "\n```")
    _install_gameverify(monkeypatch, lambda p: {"passed": True,
                                                "failure_class": None, "hint": "",
                                                "witness": {}})

    res = GG.generate_game("anything", out_dir=str(tmp_path),
                           backend="anthropic", max_repairs=4)

    assert res["backend"] == "anthropic"
    assert res["verdict"] == "COMPLETED"
    assert res["design"].startswith("DESIGN")
    # The generated file holds the extracted python module, not the DESIGN prose.
    with open(res["game_path"], encoding="utf-8") as f:
        written = f.read()
    assert "TITLE" in written and "DESIGN" not in written
