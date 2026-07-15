---
id: hub
kind: hub
created_by: human-seed wave-1
run_id: reseed-2026-07-14
wave: 1
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
load_when: always — first read every design task; route from here, never load the whole library
rationale: The orchestrator hub for the reseeded designer skills library. Replaces the deleted INDEX.md router with a phase-keyed decision matrix so the frozen brain loads the ONE card a task needs, never the shelf. Selective loading is the hard invariant (BUDGETS.md retune); the caps are soft.
provenance: notes/engines/DESIGNER_AGENT_PLAN.md §4; notes/engines/EXAMPLES_STRUCTURE_GUIDE.md §1-2-5; godot-master orchestrator pattern — knowledge derived from thedivergentai/gd-agentic-skills (LGPLv3) + godotengine docs, paraphrased.
---

# Designer HUB — route by design phase, load one thing

You are designing a Godot 2D game as a **JSON spec** (`godotworld/SPEC.md`): the
model emits DATA, typed-state verifies (G0-G4), a witness replays byte-exact. This
hub routes you to the ONE skill a task needs. **Selective loading is the rule** —
never load the whole library; a card you don't need is context you can't afford.

## The routing matrix

| Design phase | The question you are answering | READ (load this) | Do NOT load |
|---|---|---|---|
| **Concept** | What game is this? which mechanic? | the ONE archetype card that fits + `universals.md` | any other archetype card; `dressing.md`; `certification.md` |
| **Layout** | Where do bodies go? how big is the arena? | `world-composition.md` (uses `inspect_world`) + `engine-truths.md` | archetype cards (concept is settled); `dressing.md` |
| **Predicates** | success / failure / checkpoints wording | `certification.md` + the predicate-grammar rows of `engine-truths.md` | `dressing.md`; `world-composition.md` |
| **Polish** | it works but looks samey | `dressing.md` | `certification.md`; archetype cards; `engine-truths.md` |

Move top-to-bottom; a phase is done when its gate is green. Do not skip to Polish
before Predicates certify — cosmetic DATA never fixes a failing predicate.

## Concept → pick ONE archetype card

Each card is a distinct differentiator family (the anti-sameness lever, §2 of the
examples guide). Choose the family whose **soul** matches the mechanic, then flip
the objective to stay clear of shipped specs.

| If the game's soul is… | Load | Family |
|---|---|---|
| steer a heading-controlled body and SETTLE on a pose | `steer-to-pose.md` | vehicle / heading-control |
| thread ordered gates or pickups, then finish | `gated-circuit.md` | vehicle + progression |
| gather scattered targets under a proximity sensor | `precision-collect.md` | perception-navigation |
| catch one falling body and route it by its label | `catch-and-route.md` | sorting / logistics |
| hop a grounded body across gaps to a landing pad | `hop-traverse.md` | precision platforming |
| outlast a rising line or a survival timer | `rising-hazard-survive.md` | timing / survival |
| one decisive strike, then freeze and hold | `one-shot-commit.md` | one-shot commit |
| shove a FREE body into a goal zone | `herd-to-zone.md` | herding (multi-body collapsed) |

**Cross-game variety mandate:** in a batch, your archetype, world orientation, and
palette must DIFFER from the previous games. Never re-emit numbered-ledges-plus-a-
goal-sensor — that is the documented attractor. If two consecutive concepts pick the
same card, change one.

## Positive routing lines (load these together)

- Any archetype card **always** pairs with `universals.md` — the 8 non-negotiables
  hold for every family.
- Writing predicates for a **park / settle / dock** goal → also read the stillness
  rows of `engine-truths.md`: a free body coasts forever, so a "come to rest" goal
  is unsatisfiable without a `velocity_clamp` or friction surface.
- Placing a rotatable body in a `contained()` goal → read the rotated-AABB row of
  `engine-truths.md` (the AABB inflates; containment reads false when geometrically
  inside).
- Adding a `sensors` fan → read `world-composition.md` (sensor-as-spine) plus the
  overlap-latency + proximity-convention rows of `engine-truths.md`.

## Negative routing lines (do NOT load X for Y)

- Do **NOT** load `dressing.md` while the spec still fails G0-G3 — polish is cosmetic
  DATA the verifier never runs; it cannot rescue a broken predicate or an escaping
  body. Certify first, dress last.
- Do **NOT** load a second archetype card to "blend" families — one game, one soul.
  Blending three archetypes is the classic incoherence failure; pick one, flip its
  objective, move on.
- Do **NOT** load `certification.md` during Concept — worrying about G4 shortcuts
  before the mechanic exists inverts the order and narrows you to an easily-certified
  band (the gate-gaming failure mode).
- Do **NOT** load `world-composition.md` for pure wording fixes — predicate grammar
  lives in `engine-truths.md` + `certification.md`, not in layout.
- Do **NOT** load the whole library "to be safe". If you loaded more than three
  skills for one task, you routed wrong — come back here and pick again.

## The whole library (one line each)

- `universals.md` — the 8 ingredients every good RL game shares.
- `engine-truths.md` — gotchas the spec must respect (stillness, overlap latency,
  predicate grammar, gravity sign, tunneling).
- `certification.md` — what G0-G4 punish, as design guidance.
- `world-composition.md` — arena layout, spatial contrast, `inspect_world`.
- `dressing.md` — cosmetic DATA: palette, parallax, decor, skinnable names, juice.
- 8 archetype cards (tables above) — core loop + skill-chain + DSL, per family.

*Each skill's own frontmatter carries its provenance. Quarried material is knowledge
derived from thedivergentai/gd-agentic-skills (LGPLv3) + godotengine docs,
paraphrased — no files copied.*
