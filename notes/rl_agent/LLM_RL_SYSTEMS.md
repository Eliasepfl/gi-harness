# LLM-designer + RL-player hybrid systems — research pass & recommendation

> Research date: 2026-07-14. Question (Elias): the G3 solvability prober (random search,
> soon a Go-Explore action-prefix tree) caps how HARD — and therefore how VARIED —
> generated games can be, because "solvable" today means "reachable by blind search".
> Hypothesis: keep the LLM designing the game AND declaring the discrete action set, but
> add an RL agent that LEARNS to play, so "solvable" can mean "learnable by a policy".
> This note verifies the prior art (every citation checked against a live source; arXiv
> IDs resolve), weighs the evidence, and ends in an opinionated architecture for THIS harness.
> The G3-caps-hardness premise is real: G3 is E=40 random episodes × H≤300 ticks with a
> checkpoint-guided second pass (CONTRACTS §4); the Go-Explore tree (adversarial/STATE_TREE.md)
> makes search stronger but is still *existence* search, never *learnability*.

---

## 1. Taxonomy of LLM+RL coupling for environment generation

| # | Pattern | What the LLM does | What RL does | "Solvable/good" = | Key works (verified) |
|---|---------|-------------------|--------------|-------------------|----------------------|
| a | **LLM designs env, RL plays it** | writes env code (+ often reward code), adapts to agent's learning progress | trains a fresh/transferred policy per task | task is *learnable at suitable difficulty* | OMNI-EPIC; EnvGen; GenEnv |
| b | **LLM writes the REWARD, RL learns** | emits reward/shaping code or a preference signal; iterates from training stats | optimizes the LLM-authored reward | policy trained on LLM reward hits the goal | Eureka; DrEureka; Text2Reward; Motif |
| c | **UED auto-curriculum (no LLM)** | — (the "difficulty engine" we may need) | co-trains student + a regret-maximizing level generator | level maximizes *regret* (learning potential) | PAIRED; ACCEL; POET / Enhanced POET; (Robust) PLR |
| d | **LLM-as-policy / skills** | plays directly, or writes reusable option-policies | (optional) distills skills into a small net | LLM/skill library completes tasks | Voyager |

### Pattern (a) — LLM designs env / RL plays (our closest analog)
- **OMNI-EPIC** — *Open-endedness via Models of human Notions of Interestingness with
  Environments Programmed in Code*, Faldor, Zhang, Cully, Clune. arXiv **2405.15568**,
  **ICLR 2025**. FMs generate *both* environment code and reward code (e.g. "traverse the
  obstacle course quickly without touching red objects"); a **Model of Interestingness (MoI)**
  archive gates new tasks for novelty/learnability; a **success detector** (LLM/VLM or code)
  decides completion; RL agents are trained per generated task (PyBullet physics), reported
  over **>200 iterations** of the generate→train→archive loop. This is the canonical
  "solvable = an RL agent can learn it" system and the direct precedent for G3'.
  Our harness is a *stricter* variant: our success is an unshaped **code** certificate, not a
  VLM judge (see §5 alignment). OMNI-EPIC's predecessor **OMNI** (same group) supplies the
  MoI idea without the code-environment part.
- **EnvGen** — *Generating and Adapting Environments via LLMs for Training Embodied Agents*,
  Zala et al. arXiv **2403.12014**, **COLM 2024**. LLM emits *environment configurations*
  (terrain, item distributions, spawn odds), a **small RL agent** trains on a mixture of
  original + generated envs, and the LLM **adapts the envs from the agent's weaknesses**
  (feedback = per-skill performance). Headline efficiency result: the small RL agent
  **outperforms a GPT-4 agent** on Crafter long-horizon tasks using only **~4 LLM calls total**,
  versus thousands of LLM calls per episode for an LLM-as-agent. This is the cost argument for
  RL-as-player and the template for the curriculum feedback loop (Phase 2).
- **GenEnv** — *Difficulty-Aligned Co-Evolution Between LLM Agents and Environment Simulators*,
  arXiv **2512.19682** (2026 preprint; secondary-sourced, verify before external citation).
  Two-player curriculum game: an LLM environment-trainer adjusts task difficulty to **target a
  success rate**, both agent- and task-pools update each epoch. Directly relevant to "make it
  harder where the agent plateaus". (Cf. also *From Trainee to Trainer*, arXiv 2606.17682, and
  SCALER, arXiv 2601.04809 — recent 2026 LLM-designs-training-env preprints, listed for
  currency, not relied on.)

