# THE ATLAS — WORLD × PLAY axes

Flagship cut of the certified-game-space map. Replaces the old **solver-effort × witness-
entropy** cut, which Elias identified as failing on two counts:

1. **Solver effort dies as an axis.** As games get harder the tree solves nothing, so the
   axis goes blank exactly where the interesting games are. Worse, under the approved
   chord-pivot / witness-RL escalade it becomes **dual-currency** — tree node expansions
   and RL sample complexity are not the same unit — so a single "effort" number silently
   mixes scales and stops meaning anything.
2. **Witness action entropy alone is a thin proxy for "interesting."** The L1 structural
   descriptors (mechanics, spatial partitions, gating…) that already exist should be ON
   the map, not hidden in a side panel.

## The two axes

Deliberately **two coherent concepts, not one collapsed "interestingness" score.** A single
composite would destroy MAP-Elites' interpretability (you could not say *why* a cell is
empty) and would be trivially gameable. Two orthogonal axes keep the empty territory
readable.

- **X — STRUCTURAL RICHNESS** ("how much world is there")
- **Y — BEHAVIOURAL RICHNESS** ("how much play is there")

Solver effort **demotes to an annotation** (point size), and its **provenance**
(`witness_source`: tree | rl) is drawn as a ring so the dual-currency size encoding is never
read blind. Dimension (2D/3D) stays the point colour.

## The formula

For a composite `C` with components `c` of weight `w_c` (weights partition 1.0), over the
library of rows `R` being rendered:

```
1. TRANSFORM   t_c(g) = f_c(raw_c(g))                       f = identity | log1p
2. NORMALISE   n_c(g) = norm(t_c(g) | {t_c(r) : r ∈ R})     minmax | rank -> [0,1]
3. AGGREGATE   C(g)   = Σ_{c∈P(g)} w_c·n_c(g)  /  Σ_{c∈P(g)} w_c
```

`P(g)` = the components **present** (non-None) for game `g`. Step 3 is a weighted mean over
present components — renormalising by present weight is what makes **missing ≠ zero**.

- `evidence(g) = Σ_{c∈P(g)} w_c` — the fraction of the composite's weight actually backed by
  data. Reported per game, in the summary, and on the map.
- A game with `evidence(g) < min_evidence` (default **0.5**) or `< 2` present components has
  **no composite value** → it goes **off-map** rather than onto a fabricated coordinate.

### Normalisation: min-max is the default, on purpose

- `minmax`: `(t-min)/(max-min)` over the library's present values. **The flagship.**
- `rank`: average-rank (ties averaged) → [0,1]. Available via `--norm rank`.

Rank normalisation makes each axis uniform **by construction** — it spreads a cluster of
near-identical games evenly across the map and thereby **manufactures apparent coverage**,
the exact lie this artifact exists to prevent. Min-max leaves a 76%-monoculture piled in one
corner where you can *see* it, and leaves the honest gaps empty. Min-max's cost (one outlier
compresses everyone toward 0) is the **truth** about a library with one weird game and twenty
similar ones. Read `rank` for ORDERING, never as coverage.

Both modes map a **degenerate** component (every game identical) to 0.5 for all games:
neutral, neither rewarding nor punishing. Composite axes are drawn on their **fixed [0,1]
domain**, never zoomed to the data — zooming would re-spread a cluster and fake coverage.

### Transforms: what log1p is and is NOT for

`log1p` on unbounded counts (`n_bodies`, `witness_ticks`) does **not** stop a spammer topping
a component (the max always normalises to 1.0). It prevents one 200-body outlier from
collapsing everyone else into a single bin. The protection against a spammer topping the axis
is **weight**, not the transform.

## The published weighting (and WHY)

### X — STRUCTURAL RICHNESS  (guarded weight = 0.90)

