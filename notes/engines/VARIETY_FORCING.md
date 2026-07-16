# VARIETY FORCING — how to make the generator diverge, not converge

Track B of `notes/LONG3D_GAP_ANALYSIS.md`, composed with the Atlas flagship (D1) of
`notes/CREATIVE_DIRECTIONS.md`. Read-only analysis pass, 2026-07-16 (Elias's question:
*how do we FORCE stronger variety?* Tonight: 6 long prompts → 79% prompt fidelity but
structural convergence — single mechanic, similar arenas, 2D-until-the-fix).

**TL;DR.** Exhortation is dead ("DIVERSITY IS THE JOB" is already in the system prompt and
all 6 converged anyway). Force it with a **staged combination**, not one mechanism:

1. **NOW — pin dimension** (mechanism (c) reduced to its one honest axis). A seeded
   `{2D,3D}` draw that *honors* an explicitly-named dimension and *draws* when the prompt
   is silent. Cheapest, passes Elias's anti-anchoring bar cleanly, and moves the single
   most flagrant axis. First result measurable today, no Atlas dependency.
2. **ONCE THE ATLAS LANDS — QD archive pressure** (mechanism (a)). Genesis/breed targeted
   at empty Atlas cells; variety becomes the coverage number itself. This is the general
   engine and it **subsumes** the anti-similarity oracle (b).
3. **Do NOT** build (b) standalone, and **do NOT** build (c)'s body-kind / win-shape axes
   as authored menus — that is the exact hardcoded taxonomy Elias keeps rejecting.

---

## 0. The empirical anchor (why this is not a vibe)

Measured this pass over the 21 game dirs in `scenes/games/`:

> **21 / 21 promoted games `extends Node2D`. Zero 3D. Including all five "3D"-named prompts.**

On the Atlas's own `dimension` descriptor the entire certified library is a **single
occupied cell**. Coverage on that axis = 1 of 2 rows, and it has been 1/2 for every game
ever shipped. The generator is not sampling a distribution with low variance — on the axis
Elias's test probes it is a **point mass**. `reachability.space_utilization` already carries
the 3D branch ("a 3D game keeps its depth extent from the geometry"), and
`api_gdscript.md` already says both dimensions are supported, so the axis *exists* in the
descriptor vocabulary and the substrate — it is simply never populated. That is the
cleanest possible variety win: a binary axis, one side never touched, contract-supported,
and already an Atlas descriptor.

Ledger context: 116 COMPLETED (gdscript 64, godot 53, js 34) — plenty of certified parents
to breed from; the problem is spread, not throughput.

---

## 1. The existing loop — the seams each mechanism must compose with

```
generate_game / revise_game
  └─ _generate_core(prompt, first_user)          # context injection, PINNED for the run
       ├─ [gdscript] _gdscript_system_prompt      # CONTRACT + advisory skill block
       ├─ first_user = _first_user_msg | override # <- the override seam (revise uses it)
       └─ _dispatch → _run_{anthropic,openrouter} → _repair_loop
                                                    # write → verify → _repair_user_msg → repeat
harden_game (guarded loop)
  └─ _run_oracles → feedback.compile_directives → revise_with_directives → re-certify
       # per-round: G4 / pressure / dead_space / g3' findings → Directive rows (fingerprinted)
```

Three facts that decide everything below:

- **`first_user` is already an override seam.** `revise_game` pins a whole different first
  message through `_generate_core(..., first_user=...)`, reused for every attempt in the
  run. A genesis / breed / menu message is the *same seam*, no new plumbing.
- **The Track-C and Track-F fixes are already in.** `_first_user_msg` is now
  dimension-NEUTRAL (the stale "2D" is gone, gamegen.py:259-271) and `_repair_loop`
  preserves partial attempts. So the *anti-3D* bias is removed — but neutrality only stops
  pushing toward 2D; it adds **zero** pull toward diversity. Neutral prompting + a model
  that defaults to 2D = still 2D. Variety needs a *positive, exogenous* signal, exactly as
  `SELF_IMPROVING_DESIGNER.md` warned.
