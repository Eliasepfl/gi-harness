# Inverse-value G4 attacker — critic-guided softlock hunting (Elias's idea)

> 2026-07-15. Replaces the LLM "negative-prompt" attacker (slow, non-real-time,
> extra LLM call/attack — DROPPED). Reuses the PPO critic G3' already trains.
> Supplies the SMART SEARCH the stale-state G4 tier (notes/engines/
> GODOT_RL_AGENTS_CAPABILITIES.md §4 + the g4 stale-state code) was missing.

## The idea (Elias)
To drive a game into a stalemate, don't search randomly — go the LEAST-optimal
direction using the value function INVERTED, run many agents in parallel from
different leaves, and flag when an agent takes actions for ~5-10 steps with NO
evolution of the state.

## Why it works
G3' trains PPO → we get BOTH a policy π (max value) AND a critic V(s)/Q(s,a),
for free. The attacker is the SAME critic inverted: `a = argmin_a Q(s,a)` (or a
low-value frontier selector). Random fuzz finds stuck regions by luck; a
critic-guided anti-optimal attacker STEERS toward dead/low-value regions
directly — higher hit-rate, ~zero extra cost, embarrassingly parallel.

## The subtlety (and its fix)
min-value covers TWO outcomes: getting STUCK (softlock) AND LOSING (hazard).
We want only stuck. Detection separates them: a LOSS terminates; a SOFTLOCK
FREEZES. So flag on state-frozen-while-acting, not on low value alone.

## Three layers
1. **SEARCH (new, Elias):** inverse-V / min-Q attacker over the trained critic
   steers toward low-value regions. Parallel: many seeds/leaves (Slurm array),
   each an anti-optimal rollout. Cheap — greedy argmin over the existing critic,
   no new training. Fallback when no critic (tree-solvable games): use the tree
   solver's milestone-value estimate inverted, or a quick shallow critic.
