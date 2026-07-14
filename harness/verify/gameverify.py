"""Module F — universal oracles for generated v2 games (CONTRACTS §4).

The game defines WHAT the game is; it can never define what SANITY is. These
checks live outside the game's reach and read engine state only (no pixels, no
VLM). A four-layer funnel, stopping at the first failing layer:

    G0_static   sandbox scan + module loads + required symbols + one controlled
                dynamic body + >= 2 entities + no initial penetration + in bounds
    G1_rollout  600-step noop rollout: no NaN, no escape, no success under noop
                (agency), determinism, per-action efficacy (dead-action check)
    G2_goal     success() is a pure bool that is False at t=0 (same for failure);
                checkpoints() is a pure dict[str, bool] of 1..6 snake_case
                milestones, all False at t=0 (v2.1)
    G3_solve    seeded random search -> a replayable witness, or UNSOLVED; v2.1
                checkpoint semantics: the runner latches milestone first-True
                ticks, dead milestones on the witness -> GOAL_ERROR, latch-order
                vs declared-order mismatch -> warning, a checkpoint-guided
                second pass reuses the best failed episode's prefix, and an
                UNSOLVED verdict carries a progress diagnosis ("the game is
                stuck between '<k>' and '<next>'")

`run_episode` is the single §2 runner reused by G1, G3, the replay renderer and
future policies. `verify_game` orchestrates the funnel.
"""

from __future__ import annotations

import random
import re
import traceback

from harness.core.sandbox import (
    SandboxViolation, load_scene_namespace, scan_source,
)

# --- Constants ([eng.] = engineering choice to calibrate) ---------------- #
K_STEPS = 6                 # physics steps per decision tick (CONTRACTS §2)
GAMEVERIFY_TIMEOUT_S = 180  # sandbox subprocess budget for a full G0-G3 run [eng.]

# G0
MIN_ACTIONS, MAX_ACTIONS = 2, 8         # declared action-set size
MIN_ENTITIES = 2
PEN_INIT_TOL = 1.5          # px: max tolerated initial dynamic-pair penetration [eng.]

# G1
NOOP_TICKS = 100            # 600 physics steps of noop rollout (600 / K_STEPS) [eng.]
ESCAPE_MARGIN = 200.0       # px: a dynamic body beyond world+margin has escaped [eng.]
EFFICACY_TICKS = 15         # hold each action from t0 for 15 ticks (90 steps) [eng.]
EFFICACY_EPS = 1e-3         # px/rad: min snapshot divergence for a "live" action [eng.]
DETERMINISM_EPS = 1e-6      # px/rad: two identical seeded runs must match within this [eng.]
NAN_EVENT_TYPES = {"nan_detected", "nan", "explosion"}

# G2 (v2.1 checkpoints)
CP_MIN, CP_MAX = 1, 6       # declared milestone count (CONTRACTS §2)
_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")

# G3
PROBE_EPISODES = 40         # random-search episodes [eng.]
PROBE_HORIZON = 120         # decision ticks per episode [eng.]
MACRO_MIN, MACRO_MAX = 1, 4  # macro-action hold length (ticks) [eng.]
WORLD_SEED = 0              # physics seed shared by every fresh world [eng.]
TRIVIAL_TICKS = 5           # a witness shorter than this marks a degenerate goal [eng.]
GUIDED_EPISODES = 20        # checkpoint-guided second-pass episodes (v2.1) [eng.]
GUIDED_SEED_BASE = 1000     # probe seeds 1000+i for the guided pass (v2.1)

_REQUIRED_SYMBOLS = ("TITLE", "PROMPT", "ACTIONS", "build", "act", "success",
                     "checkpoints")
_CALLABLE_SYMBOLS = ("build", "act", "success", "checkpoints")


# ======================================================================== #
# Game handle
# ======================================================================== #
class Game:
    """Thin view over a loaded game namespace (CONTRACTS §2 symbols)."""

    def __init__(self, ns: dict):
        self.namespace = ns
        self.title = ns.get("TITLE")
        self.prompt = ns.get("PROMPT")
        self.actions = ns.get("ACTIONS")
        self.build = ns.get("build")
        self.act = ns.get("act")
        self.on_step = ns.get("on_step")      # optional
        self.success = ns.get("success")
        self.failure = ns.get("failure")      # optional
        self.checkpoints = ns.get("checkpoints")  # required by v2.1 (G0 enforces)


