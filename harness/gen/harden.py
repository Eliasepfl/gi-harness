"""The FEEDBACK-COMPILER DRIVER — revise-from-current-source with a convergence guard.

The impure half of the feedback loop (the pure taxonomy lives in `harness/gen/feedback.py`).

  * :func:`revise_with_directives` loads the CURRENT source, compiles the directives into
    ONE minimal-edit instruction, routes the skill context on the DIRECTIVE text (the
    godot-master orchestrator is kept — a revise turn is the README's "audit an existing
    project" case — with the domain layer selected by the defect), injects that skill block
    into the revise message, and runs the FULL verify->repair loop via `gamegen.revise_game`.

  * :func:`harden_game` is the guarded loop: run the oracles (G4 always, G3' optionally),
    compile directives, revise, RE-CERTIFY, and repeat — up to `max_rounds` per finding. A
    revise attempt writes ONLY into the run sandbox; the last certified version on disk is
    NEVER overwritten with a fix that fails to re-certify. If a defect's fingerprint recurs
    after a revise (the fix did not take), the loop stops with ``REPAIR_STALLED``. Every
    directive + its resulting verdict is appended to the per-game ledger (auditable history).

Seams (module-level; tests monkeypatch to run offline with no Godot / torch / network):
``verify_fn``, ``attack_fn``, ``g3_fn``, ``revise_fn``, ``render_skills_fn``.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from harness.gen import feedback


_LEDGER_PATH = "runs/ledger.jsonl"
MAX_ROUNDS_PER_FINDING = 3          # convergence guard: rounds per finding [eng.]


# ======================================================================== #
# Seams (default implementations; tests replace these module-level names)
# ======================================================================== #
def _slug_of(game_path: str) -> str:
    return os.path.splitext(os.path.basename(str(game_path)))[0]


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _engine_of(game_path: str, source: str) -> str:
    try:
        from harness.verify.gameverify import detect_engine
        return detect_engine(game_path, source)
    except Exception:  # noqa: BLE001
        return "gdscript" if str(game_path).endswith(".gd") else "py"


def _default_verify(game_path: str) -> dict:
    from harness.verify.gameverify import verify_game
    return verify_game(game_path)


def _default_attack(game_path: str, source: str, report: dict, *, engine: str,
                    tiers=(0,), stale: bool = True, seed: int = 0) -> dict:
    """Run G4 on the CURRENT game. Uses the lower-level ``run_g4`` (not ``attack_game``)
    so broken shapes are surfaced even when the game trips a verify gate (e.g. the
    single-action anti-triviality gate flips an otherwise-certified game to GOAL_ERROR):
    tier 0's single-action lens still fires and compiles to a repair directive."""
    from harness.verify.g4 import run_g4
    rep = dict(report) if isinstance(report, dict) else {}
    rep.setdefault("engine", engine)
    return run_g4(source, rep, engine=engine, slug=_slug_of(game_path),
                  tiers=tuple(tiers), stale=bool(stale), seed=seed)


def _default_g3(game_path: str, *, budget_steps: int, **kwargs) -> dict:
    from harness.rl.certify import g3_prime
    return g3_prime(game_path, budget_steps=budget_steps, **kwargs)


def _default_revise(source: str, directive: str, *, out_dir: str, backend: str,
                    max_repairs: int, engine, skill_context) -> dict:
    from harness.gen.gamegen import revise_game
    return revise_game(source, directive, out_dir=out_dir, backend=backend,
                       max_repairs=max_repairs, engine=engine,
                       skill_context=skill_context)


def _default_render_skills(directive_text: str, *, root=None, use_llm: bool = True) -> str:
    """Skill reference block for a REPAIR turn — routed on the DIRECTIVE/ERROR text.

    The routing QUERY is the directive text (not the original game prompt), so the
    domain layer is selected by the DEFECT being fixed while the current game source
    stays authoritative. The godot-master orchestrator is KEPT (orchestrator=True):
    the gd-agentic README routes "auditing an existing project to find anti-patterns /
    standards violations" to godot-master, and a revise turn is exactly that audit-and-
    fix. Absent library -> "" (harmless)."""
    try:
        from harness.gen.skill_context import render_skill_context
        return render_skill_context(directive_text, orchestrator=True, root=root,
                                    use_llm=use_llm)
    except Exception:  # noqa: BLE001 - skill routing must never break a repair
        return ""


