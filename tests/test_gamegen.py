"""Tests for module G (gamegen: open-ended v2 generator), WITHOUT any network.

`harness.gameverify.verify_game` is mocked (via sys.modules) to simulate
pass/fail reports; the anthropic backend is short-circuited by monkeypatch.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import types

import httpx
import anthropic
import pytest

from harness.gen import gamegen as GG


# --- Helpers ------------------------------------------------------------------

def _no_import(source):
    """No import / from ... import statement in the emitted game code."""
    return re.search(r"(?m)^\s*(import|from)\s", source) is None


def _exec_module(source):
    ns = {}
    exec(compile(source, "<game>", "exec"), ns)
    return ns


def _install_gameverify(monkeypatch, fn):
    mod = types.ModuleType("harness.verify.gameverify")
    mod.verify_game = fn
    monkeypatch.setitem(sys.modules, "harness.verify.gameverify", mod)


def _remove_gameverify(monkeypatch):
    monkeypatch.delitem(sys.modules, "harness.verify.gameverify", raising=False)


@pytest.fixture(autouse=True)
def _ledger_to_tmp(tmp_path_factory, monkeypatch):
    """Redirect the telemetry ledger away from the real repo for every test.

    Uses its own temp dir (not the test's tmp_path) so tests that treat
    tmp_path as out_dir see no foreign files."""
    ledger_dir = tmp_path_factory.mktemp("ledger")
    monkeypatch.setattr(GG, "_LEDGER_PATH", str(ledger_dir / "test_ledger.jsonl"))


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
    # Also make the openrouter link in the auto chain unavailable, so no real
    # network call is made and auto ends on templates.
    monkeypatch.setattr(GG, "requests", None)
    _install_gameverify(monkeypatch, lambda p: {"passed": True,
                                                "failure_class": None, "hint": "",
                                                "witness": {}})

    res = GG.generate_game("a puck on ice", out_dir=str(tmp_path),
                           backend="auto", max_repairs=4)

    assert res["backend"] == "template"
    assert res["verdict"] == "COMPLETED"
    assert "note" in res and "anthropic unavailable" in res["note"]
    # The auto chain also records the openrouter fallback.
    assert "openrouter unavailable" in res["note"]


def test_auto_falls_back_on_connection_error(tmp_path, monkeypatch):
    def boom():
        raise anthropic.APIConnectionError(
            request=httpx.Request("GET", "http://localhost"))

    monkeypatch.setattr(GG, "_make_client", boom)
    monkeypatch.setattr(GG, "requests", None)  # openrouter unavailable too
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


# =============================================================================
#  OpenRouter backend (all mocked — NEVER a real key or a real request)
# =============================================================================
# The real key is never used here; tests inject an obvious fake value.
_FAKE_KEY = "sk-or-v1-FAKEKEYFORTESTS"
_FAKE_MODEL = "vendor/fake-model:free"


class _FakeResp:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, status_code, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class _FakeRequests:
    """Stand-in for the `requests` module: records .post calls, returns canned
    responses in order (a single response is reused for every call)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json,
                           "timeout": timeout})
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)


def _fake_secrets(monkeypatch, key=_FAKE_KEY, model=_FAKE_MODEL):
    monkeypatch.setattr(GG, "_resolve_secret",
                        lambda name: {"OPENROUTER_API_KEY": key,
                                      "OPENROUTER_MODEL": model}.get(name))


def _chat(content):
    return {"choices": [{"message": {"content": content}}]}


# --- Secret resolution: os.environ vs env.py ---------------------------------

