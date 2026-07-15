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

import math
import os
import random
import re
import traceback

from harness.core.sandbox import (
    SandboxViolation, load_scene_namespace, scan_source,
)
from harness.verify.executors import (
    GodotExecutor, JsExecutor, PyExecutor, VerifyError,
)

# --- Constants ([eng.] = engineering choice to calibrate) ---------------- #
K_STEPS = 6                 # physics steps per decision tick (CONTRACTS §2)
GAMEVERIFY_TIMEOUT_S = 480  # sandbox subprocess budget for a full G0-G3 run [eng.]
                            # (raised with TICK_BUDGET 21k->63k, 2026-07-15 - an
                            # UNSOLVED search now legitimately runs ~3x longer)

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
PROBE_HORIZON = 300         # decision ticks per episode (~30s of play; RL-scale levels) [eng.]
MACRO_MIN, MACRO_MAX = 1, 4  # macro-action hold length (ticks) [eng.]
WORLD_SEED = 0              # physics seed shared by every fresh world [eng.]
WORLD_W_BOUNDS = (800, 2400)   # declared WORLD_SIZE width bounds (px) [eng.]
WORLD_H_BOUNDS = (600, 1600)   # declared WORLD_SIZE height bounds (px) [eng.]
TRIVIAL_TICKS = 20          # a witness shorter than this marks a degenerate goal (~2s of decisions; v2.3 raised from 5) [eng.]
SINGLE_ACTION_HORIZON = PROBE_HORIZON  # hold each action this long for the single-action probe [eng.]
SOLIDITY_FRAC = 0.5         # witness replay: max solid-pair overlap depth as a fraction of the thinner body's smaller bbox dimension [eng.]
SOLIDITY_TICKS = 2          # consecutive sampled ticks the overlap must persist to fail (transient impact slop is physics) [eng.]
GUIDED_EPISODES = 30        # checkpoint-guided second-pass episodes (longer horizons need more) [eng.]
GUIDED_SEED_BASE = 1000     # probe seeds 1000+i for the guided pass (v2.1)

# G3 solver selection (v2.4): the Go-Explore state-action tree is the default —
# it chains precise stages random search cannot (see harness/verify/treesolve.py).
# The legacy random path stays intact and selectable. Override per call with the
# HARNESS_G3_SOLVER env var ("random" | "tree"), read at call time.
G3_SOLVER = "tree"


def _g3_solver() -> str:
    """Active G3 solver: HARNESS_G3_SOLVER env override, else the module default."""
    return os.environ.get("HARNESS_G3_SOLVER", G3_SOLVER).strip().lower() or G3_SOLVER


def _run_g3(executor, game_source, actions, declared):
    """Dispatch to the tree solver (default) or the legacy random search."""
    if _g3_solver() == "random":
        return run_g3(executor, game_source, actions, declared)
    from harness.verify.treesolve import run_g3_tree
    return run_g3_tree(executor, game_source, actions, declared)


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
        self.world_size = ns.get("WORLD_SIZE")    # optional (v2.3; G0 validates bounds)


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
def _default_world_factory(seed: int = 0, size=(800, 600)):
    """Real World (module E), imported lazily so tests can inject a fake."""
    from harness.core.world import World
    return World(seed=seed, size=size)


def _world_size_of(game) -> tuple | None:
    """The game's declared WORLD_SIZE as a (w, h) tuple if it is shaped like
    one (bounds are G0's job), else None (default world)."""
    ws = getattr(game, "world_size", None)
    if (isinstance(ws, (list, tuple)) and len(ws) == 2
            and all(isinstance(v, (int, float)) and v > 0 for v in ws)):
        return (ws[0], ws[1])
    return None


def _fresh(factory, game: Game):
    """A fresh world with the game built into it. Raises on build failure."""
    size = _world_size_of(game)
    world = factory(seed=WORLD_SEED, size=size) if size else factory(seed=WORLD_SEED)
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

    # Declared WORLD_SIZE (optional) is well-shaped and within bounds.
    ws_ok, ws_detail = _world_size_check(game.world_size)
    checks["world_size"] = check(ws_ok, **ws_detail)
    if not ws_ok:
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


def _world_size_check(declared) -> tuple[bool, dict]:
    """Validate an optional declared WORLD_SIZE: None passes with the default;
    otherwise it must be a 2-sequence of numbers within the engineering bounds."""
    if declared is None:
        return True, {"declared": None, "effective": [800, 600]}
    shaped = (isinstance(declared, (list, tuple)) and len(declared) == 2
              and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                      for v in declared))
    if not shaped:
        return False, {"declared": repr(declared), "error": "not a [w, h] pair"}
    w, h = declared
    in_bounds = (WORLD_W_BOUNDS[0] <= w <= WORLD_W_BOUNDS[1]
                 and WORLD_H_BOUNDS[0] <= h <= WORLD_H_BOUNDS[1])
    if not in_bounds:
        return False, {"declared": [w, h],
                       "bounds": [list(WORLD_W_BOUNDS), list(WORLD_H_BOUNDS)]}
    return True, {"declared": [w, h], "effective": [w, h]}


