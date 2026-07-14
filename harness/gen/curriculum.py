"""Curriculum loop — learning-difficulty feeds a designer directive (ACCEL-style).

This is Phase 2 of ``notes/rl_agent/LLM_RL_SYSTEMS.md``: the structural answer to
the "complexity wall". A pure existence prober (G3 / the Go-Explore tree) can only
certify "some solution exists"; it cannot say whether a game sits at the *frontier
of learnability*, so generation is pushed easier until blind search stumbles on the
goal. G3' (``harness/rl/certify.py``) measures learnability with a small PPO policy.
This module MERGES the two signals into one machine-readable *difficulty profile*,
turns that profile into a GENERAL designer *directive* anchored to the game's own
milestone names, and wires a one-iteration *curriculum round*:

    generate → certify(tree G3) → G3' (RL) → profile → directive → regenerate

The LLM designer becomes ACCEL's edit operator, but smarter: "the agent plateaus
before '<milestone>' — ease exactly that stage" / "solved with no play — redesign
the goal gate" / "mastered every stage — deepen the level after '<milestone>'".

Design rules honoured here (task hard rules):
  * ADDITIVE ONLY — nothing in ``treesolve.py`` / ``certify.py`` / ``g4.py`` /
    ``render.py`` / ``prompts/*.md`` is modified. The directive is spliced at CALL
    time into the generator's USER prompt (like a repair hint), never into a frozen
    prompt section.
  * The directive carries NO game-specific hardcoding — it is per-game DATA derived
    from the profile's milestone names, exactly as repair hints are.
  * ``difficulty_profile`` is a PURE function of the two report dicts: identical
    inputs → byte-identical profile (deterministic; every threshold is an [eng.]
    constant). No network, no clock, no training inside the profile/directive.

Grade thresholds are calibrated against the three real G3' spike datapoints
(``notes/rl_agent/G3_PRIME_SPIKE.md`` §3, budget 1.2 M): gem_cavern (stochastic
0.656) and meteor_gauntlet (0.625) → ``target``; two_switch_vault (0.188, but RL
reached success in training) → ``hard``; a cliffside-style goal reachable without
play → ``degenerate``; a game no policy ever wins → ``not_learnable``.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import datetime, timezone

# --- Grade thresholds ([eng.] — calibrated on the 3 G3' spike datapoints) ----
# The graded learnability signal is the STOCHASTIC (sampled) success rate: on the
# fully-deterministic showcase games the GREEDY rate is degenerate/binary (0 or 1),
# so the spike (§3.1) reports the stochastic rate as the grade and keeps greedy as
# the witness's preferred, determinism-first form. Grades band on that rate:
NOT_LEARNABLE_RATE = 0.05   # <= this AND RL never once reached success -> not_learnable [eng.]
TARGET_RATE_LO = 0.50       # [TARGET_RATE_LO, EASY_RATE) -> target (the frontier band) [eng.]
EASY_RATE = 0.90            # >= this -> easy (the sampled policy wins comfortably) [eng.]
# Degenerate = the goal is reachable with almost no play. Two corroborating signals:
DEGENERATE_WITNESS_TICKS = 30   # a certified (tree) witness this short barely clears the
                                # anti-triviality floor (20) -> the goal gate is too close [eng.]
DEGENERATE_STEPS = 500          # RL reaches its FIRST success within this many env-steps [eng.]

GRADES = ("degenerate", "easy", "target", "hard", "not_learnable")

# Curriculum telemetry — one JSON line per round appended to the runs ledger,
# consistent with harness.core.telemetry conventions (append-only JSONL event log).
_LEDGER_PATH = "runs/ledger.jsonl"


# ======================================================================== #
# Small pure helpers
# ======================================================================== #
def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _round(x, n=3):
    return None if x is None else round(float(x), n)


def _plateau_index(curve: list, band: float) -> int | None:
    """Earliest index from which `curve` stays within `band` of its FINAL value.

    A deterministic "the curve has settled here" locator: the learning curve is
    considered plateaued from the first update `i` such that every later value is
    within `band` of the converged (final) value. Returns None for an empty curve.
    """
    if not curve:
        return None
    final = float(curve[-1])
    for i in range(len(curve)):
        if all(abs(float(c) - final) <= band for c in curve[i:]):
            return i
    return len(curve) - 1


def _tail_mean(curve: list) -> float | None:
    """Mean over the last quarter of a curve (the plateau region). None if empty."""
    if not curve:
        return None
    k = max(1, len(curve) // 4)
    tail = curve[-k:]
    return sum(float(c) for c in tail) / float(len(tail))


# ======================================================================== #
# Signal extraction (from the two report dicts)
# ======================================================================== #
def _milestones(verify_report: dict, g3p_result: dict) -> list[str]:
    """Declared milestone order (the anchor for every directive).

    Prefer G3''s `checkpoint_keys` (the RL env's frozen declared order); fall back
    to the tree witness's checkpoint keys from the verify report.
    """
    keys = g3p_result.get("checkpoint_keys")
    if isinstance(keys, list) and keys:
        return [str(k) for k in keys]
    witness = (verify_report or {}).get("witness") or {}
    cps = witness.get("checkpoints") or {}
    return [str(k) for k in cps.keys()]


def _solver_profile(verify_report: dict) -> dict:
    """Solver-side (existence-search) difficulty facts from the verify report."""
    report = verify_report or {}
    witness = report.get("witness") or {}
    latch = dict(witness.get("checkpoints") or {})
    latched_ticks = [t for t in latch.values() if isinstance(t, (int, float))]
    spread = (int(max(latched_ticks) - min(latched_ticks))
              if len(latched_ticks) >= 2 else None)

    g3 = ((report.get("layers") or {}).get("G3_solve") or {})
    episodes = (g3.get("checks") or {}).get("episodes") or {}
    replays = episodes.get("run")
    solver_kind = episodes.get("solver")

    return {
        "witness_ticks": witness.get("ticks"),
        "tree_replays_to_solve": replays,
        "solver_kind": solver_kind,
        "latch_ticks": {k: latch.get(k) for k in latch},
        "latch_spread": spread,
    }


def _rl_profile(g3p_result: dict, milestones: list[str]) -> dict:
    """RL-side (learnability) difficulty facts from the G3' result.

    The key localisation step: `checkpoints_curve` is the per-update MEAN count of
    latched milestones. Its plateau value maps to WHICH milestone the policy stalls
    at — floor(plateau_mean_latched) milestones are reliably reached, so the milestone
    at that index is the one the curve stalls before. `per_milestone_mastery[k]` is
    the clamped (plateau_mean_latched - k) — ~1.0 for reliably-reached stages, tapering
    to 0.0 past the stall.
    """
    g3p = g3p_result or {}
    stochastic = float(g3p.get("stochastic_success_rate") or 0.0)
    greedy = float(g3p.get("final_success_rate") or 0.0)
    steps_first = g3p.get("steps_to_first_success")

    cp_curve = list(g3p.get("checkpoints_curve") or [])
    succ_curve = list(g3p.get("curve_success") or [])

    plateau_mean_latched = _tail_mean(cp_curve)
    p_idx = _plateau_index(succ_curve, band=0.05)
    plateau_success = (succ_curve[p_idx] if (p_idx is not None and succ_curve)
                       else None)

    n = len(milestones)
    stalling_index = None
    stalling_milestone = None
    last_mastered = None
    per_milestone = {}
    if plateau_mean_latched is not None and n:
        idx = int(math.floor(plateau_mean_latched))
        idx = int(_clamp(idx, 0, n))          # 0..n (n == every milestone mastered)
        stalling_index = idx
        stalling_milestone = milestones[idx] if idx < n else None
        last_mastered = milestones[idx - 1] if idx >= 1 else None
        per_milestone = {m: _round(_clamp(plateau_mean_latched - k, 0.0, 1.0))
                         for k, m in enumerate(milestones)}

    return {
        "learnable": bool(g3p.get("learnable")),
        "success_rate": _round(stochastic),          # graded signal (spike §3.1)
        "greedy_success_rate": _round(greedy),       # witness form (deterministic)
        "steps_to_first_success": steps_first,
        "reached_success_in_training": steps_first is not None,
        "plateau_update": p_idx,
        "plateau_success_rate": _round(plateau_success),
        "plateau_mean_latched": _round(plateau_mean_latched),
        "stalling_index": stalling_index,
        "stalling_milestone": stalling_milestone,
        "last_mastered_milestone": last_mastered,
        "per_milestone_mastery": per_milestone,
        "budget_steps": g3p.get("budget_steps"),
        "trained_steps": g3p.get("trained_steps"),
        "stopped_early": bool(g3p.get("stopped_early")),
    }


def _grade(solver: dict, rl: dict) -> str:
    """Difficulty grade in GRADES, from the merged signals (explicit [eng.] bands).

    Ordered so the calibration datapoints land where the spike puts them:
      * degenerate — mastered AND the goal is reachable with almost no play
        (short certified witness OR near-instant first RL success)
      * easy       — the sampled policy wins comfortably (>= EASY_RATE)
      * target     — learnable in the frontier band [TARGET_RATE_LO, EASY_RATE)
      * not_learnable — never once reached success AND flat near zero
      * hard       — everything else below target (progress made, not cracked)
    """
    sr = float(rl.get("success_rate") or 0.0)
    greedy = float(rl.get("greedy_success_rate") or 0.0)
    reached = bool(rl.get("reached_success_in_training"))
    wt = solver.get("witness_ticks")
    steps_first = rl.get("steps_to_first_success")

    trivially_short = isinstance(wt, (int, float)) and wt < DEGENERATE_WITNESS_TICKS
    near_instant = (isinstance(steps_first, (int, float))
                    and steps_first <= DEGENERATE_STEPS)

    if (sr >= EASY_RATE or greedy >= EASY_RATE) and (trivially_short or near_instant):
        return "degenerate"
    if sr >= EASY_RATE:
        return "easy"
    if sr >= TARGET_RATE_LO:
        return "target"
    if not reached and sr <= NOT_LEARNABLE_RATE:
        return "not_learnable"
    return "hard"


# ======================================================================== #
# The profile
# ======================================================================== #
def difficulty_profile(verify_report: dict, g3p_result: dict) -> dict:
    """Merge the tree-G3 verify report and the G3' result into one difficulty profile.

    PURE and deterministic: same inputs -> byte-identical output. Structure::

        {"grade": <one of GRADES>,
         "milestones": [declared order],
         "solver": {witness_ticks, tree_replays_to_solve, latch_ticks, latch_spread,...},
         "rl":     {learnable, success_rate, steps_to_first_success, plateau_*,
                    stalling_milestone, per_milestone_mastery, ...},
         "thresholds": {the [eng.] grade constants},
         "title", "game_path"}
    """
    verify_report = verify_report or {}
    g3p_result = g3p_result or {}

    milestones = _milestones(verify_report, g3p_result)
    solver = _solver_profile(verify_report)
    rl = _rl_profile(g3p_result, milestones)
    grade = _grade(solver, rl)

    return {
        "grade": grade,
        "milestones": milestones,
        "solver": solver,
        "rl": rl,
        "thresholds": {
            "NOT_LEARNABLE_RATE": NOT_LEARNABLE_RATE,
            "TARGET_RATE_LO": TARGET_RATE_LO,
            "EASY_RATE": EASY_RATE,
            "DEGENERATE_WITNESS_TICKS": DEGENERATE_WITNESS_TICKS,
            "DEGENERATE_STEPS": DEGENERATE_STEPS,
        },
        "title": g3p_result.get("title") or verify_report.get("title"),
        "game_path": g3p_result.get("game_path"),
    }


# ======================================================================== #
# The directive
# ======================================================================== #
def _first(milestones: list[str], fallback: str = "the first milestone") -> str:
    return milestones[0] if milestones else fallback


def _last(milestones: list[str], fallback: str = "the final milestone") -> str:
    return milestones[-1] if milestones else fallback


def directive(profile: dict) -> str:
    """A GENERAL designer prompt block for one profile, anchored to milestone names.

    Returns a multi-line block (spliced into the generator's USER prompt at call
    time, exactly like a repair hint). It names the stalling milestone from the
    profile and the ACCEL-style edit — ease HERE / harden THERE / redesign the gate
    — with NO game-specific hardcoding: everything game-specific is DATA read out of
    the profile.
    """
    grade = profile.get("grade")
    milestones = list(profile.get("milestones") or [])
    rl = profile.get("rl") or {}
    solver = profile.get("solver") or {}

    sr = rl.get("success_rate")
    steps = rl.get("steps_to_first_success")
    stalling = rl.get("stalling_milestone")
    last_mastered = rl.get("last_mastered_milestone")
    witness_ticks = solver.get("witness_ticks")

    head = f"[CURRICULUM DIRECTIVE — grade: {grade}]"

    if grade == "degenerate":
        gate = _last(milestones, "the win condition")
        body = (
            f"The goal is reachable with almost no play (certified witness "
            f"{witness_ticks} decision ticks; the RL agent reaches success in "
            f"{steps} env-steps at rate {sr}). The goal GATE is degenerate. "
            f"Redesign it so winning demands genuine play: move the goal far from "
            f"the start, add a gating milestone AFTER '{gate}', or require an "
            f"intermediate state that blind/noop motion cannot reach. Do NOT simply "
            f"rescale — the win must stop being reachable by drift.")
    elif grade == "easy":
        anchor = _last(milestones, "the last stage")
        body = (
            f"The RL agent masters every stage (success rate {sr} in {steps} "
            f"env-steps). Deepen the level AFTER '{anchor}': add a new stage beyond "
            f"it — one more milestone plus an obstacle/hazard that gates it — or "
            f"lengthen the path and tighten a tolerance. KEEP the earlier stages "
            f"that already work; only extend past '{anchor}'.")
    elif grade == "target":
        anchor = stalling or _last(milestones, "the current final stage")
        body = (
            f"Well-calibrated: learnable at the frontier (success rate {sr}); the "
            f"policy stalls productively around '{anchor}'. This game is CERTIFIED "
            f"at target difficulty — keep the current stage structure. If producing "
            f"a variant, preserve the difficulty band around '{anchor}' (re-theme, "
            f"don't re-scale).")
    elif grade == "hard":
        stall = stalling or _first(milestones)
        mastered = f" (it reliably reaches '{last_mastered}')" if last_mastered else ""
        body = (
            f"The agent plateaus BEFORE '{stall}': success rate {sr}{mastered}, but "
            f"it rarely gets past '{stall}'. EASE exactly that stage — widen the "
            f"platform, slow or steady the hazard, enlarge the target, or relax the "
            f"timing at '{stall}' — and KEEP every later stage intact. Change only "
            f"the '{stall}' gate; do not touch the stages the agent already clears.")
    else:  # not_learnable
        stall = stalling or _first(milestones)
        body = (
            f"No policy learned to win within budget (success rate {sr}; the agent "
            f"never gets reliably past '{stall}'). Ease the EARLIEST blocking stage "
            f"'{stall}': make its gate reachable — wider gap, slower/steadier "
            f"hazard, a closer checkpoint — WITHOUT changing the goal. If it stalls "
            f"at the very first milestone, ease the opening so play can begin.")

    return f"{head}\n{body}"


# ======================================================================== #
# Prompt / engine extraction from a game artifact
# ======================================================================== #
_PROMPT_PY_RE = re.compile(r"""(?m)^\s*PROMPT\s*=\s*(['"])(.*?)\1""")
_PROMPT_JS_RE = re.compile(r"""(?m)^\s*(?:const|let|var)\s+PROMPT\s*=\s*(['"])(.*?)\1""")


def _extract_prompt(source: str) -> str | None:
    """The game's declared PROMPT string (py or js), or None if not found."""
    for rx in (_PROMPT_PY_RE, _PROMPT_JS_RE):
        m = rx.search(source or "")
        if m:
            return m.group(2)
    return None


def _engine_of(game_path: str, source: str) -> str:
    """Reuse gameverify's engine detection (py | js | godot)."""
    try:
        from harness.verify.gameverify import detect_engine
        return detect_engine(game_path, source)
    except Exception:  # noqa: BLE001 - fall back to extension
        return "js" if str(game_path).lower().endswith(".js") else "py"


# ======================================================================== #
# Injectable seams (so tests mock without torch / network)
# ======================================================================== #
def _default_verify(game_path: str) -> dict:
    from harness.verify.gameverify import verify_game
    return verify_game(game_path)


def _default_g3_prime(game_path: str, budget_steps: int, **kwargs) -> dict:
    from harness.rl.certify import g3_prime
    return g3_prime(game_path, budget_steps=budget_steps, **kwargs)


def _default_generate(prompt: str, *, out_dir: str, backend: str, engine: str) -> dict:
    from harness.gen.gamegen import generate_game
    return generate_game(prompt, out_dir=out_dir, backend=backend, engine=engine)


# Module-level indirection: tests monkeypatch these names on the module.
verify_fn = _default_verify
g3_prime_fn = _default_g3_prime
generate_fn = _default_generate


# ======================================================================== #
# Ledger
# ======================================================================== #
def _append_ledger_event(entry: dict, path: str) -> dict:
    """Append ONE curriculum event line to the runs ledger (creates the dir).

    Distinct ``"event": "curriculum_round"`` discriminator so the append-only
    JSONL log stays honest (harness.core.telemetry conventions); mirrors
    telemetry.record_run's write pattern.
    """
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return entry


def _ledger_entry(profile: dict, directive_text: str, *, backend: str,
                  budget_steps: int, action_taken: str, new_game_path,
                  wall_s: float) -> dict:
    rl = profile.get("rl") or {}
    solver = profile.get("solver") or {}
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "curriculum_round",
        "game_path": profile.get("game_path"),
        "title": profile.get("title"),
        "grade": profile.get("grade"),
        "backend": backend,
        "budget_steps": budget_steps,
        "action_taken": action_taken,
        "new_game_path": new_game_path,
        "directive": directive_text,
        "rl": {
            "learnable": rl.get("learnable"),
            "success_rate": rl.get("success_rate"),
            "steps_to_first_success": rl.get("steps_to_first_success"),
            "stalling_milestone": rl.get("stalling_milestone"),
        },
        "solver": {
            "witness_ticks": solver.get("witness_ticks"),
            "tree_replays_to_solve": solver.get("tree_replays_to_solve"),
        },
        "wall_s": round(float(wall_s), 2),
    }


# ======================================================================== #
# One curriculum-loop iteration
# ======================================================================== #
def curriculum_round(game_path: str, *, backend: str = "auto",
                     budget_steps: int = 200_000,
                     out_dir: str = "scenes/games/curriculum",
                     ledger_path: str | None = None,
                     g3p_kwargs: dict | None = None) -> dict:
    """One iteration of the curriculum loop on the game at `game_path`.

    verify (tree G3) -> G3' (RL, `budget_steps`) -> difficulty_profile -> directive.
    If the grade is ``target`` the game is CERTIFIED at frontier difficulty and the
    round stops. Otherwise the original PROMPT + directive are composed and handed
    to ``gamegen.generate_game`` (the directive rides the USER prompt — additive,
    no frozen-section edit) to produce the NEXT version into `out_dir`.

    A ``curriculum_round`` event line is appended to the runs ledger. Returns the
    round record::

        {"game_path", "grade", "profile", "directive", "action_taken",
         "new_game_path", "verify_passed", "backend", "budget_steps", "wall_s"}

    `action_taken` in {"certified_target", "regenerated", "verify_failed",
    "regenerate_failed"}.
    """
    t0 = time.time()
    ledger_path = ledger_path or _LEDGER_PATH
    g3p_kwargs = dict(g3p_kwargs or {})

    with open(game_path, "r", encoding="utf-8") as fh:
        source = fh.read()

    verify_report = verify_fn(game_path)
    verify_passed = bool(verify_report.get("passed"))

    if not verify_passed:
        # The loop grades CERTIFIED games; if the current game does not certify,
        # stop honestly (this is a generate/verify problem, not a difficulty one).
        profile = {
            "grade": None, "milestones": [], "solver": _solver_profile(verify_report),
            "rl": {}, "title": None, "game_path": game_path,
            "verify_failure_class": verify_report.get("failure_class"),
            "verify_hint": verify_report.get("hint"),
        }
        record = {
            "game_path": game_path, "grade": None, "profile": profile,
            "directive": None, "action_taken": "verify_failed",
            "new_game_path": None, "verify_passed": False, "backend": backend,
            "budget_steps": budget_steps, "wall_s": round(time.time() - t0, 2),
        }
        _append_ledger_event(
            _ledger_entry(profile, None, backend=backend, budget_steps=budget_steps,
                          action_taken="verify_failed", new_game_path=None,
                          wall_s=time.time() - t0),
            ledger_path)
        return record

    # --- G3' RL learnability certificate (the single monkeypatch seam) ---
    g3p_result = g3_prime_fn(game_path, budget_steps, **g3p_kwargs)

    profile = difficulty_profile(verify_report, g3p_result)
    directive_text = directive(profile)
    grade = profile["grade"]

    new_game_path = None
    if grade == "target":
        action_taken = "certified_target"
    else:
        # Compose the ORIGINAL prompt + directive; regenerate the NEXT version.
        engine = _engine_of(game_path, source)
        original_prompt = _extract_prompt(source) or (profile.get("title") or "game")
        augmented = f"{original_prompt.strip()}\n\n{directive_text}"
        try:
            gen = generate_fn(augmented, out_dir=out_dir, backend=backend,
                              engine=engine)
            new_game_path = gen.get("game_path")
            action_taken = "regenerated" if new_game_path else "regenerate_failed"
        except Exception as exc:  # noqa: BLE001 - a bad regenerate must not crash the loop
            action_taken = "regenerate_failed"
            new_game_path = None
            directive_text = directive_text + f"\n[regenerate error: {exc}]"

    wall_s = time.time() - t0
    _append_ledger_event(
        _ledger_entry(profile, directive_text, backend=backend,
                      budget_steps=budget_steps, action_taken=action_taken,
                      new_game_path=new_game_path, wall_s=wall_s),
        ledger_path)

    return {
        "game_path": game_path,
        "grade": grade,
        "profile": profile,
        "directive": directive_text,
        "action_taken": action_taken,
        "new_game_path": new_game_path,
        "verify_passed": True,
        "backend": backend,
        "budget_steps": budget_steps,
        "wall_s": round(wall_s, 2),
    }
