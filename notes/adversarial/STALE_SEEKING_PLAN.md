# Stale-seeking plan — policy-guided descent + anti-idle PPO SEEKER (literature-grounded)

> 2026-07-15. Grounds Elias's two new principles in the game-testing / adversarial-RL /
> reward-design literature (three lanes, adversarially skeptic-verified against primary
> texts) and turns them into the next attacker tiers. Anchors:
> `harness/rl/adversary.py` (merged S1 attacker), `INVERSE_VALUE_G4.md` (design + A/B),
> `G4_DESIGN.md` (grade ladder: open / repaired / hardened / bulletproof, tiers 0/1/2).

## 1. The two principles, stated precisely

**P1 — POLICY-GUIDED DESCENT.** Sample waypoints from the winning trajectory (and from
the archive of low-V visited states), then use the WORKING policy — it already knows how
to navigate — to travel INTO low-V / inverse-value positions, and only THERE hand off to
the freeze-seeking attacker. Rationale: the anti-policy attacker starting cold reaches
shallow dead regions; a competent traveler deposits it deep in committed low-value
territory it could never reach on its own ("making more sure it would go to these
unoptimal positions, as we already have a working policy").

**P2 — ANTI-IDLING.** The freeze-seeking reward must decay over time/repetition. Going
into a corner and waiting is NOT a softlock — it is refusing to finish. The system must
distinguish **environment-stuck** (success unreachable from here) from **agent-refusal**
(success reachable, policy won't take it), and the SEEKER's training signal must not pay
for refusal.

## 2. What the literature says (skeptic-corrected)

### 2.1 Game-testing lane
- **Stuck-definition taxonomy** (weak→strong): (a) episode-timeout position clustering
  (Bergdahl et al. 2020, EA SEED, arXiv:2103.15819 — "Stuck Player" sandbox, timeout
  positions "clearly indicating where the agents get stuck"); (b) terminal-state
  distribution outliers over a visited-state buffer (Gordillo et al. 2021,
  arXiv:2103.13798 — found an unseeded physics trap); (c) N-consecutive-actions-without-
  progress heuristics (TITAN, arXiv:2509.22170 — 20 actions triggers LLM reflection;
  deployed in 8 real QA pipelines; **~30% overall report-level false-alarm rate** — note:
  overall, not stuck-oracle-specific); (d) our frozen-fingerprint DETECT; (e) formal
  AG(EF(goal)) with counterexample traces (Mawhorter & Smith, FDG 2021 — exact but
  offline on a hand-authored abstraction).
- **Novelty position (phrased honestly):** we found NO published system that closes the
  loop heuristic-online-DETECT → solver refutation → certified replayable witness. TITAN
  stops at (c); the CTL work never runs against a live policy; Cooper FDG 2025 is
  solver-certified but offline PCG-side. State as "we found none", never "none exists".
- **Anti-camping reward in deployed testers:** Gordillo's count-annealed curiosity
  R = 0.5·(1 − N_i/500) — decays to exactly zero as a point is revisited; respawn sampled
  from the visited buffer weighted by INVERSE visit count; 320 agents / 24h → 90% map
  coverage. cMarlTest (arXiv:2502.14606) goes further: revisits get a decreasing reward
  that can go NEGATIVE. CCPT (Sestini et al., arXiv:2202.10057) hard-wires anti-refusal:
  an always-on extrinsic goal term ("needed to make agents arrive in the goal area
  independently of α") and a filter keeping only deviating trajectories **that still
  reach the goal**.
- **Correction (Reveal-More):** its seeds are periodic save states from a human playing
  normally, NOT winning trajectories — "explore around the winning spine" as a single
  policy is CCPT's contribution; Reveal-More grounds only "amplify around known
  trajectories".

### 2.2 Adversarial / goal-reaching lane
- **The two-level pattern is the consensus, and it IS P1:** SELECT a target waypoint via
  archive/value heuristics; REACH it with a competent (ideally goal-conditioned) policy;
  EXPLORE/descend locally from there. Go-Explore ("first return, then explore", Nature
  2021, arXiv:2004.12919; policy-based variant returns via a goal-conditioned policy —
  robust to stochasticity, unlike open-loop replay). PEG (arXiv:2303.13002): plan the
  goal such that commanding the current goal-conditioned policy lands the agent in
  high-exploration-value states, then explore.
- **Correction (selection signal):** Go-Explore/PEG select by NOVELTY/exploration-value
  (Go-Explore weight W = 1/sqrt(C_seen+1)), not low-V. Selecting descent targets by the
  critic's own LOW V is OUR adaptation — narrow the novelty claim to "first use of
  inverse-critic targeting for archive descent in game QA" and cite Uesato et al.'s AVF
  (ICLR 2019, arXiv:1812.01647 — adversarial value function selecting evaluation states
  where the agent likely fails) as the adjacent precedent.
- **Why not greedy per-step V-descent:** the value net is least reliable exactly in
  low-V/OOD regions (directional support: arXiv:2304.13424, Table 2 — Q consistently
  lower on OOD/failure states; NB the previously-circulated verbatim quote from this
  paper is fabricated — cite as paraphrase). Use V to pick destinations and order
  candidates, never as a per-step controller. `adversary.py` already obeys this.
- **Directed, not scalar-max seekers:** ROA-Star (NeurIPS 2023) — goal-conditioned
  exploiters "exploit the weaknesses in certain directions", "greatly improved" at
  weakness-spotting vs AlphaStar's unconditioned exploiters (the "late in training"
  degradation phrasing is paraphrase). Gleave et al. (ICLR 2020, arXiv:1905.10615):
  a value-minimizing adversarial policy reliably breaks a self-play-robust victim —
  existence proof for policy-trained value minimization; also motivates escalation
  (fine-tuning vs one adversary just breeds the next).
- **Risk-guided falsification done right:** arXiv:2506.03469 prioritizes high-risk +
  underrepresented states using a TD-trained risk critic PLUS ensemble epistemic
  uncertainty PLUS TD-error-guided mutation — the upgrade path for waypoint selection
  beyond plain low-V.

### 2.3 Anti-idle reward-design lane
- **Camping is the canonical degenerate optimum.** Noisy-TV is a failure of
  prediction-error curiosity (Burda et al. Large-Scale Study, arXiv:1808.04355 — the
  literal TV-in-maze); RND (arXiv:1810.12894) was designed to MITIGATE it (do not
  attribute the failure to RND). "Any undecaying bonus gets farmed by a stationary or
  looping policy" is a design maxim distilled from demonstrated instances, not a stated
  theorem — and the farmed behavior can be an action LOOP, not literal motionlessness
  (which is why DETECT has the cycle clause).
- **The three converging fixes:** (i) count/visit annealing (Gordillo, cMarlTest,
  pseudo-counts); (ii) episodic novelty that resets each episode in a
  controllability-filtered embedding (NGU, arXiv:2002.06038 — memory "starts completely
  empty" each episode); (iii) displacement/reachability prerequisites (Savinov et al.,
  arXiv:1810.02274 — bonus only for states ≥ k reachability-steps from episodic memory,
  built to kill couch-potato behavior).
- **PBRS descent shaping is safe — with two conditions.** Ng-Harada-Russell 1999:
  F(s,s') = γΦ(s')−Φ(s) preserves the optimal-policy set; Devlin & Kudenko 2012 extend
  to time-varying/online-updating potentials. **Skeptic corrections:** (a) cycle sums
  telescope to zero only at γ=1; at γ<1 a camper at s collects (1−γ)·V(s) per step under
  Φ=−V — no camping OPTIMUM is created (invariance bounds total farmable shaping at
  −Φ(s₀)), but the "cannot be camped because cycles net zero" argument is wrong as
  stated; (b) safety is CONDITIONAL on truncation hygiene — truncation-treated-as-
  terminal leaves a −γ^T·V(s_T) residual that actively REWARDS being idle-kicked while
  camped in low-V corners.
- **Truncation hygiene:** Pardo et al. (ICML 2018, arXiv:1712.00378) — bootstrap V(s_T)
  on time-limit truncation; Gymnasium terminated/truncated split codifies it.
  `harness/rl/env.py:257-266` already implements the split; **follow-up: verify the
  pinned SB3 version bootstraps on `truncated` for PPO before relying on R4.**
- **Environment-stuck vs agent-refusal:** softlock = reachable state where EF(goal)
  fails (Mawhorter & Smith — verbatim); the stuck-vs-refusal dichotomy is OUR extension
  of their policy-independent formalism, present it as such. Their "wiggle-room" example
  (Mario alive and moving in an inescapable pit) proves fingerprint-freeze is neither
  necessary nor sufficient — the true discriminator is progress-REACHABILITY, and
  gi-harness owns the oracle (tree solver + deterministic prefix-replay forking — note:
  replay-fork, not an engine-state snapshot).
- **Anti-recipe — flat per-step living cost.** Taxes deep excursions exactly as much as
  camping and adds termination-seeking pressure. (Our arithmetic argument, not a
  literature citation.)

## 3. THE PLAN — attacker tier ladder

Sub-tiers within the G4 softlock attacker (all feed the same CONFIRM =
`harness.verify.g4.refute_prefix`; grades per G4_DESIGN §3.7):

```
S0  random fuzz                      (merged — the A/B baseline arm)
S1  anti-policy + V-frontier + backplay   (merged — adversary.py)
S1.5 POLICY-GUIDED DESCENT           (NEW — this plan, build first)
S2  PPO stale-SEEKER + anti-idle reward   (in-flight — spec below)
S2+ goal-conditioned / population escalation   (contingency only)
```

### 3.1 S1.5 — policy-guided descent (P1)  [IMPLEMENTED 2026-07-15]

> Built in `harness/rl/adversary.py` (`descent_chooser` alpha-ramp, `select_waypoints`
> low-V pool, `descent_search`) + `harness/verify/g4.py` (`_run_descent`, cheap model-
> gated tier slotted between S1 and S2). A/B (S1 greedy vs S1.5 descent) + the multi-step
> fixture `tests/fixtures/gd_games/softlock_maze.gd` are in `INVERSE_VALUE_G4.md` §S1.5:
> on the multi-step maze greedy certifies 0, descent certifies 8 (same budget). One
> adaptation vs the spec below: the frontier EXCLUDES terminal/OOB states (dead bodies
> are not reachable RETURN targets) — otherwise the critic's OOB unreliability floods the
> low-V pool. EFFICIENCY-ONLY; CONFIRM unchanged.

Three phases per rollout, composed in `rollout()`'s existing phase machinery:

1. **Waypoint sampling.** Targets from two pools: (a) states along the certified G3
   winning witness at the existing `PREFIX_HANDOFF` ticks (Backplay / CCPT winning-spine
   sampling); (b) the persistent low-V archive (§5) — selection weight combines V
   ascending with Gordillo/Go-Explore inverse-visitation 1/sqrt(C+1). [Cited: Backplay,
   Go-Explore archive selection, Gordillo respawn weighting, our low-V adaptation w/ AVF
   as precedent.] **EFFICIENCY-ONLY.**
2. **Travel via the working policy.** Return-to-waypoint by deterministic prefix replay
   (we have exact replayability — the cheap, sound Go-Explore "restore" analog), then a
   DESCENT phase driven by an α-mixed chooser: with prob (1−α) sample from π (competent
   navigation — stays alive, keeps mobility), with prob α take the anti-policy argmin;
   α ramps 0→1 over the descent phase (CCPT's α-conditioning, collapsed to a per-tick
   schedule since we have no goal-conditioned policy yet). This travels INTO low-V
   territory instead of dying at the first hazard, which is precisely the weak spot the
   A/B exposed (weak critic → constant action → pit dive → LOSS). [Cited: CCPT, PEG
   plan-then-command, Go-Explore policy-based return.] **EFFICIENCY-ONLY.**
3. **Freeze-seek handoff.** Full anti-policy control (existing chooser) + DETECT window,
   unchanged.

Non-goal for now: training a true goal-conditioned return policy (HER/PEG). Deterministic
replay is exact in our engine (fixed seed, `Engine.time_scale` caveat respected), so the
Nature-2021 stochasticity argument for a return POLICY does not yet bind. Revisit only if
nondeterminism appears; then Go-Explore policy-based + HER is the cited path.

### 3.2 S2 — PPO stale-SEEKER anti-idle reward spec (P2)

Reward = the DETECT condition made into an event reward, disciplined by four terms.
Every term is **EFFICIENCY-ONLY**: certification comes solely from CONFIRM, so reward
failures move yield, never validity (see §3.3 for the one soundness asterisk).

- **R1 — decayed, novelty-gated freeze bonus (decay schedule).** Event reward when a
  DETECT window fires at fingerprint cell c: r = β·λ^{m_c}, m_c = count of previously
  rewarded windows at c THIS episode (per-cell counter, reset each episode — NGU-style
  episodic memory), λ ≈ 0.6–0.9. Farmable value at one cell bounded by β/(1−λ): finding
  two distinct candidate cells strictly beats camping one. Per-episode reset covers the
  false-negative risk of a true softlock at an already-farmed cell. [Gordillo annealing,
  NGU episodic term, cMarlTest decay.]
- **R2 — escapability probe gate (the refusal/stuck discriminator).** Before crediting a
  freeze at s: fork by deterministic prefix replay, run a SHALLOW progress probe —
  tier (a) cheap: apply K actions × d ticks, any fingerprint change ⇒ possible refusal;
  tier (b) correct: shallow tree-solver lookahead depth D asking whether ANY action
  sequence latches a checkpoint / increases progress. Pay R1 only when (b) finds no
  progress. **Gate on progress-reachability, NOT state change** — wiggle-room softlocks
  (Mawhorter & Smith) move while remaining dead; state-invariance gating misclassifies
  them as refusal. This is CCPT's anti-refusal principle transposed: refusal-while-
  success-reachable earns nothing. [Mawhorter & Smith EF(goal); CCPT extrinsic/filter;
  Leave-No-Trace / reversibility-RL as the learned-approximation strand we replace with
  the exact oracle.]
- **R3 — displacement prerequisite (mobility).** Multiply the freeze bonus by
  1[≥ n_min distinct fingerprint cells in the last W ticks pre-freeze]. Starves
  spawn-camping outright. Blind spot (softlocks adjacent to spawn) is covered by
  backplay/waypoint seeding, which carries displacement for free. [Savinov reachability
  prerequisite; Gordillo/ARLPCG mobility pressure.]
- **R4 — PBRS descent pressure.** F(s,s') = γΦ(s')−Φ(s), Φ = −V_working (critic reused
  from G3', updated online under the Devlin-Kudenko dynamic-PBRS license). Pays for
  descending into low-V, cannot create a camping optimum (invariance theorem — NOT the
  γ=1 telescoping argument), and makes sit-still dominated whenever a V-decreasing
  action exists. **Enabled only after R5 is verified** (truncation-as-terminal flips
  this term pro-camping).
  - **R4b — VALUE-DEATH (the DETECT-side sibling of R4, IMPLEMENTED 2026-07-15).** R4 uses
    V as a training-reward *shaping* pressure toward low-V; R4b uses V as a *DETECT signal*
    for a softlock that MOTION cannot see. `adversary.detect_value_death` fires when V(s)
    is at/below a RELATIVE collapse floor (`Vmin + 0.25·(Vmax−Vmin)` over the rollout's own
    V — relative so it adapts per game; a flat critic yields no floor) for a full window,
    no new latch, non-terminal — REGARDLESS of fingerprint deltas, so a body WIGGLING in a
    trap (which `frozen`/`cycle` structurally miss — the Mawhorter-Smith wiggle-room hole)
    is caught. Its reward-side twin is `StaleSeekReward.low_v_occupancy_coef`/`low_v_floor`:
    a motion-invariant low-V occupancy reward ALONGSIDE the freeze term, same mobility gate
    + decay, off by default. Fixture `softlock_wiggle.gd` proves it. EFFICIENCY-ONLY —
    inherits the critic's quality; the fingerprint modes stay the critic-free floor; CONFIRM
    is the sole certifier (a value_death candidate is a suspect until `refute_prefix`).
    Unlike R4 this needs NO R5 precondition: it is a detector, not a training-reward term
    (the reward twin is off by default and never the certifier), so no truncation-hygiene
    dependency binds it.
- **R5 — truncation hygiene (precondition).** Idle-kick and horizon caps emit
  `truncated` with partial-episode bootstrapping; normalized elapsed-time in the
  observation (R1 is time-indexed → keep Markov). env.py already splits
  terminated/truncated; VERIFY pinned SB3 PPO bootstraps truncated before enabling R4.
  [Pardo et al.; Gymnasium.]
- **Anti-recipe:** no flat living cost (taxes search = camping; termination-seeking).

### 3.3 SOUND vs EFFICIENCY-ONLY (the honest ledger)

- **SOUND (CONFIRM-protected):** only the certificate itself — refutation-direction:
  solver finds an escape ⇒ camper claim dead, with a replayable disproof; solver finds
  no success under prefix P within (H, TICK_BUDGET) ⇒ **"solver-refuted-within-budget"
  softlock**, replayable {seed, actions}. Label it exactly that unless the subtree is
  saturated (then it is exhaustive). Zero-false-certification invariant stays enforced
  by witness replay assertion (certify.py pattern).
- **EFFICIENCY-ONLY:** S1.5 entirely; R1–R4 AND R4b value_death (the DETECT trigger +
  its reward twin); DETECT thresholds and the relative collapse floor; waypoint selection;
  candidate ordering. Their failure modes move detect/certified yield and recall,
  never certificate validity — value_death only widens DETECT recall; CONFIRM is unchanged.

### 3.4 S2+ contingencies (do not build preemptively)

- SEEKER mode-collapses onto one softlock family → condition it on a target fingerprint
  region / DETECT signature (ROA-Star directed exploiters; DisCo-style region goals).
- Critic mode-collapse in SEARCH → small Pareto population over (progress, novelty)
  (Wuji), sized 3–5, only if the single anti-policy arm's diversity metric (§6) stalls.

## 4. Budget allocation and grade wiring (cheap → deep)

| Sub-tier | Marginal cost | Runs when | Grade role |
|---|---|---|---|
| Free pre-filter: log episode-end fingerprints of ALL G3'/eval runs; outlier cells → seed CONFIRM directly | ~0 (logging) | always | feeds every grade |
| S0 fuzz | ticks only | always (Tier-0 analog) | open |
| S1 anti-policy | ticks only (critic free from G3') | g3_prime artifact exists AND final_success_rate > 0 (the weak-critic gate — measured follow-up in INVERSE_VALUE_G4) | open → hardened rounds |
| S1.5 descent | ~2× S1 tick budget (travel phase) | same gate + certified witness exists | hardened rounds; required before "hardened" is granted |
| S2 SEEKER | PPO training run (≈ one G3' probe budget) + eval ticks | bulletproof candidacy only (Tier-2 analog: deliberately rare) | bulletproof rounds |
| CONFIRM | one G3-solve per candidate | always, cap top-M ordered by V ascending | the only certifier |

Cross-run: the persistent archive (§5) means successive rounds do not re-explore
(TITAN coverage-memory pattern); budget_ticks caps stay per-round.

## 5. Concrete integration points

`harness/rl/adversary.py` (merged S1 — extend, don't fork):
- New `descent_chooser(critic, alpha_schedule)` beside `anti_policy_chooser()` (~L152):
  per-tick mix of sample-from-π and argmin-π, α from tick-indexed schedule.
- `rollout()` (~L181): already supports `prefix=` replay + handoff; add an optional
  `phases=[(chooser, n_ticks), ...]` so one rollout runs travel-then-seek. `handoff_tick`
  bookkeeping generalizes to phase boundaries.
- `_collect_frontier()` (~L417): collect top-k low-V states per rollout (currently only
  the single best) and record (fp_cell, V, prefix, visit_count) into a persistent
  archive (JSON beside the g4 report); selection weight V-ascending × 1/sqrt(C+1).
- `search()` layer 3 (~L400): waypoint pool = archive ∪ witness handoffs; S1.5 rollouts
  use the phase schedule; keep `budget_ticks` accounting unchanged.
- `ab_bench()` (~L441): add arms `descent` and (later) `seeker`; add per-arm
  `camper_fraction` (CONFIRM-refuted / DETECT firings) and `distinct_certified`.

New module `harness/rl/stale_seeker.py` (the in-flight S2 work lands here):
- Gym wrapper over `PlanckEnv` (`harness/rl/env.py` — reuse R_CHECKPOINT plumbing and
  the existing terminated/truncated split at env.py:257-266) implementing R1–R5;
  DETECT-event trigger reuses `detect_softlock_window()` verbatim as the reward event.
- R2 probe calls a shallow-budget variant of `harness.verify.g4.refute_prefix`
  (small H, small tick budget) — precision filter only, never the certificate.
- SB3 PPO via `sb3_trainer.py` patterns; SLURM: max 4-5 jobs, mit_preemptable,
  --requeue (per standing job policy).
- Certification path unchanged: SEEKER's DETECT hits → candidate dicts (same schema as
  `_candidate_from_rollout`) → g4 CONFIRM → `_HARD_OUTCOMES` → grade.

## 6. Metrics — extending the measured A/B (INVERSE_VALUE_G4 §Measured)

Same-budget arms: `random | anti-policy(S1) | descent(S1.5) | seeker(S2)`; per arm:
- **detect/1k** and **certified/1k** (existing headline numbers; S1 baseline: 10.80 /
  2.70 competent critic vs 2.08 / 2.08 fuzz).
- **camper_fraction** = CONFIRM-refuted DETECT hits / DETECT hits — the direct measure
  of P2 precision; should fall sharply when R1+R2 land. **Skeptic caveat: this is
  precision ONLY — it cannot see softlocks never flagged.**
- **Recall proxy:** a planted-softlock corpus (games with deliberately authored
  softlocks, the Cooper FDG-2025 move transposed to LLM-written GDScript) — report
  fraction of planted locks certified per budget. This is the recall half
  camper_fraction misses.
- **distinct_certified** (deduped frozen fingerprints) — diversity / mode-collapse
  sentinel, triggers §3.4 contingencies.
- **time-to-first-certified** (ticks) — the cheap-tier ordering justification.
- **Honest cost accounting:** S2 reports certified per 1k ticks TWICE — eval-ticks only
  AND total including PPO training ticks; S1/S1.5 report critic cost as zero-marginal
  (reused G3' artifact) but say so explicitly. CONFIRM solve budgets reported per arm.
- **Zero false certifications** remains a hard invariant (witness replay assertion),
  reported per run.
