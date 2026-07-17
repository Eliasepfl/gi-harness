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

from harness.repair_language import PRESERVE_SHORT, REACHABILITY_FIXES
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
CONTEXT_BURST_TICKS = 8     # ticks of each OTHER action used to build a dynamic
                            # context before re-probing an action that looked dead
                            # at t=0 — so a brake is tested on a MOVING body, a drop
                            # while holding, etc. Small: this is a G1 gate, not a
                            # solver (cost stays at the old t=0 pass unless something
                            # is dead at rest) [eng.]
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

# G3.8 material-anchoring (ADVISORY). Evidence thresholds for the center-proximity check —
# NOT rule text (the contract carries no numbers). A milestone flip is "unanchored" when the
# controlled body sits farther than the tolerance from every reported body at the flip; the
# tolerance floors at ANCHOR_TOL_FLOOR px and otherwise scales with the occupancy diagonal.
ANCHOR_TOL_FLOOR = 24.0     # px: a flip within this of a real body is anchored regardless of arena size [eng.]
ANCHOR_TOL_FRAC = 0.06      # fraction of the occupancy-bounds diagonal that still counts as "at" a body [eng.]

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

    # --- Action efficacy: each declared action must move the world from SOME
    # context (not only t=0). Context-dependent actions — brake on a moving body,
    # drop/release/un-grab — are inert at rest but live once another action sets up
    # the dynamic state, so they are re-probed from a short burst of each OTHER
    # action before being called dead (2026-07-15 parking-game false positive). ---
    dead, effect, n_contexts = _action_efficacy(executor, game_source, actions)
    checks["efficacy"] = check(not dead, dead=dead, effect=effect,
                               contexts=n_contexts)

    layer["passed"] = all(c["pass"] for c in checks.values())
    return layer


def _action_efficacy(executor, game_source, actions):
    """Per-action efficacy over a small, deterministic set of contexts.

    An action is LIVE if, held for ``EFFICACY_TICKS``, it diverges the world from
    the otherwise-identical noop continuation of SOME probed context; dead only if
    it diverges in NONE. Contexts, in fixed order:

      (0) the initial state (t=0), and — only for actions that look dead there —
      (1..) the state left by a ``CONTEXT_BURST_TICKS`` burst of each OTHER action.

    The t=0 pass alone reproduces the pre-fix check, so a game whose actions are all
    live from rest pays exactly the old cost (no burst batch is run). Everything is
    seeded (``WORLD_SEED``) and iterated in declared order, so it is reproducible.

    Returns ``(dead, effect, n_contexts)``: ``effect`` maps EVERY declared action to
    its BEST (max) divergence across the probed contexts (``None`` for an infinite /
    body-set-changing divergence, as before), preserving the shape g4 reads;
    ``n_contexts`` is how many contexts a dead action was probed from (``1`` when the
    t=0 pass already cleared everything, else ``1 + #other actions``)."""
    actions = list(actions)

    # (0) t=0 pass: baseline noop + each action held from rest.
    t0_specs = [{"seed": WORLD_SEED, "actions": [None] * EFFICACY_TICKS}]
    t0_specs += [{"seed": WORLD_SEED, "actions": [a] * EFFICACY_TICKS} for a in actions]
    t0 = executor.run_batch(game_source, t0_specs, EFFICACY_TICKS)
    base0 = t0[0]["final_snapshot"]
    best = {a: _snapshot_delta(rec["final_snapshot"], base0)
            for a, rec in zip(actions, t0[1:])}

    dead0 = [a for a in actions if best[a] <= EFFICACY_EPS]
    if not dead0:
        return [], {a: _round_inf(best[a]) for a in actions}, 1

    # (1..) burst pass: re-probe each t=0-dead action from the state left by a short
    # burst of every OTHER action. One shared baseline per burst context (burst then
    # noop); each candidate is that same burst then the probed action held. Run as a
    # single batch (one subprocess for the out-of-process lanes).
    horizon = CONTEXT_BURST_TICKS + EFFICACY_TICKS
    base_specs = [{"seed": WORLD_SEED,
                   "actions": [b] * CONTEXT_BURST_TICKS + [None] * EFFICACY_TICKS}
                  for b in actions]
    cand_index = [(b, a) for b in actions for a in dead0 if a != b]
    cand_specs = [{"seed": WORLD_SEED,
                   "actions": [b] * CONTEXT_BURST_TICKS + [a] * EFFICACY_TICKS}
                  for (b, a) in cand_index]
    recs = executor.run_batch(game_source, base_specs + cand_specs, horizon)
    base_snaps = {b: recs[i]["final_snapshot"] for i, b in enumerate(actions)}
    for (b, a), rec in zip(cand_index, recs[len(actions):]):
        d = _snapshot_delta(rec["final_snapshot"], base_snaps[b])
        best[a] = max(best[a], d)

    n_contexts = 1 + max(0, len(actions) - 1)   # t=0 + one per OTHER action
    dead = [a for a in actions if best[a] <= EFFICACY_EPS]
    return dead, {a: _round_inf(best[a]) for a in actions}, n_contexts


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


