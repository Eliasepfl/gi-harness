"""G0.5 — geometric checkpoint/goal reachability pre-filter (Elias directive 1).

A CHEAP, coarse geometric flood-fill run BETWEEN the G0/G1 static+rollout gates and
the expensive G3 tree solve. Some generated games are UNSOLVABLE because a
checkpoint/goal region is geometrically WALLED OFF — the controlled body can never
reach it through the static geometry, so RL latches zero checkpoints and G3 burns its
whole budget proving the obvious. This filter flood-fills a coarse occupancy grid from
the controlled body's spawn over the static-body footprints and asks: does a
collision-free corridor plausibly EXIST from spawn to each target region?

HONEST SEMANTICS — NECESSARY, NOT SUFFICIENT:
  * reachability FAILS  => the target is DEFINITELY unreachable => the game is
                           unsolvable => a fast reject with a clear repair hint
                           ('checkpoint/goal X is walled off / unreachable').
  * reachability PASSES => a corridor MIGHT exist; this does NOT prove the game is
                           dynamically solvable (momentum, timing, gravity, damping,
                           charge combos are all ignored) — that stays G3 (the tree
                           solve) + G3' (RL).

It NEVER runs the physics engine — it is a purely geometric static check over t=0
positions + static footprints. The grid uses cell-CENTRE sampling, inflates every
static footprint by the controlled body's clearance radius, and treats a target as
reached if the flood lands in its NEIGHBOURHOOD (the player only needs to get near a
target, not stand exactly on it). Every one of those choices errs toward PASSING: a
false reject would wrongly kill a solvable game, whereas a false pass merely defers to
G3 — the honest fallback.

Works in 2D and 3D uniformly (the spawn/target/AABB tuples carry the dimension).
"""

from __future__ import annotations

import math
from collections import deque

# --- Constants ([eng.] = calibrated engineering choice) ------------------- #
MIN_CELL = 8.0                 # px: finest grid cell (a corridor narrower than this is
                               # genuinely impassable) [eng.]
MAX_CELLS_PER_AXIS_2D = 160    # cap so a 2D grid stays <= 160^2 cells (cheap) [eng.]
MAX_CELLS_PER_AXIS_3D = 48     # cap so a 3D grid stays <= 48^3 cells (cheap) [eng.]
DEFAULT_CLEARANCE = 12.0       # px: default controlled-body radius if none supplied [eng.]
BOUNDS_MARGIN = 40.0           # px: pad the grid past the world/geometry extent [eng.]
NEIGHBOURHOOD = 1              # cells: a target is reached if the flood lands within this
                               # Chebyshev radius of it (get-near tolerance) [eng.]


def _detail(unreachable) -> str:
    if not unreachable:
        return "every target region is geometrically reachable from spawn"
    names = ", ".join(str(u) for u in unreachable)
    return (f"checkpoint/goal region(s) walled off / unreachable from spawn: {names} "
            f"(no collision-free corridor exists through the static geometry)")


