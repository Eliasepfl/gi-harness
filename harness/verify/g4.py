"""G4 — adversarial suite (Tier 0 mechanical + Tier 1 cheap-LLM attackers).

G4 sits AFTER the G0-G3 funnel (harness.verify.gameverify) and is optional: it
takes a CERTIFIED game (source + its passing report with witness) and hammers it
to prove it is bulletproof — or hands back the crack. It never edits or re-runs
the funnel; it rides the SAME executor seam (harness.verify.executors) and the
SAME §2 runner semantics, adding only a referee (an outcome classifier) on top.

Design: notes/adversarial/G4_DESIGN.md. Everything here is pure-Python
orchestration; the physics engine is reached ONLY through the batch executor, so
the same probes run on a `py` (pymunk) or `js` (Planck) game unchanged.

Two tiers:

  TIER 0 — mechanical, NO LLM, seeded + deterministic (native speed):
    * Avoidance probes — policies that actively try NOT to win (all-noop,
      noop-heavy mixes, anti-witness = the witness with its actions permuted).
      A win under any of these = the goal is reachable without playing ->
      `unintended_success` -> HARD fail (degenerate/unavoidable goal).
    * Single-action-win — hold each declared action for the whole budget; a win
      is flagged with its tick count (the anti-triviality bar the >=5-tick G3
      threshold can miss — jelly-tower solved in 8 ticks by one action).
    * Breaker fuzz — seeded action-fuzz families (max-frequency spam, alternating
      extremes, boundary hugging, long holds, random macro-actions) hunting NaN,
      escapes (executor `nan`/`oob` extras) and stuck/soft-lock states. Thousands
      of cheap episodes; findings counted.
    * Faster-than-witness shortcut — any episode that WINS in
      < witness.ticks x SHORTCUT_FACTOR (or < TRIVIAL_TICKS) is reported as a
      `shortcut_beats_witness` (evidence the intended path is bypassable — a
      warning, not a hard fail).

  TIER 1 — cheap-LLM attack proposers (interface + one working OpenRouter lane):
    * Attackers emit PURE DATA only — ATTACK RECORDs (JSON): a pattern name from
      a fixed strategy vocabulary + params, or an explicit action sequence. Never
      code. Validation is mechanical replay through the executor.
    * The OpenRouter lane reuses harness.gen.gamegen's HTTP helpers (key/model
      from env.py, retry/backoff) WITHOUT touching that module; one call per lane
      (model) returns K proposals. Each is replayed and classified as
      incomprehension (invalid / unreplayable) | misconception (replayed, no
      finding) | hit (a finding). Every proposal + its replay outcome is persisted
      to the report for traceability.
    * Degrades gracefully: no key / no `requests` -> tier 1 skipped with a clear
      report field; never raises to the caller.

Grade (report `grade`):
  * `open`       — a HARD finding exists (unintended_success / nan / escape);
                   route to the author repair loop.
  * `hardened`   — survived the enabled tiers with no hard finding, but soft
                   findings remain OR tier 1 was not run cleanly.
  * `bulletproof`— zero findings across tier 0 AND a cleanly-run tier 1.
"""

from __future__ import annotations

import hashlib
import json
import random

# The executor seam + the §2 runner helpers. We only READ gameverify (never edit
# it): its loader, engine detector and default World factory are stable handles.
from harness.verify.executors import JsExecutor, PyExecutor, VerifyError
from harness.verify.gameverify import (
    _default_world_factory, detect_engine, load_game,
)

SCHEMA = "g4_report/v1"

# --- Constants ([eng.] = calibrated engineering choice) ------------------- #
WORLD_SEED = 0             # physics seed — the game was certified on this (G3 witness)
PROBE_HORIZON = 120        # decision ticks per probe episode (matches G3 PROBE_HORIZON)
ESCAPE_MARGIN = 200.0      # px beyond world+margin = an escape (matches G1 ESCAPE_MARGIN)
TRIVIAL_TICKS = 5          # a win faster than this is degenerate (matches G3)
SHORTCUT_FACTOR = 0.5      # a win in < witness.ticks x this is a shortcut [eng.]

# Stuck / soft-lock heuristic (from the episode dict only — cheap, no frames):
# the controlled body TRAVELLED (moved > STUCK_MOVE_MIN from its start) then went
# immobile (speed < STUCK_SPEED_EPS) with no fresh checkpoint latch for the last
# STUCK_WINDOW ticks, and the episode never terminated. A body that simply never
# moved is NOT a soft-lock (that is a non-starter, G3's concern), so displacement
# is required — this keeps the signal low-noise.
STUCK_WINDOW = 20          # ticks with no new latch to call it stuck [eng.]
STUCK_SPEED_EPS = 1.0      # px/tick speed below which the body is "immobile" [eng.]
STUCK_MOVE_MIN = 20.0      # px the body must have travelled from start first [eng.]