# ======================================================================== #
# G1 — noop rollout, agency, determinism, action efficacy
# ======================================================================== #
def run_g1(executor, game_source, actions):
    """G1 rollout via an executor (CONTRACTS §4). Engine-agnostic: reads the
    episode dicts the executor returns (``final_snapshot`` + the ``nan``/``oob``
    extras the ``escape_margin`` rollout carries). With ``PyExecutor`` this is a
    byte-for-byte refactor of the pre-seam in-process funnel."""
    layer = {"passed": False, "checks": {}}
    checks = layer["checks"]
    actions = list(actions or [])
    noop_spec = {"seed": WORLD_SEED, "actions": [None] * NOOP_TICKS}

    # --- Full noop rollout: agency + NaN + escape (extras via escape_margin) ---
    noop = executor.run_batch(game_source, [noop_spec], NOOP_TICKS,
                              escape_margin=ESCAPE_MARGIN)[0]
    checks["no_nan"] = check(noop["result"] != "error" and not noop.get("nan", False),
                             result=noop["result"])

    escaped = list(noop.get("oob", []))
    checks["no_escape"] = check(not escaped, offenders=escaped)

    # Agency: success must never fire with zero actions.
    checks["agency"] = check(noop["result"] != "success", result=noop["result"])

    # --- Determinism: two fresh seeded worlds, identical noop rollout ---
    r1, r2 = executor.run_batch(game_source, [dict(noop_spec), dict(noop_spec)],
                                NOOP_TICKS)
    delta = _snapshot_delta(r1["final_snapshot"], r2["final_snapshot"])
    checks["determinism"] = check(delta <= DETERMINISM_EPS, delta=_round_inf(delta))

    # --- Action efficacy: each declared action must move the world ---
    eff_specs = [{"seed": WORLD_SEED, "actions": [None] * EFFICACY_TICKS}]
    eff_specs += [{"seed": WORLD_SEED, "actions": [a] * EFFICACY_TICKS} for a in actions]
    eff = executor.run_batch(game_source, eff_specs, EFFICACY_TICKS)
    baseline = eff[0]["final_snapshot"]
    dead = []
    effect = {}
    for action, rec in zip(actions, eff[1:]):
        d = _snapshot_delta(rec["final_snapshot"], baseline)
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
def _collect_pass(executor, game_source, specs, seeds, horizon, episodes):
    """Run a probe pass and append every finished episode to `episodes`, stopping
    (and returning a witness) at the first success.

    A batched executor (JS) runs the WHOLE pass in one process, then we walk the
    records in order — appending up to (and including) the first success — so the
    resulting `episodes` list and witness are identical to the per-episode,
    early-stopping stream a non-batched executor (Py) produces."""
    seeds = list(seeds)
    if executor.batched:
        recs = executor.run_batch(game_source, specs, horizon)
        for seed, ep in zip(seeds, recs):
            episodes.append(ep)
            if ep["result"] == "success":
                return _make_witness(seed, ep)
        return None
    for seed, spec in zip(seeds, specs):
        ep = executor.run_batch(game_source, [spec], horizon)[0]
        episodes.append(ep)
        if ep["result"] == "success":
            return _make_witness(seed, ep)
    return None


def run_g3(executor, game_source, actions, declared):
    """Random macro-action search -> witness, UNSOLVED, or a trivial goal, driven
    by an executor (CONTRACTS §4). All the diagnostic logic (dead milestones,
    order mismatch, guided second pass, progress) is pure and engine-agnostic —
    it eats the returned episode dicts and never touches the engine.

    v2.1: each episode's checkpoint latches feed (a) the dead-milestone check
    on the witness, (b) a declared-vs-empirical order warning, (c) a guided
    second pass reusing the best failed episode's action prefix, and (d) the
    UNSOLVED progress diagnosis.
    """
    layer = {"passed": False, "checks": {}, "warnings": [], "progress": None}
    checks = layer["checks"]
    actions = list(actions or [])
    episodes: list[dict] = []      # every finished (non-witness) episode's data

    # --- First pass: pure random macro-action search ---
    specs = [{"seed": WORLD_SEED,
              "actions": _macro_plan(random.Random(e), actions, PROBE_HORIZON)}
             for e in range(PROBE_EPISODES)]
    witness = _collect_pass(executor, game_source, specs, range(PROBE_EPISODES),
                            PROBE_HORIZON, episodes)

    # --- Checkpoint-guided second pass (v2.1) ---
    # If pure random failed but some episode latched >= 1 milestone, replay the
    # best episode's prefix (up to its last latch tick) + random continuation.
    guided_ran = False
    if witness is None:
        prefix = _best_prefix(episodes, declared)
        if prefix:
            guided_ran = True
            gseeds = [GUIDED_SEED_BASE + i for i in range(GUIDED_EPISODES)]
            gspecs = [{"seed": WORLD_SEED,
                       "actions": prefix + _macro_plan(random.Random(s), actions,
                                                       PROBE_HORIZON - len(prefix))}
                      for s in gseeds]
            witness = _collect_pass(executor, game_source, gspecs, gseeds,
                                    PROBE_HORIZON, episodes)

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
    # (frames_every=1 so the same replay feeds the solidity scan below)
    replay = executor.run_batch(game_source,
                                [{"seed": WORLD_SEED, "actions": witness["actions"]}],
                                len(witness["actions"]), frames_every=1)[0]
    checks["replayable"] = check(replay["result"] == "success", result=replay["result"])

    # --- Solidity: no sustained deep interpenetration on the winning path ---
    worst = _solidity_scan(replay.get("frames", []))
    checks["solidity"] = check(worst is None, **(worst or {}))

    layer["passed"] = all(c["pass"] for c in checks.values())
    return layer


