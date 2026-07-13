# Parts Bank — design study (v2.2 candidate, becomes CONTRACTS §9)

> Status: DESIGN ONLY (2026-07-13). Nothing here is implemented. This document is
> the study for a curated **bank of parts**: named items / obstacles / elements
> with PREDICTABLE physical properties (optionally sprites) that the generation
> model pulls from by NAME based on prompt context, so the code-writing model has
> far less physics to get right. Aligns with OBJECTIVES (pyramid strategy,
> lessons-harvest phase; hard rule "no game-specific hardcoding"; small
> verb-shaped API) and CONTRACTS §1/§2/§4. When ratified, the normative subset
> becomes CONTRACTS §9 and a compact block enters `gamegen._SYSTEM_PROMPT`.

---

## 0. One-paragraph thesis

The generator is reliable at inventing MECHANICS (verbs: forces, custom gravity,
drag, scoring, win/lose logic) and unreliable at calibrating OBJECTS (nouns:
masses, joint anchors, non-overlapping placement, static vs dynamic, sensor
wiring). The ledger proves this concretely (§Part A.2): every repair iteration in
the valid campaign fell into just two buckets, and the single class that never
converged — numerical explosion from a hand-built pendulum joint — is a pure
noun-calibration failure. A **parts bank** is a curated, versioned, pre-certified
vocabulary of nouns exposed through ONE new verb, `world.part(...)`. It does not
enlarge the API surface (verb-shaped lesson), it does not touch mechanics
(freedom for verbs stays), and it converts a class of "the model guessed wrong
physics" into "the model picked a name that was already proven correct."

---

# PART A — Grounding

## A.1 How the pattern appears in prior art

The recurring structure across all of these is the same: **a fixed pool of
predictable building blocks + a thin composition layer + a constraint that keeps
compositions valid.** What makes each bank USEFUL is that the blocks carry
guarantees the composer no longer has to re-derive; what makes a bank
CONSTRAINING is when the pool is too small, too rigid, or when it also tries to
own the RULES (not just the objects).

| Prior art | The "bank" | What made it USEFUL | Failure mode when CONSTRAINING | Lesson we take |
|---|---|---|---|---|
| **GenSim** (ICLR 2024 spotlight, arXiv 2310.01361) — the paper we already reference | A **task library** of high-quality (description, code) templates, seeded from a human-curated benchmark; the LLM composes tasks over the Ravens/CLIPort **fixed asset pool** (blocks, bowls, pallets, bins, letters — referenced by name) and retrieves similar library tasks as few-shot context ("augmented generation"). | Reusable templates + a bounded asset vocabulary let them scale a benchmark **10× to 100+ tasks** and get real sim-to-real transfer (+25% over baselines). The library is the scaling lever. | Novelty is bounded by the asset pool + retrieved exemplars; over-retrieval biases toward rehashing library tasks. | Retrieve a **themed subset** as context, don't dump the whole pool; keep the pool broad; measure novelty. |
| **Unity prefabs** | A prefab is a saved GameObject (geometry + components + default values) instanced by reference; instances carry **per-instance overrides** and prefab *variants*. | Author once, reuse everywhere; overrides give per-use variation without forking the asset; a fix to the prefab propagates. | Deep override chains / variant trees get unpredictable; monolithic prefabs are hard to recompose. | **Bounded per-instance overrides** are the sweet spot: reuse + variation without a fork. |
| **Roblox Toolbox / Creator marketplace** | Searchable catalog of community + first-party models/parts dropped into a scene. | Enormous breadth; instant scene population. | **No quality/physics guarantee** — many parts are broken, unlicensed, or mis-scaled; curation is the user's problem. | A bank is only as good as its **curation + provenance/license**; we certify before admission. |
| **PuzzleScript** (OBJECTS + LEGEND) | `OBJECTS` declares each object (name, colour, 5×5 sprite); `LEGEND` binds a single char to an object and defines aggregates (`and`) and property classes (`or`, e.g. `Flying = Bat or Bird`). | A tiny fixed object vocabulary + a legend makes levels writable as ASCII and rules readable; **objects are nouns, RULES are separate**. | If you need a noun the vocabulary lacks, you're stuck; expressiveness is capped by the object set. | Enforce the **noun/verb split** at the schema level: the bank owns objects; the game keeps its own rules. |
| **Tiled object templates (`.tx`) / LDtk entities** | A saved template/entity with typed **fields** (bounded types, e.g. a `Mob` with `hitPoints: Int[0..10]`), instanced into levels; importers map an entity to an engine prefab. | Typed, constrained fields = reusable, engine-portable, validated at author time; one definition drives multiple engines. | Rigid field schemas resist ad-hoc needs; entity ≠ behavior (still need engine code). | **Typed, range-constrained fields** + **engine bindings** are exactly our override + portability model. |
| **Angry-Birds level-gen** (Ferreira & Toledo CIG'14; Stephenson & Renz CIG'16) | A **block vocabulary** (block types, materials, sizes) composed top-down into structures, each **checked for global stability** (simulated velocity over time) and rejected if unstable. | Vocabulary + an **automatic stability oracle** yields diverse *and valid* structures at scale; stability is a property of the blocks + a check, not of the author. | Probability tables + symmetry constraints narrow the style; unconstrained composition still produces junk without the oracle. | The bank must pair each part with a **physics invariant a verifier enforces** — a "part" is a shape *and* its stability contract. |
| **PCG design-pattern libraries** (Dahlskog & Togelius, FDG'12 / PCG'14) | Mined **micro-patterns** (vertical slices of real levels) + **meso-patterns** (abstractions); generation uses "number of patterns present" as the fitness objective. | Patterns encode *proven-good* local structure; reusing them transfers designer intent and guarantees stylistic coherence. | Pattern-maximizing fitness converges toward the source corpus → sameness if patterns are the only objective. | Patterns/parts are a FLOOR on validity, not the whole objective; **keep an independent diversity pressure** so the bank raises quality without collapsing variety. |