- **The pin lives only in message #1.** `_repair_user_msg` (gamegen.py:321) does NOT
  restate the dimension/cell target. Over 5 repair rounds a pinned "3D" can silently
  regress to 2D. Any pin must be reinforced (in the run's system prompt or echoed in the
  repair message) or it leaks. *Composition detail that bites all three mechanisms.*

The feedback taxonomy (`feedback.py`) is a clean insertion point: each defect class is one
`_compile_*` row returning `Directive(source, origin, fingerprint, severity, text)`, joined
by `compile_directives`. Adding a variety directive = one new row + wiring it into
`_run_oracles`. The **severity tiers are DEFECT / DIFFICULTY only** — a "too similar"
finding is neither (not broken, not hard-to-learn); it needs a third, advisory tier or it
will be mis-budgeted (see (b)).

---

## 2. Mechanism (a) — QD / Atlas archive pressure

Generation targeted at EMPTY archive cells: breed two certified parents into an empty
region, or genesis prompt-free from a cell brief. Variety = measured coverage. This is D1's
own body.

**Composition.** New `first_user` builders `_genesis_user_msg(cell_brief)` /
`_breed_user_msg(parent_a_src, parent_b_src, inherit_brief)` beside `_revise_user_msg`,
driven through the untouched `_generate_core → _dispatch → _repair_loop` funnel via the
override seam. The Atlas (`descriptors.py → runs/atlas.jsonl`, being built) is *read* to
pick the emptiest cell and the adjacent occupied parents. **No verifier, harden, or feedback
change for pure genesis.** The repair loop, funnel, harden driver stay byte-identical.

