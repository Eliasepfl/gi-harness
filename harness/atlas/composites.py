"""WORLD × PLAY — the ATLAS's flagship composite axes, and the machinery to re-cut them.

The map's two axes are each a COHERENT CONCEPT built from the raw descriptors
``descriptors.py`` already extracts:

  * **X — STRUCTURAL RICHNESS** ("how much world is there")
  * **Y — BEHAVIOURAL RICHNESS** ("how much play is there")

Deliberately TWO axes, not one collapsed "interestingness" score: a single number would
destroy MAP-Elites' interpretability (you could not say WHY a cell is empty) and would be
trivially gameable. Two orthogonal concepts keep the empty territory readable.

WHY COMPOSITES ARE DANGEROUS (and what we do about it)
------------------------------------------------------
A composite's weighting is arbitrary and therefore Goodhart-able. Three commitments keep
this honest, and they are contractual — not stylistic:

1. **Every raw component stays in ``atlas.jsonl``.** The composite is DERIVED, never a
   replacement. Any claim this map makes can be re-derived from, or contradicted by, the
   raw descriptors. Composites are recomputed from raw at render time, never trusted from
   a stored value (they are library-relative — see below).
2. **Missing NEVER means zero.** A component that is uncomputable for a game propagates as
   ``None`` and is dropped from BOTH sides of the weighted mean — it does not silently
   score 0, which would punish a game for OUR missing instrumentation. (This exact
   None-vs-0 distinction was a bug fixed earlier in ``structural_sections``; it is
   preserved here and tested.)
3. **The weighting is published, not hidden.** The exact weights, transforms and the
   normalisation formula live in this docstring, in ``notes/ATLAS_WORLD_PLAY.md``, and in
   the rendered SVG's own legend.

THE FORMULA
-----------
For a composite ``C`` with components ``c`` of weight ``w_c`` (weights sum to 1.0), over a
library of rows ``R``:

  1. TRANSFORM   ``t_c(g) = f_c(raw_c(g))``            (f = identity, or log1p — see below)
  2. NORMALISE   ``n_c(g) = norm(t_c(g) | {t_c(r) : r ∈ R})``  -> [0, 1] (min-max | rank)
  3. AGGREGATE   ``C(g) = Σ_{c ∈ P(g)} w_c · n_c(g)  /  Σ_{c ∈ P(g)} w_c``

where ``P(g)`` = the components PRESENT (non-None) for game ``g``. Step 3 is a weighted
mean over present components — the renormalisation by present weight mass is what makes
"missing" different from "zero".

  * ``evidence(g) = Σ_{c ∈ P(g)} w_c`` — the fraction of the composite's weight actually
    backed by data for ``g``. Reported per game, in the summary, and on the map.
  * A game with ``evidence(g) < min_evidence`` (default 0.5) or fewer than
    ``MIN_COMPONENTS`` (2) present components has NO composite value (``None``) — it goes
    OFF-MAP rather than being placed on a fabricated coordinate. A "composite" of one
    component is not a composite.

NORMALISATION — why MIN-MAX is the default
------------------------------------------
``minmax``: ``(t - min) / (max - min)`` over the library's present values.
``rank``   : average-rank (midrank, ties averaged) mapped to [0, 1].

**min-max is the default, and that is a deliberate choice about honesty, not taste.** Rank
normalisation makes each axis uniform BY CONSTRUCTION: it would spread a cluster of
near-identical games evenly across the map and thereby manufacture apparent coverage —
exactly the lie this artifact exists to prevent ("the empty territory is the point"). If
the library is 76% one kind of game, min-max leaves those games piled in one corner where
you can SEE the monoculture, and leaves the honest gaps empty. Min-max's cost is real and
accepted: a single outlier compresses everyone else toward 0 — but that compression is the
TRUTH about a library with one weird game and twenty similar ones.

``rank`` remains available (``--norm rank``) for reading ORDERING when magnitudes are not
the question. It is not the flagship because a rank map cannot be read as coverage.

Both modes map a DEGENERATE component (every game identical -> no information) to 0.5 for
every game: neutral, neither rewarding nor punishing. Under ``rank`` this falls out of the
midrank arithmetic for free; under ``minmax`` it is an explicit branch. Degenerate
components are flagged in the coverage report.

TRANSFORMS — what log1p is and is NOT for
-----------------------------------------
``log1p`` is applied to unbounded, multiplicative-variation counts (body counts, witness
ticks). Note precisely what it buys: under min-max it does NOT stop a spammer from topping
a component (the max always normalises to 1.0). What it prevents is one 200-body outlier
COLLAPSING the other twenty games into a single bin. The protection against a spammer
topping the X axis is WEIGHT, not the transform — see below.

ANTI-GAMING — why X cannot be topped by spamming bodies
-------------------------------------------------------
``structural_sections`` counts only footprint-carrying static bodies (via
``reachability._aabb_of``), so padding ``state()`` with zero-extent markers cannot inflate
it — a property the 50-marker inflation test pins. This composite must not re-open that
door around the guard, so X's weight is concentrated on GUARDED components:

  * 0.90 of X's weight is on channels that resist declaration-only inflation:
    ``n_mechanics`` (dead verbs excluded by G1; mirror controls collapse to one system),
    ``structural_sections`` / ``n_static_footprint`` (footprint-guarded),
    ``gating_depth`` (a chain that must be TRAVERSED by a witness, not declared),
    ``autonomous_bodies`` (bodies observed to move).
  * 0.10 — the smallest weight — is the raw, UNGUARDED ``n_bodies`` count. Elias asked for
    the geometry counts to be on the map "as available"; they are, capped. The per-class
    splits (``n_static``/``n_dynamic``/``n_sensor``) are deliberately NOT separate
    components: they sum to ``n_bodies``, so giving each a weight would be the same
    geometry counted four times — four redundant inflation paths and 4x the influence for
    the one channel we least trust.

  A game spamming 200 zero-extent bodies moves ONLY ``n_bodies`` (the markers carry no
  footprint, no mechanics, no gating), so it can buy at most its share of 0.10 weight and
  cannot reach the top of X without real mechanics, real footprint structure and real
  gating. Pinned by ``test_composite_spam_bodies_cannot_top_structural_richness``.

  HONEST CAVEAT (a consequence of the data gap, do not paper over it): because step 3
  renormalises over PRESENT weight, missing guarded components AMPLIFY the share of the
  ones that remain. With today's host emitting no per-body extents, X's maximum attainable
  evidence is 0.58, so ``n_bodies`` carries 0.10/0.58 ≈ 17% of X rather than the intended
  10%. The unguarded channel is over-weighted until the host emits extents. See
  ``notes/ATLAS_WORLD_PLAY.md`` §data-gap.

Y's weight is 0.60 on witness-PROVEN channels (``witness_entropy`` + ``distinct_actions``
are read off an actual winning trajectory and cannot be faked without really playing).
``n_checkpoints`` (0.20) is DECLARED (G2-wellformed but declarable) and ``witness_ticks``
(0.20) is inflatable by idle padding — a documented residual, capped at 0.20 each.

LIBRARY-RELATIVE — the caveat that makes composites re-cuttable, not dogma
-------------------------------------------------------------------------
Normalisation is over the row set being rendered, so a composite value is meaningful only
RELATIVE to that library: adding a game can move every other game's coordinate. This is
why raw components stay in the JSONL and why the renderer recomputes rather than trusting
a stored composite. It is also why ``--x``/``--y`` take ANY descriptor or composite: the
map is a CHOICE OF CUT, not a commitment. ``--x auto`` restores the original
spread×coverage axis selection; ``--x solver_expansions --y witness_entropy`` restores the
legacy cut verbatim.
"""