**Cross-cutting extract.** A bank pays off when (1) its units carry a
**machine-checkable guarantee** (Angry-Birds stability, LDtk typed fields), (2)
composition is **thin and the rules stay separate** (PuzzleScript, prefabs), (3)
the composer sees a **themed subset**, not the whole pool (GenSim retrieval), and
(4) there is **independent diversity pressure** so validity doesn't become
sameness (the PCG-patterns caveat). All four map directly onto Part B.

## A.2 What the ledger says a parts bank would (and would not) fix

Two ledgers exist. `ledger_invalid_20260713_blind_repairs.jsonl` is a **discarded
batch**: a repair-loop bug fed empty hints back (all `failure_class: null`,
5/5 ENV_ERROR after 4 wasted attempts) — orthogonal to parts, excluded from the
counts below except as evidence that repair iterations are expensive (280–503 s
each, all wasted). `ledger.jsonl` is the **valid** post-fix batch (5 runs).

**Valid batch, exact tally (5 runs, 11 attempts, 6 repair iterations):**

| Prompt | Verdict | Attempts | Repair iters | Failure class(es) on failed attempts |
|---|---|---|---|---|
| swing a wrecking ball to knock a tower into a pit | **ENV_ERROR (never solved)** | 4 | 3 | `G1_rollout.no_nan` ×4 (+ efficacy ×2) |
| bounce a ball off walls to hit ceiling target | COMPLETED | 2 | 1 | `G1_rollout.efficacy` (dead action `wait`) |
| stack two crates to reach a high ledge | COMPLETED | 1 | 0 | — (clean) |
| steer a raft across a river current to the dock | COMPLETED | 2 | 1 | `G1_rollout.efficacy` (dead action `wait`) |
| launch a rocket and land it gently on a platform | COMPLETED | 2 | 1 | `G1_rollout.efficacy` (dead action `wait`) |

Completion rate 4/5 = 80%. **Every one of the 6 repair iterations falls into
exactly two classes**, split 50/50:

- **NaN / numerical explosion from a bad joint (3/6 iters, and the ONLY
  non-completion).** Root cause is visible in the generated code
  (`scenes/games/swing_a_wrecking_ball_.../a4.py`): a `PinJoint` with
  `anchor_b=(0, 250)` — a 250 px lever arm on a mass-2 ball — is an unstable
  hand-built pendulum that explodes under the solver. This is a **pure
  noun-calibration failure**: the model wanted "a wrecking ball on a chain" and
  mis-built the joint. It consumed the entire 403 s run and still failed.