def load_game(source: str) -> Game:
    """Load game source into a restricted namespace and wrap it as a Game."""
    return Game(load_scene_namespace(source, scan=False))


# ======================================================================== #
# The single runner (CONTRACTS §2)
# ======================================================================== #
def run_episode(game: Game, world, actions_iter, max_ticks: int) -> dict:
    """Run one episode on an ALREADY-BUILT world (caller does game.build).

    `actions_iter` is any iterable of per-tick actions. Each item is either a
    string -> `game.act(world, s)`, or `None` -> a noop tick (act is skipped;
    G1's "noop" is exactly this). One item is consumed per decision tick.

    A decision tick is: optional act; then K_STEPS x [world.step(1); on_step];
    then latch checkpoints; then check failure, then success. The episode ends
    on success/failure, on a caught exception from game/engine code, on the
    step budget, or when the iterator is exhausted.

    CHECKPOINT LATCHING (v2.1): after each tick the runner evaluates
    `game.checkpoints(world)` and records the FIRST tick (1-based, comparable
    to "ticks") each key became True. Latching is runner-side so a milestone
    stays "passed" even if its underlying state later regresses; the game's
    predicates stay pure. Games without a checkpoints symbol get an empty map
    (legacy callers such as the replay renderer keep working).

    Returns {"result", "ticks", "steps", "actions", "snapshot", "checkpoints",
    "error"} with result in {"success","failure","budget","exhausted","error"}.
    """
    it = iter(actions_iter)
    applied: list = []
    latches: dict = {}
    result = "budget"
    for _ in range(max_ticks):
        try:
            action = next(it)
        except StopIteration:
            result = "exhausted"
            break
        try:
            applied.append(action)
            if action is not None:
                game.act(world, action)
            for _s in range(K_STEPS):
                world.step(1)
                if game.on_step is not None:
                    game.on_step(world)
            # Latch BEFORE the terminal checks so milestones reached on the
            # winning/losing tick are still recorded.
            if game.checkpoints is not None:
                for key, value in game.checkpoints(world).items():
                    if key not in latches:
                        latches[key] = None
                    if latches[key] is None and value:
                        latches[key] = len(applied)
            if game.failure is not None and game.failure(world):
                result = "failure"
                break
            if bool(game.success(world)):
                result = "success"
                break
        except Exception:
            return {"result": "error", "ticks": len(applied),
                    "steps": _safe_steps(world), "actions": applied,
                    "snapshot": _safe_snapshot(world), "checkpoints": latches,
                    "error": traceback.format_exc(limit=4)}
    return {"result": result, "ticks": len(applied), "steps": _safe_steps(world),
            "actions": applied, "snapshot": _safe_snapshot(world),
            "checkpoints": latches, "error": None}


def _safe_steps(world) -> int:
    try:
        return int(world.steps)
    except Exception:
        return -1


def _safe_snapshot(world) -> dict:
    try:
        return world.snapshot()
    except Exception:
        return {}


# ======================================================================== #
# Snapshot helpers
# ======================================================================== #
def _snapshot_delta(a: dict, b: dict) -> float:
    """Max absolute divergence over shared bodies; inf if the key sets differ."""
    if set(a) != set(b):
        return float("inf")
    worst = 0.0
    for name, sa in a.items():
        sb = b[name]
        for key in ("pos", "vel"):
            for x, y in zip(sa.get(key, []), sb.get(key, [])):
                worst = max(worst, abs(float(x) - float(y)))
        worst = max(worst, abs(float(sa.get("angle", 0.0)) - float(sb.get("angle", 0.0))))
    return worst


# ======================================================================== #
# World construction
# ======================================================================== #
def _default_world_factory(seed: int = 0):
    """Real World (module E), imported lazily so tests can inject a fake."""
    from harness.core.world import World
    return World(seed=seed)


def _fresh(factory, game: Game):
    """A fresh world with the game built into it. Raises on build failure."""
    world = factory(seed=WORLD_SEED)
    game.build(world)
    return world


def _dynamic_entities(world) -> list[str]:
    out = []
    for name in world.entities():
        try:
            if not world.query(name).get("static", False):
                out.append(name)
        except Exception:
            pass
    return out


