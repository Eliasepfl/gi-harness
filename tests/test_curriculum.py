"""Tests for the curriculum loop (harness.gen.curriculum), WITHOUT torch or network.

The three deliverable-3 planks:
  * profile/grade thresholds on synthetic verify+g3p fixtures — all 5 grades;
  * directive content anchored to the stalling milestone;
  * a curriculum_round dry test with backend "template" and a MOCKED g3_prime
    (no PPO training) asserting the loop wiring + the ledger event.

Every test is hermetic: verify_game, g3_prime and generate_game are stubbed via
the module-level seams (curriculum.verify_fn / g3_prime_fn / generate_fn).
"""
from __future__ import annotations

import json

import pytest

from harness.gen import curriculum as C
from harness.repair_language import PRESERVE_CLAUSE


# ======================================================================== #
# Fixture builders (synthetic verify report + G3' result)
# ======================================================================== #
def vreport(*, witness_ticks=100, checkpoints=None, replays=50, passed=True,
            solver="tree"):
    checkpoints = checkpoints if checkpoints is not None else {"m1": 10, "m2": 50}
    return {
        "passed": passed,
        "failure_class": None if passed else "UNSOLVED",
        "witness": {"seed": 0, "actions": ["x"] * witness_ticks,
                    "ticks": witness_ticks, "checkpoints": dict(checkpoints)},
        "layers": {"G3_solve": {"passed": passed, "checks": {
            "episodes": {"pass": True, "run": replays, "solver": solver}}}},
    }


def g3p(*, stochastic, greedy=0.0, steps_first=1000, cp_curve=None,
        succ_curve=None, cp_keys=None, learnable=None, budget=1_200_000,
        trained=100_000, title="Fixture", game_path="/x/game.js"):
    cp_keys = cp_keys if cp_keys is not None else ["m1", "m2"]
    cp_curve = cp_curve if cp_curve is not None else [0.5, 1.0, 1.2, 1.2]
    succ_curve = succ_curve if succ_curve is not None else [0.0, 0.3, stochastic,
                                                            stochastic]
    if learnable is None:
        learnable = stochastic >= 0.5 or greedy >= 0.5
    return {
        "learnable": learnable,
        "steps_to_first_success": steps_first,
        "checkpoints_curve": cp_curve,
        "final_success_rate": greedy,
        "stochastic_success_rate": stochastic,
        "curve_success": succ_curve,
        "rl_witness": {"seed": 0, "actions": ["x"], "ticks": witness_ticks_of(cp_curve)},
        "wall_clock_s": 42.0,
        "checkpoint_keys": cp_keys,
        "budget_steps": budget,
        "trained_steps": trained,
        "stopped_early": True,
        "title": title,
        "game_path": game_path,
    }


def witness_ticks_of(_):
    return 40  # irrelevant filler for the rl_witness field


# ======================================================================== #
# Grade thresholds — all five grades
# ======================================================================== #
def test_grade_target_gem_cavern_like():
    """gem_cavern @1.2M: stochastic 0.656 -> target (the frontier band)."""
    p = C.difficulty_profile(
        vreport(witness_ticks=107, checkpoints={"a": 20, "b": 60, "c": 95}),
        g3p(stochastic=0.656, greedy=0.0, steps_first=1832,
            cp_keys=["a", "b", "c"], cp_curve=[0.4, 1.1, 1.8, 2.1, 2.1]))
    assert p["grade"] == "target"
    assert p["rl"]["success_rate"] == 0.656


def test_grade_target_meteor_like_greedy_perfect():
    """meteor_gauntlet: greedy 1.0 but graded (stochastic) 0.625 -> target, NOT
    easy/degenerate (witness 98 ticks, first success 840 steps — real play)."""
    p = C.difficulty_profile(
        vreport(witness_ticks=98, checkpoints={"a": 20, "b": 55, "c": 90}),
        g3p(stochastic=0.625, greedy=1.0, steps_first=840,
            cp_keys=["a", "b", "c"], cp_curve=[0.5, 1.5, 2.4, 2.6]))
    assert p["grade"] == "target"


