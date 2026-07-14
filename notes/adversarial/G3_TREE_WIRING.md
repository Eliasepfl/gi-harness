# G3 ← state-action tree wiring (v2.4, landed 14 juil.)

The Go-Explore tree (harness/core/statetree.py) is now G3's DEFAULT solver.
Built by an Opus agent in a worktree; merged with 374 tests green. This note
preserves the design + acceptance context for reuse.

## Design (harness/verify/treesolve.py, `run_g3_tree`)

- Dispatch: `gameverify._run_g3` — `G3_SOLVER = "tree"` module default,
  `HARNESS_G3_SOLVER=random|tree` env override read at call time. Legacy
  `run_g3` (random + guided pass) intact and selectable. gameverify diff was
  23 insertions.
- Macro edges: an edge is `"<action>*<hold>"`, hold 1..MACRO_MAX(4) part of
  the edge identity; a node's macro prefix flattens to per-tick actions for
  replay. Variable-length plans batch safely under one max_ticks because the
  runner stops at min(max_ticks, len(actions)).
- Expansion = Go-Explore rollout: restart from the best frontier leaf (replay
  its prefix), append a random-macro tail to the horizon, insert the macro
  chain via `StateTree.record` (dedup merges shared prefixes — a milestone
  reached once becomes one leaf everyone restarts from). One rollout ≈ one
  legacy random episode in cost.
- `eps=0`: the no-effect rule is off (a single final-snapshot rollout gives no
  exact intermediate fingerprints); per-node checkpoint knowledge is exact,
  from cumulative latch ticks.
- Frontier policy: hammer the single best leaf with EPSILON=0.2 uniform
  exploration. Best-leaf key = (most milestones latched, fewest unproductive
  "deaths", fewest ticks-to-reach, least-visited, stable prefix). The deaths
  term steers off deceptive leaves (milestone latched mid-air then a fall).
- Batching: JsExecutor → BATCH_SIZE=24 rollouts per run_batch round (one node
  process per round = the per-leaf commitment); PyExecutor streams with
  early-stop. Budget TICK_BUDGET=21000 simulated ticks (parity with the old
  (40+30)×300). Deterministic: SOLVER_SEED=0, byte-identical witnesses across
  separate CLI processes. statetree.py needed ZERO changes.

## Acceptance (production CLI, sandboxed, tree solver)

| game | verdict | witness ticks | replays | wall |
|---|---|---|---|---|
| boulder_run | PASS | 199 | 2 | 5.1s |
| demolition_yard | PASS | 102 | 5 | 4.3s |
| flood_tower | PASS | 124 | 128 | 6.5s |
| gem_cavern | PASS | 107 | 54 | 4.2s |
| meteor_gauntlet | PASS | 98 | 10 | 3.0s |
| two_switch_vault | PASS | 102 | 26 | 3.5s |

Random baselines ~2-3s/game; tree stays within the ~2x wall-clock envelope.

## The cliffside datapoint (non-gating, per Elias)

Random search: 0/70 episodes reach the flag (UNSOLVED). The tree reaches
`at_flag` in **2 replays** — witness `hop,shove,shove,shove,shove`, 5 decision
ticks, independently replayable. Verdict flips to **GOAL_ERROR (trivial,
5 < 20)**: the flag is reachable by a shove-fling into its low sensor without
the intended crate-topple/climb. The tree didn't just solve what random
couldn't — it produced a MORE CORRECT verdict (degenerate goal, not unsolved).
Lesson: a stronger solver upgrades verification *accuracy*, not just coverage.

## Tests

- tests/test_treesolve.py: 14 tests (solve streaming + batched, replayable
  non-trivial witness, prefix dedup, determinism, budget, UNSOLVED progress
  diagnosis, macro expansion, solver selection).
- tests/test_gameverify.py: the guided-second-pass test pinned to
  HARNESS_G3_SOLVER=random (it asserts random-search specifics).
- Full suite in the main checkout: 374 passed.

## Follow-ups on the shelf

- The tree's UNSOLVED progress diagnosis feeds the same hint path; richer
  per-branch diagnostics (which leaf families stall) are available in the tree
  if we ever want deeper repair hints.
- G4 could reuse the tree for avoidance probes (minimize-progress frontier) —
  the shared-tree-for-probes idea from STATE_TREE.md, not yet wired.
- RL G3' (see notes/rl_agent/LLM_RL_SYSTEMS.md) slots AFTER this tree as the
  learnability layer; the tree witness can warm-start PPO.
