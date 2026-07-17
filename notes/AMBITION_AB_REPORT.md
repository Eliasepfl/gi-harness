# Ambition A/B Report — old-hints/angle-bug/no-CoT  vs  reachable-not-shallower/angle-fixed/CoT

**Analyst pass, 2026-07-16.** Read-only over the two experiment dirs + repo. Model under test:
`tencent/hy3:free` (Tencent Hunyuan-3, free tier) for **both** waves — so every delta below is
hint/harness, not model. All 16 runs `integrity=ok` (no tripwire).

- BASELINE = `/orcd/scratch/orcd/008/enaha/gi/ambition_probe/` (old laundering hints, angle bug live, no CoT)
- CONDITION B = `/orcd/scratch/orcd/008/enaha/gi/ambition_ab/` (reachable-not-shallower hints, angle fixed, CoT in `a<N>.trace.json`)
- Only sbatch delta: B sets `OPENROUTER_REASONING_MAX_TOKENS=12000` (captures CoT). Caps / angle-fix /
  stall-guard / hint wording live in the harness. Observed attempt ceilings: **baseline ≤5, B ≤9**;
  B stall-guard halts a run after "the same defect survived 4 attempts unchanged".

---

## EXECUTIVE SUMMARY

1. **Certified count is unchanged (2/8 → 2/8) but the *composition* changed.** Baseline certified
   heist + siege. B certified **heist + submarine**. So B **gained submarine** (the angle-fix win) and
   **lost siege** (regression, investigated below).
2. **The angle fix worked.** Baseline killed all three 3-D games with `gd_protocol`/VERIFY_ERROR
   (verification-infra death) regardless of content. B has **zero** `gd_protocol` errors: submarine
   certifies, cargo/stack now fail on *real* physics (bodies out of bounds), i.e. they reach genuine
   verification. 3-D is no longer infra-killed.
3. **The preserve-clause worked at the reasoning level.** Under the new hints, mechanics **survive
   repair — and frequently grow** (siege wall-refs 29→58, balance cables 2→15, stack blocks 22→27).
   **Zero repair-time demolition cases**, versus the baseline heist which shed timer/doors when told to
   "make the first stage easier". The smoking gun: heist B a6 explicitly *rejects* a shallowing fix,
   reasoning "*That collapses to single repeated verb 'e' - not allowed (collapsing multi-stage into
   single repeated verb worse)*" — a direct echo of the injected clause. **Laundering-via-repair is gone.**
4. **Still 2/8, because two *different* bottlenecks now dominate:**
   - **GDScript dialect / strict-parser friction** eats first attempts across the board (5/8 B games die
     att1 on type-inference / wrong-class parse errors). The raised caps largely funded *retry churn*,
     not deeper design.
   - **The "stuck one step short of success" solvability wall.** balance (359/360 reached the penultimate
     milestone), herd (216/216), fluid, siege all end with the penultimate milestone fully reached and
     `success` never — sometimes *because* ambition grew faster than the model could tune the physics.