def test_grade_hard_two_switch_vault_like():
    """two_switch_vault @1.2M: stochastic 0.188 but RL reached success once
    (first success 1136) -> hard (learnable-but-not-cracked), not not_learnable."""
    keys = ["switch_a", "cleared_gap1", "switch_b", "cleared_gap2"]
    p = C.difficulty_profile(
        vreport(witness_ticks=102,
                checkpoints={"switch_a": 15, "cleared_gap1": 40,
                             "switch_b": 70, "cleared_gap2": 100}),
        g3p(stochastic=0.188, greedy=0.0, steps_first=1136, cp_keys=keys,
            cp_curve=[0.1, 0.5, 1.0, 1.3, 1.5, 1.5, 1.5, 1.5]))
    assert p["grade"] == "hard"
    # Localises the stall between the first gap and the second gate.
    assert p["rl"]["stalling_milestone"] == "cleared_gap1"
    assert p["rl"]["last_mastered_milestone"] == "switch_a"


def test_grade_not_learnable():
    """No policy ever reached success within budget, flat near zero -> not_learnable."""
    p = C.difficulty_profile(
        vreport(witness_ticks=150, checkpoints={"a": 20, "b": 60, "c": 120}),
        g3p(stochastic=0.0, greedy=0.0, steps_first=None, cp_keys=["a", "b", "c"],
            cp_curve=[0.0, 0.1, 0.1, 0.1], succ_curve=[0.0, 0.0, 0.0, 0.0]))
    assert p["grade"] == "not_learnable"
    assert p["rl"]["stalling_milestone"] == "a"  # stuck at the first milestone


def test_grade_easy():
    """Sampled policy wins comfortably (0.95), real play required -> easy."""
    p = C.difficulty_profile(
        vreport(witness_ticks=120, checkpoints={"a": 20, "b": 60, "c": 110}),
        g3p(stochastic=0.95, greedy=1.0, steps_first=5000, cp_keys=["a", "b", "c"],
            cp_curve=[1.0, 2.0, 2.9, 3.0], succ_curve=[0.2, 0.7, 0.95, 0.95]))
    assert p["grade"] == "easy"


def test_grade_degenerate_short_witness():
    """Mastered AND certified witness barely clears anti-triviality -> degenerate."""
    p = C.difficulty_profile(
        vreport(witness_ticks=22, checkpoints={"a": 5, "b": 15}),
        g3p(stochastic=0.98, greedy=1.0, steps_first=200, cp_keys=["a", "b"],
            cp_curve=[1.5, 2.0, 2.0, 2.0], succ_curve=[0.8, 0.98, 0.98, 0.98]))
    assert p["grade"] == "degenerate"


def test_grade_degenerate_near_instant_first_success():
    """Mastered AND first success within DEGENERATE_STEPS -> degenerate even with a
    longer witness (the goal gate lets blind motion through fast)."""
    p = C.difficulty_profile(
        vreport(witness_ticks=90, checkpoints={"a": 20, "b": 60}),
        g3p(stochastic=0.97, greedy=1.0, steps_first=300, cp_keys=["a", "b"],
            cp_curve=[2.0, 2.0, 2.0], succ_curve=[0.97, 0.97, 0.97]))
    assert p["grade"] == "degenerate"


def test_all_five_grades_reachable():
    grades = set()
    grades.add(C.difficulty_profile(vreport(witness_ticks=22),
               g3p(stochastic=0.98, greedy=1.0, steps_first=200))["grade"])
    grades.add(C.difficulty_profile(vreport(witness_ticks=120),
               g3p(stochastic=0.95, greedy=1.0, steps_first=5000))["grade"])
    grades.add(C.difficulty_profile(vreport(),
               g3p(stochastic=0.6, steps_first=1500))["grade"])
    grades.add(C.difficulty_profile(vreport(),
               g3p(stochastic=0.2, steps_first=1136))["grade"])
    grades.add(C.difficulty_profile(vreport(),
               g3p(stochastic=0.0, steps_first=None))["grade"])
    assert grades == set(C.GRADES)


# ======================================================================== #
# Determinism
# ======================================================================== #
def test_profile_is_deterministic():
    v = vreport(witness_ticks=102, checkpoints={"a": 15, "b": 70})
    r = g3p(stochastic=0.188, steps_first=1136, cp_keys=["a", "b"])
    p1 = C.difficulty_profile(v, r)
    p2 = C.difficulty_profile(v, r)
    assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True)


