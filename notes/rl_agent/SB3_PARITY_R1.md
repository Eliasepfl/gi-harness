# SB3-vs-vendored G3' parity — round 1 (ORCD, in-image)

> 2026-07-14, job 17919487 (mit_preemptable, 12 tasks, `-c 2`, same image
> `gi-certifier.sif` w/ sb3 2.9.0 + gymnasium 1.3.0). The §6.7 acceptance run
> from `GODOT_RL_AGENTS_CAPABILITIES.md`: 6 showcase games × {vendored, sb3}
> via `harness rl probe --budget 2000000`. Raw JSONs:
> `~/orcd/scratch/gi/runs/parity_sb3_17919487/`.

## Result: verdict parity 6/6 — default trainer flipped to sb3

| game | vendored sr | sb3 sr | learnable (both) | bridge_ok (both) |
|---|---|---|---|---|
| boulder_run | 1.0 | 1.0 | true | true |
| demolition_yard | 1.0 | 1.0 | true | true |
| gem_cavern | 0.656 | 0.5 | true | true |
| meteor_gauntlet | 0.031 | 0.062 | false | true |
| two_switch_vault | 0.125 | 0.094 | false | true |
| flood_tower | 0.0 | 0.0 | false | (no witness) |

Every learnable/not-learnable verdict and every bridge matches; the largest
sr delta (gem_cavern 0.156) is ~1.8σ at n_eval=32 — within eval noise for a
single comparison. Difficulty ordering identical to the R1 map. Per the
acceptance bar, **`trainer="sb3"` is now the default** in `g3_prime` and
`harness rl probe`; the vendored loop stays available via
`--trainer vendored` and is deleted only after one live curriculum round on
sb3 behaves (final [LF] step).

## The important side-finding: "2M budget" is not the binding knob

All 12 runs `stopped_early=true` at **45–72k steps** — including the
not-learnable games — because `ppo.train`'s plateau rule
(`patience=40` updates on the smoothed return, `harness/rl/ppo.py:43`,
mirrored in the sb3 trainer) fires long before any 2M budget matters.
Consequence for the curriculum hypothesis in STATE.md ("remaining gap to
target = budget-limited (2M and/or a second ease round)"): **raising the
budget alone changes nothing on a plateaued game** — the run ends at the
same plateau. The knobs that actually extend training are `patience` /
`plateau_window` (or reward shaping that un-flattens the plateau). Any
future "full 2M rung" experiment must raise patience alongside budget, and
the cost model in `ORCD_GODOT_RL_PLAN.md` §4 (13–20 min/game at 2M) only
applies when patience allows the budget to be consumed — plateaued games
cost ~30–60 s, which makes wide screens far cheaper than planned.