def check_reachability(spawn, targets, occupancy, world_size, *,
                       clearance=DEFAULT_CLEARANCE, cell=None):
    """Coarse geometric reachability of every target region from ``spawn``.

    ``spawn``      : the controlled body's t=0 centre, a 2- or 3-tuple.
    ``targets``    : list of ``{"name": str, "pos": (x, y[, z])}`` regions to reach.
    ``occupancy``  : list of static AABBs ``((min...), (max...))`` (the walls/solids).
    ``world_size`` : ``(w, h[, d])`` — the play extent (grid is padded past it).
    ``clearance``  : the controlled body's radius; every wall is inflated by it.
    ``cell``       : optional explicit cell size (else derived + axis-capped).

    Returns a dict::

        {"reachable": bool, "unreachable": [name, ...], "targets": N,
         "dims": 2|3, "cells": (nx, ny[, nz]) | None, "detail": str}

    NECESSARY-not-SUFFICIENT: ``reachable == False`` proves unsolvable; ``True`` does
    not prove solvable (see the module docstring).
    """
    spawn = [float(x) for x in spawn]
    dims = len(spawn)
    targets = list(targets or [])
    occ = [([float(v) for v in mn], [float(v) for v in mx])
           for mn, mx in (occupancy or [])]

    # No static occupancy => nothing can wall a target off; no targets => nothing to
    # check. Either way we can only REJECT on proof, so PASS trivially.
    if not occ or not targets:
        return {"reachable": True, "unreachable": [], "targets": len(targets),
                "dims": dims, "cells": None,
                "detail": ("no static occupancy geometry to wall off a target"
                           if not occ else "no target regions to check")}

    clearance = float(clearance or 0.0)

    # --- Grid bounds: the world box, expanded only to include spawn/targets (a body
    # can sit at the very edge). Walls do NOT drive the bounds — the player is confined
    # to the play area, so a wall spanning the full world dimension genuinely seals it
    # (cells whose centre lies OUTSIDE [0, world_size] are treated as blocked below, so
    # there is no spurious go-around lane past a world-spanning wall).
    world = [float(world_size[i]) if i < len(world_size) else None for i in range(dims)]
    lo = [min(0.0, spawn[i]) for i in range(dims)]
    hi = [max(world[i] if world[i] is not None else spawn[i], spawn[i])
          for i in range(dims)]
    for t in targets:
        p = t["pos"]
        for i in range(dims):
            lo[i] = min(lo[i], float(p[i]))
            hi[i] = max(hi[i], float(p[i]))
    for i in range(dims):
        lo[i] -= BOUNDS_MARGIN
        hi[i] += BOUNDS_MARGIN

    # --- Cell size: fine enough to resolve corridors, capped so the grid stays cheap.
    max_axis = max(hi[i] - lo[i] for i in range(dims)) or MIN_CELL
    cap = MAX_CELLS_PER_AXIS_2D if dims == 2 else MAX_CELLS_PER_AXIS_3D
    if cell is None:
        cell = max(MIN_CELL, max_axis / cap)
    cell = float(cell)
    n = [max(1, min(cap, int(math.ceil((hi[i] - lo[i]) / cell)))) for i in range(dims)]

    # Inflate every wall by the clearance radius (the player CENTRE cannot get within
    # `clearance` of a solid), so a gap narrower than the player reads as blocked.
    inflated = [([mn[i] - clearance for i in range(dims)],
                 [mx[i] + clearance for i in range(dims)]) for mn, mx in occ]

    def centre(idx):
        return [lo[i] + (idx[i] + 0.5) * cell for i in range(dims)]

    def blocked(pt):
        # Outside the play area (per bounded axis) -> not walkable; this confines the
        # flood to the world so a world-spanning wall truly separates the space.
        for i in range(dims):
            if world[i] is not None and (pt[i] < 0.0 or pt[i] > world[i]):
                return True
        for mn, mx in inflated:
            if all(mn[i] <= pt[i] <= mx[i] for i in range(dims)):
                return True
        return False

    def to_idx(pt):
        return tuple(min(n[i] - 1, max(0, int((float(pt[i]) - lo[i]) / cell)))
                     for i in range(dims))

    # Axis-aligned neighbour offsets (4-conn in 2D, 6-conn in 3D).
    offs = []
    for i in range(dims):
        for d in (-1, 1):
            o = [0] * dims
            o[i] = d
            offs.append(tuple(o))

    # --- BFS flood over FREE cells (cell-centre sampling); the spawn cell is forced
    # free (the player IS standing there, even if it grazes an inflated wall).
    start = to_idx(spawn)
    seen = {start}
    dq = deque([start])
    while dq:
        c = dq.popleft()
        for o in offs:
            nc = tuple(c[i] + o[i] for i in range(dims))
            if any(nc[i] < 0 or nc[i] >= n[i] for i in range(dims)) or nc in seen:
                continue
            if blocked(centre(nc)):
                continue
            seen.add(nc)
            dq.append(nc)

    # A target is reachable if the flood lands anywhere in its NEIGHBOURHOOD (get-near
    # tolerance — the player need only approach a target region, not stand on it).
    def reached(pos):
        tc = to_idx(pos)
        rng = range(-NEIGHBOURHOOD, NEIGHBOURHOOD + 1)
        if dims == 2:
            return any((tc[0] + dx, tc[1] + dy) in seen for dx in rng for dy in rng)
        return any((tc[0] + dx, tc[1] + dy, tc[2] + dz) in seen
                   for dx in rng for dy in rng for dz in rng)

    unreachable = [t.get("name", str(tuple(t["pos"]))) for t in targets
                   if not reached(t["pos"])]

    return {"reachable": not unreachable, "unreachable": unreachable,
            "targets": len(targets), "dims": dims, "cells": tuple(n),
            "detail": _detail(unreachable)}


