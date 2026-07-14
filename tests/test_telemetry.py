"""Tests for harness.telemetry — the runs ledger. Offline, tmp_path only."""
from __future__ import annotations

import json

from harness.core import telemetry as TEL


# --- Synthetic generate_game results ------------------------------------------

def _flagrant_report():
    """A G0 report with model-discipline errors: forbidden import + syntax noise
    + missing symbols (the model ignored the instructions)."""
    return {
        "passed": False,
        "failure_class": "ENV_ERROR",
        "hint": "code rejected by the sandbox: forbidden import 'os'",
        "layers": {
            "G0_static": {"passed": False, "checks": {
                "sandbox_scan": {"pass": False, "violations": [
                    "line 1: forbidden import 'os'",
                    "syntax error: invalid syntax (line 40)"]},
                "symbols": {"pass": False, "missing": ["checkpoints"]},
            }},
            "G1_rollout": {"passed": False, "checks": {}},
        },
        "witness": None,
    }


def _healthy_failure_report():
    """An UNSOLVED report — a healthy design failure, NOT flagrant."""
    return {
        "passed": False,
        "failure_class": "UNSOLVED",
        "hint": "5/40 episodes reached 'lift_off', none reached 'in_orbit'",
        "layers": {
            "G3_solve": {"passed": False, "checks": {
                "solvable": {"pass": False},
                "episodes": {"pass": True, "run": 60},
            }},
        },
        "witness": None,
    }


def _passing_report():
    return {
        "passed": True, "failure_class": None, "hint": "valid game",
        "layers": {},
        "witness": {"seed": 3, "actions": ["a", "b"], "ticks": 17,
                    "checkpoints": {"lift_off": 4, "in_orbit": 15}},
    }


def _result(verdict="COMPLETED", backend="openrouter", attempts=None):
    return {
        "game_path": "scenes/games/x/x.py",
        "verdict": verdict,
        "backend": backend,
        "attempts": attempts if attempts is not None else
        [{"report": _flagrant_report()},
         {"report": _healthy_failure_report()},
         {"report": _passing_report()}],
        "design": "DESIGN\nTheme: t",
        "integrity": "ok",
    }


# --- record_run -----------------------------------------------------------------

def test_record_run_writes_valid_jsonl(tmp_path):
    path = tmp_path / "runs" / "ledger.jsonl"  # dir created on demand

    entry = TEL.record_run(_result(), "launch a rocket", "vendor/model:free",
                           wall_s=123.456, path=str(path))

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed == json.loads(json.dumps(entry, default=str))
    assert parsed["prompt"] == "launch a rocket"
    assert parsed["backend"] == "openrouter"
    assert parsed["model"] == "vendor/model:free"
    assert parsed["verdict"] == "COMPLETED"
    assert parsed["attempts"] == 3
    assert parsed["wall_s"] == 123.46
    assert "T" in parsed["ts"]  # ISO timestamp


def test_record_run_extracts_failures_and_flagrant(tmp_path):
    path = tmp_path / "ledger.jsonl"
    entry = TEL.record_run(_result(), "p", "m", 1.0, path=str(path))

    # Two non-passing attempts -> two failure entries, in order.
    assert [f["failure_class"] for f in entry["failures"]] == ["ENV_ERROR", "UNSOLVED"]
    f0 = entry["failures"][0]
    assert "G0_static.sandbox_scan" in f0["failed_checks"]
    assert "G0_static.symbols" in f0["failed_checks"]
    assert f0["hint"].startswith("code rejected")
    # G1 has no failing named check -> nothing spurious from it.
    assert not any(c.startswith("G1_") for c in f0["failed_checks"])

    # Flagrant labels: only from the discipline attempt, not from UNSOLVED.
    assert sorted(entry["flagrant"]) == ["forbidden_import", "missing_symbols",
                                         "no_python_block"]

    # Witness data comes from the FINAL attempt.
    assert entry["witness_ticks"] == 17
    assert entry["checkpoints"] == {"lift_off": 4, "in_orbit": 15}


def test_record_run_healthy_failures_are_not_flagrant(tmp_path):
    res = _result(verdict="UNSOLVED",
                  attempts=[{"report": _healthy_failure_report()}] * 3)
    entry = TEL.record_run(res, "p", "m", 2.0, path=str(tmp_path / "l.jsonl"))
    assert entry["flagrant"] == []
    assert len(entry["failures"]) == 3
    assert entry["witness_ticks"] is None
    assert entry["checkpoints"] == {}