- **Dead action `wait` (3/6 iters, 3 distinct games).** The model reflexively
  declares an idle `wait` action that does nothing, tripping the G1 action-efficacy
  (dead-action) check. This is a **verb/mechanic** mistake, not a noun mistake.

**What a parts bank plausibly ELIMINATES** (physics mis-calibration = nouns):

| Failure class | Verifier check | Why the bank removes it |
|---|---|---|
| NaN/explosion from bad joint setups | `G1_rollout.no_nan` | A `wrecking_ball` / `pendulum` part is a **pre-certified subassembly** (anchor+ball+correctly-anchored joint) that passed bank-CI settle without NaN. **≈50% of valid-batch repair iters + the only reliability failure.** |
| Initial penetration | `G0.no_penetration` | Parts carry exact sizes and snap-anchored placement; terrain tiles abut instead of overlap. |
| Floating / out-of-bounds objects | `G0.in_bounds`, `broken_floating` class | Terrain parts resolve to STATIC bodies by construction; props declare a rest state. |
| Wrong densities → weird dynamics | `G1` (efficacy magnitude, settle) | Every part ships sane default mass/friction/elasticity, tuned once. |
| Hazard/sensor mis-wiring | `G0`/`G2` | Hazard parts self-wire `on_contact`→lethal flag; triggers are sensors by construction. |

**What it CANNOT touch** (goal/verb design — must stay prompt/rule work):

