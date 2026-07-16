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
from harness.verify.executors import GodotExecutor, JsExecutor, PyExecutor, VerifyError
from harness.verify.gameverify import (
    EFFICACY_EPS, _default_world_factory, detect_engine, load_game,
)
# The shared state-tree substrate — reused (never forked) for the stale-state
# tier: fingerprint()/fp_delta() power trigger 1a's cycle test and the SAME
# Go-Explore solver (treesolve) certifies/refutes softlocks (oracle 1c).
from harness.core.statetree import (
    EXHAUSTED, TERMINAL_STUCK, fingerprint, fp_delta,
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

# Stale-state tier (softlock oracle) defaults ([eng.]).
STALE_H = 60               # exploration horizon BEYOND the suspect prefix P (len(P)+H)
STALE_TOP_M = 8            # cap on suspect prefixes escalated to the 1c oracle
STALE_CAND_BUDGET = 6000   # tick budget for the inverted-objective candidate search

# Inverse-value tier (the model-steered smart search) defaults ([eng.]).
IV_SEEDS = 12              # parallel anti-policy rollout seeds
IV_EPS = 0.1              # uniform-random exploration fraction per steered tick
IV_WINDOW = 6             # DETECT sliding-window length (N in 5..10)
IV_MAX_TICKS = PROBE_HORIZON  # per-rollout decision-tick cap
# The oracle budget defaults to treesolve.TICK_BUDGET (one full G3 solve) — read at
# call time so a test can shrink it; see _stale_oracle_budget().

# Policy-guided descent tier (S1.5, harness/rl/adversary.descent_search) defaults ([eng.]).
# Elias's return-then-descend: navigate the working policy to a low-V waypoint, THEN
# alpha-ramp into the freeze pocket. CHEAP (no training) so it runs in the standard smart
# path, slotted BETWEEN the greedy inverse-value tier (S1) and the deep seeker (S2). Same
# model gate as S1. Design: notes/adversarial/STALE_SEEKING_PLAN.md §3.1.
DESCENT_WAYPOINTS = 6      # low-V waypoints selected per descent search
DESCENT_TICKS = 30        # descent-phase length; alpha ramps 0->1 over this

# Deep seeker tier (the TRAINED stale-seeker, harness/rl/stale_seek.py) defaults ([eng.]).
# This tier costs ONE PPO training per game, so it is OFF unless the caller asks for the
# DEEP grade (`deep=True`) AND the cheap tiers above certified NO softlock — it is a
# last-resort escalation, never on the hot path. gdscript lane only (GodotBatchVecEnv is
# the batched serve host); a game_path is required (the seeker spawns a Godot serve).
SEEKER_BUDGET = 20000      # env-steps of PPO adversary training (the seeker's tick cost)
SEEKER_NUM_ENVS = 4        # batched in-scene instances (N-in-one-proc at speedup 8)
SEEKER_SEEDS = (0,)        # harvest seeds (seed 0 == WORLD_SEED, what CONFIRM replays at)
SEEKER_WAYPOINTS = (0,)    # witness-trajectory prefix cuts to seed harvest rollouts from
SEEKER_TOP_M = 8           # cap on seeker candidates escalated to the CONFIRM oracle

# Which outcomes are HARD (route-to-repair) vs SOFT (warning/flag). `softlock` is a
# 1c-certified prefix (design §4 grading); the heuristic `stuck` stays SOFT.
# `broken_gating` is a win that reaches success WITHOUT latching a declared checkpoint
# that is supposed to gate it (the gate is bypassable) -> HARD (Elias directive 4);
# a plain `shortcut_beats_witness` that still passes every gate stays SOFT/informational.
_HARD_OUTCOMES = {"unintended_success", "nan", "escape", "softlock", "broken_gating"}


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
    "stale_seek": "steer a Go-Explore search toward stale/cycling dead-ends "
                  "(inverted-objective frontier) and refute reachability there. "
                  "ORACLE-DRIVEN: the stale tier drives it, not a per-tick expander.",
}

# Registry entries that are NOT stateless per-tick expanders: they name a search
# STRATEGY the harness drives (the softlock oracle steers `stale_seek` via the
# inverted-objective frontier). Excluded from the Tier-1 attacker menu — an
# attacker cannot emit a flat plan for them; _expand rejects them as it should.
_ORACLE_STRATEGIES = {"stale_seek"}


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


