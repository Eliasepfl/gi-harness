"""Render the ATLAS map: a 2D chart of certified game-space where the EMPTY territory
is the point (ATLAS D1, read-only).

Given the descriptor rows ``build`` produced, :func:`render_atlas`

  1. picks the two MOST-DISCRIMINATING numeric descriptor axes for THIS dataset
     (spread × coverage, de-correlated), encoding dimension as colour and a third
     descriptor as point size;
  2. grids the two axes into cells, shades the COLONISED cells lightly and leaves the
     empty cells dark, so the uncolonised regions are visually obvious;
  3. writes a hand-built, self-contained SVG (dark panel, mono labels, one green accent
     — theme-consistent with the site) and returns the coverage math + the emptiest
     regions phrased as candidate generation targets.

Pure Python + stdlib (no PIL / cairosvg / matplotlib): the SVG is emitted as text.
"""

from __future__ import annotations

import html
import math

from harness.atlas.descriptors import DESCRIPTOR_KEYS  # noqa: F401  (schema anchor)

# --- Theme (GitHub-dark; ONE green accent) -------------------------------- #
BG = "#0d1117"
PANEL = "#161b22"
GRID = "#21262d"
BORDER = "#30363d"
CELL_FILL = "#12261a"        # faint green wash marking a colonised cell
TEXT = "#c9d1d9"
MUTED = "#8b949e"
ACCENT = "#3fb950"           # green — 2D games / headline accent
ACCENT_3D = "#58a6ff"        # blue — 3D games (a second hue, not a second accent)
UNKNOWN = "#6e7681"          # grey — unknown dimension

# Candidate numeric axes, in a FIXED tie-break priority (most behaviourally meaningful
# first). Discrimination is measured on the data; ties fall back to this order.
_CANDIDATE_AXES = (
    "witness_entropy",
    "space_util_linear_ratio",
    "solver_expansions",
    "witness_ticks",
    "distinct_actions",
    "n_checkpoints",
    "solver_episodes",
    "n_bodies",
    "n_dynamic",
    "n_static",
    "solver_ticks",
)

# The ATLAS thesis (CREATIVE_DIRECTIONS.md D1): our descriptors are PROOFS ABOUT PLAY
# (how a game was solved / attacked / proportioned), NOT code features or embeddings —
# "the non-obvious twist over academic QD". So axis selection carries a SALIENCE prior:
# a play-proof descriptor (witness entropy, solver effort, witness length, the
# proportion proof) is preferred over a bare geometry COUNT of equal spread, so the map
# foregrounds behaviour rather than being drowned out by fully-populated body counts.
# This never hardcodes WHICH axes win — spread × coverage on the actual data still
# decides; the prior only breaks the behaviour-vs-geometry tilt.
_SALIENCE = {
    "witness_entropy": 1.0,
    "space_util_linear_ratio": 1.0,   # a proportion PROOF, named in the mission brief
    "solver_expansions": 1.0,
    "witness_ticks": 0.95,
    "distinct_actions": 0.9,
    "solver_episodes": 0.9,
    "solver_ticks": 0.9,
    "n_checkpoints": 0.8,             # a design count, mildly behavioural
    "n_bodies": 0.7,                  # bare geometry counts — weakest thesis
    "n_dynamic": 0.7,
    "n_static": 0.7,
}

_AXIS_LABEL = {
    "witness_entropy": "witness action entropy (bits)",
    "space_util_linear_ratio": "space emptiness (playfield / action span, per axis)",
    "solver_expansions": "solver effort (tree node expansions)",
    "witness_ticks": "witness length (decision ticks)",
    "distinct_actions": "distinct actions used",
    "n_checkpoints": "checkpoint count",
    "solver_episodes": "solver episodes",
    "n_bodies": "body count",
    "n_dynamic": "dynamic body count",
    "n_static": "static body count",
    "solver_ticks": "solver ticks explored",
}

_AXIS_SHORT = {
    "witness_entropy": "entropy",
    "space_util_linear_ratio": "emptiness",
    "solver_expansions": "solver-effort",
    "witness_ticks": "witness-len",
    "distinct_actions": "distinct-actions",
    "n_checkpoints": "checkpoints",
    "solver_episodes": "solver-episodes",
    "n_bodies": "bodies",
    "n_dynamic": "dynamic-bodies",
    "n_static": "static-bodies",
    "solver_ticks": "solver-ticks",
}