- Dead-action / agency (`G1_rollout.efficacy`) — the `wait` class, the OTHER 50%.
  A noun bank cannot stop the model from declaring an idle action. Fix belongs in
  the prompt (state "there is no implicit idle move; do not add a do-nothing
  action") — noted here so the bank is not oversold.
- Goal well-formedness `G2` (success false at t=0, purity, checkpoint frame).
- Solvability / `UNSOLVED` `G3` and milestone ordering — reachability of the goal.

**Honest caveat:** n=5 is tiny; treat the 50/50 split as directional. But the
*mechanism* is unambiguous — the hardest, non-converging failure in the campaign
is precisely the joint-calibration class a bank subassembly is built to remove.

---

# PART B — The design (normative candidate for CONTRACTS §9)

## B.1 Bank entry schema

The bank is **DATA** — a directory of JSON entries plus optional sprite blobs,
versioned independently of harness code. One entry = one named part. A part may
be a single body OR a **subassembly** (several bodies + joints) — subassemblies
are where the leverage is, because they hide exactly the joint/anchor math that
exploded in the ledger.

```jsonc
{
  "name": "wrecking_ball",
  "bank_version": "1.0.0",              // catalog version this entry belongs to
  "category": "mobile",                 // terrain | prop | hazard | mobile | trigger | decor
  "summary": "heavy ball on a rigid chain hung from a fixed anchor; swings as a pendulum",

  // --- shape / assembly: engine-NEUTRAL realization (see B.6 for bindings) ---
  "assembly": [
    { "role": "anchor", "shape": "circle", "radius": 4,
      "static": true, "offset": [0, 250] },
    { "role": "ball",   "shape": "circle", "radius": 25, "mass": 10.0,
      "friction": 0.6, "elasticity": 0.2, "offset": [0, 0] }
  ],
  "joints": [
    { "type": "pin", "a": "anchor", "b": "ball",
      "anchor_a": [0, 0], "anchor_b": [0, 0] }   // anchors at CoM -> stable pendulum
  ],
  "primary": "ball",                    // the handle world.part(...) returns
  "control_candidate": "ball",          // natural controlled body if the game wants one

  // --- defaults already baked into assembly; overrides are the ONLY knobs games get ---
  "overridable": {
    "mass":  { "path": "ball.mass",   "range": [2.0, 40.0] },
    "radius":{ "path": "ball.radius", "range": [12, 40] },
    "chain": { "path": "anchor.offset[1]", "range": [120, 320] },  // arm length
    "scale": { "range": [0.6, 1.6] }    // uniform scale of the whole assembly
  },

  // --- declarative behavior hooks the harness wires (game logic stays free) ---
  "behavior": { "kind": "pendulum", "impulse_axis": "x" },

  // --- INVARIANTS the verifier enforces per instantiation (B.4) ---
  "invariants": ["anchor_is_static", "joint_present", "settles_without_nan",
                 "no_self_penetration"],

  // --- cosmetic only, never read by physics or verifier ---
  "sprite": { "ref": "sprites/wrecking_ball.png", "anchor": [0.5, 0.5],
              "attach": "ball" },

  "provenance": { "author": "harness-curated", "license": "CC0-1.0",
                  "source": "hand-authored", "certified_ts": "..." },

  "engine_bindings": { "pymunk": {}, "planck": {}, "phaser": {} }  // B.6
}
```

Category semantics + the invariant each category MUST satisfy (this is the
contract the verifier keys off — a part is a shape *and* a guarantee):

| Category | Meaning | Enforced invariant(s) |
|---|---|---|
| `terrain` | ground, walls, ledges, platforms (fixed geometry) | all sub-bodies STATIC; stays put under noop |
| `prop` | movable object the player pushes/stacks/carries | dynamic; settles to rest under noop; sane mass |
| `hazard` | lethal / failure-inducing element | sensor OR self-wires `on_contact(controlled, ·, lethal_flag)` |
| `mobile` | pre-jointed subassembly (pendulum, moving platform, seesaw) | joint(s) present; **settles without NaN**; bounded motion |
| `trigger` | goal zone, checkpoint pad, switch | is a SENSOR; non-lethal; detectable via contacts |
| `decor` | cosmetic only | zero physical footprint (sensor, mass≈0) or sprite-only |

Provenance/license is mandatory (Roblox-Toolbox lesson: a bank is only as good as
its curation). Sprites are strictly cosmetic — verification never reads pixels
(CONTRACTS: "no pixels anywhere in verification"); they become real only on the
JS/Phaser engines (B.6).

## B.2 World API surface — ONE new verb

```python
def part(self, name: str, kind: str, *, pos, **overrides) -> str
    # Instantiate bank part `kind` at world position `pos` under namespace `name`.
    # Sub-bodies register as "name", "name.anchor", "name.ball", ...
    # Returns the PRIMARY handle ("name" -> the primary sub-body).
    # Overrides: only keys in the entry's `overridable` map, each clamped to its
    #   declared range; an out-of-range or unknown override raises ValueError
    #   (surfaced by G0 as ENV_ERROR with a precise hint).
```

Usage — the wrecking-ball game, before vs after:

```python
# BEFORE (free code — this is the version that exploded, a4.py):
world.add("anchor", "circle", pos=(200, 500), radius=2, static=True)
world.add("ball", "circle", pos=(200, 250), radius=20, mass=2.0)
world.control("ball")
world.pin("anchor", "ball", anchor_a=(0, 0), anchor_b=(0, 250))   # <- 250px lever -> NaN

# AFTER (bank noun; mechanic stays free):
world.part("wrecker", "wrecking_ball", pos=(200, 250), chain=250, mass=10.0)
world.control("wrecker")            # primary handle = the ball
# act(), on_step(), success(), checkpoints() are still 100% the game's own code.
```

**The noun/verb boundary is the load-bearing rule.** The bank supplies NOUNS
(calibrated objects/subassemblies). The game keeps writing every VERB itself:
`ACTIONS`, `act()`, `on_step()`, `success()`, `failure()`, `checkpoints()`.
`world.add()` stays the **escape hatch** — anything the bank lacks is still
free code, so the design ceiling is never lowered. This keeps the API
verb-shaped: we add exactly one verb, not sixty nouns-as-methods
(GAME_ENGINE_INTEGRATIONS: "small, verb-shaped APIs beat big ones").

**Bounded overrides.** Only the entry's `overridable` keys, each range-clamped.
No override may flip a category invariant (e.g. you cannot override a `terrain`
part to be dynamic, or a `trigger` to be non-sensor) — those keys simply are not
in `overridable`. `scale` is uniform and range-bounded so geometry stays sane.

**Preventing bank-driven sameness** (the PCG-patterns caveat, made concrete):

1. **Breadth.** Target ~60 parts across the 6 categories so combinations
   explode: with ~10 parts/category and a typical game using 3–5 parts, the
   composition space is ~10^4–10^5 before overrides and free mechanics.
2. **Themed subsets per prompt** (GenSim retrieval, B.3). The model never sees the
   whole catalog — it sees an ~8–15 part slice selected by prompt keywords, so
   different prompts get different vocabularies → structurally different games.
3. **Originality budget shifts to VERBS.** Nouns are where the model failed and
   where we want *reliability*; mechanics are where we want *variety* and where
   the model is strong. The prompt's "invent a mechanic — do NOT default to a
   platformer" instruction stays; the bank frees tokens/iterations to spend there.
4. **Diversity measurement over part-combinations.** Every certified game logs the
   multiset of `{kind, category}` it used (from the DESIGN "Parts used" line,
   B.3). Feed this into the planned effective-semantic-diversity metric (OBJECTIVES
   "effective semantic diversity, COLM 2025"): treat the part-multiset as one axis
   of the descriptor. Two certified games with the same part-multiset AND the same
   mechanic family flag as near-duplicates. The bank raises the validity floor; the
   diversity metric guards the ceiling — independent pressures, per the PCG lesson.
5. **Escape hatch preserves the ceiling.** Exotic prompts (pyramid rung 3) can
   ignore the bank entirely and go full free-code.

## B.3 Prompt integration (without bloating the system prompt)

Current `_SYSTEM_PROMPT` measures **~7060 chars ≈ 1765 tokens** (131 lines). A
naive full 60-entry catalog with schemas would add **~3000–3600 tokens** — it
would *more than double* the prompt and blunt the model (verb-shaped lesson). So:
**two-tier injection.**

**Tier 1 — always in the system prompt (~+180 tokens).** The verb signature +
the noun/verb rule + a 6-line category legend. No individual parts listed.

```
# Parts bank (optional) — calibrated NOUNS you may drop in by name
world.part(name, kind, *, pos, **overrides) -> str
  Instantiates a pre-certified part (single body or a jointed subassembly) at pos.
  Sub-bodies namespace as name.<role>; the return value is the primary handle.
  Overrides are bounded (see the part menu); world.add stays available for
  anything the bank lacks. Parts give you correct OBJECTS; you still write the
  MECHANIC (actions, rules, win/lose). Categories:
    terrain (static ground/walls/ledges) · prop (movable) · hazard (lethal) ·
    mobile (pre-jointed: pendulums, moving platforms, seesaws) ·
    trigger (sensor goal/switch) · decor (cosmetic).
  A "Parts used" line in your DESIGN block must list each part + overrides.
```

**Tier 1b — themed part menu in the FIRST USER message (~+150–250 tokens).** A
retriever selects an ~8–15 part subset by prompt keywords (embedding or keyword
match over `name`+`summary`+category) and inlines one compact line each:

```
Parts available for this prompt (pick by name, or ignore and use world.add):
  wrecking_ball (mobile) — heavy ball on a rigid chain; swings as a pendulum
    overrides: mass 2–40, chain 120–320, scale 0.6–1.6
  ledge (terrain) — static rectangular shelf; size/pos overridable
  crate (prop) — light stackable box; mass 0.2–2, size 20–60
  pit_zone (trigger) — sensor rectangle; fires contact when a body enters
  ... (~8–15 lines)
```

**Tier 2 — full schema on demand (0 tokens unless used).** The one-line menu is
enough to *choose*; the model does not need each entry's full JSON. Full
`overridable`/sub-body-role detail is only needed when the model actually uses a
part, and the compact override summary in Tier 1b already covers the common case.
If a part's subassembly roles matter (e.g. the game wants `world.control` on a
non-primary sub-body), the retriever expands that single entry's role list inline.