def test_per_milestone_mastery_tapers_at_stall():
    keys = ["a", "b", "c", "d"]
    p = C.difficulty_profile(
        vreport(checkpoints={k: 10 * (i + 1) for i, k in enumerate(keys)}),
        g3p(stochastic=0.3, steps_first=900, cp_keys=keys,
            cp_curve=[0.2, 0.8, 1.3, 1.5, 1.5, 1.5, 1.5, 1.5]))
    m = p["rl"]["per_milestone_mastery"]
    assert m["a"] == 1.0 and m["b"] == 0.5
    assert m["c"] == 0.0 and m["d"] == 0.0


# ======================================================================== #
# Directive — anchored to the stalling milestone
# ======================================================================== #
def test_directive_hard_names_stalling_milestone_and_makes_it_reachable():
    keys = ["switch_a", "cleared_gap1", "switch_b", "cleared_gap2"]
    p = C.difficulty_profile(
        vreport(witness_ticks=102,
                checkpoints={k: 20 * (i + 1) for i, k in enumerate(keys)}),
        g3p(stochastic=0.188, steps_first=1136, cp_keys=keys,
            cp_curve=[0.1, 0.5, 1.0, 1.3, 1.5, 1.5, 1.5, 1.5]))
    d = C.directive(p)
    assert "grade: hard" in d
    assert "cleared_gap1" in d              # names the stalling milestone
    assert "REACHABLE" in d                 # make exactly that stage reachable...
    assert PRESERVE_CLAUSE in d             # ...NOT shallower (ambition audit)
    assert "switch_a" in d                  # names the last mastered stage


def test_directive_not_learnable_eases_first_stage():
    p = C.difficulty_profile(
        vreport(checkpoints={"reach_ledge": 20, "cross_pit": 60, "climb": 120}),
        g3p(stochastic=0.0, steps_first=None,
            cp_keys=["reach_ledge", "cross_pit", "climb"],
            cp_curve=[0.0, 0.1, 0.1, 0.1]))
    d = C.directive(p)
    assert "grade: not_learnable" in d
    assert "reach_ledge" in d               # earliest blocking stage


def test_directive_easy_deepens_after_last_milestone():
    keys = ["a", "b", "c"]
    p = C.difficulty_profile(
        vreport(witness_ticks=120, checkpoints={k: 30 * (i + 1) for i, k in enumerate(keys)}),
        g3p(stochastic=0.95, greedy=1.0, steps_first=5000, cp_keys=keys,
            cp_curve=[1.0, 2.0, 2.9, 3.0], succ_curve=[0.2, 0.7, 0.95, 0.95]))
    d = C.directive(p)
    assert "grade: easy" in d
    assert "'c'" in d                       # deepen AFTER the last stage
    assert "Deepen" in d


def test_directive_target_holds():
    p = C.difficulty_profile(
        vreport(witness_ticks=107, checkpoints={"a": 20, "b": 60, "c": 95}),
        g3p(stochastic=0.656, steps_first=1832, cp_keys=["a", "b", "c"],
            cp_curve=[0.4, 1.1, 1.8, 2.1, 2.1]))
    d = C.directive(p)
    assert "grade: target" in d
    assert "CERTIFIED" in d


# ======================================================================== #
# curriculum_round — dry loop wiring + ledger (mocked g3_prime, no torch)
# ======================================================================== #
_GAME_SRC = '''# a minimal game artifact — only PROMPT/engine are read by the round
PROMPT = "guide the puck across the ice onto the glowing pad"
ACTIONS = ["left", "right"]
'''


@pytest.fixture
def game_file(tmp_path):
    p = tmp_path / "game.py"
    p.write_text(_GAME_SRC, encoding="utf-8")
    return str(p)