from __future__ import annotations

import math

from harness.atlas.descriptors import DESCRIPTOR_KEYS

# Aggregation guards (documented above; both are tunable at the CLI via --min-evidence).
DEFAULT_MIN_EVIDENCE = 0.5
MIN_COMPONENTS = 2
DEFAULT_NORM = "minmax"
NORM_MODES = ("minmax", "rank")


# ======================================================================== #
# Row access (shared with render.py — the schema-level accessors)
# ======================================================================== #
def desc_of(row):
    """The descriptor dict of a row (accepts nested ``{descriptors:{...}}`` or a flat row)."""
    if isinstance(row, dict) and isinstance(row.get("descriptors"), dict):
        return row["descriptors"]
    return row if isinstance(row, dict) else {}


def num_or_none(v):
    """``v`` if it is a real number (bools are NOT numbers here), else ``None``."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# ======================================================================== #
# Normalisation
# ======================================================================== #
def normalise_column(values, mode=DEFAULT_NORM):
    """Normalise one component column to [0, 1], PRESERVING ``None``.

    Returns ``(normalised_values, degenerate)`` where ``normalised_values`` is aligned to
    ``values`` (``None`` in -> ``None`` out — a missing value is never imputed) and
    ``degenerate`` is True when every present value is identical (no information).

    ``minmax`` -> ``(v - lo) / (hi - lo)``; ``rank`` -> midrank (ties averaged) over the
    present values, mapped to [0, 1]. A degenerate column is 0.5 everywhere in both modes:
    neutral. Under ``rank`` that is not a special case — the midrank of an all-tied column
    is ``(n+1)/2``, which maps to exactly 0.5.
    """
    if mode not in NORM_MODES:
        raise ValueError(f"unknown normalisation mode {mode!r}; expected one of {NORM_MODES}")
    present = [v for v in values if v is not None]
    if not present:
        return [None] * len(values), False
    lo, hi = min(present), max(present)
    degenerate = hi <= lo
    if degenerate:
        return [None if v is None else 0.5 for v in values], True
    if mode == "minmax":
        span = hi - lo
        return [None if v is None else (v - lo) / span for v in values], False
    # rank: midrank over present values, ties averaged -> identical values keep identical
    # coordinates (a true monoculture still stacks in ONE cell rather than being spread).
    order = sorted(present)
    n = len(order)
    midrank = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and order[j + 1] == order[i]:
            j += 1
        # ranks i+1 .. j+1 (1-based) all tie -> their average
        midrank[order[i]] = ((i + 1) + (j + 1)) / 2.0
        i = j + 1
    if n == 1:
        return [None if v is None else 0.5 for v in values], False
    return [None if v is None else (midrank[v] - 1.0) / (n - 1.0) for v in values], False


# ======================================================================== #
# Component / Composite
# ======================================================================== #
_TRANSFORMS = {
    "linear": lambda v: float(v),
    # counts are non-negative; clamp defensively so a bad artifact cannot raise here
    "log1p": lambda v: math.log1p(max(0.0, float(v))),
}


class Component:
    """One weighted, normalised input to a composite axis.

    ``guarded`` records whether the channel resists declaration-only inflation — it is
    documentation that the anti-gaming argument can be AUDITED (and asserted in tests),
    not a computation input.
    """

    __slots__ = ("key", "weight", "transform", "why", "guarded")

    def __init__(self, key, weight, why, *, transform="linear", guarded=True):
        if key not in DESCRIPTOR_KEYS:
            raise ValueError(f"component {key!r} is not a descriptor: {DESCRIPTOR_KEYS}")
        if transform not in _TRANSFORMS:
            raise ValueError(f"unknown transform {transform!r}")
        self.key = key
        self.weight = float(weight)
        self.transform = transform
        self.why = why
        self.guarded = bool(guarded)

    def transform_value(self, v):
        """Apply the component's transform to a raw value; ``None`` propagates."""
        n = num_or_none(v)
        return None if n is None else _TRANSFORMS[self.transform](n)


