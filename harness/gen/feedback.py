"""The FEEDBACK COMPILER — post-cert oracle outcomes -> personalized repair directives.

This is the PURE half of the feedback loop (the impure revise/harden driver lives in
`harness/gen/harden.py`). `compile_directives(oracle_results)` maps the machine-readable
results of the two post-certification oracles — G3' (RL learnability, `rl/certify.g3_prime`)
and G4 (adversarial attack, `verify/g4.run_g4`) — onto a list of :class:`Directive`s: the
personalized, milestone-anchored repair messages that re-enter generation FROM THE CURRENT
SOURCE (the revise path), each carrying a stable dedup fingerprint for the convergence guard.

The taxonomy (one directive-producing row per defect; every other outcome yields NOTHING):

  G3' (learnability) — read AFTER the full budget (still_improving == False):
    * still_improving == True      -> NO directive; the harden loop emits `continue_training`
                                      (the curve was climbing when the budget ended).
    * learnable == True            -> NO directive (the trained agent already solves it).
    * NOTHING ever latched          -> `g3_unsolvable`: the first objective is unreachable or
                                      the controls cannot make progress.
    * partial latch (stalls)        -> `g3_plateau`: names the LAST reliably-latched checkpoint
                                      and the FIRST never/rarely-latched one (a checkpoint pair).
    * every milestone latched but
      success stays 0               -> `g3_difficulty` (optional): reaches every milestone yet
                                      never wins — the final win condition is mis-gated/too hard.

  G4 (adversarial) — directive-producing outcomes only; the rest are informational:
    * single_action_win            -> `single_action_win`: the game is winnable by one action.
    * broken_gating                -> `broken_gating`: success reachable WITHOUT a gating checkpoint.
    * softlock                     -> `softlock`: quotes the frozen-state reproducer. ONLY the
                                      CERTIFIED class (tree-refutation confirmed — the
                                      inverse-value tier's oracle) compiles; heuristic `stuck`
                                      findings are informational. Rationale (first harden wave,
                                      2026-07-15): unconfirmed fuzz-"stuck" directives are
                                      unfixable-by-construction — a mostly-idle fuzzer looks
                                      immobile in ANY game; drive-cart re-certified a fix and
                                      the same fingerprint recurred (REPAIR_STALLED).
    * stuck, shortcut_beats_witness, escape, nan, unintended_success, ... -> NO directive
      (informational).

  PRESSURE (WAVE 1 failure-witness gate, gameverify._failure_witness_gate) — read from
  ``oracle_results["pressure"]`` (extract from a verify report with
  :func:`pressure_finding`):
    * no_pressure          -> `no_pressure`: is_failure() is hardcoded constant-false —
                              the game declares NO lose condition, so idling is free
                              (static proof). Repair: add a real failure condition.
    * failure_unreachable  -> `failure_unreachable`: is_failure() has logic but NO
                              adversarial rollout ever loses (the win races ahead, or the
                              detector never triggers). Repair: make failure triggerable.
    * has_pressure / other -> NO directive (a reachable failure was witnessed; healthy).

  DEAD SPACE (WAVE 2 proportion gate, gameverify._dead_space_gate) — read from
  ``oracle_results["dead_space"]`` (extract with :func:`dead_space_finding`):
    * dead_space           -> `dead_space`: the declared playfield is ~Nx (per axis)
                              larger than the span the action uses — an over-empty world.
                              A DIFFICULTY-tier POLISH (still certifies): tighten the world
                              to the action, or spread the elements to fill it.
    * proportioned / other -> NO directive (the world is sized to the action; healthy).

  RUNTIME ERROR (gdscript verify-lane stderr capture, gd_exec.parse_runtime_errors) —
  read from ``oracle_results["runtime_error"]`` (extract from a verify report with
  :func:`runtime_error_finding`):
    * a SCRIPT ERROR record   -> `runtime_error`: names the exact crash site
                                 (``<method>() crashes at line N: <message>``). DEFECT,
                                 proof-carrying (file:line + message). Compiled FIRST —
                                 a mid-episode crash is the root cause behind any
                                 downstream G0/G1 symptom.
    * no record / clean       -> NO directive (the game ran to completion).

PURE and deterministic: identical oracle dicts -> identical directives (same order, same
fingerprints). No I/O, no network, no torch — the whole taxonomy is offline-testable.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


# --- Thresholds ([eng.]) -----------------------------------------------------
LATCH_RELIABLE = 0.5   # per-checkpoint eval latch rate >= this == "reliably reached"
UNREACHED_MAX = 0.05   # max latch rate <= this == "nothing ever latched" (unsolvable)
NO_SUCCESS_MAX = 0.0   # success rate <= this == "never wins" (difficulty-reduction row)

# G4 outcomes that COMPILE to a directive. Everything else a G4 report can carry
# (stuck — the UNCONFIRMED heuristic class, shortcut_beats_witness, escape, nan,
# unintended_success, intended_success, nothing) is informational and yields NO
# directive: only defects with a PROOF (a replayable reproducer the model can be
# held to) are worth a repair round.
G4_DIRECTIVE_OUTCOMES = ("single_action_win", "broken_gating", "softlock")


# --- Directive SEVERITY tiers ([eng.], 2026-07-15 harden wave) ---------------
# A DEFECT is a real, proof-carrying brokenness (a single-action win, a bypassed gate,
# a softlock, no stakes, an unsolvable opening): worth the FULL repair budget — the game
# is BROKEN. A DIFFICULTY finding means the game is HARD-TO-LEARN, not broken: it
# RE-CERTIFIES unchanged (it stays valid), so grinding revise rounds on it wastes budget
# and can DEGRADE a good game chasing a phantom fix. The soft rows are the two G3'
# learnability-CURVE rows — the agent plateaus (`g3_plateau`) or reaches every milestone
# yet never wins (`g3_difficulty`) — plus the WAVE-2 `dead_space` PROPORTION polish (an
# over-empty world still certifies; it is a design polish, not a brokenness). Everything
# else — the G4 shapes, the PRESSURE no-stakes rows, and `g3_unsolvable` (NOTHING latched
# -> the game is broken, not merely hard) — is a DEFECT. The harden loop reads this tier to
# budget rounds (defects get `max_rounds`, difficulty a small `difficulty_budget` nudge)
# and to pick the terminal verdict (a difficulty that survives its nudge is HARDENED_HARD,
# a SUCCESS-ish terminal — never a repair failure).
DEFECT = "defect"
DIFFICULTY = "difficulty"
DIFFICULTY_SOURCES = frozenset({"g3_plateau", "g3_difficulty", "dead_space"})


def severity_of(source: str) -> str:
    """Map a taxonomy row id onto its severity tier — ``DIFFICULTY`` for the two G3'
    learnability-curve rows, ``DEFECT`` for every proof-carrying brokenness. Pure."""
    return DIFFICULTY if source in DIFFICULTY_SOURCES else DEFECT


# ======================================================================== #
# The directive
# ======================================================================== #
@dataclass(frozen=True)
class Directive:
    """One personalized repair directive compiled from an oracle finding.

    Fields:
      source          — the taxonomy row id (e.g. ``"g3_plateau"``, ``"broken_gating"``).
      origin          — which oracle produced it: ``"g3_prime"`` or ``"g4"``.
      checkpoint_keys — the declared milestone key(s) the directive is about (may be empty).
      text            — the human-readable, milestone-anchored directive message (the thing
                        spliced into the revise prompt).
      fingerprint     — a STABLE dedup id (source + checkpoint keys only; volatile run data
                        such as tick counts is excluded) so a defect that survives a repair
                        recompiles to the SAME fingerprint and the convergence guard can stop.
      severity        — the tier the harden loop budgets by: ``DEFECT`` (worth the full
                        repair budget) or ``DIFFICULTY`` (hard-to-learn; a small nudge only).
                        Derived from ``source`` via :func:`severity_of`.
      detail          — optional extra provenance (reproducer summary, action, rates).
    """
    source: str
    origin: str
    checkpoint_keys: tuple = ()
    text: str = ""
    fingerprint: str = ""
    severity: str = DEFECT
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "origin": self.origin,
            "checkpoint_keys": list(self.checkpoint_keys),
            "text": self.text,
            "fingerprint": self.fingerprint,
            "severity": self.severity,
            "detail": dict(self.detail),
        }


def _fingerprint(source: str, checkpoint_keys) -> str:
    """Stable dedup id: the DEFECT identity (row + checkpoints), never volatile run data.

    A single_action_win (no checkpoints) fingerprints on the row alone, so a repeat win by
    ANY action collapses to the same id; a plateau fingerprints on its stall-boundary pair;
    broken_gating on the skipped gate. This is exactly what the convergence guard needs: if
    the same defect recompiles after a revise, its fingerprint matches -> REPAIR_STALLED.
    """
    keys = ",".join(str(k) for k in (checkpoint_keys or []))
    digest = hashlib.sha1(f"{source}|{keys}".encode("utf-8")).hexdigest()[:12]
    return f"fp_{digest}"


def _mk(source: str, origin: str, checkpoint_keys, text: str, detail=None) -> Directive:
    keys = tuple(checkpoint_keys or ())
    return Directive(source=source, origin=origin, checkpoint_keys=keys,
                     text=text, fingerprint=_fingerprint(source, keys),
                     severity=severity_of(source), detail=dict(detail or {}))


# ======================================================================== #
# G3' (learnability) -> directives
# ======================================================================== #
def _checkpoint_pair(keys: list, rates: dict):
    """(last reliably-latched key, first never/rarely-latched key) in DECLARED order.

    ``last_reliable`` is the DEEPEST declared checkpoint reached in >= LATCH_RELIABLE of the
    eval episodes; ``first_stuck`` is the checkpoint immediately AFTER it (the stall boundary
    — the first the agent does not reliably reach). If nothing is reliably latched,
    (None, first declared key). If everything is reliably latched, (last key, None)."""
    reliable_idx = [i for i, k in enumerate(keys)
                    if float(rates.get(k, 0.0)) >= LATCH_RELIABLE]
    if not reliable_idx:
        return None, (keys[0] if keys else None)
    last_i = max(reliable_idx)
    last_reliable = keys[last_i]
    first_stuck = keys[last_i + 1] if last_i + 1 < len(keys) else None
    return last_reliable, first_stuck


def _compile_g3(g3: dict) -> list:
    if not g3:
        return []
    # PROGRESSING or already SOLVED -> no repair directive (the loop continues training
    # in the first case, certifies in the second).
    if g3.get("still_improving"):
        return []
    if g3.get("learnable"):
        return []

    keys = [str(k) for k in (g3.get("checkpoint_keys") or [])]
    rates = {str(k): float(v) for k, v in (g3.get("per_checkpoint_latch_rate") or {}).items()}
    sr = float(g3.get("stochastic_success_rate") or 0.0)
    gr = float(g3.get("final_success_rate") or 0.0)
    success = max(sr, gr)
    budget = g3.get("budget_steps")
    n_eval = g3.get("n_eval")

    max_rate = max(rates.values()) if rates else 0.0
    min_rate = min(rates.values()) if rates else 0.0

    # (1) Nothing ever latched after the full budget -> unsolvable by the trained agent.
    if not keys or max_rate <= UNREACHED_MAX:
        first = keys[0] if keys else None
        gate = f"the first objective '{first}'" if first else "the first objective"
        text = (
            "UNSOLVABLE BY THE TRAINED AGENT: after the full "
            f"{budget}-env-step budget the RL policy never latched ANY declared "
            f"checkpoint (success rate {round(success, 3)}). {gate.capitalize()} appears "
            "unreachable, or the controls cannot make progress at all. Make the opening "
            "playable — bring the first objective within reach of the starting state and "
            "verify the ACTIONS actually move the agent toward it — WITHOUT removing the "
            "goal. If nothing can be reached, the game is broken, not merely hard.")
        return [_mk("g3_unsolvable", "g3_prime", [first] if first else [], text,
                    {"success_rate": round(success, 3), "budget_steps": budget,
                     "per_checkpoint_latch_rate": rates})]

    last_reliable, first_stuck = _checkpoint_pair(keys, rates)

    # (2) Every declared milestone reliably latched, yet the agent never wins -> the WIN
    # CONDITION is the wall (difficulty-reduction row).
    if first_stuck is None and success <= NO_SUCCESS_MAX:
        gate = last_reliable or (keys[-1] if keys else "the final milestone")
        text = (
            "REACHES EVERY MILESTONE BUT NEVER WINS: over the eval episodes the agent "
            f"reliably latches every declared checkpoint (through '{gate}') yet its "
            f"success rate stays {round(success, 3)} after the full budget. The final win "
            "condition is mis-gated or too strict — loosen the success test just past "
            f"'{gate}' (widen the goal region, relax the final tolerance/timing) so "
            "reaching the last milestone can actually convert into a win. KEEP every "
            "earlier stage intact.")
        return [_mk("g3_difficulty", "g3_prime", [gate], text,
                    {"success_rate": round(success, 3), "n_eval": n_eval,
                     "per_checkpoint_latch_rate": rates})]

    # (3) Partial progress -> the checkpoint-pair (plateau) directive.
    if first_stuck is None:
        # everything latches but the agent sometimes wins -> hard-but-learnable, not a
        # harden defect (leave it to the curriculum difficulty loop).
        return []
    if last_reliable is None:
        text = (
            f"STALLS AT THE VERY FIRST OBJECTIVE: the agent rarely even reaches "
            f"'{first_stuck}' (latch rate {rates.get(first_stuck, 0.0)}, success "
            f"{round(success, 3)}). Ease the opening so play can begin — bring "
            f"'{first_stuck}' closer to the start, widen its trigger, or slow/steady any "
            "hazard before it — WITHOUT changing the goal or any later stage.")
    else:
        text = (
            f"THE AGENT PLATEAUS BETWEEN '{last_reliable}' AND '{first_stuck}': it "
            f"reliably reaches '{last_reliable}' (latch rate {rates.get(last_reliable, 0.0)}) "
            f"but rarely gets past to '{first_stuck}' (latch rate {rates.get(first_stuck, 0.0)}; "
            f"overall success {round(success, 3)}). EASE exactly the '{last_reliable}' -> "
            f"'{first_stuck}' segment — widen the gap, slow or steady the hazard, enlarge "
            f"the target, or relax the timing there — and KEEP every stage the agent already "
            "clears, and every stage after, intact.")
    return [_mk("g3_plateau", "g3_prime", [k for k in (last_reliable, first_stuck) if k],
                text, {"last_reliable": last_reliable, "first_stuck": first_stuck,
                       "success_rate": round(success, 3),
                       "per_checkpoint_latch_rate": rates})]


# ======================================================================== #
# G4 (adversarial) -> directives
# ======================================================================== #
def _g4_directive(finding: dict):
    outcome = finding.get("outcome")
    ev = finding.get("evidence") or {}
    repro = finding.get("reproducer") or {}

    if outcome == "single_action_win":
        action = finding.get("action")
        ticks = finding.get("ticks")
        text = (
            "BROKEN GAME — WINNABLE BY ONE ACTION: repeating the single action "
            f"{action!r} alone wins in {ticks} decision ticks. Success needs no varied "
            "play. Add a REAL obstacle or choice on the winning path — a gate that a "
            "different action must open, a hazard that spamming one direction hits, or a "
            "second objective off that axis — so no single repeated action can win. Keep "
            "the goal reachable by genuine play.")
        return _mk("single_action_win", "g4", [], text,
                   {"action": action, "ticks": ticks, "reproducer": repro})

    if outcome == "broken_gating":
        skipped = list(ev.get("skipped_checkpoints") or [])
        keys_txt = ", ".join(f"'{k}'" for k in skipped) or "a declared gating checkpoint"
        text = (
            f"BROKEN GATING: success is reachable WITHOUT latching {keys_txt} — a "
            "checkpoint that is supposed to gate the win. The gate is bypassable. Make "
            f"success actually REQUIRE {keys_txt}: place the goal behind that milestone so "
            "no path reaches success while skipping it (no shortcut around the gate). Keep "
            "every checkpoint name and the goal itself unchanged.")
        return _mk("broken_gating", "g4", skipped, text,
                   {"skipped_checkpoints": skipped, "reproducer": repro})

    if outcome in ("softlock", "stuck"):
        detail = finding.get("detail") or ""
        plan = ((repro.get("action_plan") or {}).get("sequence")
                or (repro.get("action_plan") or {}).get("actions") or [])
        prov = repro.get("provenance") or {}
        summary = detail or (
            f"an action prefix (len {len(plan)}) drives the game into a dead-end state")
        if outcome == "softlock":
            text = (
                f"SOFTLOCK (dead-end state): {summary}. Reproducer: seed "
                f"{repro.get('seed')}, action prefix of length {len(plan)}"
                + (f" (subtree {prov.get('subtree_status')})" if prov else "")
                + ". Ensure EVERY reachable state can still reach the goal — remove the "
                "one-way trap, or add an escape/reset from the dead end — WITHOUT changing "
                "the goal or the intended path.")
        else:
            text = (
                f"SOFT-LOCK (controlled body immobilised): {summary}. Reproducer: seed "
                f"{repro.get('seed')}, action prefix of length {len(plan)}. An adversarial "
                "player travelled then got permanently stuck with no way to make further "
                "progress. Remove the trap geometry / add an escape so the agent can never "
                "be permanently immobilised, keeping the intended path intact.")
        return _mk(outcome, "g4", [], text,
                   {"reproducer": repro, "detail": detail})

    return None


def _compile_g4(g4: dict) -> list:
    if not g4:
        return []
    out, seen = [], set()
    for finding in (g4.get("findings") or []):
        if finding.get("outcome") not in G4_DIRECTIVE_OUTCOMES:
            continue                                   # informational -> NO directive
        d = _g4_directive(finding)
        if d is None or d.fingerprint in seen:
            continue
        seen.add(d.fingerprint)
        out.append(d)
    return out


# ======================================================================== #
# PRESSURE (WAVE 1 failure-witness gate) -> directives
# ======================================================================== #
# Outcomes that COMPILE to a directive. ``has_pressure`` (a reachable failure was
# witnessed) and anything else are informational -> NO directive. Proof-carrying:
# ``no_pressure`` carries a STATIC proof (is_failure literally returns false);
# ``failure_unreachable`` carries the broad-sweep reproducer-ABSENCE (n_plans lost 0)
# — both are fixable-by-construction (add / repair a reachable lose condition), unlike
# the unconfirmed `stuck` heuristic the taxonomy deliberately drops.
PRESSURE_DIRECTIVE_OUTCOMES = ("no_pressure", "failure_unreachable")


def _compile_pressure(pressure: dict) -> list:
    if not pressure:
        return []
    outcome = pressure.get("outcome")
    if outcome not in PRESSURE_DIRECTIVE_OUTCOMES:
        return []                              # has_pressure / other -> healthy, no directive
    detail = pressure.get("detail") or ""
    constant_false = bool(pressure.get("constant_false"))
    if outcome == "no_pressure":
        head = "NO STAKES — THE GAME CANNOT BE LOST"
        fallback = ("is_failure() is hardcoded false; add a real failure condition "
                    "(hazard, timeout, out-of-bounds, resource depletion) so a stalled "
                    "episode is punished. Keep the goal reachable.")
    else:
        head = "UNREACHABLE FAILURE — THE GAME CANNOT BE LOST IN PRACTICE"
        fallback = ("is_failure() never fires from any reachable state; make failure a "
                    "condition a real player could actually trigger (the win may resolve "
                    "first, or the detector never triggers). Keep the goal reachable.")
    text = f"{head}: {detail or fallback}"
    return [_mk(outcome, "pressure", [], text,
                {"constant_false": constant_false,
                 "evidence": dict(pressure.get("evidence") or {}),
                 "reproducer": dict(pressure.get("reproducer") or {})})]


def pressure_finding(verify_report: dict) -> dict:
    """Pull the machine-readable PRESSURE finding out of a verify report — the
    failure-witness gate stashes it under
    ``layers.G3_solve.checks.failure_witness.finding`` (gameverify). Returns ``{}``
    when the gate did not run (non-gdscript engine, or a game rejected earlier).

    Convenience so the harden driver can wire it in one line —
    ``oracle_results["pressure"] = pressure_finding(report)`` — while this compiler
    stays PURE (it only ever reads the ``oracle_results`` dict it is handed)."""
    try:
        fw = (((verify_report or {}).get("layers") or {}).get("G3_solve") or {}) \
            .get("checks", {}).get("failure_witness") or {}
        return dict(fw.get("finding") or {})
    except Exception:
        return {}


# ======================================================================== #
# DEAD SPACE / PROPORTION (WAVE 2 space-utilization gate) -> directive
# ======================================================================== #
# The dead-space gate (gameverify._dead_space_gate, DEMO_GAP_ANALYSIS §Gap 3) measures a
# purely-geometric FACT: the declared playfield is ~Nx (per axis) larger than the span the
# action uses. An over-empty world still CERTIFIES (advisory, non-gating), so this is a
# DIFFICULTY-tier POLISH, not a defect — a small nudge toward a tighter world, never worth
# the full repair budget. Proof-carrying: the finding quotes the measured ratio (a bounded
# static fact the model can be held to). ``dead_space`` compiles; ``proportioned`` does not.
DEAD_SPACE_DIRECTIVE_OUTCOMES = ("dead_space",)


def _compile_dead_space(finding: dict) -> list:
    if not finding:
        return []
    if finding.get("outcome") not in DEAD_SPACE_DIRECTIVE_OUTCOMES:
        return []                              # proportioned / other -> healthy, no directive
    detail = finding.get("detail") or ""
    fallback = ("the playfield dwarfs the region the action uses; tighten the world to the "
                "action or spread the elements to fill it. Keep the goal reachable.")
    text = f"DEAD SPACE — MOST OF THE WORLD IS EMPTY: {detail or fallback}"
    return [_mk("dead_space", "proportion", [], text,
                {"linear_ratio": finding.get("linear_ratio"),
                 "measure_ratio": finding.get("measure_ratio"),
                 "threshold": finding.get("threshold"), "dims": finding.get("dims")})]


def dead_space_finding(verify_report: dict) -> dict:
    """Pull the machine-readable ``dead_space`` finding off a verify report (the gdscript
    lane stashes it at ``report["dead_space"]`` ONLY when the proportion gate flagged an
    over-empty world — parallel to ``report["runtime_error"]``). Returns ``{}`` for a
    proportioned game (or a non-gdscript engine). A one-line bridge for the harden driver
    (``oracle_results["dead_space"] = dead_space_finding(rep)``) that keeps this compiler
    PURE."""
    try:
        return dict((verify_report or {}).get("dead_space") or {})
    except Exception:
        return {}


# ======================================================================== #
# RUNTIME ERROR (verify-lane stderr capture) -> directive
# ======================================================================== #
# A generated game that PARSES but CRASHES AT RUNTIME (a null deref in act(), a
# build() that raises) is the ROOT defect the funnel would otherwise misreport as a
# downstream symptom ("no controlled body", "dead action"). The gdscript verify lane
# mines the tee'd Godot stderr (harness/verify/gd_exec.parse_runtime_errors) and
# stashes the first SCRIPT ERROR record on the report as ``runtime_error``. It is
# DEFECT-severity and PROOF-CARRYING: a file:line + message reproducer the model can be
# held to (like single_action_win / softlock, unlike the dropped heuristic `stuck`).
def _compile_runtime_error(rte) -> list:
    """Compile the ``runtime_error`` finding (a parsed SCRIPT ERROR record) into a repair
    directive naming the exact crash site. Accepts a single record dict or a list; empty
    / falsy -> no directive. Fingerprint keys on the crash LOCATION (method@line) so a
    crash that MOVES after a fix is a distinct defect (progress), while the SAME crash
    recompiling to the same fingerprint trips the convergence guard (REPAIR_STALLED)."""
    if not rte:
        return []
    rec = rte[0] if isinstance(rte, (list, tuple)) else rte
    if not rec:
        return []
    method = rec.get("method") or "a game method"
    line = rec.get("line")
    message = rec.get("message") or "runtime script error"
    kind = rec.get("kind") or "runtime"
    where = f"line {line}" if line is not None else "an unknown line"
    verb = "hits a parse error" if kind == "parse" else "crashes"
    text = (
        f"RUNTIME CRASH — {method}() {verb} at {where}: {message}. The engine aborted "
        f"the call mid-episode (GDScript has no exceptions, so the call silently did "
        f"nothing and the game misreports downstream — a dead action or a missing body). "
        f"Fix the null / uninitialised value at that line: guard it (check for null "
        f"before use), initialise it in build(), or correct the call so {method}() runs "
        f"to completion every tick. Keep the goal and the intended play intact.")
    loc = f"{method}@{line}"
    return [Directive(source="runtime_error", origin="runtime", checkpoint_keys=(),
                      text=text, fingerprint=_fingerprint("runtime_error", [loc]),
                      detail={"method": method, "line": line, "message": message,
                              "kind": kind})]


def runtime_error_finding(verify_report: dict) -> dict:
    """Pull the machine-readable ``runtime_error`` record off a verify report (the
    gdscript lane stashes it at ``report["runtime_error"]`` when a build/act crash was
    captured from stderr). Returns ``{}`` when the game did not crash — a one-line bridge
    for the harden driver (``oracle_results["runtime_error"] = runtime_error_finding(rep)``)
    that keeps this compiler PURE."""
    try:
        return dict((verify_report or {}).get("runtime_error") or {})
    except Exception:
        return {}


# ======================================================================== #
# Public API
# ======================================================================== #
def compile_directives(oracle_results: dict) -> list:
    """Map post-cert oracle outcomes onto personalized repair :class:`Directive`s.

    ``oracle_results`` carries optional ``"runtime_error"`` (a captured SCRIPT ERROR
    record — see :func:`runtime_error_finding`), ``"g4"`` (a `run_g4` report),
    ``"pressure"`` (a failure-witness finding — see :func:`pressure_finding`),
    ``"dead_space"`` (a proportion finding — see :func:`dead_space_finding`) and
    ``"g3_prime"`` (a `g3_prime` result) dicts. Returns the directives to feed the revise
    loop — RUNTIME CRASH (the root defect) first, then G4 (broken-game shapes), then
    PRESSURE (no stakes), then DEAD SPACE (proportion polish), then G3' (learnability) —
    deduplicated by fingerprint. An empty list means either a CLEAN game or a
    still-progressing G3' run (see :func:`continue_training`)."""
    oracle_results = oracle_results or {}
    directives, seen = [], set()
    for d in (_compile_runtime_error(oracle_results.get("runtime_error") or {})
              + _compile_g4(oracle_results.get("g4") or {})
              + _compile_pressure(oracle_results.get("pressure") or {})
              + _compile_dead_space(oracle_results.get("dead_space") or {})
              + _compile_g3(oracle_results.get("g3_prime") or {})):
        if d.fingerprint in seen:
            continue
        seen.add(d.fingerprint)
        directives.append(d)
    return directives


def continue_training(oracle_results: dict) -> bool:
    """True IFF the G3' curve was still improving when the budget ended AND the game is
    not yet solved — the compiler's `continue_training` signal (give the agent more
    budget before judging learnability, rather than issuing a repair directive)."""
    g3 = (oracle_results or {}).get("g3_prime") or {}
    return bool(g3) and bool(g3.get("still_improving")) and not g3.get("learnable")


def combined_directive_text(directives) -> str:
    """Join several directives into ONE minimal-edit instruction block for the revise
    message. Numbered when there is more than one so the model addresses each defect."""
    ds = list(directives or [])
    if not ds:
        return ""
    if len(ds) == 1:
        return ds[0].text
    lines = ["Apply ALL of the following repair directives with minimal edits:"]
    for i, d in enumerate(ds, 1):
        lines.append(f"({i}) {d.text}")
    return "\n\n".join(lines)
