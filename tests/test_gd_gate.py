"""Pure-python tests for the GDScript (GameAPI) lane's G0 code gates + routing.

Always run (no Godot binary needed):

* the BANNED-API scanner catches every HARD forbidden construct (parameterized
  negative fixtures as inline strings) and passes the clean ``mini_collect.gd``
  fixture; the unseeded GLOBAL rng family is an ADVISORY (a warning, never a G0
  fail — the G1 two-run drift gate judges determinism empirically) while a method
  call on ``self.rng`` stays clean; ``load``/``preload``/``ResourceLoader`` scan
  CLEAN since guardrails v2 (res://-confined, sandbox-contained reads) and
  ``ResourceSaver`` (the write vector into the source-project res://) stays HARD;
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
    GD_REQUIRED_METHODS, is_failure_constant_false, is_hard, scan_advisories,
    scan_gd_source, scan_violations,
)
from harness.verify.gd_exec import classify_check_output  # noqa: E402
from harness.verify.godot_exec import scrubbed_env  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GD_DIR = os.path.join(_ROOT, "tests", "fixtures", "gd_games")
_MINI = os.path.join(_GD_DIR, "mini_collect.gd")


# ====================================================================== #
# 0. WARNING-as-error reclassification at G0 (2026-07-17 parser-friction, item 1)
# ====================================================================== #
# The exact stdout Godot v4.7.stable emits from `--check-only` for each class, captured
# in-image. classify_check_output must admit ONLY the benign warning class and keep every
# genuine parse/type/syntax error fatal, so an untyped `:=`-inference warning stops sinking
# att1 while a real error still fails.

# Class A: a type-inference/Variant WARNING promoted to an error (BENIGN). MUST become ok.
_OUT_WARNING_ONLY = (
    "Godot Engine v4.7.stable.official.5b4e0cb0f - https://godotengine.org\n\n"
    "SCRIPT ERROR: Parse Error: The variable type is being inferred from a Variant value, "
    "so it will be typed as Variant. (Warning treated as error.)\n"
    "          at: GDScript::reload (/tmp/probe_5k2vfy22.gd:4)\n"
    'ERROR: Failed to load script "/tmp/probe_5k2vfy22.gd" with error "Parse error".\n'
    "   at: load (modules/gdscript/gdscript_resource_format.cpp:46)\n")

# Class B: a GENUINE hard type-inference error (fails even with warnings off). MUST stay fatal.
_OUT_HARD_INFER = (
    "Godot Engine v4.7.stable.official.5b4e0cb0f - https://godotengine.org\n\n"
    'SCRIPT ERROR: Parse Error: Cannot infer the type of "first" variable because the '
    "value doesn't have a set type.\n"
    "          at: GDScript::reload (/tmp/probe_fjvzw4ld.gd:4)\n"
    'ERROR: Failed to load script "/tmp/probe_fjvzw4ld.gd" with error "Parse error".\n')

# Class C: a GENUINE syntax error. MUST stay fatal.
_OUT_SYNTAX = (
    "Godot Engine v4.7.stable.official.5b4e0cb0f - https://godotengine.org\n\n"
    'SCRIPT ERROR: Parse Error: Unexpected "Indent" in class body.\n'
    "          at: GDScript::reload (/tmp/probe_xb6t8lk8.gd:3)\n"
    'ERROR: Failed to load script "/tmp/probe_xb6t8lk8.gd" with error "Parse error".\n')


def test_warning_only_check_output_is_reclassified_nonfatal():
    v = classify_check_output(_OUT_WARNING_ONLY)
    assert v["ok"] is True and v["error"] is None
    # the benign warning is recorded (stripped of the escalation tag) for observability
    assert v["warnings"] and "inferred from a Variant value" in v["warnings"][0]
    assert "Warning treated as error" not in v["warnings"][0]


@pytest.mark.parametrize("out,label", [(_OUT_HARD_INFER, "hard-infer"),
                                       (_OUT_SYNTAX, "syntax")])
def test_genuine_parse_errors_stay_fatal(out, label):
    v = classify_check_output(out)
    assert v["ok"] is False, label
    assert v["error"] and "Parse Error" in v["error"]


def test_a_real_error_alongside_a_warning_stays_fatal():
    # Conservative: if ANY SCRIPT ERROR diagnostic lacks the warning tag, the whole load is
    # fatal — a warning can never launder a genuine error into a pass.
    mixed = _OUT_WARNING_ONLY + _OUT_SYNTAX
    v = classify_check_output(mixed)
    assert v["ok"] is False


def test_nonzero_with_no_script_error_diagnostic_stays_fatal():
    # rc!=0 but no SCRIPT ERROR line (timeout, missing file, engine abort) -> fatal.
    assert classify_check_output("ERROR: something else went wrong\n")["ok"] is False
    assert classify_check_output("")["ok"] is False


# ====================================================================== #
# 1. Banned-API scanner — the HARD core (a hit fails G0)
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
    ("resource_saver", 'func build(s): ResourceSaver.save(r, "res://x.tres")'),
    ("gdscript_class", "func build(s): var g = GDScript.new()"),
    ("set_script", "func build(s): node.set_script(x)"),
    ("time", "func build(s): var t = Time.get_ticks_msec()"),
    ("scene_tree", "func build(s): get_tree().quit()"),
]


@pytest.mark.parametrize("rule,src", _BANNED, ids=[r for r, _ in _BANNED])
def test_scanner_catches_each_banned_api(rule, src):
    findings = scan_gd_source("extends Node2D\n" + src)
    rules = {f["rule"] for f in findings}
    assert rule in rules, (rule, rules, src)
    # every HARD finding carries a 1-based line, a token, and hard severity...
    for f in findings:
        assert f["line"] >= 1 and f["token"]
        assert is_hard(f), f
    # ...and lands in the violations payload the G0 gate hard-fails on.
    assert scan_violations("extends Node2D\n" + src), src


# ---------------------------------------------------------------------- #
# 1a. ADVISORY rules — found + surfaced as hints, NEVER a hard violation
#     (guardrails v2: the G1 two-run drift gate empirically catches RNG
#     nondeterminism, so the lexical rules only warn).
# ---------------------------------------------------------------------- #
_ADVISORY = [
    ("randomize", "func build(s): randomize()"),
    ("global_rng", "func build(s): var x = randf()"),
    ("global_seed", "func build(s): seed(5)"),
]


@pytest.mark.parametrize("rule,src", _ADVISORY, ids=[r for r, _ in _ADVISORY])
def test_global_rng_family_is_advisory_not_hard(rule, src):
    full = "extends Node2D\n" + src
    findings = scan_gd_source(full)
    hits = [f for f in findings if f["rule"] == rule]
    assert hits, (rule, findings)                        # still FOUND (a hint)
    for f in hits:
        assert f["severity"] == "advisory" and not is_hard(f), f
        assert "advisory" in str(f) and "banned" not in str(f)
        assert "world_seed" in f["message"]              # the teachable fix
    assert scan_violations(full) == []                   # NOT a hard violation
    assert any(rule in a for a in scan_advisories(full))  # routed to warnings


def test_scanner_catches_global_randi_and_randf_range():
    # The whole unseeded family is still FOUND — as an advisory, not a violation.
    for bad in ("var x = randi()", "var y = randi_range(0, 3)",
                "var z = randf_range(0.0, 1.0)"):
        src = "extends Node2D\nfunc build(s): " + bad
        assert any(f["rule"] == "global_rng" and not is_hard(f)
                   for f in scan_gd_source(src)), bad
        assert scan_violations(src) == [], bad


def test_scanner_allows_self_rng_methods():
    # A method call on the seeded self.rng is the SANCTIONED path -> not a finding.
    for good in ("var x = self.rng.randf()", "var y = rng.randi_range(0, 3)",
                 "var z = rng.randf_range(-5.0, 5.0)"):
        assert scan_gd_source("extends Node2D\nfunc build(s): " + good) == [], good


def test_scanner_flags_rng_randomize_even_on_receiver():
    # randomize() reseeds from the wall clock -> flagged even as rng.randomize()
    # (advisory: the two-run drift gate is the hard judge).
    assert any(f["rule"] == "randomize"
               for f in scan_gd_source("extends Node2D\nfunc build(s): rng.randomize()"))


# ---------------------------------------------------------------------- #
# 1b. ALLOWED since guardrails v2 — load/preload/ResourceLoader scan CLEAN
#     (res://-confined reads; the sandbox + still-hard FileAccess/network/
#     GDScript rules contain everything else).
# ---------------------------------------------------------------------- #
def test_load_preload_and_resource_loader_scan_clean():
    for allowed in ('var r = load("res://x.gd")',
                    'var r = preload("res://chord_util.gd")',
                    'var r = ResourceLoader.load("res://x.tres")',
                    "var r = load(path)"):                 # computed arg: still res://-confined
        src = "extends Node2D\nfunc build(s): " + allowed
        assert scan_gd_source(src) == [], (allowed, scan_gd_source(src))
        assert scan_violations(src) == [] and scan_advisories(src) == []


def test_resource_saver_stays_hard_when_loader_is_free():
    # The ONE narrow guard kept from the old Resource(Loader|Saver) rule: the host
    # runs from a SOURCE project, so res:// is writable — ResourceSaver is the
    # write vector and stays a hard violation.
    src = 'extends Node2D\nfunc build(s): ResourceSaver.save(r, "res://serve_game.gd")'
    assert any(f["rule"] == "resource_saver" and is_hard(f)
               for f in scan_gd_source(src))
    assert scan_violations(src)


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


def test_run_g0_gd_advisory_findings_do_not_fail():
    # Guardrails v2: advisory findings (unseeded global RNG) ride along in the
    # sandbox_scan check for observability but NEVER fail the gate.
    adv = ["line 4: advisory global_rng (...) — 'randf('"]
    g0 = run_g0_gd(_wellformed_gd_facts(), [], adv)
    assert g0["passed"] is True, g0
    assert g0["checks"]["sandbox_scan"]["pass"] is True
    assert g0["checks"]["sandbox_scan"]["advisories"] == adv
    assert g0["checks"]["sandbox_scan"]["violations"] == []


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


# ====================================================================== #
# 6. Static 'is_failure is hardcoded false' detector (WAVE 1 PRESSURE)
# ====================================================================== #
def _read(name):
    with open(os.path.join(_GD_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def test_is_failure_constant_false_flags_no_pressure_fixture():
    # The canonical UNFAILABLE fixture: is_failure() is a literal `return false`.
    assert is_failure_constant_false(_read("no_pressure.gd")) is True
    # mini_collect / softlock_pit / single_action_win / walled_goal are all constant-false.
    for name in ("mini_collect.gd", "softlock_pit.gd", "single_action_win.gd",
                 "walled_goal.gd", "flyoff.gd"):
        assert is_failure_constant_false(_read(name)) is True, name


def test_is_failure_not_constant_false_for_losable_fixture():
    # losable.gd returns `_sunk` (real logic) -> NOT flagged; the dynamic gate decides.
    assert is_failure_constant_false(_read("losable.gd")) is False


@pytest.mark.parametrize("body,const", [
    ("func is_failure() -> bool:\n\treturn false\n", True),
    ("func is_failure() -> bool: return false\n", True),              # inline body
    ("func is_failure():\n\treturn false\n", True),                   # no type hint
    ("func is_failure() -> bool:\n\t# no lose condition\n\treturn false\n", True),
    ("func is_failure() -> bool:\n\treturn   false\n", True),         # extra whitespace
    ("func is_failure() -> bool:\n\treturn _sunk\n", False),          # reads a var
    ("func is_failure() -> bool:\n\treturn _t > 600\n", False),       # a real predicate
    ("func is_failure() -> bool:\n\tif dead: return true\n\treturn false\n", False),
    ("func is_failure() -> bool:\n\treturn true\n", False),           # (degenerate, but not const-FALSE)
])
def test_is_failure_constant_false_cases(body, const):
    src = "extends Node2D\n" + body + "func actions(): return [\"a\", \"b\"]\n"
    assert is_failure_constant_false(src) is const, body


def test_is_failure_constant_false_absent_method_is_false():
    # No is_failure at all -> not 'constant false' (the contract probe handles absence).
    assert is_failure_constant_false("extends Node2D\nfunc build(s): pass\n") is False


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