def test_record_run_appends(tmp_path):
    path = str(tmp_path / "l.jsonl")
    TEL.record_run(_result(), "one", "m", 1.0, path=path)
    TEL.record_run(_result(verdict="UNSOLVED"), "two", "m", 2.0, path=path)
    lines = (tmp_path / "l.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["prompt"] == "one"
    assert json.loads(lines[1])["prompt"] == "two"


# --- stats ------------------------------------------------------------------------

def _seed_ledger(tmp_path):
    """Two models on openrouter + one template run."""
    path = str(tmp_path / "ledger.jsonl")
    # model A: 2 runs, 1 completed (3 attempts), 1 UNSOLVED, has flagrant labels.
    TEL.record_run(_result(), "pa1", "model-a", 100.0, path=path)
    TEL.record_run(_result(verdict="UNSOLVED",
                           attempts=[{"report": _healthy_failure_report()}] * 4),
                   "pa2", "model-a", 200.0, path=path)
    # model B: 1 run, completed first try, no failures.
    TEL.record_run(_result(attempts=[{"report": _passing_report()}]),
                   "pb1", "model-b", 50.0, path=path)
    # template: 1 INVALIDATED run.
    TEL.record_run(_result(verdict="INVALIDATED", backend="template",
                           attempts=[{"report": _passing_report()}]),
                   "pt1", "template", 1.0, path=path)
    return path


def test_stats_aggregates_two_models(tmp_path):
    path = _seed_ledger(tmp_path)
    data = TEL.stats(path)

    assert data["total_runs"] == 4
    by_model = {g["model"]: g for g in data["groups"]}
    assert set(by_model) == {"model-a", "model-b", "template"}

    a = by_model["model-a"]
    assert a["backend"] == "openrouter"
    assert a["runs"] == 2
    assert a["completed"] == 1
    assert a["completion_rate"] == 0.5
    assert a["invalidated"] == 0
    assert a["mean_attempts_to_completed"] == 3.0   # only the completed run counts
    assert a["mean_wall_s"] == 150.0
    # failure_class histogram across both runs: ENV_ERROR x1, UNSOLVED x(1+4).
    assert a["failure_classes"] == {"ENV_ERROR": 1, "UNSOLVED": 5}
    assert a["flagrant"] == {"forbidden_import": 1, "missing_symbols": 1,
                             "no_python_block": 1}

    b = by_model["model-b"]
    assert b["runs"] == 1 and b["completed"] == 1
    assert b["completion_rate"] == 1.0
    assert b["mean_attempts_to_completed"] == 1.0
    assert b["failure_classes"] == {} and b["flagrant"] == {}

    t = by_model["template"]
    assert t["invalidated"] == 1
    assert t["completed"] == 0
    assert t["mean_attempts_to_completed"] is None


def test_stats_empty_or_missing_ledger(tmp_path):
    data = TEL.stats(str(tmp_path / "nope.jsonl"))
    assert data == {"total_runs": 0, "groups": []}


def test_stats_skips_corrupt_lines(tmp_path):
    path = tmp_path / "l.jsonl"
    TEL.record_run(_result(), "ok", "m", 1.0, path=str(path))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("{corrupt json\n\n")
    data = TEL.stats(str(path))
    assert data["total_runs"] == 1


# --- CLI: game stats ---------------------------------------------------------------

def test_cli_game_stats_json(tmp_path, capsys):
    from harness import cli
    path = _seed_ledger(tmp_path)

    rc = cli.main(["game", "stats", "--path", path, "--json"])
    data = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert data["total_runs"] == 4
    assert {g["model"] for g in data["groups"]} == {"model-a", "model-b", "template"}


def test_cli_game_stats_table_and_empty(tmp_path, capsys):
    from harness import cli
    path = _seed_ledger(tmp_path)

    rc = cli.main(["game", "stats", "--path", path])
    out = capsys.readouterr().out
    assert rc == 0
    assert "RUN LEDGER" in out and "4 runs" in out
    assert "model-a" in out and "model-b" in out
    assert "failure_classes" in out and "flagrant" in out

    rc_empty = cli.main(["game", "stats", "--path", str(tmp_path / "none.jsonl")])
    assert rc_empty == 1