# Default tier-0 fuzz sizing (a real pass; scale up via kwargs for thousands).
DEFAULT_FUZZ_RANDOM = 300  # seeded random macro-action episodes
DEFAULT_FUZZ_LONG = 150    # seeded long-hold episodes
DEFAULT_NOOP_HEAVY = 60    # noop-heavy avoidance episodes
DEFAULT_ALT_PERIODS = (1, 2, 3, 5, 8)   # alternating-extremes periods
DEFAULT_ANTI_VARIANTS = 3  # anti-witness permutation variants

# Tier-1 defaults.
DEFAULT_ATTACKS_PER_CALL = 5

# Which outcomes are HARD (route-to-repair) vs SOFT (warning/flag).
_HARD_OUTCOMES = {"unintended_success", "nan", "escape"}


class _InvalidPlan(Exception):
    """An action plan that cannot be validated/expanded (out-of-vocabulary token,
    unknown pattern, empty sequence). In Tier 1 this maps to `incomprehension`."""


# ======================================================================== #
# Strategy vocabulary — the fixed menu of parameterized patterns.
# These deterministic expanders ARE the Tier-0 fuzz seeds AND the Tier-1 attacker
# vocabulary. Every token an expander emits is drawn from the game's own ACTIONS
# (or None for a noop tick), so a pattern is always in-vocabulary by construction;
# only an attacker-supplied `sequence` can carry an unknown token (-> rejected).
# ======================================================================== #
STRATEGY_VOCAB = {
    "spam": "hold ONE action every tick. params: {action}",
    "boundary_hug": "hold one action to pin the body against a wall. params: {action}",
    "alternate": "alternate two actions in blocks. params: {a, b, period}",
    "long_hold": "random actions, each held for a long block. params: {seed}",
    "random_macro": "random actions held 1-4 ticks (G3-style search). params: {seed}",
    "hold_then_random": "hold an action, then go random. params: {hold, hold_ticks, then, seed}",
    "avoid": "mostly idle (noop-heavy) — try NOT to progress. params: {seed}",
    "noop": "do nothing at all. params: {}",
    "sequence": "an explicit per-tick action list. params: {sequence: [action, ...]}",
}


def _expand(pattern, params, actions, horizon, *, witness_actions=None):
    """Deterministically expand a (pattern, params) into a flat per-tick action
    list of length <= horizon. Raises _InvalidPlan on any out-of-vocabulary token
    or unknown pattern. `None` is a legal token (a noop tick)."""
    params = params or {}

    def _check(token):
        if token is not None and token not in actions:
            raise _InvalidPlan(f"action {token!r} not in ACTIONS")
        return token

    if pattern in ("spam", "boundary_hug"):
        a = _check(params.get("action"))
        if a is None:
            raise _InvalidPlan("spam/boundary_hug needs an 'action'")
        return [a] * horizon

    if pattern == "alternate":
        a = _check(params.get("a"))
        b = _check(params.get("b"))
        if a is None or b is None:
            raise _InvalidPlan("alternate needs 'a' and 'b'")
        period = max(1, int(params.get("period", 1)))
        plan: list = []
        while len(plan) < horizon:
            plan.extend([a] * period)
            plan.extend([b] * period)
        return plan[:horizon]

    if pattern == "noop":
        return [None] * horizon

    if pattern == "avoid":
        rng = random.Random(int(params.get("seed", 0)))
        return [None if rng.random() < 0.85 else rng.choice(actions)
                for _ in range(horizon)]

    if pattern == "long_hold":
        rng = random.Random(int(params.get("seed", 0)))
        plan = []
        while len(plan) < horizon:
            plan.extend([rng.choice(actions)] * rng.randint(8, 30))
        return plan[:horizon]

    if pattern == "random_macro":
        rng = random.Random(int(params.get("seed", 0)))
        plan = []
        while len(plan) < horizon:
            plan.extend([rng.choice(actions)] * rng.randint(1, 4))
        return plan[:horizon]

    if pattern == "hold_then_random":
        hold = _check(params.get("hold"))
        if hold is None:
            raise _InvalidPlan("hold_then_random needs a 'hold' action")
        hold_ticks = max(0, min(horizon, int(params.get("hold_ticks", 30))))
        then = params.get("then")
        if then is not None:
            _check(then)
        rng = random.Random(int(params.get("seed", 0)))
        plan = [hold] * hold_ticks
        if then is not None and len(plan) < horizon:
            plan.append(then)             # the seeded first post-hold action
        while len(plan) < horizon:
            plan.append(rng.choice(actions))
        return plan[:horizon]

    if pattern == "anti_witness":
        # Tier-0 only: the witness with its actions cyclically permuted (do the
        # "wrong" moves). None stays None. Not part of the attacker vocabulary.
        wit = list(witness_actions or [])
        if not wit:
            raise _InvalidPlan("anti_witness needs a witness")
        shift = max(1, int(params.get("shift", 1)))
        if params.get("reverse"):
            wit = list(reversed(wit))
        out = []
        for t in wit:
            if t is None or t not in actions:
                out.append(t)
            else:
                out.append(actions[(actions.index(t) + shift) % len(actions)])
        return out[:horizon] or [None]

    if pattern == "sequence":
        seq = params.get("sequence")
        if not isinstance(seq, list) or not seq:
            raise _InvalidPlan("sequence needs a non-empty list")
        out = [_check(t) for t in seq]
        return out[:horizon]

    raise _InvalidPlan(f"unknown pattern {pattern!r}")


