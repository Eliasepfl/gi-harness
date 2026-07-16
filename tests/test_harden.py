"""Tests for the feedback-compiler DRIVER (harness.gen.harden) — revise-from-current-
source + the convergence guard + skill routing on repair turns.

Hermetic: the oracle / revise / skill seams are monkeypatched (harden.verify_fn,
attack_fn, g3_fn, revise_fn, render_skills_fn), so no Godot / torch / network. The
in-image end-to-end smoke on a real .gd fixture lives in test_gd_lane-style runs; this
file proves the WIRING, the guard, the no-overwrite invariant, the ledger, and that the
revise turn routes DOMAIN skills on the directive text while KEEPING the godot-master
orchestrator (Elias's corrected routing: a revise turn is the README's "audit an
existing project" case, so orchestrator=True; the QUERY is the directive, not the prompt).
"""
from __future__ import annotations

import json
import os

import pytest

from harness.gen import harden as H
from harness.gen import feedback as F
from harness.gen import skill_context as SC

try:
    from harness.verify.executors import find_godot_exe
    _GODOT = find_godot_exe()
except Exception:  # noqa: BLE001
    _GODOT = None
requires_godot = pytest.mark.skipif(_GODOT is None, reason="Godot binary not present")

_FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "gd_skills")
_GD_GAMES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "gd_games")


def _write_game(path, body="extends Node2D\n# original certified game\n"):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return body


def _g4_single_action():
    return {"schema": "g4_report/v1", "grade": "open",
            "findings": [{"outcome": "single_action_win", "hard": False,
                          "action": "right", "ticks": 37, "reproducer": {"seed": 0},
                          "evidence": {}}]}


def _g4_clean():
    return {"schema": "g4_report/v1", "grade": "hardened", "findings": []}


# ======================================================================== #
# Skill routing on the repair turn (spec addendum + Elias correction):
# the QUERY is the DIRECTIVE text (not the game prompt); the godot-master orchestrator
# is KEPT (a revise turn is the README's "audit an existing project" case), with the
# domain layer selected by the directive.
# ======================================================================== #
def _master_lib(tmp_path):
    """A tiny gd-agentic library that DOES carry a godot-master orchestrator, so we can
    assert it leads the repair-turn skill block (the shared gd_skills fixture has none)."""
    root = tmp_path / "lib"
    (root / "skills" / "godot-master").mkdir(parents=True)
    (root / "skills" / "godot-2d-physics").mkdir(parents=True)
    (root / "skills" / "godot-audio-mixing").mkdir(parents=True)
    (root / "skills" / "godot-master" / "SKILL.md").write_text(
        "---\nname: godot-master\n---\nMASTER ORCHESTRATOR decision matrix body.\n")
    (root / "skills" / "godot-2d-physics" / "SKILL.md").write_text(
        "---\nname: godot-2d-physics\n---\nPHYSICS collision rigidbody patterns.\n")
    (root / "skills" / "godot-audio-mixing" / "SKILL.md").write_text(
        "---\nname: godot-audio-mixing\n---\nAUDIO bus mixing patterns.\n")
    (root / "skills_index.json").write_text(json.dumps([
        {"name": "godot-2d-physics",
         "keywords": ["physics", "collision", "rigidbody", "area2d", "move_and_slide"]},
        {"name": "godot-audio-mixing",
         "keywords": ["audio", "music", "AudioStreamPlayer", "bus", "volume"]},
    ]))
    return str(root)


def test_default_render_skills_routes_on_directive_keeping_orchestrator(monkeypatch):
    seen = {}

    def spy(prompt, *args, **kwargs):
        seen["prompt"] = prompt
        seen["orchestrator"] = kwargs.get("orchestrator")
        seen["use_llm"] = kwargs.get("use_llm")
        return "SKILLBLOCK"

    monkeypatch.setattr(SC, "render_skill_context", spy)
    out = H._default_render_skills("fix the collision rigidbody gate", root=_FIX,
                                   use_llm=False)
    assert out == "SKILLBLOCK"
    assert seen["prompt"] == "fix the collision rigidbody gate"   # routed on DIRECTIVE
    assert seen["orchestrator"] is True                           # orchestrator KEPT
    assert seen["use_llm"] is False