def classify(ep, engine, *, avoidance, witness_ticks, controlled, initial_snapshot,
             required_checkpoints=None):
    """Map a replayed episode dict to (outcome, evidence).

    Outcome vocabulary (findings marked ✓; `nothing`/`intended_success` are not):
      unintended_success ✓  success under an avoidance plan
      nan                ✓  NaN/explosion during replay
      escape             ✓  a dynamic body left world+ESCAPE_MARGIN
      broken_gating      ✓  success reached WITHOUT latching a declared checkpoint
                            that should gate it (the gate is bypassable) — HARD
      shortcut_beats_witness ✓  success far faster than the certified witness, but
                            every declared gate still latched — SOFT/informational
      stuck              ✓  the controlled body travelled then soft-locked
      intended_success      a normal, non-shortcut win (not a finding)
      nothing               the attack failed to break the game

    `required_checkpoints` is the declared milestone set that SHOULD gate success
    (the witness's checkpoint keys). A non-avoidance win that latches all of them
    but is merely fast is an informational shortcut; one that reaches success while
    a required gate stays unlatched is `broken_gating` (Elias directive 4).
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
        # Broken gating (HARD): a win that skips a declared/required checkpoint the
        # game intends to gate success. Distinguishes a genuine gating hole from a
        # benign shortcut (Elias directive 4) — checked BEFORE the shortcut lens so a
        # fast win that ALSO skips a gate reports the stronger (hard) finding.
        skipped = [k for k in (required_checkpoints or [])
                   if ev["checkpoints"].get(k) is None]
        if skipped:
            ev["skipped_checkpoints"] = skipped
            return "broken_gating", ev
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
    if engine == "godot":
        # Declarative-spec games attack through the SAME seam as py/js — the batch
        # executor is the only physics contact. Without this branch a .spec.json
        # would fall through to PyExecutor and mis-execute the spec as pymunk code.
        return GodotExecutor()
    if engine == "gdscript":
        # Generated .gd games drive through the serve host (serve_game.gd) — the
        # SAME run_batch(seed, actions) seam the funnel used, so the whole G4
        # machinery (fuzz families, referee, stale oracle) is engine-agnostic.
        # Without this branch a .gd would fall to PyExecutor and error out trying
        # to parse GDScript as pymunk Python ("game attack failed: invalid syntax").
        from harness.verify.gd_exec import GdExecutor
        return GdExecutor()
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
    # The declared checkpoints that SHOULD gate success (broken-gating detection).
    required_checkpoints = list((witness.get("checkpoints") or {}).keys()) if witness else []

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
              "broken_gating": 0, "shortcut_beats_witness": 0,
              "single_action_win": 0, "intended_success": 0}
    families: dict = {}

    for (spec, plan), ep in zip(kept, episodes):
        fam = spec["family"]
        families.setdefault(fam, {"episodes": 0, "findings": 0})
        families[fam]["episodes"] += 1
        avoidance = spec["group"] == "avoidance"
        outcome, ev = classify(ep, engine, avoidance=avoidance,
                               witness_ticks=witness_ticks, controlled=controlled,
                               initial_snapshot=initial,
                               required_checkpoints=required_checkpoints)

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
    if outcome == "broken_gating":
        skipped = ev.get("skipped_checkpoints", [])
        return (f"fuzz family '{fam}' won at tick {ev['ticks']} WITHOUT latching "
                f"required checkpoint(s) {', '.join(skipped)} — success is reachable "
                f"while skipping a declared gate (broken gating)")
    if outcome == "shortcut_beats_witness":
        return (f"fuzz family '{fam}' won in {ev['ticks']} ticks "
                f"(witness {witness_ticks}) — faster than the intended path, but every "
                f"declared checkpoint still latched (informational shortcut)")
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
    # Oracle-driven strategies are not attacker-emittable flat plans -> hide them.
    return "\n".join(f"  - {name}: {desc}" for name, desc in STRATEGY_VOCAB.items()
                     if name not in _ORACLE_STRATEGIES)


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
    required_checkpoints = list((witness.get("checkpoints") or {}).keys())
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
                                   initial_snapshot=initial,
                                   required_checkpoints=required_checkpoints)
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
# STALE-STATE TIER — softlock triggers (1a/1b) + bounded tree-refutation (1c)
# Design: notes/engines/GODOT_RL_AGENTS_CAPABILITIES.md §4. Cheap high-recall
# TRIGGERS gate one expensive high-precision ORACLE; a trigger NEVER fails a game
# on its own — only a 1c-certified prefix does (a hard `softlock` finding).
# ======================================================================== #
def _frame_snapshot(frame) -> dict:
    """A frame's per-entity ``query`` dicts already carry pos/vel/angle, which is
    all :func:`statetree.fingerprint` reads — so a frame IS a snapshot for it."""
    return frame.get("entities", {}) or {}


def _last_latch(checkpoints) -> int:
    latched = [t for t in (checkpoints or {}).values() if t is not None]
    return max(latched) if latched else 0


def trigger_state_cycling(frames, checkpoints, ticks, *, window=STUCK_WINDOW,
                          eps=EFFICACY_EPS):
    """TRIGGER 1a — state-hash cycling. Fires when NO checkpoint latched for the
    last ``window`` ticks AND the statetree fingerprint trajectory closes a cycle
    (a later state within ``eps`` of an earlier one — ``fp_delta < EFFICACY_EPS``).

    Rides an already-replayed (framed) episode; legit periodic motion trips it too
    (that is intended — the oracle, not the trigger, decides softlock vs healthy).
    Returns ``(fired, info)`` where ``info['cycle_start']`` is the tick the cycle
    opens (the natural cut point for the suspect prefix P)."""
    info = {"no_recent_latch": False, "last_latch": _last_latch(checkpoints),
            "cycle": False, "cycle_start": None, "cycle_period": None}
    info["no_recent_latch"] = info["last_latch"] <= ticks - window
    if not info["no_recent_latch"] or ticks < window or not frames:
        return False, info
    # Fingerprint the no-progress tail (frames at/after the last latch) and look
    # for a recurrence — the fingerprint SET closing a cycle.
    last = info["last_latch"]
    tail = [(int(fr.get("tick", i)), fingerprint(_frame_snapshot(fr)))
            for i, fr in enumerate(frames) if int(fr.get("tick", i)) >= last]
    for i in range(len(tail)):
        for j in range(i + 1, len(tail)):
            if fp_delta(tail[i][1], tail[j][1]) < eps:
                info.update(cycle=True, cycle_start=tail[i][0],
                            cycle_period=tail[j][0] - tail[i][0])
                return True, info
    return False, info


def trigger_entity_unreachable(ep, initial_snapshot):
    """TRIGGER 1b — entity out-of-reach. Fires when a body present at the start is
    ABSENT from the final snapshot (a success-required entity destroyed/removed ->
    structurally unreachable) OR a dynamic body escaped world+ESCAPE_MARGIN (the
    existing oob machinery, ``ep['oob']``). A TRIGGER — never fails a game alone."""
    final = ep.get("final_snapshot") or {}
    missing = sorted(set(initial_snapshot or {}) - set(final))
    escaped = list(ep.get("oob", []) or [])
    fired = bool(missing or escaped)
    return fired, {"missing": missing, "escaped": escaped}


class _PrefixExecutor:
    """Executor adapter that re-roots the world AFTER a fixed prefix ``P``: every
    replay is silently ``P + actions`` and every returned episode is expressed in
    the AFTER-P frame (ticks/checkpoints shifted by ``len(P)``). This lets the SAME
    treesolve Go-Explore search explore *continuations of P* with zero solver
    changes — its ``actions:[]`` root replay becomes the post-P state (oracle 1c)."""

    def __init__(self, inner, prefix):
        self._inner = inner
        self._prefix = list(prefix)
        self.batched = getattr(inner, "batched", False)

    def run_batch(self, game_source, episodes, max_ticks, frames_every=0,
                  escape_margin=None):
        p, plen = self._prefix, len(self._prefix)
        specs = [{"seed": e.get("seed", WORLD_SEED),
                  "actions": p + list(e.get("actions", []))} for e in episodes]
        recs = self._inner.run_batch(game_source, specs, int(max_ticks) + plen,
                                     frames_every=frames_every,
                                     escape_margin=escape_margin)
        out = []
        for rec in recs:
            local = dict(rec)
            local["ticks"] = max(0, int(rec.get("ticks", 0)) - plen)
            cps = {}
            for k, t in (rec.get("checkpoints") or {}).items():
                cps[k] = None if t is None else (0 if t <= plen else t - plen)
            local["checkpoints"] = cps
            if "actions" in rec:
                local["actions"] = list(rec["actions"])[plen:]
            out.append(local)
        return out


def _stale_oracle_budget(budget):
    if budget is not None:
        return budget
    from harness.verify import treesolve as ts
    return ts.TICK_BUDGET


def refute_prefix(executor, game_source, actions, prefix, *, H=STALE_H,
                  budget=None, engine="py", seed=WORLD_SEED):
    """ORACLE 1c — bounded tree-refutation. Plant ``prefix`` (P) as a realised leaf
    in a fresh StateTree and run the SAME Go-Explore solver that certifies G3 on
    continuations of P at horizon ``len(P)+H`` under ``budget`` (default
    ``treesolve.TICK_BUDGET`` = one G3 solve).

    Verdict: budget exhausted with NO ``TERMINAL_SUCCESS`` under P -> the prefix is
    a CERTIFIED softlock witness (``certified=True``); a continuation that wins
    REFUTES it (``certified=False`` with the winning ``witness``). Subtree
    saturation to all-terminal (STUCK/EXHAUSTED) is the stronger verdict
    (``subtree_status='saturated'``). A budgeted refutation, not a proof."""
    from harness.verify import treesolve as ts
    budget = _stale_oracle_budget(budget)
    wrapped = _PrefixExecutor(executor, prefix)
    witness, episodes, replays, tree = ts._tree_search(
        wrapped, game_source, list(actions), H, budget=budget)
    frontier = tree.frontier()
    root = tree.root
    saturated = not frontier
    subtree_status = ("saturated" if saturated
                      else ("terminal_stuck" if root.status == TERMINAL_STUCK
                            else ("exhausted" if root.status == EXHAUSTED
                                  else "budget_exhausted")))
    return {
        "certified": witness is None,
        "witness": witness,
        "subtree_status": subtree_status,
        "H": H, "budget": budget, "engine": engine, "seed": seed,
        "nodes": len(tree), "replays": replays,
        "ticks_simulated": tree.ticks_simulated,
    }


def _stale_candidate_plans(executor, game_source, actions, horizon, seed,
                           cand_budget):
    """The suspect-prefix generators: deterministic coverage (spam each action;
    each ordered pair alternated) PLUS the inverted-objective tree search (req 5,
    steering fuzz toward stale regions). Returns a de-duplicated list of flat
    action plans — the raw material triggers 1a/1b then filter."""
    plans: list = []
    for a in actions:
        plans.append([a] * horizon)
    for a in actions:
        for b in actions:
            if a != b:
                plans.append(([a, b] * (horizon // 2 + 1))[:horizon])
    try:
        from harness.verify import treesolve as ts
        _, inv_eps, _, _ = ts._tree_search(
            executor, game_source, list(actions), horizon,
            select=ts._select_leaves_inverted, budget=cand_budget)
        for ep in inv_eps:
            acts = list(ep.get("actions") or [])
            if acts:
                plans.append(acts)
    except VerifyError:
        pass                               # steering is best-effort; seeds stand alone
    seen, uniq = set(), []
    for p in plans:
        key = tuple(p)
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def _run_stale(executor, game_source, engine, actions, report, *,
               controlled, initial, horizon, seed, requested,
               stale_H, stale_budget, stale_cand_budget, top_m):
    """The stale-state tier: generate stale-seeking episodes, gate them through the
    triggers, dedup suspect prefixes by fingerprint (cap top-M), and refute each
    with oracle 1c. A certified prefix -> a hard `softlock` finding."""
    block = {"status": "skipped_not_requested", "reason": "", "candidates": [],
             "triggered": 0, "certified": 0, "findings": [], "episodes": 0,
             "passed": True}
    if not requested:
        block["reason"] = "stale tier not requested"
        return block

    if not actions:
        block["status"] = "skipped"
        block["reason"] = "no ACTIONS to explore"
        return block

    block["status"] = "run"
    try:
        plans = _stale_candidate_plans(executor, game_source, actions, horizon,
                                       seed, stale_cand_budget)
        specs = [{"seed": WORLD_SEED, "actions": p} for p in plans]
        episodes = executor.run_batch(game_source, specs, horizon, frames_every=1)
    except VerifyError as exc:
        block["status"] = "error"
        block["reason"] = f"engine failure during stale candidate replay: {exc}"
        return block

    block["episodes"] = len(episodes)
    # -- Triggers: keep budget episodes that cycle (1a) or lose an entity (1b) --
    suspects: list = []
    seen_fp = set()
    for plan, ep in zip(plans, episodes):
        if ep.get("result") != "budget":
            continue
        fired_a, info_a = trigger_state_cycling(ep.get("frames", []),
                                                ep.get("checkpoints", {}),
                                                int(ep.get("ticks", 0)))
        fired_b, info_b = trigger_entity_unreachable(ep, initial)
        if not (fired_a or fired_b):
            continue
        # Cut P at the cycle opening (1a) — the moves that led INTO the stale
        # region; fall back to the last-latch boundary for a 1b-only trigger.
        cut = (info_a["cycle_start"] if fired_a and info_a["cycle_start"] is not None
               else _last_latch(ep.get("checkpoints", {})))   # 1b-only -> last progress
        cut = max(1, min(int(cut), len(ep.get("actions", plan))))
        prefix = list(ep.get("actions", plan))[:cut]
        fp = fingerprint(ep.get("final_snapshot", {}) or {})
        if fp in seen_fp:
            continue                       # dedup suspects by their end-state
        seen_fp.add(fp)
        suspects.append({"prefix": prefix, "trigger_1a": fired_a,
                         "trigger_1b": fired_b, "info_1a": info_a,
                         "info_1b": info_b, "evidence": _evidence(ep, engine),
                         # the frozen pocket the body ends in (the body is pinned from the
                         # cycle-start on, so the episode's final snapshot IS the freeze) —
                         # kept so a certified suspect carries frozen_state with NO extra run.
                         "final_ep": {"final_snapshot": ep.get("final_snapshot", {}) or {},
                                      "ticks": int(ep.get("ticks", 0) or 0),
                                      "checkpoints": ep.get("checkpoints", {}) or {}}})
        if len(suspects) >= top_m:
            break

    block["triggered"] = len(suspects)
    block["candidates"] = [{"prefix": s["prefix"], "trigger_1a": s["trigger_1a"],
                            "trigger_1b": s["trigger_1b"]} for s in suspects]

    # -- Oracle 1c: refute each suspect; a certified prefix is a softlock finding --
    findings = []
    for s in suspects:
        try:
            res = refute_prefix(executor, game_source, actions, s["prefix"],
                                H=stale_H, budget=stale_budget, engine=engine,
                                seed=seed)
        except VerifyError:
            continue                       # a dead refutation must not sink the tier
        if not res["certified"]:
            continue
        block["certified"] += 1
        prefix = s["prefix"]
        final_ep = s.get("final_ep") or {}
        last_cp = _last_latched_checkpoint(final_ep.get("checkpoints"))
        findings.append({
            "outcome": "softlock", "tier": "stale", "family": "tree_refute",
            "hard": True,
            "detail": (f"action prefix (len {len(prefix)}) soft-locks the game — the "
                       f"G3 solver found no win in {res['budget']} ticks under it "
                       f"(subtree {res['subtree_status']})"),
            "reproducer": {
                "engine": engine, "seed": seed,
                "action_plan": {"kind": "sequence", "sequence": list(prefix)},
                "provenance": {"oracle": "tree_refute", "H": res["H"],
                               "budget": res["budget"], "engine": engine,
                               "seed": seed, "subtree_status": res["subtree_status"]},
            },
            "evidence": s["evidence"],
            "frozen_state": _frozen_state(final_ep, controlled, last_cp),
        })

    _attach_enclosure(engine, game_source, findings)
    block["findings"] = findings
    block["passed"] = not findings
    return block


# ======================================================================== #
# INVERSE-VALUE TIER — the PRIMARY smart search (Elias's idea, gdscript lane).
# Design: notes/adversarial/INVERSE_VALUE_G4.md. When a trained G3' model artifact is
# available it STEERS the softlock hunt (anti-policy rollouts + V-frontier + witness-
# prefix backplay, harness.rl.adversary) AHEAD of the random fuzz — a critic-guided
# search that goes straight for the low-value/dead pockets random fuzz finds by luck.
# The candidates it surfaces are CONFIRMED by the SAME refute_prefix tree oracle (1c),
# so a certified prefix is the same hard `softlock` finding. Gated STRICTLY on a model:
# with no model artifact the tier is skipped and the ladder is byte-for-byte unchanged.
# DETECT here is critic-guided AND critic-gated: with a competent critic in hand the
# smart tiers ARM the motion-invariant value_death trigger (adversary.detect_value_death)
# alongside frozen/cycle, so a body WIGGLING in a trap (which the motion tests miss) is
# still caught; its candidates flow through the SAME CONFIRM (provenance kind="value_death").
# ======================================================================== #
def _load_iv_critic(model=None, model_path=None):
    """Resolve an inverse-value critic. ``model`` may already satisfy the critic
    contract (``action_probs``/``value`` — the test/handoff seam) or be a raw SB3
    model to wrap; ``model_path`` loads a saved SB3 ``.zip``. Returns None when neither
    is supplied. SB3/torch are imported lazily — only a real model artifact pays."""
    from harness.rl.adversary import SB3PolicyCritic
    if model is not None:
        if hasattr(model, "action_probs") and hasattr(model, "value"):
            return model                       # already a critic (injected/duck-typed)
        return SB3PolicyCritic(model)
    if model_path:
        return SB3PolicyCritic(_load_sb3_any(model_path))
    return None


def _load_sb3_any(path):
    """Load a saved SB3 model without knowing its algo up front (g3_prime saves a
    ``.zip`` whose method may be ppo/a2c/dqn). Tries each class; the matching one wins."""
    last = None
    from stable_baselines3 import A2C, DQN, PPO
    for cls in (PPO, A2C, DQN):
        try:
            return cls.load(path, device="cpu")
        except Exception as exc:               # noqa: BLE001 - wrong class -> next
            last = exc
    raise VerifyError("iv_model_load_failed", f"could not load SB3 model {path!r}: {last}")


def _iv_free_port() -> int:
    """Grab an ephemeral loopback port and release it. The inverse-value search env
    must NOT reuse the ``GdExecutor``'s serve port (both default to
    ``godot_env.DEFAULT_PORT_BASE`` + offset 0, which the executor already holds for
    the whole suite) — binding the same port raises ``port_in_use`` and sinks the
    tier. A fresh ephemeral port sidesteps the collision."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _default_iv_env_factory(game_path):
    """A 0-arg factory building the serve env the anti-policy rollout steps (gdscript
    lane). Imported lazily so the common g4 path never touches the RL env module.

    Each built env binds its OWN ephemeral port so it never collides with the CONFIRM
    executor's long-lived serve host (see ``_iv_free_port``)."""
    if not game_path:
        return None

    def _factory():
        from harness.rl.godot_env import GodotServeEnv
        return GodotServeEnv(game_path, port_base=_iv_free_port())

    return _factory


