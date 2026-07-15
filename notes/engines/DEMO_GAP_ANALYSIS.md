# Demo gap analysis — our certified games vs godot_rl_agents_examples

> Synthesis lane, 2026-07-15. Joins the two audits (our 6 games incl. skeptic
> corrections; 5 reference demos incl. skeptic corrections) into ranked gaps,
> root causes, and concrete levers in OUR pipeline. Sources:
> `scenes/games/*/`, `tests/fixtures/gd_games/mini_collect_3d.gd`,
> `/home/enaha/GI/godot_rl_agents_examples/examples/{ScoreTheGoal,3DCarParking,ItemSortingCart,RobotFPS,HovercraftRacing}`.
> Companion notes: `notes/engines/FEEDBACK_LOOP.md` (taxonomy),
> `notes/adversarial/INVERSE_VALUE_G4.md` (softlock attacker),
> `notes/rl_agent/CURRICULUM_LOOP.md`.
>
> Framing: Elias's two new principles are the demo-quality lens.
> **ANTI-IDLING** needs games where inaction is distinguishable from progress
> (a clock or a hazard), otherwise refusal-vs-stuck can only be guessed
> harness-side. **POLICY-GUIDED DESCENT** needs games whose reachable space has
> structured low-V basins near the winning trajectory, not featureless drift.
> Most gaps below matter exactly because they starve one of these two.

---

## 1. Side-by-side on shared axes

Corrected numbers from both skeptic passes. "Ours" = 5 certified games +
`mini_collect_3d` fixture. "Demos" = the 5 godot_rl_agents examples.

| Axis | OUR games | REFERENCE demos | Verdict |
|---|---|---|---|
| **Dimensionality** | 2D x5; one nominal 3D with z locked to a plane (effectively 2D; both goals collinear → 1D task) | 3D x5 (`AIController3D` everywhere), incl. one deliberately 1-D task (ItemSortingCart) on a 3D stage | Gap in presentation more than task structure — demos also ship 1-D tasks, but stage them in 3D |
| **Proportion (world : controlled body, linear)** | 20-69x with the mechanic confined to slivers (blocker 80/520 px; pillars ~0.15% of field). Exception: drive_a_cart's annulus corridor is 4.4:1 — good ratio, zero content | 1:5 to 1:25 for task games; racer 1:50 but progress-dense (track ribbon IS the content) | Ours have dead space; theirs match arena to mechanic |
| **Content density** | ~15-140 nodes but mostly inert walls (drive: 128 wall segments, 0 obstacles); 1 mechanic region per game | ~30-60 nodes/env; density from one .glb map + a few dynamic bodies; decorations explicitly stripped in training mode | Similar node budgets; theirs spend the budget on interactive matter |
| **Mechanics depth** | One mechanic each; moving obstacles in 2/6; free-body physics in 1 (push, but ball is lighter than pusher); the one "timing" game (cross) has provably unreachable car hits — 5x dash_up wins every seed | Kick-into-matched-goal, precision parking among randomized obstacles, catch-then-deliver loops, FFA shooting w/ hp + spawn protection, racing w/ powerups + opponent | Theirs compose 2-3 interacting mechanics; ours average ~0.8 real mechanics |
| **Controller style** | 4 discrete actions always, no no-op; kinematic teleports (cross: 1-tick 6000 px/s), per-tick velocity overrides (drive), sticky force with no neutral (push); impulses done right in fly/knock/mini | Physics-first everywhere: RigidBody3D force/torque or VehicleBody3D engine_force; 2-continuous or multi-branch discrete (RobotFPS 3/3/3/2); rate-limited steering | Ours fight the engine in 3/6; theirs let the integrator be the dynamics |
| **Episode design** | No in-game clock except knock (600 steps); episode end = harness truncation with no semantics | Task games hard-timeout (350/1750/5000; penalty only in ScoreTheGoal); competitive games explicitly disable timeout and end on death/lap. Progress-ratchet rewards make idling worthless by construction | Theirs choose an episode philosophy per genre; ours default to "run until the harness gives up" |
| **Randomization** | drive: rng seeded, never drawn — byte-identical across seeds. Others: obstacle jitter only; start+goal always fixed | Spawn/goal/category re-rolled per episode; parking re-rolls obstacle COUNT + occupancy (quasi-layout); geometry static in all 5; racer even pins its RNG (seed=10) for reproducibility | Both keep geometry static — but theirs re-roll the task instance, ours re-roll decoration |
| **Failure conditions** | 4/6 effectively unfailable (push and mini return false unconditionally; drive's OOB is walled off; cross's hit detector is unreachable on the dash lattice). knock has a success/failure race at the goal line | Task games: rich, penalized failure sets (parking: −6 for OOB / grass / wall / car / flip / retreat-after-approach). Competitive: death is the episode | The single largest gap; see Gap 1 |
| **Visuals** | Primitive shapes, flat colors | .glb maps, HSV-randomized agent colors, particles, reactive materials — all cosmetic, stripped for training | Real but cheap gap; matters for GIFs only (see §4) |