def test_revise_skill_context_leads_with_master_and_routes_on_directive(tmp_path):
    lib = _master_lib(tmp_path)
    game = tmp_path / "g.gd"
    _write_game(str(game))
    captured = {}

    def fake_revise(source, directive, *, out_dir, backend, max_repairs, engine,
                    skill_context):
        captured["skill_context"] = skill_context
        captured["directive"] = directive
        return {"verdict": "COMPLETED", "game_path": str(game)}

    H.revise_fn = fake_revise
    try:
        directive = F.Directive(source="broken_gating", origin="g4",
                                checkpoint_keys=("k",),
                                text="Fix the collision rigidbody area2d move_and_slide gate",
                                fingerprint="fp_x")
        H.revise_with_directives(str(game), [directive], out_dir=str(tmp_path / "sb"),
                                 backend="template", engine="gdscript",
                                 skill_root=lib, skill_use_llm=False)
    finally:
        H.revise_fn = H._default_revise

    sc = captured["skill_context"]
    assert "godot-master" in sc                          # orchestrator PRESENT
    assert "godot-2d-physics" in sc                       # domain layer routed on directive
    # master leads the block (appears before the routed domain skill)
    assert sc.index("godot-master") < sc.index("godot-2d-physics")
    # a DIFFERENT directive text routes to a DIFFERENT domain skill (routing is on text)
    audio = SC.render_skill_context("audio music AudioStreamPlayer bus volume",
                                    orchestrator=True, root=lib, use_llm=False)
    assert "godot-audio-mixing" in audio
    assert "godot-2d-physics" not in audio


# ======================================================================== #
# Convergence guard — same fingerprint twice -> REPAIR_STALLED, source untouched
# ======================================================================== #
def _install_seams(monkeypatch, *, g4, revise_verdict, sandbox):
    monkeypatch.setattr(H, "verify_fn", lambda p: {"passed": False,
                                                   "failure_class": "GOAL_ERROR"})
    monkeypatch.setattr(H, "attack_fn",
                        lambda gp, src, rep, **kw: g4() if callable(g4) else g4)
    monkeypatch.setattr(H, "render_skills_fn", lambda text, **kw: "")

    calls = {"n": 0}

    def fake_revise(source, directive, *, out_dir, backend, max_repairs, engine,
                    skill_context):
        calls["n"] += 1
        os.makedirs(out_dir, exist_ok=True)
        # write the "fix" into the SANDBOX (never the original path)
        p = os.path.join(out_dir, "revised.gd")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("extends Node2D\n# a revised (still-broken) game\n")
        return {"verdict": revise_verdict, "game_path": p}

    monkeypatch.setattr(H, "revise_fn", fake_revise)
    return calls


def test_repair_stalled_when_fingerprint_repeats_and_source_untouched(tmp_path, monkeypatch):
    game = tmp_path / "single.gd"
    original = _write_game(str(game))
    ledger = tmp_path / "ledger.jsonl"

    # The attack ALWAYS returns the same single_action_win -> the "fix" never removes it.
    _install_seams(monkeypatch, g4=_g4_single_action, revise_verdict="COMPLETED",
                   sandbox=tmp_path)

    report = H.harden_game(str(game), out_dir=str(tmp_path / "sb"), backend="template",
                           run_g3=False, max_rounds=3, ledger_path=str(ledger))

    assert report["final_verdict"] == "REPAIR_STALLED"
    assert report["directives_issued"] == 1            # issued once; round 2 detected repeat
    # the ORIGINAL certified file on disk was NEVER overwritten
    assert report["original_untouched"] is True
    with open(game, encoding="utf-8") as fh:
        assert fh.read() == original
    # ledger recorded the directive + verdicts
    lines = [json.loads(l) for l in ledger.read_text().splitlines()]
    assert any(e["event"] == "harden_round" and e["verdict"] == "REPAIR_STALLED"
               for e in lines)
    assert all("fingerprint" in e and "directive" in e for e in lines)


def test_failed_recert_never_overwrites_certified_source(tmp_path, monkeypatch):
    game = tmp_path / "single.gd"
    original = _write_game(str(game))
    ledger = tmp_path / "ledger.jsonl"

    # the stub backend returns BROKEN code (does NOT re-certify: verdict != COMPLETED)
    _install_seams(monkeypatch, g4=_g4_single_action, revise_verdict="UNSOLVED",
                   sandbox=tmp_path)

    report = H.harden_game(str(game), out_dir=str(tmp_path / "sb"), backend="template",
                           run_g3=False, max_rounds=3, ledger_path=str(ledger))

    assert report["final_verdict"] == "REPAIR_FAILED"
    assert report["original_untouched"] is True
    assert report["final_game_path"] == str(game)      # stayed on the certified version
    with open(game, encoding="utf-8") as fh:
        assert fh.read() == original


