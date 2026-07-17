"""Mechanical discovery of CONTRADICTORY (near-antiparallel) action pairs (Phase 2, Elias).

Opposition between actions is derived MECHANICALLY from each action's MEASURED effect on the
controlled body -- NEVER from action NAMES (Elias's hard doctrine: no name taxonomies on any
surface). A cheap per-action probe (see :meth:`harness.rl.godot_env.GodotServeEnv.probe_effect_vectors`)
applies each action alone for a few ticks from the FIXED build seed and records the controlled
body's net displacement -- its effect vector. Two actions are CONTRADICTORY when those vectors
are near-antiparallel (cosine below a threshold) AND of comparable magnitude (neither dwarfs the
other). In chord mode the env then PROJECTS a both-pressed contradictory pair to NEITHER key
(``harness.verify.chord.project_opposition``) -- their physical net effect is ~zero, so the 2^n
action space collapses toward the natural controller semantics (e.g. 6 thrusts: 64 -> 27 =
3 axes x {-1, 0, +1}) and exploration stops wasting mass on self-cancelling combos.

Pure + numpy-only (no engine dependency) so the discovery MATH is unit-tested on synthetic
effect vectors, independent of any live probe.
"""
from __future__ import annotations

import numpy as np

# Defaults ([eng.]): near-antiparallel = cosine below -0.9 (a hard, unambiguous opposition,
# not a mild "somewhat opposite"); comparable magnitude = the larger effect is at most 3x the
# smaller (so a strong action is never paired with a weak, barely-moving one); an effect below
# MIN_MAG is treated as "no measurable effect" and never paired (a non-mover / grab action).
COS_THRESHOLD = -0.9
MAG_RATIO = 3.0
MIN_MAG = 1e-6


def antiparallel_pairs(vectors, *, cos_threshold: float = COS_THRESHOLD,
                       mag_ratio: float = MAG_RATIO, min_mag: float = MIN_MAG):
    """Discover the CONTRADICTORY action pairs from a list of per-action effect ``vectors``
    (``vectors[i]`` = action i's measured effect vector on the controlled body).

    Returns a sorted list of ``(i, j)`` index pairs (``i < j``) whose effect vectors are
    near-antiparallel (``cos(v_i, v_j) < cos_threshold``) AND of comparable magnitude
    (``max/min <= mag_ratio``). Effects with magnitude below ``min_mag`` are excluded (no
    measurable effect -> never contradictory). Pure; no action names are consulted."""
    vs = [np.asarray(v, dtype=float).reshape(-1) for v in (vectors or [])]
    mags = [float(np.linalg.norm(v)) for v in vs]
    pairs: list[tuple[int, int]] = []
    n = len(vs)
    for i in range(n):
        if mags[i] < min_mag:
            continue
        for j in range(i + 1, n):
            if mags[j] < min_mag:
                continue
            cos = float(np.dot(vs[i], vs[j]) / (mags[i] * mags[j]))
            ratio = max(mags[i], mags[j]) / max(min(mags[i], mags[j]), min_mag)
            if cos < cos_threshold and ratio <= mag_ratio:
                pairs.append((i, j))
    return pairs
