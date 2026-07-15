# Why the 3D demo was easy and the designer struggles — the four asymmetries

> 2026-07-15, commissioned by Elias after PROOF3D: "How did you write this
> demo so easily but the agent struggles so much with the harness?" Honest
> answer: the demo and the designer are playing different games, stacked
> four asymmetries deep. Naming them tells us exactly which levers matter.

## The four asymmetries

| axis | me, writing PROOF3D | the designer (deepseek in the harness) |
|---|---|---|
| **Medium** | GDScript + full engine API — a language with millions of training examples, Turing-complete, self-instrumentable | a bespoke JSON DSL that exists in ZERO training data, defined by one prompt page, forbidden from code |
| **Feedback** | print anything, any tick; run→read→fix loop in minutes; I chose my own instrumentation (velocity, sleeping, drop-test) | ONE typed hint sentence per failed attempt, 5 attempts, no instrumentation, no questions, no world-probing |
| **Acceptance bar** | "reached the goal once on my machine" — no witness replay, no determinism proof, no adversarial attack, no budgeted-solver solvability | provably solvable by a BUDGETED random-tail searcher + deterministic + G4-hardened. The slalom was humanly drivable at iter-2; it failed because OUR solver couldn't drive at 21k ticks. The certifier's search competence caps the expressible design space |
| **Agency** | frontier model WITH tools, iteration, hypothesis-testing (the set_active fix came from a discriminating experiment, not knowledge) | small fast model doing ONE-SHOT emission — no tools, no iteration inside an attempt, no experiments |

The brutal summary: we gave the **small model** the **novel medium**, the
**thin feedback**, and the **hard grader** — and gave the frontier model the
easy configuration. Tonight tested the weakest possible corner of the
design space and drew conclusions about the whole space.

## What this does NOT say

It does not say JSON specs can't work. The certified slalom, the G4 catch
(decorative gates), the revise fix, and the budget experiment all worked
THROUGH the spec lane. It says: one-shot small-model emission into a novel
DSL against a hard certifier, without agency, is the configuration that
struggles — and it is the only configuration we have run so far.

## The levers, one per asymmetry

1. **Agency (biggest, already planned):** the P2 designer agent IS the fix —
   tools (`inspect_world`, serve-probes, `certify(depth=verify)` as a cheap
   self-check), iteration inside a design session, memory. Tonight I was
   effectively a hand-operated P2: same loop shape the plan gives the agent.
2. **Feedback:** richer typed feedback even for the one-shot lane — verify
   hints already improved (iter hints were genuinely diagnostic); add
   inspect_world warnings into the repair context so the model sees layout
   problems before the funnel does.
3. **Acceptance:** the solver must scale with design sophistication or it
   silently taxes creativity (budget x3 fixed the slalom; steering-aware
   search / RL-assisted solving are next). Track: solver competence is part
   of the product, not fixed infrastructure.
4. **Medium:** keep the DSL (the moat) but close the training-distribution
   gap: examples-free reference prompt (rewrite landed from the swarm),
   vocabulary that matches how models think about games (view/topdown,
   heading verbs — both landed today), and optionally a TRANSPILE lane
   (a stronger code-model writes constrained code that compiles to spec)
   as an experiment — expressiveness of code, certificate of data.

## Implication for the godogen/gd-agentic question

The head-to-head stands, but this analysis predicts its outcome shape:
lane B (runtime code) buys asymmetries 1+4 instantly (familiar medium,
code-native models) and pays with the certificate. Lane A closes the same
asymmetries with P2 agency + the rewrite, keeping the certificate. The fair
comparison is lane B vs lane A WITH P2 — not vs tonight's one-shot corner.