5. **The contract is NOT the initial-ambition bottleneck.** Every a1 engages the HARD RULES richly
   (STAKES 8/8, MATERIAL REALITY 8/8, determinism/rng 8/8, seven-methods 7/8, PhysicsServer3D in all 3
   3-D games) and produces distinct, ambitious designs. The one directive that **never** fires is
   DIVERSITY / "least-obvious reading" (**0/8**) — but it isn't the blocker: the 8 prompts are already
   distinct and the model reads them literally-but-richly. The only genuine construction-bias is that
   **compound** mechanics get flattened (heist "dwell-timer alarm" → plain global clock; fluid "pipe
   segments" → one pipe).

---

## PART 1 — QUANTITATIVE A/B TABLE

### 1.1 Verdicts + attempts (attempts is a first-class column)

| # | prompt | BASELINE verdict | att | B verdict | att | movement |
|---|--------|------------------|-----|-----------|-----|----------|
| 1 | cargo3D  | VERIFY_ERROR (gd_protocol/angle) | 4 | ENV_ERROR (stall) | 7 | infra-death → real physics failure |
| 2 | heist2D  | **COMPLETED** (188t) | 3 | **COMPLETED** (156t) | 6 | certified both; B a1 shallower start |
| 3 | balance2D| ENV_ERROR | 5 | UNSOLVED | 9 | never-ran → 359/360 reach last step |
| 4 | herd2D   | ENV_ERROR | 5 | UNSOLVED | 6 | never-ran → first milestone reachable, stuck |
| 5 | stack3D  | VERIFY_ERROR (gd_protocol/angle) | 2 | ENV_ERROR (stall) | 9 | infra-death → real physics failure |
| 6 | fluid2D  | UNSOLVED | 5 | UNSOLVED | 8 | stuck at last step both; B milestones 3→2 |
| 7 | subm3D   | VERIFY_ERROR (gd_protocol/angle) | 1 | **COMPLETED** (191t) | 2 | **infra-death → certified (the angle-fix win)** |
| 8 | siege2D  | **COMPLETED** (108t) | 2 | **GOAL_ERROR** | 9 | **certified → regression (oscillation)** |

Certified: baseline {heist, siege}; B {heist, submarine}. B reaches the solvability/goal gate in
**7/8** runs (all but the two ENV physics deaths) vs baseline ~3/8 — deeper chains, real verification.

### 1.2 Ambition metrics per attempt (parsed from each `a<N>.gd`)

Columns: `loc / actions / checkpoint-latch-keys / add_child / joints / timer-refs`.
Note two metric caveats: (a) **checkpoint-latch-keys** counts a `{"x":false,...}` latch dict — some games
track milestones differently, so the authoritative milestone count is the verifier's `reach_counts`
(given in 1.3); (b) **timer-refs** counts `timer|dwell|alarm|time_left|…` tokens.

```
#1 cargo3D
 BASE a1 251/7/2/16/1/0   a2 238/7/2/16/1/0   a3 238/7/2/16/1/0   a4 245/7/2/16/1/0
 B    a1 219/7/3/14/3/0   a2 207/7/3/12/0/0 … a7 235/7/3/14/0/0        (joints 3→0: FixedJoint3D purge)
#2 heist2D
 BASE a1 274/4/5/14/0/10  a2 217/4/5/14/0/8   a3 216/4/5/14/0/8        (baseline SHEDS: door19→14, timer13→11)
 B    a1 148/4/1/17/0/6 … a6 138/4/1/17/0/6                            (flat: door15→14, timer 6→6 — but timer is a GLOBAL clock)
#3 balance2D
 BASE a1 206/6/5/13/2/0 … a5 237/6/5/19/2/0
 B    a1 153/6/3/9/2/0 … a5 166/6/3/11/2/0  a6 159/6/3/10/0/0 … a9 161/6/3/10/0/0  (springs→manual forces; 3 cables preserved)
#4 herd2D
 BASE a1 204/4/2/8/0/0 … a5 223/4/2/10/0/0
 B    a1 228/4/3/10/0/0 … a6 231/4/3/12/0/0                            (wolf/sheep/pen preserved)
#5 stack3D
 BASE a1 155/3/1/4/0/0  a2 146/3/1/4/0/0
 B    a1 143/3/1/5/0/0 … a7 210/3/1/10/0/0  a8/a9 221/3/1/10/0/0        (grew 143→221 loc, addc 5→10)
#6 fluid2D
 BASE a1 170/4/1/10/0/0 … a5 176/4/1/10/0/0                            (4 actions = TWO baffles a,b)
 B    a1 144/2/0/14/0/0 … a8 185/2/0/20/0/0                            (2 actions = ONE pipe — shallower construction)
#7 subm3D   (B: a1/a2 = CERTIFIED strafe design; a3-a5 = ORPHANS from an overwritten earlier run, see note)
 BASE a1 142/4/0/8/0/0
 B    a1 153/4/3/8/0/0  a2 145/4/3/8/0/0    [orphan a3-a5 166/2/1/10/0/0]
#8 siege2D
 BASE a1 148/5/2/12/0/0  a2 169/5/2/16/0/0                            (0 joints; simple; certified)
 B    a1 174/5/2/13/1/0  a2 208/5/2/19/1/0 … a4-a9 206-210/5/2/19/2/0  (PinJoint2D + 4-block toppling wall — MORE ambitious, never certified)
```

**subm3D orphan note.** subm B's dir holds 5 `a<N>` files but `gen_7.json` records only 2 attempts
(COMPLETED att2, 191t). File mtimes prove two runs: a3/a4/a5 (23:11–23:13, a 2-action *ballast/vent*
"depth-marker" design) were written *before* a1/a2 (23:19–23:20, a 4-action *strafe* design). A second
run overwrote a1/a2 and certified at att2; a3-a5 are leftovers of the earlier design. Authoritative B
result = **COMPLETED att2**. (All 5 traces are read; a3-a5 belong to the abandoned chain.)

### 1.3 Where B progressed / where it did not (called honestly)

**Progressed:**
- **3-D no longer infra-killed** (headline). Baseline: cargo/stack/subm = 100% `gd_protocol` death. B:
  submarine certified; cargo/stack fail on real physics; zero `gd_protocol`.
- **Deeper, real chains.** B reaches the solvability/goal gate in 7/8 (vs ~3/8). balance/herd/fluid/siege
  all obtain a *working deterministic sim* and milestone-reachability analysis that baseline never got to.
- **Tantalizingly close.** balance 359/360 episodes reach the penultimate `past_right`; siege 528/528
  reach `fired`; herd/fluid 216/216 reach their first milestone. The last step is the wall.

**Did not:**
- **Still 2/8 certified.**
- **siege regression** (baseline certified att2 → B GOAL_ERROR att9). Details in Part 3.
- **The raised caps didn't convert to certifications.** They funded (a) repeated *identical* ENV_ERRORs
  until the stall-guard fired (cargo att4-7 & stack att6-9 both = same "body out of bounds"; herd att3-6
  identical) and (b) futile last-mile tweaks (fluid 8, siege oscillating trivial↔unsolvable).
- **fluid milestones dropped 3→2** and actions 4→2: a *shallower construction*, not a repair-laundering.

---

## PART 2 — LAUNDERING CHECK UNDER THE NEW HINTS

Method: per run, per attempt, count domain-mechanic vocabulary and watch whether named mechanics
**survive** revisions (the baseline heist confirmed-laundering shape: doors 19→14, sight-cone 17→12,
timer 13→11 all shed in the a1→a2 repair that "made the first stage easier").

Mechanic-vocab survival, a1 → final attempt (token counts):

| run | mechanic tokens (a1 → final) | verdict |
|-----|------------------------------|---------|
| cargo B  | crate 42→43, barge 38→36, magnet 33, counterweight 11→11, weight-order 51→53; **joint 12→0** | joint drop = legit fix (FixedJoint3D doesn't exist), not laundering |
| heist B  | door 15→14, key 14→13, guard 26→24, sight 16→14, vault 12→11, timer 6→6 | **flat — mechanics preserved** |
| balance B| cable 2→15, tension 12, 3-cable tokens 13→14, roller 24→30 | preserved & enriched |
| herd B   | sheep 17→16, wolf 28→26, pen 36→35, dog 28→24 | preserved |
| stack B  | block 22→27, crane 7, wind 6, release 9 | preserved |
| fluid B  | pipe 18→27, water 28→53, tank 24→23, source 23→22, fill 12 | preserved (but started shallow) |
| subm B   | ballast 13→12, trench 36→32, pressure 12→12 | preserved |
| siege B  | catapult 30→25, tension 14, **wall 29→58**, boulder 21→23, joint 1→2 | **preserved & grown** |

**VERDICT: the new hints preserve mechanics through repair.** Across all 8 B runs there is **not one
case** of a mechanic being demolished during a repair round — the opposite of the baseline heist. Where
counts fall it is a legitimate API fix (cargo `joint 12→0` because `FixedJoint3D` is not a real class),
never a "make it easier" shave. Several runs *gain* mechanic mass under repair (siege wall-refs double,
balance re-expresses spring-cables as manual tension). Two runs even *reject* a shave mid-reasoning:
stack considers "reduce number of blocks?" then self-corrects "*but we must keep 4 blocks*"; heist a6
rejects the one-axis collapse (Part 3b). The **nearest-to-a-shave** case in the whole B set is herd
zeroing a close-range dog→sheep *repel steering force* ("makes them easier to gather") — but this tunes
"the forces the ACTIONS apply" (an explicitly sanctioned reachability knob) and preserves the wolf,
pen, sheep and both stages, so it is force-tuning within the mechanic, not demolition. The remaining ambition shortfall is therefore
**not laundering**; it is (a) construction-time under-reading of *compound* mechanics (Part 3a) and
(b) the last-mile solvability wall (Part 1.3).

---

## PART 3 — CoT READING (all 59 traces read)

### 3a. First-generation influence — which contract phrases shape a1 design

Signature scan of all 8 `ATTEMPT 1` blocks (Y = the contract idea is quoted / reacted to before design):

```
game    not-a-circle  DIVERSITY  STAKES  MATERIAL  DETERMIN  Phys3D  single-act  7-methods  ORIGINAL
cargo        .            .         Y        Y         Y        Y        .           .          Y
heist        (Y)*         .         Y        Y         Y       (n/a)     .           Y          Y
balance      .            .         Y        Y         Y        .        Y           Y          Y
herd         Y            .         Y        Y         Y        .        .           Y          Y
stack        .            .         Y        Y         Y        Y        .           Y          Y
fluid        .            .         Y        Y         Y        .        .           Y          Y
subm         Y            .         Y        Y         Y        Y        Y           Y          Y
siege        .            .         Y        Y         Y        .        Y           Y          Y
TALLY        2(+)         0         8        8         8        3        3           7          8
```
`*` regex undercounts not-a-circle: heist a1 explicitly says "the thief… a small square (**not circle**)"
and "shape RectangleShape2D (not circle)"; ~half the runs deliberately choose a non-circle shape.

**Findings.**
- **The HARD RULES land universally.** Every a1 opens "design an ORIGINAL … game", reasons about
  losability/pressure (STAKES 8/8), anchors goals to real `Area2D/3D` nodes and latches off overlaps
  (MATERIAL REALITY 8/8 — e.g. subm "goal is an Area3D … latch off overlap"; fluid "tank is a real node…
  goal anchored"), and seeds an rng from `world_seed` while noting the global-`randi` ban (8/8). All 3
  3-D games engage the `PhysicsServer3D.set_active(true)` quirk explicitly (subm spends a paragraph on it).
- **DIVERSITY / "least-obvious reading" is inert (0/8).** No a1 ever performs the prescribed step —
  "name to yourself two or three genuinely different readings … then build the one that is least obvious."
  The model takes the **literal, first reading** of the prompt every time. It is *not* a problem here
  because the 8 seeds are already distinct and the literal readings are mechanically rich; but as a fact
  about the contract's influence, the aspiration-level sentence does not shape initial design, while the
  imperative HARD RULES do.

**The heist dwell-timer datapoint (measured construction-bias).** heist a1 **does** engage the prompt's
"dwell-timer alarm": it writes "there is a dwell-timer alarm counting down; if timer reaches zero before
reaching vault → failure." But in translation it **flattens the compound mechanic**: it implements a
plain **global** countdown — `var time_left := 30.0`, decremented every frame unconditionally
(`time_left -= delta`, `if time_left <= 0`) — and makes guard-sight a **separate instant** failure
("if seen by guard → failure"). The *dwell* semantics (an alarm that arms/counts while you linger in
sight) are never modelled. So it is neither a conscious drop nor a non-engagement: it is a **semantic
under-reading** — the model reaches for the simplest mechanism that satisfies the surface words and
splits a compound mechanic into two simpler independent ones. (Vocab confirms: heist B carries a working
`time_left` clock, 6 refs, but no dwell/sight-armed alarm.) The same flattening appears in **fluid**: the
model reads "rotate pipe **segments**" (plural), notes "could be multiple pipe segments but only one
[controlled]", and builds **one** rotatable pipe + a single water-blob — collapsing the routing degrees
of freedom (baseline had two independent baffles; B milestones 3→2). **balance is the counter-example**:
it faithfully built **three** independent `DampedSpringJoint2D` cables (6 actions) and even quoted "the
prompt is a seed, not a spec … but it explicitly states … counts (three suspension cables)" while
honoring them.

### 3b. Repair reasoning — simplify vs reachability-fix; does the preserve-clause show?

Confirmed the injected wording reaches the model: the **solvability-lane** hints in B carry
`PRESERVE_SHORT` verbatim — "*…make exactly that one step reachable (widen the gap, steady the hazard,
enlarge the target, or relax the timing at that step alone) … keep the mechanic, its gating and every
stage — removing locks/timers/stages/hazards, or collapsing the design into one repeated verb, is a worse
outcome than this failure.*" The **verify-lane** (ENV_ERROR) hints do **not** carry it — correct, since
demolition temptation only arises on reachability failures.

- **The model verbalizes reachability-fixing, not simplification.** heist a3: diagnoses that key2 sits
  inside the guard's permanent sight column and proposes a **local** move — "*move key2 off the guard's
  patrol line … from (300,100) to (260,100)*" — and states "*keep guard cone as is. No other changes.*"
  heist a4 debugs the actual geometry ("*the walls already seal completely … the door was redundant*")
  and widens the doorway gap — sophisticated, structure-preserving. subm a2/a3 bring the goal closer and
  fix tolerances "WITHOUT removing" stages. Nowhere does a repair plan say "I'll remove the timer / unlock
  the doors / line everything up."
- **Smoking-gun preserve-clause echo.** heist **a6** considers a straight-line east layout, then rejects
  it in its own words: "*That collapses to single repeated verb 'e' - not allowed (collapsing multi-stage
  into single repeated verb worse). So we must keep objectives not lined on one axis*" — and adds vertical
  n/s/e/w variation instead. The clause **demonstrably steered the model away from a shallowing move**.
- **siege regression is *ambition inflation*, not laundering.** siege B's a1 is far richer than baseline's
  certified version: catapult_arm as a controlled `RigidBody2D` on a `PinJoint2D` pivot, a separate
  boulder, a **dynamic 4-block toppling wall**, and a moving reinforcement cart as the pressure (joints
  1→2, wall-refs 29→58). That richer surface is hard to tune, and the model burned attempts on
  infrastructure: a1→a2 coordinate-frame bounds; a2→a3 "success under noop" + **dead actions**
  (tension_up/down only changed a variable — the model correctly bound tension to the arm pose); a3→a4 a
  strict-parser error; then a5-a9 oscillating between an un-toppleable wall (UNSOLVED `fired→wall_toppled`)
  and a trivially-toppled one (GOAL_ERROR "trivially reached in 14 ticks"). The preserve-clause *let* the
  model keep inflating complexity; it never converged.

### 3c. What else the traces reveal

**(i) GDScript dialect / API misconceptions (the dominant attempt-sink).** The model half-remembers a
different Godot and confidently invents classes/methods that don't exist in the target (Godot 4):

| invented / wrong | reality | where | damage |
|---|---|---|---|
| `FixedJoint3D` | no such class (PinJoint/Hinge/Slider/…) | cargo a1 | killed att1; forced dropping the magnet-grab joint (joint 12→0) |
| `PolygonShape2D` (as a Shape2D) | not a shape resource | heist a1→a2 | flattened the triangular sight-cone to a rectangle |
| `has_overlapping_body` | `overlaps_body` | subm (orphan a5) | mid-episode crash |
| `RigidBody2D.MODE_STATIC` | Godot-3 enum, removed | herd a1, fluid a1 | killed att1 |
| `apply_angular_impulse` / `add_central_force` | `apply_torque_impulse` / `apply_central_force` | fluid a2, stack a2 | mid-episode crash |
| `Vector2.limited` | `limit_length` | balance (baseline) a1 | killed att1 |

**(ii) The strict-parser trap.** A whole error class is the loader running `--check-only` with
warnings-treated-as-errors + strict typing: "*Cannot infer the type of "x" variable*" and "*inferred
from a Variant value … (Warning treated as error.)*" hit **att1 in cargo, herd, stack, balance** and cost
siege an entire wasted attempt (**a4, 12,658 chars** of the model guessing which `var` is untyped before
capitulating to explicit types everywhere). The contract never warns that `:= []`/untyped locals are
fatal here.

**(iii) A contract↔verifier contradiction.** The contract says a 3-D `angle` may be "*a scalar yaw or an
[x, y, z] vector*". The model trusted it and returned a 3-vector — and the solver **crashed** calling
`float()` on the list ("*float() argument must be … not 'list'*"), costing subm both runs' att1 until the
model worked around it by returning scalar yaw. Submarine only certified after this workaround. This is a
pure bug: the permitted form is unverifiable.

**(iv) Reasoning-length correlates with *confusion*, not success.** The longest traces are the model
*flailing* on opaque errors (heist a5 non-determinism 11,497 chars; siege a4 Variant-warning 12,658;
cargo a2 11,802), while the successful fixes were concise and targeted (heist a3/a4 reachability moves;
subm a5 one-line method rename). Three attempts have **empty** reasoning (herd a2, stack a2, stack a4);
herd a2's empty-reasoning output still fixed the parse error and reached the solvability gate. There is
**no** "longer reasoning → better outcome" signal; if anything the reverse.

---

## PART 4 — RECOMMENDATIONS (ranked by certified-ambitious-games-per-wave, each tagged)

**1. Kill the GDScript dialect / strict-parser friction. [harness-side + prompt-side]** — biggest lever.
   It eats att1 in 5/8 B runs and burns whole attempts on non-design errors, and the raised caps mostly
   fund this churn. Two surgical moves, both framed as *runtime HARD RULES* (like the determinism/banned
   rules), **not** design menus — so doctrine holds:
   - (a) *Parser*: stop treating type-inference / "inferred-from-Variant" **warnings as errors** at G0 —
     they are neither determinism nor sandbox violations. (Cheapest single fix; recovers cargo/herd/
     stack/balance att1 and the siege-a4 waste.)
   - (b) *One contract line naming the runtime*: "The loader is Godot 4.x, `--check-only`, warnings-as-
     errors: give every `var` an explicit type; `RigidBody2/3D` use `apply_central_force`/
     `apply_torque_impulse`; `Area2/3D` uses `overlaps_body`; joints are `PinJoint/Hinge/Slider/…`; there
     is no `MODE_*`, `FixedJoint3D`, or `PolygonShape2D`." This is informative de-biasing about the
     *runtime*, not a worked game — consistent with "de-bias must be informed, not a shave."

**2. Fix the 3-D `angle` contract↔verifier contradiction. [harness-side]** — cheap, certain win. Either
   make the solver accept a vector-valued `angle`, or change the contract to "`angle` is always a scalar
   (yaw in 3-D)." Submarine *only* certified after hand-working-around this; it is silently taxing every
   3-D run's att1.

**3. Help the last-mile (penultimate-milestone → success), don't just raise caps. [hint-side]** — this is
   now the dominant blocker among runs that *reach* the gate (balance/herd/fluid stuck one step short;
   siege oscillating). The current solvability hint ("widen the gap / steady the hazard / enlarge the
   target / relax the timing") is generic; for the *final* step the model can't tell which knob. When the
   **same** penultimate→success gap survives ≥2 attempts, escalate the hint with **witness telemetry** —
   the best episode's closest approach and on which axis — so the fix is targeted, not guessed. Keep it
   mechanical/telemetric (no prescriptions).

**4. Tighten the stall-guard and vary the framing on repeats. [harness-side]** — the guard fires only
   after 4 *identical* ENV_ERRORs (cargo att4-7, stack att6-9, herd att3-6 all byte-identical diagnoses),
   i.e. after the model has already wasted them. Fire after 2 identical defects, and on a repeated
   "body out of bounds" escalate to a concrete directive ("clamp position/velocity in `_physics_process`,
   never in `act`") rather than re-sending the same hint.

**5. The contract is fine — leave the diversity section alone. [none-needed]** — a1s engage the prompt
   and the hard rules richly and already produce distinct, ambitious games; the 0/8 DIVERSITY engagement
   is not costing certifications, and its original job (anti-collapse under repair) is now done by the
   preserve-clause. Adding examples/menus would violate doctrine for no gain. **Say it plainly: the
   contract is not the problem.**

**6. Compound-mechanic flattening is a known model limit, low-ROI to chase. [prompt-side / effectively
   none-needed]** — the heist "dwell-timer alarm → global clock" and fluid "segments → one pipe"
   under-readings are a nuance-parsing gap of a free ~30B model, not a diversity or contract failure.
   Closing it mechanically would require a per-prompt mechanic taxonomy (violates "metrics stay
   mechanical; no menus"). Accept it; the leverage is in items 1–3.

**Bottom line for Elias:** the two changes under test both did exactly their job — the **angle fix**
un-blocked 3-D verification (net +submarine) and the **preserve-clause** eliminated repair-laundering
(mechanics now survive/grow, with a caught-in-the-act reasoning trace to prove it). Certified count held
at 2/8 only because those wins were masked by (1) parser/API attrition that the free model can't
self-heal and (2) a last-mile solvability wall — neither of which is a contract problem. Fix items 1–2
first; they are cheap and directly convert ENV deaths into gate-reaching runs.
