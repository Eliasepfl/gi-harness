# Dead-space (proportion) gate + dimension measurement — WAVE 2 space lever

> Implementation + measurement note, 2026-07-16. Builds DEMO_GAP_ANALYSIS.md §Gap 3
> (dead space, ranked HIGH) as an ADVISORY gate mirroring the PRESSURE gate's pattern,
> and measures the 3D-reach finding (§1 dimensionality row) with a routing diagnosis +
> a one-arm generation wave. Scope amendment (Elias, mid-task): the prompt-side
> dimension nudge (lever 2) was REVERTED before any generation used it —
> `api_gdscript.md` / `design_block_gdscript.md` are byte-unchanged; the A/B became a
> routing diagnosis + single-arm wave. All data below is from the reverted-prompt
> (current-contract) state.

---

## 1. The metric — `reachability.space_utilization` (harness-side fact)

How big is the declared PLAYFIELD versus the SPAN the action actually uses? Purely
geometric, computed from the game's OWN t=0 geometry facts (the same serve-host
`geometry` list the G0.5 flood reads). Dimension-aware.

* **Playfield box** = declared world box `[0,w]x[0,h]` U every body position U every
  static wall footprint. A 3D game's depth extent comes from the geometry (world_size
  only bounds x,y), so a z-flat game reads z-thin, not z-empty.
* **Action-span box** = controlled-body footprint (clearance radius) U every
  **reachable** checkpoint/goal target — walled-off targets are excluded by re-using
  the G0.5 flood, so decoration the player can never touch cannot inflate the span.
* `measure_ratio` = playfield measure / span measure (AREA in 2D, VOLUME in 3D).
* `linear_ratio` = `measure_ratio ** (1/dims)` — the per-axis normalised reading,
  comparable across 2D and 3D; the number the directive quotes.

**Threshold** (harness-side only, never on a generation surface): `linear_ratio > 5.0`
(both dims; `DEAD_SPACE_LINEAR_2D/3D` kept as separate knobs). Calibrated on the
certified reference fixtures' own geometry so none false-flags, with margin:

| fixture | dims | linear ratio | verdict |
|---|---|---|---|
| mini_collect | 2D | 2.91 | proportioned |
| losable | 2D | 3.78 | proportioned |
| mini_collect_3d | 3D | 4.04 | proportioned (the tightest reference — 3D cannot be stricter than ~4 without a false reject) |
| dead_space (new fixture) | 2D | ~7.5 | flagged |

At >5x per axis a 2D world is >96 % empty area, a 3D world >99 % empty volume.

## 2. Wiring — advisory end-to-end (mirrors PRESSURE exactly)

* `gameverify._dead_space_gate` — runs LAST in the gdscript funnel (after the
  failure-witness gate), only on a still-certified game. Records a NON-gating
  `dead_space` sub-check under `G3_solve` (always `pass=True`; the signal is the
  `dead_space` bool + ratio) and, ONLY when flagged, a top-level
  `report["dead_space"]` finding + a `PROPORTION:` warning (parallel to
  `runtime_error` — proportioned games keep the exact report key set). Never blocks
  certification; exception-safe (a measurement hiccup is a no-op).
* `feedback` — `dead_space` taxonomy row at **DIFFICULTY** severity (an over-empty
  world still certifies: polish, not defect; added to `DIFFICULTY_SOURCES`), a
  `dead_space_finding(report)` bridge, compiled in order runtime -> G4 -> pressure ->
  dead_space -> G3'. Directive text quotes the measured ratio and repairs by
  principle (tighten the world to the action, or fill it) — no numbers as rules, no
  node lists.
* Fixture `tests/fixtures/gd_games/dead_space.gd` — certifies G0-G3 through the real
  funnel yet flags (tiny collect scene in a 2000x1400 world).

Why advisory: a bounded heuristic over static geometry (a sprawling-but-legitimate
design — a long track, a scattered hunt — must never be wrongly rejected); the revise
loop, not a reject, delivers the bar — the same false-reject discipline as PRESSURE
and G0.5.

## 3. Routing diagnosis (is 2D-default a router problem?)