class Composite:
    """A named, weighted composite over descriptor components — one axis of the map."""

    def __init__(self, name, label, short, components, doc=""):
        self.name = name
        self.label = label
        self.short = short
        self.components = tuple(components)
        self.doc = doc
        total = sum(c.weight for c in self.components)
        # Weights are a published contract; they must be a partition of 1.0 so that
        # "evidence" reads directly as a fraction of the composite's weight mass.
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"composite {name!r} weights sum to {total}, expected 1.0")

    @property
    def total_weight(self):
        return 1.0

    @property
    def guarded_weight(self):
        """Weight carried by channels that resist declaration-only inflation."""
        return sum(c.weight for c in self.components if c.guarded)

    def evaluate(self, rows, *, norm=DEFAULT_NORM, min_evidence=DEFAULT_MIN_EVIDENCE):
        """Compute this composite over ``rows`` (the library defines the normalisation).

        Returns ``(values, audit)`` — ``values`` aligned to ``rows`` (float in [0,1] or
        ``None`` when the evidence is too thin to place the game), and ``audit`` a per-row
        dict recording exactly which components backed the number:
        ``{value, evidence, components_present, components_missing, normalised}``.
        """
        cols, degenerate = {}, {}
        for c in self.components:
            raw = [c.transform_value(desc_of(r).get(c.key)) for r in rows]
            cols[c.key], degenerate[c.key] = normalise_column(raw, norm)

        values, audit = [], []
        for i, _row in enumerate(rows):
            num = den = 0.0
            present, missing, normalised = [], [], {}
            for c in self.components:
                v = cols[c.key][i]
                if v is None:
                    missing.append(c.key)
                    continue
                present.append(c.key)
                normalised[c.key] = round(v, 6)
                num += c.weight * v
                den += c.weight
            # Thin evidence -> NO coordinate. A game is left off-map rather than placed on
            # a number invented from one component (or from none at all).
            if len(present) < MIN_COMPONENTS or den < min_evidence * self.total_weight:
                value = None
            else:
                value = round(num / den, 6)
            values.append(value)
            audit.append({"value": value, "evidence": round(den, 6),
                          "components_present": present, "components_missing": missing,
                          "normalised": normalised})
        return values, audit

    def coverage(self, rows, *, norm=DEFAULT_NORM, min_evidence=DEFAULT_MIN_EVIDENCE):
        """Truthful coverage report for this composite over ``rows``: how many games can
        actually be placed, how much evidence backs them, and WHICH components are
        missing across the library (the queue of what the host must start emitting)."""
        values, audit = self.evaluate(rows, norm=norm, min_evidence=min_evidence)
        n = len(rows)
        per_component = {}
        for c in self.components:
            present = sum(1 for r in rows if num_or_none(desc_of(r).get(c.key)) is not None)
            per_component[c.key] = {"weight": c.weight, "n_present": present,
                                    "n_total": n, "guarded": c.guarded,
                                    "transform": c.transform}
        evid = [a["evidence"] for a in audit]
        placed = [v for v in values if v is not None]
        # The maximum evidence any game in THIS library actually achieves — the honest
        # ceiling imposed by whatever the artifact channel does not emit yet.
        return {"name": self.name, "n_total": n, "n_placed": len(placed),
                "n_full_evidence": sum(1 for e in evid if e >= 1.0 - 1e-9),
                "max_evidence": round(max(evid), 6) if evid else 0.0,
                "min_evidence_threshold": min_evidence,
                "components": per_component}