# ======================================================================== #
# Geometry-facts adapter — build the check inputs from the serve host's t=0 facts.
# ======================================================================== #
def targets_and_occupancy(bodies):
    """Split a list of t=0 body facts into (spawn, targets, occupancy) for the check.

    Each body fact is a dict with ``pos`` and optional ``static``/``sensor``/
    ``controlled`` flags and an optional ``aabb`` (``[min..., max...]``) or
    ``half_extents`` footprint. Classification:

      * the controlled body        -> the flood SPAWN (+ its clearance radius).
      * static, non-sensor, with a real footprint -> OCCUPANCY (a wall/solid).
      * everything else (markers, sensor goal pads, dynamic props) -> a TARGET region
        the controlled body must be able to reach.

    A footprint-less static body (a bare position marker — e.g. a gem) is NOT a wall;
    it contributes a target, never occupancy, so a marker never blocks the flood.
    Returns ``(spawn|None, clearance, targets, occupancy)``.
    """
    spawn = None
    clearance = DEFAULT_CLEARANCE
    targets = []
    occupancy = []
    for b in bodies or []:
        pos = b.get("pos")
        if not isinstance(pos, (list, tuple)) or len(pos) < 2:
            continue
        pos = tuple(float(v) for v in pos)
        aabb = _aabb_of(b, pos)
        if b.get("controlled"):
            spawn = pos
            if aabb is not None:
                half = [(aabb[1][i] - aabb[0][i]) / 2.0 for i in range(len(pos))]
                clearance = max(MIN_CELL, min(half) if half else DEFAULT_CLEARANCE)
            continue
        if b.get("static") and not b.get("sensor") and aabb is not None:
            occupancy.append(aabb)
        else:
            targets.append({"name": b.get("name", str(pos)), "pos": pos})
    return spawn, clearance, targets, occupancy


def _aabb_of(body, pos):
    """The body's static footprint as (min_corner, max_corner), or None if it has no
    real extent (a bare marker). Accepts an explicit ``aabb`` or a ``half_extents``/
    ``radius`` field on the body fact."""
    dims = len(pos)
    aabb = body.get("aabb")
    if isinstance(aabb, (list, tuple)) and len(aabb) == 2 \
            and all(isinstance(c, (list, tuple)) and len(c) >= dims for c in aabb):
        mn = [float(aabb[0][i]) for i in range(dims)]
        mx = [float(aabb[1][i]) for i in range(dims)]
        if all(mx[i] - mn[i] > 1e-6 for i in range(dims)):
            return (mn, mx)
        return None
    half = body.get("half_extents")
    if isinstance(half, (list, tuple)) and len(half) >= dims:
        h = [abs(float(half[i])) for i in range(dims)]
        if all(v > 1e-6 for v in h):
            return ([pos[i] - h[i] for i in range(dims)],
                    [pos[i] + h[i] for i in range(dims)])
        return None
    r = body.get("radius")
    if isinstance(r, (int, float)) and float(r) > 1e-6:
        return ([pos[i] - float(r) for i in range(dims)],
                [pos[i] + float(r) for i in range(dims)])
    return None