def _runtime_error_finding(records) -> dict | None:
    """The first RUNTIME-kind SCRIPT ERROR record (else the first record) as a report
    finding, or ``None``. Runtime crashes are the ROOT cause the funnel would otherwise
    misreport as a downstream symptom, so they take priority over a parse-kind record."""
    records = list(records or [])
    if not records:
        return None
    for r in records:
        if r.get("kind") == "runtime":
            return dict(r)
    return dict(records[0])


def _hint_runtime_error(rec: dict) -> str:
    """A repair hint naming the exact crash site — surfaced INSTEAD of the misleading
    downstream symptom ("no controlled body" / "dead action") a runtime crash causes."""
    method = rec.get("method") or "a game method"
    line = rec.get("line")
    where = f"res://game.gd:{line}" if line is not None else "an unknown line"
    verb = "hit a parse error" if rec.get("kind") == "parse" else "crashed"
    return (f"your {method}() {verb} at {where}: "
            f"{rec.get('message') or 'runtime script error'} — the engine aborted the "
            f"call mid-episode (guard the null/uninitialised value). This is the ROOT "
            f"cause behind any downstream symptom (dead action / missing body).")


def _attach_runtime_error(report: dict, executor) -> dict:
    """Additive: if the executor captured a runtime SCRIPT ERROR (build/act crash), add
    a ``runtime_error`` finding to the report and override the hint to name the crash.
    A no-op when nothing crashed, so a clean game's report keys/hint are untouched."""
    rec = _runtime_error_finding(getattr(executor, "runtime_errors", None))
    if rec:
        report["runtime_error"] = rec
        report["hint"] = _hint_runtime_error(rec)
    return report


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
        eff = checks["efficacy"]
        n = eff.get("contexts", 1)
        return (f"dead action(s) with no effect on the world from any of {n} "
                f"probed context(s): {', '.join(eff['dead'])}")
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
        # "fix or remove them" (pre-2026-07-16) offered DELETION as an equal option, and
        # deleting is always the cheaper branch — so the model took it, and a staged
        # design lost a stage per repair round. A dead milestone means the winning path
        # misses it: re-wire the path, keep the milestone. [ambition audit]
        return (f"dead milestone(s) never latched on the winning path: "
                f"{', '.join(dead)} — re-wire each so the winning path actually latches "
                f"it: put it ON the intended route, or gate the goal behind it so no win "
                f"can skip it. Keep every milestone; do not delete one to pass this "
                f"check; {PRESERVE_SHORT}")
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
    """Milestone-aware UNSOLVED hint: name the boundary where the probe stalls, then ask
    for a REACHABILITY fix at exactly that boundary.

    These three hints were the loop's main ambition launderers (2026-07-16 audit): they
    said "make the goal easier to reach" / "make the first stage easier", and the model
    obeyed by demolishing the mechanic (unlocking every door, collapsing a dwell-timer
    alarm to disarm-on-touch, lining the objectives on one axis). An unreached opening is
    a REACHABILITY defect, so they now name the local fix and forbid the demolition —
    mirroring the `g3_unsolvable` directive that already got this right. See
    `harness.repair_language`."""
    total = checks.get("episodes", {}).get("run", PROBE_EPISODES)
    if not progress or not progress.get("reach_counts"):
        return (f"no random rollout reached success in {total} episodes "
                f"x {PROBE_HORIZON} ticks — bring the first objective within reach of "
                f"the starting state and verify the ACTIONS actually move the agent "
                f"toward it (adjust {REACHABILITY_FIXES}), WITHOUT removing the goal; "
                f"{PRESERVE_SHORT}")
    reach = progress["reach_counts"]
    declared = list(reach)
    stuck = progress.get("stuck_after")
    if stuck is None:
        return (f"no episode reached the first milestone '{declared[0]}' "
                f"in {total} episodes — bring '{declared[0]}' within reach of the "
                f"starting state and verify the ACTIONS actually move the agent toward "
                f"it (adjust {REACHABILITY_FIXES}), WITHOUT removing '{declared[0]}' or "
                f"any later stage; {PRESERVE_SHORT}")
    nxt = declared[declared.index(stuck) + 1] if declared.index(stuck) + 1 < len(declared) \
        else "success"
    return (f"{reach[stuck]}/{total} episodes reached '{stuck}', none reached "
            f"'{nxt}' — the game is stuck between '{stuck}' and '{nxt}'; make exactly "
            f"that one step reachable (widen the gap, steady the hazard, enlarge the "
            f"target, or relax the timing at that step alone) and keep every stage "
            f"through '{stuck}', and every stage after '{nxt}', intact; {PRESERVE_SHORT}")


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
def verify_game_rescue(game_path: str, sandboxed: bool = True, *, world_factory=None,
                       **rescue_kwargs) -> dict:
    """ADDITIVE orchestration: the tree verify funnel, then — ONLY when it leaves the game
    UNSOLVED-with-progress — an RL-witness SECOND certification pass (`certify.rescue_certify`).

    The plain :func:`verify_game` path is untouched (tree-only, fast, byte-identical); this is
    the explicit opt-in second lane behind the `game rescue` CLI verb / harden's rl_rescue
    flag. A tree-certified report is stamped ``witness_source="tree"``; a game the trained
    policy rescues becomes a first-class certified report with ``witness_source="rl"`` (witness
    shape identical, so G4 / atlas / demos consume it unchanged). See `rescue_certify` for the
    convergence + deterministic-replay bar and the honest-failure block. `rescue_kwargs` are
    forwarded (budget_steps, num_envs, n_eval, thresholds, save_model, g3_fn seam, ...)."""
    report = verify_game(game_path, sandboxed=sandboxed, world_factory=world_factory)
    from harness.rl.certify import rescue_certify
    return rescue_certify(game_path, verify_report=report, **rescue_kwargs)


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
            # A build() that crashed at runtime failed the builds gate above with a
            # downstream-looking symptom; name the real SCRIPT ERROR site instead.
            return _attach_runtime_error(report, executor)

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
            # An act() that crashed at runtime made its action inert -> G1 reports a
            # misleading "dead action"; name the real SCRIPT ERROR site instead.
            return _attach_runtime_error(report, executor)

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

        # --- G3.6: failure-witness / PRESSURE gate (WAVE 1, DEMO_GAP_ANALYSIS §Gap 1+2)
        # ADVISORY: confirms is_failure() can fire from a reachable state (real stakes).
        # A game that cannot be lost records a warning + a repair directive but is NOT
        # blocked (see _failure_witness_gate). Runs LAST, only on a still-certified game.
        if report.get("passed"):
            report = _failure_witness_gate(executor, source, actions, report)

        # --- G3.7: dead-space / PROPORTION gate (WAVE 2, DEMO_GAP_ANALYSIS §Gap 3)
        # ADVISORY: measures the space-utilization ratio from t=0 geometry (world vs the
        # span the action uses). An over-empty world records a warning + repair directive
        # but is NOT blocked (see _dead_space_gate). Only on a still-certified game.
        if report.get("passed"):
            report = _dead_space_gate(facts, report)

        # --- G3.8: material-anchoring / MATERIAL REALITY gate (contract: api_gdscript.md)
        # ADVISORY: replays the certified witness and checks each milestone flip lands ON a real
        # reported body, not a bare coordinate in open space. Warns + stashes a repair directive
        # but is NOT blocked (see _anchoring_gate). Only on a still-certified game.
        if report.get("passed"):
            report = _anchoring_gate(executor, source, report, facts)
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