def _last_latched_checkpoint(checkpoints):
    """The checkpoint that latched LAST (max tick) on a prefix replay — the anchor the
    repair hint names ('the last milestone before the freeze')."""
    latched = [(t, k) for k, t in (checkpoints or {}).items() if t is not None]
    if not latched:
        return None
    return max(latched)[1]


# ======================================================================== #
# FROZEN-STATE ENRICHMENT (Elias directive: "give the feedback of the position and state
# of the game FROM THE GAME ENGINE ... of the softlock, to help the LLM that wrote the game
# be more aware of it"). The certified-softlock finding now carries the ENGINE-TRUTH frozen
# pocket — controlled body {name,pos,vel}, the nearest OTHER bodies, ticks, the last latched
# checkpoint, dimension — DERIVED from the ALREADY-replayed prefix episode (zero extra engine
# runs on the certify path). Best-effort ENCLOSURE names the pocket walls from the SAME G0.5
# check-op geometry (a cheap t=0 fetch, gdscript only, ONLY when a finding exists). Positions
# ride in the finding DETAIL / directive TEXT ONLY — NEVER in the dedup fingerprint (that keys
# on the defect identity; folding volatile coordinates in would break the convergence guard).
# ======================================================================== #
FROZEN_NEARBY = 5          # cap on the nearest OTHER bodies named in a frozen_state [eng.]
ENCLOSE_PAD = 24.0         # px: inflate a wall's footprint when testing "bounds the pocket" [eng.]