# ======================================================================== #
# WAVE 1 — PRESSURE: terminal reachability via the certified tree solver.
# ======================================================================== #
# The G0.5 flood above answers a STATIC GEOMETRIC question — can the controlled
# body get NEAR a target through the walls. Wave 1 (notes/engines/DEMO_GAP_ANALYSIS.md,
# the #1 ranked gap) needs a DYNAMIC one: from a given state, can play still reach a
# TERMINAL — success OR failure? Two things the analysis wants ride on that answer:
#
#   * the FAILURE-WITNESS gate — is ``is_failure()`` reachable from ANY reachable
#     state at all? A game whose failure never fires has no stakes: idling is free,
#     Elias's ANTI-IDLING softlock principle has no in-game meaning.
#   * Elias's stuck-vs-refusal separator — a non-terminal state from which NO terminal
#     is reachable is a real ENVIRONMENT-softlock; a state from which a terminal IS
#     reachable but whose own trajectory idles is AGENT-refusal, NOT a game defect.
#
# Both ride the executor seam and reuse the SAME Go-Explore solver that certifies G3
# (``harness.verify.treesolve``), so a "reachable" verdict is a REPLAYABLE witness,
# never a guess. NOT finding a terminal within the budget is a BOUNDED negative
# (necessary-not-sufficient for unreachability) — the honest read for a softlock
# signal, exactly as ``g4.refute_prefix`` (oracle 1c) treats a no-success budget
# exhaustion. Engine-agnostic: py / js / gdscript all speak ``run_batch``.


class _PrefixExecutor:
    """Wrap an executor so every episode is REPLAYED after a fixed action ``prefix``
    — i.e. the "state under test" is expressed as 'replay this prefix from spawn',
    the only portable, engine-agnostic handle on a reached state we have (the same
    convention ``g4``'s private prefix wrapper and the softlock CONFIRM oracle use).
    Ticks/checkpoints/actions are re-based so continuations look like they started at
    the prefix. Kept local (a ~20-line mirror) so this module never imports an
    adversary-owned private symbol."""

    def __init__(self, inner, prefix):
        self._inner = inner
        self._prefix = list(prefix or [])
        self.batched = getattr(inner, "batched", False)

    def run_batch(self, game_source, episodes, max_ticks, frames_every=0,
                  escape_margin=None):
        p, plen = self._prefix, len(self._prefix)
        specs = [{"seed": e.get("seed", 0), "actions": p + list(e.get("actions", []))}
                 for e in episodes]
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


def _first_failure(episodes):
    """The first rollout dict that ended in a ``failure`` terminal, or None."""
    return next((ep for ep in (episodes or []) if ep.get("result") == "failure"),
                None)


def _failure_witness(ep, world_seed):
    return {"seed": world_seed, "actions": list(ep.get("actions") or []),
            "ticks": int(ep.get("ticks", 0) or 0)}


def terminal_reachable(executor, game_source, actions, *, prefix=(),
                       horizon=None, budget=None):
    """Can play reach a TERMINAL — success OR failure — from the state reached by
    ``prefix`` (empty prefix = spawn)?  Runs the certified Go-Explore G3 solver on
    continuations of the prefix and reads BOTH terminal kinds off the rollouts.

    Returns::

        {"reachable": bool,
         "kind":     "success" | "failure" | None,
         "verdict":  "reachable" | "env_softlock",
         "witness":  {"seed", "actions", "ticks"} | None,
         "replays":  int, "prefix_len": int}

    ``reachable == True`` is PROVEN — a replayable continuation hits a terminal.
    ``verdict == "env_softlock"`` (``reachable == False``) is a BOUNDED negative: no
    terminal was found within ``budget`` — the honest signal for a real environment
    softlock. This is the principled stuck-vs-refusal separator the plan references:
    the adversary CONFIRM layer and the feedback loop both read it to tell a stuck
    ENVIRONMENT (this) from an idle AGENT (a state that IS terminal-reachable but
    whose own trajectory refuses to advance — not a game defect)."""
    from harness.verify import gameverify as gv
    from harness.verify import treesolve as ts
    actions = list(actions or [])
    horizon = gv.PROBE_HORIZON if horizon is None else int(horizon)
    prefix = tuple(prefix or ())
    ex = _PrefixExecutor(executor, prefix) if prefix else executor

    witness, episodes, replays, _tree = ts._tree_search(
        ex, game_source, actions, horizon, budget=budget)
    if witness is not None:
        return {"reachable": True, "kind": "success", "verdict": "reachable",
                "witness": witness, "replays": replays, "prefix_len": len(prefix)}

    # No success within budget — did any rollout LOSE (a failure terminal)?  A
    # failure ends the episode just as a win does, so it too proves the state can
    # still leave non-terminal limbo.
    fail = _first_failure(episodes)
    if fail is not None:
        return {"reachable": True, "kind": "failure", "verdict": "reachable",
                "witness": _failure_witness(fail, gv.WORLD_SEED),
                "replays": replays, "prefix_len": len(prefix)}

    return {"reachable": False, "kind": None, "verdict": "env_softlock",
            "witness": None, "replays": replays, "prefix_len": len(prefix)}