# ======================================================================== #
# Failure-witness gate — WAVE 1 PRESSURE (DEMO_GAP_ANALYSIS §Gap 1 + 2)
# ======================================================================== #
# A certified game must have STAKES: a REACHABLE failure. ``is_failure()`` is
# syntactically mandatory but semantically OPTIONAL — nothing in G0-G3 exercises it,
# so a constant-false body certifies clean, idling is free, and Elias's ANTI-IDLING
# softlock principle has no in-game meaning (4/6 of our games are unfailable — the #1
# ranked demo gap). This gate confirms is_failure can actually fire from a reachable
# state (a FAILURE witness, dual to the G3 success witness) and, when it cannot,
# records the finding + a repair directive.
#
# WHY ADVISORY — a WARNING that compiles to a repair directive, never a hard
# cert-block (the documented decision the mission asks for):
#   1. FALSE-REJECT DISCIPLINE. The failure sweep is a BOUNDED negative (necessary-
#      not-sufficient for unfailability); a genuine but hard-to-trigger failure — a
#      slow timeout, a resource that depletes late — must never be wrongly rejected,
#      the same "err toward passing" stance G0.5 already takes.
#   2. COMPOSES WITH THE BLOCKING GATES. single_action / G0.5 own their hard verdicts;
#      pressure is orthogonal and must not preempt a broken fixture's real defect
#      (which is why it runs LAST and only on an otherwise-certified game).
#   3. THE LOOP DELIVERS THE BAR. Wave-1 acceptance ("0 constant-false is_failure in
#      the CERTIFIED set") is met by the REVISE loop, not a reject: the finding is
#      proof-carrying (a static constant-false proof, or the broad-sweep reproducer-
#      absence) and ALWAYS compiles a directive (feedback._compile_pressure), driving
#      the final set to have stakes — Elias's "looped approach" (FEEDBACK_LOOP.md).
# It is stored as a NON-GATING sub-check under G3_solve (``failure_witness``, always
# pass=True; the real signal is ``has_failure_witness``) plus a report warning, so no
# top-level report key changes and report["passed"] / failure_class are untouched.