def _coords(v):
    """A snapshot pos/vel field -> a list of rounded floats, or None if unusable. Tolerant:
    a weird/missing field degrades to None (the certify path must never break on a snapshot)."""
    if not isinstance(v, (list, tuple)):
        return None
    try:
        out = [round(float(x), 2) for x in v]
    except (TypeError, ValueError):
        return None
    return out or None


def _frozen_state(prefix_ep, controlled, last_cp, *, n_nearby=FROZEN_NEARBY):
    """The ENGINE-TRUTH frozen pocket, read from an ALREADY-replayed episode's final
    snapshot (NO new engine run). Compact + JSON-safe: the controlled body's frozen
    {name,pos,vel}, the <=N nearest OTHER bodies by name+pos+dist, ticks elapsed, the last
    latched checkpoint, and the world dimension. ``enclosing`` starts empty and is filled
    best-effort by :func:`_attach_enclosure`. Every field is best-effort — a missing body /
    field degrades to None, never an error."""
    snap = prefix_ep.get("final_snapshot") or {}
    ticks = int(prefix_ep.get("ticks", 0) or 0)
    ctrl_body = snap.get(controlled) or {}
    pos = _coords(ctrl_body.get("pos"))
    vel = _coords(ctrl_body.get("vel"))
    others = []
    for name, body in snap.items():
        if name == controlled or not isinstance(body, dict):
            continue
        opos = _coords(body.get("pos"))
        if opos is None:
            continue
        dist = _displacement(pos, opos) if pos else None
        others.append((dist if dist is not None else float("inf"), name, opos))
    others.sort(key=lambda t: t[0])
    nearby = [{"name": n, "pos": p,
               "dist": (round(d, 2) if d != float("inf") else None)}
              for d, n, p in others[:max(0, int(n_nearby))]]
    return {
        "controlled": {"name": controlled, "pos": pos, "vel": vel},
        "nearby": nearby,
        "ticks_elapsed": ticks,
        "last_latched_checkpoint": last_cp,
        "dimension": (len(pos) if pos else None),
        "enclosing": [],
    }


def _geometry_facts(engine, game_source):
    """The serve host's t=0 GEOMETRY facts (static bodies + AABBs/half-extents) via the SAME
    G0.5 check op the reachability pre-filter uses. Best-effort + cheap: gdscript only, on a
    FRESH short-lived executor bound to its OWN ephemeral port (so it never disturbs the
    suite's long-lived CONFIRM serve host — check-after-batch on the shared handle is untested),
    ONE t=0 exchange, no physics batch. [] for other engines or on ANY failure -> enclosure
    then omits gracefully."""
    if engine != "gdscript" or not game_source:
        return []
    try:
        from harness.verify.gd_exec import GdExecutor
        ex = GdExecutor(port_base=_iv_free_port())
        try:
            facts = ex.run_check(game_source)
        finally:
            close = getattr(ex, "close", None)
            if callable(close):
                close()
        return list((facts or {}).get("geometry") or [])
    except Exception:  # noqa: BLE001 - enclosure is best-effort; never sink the tier
        return []


def _enclosing_facts(pos, geometry, *, pad=ENCLOSE_PAD):
    """The STATIC bodies whose (padded) footprint BOUNDS the frozen position — the pocket
    walls, named for the directive. Reuses reachability._aabb_of to read each body's
    aabb/half_extents/radius footprint. Best-effort: footprint-less bodies (bare markers),
    the controlled body, and non-static bodies are skipped; [] when nothing bounds the point."""
    if not pos:
        return []
    from harness.verify.reachability import _aabb_of
    out = []
    for b in geometry or []:
        if not isinstance(b, dict) or not b.get("static") or b.get("controlled"):
            continue
        bpos = _coords(b.get("pos"))
        if bpos is None:
            continue
        aabb = _aabb_of(b, bpos)
        if aabb is None:
            continue
        mn, mx = aabb
        dims = min(len(pos), len(mn), len(mx))
        if dims and all((mn[i] - pad) <= pos[i] <= (mx[i] + pad) for i in range(dims)):
            out.append({"name": b.get("name") or "",
                        "aabb": [[round(float(x), 2) for x in mn],
                                 [round(float(x), 2) for x in mx]]})
    return out


def _attach_enclosure(engine, game_source, findings):
    """Best-effort ADD: name the pocket walls (enclosing static bodies) on each finding's
    frozen_state, from the G0.5 check-op geometry. ONE cheap t=0 fetch, only when there is a
    finding to enrich; a graceful no-op (enclosing stays []) when geometry is unavailable."""
    if not findings:
        return
    geometry = _geometry_facts(engine, game_source)
    if not geometry:
        return
    for f in findings:
        fs = f.get("frozen_state")
        if fs and fs.get("controlled"):
            fs["enclosing"] = _enclosing_facts(fs["controlled"].get("pos"), geometry)


def _enrich_certified_softlocks(executor, game_source, findings, controlled, engine, *,
                                seed_default=WORLD_SEED):
    """Attach a frozen_state to certified-softlock findings that LACK one (the deep-seeker
    path builds findings in ``stale_seek.confirm_candidates`` without a snapshot). ONE bounded
    replay per finding (len(prefix) ticks) — acceptable on the DEEP lane, where a single PPO
    training already dominates cost. Then names the pocket walls (best-effort enclosure)."""
    for f in findings:
        if f.get("frozen_state"):
            continue
        repro = f.get("reproducer") or {}
        prefix = list((repro.get("action_plan") or {}).get("sequence") or [])
        seed = int(repro.get("seed", seed_default))
        try:
            ep = executor.run_batch(game_source, [{"seed": seed, "actions": prefix}],
                                    len(prefix), escape_margin=ESCAPE_MARGIN)[0]
        except VerifyError:
            ep = {"result": "budget", "ticks": len(prefix), "checkpoints": {}}
        last_cp = _last_latched_checkpoint(ep.get("checkpoints"))
        f["frozen_state"] = _frozen_state(ep, controlled, last_cp)
    _attach_enclosure(engine, game_source, findings)