# ======================================================================== #
# Row access + small stats
# ======================================================================== #
def _desc(row):
    """The descriptor dict of a row (accepts nested ``{descriptors:{...}}`` or a flat row)."""
    if isinstance(row, dict) and isinstance(row.get("descriptors"), dict):
        return row["descriptors"]
    return row if isinstance(row, dict) else {}


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _values(rows, key):
    return [_num(_desc(r).get(key)) for r in rows]


def _pearson(xs, ys):
    """Pearson r over the pairs where BOTH are non-null; 0.0 if under-determined."""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return 0.0
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    sxx = sum((p[0] - mx) ** 2 for p in pairs)
    syy = sum((p[1] - my) ** 2 for p in pairs)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    if sxx <= 0 or syy <= 0:
        return 0.0
    return sxy / math.sqrt(sxx * syy)


def _bin_index(v, lo, hi, n_bins):
    if hi <= lo:
        return 0
    idx = int((v - lo) / (hi - lo) * n_bins)
    return min(n_bins - 1, max(0, idx))


# ======================================================================== #
# Axis selection
# ======================================================================== #
def _axis_score(key, vals, n_rows, n_bins):
    """Discrimination score for one candidate axis: SPREAD (# distinct occupied bins,
    normalised) × sqrt(COVERAGE) × play-proof SALIENCE. A near-constant or sparsely
    populated axis scores low; ``sqrt(coverage)`` softens the tilt toward fully-populated
    geometry counts so a 64%-covered behavioural axis is not drowned out. Returns
    ``(score, coverage, n_present, n_distinct_bins)``; score 0 if ineligible.

    Eligibility: at least ``max(3, 30% of rows)`` present values and >= 2 distinct."""
    present = [v for v in vals if v is not None]
    n_present = len(present)
    coverage = n_present / n_rows if n_rows else 0.0
    min_present = max(3, int(math.ceil(0.30 * n_rows)))
    if n_present < min_present or len(set(present)) < 2:
        return (0.0, coverage, n_present, 0)
    lo, hi = min(present), max(present)
    bins = {_bin_index(v, lo, hi, n_bins) for v in present}
    salience = _SALIENCE.get(key, 0.8)
    score = salience * (len(bins) / n_bins) * math.sqrt(coverage)
    return (score, coverage, n_present, len(bins))


def select_axes(rows, n_bins=6):
    """Pick ``(x_key, y_key, size_key, scores)`` — the two most-discriminating,
    de-correlated numeric axes for THIS dataset plus a third for point size.

    x = the top-scoring axis. y = the next-best axis whose |correlation| with x is < 0.9
    (avoid a near-duplicate second axis). size = the best remaining axis (or None)."""
    n_rows = len(rows)
    ranked = []
    for key in _CANDIDATE_AXES:
        vals = _values(rows, key)
        score, cov, n_present, n_bins_occ = _axis_score(key, vals, n_rows, n_bins)
        ranked.append((score, -_CANDIDATE_AXES.index(key), key, cov, n_present, n_bins_occ))
    # Highest score first; fixed candidate order breaks ties (via the negated index).
    ranked.sort(reverse=True)
    scores = {r[2]: {"score": round(r[0], 4), "coverage": round(r[3], 3),
                     "n_present": r[4], "bins_occupied": r[5]} for r in ranked}
    eligible = [r for r in ranked if r[0] > 0.0]
    if not eligible:
        return (None, None, None, scores)
    x_key = eligible[0][2]
    xs = _values(rows, x_key)
    y_key = None
    for r in eligible[1:]:
        if abs(_pearson(xs, _values(rows, r[2]))) < 0.9:
            y_key = r[2]
            break
    if y_key is None and len(eligible) > 1:
        y_key = eligible[1][2]
    size_key = next((r[2] for r in eligible if r[2] not in (x_key, y_key)), None)
    return (x_key, y_key, size_key, scores)


# ======================================================================== #
# Grid + coverage
# ======================================================================== #
def _axis_bounds(rows, key):
    present = [v for v in _values(rows, key) if v is not None]
    if not present:
        return (0.0, 1.0)
    lo, hi = min(present), max(present)
    if hi <= lo:
        hi = lo + 1.0
    pad = (hi - lo) * 0.06
    lo_pad = lo - pad
    # Every atlas descriptor is non-negative (counts, ticks, entropy, ratios); don't let
    # symmetric padding push a lower bound below 0 into a meaningless negative axis tick.
    if lo >= 0.0:
        lo_pad = max(0.0, lo_pad)
    return (lo_pad, hi + pad)


def _tercile_label(v, lo, hi):
    if hi <= lo:
        return "mid"
    frac = (v - lo) / (hi - lo)
    return "low" if frac < 1 / 3 else ("high" if frac > 2 / 3 else "mid")