def _pressure_hint(*, constant_false: bool) -> str:
    if constant_false:
        return ("the game cannot be lost — is_failure() is hardcoded to return false, "
                "so a stalled or idle episode is indistinguishable from real play. Add "
                "a real failure condition (a hazard that ends the run, a timeout / step "
                "budget, an out-of-bounds, or a depletable resource) and read it in "
                "is_failure() so play has stakes. Keep the goal reachable.")
    return ("the game cannot be lost — is_failure() never fires from ANY reachable "
            "state under a broad adversarial rollout, so idling is free. Either the "
            "lose condition is unreachable (a threshold no trajectory crosses, or the "
            "win always resolves first) or the detector never triggers. Make failure a "
            "condition a real player could actually trigger. Keep the goal reachable.")


def _pressure_finding(*, outcome: str, constant_false: bool, hint: str,
                      reproducer: dict, evidence: dict) -> dict:
    """The machine-readable pressure finding the feedback compiler's pressure row
    consumes. ``outcome`` in {no_pressure, failure_unreachable, has_pressure}; only
    the first two compile to a directive (feedback._compile_pressure)."""
    return {"outcome": outcome, "constant_false": bool(constant_false),
            "detail": hint, "reproducer": dict(reproducer or {}),
            "evidence": dict(evidence or {})}