def _make_witness(seed: int, ep: dict) -> dict:
    return {"seed": seed, "actions": ep["actions"], "ticks": ep["ticks"],
            "checkpoints": dict(ep.get("checkpoints", {}))}


def _aabb_reliable(q: dict) -> bool:
    """AABB overlap depth is meaningful only for circles and axis-aligned boxes.

    Polys (ramps!) and rotated boxes inflate their AABB: a ball riding a 45deg
    ramp reads as ~100% 'inside' the ramp's bounding box on honest contact.
    Such bodies are excluded from the solidity scan rather than producing
    false rejections (observed: a legal poly ramp flagged at 52px depth)."""
    shape = q.get("shape", "box")
    if shape == "circle":
        return True
    if shape != "box":
        return False
    ang = float(q.get("angle") or 0.0)
    a = ang % (math.pi / 2.0)
    return min(a, math.pi / 2.0 - a) < 0.10  # within ~5.7deg of axis-aligned


def _solidity_scan(frames: list) -> dict | None:
    """Engine-agnostic solidity scan over a frames list ({tick, entities:{query}}).

    Flags SUSTAINED deep interpenetration between two non-sensor bodies (at
    least one dynamic): AABB overlap depth > SOLIDITY_FRAC x the thinner body's
    smaller bbox dimension, persisting >= SOLIDITY_TICKS consecutive sampled
    frames. A body sitting half inside another on the WINNING path means the
    game plays as "passes through obstacles"; one-frame solver slop under an
    impact does not. Returns the worst offender or None."""
    runs: dict = {}
    worst: dict | None = None
    for fr in frames:
        ents = fr.get("entities", {})
        names = [n for n, q in ents.items()
                 if not q.get("sensor") and q.get("bbox") and _aabb_reliable(q)]
        over_now = set()
        for i, a in enumerate(names):
            qa = ents[a]
            for b in names[i + 1:]:
                qb = ents[b]
                if qa.get("static") and qb.get("static"):
                    continue
                ba, bb = qa["bbox"], qb["bbox"]
                ox = min(ba[2], bb[2]) - max(ba[0], bb[0])
                oy = min(ba[3], bb[3]) - max(ba[1], bb[1])
                if ox <= 0.0 or oy <= 0.0:
                    continue
                depth = min(ox, oy)
                thin = min(ba[2] - ba[0], ba[3] - ba[1], bb[2] - bb[0], bb[3] - bb[1])
                if thin < 1.0:  # zero-thickness segments: AABB depth is meaningless
                    continue
                if depth <= SOLIDITY_FRAC * thin:
                    continue
                pair = (a, b)
                over_now.add(pair)
                runs[pair] = runs.get(pair, 0) + 1
                if runs[pair] >= SOLIDITY_TICKS:
                    frac = depth / thin
                    if worst is None or frac > worst["frac"]:
                        worst = {"pair": [a, b], "depth": round(depth, 1),
                                 "frac": round(frac, 2), "tick": fr.get("tick")}
        for pair in [p for p in runs if p not in over_now]:
            del runs[pair]
    return worst


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
    if not checks.get("world_size", {"pass": True}).get("pass", True):
        c = checks["world_size"]
        if c.get("error"):
            return f"WORLD_SIZE malformed ({c.get('declared')}): {c['error']}"
        return (f"WORLD_SIZE {c.get('declared')} out of bounds — width "
                f"{WORLD_W_BOUNDS[0]}..{WORLD_W_BOUNDS[1]}, height "
                f"{WORLD_H_BOUNDS[0]}..{WORLD_H_BOUNDS[1]}")
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
    if not checks.get("solidity", {"pass": True}).get("pass", True):
        c = checks["solidity"]
        a, b = c.get("pair", ["?", "?"])
        pct = int(100 * (c.get("frac") or 0))
        return (f"solid bodies interpenetrate on the winning path: {a} sits "
                f"{c.get('depth')}px inside {b} (~{pct}% of the thinner body) around "
                f"tick {c.get('tick')} — reduce impulse/velocity magnitudes, enlarge "
                f"or slow the bodies so collisions stay coherent")
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
# JS engine: G0/G2 layers over the runner's "check" facts
# ======================================================================== #
# The JS runner (nodeworld/runner.js "check" mode) returns RAW engine facts;
# these layers apply the SAME thresholds/gating/formatting the pymunk G0/G2 use,
# so the report `checks` dicts — and hence `_hint_g0`/`_hint_g2` — are identical
# across engines. Thresholds stay here in Python (CONTRACTS §4).

