"""Breeding placement — where does a CHILD land relative to its two PARENTS on the
atlas? (ATLAS D1 breeding experiment; additive, read-only.)

Elias's question (2026-07-16, verbatim intent): after fusing two working games, "on
pourrait voir où il se positionne par rapport aux deux jeux parents sur la map ATLAS".
This module answers it deterministically on the (solver-effort, entropy) plane:

  placement(child, parent_a, parent_b, rows=None, ...) -> dict
      the child's geometric position vs the parent segment: BETWEEN the parents,
      BEYOND them (left the segment - new territory), or COLLAPSED onto one parent.
  render_breeding_svg(rows, triads, out_path=None, ...) -> str
      the atlas map with parent-parent-child triangles overlaid (dashed parent
      baseline, solid inheritance edges; children coloured by arm).

Axes default to ``solver_expansions`` (x - how hard the machine had to think) and
``witness_entropy`` (y - how varied the winning play is): both are certification
PROOFS, the atlas thesis. Distances are computed after LINEAR min-max normalisation
over the supplied row set (matching render.py's linear binning), so "collapsed /
between / beyond" is judged in map units, not raw units. Normalisation bounds,
thresholds and labels all live HARNESS-SIDE - nothing here ever reaches the LLM
surface, so the anti-anchoring bar is untouched.

Pure stdlib; a missing descriptor yields the honest label "off_map", never a crash.
"""

from __future__ import annotations

import html
import math

from harness.atlas.render import (
    ACCENT, ACCENT_3D, BG, BORDER, GRID, MUTED, PANEL, TEXT, UNKNOWN,
    _AXIS_LABEL, _FONT,
)

# Child colours by breeding arm (A = objective search over sources, B = prompt
# fusion). Orange/violet: distinct from the 2D/3D dimension hues of the base map.
ARM_COLORS = {"A": "#f0883e", "B": "#d2a8ff"}

# Placement thresholds, in NORMALISED map units (each axis min-max scaled to [0,1]
# over the row set). Judgement calls, documented as such:
#   collapsed : child within max(15% of the parent gap, 0.05) of one parent -> it
#               effectively cloned that parent's behaviour point.
#   between   : child projects INSIDE the parent segment (0<=t<=1) and sits within
#               max(25% of the parent gap, 0.08) of the segment -> interpolation.
#   beyond    : everything else -> the child left the parents' axis (new territory).
_COLLAPSE_FRAC = 0.15
_COLLAPSE_MIN = 0.05
_BETWEEN_FRAC = 0.25
_BETWEEN_MIN = 0.08

DEFAULT_X = "solver_expansions"
DEFAULT_Y = "witness_entropy"


# ======================================================================== #
# Row access + normalisation
# ======================================================================== #
def _desc(row):
    """Descriptor dict of a row (accepts ``{descriptors: {...}}`` or a flat dict)."""
    if isinstance(row, dict) and isinstance(row.get("descriptors"), dict):
        return row["descriptors"]
    return row if isinstance(row, dict) else {}


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _xy(row, x_key, y_key):
    d = _desc(row)
    return _num(d.get(x_key)), _num(d.get(y_key))


def _bounds(rows, key):
    """(lo, hi) over the present values of ``key`` in ``rows``; degenerate spans get
    a unit width so normalisation never divides by zero."""
    vals = [v for v in (_num(_desc(r).get(key)) for r in rows) if v is not None]
    if not vals:
        return (0.0, 1.0)
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        hi = lo + 1.0
    return (float(lo), float(hi))


def _norm(v, lo, hi):
    return (v - lo) / (hi - lo)