**Anti-anchoring.** PASSES, and is the *model* for how to pass. Bins and coordinates live
harness-side; the LLM sees the cell as a **qualitative brief** ("a game won by a long,
varied sequence of moves in a tight arena") — never `witness_len>200, entropy>0.8`. Numbers
never cross the surface. The descriptors are computed from certified artifacts, not authored
lists, so nothing is hardcoded on the LLM face.

**Measurability.** The *definitional* win: the number **is** Atlas coverage
(filled cells / reachable cells), before vs after a batch. The mechanism is measured on
exactly the metric it optimizes — no separate instrument.

**Failure modes.** *Descriptor gaming (Goodhart)* — the model lands in an empty cell by
gaming the descriptor rather than by genuine novelty (pad the witness with filler to inflate
`witness_len`; add a decorative checkpoint to bump `n_checkpoints`). Partly self-defended
because the descriptors are **proofs**: a no-op filler action is probed dead by G1, a
degenerate witness trips the anti-triviality / dead-space gates. But descriptor gaming is
the known QD pathology (you illuminate the *descriptor*, not the *interesting-ness*), and a
weak axis will be gamed. Mitigation: prefer axes that are expensive to fake (solver effort,
failure-witness existence) over cheap ones (raw counts). *Second failure:* empty cells can
be empty because they are **infeasible**, not unexplored — budget burned chasing a cell no
certifiable game occupies. Report a per-cell "attempts before first fill" so dead cells are
retired, not re-hammered.

**Cost to first result.** Medium (~1 day), but the expensive half — the Atlas extractor — is
already in flight. Marginal cost = two message builders + one genesis job through the funnel.

---

## 3. Mechanism (b) — anti-similarity oracle (novelty-as-repair-directive)

A candidate too close to an existing certified game *on the Atlas descriptors, not text*
gets a "too similar to X — diverge" directive in the repair loop.

**Composition.** A new feedback row `_compile_similarity → Directive(source="too_similar",
origin="atlas", ...)`, parallel to `_compile_dead_space`, wired into `_run_oracles` (which
would compute the candidate's descriptor row and nearest-neighbour distance against
`atlas.jsonl`) and `compile_directives`. Fingerprint keys on the nearest-neighbour slug +
shared cell so the convergence guard applies. Natural fit for `harden_game` (it already runs
oracles per round and compiles directives).

**Anti-anchoring.** PASSES *only if* the directive is rendered qualitatively ("too close to a
game that is also won by a short single-axis push in an open box — diverge on how it is won
or where it is controlled"), never as a descriptor vector. Same discipline as (a).

**Measurability.** Indirect: reduces the near-duplicate / cell-collision rate; coverage
rises only as a side effect (a rejected duplicate *must* move somewhere). Cleanest proxy =
the nearest-neighbour descriptor-distance distribution across a batch, or the collision rate
(fraction of new games landing in an already-occupied cell). Not the coverage number
directly.

**Failure modes.** *Creativity tax / false rejections* — a genuinely good game rejected for
sitting near an existing one; the model spends revise budget "diverging" and can DEGRADE the
game chasing a phantom (this is precisely the harden loop's REPAIR_STALLED / phantom-fix
risk that `feedback.py` already warns about for DIFFICULTY rows). *Severity mismatch* —
"too similar" is neither DEFECT (not broken) nor DIFFICULTY (not hard-to-learn); forcing it
into either tier mis-budgets it. It needs a new **advisory / nudge-only** tier that never
blocks certification and never triggers REPAIR_FAILED. *Repulsive-only* — it pushes AWAY
from occupied cells but has no TARGET, so a diverged candidate can land in *another* occupied
cell or off the certifiable manifold entirely. It is the weaker, targetless half of (a):
(a) says "go fill THIS empty cell"; (b) only says "don't be like THAT one."

**Cost to first result.** Medium: needs the Atlas + one taxonomy row + oracle wiring + a
new severity tier. More surface than (a)'s genesis path, for a weaker signal.

---

## 4. Mechanism (c) — pinned `{dimension, body-kind, win-shape}` menu (the "jazz chart")

A seeded draw the model must honor, pinned into `_first_user_msg`, which also mechanically
fixes dimension diversity.

**Composition.** Trivial — a `_menu_user_msg(prompt, drawn_cell)` overriding `first_user`
through the *existing* seam. Note: this is a NEW concept, **not** the existing
`retrieval.retrieve_menu` (that is the Tier-1b *parts-bank* lexical menu — reusing the word
"menu" here would confuse the two). Fantasy-prompt conflict resolution: **the explicit
prompt always wins; the draw fills only what the prompt leaves open.** When the seed says
"parking" and the draw says "flying", flying is discarded — otherwise the 79% fidelity that
is our one strength is destroyed. This is just the current `_first_user_msg` philosophy
("everything it leaves open is yours") with the model's *default* (which collapses to
2D / single-mechanic) replaced by a *seeded* choice.

**Anti-anchoring — the honest verdict (Elias asked for this explicitly).**

The framing "it is a SEEDED DRAW, not a fixed list" **does not, by itself, pass the bar.**
The seed only makes the choice *reproducible*; it says nothing about *where the choices come
from*. A draw selects an index into a set — and **the provenance of that set is the whole
anti-anchoring question**:

- **`dimension` axis → PASSES.** `{2D, 3D}` is a fundamental binary the contract already
  names on both sides (api_gdscript.md line 11). It is not a taxonomy in the anchoring
  sense — it prescribes no mechanic, no node type, no worked example. Pinning it is
  *fidelity* (when named) or a *coin flip over a substrate primitive* (when silent). This is
  the one axis of (c) that is clean.
- **`body-kind` and `win-shape` axes → FAIL as authored menus.** If these draw from a
  constant like `BODY_KINDS = ["car","drone","puck","boat",...]` on the prompt surface,
  that is **exactly** the pattern Elias has rejected 4+ times: "worked examples / world_size
  / node types in the GDScript contract", "keyword archetype matcher for the asset bank"
  (`feedback_llm_routing_over_hardcoding.md`). A hardcoded body-kind list *is* the archetype
  matcher he killed. The seed does not rescue it.

So (c) passes **only** if the non-dimension axes get their vocabulary from a source that
already passes:
  1. **Atlas-measured bins** — but the Atlas descriptors are *behavioral proofs*
     (witness length, action entropy, solver effort), NOT semantics like "car" — so
     "body-kind" is not even an Atlas axis. Deriving the draw-set from the Atlas turns (c)
     *into* (a).
  2. **A light per-run LLM call over the prompt** (the routing pattern the memory
     prescribes) — the model proposes the unspecified body/win itself, harness seeds the
     *selection*, deterministic fallback only behind `use_llm=False`. This passes, but it is
     no longer a "pinned menu" — it is LLM routing with a seeded tiebreak.

**Verdict:** (c) is two mechanisms wearing one coat. Its `dimension` axis is the cheapest,
cleanest variety win in the whole analysis and should ship. Its `body-kind`/`win-shape` axes
as a literal menu **fail Elias's bar** and must either be measured into the Atlas (→ (a)) or
drawn by a light LLM call (→ routing). Ship the honest half; do not build the anchoring half.

**Measurability.** Only the `dimension` axis of (c) overlaps the Atlas descriptor set, so
**only dimension coverage is Atlas-measurable**. Body-kind / win-shape variety would need a
*separate* instrument the Atlas does not provide — another reason those axes belong in the
Atlas (measured) rather than in an authored menu.

**Cost to first result.** CHEAPEST for the dimension axis: one message builder + a 50/50
seeded draw (or echo the named dimension), **no Atlas dependency, no new oracle, no new
severity tier.** Shippable today; A/B on the 3 explicitly-3D prompts.

---

## 5. What the literature calls these (brief, cited)

- **(a) = MAP-Elites / Quality-Diversity.** Illuminating a behavior-descriptor grid and
  filling empty cells: Mouret & Clune, *Illuminating search spaces by mapping elites* (2015).
  Active for game content in 2025-26: *Generational Adversarial MAP-Elites*, ALIFE 2025
  (arxiv 2505.06617); QD level-blending in latent space (arxiv 2102.12463); MAP-Elites for
  dungeon maps (arxiv 2202.09301). **Our twist (per CREATIVE_DIRECTIONS, externally checked
  2026-07-16): all prior work bins on code features, latent embeddings, or LLM-judged
  novelty — nobody bins on *certified behavior*.** That is the ownable gap.
- **(b) = Novelty Search.** Reward distance-to-archive, objective-agnostic: Lehman &
  Stanley, *Abandoning objectives: evolution through novelty search alone* (2011). The
  anti-sim oracle is novelty-search-as-repair-directive. Its documented pathology — novelty
  without a quality floor drifts to *different but worthless* — is exactly the creativity-tax
  failure above; QD (a) is the field's answer to it (novelty **and** an elite floor).
- **(c) = Constrained / controllable generation.** Conditioning generation on a target
  attribute vector — controllable PCG. Pinning a target cell a-priori and forcing the
  generator to hit it is *targeted illumination* (a MAP-Elites cell chosen up front rather
  than discovered). The "jazz chart" is prompt-conditioning on a target descriptor.

The clean way to read the three: **(a) is the archive, (b) is (a)'s repulsion term without
the archive, (c) is (a)'s single-cell targeting done up-front.** They are not three rivals;
they are three views of one QD machine. That is why the recommendation is staged, not a pick.

---

## 6. Recommendation — staged, scored on the Atlas coverage number

**Stage 0 (ship now): dimension pin.** Build (c) reduced to its one anti-anchoring-clean,
Atlas-measured axis. `_dimension_pin(prompt, seed)`:
- if the prompt names a dimension → honor it (fidelity, not anchoring);
- else → seeded `{2D,3D}` draw, ~50/50;
- pin it in message #1 **and reinforce it** (add the pinned dimension to the run's system
  prompt or echo it in `_repair_user_msg`) so a repair round cannot silently regress to 2D
  (the §1 leak).