def _failure_witness_gate(executor, game_source, actions, report):
    """The failure-witness (PRESSURE) gate. ADVISORY: records whether is_failure can
    fire from a reachable state; never blocks certification (see the section header).

    Two paths to 'no stakes', distinguished for the directive (mission item 2):
      * CONSTANT-FALSE (static proof) — is_failure() is literally ``return false``:
        outcome ``no_pressure`` (the game declares no lose condition at all).
      * FAILURE-UNREACHABLE (dynamic) — is_failure() has logic but no adversarial
        rollout ever loses (the race where success always resolves first, or a
        detector that never triggers): outcome ``failure_unreachable``.
    A reachable failure records outcome ``has_pressure`` with the witness (no directive).
    Engine trouble leaves the verdict untouched."""
    from harness.verify.gd_gate import is_failure_constant_false
    from harness.verify.reachability import failure_reachable

    constant_false = is_failure_constant_false(game_source)
    fr = None
    if not constant_false:
        try:
            fr = failure_reachable(executor, game_source, actions)
        except VerifyError:
            return report                          # engine trouble -> no verdict change

    if constant_false:
        outcome, has_failure = "no_pressure", False
        hint, reproducer, evidence = _pressure_hint(constant_false=True), {}, {}
    elif fr and fr.get("reachable"):
        outcome, has_failure = "has_pressure", True
        hint, reproducer = "", (fr.get("witness") or {})
        evidence = {"n_plans": fr.get("n_plans"), "n_failed": fr.get("n_failed")}
    else:
        outcome, has_failure = "failure_unreachable", False
        hint, reproducer = _pressure_hint(constant_false=False), {}
        evidence = {"n_plans": (fr or {}).get("n_plans"), "n_failed": 0}

    finding = _pressure_finding(outcome=outcome, constant_false=constant_false,
                                hint=hint, reproducer=reproducer, evidence=evidence)
    layer = report.setdefault("layers", {}).setdefault(
        "G3_solve", {"passed": True, "checks": {}})
    layer.setdefault("checks", {})["failure_witness"] = check(
        True, advisory=True, has_failure_witness=has_failure,
        outcome=outcome, constant_false=constant_false,
        witness=(reproducer or None), finding=finding)
    if not has_failure:
        report.setdefault("warnings", []).append("PRESSURE: " + hint)
    return report


# ======================================================================== #
# Dead-space / PROPORTION gate — WAVE 2 SPACE (DEMO_GAP_ANALYSIS §Gap 3)
# ======================================================================== #
# Our generated worlds are 20-69x emptier than the reference demos (a radius-16 puck on
# an 800x600 table; the mechanic in a sliver) — the #3 ranked demo gap. This gate turns
# that into a measured, purely-geometric FACT: the dead-space ratio (reachability.
# space_utilization) — how big the declared PLAYFIELD is versus the SPAN the action
# actually uses (the controlled body + the reachable checkpoints/goals). Dimension-aware
# (a 2D AREA ratio, a 3D VOLUME ratio, normalised to a per-axis LINEAR ratio).
#
# ADVISORY, mirroring the PRESSURE gate EXACTLY (the mission's brief): a bounded
# heuristic over static geometry is NON-gating — never a hard cert-block. Over-emptiness
# is a POLISH concern (DIFFICULTY-tier in the feedback taxonomy), not a defect; a genuine
# but sprawling design (a wide track, a scattered-collectible hunt) must never be wrongly
# rejected, so it records a warning + a proof-carrying finding that ALWAYS compiles a
# repair directive (feedback._compile_dead_space) and drives the loop toward tighter
# worlds — it never blocks. Stored as a NON-GATING sub-check under G3_solve
# (``dead_space``, always pass=True; the real signal is the ``dead_space`` bool + the
# ratio) plus, ONLY when flagged, a report warning and the top-level ``report["dead_space"]``
# finding the feedback bridge reads (parallel to ``report["runtime_error"]`` — present iff
# there is something to repair). report["passed"] / failure_class are untouched either way.

def _dead_space_hint(su: dict) -> str:
    """The principle-phrased repair directive body (no numbers baked into a rule, no node
    list — a bounded fact + a design principle the model acts on)."""
    ratio = su.get("linear_ratio")
    return (f"the playfield is ~{ratio:.1f}x larger (per axis) than the region the action "
            "needs — most of the world is empty space the mechanic never touches, so play "
            "reads as aimless drift. Tighten the world to the action (shrink the arena / "
            "WORLD_SIZE toward the region the controlled body and its goals occupy), or "
            "spread the elements so they fill the space, so the world is proportioned to "
            "the mechanic. Keep the goal reachable and the mechanic intact.")