def test_clean_game_reports_hardened_no_directives(tmp_path, monkeypatch):
    game = tmp_path / "clean.gd"
    _write_game(str(game))
    monkeypatch.setattr(H, "verify_fn", lambda p: {"passed": True})
    monkeypatch.setattr(H, "attack_fn", lambda gp, src, rep, **kw: _g4_clean())
    monkeypatch.setattr(H, "render_skills_fn", lambda text, **kw: "")

    report = H.harden_game(str(game), out_dir=str(tmp_path / "sb"), backend="template",
                           run_g3=False, max_rounds=3, ledger_path=str(tmp_path / "l.jsonl"))
    assert report["final_verdict"] == "HARDENED"
    assert report["directives_issued"] == 0


def test_successful_fix_then_clean_promotes_and_hardens(tmp_path, monkeypatch):
    game = tmp_path / "g.gd"
    original = _write_game(str(game))
    ledger = tmp_path / "ledger.jsonl"

    # round 1 attack finds a single_action_win; the revise re-certifies; round 2 attack
    # is clean -> HARDENED, with the certified working copy moved into the sandbox.
    state = {"round": 0}

    def attack(gp, src, rep, **kw):
        state["round"] += 1
        return _g4_single_action() if state["round"] == 1 else _g4_clean()

    monkeypatch.setattr(H, "verify_fn", lambda p: {"passed": True})
    monkeypatch.setattr(H, "attack_fn", attack)
    monkeypatch.setattr(H, "render_skills_fn", lambda text, **kw: "")

    def fake_revise(source, directive, *, out_dir, backend, max_repairs, engine,
                    skill_context):
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, "fixed.gd")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("extends Node2D\n# a genuinely fixed game\n")
        return {"verdict": "COMPLETED", "game_path": p}

    monkeypatch.setattr(H, "revise_fn", fake_revise)

    report = H.harden_game(str(game), out_dir=str(tmp_path / "sb"), backend="template",
                           run_g3=False, max_rounds=3, ledger_path=str(ledger))
    assert report["final_verdict"] == "HARDENED"
    assert report["directives_issued"] == 1
    assert report["final_game_path"] != str(game)      # adopted the re-certified copy
    assert report["original_untouched"] is True
    with open(game, encoding="utf-8") as fh:
        assert fh.read() == original


def test_continue_training_signal_from_g3(tmp_path, monkeypatch):
    game = tmp_path / "g.gd"
    _write_game(str(game))
    monkeypatch.setattr(H, "verify_fn", lambda p: {"passed": True})
    monkeypatch.setattr(H, "attack_fn", lambda gp, src, rep, **kw: _g4_clean())
    monkeypatch.setattr(H, "render_skills_fn", lambda text, **kw: "")
    monkeypatch.setattr(H, "g3_fn", lambda gp, budget_steps, **kw: {
        "still_improving": True, "learnable": False, "checkpoint_keys": ["m1"],
        "per_checkpoint_latch_rate": {"m1": 0.4}, "stochastic_success_rate": 0.1})

    report = H.harden_game(str(game), out_dir=str(tmp_path / "sb"), backend="template",
                           run_g3=True, budget_steps=1_000_000, max_rounds=3,
                           ledger_path=str(tmp_path / "l.jsonl"))
    assert report["final_verdict"] == "CONTINUE_TRAINING"
    assert report["directives_issued"] == 0


# ======================================================================== #
# SEVERITY-TIERED budget (2026-07-15 harden wave): DIFFICULTY findings (g3_plateau /
# g3_difficulty) are HARD-TO-LEARN, not broken — they re-certify unchanged, so they earn a
# small `difficulty_budget` nudge and a SUCCESS-ish terminal (HARDENED_HARD), never the
# MAX_ROUNDS/REPAIR_* failure track a DEFECT gets. The certified defect-clean game is
# preserved: a difficulty nudge never advances the deliverable.
# ======================================================================== #
def _g3_plateau():
    """A plateaued learnability curve -> the g3_plateau (DIFFICULTY) directive."""
    return {"still_improving": False, "learnable": False,
            "checkpoint_keys": ["m1", "m2", "m3"],
            "per_checkpoint_latch_rate": {"m1": 1.0, "m2": 0.9, "m3": 0.05},
            "stochastic_success_rate": 0.0, "final_success_rate": 0.0,
            "budget_steps": 1_000_000, "n_eval": 32}


