# L1 complexity descriptors + router robustness fixes

Date: 2026-07-16. Task lane off `notes/engines/COMPOSITION_GAP.md` (§2 routing caveats,
§3 L1). Two independent pieces, both additive/robustness — no LLM-surface change, the
verifier untouched.

## TASK A — router bugfixes (`harness/gen/skill_context.py`)

All three flagged in COMPOSITION_GAP §2 ("Routing caveats") were verified before fixing.

1. **Duplicate + unreachable index entries → loader reconciliation.**
   VERIFIED on the vendored `skills_index.json`: it listed `godot-navigation-pathfinding`
   TWICE (exact duplicate — deflates BM25 IDF for navigation terms) while
   `godot-ai-navigation` existed on disk with a full `SKILL.md` but was ABSENT from the
   index (its frontmatter even mis-declares the pathfinding name), so the index alone
   left it permanently dead weight (95/96 routable).
   FIX (loader-side only; the LGPLv3 library is never edited): `load_index` now calls
   `_reconcile_index`, which (a) DEDUPs exact-duplicate names, (b) DROPs index entries
   whose `SKILL.md` body is missing (an unroutable phantom that only pollutes BM25), and
   (c) SUPPLEMENTs disk-only skills — any `skills/<dir>/SKILL.md` whose DIR name is not
   already an entry becomes routable (`name` = the dir so `_skill_body` resolves;
   `description` parsed from the SKILL.md frontmatter). Defence-in-depth: a no-op on a
   clean index (the coordinator later fixed the data at source), so the recovery is
   proved by a synthetic-fixture test, not the live library.

2. **10-pick ceiling binds → raised to 14, binding made observable.**
   `_LLM_ROUTE_CEILING` 10 → 14. Evidence (COMPOSITION_GAP §2): rich prompts returned
   8-10 STRUCTURAL skills, and a full game also wants genre + physics/dimension + camera
   + controls → comfortably >10, so 10 truncated real routing. 14 clears the observed
   demand while still refusing a whole-catalog dump (~95 skills). `_llm_route` now LOGS
   (INFO) when the model names more relevant skills than the ceiling, so a binding cap is
   never silent. (No API key in this env; verified from code + the audit's live probes.)

3. **Silent BM25 degradation → observable.**
   `select_skills` records `last_route_diagnosis()` and LOGS (WARNING) when routing
   degrades: a silent LLM→BM25 fallback (`ROUTE_BM25_FALLBACK`), a weak top-score match
   (`ROUTE_BM25_WEAK`, below `_BM25_WEAK_SCORE`), or no match at all (`ROUTE_BM25_EMPTY`).
   `use_llm=False` with a healthy match is the DELIBERATE offline path → not flagged. The
   LLM-first path is byte-untouched; this only observes what the fallback did.

Tests: `tests/test_skill_context.py` — synthetic-library reconciliation (disk-only
routable, duplicate collapsed, index-only ghost skipped), real-library healthy invariant,
ceiling regression + binding log, four BM25 route-diagnosis cases.

## TASK B — L1 complexity descriptors (`harness/atlas/descriptors.py`)

Pure MEASUREMENT (Elias: instrumentation, NOT steering). Deterministic, `None` when
uncomputable, computed from EXISTING artifacts (game code + verify report + t=0 facts).
Added to `DESCRIPTOR_KEYS` and every `atlas.jsonl` row.

| descriptor | source | meaning |
|---|---|---|
| `n_mechanics` | G1 action-efficacy `effect` map | # distinct LIVE world-effects among declared verbs (mirror controls collapse; a body-set change is its own signature) — interaction density proxy |
| `structural_sections` | t=0 geometry facts | connected static-footprint clusters (union-find over AABBs, world-relative adjacency) — spatial partitions |
| `n_static_footprint` | t=0 geometry facts | footprint-carrying static bodies (the anti-gaming guard's visible companion) |
| `gating_depth` | witness `checkpoints` | length of the ordered checkpoint chain = # distinct latch ticks (simultaneous latches collapse) |
| `autonomous_bodies` | replay frames | non-controlled, non-sensor bodies that MOVE across frames; `None` when no frames exist (the common case) |

**ANTI-GAMING GUARD (required, Elias).** The measurement channel derives geometry from
the game's self-reported `state()`, so it is inflatable by `state()`-padding with
footprint-less marker bodies. `structural_sections` counts ONLY footprint-carrying static
bodies (via `reachability._aabb_of`, which returns `None` for zero-extent / unreferenced
markers), so padding the body list cannot move the count. `n_static_footprint` exposes the
guard: `n_static >> n_static_footprint` reveals excluded markers. Cheap host-vs-self
cross-check: we count from the host geometry facts, not the game's self-declared count.
Test `test_structural_sections_anti_gaming_rejects_inflation` pins this (2 clusters
whether 0 or 50 markers are padded in).

**Render (additive, opt-in).** `render_atlas(..., complexity_panel=True)` (CLI
`--complexity`) draws a gold L1 STRUCTURAL-COMPLEXITY strip below the map ranking certified
games richest-first — it never touches axis selection or the existing layout. Default off,
so the existing map is unchanged.

Tests: `tests/test_atlas.py` — each descriptor's correctness/determinism/None-safety, the
anti-gaming inflation fixture, panel off-by-default + renders-when-enabled.

## Files
- `harness/gen/skill_context.py` (router)
- `harness/atlas/{descriptors.py, render.py, build.py}` (L1)
- `tests/test_skill_context.py`, `tests/test_atlas.py`
</content>