def _install_seams(monkeypatch, *, g3p_result, verify_passed=True, gen_spy=None,
                   revise_spy=None):
    monkeypatch.setattr(C, "verify_fn",
                        lambda gp: vreport(witness_ticks=102,
                                           checkpoints={"switch_a": 15,
                                                        "cleared_gap1": 40,
                                                        "switch_b": 70},
                                           passed=verify_passed))

    calls = {"g3p": [], "gen": [], "revise": []}

    def fake_g3p(game_path, budget_steps, **kw):
        calls["g3p"].append((game_path, budget_steps))
        return g3p_result

    def fake_gen(prompt, *, out_dir, backend, engine):
        calls["gen"].append({"prompt": prompt, "out_dir": out_dir,
                             "backend": backend, "engine": engine})
        import os
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "next.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_GAME_SRC)
        return {"game_path": path, "verdict": "COMPLETED"}

    def fake_revise(source, directive, *, out_dir, backend, engine):
        calls["revise"].append({"source": source, "directive": directive,
                                "out_dir": out_dir, "backend": backend,
                                "engine": engine})
        import os
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "revised.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_GAME_SRC)
        return {"game_path": path, "verdict": "COMPLETED"}

    monkeypatch.setattr(C, "g3_prime_fn", fake_g3p)
    monkeypatch.setattr(C, "generate_fn", gen_spy or fake_gen)
    monkeypatch.setattr(C, "revise_fn", revise_spy or fake_revise)
    return calls


def test_curriculum_round_hard_regenerates_and_logs(tmp_path, game_file, monkeypatch):
    keys = ["switch_a", "cleared_gap1", "switch_b"]
    result = g3p(stochastic=0.188, steps_first=1136, cp_keys=keys,
                 cp_curve=[0.1, 0.5, 1.0, 1.3, 1.5, 1.5, 1.5, 1.5],
                 game_path=game_file, title="Vault")
    calls = _install_seams(monkeypatch, g3p_result=result)
    ledger = tmp_path / "ledger.jsonl"
    out_dir = tmp_path / "next"

    rec = C.curriculum_round(game_file, backend="template", budget_steps=1234,
                             out_dir=str(out_dir), ledger_path=str(ledger),
                             mode="regenerate")

    # --- loop wiring ---
    assert rec["grade"] == "hard"
    assert rec["action_taken"] == "regenerated"
    assert rec["mode"] == "regenerate"
    assert calls["g3p"] == [(game_file, 1234)]          # G3' called with the budget
    assert len(calls["gen"]) == 1                        # regenerate fired
    assert calls["revise"] == []                         # regenerate mode: revise not fired
    gen_call = calls["gen"][0]
    # The ORIGINAL prompt + the directive were composed into the USER prompt.
    assert "guide the puck" in gen_call["prompt"]
    assert "[CURRICULUM DIRECTIVE" in gen_call["prompt"]
    assert "cleared_gap1" in gen_call["prompt"]         # anchored to the stall
    assert gen_call["engine"] == "py"
    # New version produced.
    assert rec["new_game_path"] and rec["new_game_path"].endswith("next.py")

    # --- ledger event ---
    lines = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    ev = lines[0]
    assert ev["event"] == "curriculum_round"
    assert ev["grade"] == "hard"
    assert ev["action_taken"] == "regenerated"
    assert ev["rl"]["stalling_milestone"] == "cleared_gap1"
    assert ev["backend"] == "template"
    assert ev["budget_steps"] == 1234


def test_curriculum_round_unsolved_regeneration_is_a_failure(tmp_path, game_file,
                                                             monkeypatch):
    # REGRESSION (first live round): generate_game writes its best attempt to
    # game_path even when the verdict is UNSOLVED — the round must NOT count
    # that as "regenerated" (the CLI would chain a doomed round on it).
    keys = ["switch_a", "cleared_gap1", "switch_b"]
    result = g3p(stochastic=0.188, steps_first=1136, cp_keys=keys,
                 cp_curve=[0.1, 0.5, 1.0, 1.3, 1.5, 1.5, 1.5, 1.5],
                 game_path=game_file, title="Vault")

    def unsolved_gen(prompt, *, out_dir, backend, engine):
        path = str(tmp_path / "next" / "failed.py")
        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_GAME_SRC)
        return {"game_path": path, "verdict": "UNSOLVED",
                "attempts": [{"report": {"hint": "stuck between 'a' and 'b'"}}]}

    _install_seams(monkeypatch, g3p_result=result, gen_spy=unsolved_gen)
    ledger = tmp_path / "ledger.jsonl"

    rec = C.curriculum_round(game_file, backend="template", budget_steps=1234,
                             out_dir=str(tmp_path / "next"),
                             ledger_path=str(ledger), mode="regenerate")

    assert rec["action_taken"] == "regenerate_failed"
    assert "UNSOLVED" in rec["directive"]
    assert "stuck between 'a' and 'b'" in rec["directive"]
    ev = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert ev["action_taken"] == "regenerate_failed"