def _install_difficulty_seams(monkeypatch, *, attack, revise_verdict="COMPLETED",
                              g3=_g3_plateau):
    """Certified game (verify passes so G3' runs), a configurable G4 + G3', and a revise
    that writes only into the sandbox. Returns the call log (paths + verdicts per revise)."""
    monkeypatch.setattr(H, "verify_fn", lambda p: {"passed": True})
    monkeypatch.setattr(H, "attack_fn",
                        lambda gp, src, rep, **kw: attack() if callable(attack) else attack)
    monkeypatch.setattr(H, "g3_fn", lambda gp, budget_steps, **kw: g3())
    monkeypatch.setattr(H, "render_skills_fn", lambda text, **kw: "")

    calls = []

    def fake_revise(source, directive, *, out_dir, backend, max_repairs, engine,
                    skill_context):
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, "revised.gd")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("extends Node2D\n# a revised game\n")
        calls.append({"out_dir": out_dir, "path": p, "directive": directive})
        return {"verdict": revise_verdict, "game_path": p}

    monkeypatch.setattr(H, "revise_fn", fake_revise)
    return calls


def test_difficulty_only_spends_one_nudge_then_hardened_hard(tmp_path, monkeypatch):
    game = tmp_path / "hard.gd"
    original = _write_game(str(game))
    ledger = tmp_path / "ledger.jsonl"
    calls = _install_difficulty_seams(monkeypatch, attack=_g4_clean)

    report = H.harden_game(str(game), out_dir=str(tmp_path / "sb"), backend="template",
                           run_g3=True, budget_steps=1_000_000, max_rounds=3,
                           difficulty_budget=1, ledger_path=str(ledger))

    # A difficulty is hard-to-learn, not broken: <= difficulty_budget rounds, HARDENED_HARD
    # (a SUCCESS-ish terminal), NOT MAX_ROUNDS/REPAIR_*.
    assert report["final_verdict"] == "HARDENED_HARD"
    assert report["rounds"] == 1                        # exactly one nudge, no grind
    assert len(calls) == 1                              # one bonus revise attempt
    assert report["directives_issued"] == 1
    # the certified defect-clean game is PRESERVED — the nudge never advances the deliverable
    assert report["final_game_path"] == str(game)
    assert report["original_untouched"] is True
    with open(game, encoding="utf-8") as fh:
        assert fh.read() == original


def test_difficulty_budget_zero_never_nudges(tmp_path, monkeypatch):
    game = tmp_path / "hard.gd"
    _write_game(str(game))
    calls = _install_difficulty_seams(monkeypatch, attack=_g4_clean)

    report = H.harden_game(str(game), out_dir=str(tmp_path / "sb"), backend="template",
                           run_g3=True, max_rounds=3, difficulty_budget=0,
                           ledger_path=str(tmp_path / "l.jsonl"))
    assert report["final_verdict"] == "HARDENED_HARD"
    assert len(calls) == 0                              # budget 0 -> no revise at all
    assert report["directives_issued"] == 0
    assert report["final_game_path"] == str(game)
    assert report["original_untouched"] is True


def test_difficulty_nudge_failing_recert_is_not_a_failure(tmp_path, monkeypatch):
    # The nudge does NOT re-certify (verdict != COMPLETED). For a DEFECT this is
    # REPAIR_FAILED; for a DIFFICULTY it is still HARDENED_HARD — the game is valid and
    # defect-clean, merely hard to learn, and the certified version is preserved.
    game = tmp_path / "hard.gd"
    original = _write_game(str(game))
    _install_difficulty_seams(monkeypatch, attack=_g4_clean, revise_verdict="UNSOLVED")

    report = H.harden_game(str(game), out_dir=str(tmp_path / "sb"), backend="template",
                           run_g3=True, max_rounds=3, difficulty_budget=1,
                           ledger_path=str(tmp_path / "l.jsonl"))
    assert report["final_verdict"] == "HARDENED_HARD"
    assert report["final_verdict"] not in ("REPAIR_FAILED", "MAX_ROUNDS", "REPAIR_STALLED")
    assert report["final_game_path"] == str(game)      # certified version preserved
    assert report["original_untouched"] is True
    with open(game, encoding="utf-8") as fh:
        assert fh.read() == original


