"""P1 gate tests for the frozen tool layer (DESIGNER_AGENT_PLAN §3 / §5).

* the registry is the three frozen tools with OpenAI function-calling schemas;
* certify(depth=verify) verdict is identical to a direct verify_game (the same
  decision `game verify` makes);
* design via the template backend passes verify;
* retrieve_parts is deterministic (two calls, identical menu) and thresholds are
  never a certify parameter.
"""
from __future__ import annotations

import pytest

from harness.designer import tools as T


# --------------------------------------------------------------------------- #
# Registry / schemas
# --------------------------------------------------------------------------- #
def test_registry_is_the_three_frozen_tools():
    assert T.tool_names() == ["design", "certify", "retrieve_parts"]
    for entry in T.REGISTRY:
        assert entry["type"] == "function"
        fn = entry["function"]
        assert set(fn) >= {"name", "description", "parameters"}
        assert fn["parameters"]["type"] == "object"


def test_certify_has_no_threshold_parameter():
    props = T.CERTIFY_SCHEMA["function"]["parameters"]["properties"]
    assert set(props) == {"game_path", "depth"}
    assert props["depth"]["default"] == "verify"
    # No threshold-shaped knob is exposed anywhere in the schema.
    assert not any("threshold" in k.lower() for k in props)


def test_dispatch_routes_by_name():
    out = T.dispatch("retrieve_parts", {"prompt": "a lava pool", "engine": "py"})
    assert set(out) == {"menu_text", "menu_mode", "names", "scores"}
    with pytest.raises(KeyError):
        T.dispatch("no_such_tool", {})


# --------------------------------------------------------------------------- #
# retrieve_parts determinism
# --------------------------------------------------------------------------- #
def test_retrieve_parts_deterministic():
    a = T.retrieve_parts("cross a lava pool on a moving platform", engine="py")
    b = T.retrieve_parts("cross a lava pool on a moving platform", engine="py")
    assert a == b
    assert a["menu_mode"] in ("menu", "legend_only")
    assert len(a["names"]) == len(a["scores"])


def test_retrieve_parts_strong_prompt_yields_menu():
    out = T.retrieve_parts("lava pool", engine="py")
    assert out["menu_mode"] == "menu"
    assert "lava_pool" in out["names"]
    assert out["menu_text"]


# --------------------------------------------------------------------------- #
# design (template backend) + certify identity
# --------------------------------------------------------------------------- #
def test_design_template_backend_passes_verify(tmp_path):
    res = T.design("a game about ice", backend="template",
                   out_dir=str(tmp_path / "games"))
    assert res["verdict"] == "COMPLETED"
    assert res["backend"] == "template"
    assert res["game_path"] and res["game_path"].endswith(".py")
    assert res["integrity"] == "ok"


# A valid, solvable game — identical to the gameverify inline fixture. certify
# and a direct verify_game route through the SAME funnel, so their verdict must
# agree (whatever it is under the live thresholds). Runs in-process (no fork).
_GAME_VALID = '''
TITLE = "Push Right"
PROMPT = "drive the block past the marker on the right"
ACTIONS = ["right", "left"]

def build(world):
    world.add("ground", shape="box", pos=(400, 10), size=(800, 20), static=True)
    world.add("player", shape="box", pos=(100, 60), size=(20, 20))
    world.control("player")

def act(world, action):
    if action == "right":
        world.impulse("player", (60, 0))
    elif action == "left":
        world.impulse("player", (-60, 0))

def success(world):
    return world.query("player")["pos"][0] > 300

def checkpoints(world):
    x = world.query("player")["pos"][0]
    return {"halfway": x > 200, "almost": x > 260}
'''


def test_certify_verify_matches_game_verify(tmp_path):
    from harness.verify.gameverify import verify_game

    game_path = str(tmp_path / "valid.py")
    with open(game_path, "w", encoding="utf-8") as fh:
        fh.write(_GAME_VALID)

    direct = verify_game(game_path, sandboxed=False)      # what `game verify` runs
    out = T.certify(game_path, depth="verify", sandboxed=False)

    # The pass/fail decision `game verify` prints must match certify's verdict.
    assert out["report"]["passed"] == direct["passed"]
    assert (out["verdict"] == "COMPLETED") == direct["passed"]
    assert out["hint"] == direct["hint"]
    assert out["witness"] == direct["witness"]
    # verify depth does not run the expensive oracles.
    assert out["g4_grade"] is None and out["learnable"] is None
    assert out["stage"] in ("G0", "G1", "G2", "G3")
    assert out["depth"] == "verify"