def _dead_space_finding(su: dict) -> dict:
    """The machine-readable proportion finding the feedback compiler's dead_space row
    consumes. ``outcome`` is ``dead_space`` (over-empty) or ``proportioned`` (healthy);
    only the former compiles to a directive (feedback._compile_dead_space)."""
    dead = bool(su.get("dead_space"))
    return {"outcome": "dead_space" if dead else "proportioned",
            "detail": _dead_space_hint(su) if dead else (su.get("detail") or ""),
            "linear_ratio": su.get("linear_ratio"),
            "measure_ratio": su.get("measure_ratio"),
            "threshold": su.get("threshold"), "dims": su.get("dims"),
            "playfield": su.get("playfield"), "span": su.get("span"),
            "n_targets": su.get("n_targets"), "n_reachable": su.get("n_reachable")}


def _dead_space_gate(facts, report):
    """The dead-space (PROPORTION) gate. ADVISORY: measures the space-utilization ratio
    from the serve host's t=0 geometry and records it as a non-gating sub-check; on an
    over-empty world it ALSO warns and stashes the finding at ``report["dead_space"]``
    for the feedback bridge. Never blocks certification (see the section header). A game
    without measurable geometry (no controlled spawn) leaves the verdict untouched."""
    from harness.verify.reachability import space_utilization
    bodies = facts.get("geometry") or []
    ws = (facts.get("world_size") or {}).get("declared") or [800, 600]
    try:
        su = space_utilization(bodies, ws)
    except Exception:
        return report                              # advisory: a measurement hiccup never blocks
    if su is None:
        return report                              # not enough geometry -> no verdict change
    finding = _dead_space_finding(su)
    layer = report.setdefault("layers", {}).setdefault(
        "G3_solve", {"passed": True, "checks": {}})
    layer.setdefault("checks", {})["dead_space"] = check(
        True, advisory=True, dead_space=bool(su["dead_space"]),
        linear_ratio=su["linear_ratio"], measure_ratio=su["measure_ratio"],
        threshold=su["threshold"], dims=su["dims"], finding=finding)
    if su["dead_space"]:
        report["dead_space"] = finding             # only when flagged (cf. runtime_error)
        report.setdefault("warnings", []).append("PROPORTION: " + finding["detail"])
    return report


# ======================================================================== #
# Material-anchoring gate — G3.8 MATERIAL REALITY (contract: api_gdscript.md)
# ======================================================================== #
# The contract: any milestone or win defined by WHERE something is must be anchored to a REAL
# node with a collision shape (a body or an area the game add_childs in build() and reports in
# state()), latched off that node's overlap/contact/position — never off a bare coordinate
# checked with distance math. A goal that is only arithmetic is invisible: it can be memorised,
# never seen or drawn. This is the spatial-milestone twin of STAKES's is_failure rule — a
# non-vacuity rule that gives a bare signature (checkpoints()/is_success()) semantic teeth.
#
# ADVISORY, mirroring the PRESSURE and PROPORTION gates EXACTLY: a NON-gating sub-check under
# G3_solve ("material_anchoring", always pass=True; the real signal is the ``anchored`` bool)
# plus, ONLY when a milestone flips in empty space, a report warning and the top-level
# ``report["anchoring"]`` finding the feedback bridge reads (parallel to ``report["dead_space"]``).
# report["passed"] / failure_class are untouched either way — a certified game stays certified,
# and the existing library (whose 10 ghost-goal games are already certified) is never re-flipped.
#
# WHAT IT CHECKS TODAY (necessary-not-sufficient, the same epistemic class as PRESSURE/
# PROPORTION): CENTER-PROXIMITY at the flip tick. The witness replay (frames_every=1, exactly
# as the G3 solidity replay) gives per-tick body POSITIONS; the t=0 CHECK geometry gives each
# body's self-reported extent. For each latched milestone (and the win) we ask: at the flip
# tick — and the tick before it, so a one-tick-late latch is forgiven — is the controlled body
# within tolerance of ANY reported body's surface? If the nearest reported body is farther than
# the tolerance, the milestone latched in open space: a bare coordinate threshold, not an event.
#
# WHAT IT CANNOT CHECK YET (deferred to the host wire — notes/engines/MATERIAL_REALITY.md):
# true geometric OVERLAP, anchoring to an Area the game does NOT self-list, and whether a
# self-reported "body" actually owns a collision shape. Those need the serve host to emit
# authoritative per-body extents and a per-tick contact/overlap set (both PURE ADDS that leave
# the un-requested wire byte-identical, to protect G1 twin-rollout identity). Until then a game
# that reports a phantom body at the goal coordinate passes this check — the contract still binds
# it, and the phase makes ZERO host changes on purpose (determinism).

