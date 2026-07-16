# CREATIVE DIRECTIONS — opening the problem space without discarding the machine

> Strategy note, 2026-07-16 (night). Input: Elias's honest read — *"notre approche est
> très classique mais clean"* — creativity is the weak criterion of the three
> (CREATIVITY / CLARITY / WORKING OUTPUT). Constraint: every direction below must
> COMPOSE with the running harness (gamegen → G0–G3 → G4 ladder → G3′ RL → harden →
> capture), never replace it. The verifier stays frozen (SELF_IMPROVING_DESIGNER.md
> invariant); creativity is layered ON TOP of the proofs, because the proofs are the
> one thing a median team does not have.

## 0. The strategic read

Median submissions to "text → game" will have: an LLM writing games, a syntax/run
check, maybe a fuzzer, GIFs of wins. Our machine's genuinely rare assets are all
**proof-shaped**:

1. **Replayable witnesses** `{seed, actions}` — bit-identical proof-of-play (G3).
2. **Certified refutations** — tree-refuted softlock witnesses with engine-truth
   `frozen_state` facts (G4 inverse-value / descent / seeker).
3. **A trained critic V(s) per game** — a free value landscape (G3′).
4. **Failure witnesses** — proof the game can be *lost* (pressure gate).
5. **The ledger** — the complete diagnosed life of every game, including failures.
6. **Deterministic behavior facts** — witness length, solver effort, space
   utilization, latch curves (treesolve / reachability / curriculum profiles).

So the creative move is NOT "add more generation ideas" (everyone has those). It is:
**turn the certification by-products into the creative medium**. Every direction
below is an inversion of something the machine already proves.