def _run_inverse_value(executor, game_source, engine, actions, report, *,
                       requested, controlled=None, game_path=None, model=None,
                       model_path=None, critic=None, candidates=None, env_factory=None,
                       horizon, seed, stale_H, stale_budget, top_m, window,
                       iv_seeds, iv_eps, iv_max_ticks):
    """The inverse-value smart tier: SEARCH+DETECT (harness.rl.adversary) surfaces
    softlock candidates, CONFIRM (refute_prefix) certifies them. A certified prefix ->
    a hard `softlock` finding tagged ``inverse_value+tree_refute`` with a repair hint
    naming the last latched checkpoint before the freeze.

    Runs ONLY when a model artifact (or an injected critic/candidates) is available;
    otherwise it is skipped and contributes nothing (ladder unchanged)."""
    block = {"status": "skipped_no_model", "reason": "", "candidates": [],
             "detected": 0, "certified": 0, "findings": [], "passed": True,
             "critic_source": None}
    if not requested:
        block["reason"] = "no trained model artifact (inverse-value tier is model-gated)"
        return block
    if not actions:
        block["status"] = "skipped"
        block["reason"] = "no ACTIONS to steer"
        return block

    from harness.rl import adversary

    block["status"] = "run"
    witness = _witness(report) or {}
    witness_actions = list(witness.get("actions") or [])
    source = "injected"

    # --- SEARCH + DETECT (skip when candidates are injected for a py/unit run) ---
    if candidates is None:
        critic = critic if critic is not None else _load_iv_critic(model, model_path)
        if critic is None:
            block["status"] = "skipped_no_model"
            block["reason"] = "model/critic could not be resolved"
            return block
        factory = env_factory or _default_iv_env_factory(game_path)
        if factory is None:
            block["status"] = "skipped"
            block["reason"] = ("inverse-value search needs a steppable env "
                               "(gdscript game_path or an injected env_factory)")
            return block
        env = factory()
        try:
            res = adversary.search(
                env, critic, seeds=list(range(iv_seeds)), eps=iv_eps, window=window,
                witness_actions=witness_actions, max_ticks=iv_max_ticks,
                value_death=True)     # a competent critic is in hand -> arm value-death DETECT
        finally:
            close = getattr(env, "close", None)
            if callable(close):
                close()
        cand_list = res["candidates"]
        source = res["source"]
    else:
        # Injected candidate prefixes (test/handoff seam) — still confirmed by 1c.
        cand_list = [c if isinstance(c, dict) else {"prefix": list(c),
                     "provenance": {"source": "injected"}, "value": None}
                     for c in candidates]

    block["critic_source"] = source
    block["detected"] = len(cand_list)
    block["candidates"] = [{"prefix": list(c["prefix"]),
                            "value": c.get("value"),
                            "provenance": c.get("provenance", {})} for c in cand_list]

    # --- CONFIRM: refute each candidate (lowest-V first, capped top-M) ---
    findings = []
    for cand in cand_list[:top_m]:
        prefix = list(cand["prefix"])
        if not prefix:
            continue
        try:
            res = refute_prefix(executor, game_source, actions, prefix,
                                H=stale_H, budget=stale_budget, engine=engine, seed=seed)
        except VerifyError:
            continue                           # a dead refutation must not sink the tier
        if not res["certified"]:
            continue
        # Replay the prefix once for its own evidence + the last-latched checkpoint the
        # repair hint names (the milestone reached just before the freeze).
        try:
            prefix_ep = executor.run_batch(
                game_source, [{"seed": seed, "actions": prefix}], len(prefix),
                escape_margin=ESCAPE_MARGIN)[0]
        except VerifyError:
            prefix_ep = {"result": "budget", "ticks": len(prefix), "checkpoints": {}}
        last_cp = _last_latched_checkpoint(prefix_ep.get("checkpoints"))
        block["certified"] += 1
        prov = dict(cand.get("provenance") or {})
        prov.update({"oracle": "inverse_value+tree_refute", "critic_source": source,
                     "H": res["H"], "budget": res["budget"], "engine": engine,
                     "seed": seed, "subtree_status": res["subtree_status"],
                     "last_checkpoint": last_cp})
        findings.append({
            "outcome": "softlock", "tier": "inverse_value",
            "family": "inverse_value+tree_refute", "hard": True,
            "detail": (f"the inverse-value attacker steered the game into a frozen "
                       f"pocket (len {len(prefix)} prefix); the G3 solver found no win "
                       f"in {res['budget']} ticks under it (subtree "
                       f"{res['subtree_status']})"),
            "repair_hint": (
                f"an inverse-value (anti-policy) attack soft-locks the game after the "
                f"'{last_cp}' checkpoint — from that pocket no continuation can win. "
                f"Ensure every reachable state past '{last_cp}' can still reach the "
                f"goal, or add an escape/reset from the dead end."
                if last_cp else
                "an inverse-value (anti-policy) attack soft-locks the game before any "
                "checkpoint latches — the early game has an inescapable pocket; add an "
                "escape/reset or make the dead-end region unreachable."),
            "reproducer": {
                "engine": engine, "seed": seed,
                "action_plan": {"kind": "sequence", "sequence": list(prefix)},
                "provenance": prov,
            },
            "evidence": _evidence(prefix_ep, engine),
            "frozen_state": _frozen_state(prefix_ep, controlled, last_cp),
        })

    _attach_enclosure(engine, game_source, findings)
    block["findings"] = findings
    block["passed"] = not findings
    return block