# Module-level indirection — tests monkeypatch these names on the module.
verify_fn = _default_verify
attack_fn = _default_attack
g3_fn = _default_g3
revise_fn = _default_revise
render_skills_fn = _default_render_skills


# ======================================================================== #
# Ledger (append-only JSONL, harness.core.telemetry conventions)
# ======================================================================== #
def _append_ledger_event(entry: dict, path: str) -> dict:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return entry


def _ledger_entry(slug, game_path, directive, verdict, round_no, *, backend,
                  budget_steps, revised_path=None) -> dict:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "harden_round",
        "game": slug,
        "game_path": game_path,
        "round": round_no,
        "backend": backend,
        "budget_steps": budget_steps,
        "source_finding": directive.source,
        "origin": directive.origin,
        "checkpoint_keys": list(directive.checkpoint_keys),
        "fingerprint": directive.fingerprint,
        "directive": directive.text,
        "verdict": verdict,
        "revised_game_path": revised_path,
    }


# ======================================================================== #
# Revise wiring
# ======================================================================== #
def revise_with_directives(game_path: str, directives, *, out_dir: str,
                           backend: str = "auto", max_repairs: int = 4,
                           engine=None, skill_root=None,
                           skill_use_llm: bool = True) -> dict:
    """Revise the CURRENT game so it satisfies `directives`, via the existing minimal-edit
    revise path (``gamegen.revise_game``), with DOMAIN-skill context routed on the directive
    text spliced into the message. Returns the standard revise result dict (verdict,
    game_path, attempts, ...) plus ``directives`` and ``skill_context`` provenance. Writes
    ONLY into `out_dir` (the run sandbox); does not touch `game_path`."""
    source = _read(game_path)
    directive_text = feedback.combined_directive_text(directives)
    skill_context = render_skills_fn(directive_text, root=skill_root,
                                     use_llm=skill_use_llm)
    engine = engine or _engine_of(game_path, source)
    result = revise_fn(source, directive_text, out_dir=out_dir, backend=backend,
                       max_repairs=max_repairs, engine=engine,
                       skill_context=skill_context)
    result = dict(result)
    result["directives"] = [d.to_dict() for d in directives]
    result["skill_context"] = skill_context
    return result


# ======================================================================== #
# The guarded harden loop
# ======================================================================== #
def _run_oracles(game_path, source, *, engine, tiers, stale, run_g3, budget_steps,
                 g3_kwargs, seed) -> dict:
    report = verify_fn(game_path)
    oracle = {"g4": attack_fn(game_path, source, report, engine=engine, tiers=tiers,
                              stale=stale, seed=seed)}
    # G3' is the RL learnability pre-filter: only run it on a game that CERTIFIES
    # (G0-G3), never spend PPO on an unsolvable game.
    if run_g3 and isinstance(report, dict) and report.get("passed"):
        oracle["g3_prime"] = g3_fn(game_path, budget_steps=budget_steps,
                                   **(g3_kwargs or {}))
    oracle["_verify"] = report
    return oracle


def _clean_verdict(oracle: dict) -> str:
    """The verdict when NO directive compiled (a clean or still-progressing game)."""
    if feedback.continue_training(oracle):
        return "CONTINUE_TRAINING"
    g4 = oracle.get("g4") or {}
    grade = g4.get("grade")
    if grade == "open":
        return "OPEN_UNMAPPED"   # a hard G4 finding outside the repair taxonomy
    if grade == "error":
        return "G4_ERROR"
    if grade == "bulletproof":
        return "BULLETPROOF"
    return "HARDENED"