# Broad adversarial failure-seeking defaults ([eng.]).
FAILURE_RANDOM_PLANS = 24     # seeded random macro rollouts in the failure sweep [eng.]


def failure_reachable(executor, game_source, actions, *, horizon=None,
                      budget=None, n_random=FAILURE_RANDOM_PLANS):
    """Does ANY reachable state trigger ``is_failure()``?  Drives a broad ADVERSARIAL
    sweep — every action SPAMMED to the horizon (coverage: drift into a wall / hazard
    / out-of-bounds), a batch of seeded RANDOM macro plans, and, only if those find
    nothing, a bounded INVERTED-objective tree search that steers toward stale/losing
    regions — and scans for a rollout that ends in ``failure``.

    Success-agnostic BY DESIGN: unlike :func:`terminal_reachable`, it never treats a
    win as the goal, so it still surfaces a losable condition even when success is
    trivial (the demo 'success always wins the race' pattern this gate must catch).

    Returns ``{"reachable": bool, "witness": {...}|None, "n_plans": int,
    "n_failed": int}``. NECESSARY-not-SUFFICIENT for UNfailability: ``reachable ==
    False`` means no adversarial rollout in the budget ever lost — the failure
    detector is (empirically) unreachable, or the game truly cannot be lost."""
    from harness.verify import gameverify as gv
    import random as _random
    actions = list(actions or [])
    horizon = gv.PROBE_HORIZON if horizon is None else int(horizon)
    if not actions:
        return {"reachable": False, "witness": None, "n_plans": 0, "n_failed": 0}

    # Pass 1 — cheap, deterministic: spam-each-action coverage + seeded random macros.
    specs = [{"seed": gv.WORLD_SEED, "actions": [a] * horizon} for a in actions]
    specs += [{"seed": gv.WORLD_SEED,
               "actions": gv._macro_plan(_random.Random(i), actions, horizon)}
              for i in range(int(n_random))]
    recs = executor.run_batch(game_source, specs, horizon)
    failed = [ep for ep in recs if ep.get("result") == "failure"]
    n_plans, n_failed = len(specs), len(failed)
    if failed:
        return {"reachable": True, "witness": _failure_witness(failed[0], gv.WORLD_SEED),
                "n_plans": n_plans, "n_failed": n_failed}

    # Pass 2 — steer: an inverted-objective tree search hunts the stale/losing
    # regions the flat sweep may miss (subtle timeouts, resource depletion).
    from harness.verify import treesolve as ts
    try:
        _w, episodes, _r, _t = ts._tree_search(
            executor, game_source, actions, horizon,
            select=ts._select_leaves_inverted, budget=budget)
    except Exception:
        episodes = []
    fail = _first_failure(episodes)
    n_plans += len(episodes)
    if fail is not None:
        return {"reachable": True, "witness": _failure_witness(fail, gv.WORLD_SEED),
                "n_plans": n_plans, "n_failed": n_failed + 1}
    return {"reachable": False, "witness": None, "n_plans": n_plans, "n_failed": n_failed}