# ======================================================================== #
# curriculum_round — REVISE mode (the default): certified source + directive
# ======================================================================== #
def test_curriculum_round_revise_is_default_and_edits_certified_source(
        tmp_path, game_file, monkeypatch):
    """Default mode is 'revise': the CERTIFIED source + directive seed the revise
    seam; a COMPLETED verdict -> 'revised' with a chainable new_game_path."""
    keys = ["switch_a", "cleared_gap1", "switch_b"]
    result = g3p(stochastic=0.188, steps_first=1136, cp_keys=keys,
                 cp_curve=[0.1, 0.5, 1.0, 1.3, 1.5, 1.5, 1.5, 1.5],
                 game_path=game_file, title="Vault")
    calls = _install_seams(monkeypatch, g3p_result=result)
    ledger = tmp_path / "ledger.jsonl"
    out_dir = tmp_path / "next"

    # No mode= argument -> default is revise.
    rec = C.curriculum_round(game_file, backend="template", budget_steps=1234,
                             out_dir=str(out_dir), ledger_path=str(ledger))

    assert rec["grade"] == "hard"
    assert rec["mode"] == "revise"
    assert rec["action_taken"] == "revised"
    assert calls["g3p"] == [(game_file, 1234)]           # G3' called with the budget
    assert calls["gen"] == []                            # revise mode: regenerate not fired
    assert len(calls["revise"]) == 1                     # the revise seam fired
    rv = calls["revise"][0]
    # The seam received the FULL certified source + the directive (a minimal EDIT,
    # NOT a from-scratch prompt).
    assert rv["source"] == _GAME_SRC
    assert "[CURRICULUM DIRECTIVE" in rv["directive"]
    assert "cleared_gap1" in rv["directive"]             # anchored to the stall
    assert rv["engine"] == "py"
    assert rv["out_dir"] == str(out_dir)
    # A new certified version was produced -> the path is chainable.
    assert rec["new_game_path"] and rec["new_game_path"].endswith("revised.py")

    ev = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert ev["event"] == "curriculum_round"
    assert ev["action_taken"] == "revised" and ev["mode"] == "revise"


def test_curriculum_round_revise_unsolved_is_a_failure_no_chain(
        tmp_path, game_file, monkeypatch):
    """A revise whose verdict is UNSOLVED -> 'revise_failed' (not 'revised'), and
    the verdict + last repair hint are recorded in the directive trail so the CLI
    chain stops (mirrors the regenerate path after 26b3fc4)."""
    keys = ["switch_a", "cleared_gap1", "switch_b"]
    result = g3p(stochastic=0.188, steps_first=1136, cp_keys=keys,
                 cp_curve=[0.1, 0.5, 1.0, 1.3, 1.5, 1.5, 1.5, 1.5],
                 game_path=game_file, title="Vault")

    def unsolved_revise(source, directive, *, out_dir, backend, engine):
        import os
        path = str(tmp_path / "next" / "revised_failed.py")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_GAME_SRC)
        return {"game_path": path, "verdict": "UNSOLVED",
                "attempts": [{"report": {"hint": "still stuck at cleared_gap1"}}]}

    _install_seams(monkeypatch, g3p_result=result, revise_spy=unsolved_revise)
    ledger = tmp_path / "ledger.jsonl"

    rec = C.curriculum_round(game_file, backend="template", budget_steps=1234,
                             out_dir=str(tmp_path / "next"),
                             ledger_path=str(ledger))     # default revise

    assert rec["action_taken"] == "revise_failed"
    assert "revise verdict: UNSOLVED" in rec["directive"]
    assert "still stuck at cleared_gap1" in rec["directive"]
    # The action is NOT a chain-advancing verdict -> the CLI loop would stop here.
    assert rec["action_taken"] not in ("revised", "regenerated")
    ev = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert ev["action_taken"] == "revise_failed"