def run_g0_js(facts: dict) -> dict:
    """G0 static layer for a JS game, from the runner's check facts."""
    layer = {"passed": False, "checks": {}}
    checks = layer["checks"]

    violations = list(facts.get("scan", []) or [])
    checks["sandbox_scan"] = check(not violations, violations=violations)
    if violations:
        return layer

    load = facts.get("load") or {}
    if not load.get("ok"):
        checks["loads"] = check(False, error=load.get("error", "load failed"))
        return layer
    checks["loads"] = check(True)

    symbols = facts.get("symbols") or {}
    defined = symbols.get("defined", {})
    is_callable = symbols.get("callable", {})
    missing = [s for s in _REQUIRED_SYMBOLS if not defined.get(s)]
    not_callable = [s for s in _CALLABLE_SYMBOLS
                    if defined.get(s) and not is_callable.get(s)]
    checks["symbols"] = check(not missing and not not_callable,
                              missing=missing, not_callable=not_callable)
    if missing or not_callable:
        return layer

    actions = facts.get("actions") or {}
    n = actions.get("length")
    actions_ok = (bool(actions.get("is_list")) and isinstance(n, int) and n
                  and bool(actions.get("all_str"))
                  and MIN_ACTIONS <= n <= MAX_ACTIONS)
    checks["actions"] = check(bool(actions_ok),
                              n=n if actions.get("is_list") else None)
    if not actions_ok:
        return layer

    ws = facts.get("world_size") or {}
    ws_ok, ws_detail = _world_size_check(ws.get("declared"))
    checks["world_size"] = check(ws_ok, **ws_detail)
    if not ws_ok:
        return layer

    build = facts.get("build") or {}
    if not build.get("ok"):
        checks["builds"] = check(False, error=build.get("error", "build failed"))
        return layer
    checks["builds"] = check(True)

    entities = list(facts.get("entities", []) or [])
    queries = facts.get("queries") or {}

    controlled = [name for name in entities if queries.get(name, {}).get("controlled")]
    one_controlled = (len(controlled) == 1
                      and not queries.get(controlled[0], {}).get("static", False))
    checks["controlled"] = check(one_controlled, controlled=controlled)

    checks["counts"] = check(len(entities) >= MIN_ENTITIES, n=len(entities))

    offenders = [[a, b, round(float(d), 3)]
                 for a, b, d in (facts.get("penetration") or [])
                 if float(d) > PEN_INIT_TOL]
    checks["no_penetration"] = check(not offenders, offenders=offenders)

    oob = [name for name in entities
           if not queries.get(name, {}).get("static", False)
           and not queries.get(name, {}).get("in_bounds", True)]
    checks["in_bounds"] = check(not oob, offenders=oob)

    layer["passed"] = all(c["pass"] for c in checks.values())
    return layer


# ======================================================================== #
# GDScript engine: G0 static layer over the serve host's "check" facts
# ======================================================================== #
# The GDScript lane (engine=gdscript) is the NEW static species (GDSCRIPT_LANE.md):
# generated CODE, not data. Its G0 fuses the three code gates —
#   (b) the python-side banned-API scan  (harness/verify/gd_gate.scan_gd_source),
#   (a) the parse gate                    (facts["load"], serve_game.gd's compile),
#   (c) the contract probe                (facts["contract"].methods, has_method) —
# with the SAME structural checks the data lanes use (actions 2..8, one controlled
# dynamic body, >=2 bodies, in bounds). The check keys mirror run_g0_js so
# `_hint_g0` renders identical hints across engines.

def run_g0_gd(facts: dict, violations) -> dict:
    """G0 static layer for a GDScript (GameAPI) game.

    ``violations`` is the python banned-API scan result (list of strings); ``facts``
    is the serve host's ``check`` payload (parse gate + contract probe + t=0 facts).
    Stops at the first failing gate, so the code never runs if the scan or parse
    gate rejects it."""
    from harness.verify.gd_gate import GD_REQUIRED_METHODS
    layer = {"passed": False, "checks": {}}
    checks = layer["checks"]

    # (b) banned-API scan (a hard fail; the code was NOT compiled/run).
    violations = list(violations or [])
    checks["sandbox_scan"] = check(not violations, violations=violations)
    if violations:
        return layer

    # (a) parse gate: the headless in-memory compile-check.
    load = facts.get("load") or {}
    if not load.get("ok"):
        checks["loads"] = check(False, error=load.get("error", "parse/compile failed"))
        return layer
    checks["loads"] = check(True)

    # (c) contract probe: every required GameAPI method is present.
    methods = (facts.get("contract") or {}).get("methods") or {}
    missing = [m for m in GD_REQUIRED_METHODS if not methods.get(m)]
    checks["symbols"] = check(not missing, missing=missing, not_callable=[])
    if missing:
        return layer

    # actions() is a list[str] of size 2..8.
    actions = facts.get("actions") or {}
    n = actions.get("length")
    actions_ok = (bool(actions.get("is_list")) and isinstance(n, int) and n
                  and bool(actions.get("all_str"))
                  and MIN_ACTIONS <= n <= MAX_ACTIONS)
    checks["actions"] = check(bool(actions_ok),
                              n=n if actions.get("is_list") else None)
    if not actions_ok:
        return layer

    ws = facts.get("world_size") or {}
    ws_ok, ws_detail = _world_size_check(ws.get("declared"))
    checks["world_size"] = check(ws_ok, **ws_detail)
    if not ws_ok:
        return layer

    build = facts.get("build") or {}
    if not build.get("ok"):
        checks["builds"] = check(False, error=build.get("error", "build failed"))
        return layer
    checks["builds"] = check(True)

    entities = list(facts.get("entities", []) or [])
    queries = facts.get("queries") or {}

    controlled = [name for name in entities if queries.get(name, {}).get("controlled")]
    one_controlled = (len(controlled) == 1
                      and not queries.get(controlled[0], {}).get("static", False))
    checks["controlled"] = check(one_controlled, controlled=controlled)

    checks["counts"] = check(len(entities) >= MIN_ENTITIES, n=len(entities))

    offenders = [[a, b, round(float(d), 3)]
                 for a, b, d in (facts.get("penetration") or [])
                 if float(d) > PEN_INIT_TOL]
    checks["no_penetration"] = check(not offenders, offenders=offenders)

    oob = [name for name in entities
           if not queries.get(name, {}).get("static", False)
           and not queries.get(name, {}).get("in_bounds", True)]
    checks["in_bounds"] = check(not oob, offenders=oob)

    layer["passed"] = all(c["pass"] for c in checks.values())
    return layer