LLM router (`skill_context.select_skills`, GLM-5.2), k=3 probe on 6 prompts: the
router pulls `godot-physics-3d` on **all three** 3D-evoking prompts (fly-rings,
park-car, land-probe; 2/3 3D-dominant, 1 tie) and 2D skills on planar prompts.
**The router is NOT under-selecting 3D.** No router change was made.

## 4. Generation wave (capped era, k=2) — 6 prompts, GLM-5.2, speedup 8, max_repairs=1

Worktree code = pre-uncap `_SKILL_K = 2`, so this table is the **capped-era
baseline** (main has since uncapped the LLM router to ~4-8 full skills; a separate
uncapped-era wave on main reproduced the same dimension result, 3/3 3D-evoking -> 2D).

| kind | prompt | verdict | dim | linear ratio | dead_space | injected skills (k=2) |
|---|---|---|---|---|---|---|
| 3D-evoking | fly a small craft through floating rings | COMPLETED | **2D** | 1.53 | no | game-loop-time-trial, genre-racing |
| 3D-evoking | park a car into a tight parking slot | UNSOLVED | **2D** | 1.00 | no | genre-racing, **physics-3d** |
| 3D-evoking | land a probe gently on a landing pad | ENV_ERROR | **2D** | n/a | n/a | **physics-3d**, 2d-physics |
| planar | push a block through a maze to the exit | COMPLETED | 2D | **6.33** | **YES** | genre-puzzle, 2d-physics |
| planar | sort falling shapes into matching bins | UNSOLVED | 2D | 1.15 | no | 2d-physics, genre-puzzle |
| planar | slingshot a ball at a stack of towers | ENV_ERROR | 2D | n/a | n/a | 2d-physics, **physics-3d** |

Findings (honest, small n=6):

* **Dimension: 0/6 games chose 3D** — including all three 3D-evoking prompts, and
  including games with `godot-physics-3d` literally in the injected context. Combined
  with the router diagnosis (§3) and the uncapped-era wave on main (3/3 3D-evoking
  still 2D with full skill bodies), the 2D default is a **model-side bias in
  GLM-5.2's generation**, not a routing or contract failure, in BOTH skill-injection
  eras. The contract's permissive "Dimension is YOURS" text does not overcome it;
  whether to re-introduce a principled dimension nudge is Elias's call. For that
  decision, the drafted-then-reverted paragraph (never shipped, never used in any
  generation) was: *"Choose the dimension the seed's action truly occupies, and do
  NOT default to the easier plane. A fiction that lives in a VOLUME - things moving
  through the air, over and around one another in depth, rising and diving, threading
  openings that have a near side and a far side - loses its nature the moment you
  flatten it onto a plane; done in 2D it becomes a different, lesser game. A fiction
  that genuinely lives on a SURFACE - sliding across a top-down field, a side-on
  world where things fall - is honestly and fully 2D, and dressing it in 3D buys
  nothing. Read what the seed describes, ask which space its motion actually needs,
  and commit to that one: 2D is not the safe default and 3D is not a badge - the
  honest match between world and fiction is the whole point."* (plus a
  `Dimension: <2D or 3D, and why>` DESIGN-block field.)
* **Proportion: the gate fired in the wild on its first outing** — push-block-maze
  CERTIFIED with its mechanic confined to a 444x27 px sliver of an 800x600 world
  (ratio 6.33): exactly the §Gap 3 "mechanic confined to slivers" pattern, now
  measured + directive-compiled instead of invisible. 3/4 measurable games were
  proportioned (1.0-1.53) — better than the legacy 20-69x set, but n is tiny and the
  two ENV_ERROR games are unmeasurable (no controlled body reaches t=0 state).
* **Caveats**: max_repairs=1 bounded the wave (2/6 ENV_ERROR is the un-repaired GLM
  G0/G1 failure rate, not the loop's); no wild 3D game exists yet to exercise the 3D
  branch of the metric outside fixtures/unit tests; verdicts COMPLETED 2/6,
  UNSOLVED 2/6, ENV_ERROR 2/6.

Raw rows: `/home/enaha/gi_wave_scratch/wave_results.json` (wave v1 was invalidated by
a multiprocessing-spawn bug in the driver script, not the harness; v2 is clean).