### Pattern (b) — LLM writes the reward, RL learns
- **Eureka** — *Human-Level Reward Design via Coding LLMs*, Ma et al. (NVIDIA). arXiv
  **2310.12931**, **ICLR 2024**. GPT-4 does **evolutionary search over reward code** using raw
  env source as context + a "reward reflection" from training stats; on **29 open-source RL
  envs / 10 robot morphologies** it beats human-expert rewards on **83%** of tasks (avg +52%
  normalized). Requires **GPU-accelerated Isaac Gym** to evaluate reward candidates fast —
  a scale we do not have, but the *reflection-from-training-stats* loop is exactly our
  "difficulty metric → designer prompt" idea.
- **DrEureka** — *Language Model Guided Sim-to-Real Transfer*, Ma et al. arXiv **2406.01967**
  (2024). Builds on Eureka; also auto-generates **domain-randomization** ranges (RAPP). Out of
  scope (no sim-to-real here) but confirms LLMs can co-author reward + difficulty knobs.
- **Text2Reward** — *Reward Shaping with Language Models for RL*, Xie et al. arXiv **2309.11489**,
  **ICLR 2024 (Spotlight)**. Generates **interpretable dense reward code** from a NL goal over a
  compact env representation; on ManiSkill2/MetaWorld matches or beats expert rewards on **13/17**
  manipulation tasks, learns 6 novel MuJoCo locomotion skills (>94%). Precedent that
  *dense shaped reward as code* is learnable and safe when the goal stays fixed.
- **Motif** — *Intrinsic Motivation from AI Feedback*, Klissarov, D'Oro et al. arXiv
  **2310.00166**, **ICLR 2024**. LLM gives **preferences over pairs of captioned states**;
  a reward model is distilled from those preferences and used as an **intrinsic reward** for
  RL on NetHack — *without* the LLM ever touching the environment. Relevant if we ever want a
  learned dense signal beyond hand-declared checkpoints; not needed for v1 (our checkpoints
  already are the dense signal).

### Pattern (c) — Unsupervised Environment Design (the difficulty engine, no LLM)
- **PAIRED** — *Emergent Complexity and Zero-shot Transfer via UED*, Dennis, Jaques et al. arXiv
  **2012.02096**, **NeurIPS 2020**. Coins UED. An env-generating **adversary** maximizes
  **regret** = (antagonist return − protagonist return), yielding solvable-but-hard levels and
  strong zero-shot transfer. Regret is the principled "at the frontier of the student's ability"
  signal we want as a difficulty metric.
- **ACCEL** — *Evolving Curricula with Regret-Based Environment Design*, Parker-Holder, Jiang et
  al. arXiv **2203.01302**, **ICML 2022 (Spotlight)**. **Edits (mutates) previously high-regret
  levels** to keep producing levels at the frontier — an *evolutionary* curriculum. **The single
  most transferable idea for us: the LLM designer is a smarter replacement for ACCEL's random
  edit operator** — "make it harder near where the agent plateaued" is an LLM-guided ACCEL edit.
- **POET / Enhanced POET** — Wang, Lehman, Clune, Stanley. arXiv **1901.01753** (2019) /
  **2003.08536**, **ICML 2020**. Co-evolve a *population* of (environment, agent) pairs; solutions
  **transfer between environments** as stepping stones. Establishes minimal criteria
  (not-too-easy / not-too-hard) for admitting a new env — our G3' pass/plateau band mirrors this.