Also relevant, not an axis of the prompt: demos normalize observations
(playing-area exports, ray-length, or hardcoded constants) and 4/5 use raycast
sensors; our `state()` is privileged typed-state by design. See §4.

---

## 2. Gaps ranked by demo-impact, with honest root causes

**Gap 1 — Idling is free: no in-game time pressure, no reachable failure (4/6).**
Demo impact: highest. Episodes read as drifting; the softlock attacker's DETECT
space is flooded with trivially non-terminal states; ANTI-IDLING cannot be
grounded because corner-and-wait is behaviorally identical to slow progress.
Root cause: **contract + missing feedback signal, then generation bias.** The
GameAPI contract makes `is_failure()` syntactically mandatory but semantically
optional — nothing in G0-G3 ever exercises it, so `return false` certifies
clean. Every gate rewards solvability; no signal ever rewards lethality, so the
generator converges on maximally safe worlds. (The truncation plumbing already
exists — `statetree.run_batch/_replay` take `max_ticks` — what's missing is
in-game semantics: refusal vs stuck.)

**Gap 2 — Failure detectors that exist are broken.** cross's car hits are
UNREACHABLE (dash lattice y-residues {40,45,55} never within 30 px of lane
centers; cars never change y) — the set's one claimed timing mechanic is
decoration. knock has a success/failure race (`x > ARENA_RIGHT` vs
`x > ARENA_RIGHT-20`; harness eval order decides). Demo impact: high — a
"timing" GIF with no timing is a false demo, and G4's competent critic wastes
budget probing a failure that cannot fire.
Root cause: **missing G-gate.** We certify success reachability (G0.5 flood,
G3 witness) and never certify failure reachability. This is a verification
blind spot, not a generation flaw — the LLM wrote a plausible detector; nothing
checked it can fire.

**Gap 3 — Dead space / flat value landscape.** 20-69x ratios with the mechanic
confined to slivers. Demo impact: high — most frames of any rollout show
featureless drift, and POLICY-GUIDED DESCENT has no structured low-V basins to
descend into (geometric substrate confirmed; the RL consequence is inference).
drive_a_cart is the separate sub-case: good corridor proportion, zero content —
a long empty hallway, not a big empty field.
Root cause: **generation bias, unmeasured.** The LLM defaults to round-number
windows (800x600) and small bodies; no advisory speaks to proportion; no
feedback signal measures what fraction of reachable space the mechanic touches.

**Gap 4 — One mechanic per game, no composition, no mid-episode state change.**
Demo impact: high for "depth" perception — demos read as games (catch THEN
deliver, park AMONG obstacles, shoot WHILE dodging); ours read as unit tests.
Root cause: **generation bias + skills.** The DESIGN block asks for "the ONE
idea you committed to" — literally singular. No skill routes composition
patterns; the parts bank retrieves bodies, not mechanic pairings.

**Gap 5 — Randomization is decorative; one game ignores its seed entirely.**
Demo impact: medium-high — demos across seeds look identical, and any RL result
smells of memorization. Root cause: **missing feedback signal first** (a
seed-differential check is trivial and absent — drive's dead rng would have
been caught by diffing two builds), **contract second** (the seed's obligations
are never stated: nothing says what MUST vary).

**Gap 6 — Checkpoints don't gate the intended skill.** `passed_pillars` at
x>180 with pillars at x 250-350; blocker bypassable through the open 440 px;
drive gates wider than the track. Demo impact: medium — but pipeline impact is
outsized: Backplay witness-prefix seeding and the G3' latch-curve directives
both assume a checkpoint certifies the mechanic was exercised.
Root cause: **contract blind spot.** Checkpoints are certified reachable and
ordered, never NECESSARY — no gate asks "can you latch k while never touching
the mechanic k names?"