# ======================================================================== #
# Report scaffolding
# ======================================================================== #
def make_report() -> dict:
    """Blank report structure (CONTRACTS §4 schema)."""
    return {
        "passed": False,
        "failure_class": None,   # None | "ENV_ERROR" | "GOAL_ERROR" | "UNSOLVED"
        "layers": {
            "G0_static": _empty_layer(),
            "G1_rollout": _empty_layer(),
            "G2_goal": _empty_layer(),
            "G3_solve": _empty_layer(),
        },
        "hint": "",
        "warnings": [],      # non-fatal notes (e.g. milestone order mismatch)
        "progress": None,    # UNSOLVED diagnosis: reach_counts + stuck_after
        "witness": None,
    }


def _empty_layer() -> dict:
    return {"passed": False, "checks": {}}


def check(passed: bool, **extra) -> dict:
    out = {"pass": bool(passed)}
    out.update(extra)
    return out


# ======================================================================== #
# G0 — static layer
# ======================================================================== #
def run_g0(factory, source: str):
    """Static checks. Returns (layer, game|None)."""
    layer = {"passed": False, "checks": {}}
    checks = layer["checks"]

    violations = scan_source(source)
    checks["sandbox_scan"] = check(not violations, violations=violations)
    if violations:
        return layer, None

    # Load in a restricted namespace.
    try:
        game = load_game(source)
    except SandboxViolation as exc:
        checks["sandbox_scan"] = check(False, violations=exc.violations)
        return layer, None
    except Exception:
        checks["loads"] = check(False, error=traceback.format_exc(limit=3))
        return layer, None
    checks["loads"] = check(True)

    # Required symbols present and callable where they must be.
    missing = [s for s in _REQUIRED_SYMBOLS if game.namespace.get(s) is None]
    not_callable = [s for s in _CALLABLE_SYMBOLS
                    if s not in missing and not callable(game.namespace.get(s))]
    checks["symbols"] = check(not missing and not not_callable,
                              missing=missing, not_callable=not_callable)
    if missing or not_callable:
        return layer, game

    # ACTIONS is a list[str] of size 2..8.
    actions = game.actions
    actions_ok = (isinstance(actions, list) and actions
                  and all(isinstance(a, str) for a in actions)
                  and MIN_ACTIONS <= len(actions) <= MAX_ACTIONS)
    checks["actions"] = check(actions_ok,
                              n=len(actions) if isinstance(actions, list) else None)
    if not actions_ok:
        return layer, game

    # build(world) runs.
    try:
        world = _fresh(factory, game)
    except Exception:
        checks["builds"] = check(False, error=traceback.format_exc(limit=3))
        return layer, game
    checks["builds"] = check(True)

    entities = list(world.entities())

    # Exactly one controlled dynamic body.
    controlled = [n for n in entities
                  if _truthy(lambda: world.query(n).get("controlled", False))]
    one_controlled = (len(controlled) == 1
                      and not _truthy(lambda: world.query(controlled[0]).get("static", False)))
    checks["controlled"] = check(one_controlled, controlled=controlled)

    # >= 2 entities.
    checks["counts"] = check(len(entities) >= MIN_ENTITIES, n=len(entities))

    # No initial penetration (dynamic pairs, sensors excluded, static-static skipped).
    static = {n: _truthy(lambda n=n: world.query(n).get("static", False)) for n in entities}
    offenders = []
    for i, a in enumerate(entities):
        for b in entities[i + 1:]:
            if static[a] and static[b]:
                continue
            try:
                depth = float(world.penetration_depth(a, b) or 0.0)
            except Exception:
                depth = 0.0
            if depth > PEN_INIT_TOL:
                offenders.append([a, b, round(depth, 3)])
    checks["no_penetration"] = check(not offenders, offenders=offenders)

    # Dynamic bodies in bounds.
    oob = [n for n in entities
           if not static[n] and not _truthy(lambda n=n: world.in_bounds(n))]
    checks["in_bounds"] = check(not oob, offenders=oob)

    layer["passed"] = all(c["pass"] for c in checks.values())
    return layer, game


def _truthy(fn) -> bool:
    try:
        return bool(fn())
    except Exception:
        return False