def _describe_cell(x_key, y_key, cx, cy, xb, yb):
    """A qualitative brief for an empty cell — a candidate generation target."""
    xl = _tercile_label(cx, *xb)
    yl = _tercile_label(cy, *yb)
    return f"{xl} {_AXIS_SHORT.get(x_key, x_key)} × {yl} {_AXIS_SHORT.get(y_key, y_key)}"


def compute_grid(rows, x_key, y_key, n_bins=6):
    """Grid the two axes and compute coverage. Returns a dict with the occupancy grid,
    coverage math, the placed/unplaced split, and the emptiest cells (ranked by depth
    into empty territory) phrased as candidate briefs."""
    if not x_key or not y_key:
        return {"coverage": 0.0, "n_cells": 0, "n_colonized": 0, "n_placed": 0,
                "n_unplaced": len(rows), "grid": [], "empty_cells": [],
                "xbounds": None, "ybounds": None, "n_bins": n_bins}
    xb = _axis_bounds(rows, x_key)
    yb = _axis_bounds(rows, y_key)
    grid = [[0] * n_bins for _ in range(n_bins)]     # grid[iy][ix] = point count
    placed = unplaced = 0
    for r in rows:
        x = _num(_desc(r).get(x_key))
        y = _num(_desc(r).get(y_key))
        if x is None or y is None:
            unplaced += 1
            continue
        ix = _bin_index(x, xb[0], xb[1], n_bins)
        iy = _bin_index(y, yb[0], yb[1], n_bins)
        grid[iy][ix] += 1
        placed += 1
    occupied = {(ix, iy) for iy in range(n_bins) for ix in range(n_bins) if grid[iy][ix]}
    n_cells = n_bins * n_bins
    n_colonized = len(occupied)

    # Emptiest cells = deepest into empty territory (max Chebyshev distance to any
    # occupied cell), tie-broken toward the extremes (corners of the space).
    empties = []
    for iy in range(n_bins):
        for ix in range(n_bins):
            if grid[iy][ix]:
                continue
            if occupied:
                depth = min(max(abs(ix - ox), abs(iy - oy)) for ox, oy in occupied)
            else:
                depth = 0
            extremity = max(abs(ix - (n_bins - 1) / 2), abs(iy - (n_bins - 1) / 2))
            cx = xb[0] + (ix + 0.5) * (xb[1] - xb[0]) / n_bins
            cy = yb[0] + (iy + 0.5) * (yb[1] - yb[0]) / n_bins
            empties.append({
                "ix": ix, "iy": iy, "depth": depth, "extremity": round(extremity, 2),
                "brief": _describe_cell(x_key, y_key, cx, cy, xb, yb),
                "x_range": [round(xb[0] + ix * (xb[1] - xb[0]) / n_bins, 3),
                            round(xb[0] + (ix + 1) * (xb[1] - xb[0]) / n_bins, 3)],
                "y_range": [round(yb[0] + iy * (yb[1] - yb[0]) / n_bins, 3),
                            round(yb[0] + (iy + 1) * (yb[1] - yb[0]) / n_bins, 3)],
            })
    empties.sort(key=lambda c: (c["depth"], c["extremity"]), reverse=True)
    return {"coverage": round(n_colonized / n_cells, 4) if n_cells else 0.0,
            "n_cells": n_cells, "n_colonized": n_colonized, "n_placed": placed,
            "n_unplaced": unplaced, "grid": grid, "empty_cells": empties,
            "xbounds": list(xb), "ybounds": list(yb), "n_bins": n_bins}


# ======================================================================== #
# SVG rendering
# ======================================================================== #
_W, _H = 1080, 760
_ML, _MR, _MT, _MB = 92, 300, 78, 92          # margins (right margin holds the legend)
_FONT = "'SFMono-Regular','Consolas','Liberation Mono',monospace"


def _color_for_dim(dim):
    return {"2D": ACCENT, "3D": ACCENT_3D}.get(dim, UNKNOWN)


def _size_for(v, lo, hi):
    if v is None or hi <= lo:
        return 5.0
    return 4.0 + 8.0 * (v - lo) / (hi - lo)


def _esc(s):
    return html.escape(str(s), quote=True)


def _short_slug(slug, n=15):
    s = str(slug)
    return s if len(s) <= n else s[: n - 1] + "…"