# ======================================================================== #
# S1.5 — POLICY-GUIDED DESCENT TIER (Elias's return-then-descend, gdscript lane).
# Design: notes/adversarial/STALE_SEEKING_PLAN.md §3.1 (BUILD-FIRST). Slots in the
# ladder BETWEEN the greedy inverse-value tier (S1) and the deep trained seeker (S2):
# use the COMPETENT working policy to NAVIGATE into a low-V basin, THEN alpha-ramp to
# the freeze-seeking anti-policy. CHEAP (policy rollouts only, no training) so it runs
# in the standard smart path, not only deep=True. SEARCH+DETECT = adversary.descent_search;
# CONFIRM = the SAME refute_prefix oracle (soundness unchanged). Model-gated like S1:
# with no model artifact / injected critic the tier is a no-op and the ladder is
# byte-for-byte unchanged.
# ======================================================================== #
def _run_descent(executor, game_source, engine, actions, report, *,
                 requested, controlled=None, game_path=None, model=None, model_path=None,
                 critic=None, candidates=None, env_factory=None, horizon,
                 seed, stale_H, stale_budget, top_m, window,
                 n_waypoints, descent_ticks, eps):
    """The policy-guided descent tier: SEARCH+DETECT (adversary.descent_search) surfaces
    softlock candidates by navigating the working policy to a low-V waypoint then alpha-
    ramping into the freeze pocket; CONFIRM (refute_prefix) certifies them. A certified
    prefix -> a hard `softlock` finding tagged ``policy_descent+tree_refute`` with a
    repair hint naming the last latched checkpoint before the freeze.

    Runs ONLY when a model artifact (or an injected critic/candidates/env_factory) is
    available; otherwise skipped (contributes nothing — ladder unchanged)."""
    block = {"status": "skipped_no_model", "reason": "", "candidates": [],
             "waypoints": [], "detected": 0, "certified": 0, "findings": [],
             "passed": True, "critic_source": None}
    if not requested:
        block["reason"] = "no trained model artifact (descent tier is model-gated)"
        return block
    if not actions:
        block["status"] = "skipped"
        block["reason"] = "no ACTIONS to steer"
        return block

    from harness.rl import adversary

    block["status"] = "run"
    witness = _witness(report) or {}
    witness_actions = list(witness.get("actions") or [])
    source = "injected"

    # --- SEARCH + DETECT (skip when candidates are injected for a py/unit run) ---
    if candidates is None:
        critic = critic if critic is not None else _load_iv_critic(model, model_path)
        if critic is None:
            block["status"] = "skipped_no_model"
            block["reason"] = "model/critic could not be resolved"
            return block
        factory = env_factory or _default_iv_env_factory(game_path)
        if factory is None:
            block["status"] = "skipped"
            block["reason"] = ("descent search needs a steppable env "
                               "(gdscript game_path or an injected env_factory)")
            return block
        env = factory()
        try:
            res = adversary.descent_search(
                env, critic, witness_actions=witness_actions, eps=eps,
                n_waypoints=n_waypoints, descent_ticks=descent_ticks,
                window=window, max_ticks=horizon,
                value_death=True)     # a competent critic is in hand -> arm value-death DETECT
        finally:
            close = getattr(env, "close", None)
            if callable(close):
                close()
        cand_list = res["candidates"]
        source = res["source"]
        block["waypoints"] = res.get("waypoints", [])
    else:
        cand_list = [c if isinstance(c, dict) else {"prefix": list(c),
                     "provenance": {"source": "injected"}, "value": None}
                     for c in candidates]

    block["critic_source"] = source
    block["detected"] = len(cand_list)
    block["candidates"] = [{"prefix": list(c["prefix"]), "value": c.get("value"),
                            "provenance": c.get("provenance", {})} for c in cand_list]

    # --- CONFIRM: refute each candidate (lowest-V first, capped top-M) ---
    findings = []
    for cand in cand_list[:top_m]:
        prefix = list(cand["prefix"])
        if not prefix:
            continue
        try:
            res = refute_prefix(executor, game_source, actions, prefix,
                                H=stale_H, budget=stale_budget, engine=engine, seed=seed)
        except VerifyError:
            continue                           # a dead refutation must not sink the tier
        if not res["certified"]:
            continue
        try:
            prefix_ep = executor.run_batch(
                game_source, [{"seed": seed, "actions": prefix}], len(prefix),
                escape_margin=ESCAPE_MARGIN)[0]
        except VerifyError:
            prefix_ep = {"result": "budget", "ticks": len(prefix), "checkpoints": {}}
        last_cp = _last_latched_checkpoint(prefix_ep.get("checkpoints"))
        block["certified"] += 1
        prov = dict(cand.get("provenance") or {})
        prov.update({"oracle": "policy_descent+tree_refute", "critic_source": source,
                     "H": res["H"], "budget": res["budget"], "engine": engine,
                     "seed": seed, "subtree_status": res["subtree_status"],
                     "last_checkpoint": last_cp})
        findings.append({
            "outcome": "softlock", "tier": "descent",
            "family": "policy_descent+tree_refute", "hard": True,
            "detail": (f"policy-guided descent navigated the game into a frozen pocket "
                       f"(len {len(prefix)} prefix: a competent return to a low-value "
                       f"waypoint then an alpha-ramped anti-policy descent); the G3 solver "
                       f"found no win in {res['budget']} ticks under it (subtree "
                       f"{res['subtree_status']})"),
            "repair_hint": (
                f"a policy-guided descent attack soft-locks the game after the "
                f"'{last_cp}' checkpoint — competent navigation reaches a pocket from "
                f"which no continuation can win. Ensure every reachable state past "
                f"'{last_cp}' can still reach the goal, or add an escape/reset from the "
                f"dead end."
                if last_cp else
                "a policy-guided descent attack soft-locks the game before any checkpoint "
                "latches — competent navigation reaches an inescapable early pocket; add "
                "an escape/reset or make the dead-end region unreachable."),
            "reproducer": {
                "engine": engine, "seed": seed,
                "action_plan": {"kind": "sequence", "sequence": list(prefix)},
                "provenance": prov,
            },
            "evidence": _evidence(prefix_ep, engine),
            "frozen_state": _frozen_state(prefix_ep, controlled, last_cp),
        })

    _attach_enclosure(engine, game_source, findings)
    block["findings"] = findings
    block["passed"] = not findings
    return block


# ======================================================================== #
# DEEP SEEKER TIER — the TRAINED stale-seeker (escalation above the greedy search)
# Design: harness/rl/stale_seek.py + notes/adversarial/INVERSE_VALUE_G4.md. A PPO
# adversary LEARNS to drive the game into a softlock; its candidates flow into the
# SAME CONFIRM oracle (refute_prefix) as the cheap tiers — no new certification path.
# ONE PPO training per game, so it runs ONLY on `deep=True` AND only when the cheap
# tiers above certified nothing. gdscript lane + a game_path required.
# ======================================================================== #
def _has_hard_softlock(findings) -> bool:
    return any(f.get("outcome") == "softlock" and f.get("hard") for f in (findings or []))


def _seeker_discover_and_confirm(game_path, game_source, engine, actions, *, seed,
                                 budget, num_envs, seeds, waypoints, top_m,
                                 stale_H, stale_budget, witness, controlled=None):
    """The heavy lane, isolated behind one seam so the ladder GATE (deep flag + cheap
    tiers empty + engine/path availability) is unit-testable by monkeypatching this.

    Trains the seeker, harvests candidates (training-time + witness-waypoint greedy
    rollouts), and refutes each through the CONFIRM oracle. Returns the confirm result
    dict (``findings`` + stats). Requires Godot; costs one PPO training."""
    from harness.rl import stale_seek
    from harness.rl.godot_env import GodotServeEnv

    trained = stale_seek.train_stale_seeker(
        game_path, budget_steps=budget, num_envs=num_envs, seed=seed)
    candidates = list(trained["candidates"])

    def make_env():
        return GodotServeEnv(game_path)
    candidates += stale_seek.harvest_candidates(
        make_env, trained["policy"], seeds=seeds, witness=witness, waypoints=waypoints)

    executor = _make_executor(engine, None)
    try:
        res = stale_seek.confirm_candidates(
            executor, game_source, actions, candidates, H=stale_H, budget=stale_budget,
            engine=engine, top_m=top_m)
        # The seeker's confirm path builds findings WITHOUT a snapshot; attach the
        # engine-truth frozen_state (one bounded replay per finding — negligible next to
        # the PPO training) + the best-effort enclosure, on the SAME executor, pre-close.
        _enrich_certified_softlocks(executor, game_source, res.get("findings", []),
                                    controlled, engine)
        return res
    finally:
        close = getattr(executor, "close", None)
        if callable(close):
            close()


def _run_seeker(game_source, engine, actions, report, *, game_path, requested,
                cheap_findings, controlled=None, seed, budget, num_envs, seeds,
                waypoints, top_m, stale_H, stale_budget):
    """The deep seeker tier. Gated: runs ONLY when `requested` (deep=True) AND the cheap
    tiers certified NO softlock AND the lane can train (gdscript + a game_path). Findings
    are the CONFIRM-certified softlocks — identical shape to the cheap stale tier's."""
    block = {"status": "skipped_not_requested", "reason": "", "findings": [],
             "certified": 0, "candidates_in": 0, "passed": True}
    if not requested:
        block["reason"] = "deep seeker tier not requested (pass deep=True)"
        return block
    if _has_hard_softlock(cheap_findings):
        block["status"] = "skipped"
        block["reason"] = "a cheap tier already certified a softlock — no deep escalation needed"
        return block
    if engine != "gdscript":
        block["status"] = "skipped"
        block["reason"] = f"deep seeker trains on the gdscript batched serve lane only (engine={engine})"
        return block
    if not game_path:
        block["status"] = "skipped"
        block["reason"] = "deep seeker needs a game_path to spawn the Godot serve host"
        return block

    block["status"] = "run"
    try:
        res = _seeker_discover_and_confirm(
            game_path, game_source, engine, actions, seed=seed, budget=budget,
            num_envs=num_envs, seeds=seeds, waypoints=waypoints, top_m=top_m,
            stale_H=stale_H, stale_budget=stale_budget, witness=_witness(report),
            controlled=controlled)
    except Exception as exc:                       # training/Godot failure must not sink the suite
        block["status"] = "error"
        block["reason"] = f"deep seeker failed: {exc}"
        return block

    block.update({"findings": res["findings"], "certified": res["certified"],
                  "candidates_in": res["candidates_in"],
                  "candidates_unique": res.get("candidates_unique", 0),
                  "refuted": res.get("refuted", 0), "probed_out": res.get("probed_out", 0)})
    block["passed"] = not res["findings"]
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