# ======================================================================== #
# G1 — noop rollout, agency, determinism, action efficacy
# ======================================================================== #
def run_g1(factory, game: Game):
    layer = {"passed": False, "checks": {}}
    checks = layer["checks"]

    # --- Full noop rollout: agency + NaN + escape ---
    world = _fresh(factory, game)
    noop = run_episode(game, world, _repeat(None, NOOP_TICKS), NOOP_TICKS)

    nan_event = any(e.get("type") in NAN_EVENT_TYPES for e in _safe_events(world))
    checks["no_nan"] = check(noop["result"] != "error" and not nan_event,
                             result=noop["result"])

    escaped = [n for n in _dynamic_entities(world)
               if not _truthy(lambda n=n: world.in_bounds(n, ESCAPE_MARGIN))]
    checks["no_escape"] = check(not escaped, offenders=escaped)

    # Agency: success must never fire with zero actions.
    checks["agency"] = check(noop["result"] != "success", result=noop["result"])

    # --- Determinism: two fresh seeded worlds, identical noop rollout ---
    w1 = _fresh(factory, game)
    w2 = _fresh(factory, game)
    r1 = run_episode(game, w1, _repeat(None, NOOP_TICKS), NOOP_TICKS)
    r2 = run_episode(game, w2, _repeat(None, NOOP_TICKS), NOOP_TICKS)
    delta = _snapshot_delta(r1["snapshot"], r2["snapshot"])
    checks["determinism"] = check(delta <= DETERMINISM_EPS, delta=_round_inf(delta))

    # --- Action efficacy: each declared action must move the world ---
    base_world = _fresh(factory, game)
    baseline = run_episode(game, base_world, _repeat(None, EFFICACY_TICKS),
                           EFFICACY_TICKS)["snapshot"]
    dead = []
    effect = {}
    for action in (game.actions or []):
        aw = _fresh(factory, game)
        snap = run_episode(game, aw, _repeat(action, EFFICACY_TICKS),
                           EFFICACY_TICKS)["snapshot"]
        d = _snapshot_delta(snap, baseline)
        effect[action] = _round_inf(d)
        if d <= EFFICACY_EPS:
            dead.append(action)
    checks["efficacy"] = check(not dead, dead=dead, effect=effect)

    layer["passed"] = all(c["pass"] for c in checks.values())
    return layer


def _repeat(value, n):
    for _ in range(n):
        yield value


def _safe_events(world) -> list:
    try:
        return list(world.events())
    except Exception:
        return []


def _round_inf(x: float):
    return None if x == float("inf") else round(x, 6)


# ======================================================================== #
# G2 — goal layer (success/failure/checkpoints well-formed at t=0)
# ======================================================================== #
def run_g2(factory, game: Game):
    layer = {"passed": False, "checks": {}}
    checks = layer["checks"]

    ok = _check_predicate(factory, game, game.success, "success", checks,
                          must_be_false_at_t0=True)
    if ok and game.failure is not None:
        ok = _check_predicate(factory, game, game.failure, "failure", checks,
                              must_be_false_at_t0=True)
    if ok:
        _check_checkpoints(factory, game, checks)

    layer["passed"] = all(c["pass"] for c in checks.values())
    return layer


def _check_predicate(factory, game, fn, label, checks, *, must_be_false_at_t0):
    """Fill callable_bool / not_true_at_t0 / pure checks for one predicate."""
    world = _fresh(factory, game)
    snap_before = _safe_snapshot(world)
    try:
        r1 = fn(world)
        r2 = fn(world)
    except Exception:
        checks[f"{label}_callable_bool"] = check(
            False, error=traceback.format_exc(limit=3))
        return False
    snap_after = _safe_snapshot(world)

    is_bool = isinstance(r1, bool) and isinstance(r2, bool)
    checks[f"{label}_callable_bool"] = check(is_bool, value=bool(r1) if is_bool else None)
    if not is_bool:
        return False

    if must_be_false_at_t0:
        checks[f"{label}_false_at_t0"] = check(not r1, value=bool(r1))

    pure = (r1 == r2) and (snap_before == snap_after)
    checks[f"{label}_pure"] = check(pure, deterministic=(r1 == r2),
                                    state_unchanged=(snap_before == snap_after))
    return all(checks[f"{label}_{k}"]["pass"]
               for k in ("callable_bool", "false_at_t0", "pure")
               if f"{label}_{k}" in checks)