**Gap 7 — Controller idiom leaks.** Kinematic teleports, per-tick velocity
overrides on a RigidBody, sticky force with no neutral action, uniformly 4
actions. Demo impact: medium — motion looks quantized/robotic in GIFs, and
physics interactions (the one thing Godot gives us for free) are suppressed.
Root cause: **skills + contract shape.** No controller-idiom knowledge is
routed at generation; the contract's `actions()` example vocabulary nudges
toward exactly-4 discrete verbs with no no-op.

**Gap 8 — Presentation (3D staging, visual identity).** Lowest rank for the
thesis, real for demos. Root cause: **presentation, honestly.** Our lane is
2D-first by choice; demos stage even 1-D tasks in 3D with one .glb and colors.
Partially chase (staging, color semantics), mostly defer (see §4).

---

## 3. Concrete levers, per gap

Anti-anchoring discipline applies to every LLM-facing surface: principles and
LLM-routed menus only — no hardcoded thresholds, sizes, step counts, or fixed
taxonomies in prompt text. Harness-side code may hardcode whatever it wants.

**Gap 1 (idling free / no failure):**
- *Generation advisory line* (LLM-facing, principle): in the system prompt
  built by `harness/gen/gamegen.py` (the contract + `_SKILL_ADVISORY_HEADER`
  block, backing text in `harness/gen/prompts/api_gdscript.md`): "A game where
  doing nothing forever is indistinguishable from playing is not a game. Give
  every game a source of pressure — something that ends or punishes a stalled
  episode from INSIDE the game — and make `is_failure()` a condition a real
  player could actually trigger." No numbers, no mechanism list.
- *DESIGN block field* (LLM-facing, menu-shaped): add one line to
  `harness/gen/prompts/design_block_gdscript.md`: `Pressure: <what punishes or
  ends inaction, and how the game can be lost>`. Forces the commitment at
  design time; the LLM chooses the mechanism.
- *Feedback taxonomy row* (harness-side): `notes/engines/FEEDBACK_LOOP.md`
  table + `harness/gen/feedback.py`: outcome "no failure reachable /
  `is_failure` constant-false" → directive "this game cannot be lost and has no
  internal pressure; a stalled agent is invisible. Add a losable condition or
  internal clock — minimal edit against current code." Facts, not vibes.
- *Harness semantics* (code): give `statetree` truncation the refusal-vs-stuck
  distinction the stale-SEEKER needs — a truncated episode whose fingerprint
  was frozen is "refusal", one still moving is "slow". This is the anti-idling
  decay's ground truth and lives entirely harness-side (`harness/rl/adversary.py`).

**Gap 2 (failure detectors broken):**
- *New G-gate* (harness-side, the load-bearing lever): a **failure-reachability
  probe** in `harness/verify/` (natural home: extend `harness/verify/g4.py`,
  which already drives adversarial rollouts, or a sibling of the G0.5 flood in
  `reachability.py`): certification requires a *failure witness* — a replayable
  action sequence from spawn with `is_failure() == true` — exactly dual to the
  success witness. Games whose failure is unreachable (cross) or unfailable
  (push) fail the gate and re-enter via `_revise_user_msg`. cross's lattice
  bug is undetectable by reading code plausibility; only a witness catches it.
- *G-gate ordering check* (harness-side, cheap, static+dynamic): success and
  failure predicates evaluated on the same tick must have a declared winner;
  the knock race becomes a G0-family fact check in `harness/verify/gd_gate.py`.

**Gap 3 (dead space):**
- *Feedback signal* (harness-side): the G0.5 occupancy flood in
  `harness/verify/reachability.py` already enumerates reachable cells. Compute
  **mechanic coverage**: fraction of reachable cells within interaction range
  of any dynamic/failure/checkpoint-relevant body. Report the measured number
  as a fact in the compiled directive (`harness/gen/feedback.py`); the
  threshold that triggers the directive lives harness-side, never in the prompt.
- *Generation advisory line* (LLM-facing, principle): "Size the world to the
  mechanic: a player should be near something that matters for most of the
  episode. Empty space the mechanic never touches is a bug, not atmosphere."
- *Curriculum tie-in*: `harness/gen/curriculum.py` `difficulty_profile` grades
  can consume mechanic-coverage as a feature — a game solved fast with tiny
  coverage is "trivially sparse", a distinct grade from "too hard".