# The synthetic key under which the WIN (the final success tick) is examined as one more
# spatial milestone — so a checkpoint-less game whose win is a bare coordinate is still caught.
WIN_MILESTONE_KEY = "is_success"


def _anchor_extent(geom) -> float:
    """A body's representative half-extent from its self-reported footprint (radius /
    half_extents / aabb), or 0.0 for a bare marker. GENEROUS (the MAX half-dimension) so a
    body resting against a large solid reads as anchored, never falsely flagged."""
    if not isinstance(geom, dict):
        return 0.0
    r = geom.get("radius")
    if isinstance(r, (int, float)) and float(r) > 0.0:
        return abs(float(r))
    half = geom.get("half_extents")
    if isinstance(half, (list, tuple)) and half:
        vals = [abs(float(v)) for v in half if isinstance(v, (int, float))]
        if vals:
            return max(vals)
    aabb = geom.get("aabb")
    if isinstance(aabb, (list, tuple)) and len(aabb) == 2 \
            and all(isinstance(c, (list, tuple)) for c in aabb):
        mn, mx = aabb[0], aabb[1]
        spans = [abs(float(mx[i]) - float(mn[i])) / 2.0
                 for i in range(min(len(mn), len(mx)))]
        if spans:
            return max(spans)
    return 0.0


def _center_distance(a, b):
    """Euclidean center distance over the shared leading components (2D or 3D), or None when
    either position is missing/ragged."""
    if not (isinstance(a, (list, tuple)) and isinstance(b, (list, tuple))):
        return None
    n = min(len(a), len(b))
    if n < 2:
        return None
    try:
        return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(n)))
    except (TypeError, ValueError):
        return None


def _anchor_tolerance(geometry, world_size) -> float:
    """The proximity tolerance: a floor OR a small fraction of the occupancy-bounds diagonal,
    whichever is larger. Reuses the dead-space gate's playfield box (space_utilization) for the
    diagonal, falling back to the declared world_size. Numbers here are EVIDENCE thresholds —
    never rule text; the contract carries no numbers."""
    diag = 0.0
    try:
        from harness.verify.reachability import space_utilization
        su = space_utilization(geometry, world_size)
    except Exception:
        su = None
    if su and su.get("playfield"):
        diag = math.sqrt(sum(float(v) ** 2 for v in su["playfield"]))
    if diag <= 0.0:
        ws = [float(v) for v in (world_size or []) if isinstance(v, (int, float))]
        diag = math.sqrt(sum(v * v for v in ws)) if ws else 0.0
    return max(ANCHOR_TOL_FLOOR, ANCHOR_TOL_FRAC * diag)


def _nearest_reported_surface(frames_by_tick, ctrl_name, extents, tick):
    """Min SURFACE distance (center distance minus both self-reported extents) from the
    controlled body to ANY non-controlled reported body, sampled at BOTH ``tick`` and
    ``tick-1`` (a one-tick-late latch is forgiven). Returns (distance, other_name), or
    (None, None) when neither frame carries the controlled body and a partner."""
    best, best_other = None, None
    ctrl_extent = _anchor_extent(extents.get(ctrl_name))
    for t in (tick, tick - 1):
        ents = frames_by_tick.get(t)
        if not isinstance(ents, dict):
            continue
        ce = ents.get(ctrl_name)
        cpos = ce.get("pos") if isinstance(ce, dict) else None
        if cpos is None:
            continue
        for name, q in ents.items():
            if name == ctrl_name or not isinstance(q, dict) or q.get("controlled"):
                continue
            cd = _center_distance(cpos, q.get("pos"))
            if cd is None:
                continue
            d = cd - ctrl_extent - _anchor_extent(extents.get(name))
            if best is None or d < best:
                best, best_other = d, name
    return best, best_other