# ======================================================================== #
# WAVE 2 — SPACE / PROPORTION: the dead-space (space-utilization) ratio.
# ======================================================================== #
# DEMO_GAP_ANALYSIS.md §Gap 3 (dead space, ranked HIGH): our generated worlds are
# 20-69x emptier than the reference demos — a radius-16 puck on an 800x600 table, the
# mechanic confined to a sliver. Most frames of any rollout show featureless drift.
# The instrument this gap wants is a CHEAP, purely-geometric FACT computed from the
# game's OWN t=0 geometry (the same body facts the G0.5 flood reads): how big is the
# declared PLAYFIELD relative to the SPAN the action actually uses?
#
# THE METRIC (dimension-aware):
#   * PLAYFIELD box = the declared world box [0, w] x [0, h] (x [0, d]) unioned with
#     every body position and every static wall footprint — "the extent the game
#     presents". For a 3D game whose world_size only bounds x,y, the depth extent comes
#     from the geometry (a z-flat game reads as z-thin, not z-empty).
#   * ACTION-SPAN box = the bounding box of the controlled body's footprint together
#     with every REACHABLE checkpoint/goal (the non-wall targets the G0.5 flood can
#     reach) — "the region the mechanic touches". The controlled body is the FLOOR of
#     the span, so a single-goal 'survive' game still has a meaningful span.
#   * measure_ratio = playfield_measure / span_measure (AREA in 2D, VOLUME in 3D):
#     "how many action-spans fit in the world".
#   * linear_ratio  = measure_ratio ** (1 / dims): the dimension-NORMALISED reading,
#     "the world is ~N times larger than the action needs, per axis" — directly
#     comparable across 2D and 3D and the number the repair directive quotes.
#
# NECESSARY-not-SUFFICIENT and ADVISORY by construction (see gameverify._dead_space_gate):
# a bounded heuristic over static geometry, never a hard cert-block. The threshold lives
# HERE, harness-side, never on any generation surface.

# Dead-space thresholds ([eng.], dimension-aware — thresholded per-dimension on the
# LINEAR ratio = measure_ratio**(1/dims), so the 2D AREA ratio and the 3D VOLUME ratio
# become comparable per-axis numbers). Calibrated on the reference fixtures' OWN geometry
# so none false-flags, with margin: mini_collect 2D ~2.9, losable 2D ~3.8,
# mini_collect_3d ~4.0 (a plane-locked 3D game, thin in y — the tightest reference). A
# game whose world is >5x the action span PER AXIS is >~96% empty area (2D) / >99.2%
# empty volume (3D): dead space. Both are 5.0 after calibration (the 3D reference sits at
# 4.0, so 3D cannot be stricter without a false reject); kept as separate knobs.
DEAD_SPACE_LINEAR_2D = 5.0
DEAD_SPACE_LINEAR_3D = 5.0
SPAN_MIN_EXTENT = 2.0 * MIN_CELL   # px: floor a degenerate span axis (avoid /0) [eng.]


def _dead_space_threshold(dims: int) -> float:
    return DEAD_SPACE_LINEAR_2D if dims == 2 else DEAD_SPACE_LINEAR_3D


def _measure(extent) -> float:
    m = 1.0
    for e in extent:
        m *= float(e)
    return m


