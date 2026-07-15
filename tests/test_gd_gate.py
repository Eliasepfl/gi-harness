"""Pure-python tests for the GDScript (GameAPI) lane's G0 code gates + routing.

Always run (no Godot binary needed):

* the BANNED-API scanner catches every forbidden construct (parameterized negative
  fixtures as inline strings) and passes the clean ``mini_collect.gd`` fixture, and
  distinguishes a method call on ``self.rng`` from the banned GLOBAL rng;
* ``detect_engine`` routes ``.gd`` (and the ``# engine: gdscript`` marker) to the
  ``gdscript`` engine;
* a banned ``.gd`` game fails G0 through ``verify_game`` WITHOUT ever spawning Godot
  (the scan short-circuits before the executor is built);
* the shared ``run_g0_gd`` layer accepts well-formed check facts and rejects a
  missing contract method / two controlled bodies / a failed parse gate;
* ``scrubbed_env`` drops credentials (a settable fake env) and the ``GdExecutor``
  spawn is wired to hand the serve host that scrubbed env.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.verify.gameverify import detect_engine, run_g0_gd, verify_game  # noqa: E402
from harness.verify.gd_gate import (  # noqa: E402
    GD_REQUIRED_METHODS, scan_gd_source, scan_violations,
)
from harness.verify.godot_exec import scrubbed_env  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MINI = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "mini_collect.gd")


# ====================================================================== #
# 1. Banned-API scanner
# ====================================================================== #
_BANNED = [
    ("os", 'func act(a): OS.execute("ls", [])'),
    ("file_access", 'func build(s): var f = FileAccess.open("x", 1)'),
    ("dir_access", 'func build(s): var d = DirAccess.open("x")'),
    ("http", "func build(s): var h = HTTPRequest.new()"),
    ("tcp", "func build(s): var srv = TCPServer.new()"),
    ("udp", "func build(s): var srv = UDPServer.new()"),
    ("stream_peer", "func build(s): var p = StreamPeerTCP.new()"),
    ("packet_peer", "func build(s): var p = PacketPeerUDP.new()"),
    ("websocket", "func build(s): var w = WebSocketPeer.new()"),
    ("thread", "func build(s): var t = Thread.new()"),
    ("mutex", "func build(s): var m = Mutex.new()"),
    ("semaphore", "func build(s): var m = Semaphore.new()"),
    ("worker_pool", "func build(s): WorkerThreadPool.add_task(x)"),
    ("engine_singleton", 'func build(s): var e = Engine.get_singleton("OS")'),
    ("class_db", 'func build(s): ClassDB.instantiate("OS")'),
    ("expression", "func build(s): var e = Expression.new()"),
    ("resource_loader", 'func build(s): ResourceLoader.load("x")'),
    ("gdscript_class", "func build(s): var g = GDScript.new()"),
    ("set_script", "func build(s): node.set_script(x)"),
    ("load", 'func build(s): var r = load("res://x.gd")'),
    ("preload", 'func build(s): var r = preload("res://x.gd")'),
    ("time", "func build(s): var t = Time.get_ticks_msec()"),
    ("randomize", "func build(s): randomize()"),
    ("global_rng", "func build(s): var x = randf()"),
    ("global_seed", "func build(s): seed(5)"),
    ("scene_tree", "func build(s): get_tree().quit()"),
]


@pytest.mark.parametrize("rule,src", _BANNED, ids=[r for r, _ in _BANNED])
def test_scanner_catches_each_banned_api(rule, src):
    findings = scan_gd_source("extends Node2D\n" + src)
    rules = {f["rule"] for f in findings}
    assert rule in rules, (rule, rules, src)
    # every finding carries a 1-based line and a token
    for f in findings:
        assert f["line"] >= 1 and f["token"]


def test_scanner_catches_global_randi_and_randf_range():
    for bad in ("var x = randi()", "var y = randi_range(0, 3)",
                "var z = randf_range(0.0, 1.0)"):
        assert any(f["rule"] == "global_rng"
                   for f in scan_gd_source("extends Node2D\nfunc build(s): " + bad)), bad


def test_scanner_allows_self_rng_methods():
    # A method call on the seeded self.rng is the SANCTIONED path -> not a finding.
    for good in ("var x = self.rng.randf()", "var y = rng.randi_range(0, 3)",
                 "var z = rng.randf_range(-5.0, 5.0)"):
        assert scan_gd_source("extends Node2D\nfunc build(s): " + good) == [], good


def test_scanner_flags_rng_randomize_even_on_receiver():
    # randomize() reseeds from the wall clock -> banned even as rng.randomize().
    assert any(f["rule"] == "randomize"
               for f in scan_gd_source("extends Node2D\nfunc build(s): rng.randomize()"))


def test_scanner_ignores_banned_token_in_comment_or_string():
    src = ('extends Node2D\n'
           '# this game never calls OS.execute or FileAccess\n'
           'func actions(): return ["OS.left", "right"]\n')
    assert scan_gd_source(src) == [], scan_gd_source(src)


def test_scanner_reports_correct_line_number():
    src = "extends Node2D\nfunc build(s):\n\tpass\nfunc act(a):\n\tOS.alert(a)\n"
    findings = [f for f in scan_gd_source(src) if f["rule"] == "os"]
    assert len(findings) == 1
    assert findings[0]["line"] == 5


def test_mini_collect_fixture_scans_clean():
    with open(_MINI, "r", encoding="utf-8") as fh:
        src = fh.read()
    assert scan_gd_source(src) == [], scan_gd_source(src)
    assert scan_violations(src) == []


# ====================================================================== #
# 2. Engine detection
# ====================================================================== #
def test_detect_engine_routes_gd():
    assert detect_engine("game.gd", "") == "gdscript"
    assert detect_engine("/abs/path/mini_collect.gd", "extends Node2D") == "gdscript"


def test_detect_engine_gdscript_marker():
    assert detect_engine("g.txt", "# engine: gdscript\nextends Node2D") == "gdscript"
    # a .gd path still wins for js/godot-looking content
    assert detect_engine("x.gd", "// engine: js") == "gdscript"


def test_detect_engine_other_lanes_unchanged():
    assert detect_engine("g.spec.json", "") == "godot"
    assert detect_engine("g.js", "") == "js"
    assert detect_engine("g.py", "") == "py"


# ====================================================================== #
# 3. verify_game short-circuits a banned .gd BEFORE any Godot spawn
# ====================================================================== #
def test_verify_game_banned_gd_fails_g0_without_godot(tmp_path):
    p = tmp_path / "evil.gd"
    p.write_text(
        "extends Node2D\n"
        "func build(world_seed):\n"
        "\tOS.execute(\"rm\", [\"-rf\", \"/\"])\n",
        encoding="utf-8")
    rep = verify_game(str(p), sandboxed=False)
    assert rep["engine"] == "gdscript"
    assert rep["failure_class"] == "ENV_ERROR"
    scan = rep["layers"]["G0_static"]["checks"]["sandbox_scan"]
    assert scan["pass"] is False
    assert any("OS" in v for v in scan["violations"])
    # the code never reached a compile/run layer
    assert rep["layers"]["G1_rollout"]["checks"] == {}


# ====================================================================== #
# 4. run_g0_gd over canned check facts (the serve host's shape)
# ====================================================================== #
def _wellformed_gd_facts() -> dict:
    return {
        "mode": "check",
        "scan": [],
        "load": {"ok": True, "error": None},
        "contract": {"methods": {m: True for m in GD_REQUIRED_METHODS}},
        "actions": {"is_list": True, "length": 4, "all_str": True,
                    "values": ["up", "down", "left", "right"]},
        "world_size": {"declared": [800.0, 600.0], "effective": [800.0, 600.0]},
        "build": {"ok": True, "error": None},
        "entities": ["player", "gem_a", "gem_b"],
        "queries": {
            "player": {"static": False, "sensor": False, "controlled": True, "in_bounds": True},
            "gem_a": {"static": True, "sensor": False, "controlled": False, "in_bounds": True},
            "gem_b": {"static": True, "sensor": False, "controlled": False, "in_bounds": True},
        },
        "penetration": [],
    }


def test_run_g0_gd_passes_wellformed_facts():
    g0 = run_g0_gd(_wellformed_gd_facts(), [])
    assert g0["passed"] is True, g0
    assert g0["checks"]["controlled"]["controlled"] == ["player"]
    assert g0["checks"]["counts"]["n"] == 3


def test_run_g0_gd_scan_violation_short_circuits():
    g0 = run_g0_gd(_wellformed_gd_facts(), ["line 3: banned os (...) — 'OS.'"])
    assert g0["passed"] is False
    assert g0["checks"]["sandbox_scan"]["pass"] is False
    assert "loads" not in g0["checks"]  # never reached the parse gate


def test_run_g0_gd_parse_gate_failure():
    facts = _wellformed_gd_facts()
    facts["load"] = {"ok": False, "error": "parse/compile failed (Error 43)"}
    g0 = run_g0_gd(facts, [])
    assert g0["passed"] is False
    assert g0["checks"]["loads"]["pass"] is False
    assert "symbols" not in g0["checks"]


def test_run_g0_gd_contract_probe_missing_method():
    facts = _wellformed_gd_facts()
    facts["contract"]["methods"]["state"] = False
    g0 = run_g0_gd(facts, [])
    assert g0["passed"] is False
    assert g0["checks"]["symbols"]["pass"] is False
    assert g0["checks"]["symbols"]["missing"] == ["state"]


def test_run_g0_gd_catches_two_controlled():
    facts = _wellformed_gd_facts()
    facts["queries"]["gem_a"]["controlled"] = True
    g0 = run_g0_gd(facts, [])
    assert g0["passed"] is False
    assert g0["checks"]["controlled"]["pass"] is False


def test_run_g0_gd_catches_too_few_actions():
    facts = _wellformed_gd_facts()
    facts["actions"] = {"is_list": True, "length": 1, "all_str": True, "values": ["go"]}
    g0 = run_g0_gd(facts, [])
    assert g0["passed"] is False
    assert g0["checks"]["actions"]["pass"] is False


# ====================================================================== #
# 5. Env scrub (GDSCRIPT_LANE.md security)
# ====================================================================== #
def test_scrubbed_env_drops_credentials_keeps_essentials():
    fake = {
        "OPENROUTER_API_KEY": "sk-should-be-gone",
        "ANTHROPIC_API_KEY": "sk-should-also-be-gone",
        "AWS_SECRET_ACCESS_KEY": "gone",
        "MY_SECRET_TOKEN": "gone",
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/enaha",
        "LD_LIBRARY_PATH": "/opt/godot/lib",
        "LC_CTYPE": "en_US.UTF-8",
        "XDG_CACHE_HOME": "/tmp/cache",
    }
    out = scrubbed_env(fake)
    assert "OPENROUTER_API_KEY" not in out
    assert "ANTHROPIC_API_KEY" not in out
    assert "AWS_SECRET_ACCESS_KEY" not in out
    assert "MY_SECRET_TOKEN" not in out
    assert out["PATH"] == "/usr/bin:/bin"
    assert out["HOME"] == "/home/enaha"
    assert out["LD_LIBRARY_PATH"] == "/opt/godot/lib"
    assert out["LC_CTYPE"] == "en_US.UTF-8"      # LC_ prefix allowed
    assert out["XDG_CACHE_HOME"] == "/tmp/cache"  # XDG_ prefix allowed


def test_scrubbed_env_allow_extra():
    out = scrubbed_env({"OPENROUTER_API_KEY": "x", "PROBE_MARKER": "1"},
                       allow_extra=("PROBE_MARKER",))
    assert out == {"PROBE_MARKER": "1"}


def test_gd_executor_spawns_with_scrubbed_env(monkeypatch, tmp_path):
    """The GdExecutor spawn hands serve_game.gd a SCRUBBED env even when the parent
    holds a credential — verified by capturing the Popen env kwarg (no Godot needed;
    the fake process 'dies' so the spawn fails fast after we've captured it)."""
    import harness.rl.godot_env as ge
    import harness.verify.gd_exec as gx
    from harness.verify.executors import VerifyError

    # A fake, already-provisioned project (``.godot`` present) so no --import subprocess
    # is needed; the spawn path is what we are capturing.
    proj = tmp_path / "godotworld"
    proj.mkdir()
    (proj / ".godot").mkdir()
    (proj / "serve_game.gd").write_text("", encoding="utf-8")
    exe = tmp_path / "godot"
    exe.write_text("", encoding="utf-8")

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-parent-secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(ge, "SPAWN_RETRIES", 1)  # fail fast (one dead spawn)

    captured: dict = {}

    class _DeadProc:
        returncode = 1

        def poll(self):
            return 1  # already exited -> accept gives up immediately

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 1

    def fake_popen(argv, *a, **k):
        captured["env"] = k.get("env")
        captured["argv"] = list(argv)
        return _DeadProc()

    monkeypatch.setattr(gx.subprocess, "Popen", fake_popen)

    ex = gx.GdExecutor(exe=str(exe), project=str(proj), port_base=_free_port())
    with pytest.raises(VerifyError):
        ex._ensure_connected()   # spawns (dead) -> raises gd_stale after capture
    ex.close()

    env = captured["env"]
    assert env is not None, "Popen must receive an explicit scrubbed env"
    assert "OPENROUTER_API_KEY" not in env
    assert env.get("PATH") == "/usr/bin"
    # the spawn targets the GDScript serve host, not runner.gd
    assert "res://serve_game.gd" in captured["argv"]


def _free_port() -> int:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