# ======================================================================== #
# THE FLAGSHIP AXES — the published weighting (see the module docstring for WHY)
# ======================================================================== #
STRUCTURAL_RICHNESS = Composite(
    "structural_richness",
    "STRUCTURAL RICHNESS — how much world is there (composite)",
    "world",
    (
        Component("n_mechanics", 0.30,
                  "distinct LIVE world-effects among declared verbs (G1 efficacy): dead "
                  "verbs are excluded and mirror controls collapse to one system, so "
                  "declaring more verbs does not buy score",
                  guarded=True),
        Component("structural_sections", 0.22,
                  "connected clusters of footprint-carrying static bodies = the world's "
                  "spatial partitions; zero-extent markers cannot inflate it",
                  guarded=True),
        Component("gating_depth", 0.18,
                  "length of the ordered checkpoint chain a WITNESS actually traversed — "
                  "must be played, cannot be declared",
                  guarded=True),
        Component("n_static_footprint", 0.12,
                  "static bodies carrying a REAL footprint (the anti-gaming guard's "
                  "visible companion)",
                  guarded=True),
        Component("autonomous_bodies", 0.08,
                  "non-controlled bodies OBSERVED to move across replay frames — world "
                  "that acts on its own",
                  guarded=True),
        Component("n_bodies", 0.10,
                  "raw t=0 body count — the one UNGUARDED channel, log-compressed and "
                  "given the smallest weight so body-spam cannot top the axis; the "
                  "per-class splits are excluded as redundant re-counts of this channel",
                  transform="log1p", guarded=False),
    ),
    doc="how much world is there",
)

