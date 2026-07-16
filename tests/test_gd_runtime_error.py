"""Runtime SCRIPT ERROR capture for the GDScript lane (ADOPT #1, MCP_FEEDBACK_TOOLS.md).

A generated game that PARSES but CRASHES AT RUNTIME (a null deref in act(), a build()
that raises) is invisible to the repair loop today: GDScript has no catchable exceptions,
so serve_game.gd's call silently aborts and the wire keeps reporting ``"error": null``.
These tests cover the mechanic that mines the tee'd stderr instead:

* **Pure-python (always run):** the SCRIPT ERROR parser (canned Godot 4.7 text; runtime vs
  parse; dedup+count; determinism; message folded onto the ``at:`` line); and the
  ``os.pread`` offset delta that reads only NEW bytes so successive acts never double-count
  a crash — plus the ``runtime_error`` feedback directive it compiles.
* **End-to-end (skipped without the Godot binary):** ``runtime_crash.gd`` (act() null-deref)
  surfaces the crash site through ``verify_game``'s hint instead of a "dead action";
  ``build_crash.gd`` flips the check-op build-ok to false; a clean game (``mini_collect.gd``)
  produces NO ``runtime_error`` and the same byte-identical rec/report as before.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.gen import feedback as F  # noqa: E402
from harness.verify.executors import find_godot_exe  # noqa: E402
from harness.verify.gameverify import verify_game  # noqa: E402
from harness.verify.gd_exec import (  # noqa: E402
    GdExecutor, parse_runtime_errors, read_stderr_delta,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GD = os.path.join(_ROOT, "tests", "fixtures", "gd_games")
_MINI = os.path.join(_GD, "mini_collect.gd")
_RUNTIME_CRASH = os.path.join(_GD, "runtime_crash.gd")
_BUILD_CRASH = os.path.join(_GD, "build_crash.gd")

GODOT_EXE = find_godot_exe()
requires_godot = pytest.mark.skipif(GODOT_EXE is None, reason="Godot binary not present")

_REPORT_KEYS = {"passed", "failure_class", "layers", "hint", "warnings",
                "progress", "witness"}

# --- Canned Godot 4.7 stderr (captured in-image, gi-certifier.sif @ 4.7.stable) -------
_BANNER = "Godot Engine v4.7.stable.official.5b4e0cb0f - https://godotengine.org\n\n"
ACT_CRASH = (
    "SCRIPT ERROR: Invalid access to property or key 'position' on a base object of "
    "type 'Nil'.\n"
    "          at: act (gdscript://-9223371885574610264.gd:6)\n"
    "          GDScript backtrace (most recent call first):\n"
    "              [0] act (gdscript://-9223371885574610264.gd:6)\n"
    "              [1] _initialize (res://serve_game.gd:456)\n"
)
BUILD_CRASH_TXT = (
    "SCRIPT ERROR: Invalid call. Nonexistent function 'set_position' in base 'Nil'.\n"
    "          at: build (gdscript://-9223371885574610264.gd:4)\n"
    "          GDScript backtrace (most recent call first):\n"
    "              [0] build (gdscript://-9223371885574610264.gd:4)\n"
    "              [1] _initialize (res://serve_game.gd:14)\n"
)
PARSE_TXT = (
    'SCRIPT ERROR: Parse Error: Unexpected "Indent" in class body.\n'
    "          at: GDScript::reload (gdscript://-9223371885524278614.gd:3)\n"
    "          GDScript backtrace (most recent call first):\n"
    "              [0] _initialize (res://serve_game.gd:21)\n"
)


def _src(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _free_port() -> int:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ====================================================================== #
# 1. Parser — canned Godot text (pure python, always run)
# ====================================================================== #
def test_parse_runtime_act_crash():
    recs = parse_runtime_errors(_BANNER + ACT_CRASH)
    assert len(recs) == 1
    r = recs[0]
    assert r["method"] == "act" and r["line"] == 6 and r["kind"] == "runtime"
    assert "'position'" in r["message"] and r["count"] == 1


def test_parse_runtime_build_crash():
    r = parse_runtime_errors(BUILD_CRASH_TXT)[0]
    assert r["method"] == "build" and r["line"] == 4 and r["kind"] == "runtime"


def test_parse_error_kind_is_parse():
    r = parse_runtime_errors(PARSE_TXT)[0]
    assert r["kind"] == "parse" and r["line"] == 3


def test_parse_prefers_game_frame_line_not_host():
    """The reported line is the gdscript:// (game) frame, never the res://serve_game.gd
    host frame that also appears in the backtrace."""
    r = parse_runtime_errors(ACT_CRASH)[0]
    assert r["line"] == 6                       # the game line, not 456 (serve host)


def test_parse_clean_stderr_is_empty():
    assert parse_runtime_errors(_BANNER) == []
    assert parse_runtime_errors("") == []
    assert parse_runtime_errors("nothing to see, no errors here\n") == []


def test_parse_dedup_and_count():
    """A crash that fires every tick collapses to ONE record with an occurrence count."""
    recs = parse_runtime_errors(ACT_CRASH * 4)
    assert len(recs) == 1 and recs[0]["count"] == 4


def test_parse_message_folded_onto_at_line():
    """Godot sometimes folds ``at: …`` onto the message line; the message is still clean
    and the frame is still parsed."""
    folded = ("SCRIPT ERROR: Cannot call 'length' on a null value. "
              "at: act (gdscript://x.gd:42)\n")
    r = parse_runtime_errors(folded)[0]
    assert r["message"] == "Cannot call 'length' on a null value."
    assert r["method"] == "act" and r["line"] == 42


def test_parse_deterministic():
    """Same crash text -> byte-identical record (determinism the capture relies on)."""
    assert parse_runtime_errors(ACT_CRASH) == parse_runtime_errors(ACT_CRASH)


def test_parse_keeps_first_n():
    blocks = "".join(
        f"SCRIPT ERROR: err {i}.\n          at: m{i} (gdscript://x.gd:{i})\n"
        for i in range(20))
    recs = parse_runtime_errors(blocks, max_records=5)
    assert len(recs) == 5 and recs[0]["line"] == 0 and recs[4]["line"] == 4


# ====================================================================== #
# 2. os.pread offset delta — no double-count (pure python, always run)
# ====================================================================== #
def test_read_stderr_delta_reads_only_new_bytes():
    """The delta reader returns bytes appended since ``offset`` and advances it — so a
    second read with no new writes is empty (the no-double-count invariant)."""
    with tempfile.TemporaryFile("w+b") as f:
        off = 0
        f.write(_BANNER.encode()); f.flush()
        text, off = read_stderr_delta(f, off)
        assert text == _BANNER and off == len(_BANNER.encode())

        # No new writes -> empty delta, offset unchanged (idempotent).
        text2, off2 = read_stderr_delta(f, off)
        assert text2 == "" and off2 == off

        # Append a crash block; the delta is exactly the new bytes.
        f.write(ACT_CRASH.encode()); f.flush()
        text3, off3 = read_stderr_delta(f, off2)
        assert parse_runtime_errors(text3)[0]["method"] == "act"

        # Reading again from the advanced offset yields nothing — the earlier crash is
        # NOT re-reported across successive acts.
        text4, off4 = read_stderr_delta(f, off3)
        assert text4 == "" and parse_runtime_errors(text4) == []


def test_read_stderr_delta_does_not_move_write_position():
    """os.pread must not disturb the file's own position, so interleaved appends keep
    accumulating (the shared-offset hazard the mechanic is built to avoid)."""
    with tempfile.TemporaryFile("w+b") as f:
        f.write(b"aaa"); f.flush()
        _, off = read_stderr_delta(f, 0)
        f.write(b"bbb"); f.flush()                # must append, not overwrite
        text, _ = read_stderr_delta(f, off)
        assert text == "bbb"
        # Whole-file view confirms nothing was clobbered.
        assert os.pread(f.fileno(), 6, 0) == b"aaabbb"


def test_read_stderr_delta_handles_none_log():
    assert read_stderr_delta(None, 0) == ("", 0)


# ====================================================================== #
# 3. Feedback — the runtime_error directive (pure python, always run)
# ====================================================================== #
def test_feedback_compiles_runtime_error_directive():
    rec = parse_runtime_errors(ACT_CRASH)[0]
    ds = F.compile_directives({"runtime_error": rec})
    assert len(ds) == 1
    d = ds[0]
    assert d.source == "runtime_error" and d.origin == "runtime"
    assert "act()" in d.text and "line 6" in d.text and "'position'" in d.text
    assert d.detail["method"] == "act" and d.detail["line"] == 6


def test_feedback_runtime_error_first_and_deterministic():
    rec = parse_runtime_errors(ACT_CRASH)[0]
    oracle = {"runtime_error": rec,
              "g4": {"findings": [{"outcome": "single_action_win",
                                   "action": "up", "ticks": 3}]}}
    d1 = F.compile_directives(oracle)
    d2 = F.compile_directives(oracle)
    assert [d.source for d in d1] == ["runtime_error", "single_action_win"]
    assert [x.to_dict() for x in d1] == [x.to_dict() for x in d2]


def test_feedback_runtime_error_moved_crash_is_distinct_defect():
    """A crash that MOVES after a fix (different method@line) is a distinct defect, so
    its fingerprint differs — the convergence guard only stalls on the SAME crash."""
    act = parse_runtime_errors(ACT_CRASH)[0]
    build = parse_runtime_errors(BUILD_CRASH_TXT)[0]
    fp_act = F.compile_directives({"runtime_error": act})[0].fingerprint
    fp_build = F.compile_directives({"runtime_error": build})[0].fingerprint
    assert fp_act != fp_build


def test_feedback_runtime_error_finding_helper():
    rec = parse_runtime_errors(ACT_CRASH)[0]
    assert F.runtime_error_finding({"runtime_error": rec}) == rec
    assert F.runtime_error_finding({}) == {}
    assert F.compile_directives({}) == []          # clean game -> no directive


# ====================================================================== #
# 4. End-to-end capture through the serve (skipped without Godot)
# ====================================================================== #
@requires_godot
def test_run_batch_populates_error_on_act_crash():
    """The wire error field (rec['error']) is populated python-side from the stderr delta
    when act() crashes, and a runtime_error record is attached; a clean game leaves both
    absent (byte-identical rec)."""
    ex = GdExecutor(port_base=_free_port())
    try:
        rec = ex.run_batch(_src(_RUNTIME_CRASH),
                           [{"seed": 0, "actions": ["up"] * 6}], 6)[0]
        assert rec.get("error"), rec
        rte = rec.get("runtime_error")
        assert rte and rte["method"] == "act" and rte["kind"] == "runtime"
        assert isinstance(rte["line"], int)
    finally:
        ex.close()


@requires_godot
def test_run_batch_clean_game_has_no_runtime_error():
    ex = GdExecutor(port_base=_free_port())
    try:
        rec = ex.run_batch(_src(_MINI),
                           [{"seed": 0, "actions": ["up"] * 6}], 6)[0]
        assert rec.get("error") is None
        assert "runtime_error" not in rec and "runtime_errors" not in rec
        assert ex.runtime_errors == []
    finally:
        ex.close()


@requires_godot
def test_run_batch_no_double_count_across_episodes():
    """After a crashing episode captures its error, a later non-acting episode reads only
    NEW (empty) stderr — it must NOT re-report the earlier crash (monotonic offset)."""
    ex = GdExecutor(port_base=_free_port())
    try:
        crash = ex.run_batch(_src(_RUNTIME_CRASH),
                            [{"seed": 0, "actions": ["up"] * 6}], 6)[0]
        assert crash.get("runtime_error")
        # A second batch with no actions calls act() zero times -> no new SCRIPT ERROR.
        quiet = ex.run_batch(_src(_RUNTIME_CRASH), [{"seed": 0, "actions": []}], 6)[0]
        assert quiet.get("error") is None and "runtime_error" not in quiet
    finally:
        ex.close()


@requires_godot
def test_run_check_overrides_build_ok_on_build_crash():
    """A build() that crashes at runtime must NOT report build ok:true — run_check reads
    the stderr delta and flips it, so a crashed build stops masquerading downstream."""
    ex = GdExecutor(port_base=_free_port())
    try:
        facts = ex.run_check(_src(_BUILD_CRASH))
        assert facts["build"]["ok"] is False, facts["build"]
        assert "build" in facts["build"]["error"]
        assert facts["runtime_error"]["method"] == "build"
    finally:
        ex.close()


@requires_godot
def test_run_check_clean_game_build_ok_true():
    ex = GdExecutor(port_base=_free_port())
    try:
        facts = ex.run_check(_src(_MINI))
        assert facts["build"]["ok"] is True
        assert "runtime_error" not in facts
        assert ex.runtime_errors == []
    finally:
        ex.close()


@requires_godot
def test_capture_deterministic_across_sessions():
    """Same crash -> same record across two independent serve sessions."""
    def _cap():
        ex = GdExecutor(port_base=_free_port())
        try:
            rec = ex.run_batch(_src(_RUNTIME_CRASH),
                              [{"seed": 0, "actions": ["up"] * 6}], 6)[0]
            r = rec["runtime_error"]
            return (r["method"], r["line"], r["message"], r["kind"])
        finally:
            ex.close()
    assert _cap() == _cap()


# ====================================================================== #
# 5. End-to-end through verify_game — the hint names the crash (skipped w/o Godot)
# ====================================================================== #
@requires_godot
def test_verify_runtime_crash_hint_names_act_not_dead_action():
    """runtime_crash.gd: G0 passes (parse+build+contract clean), G1 fails — but the hint
    names the act() crash site + message, NOT the misleading "dead action" symptom."""
    rep = verify_game(_RUNTIME_CRASH, sandboxed=False)
    assert rep["passed"] is False
    assert rep["engine"] == "gdscript"
    assert rep["layers"]["G0_static"]["passed"] is True
    assert rep["layers"]["G1_rollout"]["passed"] is False
    rte = rep.get("runtime_error")
    assert rte and rte["method"] == "act" and rte["kind"] == "runtime"
    assert isinstance(rte["line"], int)
    hint = rep["hint"]
    assert "act()" in hint and "crashed" in hint and f"res://game.gd:{rte['line']}" in hint
    # NOT the raw G1 efficacy symptom ("dead action(s) with no effect on the world …").
    assert "with no effect on the world" not in hint


@requires_godot
def test_verify_build_crash_hint_names_build():
    """build_crash.gd: the build() crash flips the G0 builds gate and the hint names the
    build() crash site instead of a downstream "no controlled body"."""
    rep = verify_game(_BUILD_CRASH, sandboxed=False)
    assert rep["passed"] is False
    assert rep["layers"]["G0_static"]["passed"] is False
    assert rep["layers"]["G0_static"]["checks"]["builds"]["pass"] is False
    rte = rep.get("runtime_error")
    assert rte and rte["method"] == "build"
    assert "build()" in rep["hint"] and "controlled" not in rep["hint"]


@requires_godot
def test_verify_clean_game_has_no_runtime_error_key():
    """A clean certifying game keeps the exact report key set — the runtime_error finding
    is added ONLY when a crash was captured (zero-overhead on healthy games)."""
    rep = verify_game(_MINI, sandboxed=False)
    assert rep["passed"] is True
    assert "runtime_error" not in rep
    assert set(rep) == _REPORT_KEYS | {"engine"}