def _dist(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


# ======================================================================== #
# Placement
# ======================================================================== #
def placement(child, parent_a, parent_b, rows=None,
              x_key=DEFAULT_X, y_key=DEFAULT_Y):
    """Classify the child's position vs its parents on the (x_key, y_key) plane.

    ``child`` / ``parent_a`` / ``parent_b``: atlas rows (or bare descriptor dicts).
    ``rows``: the row set that fixes the normalisation bounds (pass the whole
    library so distances are in the SAME map units for every triad); defaults to
    the three points themselves.

    Returns a dict: ``label`` in {"between", "beyond", "collapsed_onto_parent_a",
    "collapsed_onto_parent_b", "parents_coincide", "off_map"}, plus the raw and
    normalised coordinates, pairwise distances (normalised units), the projection
    parameter ``t`` along the parent segment and the perpendicular distance
    ``d_perp``. All numbers are deterministic; missing descriptors -> "off_map".
    """
    cx, cy = _xy(child, x_key, y_key)
    ax, ay = _xy(parent_a, x_key, y_key)
    bx, by = _xy(parent_b, x_key, y_key)
    out = {
        "x_key": x_key, "y_key": y_key,
        "child_raw": [cx, cy], "parent_a_raw": [ax, ay], "parent_b_raw": [bx, by],
        "label": "off_map", "t": None, "d_perp": None,
        "d_child_a": None, "d_child_b": None, "d_parents": None,
    }
    if None in (cx, cy, ax, ay, bx, by):
        return out

    base = rows if rows else [child, parent_a, parent_b]
    xb = _bounds(base, x_key)
    yb = _bounds(base, y_key)
    C = (_norm(cx, *xb), _norm(cy, *yb))
    A = (_norm(ax, *xb), _norm(ay, *yb))
    B = (_norm(bx, *xb), _norm(by, *yb))

    d_ab = _dist(A, B)
    d_ca = _dist(C, A)
    d_cb = _dist(C, B)
    out.update({"child_norm": [round(C[0], 4), round(C[1], 4)],
                "parent_a_norm": [round(A[0], 4), round(A[1], 4)],
                "parent_b_norm": [round(B[0], 4), round(B[1], 4)],
                "d_child_a": round(d_ca, 4), "d_child_b": round(d_cb, 4),
                "d_parents": round(d_ab, 4),
                "x_bounds": list(xb), "y_bounds": list(yb)})

    collapse_r = max(_COLLAPSE_FRAC * d_ab, _COLLAPSE_MIN)
    if d_ab < 1e-9:
        out["label"] = ("collapsed_onto_parent_a" if d_ca <= collapse_r
                        else "parents_coincide")
        return out

    # Projection of C onto the AB segment.
    abx, aby = B[0] - A[0], B[1] - A[1]
    t = ((C[0] - A[0]) * abx + (C[1] - A[1]) * aby) / (d_ab * d_ab)
    proj = (A[0] + t * abx, A[1] + t * aby)
    d_perp = _dist(C, proj)
    out["t"] = round(t, 4)
    out["d_perp"] = round(d_perp, 4)

    if d_ca <= collapse_r or d_cb <= collapse_r:
        out["label"] = ("collapsed_onto_parent_a" if d_ca <= d_cb
                        else "collapsed_onto_parent_b")
    elif 0.0 <= t <= 1.0 and d_perp <= max(_BETWEEN_FRAC * d_ab, _BETWEEN_MIN):
        out["label"] = "between"
    else:
        out["label"] = "beyond"
    return out


# ======================================================================== #
# Triangle-overlay SVG
# ======================================================================== #
_W, _H = 1080, 760
_ML, _MR, _MT, _MB = 92, 300, 78, 92


def _esc(s):
    return html.escape(str(s), quote=True)


def _short(slug, n=17):
    s = str(slug)
    return s if len(s) <= n else s[: n - 1] + "…"


def _color_for_dim(dim):
    return {"2D": ACCENT, "3D": ACCENT_3D}.get(dim, UNKNOWN)


def render_breeding_svg(rows, triads, out_path=None,
                        x_key=DEFAULT_X, y_key=DEFAULT_Y):
    """Render the atlas plane with parent-parent-child triangles overlaid.

    ``rows``  : the full library's atlas rows (``{slug, descriptors}``), indexed by
                slug — parents AND children must be present to be drawn.
    ``triads``: ``[{"child": slug, "parent_a": slug, "parent_b": slug, "arm": "A"}]``.

    Library games are dim points (coloured by dimension); parents keep their hue at
    full opacity; children are coloured by arm (orange = A, violet = B). Each triad
    draws a dashed parent baseline A--B and solid inheritance edges A--child /
    B--child. Rows or triad members missing either axis are skipped (they are
    exactly the "off_map" placements). Returns the SVG text; writes it to
    ``out_path`` when given.
    """
    by_slug = {r.get("slug"): r for r in rows if isinstance(r, dict)}
    xb = _bounds(rows, x_key)
    yb = _bounds(rows, y_key)
    # 6% padding, floored at 0 (all atlas descriptors are non-negative).
    pad_x = (xb[1] - xb[0]) * 0.06
    pad_y = (yb[1] - yb[0]) * 0.06
    xb = (max(0.0, xb[0] - pad_x), xb[1] + pad_x)
    yb = (max(0.0, yb[0] - pad_y), yb[1] + pad_y)

    px0, py0 = _ML, _MT
    pw, ph = _W - _ML - _MR, _H - _MT - _MB
    px1, py1 = px0 + pw, py0 + ph

    def sx(x):
        return px0 + (x - xb[0]) / (xb[1] - xb[0]) * pw

    def sy(y):
        return py1 - (y - yb[0]) / (yb[1] - yb[0]) * ph

    def pt(slug):
        r = by_slug.get(slug)
        if r is None:
            return None
        x, y = _xy(r, x_key, y_key)
        if x is None or y is None:
            return None
        return (sx(x), sy(y))

    tri_slugs = set()
    for tr in triads:
        tri_slugs |= {tr.get("child"), tr.get("parent_a"), tr.get("parent_b")}

    S = []
    S.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{_W}" height="{_H}" '
             f'viewBox="0 0 {_W} {_H}" font-family="{_FONT}">')
    S.append(f'<rect width="{_W}" height="{_H}" fill="{BG}"/>')
    S.append(f'<text x="{_ML}" y="34" fill="{TEXT}" font-size="21" font-weight="bold">'
             f'THE ATLAS — breeding triangles (parents → child)</text>')
    S.append(f'<text x="{_ML}" y="56" fill="{MUTED}" font-size="12">'
             f'dashed line joins the two parents; solid edges join each parent to the '
             f'child; child colour = breeding arm</text>')
    S.append(f'<rect x="{px0}" y="{py0}" width="{pw}" height="{ph}" fill="{PANEL}" '
             f'stroke="{BORDER}" stroke-width="1"/>')

    # Light grid (quarters) + axis labels.
    for i in range(1, 4):
        gx = px0 + i * pw / 4
        gy = py0 + i * ph / 4
        S.append(f'<line x1="{gx:.1f}" y1="{py0}" x2="{gx:.1f}" y2="{py1}" '
                 f'stroke="{GRID}" stroke-width="1"/>')
        S.append(f'<line x1="{px0}" y1="{gy:.1f}" x2="{px1}" y2="{gy:.1f}" '
                 f'stroke="{GRID}" stroke-width="1"/>')
    for i in range(5):
        vx = xb[0] + i * (xb[1] - xb[0]) / 4
        vy = yb[0] + i * (yb[1] - yb[0]) / 4
        S.append(f'<text x="{px0 + i * pw / 4:.1f}" y="{py1 + 18}" fill="{MUTED}" '
                 f'font-size="10" text-anchor="middle">{vx:.3g}</text>')
        S.append(f'<text x="{px0 - 8}" y="{py1 - i * ph / 4 + 3:.1f}" fill="{MUTED}" '
                 f'font-size="10" text-anchor="end">{vy:.3g}</text>')
    S.append(f'<text x="{px0 + pw / 2:.0f}" y="{_H - 40}" fill="{TEXT}" font-size="13" '
             f'text-anchor="middle">{_esc(_AXIS_LABEL.get(x_key, x_key))} →</text>')
    S.append(f'<text x="26" y="{py0 + ph / 2:.0f}" fill="{TEXT}" font-size="13" '
             f'text-anchor="middle" transform="rotate(-90 26 {py0 + ph / 2:.0f})">'
             f'{_esc(_AXIS_LABEL.get(y_key, y_key))} →</text>')

    # Triangle edges first (under the points).
    for tr in triads:
        pa, pb, pc = pt(tr.get("parent_a")), pt(tr.get("parent_b")), pt(tr.get("child"))
        col = ARM_COLORS.get(str(tr.get("arm", "")).upper(), TEXT)
        if pa and pb:
            S.append(f'<line x1="{pa[0]:.1f}" y1="{pa[1]:.1f}" x2="{pb[0]:.1f}" '
                     f'y2="{pb[1]:.1f}" stroke="{MUTED}" stroke-width="1" '
                     f'stroke-dasharray="5 4" stroke-opacity="0.7"/>')
        for pp in (pa, pb):
            if pp and pc:
                S.append(f'<line x1="{pp[0]:.1f}" y1="{pp[1]:.1f}" x2="{pc[0]:.1f}" '
                         f'y2="{pc[1]:.1f}" stroke="{col}" stroke-width="1.6" '
                         f'stroke-opacity="0.9"/>')

    # Library points (dim), then triad members (bright) + labels.
    child_arm = {}
    for tr in triads:
        child_arm[tr.get("child")] = str(tr.get("arm", "")).upper()
    lab_i = 0
    for r in rows:
        slug = r.get("slug") if isinstance(r, dict) else None
        p = pt(slug)
        if p is None:
            continue
        in_tri = slug in tri_slugs
        if slug in child_arm:
            col = ARM_COLORS.get(child_arm[slug], TEXT)
            rad, op = 7.0, 0.95
        else:
            col = _color_for_dim(_desc(r).get("dimension"))
            rad, op = (6.0, 0.9) if in_tri else (4.0, 0.35)
        S.append(f'<circle cx="{p[0]:.1f}" cy="{p[1]:.1f}" r="{rad}" fill="{col}" '
                 f'fill-opacity="{op}" stroke="{BG}" stroke-width="1"/>')
        if in_tri:
            dy = -rad - 4 if (lab_i % 2 == 0) else rad + 11
            lab_i += 1
            S.append(f'<text x="{p[0] + rad + 3:.1f}" y="{p[1] + dy:.1f}" '
                     f'fill="{TEXT}" font-size="9.5">{_esc(_short(slug))}</text>')

    # Legend.
    lx, ly = px1 + 24, py0 + 4
    S.append(f'<text x="{lx}" y="{ly + 10}" fill="{TEXT}" font-size="13" '
             f'font-weight="bold">LEGEND</text>')
    entries = [("parent (2D)", ACCENT), ("parent (3D)", ACCENT_3D),
               ("child — arm A (sources)", ARM_COLORS["A"]),
               ("child — arm B (prompts)", ARM_COLORS["B"]),
               ("library game", UNKNOWN)]
    for i, (lab, col) in enumerate(entries):
        yc = ly + 34 + i * 18
        S.append(f'<circle cx="{lx + 6}" cy="{yc - 4}" r="5" fill="{col}"/>')
        S.append(f'<text x="{lx + 18}" y="{yc}" fill="{MUTED}" font-size="11">'
                 f'{_esc(lab)}</text>')
    yc0 = ly + 34 + len(entries) * 18 + 14
    S.append(f'<text x="{lx}" y="{yc0}" fill="{TEXT}" font-size="12" '
             f'font-weight="bold">triads</text>')
    for i, tr in enumerate(triads):
        yc = yc0 + 18 + i * 15
        col = ARM_COLORS.get(str(tr.get("arm", "")).upper(), TEXT)
        S.append(f'<text x="{lx}" y="{yc}" fill="{col}" font-size="10">'
                 f'{_esc(_short(tr.get("child"), 24))}  '
                 f'[{_esc(str(tr.get("arm", "?")))}]</text>')
    S.append('</svg>')
    svg = "\n".join(S)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(svg)
    return svg
