# Feasibility & literature grounding — inverse-V G4 attacker

> 2026-07-15. Commissioned by Elias. Sibling to INVERSE_VALUE_G4.md — is the design
> backed, what do we borrow, what's ours. Five web dives; every citation kept.

## 1. IS OUR DESIGN BACKED? — verdict: YES, a cheap COMPOSITION of well-cited parts

Not one named technique; four established lines fused. Each leg strongly grounded;
the *fusion* (reuse the trained PPO critic, inverted, as a zero-cost test-time
acting attacker to hunt softlocks) is what nobody has named.

- **Attacker that ACTS in the env (not pixel perturbation) to induce failure** —
  Gleave et al., *Adversarial Policies: Attacking Deep RL*, ICLR 2020
  (arXiv:1905.10615). THE precedent for our exact typed-state, act-in-env setting.
  Load-bearing: a *masked* victim (can't see adversary body) is far more robust →
  the attack works by pushing the victim OFF-DISTRIBUTION, not by force; and
  re-attacking a hardened victim finds a *new* adversary (arms race). Strongly
  grounds both our frame and the off-distribution failure mode. (BAIR blog:
  bair.berkeley.edu/blog/2020/03/27/attacks/)
- **Use the value to decide worst action / steer to a target low-value state** —
  "worst action = least-Q" is an explicit attack objective in the survey lit; our
  `argmin_a Q(s,a)` is its direct action-selection analog. Lin et al., *Tactics of
  Adversarial Attack on Deep RL*, IJCAI 2017 (arXiv:1703.06748): strategically-timed
  (attack only where the value/preference gap is large) + enchanting (steer toward a
  target state) are the two nearest cousins. Pinto et al., RARL, ICML 2017
  (arXiv:1703.02702): a learned adversary minimizes protagonist return — same
  min-value driver, but RARL *trains* an adversary; we reuse the critic (our delta).
  Formal backbone: Uesato et al., *Rigorous Agent Evaluation* / Adversarial Value
  Function, ICLR 2019 (arXiv:1812.01647) — "value function of an adversary rewarded
  1 for failure," reuses training data, found failures 3100x faster than random.
  Maps ~1:1 onto "reuse the PPO critic, inverted." Adaptive Stress Testing (Lee et
  al., JAIR) is the same family.
- **Exploiter that hunts weaknesses** — AlphaStar main/league exploiters (Vinyals
  et al., Nature 2019); Minimax Exploiter (arXiv:2311.17190); exploitability geometry
  (Czarnecki et al., *Real World Games Look Like Spinning Tops*, NeurIPS 2020,
  arXiv:2004.09468). These are population/train-time; ours is a cheap TEST-time
  greedy exploiter of one fixed critic.
- **"Stuck = cannot win" is a real, developer-confirmed bug class** — Wuji (Zheng
  et al., ASE 2019 distinguished paper; repo NeteaseFuxiRL/wuji) found 3 confirmed
  live-game bugs; *Using RL for Load Testing of Video Games* (arXiv:2201.06865)
  enumerates the STUCK oracle; curiosity playtesting (arXiv:2103.13798).

**Honest caveats (already right in our note):** (a) `argmin_a Q` drives BY
CONSTRUCTION into OOD regions where learned value is least trustworthy (Fujimoto
extrapolation error / BCQ, CQL) — so the critic only STEERS; soundness comes from
critic-independent DETECT+CONFIRM. A wrong critic costs search efficiency, not
validity. (b) FP risk: MDPFuzz replicability study (arXiv:2502.19116, ISSTA 2024)
found a coverage-guided fuzzer beaten by its own ablated random version → we MUST
A/B inverse-V vs random fuzz. STARLA (arXiv:2206.07813, TSE 2023) is the
falsification-oracle lineage our CONFIRM sits in.

## 2. THE TRAJECTORY-SAMPLING UPGRADE — "branch adversarially from states along the winning witness"

Verdict: STRONGLY grounded, and it's the natural next move. Decompose:

- **Return-then-branch** = Go-Explore (Ecoffet et al., Nature 2021, arXiv:2004.12919;
  earlier arXiv:1901.10995): archive promising states, RETURN (exact sim save/restore
  — our serve-based Godot envs satisfy the Phase-1 assumption), THEN branch. Failure
  modes: detachment, derailment. Our statetree/treesolve already IS a Go-Explore
  archive — adopt, don't build.
- **Seed from a known-good trajectory** = Backplay (Resnick et al. 2018,
  arXiv:1807.06919): sample the start from a window sliding backward along ONE good
  trajectory. Salimans & Chen, *Montezuma from a single demonstration* (2018,
  arXiv:1812.03381) — reset-to-demo-states with a backward curriculum, optimized
  with **PPO** (our exact G3' stack). RFCL (Tao et al., ICLR 2024, arXiv:2405.03379)
  generalizes to per-demo reverse curricula via state resets — cite if we branch
  from several certified wins. Reverse Curriculum Generation (Florensa et al., CoRL
  2017). Restart distribution as a first-class, VALUE-PRIORITIZED object: Tavakoli
  et al. 2018 (arXiv:1811.11298) — prioritize restart states by TD-error/value, which
  is exactly "prioritize trajectory seeds by low critic value."

**The only change we make: flip the objective SOLVE → BREAK.** Launching attacks
from states along the winning trajectory = probing whether the attacker can knock the
agent OFF the backward-reachable (success-reachable) set into a forward-only softlock.

**Implement on our stack:** treesolve already stores cells → trajectory-cell reset
is native. (1) Take the G3-certified winning {seed,actions}; replay through lockstep
frame-stepping, snapshotting statetree nodes per milestone/cell. (2) Prioritize seeds
by low critic value (Tavakoli prioritized restart), not uniformly — justified by
critical-state work: attacking <1% of high-leverage states cut agent perf >40%
(*Critical State Detection*, IEEE 2022, doc 9680180; *Policy Disruption in RL*,
arXiv:2507.18113, 2025). (3) Per seed run the inverse-V rollout (DETECT), then 1c
CONFIRM on survivors. Closest shipped system to our whole G4: Lu/Georgescu/Verwey,
*Go-Explore for 3D reachability testing*, EXAG@AIIDE 2022 (arXiv:2209.00570, IEEE
ToG) — cell = discretized position, RETURN = save/restore, reset ∝ 1/visits or
navmesh-distance — but explorer is RANDOM, oracle is coverage. Our deltas: critic
steering + freeze-detector + tree-refutation confirm.

## 3. SOLVABILITY / SOFTLOCK METHODS with a DETERMINISTIC verdict (like our tree-refutation)

Our CONFIRM (from frozen prefix P, exhaust continuations to len(P)+H, no
TERMINAL_SUCCESS ⇒ witness) IS a localized, search-based Bounded Model Check of
`EF(success)` from P (Biere/Clarke BMC, FMSD 2001). The whole-game property is the
peer-reviewed softlock definition `AG(EF goal)` — Mawhorter & Smith, *Softlock
Detection for Super Metroid with CTL*, FDG 2021 (adamsmith.as/papers/fdg21-softlock.pdf):
tile-based typed-state, softlock = reachable state where `EF(goal)` is false, verifier
failure yields a counterexample witness. Borrow:

- **Stuck-vs-lose separation** (resolves our min-Q subtlety): Cooper & Bazzaz, *Stuck
  in the Middle*, FDG 2025 (pcgworkshop.com/archive/cooper2025softlocks.pdf) — label
  each state FORWARD-reachable / BACKWARD-reachable / SINK (inevitable loss); softlock
  = forward ∧ ¬backward ∧ ¬sink ∧ ¬terminal. Adopt as our DEFINITION; our certified
  win seeds the backward-reachable set for free.
- **Sound-but-incomplete pruning** for a cheap attacker frontier + dossier facts:
  Sokoban frozen/corral patterns (sokobano.de wiki; PSPACE-complete, Culberson 1997);
  planning dead-end detection — Steinmetz & Hoffmann, *Clause-Learning / Trapper*,
  AAAI 2016 + online nogood learning (ICAPS 2017, AIJ) = a learnable dead-end
  classifier over our quantized typed-state features.
- **Upgrade witness → machine-checkable CERTIFICATE**: Eriksson, Röger & Helmert,
  *Unsolvability Certificates for Classical Planning* (AAAI) + *Certified Unsolvability
  with PDR* (ICAPS) — emit the closed explored-state set + inductive "no edge escapes
  toward success within H," verifiable without re-running search.
- **Exact backstop for DISCRETE GDScript games**: encode the deterministic typed-state
  relation as ASP (clingo / Smith & Mateas ASP4PCG, TCIAIG 2011; Sturgeon-MKIII
  co-generates level + certified playthrough) or NuSMV, check `EF(success)` directly —
  stronger than sampled Go-Explore. HARD LIMIT: needs a faithful finite encoding;
  NOT continuous physics — there replay-based bounded search is the honest tool.
  PuzzleScript BFS (vexorian/puzzlescript-bfs), Keke/Baba solvers
  (MasterMilkX/KekeCompetition) = discrete testbeds (heuristic — stress tests, not
  oracles).

**Soundness nuance to encode:** "no success within H" is sound-WITHIN-BOUND only. It
becomes a true completability refutation exactly when (a) the subtree SATURATES
(finite-state exhaustion — what our exact-prefix keying + quantized dedup make
attainable) or (b) H ≥ the BMC completeness threshold. Report CERTIFIED only under
saturation, HEURISTIC otherwise.

## 4. GODOT TOOLS / FUNCTIONS to ADOPT or MINE

- **GdUnit4** (godot-gdunit-labs/gdUnit4, MIT) — TOP ADOPT. `SceneRunner`:
  `simulate_frames(n)` (discrete tick advance, ignores time factor),
  `await_input_processed()` (input consumed before next assert), `set_time_factor()`.
  This is our deterministic rollout + witness-REPLAY harness. Headless CLI, JUnit XML.
- **GUT** (bitwes/Gut, MIT, v9.6.1) — mature generic assertion/headless alternative;
  weaker frame-stepping than SceneRunner. Reuse its JUnit-XML + exit-code CI pattern
  to emit the G4 verdict artifact.
- **AStarGrid2D / AStar2D** (Godot core) — ADOPT as a cheap deterministic reachability
  PRE-FILTER for grid/pathfinding games: `get_id_path()` returns EMPTY = unreachable
  (default `allow_partial_path=false`); `set_point_solid()` from the collision map.
  Guard known bugs (issues #86866, #113975). Proves only geometric reachability, NOT
  game-logic solvability → still needs the tree solver. NavigationServer `map_get_path`
  PROJECTS to nearest point instead of failing cleanly → weaker oracle, prefer AStar.
- **godot-mcp** (satelliteoflove/godot-mcp, MIT) — MINE AS DESIGN TEMPLATE (editor-
  attached, not headless). `godot_game_time` ("freeze clock, step exact slices,
  step-until condition, inputs riding inside the window") = near-exact spec for our
  DETECT loop; `godot_runtime_state` (typed-state-as-JSON) = our fingerprint read.
- **godot_rl_agents** (edbeeching/godot_rl_agents — we use it) — ROLLOUT RUNNER, not
  an oracle: `gdrl --eval --restore <ckpt>` + Sync-node "Speed Up 8" (lockstep, per
  physics step). No softlock module exists → confirms we build the oracle. Bridge:
  Beeching et al. (arXiv:2112.03636).
- **TITAN** (arXiv:2509.22170) — mine online stuck monitors: task-status stall-count +
  EXECUTION-TIME anomaly (action normally ~1s takes >10s), cheap signals to add beside
  our fingerprint-freeze trigger. cMarlTest (arXiv:2502.14606): report entity /
  entity-CONNECTION (button→door) / spatial coverage. **SKIP**: GodotTestDriver
  (C#-only), PlayGodot (custom build + pixel asserts).

**LOAD-BEARING determinism constraint** (Godot forum; snopekgames SG Physics 2D;
godot.rapier.rs; issue #24334): Godot float physics is deterministic play-to-play only
on the SAME build, and **`Engine.time_scale` BREAKS determinism**. So realize "speedup
8" and witness replay via LOCKSTEP frame-stepping (Sync-node / `simulate_frames`),
NEVER `time_scale`, and pin the Godot build in the reproducer provenance — else the
{seed,actions} witness won't reproduce.

## 5. WHAT'S NOVEL vs KNOWN (honest)

- **KNOWN / prior art (cite, don't claim):** critic/value-guided steering-to-failure
  (Uesato AVF, AST, RARL, Lin, Gleave); return-then-branch + trajectory-seed resets
  (Go-Explore, Backplay, Salimans-Chen, RFCL, Tavakoli); coverage/curiosity playtesting
  (EA SEED arXiv:2103.15819/2103.13798, CCPT arXiv:2202.10057); CTL/ASP/BMC solvability
  oracles; "stuck = cannot win" bug class (Wuji, load-testing taxonomy).
- **OUR SPECIFIC CONJUNCTION (defensible, and modest):** reuse the *same* trained PPO
  critic INVERTED as a zero-extra-cost, embarrassingly-parallel TEST-TIME acting
  attacker (vs a separately-trained adversary or observation perturbation — no cited
  work does this), launched from states along the CERTIFIED WINNING trajectory, gated
  by a critic-independent typed-state freeze/cycle DETECT and a SOUND bounded
  tree-refutation CONFIRM yielding a deterministic replayable witness. The field's own
  survey says "there is no oracle... made manually AFTER agents play" (Politowski et
  al., GAS@ICSE 2022, arXiv:2202.12777) — our heuristic-flag + reachability-refutation
  CERTIFICATE beats anything surveyed. Foreground CONFIRM; state the steering +
  trajectory-sampling as re-framings of prior art.

## 6. BUILD DELTA — how this refines INVERSE_VALUE_G4.md

1. **Add a trajectory-sampling seed source to Layer 1 (SEARCH).** Refine "many
   seeds/leaves" → PRIMARY seed set = states snapshotted along the G3-certified winning
   trajectory, prioritized by low critic value (Tavakoli restart, arXiv:1811.11298;
   Backplay window, arXiv:1807.06919; Salimans-Chen PPO reset, arXiv:1812.03381).
   Native on our treesolve cells; frame as "knock off the backward-reachable set."
2. **Cite grounding inline:** Gleave (1905.10615) for the frame, Uesato AVF (1812.01647)
   for the critic-inversion backbone + 3100x-vs-random, Go-Explore (2004.12919) for
   return-then-branch, Cooper & Bazzaz (FDG 2025) for the stuck-vs-sink DEFINITION.
3. **Add a REQUIRED A/B** to Honest limits: inverse-V steering vs random fuzz (MDPFuzz
   replicability, 2502.19116) — guided search has lost to ablated random; show ours wins.
4. **Tighten CONFIRM wording:** CERTIFIED only under subtree saturation or H ≥ BMC
   completeness threshold (Biere/Clarke; Mawhorter-Smith `AG(EF goal)`); HEURISTIC
   otherwise. Consider upgrading the witness to an Eriksson/Röger/Helmert certificate.
5. **Pin the harness:** run inverse-V rollouts AND the 1c replay through GdUnit4
   `SceneRunner.simulate_frames` / godot_rl Sync-node speedup — NEVER `Engine.time_scale`
   (issue #24334) — add the Godot build id to provenance, an AStarGrid2D `get_id_path`
   pre-filter for pathfinding games, and a TITAN execution-time monitor as 2nd DETECT.