def _check_checkpoints(factory, game, checks) -> bool:
    """v2.1: checkpoints(world) -> dict[str, bool], 1..6 snake_case entries,
    ALL False at t=0, pure (same 2-call + snapshot protocol as success)."""
    world = _fresh(factory, game)
    snap_before = _safe_snapshot(world)
    try:
        c1 = game.checkpoints(world)
        c2 = game.checkpoints(world)
    except Exception:
        checks["checkpoints_wellformed"] = check(
            False, error=traceback.format_exc(limit=3))
        return False
    snap_after = _safe_snapshot(world)

    is_dict = isinstance(c1, dict) and isinstance(c2, dict)
    n = len(c1) if is_dict else None
    bad_keys = ([k for k in c1 if not (isinstance(k, str) and _SNAKE_CASE.match(k))]
                if is_dict else [])
    non_bool = ([k for k, v in c1.items() if not isinstance(v, bool)]
                if is_dict else [])
    wellformed = (is_dict and CP_MIN <= n <= CP_MAX
                  and not bad_keys and not non_bool)
    checks["checkpoints_wellformed"] = check(wellformed, n=n, bad_keys=bad_keys,
                                             non_bool=non_bool)
    if not wellformed:
        return False

    true_at_t0 = [k for k, v in c1.items() if v]
    checks["checkpoints_false_at_t0"] = check(not true_at_t0, offenders=true_at_t0)

    pure = (c1 == c2) and (snap_before == snap_after)
    checks["checkpoints_pure"] = check(pure, deterministic=(c1 == c2),
                                       state_unchanged=(snap_before == snap_after))
    return not true_at_t0 and pure


# ======================================================================== #
# G3 — solvability probe (seeded random search + checkpoint guidance)
# ======================================================================== #
def run_g3(factory, game: Game):
    """Random macro-action search -> witness, UNSOLVED, or a trivial goal.

    v2.1: each episode's checkpoint latches feed (a) the dead-milestone check
    on the witness, (b) a declared-vs-empirical order warning, (c) a guided
    second pass reusing the best failed episode's action prefix, and (d) the
    UNSOLVED progress diagnosis.
    """
    layer = {"passed": False, "checks": {}, "warnings": [], "progress": None}
    checks = layer["checks"]

    declared = _declared_order(factory, game)
    episodes: list[dict] = []      # every finished (non-witness) episode's data
    witness = None

    # --- First pass: pure random macro-action search ---
    for episode in range(PROBE_EPISODES):
        probe_rng = random.Random(episode)
        plan = _macro_plan(probe_rng, game.actions, PROBE_HORIZON)
        world = _fresh(factory, game)
        ep = run_episode(game, world, iter(plan), PROBE_HORIZON)
        episodes.append(ep)
        if ep["result"] == "success":
            witness = _make_witness(episode, ep)
            break

    # --- Checkpoint-guided second pass (v2.1) ---
    # If pure random failed but some episode latched >= 1 milestone, replay the
    # best episode's prefix (up to its last latch tick) + random continuation.
    guided_ran = False
    if witness is None:
        prefix = _best_prefix(episodes, declared)
        if prefix:
            guided_ran = True
            for i in range(GUIDED_EPISODES):
                seed = GUIDED_SEED_BASE + i
                cont_rng = random.Random(seed)
                plan = prefix + _macro_plan(cont_rng, game.actions,
                                            PROBE_HORIZON - len(prefix))
                world = _fresh(factory, game)
                ep = run_episode(game, world, iter(plan), PROBE_HORIZON)
                episodes.append(ep)
                if ep["result"] == "success":
                    witness = _make_witness(seed, ep)
                    break

    checks["episodes"] = check(True, run=len(episodes), budget=PROBE_EPISODES,
                               guided=guided_ran)

    # --- No witness -> UNSOLVED, with a progress diagnosis ---
    checks["solvable"] = check(witness is not None)
    if witness is None:
        layer["progress"] = _progress(declared, episodes)
        return layer
    layer["witness"] = witness

    # --- Anti-triviality ---
    checks["non_trivial"] = check(witness["ticks"] >= TRIVIAL_TICKS,
                                  ticks=witness["ticks"])
    if not checks["non_trivial"]["pass"]:
        return layer

    # --- Dead milestones: every checkpoint must have latched on the witness.
    # A latch is recorded during the episode, hence always at/before the
    # success tick; never-latched (None) = dead milestone.
    dead = [k for k, t in witness["checkpoints"].items() if t is None]
    checks["milestones_latched"] = check(not dead, dead=dead)

    # --- Declared order vs empirical latch order (non-fatal warning) ---
    mismatch = _order_mismatch(declared, witness["checkpoints"])
    if mismatch:
        layer["warnings"].append(
            f"checkpoint latch order differs from declared order: declared "
            f"[{', '.join(declared)}], observed [{', '.join(mismatch)}]")

    # --- The witness must be EXACTLY replayable from a fresh seeded world ---
    replay_world = _fresh(factory, game)
    replay = run_episode(game, replay_world, iter(witness["actions"]),
                         len(witness["actions"]))
    checks["replayable"] = check(replay["result"] == "success", result=replay["result"])

    layer["passed"] = all(c["pass"] for c in checks.values())
    return layer