# --- critic-competence gate helpers (the RAISED model-gate predicate's honest surface) --- #
def _critic_rates(g3_result):
    """(greedy_sr, stochastic_sr) read from a g3' result, tolerating the legacy key names."""
    g3 = g3_result if isinstance(g3_result, dict) else {}
    gr = g3.get("greedy_sr", g3.get("final_success_rate"))
    sr = g3.get("stochastic_sr", g3.get("stochastic_success_rate"))
    return gr, sr


def _critic_downgrade_note(g3_result) -> str:
    """The honest reason stamped on a smart block when a handed-off critic is not competent."""
    gr, sr = _critic_rates(g3_result)
    return ("critic NOT competent (demo_ready=False, "
            f"greedy_sr={gr}, stochastic_sr={sr}): the trained value map has not converged, "
            "so the model-steered smart tier was DOWNGRADED to the critic-free ladder "
            "(unconverged critic == noise; the A/B showed weak-critic == 0)")


def _critic_gate_summary(g3_result, model_armed, critic_ok, downgraded) -> dict:
    """Top-level surface of the model-gate decision (never a silent drop)."""
    gr, sr = _critic_rates(g3_result)
    has_g3 = isinstance(g3_result, dict)
    return {
        "model_armed": bool(model_armed),
        "competent": (None if g3_result is None else bool(critic_ok)),
        "downgraded": bool(downgraded),
        "demo_ready": (bool(g3_result.get("demo_ready")) if has_g3 else None),
        "greedy_sr": gr,
        "stochastic_sr": sr,
        "note": (_critic_downgrade_note(g3_result) if downgraded
                 else ("critic competent -> smart tiers armed"
                       if (model_armed and critic_ok and has_g3)
                       else "no critic handoff (g3_result absent or no model artifact)")),
    }


def run_g4(game_source, report, *, engine=None, slug="game", tiers=(0,),
           world_factory=None, seed=0, horizon=PROBE_HORIZON,
           fuzz_random=DEFAULT_FUZZ_RANDOM, fuzz_long=DEFAULT_FUZZ_LONG,
           noop_heavy=DEFAULT_NOOP_HEAVY, alt_periods=DEFAULT_ALT_PERIODS,
           anti_variants=DEFAULT_ANTI_VARIANTS,
           k=DEFAULT_ATTACKS_PER_CALL, models=None,
           stale=False, stale_H=STALE_H, stale_budget=None,
           stale_cand_budget=STALE_CAND_BUDGET, top_m=STALE_TOP_M,
           game_path=None, model=None, model_path=None, g3_result=None, iv_critic=None,
           iv_candidates=None, iv_env_factory=None, iv_seeds=IV_SEEDS,
           iv_eps=IV_EPS, iv_window=IV_WINDOW, iv_max_ticks=IV_MAX_TICKS,
           descent_critic=None, descent_candidates=None, descent_env_factory=None,
           descent_waypoints=DESCENT_WAYPOINTS, descent_ticks=DESCENT_TICKS,
           deep=False, seeker_budget=SEEKER_BUDGET,
           seeker_num_envs=SEEKER_NUM_ENVS, seeker_seeds=SEEKER_SEEDS,
           seeker_waypoints=SEEKER_WAYPOINTS, seeker_top_m=SEEKER_TOP_M):
    """Run the adversarial suite on a certified game (source + its G0-G3 report).

    Returns the g4 report block (a machine-readable dict, schema `g4_report/v1`).
    Deterministic under `seed`: same inputs -> identical findings. `tiers` selects
    which tiers to run (0 always runs; include 1 for the LLM lane). Tier 1 degrades
    gracefully when no OpenRouter key is configured.

    `stale=True` also runs the stale-state tier (softlock triggers 1a/1b + the
    bounded tree-refutation oracle 1c); a certified prefix is a hard `softlock`
    finding -> grade `open`. It rides the SAME executor + treesolve solver.

    A trained `model`/`model_path` (or the injected `iv_*`/`descent_*` seams) arms the
    two SMART tiers that LEAD the ladder: S1 the greedy inverse-value hunt (harness.rl.
    adversary.search) and S1.5 policy-guided descent (adversary.descent_search — navigate
    the working policy to a low-V waypoint, then alpha-ramp into the freeze pocket). Both
    are CHEAP (policy rollouts, no training) and both confirm through the SAME
    refute_prefix oracle. With no model the ladder is byte-for-byte unchanged.

    CRITIC-COMPETENCE GATE. A model artifact arms the smart tiers ONLY when its critic
    CONVERGED — pass the producing `g3_result` and the gate re-checks it with the shared
    `certify.critic_competent` (demo_ready-style). An UNCONVERGED critic's value map is noise
    (the A/B showed weak-critic == 0, worse than the critic-free fuzz), so it is DOWNGRADED
    to the critic-free ladder HONESTLY: the reason rides on each smart block and a top-level
    `critic_gate` summary — never a silent drop. Injected test/handoff seams are a
    known-competent critic and bypass this gate; with no `g3_result` the artifact path stays
    backward-compatible (armed).

    `deep=True` additionally arms the DEEP SEEKER tier — a TRAINED PPO stale-seeker
    (harness/rl/stale_seek.py). It is COSTLY (one PPO training per game) so it runs
    ONLY when the cheap tiers above certified NO softlock, and only on the gdscript
    lane with a `game_path` (the seeker spawns a batched Godot serve host). Its
    candidates flow into the SAME CONFIRM oracle, so a finding is an identical hard
    `softlock`. `deep` implies `stale` (the cheap tiers run first, as the gate).
    """
    if deep:
        stale = True                          # the cheap stale tier is the deep gate
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
    # An out-of-process executor with a persistent handle (the gdscript serve host)
    # must be torn down when the suite ends; per-batch spawners (js/godot) and the
    # in-process py executor have no close() and are skipped by the guard below.
    try:
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

        # CRITIC-COMPETENCE GATE (the RAISED model-gate predicate). A real model artifact
        # arms the smart tiers ONLY when its critic CONVERGED to a demo-ready policy
        # (certify.critic_competent over the producing g3' result). An unconverged critic's
        # value map is noise — the A/B showed weak-critic == 0, worse than the critic-free
        # fuzz — so we DOWNGRADE to the critic-free ladder, HONESTLY (reason on each smart
        # block + the `critic_gate` summary), never silently. Injected test/handoff seams
        # (critic/candidates/env_factory) ARE a known-competent critic and bypass the g3
        # gate; with no g3_result the artifact path stays backward-compatible (armed).
        from harness.rl.certify import critic_competent
        _model_armed = model is not None or model_path is not None
        _iv_seams = any(x is not None for x in (iv_critic, iv_candidates, iv_env_factory))
        _descent_seams = any(x is not None for x in
                             (descent_critic, descent_candidates, descent_env_factory))
        _critic_ok = True if g3_result is None else critic_competent(g3_result)
        _critic_downgraded = bool(_model_armed and not _critic_ok)

        # PRIMARY smart tier (ahead of random fuzz in the ladder): the model-steered
        # inverse-value softlock hunt. Armed by a competent critic OR an injected seam.
        iv_requested = _iv_seams or (_model_armed and _critic_ok)
        iv_block = _run_inverse_value(
            executor, game_source, engine, actions, report,
            requested=iv_requested, controlled=controlled, game_path=game_path, model=model,
            model_path=model_path, critic=iv_critic, candidates=iv_candidates,
            env_factory=iv_env_factory, horizon=horizon, seed=seed, stale_H=stale_H,
            stale_budget=stale_budget, top_m=top_m, window=iv_window,
            iv_seeds=iv_seeds, iv_eps=iv_eps, iv_max_ticks=iv_max_ticks)

        # S1.5 SMART TIER — policy-guided descent, slotted BETWEEN S1 (greedy) and the
        # deep seeker (S2). Same critic-competence gate as S1 (a competent `model`/`model_path`
        # arms both); dedicated `descent_*` seams inject a critic/candidates/env for tests
        # WITHOUT perturbing the S1 (inverse-value) tests. CHEAP (policy rollouts) — std path.
        descent_requested = _descent_seams or (_model_armed and _critic_ok)
        descent_block = _run_descent(
            executor, game_source, engine, actions, report,
            requested=descent_requested, controlled=controlled, game_path=game_path,
            model=model, model_path=model_path, critic=descent_critic,
            candidates=descent_candidates, env_factory=descent_env_factory, horizon=horizon,
            seed=seed, stale_H=stale_H, stale_budget=stale_budget, top_m=top_m,
            window=iv_window, n_waypoints=descent_waypoints, descent_ticks=descent_ticks,
            eps=iv_eps)

        # HONEST DOWNGRADE: a model was in hand but its critic was not competent, so the
        # model-armed smart tiers were dropped to the critic-free ladder. Annotate each
        # dropped block's reason (seam-injected blocks ran on their own critic — untouched).
        if _critic_downgraded:
            _note = _critic_downgrade_note(g3_result)
            for _blk, _seamed in ((iv_block, _iv_seams), (descent_block, _descent_seams)):
                if not _seamed:
                    _blk["reason"] = _note
                    _blk["critic_downgraded"] = True

        stale_block = _run_stale(executor, game_source, engine, actions, report,
                                 controlled=controlled, initial=initial, horizon=horizon,
                                 seed=seed, requested=bool(stale), stale_H=stale_H,
                                 stale_budget=stale_budget,
                                 stale_cand_budget=stale_cand_budget, top_m=top_m)

        # The smart tiers (S1 + S1.5) lead the ladder; the mechanical/stale tiers follow.
        smart_findings = list(iv_block["findings"]) + list(descent_block["findings"])
        cheap_findings = (list(tier0["findings"]) + list(tier1["findings"])
                          + list(stale_block["findings"]))
        # Deep seeker tier — armed by deep=True, but only escalated when NOTHING above
        # certified a softlock (gate lives in _run_seeker; the CHEAP descent tier counts
        # as an "above" tier, so a descent-certified softlock skips the deep escalation).
        seeker_block = _run_seeker(
            game_source, engine, actions, report, game_path=game_path,
            requested=bool(deep), cheap_findings=smart_findings + cheap_findings,
            controlled=controlled, seed=seed, budget=seeker_budget,
            num_envs=seeker_num_envs, seeds=seeker_seeds, waypoints=seeker_waypoints,
            top_m=seeker_top_m, stale_H=stale_H, stale_budget=stale_budget)

        findings = smart_findings + cheap_findings + list(seeker_block["findings"])
        grade = _grade(findings, tier1, tiers)
        out.update({
            "grade": grade,
            "passed": grade != "open",
            # The RAISED model-gate's honest surface: whether a handed-off critic was judged
            # competent and, if not, that the smart tiers were downgraded (never silent).
            "critic_gate": _critic_gate_summary(g3_result, _model_armed, _critic_ok,
                                                _critic_downgraded),
            "inverse_value": iv_block,
            "descent": descent_block,
            "tier0": tier0,
            "tier1": tier1,
            "stale": stale_block,
            "seeker": seeker_block,
            "findings": findings,
            "hard_findings": [f for f in findings if f["hard"]],
        })
        return out
    finally:
        close = getattr(executor, "close", None)
        if callable(close):
            close()