def run_g2_js(g2: dict) -> dict:
    """G2 goal layer for a JS game, from the runner's check facts."""
    layer = {"passed": False, "checks": {}}
    checks = layer["checks"]

    ok = _g2js_predicate(g2.get("success") or {}, "success", checks)
    failure = g2.get("failure")
    if ok and failure is not None:
        ok = _g2js_predicate(failure, "failure", checks)
    if ok:
        _g2js_checkpoints(g2.get("checkpoints") or {}, checks)

    layer["passed"] = all(c["pass"] for c in checks.values())
    return layer


def _g2js_predicate(facts: dict, label: str, checks: dict) -> bool:
    if facts.get("error"):
        checks[f"{label}_callable_bool"] = check(False, error=facts["error"])
        return False
    is_bool = bool(facts.get("is_bool"))
    value = facts.get("value")
    checks[f"{label}_callable_bool"] = check(is_bool, value=bool(value) if is_bool else None)
    if not is_bool:
        return False
    checks[f"{label}_false_at_t0"] = check(not value, value=bool(value))
    pure = bool(facts.get("deterministic")) and bool(facts.get("state_unchanged"))
    checks[f"{label}_pure"] = check(pure, deterministic=bool(facts.get("deterministic")),
                                    state_unchanged=bool(facts.get("state_unchanged")))
    return all(checks[f"{label}_{k}"]["pass"]
               for k in ("callable_bool", "false_at_t0", "pure")
               if f"{label}_{k}" in checks)


def _g2js_checkpoints(facts: dict, checks: dict) -> bool:
    if facts.get("error"):
        checks["checkpoints_wellformed"] = check(False, error=facts["error"])
        return False
    is_dict = bool(facts.get("is_dict"))
    keys = list(facts.get("keys", []) or [])
    n = facts.get("n")
    bad_keys = ([k for k in keys if not (isinstance(k, str) and _SNAKE_CASE.match(k))]
                if is_dict else [])
    non_bool = list(facts.get("non_bool_keys", []) or []) if is_dict else []
    wellformed = (is_dict and isinstance(n, int) and CP_MIN <= n <= CP_MAX
                  and not bad_keys and not non_bool)
    checks["checkpoints_wellformed"] = check(wellformed, n=n, bad_keys=bad_keys,
                                             non_bool=non_bool)
    if not wellformed:
        return False

    true_at_t0 = list(facts.get("true_keys", []) or [])
    checks["checkpoints_false_at_t0"] = check(not true_at_t0, offenders=true_at_t0)

    pure = bool(facts.get("deterministic")) and bool(facts.get("state_unchanged"))
    checks["checkpoints_pure"] = check(pure, deterministic=bool(facts.get("deterministic")),
                                       state_unchanged=bool(facts.get("state_unchanged")))
    return not true_at_t0 and pure


# ======================================================================== #
# Engine detection
# ======================================================================== #
_JS_MARKER = re.compile(r"(?m)^\s*(?:#|//)\s*engine\s*:\s*js\b")
# The Godot lane's artifact is a declarative JSON spec (godotworld/SPEC.md): a
# `.spec.json` path, or JSON carrying a top-level `"engine": "godot"` marker.
_GODOT_MARKER = re.compile(r'"engine"\s*:\s*"godot"')
# The GDScript lane's artifact is a `.gd` game extending GameAPI (godotworld/
# GAME_API.md): a `.gd` path, or an `# engine: gdscript` marker.
_GD_MARKER = re.compile(r"(?m)^\s*#\s*engine\s*:\s*gdscript\b")