**DESIGN block change** — one new line (also machine-readable for the verifier +
diversity logger):

```
DESIGN
Theme: ...
Entities: ...
Parts used: wrecking_ball(chain=250, mass=10) as "wrecker"; pit_zone as "pit"   <-- NEW
Mechanic twist: ...
Actions: ...
Milestones: ...
Win / Lose: ...
```

**Net token cost:** system prompt +~180, first user message +~200 ⇒ **~+380
tokens**, versus ~+3600 for a full inline catalog — and the generated `build()`
*shrinks* (B.5), so end-to-end token use can fall.

## B.4 Verification integration

Parts add checks at the layers that already exist (CONTRACTS §4); free-code games
verify exactly as today (all part checks are no-ops when no `world.part` is used).

**Bank-CI (one-time, offline, when a part is admitted — not per game).** Each
part is instantiated in isolation across a grid of its override ranges and run
through the existing `G0` + a 300-step noop `G1` settle. A part is admitted to the
catalog only if it passes its declared invariants everywhere in range. This is
what lets in-game checks stay cheap: the part is *already proven*, so at game time
we only re-verify that THIS instantiation respects the invariants.

**G0 gains per-part invariant checks (cheap, mechanical), alongside
`no_penetration`/`in_bounds`:**

```
for each world.part(name, kind, **ov) call recorded during build:
  - kind exists in the PINNED bank version           -> else ENV_ERROR "unknown part 'kind'"
  - every ov key is in entry.overridable, value in range -> else ENV_ERROR "override X out of range"
  - resolve sub-bodies; assert the category invariant on the live bodies:
      terrain  -> all sub-bodies static
      trigger  -> primary is a sensor, non-lethal
      hazard   -> sensor, or on_contact(controlled, part, <flag>) was registered
      mobile   -> declared joint(s) exist; no initial self-penetration
```