- **(Robust) PLR / DCD** — *Replay-Guided Adversarial Environment Design*, Jiang et al. arXiv
  **2110.02439**, **NeurIPS 2021** (generalizes *Prioritized Level Replay*, Jiang et al. ICML
  2021). Cheapest, most practical UED: **score already-seen levels by a regret proxy — positive
  value loss — and preferentially replay the high-regret ones**; freezing updates on uncurated
  levels gives a Nash robustness guarantee. **The regret proxy is computable for free from a PPO
  value head** → this is our concrete curriculum signal (§4).

### Pattern (d) — LLM-as-policy / skills, and when it loses
- **Voyager** — *An Open-Ended Embodied Agent with LLMs*, Wang et al. arXiv **2305.16291** (2023).
  GPT-4 as a lifelong Minecraft agent: automatic curriculum + an **ever-growing skill library of
  executable code** + iterative self-verification. Strength: zero training, composes skills,
  transfers to new worlds. Weakness for us: **one+ LLM call per decision**, so as a per-game
  *solvability certifier* run thousands of times it is economically hopeless (EnvGen quantifies
  the gap: thousands of LLM calls/episode vs 4 total). Voyager's *skill library* is, however, the
  right shape for the **designer** side (OBJECTIVES.md already anticipates "reusable skills may
  emerge") — not the player side.
- **MIRA framing** (General Intuition). MIRA is a **playable neural world model** trained by
  General Intuition + Kyutai on ~10,000 h of Rocket League bot play, running ~20 fps from key
  presses (generalintuition.com; TechCrunch 2025-10-16). It is a *learned generalist dynamics
  model*, not a per-task RL certifier. Read it as the far-future "one generalist agent/world-model
  across many envs" rung — relevant to the per-game-vs-generalist decision (§6), not to G3'.

---

## 2. Effectiveness & hobby-scale reproducibility

| Work | Demonstrated on | Agent/LLM budget | Hardware | Reproducible on 1 Windows PC? |
|------|-----------------|------------------|----------|-------------------------------|
| OMNI-EPIC | PyBullet robot tasks, >200 loop iters | per-task RL train + FM calls per iter | GPU + FM API | **Method yes, scale no** — clone the loop at small scale |
| EnvGen | Crafter (2D) | **~4 LLM calls total** + one small RL agent | modest (CPU/1 GPU) | **Yes** — closest budget analog |
| Eureka | 29 IsaacGym envs | GPU batch of reward candidates × PPO | **needs GPU (Isaac Gym)** | reward-reflection idea yes; scale no |
| Text2Reward | ManiSkill2/MetaWorld/MuJoCo | per-task SAC/PPO | 1 GPU typical | partial |
| Motif | NetHack | large-scale RL (billions of steps) | cluster | idea yes, scale no |
| PAIRED/ACCEL/PLR | MiniGrid, BipedalWalker, car-racing | PPO student, many env-steps | **1 GPU / strong CPU** | **Yes** — small discrete/continuous, CPU-runnable |
| POET | 2D BipedalWalker | large population, evolution | cluster | small pop yes; full no |
| Voyager | Minecraft | 1 GPT-4 call/decision | API only | yes but **$$ per episode** |

**Takeaways for a no-/one-GPU shop.** (i) The UED family (PLR/ACCEL/PAIRED) and EnvGen are the
CPU-friendly, small-net precedents — our 2-8-action, ~100-dim, ≤300-tick games are *far* smaller
than BipedalWalker, so **PPO on CPU is comfortable**. (ii) Eureka/Motif's headline numbers ride
on GPU-scale RL we won't match — we borrow their *loops* (reward reflection; preference→reward),
not their scale. (iii) The single biggest evidence-backed lever is EnvGen's asymmetry: **spend
LLM calls on design (rare), spend cheap RL steps on play (often)**.

---

## 3. The core reframing: existence-search vs learnability

- **G3 today (and the Go-Explore tree) answer "does ANY solution exist?"** — an *existence*
  certificate (a witness action sequence). This caps difficulty twice: (a) games are pushed
  *easier* until blind/tree search stumbles on the goal; (b) UNSOLVED just means "search failed",
  not "no learner could win".
- **G3' answers "can a POLICY LEARN to solve it?"** — a *learnability* certificate. A game whose
  Go-Explore tree saturates at milestone k but where PPO climbs to a high success rate is exactly
  the **"hard but learnable"** band POET/ACCEL/PLR chase — the games we currently cannot certify
  and therefore cannot generate. This is the direct answer to Elias's complexity wall.
- The two are **complementary, ordered**: existence-search is the cheap pre-filter; learnability
  is the expensive grader. Keep both.

---

## 4. Fit for OUR harness — RECOMMENDED architecture

**Givens** (CONTRACTS §1-2, OBJECTIVES): 2-8 game-declared discrete actions; decision tick =
`act()` + K=6 physics steps; **pure** state queries (≤14 bodies × pos/vel/angle/angular_vel/bbox);
1-6 **runner-latched checkpoints** = built-in dense milestone reward; `success` = binary unshaped
terminal certificate; deterministic seeded engines (pymunk + Planck), bit-exact & replayable;
batched Planck executor (thousands of episodes/min); lean Python harness (pymunk + PIL).

### 4.1 The recommendation, opinionated

- **Algorithm: PPO** (on-policy, clipped), small **MLP** (2×256), categorical head over the
  game's 2-8 actions. Rationale over DQN: robust to *per-game reward-scale variance* with almost
  no retuning (we cannot hyper-tune per game), stable across wildly heterogeneous games, and its
  **value head hands us the PLR regret proxy (positive value loss) for free**. State is fully
  observed (pure queries) ⇒ **no recurrence needed**; plain MLP suffices. DQN/Rainbow only if a
  sample-efficiency problem shows up in Phase 0.
- **Library: vendored CleanRL `ppo.py`** (single file), adapted — not imported. CleanRL is
  single-file/hackable (arXiv 2111.08819, **JMLR 2022**) and benchmarked ~2.3× faster than a
  Stable-Baselines3 PPO in at least one CPU comparison; we need to bolt in our reward shape, the
  vectorized Planck executor, and the **plateau/early-stop "declare UNSOLVABLE-BY-RL" logic**,
  which is far easier in one editable file. **Fallback: Stable-Baselines3** (JMLR 2021) if we want
  a battle-tested `PPO` with less code and accept the heavier dep + slower loop. Avoid a fully
  hand-rolled PPO (correctness risk not worth it).
- **Observation** (code-state, NOT pixels): flatten per body **relative to the controlled body** —
  (dx, dy, vx, vy, sin θ, cos θ, ω) — plus the controlled body's world-normalized absolute pose,
  the **latched-checkpoint one-hot**, and normalized tick; pad/mask to 14 bodies (~110 dims).
  Normalize by WORLD_SIZE and a typical-velocity scale. This is deliberately code-state, matching
  the challenge's "code-defined truth" framing; their pixel vision policy is theirs and unavailable.
- **Reward shape** (the OMNI-EPIC lesson, encoded): `r = +1 per newly-latched checkpoint`
  (latch-once, already runner-enforced) `+ B_success` on the terminal certificate
  `− B_fail` on `failure()` `− λ·1` small per-tick time cost. **`success` stays the unshaped
  binary certificate — the "solved?" decision never reads shaped reward** (hack-resistant by
  construction). Checkpoints only *accelerate learning*; they never *define* success.
- **Budget & the UNSOLVABLE-BY-RL rule.** Cap per game at **~2M env-steps ≈ 10-20 min CPU**
  (a 150-tick game at "thousands of episodes/min" from the batched executor ≈ 0.3-0.5M steps/min
  of pure sim; small-MLP PPO update is the marginal cost). Declare **UNSOLVABLE-BY-RL** iff no
  episode reached `success` within budget **AND** the success-rate curve is flat (slope ≈ 0 over
  the last 25%). Start at 2M in Phase 0, then **let the ledger tighten it** (harness philosophy:
  thresholds are `[eng.]` and data-arbitrated, cf. OBJECTIVES telemetry directive).
- **RL witness = determinism-preserving.** Training may be nondeterministic (torch/threads) — it
  is **offline**; only the *emitted* certificate must replay. The RL witness is a **greedy (argmax)
  rollout of the trained policy under a fixed world_seed → an action sequence + seed**, which the
  existing pipeline replays bit-exactly (STATE_TREE keys everything on the action prefix). Store
  `policy_weights_hash` for provenance. Thus RL slots into the determinism-first harness with **zero**
  change to replay/witness machinery.

### 4.2 The seam — G3' AFTER the tree prober

```
G0 static ─ G1 rollout/agency ─ G2 goal ─ G3 solvability(random → Go-Explore tree)
                                                     │ witness found?  (fast pre-filter, free)
                                          no ────────┴──────── yes
                                          │                     │
                                   UNSOLVED (as today)     ┌─── G3' RL-LEARNABILITY ───┐
                                                           │ vectorized in-proc Gym env  │
                                                           │ (wraps run_episode) + PPO   │
                                                           │ emits: learnable? (y/n),    │
                                                           │ difficulty metrics, RL      │
                                                           │ witness (greedy, seeded)    │
                                                           └── grade + ledger + G4 ──────┘
```

- **The tree prober stays the pre-filter.** RL runs **only if G3 found a witness** — never waste
  PPO on an unsolvable game, and the witness can **warm-start** RL (behaviour-clone the prefix, or
  use it as a reference rollout). This is the "cheap search before expensive learning" pattern
  (PLR pre-scores levels cheaply before investing).
- **Difficulty metrics G3' emits** (the curriculum signal, feeding designer prompts): steps-to-first-
  success, area-under the success-rate curve, final success rate, **plateau milestone** (where slope
  →0 with success<1), and the **PLR/ACCEL regret proxy = mean positive value loss**. These convert
  "solvable" into a graded **learnability score** and localize *where* difficulty lives — an
  ACCEL/GenEnv-style "make it harder between milestone k and k+1" prompt back to the LLM designer.

### 4.3 Two wrappers, not one — Gym for the probe, OpenEnv for the rung

- **Inner probe = a minimal in-process Gymnasium `Env`** around the *existing* `run_episode`
  (`reset`=World(seed)+build; `step`=act+6×world.step+on_step, reward=checkpoint latch deltas + terminal;
  `done`=success/failure/budget). ~1 day of work because it's a thin call into code that already
  exists; vectorize with `SyncVectorEnv` or the batched Planck executor. **This makes the games
  CleanRL/SB3-compatible for free.** Do **not** route the inner loop through OpenEnv — its
  FastAPI/WebSocket transport (OPENENV_ANALYSIS.md §2.2) is per-step HTTP overhead that would
  dominate a thousands-of-episodes probe.
- **OpenEnv is the OUTER rung** (already scoped, ~4-6 days, OPENENV_ANALYSIS.md §3): serve *certified*
  games as standard `reset/step/state` envs to external trainers (TRL GRPO), publish as HF Spaces.
  Its `StepResult.reward` is the natural home for our checkpoint signal, but that's for the
  training/eval rung, **not** the inner G3' certifier. **Reuse the single `run_episode`** for both
  wrappers (avoid the two-runner drift the OpenEnv note warns about). Net: HF OpenEnv gives us the
  downstream socket for free; it gives the inner probe nothing worth its transport cost.

---

## 5. Alignment with tech_challenge.md (quotes)

Coupling an RL player is not merely allowed — the brief's stated downstream uses *require* it:

- > "**Post-training environments.** A massive supply of diverse environments for training and
  > evaluating our vision-based policy on specific goals and rewards."
  Our RL player is precisely a *post-training consumer*; a harness that can certify **learnability**
  produces the "diverse environments... on specific goals and rewards" they name. Harder+varied is
  the point.
- > "Because environments are defined in code, you can encode verifiable objectives directly...
  > This is far more reliable than using a VLM on pixel output to check whether something happened."
  Our **unshaped `success` code certificate + code-state reward** is exactly this — and it is
  *stricter* than OMNI-EPIC's VLM/LLM success detector. **Do not** drift G3' toward a VLM judge; the
  code certificate is the moat.
- > "**Reward model training.** Generate many environments in code space, train a reward model on the
  > programmatic signals, and then apply that reward model to pixel-based observation."
  Strongest alignment: our **latched checkpoints ARE the programmatic signal** a reward model trains
  on (CONTRACTS §2 says so), and **the RL player is what generates the (state, programmatic-reward)
  trajectories** that reward model needs. Coupling RL directly manufactures their reward-model rung's
  training data.
- The vision policy is theirs and unavailable ⇒ anything we train is **ours and code-state-based**.
  The brief notes "models like Claude perform well at progressing through 2D environments on their
  own", but for a *per-game learnability certifier run thousands of times*, a cheap code-state RL
  policy (0 LLM calls at play time) beats LLM-as-policy on cost (EnvGen's asymmetry).

---

## 6. Risks & mitigations

1. **Reward hacking of shaped checkpoints.** A policy could farm an easy off-path milestone.
   *Mitigation, and yes it suffices:* the **certificate is the unshaped binary `success`** — the
   "solved" verdict never reads shaped reward; **latch-once** kills farming; G3 already requires
   *every checkpoint to fire at/before the success tick on the witness* (dead/off-path milestones →
   GOAL_ERROR) and flags firing-order mismatch; **G4 avoidance probes** catch degenerate/unavoidable
   goals. Shaped reward can only make learning *faster*, never make a *false* game pass.
2. **Non-determinism from a learned policy** in a determinism-first pipeline. *Mitigation:* the RL
   witness is a **greedy, fixed-seed rollout reduced to an action sequence** — replayed bit-exactly
   like the G3 witness (the pipeline keys on action prefixes). Training's nondeterminism is offline
   and irrelevant to the emitted certificate. Log `policy_hash + seed` for provenance.
3. **Training instability across wildly-varied games.** *Recommendation: per-game FRESH small agent
   is the default.* Games declare their **own** action semantics (2-8 arbitrary strings) ⇒ there is
   **no shared action space**, so a generalist would face heavy negative transfer; OMNI-EPIC likewise
   trains per-task. The **generalist agent / world-model across many envs (the MIRA framing)** is a
   *later research rung*: it needs a canonical observation/action encoding we don't yet have, and its
   payoff (cross-game transfer) is a bonus, not a certifier requirement. PPO's low retuning need is
   what makes per-game-fresh affordable. The early-stop rule (risk 1's budget) must be robust to games
   PPO simply cannot crack — **declare UNSOLVABLE-BY-RL and move on, never hang**.
4. **Compute creep.** Keep RL **off the hot path**: the free Go-Explore tree pre-filters; PPO runs
   only on games with a witness that we want graded. Per-game PPO is minutes on CPU at our tiny
   obs/action scale; **parallelize across games with processes** (pymunk `Space` is single-thread —
   OPENENV_ANALYSIS.md §3.4).

---

## 7. Phased plan

- **Phase 0 — Spike (~2-3 days).** In-process Gymnasium wrapper around `run_episode`; vendor+adapt
  CleanRL `ppo.py`; run on 5-10 already-certified games. **Deliverable: a calibration table** —
  (game, G3 witness ticks, PPO steps-to-first-success, final success rate, wall-clock). Validates the
  feature vector, reward shape, and the 2M-step / plateau budget. Kills the idea early if PPO can't
  clear even easy certified games on CPU in ~15 min.
- **Phase 1 — Integration as G3' (~3-5 days).** Add G3' as an **optional verifier layer after the
  Go-Explore tree**, gated on a G3 witness. Emit `learnable ∈ {yes,no}`, difficulty metrics
  (steps-to-solve, AUC, final success rate, plateau milestone, regret proxy), and the deterministic
  RL witness. Append all to `runs/ledger.jsonl` (extend the telemetry directive). Certified games gain
  a **learnability grade** alongside the existing verdict.
- **Phase 2 — Curriculum loop (~1-2 weeks).** Feed G3' difficulty metrics back into the modular
  designer prompts (`harness/gen/prompts/*.md`), ACCEL/GenEnv-style: *"agent plateaued at milestone k
  (success 30%) — deepen the challenge between k and k+1"* / *"solved in 20k steps — raise difficulty"*.
  **The LLM designer becomes ACCEL's edit operator, but smarter.** Optional stretch: the generalist-
  agent / world-model experiment (MIRA framing) once observation canonicalization is stable and
  cross-game transfer is worth measuring.

---

## 8. Sources (verified; arXiv IDs resolve)

- OMNI-EPIC — arXiv 2405.15568 (ICLR 2025): https://arxiv.org/abs/2405.15568
- EnvGen — arXiv 2403.12014 (COLM 2024): https://arxiv.org/abs/2403.12014
- GenEnv — arXiv 2512.19682 (2026 preprint, secondary-sourced): https://arxiv.org/abs/2512.19682
- Eureka — arXiv 2310.12931 (ICLR 2024): https://arxiv.org/abs/2310.12931
- DrEureka — arXiv 2406.01967 (2024): https://arxiv.org/abs/2406.01967
- Text2Reward — arXiv 2309.11489 (ICLR 2024 Spotlight): https://arxiv.org/abs/2309.11489
- Motif — arXiv 2310.00166 (ICLR 2024): https://arxiv.org/abs/2310.00166
- PAIRED — arXiv 2012.02096 (NeurIPS 2020): https://arxiv.org/abs/2012.02096
- ACCEL — arXiv 2203.01302 (ICML 2022): https://arxiv.org/abs/2203.01302
- POET — arXiv 1901.01753 (2019): https://arxiv.org/abs/1901.01753
- Enhanced POET — arXiv 2003.08536 (ICML 2020): https://arxiv.org/abs/2003.08536
- Replay-Guided Adversarial Env Design / Robust PLR — arXiv 2110.02439 (NeurIPS 2021): https://arxiv.org/abs/2110.02439
- Voyager — arXiv 2305.16291 (2023): https://arxiv.org/abs/2305.16291
- CleanRL — arXiv 2111.08819 (JMLR 2022): https://arxiv.org/abs/2111.08819
- Stable-Baselines3 — JMLR 22(268), 2021: https://jmlr.org/papers/v22/20-1364.html
- MIRA / General Intuition — https://www.generalintuition.com/ ; https://techcrunch.com/2025/10/16/general-intuition-lands-134m-seed-to-teach-agents-spatial-reasoning-using-video-game-clips/
- HF OpenEnv — https://github.com/huggingface/OpenEnv (analysis: notes/OPENENV_ANALYSIS.md)

> Unverified / flagged: a "GenPool" env-generation paper (named with a "?" in the brief) did **not**
> resolve to any real work — the nearest real systems are **GenEnv** (2512.19682) and **EnvPool**
> (arXiv 2206.10558, a parallel-execution engine, not LLM generation). Do not cite "GenPool". OMNI
> (OMNI-EPIC's predecessor) is referenced by name via OMNI-EPIC; the recent 2026 preprints
> (From Trainee to Trainer 2606.17682, SCALER 2601.04809) are listed for currency only.