def test_secret_environ_wins_over_env_py(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-ENVIRON-WINS")
    # env.py would offer a different value, but os.environ must take precedence.
    fake_env = types.SimpleNamespace(OPENROUTER_API_KEY="sk-or-FROM-ENVPY",
                                     OPENROUTER_MODEL="envpy/model")
    monkeypatch.setattr(GG, "_load_env_module", lambda: fake_env)
    assert GG._resolve_secret("OPENROUTER_API_KEY") == "sk-or-ENVIRON-WINS"


def test_secret_falls_back_to_env_py(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    fake_env = types.SimpleNamespace(OPENROUTER_API_KEY="sk-or-FROM-ENVPY",
                                     OPENROUTER_MODEL="envpy/model")
    monkeypatch.setattr(GG, "_load_env_module", lambda: fake_env)
    assert GG._resolve_secret("OPENROUTER_API_KEY") == "sk-or-FROM-ENVPY"
    assert GG._resolve_secret("OPENROUTER_MODEL") == "envpy/model"


def test_secret_none_when_env_py_missing(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(GG, "_load_env_module", lambda: None)  # env.py absent
    assert GG._resolve_secret("OPENROUTER_API_KEY") is None


def test_secret_none_when_name_absent_in_env_py(monkeypatch):
    monkeypatch.delenv("NOPE_KEY", raising=False)
    monkeypatch.setattr(GG, "_load_env_module",
                        lambda: types.SimpleNamespace(OTHER="x"))
    assert GG._resolve_secret("NOPE_KEY") is None


# --- _openrouter_complete: request shape, retries, errors --------------------

def test_openrouter_complete_success_request_shape(monkeypatch):
    _fake_secrets(monkeypatch)
    fake = _FakeRequests([_FakeResp(200, _chat("hi there"))])
    monkeypatch.setattr(GG, "requests", fake)

    out = GG._openrouter_complete("SYS", [{"role": "user", "content": "seed"}])

    assert out == "hi there"
    call = fake.calls[0]
    assert call["url"] == GG._OPENROUTER_URL
    assert call["headers"]["Authorization"] == f"Bearer {_FAKE_KEY}"
    assert call["timeout"] == GG._OPENROUTER_TIMEOUT
    body = call["json"]
    assert body["model"] == _FAKE_MODEL
    assert body["max_tokens"] == GG._OPENROUTER_MAX_TOKENS
    # System prompt is prepended before the caller's messages.
    assert body["messages"][0] == {"role": "system", "content": "SYS"}
    assert body["messages"][1] == {"role": "user", "content": "seed"}


def test_openrouter_retries_on_429_then_succeeds(monkeypatch):
    _fake_secrets(monkeypatch)
    fake = _FakeRequests([
        _FakeResp(429, headers={"Retry-After": "0"}),
        _FakeResp(503),
        _FakeResp(200, _chat("recovered")),
    ])
    monkeypatch.setattr(GG, "requests", fake)
    monkeypatch.setattr(GG.time, "sleep", lambda s: None)  # no real backoff wait

    out = GG._openrouter_complete("SYS", [])
    assert out == "recovered"
    assert len(fake.calls) == 3  # 429 -> 503 -> 200


def test_openrouter_gives_up_after_retry_budget(monkeypatch):
    _fake_secrets(monkeypatch)
    fake = _FakeRequests([_FakeResp(429, headers={"Retry-After": "0"})])  # always 429
    monkeypatch.setattr(GG, "requests", fake)
    monkeypatch.setattr(GG.time, "sleep", lambda s: None)

    with pytest.raises(GG._BackendUnavailable) as ei:
        GG._openrouter_complete("SYS", [])
    # 1 initial attempt + _OPENROUTER_MAX_RETRIES retries.
    assert len(fake.calls) == GG._OPENROUTER_MAX_RETRIES + 1
    assert "429" in str(ei.value)


def test_openrouter_4xx_raises_unavailable_and_hides_key(monkeypatch):
    secret = "sk-or-v1-MUST-NOT-LEAK"
    _fake_secrets(monkeypatch, key=secret)
    fake = _FakeRequests([_FakeResp(
        401, {"error": {"message": "No auth credentials found"}})])
    monkeypatch.setattr(GG, "requests", fake)

    with pytest.raises(GG._BackendUnavailable) as ei:
        GG._openrouter_complete("SYS", [])
    msg = str(ei.value)
    assert "401" in msg
    assert "No auth credentials" in msg     # the API's own message is surfaced
    assert secret not in msg                # ... but the key never leaks
    assert len(fake.calls) == 1             # 4xx is not retried


def test_openrouter_model_error_reports_api_message(monkeypatch):
    _fake_secrets(monkeypatch)
    fake = _FakeRequests([_FakeResp(
        400, {"error": {"message": "vendor/fake-model:free is not a valid model ID"}})])
    monkeypatch.setattr(GG, "requests", fake)

    with pytest.raises(GG._BackendUnavailable) as ei:
        GG._openrouter_complete("SYS", [])
    assert "not a valid model ID" in str(ei.value)


def test_openrouter_unavailable_without_requests(monkeypatch):
    _fake_secrets(monkeypatch)
    monkeypatch.setattr(GG, "requests", None)
    with pytest.raises(GG._BackendUnavailable):
        GG._openrouter_complete("SYS", [])


def test_openrouter_unavailable_without_config(monkeypatch):
    monkeypatch.setattr(GG, "requests", _FakeRequests([_FakeResp(200, _chat("x"))]))
    monkeypatch.setattr(GG, "_resolve_secret", lambda name: None)  # no key/model
    with pytest.raises(GG._BackendUnavailable):
        GG._openrouter_complete("SYS", [])


def test_retry_after_parsing():
    assert GG._retry_after(_FakeResp(429, headers={"Retry-After": "2.5"}), 1.0) == 2.5
    assert GG._retry_after(_FakeResp(429, headers={}), 1.0) == 1.0
    assert GG._retry_after(_FakeResp(429, headers={"Retry-After": "nope"}), 1.0) == 1.0


# --- Backend selection: auto -> openrouter, and the openrouter repair loop ----

def test_auto_uses_openrouter_when_anthropic_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(GG, "anthropic", None)  # anthropic down
    _fake_secrets(monkeypatch)
    content = "DESIGN\nTheme: t\n```python\n" + GG._DRIFT + "\n```"
    fake = _FakeRequests([_FakeResp(200, _chat(content))])
    monkeypatch.setattr(GG, "requests", fake)
    _install_gameverify(monkeypatch, lambda p: {"passed": True, "failure_class": None,
                                                "hint": "", "witness": {}})

    res = GG.generate_game("drift on ice", out_dir=str(tmp_path),
                           backend="auto", max_repairs=2)

    assert res["backend"] == "openrouter"
    assert res["verdict"] == "COMPLETED"
    # The request actually carried the configured model + bearer key.
    assert fake.calls[0]["json"]["model"] == _FAKE_MODEL
    assert fake.calls[0]["headers"]["Authorization"] == f"Bearer {_FAKE_KEY}"


def test_openrouter_backend_shares_repair_loop(tmp_path, monkeypatch):
    _fake_secrets(monkeypatch)
    content = "DESIGN\nTheme: t\n```python\n" + GG._DRIFT + "\n```"
    fake = _FakeRequests([_FakeResp(200, _chat(content))])  # same content each call
    monkeypatch.setattr(GG, "requests", fake)

    calls = {"n": 0}

    def fake_verify(path):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"passed": False, "failure_class": "UNSOLVED",
                    "hint": "0/40 episodes", "witness": None,
                    "progress": {"reach_counts": {"moved_off_start": 5},
                                 "stuck_after": "moved_off_start"}}
        return {"passed": True, "failure_class": None, "hint": "",
                "witness": {"ticks": 9, "actions": ["left"], "seed": 1,
                            "checkpoints": {"moved_off_start": 2}}}

    _install_gameverify(monkeypatch, fake_verify)

    res = GG.generate_game("drift", out_dir=str(tmp_path),
                           backend="openrouter", max_repairs=3)

    assert res["backend"] == "openrouter"
    assert res["verdict"] == "COMPLETED"
    assert len(res["attempts"]) == 2
    assert len(fake.calls) == 2
    # The repair turn carried the verifier feedback back to the model.
    repair_msgs = fake.calls[1]["json"]["messages"]
    user_texts = [m["content"] for m in repair_msgs if m["role"] == "user"]
    assert any("no random rollout reached success" in t for t in user_texts)


# =============================================================================
#  Per-run sandbox dir layout
# =============================================================================

def test_per_run_sandbox_dir_layout(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_verify(path):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"passed": False, "failure_class": "UNSOLVED",
                    "hint": "stuck", "witness": None}
        return {"passed": True, "failure_class": None, "hint": "",
                "witness": {"ticks": 9, "actions": [], "seed": 0, "checkpoints": {}}}

    _install_gameverify(monkeypatch, fake_verify)

    res = GG.generate_game("a puck on ice", out_dir=str(tmp_path),
                           backend="template", max_repairs=3)

    slug = GG._slug("a puck on ice")
    run_dir = tmp_path / slug
    assert run_dir.is_dir()
    # One file per attempt: a1.py, a2.py.
    assert (run_dir / "a1.py").is_file()
    assert (run_dir / "a2.py").is_file()
    # Final game promoted to <slug>.py inside the run dir.
    final = run_dir / f"{slug}.py"
    assert final.is_file()
    assert os.path.abspath(res["game_path"]) == os.path.abspath(str(final))
    assert final.read_text(encoding="utf-8") == (run_dir / "a2.py").read_text(encoding="utf-8")
    # The run wrote ONLY into its own sandbox dir (nothing else in out_dir).
    assert {p.name for p in tmp_path.iterdir()} == {slug}


# =============================================================================
#  Run integrity: base-code freeze / INVALIDATED verdict
# =============================================================================

def test_integrity_ok_when_base_untouched(tmp_path, monkeypatch):
    _install_gameverify(monkeypatch, lambda p: {"passed": True, "failure_class": None,
                                                "hint": "", "witness": {}})
    res = GG.generate_game("a puck on ice", out_dir=str(tmp_path),
                           backend="template", max_repairs=2)
    assert res["integrity"] == "ok"
    assert res["verdict"] == "COMPLETED"


def test_base_mutation_forces_invalidated(tmp_path, monkeypatch):
    _install_gameverify(monkeypatch, lambda p: {"passed": True, "failure_class": None,
                                                "hint": "", "witness": {}})
    # Simulate a base-code change mid-run via a monkeypatched violations().
    monkeypatch.setattr(GG.integrity, "violations",
                        lambda before, root: ["harness/world.py"])

    res = GG.generate_game("a puck on ice", out_dir=str(tmp_path),
                           backend="template", max_repairs=2)

    assert res["verdict"] == "INVALIDATED"
    assert res["integrity"] == {"violated": ["harness/world.py"]}
    # The game was still produced; it simply does not count.
    assert res["game_path"] is not None


# =============================================================================
#  game demo command (mocked generate_game + replay_gif; no network, no physics)
# =============================================================================

def _canned_result(tmp_path, verdict="COMPLETED", backend="template"):
    game_path = str(tmp_path / "g.py")
    with open(game_path, "w", encoding="utf-8") as fh:
        fh.write("TITLE = 'x'\n")
    return {
        "game_path": game_path,
        "verdict": verdict,
        "backend": backend,
        "attempts": [
            {"report": {"passed": False, "failure_class": "UNSOLVED",
                        "hint": "stuck between a and b", "witness": None}},
            {"report": {"passed": True, "failure_class": None, "hint": "solved",
                        "witness": {"ticks": 12, "actions": ["left"], "seed": 0,
                                    "checkpoints": {"m1": 3, "m2": 9}}}},
        ],
        "design": "DESIGN\nTheme: t\n",
        "integrity": "ok",
    }


def test_game_demo_json_from_mocked_generate(tmp_path, monkeypatch, capsys):
    from harness import cli

    def fake_generate(prompt, out_dir="scenes/games", backend="auto", max_repairs=4):
        return _canned_result(tmp_path, backend=backend)

    def fake_replay(game_path, out_path, **kw):
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("gif")
        return {"ticks": 12, "result": "success", "out_path": out_path}

    monkeypatch.setattr("harness.gen.gamegen.generate_game", fake_generate)
    monkeypatch.setattr("harness.render.replay_gif", fake_replay)

    rc = cli.main(["game", "demo", "--prompts", "p one", "p two",
                   "--backend", "template", "--json"])
    out = capsys.readouterr().out
    data = json.loads(out)

    assert rc == 0
    assert data["all_completed"] is True
    assert len(data["demos"]) == 2
    d = data["demos"][0]
    assert d["verdict"] == "COMPLETED"
    assert d["backend"] == "template"
    assert d["attempts"] == 2
    assert d["witness_ticks"] == 12
    assert d["checkpoints"] == {"m1": 3, "m2": 9}
    assert d["integrity"] == "ok"
    # Per-failed-attempt failure_class + hint captured (one entry: the 1st try).
    assert d["failed_attempts"] == [
        {"n": 1, "failure_class": "UNSOLVED", "hint": "stuck between a and b"}]
    assert d["gif"] is not None


def test_game_demo_exit_code_reflects_completion(tmp_path, monkeypatch, capsys):
    from harness import cli

    def fake_generate(prompt, out_dir="scenes/games", backend="auto", max_repairs=4):
        verdict = "COMPLETED" if "good" in prompt else "UNSOLVED"
        res = _canned_result(tmp_path, verdict=verdict)
        if verdict != "COMPLETED":
            # A failed run: last attempt did not pass, no witness.
            res["attempts"] = [{"report": {"passed": False,
                                           "failure_class": "UNSOLVED",
                                           "hint": "never solved", "witness": None}}]
        return res

    monkeypatch.setattr("harness.gen.gamegen.generate_game", fake_generate)
    monkeypatch.setattr("harness.render.replay_gif",
                        lambda gp, op, **kw: {"result": "success", "out_path": op})

    # One good + one bad -> exit 1 (not all COMPLETED).
    rc = cli.main(["game", "demo", "--prompts", "good one", "bad one",
                   "--backend", "template", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert data["all_completed"] is False

    # All good -> exit 0.
    rc2 = cli.main(["game", "demo", "--prompts", "good one", "good two",
                    "--backend", "template", "--json"])
    data2 = json.loads(capsys.readouterr().out)
    assert rc2 == 0
    assert data2["all_completed"] is True


# =============================================================================
#  Reasoning cap + null-content salvage (all mocked, no network)
# =============================================================================

def _fake_secrets_with_cap(monkeypatch, cap_value):
    monkeypatch.setattr(
        GG, "_resolve_secret",
        lambda name: {"OPENROUTER_API_KEY": _FAKE_KEY,
                      "OPENROUTER_MODEL": _FAKE_MODEL,
                      "OPENROUTER_REASONING_MAX_TOKENS": cap_value}.get(name))


def test_reasoning_cap_default_in_request_body(monkeypatch):
    _fake_secrets(monkeypatch)  # no reasoning secret -> default cap
    fake = _FakeRequests([_FakeResp(200, _chat("ok"))])
    monkeypatch.setattr(GG, "requests", fake)

    GG._openrouter_complete("SYS", [])

    body = fake.calls[0]["json"]
    assert body["reasoning"] == {"max_tokens": GG._OPENROUTER_REASONING_DEFAULT}


def test_reasoning_cap_custom_value(monkeypatch):
    _fake_secrets_with_cap(monkeypatch, "1234")
    fake = _FakeRequests([_FakeResp(200, _chat("ok"))])
    monkeypatch.setattr(GG, "requests", fake)

    GG._openrouter_complete("SYS", [])

    assert fake.calls[0]["json"]["reasoning"] == {"max_tokens": 1234}


def test_reasoning_cap_zero_disables_field(monkeypatch):
    _fake_secrets_with_cap(monkeypatch, "0")
    fake = _FakeRequests([_FakeResp(200, _chat("ok"))])
    monkeypatch.setattr(GG, "requests", fake)

    GG._openrouter_complete("SYS", [])

    assert "reasoning" not in fake.calls[0]["json"]


def test_reasoning_cap_garbage_falls_back_to_default(monkeypatch):
    _fake_secrets_with_cap(monkeypatch, "not-a-number")
    assert GG._reasoning_cap() == GG._OPENROUTER_REASONING_DEFAULT


_NULL_CONTENT_BODY = {"choices": [{"message": {"content": None},
                                   "finish_reason": "length"}]}


def test_null_content_salvage_halves_cap_once(monkeypatch):
    # 200-with-null-content (reasoning ate the budget) -> retry ONCE at cap/2.
    _fake_secrets(monkeypatch)
    fake = _FakeRequests([_FakeResp(200, _NULL_CONTENT_BODY),
                          _FakeResp(200, _chat("recovered"))])
    monkeypatch.setattr(GG, "requests", fake)

    out = GG._openrouter_complete("SYS", [])

    assert out == "recovered"
    assert len(fake.calls) == 2
    cap = GG._OPENROUTER_REASONING_DEFAULT
    assert fake.calls[0]["json"]["reasoning"] == {"max_tokens": cap}
    assert fake.calls[1]["json"]["reasoning"] == {"max_tokens": cap // 2}


def test_null_content_twice_gives_up_key_free(monkeypatch):
    secret = "sk-or-v1-NEVER-IN-ERRORS"
    _fake_secrets(monkeypatch, key=secret)
    fake = _FakeRequests([_FakeResp(200, _NULL_CONTENT_BODY)])  # reused each call
    monkeypatch.setattr(GG, "requests", fake)

    with pytest.raises(GG._BackendUnavailable) as ei:
        GG._openrouter_complete("SYS", [])

    assert len(fake.calls) == 2          # initial + exactly one salvage
    assert secret not in str(ei.value)


def test_null_content_no_salvage_when_cap_disabled(monkeypatch):
    # With the reasoning field disabled there is no cap to halve -> fail fast.
    _fake_secrets_with_cap(monkeypatch, "0")
    fake = _FakeRequests([_FakeResp(200, _NULL_CONTENT_BODY)])
    monkeypatch.setattr(GG, "requests", fake)

    with pytest.raises(GG._BackendUnavailable):
        GG._openrouter_complete("SYS", [])
    assert len(fake.calls) == 1


def test_blank_content_treated_as_null(monkeypatch):
    # Whitespace-only content is as useless as null: same salvage path.
    _fake_secrets(monkeypatch)
    fake = _FakeRequests([_FakeResp(200, _chat("   \n  ")),
                          _FakeResp(200, _chat("real text"))])
    monkeypatch.setattr(GG, "requests", fake)

    assert GG._openrouter_complete("SYS", []) == "real text"
    assert len(fake.calls) == 2


# =============================================================================
#  Telemetry hook in generate_game (ledger redirected to tmp by the fixture)
# =============================================================================

def test_generate_game_records_one_ledger_line(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(GG, "_LEDGER_PATH", str(ledger))
    _install_gameverify(monkeypatch, lambda p: {"passed": True, "failure_class": None,
                                                "hint": "", "witness": {"ticks": 8,
                                                "actions": [], "seed": 0,
                                                "checkpoints": {"m": 2}}})

    GG.generate_game("a puck on ice", out_dir=str(tmp_path / "out"),
                     backend="template", max_repairs=2)

    lines = ledger.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["prompt"] == "a puck on ice"
    assert entry["backend"] == "template"
    assert entry["model"] == "template"
    assert entry["verdict"] == "COMPLETED"
    assert entry["attempts"] == 1
    assert entry["witness_ticks"] == 8
    assert entry["checkpoints"] == {"m": 2}
    assert entry["integrity"] == "ok"
    assert entry["wall_s"] >= 0


def test_telemetry_failure_never_breaks_a_run(tmp_path, monkeypatch):
    def boom(*a, **kw):
        raise OSError("disk full")

    import harness.core.telemetry as Tel
    monkeypatch.setattr(Tel, "record_run", boom)
    _install_gameverify(monkeypatch, lambda p: {"passed": True, "failure_class": None,
                                                "hint": "", "witness": {}})

    res = GG.generate_game("a puck on ice", out_dir=str(tmp_path),
                           backend="template", max_repairs=2)
    assert res["verdict"] == "COMPLETED"  # the run survived the telemetry crash


# --- OpenRouter keep-alive padding (diagnosed live on long GLM generations) ---

class _PaddedResp:
    """A 200 body with anti-timeout padding before the JSON document."""

    def __init__(self, payload, padding):
        import json as _json
        self.text = padding + _json.dumps(payload)
        self.status_code = 200

    def json(self):
        import json as _json
        return _json.loads(self.text)  # raises on padded bodies, like requests


def test_openrouter_content_tolerates_keepalive_padding():
    from harness.gen.gamegen import _openrouter_content, _openrouter_json
    payload = {"choices": [{"message": {"content": "DESIGN\nok\n```python\nX=1\n```"}}]}
    padding = (": OPENROUTER PROCESSING\n" * 40) + ("\n" * 900)
    assert _openrouter_content(_PaddedResp(payload, "")) is not None
    assert _openrouter_content(_PaddedResp(payload, padding)) is not None
    assert _openrouter_json(_PaddedResp({}, "")) == {}


def test_openrouter_json_returns_none_on_garbage():
    from harness.gen.gamegen import _openrouter_json
    assert _openrouter_json(_PaddedResp({}, "")) == {}

    class Garbage:
        text = "no json here"
        status_code = 200

        def json(self):
            raise ValueError("bad")

    assert _openrouter_json(Garbage()) is None


# =============================================================================
#  Tier-1b parts pipeline: retrieval injection, pinning, and ledger block
# =============================================================================

# A game that instantiates a bank part via world.part (so parts_used is non-empty).
_PART_GAME_PY = '''TITLE = "wreck"
PROMPT = "swing a wrecking ball"
ACTIONS = ["push", "pull"]
def build(world):
    world.part("wrecker", "wrecking_ball", pos=(400, 230))
    world.control("wrecker")
def act(world, action):
    world.impulse("wrecker", (60 if action == "push" else -60, 0))
def success(world):
    return world.query("wrecker")["pos"][0] > 600
def checkpoints(world):
    return {"swung": world.query("wrecker")["pos"][0] > 500}
'''


def test_bank_menu_injected_and_pinned_across_repairs(tmp_path, monkeypatch):
    # The retrieved Tier-1b menu is spliced into the system prompt and PINNED:
    # every repair attempt sees the identical system prompt (same menu).
    systems = []

    def fake_complete(client, system, messages):
        systems.append(system)
        return "DESIGN\nParts used: wrecking_ball as \"wrecker\"\n```python\n" + \
            _PART_GAME_PY + "\n```"

    verify_calls = {"n": 0}

    def fake_verify(path):
        verify_calls["n"] += 1
        if verify_calls["n"] == 1:
            return {"passed": False, "failure_class": "UNSOLVED",
                    "hint": "0/40 episodes", "witness": None}
        return {"passed": True, "failure_class": None, "hint": "", "witness": {}}

    monkeypatch.setattr(GG, "_make_client", lambda: object())
    monkeypatch.setattr(GG, "_llm_complete", fake_complete)
    _install_gameverify(monkeypatch, fake_verify)

    res = GG.generate_game("swing a wrecking ball to smash a tower",
                           out_dir=str(tmp_path), backend="anthropic", max_repairs=4)

    assert res["verdict"] == "COMPLETED"
    assert len(systems) == 2                       # initial + one repair
    # The menu was injected (the retrieved part shows up in the system prompt)...
    assert "wrecking_ball (mobile)" in systems[0]
    assert "world.part(" in systems[0]
    # ...and it is PINNED — byte-identical system prompt across repair iterations.
    assert systems[0] == systems[1]
    # Pipeline telemetry reflects the pinned menu + the part the game used.
    pipe = res["pipeline"]
    assert pipe["menu_mode"] == "menu"
    assert "wrecking_ball" in pipe["retrieved"]
    assert pipe["parts_used"] == ["wrecking_ball"]


def test_ledger_pipeline_block_written(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(GG, "_LEDGER_PATH", str(ledger))
    monkeypatch.setattr(GG, "_make_client", lambda: object())
    monkeypatch.setattr(
        GG, "_llm_complete",
        lambda client, system, messages: "DESIGN\nt\n```python\n" + _PART_GAME_PY + "\n```")
    _install_gameverify(monkeypatch, lambda p: {"passed": True, "failure_class": None,
                                                "hint": "", "witness": {}})

    GG.generate_game("swing a wrecking ball to smash a tower",
                     out_dir=str(tmp_path / "out"), backend="anthropic", max_repairs=2)

    entry = json.loads(ledger.read_text(encoding="utf-8").strip().splitlines()[0])
    assert "pipeline" in entry
    pipe = entry["pipeline"]
    assert pipe["menu_mode"] == "menu"
    assert "wrecking_ball" in pipe["retrieved"]
    assert pipe["parts_used"] == ["wrecking_ball"]


def test_pipeline_off_for_template_backend(tmp_path, monkeypatch):
    _install_gameverify(monkeypatch, lambda p: {"passed": True, "failure_class": None,
                                                "hint": "", "witness": {}})
    res = GG.generate_game("swing a wrecking ball to smash a tower",
                           out_dir=str(tmp_path), backend="template", max_repairs=1)
    # The offline template backend bypasses retrieval entirely.
    assert res["pipeline"]["menu_mode"] == "off"
    assert res["pipeline"]["retrieved"] == []


def test_use_bank_false_skips_retrieval(tmp_path, monkeypatch):
    seen = {"system": None}

    def fake_complete(client, system, messages):
        seen["system"] = system
        return "DESIGN\nt\n```python\n" + GG._DRIFT + "\n```"

    monkeypatch.setattr(GG, "_make_client", lambda: object())
    monkeypatch.setattr(GG, "_llm_complete", fake_complete)
    _install_gameverify(monkeypatch, lambda p: {"passed": True, "failure_class": None,
                                                "hint": "", "witness": {}})

    res = GG.generate_game("swing a wrecking ball to smash a tower",
                           out_dir=str(tmp_path), backend="anthropic",
                           max_repairs=1, use_bank=False)

    assert res["pipeline"]["menu_mode"] == "off"
    assert res["pipeline"]["retrieved"] == []
    # With the bank off, the system prompt is the menu-free baseline.
    assert seen["system"] == GG._SYSTEM_PROMPT
    assert "world.part(" not in seen["system"]


def test_auto_fallback_to_template_records_menu_off(tmp_path, monkeypatch):
    # A themed prompt would retrieve a menu, but if every LLM backend is down the
    # run falls to templates and the menu was never used -> honest "off".
    monkeypatch.setattr(GG, "anthropic", None)
    monkeypatch.setattr(GG, "requests", None)
    _install_gameverify(monkeypatch, lambda p: {"passed": True, "failure_class": None,
                                                "hint": "", "witness": {}})

    res = GG.generate_game("swing a wrecking ball to smash a tower",
                           out_dir=str(tmp_path), backend="auto", max_repairs=1)

    assert res["backend"] == "template"
    assert res["pipeline"]["menu_mode"] == "off"
    assert res["pipeline"]["retrieved"] == []


def test_legend_only_when_prompt_offtheme(tmp_path, monkeypatch):
    seen = {"system": None}

    def fake_complete(client, system, messages):
        seen["system"] = system
        return "DESIGN\nt\n```python\n" + GG._DRIFT + "\n```"

    monkeypatch.setattr(GG, "_make_client", lambda: object())
    monkeypatch.setattr(GG, "_llm_complete", fake_complete)
    _install_gameverify(monkeypatch, lambda p: {"passed": True, "failure_class": None,
                                                "hint": "", "witness": {}})

    res = GG.generate_game("a game about abstract colours and rhythmic music",
                           out_dir=str(tmp_path), backend="anthropic", max_repairs=1)

    assert res["pipeline"]["menu_mode"] == "legend_only"
    assert res["pipeline"]["retrieved"] == []
    # Legend-only falls back to the menu-free baseline prompt.
    assert seen["system"] == GG._SYSTEM_PROMPT


# --- parts_used parsing (unit) -----------------------------------------------

def test_parse_parts_used_py_captures_part_kind():
    bank_names = {"wrecking_ball", "crate_light", "goal_zone"}
    src = ('world.part("wrecker", "wrecking_ball", pos=(400, 230))\n'
           'world.part("box", "crate_light", pos=(200, 60))\n'
           'world.part("box2", "crate_light", pos=(260, 60))\n'   # dup kind
           'world.add("floor", "box", pos=(400, 10), static=True)\n')
    used = GG._parse_parts_used(src, "py", bank_names)
    assert used == ["wrecking_ball", "crate_light"]   # deduped, in order


def test_parse_parts_used_js_matches_bank_names_among_add():
    bank_names = {"puck", "goal_zone", "wall"}
    src = ('world.add("puck", "circle", { pos: [180, 150], radius: 16 });\n'
           'world.add("goal_zone", "box", { pos: [560, 430], sensor: true });\n'
           'world.add("scenery", "box", { pos: [10, 10] });\n')   # not a bank name
    used = GG._parse_parts_used(src, "js", bank_names)
    assert used == ["puck", "goal_zone"]


def test_parse_parts_used_empty_when_no_bank_calls():
    assert GG._parse_parts_used(GG._DRIFT, "py", {"puck", "goal_zone"}) == []
    assert GG._parse_parts_used("", "py", {"puck"}) == []