def harden_game(game_path: str, *, out_dir: str = "scenes/games/harden",
                backend: str = "auto", max_repairs: int = 4, engine=None,
                tiers=(0,), stale: bool = True, run_g3: bool = False,
                budget_steps: int = 1_000_000, g3_kwargs: dict | None = None,
                max_rounds: int = MAX_ROUNDS_PER_FINDING,
                ledger_path: str | None = None, skill_root=None,
                skill_use_llm: bool = True, seed: int = 0) -> dict:
    """Run the guarded feedback-repair loop on the game at `game_path`.

    Each round: oracles (G4 always; G3' when `run_g3` and the game certifies) ->
    `compile_directives` -> revise-from-current-source -> re-certify. Convergence guard:
    at most `max_rounds` rounds; if a defect's fingerprint recurs after a revise (the fix
    did not remove it) the loop stops with ``REPAIR_STALLED``. A revise that fails to
    re-certify (verdict != COMPLETED) stops with ``REPAIR_FAILED`` and the last certified
    version is kept — the ORIGINAL `game_path` file is NEVER written by this loop (revise
    attempts land in the sandbox; a re-certified revision is adopted as the working copy).

    Returns::

        {"schema", "game", "game_path", "directives_issued", "rounds",
         "round_records", "final_verdict", "final_game_path", "original_untouched"}

    `final_verdict` in {HARDENED, BULLETPROOF, CONTINUE_TRAINING, REPAIR_STALLED,
    REPAIR_FAILED, MAX_ROUNDS, OPEN_UNMAPPED, G4_ERROR}.
    """
    t0 = time.time()
    ledger_path = ledger_path or _LEDGER_PATH
    slug = _slug_of(game_path)
    original_source = _read(game_path)

    current_path = game_path
    seen: set[str] = set()
    rounds: list[dict] = []
    directives_issued = 0
    verdict = None
    final_path = current_path
    budget_for_ledger = budget_steps if run_g3 else None

    for r in range(1, max_rounds + 1):
        source = _read(current_path)
        engine_r = engine or _engine_of(current_path, source)
        oracle = _run_oracles(current_path, source, engine=engine_r, tiers=tiers,
                              stale=stale, run_g3=run_g3, budget_steps=budget_steps,
                              g3_kwargs=g3_kwargs, seed=seed)
        directives = feedback.compile_directives(oracle)

        if not directives:
            verdict = _clean_verdict(oracle)
            rounds.append({"round": r, "directives": [], "verdict": verdict})
            break

        # Convergence guard: a directive we already tried to fix is BACK -> stalled.
        repeats = [d for d in directives if d.fingerprint in seen]
        if repeats:
            verdict = "REPAIR_STALLED"
            for d in directives:
                _append_ledger_event(
                    _ledger_entry(slug, current_path, d, "REPAIR_STALLED", r,
                                  backend=backend, budget_steps=budget_for_ledger),
                    ledger_path)
            rounds.append({"round": r, "verdict": "REPAIR_STALLED",
                           "directives": [d.to_dict() for d in directives],
                           "stalled_fingerprints": [d.fingerprint for d in repeats]})
            break

        for d in directives:
            seen.add(d.fingerprint)
        directives_issued += len(directives)

        round_sandbox = os.path.join(out_dir, slug, f"round_{r}")
        revise_res = revise_with_directives(
            current_path, directives, out_dir=round_sandbox, backend=backend,
            max_repairs=max_repairs, engine=engine_r, skill_root=skill_root,
            skill_use_llm=skill_use_llm)
        rv = revise_res.get("verdict")
        revised_path = revise_res.get("game_path")

        for d in directives:
            _append_ledger_event(
                _ledger_entry(slug, current_path, d, rv, r, backend=backend,
                              budget_steps=budget_for_ledger, revised_path=revised_path),
                ledger_path)
        rounds.append({"round": r, "verdict": rv, "revised_game_path": revised_path,
                       "directives": [d.to_dict() for d in directives]})

        if rv != "COMPLETED":
            # The fix did NOT re-certify -> keep the last certified version, never
            # overwrite. Attempts remain in the sandbox for audit.
            verdict = "REPAIR_FAILED"
            break

        # Re-certified through the full G0-G3 funnel -> adopt the revised (sandbox) game
        # as the working copy. The ORIGINAL game_path on disk stays untouched.
        current_path = revised_path
        final_path = revised_path
    else:
        verdict = verdict or "MAX_ROUNDS"

    return {
        "schema": "harden_report/v1",
        "game": slug,
        "game_path": game_path,
        "directives_issued": directives_issued,
        "rounds": len(rounds),
        "round_records": rounds,
        "final_verdict": verdict,
        "final_game_path": final_path,
        "original_untouched": _read(game_path) == original_source,
        "wall_s": round(time.time() - t0, 2),
    }