def detect_engine(game_path: str, source: str = "") -> str:
    """Game engine: 'gdscript' for a `.gd` path or an `# engine: gdscript` marker;
    'godot' for a `.spec.json` path or an `"engine":"godot"` JSON marker; 'js' for a
    `.js` path or an `# engine: js` / `// engine: js` marker; otherwise 'py'."""
    path = str(game_path).lower()
    if path.endswith(".gd") or _GD_MARKER.search(source or ""):
        return "gdscript"
    if path.endswith(".spec.json") or _GODOT_MARKER.search(source or ""):
        return "godot"
    if path.endswith(".js"):
        return "js"
    if _JS_MARKER.search(source or ""):
        return "js"
    return "py"


def gdscript_route_available() -> bool:
    """Whether the GDScript verify route — the G0 code-gates + serve-contract
    executor (TRACK C) — is importable in this build. The generator writes `.gd`
    games regardless; this only tells callers/tests whether the FULL G0-G3 funnel
    can run yet, so a template-backend e2e can skip gracefully until TRACK C merges."""
    try:
        import harness.verify.gd_exec  # noqa: F401  (TRACK C: the serve-contract executor)
        return True
    except ImportError:
        return False


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
    try:
        with open(game_path, "r", encoding="utf-8") as fh:
            source = fh.read()
    except OSError as exc:
        report["failure_class"] = "ENV_ERROR"
        report["hint"] = f"game unreadable: {exc}"
        return report

    # Route by game engine. The pymunk (py) path is unchanged; the Planck (js) and
    # Godot (declarative-spec) paths run the SAME funnel over their executor + the
    # runner's check facts.
    engine = detect_engine(game_path, source)
    if engine == "gdscript":
        return _verify_gdscript(source, report)
    if engine == "godot":
        return _verify_godot(source, report)
    if engine == "gdscript":
        return _verify_gdscript(source, report)
    if engine == "js":
        return _verify_js(source, report)
    return _verify_py(source, report, world_factory)


def _verify_py(source: str, report: dict, world_factory) -> dict:
    """The pymunk funnel: G0/G2 in-process; G1/G3 through a PyExecutor (a pure,
    byte-identical refactor of the pre-seam in-process path)."""
    factory = world_factory or _default_world_factory
    executor = PyExecutor(world_factory=factory)

    # --- G0 ---
    g0, game = run_g0(factory, source)
    report["layers"]["G0_static"] = g0
    if not g0["passed"]:
        report["failure_class"] = "ENV_ERROR"
        report["hint"] = _hint_g0(g0["checks"])
        return report

    # --- G1 ---
    try:
        g1 = run_g1(executor, game, game.actions)
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
    declared = _declared_order(factory, game)
    try:
        g3 = _run_g3(executor, game, game.actions, declared)
    except Exception:
        g3 = {"passed": False, "checks": {"crash": check(False, error=traceback.format_exc(limit=3))}}
    return _finish_g3(report, g3)


def _verify_js(source: str, report: dict) -> dict:
    """The Planck (Node) funnel: G0/G2 from the runner's check facts, G1/G3 through
    a JsExecutor (one node process per layer batch). Adds "engine": "js"."""
    report["engine"] = "js"
    executor = JsExecutor()
    try:
        # G0 + G2 facts come from ONE "check" job.
        facts = executor.run_check(source)

        g0 = run_g0_js(facts)
        report["layers"]["G0_static"] = g0
        if not g0["passed"]:
            report["failure_class"] = "ENV_ERROR"
            report["hint"] = _hint_g0(g0["checks"])
            return report

        actions = (facts.get("actions") or {}).get("values") or []
        declared = list(((facts.get("g2") or {}).get("checkpoints") or {}).get("keys", []))

        # --- G1 ---
        try:
            g1 = run_g1(executor, source, actions)
        except VerifyError:
            raise
        except Exception:
            g1 = {"passed": False, "checks": {"crash": check(False, error=traceback.format_exc(limit=3))}}
        report["layers"]["G1_rollout"] = g1
        if not g1["passed"]:
            report["failure_class"] = "ENV_ERROR"
            report["hint"] = _hint_g1(g1["checks"])
            return report

        # --- G2 ---
        g2 = run_g2_js((facts.get("g2") or {}))
        report["layers"]["G2_goal"] = g2
        if not g2["passed"]:
            report["failure_class"] = "GOAL_ERROR"
            report["hint"] = _hint_g2(g2["checks"])
            return report

        # --- G3 ---
        try:
            g3 = _run_g3(executor, source, actions, declared)
        except VerifyError:
            raise
        except Exception:
            g3 = {"passed": False, "checks": {"crash": check(False, error=traceback.format_exc(limit=3))}}
        return _finish_g3(report, g3)
    except VerifyError as exc:
        # Node missing / crash / timeout / unparseable output -> VERIFY_ERROR
        # shape (no funnel layers), exactly like sandbox.run_sandboxed trouble.
        return exc.as_report()


