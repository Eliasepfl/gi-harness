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

* **Multi-round LIVE campaign.** The first live campaign (§8) is done — a `--rounds 2
  --backend openrouter` run that revises, re-certifies and re-grades across rounds. It
  showed learnability *moving* (sr 0.0 → 0.125) but not yet crossing into `target` at
  500 k; the trajectory hard → target across rounds still wants the full 2 M budget.
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

## 8. Revise mode — the minimal-edit curriculum operator (default)

`curriculum_round(game_path, ..., mode="revise" | "regenerate")`, **default `revise`**;
CLI `--mode revise|regenerate` on `game curriculum`.

**Why it exists.** The first LIVE round (commit `26b3fc4`) ran `mode="regenerate"`: the
directive rode the ORIGINAL PROMPT into `generate_game`, which re-designed the game FROM
SCRATCH. hy3:free then failed **5/5** attempts to rebuild `two_switch_vault` under the
v2.3/v2.4 bar — from-scratch generation THREW AWAY the certified design the directive only
wanted to *tweak* (it rebuilt the world with a different `WORLD_SIZE`, different entity
names and a different layout, then couldn't re-solve it). ACCEL edits levels; it does not
regenerate them. `revise` is that missing edit operator.

**What it does.** On a non-`target` grade, the CERTIFIED source seeds the SAME
verify→repair loop as a minimal EDIT task (`gamegen.revise_game` →
`gamegen._revise_user_msg`): the model gets the full current module + a task block —
*"this game is CERTIFIED; apply ONLY this directive with a minimal edit; keep entities,
ACTIONS, checkpoint names, the PROMPT line and every other stage intact"* — and returns the
FULL revised module, which then goes through the identical oracles + `max_repairs` repair
ledger (engine unchanged). It reuses the existing repair machinery via a single
`first_user` override threaded through `_dispatch` / the backend runners; the from-scratch
`generate_game` path is byte-unchanged (tests protect it). `action_taken`:
`"revised"` on verdict COMPLETED / `"revise_failed"` (verdict + last repair hint appended
to the directive trail, exactly as the regenerate path does after `26b3fc4`). The revised
game keeps the ORIGINAL PROMPT (provenance); TITLE may gain a version suffix.

Seams (tests monkeypatch, no torch/network): `verify_fn`, `g3_prime_fn`, `generate_fn`,
**`revise_fn`**. Ledger + return record now carry `"mode"`. The CLI chain advances on
`"revised"` OR `"regenerated"` (with a `new_game_path`), and stops otherwise.

### 8.1 The first LIVE revise round (the acceptance)

Ran from the agent worktree, `NODE_PATH` → main-checkout `nodeworld/node_modules`,
`OPENROUTER_API_KEY`/`_MODEL` injected into the process env from the main-checkout
`env.py` (gamegen's `_repo_root()` resolves to the *worktree*, which has no `env.py`):

```
python -m harness game curriculum \
  scenes/games/v23_showcase/two_switch_vault/game.js \
  --mode revise --budget 500000 --rounds 2 --backend openrouter \
  --out-dir scenes/games/curriculum_r1_revise --json
```

Backend **openrouter / tencent/hy3:free**. Whole 2-round CLI run ≈ **200 s** wall.

| Round | Input game | verify (tree) | G3′ @500 k (`sr`, first-succ, `cleared_gap1` mastery, plateau_latched) | grade | action | round wall |
|---|---|---|---|---|---|---|
| 1 | original `two_switch_vault` | ✓ 102 ticks, 26 replays | `sr` **0.0**, 1136, **0.155**, 1.155 | **hard** (stall `cleared_gap1`) | **revised** (hy3, 1 attempt, COMPLETED) | 104.7 s |
| 2 | round-1 revised (chained ✓) | ✓ 77 ticks, 45 replays | `sr` **0.125**, 792, **0.485**, 1.485 | **hard** (stall `cleared_gap1`) | **revised** (hy3, 1 attempt, COMPLETED) | 94.4 s |

**Did hy3 produce a COMPLETED minimal edit?** YES — round 1, verdict COMPLETED on the
*first* attempt (zero repairs), where from-scratch regeneration had failed 5/5.

**Does the diff target `cleared_gap1` only?** YES — the entire content diff (EOL-normalised)
is **3 edits**, all on the first gap:
* `TITLE` → `"Two-Switch Vault (eased gap1)"` (the allowed version suffix);
* `ground_stone` (the middle shelf) `pos [930,150] size [460,300]` → `pos [910,150]
  size [500,300]` — extended leftward to NARROW gap1;
* `spike` (the gap1 hazard) `pos [630,215] size [130,90]` → `pos [610,215] size [80,90]`
  — narrowed, annotated `// EASED gap1`.

`PROMPT` is byte-identical (provenance ✓), `ACTIONS` identical, and every later stage —
`switch_b`, `cleared_gap2`, `at_vault`, both gates, `on_step`, `success`, `failure`, all
checkpoint names/logic, the hero, `spike_2`, decor — is untouched. This is exactly the ACCEL
"ease HERE, keep the rest" edit the `hard` directive asked for, which from-scratch
regeneration could not do.

**Did round 2's grade move (hard → target/easy)?** NO — it stayed `hard`. BUT the ease
*measurably* improved learnability at a fixed 500 k budget: `sr` 0.0 → 0.125, first-success
1136 → 792 steps, and the localiser confirms the stall is loosening — `cleared_gap1`
per-milestone mastery **0.155 → 0.485** (≈3× further past the eased gate),
`plateau_mean_latched` 1.155 → 1.485. The agent gets meaningfully further past gap1; one
ease round at 500 k did not cross the `TARGET_RATE_LO = 0.50` band. This corroborates the §7
"budget-limited hard" caveat: the honest next lever is the full 2 M budget (and/or a second
ease round), not a bigger structural change. Revise mode did NOT fail — no `revise_failed`
this campaign — so the 5/5 from-scratch failure mode is retired for this game.

### 8.2 Notes / follow-ups from the live round

* **Round chaining overwrites the intermediate artifact.** Because the PROMPT is preserved,
  every round slugs into the SAME `<out_dir>/<slug>/` — round 2 reads the round-1 file
  (curriculum_round captures the source string first) and then `revise_game` promotes its
  own output to the same path, overwriting round 1's game. Functionally safe (the source is
  read before the overwrite), but the per-round artifacts are lost. A versioned per-round
  subdir (`.../round_k/`) would keep the full trajectory.
* **500 k is boundary-thin for `hard`.** Same caveat as the 200 k demo (§4): a production
  campaign should run 2 M so a still-`hard` grade after an ease is trustworthy, not a
  budget artefact.