def test_defect_and_difficulty_mix_spends_defects_first(tmp_path, monkeypatch):
    game = tmp_path / "mix.gd"
    original = _write_game(str(game))
    ledger = tmp_path / "ledger.jsonl"

    # round 1 attack surfaces a DEFECT (single_action_win) alongside a persistent plateau;
    # round 2 (after the defect fix re-certifies) the attack is clean, plateau remains.
    state = {"round": 0}

    def attack():
        state["round"] += 1
        return _g4_single_action() if state["round"] == 1 else _g4_clean()

    calls = _install_difficulty_seams(monkeypatch, attack=attack)

    report = H.harden_game(str(game), out_dir=str(tmp_path / "sb"), backend="template",
                           run_g3=True, max_rounds=3, difficulty_budget=1,
                           ledger_path=str(ledger))

    assert report["final_verdict"] == "HARDENED_HARD"
    recs = report["round_records"]
    # round 1 addressed the DEFECT first (difficulty deferred), round 2 nudged the difficulty
    assert [d["source"] for d in recs[0]["directives"]] == ["single_action_win"]
    assert recs[0]["verdict"] == "COMPLETED"
    assert recs[0].get("deferred_difficulty") == ["g3_plateau"]
    assert recs[1].get("severity") == "difficulty" and recs[1].get("nudged") is True
    assert [d["source"] for d in recs[1]["directives"]] == ["g3_plateau"]
    assert report["directives_issued"] == 2            # 1 defect + 1 difficulty
    # the DEFECT fix (round 1 sandbox) is the deliverable; the difficulty nudge did NOT
    # advance it, and the on-disk original is untouched throughout.
    assert report["final_game_path"] == calls[0]["path"]
    assert report["final_game_path"] != calls[1]["path"]
    assert report["original_untouched"] is True
    with open(game, encoding="utf-8") as fh:
        assert fh.read() == original


# ======================================================================== #
# In-image end-to-end smoke on the real single_action_win.gd fixture (Godot).
# Uses the REAL oracle + revise seams (template backend, no LLM). Verifies the whole
# loop: G4 surfaces single_action_win -> a directive compiles -> the revise loop runs
# -> the game either gets fixed or the guard trips REPAIR_STALLED, the ledger is
# written, and the certified fixture on disk is never overwritten.
# ======================================================================== #
@requires_godot
def test_harden_smoke_single_action_win_in_image(tmp_path, monkeypatch):
    import shutil
    src = os.path.join(_GD_GAMES, "single_action_win.gd")
    game = tmp_path / "single_action_win.gd"
    shutil.copy(src, str(game))
    original = game.read_text()
    ledger = tmp_path / "ledger.jsonl"

    # Route the harness ledger writes (gamegen telemetry) to the sandbox, not runs/.
    monkeypatch.setenv("HARNESS_LEDGER", str(tmp_path / "gamegen_ledger.jsonl"))
    monkeypatch.setenv("HARNESS_ENGINE", "gdscript")

    report = H.harden_game(str(game), out_dir=str(tmp_path / "sb"), backend="template",
                           tiers=(0,), stale=False, run_g3=False, max_rounds=2,
                           ledger_path=str(ledger))

    # The compiler produced at least one directive (the single-action defect) and the
    # loop ran to a known terminal verdict.
    assert report["directives_issued"] >= 1
    assert report["rounds"] >= 1
    assert report["final_verdict"] in {
        "HARDENED", "BULLETPROOF", "REPAIR_STALLED", "REPAIR_FAILED", "MAX_ROUNDS",
        "OPEN_UNMAPPED"}
    # The certified fixture on disk was never overwritten by a candidate fix.
    assert report["original_untouched"] is True
    assert game.read_text() == original
    # The ledger recorded the round(s) with the compiled single_action_win directive.
    lines = [json.loads(l) for l in ledger.read_text().splitlines()]
    assert lines and any(e["source_finding"] == "single_action_win" for e in lines)