def _verify_godot(source: str, report: dict) -> dict:
    """The Godot (declarative-spec) funnel: a line-for-line twin of ``_verify_js``.
    G0/G2 come from the FROZEN runner.gd's "check" facts (reusing ``run_g0_js`` /
    ``run_g2_js`` verbatim — the runner emits the SAME fact shape as runner.js);
    G1/G3 batch through a ``GodotExecutor``. The tree solver runs unchanged (it only
    needs ``run_batch``). Adds ``"engine": "godot"``."""
    report["engine"] = "godot"
    executor = GodotExecutor()
    try:
        # G0 + G2 facts come from ONE "check" job.
        facts = executor.run_check(source)

        g0 = run_g0_js(facts)
        report["layers"]["G0_static"] = g0
        if not g0["passed"]:
            report["failure_class"] = "ENV_ERROR"
            report["hint"] = _hint_g0(g0["checks"])
            return report

        actions = (facts.get("actions") or {}).get("values") or []
        declared = list(((facts.get("g2") or {}).get("checkpoints") or {}).get("keys", []))

        # --- G1 ---
        try:
            g1 = run_g1(executor, source, actions)
        except VerifyError:
            raise
        except Exception:
            g1 = {"passed": False, "checks": {"crash": check(False, error=traceback.format_exc(limit=3))}}
        report["layers"]["G1_rollout"] = g1
        if not g1["passed"]:
            report["failure_class"] = "ENV_ERROR"
            report["hint"] = _hint_g1(g1["checks"])
            return report

        # --- G2 ---
        g2 = run_g2_js((facts.get("g2") or {}))
        report["layers"]["G2_goal"] = g2
        if not g2["passed"]:
            report["failure_class"] = "GOAL_ERROR"
            report["hint"] = _hint_g2(g2["checks"])
            return report

        # --- G3 ---
        try:
            g3 = _run_g3(executor, source, actions, declared)
        except VerifyError:
            raise
        except Exception:
            g3 = {"passed": False, "checks": {"crash": check(False, error=traceback.format_exc(limit=3))}}
        return _finish_g3(report, g3)
    except VerifyError as exc:
        # Godot binary missing / crash / timeout / unparseable output -> VERIFY_ERROR
        # shape (no funnel layers), exactly like sandbox.run_sandboxed trouble.
        return exc.as_report()


def _verify_gdscript(source: str, report: dict) -> dict:
    """The GDScript (GameAPI) funnel: G0 fuses the python banned-API scan + the serve
    host's parse gate + contract probe (``run_g0_gd``); G1/G3 batch through a
    ``GdExecutor`` over ``serve_game.gd``; G2 reuses ``run_g2_js`` on the host's t=0
    goal facts (is_success/is_failure/checkpoints share the JS check shape). The code
    is NEVER compiled or run until the static scan passes. Adds ``"engine": "gdscript"``."""
    from harness.verify.gd_exec import GdExecutor
    from harness.verify.gd_gate import scan_violations
    report["engine"] = "gdscript"

    # (b) banned-API scan FIRST — a hard fail short-circuits BEFORE any Godot spawn,
    # so unscanned code is never compiled or executed.
    violations = scan_violations(source)
    if violations:
        g0 = run_g0_gd({}, violations)
        report["layers"]["G0_static"] = g0
        report["failure_class"] = "ENV_ERROR"
        report["hint"] = _hint_g0(g0["checks"])
        return report

    executor = GdExecutor()
    try:
        # G0 (parse gate + contract probe + structural) + G2 facts: ONE check op.
        facts = executor.run_check(source)

        g0 = run_g0_gd(facts, [])
        report["layers"]["G0_static"] = g0
        if not g0["passed"]:
            report["failure_class"] = "ENV_ERROR"
            report["hint"] = _hint_g0(g0["checks"])
            return report

        actions = (facts.get("actions") or {}).get("values") or []
        declared = list(((facts.get("g2") or {}).get("checkpoints") or {}).get("keys", []))

        # --- G1 ---
        try:
            g1 = run_g1(executor, source, actions)
        except VerifyError:
            raise
        except Exception:
            g1 = {"passed": False, "checks": {"crash": check(False, error=traceback.format_exc(limit=3))}}
        report["layers"]["G1_rollout"] = g1
        if not g1["passed"]:
            report["failure_class"] = "ENV_ERROR"
            report["hint"] = _hint_g1(g1["checks"])
            return report

        # --- G2 ---
        g2 = run_g2_js((facts.get("g2") or {}))
        report["layers"]["G2_goal"] = g2
        if not g2["passed"]:
            report["failure_class"] = "GOAL_ERROR"
            report["hint"] = _hint_g2(g2["checks"])
            return report

        # --- G0.5: geometric reachability pre-filter (cheap, BEFORE the G3 solve) ---
        # A checkpoint/goal geometrically walled off from spawn is definitely unsolvable;
        # reject fast so G3 never burns its budget on it (Elias directive 1). Passing is
        # necessary-not-sufficient (dynamic solvability stays G3).
        g05 = _run_reachability(facts)
        report["layers"]["G0_5_reach"] = g05
        if not g05["passed"]:
            report["failure_class"] = "GOAL_ERROR"
            report["hint"] = g05.get("hint") or "a checkpoint/goal region is walled off"
            return report

        # --- G3 ---
        try:
            g3 = _run_g3(executor, source, actions, declared)
        except VerifyError:
            raise
        except Exception:
            g3 = {"passed": False, "checks": {"crash": check(False, error=traceback.format_exc(limit=3))}}
        report = _finish_g3(report, g3)

        # --- G3.5: single-action anti-triviality (Elias directive 3) ---
        # A cheap probe run as part of the gdscript verify feedback: a game winnable by
        # spamming ONE action is BROKEN -> flip to GOAL_ERROR with the repair hint the
        # generation loop consumes. Only worth running on an otherwise-certified game.
        if report.get("passed"):
            report = _single_action_gate(executor, source, actions, report)
        return report
    except VerifyError as exc:
        # Godot missing / spawn stale / crash / unparseable -> VERIFY_ERROR shape.
        return exc.as_report()
    finally:
        executor.close()