**G1 expectations per category** (layered on the existing 600-step noop rollout /
determinism / efficacy checks):

| Category | G1 expectation |
|---|---|
| terrain | sub-bodies do not move under noop (Δpos ≈ 0) |
| prop | comes to rest under noop (bounded settle, no escape) |
| mobile | **no NaN/explosion** + motion stays bounded (this is the check the ledger's wrecking ball failed; a bank pendulum passes by construction) |
| hazard (sensor) | never produces a physical collision impulse |
| trigger | sensor never blocks a body; contact-detectable |

**Integrity manifest (bank = versioned DATA, not code).** The bank is not part of
the frozen base code, but a run must be reproducible and a part must not mutate
mid-run. So:

- Bank lives at `banks/parts/<version>/` with `bank.lock = {version, sha256(catalog)}`.
- `integrity.snapshot()` (currently harness `*.py` + `CONTRACTS.md`) is EXTENDED to
  also hash the pinned bank catalog file(s). A bank change mid-run invalidates the
  run exactly like a base-code change (OBJECTIVES hard rule). Sprite blobs are
  hashed too (cosmetic, but pinning keeps demos reproducible).
- Each `runs/ledger.jsonl` line records `bank_version` (new field), so `game stats`
  can attribute reliability gains to a bank version.

## B.5 Scaling argument (numbers)

Grounded in the valid ledger (§A.2); figures the tiny sample can't pin down are
marked `[est.]`.

**Reliability.** The bank eliminates the `no_nan`-from-joint class outright. On
the valid batch that is **3 of 6 repair iterations AND the only non-completion**
(the wrecking ball, 403 s, 4 attempts, never solved). Swapping in a certified
`wrecking_ball` part converts that run to a first-attempt pass ⇒ completion
80% → ~100% on this sample, and the campaign's worst money-pit disappears.

**Iteration cost.** Each repair iteration is one LLM generate + full verify
(~110–200 s and ~2–4k tokens in the valid batch; the discarded blind-repair batch
burned 280–503 s per fully-wasted run). Removing ~50% of repair iterations (the
noun classes) saves, on this batch, ~3 iterations across 5 games ≈ **0.6
iterations/game `[est.]`**, concentrated on joint/hazard/terrain-heavy prompts —
where it is the difference between converging and never converging.

**Per-game token cost.** `day2_lander.py` is ~4585 chars (~1146 tokens); its
`build()` is ~15 `add()` lines (pad + two rock hazards + ship). A bank version is
~3 `world.part` calls ⇒ `build()` roughly **40–60% shorter**, ~150–250 fewer
output tokens/game and fewer chances to mis-set a density. Prompt input cost rises
~+380 tokens (B.3) but is prompt-cached across a batch, so amortized ≈ 0.

**When the bank pays off.** Fixed cost = authoring + bank-CI-certifying N parts
(one settle-grid pass each). Variable payoff = (repair iterations saved) × (cost
per iteration) + (reliability wins on otherwise-unsolvable joint prompts). With
~50% of repair iterations addressable at ~0.6 saved/game, a ~60-part bank
amortizes over roughly **30–50 generated games `[est.]`**, and pays off
*immediately* on any single joint/hazard-heavy prompt (those can currently fail to
converge at all — infinite marginal value). Recommendation: seed the bank with the
~12–15 parts the day-1/day-2 corpus already implies (B.6) — highest-leverage
first, joints and hazards before decor.

## B.6 Migration path

**v2.2 = bank ALONGSIDE free code (both allowed, additive, non-breaking).**

- Game module format (CONTRACTS §2) is UNCHANGED — same required symbols. `world.add`
  stays; `world.part` is purely additive. Free-code games verify byte-identically to
  today. No day-1/day-2 game breaks.
- `_SYSTEM_PROMPT` gains the Tier-1 block; `_first_user_msg` gains the retrieved
  Tier-1b menu; DESIGN gains the "Parts used" line.
- `gameverify` gains the B.4 G0/G1 part checks (guarded by "did this game call
  `world.part`?"). `integrity` gains bank hashing. `ledger` gains `bank_version`.
- Ownership stays within CONTRACTS §8 lanes: `world.py` (agent E) adds `part()`;
  `gameverify.py` (F) adds part checks; `gamegen.py` (G) adds prompt tiers +
  retriever; the bank JSON + `banks/` is orchestrator-owned data.

**Which day-2 / campaign games would have used parts** (the noun/verb split in
practice — CONTROLLED body + MECHANIC stay free; scenery/hazards/triggers come
from the bank):

| Game | From bank (nouns) | Stays free code (verbs / controlled body) |
|---|---|---|
| day2_wrecking | `wrecking_ball` (mobile), `crate`×3 (prop), `ledge`+`pit_zone` | pump impulses, fall-counting success — **the NaN vanishes** |
| day2_lander | `landing_pad` (terrain), `rock`×2 (hazard) | ship body, vacuum-drag + impact-speed mechanic, scoring |
| stack two crates | `crate`×2 (prop), `ledge` (terrain) | push/carry actions, stacking success |
| bounce ball → target | `wall`×N (terrain), `target_zone` (trigger), `ball` (prop) | launch/aim mechanic, bounce-count milestones |
| steer raft → dock | `river_bank` (terrain), `dock_zone` (trigger) | **current = a FORCE = mechanic, stays free**; raft is controlled |
| launch rocket → pad | `landing_pad` (terrain) | thrust mechanic; rocket is the controlled body |

Pattern: terrain/props/hazards/triggers are bank nouns; the CONTROLLED body and
every FORCE/gravity/drag/scoring rule stay free. Exactly the split that makes the
bank remove the physics-calibration failures without touching mechanic variety.

**Engine-portable schema (pymunk-first, but built to travel).** The entry's
`assembly`/`joints`/`overridable` are expressed in an **engine-neutral** vocabulary
(primitive shapes, joint types, physical props). Per-engine realization lives in
`engine_bindings`:

- `pymunk` (now) — maps to `world.add` + `pin/pivot/spring`; sprites ignored.
- `planck` / `matter` (rung-4 step 1, per GAME_ENGINE_INTEGRATIONS) — same JSON,
  Box2D bodies + joints; the loop stays at parity.
- `phaser` (visual demos) — the `sprite.ref` finally renders; the bank's cosmetic
  layer becomes real with zero change to the physics contract.

The behavior hooks (`hazard` contact-wiring, `mobile` moving-platform params) map
to each engine's contact/step API, so the same certified part drives pymunk today
and a real 2D engine later — one bank JSON, many engines. This keeps the bank
aligned with the recommendation ladder (stay pymunk for the campaign; Planck.js
next) instead of hard-coding it to one substrate.

---

## Appendix — open questions to resolve before implementation

1. **Retriever** — keyword vs embedding match for the themed subset; who owns it
   (gamegen) and its offline index build. Start with keyword match over
   `name`+`summary`+category (cheap, deterministic, no new dependency).
2. **Subassembly namespacing** — confirm `name.<role>` handles don't collide with
   the ≤14-body cap; a subassembly counts its sub-bodies toward the cap (a
   `wrecking_ball` = 2 bodies). The menu should show each part's body count.
3. **Override clamp vs reject** — clamp silently, or reject out-of-range as
   ENV_ERROR? Recommend **reject** (a clear hint teaches the model the bounds;
   silent clamps hide intent from the diversity log).
4. **Diversity descriptor weighting** — how much the part-multiset axis counts vs
   the mechanic axis in the effective-diversity metric (defer to that workstream).
5. **Bank governance** — admission criteria, versioning cadence, license audit for
   any non-hand-authored sprites (provenance field is mandatory now to keep the
   option open).