def _make_witness(seed: int, ep: dict) -> dict:
    return {"seed": seed, "actions": ep["actions"], "ticks": ep["ticks"],
            "checkpoints": dict(ep.get("checkpoints", {}))}


def _declared_order(factory, game) -> list[str]:
    """Declared milestone order = insertion order of the t=0 checkpoints dict."""
    try:
        return list(game.checkpoints(_fresh(factory, game)).keys())
    except Exception:
        return []


def _order_mismatch(declared: list[str], latches: dict) -> list[str] | None:
    """Empirical latch order if it contradicts the declared order, else None.

    A contradiction is a declared-earlier milestone latching strictly AFTER a
    declared-later one (same-tick latches never contradict).
    """
    ticks = [latches.get(k) for k in declared]
    contradicts = any(
        ticks[i] is not None and ticks[j] is not None and ticks[i] > ticks[j]
        for i in range(len(declared)) for j in range(i + 1, len(declared)))
    if not contradicts:
        return None
    latched = sorted((latches[k], i) for i, k in enumerate(declared)
                     if latches.get(k) is not None)
    return [declared[i] for _, i in latched]


def _best_prefix(episodes: list[dict], declared: list[str]) -> list | None:
    """Action prefix of the most promising failed episode, up to its last latch.

    Best = deepest declared milestone latched, then the earliest last-latch
    tick (more remaining budget for the random continuation).
    """
    best = None
    best_key = None
    for ep in episodes:
        latches = ep.get("checkpoints", {})
        hit = [(i, latches[k]) for i, k in enumerate(declared)
               if latches.get(k) is not None]
        if not hit:
            continue
        depth = max(i for i, _ in hit)
        last_tick = max(t for _, t in hit)
        if last_tick >= PROBE_HORIZON:
            continue  # no room left for a continuation
        key = (depth, -last_tick)
        if best_key is None or key > best_key:
            best_key = key
            best = ep["actions"][:last_tick]
    return best


def _progress(declared: list[str], episodes: list[dict]) -> dict:
    """UNSOLVED diagnosis: how far did the probe get, milestone by milestone."""
    reach_counts = {
        name: sum(1 for ep in episodes
                  if ep.get("checkpoints", {}).get(name) is not None)
        for name in declared}
    stuck_after = None
    for name in declared:                      # deepest declared name reached
        if reach_counts.get(name, 0) > 0:
            stuck_after = name
    return {"reach_counts": reach_counts, "stuck_after": stuck_after}


def _macro_plan(rng: random.Random, actions, horizon: int) -> list:
    """Flat per-tick action list built from macro-actions (hold 1-4 ticks)."""
    plan: list = []
    while len(plan) < horizon:
        action = rng.choice(actions)
        hold = rng.randint(MACRO_MIN, MACRO_MAX)
        plan.extend([action] * hold)
    return plan[:horizon]


# ======================================================================== #
# Hints
# ======================================================================== #
def _hint_g0(checks: dict) -> str:
    if not checks.get("sandbox_scan", {}).get("pass", True):
        v = checks["sandbox_scan"].get("violations") or ["non-compliant code"]
        return f"code rejected by the sandbox: {v[0]}"
    if not checks.get("loads", {}).get("pass", True):
        return f"module failed to load: {checks['loads'].get('error', 'unknown error')}"
    if not checks.get("symbols", {}).get("pass", True):
        s = checks["symbols"]
        return (f"missing/invalid game symbols: "
                f"{', '.join(s.get('missing', []) + s.get('not_callable', [])) or 'unknown'}")
    if not checks.get("actions", {}).get("pass", True):
        return f"ACTIONS must be a list of 2..8 strings (got {checks['actions'].get('n')})"
    if not checks.get("builds", {}).get("pass", True):
        return f"build(world) failed: {checks['builds'].get('error', 'unknown error')}"
    if not checks.get("controlled", {}).get("pass", True):
        c = checks["controlled"].get("controlled", [])
        return (f"exactly one controlled dynamic body required (found {c or 'none'})")
    if not checks.get("counts", {}).get("pass", True):
        return f"too few entities ({checks['counts'].get('n')}); need at least {MIN_ENTITIES}"
    if not checks.get("no_penetration", {}).get("pass", True):
        a, b, d = checks["no_penetration"]["offenders"][0]
        return f"initial interpenetration between {a} and {b} ({d}px)"
    if not checks.get("in_bounds", {}).get("pass", True):
        return f"dynamic body out of bounds: {', '.join(checks['in_bounds']['offenders'])}"
    return "static failure (G0)"