def _finish_g3(report: dict, g3: dict) -> dict:
    """Shared G3 finalisation + verdict classification (engine-agnostic)."""
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
        elif not checks3.get("solidity", {"pass": True}).get("pass", True):
            # Broken physics on the winning path is an ENVIRONMENT error.
            report["failure_class"] = "ENV_ERROR"
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


# ======================================================================== #
# Single-action anti-triviality probe (Elias directive 3)
# ======================================================================== #
# A game winnable by SPAMMING one action is a BROKEN game — success needs no varied
# play. G3's TRIVIAL_TICKS bar catches fast wins but not a slow one-action grind, so
# this cheap probe holds EACH declared action for the full horizon (one batch of
# len(actions) episodes) and rejects a certified game if any single action wins. It
# rides the SAME executor seam as G1/G3, so it is engine-agnostic; the gdscript funnel
# wires it after G3 so the generation repair loop gets an actionable hint.

def single_action_probe(executor, game_source, actions, horizon=SINGLE_ACTION_HORIZON):
    """Probe whether the game is winnable by repeating ONE action. Holds each declared
    action for `horizon` decision ticks (one batch) and returns the [(action, ticks),
    ...] that reach success — empty means no single action alone wins. Deterministic
    (WORLD_SEED); engine-agnostic (py/js/gdscript via the executor)."""
    actions = list(actions or [])
    if not actions:
        return []
    specs = [{"seed": WORLD_SEED, "actions": [a] * int(horizon)} for a in actions]
    recs = executor.run_batch(game_source, specs, int(horizon))
    wins = []
    for a, rec in zip(actions, recs):
        if rec.get("result") == "success":
            wins.append((a, int(rec.get("ticks", 0))))
    return wins


def _run_reachability(facts) -> dict:
    """G0.5 — geometric checkpoint/goal reachability pre-filter (Elias directive 1).

    A CHEAP static flood-fill run between the G0/G1/G2 gates and the expensive G3 tree
    solve. Reads the serve host's t=0 geometry facts, splits them into spawn / target
    regions / static occupancy, and asks whether a collision-free corridor plausibly
    exists from spawn to every target. NECESSARY-not-SUFFICIENT: a walled-off target is
    a fast, definite reject (GOAL_ERROR); PASSING does NOT prove dynamic solvability —
    that stays G3. If there is no occupancy geometry, no targets, or no spawn, there is
    nothing to prove walling-off with, so the layer passes and defers to G3."""
    from harness.verify.reachability import check_reachability, targets_and_occupancy
    layer = {"passed": True, "checks": {}, "hint": ""}
    bodies = facts.get("geometry") or []
    ws = (facts.get("world_size") or {}).get("declared") or [800, 600]
    spawn, clearance, targets, occ = targets_and_occupancy(bodies)
    if spawn is None or not occ or not targets:
        layer["checks"]["reachable"] = check(
            True, reason="no static occupancy / target regions / spawn to check")
        return layer
    res = check_reachability(spawn, targets, occ, ws, clearance=clearance)
    layer["checks"]["reachable"] = check(
        res["reachable"], unreachable=res["unreachable"], targets=res["targets"],
        dims=res["dims"], cells=list(res["cells"]) if res["cells"] else None)
    layer["passed"] = res["reachable"]
    layer["hint"] = res["detail"]
    return layer


def _single_action_hint(action, ticks) -> str:
    return ("BROKEN: your game is winnable by repeating a single action "
            f"({action!r} alone wins in {ticks} decision ticks) — add a real "
            "obstacle/choice so success needs varied play")


def _single_action_gate(executor, game_source, actions, report):
    """Run the single-action probe as part of the verify feedback and, on a win, flip
    the certified report to a GOAL_ERROR carrying the BROKEN repair hint (Elias
    directive 3). A clean probe records a passing G3 sub-check and leaves the
    certification intact. Engine trouble leaves the verdict untouched."""
    try:
        wins = single_action_probe(executor, game_source, actions, SINGLE_ACTION_HORIZON)
    except VerifyError:
        return report
    layer = report.setdefault("layers", {}).setdefault(
        "G3_solve", {"passed": True, "checks": {}})
    layer.setdefault("checks", {})["single_action"] = check(
        not wins, wins=[{"action": a, "ticks": t} for a, t in wins])
    if wins:
        a, t = wins[0]
        layer["passed"] = False
        report["passed"] = False
        report["failure_class"] = "GOAL_ERROR"
        report["hint"] = _single_action_hint(a, t)
    return report