Rationale: it is the highest-leverage, cheapest, honest move; it ends the 21/21 Node2D
collapse; it needs no Atlas and no oracle; and it directly moves the one axis Elias's test
probes. It does **not** require waiting on the flagship.

**Stage 1 (once `atlas.jsonl` exists): QD archive pressure (a).** Genesis/breed toward the
emptiest cell, briefs qualitative. This is the general variety engine and it **subsumes (b)**
— expose the anti-similarity signal only as an advisory nudge directive *if* genesis alone
under-fills, never as a standalone gate. Score: Atlas coverage before/after a batch.

**Do not build:** (b) standalone (targetless, creativity tax, needs a new tier for a weaker
signal); (c)'s body-kind/win-shape as authored menus (fails the anti-anchoring bar). If those
semantic axes are wanted, *measure them into the Atlas descriptors first* (→ (a)) or *draw
them with a light LLM call* (→ routing) — never a constant list on the LLM surface.

---

## 7. First experiment — measurable on the Atlas coverage number

**Goal:** move the Atlas `dimension` axis from a degenerate single cell (21/21 2D) to
occupied on both sides, and prove the move raises coverage rather than gaming it.

1. **Baseline (needs only the Atlas extractor, no gen-side change).** Run
   `descriptors.py` over the 21 existing dirs → `atlas.jsonl`. On a 2-axis map
   `{dimension × n_checkpoints}` (or `{dimension × witness_len}`), record coverage: today
   the **entire 3D row is empty** — every occupied cell sits in the 2D row. Report the
   fraction of reachable cells filled; it is capped at ~half because one whole axis-half is
   unreachable by construction.