# ======================================================================== #
# The referee — classify one replayed episode into the outcome vocabulary.
# ======================================================================== #
def _speed(vel) -> float:
    try:
        return (float(vel[0]) ** 2 + float(vel[1]) ** 2) ** 0.5
    except (TypeError, IndexError, ValueError):
        return 0.0


def _displacement(a, b) -> float:
    try:
        return ((float(a[0]) - float(b[0])) ** 2 + (float(a[1]) - float(b[1])) ** 2) ** 0.5
    except (TypeError, IndexError, ValueError):
        return 0.0


def _digest(snapshot) -> str:
    """Short, stable hash of a (rounded) snapshot — reproducibility / dedup."""
    try:
        rounded = {n: {k: [round(float(x), 2) for x in v] if isinstance(v, (list, tuple))
                       else round(float(v), 2) for k, v in body.items()}
                   for n, body in sorted(snapshot.items())}
        payload = json.dumps(rounded, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 - a weird snapshot must not break classification
        payload = json.dumps(snapshot, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _evidence(ep, engine) -> dict:
    result = ep.get("result")
    ticks = int(ep.get("ticks", 0))
    latches = ep.get("checkpoints", {}) or {}
    return {
        "result": result,
        "ticks": ticks,
        "success_tick": ticks if result == "success" else None,
        "failure_tick": ticks if result == "failure" else None,
        "checkpoints": dict(latches),
        "nan": bool(ep.get("nan", False)),
        "escape": list(ep.get("oob", []) or []),
        "final_digest": _digest(ep.get("final_snapshot", {}) or {}),
        "engine": engine,
    }


def classify(ep, engine, *, avoidance, witness_ticks, controlled, initial_snapshot):
    """Map a replayed episode dict to (outcome, evidence).

    Outcome vocabulary (findings marked ✓; `nothing`/`intended_success` are not):
      unintended_success ✓  success under an avoidance plan
      nan                ✓  NaN/explosion during replay
      escape             ✓  a dynamic body left world+ESCAPE_MARGIN
      shortcut_beats_witness ✓  success far faster than the certified witness
      stuck              ✓  the controlled body travelled then soft-locked
      intended_success      a normal, non-shortcut win (not a finding)
      nothing               the attack failed to break the game
    """
    ev = _evidence(ep, engine)
    result = ev["result"]

    # Physics broke — highest priority, regardless of result.
    if ev["nan"]:
        return "nan", ev
    if ev["escape"]:
        return "escape", ev

    if result == "success":
        if avoidance:
            return "unintended_success", ev
        if (witness_ticks is not None
                and ev["ticks"] < max(TRIVIAL_TICKS, witness_ticks * SHORTCUT_FACTOR)):
            return "shortcut_beats_witness", ev
        return "intended_success", ev

    # Non-terminal: look for a soft-lock (travelled, then immobile, no fresh latch).
    if (result == "budget" and controlled and initial_snapshot
            and controlled in (ep.get("final_snapshot") or {})
            and controlled in initial_snapshot):
        final = ep["final_snapshot"][controlled]
        start = initial_snapshot[controlled]
        moved = _displacement(final.get("pos"), start.get("pos"))
        immobile = _speed(final.get("vel")) < STUCK_SPEED_EPS
        latched = [t for t in ev["checkpoints"].values() if t is not None]
        last_latch = max(latched) if latched else 0
        no_recent_latch = last_latch <= ev["ticks"] - STUCK_WINDOW
        if moved > STUCK_MOVE_MIN and immobile and no_recent_latch and ev["ticks"] >= STUCK_WINDOW:
            return "stuck", ev

    return "nothing", ev


# ======================================================================== #
# Replay helpers
# ======================================================================== #
def _make_executor(engine, world_factory):
    if engine == "js":
        return JsExecutor()
    return PyExecutor(world_factory=world_factory or _default_world_factory)


def _replay_all(executor, game_source, plans, horizon):
    """Replay a list of flat action plans -> list of episode dicts (with the
    nan/oob escape extras). One batch call (JS: one node process)."""
    specs = [{"seed": WORLD_SEED, "actions": list(p)} for p in plans]
    return executor.run_batch(game_source, specs, horizon, escape_margin=ESCAPE_MARGIN)


def _initial_snapshot(executor, game_source):
    """The freshly-built world's snapshot (a 0-tick episode) — the start pose the
    stuck heuristic measures displacement from."""
    try:
        recs = executor.run_batch(game_source, [{"seed": WORLD_SEED, "actions": []}], 0)
        return recs[0].get("final_snapshot", {}) or {}
    except Exception:  # noqa: BLE001 - stuck detection is best-effort
        return {}


def _reproducer(engine, pattern, params, plan):
    """A replayable reproducer for a finding (design §3.5: engine + seed + plan)."""
    rep = {"engine": engine, "seed": WORLD_SEED,
           "action_plan": {"pattern": pattern, "params": dict(params or {})}}
    # anti_witness / sequence are only replayable with the concrete list, so keep
    # a (capped) flat sequence alongside the pattern for exact reproduction.
    if pattern in ("anti_witness", "sequence"):
        rep["action_plan"] = {"kind": "sequence", "sequence": list(plan)[:PROBE_HORIZON]}
    return rep


# ======================================================================== #
# Controlled-body / witness extraction from the certified report
# ======================================================================== #
def _controlled_from_report(report):
    try:
        got = report["layers"]["G0_static"]["checks"]["controlled"]["controlled"]
        if isinstance(got, list) and got:
            return got[0]
    except (KeyError, TypeError, IndexError):
        pass
    return None


def _derive_controlled(report, game_source, engine, world_factory):
    """The controlled body's name — from the report, else (py) by building once."""
    name = _controlled_from_report(report)
    if name or engine != "py":
        return name
    try:
        game = load_game(game_source)
        world = (world_factory or _default_world_factory)(seed=WORLD_SEED)
        game.build(world)
        return world.controlled()
    except Exception:  # noqa: BLE001 - controlled derivation is best-effort
        return None


def _witness(report):
    w = report.get("witness") if isinstance(report, dict) else None
    return w if isinstance(w, dict) else None


# ======================================================================== #
# TIER 0 — mechanical fuzz
# ======================================================================== #
def _tier0_specs(actions, witness_actions, horizon, seed, *,
                 fuzz_random, fuzz_long, noop_heavy, alt_periods, anti_variants):
    """Build the deterministic (group, family, pattern, params) spec list.

    `group` in {"avoidance", "single_action", "breaker"} drives classification;
    every spec is expandable via _expand so its reproducer replays exactly.
    """
    specs = []

    def add(group, family, pattern, params):
        specs.append({"group": group, "family": family,
                      "pattern": pattern, "params": params})

    # -- Avoidance: try NOT to win (a win here => degenerate goal) --
    add("avoidance", "noop", "noop", {})
    for i in range(noop_heavy):
        add("avoidance", "noop_heavy", "avoid", {"seed": seed * 100003 + i})
    if witness_actions and len(actions) >= 2:
        # shift in 1..len(actions)-1 is ALWAYS a non-identity permutation (a shift
        # that is a multiple of len(actions) would just replay the witness and
        # falsely read as an avoidance win). `reverse` adds order diversity.
        n_shifts = len(actions) - 1
        for v in range(anti_variants):
            add("avoidance", "anti_witness", "anti_witness",
                {"shift": 1 + (v % n_shifts), "reverse": v >= n_shifts})

    # -- Single-action-win: hold each action for the whole budget --
    for a in actions:
        add("single_action", "spam", "spam", {"action": a})

    # -- Breaker fuzz families --
    # boundary hugging (pin against a wall) — spam each action, distinct family tag
    for a in actions:
        add("breaker", "boundary_hug", "boundary_hug", {"action": a})
    # alternating extremes — ordered action pairs x periods
    for i, a in enumerate(actions):
        for b in actions:
            if a == b:
                continue
            for period in alt_periods:
                add("breaker", "alternate", "alternate",
                    {"a": a, "b": b, "period": period})
    # long holds
    for i in range(fuzz_long):
        add("breaker", "long_hold", "long_hold", {"seed": seed * 911 + i})
    # random macro-action search (G3-style, more of it)
    for i in range(fuzz_random):
        add("breaker", "random_macro", "random_macro", {"seed": seed * 7 + i})

    return specs


def _run_tier0(executor, game_source, engine, actions, report, *,
               seed, horizon, fuzz_random, fuzz_long, noop_heavy,
               alt_periods, anti_variants, controlled, initial):
    witness = _witness(report)
    witness_actions = list(witness.get("actions", [])) if witness else []
    witness_ticks = witness.get("ticks") if witness else None

    specs = _tier0_specs(actions, witness_actions, horizon, seed,
                         fuzz_random=fuzz_random, fuzz_long=fuzz_long,
                         noop_heavy=noop_heavy, alt_periods=alt_periods,
                         anti_variants=anti_variants)

    # Expand every spec (drop the rare un-expandable one, e.g. anti_witness with
    # no witness — already guarded, but stay defensive).
    plans, kept = [], []
    for spec in specs:
        try:
            plan = _expand(spec["pattern"], spec["params"], actions, horizon,
                           witness_actions=witness_actions)
        except _InvalidPlan:
            continue
        plans.append(plan)
        kept.append((spec, plan))

    episodes = _replay_all(executor, game_source, plans, horizon)

    findings = []
    single_flags = []
    shortcuts = []
    counts = {"nan": 0, "escape": 0, "stuck": 0, "unintended_success": 0,
              "shortcut_beats_witness": 0, "single_action_win": 0, "intended_success": 0}
    families: dict = {}

    for (spec, plan), ep in zip(kept, episodes):
        fam = spec["family"]
        families.setdefault(fam, {"episodes": 0, "findings": 0})
        families[fam]["episodes"] += 1
        avoidance = spec["group"] == "avoidance"
        outcome, ev = classify(ep, engine, avoidance=avoidance,
                               witness_ticks=witness_ticks, controlled=controlled,
                               initial_snapshot=initial)

        # Single-action-win is a separate lens on the spam family (a win at all is
        # a flag; the tick count is the payload the >=5-tick bar can miss).
        if spec["group"] == "single_action" and ev["result"] == "success":
            counts["single_action_win"] += 1
            action = spec["params"]["action"]
            flag = {"outcome": "single_action_win", "tier": 0, "family": "single_action",
                    "hard": False, "action": action, "ticks": ev["ticks"],
                    "detail": f"action {action!r} alone wins in {ev['ticks']} decision ticks",
                    "reproducer": _reproducer(engine, "spam", {"action": action}, plan),
                    "evidence": ev}
            single_flags.append(flag)
            findings.append(flag)
            families[fam]["findings"] += 1

        if outcome in ("nothing", "intended_success"):
            if outcome == "intended_success":
                counts["intended_success"] += 1
            continue

        counts[outcome] = counts.get(outcome, 0) + 1
        families[fam]["findings"] += 1
        finding = {
            "outcome": outcome,
            "tier": 0,
            "family": fam,
            "hard": outcome in _HARD_OUTCOMES,
            "detail": _tier0_detail(outcome, spec, ev, witness_ticks),
            "reproducer": _reproducer(engine, spec["pattern"], spec["params"], plan),
            "evidence": ev,
        }
        findings.append(finding)
        if outcome == "shortcut_beats_witness":
            shortcuts.append(finding)

    hard = any(f["hard"] for f in findings)
    block = {
        "passed": not hard,
        "episodes": len(kept),
        "counts": counts,
        "families": families,
        "avoidance": {
            "episodes": sum(1 for s, _ in kept if s["group"] == "avoidance"),
            "unintended_success": counts["unintended_success"],
            "passed": counts["unintended_success"] == 0,
        },
        "single_action_win": {
            "episodes": len(actions),
            "flags": single_flags,
            "passed": len(single_flags) == 0,
        },
        "breaker": {
            "episodes": sum(1 for s, _ in kept if s["group"] == "breaker"),
            "nan": counts["nan"], "escape": counts["escape"], "stuck": counts["stuck"],
            "passed": counts["nan"] == 0 and counts["escape"] == 0,
        },
        "shortcut": {
            "factor": SHORTCUT_FACTOR,
            "witness_ticks": witness_ticks,
            "shortcuts": shortcuts,
        },
        "findings": findings,
    }
    return block


def _tier0_detail(outcome, spec, ev, witness_ticks):
    fam = spec["family"]
    if outcome == "unintended_success":
        return (f"avoidance probe '{fam}' still won at tick {ev['ticks']} — "
                f"the goal is reachable without playing (degenerate/unavoidable)")
    if outcome == "shortcut_beats_witness":
        return (f"fuzz family '{fam}' won in {ev['ticks']} ticks "
                f"(witness {witness_ticks}) — the intended path is bypassable")
    if outcome == "nan":
        return f"fuzz family '{fam}' triggered a NaN/explosion during replay"
    if outcome == "escape":
        return (f"fuzz family '{fam}' drove {', '.join(ev['escape'])} beyond "
                f"world+{int(ESCAPE_MARGIN)}px (escape)")
    if outcome == "stuck":
        return (f"fuzz family '{fam}' soft-locked the controlled body "
                f"(travelled then immobile, no latch for {STUCK_WINDOW}+ ticks)")
    return f"{outcome} ({fam})"


# ======================================================================== #
# TIER 1 — cheap-LLM attack proposers (OpenRouter lane)
# ======================================================================== #
def _attacker_complete(system, messages, model):
    """One attacker completion -> raw text. THE network seam (tests monkeypatch
    this). Reuses gamegen's OpenRouter plumbing (retry/backoff/redaction) WITHOUT
    touching gamegen; raises gamegen._BackendUnavailable on any trouble."""
    from harness.gen import gamegen as gg
    if gg.requests is None:
        raise gg._BackendUnavailable("requests package not installed")
    key = gg._resolve_secret("OPENROUTER_API_KEY")
    if not key:
        raise gg._BackendUnavailable("OpenRouter API key not configured")
    cap = gg._reasoning_cap()
    resp = gg._openrouter_request(key, model, system, messages, cap)
    content = gg._openrouter_content(resp)
    if content is None:
        raise gg._BackendUnavailable("empty attacker completion")
    return content


def _attacker_models(models):
    """Resolve the attacker lane models: explicit arg > OPENROUTER_ATTACKER_MODELS
    (comma-separated) > OPENROUTER_MODEL (single lane) > []."""
    if models:
        return list(models)
    from harness.gen import gamegen as gg
    raw = gg._resolve_secret("OPENROUTER_ATTACKER_MODELS")
    if raw:
        got = [m.strip() for m in str(raw).split(",") if m.strip()]
        if got:
            return got
    single = gg._resolve_secret("OPENROUTER_MODEL")
    return [single] if single else []


def _have_key():
    from harness.gen import gamegen as gg
    return gg.requests is not None and bool(gg._resolve_secret("OPENROUTER_API_KEY"))


def _short_model(model):
    return str(model).split("/")[-1].split(":")[0]


def _vocab_text():
    return "\n".join(f"  - {name}: {desc}" for name, desc in STRATEGY_VOCAB.items())


def _tier1_system():
    return (
        "You are a red-team ATTACKER probing a certified 2D physics game. You NEVER "
        "write code. You output ONLY pure data: a JSON array of attack proposals. "
        "Each proposal targets a way to break the game: winning without playing, "
        "winning far faster than the intended path, or crashing the physics "
        "(NaN / a body escaping the world). Every attack is validated by mechanical "
        "replay, so a wrong guess is free — propose diverse, aggressive attacks.")


def _tier1_user(game_source, report, actions, k):
    witness = _witness(report) or {}
    brief = {
        "actions": list(actions),
        "witness_ticks": witness.get("ticks"),
        "checkpoints": list((witness.get("checkpoints") or {}).keys()),
    }
    return (
        "GAME SOURCE:\n```\n" + game_source.strip() + "\n```\n\n"
        "CERTIFIED REPORT (brief):\n" + json.dumps(brief, ensure_ascii=False) + "\n\n"
        "STRATEGY VOCABULARY (pick a pattern name + params, or an explicit sequence):\n"
        + _vocab_text() + "\n\n"
        f"Return a JSON array of exactly {k} proposals. Each item:\n"
        '  {"hypothesis": "<one line>", '
        '"action_plan": {"pattern": "<name>", "params": {...}}}\n'
        'or {"hypothesis": "...", "action_plan": {"kind": "sequence", '
        '"sequence": ["<action>", ...]}}.\n'
        "Only use action tokens from the game's ACTIONS. Output the JSON array ONLY.")


def _parse_proposals(text):
    """Extract the JSON array of proposals from raw attacker text. Tolerant of a
    code fence or leading prose; returns [] if nothing parses."""
    if not isinstance(text, str):
        return []
    candidate = text.strip()
    # Strip a ```json ... ``` fence if present.
    if "```" in candidate:
        parts = candidate.split("```")
        for part in parts:
            p = part.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("[") or p.startswith("{"):
                candidate = p
                break
    start = candidate.find("[")
    end = candidate.rfind("]")
    if start >= 0 and end > start:
        try:
            data = json.loads(candidate[start:end + 1])
            if isinstance(data, list):
                return data
        except ValueError:
            pass
    # Fall back: a single object.
    try:
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            return [obj]
        if isinstance(obj, list):
            return obj
    except ValueError:
        pass
    return []


def _proposal_plan(proposal, actions, horizon):
    """(pattern, params, plan) from one raw proposal. Raises _InvalidPlan if the
    proposal is malformed / out-of-vocabulary (-> incomprehension)."""
    if not isinstance(proposal, dict):
        raise _InvalidPlan("proposal is not an object")
    ap = proposal.get("action_plan", proposal)
    if not isinstance(ap, dict):
        raise _InvalidPlan("action_plan is not an object")
    if ap.get("kind") == "sequence" or "sequence" in ap:
        params = {"sequence": ap.get("sequence")}
        plan = _expand("sequence", params, actions, horizon)
        return "sequence", params, plan
    pattern = ap.get("pattern")
    if not pattern or pattern not in STRATEGY_VOCAB or pattern == "sequence":
        raise _InvalidPlan(f"unknown/invalid pattern {pattern!r}")
    params = ap.get("params") or {}
    plan = _expand(pattern, params, actions, horizon)
    return pattern, params, plan


def _run_tier1(executor, game_source, engine, actions, report, *,
               k, models, controlled, initial, horizon, requested):
    block = {"status": "skipped_not_requested", "reason": "", "models": [],
             "attackers": [], "records": [], "findings": [], "episodes": 0,
             "passed": True}
    if not requested:
        block["reason"] = "tier 1 not requested"
        return block

    if not _have_key():
        block["status"] = "skipped_no_key"
        block["reason"] = ("OpenRouter unavailable (no OPENROUTER_API_KEY / requests) "
                           "— tier 1 skipped, tier 0 stands alone")
        return block

    lanes = _attacker_models(models)
    if not lanes:
        block["status"] = "skipped_no_key"
        block["reason"] = ("no attacker model configured "
                           "(OPENROUTER_ATTACKER_MODELS / OPENROUTER_MODEL)")
        return block

    block["status"] = "run"
    block["models"] = list(lanes)
    witness = _witness(report) or {}
    witness_ticks = witness.get("ticks")
    system = _tier1_system()
    user = _tier1_user(game_source, report, actions, k)

    all_findings = []
    for lane, model in enumerate(lanes):
        attacker_id = f"{_short_model(model)}#lane{lane}"
        stat = {"attacker_id": attacker_id, "model": model, "attempts": 0,
                "findings": 0, "incomprehension": 0, "misconception": 0}

        try:
            text = _attacker_complete(system, [{"role": "user", "content": user}], model)
        except Exception as exc:  # noqa: BLE001 - a dead lane must not sink the pass
            stat["error"] = f"{type(exc).__name__}: {exc}"
            block["attackers"].append(stat)
            continue

        proposals = _parse_proposals(text)[:k]
        # Validate + expand all replayable proposals, then batch-replay them.
        prepared = []
        for proposal in proposals:
            stat["attempts"] += 1
            record = {"schema": "attack_record/v1", "attacker_id": attacker_id,
                      "model": model,
                      "hypothesis": (proposal.get("hypothesis")
                                     if isinstance(proposal, dict) else None),
                      "proposal": proposal}
            try:
                pattern, params, plan = _proposal_plan(proposal, actions, horizon)
            except _InvalidPlan as exc:
                record["outcome"] = "incomprehension"
                record["failure_class"] = "incomprehension"
                record["reason"] = str(exc)
                stat["incomprehension"] += 1
                block["records"].append(record)
                continue
            record["_pattern"] = pattern
            record["_params"] = params
            prepared.append((record, pattern, params, plan))

        if prepared:
            episodes = _replay_all(executor, game_source,
                                   [p for _, _, _, p in prepared], horizon)
        else:
            episodes = []

        for (record, pattern, params, plan), ep in zip(prepared, episodes):
            outcome, ev = classify(ep, engine, avoidance=False,
                                   witness_ticks=witness_ticks, controlled=controlled,
                                   initial_snapshot=initial)
            record.pop("_pattern", None)
            record.pop("_params", None)
            record["evidence"] = ev
            record["reproducer"] = _reproducer(engine, pattern, params, plan)
            if outcome in ("nothing", "intended_success"):
                record["outcome"] = outcome
                record["failure_class"] = "misconception"
                stat["misconception"] += 1
            else:
                record["outcome"] = outcome
                record["failure_class"] = "hit"
                stat["findings"] += 1
                finding = {"outcome": outcome, "tier": 1, "family": pattern,
                           "hard": outcome in _HARD_OUTCOMES,
                           "attacker_id": attacker_id,
                           "detail": f"attacker {attacker_id} broke the game via "
                                     f"{pattern} -> {outcome}",
                           "reproducer": record["reproducer"], "evidence": ev}
                all_findings.append(finding)
            block["records"].append(record)

        block["attackers"].append(stat)

    block["findings"] = all_findings
    block["episodes"] = len(block["records"])
    block["passed"] = not any(f["hard"] for f in all_findings)
    return block


# ======================================================================== #
# Grade + public entry points
# ======================================================================== #
def _grade(findings, tier1_block, tiers):
    hard = any(f["hard"] for f in findings)
    if hard:
        return "open"
    tier1_clean = (1 in tiers and tier1_block.get("status") == "run"
                   and not tier1_block.get("findings"))
    if not findings and tier1_clean:
        return "bulletproof"
    return "hardened"


def run_g4(game_source, report, *, engine=None, slug="game", tiers=(0,),
           world_factory=None, seed=0, horizon=PROBE_HORIZON,
           fuzz_random=DEFAULT_FUZZ_RANDOM, fuzz_long=DEFAULT_FUZZ_LONG,
           noop_heavy=DEFAULT_NOOP_HEAVY, alt_periods=DEFAULT_ALT_PERIODS,
           anti_variants=DEFAULT_ANTI_VARIANTS,
           k=DEFAULT_ATTACKS_PER_CALL, models=None):
    """Run the adversarial suite on a certified game (source + its G0-G3 report).

    Returns the g4 report block (a machine-readable dict, schema `g4_report/v1`).
    Deterministic under `seed`: same inputs -> identical findings. `tiers` selects
    which tiers to run (0 always runs; include 1 for the LLM lane). Tier 1 degrades
    gracefully when no OpenRouter key is configured.
    """
    tiers = tuple(sorted(set(tiers) | {0}))   # tier 0 always runs (free, seeds facts)
    engine = engine or "py"
    game = load_game(game_source) if engine == "py" else None
    actions = list(getattr(game, "actions", None)
                   or _actions_from_report(report) or [])

    out = {
        "schema": SCHEMA, "game": slug, "engine": engine,
        "tiers_run": list(tiers), "seed": seed,
        "witness_ticks": (_witness(report) or {}).get("ticks"),
        "actions": actions,
    }

    if not actions:
        out.update({"grade": "error", "passed": False,
                    "error": "no ACTIONS available to attack", "findings": []})
        return out

    executor = _make_executor(engine, world_factory)
    controlled = _derive_controlled(report, game_source, engine, world_factory)
    initial = _initial_snapshot(executor, game_source)

    try:
        tier0 = _run_tier0(executor, game_source, engine, actions, report,
                           seed=seed, horizon=horizon, fuzz_random=fuzz_random,
                           fuzz_long=fuzz_long, noop_heavy=noop_heavy,
                           alt_periods=alt_periods, anti_variants=anti_variants,
                           controlled=controlled, initial=initial)
    except VerifyError as exc:
        out.update({"grade": "error", "passed": False,
                    "error": f"engine failure during tier 0: {exc}", "findings": []})
        return out

    tier1 = _run_tier1(executor, game_source, engine, actions, report,
                       k=k, models=models, controlled=controlled, initial=initial,
                       horizon=horizon, requested=1 in tiers)

    findings = list(tier0["findings"]) + list(tier1["findings"])
    grade = _grade(findings, tier1, tiers)
    out.update({
        "grade": grade,
        "passed": grade != "open",
        "tier0": tier0,
        "tier1": tier1,
        "findings": findings,
        "hard_findings": [f for f in findings if f["hard"]],
    })
    return out


def _actions_from_report(report):
    """Best-effort ACTIONS recovery from a report (used for the JS engine, where
    we do not load the module in-process)."""
    try:
        return list(report["actions"])
    except (KeyError, TypeError):
        return None


def attack_game(game_path, *, tiers=(0,), sandboxed=True, world_factory=None,
                seed=0, k=DEFAULT_ATTACKS_PER_CALL, models=None, **fuzz_kwargs):
    """Verify (to obtain the certified report + witness) then attack a game file.

    Only a game that PASSES the G0-G3 funnel is attacked (G4 rides after G3). An
    uncertified game returns a report with grade "uncertified" and the funnel hint.
    """
    from harness.verify.gameverify import verify_game

    try:
        with open(game_path, "r", encoding="utf-8") as fh:
            source = fh.read()
    except OSError as exc:
        return {"schema": SCHEMA, "grade": "error", "passed": False,
                "error": f"game unreadable: {exc}", "findings": []}

    engine = detect_engine(game_path, source)
    report = verify_game(game_path, sandboxed=sandboxed, world_factory=world_factory)

    if not isinstance(report, dict) or not report.get("passed"):
        return {"schema": SCHEMA, "game": _slug_of(game_path), "engine": engine,
                "grade": "uncertified", "passed": False,
                "error": "game did not pass the G0-G3 funnel; G4 attacks certified "
                         "games only",
                "funnel_hint": (report or {}).get("hint") if isinstance(report, dict) else None,
                "findings": []}

    # Carry ACTIONS into the report so the JS path can recover them for run_g4.
    report = dict(report)
    report.setdefault("engine", engine)
    return run_g4(source, report, engine=engine, slug=_slug_of(game_path),
                  tiers=tiers, world_factory=world_factory, seed=seed, k=k,
                  models=models, **fuzz_kwargs)


def _slug_of(game_path):
    import os
    return os.path.splitext(os.path.basename(str(game_path)))[0]


# ======================================================================== #
# Finding -> author-repair report (reuses the existing repair contract, §4.7)
# ======================================================================== #
_REPAIR_HINTS = {
    "unintended_success": "an avoidance policy won without playing; tighten the goal "
                          "so it cannot be reached by idling or the wrong moves",
    "shortcut_beats_witness": "a fuzz policy won far faster than the intended path; "
                              "make the goal require the full progression",
    "nan": "adversarial input drove the physics to NaN/explosion; bound forces/velocities",
    "escape": "adversarial input drove a body out of the world; add bounds or damping",
    "stuck": "an adversarial player soft-locked the controlled body; avoid dead-end states",
    "single_action_win": "one repeated action alone solves the game; require real play",
}


def to_repair_report(finding):
    """Shape a G4 finding as the author-repair report gamegen._repair_user_msg
    already renders (failure_class + hint + reproducer + JSON)."""
    outcome = finding.get("outcome")
    return {
        "passed": False,
        "failure_class": "G4_FINDING",
        "outcome": outcome,
        "hint": _REPAIR_HINTS.get(outcome, finding.get("detail", "adversarial finding")),
        "g4_reproducer": finding.get("reproducer"),
        "g4_evidence": finding.get("evidence"),
    }