BEHAVIOURAL_RICHNESS = Composite(
    "behavioural_richness",
    "BEHAVIOURAL RICHNESS — how much play is there (composite)",
    "play",
    (
        Component("witness_entropy", 0.35,
                  "Shannon entropy (bits) over the winning witness's action sequence — "
                  "how varied the proven play actually is",
                  guarded=True),
        Component("distinct_actions", 0.25,
                  "verbs actually USED in the witness; declared-but-unused verbs earn "
                  "nothing",
                  guarded=True),
        Component("n_checkpoints", 0.20,
                  "stages the game gates play into (G2-wellformed, but DECLARED — a "
                  "documented residual gaming surface, capped at this weight)",
                  guarded=False),
        Component("witness_ticks", 0.20,
                  "how long the proven play is, log-compressed: idle padding inflates "
                  "ticks linearly, so the transform blunts it and the weight caps it",
                  transform="log1p", guarded=False),
    ),
    doc="how much play is there",
)

COMPOSITES = {c.name: c for c in (STRUCTURAL_RICHNESS, BEHAVIOURAL_RICHNESS)}

# The flagship cut: WORLD × PLAY, with solver effort DEMOTED to an annotation (size).
DEFAULT_X = STRUCTURAL_RICHNESS.name
DEFAULT_Y = BEHAVIOURAL_RICHNESS.name
DEFAULT_SIZE = "solver_expansions"

# The legacy cut, kept renderable verbatim (`--x solver_expansions --y witness_entropy`).
LEGACY_X = "solver_expansions"
LEGACY_Y = "witness_entropy"

# Every valid --x/--y value: any composite, any descriptor, or "auto" (the original
# spread x coverage selection). The map is a choice of cut, not a commitment.
AUTO = "auto"


def axis_choices():
    """All accepted ``--x`` / ``--y`` values, in a stable, documented order."""
    return (AUTO,) + tuple(COMPOSITES) + tuple(DESCRIPTOR_KEYS)


def is_composite(key):
    return key in COMPOSITES


def validate_axis(key):
    """Return ``key`` if it names a composite, a descriptor, or ``auto``; else raise."""
    if key in axis_choices():
        return key
    raise ValueError(f"unknown axis {key!r}; choose one of: {', '.join(axis_choices())}")


