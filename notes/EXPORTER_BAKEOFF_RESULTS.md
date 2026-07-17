# Results note — Exporter validation + Exploration bake-off (2026-07-18)

## Exporter (merged e755e78 + negatives 82802eb)
- 2D window_washer: 34 steps = 34 frames = 34 ticks, return +10.433 (shaping 1.0 + decayed terminal 9.433, arithmetically exact), 292 KB.
- 3D marble_maze: 44/44/44, return +10.267, 3.66 MB. VALIDATION PASS 2/2 (job 18159179).
- Negatives batch (job 18162343, PASS 14/14): witness [+10.267,+10.433] · perturbed near-miss [+0.250,+10.433] (spans) · random [-2.000,+0.250]. Outcomes success 3 / failure 5 / timeout 6; every non-win return < every win return (mechanical: win terminal ≥5.0, shaping ≤1.0, failure −2.0).

## Exploration bake-off (3 farm-failure games × 3 arms, 2M budget, 2.5h wall)
| game | metric | baseline | RND | warmstart |
|---|---|---|---|---|
| window_washer 2D | greedy / first-success | 0.000 / 1,344 | 0.000 / 1,344 | **0.094 / 48** |
| card_castle 3D | greedy / first-success | 0.000 / never | 0.000 / never | 0.000 / **1,056 (only arm ever to win)** |
| station_repair 3D | greedy / first-success | 0.000 / 108,064 | 0.000 / 323,104 (worse) | 0.000 / **3,192** |

- Warmstart wins discovery 28–34×; RND is a net loser (delayed station). No arm demo_ready at this wall: warmstart ran 72–388 sps (prefix replay each reset) vs ~3,000 — throughput-bound, not exploration-bound.
- Throughput fix landed on the bake-off branch: bulk serve_replay, profiled 4.3× per reset (339→79 ms). card_castle curriculum annealed 18→4 before a plateau-detector misfire cut it with 2.3h unused — plateau/curriculum interaction is the known next fix. Re-runs in flight.
- Recommendation: witness-warmstart = rescue default; RND dropped.
