# Night Report — 2026-07-17→18 (the population night)

**Mission (Elias):** by morning, ~15 fresh demos — generated A-to-Z tonight (no breeding children, library legacy doesn't count), varied mechanics, PRESENTABLE to GI (bar: first_3d_follow.gif), reviewed with maximum severity. Model: tencent/hy3:free, reasoning UNTRUNCATED (100k).

## Result: 11 accepted demos (10 firm + 1 provisional), 140 A-to-Z generations, 28 certified

| # | demo | dim | mechanic | attempts | witness | verdict trail |
|---|------|-----|----------|----------|---------|---------------|
| 1 | marble maze (tilt floor) | 3D | plate tilt / holes | 6 | 44t | reject@overview → ACCEPT@follow |
| 2 | station repair (jetpack) | 3D | orbit + bolt panels | 4 | 221t | ACCEPT (best of night) |
| 3 | domino hall | 3D | chain topple + mid-run repositioning | 3 | 49t | ACCEPT |
| 4 | window washer | 2D | winch platform, dodge windows | 3 | 34t | ACCEPT (visual clause win) |
| 5 | lava shaft | 2D | climb + rising lava + rope | 5 | 21t | ACCEPT |
| 6 | pearl diver | 2D | dive/breathe cycles, closing clams | 3 | 270f | ACCEPT (haiku review) |
| 7 | card castle | 3D | lean-balance construction | 3 | 20f | ACCEPT both cams |
| 8 | chimney drop | 3D | steered descent, crooked flue | ~4 | 126f | ACCEPT@follow |
| 9 | egg-and-spoon | 3D | carried balance | ~4 | 28t | reject@follow(void) → ACCEPT@overview |
| 10 | window box hoist | 2D | pulley + swing past obstacles | ~3 | 20t | ACCEPT |
| 11 | skydrop (air-brake crate) | 3D | steered fall onto moving bed | 4 | 268f | PROVISIONAL |

GIFs: `notes/gifs/night/` + scratch `gi/demos/night/`. All promoted into `scenes/games/` (library now 40).

## Wave economics (attempts stat per Elias's requirement — full per-game data in wave*/gen_*.json)

| wave | prompts | certified | notable |
|------|---------|-----------|---------|
| 1 | 20 varied | 8 (40%) | 3D converts best |
| 2 | 20 + visual clause | 5 (25%) | ENV_ERROR×11 = .material hallucinations |
| 3 | 20 + refined materials clause | 3 (15%) | UNSOLVED×10 = solver ceiling on rich mechanics |
| 4 | 20 + COMPACT-world clause | 7 (35%) | best clause combo |
| 5+6 | 40 | 5 (12%) | quirky mechanics hit solver ceiling |
| 7 | 20 | 0 | **OpenRouter 429 — free-tier rate limit** (retry scheduled) |

## The night's laws (each learned from a reject, each wired into qa/*.sbatch or wave clauses)

1. **3D → follow camera** (overview shrinks everything to specks) → then **dual-cam** (follow occludes behind big bodies: tug, siege) — reviewer picks.
2. **Stored witness > fresh re-solve** (capture re-solve missed on tug; witness.json now passed via --actions).
3. **Frame-0 trim** (gray settle frame).
4. **Bare 2D = void-box reject** → prompt-side visual clauses (Polygon2D.color / StandardMaterial3D.new()+material_override — the .material crash fix).
5. **Compact worlds** (mid-size worlds defeat BOTH cameras: siege).
6. **Haiku reviewers are properly harsh** (caught bucket-teleport, rigid dough, swing-less chandelier — mechanic-fidelity rejects beyond my grid checks).
7. **The two standing bottlenecks Elias named are both CONFIRMED tonight**: model capability (Godot-3 ghosts persist at reduced rate; 429 caps free-tier volume) and the single-action tree solver (UNSOLVED×10 on wave 3's richer mechanics — chord-solver Phase 3 is the answer).

## Atlas: coverage 22% → **31%** (11/36 cells, 17 games placed vs 9)
`gi/atlas_morning/atlas.{jsonl,svg}` — the night's games colonize the mid/high band; station repair tops behavioural richness. Emptiest frontiers shifted to low-world corners (next wave targets).

## Also landed on main tonight (pushed, 4ae2921 → b47fdf0)
- parser-friction lever: benign-warning G0 reclassification (probe-proven: only Variant-inference warnings are benign), Godot-4 runtime hard-rule line, last-mile reach telemetry in hints, stall cap 4→2 with reframe-grace.
- CHORD Phase 2 complete: opt-in MultiBinary PPO, idle tick (allow_idle capability), Elias's measured-opposition projection (physics-discovered [[up,down],[left,right]]; killed the left+right 9600/9600 thrash), diagonal-control bench: MB first-success ~1.6× sooner. Defaults byte-identical Discrete.

## Morning queue
- Wave-7 retry (8 prompts) fires ~40min after the 429 (job 18135755) — process on landing.
- Skydrop provisional → confirm or cut on full-motion view.
- Site day3 night section (gifs + atlas) — in progress at report time.
- CoT deep-read across wave traces (a<N>.trace.json everywhere) — material exists for the analysis Elias asked; wave-level skim done (material hallucinations found there), full read queued.