def _unanchored_milestones(frames, geometry, checkpoints, success_tick, world_size):
    """PURE core of the anchoring gate (engine-agnostic; no executor). Returns the list of
    offending milestone entries ``{milestone, tick, controlled, nearest_body, distance, tol}``
    — one per milestone whose flip sits farther than the tolerance from every reported body.
    Empty list == every milestone is anchored (or there is nothing measurable). Deterministic."""
    frames_by_tick = {}
    for fr in frames or []:
        if isinstance(fr, dict) and fr.get("tick") is not None:
            frames_by_tick[int(fr["tick"])] = fr.get("entities") or {}
    geo = {str(g.get("name")): g for g in (geometry or [])
           if isinstance(g, dict) and g.get("name") is not None}
    ctrl_name = next((n for n, g in geo.items() if g.get("controlled")), None)
    if ctrl_name is None or not frames_by_tick:
        return []
    tol = _anchor_tolerance(geometry, world_size)

    candidates = [(str(k), int(t)) for k, t in (checkpoints or {}).items() if t is not None]
    if isinstance(success_tick, int) and success_tick > 0:
        candidates.append((WIN_MILESTONE_KEY, success_tick))

    offending, seen_flips = [], set()
    for key, tick in candidates:
        d, other = _nearest_reported_surface(frames_by_tick, ctrl_name, geo, tick)
        if d is None or d <= tol:
            continue                                   # anchored (or unmeasurable -> not flagged)
        flip = (tick, other)                           # same tick + same nearest body == one flip
        if flip in seen_flips:
            continue                                   # the win coincides with a checkpoint flip
        seen_flips.add(flip)
        offending.append({"milestone": key, "tick": tick, "controlled": ctrl_name,
                          "nearest_body": other, "distance": round(d, 1), "tol": round(tol, 1)})
    return offending


def _anchoring_finding(offending) -> dict:
    """The machine-readable MATERIAL-REALITY finding the feedback compiler consumes. ``outcome``
    is ``unanchored`` (>=1 milestone flips in empty space) or ``anchored`` (healthy); only the
    former compiles to a directive (feedback._compile_anchoring)."""
    offending = list(offending or [])
    if not offending:
        return {"outcome": "anchored", "milestones": []}
    detail = (f"{len(offending)} milestone(s) flip in empty space: " + "; ".join(
        f"'{m['milestone']}' at tick {m['tick']} is {m['distance']}px from "
        f"'{m['nearest_body']}' (tolerance {m['tol']}px)" for m in offending))
    return {"outcome": "unanchored", "milestones": offending, "detail": detail}


def _anchoring_gate(executor, game_source, report, facts):
    """The material-anchoring (MATERIAL REALITY) gate. ADVISORY: replays the certified witness
    (frames_every=1, as the G3 solidity replay does) and asks whether each latched milestone
    (and the win) flips within tolerance of a REAL reported body. Records a NON-gating sub-check
    always; on a flip in empty space it ALSO warns and stashes the finding at
    ``report["anchoring"]`` for the feedback bridge. Never blocks certification (see the section
    header). Engine trouble, a witness-less report, or a measurement hiccup leave the verdict
    untouched."""
    witness = report.get("witness") or {}
    actions = witness.get("actions")
    if not actions:
        return report                                  # nothing to replay -> no verdict change
    try:
        replay = executor.run_batch(
            game_source, [{"seed": WORLD_SEED, "actions": actions}],
            len(actions), frames_every=1)[0]
    except VerifyError:
        return report                                  # engine trouble -> no verdict change
    world_size = (facts.get("world_size") or {}).get("declared") or [800, 600]
    try:
        offending = _unanchored_milestones(
            replay.get("frames") or [], facts.get("geometry") or [],
            witness.get("checkpoints") or {}, witness.get("ticks"), world_size)
    except Exception:
        return report                                  # advisory: a measurement hiccup never blocks

    finding = _anchoring_finding(offending)
    anchored = not offending
    layer = report.setdefault("layers", {}).setdefault(
        "G3_solve", {"passed": True, "checks": {}})
    layer.setdefault("checks", {})["material_anchoring"] = check(
        True, advisory=True, anchored=anchored, finding=finding)
    if not anchored:
        report["anchoring"] = finding                  # only when flagged (cf. dead_space)
        report.setdefault("warnings", []).append("ANCHORING: " + finding["detail"])
    return report