2. **DETECT (Elias's criterion, = stale-state trigger 1a):** over a window of
   N=5..10 decision ticks WITH actions applied: statetree.fingerprint() delta
   < EFFICACY_EPS (state frozen) OR a closed fingerprint cycle, AND no new
   checkpoint latched, AND not terminal. False-positive guard: legit "push into
   wall" moments are short; require the FULL window + no checkpoint progress.
3. **CONFIRM (existing 1c oracle):** from the frozen prefix P, run the G3
   Go-Explore solver on continuations at horizon len(P)+H, TICK_BUDGET. No
   TERMINAL_SUCCESS under P -> certified softlock witness (deterministic,
   replayable {seed, actions}); subtree saturation is stronger. This is what
   makes it a real finding, not a heuristic.

## Grading + integration
- softlock (1c-certified) -> hard outcome -> G4 grade `open` (existing
  `_HARD_OUTCOMES`); heuristic-only (1a/2 without 1c) stays soft.
- Slots into the g4 attacker ladder as the PRIMARY smart search, ahead of
  random fuzz. Reproducer = P + provenance {oracle:"inverse_value+tree_refute",
  critic_source, H, budget, seed}.

## Dependency + cost
- Needs the G3' critic -> composes with the G3'-on-gdscript wiring (in flight).
- Per game: the inverse-V rollouts are as cheap as greedy eval (seconds at
  speedup 8); the 1c confirm is one G3-solve budget per candidate (cap top-M).
- Parallel: one game per Slurm task; within a task, many inverse-V seeds.

## Honest limits
- The critic is only as good as the G3' training; a weak critic -> weaker
  steering (but detection+confirm are still sound, just less efficient search).
- Distinguishing "stuck" from "slow legit progress" is threshold-tuned (the
  N-window + no-checkpoint guard); the 1c oracle is the backstop against FPs.

## Measured (2026-07-15, softlock_pit.gd, in-image, speedup 8)
A/B at the SAME 1600-tick budget (6 seeds, eps 0.1, window 6, backplay handoffs
8/16/32/48 from the certified G3 witness; CONFIRM = refute_prefix H=30/3000):

| arm                          | detect/1k | certified/1k |
|------------------------------|-----------|--------------|
| inverse-value, competent critic | 10.80  | 2.70 (3/3 cap) |
| random fuzz                  | 2.08      | 2.08 (1)     |
| inverse-value, WEAK critic*  | 0.0       | 0.0          |

*the weak-critic honest limit made concrete: a quicktest-budget PPO (8-24k steps,
greedy success 0.0) has a near-uniform policy, so `argmin pi` degenerates to a
CONSTANT action that dives into a terminal (play-bounds loss) — a LOSS, which
DETECT correctly refuses to flag. With a competent critic (one that avoids the
pit dive — what a real G3'-certified artifact looks like) the steering is ~5x
random on detections and beats it on certified findings at the same budget.
Follow-up worth taking: gate the ladder tier on g3_prime SUCCESS (final_success
_rate > 0), not artifact existence alone. Zero false certifications in any arm.
---

# S1.5 — POLICY-GUIDED DESCENT (Elias's return-then-descend, implemented)

> 2026-07-15. Builds STALE_SEEKING_PLAN.md §3.1 (marked BUILD-FIRST). Slots in the g4
> ladder BETWEEN the greedy inverse-value tier (S1) and the deep trained seeker (S2):
> `harness/rl/adversary.py` (`descent_chooser`, `select_waypoints`, `descent_search`),
> `harness/verify/g4.py` (`_run_descent`, the model-gated CHEAP tier). Same CONFIRM
> oracle (`refute_prefix`) — no new certification path. Findings shape identical to the
> certified-softlock class (tier `descent`, family `policy_descent+tree_refute`).

## The idea
The greedy S1 attacker takes `argmin(pi)` from step 0. Where that collapses to one
direction it dives OOB (a LOSS, which DETECT refuses) or overshoots a turn, so it can
never COMPOSE a multi-step pocket entry (navigate AROUND, then IN). S1.5 uses the
COMPETENT working policy to NAVIGATE to a low-value waypoint, THEN alpha-ramps into the
freeze pocket — the literature's return-then-explore / PEG plan-then-command pattern.

## Three phases (`adversary.descent_search`)
1. **Waypoint pool + LOW-V selection** (`select_waypoints`). Targets from (a) the winning
   witness at PREFIX_HANDOFF cuts (Backplay / CCPT winning-spine) and (b) an inverse-
   visitation FRONTIER — the lowest-V states from short anti-policy rollouts
   (`collect_low_v_states`, top-k per rollout; TERMINAL / OOB states EXCLUDED — a body
   that left the arena is a dead-end, not a reachable RETURN target, and the critic is
   least reliable exactly there). Selected by LOW V ascending (our adaptation; AVF the
   adjacent precedent). **[eng.]**
2. **Return phase** — deterministic prefix replay to the waypoint (`rollout(prefix=...)`);
   bit-identical run-to-run (sound; a replay-fork, not an engine snapshot). The witness
   prefix for (a), the recorded action prefix for (b).
3. **Descent phase** — the alpha-ramped `descent_chooser`: at descent-tick `t`, with prob
   `alpha = linear_alpha_schedule(t)` take `argmin(pi)` (freeze-seek), else SAMPLE from
   `pi` (competent navigation — stays alive, keeps mobility). `alpha` ramps `0 -> 1` over
   `descent_ticks` — a SMOOTH handoff, not a hard switch. **alpha schedule [eng.]**
Frozen windows -> DETECT -> the SAME `refute_prefix` CONFIRM (soundness unchanged).

## A/B — S1 greedy vs S1.5 descent (candidates + CERTIFIED per 1k SEARCH ticks)
Same per-arm tick budget, same injected scripted critic (a stand-in for a trained G3'
policy — soundness is critic-independent: DETECT+CONFIRM certify regardless of how the
prefix was found). CONFIRM = one tree-solve per candidate (shared downstream cost).
Reproduce: `python scripts/descent_ab.py` in the certifier image.

Measured 2026-07-15 (Slurm job 18023279, mit_preemptable, speedup 8; budget 2400
ticks/arm; CONFIRM H=30/4000):

| fixture | arm | ticks | cands | cert | cert/1k |
|---|---|---|---:|---:|---:|
| SINGLE-STEP `softlock_pit.gd`  | S1 greedy     | 938 | 13 | 1 | 1.07 |
| SINGLE-STEP `softlock_pit.gd`  | S1.5 descent  | 357 |  3 | 3 | 8.40 |
| MULTI-STEP  `softlock_maze.gd` | S1 greedy     | 877 |  0 | 0 | 0.00 |
| MULTI-STEP  `softlock_maze.gd` | S1.5 descent  | 383 |  8 | 8 | 20.89 |

**Reading it (honest verdict).** On the MULTI-STEP maze the pocket is off BOTH axes from
the start (needs travel-RIGHT then turn-DOWN, and every impulse is pure-axis so no single
spammed / argmin-collapsed direction composes it): greedy argmin-from-0 dives straight
down the start column and certifies **ZERO**, while descent navigates to a pocket-band
waypoint and certifies **8**. That IS the point of the tier — **S1.5 >= S1, strictly
greater exactly where composition is required.** On the SINGLE-STEP pit the trap is one
straight dive away so both trip it, and descent also edges greedy on CERTIFIED/1k (8.40 vs
1.07): greedy's 13 detections are mostly the pit's period-2 cycle churn that CONFIRM
REFUTES (1/13 certifies), whereas descent's few candidates are all real softlocks (3/3).
`softlock_maze.gd` certifies G0-G3 and its pocket is reachable ONLY via the two-leg route
(`tests/test_gd_descent.py`). Zero false certifications; mini_collect stays clean.
---

# The TRAINED stale-seeker (PPO escalation tier)

> 2026-07-15. Elias: "can we not use PPO too for getting into stale states?"
> Code: `harness/rl/stale_seek.py` (reward + train + harvest + confirm),
> `harness/verify/g4.py` (`_run_seeker`, the gated deep tier). The greedy
> anti-policy search above stays FIRST-CLASS and runs FIRST; this is an
> ADDITIONAL last-resort escalation, not a replacement.

## The idea
The greedy anti-policy STEERS with a frozen critic. A PPO adversary goes further:
it LEARNS a policy whose reward IS the DETECT precondition, so it discovers stale
regions the one-step-greedy search cannot compose. It rides the EXACT G3' stack —
`GodotServeEnv` / `GodotBatchVecEnv` / `sb3_trainer` — batched N-in-one-proc at
speedup 8. No new training stack, no new certifier.

## Three pieces (all reuse existing machinery)
1. **REWARD** (`StaleSeekReward`): a small, TIME-DECAYING positive per step where the
   state fingerprint froze (`fp_delta < EFFICACY_EPS`, the SAME imported constant)
   while an action was applied, no new checkpoint latched, and the episode did not
   terminate — escalating over consecutive frozen ticks, big bonus + a CANDIDATE
   emission on a full `window` (= g4's `STUCK_WINDOW`, imported). The freeze test
   reads the SAME obs vector the policy sees (`fingerprint_from_obs` inverts
   `build_obs_vector`; float32 resolution ~5e-5 px << EFFICACY_EPS, so the decision
   is faithful). A LOSS (terminal, not success) AND a WIN are both strongly penalised
   — a loss is not a softlock (Elias's original distinction), a win defeats us.
2. **HARVEST** (`train_stale_seeker` + `harvest_candidates`): training logs every
   window-complete `{seed, prefix}`; then the trained policy is greedy-rolled from
   points SAMPLED ALONG THE WINNING TRAJECTORY (replay a witness prefix = the working
   policy's competent navigation to a deep waypoint, THEN freeze-seek from there —
   addendum #2). Prefix = the moves leading INTO the frozen region (the CONFIRM cut).
3. **CONFIRM** (`confirm_candidates` -> `g4.refute_prefix`): every candidate goes
   through the UNCHANGED tree-refutation oracle. No cert without a refuted, replayable
   witness — soundness identical to the cheap tiers. A certified prefix is a `softlock`
   finding of the SAME shape (`tier:"seeker"`, hard -> grade `open`).

## Anti-idling (Elias addendum #1 — mandatory)
"Going into a corner and waiting is NOT a softlock; it is purposely not finishing."
Three guards, all baked into the reward:
- **(a) time decay**: `decay(tick) = max(0, 1 - tick/horizon)` scales the freeze
  reward, so camping to the horizon earns ~nothing — an early trap is worth more.
- **(b) mobility gate**: a freeze scores only AFTER the body has travelled
  `STUCK_MOVE_MIN` px (imported) this episode — a body that never moved is idling,
  not stuck.
- **(c) escapability**: the window only completes on a SUSTAINED same-action freeze
  (freeze-then-leave never earns the bonus); DIFFERENT-action escape is caught by a
  cheap pre-CONFIRM `escapability_probe` (random tails that reach success -> drop) and,
  soundly, by CONFIRM itself (it tries every action). Escapable candidates never certify.
Thresholds tagged `[eng.]` in the code are calibration knobs a principled
anti-idling/softlock-seeking literature pass can refine.

## Gate + honest cost
ONE PPO training per game. So the tier is OFF unless the caller asks for the DEEP
grade (`run_g4(deep=True)` / `attack_game(deep=True)`) AND the cheap tiers above
certified NOTHING AND the lane can train (gdscript + a `game_path`). `deep=True`
implies `stale=True` — the cheap stale tier IS the gate. If a cheap tier already
certified a softlock, the seeker is skipped ("no wasted PPO training").

## A/B — candidates + certified per 1k SEARCH ticks (honest accounting)
Fixture `tests/fixtures/gd_games/softlock_pit.gd` (a certified GDScript momentum-pit
whose slow approach FREEZES forever). "ticks" = simulation/env ticks spent DISCOVERING
candidates; the seeker's total INCLUDES its PPO training ticks (that is the honest
comparison). CONFIRM (one tree-solve/candidate) is the shared downstream cost.
Reproduce: `python scripts/stale_seek_ab.py` in the certifier image.

Measured 2026-07-15 (Slurm job 18019062, mit_preemptable, speedup 8; seeker =
PPO 4k steps, num_envs=2, horizon 40, window 5):

| method                  | ticks | cands | certified | cand/1k | cert/1k |
|-------------------------|------:|------:|----------:|--------:|--------:|
| random_fuzz             |  2039 |     5 |         5 |    2.45 |    2.45 |
| greedy_anti_policy      |   423 |     4 |         4 |    9.46 |    9.46 |
| trained_seeker(+train)  |  4105 |    10 |         8 |    2.44 |    1.95 |

**Reading it.** On this SIMPLE pit the trap is one spam-run away, so the GREEDY
anti-policy trips it almost immediately (9.46 cert/1k — ~4-5x both others); the
TRAINED seeker finds the MOST candidates in absolute terms (10 unique, 8 certified —
it explores more of the frozen region), but its ~4k training ticks sink its per-tick
efficiency to ~1.95 cert/1k — **on the simple fixture the trained seeker LOSES on
efficiency, exactly as predicted.** That is the point: the seeker is
the escalation tier for the games the cheap search CANNOT compose (a pocket reachable
only by a multi-step route — push a block, then enter behind it), where the greedy
one-step search burns its budget and the learned/​witness-seeded policy pays back its
training cost. The `_run_seeker` gate encodes this: cheap-first, seeker only when
cheap comes up empty. A compositional multi-step fixture to demonstrate the seeker
WINNING is the natural next fixture (designed, not yet measured — see below).

## Cross-reference: STALE_SEEKING_PLAN.md R1-R5 (implemented vs the recipe)
The literature-grounded reward recipe (`STALE_SEEKING_PLAN.md` §3.2) landed on main
in parallel with this implementation. Where the shipped reward matches/deviates —
all deviations are EFFICIENCY-ONLY per the plan's own ledger (§3.3, CONFIRM is sole
certifier), so none require rework to stay sound:
- **R1 (decayed, novelty-gated freeze bonus):** MATCH on decay + anti-farming; DEVIATES
  in mechanism — time-indexed linear decay (tick/horizon) + one bonus per frozen RUN
  (`emitted` latch), not the per-fingerprint-cell λ^m_c episodic counter; the obs already
  carries normalized elapsed time (env.py `build_obs_vector`), so time-indexing keeps Markov.
- **R2 (escapability probe gate):** PRINCIPLE MATCH (refusal-while-progress-reachable earns
  nothing) but placed at HARVEST/CONFIRM time (`escapability_probe` drops trivially-escapable
  candidates pre-oracle), not inside the training reward — the batched serve host cannot
  fork mid-episode; CONFIRM's full-action lookahead is the exact tier-(b) oracle.
- **R3 (displacement prerequisite):** MATCH in intent (starves spawn-camping via the
  imported `STUCK_MOVE_MIN`); DEVIATES in metric — cumulative travelled px this episode,
  not distinct-fingerprint-cells over a sliding pre-freeze window.
- **R4 (PBRS descent pressure):** NOT ENABLED — a `low_v_coef` shaping hook exists but is
  off by default and plain −V, not the γΦ(s')−Φ(s) PBRS form; the plan itself gates R4 on
  R5 verification, which has not been performed.
- **R5 (truncation hygiene):** MATCH — truncation is reward-neutral (never the terminal
  penalty), the envs split done_term/done_trunc on the wire, and elapsed time is in the
  obs; the "pinned SB3 bootstraps truncated" verification is still owed before arming R4.
- **Anti-recipe (no flat living cost):** MATCH — no living cost anywhere in the reward.

## Honest limits (seeker tier)
- Costs one PPO training per game — only worth it as the gated deep escalation.
- Online batched PPO cannot reset to an arbitrary mid-trajectory state, so the
  witness-waypoint seeding lives in the HARVEST rollouts (fully under our control, no
  credit-assignment corruption), not in the online training loop.
- The batched VecEnv autoreset contract forbids forcing a mid-episode done from a
  wrapper, so the batched trainer emits-and-continues (reward bounded: freeze reward
  capped at the window and time-decayed); the single-env harvest wrapper ends the
  episode on window-complete. Candidate discovery is identical.
- Determinism: seeded policy + fixed seeds + imported fixed thresholds; CONFIRM is the
  sound backstop against every false positive, unchanged.
