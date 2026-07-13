"""Tests for module C (generator + templates), WITHOUT any network call.

`harness.verifier.verify_scene` is mocked (via sys.modules) to simulate
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

from harness import generator as G
from harness import templates as T


# --- Helpers ------------------------------------------------------------------

def _no_import(source):
    """No import / from ... import statement in the emitted code."""
    return re.search(r"(?m)^\s*(import|from)\s", source) is None


def _exec_module(source):
    """Compile and execute the scene module, return its namespace."""
    ns = {}
    exec(compile(source, "<scene>", "exec"), ns)
    return ns


def _install_verifier(monkeypatch, fn):
    """Inject a fake harness.verifier module with verify_scene=fn."""
    mod = types.ModuleType("harness.verifier")
    mod.verify_scene = fn
    monkeypatch.setitem(sys.modules, "harness.verifier", mod)


def _remove_verifier(monkeypatch):
    monkeypatch.delitem(sys.modules, "harness.verifier", raising=False)


# Legacy bilingual commands kept as-is: they exercise the French->template path.
_COMMANDS = {
    "push_ball": "pousse la balle dans la zone",
    "stack": "empile les caisses vers la zone haute",
    "climb": "atteins la plateforme la plus haute",
    "reach": "va rejoindre la zone de l'autre côté",
}


# --- Templates: shape and validity -------------------------------------------

@pytest.mark.parametrize("kind,command", list(_COMMANDS.items()))
def test_template_source_is_valid_module(kind, command):
    src = T.build_scene_source(command)
    # Parses without error.
    ast.parse(src)
    # Contains the elements required by CONTRACTS §2.
    assert "SCENE_DESCRIPTION" in src
    assert "AVAILABLE_ACTIONS" in src
    assert "def build_scene" in src
    assert "def get_success" in src
    # No import in the emitted code.
    assert _no_import(src)
    # build_scene / get_success are defined and callable.
    ns = _exec_module(src)
    assert callable(ns["build_scene"])
    assert callable(ns["get_success"])
    assert ns["AVAILABLE_ACTIONS"] == ["left", "right", "jump", "noop"]


def test_selection_by_keywords():
    # Bilingual matcher: both legacy French and English commands map correctly.
    assert T.select_template("pousse la balle dans la zone") == "push_ball"
    assert T.select_template("push the ball") == "push_ball"
    assert T.select_template("empile les boîtes") == "stack"
    assert T.select_template("stack the boxes") == "stack"
    assert T.select_template("atteins la plateforme") == "climb"
    assert T.select_template("reach the top platform") == "climb"
    assert T.select_template("climb up") == "climb"
    # No keyword -> default template.
    assert T.select_template("go to the other side of the level") == "reach"
    assert T.select_template("") == "reach"


def test_determinism_same_and_different():
    cmd = "push the ball into the zone"
    assert T.build_scene_source(cmd) == T.build_scene_source(cmd)
    # Two different commands of the same template -> different parameters.
    a = T.build_scene_source("push the ball")
    b = T.build_scene_source("push the ball hard now")
    assert T.select_template("push the ball") == T.select_template("push the ball hard now")
    assert a != b


# --- Repair loop (verify mocked) ---------------------------------------------

def test_repair_fail_then_pass_completed(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_verify(path):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"passed": False, "failure_class": "GOAL_ERROR",
                    "hint": "the agent does not reach the zone"}
        return {"passed": True, "failure_class": None, "hint": ""}

    _install_verifier(monkeypatch, fake_verify)

    res = G.generate("reach the top platform", out_dir=str(tmp_path),
                     backend="template", max_repairs=4)

    assert res["verdict"] == "COMPLETED"
    assert res["backend"] == "template"
    assert len(res["attempts"]) == 2
    assert calls["n"] == 2
    assert res["scene_path"] is not None


def test_repair_permanent_fail_env_error(tmp_path, monkeypatch):
    def fake_verify(path):
        return {"passed": False, "failure_class": "ENV_ERROR",
                "hint": "floating object"}

    _install_verifier(monkeypatch, fake_verify)

    res = G.generate("push the ball", out_dir=str(tmp_path),
                     backend="template", max_repairs=4)

    assert res["verdict"] == "ENV_ERROR"
    # 1 initial attempt + 4 repairs.
    assert len(res["attempts"]) == 5


def test_compile_errors_capped_env_error(tmp_path, monkeypatch):
    # Compile errors (L0 builds): hard cap at 5 regardless of max_repairs.
    def fake_verify(path):
        return {
            "passed": False,
            "failure_class": None,
            "layers": {"L0_static": {"checks": {"builds": {"pass": False}}}},
            "hint": "compile error",
        }

    _install_verifier(monkeypatch, fake_verify)

    res = G.generate("stack the crates", out_dir=str(tmp_path),
                     backend="template", max_repairs=20)

    assert res["verdict"] == "ENV_ERROR"
    assert len(res["attempts"]) == 5


def test_verification_unavailable_partial(tmp_path, monkeypatch):
    _remove_verifier(monkeypatch)
    # Ensure the real import also fails.
    monkeypatch.setattr(G, "_verify", lambda p: None)

    res = G.generate("go to the other side", out_dir=str(tmp_path),
                     backend="template", max_repairs=4)

    assert res["verdict"] == "PARTIAL"
    assert len(res["attempts"]) == 1
    assert res["scene_path"] is not None


# --- Auto fallback when anthropic is unavailable ------------------------------

def test_auto_falls_back_to_template(tmp_path, monkeypatch):
    def boom():
        raise anthropic.APIConnectionError(
            request=httpx.Request("GET", "http://localhost"))

    monkeypatch.setattr(G, "_make_client", boom)
    _install_verifier(monkeypatch, lambda p: {"passed": True,
                                              "failure_class": None, "hint": ""})

    res = G.generate("push the ball into the zone", out_dir=str(tmp_path),
                     backend="auto", max_repairs=4)

    assert res["backend"] == "template"
    assert res["verdict"] == "COMPLETED"
    assert "note" in res and "anthropic unavailable" in res["note"]


def test_auto_uses_llm_when_available(tmp_path, monkeypatch):
    # Fake client + fake completion (no network).
    monkeypatch.setattr(G, "_make_client", lambda: object())
    monkeypatch.setattr(
        G, "_llm_complete",
        lambda client, system, messages:
            "Here is the scene:\n```python\n" + T.build_scene_source("x") + "\n```")
    _install_verifier(monkeypatch, lambda p: {"passed": True,
                                              "failure_class": None, "hint": ""})

    res = G.generate("push the ball", out_dir=str(tmp_path),
                     backend="anthropic", max_repairs=4)

    assert res["backend"] == "anthropic"
    assert res["verdict"] == "COMPLETED"


def test_extract_code_first_python_block():
    text = "bla\n```python\nA = 1\n```\ntext\n```python\nB = 2\n```"
    assert G._extract_code(text).strip() == "A = 1"
