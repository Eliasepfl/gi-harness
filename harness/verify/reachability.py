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