def render_svg(rows, x_key, y_key, size_key, grid_info, scores):
    n_bins = grid_info["n_bins"]
    xb, yb = grid_info["xbounds"], grid_info["ybounds"]
    px0, py0 = _ML, _MT
    pw, ph = _W - _ML - _MR, _H - _MT - _MB
    px1, py1 = px0 + pw, py0 + ph

    def sx(x):
        return px0 + (x - xb[0]) / (xb[1] - xb[0]) * pw

    def sy(y):
        return py1 - (y - yb[0]) / (yb[1] - yb[0]) * ph      # y grows upward

    size_vals = [v for v in _values(rows, size_key) if v is not None] if size_key else []
    s_lo = min(size_vals) if size_vals else 0.0
    s_hi = max(size_vals) if size_vals else 1.0

    S = []
    S.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{_W}" height="{_H}" '
             f'viewBox="0 0 {_W} {_H}" font-family="{_FONT}">')
    S.append(f'<rect width="{_W}" height="{_H}" fill="{BG}"/>')
    # Title + subtitle.
    S.append(f'<text x="{_ML}" y="34" fill="{TEXT}" font-size="21" '
             f'font-weight="bold">THE ATLAS — certified game-space</text>')
    S.append(f'<text x="{_ML}" y="56" fill="{MUTED}" font-size="12">'
             f'each point is a certified game; axes are its deterministic behaviour '
             f'descriptors; dark cells are unexplored territory</text>')
    # Plot panel.
    S.append(f'<rect x="{px0}" y="{py0}" width="{pw}" height="{ph}" fill="{PANEL}" '
             f'stroke="{BORDER}" stroke-width="1"/>')

    # Colonised-cell wash.
    grid = grid_info["grid"]
    cw, ch = pw / n_bins, ph / n_bins
    for iy in range(n_bins):
        for ix in range(n_bins):
            if grid[iy][ix]:
                gx = px0 + ix * cw
                gy = py1 - (iy + 1) * ch
                S.append(f'<rect x="{gx:.1f}" y="{gy:.1f}" width="{cw:.1f}" '
                         f'height="{ch:.1f}" fill="{CELL_FILL}"/>')
    # Grid lines.
    for i in range(n_bins + 1):
        gx = px0 + i * cw
        gy = py0 + i * ch
        S.append(f'<line x1="{gx:.1f}" y1="{py0}" x2="{gx:.1f}" y2="{py1}" '
                 f'stroke="{GRID}" stroke-width="1"/>')
        S.append(f'<line x1="{px0}" y1="{gy:.1f}" x2="{px1}" y2="{gy:.1f}" '
                 f'stroke="{GRID}" stroke-width="1"/>')

    # Axis ticks (bin edges) + labels.
    for i in range(n_bins + 1):
        vx = xb[0] + i * (xb[1] - xb[0]) / n_bins
        gx = px0 + i * cw
        S.append(f'<text x="{gx:.1f}" y="{py1 + 18}" fill="{MUTED}" font-size="10" '
                 f'text-anchor="middle">{vx:.2g}</text>')
        vy = yb[0] + i * (yb[1] - yb[0]) / n_bins
        gy = py1 - i * ch
        S.append(f'<text x="{px0 - 8}" y="{gy + 3:.1f}" fill="{MUTED}" font-size="10" '
                 f'text-anchor="end">{vy:.2g}</text>')
    S.append(f'<text x="{px0 + pw / 2:.0f}" y="{_H - 40}" fill="{TEXT}" font-size="13" '
             f'text-anchor="middle">{_esc(_AXIS_LABEL.get(x_key, x_key))} →</text>')
    S.append(f'<text x="26" y="{py0 + ph / 2:.0f}" fill="{TEXT}" font-size="13" '
             f'text-anchor="middle" transform="rotate(-90 26 {py0 + ph / 2:.0f})">'
             f'{_esc(_AXIS_LABEL.get(y_key, y_key))} →</text>')

    # Points + labels.
    # Draw densest cells' labels last so they sit on top; alternate the label's vertical
    # offset by a stable per-point parity so clustered points do not perfectly overlap.
    placed_pts = []
    for i, r in enumerate(rows):
        d = _desc(r)
        x = _num(d.get(x_key))
        y = _num(d.get(y_key))
        if x is None or y is None:
            continue
        cx, cy = sx(x), sy(y)
        rad = _size_for(_num(d.get(size_key)) if size_key else None, s_lo, s_hi)
        col = _color_for_dim(d.get("dimension"))
        slug = r.get("slug") if isinstance(r, dict) else None
        S.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{rad:.1f}" fill="{col}" '
                 f'fill-opacity="0.82" stroke="{BG}" stroke-width="1"/>')
        placed_pts.append((i, cx, cy, rad, slug))
    for i, cx, cy, rad, slug in placed_pts:
        if not slug:
            continue
        dy = -rad - 3 if (i % 2 == 0) else rad + 9   # stagger above / below
        S.append(f'<text x="{cx + rad + 3:.1f}" y="{cy + dy:.1f}" fill="{TEXT}" '
                 f'font-size="9" fill-opacity="0.88">{_esc(_short_slug(slug))}</text>')

    # Legend / stats panel (right margin).
    lx = px1 + 24
    ly = py0 + 4
    cov = grid_info["coverage"]
    S.append(f'<text x="{lx}" y="{ly + 10}" fill="{TEXT}" font-size="13" '
             f'font-weight="bold">COVERAGE</text>')
    S.append(f'<text x="{lx}" y="{ly + 34}" fill="{ACCENT}" font-size="24" '
             f'font-weight="bold">{cov * 100:.0f}%</text>')
    S.append(f'<text x="{lx}" y="{ly + 52}" fill="{MUTED}" font-size="10.5">'
             f'{grid_info["n_colonized"]} / {grid_info["n_cells"]} cells colonised '
             f'({n_bins}×{n_bins} grid)</text>')
    S.append(f'<text x="{lx}" y="{ly + 68}" fill="{MUTED}" font-size="10.5">'
             f'{grid_info["n_placed"]} games placed, {grid_info["n_unplaced"]} off-map</text>')

    # dimension legend
    yy = ly + 100
    S.append(f'<text x="{lx}" y="{yy}" fill="{TEXT}" font-size="12" '
             f'font-weight="bold">dimension</text>')
    for i, (lab, col) in enumerate((("2D", ACCENT), ("3D", ACCENT_3D), ("?", UNKNOWN))):
        yc = yy + 18 + i * 17
        S.append(f'<circle cx="{lx + 6}" cy="{yc - 4}" r="5" fill="{col}"/>')
        S.append(f'<text x="{lx + 18}" y="{yc}" fill="{MUTED}" font-size="11">{lab}</text>')
    if size_key:
        yy2 = yy + 18 + 3 * 17 + 14
        S.append(f'<text x="{lx}" y="{yy2}" fill="{TEXT}" font-size="12" '
                 f'font-weight="bold">size = {_esc(_AXIS_SHORT.get(size_key, size_key))}</text>')

    # emptiest-region briefs
    yb0 = yy + 18 + 3 * 17 + (46 if size_key else 20)
    S.append(f'<text x="{lx}" y="{yb0}" fill="{TEXT}" font-size="12" '
             f'font-weight="bold">emptiest frontiers</text>')
    seen = set()
    shown = 0
    for cell in grid_info["empty_cells"]:
        if cell["depth"] < 1 or cell["brief"] in seen:
            continue
        seen.add(cell["brief"])
        yc = yb0 + 18 + shown * 15
        S.append(f'<text x="{lx}" y="{yc}" fill="{MUTED}" font-size="10">'
                 f'• {_esc(cell["brief"])}</text>')
        shown += 1
        if shown >= 4:
            break

    S.append('</svg>')
    return "\n".join(S)