def space_utilization(bodies, world_size, *, clearance=None):
    """The dead-space / space-utilization proportion FACT from t=0 geometry (DEMO_GAP §Gap 3).

    ``bodies``     : the serve host's ``geometry`` facts — the SAME list the G0.5 flood
                     reads (``{pos, static, sensor, controlled, aabb?/half_extents?/radius?}``).
    ``world_size`` : the declared ``[w, h]`` (2D bound; a 3D game keeps its depth extent
                     from the geometry). ``clearance`` overrides the controlled radius.

    Returns a dict (or ``None`` when there is not enough geometry — no controlled spawn):

        {"dims", "playfield", "span", "playfield_measure", "span_measure",
         "measure_ratio", "linear_ratio", "threshold", "dead_space", "n_targets",
         "n_reachable", "detail"}

    ``dead_space`` is ``linear_ratio > threshold[dims]`` — a BOUNDED heuristic, advisory
    only (see the section header). Pure geometry: no engine, no physics, deterministic."""
    spawn, clr, targets, occ = targets_and_occupancy(bodies)
    if spawn is None:
        return None                                  # no controlled body -> nothing to measure
    dims = len(spawn)
    clr = float(clearance) if clearance is not None else float(clr)
    ws = [float(v) for v in (world_size or [])]

    # Which targets can the action actually reach? Reuse the G0.5 flood so a walled-off
    # marker does not count toward the span (it cannot be part of the play). With no
    # occupancy the flood is trivial -> every target is reachable.
    reachable = list(targets)
    if occ and targets:
        res = check_reachability(spawn, targets, occ, ws, clearance=clr)
        blocked = set(res.get("unreachable") or [])
        reachable = [t for t in targets if t.get("name") not in blocked]

    # --- PLAYFIELD box: world box (per bounded axis) U every body position U walls. ---
    all_pts = [spawn] + [t["pos"] for t in targets
                         if isinstance(t.get("pos"), (list, tuple)) and len(t["pos"]) >= dims]
    lo_p = [min(float(p[i]) for p in all_pts) for i in range(dims)]
    hi_p = [max(float(p[i]) for p in all_pts) for i in range(dims)]
    for i in range(dims):
        if i < len(ws):                              # this axis has a declared world bound
            lo_p[i] = min(lo_p[i], 0.0)
            hi_p[i] = max(hi_p[i], ws[i])
    for mn, mx in occ:                               # walls can push the extent outward
        for i in range(dims):
            lo_p[i] = min(lo_p[i], float(mn[i]))
            hi_p[i] = max(hi_p[i], float(mx[i]))

    # --- ACTION-SPAN box: controlled footprint U every reachable target (get-near). ---
    lo_s = [spawn[i] - clr for i in range(dims)]
    hi_s = [spawn[i] + clr for i in range(dims)]
    for t in reachable:
        p = t["pos"]
        if not isinstance(p, (list, tuple)) or len(p) < dims:
            continue                                 # ragged pos -> skip (never crash verify)
        for i in range(dims):
            lo_s[i] = min(lo_s[i], float(p[i]) - clr)
            hi_s[i] = max(hi_s[i], float(p[i]) + clr)

    span = [max(hi_s[i] - lo_s[i], SPAN_MIN_EXTENT) for i in range(dims)]
    # The playfield always CONTAINS the span; floor it so the ratio can never dip below 1.
    playfield = [max(hi_p[i] - lo_p[i], span[i]) for i in range(dims)]

    pf_measure = _measure(playfield)
    sp_measure = _measure(span)
    measure_ratio = pf_measure / sp_measure if sp_measure > 0 else 1.0
    linear_ratio = measure_ratio ** (1.0 / dims)
    threshold = _dead_space_threshold(dims)
    dead = linear_ratio > threshold

    kind = "area" if dims == 2 else "volume"
    detail = (f"the playfield is ~{linear_ratio:.1f}x larger per axis than the region the "
              f"action uses ({dims}D {kind} ratio {measure_ratio:.0f}x); "
              + ("most of the world is empty space the mechanic never touches"
                 if dead else "the world is proportioned to the action"))
    return {"dims": dims,
            "playfield": [round(v, 2) for v in playfield],
            "span": [round(v, 2) for v in span],
            "playfield_measure": round(pf_measure, 2),
            "span_measure": round(sp_measure, 2),
            "measure_ratio": round(measure_ratio, 3),
            "linear_ratio": round(linear_ratio, 3),
            "threshold": threshold, "dead_space": bool(dead),
            "n_targets": len(targets), "n_reachable": len(reachable),
            "detail": detail}