External check (2026-07-16): QD/MAP-Elites over game content is an active academic
lane — [Generational Adversarial MAP-Elites, ALIFE 2025](https://arxiv.org/html/2505.06617v2),
[CreativeGame: mechanic-aware creative game generation](https://arxiv.org/pdf/2604.19926),
[level blending via QD in latent space](https://arxiv.org/pdf/2102.12463),
[V-GameGym benchmark](https://aclanthology.org/2026.findings-acl.276/) — but ALL of
it bins on code features, latent embeddings, or LLM-judged novelty. **Nobody bins on
certified behavior.** That is the gap we can own.

---

## 1. Ranked directions (creativity-impact × feasibility)

### D1 — THE ATLAS: illuminate certified game-space, then breed into the dark  ★ FLAGSHIP

**Idea.** Treat the certified library as a MAP-Elites archive over **deterministic
behavior descriptors computed from certification artifacts**: witness length, witness
action entropy, solver effort (Go-Explore cells per witness tick), space utilization
/ mechanic coverage, pressure class (failure witness exists / its length), G3′
learnability grade, dimension, checkpoint count, G4 finding density. Generation stops
being only prompt→game and becomes **cell-targeted**: *breed* two elite games to fill
an empty region (child inherits mechanic of A + pressure of B, carried as two parent
sources in one revise-shaped message), or *genesis* prompt-free from a parts-bank
genome + a qualitative cell brief. Variety becomes a measured number: **archive
coverage before/after** — exactly the "exogenous, separately-measured variety axis"
our own SELF_IMPROVING_DESIGNER.md verdict demanded and never built.

**Why it scores.** CREATIVITY: it is the literal answer to "how you open the problem
space" — the submission's first figure becomes a *map of game-space* with our games
as landmarks and the empty territory named; and the headline demo is **a certified
game nobody prompted**, born from two parents, with its full certificate. The
non-obvious twist over academic QD: our descriptors are *proofs about play* (how the
game was solved, attacked, learned), not code features or embeddings — an oracle only
this stack has. CLARITY: one picture. WORKING OUTPUT: the funnel, certifier, harden,
capture are all untouched — the atlas only reads artifacts and adds two new user-msg
builders.

**Seams.** `harness/gen/curriculum.py::difficulty_profile` (solver+RL profiles
exist); `harness/verify/reachability.py::space_utilization`; treesolve stats under
`layers.G3_solve`; `runs/ledger.jsonl`; `harness/gen/retrieval.py::retrieve_menu`
(genome menu); `harness/gen/gamegen.py` — add `_breed_user_msg` / `_genesis_user_msg`
as siblings of `_revise_user_msg`; artifacts in `scenes/games/*/`. Anti-anchoring
holds: bins and numbers live harness-side; cell briefs are rendered as qualitative
principles on the LLM surface (no magic values).

**Minimal experiment (1 day).** Descriptor extractor over the ~20 existing game dirs
→ `runs/atlas.jsonl` + one map figure; pick the emptiest cell; run ONE breed and ONE
genesis through the untouched funnel on `mit_preemptable`. Success = one certified
child in a previously-empty cell; report the coverage delta honestly even if zero.

### D2 — THE ADVERSARY TURNS DESIGNER: refutations recycled as content

**Idea.** Certified G4 findings stop being only defects and become **design
material**. A certified softlock (with its `frozen_state` engine facts: position,
velocity, enclosing walls) gets a **promote** directive instead of a repair one:
*keep the pocket — make it a visible, losable hazard* (the trap becomes an
intentional pit; `is_failure()` fires there, so the pressure gate now witnesses a
loss AT the former bug). Shortcut findings (informational, easier-than-witness)
become an official second demo category: **intended route vs discovered route**, side
by side — the speedrun the attacker found, replayed as a first-class witness.

**Why it scores.** CREATIVITY: it inverts the harness's own moral — *the bug is the
level*. Every hazard in the final game is literally certified dangerous; every
"glitch route" is a replayable proof. Median teams have fuzzers; nobody recycles
refutation witnesses into level design. CLARITY: before/after GIF pairs explain
themselves. WORKING: one new directive class + one harden mode; certification
unchanged (the promoted game must re-certify AND now pass pressure with a witness at
the pocket).

**Seams.** `harness/verify/g4.py` findings (the `frozen_state` block already carries
pos/vel/nearby/enclosing — built 2026-07-16); `harness/gen/feedback.py` — a
`promote_softlock` row beside the repair row (severity machinery untouched);
`harness/gen/harden.py` — a `--design` mode: on `HARDENED`-family verdicts, offer one
promote round; `harness/verify/capture.py::capture_gif(actions=...)` replays attacker
prefixes for the demo overlay.

**Minimal experiment.** Take one certified softlock finding (fixture
`softlock_maze.gd` or a real game's), hand-compile the promote directive from the
finding JSON, one `_revise_user_msg` round, full re-cert + require a failure witness
located at the pocket. Render the before/after GIFs.

### D3 — EVERY GAME SHIPS ITS BIOGRAPHY: the witness as narrative

**Idea.** Auto-generate a **birth certificate** per game from the ledger: each
attempt and its diagnosed flaw, the adversary's best finding replayed as a GIF (red
overlay at the frozen state), the repair directive that fixed it, the final witness
solve, the RL learning curve. The demo stops showing "a game" and starts telling
**the story of its certification** — which is the story of the harness itself. The
parking prompt's 5-attempt saga (parse → OOB cones → pre-satisfied checkpoint → dead
milestones → single-action win) becomes a headline exhibit: *a certified non-game
with five distinct diagnosed flaws is the strongest possible demo of the verifier*.

**Why it scores.** CLARITY weaponized (the submission explains itself through
stories), CREATIVITY decent (failure as exhibit; nobody demos their rejects), WORKING
trivially (read-only over existing artifacts).

**Seams.** `runs/ledger.jsonl` (every directive + verdict already recorded);
`scenes/games/<slug>/a1..a5.gd` (attempt history already on disk);
`capture.capture_gif` (witness AND attacker prefixes are replayable action lists);
`feedback.Directive.detail`.

**Minimal experiment.** `scripts/game_card.py <slug>` → one markdown card + 2 GIFs
for fly-rings, and the parking FAILURE card. Half a day.

### D4 — THE CAMPAIGN: certified games as rooms of a certified meta-world

**Idea.** A harness-side composer wraps N certified games as sub-nodes of one hub
GameAPI (doors latch on each child's `is_success()`; checkpoints namespaced
`room1/...`); the composite goes through the SAME funnel, so the campaign is
certified as a whole and its witness is the concatenation of room solves. The atlas
(D1) curates the rooms — the meta-world is a guided tour of the map.

**Why it scores.** "We don't generate games; we generate a certified world of games."
The composition is deterministic harness code (no LLM risk); creativity lives in the
framing and curation. Risk: `state()`/`actions()` multiplexing is a real (bounded)
wrapper problem, and cert cost scales with rooms — rank below the top 3.

**Seams.** `godotworld/serve_game.gd` (duck-typed GameAPI — children speak the same
contract); `gameverify` funnel unchanged; `capture` for the multi-room GIF.

**Minimal experiment.** Hand-write `hub.gd` chaining TWO certified games; pass G1
determinism; replay both witnesses concatenated; capture the transit GIF.

### D5 — SOLVER-PSYCHOLOGY METRICS: machine-surprise as an oracle (feeds D1)

**Idea.** "How hard did the machine have to think" as first-class metadata:
Go-Explore cells expanded per witness tick; refuted-candidate density in G4; the
critic's V(s) traced along the witness — a **tension curve** rendered under each demo
GIF like a film score (non-monotone V = drama). Deterministic, judge-free creativity
signal; the best-separating metric becomes an atlas axis.

**Seams.** treesolve stats; g4 findings; `harness/rl/adversary.py` already loads
critics for inference. **Minimal:** compute 3 candidate metrics on the 5 certified
games, one plot, pick one axis. Half a day; do it inside D1's Monday.

### D6 — SKILL-COUPLED LADDERS: one prompt → a certified difficulty staircase

**Idea.** From one certified game, emit revise-variants targeted at the measured RL
latch curve ("gentler between cp k and k+1"), each re-certified, ordered by measured
steps-to-solve → an auto-generated, *proof-carrying* curriculum. Twist over median
auto-curriculum: rungs are certified and the ordering is measured, not asserted.
Cost: one G3′ run per rung — expensive; keep as a stretch.

**Seams.** `curriculum.curriculum_round` + `difficulty_profile` (already exist);
`_revise_user_msg`; `sb3_trainer`. **Minimal:** 3 rungs of fly-rings at quicktest RL
budgets; check monotonicity.

### D7 — THE GAME PLAYS YOU BACK (demo garnish, not a direction)

`demo_player.gd` + the trained critic + the softlock atlas → a live coach overlay
("you are descending into a proven dead end"). Charming; fold into D3/D5 demo
dressing if time remains. Not independently load-bearing.

---

## 2. Kill list (median ideas, cut or demoted)

- **Daily-challenge pipeline** — any PCG team proposes this. Kept ONLY as garnish:
  public "beat the witness" where submissions are replayable action files verified by
  the serve host (proof-carrying speedruns). Not a direction.
- **Designer-model tournament / self-play arena** — median; adds no new oracle.
- **LLM/VLM-judged novelty or creativity scores** — violates the lane's no-judge
  stance AND is what academic QD-LLM work already does (embedding-distance novelty).
  Cut on both grounds; D1/D5 are the deterministic replacement.
- **Visual/asset/3D upgrades as "creativity"** — polish; the asset lane exists.
- **Full POET-style open-ended coevolution** — a research program, not a composable
  direction. D1 imports its measurable core (archive coverage, novelty-as-rank)
  without the runtime.

## 3. Top 3 and the flagship argument

**Top 3: D1 (Atlas) > D2 (Adversary-as-designer) > D3 (Biographies).**
D1 and D2 tie on raw score (D1: creativity 9 × feasibility 8; D2: 8 × 9); D1 wins the
flagship because it opens the SPACE while D2 deepens individual games.

**Flagship = D1, THE ATLAS**, because:

1. It answers the criterion sentence *literally*: "how you open the problem space" →
   we ship a map of the problem space, with the unexplored regions named and then
   deliberately colonized.
2. It converts our weakness into the story: the "classical but clean" machine is
   exactly what makes illumination *trustworthy* — every point on the map is a proof,
   not a vibe. Against the 2025–26 QD-for-games literature, certified-behavior
   descriptors are ours alone.
3. It subsumes the runners-up instead of competing with them: D5's metrics are its
   axes; D2's finding-density is an axis and promote-rounds mint new elites; D3's
   cards caption each cell; D4's campaign is a tour of its elites. One flagship,
   every direction composes.
4. It is honest to our own notes: SELF_IMPROVING_DESIGNER.md's verdict ("variety
   needs an exogenous signal and a separately-measured axis, or the loop quietly
   trades it away") is implemented, not just cited.
5. Monday-night deliverable is concrete: `atlas.svg` + one prompt-free certified
   child + a coverage number.

## 4. What to build Monday morning (flagship, ≤1 day)

1. **AM (~3h) — `harness/atlas/descriptors.py`** (new, read-only over artifacts):
   for each `scenes/games/<slug>/` with a verify report / ledger events, emit one row
   `{slug, witness_len, action_entropy, solver_cells_per_tick, space_utilization,
   has_failure_witness, failure_witness_len, g3p_grade, dimension, n_checkpoints,
   g4_finding_density}` → `runs/atlas.jsonl`. Missing artifacts → nulls; NO re-runs,
   NO engine cost. Unit test: rows for the 5 certified games + the parking failure.
2. **AM (~1h) — `scripts/atlas_map.py`**: bin two chosen axis pairs (start:
   witness_len × action_entropy, pressure × space_utilization), render one SVG map
   in-image (matplotlib); print the 3 emptiest cells with a qualitative brief each.
3. **PM (~3h) — gamegen seam**: `_genesis_user_msg(cell_brief, parts_menu)` and
   `_breed_user_msg(parent_a_src, parent_b_src, inherit_brief)` beside
   `_revise_user_msg`; parents = adjacent occupied cells; briefs qualitative
   (anti-anchoring: numbers never cross the LLM surface). CLI:
   `harness game breed <slugA> <slugB>` / `harness game genesis --cell <id>`.
   The repair loop, funnel, and harden driver are byte-untouched.
4. **PM — cluster**: 1 genesis + 1 breed job, standard Slurm discipline
   (`mit_preemptable`, in-image, per-task ledger shards).
5. **Evening — measure & exhibit**: archive coverage before/after; if a child
   certifies in a new cell, capture its GIF and stamp its card (D3-style) with
   `born_from: [parentA, parentB] / cell: <id> / prompt: NONE`.

Acceptance: (a) atlas rows for 100% of game dirs that have artifacts; (b) the map
renders; (c) ≥1 child fully certified; (d) the coverage delta is reported honestly,
including a zero.

## Sources (external, checked 2026-07-16)

- [Generational Adversarial MAP-Elites (ALIFE 2025)](https://arxiv.org/html/2505.06617v2)
- [CreativeGame: mechanic-aware creative game generation (2026)](https://arxiv.org/pdf/2604.19926)
- [Generating and blending game levels via QD in latent space](https://arxiv.org/pdf/2102.12463)
- [V-GameGym: visual game generation benchmark (ACL 2026)](https://aclanthology.org/2026.findings-acl.276/)
- [Illuminating dungeon maps / missions with MAP-Elites](https://arxiv.org/pdf/2202.09301)
