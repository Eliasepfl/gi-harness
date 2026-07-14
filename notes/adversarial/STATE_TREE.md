# Shared state-action tree — design notes (2026-07-13, late)

> Elias's proposal: like the Agent World Model database, but for ACTION/STATE
> HISTORY — never explore the same action combination twice; when a planned
> prefix was already tried, restart AT ITS LEAF and branch with an untried
> action. Asynchronous lanes populate the tree fast; attackers get much
> stronger. His caveat: an action repeated with no state change = probably
> stuck → terminate the episode, and beware duplicates in the database.
> Designed by the orchestrator (Fable) per his request. Companion to
> `G4_DESIGN.md` (the tree becomes the substrate G4 lanes explore).

## Prior art (name it in the submission)

- **Go-Explore** (Ecoffet et al. 2019/2021): archive reached states, RETURN to
  a promising archived state, EXPLORE from there. Solved Montezuma's Revenge
  with exactly this return-then-explore loop. Our design is Go-Explore with a
  deterministic simulator and multi-writer async lanes.
- **MCTS transposition tables / tree reuse** (game search): per-node tried-
  action sets, frontier selection, merging equivalent states.
- AWM's DB analogy (Elias): they persist env state for tool-use; we persist
  the SEARCH over states.

## Core identity decision — the node IS the action prefix

Our worlds are deterministic given (game, engine, world_seed, action prefix)
— proven bit-exact on both pymunk and Planck. Therefore:

- **Node key = hash(game_hash, engine, world_seed, action_prefix).** Exact,
  collision-free identity with zero physics storage. The tree of prefixes IS
  the state tree.
- **Restore = REPLAY the prefix.** Never snapshot/restore physics state:
  pymunk/Planck cannot serialize solver caches, so restored states silently
  diverge from true continuations — which would break witness replayability,
  the property the whole harness stands on. Replays are ms-cheap (a 120-tick
  episode ≈ 1 ms in-process); "starting at the leaf" costs a replay, not a
  database of world states. Optimization when a lane expands several children
  of one node: replay the prefix ONCE, then fork exploration in-process.
- **Approximate state index (quantized snapshot digest, e.g. 1 px / 0.1 rad
  buckets) exists ONLY for heuristics** — novelty scoring, "≈been here
  before" penalties, near-duplicate detection. It NEVER merges nodes and
  never serves as a restore point: false merges from quantization would
  corrupt the tree; heuristic use makes them harmless. (This is the clean
  resolution of the duplicate worry at the identity level.)

## Node & edge record (SQLite, WAL mode — async multi-writer friendly)

```
node:  key, parent_key, depth_ticks, action_from_parent,
       state_digest (exact), state_features (quantized: controlled pos/vel,
       per-milestone latched?, flags, KE), status (open | terminal_success |
       terminal_failure | terminal_stuck | exhausted), provenance (lane,
       episode, ts)
edge:  (node_key, action) -> outcome (child_key | no_effect | terminal_*),
       claimed_by (lane, for optimistic locking), tried_at
```

- **"Never the same action twice" is enforced by edge claiming**: a lane
  atomically claims (node, action) before exploring; claimed/tried edges are
  never re-explored (per game version). Two async lanes can never duplicate
  work — by construction, not by convention.
- **Frontier** = open nodes with untried actions. Selection heuristics:
  novelty (feature distance via the approximate index), milestone progress
  (nodes where new latches occurred), depth diversity, and G4 intent
  (avoidance lanes prefer low-progress branches; breaker lanes prefer
  high-energy states).

## The stuck rule (Elias's caveat, made mechanical)

Per decision tick during expansion: if action a at node n yields
Δstate < ε (feature delta below threshold — same spirit as G1's efficacy
epsilon):

1. **No child node is created.** The edge records `no_effect` — an EDGE FACT,
   not a state. This prevents duplicate near-identical nodes at the source
   rather than cleaning them later.
2. K consecutive no-effect ticks (K≈8 [eng.]) → node status `terminal_stuck`,
   episode ends (his rule), and a dossier fact is emitted with provenance:
   "action a is inert in region R" — exactly the shape G1's efficacy oracle
   already speaks, now localized per-state instead of only at t=0.
3. The approximate index additionally down-scores frontier nodes whose
   features ≈ a known stuck region (don't keep sending lanes into the swamp).

## What each consumer gains

- **G3 solvability**: the probe's "guided second pass" becomes true tree
  search — witnesses found faster, and UNSOLVED diagnoses become "the tree
  saturated at milestone k" (a stronger, checkable claim than "40 random
  episodes failed").
- **G4 attackers (cheap LLMs)**: they no longer propose blind action strings;
  they receive frontier summaries ("node 4f2a: ball at pad edge, milestones
  2/3, untried: [nudge_left, drop]") and spend their intelligence on WHICH
  frontier to push — the tree turns weak models into strong explorers (Elias's
  point: the tree populating asynchronously makes attackers more powerful).
- **The dossier**: edge facts (no-effect regions, kill transitions, latch
  transitions) flow in with provenance automatically.
- **Smart-vs-weak comparison**: attacker-attributed edges make the two
  "trees" literally subgraphs of one shared tree — the comparison view is a
  graph diff, not a reconstruction.

## Scope & lifecycle

- Tree is keyed per (game code hash, engine, world_seed, bank_version):
  any repair → new tree (facts already exported to the dossier survive;
  the old tree is archived for the comparison views).
- Storage: ~200 B/node; 100k nodes ≈ 20 MB SQLite — trivial. Prune policy:
  drop exhausted subtrees below depth D once their facts are exported.
- Ships with the Planck port (the batch executor gains a `replay_prefix`
  entry point; SQLite store in `runs/trees/`); pymunk gets the same API for
  parity testing.

## Open questions (to resolve during implementation)

1. ε and K for the stuck rule — calibrate against real games (jelly's spring
   jiggle must not read as "change"; use the G1 efficacy epsilon as the seed).
2. Frontier scoring weights (novelty vs milestone progress) — start uniform,
   let the ledger arbitrate.
3. Cross-seed generalization: the tree is per-seed; do facts transfer across
   world seeds? (Facts yes with "seed-0" provenance tags; nodes no.)