def render_atlas(rows, out_svg_path=None, *, n_bins=6):
    """The public entry point. Selects axes, computes the grid + coverage, renders the
    SVG (written to ``out_svg_path`` if given), and returns a summary dict:
    ``{axes, size_axis, coverage, n_cells, n_colonized, n_placed, n_unplaced,
    empty_cells, axis_scores, svg}``."""
    x_key, y_key, size_key, scores = select_axes(rows, n_bins=n_bins)
    grid_info = compute_grid(rows, x_key, y_key, n_bins=n_bins)
    svg = render_svg(rows, x_key, y_key, size_key, grid_info, scores) if x_key and y_key else ""
    if out_svg_path and svg:
        with open(out_svg_path, "w", encoding="utf-8") as fh:
            fh.write(svg)
    return {"axes": (x_key, y_key), "size_axis": size_key,
            "coverage": grid_info["coverage"], "n_cells": grid_info["n_cells"],
            "n_colonized": grid_info["n_colonized"], "n_placed": grid_info["n_placed"],
            "n_unplaced": grid_info["n_unplaced"],
            "empty_cells": grid_info["empty_cells"], "axis_scores": scores,
            "axis_labels": {"x": _AXIS_LABEL.get(x_key, x_key),
                            "y": _AXIS_LABEL.get(y_key, y_key)},
            "svg": svg}