# ======================================================================== #
# AxisSpace — the ONE place an axis key becomes numbers
# ======================================================================== #
class AxisSpace:
    """Resolves axis keys (raw descriptor OR composite) to per-row values over a FIXED
    row set.

    Composites are library-relative, so they are computed ONCE here against the rows being
    rendered and never read from a stored value — re-rendering a subset re-derives the
    coordinates for that subset, which is the only honest reading.

    The row set defines the normalisation: overlays (ghosts / frontier) are deliberately
    NOT part of it, exactly as they are excluded from the coverage math.
    """

    def __init__(self, rows, *, norm=DEFAULT_NORM, min_evidence=DEFAULT_MIN_EVIDENCE,
                 composites=None):
        if norm not in NORM_MODES:
            raise ValueError(f"unknown normalisation mode {norm!r}; expected {NORM_MODES}")
        self.rows = list(rows)
        self.norm = norm
        self.min_evidence = float(min_evidence)
        self.composites = dict(COMPOSITES if composites is None else composites)
        self._cols = {}
        self._audit = {}
        for name, comp in self.composites.items():
            vals, audit = comp.evaluate(self.rows, norm=self.norm,
                                        min_evidence=self.min_evidence)
            self._cols[name] = vals
            self._audit[name] = audit

    # -- resolution ------------------------------------------------------ #
    def is_composite(self, key):
        return key in self.composites

    def column(self, key):
        """Per-row values for ``key``, aligned to ``rows``; ``None`` where unavailable."""
        if key is None:
            return [None] * len(self.rows)
        if key in self._cols:
            return list(self._cols[key])
        return [num_or_none(desc_of(r).get(key)) for r in self.rows]

    def value(self, i, key):
        """The value of axis ``key`` for row index ``i``."""
        if key is None:
            return None
        if key in self._cols:
            return self._cols[key][i]
        return num_or_none(desc_of(self.rows[i]).get(key))

    def label(self, key):
        c = self.composites.get(key)
        return c.label if c else key

    def short(self, key):
        c = self.composites.get(key)
        return c.short if c else key

    # -- audit / coverage ------------------------------------------------ #
    def audit(self, key):
        """Per-row evidence audit for a composite axis (empty for a raw descriptor)."""
        return list(self._audit.get(key, []))

    def coverage(self, key):
        """Truthful coverage for one axis: composites report their component breakdown;
        raw descriptors report simple presence."""
        comp = self.composites.get(key)
        if comp is not None:
            return comp.coverage(self.rows, norm=self.norm, min_evidence=self.min_evidence)
        col = self.column(key)
        return {"name": key, "n_total": len(col),
                "n_placed": sum(1 for v in col if v is not None),
                "n_full_evidence": sum(1 for v in col if v is not None),
                "max_evidence": 1.0 if any(v is not None for v in col) else 0.0,
                "min_evidence_threshold": self.min_evidence, "components": {}}

    def incomplete_rows(self, keys):
        """How many rows lack FULL evidence across ``keys`` — the honest "N games have
        incomplete descriptors" number the render/summary must state."""
        n = 0
        for i in range(len(self.rows)):
            full = True
            for k in keys:
                if k is None:
                    continue
                comp = self.composites.get(k)
                if comp is not None:
                    if self._audit[k][i]["evidence"] < 1.0 - 1e-9:
                        full = False
                elif self.value(i, k) is None:
                    full = False
            if not full:
                n += 1
        return n

    def composite_rows(self, keys=None):
        """The derived, per-row composite block for ``atlas.jsonl`` — an AUDIT TRAIL
        beside the raw descriptors, never a replacement for them.

        Recorded with its provenance (``norm``, ``min_evidence``) because the value is
        library-relative and therefore only meaningful together with how it was made.
        """
        names = [k for k in (keys or self.composites) if k in self.composites]
        out = []
        for i in range(len(self.rows)):
            blk = {}
            for name in names:
                a = self._audit[name][i]
                blk[name] = {"value": a["value"], "evidence": a["evidence"],
                             "components_present": a["components_present"],
                             "components_missing": a["components_missing"],
                             "norm": self.norm, "min_evidence": self.min_evidence}
            out.append(blk)
        return out