def _actions_from_report(report):
    """Best-effort ACTIONS recovery from a report (used for the JS engine, where
    we do not load the module in-process)."""
    try:
        return list(report["actions"])
    except (KeyError, TypeError):
        pass
    # The G1 efficacy check holds the declared move set as its `effect` keys —
    # present in every certified report, both engines.
    try:
        eff = report["layers"]["G1_rollout"]["checks"]["efficacy"]["effect"]
        return list(eff.keys()) or None
    except (KeyError, TypeError):
        return None


def attack_game(game_path, *, tiers=(0,), sandboxed=True, world_factory=None,
                seed=0, k=DEFAULT_ATTACKS_PER_CALL, models=None,
                model=None, model_path=None, **fuzz_kwargs):
    """Verify (to obtain the certified report + witness) then attack a game file.

    Only a game that PASSES the G0-G3 funnel is attacked (G4 rides after G3). An
    uncertified game returns a report with grade "uncertified" and the funnel hint.

    ``model`` / ``model_path`` (a trained G3' SB3 artifact) turn on the PRIMARY
    inverse-value smart tier (harness.rl.adversary): a critic-steered softlock hunt
    ahead of the random fuzz. With neither, the ladder is unchanged.
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
                  models=models, game_path=game_path, model=model,
                  model_path=model_path, **fuzz_kwargs)


def _slug_of(game_path):
    import os
    return os.path.splitext(os.path.basename(str(game_path)))[0]


# ======================================================================== #
# Finding -> author-repair report (reuses the existing repair contract, §4.7)
# ======================================================================== #
_REPAIR_HINTS = {
    "unintended_success": "an avoidance policy won without playing; tighten the goal "
                          "so it cannot be reached by idling or the wrong moves",
    "broken_gating": "BROKEN GATING: success is reachable WITHOUT passing a declared "
                     "checkpoint that is supposed to gate it — make success actually "
                     "require every gating milestone (no bypass)",
    "shortcut_beats_witness": "a fuzz policy won far faster than the intended path; "
                              "make the goal require the full progression",
    "nan": "adversarial input drove the physics to NaN/explosion; bound forces/velocities",
    "escape": "adversarial input drove a body out of the world; add bounds or damping",
    "stuck": "an adversarial player soft-locked the controlled body; avoid dead-end states",
    "single_action_win": "BROKEN: your game is winnable by repeating a single action — "
                         "add a real obstacle/choice so success needs varied play",
    "softlock": "an action prefix drives the game into a state from which no continuation "
                "can win (certified by the tree-refutation oracle); ensure every reachable "
                "state can still reach the goal, or add an escape/reset from dead ends",
}


def to_repair_report(finding):
    """Shape a G4 finding as the author-repair report gamegen._repair_user_msg
    already renders (failure_class + hint + reproducer + JSON)."""
    outcome = finding.get("outcome")
    # A finding-level `repair_hint` (the inverse-value tier names the last checkpoint
    # before the freeze) wins over the generic per-outcome hint so the feedback
    # compiler gets the specific, actionable pointer; else fall back by outcome.
    hint = (finding.get("repair_hint")
            or _REPAIR_HINTS.get(outcome, finding.get("detail", "adversarial finding")))
    return {
        "passed": False,
        "failure_class": "G4_FINDING",
        "outcome": outcome,
        "hint": hint,
        "g4_reproducer": finding.get("reproducer"),
        "g4_evidence": finding.get("evidence"),
    }