def _hint_g1(checks: dict) -> str:
    if not checks.get("no_nan", {}).get("pass", True):
        return "numerical explosion (NaN) during the noop rollout"
    if not checks.get("no_escape", {}).get("pass", True):
        return f"dynamic body escaped the world: {', '.join(checks['no_escape']['offenders'])}"
    if not checks.get("agency", {}).get("pass", True):
        return "success is already reached under a noop rollout (no player agency required)"
    if not checks.get("determinism", {}).get("pass", True):
        return (f"non-deterministic simulation: two identical seeded rollouts diverged "
                f"(delta={checks['determinism'].get('delta')})")
    if not checks.get("efficacy", {}).get("pass", True):
        return f"dead action(s) with no effect on the world: {', '.join(checks['efficacy']['dead'])}"
    return "rollout failure (G1)"


def _hint_g2(checks: dict) -> str:
    for label in ("success", "failure"):
        if not checks.get(f"{label}_callable_bool", {"pass": True}).get("pass", True):
            return (f"{label}() malformed: "
                    f"{checks[f'{label}_callable_bool'].get('error', 'does not return a bool')}")
        if not checks.get(f"{label}_false_at_t0", {"pass": True}).get("pass", True):
            return f"{label}() is already true at t=0 (degenerate goal)"
        if not checks.get(f"{label}_pure", {"pass": True}).get("pass", True):
            detail = ("non-deterministic result"
                      if not checks[f"{label}_pure"].get("deterministic")
                      else "mutates the world state")
            return f"{label}() is not pure: {detail}"
    if not checks.get("checkpoints_wellformed", {"pass": True}).get("pass", True):
        c = checks["checkpoints_wellformed"]
        if c.get("error"):
            return f"checkpoints() malformed: {c['error']}"
        offenders = c.get("bad_keys", []) + c.get("non_bool", [])
        detail = (f"invalid entries: {', '.join(offenders)}" if offenders
                  else f"must be a dict of {CP_MIN}..{CP_MAX} snake_case bool "
                       f"entries (got {c.get('n')})")
        return f"checkpoints() malformed: {detail}"
    if not checks.get("checkpoints_false_at_t0", {"pass": True}).get("pass", True):
        offenders = checks["checkpoints_false_at_t0"].get("offenders", [])
        return (f"checkpoint(s) already true at t=0: {', '.join(offenders)} "
                f"— milestones must start unreached")
    if not checks.get("checkpoints_pure", {"pass": True}).get("pass", True):
        detail = ("non-deterministic result"
                  if not checks["checkpoints_pure"].get("deterministic")
                  else "mutates the world state")
        return f"checkpoints() is not pure: {detail}"
    return "goal failure (G2)"


def _hint_g3(checks: dict, layer: dict) -> str:
    if not checks.get("non_trivial", {"pass": True}).get("pass", True):
        w = layer.get("witness") or {}
        return (f"goal is trivially reached in {w.get('ticks')} decision ticks "
                f"(< {TRIVIAL_TICKS}); make the goal require real play")
    if not checks.get("milestones_latched", {"pass": True}).get("pass", True):
        dead = checks["milestones_latched"].get("dead", [])
        return (f"dead milestone(s) never latched on the winning path: "
                f"{', '.join(dead)} — fix or remove them")
    if not checks.get("solvable", {}).get("pass", True):
        return _hint_unsolved(checks, layer.get("progress"))
    if not checks.get("replayable", {}).get("pass", True):
        return "the discovered witness does not replay to success (non-deterministic engine)"
    return "solvability failure (G3)"


