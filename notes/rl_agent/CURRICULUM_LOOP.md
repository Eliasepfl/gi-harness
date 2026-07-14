# Curriculum loop — learning-difficulty drives the designer (ACCEL-style)

> Phase 2 of `notes/rl_agent/LLM_RL_SYSTEMS.md`. Built in `harness/gen/curriculum.py`
> (+ `game curriculum` CLI, `tests/test_curriculum.py`). This is the STRUCTURAL answer
> to Elias's "complexity wall": G3' measures *learnability*, a machine-readable
> difficulty profile localises *where* a game is too easy / too hard, and a general
> designer *directive* — anchored to the game's own milestone names — feeds that back
> as an ACCEL edit ("harden here / ease there"). The LLM designer becomes ACCEL's edit
> operator, but smarter. No prompt-section edits: the directive is spliced into the
> generator's USER prompt at call time, exactly like a repair hint.

## 1. The loop

```
              ┌───────────────────────── curriculum_round(game) ─────────────────────────┐
              │                                                                           │
 prompt ──► generate_game ──► game.js ──► verify_game (G0-G2, G3 = Go-Explore TREE)       │
              ▲                               │  passed? witness{ticks, latch-ticks}       │
              │                               ▼                                           │
              │                          g3_prime (G3' RL, PPO, `budget_steps`)           │
              │                               │  learnable? success_rate, first-success,   │
              │                               │  checkpoints_curve, plateau                │
              │                               ▼                                           │
              │                        difficulty_profile(verify, g3')                     │
              │                               │  merge solver + RL signals                 │
              │                               │  → grade ∈ {degenerate, easy, target,      │
              │                               │             hard, not_learnable}           │
              │                               ▼                                           │
              │        grade == target ? ──yes──► STOP: certified at frontier difficulty   │
              │                          │no                                              │
              │                               ▼                                           │
              │                        directive(profile)  (anchored to milestone names)   │
              │                               │                                           │
              └──── compose: ORIGINAL PROMPT + directive  ──► generate NEXT version ───────┘
                    (directive rides the USER prompt — additive, no frozen-section edit)
```

* **Solver stays the pre-filter.** G3' runs only on a game that already certified
  (tree witness exists) — never spend PPO on an unsolvable game (LLM_RL_SYSTEMS §4.2).
* **`success` never reads shaped reward.** Grades are computed from the *graded
  stochastic success rate* + the checkpoint-latch curve; the "solved?" certificate is
  still the unshaped binary `success`. Shaping can only make learning faster, never
  pass a false game.
* **Deterministic.** `difficulty_profile` is a pure function of the two report dicts —
  identical inputs → byte-identical profile. Every threshold is an `[eng.]` constant.

## 2. The difficulty profile (what gets merged)

`difficulty_profile(verify_report, g3p_result) -> dict`:

**Solver side** (existence-search, from the tree-G3 verify report):
`witness_ticks`, `tree_replays_to_solve` (how many rollouts the tree needed — a cheap
existence-hardness proxy), `latch_ticks` (per-milestone first-latch tick on the winning
path), `latch_spread` (max−min latch tick — where the winning path's effort concentrates).

**RL side** (learnability, from G3'):
`learnable`, `success_rate` (STOCHASTIC — the graded signal; on fully-deterministic
games the greedy rate is degenerate/binary, spike §3.1), `greedy_success_rate` (the
witness's determinism-first form), `steps_to_first_success`, `plateau_update` /
`plateau_success_rate` (where the success curve settles), and the localiser:
**`plateau_mean_latched`** → **`stalling_milestone`** + **`per_milestone_mastery`**.
`checkpoints_curve` is the per-update *mean count* of latched milestones; its plateau
value maps to WHICH milestone the policy stalls at — `floor(plateau_mean_latched)`
milestones are reliably reached, so the milestone at that index is the one it stalls
before. `per_milestone_mastery[k] = clamp(plateau_mean_latched − k, 0, 1)` — ~1.0 for
mastered stages, tapering to 0.0 past the stall.

## 3. Grade thresholds + rationale

Graded on the STOCHASTIC success rate `sr` (spike §3.1: greedy is 0/1-degenerate on the
deterministic showcase games, so the sampled rate is the learnability grade; greedy stays
the witness form). Constants are `[eng.]`, calibrated on the three G3' spike datapoints
(`G3_PRIME_SPIKE.md` §3, budget 1.2 M).

| Grade | Rule (ordered) | Rationale |
|---|---|---|
| **degenerate** | `(sr ≥ 0.90 or greedy ≥ 0.90)` **and** (`witness_ticks < 30` **or** `steps_to_first_success ≤ 500`) | Mastered AND the goal is reachable with almost no play — a certified witness barely over the anti-triviality floor (20), or RL wins near-instantly. The goal GATE is broken. |
| **easy** | `sr ≥ 0.90` | The sampled policy wins comfortably; real play was required (not degenerate) but there's headroom to deepen. |
| **target** | `0.50 ≤ sr < 0.90` | The frontier band — learnable but not trivially. **gem_cavern (0.656) / meteor (0.625)** live here. Keep it. |
| **not_learnable** | `sr ≤ 0.05` **and** RL never once reached success | Honest "declare UNSOLVABLE-BY-RL": flat near zero, no witness found by PPO within budget. |
| **hard** | everything else below target | Progress made, not cracked. **two_switch_vault (0.188)** — RL reached success once (first-success 1136) but plateaus below 50%. The learnable-but-not-cracked band POET/ACCEL/PLR chase. |

Threshold constants (`harness/gen/curriculum.py`):
`NOT_LEARNABLE_RATE = 0.05`, `TARGET_RATE_LO = 0.50`, `EASY_RATE = 0.90`,
`DEGENERATE_WITNESS_TICKS = 30`, `DEGENERATE_STEPS = 500`.

**Why grade on stochastic-only for the main bands:** it is the single choice that lands
all three real datapoints where the spike puts them (gem 0.656 → target, meteor 0.625 →
target *despite* greedy 1.0, vault 0.188 → hard). Greedy is used only as a corroborator
in the degenerate guard (a perfect argmax solve of a trivially short level is still
degenerate). The **hard vs not_learnable** split is `steps_to_first_success is None`:
if PPO ever reached `success` even once, the game is hard (budget/capacity limited — the
spike's exact framing for two_switch), not impossible.

## 4. The two real demo directives (verbatim)

Ran `verify_game` + `g3_prime(budget_steps=200_000)` on the real showcase artifacts
(main-checkout `scenes/games/v23_showcase/…/game.js`, `NODE_PATH` → main `node_modules`).
No live LLM round — the loop stops at the directive (no API spend). ~55–58 s PPO/game on
CPU (early-stopped ~116k–127k of the 200k budget).

**gem_cavern** — verify passed (tree witness 107 ticks, 54 replays); G3' learnable, `sr`
0.500, first-success 1832. Milestones `got_gem1..got_gem4, at_exit`; per-milestone mastery
`{gem1:1.0, gem2:1.0, gem3:1.0, gem4:1.0, at_exit:0.208}` → masters all four gem pickups,
stalls only on the final exit. **Grade: target.**

```
[CURRICULUM DIRECTIVE — grade: target]
Well-calibrated: learnable at the frontier (success rate 0.5); the policy stalls productively around 'at_exit'. This game is CERTIFIED at target difficulty — keep the current stage structure. If producing a variant, preserve the difficulty band around 'at_exit' (re-theme, don't re-scale).
```

**two_switch_vault** — verify passed (tree witness 102 ticks, 26 replays); G3' NOT
learnable @200k, `sr` 0.156, first-success 1136. Milestones `switch_a, cleared_gap1,
switch_b, cleared_gap2, at_vault`; per-milestone mastery `{switch_a:1.0, cleared_gap1:0.49,
switch_b:0.0, cleared_gap2:0.0, at_vault:0.0}` → reliably hits the first switch,
half-clears the first gap, never the second gate — exactly the spike's diagnosis "stalls
between switch_a→cleared_gap1 and the second gate". **Grade: hard.**

```
[CURRICULUM DIRECTIVE — grade: hard]
The agent plateaus BEFORE 'cleared_gap1': success rate 0.156 (it reliably reaches 'switch_a'), but it rarely gets past 'cleared_gap1'. EASE exactly that stage — widen the platform, slow or steady the hazard, enlarge the target, or relax the timing at 'cleared_gap1' — and KEEP every later stage intact. Change only the 'cleared_gap1' gate; do not touch the stages the agent already clears.
```

> **Budget caveat.** At 200 k, gem_cavern lands `sr` exactly 0.500 (vs 0.656 @1.2 M) —
> right on the `TARGET_RATE_LO` boundary. The thresholds are calibrated to the 1.2 M
> spike values (where target has margin); a production campaign should run the design
> default (2 M) or at least 500 k so the grade is not boundary-fragile. The 200 k demo
> exists to show the mechanism in ~2 min, not to be the calibration authority.

## 5. Running it

```bash
# One round (default budget 200k env-steps), JSON round record:
python -m harness game curriculum scenes/games/<game>.js --json

# Up to K rounds, real budget, regenerate next versions into a dir:
python -m harness game curriculum <game>.js --budget 2000000 --rounds 3 \
       --backend anthropic --out-dir scenes/games/curriculum
```

Each round appends a `{"event":"curriculum_round", grade, action_taken, directive,
new_game_path, rl{…}, solver{…}}` line to `runs/ledger.jsonl` (append-only event log,
telemetry conventions). `action_taken ∈ {certified_target, regenerated, verify_failed,
regenerate_failed}`. The CLI stops early once a game grades `target`.

## 6. At scale (cluster)

Per-game PPO is embarrassingly parallel and socket-free (one game ≈ one core), so a
curriculum campaign is a Slurm **array**: the array recipe in
`notes/compute/ORCD_DEPLOYMENT.md §3(a)` (`-a 0-199%200`, `-c 1`, `mem-per-core=4G`,
`mit_preemptable`) runs per-game G3' directly; each task = verify → G3' → profile →
directive, writing the round record to a scratch ledger shard (the note's §"ledger append
is racy across array tasks" caveat applies — shard per task, merge after). Pin torch-CPU +
`nodeworld/node_modules` (planck 1.5.0) into the image (§2). A LIVE regenerate round needs
the LLM backend, so run generation as a login-node loop (§4c egress note) and submit the
verify+G3' arrays over the produced games.

## 7. Follow-ups

* **Multi-round LIVE campaign.** This deliverable wires the loop and stops at the
  directive (no API spend). Next: an actual `--rounds K --backend anthropic` run that
  regenerates, re-certifies, and shows the grade migrating hard → target across rounds —
  the ACCEL curriculum trajectory, logged per round.
* **Get two_switch_vault over the line first** (spike §6): it flipped grades with more
  budget/capacity once (meteor did), so before trusting a `hard` directive to *ease* the
  game, try the cheap levers (2×256 net, full 2 M budget, entropy anneal). A premature
  ease on a budget-limited `hard` would dumb down a genuinely good game.
* **Per-milestone reward curricula.** `per_milestone_mastery` already localises the stall;
  a natural next rung is a *distance-to-next-milestone* dense reward for exactly the
  stalling stage (spike §6 lever (c)) — curriculum on the REWARD, not only the level.
* **Grade-aware regenerate budget.** `not_learnable`/`hard` rounds could get a bigger G3'
  budget on the *next* version to avoid re-labelling a fixed game as still-hard from a
  too-small probe.
```