| weight | component            | guarded | transform | why |
|-------:|----------------------|:-------:|-----------|-----|
| 0.30 | `n_mechanics`          | ✅ | linear | distinct LIVE world-effects (G1 efficacy); dead verbs excluded, mirror controls collapse to one system — declaring more verbs buys nothing |
| 0.22 | `structural_sections`  | ✅ | linear | connected clusters of footprint-carrying static bodies = spatial partitions; zero-extent markers cannot inflate it |
| 0.18 | `gating_depth`         | ✅ | linear | ordered checkpoint chain a **witness traversed** — must be played, not declared |
| 0.12 | `n_static_footprint`   | ✅ | linear | static bodies carrying a REAL footprint (the anti-gaming guard's visible companion) |
| 0.08 | `autonomous_bodies`    | ✅ | linear | non-controlled bodies OBSERVED to move across replay frames |
| 0.10 | `n_bodies`             | ⚠️ **UNGUARDED** | log1p | raw t=0 body count — the one channel a game can pad; smallest weight, log-compressed |

**Anti-gaming.** 0.90 of X's weight sits on channels that resist declaration-only inflation.
A game spamming 200 zero-extent bodies moves **only** `n_bodies` (markers carry no footprint,
no mechanics, no gating), so it can buy at most that channel's capped 0.10 share and cannot
top X. Pinned by `test_composite_spam_bodies_cannot_top_structural_richness` and, at the
descriptor level, by the 50-marker inflation test that stays green.

The per-class splits (`n_static`/`n_dynamic`/`n_sensor`) are **deliberately excluded**: they
sum to `n_bodies`, so weighting each would be the same geometry counted four times — four
redundant inflation paths and 4× the influence for the channel we least trust.

### Y — BEHAVIOURAL RICHNESS  (witness-proven weight = 0.60)

| weight | component          | guarded | transform | why |
|-------:|--------------------|:-------:|-----------|-----|
| 0.35 | `witness_entropy`    | ✅ | linear | Shannon entropy (bits) over the winning witness's actions — **kept, inside the composite, per Elias** |
| 0.25 | `distinct_actions`   | ✅ | linear | verbs actually USED in the witness; declared-but-unused verbs earn nothing |
| 0.20 | `n_checkpoints`      | ⚠️ | linear | stages the game gates play into (G2-wellformed but DECLARED — a documented residual, capped) |
| 0.20 | `witness_ticks`      | ⚠️ | log1p | length of proven play; idle padding inflates ticks, so log blunts it and weight caps it |

`witness_entropy` + `distinct_actions` (0.60) are read off an **actual winning trajectory**
and cannot be faked without really playing.

## <a name="data-gap"></a>Known data gap (do NOT paper over it)

The serve host's t=0 `run_check` geometry currently emits only `pos` + flags
(`controlled/static/sensor`) per body — **no per-body extents** (`half_extents`/`aabb`/
`radius`). Consequences, handled honestly (never as false zeros):

- `structural_sections` and `n_static_footprint` come back **None** for any game *with*
  static bodies (extent unmeasurable), and a genuine **0** for a game with *no* static
  bodies (measurable). This None-vs-0 split is the contract; scoring None as 0 was a bug
  fixed earlier and is pinned by tests.
- `n_mechanics` / `gating_depth` need a `--verify` pass (G1 efficacy + a witness).
- `autonomous_bodies` needs replay **frames**, which a standard verify report does not carry
  → **None** across today's library.

**Evidence ceiling today.** For a game with static bodies, X's maximum attainable evidence is
**0.58** (mechanics .30 + gating .18 + bodies .10). Because step 3 renormalises over present
weight, the missing guarded components **amplify** the unguarded `n_bodies` share to
0.10/0.58 ≈ **17%** of X (vs the intended 10%) — the unguarded channel is over-weighted until
the host emits extents. This is disclosed on the map, in the summary, and pinned by
`test_composite_spam_cannot_top_x_under_todays_data_gap` (the spammer still cannot win).

**What the host must start emitting** (queued separately — NOT implemented here): per-body
extents in `run_check` geometry (`half_extents` for boxes, `radius` for circles/spheres, or
an `aabb`). That single change lifts `structural_sections` + `n_static_footprint` from None to
real values and raises X's ceiling from 0.58 to 0.92, restoring `n_bodies` to its intended
10%. Replay-frame capture would additionally populate `autonomous_bodies` (0.08).

## Coverage over the real library (22 games, offline rebuild)

Built from the report index (`runs/*/gen_*.json`) + cached t=0 facts, no engine:

- **9 / 22 placeable** on WORLD × PLAY (a game needs ≥2 present components on *each* axis;
  in practice it needs a COMPLETED report with a witness). The other 13 are off-map:
  fusion/breed drafts with no report yet, or ENV/VERIFY/UNSOLVED verdicts.
- **22 / 22 have incomplete descriptors** on these axes (the extent gap; stated on the map).
- Per-component presence: `n_mechanics` 13/22, `gating_depth` 9/22, `n_bodies` 16/22,
  `witness_entropy`/`distinct_actions` 9/22, `n_checkpoints` 11/22, `structural_sections`
  2/22 (the two zero-static games), `autonomous_bodies` 0/22.

### How the picture CHANGES vs the old cut

| | old: effort × entropy | new: WORLD × PLAY |
|---|---|---|
| coverage | 16.7% (6/36 cells) | **22.2% (8/36 cells)** |
| placed | 9 | 9 |
| what the empty region MEANS | "the tree's search was cheap/expensive here" — an artifact of the solver, dying under RL | **"no game in this library is structurally rich"** — an actionable generation target |

The nine placed games pile into the **low-mid structural-richness band (X ≈ 0.29–0.64)** and
the **entire high-richness half (X > 0.67) is empty**. That emptiness is the payoff: the
"navigate-to-zone monoculture" (parking, lander, obstacle-run, drone-canyon, platformer,
fly-craft — all *steer one body to a zone*) is now **visible as a cluster** in one region,
with the structurally-rich frontier blank. The three emptiest cells the map names are all
`high world × {low,mid,high} play` — precisely the worlds the library lacks. The old
solver-effort axis could not surface this, because "how hard was the search" is not "how much
world is there."

## The map is a CHOICE OF CUT, not dogma

Raw per-game descriptors stay in `atlas.jsonl` (a derived `composites` audit block is added
*beside* them, never instead), so every claim is re-derivable. The renderer recomputes
composites from raw at render time — a stored composite is never trusted, because it is
library-relative (adding a game moves everyone).

```
# flagship (default): WORLD × PLAY, solver effort demoted to point size
python -m harness.atlas.build --games 'scenes/games/*' --out runs/atlas/ --reports 'runs/*/gen_*.json' --facts --verify

# the legacy map, kept renderable verbatim
python -m harness.atlas.build ... --legacy-cut          # = --x solver_expansions --y witness_entropy

# any other cut: any composite, any descriptor, or auto (original spread × coverage pick)
python -m harness.atlas.build ... --x n_mechanics --y witness_entropy
python -m harness.atlas.build ... --x auto --y auto
python -m harness.atlas.build ... --norm rank --min-evidence 0.4
python -m harness.atlas.build --axis-choices            # list every valid axis
```

Tests: `tests/test_atlas.py` (74 pass) — composite math (min-max/rank, None-propagation,
no silent zeros, the weighted-mean-over-present contract), the anti-gaming inflation guards
(descriptor 50-marker test + composite spam tests, including under today's data gap), the
`--x/--y` re-cut and legacy-cut, truthful coverage reporting, and the fixed-domain /
monoculture-visibility property.