def _hint_unsolved(checks: dict, progress: dict | None) -> str:
    """Milestone-aware UNSOLVED hint: name the boundary where the probe stalls."""
    total = checks.get("episodes", {}).get("run", PROBE_EPISODES)
    if not progress or not progress.get("reach_counts"):
        return (f"no random rollout reached success in {total} episodes "
                f"x {PROBE_HORIZON} ticks — make the goal easier to reach")
    reach = progress["reach_counts"]
    declared = list(reach)
    stuck = progress.get("stuck_after")
    if stuck is None:
        return (f"no episode reached the first milestone '{declared[0]}' "
                f"in {total} episodes — make the first stage easier")
    nxt = declared[declared.index(stuck) + 1] if declared.index(stuck) + 1 < len(declared) \
        else "success"
    return (f"{reach[stuck]}/{total} episodes reached '{stuck}', none reached "
            f"'{nxt}' — the game is stuck between '{stuck}' and '{nxt}'")


# ======================================================================== #
# Orchestration
# ======================================================================== #
def verify_game(game_path: str, sandboxed: bool = True, *, world_factory=None) -> dict:
    """Run the G0 -> G1 -> G2 -> G3 funnel on the game at `game_path`.

    sandboxed=True  : run in an isolated subprocess (prod default; the "gameverify"
                      job re-enters this function with sandboxed=False).
    sandboxed=False : in-process. `world_factory(seed=0) -> World` is injectable
                      for tests (default = harness.world.World).
    """
    if sandboxed:
        from harness.core.sandbox import run_sandboxed
        # Game verification legitimately includes the G3 probe (tens of thousands
        # of physics steps) plus a Windows spawn re-import; the legacy 20 s default
        # times out under machine load and yields an error-shaped report with no
        # hint — which would make repair loops run blind. [eng.]
        return run_sandboxed(game_path, "gameverify", timeout_s=GAMEVERIFY_TIMEOUT_S)

    report = make_report()
    factory = world_factory or _default_world_factory

    try:
        with open(game_path, "r", encoding="utf-8") as fh:
            source = fh.read()
    except OSError as exc:
        report["failure_class"] = "ENV_ERROR"
        report["hint"] = f"game unreadable: {exc}"
        return report

    # --- G0 ---
    g0, game = run_g0(factory, source)
    report["layers"]["G0_static"] = g0
    if not g0["passed"]:
        report["failure_class"] = "ENV_ERROR"
        report["hint"] = _hint_g0(g0["checks"])
        return report

    # --- G1 ---
    try:
        g1 = run_g1(factory, game)
    except Exception:
        g1 = {"passed": False, "checks": {"crash": check(False, error=traceback.format_exc(limit=3))}}
    report["layers"]["G1_rollout"] = g1
    if not g1["passed"]:
        report["failure_class"] = "ENV_ERROR"
        report["hint"] = _hint_g1(g1["checks"])
        return report

    # --- G2 ---
    try:
        g2 = run_g2(factory, game)
    except Exception:
        g2 = {"passed": False, "checks": {"crash": check(False, error=traceback.format_exc(limit=3))}}
    report["layers"]["G2_goal"] = g2
    if not g2["passed"]:
        report["failure_class"] = "GOAL_ERROR"
        report["hint"] = _hint_g2(g2["checks"])
        return report

    # --- G3 ---
    try:
        g3 = run_g3(factory, game)
    except Exception:
        g3 = {"passed": False, "checks": {"crash": check(False, error=traceback.format_exc(limit=3))}}
    report["layers"]["G3_solve"] = g3
    report["witness"] = g3.get("witness")
    report["warnings"].extend(g3.get("warnings") or [])
    if not g3["passed"]:
        checks3 = g3["checks"]
        # Trivial goal and dead milestones are degenerate-GOAL failures;
        # genuinely unreached success is UNSOLVED (with the progress diagnosis).
        if not checks3.get("non_trivial", {"pass": True}).get("pass", True):
            report["failure_class"] = "GOAL_ERROR"
        elif not checks3.get("milestones_latched", {"pass": True}).get("pass", True):
            report["failure_class"] = "GOAL_ERROR"
        else:
            report["failure_class"] = "UNSOLVED"
            report["progress"] = g3.get("progress")
        report["hint"] = _hint_g3(checks3, g3)
        return report

    report["passed"] = True
    report["failure_class"] = None
    report["hint"] = (f"valid game: static sane, deterministic rollout with live actions, "
                      f"well-formed goal, solved in {report['witness']['ticks']} decision ticks.")
    return report