def test_revise_user_msg_carries_source_and_minimal_edit_instruction():
    """The revise prompt (gamegen._revise_user_msg) embeds the full certified
    source verbatim and the minimal-edit task instruction."""
    from harness.gen import gamegen as GG
    directive = ("[CURRICULUM DIRECTIVE — grade: hard]\n"
                 "EASE the 'cleared_gap1' gate; keep every later stage intact.")
    msg = GG._revise_user_msg(_GAME_SRC, directive, engine="py")
    assert _GAME_SRC in msg                              # full source, not a diff
    assert directive in msg                              # the directive rides along
    assert "CERTIFIED" in msg                            # framed as a certified edit
    assert "MINIMAL EDIT" in msg                         # the minimal-edit instruction
    assert "PROMPT" in msg                               # preserve provenance
    # JS variant fences the source as javascript.
    msg_js = GG._revise_user_msg(_GAME_SRC, directive, engine="js")
    assert "```javascript" in msg_js


def test_cli_curriculum_chains_on_revised_stops_on_failed(tmp_path, monkeypatch):
    """The CLI advances the chain on a 'revised' round (feeding its new_game_path
    into the next round) and stops on a 'revise_failed' round; default mode is
    revise."""
    from harness import cli
    import harness.gen.curriculum as CC

    v2 = str(tmp_path / "v2.js")
    seq = iter([
        {"action_taken": "revised", "new_game_path": v2, "grade": "hard",
         "profile": {}, "directive": "d1", "game_path": "g0"},
        {"action_taken": "revise_failed", "new_game_path": str(tmp_path / "x.js"),
         "grade": "hard", "profile": {}, "directive": "d2", "game_path": v2},
    ])
    seen = []

    def fake_round(game_path, **kw):
        seen.append((game_path, kw.get("mode")))
        return next(seq)

    monkeypatch.setattr(CC, "curriculum_round", fake_round)

    args = cli.build_parser().parse_args(
        ["game", "curriculum", "g0", "--rounds", "3", "--json"])
    rc = args.func(args)

    assert rc == 0
    assert len(seen) == 2                                 # round 3 never ran (chain stopped)
    assert seen[0] == ("g0", "revise")                    # default mode is revise
    assert seen[1][0] == v2                               # round 2 got the revised path


def test_curriculum_round_target_stops_no_regenerate(tmp_path, game_file, monkeypatch):
    result = g3p(stochastic=0.656, steps_first=1832,
                 cp_keys=["switch_a", "cleared_gap1", "switch_b"],
                 cp_curve=[0.4, 1.1, 1.8, 2.1, 2.1], game_path=game_file)
    calls = _install_seams(monkeypatch, g3p_result=result)
    ledger = tmp_path / "ledger.jsonl"

    rec = C.curriculum_round(game_file, backend="template", budget_steps=200_000,
                             out_dir=str(tmp_path / "next"), ledger_path=str(ledger))

    assert rec["grade"] == "target"
    assert rec["action_taken"] == "certified_target"
    assert rec["new_game_path"] is None
    assert calls["gen"] == []                            # NO regenerate on target
    ev = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert ev["event"] == "curriculum_round" and ev["action_taken"] == "certified_target"


def test_curriculum_round_verify_failed_stops(tmp_path, game_file, monkeypatch):
    calls = _install_seams(
        monkeypatch,
        g3p_result=g3p(stochastic=0.5, steps_first=1000),
        verify_passed=False)
    ledger = tmp_path / "ledger.jsonl"

    rec = C.curriculum_round(game_file, backend="template", budget_steps=1000,
                             out_dir=str(tmp_path / "next"), ledger_path=str(ledger))

    assert rec["verify_passed"] is False
    assert rec["action_taken"] == "verify_failed"
    assert calls["g3p"] == []                            # never trains on an uncertified game
    assert calls["gen"] == []
    ev = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert ev["action_taken"] == "verify_failed"