**Gap 4 (no composition):**
- *DESIGN block* (LLM-facing): change the singular framing in
  `design_block_gdscript.md` — `Mechanic twist:` becomes `Mechanics: <the core
  mechanic, plus how a second element interacts with or interferes with it>`.
  Principle-phrased; no taxonomy of allowed mechanics.
- *Skill routing* (LLM-routed menu — allowed): route a composition/pacing
  domain skill through `harness/gen/skill_context.py` `render_skill_context`
  on fresh-generation turns; candidates in the mined pack surveyed in
  `notes/engines/CLAUDE_GAMEGEN_SKILLS.md`. The skill layer selects on prompt
  text, so this stays a menu, not an anchor.
- *Parts bank* (`harness/gen/retrieval.py`, `CONTRACTS.md §9`): admit certified
  mechanic PAIRINGS (moving-hazard + push-body; timer + ordered-gates) as
  retrievable parts, not just bodies. Retrieval is deterministic and
  advisory — consistent with the existing "menu is advisory" stance
  (`gamegen.py` line ~965).

**Gap 5 (randomization):**
- *New G-gate, trivial* (harness-side): **seed-differential check** in
  `harness/verify/gameverify.py`: build the game at two seeds, diff `state()`
  at t=0 and after a short scripted rollout; byte-identical ⇒ fail with a
  facts directive ("`rng` is seeded but never drawn; every seed produces this
  identical layout"). Catches drive_a_cart's dead rng in milliseconds.
- *Contract principle* (LLM-facing, in `api_gdscript.md` / `CONTRACTS.md`):
  "The seed is a promise: two seeds must produce visibly different episodes —
  vary what the player must ADAPT to (starts, goals, hazard placement), not
  only decoration." Note the demos' honest lesson: geometry can stay static;
  it's the task instance that must re-roll.
- *G3' cross-seed eval* (harness-side): the SB3 lane
  (`harness/rl/sb3_trainer.py`) evaluates trained policies on held-out seeds;
  a train/eval success gap becomes a curriculum fact (memorization signal).

**Gap 6 (checkpoints don't gate):**
- *New G4 taxonomy row* (harness-side): alongside the existing
  `single_action_win` and shortcut-with-broken-gating rows in
  `notes/engines/FEEDBACK_LOOP.md`, add **checkpoint-vacuity**: tree-solve
  (`harness/verify/treesolve.py`) for a path that latches checkpoint k while
  provably never entering the mechanic region k names (region derived from the
  game's own bodies, not hardcoded). Hit ⇒ directive "checkpoint '<key>'
  certifies nothing: latchable at <state> without touching <bodies>."
  push_a_heavy's `passed_pillars` (fires 70 px before the pillars) is the
  canonical catch. This directly repairs Backplay prefix quality for
  `harness/rl/adversary.py`.
- *Generation advisory line* (LLM-facing, principle): "A checkpoint is a claim
  that a skill was demonstrated. Place it where only the intended play can
  reach it, not merely along the way."

**Gap 7 (controller idioms):**
- *Skill routing* (LLM-routed): a Godot physics-controller idiom skill
  (force/impulse-first, engine-integrated motion, rate-limited steering) via
  `render_skill_context` — the demos' concrete patterns
  (`apply_central_force`+torque, `engine_force`/steering, move_toward rate
  limits) belong in skill text as *reference knowledge (advisory)*, which is
  exactly what the `_SKILL_ADVISORY_HEADER` framing licenses.
- *Contract principle* (LLM-facing): "Let the engine integrate motion: prefer
  forces and impulses over writing velocity or position each tick. Include a
  way to do nothing." Phrased as principle; no fixed action-count, no imposed
  action taxonomy.

**Gap 8 (presentation):** cosmetic-only pass, cheapest slice: color semantics
(category = color, demo convention) and the existing asset lane
(`notes/engines/ASSET_BANK.md`, `notes/engines/DEMO_CAPTURE_LANE.md`). No
generation-surface change; explicitly deprioritized below.

---

## 4. What NOT to chase

- **Pixel/camera observations.** The demos themselves use none. Our privileged
  typed `state()` is what makes tree-solving, fingerprinting, and witness
  replay possible. Raycast sensors are a sim-to-deploy concern we don't have.
- **Continuous action spaces.** VehicleBody steering is pretty, but the
  tree-solver, replayable witnesses, and the softlock CONFIRM stage all live
  on discrete, enumerable actions. Multi-branch discrete (RobotFPS-style) is
  the ceiling worth considering — full continuous control breaks the
  certification substrate for zero thesis gain.
- **Hand-shaped in-game rewards.** Demos embed tuned shaping (ratchets,
  progress deltas, −6 uniform penalties) in game code. Our reward surface is
  harness-side, derived from certified checkpoints (G3' latch rewards). Import
  the *anti-idling philosophy* (Gap 1 levers), never the tuned constants —
  copying reward numbers into LLM-written games is precisely the anchoring
  we forbid, and un-certifiable to boot.
- **Multi-agent / self-play FFA.** RobotFPS's death-driven episode design is
  elegant, but multi-agent breaks single-file GameAPI semantics, witness
  determinism, and the G3 solver. A different research program.
- **Imported .glb art, shaders, particles.** The demos strip decorations in
  training mode themselves — they know it's demo dressing. Cosmetic lane only.
- **Timeout-disabled competitive episode design.** Only coherent when death is
  ubiquitous; chasing it before Gap 1 is closed inverts the dependency.
- **Full 3D as a blanket goal.** Demos are 3D because godot_rl targets 3D
  robotics-ish tasks. Our G0.5 flood/bounds logic is dimension-aware already
  (FEEDBACK_LOOP.md); one 3D wave is worth a probe (Wave 3), but 3D-everywhere
  multiplies verify cost while the ranked gaps are dimension-independent.

---

## 5. Three-wave experiment to close the top gaps

Fixed panel across all waves: N=10 fresh prompts + the 5 existing games
revised via `_revise_user_msg`; every game through G0→G3, G4 attack, G3' RL
probe; softlock attacker A/B (competent vs weak critic) per
`notes/adversarial/INVERSE_VALUE_G4.md` conventions. Acceptance thresholds
below are experiment-doc numbers — they live here and in harness code, never
on generation surfaces.

**Wave 1 — PRESSURE (Gaps 1+2).** Ship: advisory principle + `Pressure:`
DESIGN field + failure-reachability gate (failure witness required) +
success/failure race check + `unfailable` feedback row.
Accept: (a) ≥8/10 new games certify WITH a replayable failure witness;
(b) 0 games with constant-false `is_failure` pass certification; (c) on the
revised set, softlock DETECT candidates refuted at CONFIRM drop ≥30% (less
trivially-non-terminal space) while certified softlocks per 1k ticks do not
drop; (d) the stale-SEEKER's refusal-vs-stuck labels become computable in-game
for every certified title.

**Wave 2 — SPACE & SEED (Gaps 3+5).** Ship: mechanic-coverage fact from the
G0.5 flood + proportion advisory + seed-differential gate + seed-promise
contract line + G3' held-out-seed eval.
Accept: (a) median mechanic-coverage of new certifications ≥3x the current
set's measured baseline (baseline measured first, in the same run); (b) 100%
of certified games show state-trajectory divergence across seeds (drive-class
dead rng extinct); (c) PPO trained on 8 seeds retains ≥70% of its success rate
on 8 held-out seeds; (d) qualitative: 3-seed demo GIF strips are visibly
distinct (capture lane, `notes/engines/DEMO_CAPTURE_LANE.md`).

**Wave 3 — DEPTH & DESCENT (Gaps 4+6+7, and the thesis payoff).** Ship:
composition DESIGN framing + mechanic-pairing parts + controller-idiom skill +
checkpoint-vacuity probe; pilot 1-2 games in 3D staging.
Accept: (a) checkpoint-vacuity rate on new certifications = 0 (probe passes);
(b) ≥6/10 new games certify with ≥2 interacting mechanics per their DESIGN
block, confirmed by the vacuity probe actually requiring both; (c) witness
lengths spread beyond the current 45-294 band with nonzero cross-seed
variance; (d) the payoff measurement: POLICY-GUIDED DESCENT A/B on Wave-3
games vs current games — certified softlocks per 1k ticks and
SEARCH-to-DETECT efficiency for the competent critic improve on the new set
(hypothesis: structured low-V basins exist to descend into; if this fails,
Gap 3's RL-consequence inference was wrong and we say so).

Sequencing rationale: Wave 1 first because every later measurement (idling
semantics, softlock search efficiency, episode pacing) is confounded while
4/6 games cannot fail; Wave 2 before Wave 3 because coverage and seed facts
are the instruments Wave 3's acceptance reads out.