2. **Intervention (Stage-0 pin only).** Re-generate through the untouched funnel:
   - the **3 explicitly-3D prompts** (gen_0 fly-rings, gen_1 parking, gen_2 drone) with the
     dimension pinned to 3D (honoring the named constraint);
   - **3 dimension-silent prompts** with the seeded 50/50 draw.
   Use `--gen_seed` (the eval note's unseeded-noise caveat) and `mit_preemptable`.
3. **Success on the Atlas number.** The **3D row goes from 0 → ≥1** occupied cell (target
   ≥3 built, ≥1 certified). Report:
   - `dimension`-axis coverage delta (rows occupied: 1/2 → 2/2);
   - dimension-**honored** rate on the 3 named prompts (tonight: **0/3** → target 3/3) —
     the direct refutation of tonight's collapse;
   - certified-vs-built split, honestly (a 3D game that builds but does not certify still
     fills the descriptor cell — report both so we don't confuse coverage with quality);
   - a Goodhart check: eyeball that the 3D games are *genuinely* spatial (depth actually
     used — `space_utilization` reports the depth extent), not a 2D game re-parented to
     `Node3D` with `z≈const`. If the depth extent is ~0, the cell is gamed and the win is
     fake — say so.

Acceptance: (a) the 3D row is occupied for the first time; (b) dimension-honored rate on the
named prompts beats 0/3; (c) at least one 3D game is *genuinely* 3D by the depth-extent
check; (d) the coverage delta is reported honestly, including a zero. Then, and only then,
turn on Stage-1 QD pressure and re-measure coverage across the full descriptor grid.

---

## Sources
- Mouret & Clune, *Illuminating search spaces by mapping elites* (2015), arxiv 1504.04909.
- Lehman & Stanley, *Abandoning objectives: evolution through novelty search alone* (2011).
- Generational Adversarial MAP-Elites, ALIFE 2025 — arxiv 2505.06617.
- QD level generation / blending in latent space — arxiv 2102.12463; dungeon maps — 2202.09301.
- `notes/CREATIVE_DIRECTIONS.md` (D1 Atlas; external QD-for-games survey, checked 2026-07-16).
- `notes/LONG3D_GAP_ANALYSIS.md` (Track B brief; the dimension smoking gun).
- `feedback_llm_routing_over_hardcoding.md` (Elias's anti-hardcoding rule — the (c) bar).
```
