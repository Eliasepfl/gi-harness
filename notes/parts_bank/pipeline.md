# Parts Bank — Stage-1 pipeline design (two-stage generation)

> Status: DESIGN ONLY (2026-07-13). Sibling study to `notes/parts_bank/design.md`
> (the bank schema / `world.part()` / verification). This file covers ONE slice:
> the **PIPELINE** that puts pre-certified parts in front of the generator, and
> the **prior art** that pins the design to what has actually worked. It does NOT
> pick a retriever (embedding vs BM25 model benchmarking is a sibling agent's
> slice); it decides *where stage-1 lives*, *what it emits*, *how it enters
> stage-2*, and *how it fails safe*. When ratified, PART C becomes the normative
> stage-1 addition to CONTRACTS §9 alongside design.md's §9.
>
> Every prior-art claim below was verified at its source this session (arXiv
> papers, the GenSim source code, and the loaded Anthropic tool-use reference);
> the "Sources" section lists what was read.

---

## 0. Thesis

The user's proposal is a **two-stage** pipeline: **Stage 1** selects the most
appropriate parts from the pre-certified bank *from the prompt alone* (physics
arrives WITH the parts, pre-given); **Stage 2** generates the whole game on that
"more solid and coherent base." This is the standard shape of every scaled
code/task generator in the literature — GenSim, ToolLLM, Gorilla, RAG-for-codegen,
and Anthropic's own tool-search all separate **"choose the vocabulary"** from
**"compose with it."** The design question is not *whether* to split (the split is
proven) but *where the split lives*: a pure harness retrieval step (A), a small
dedicated "kit-picker" LLM call (B), or an in-call tool the generator invokes
mid-flight (C). The recommendation is **A for v2.2** (deterministic, ~0 extra
tokens, one generation call, drop-in to the existing `_repair_loop`), measure the
repair-rate delta on the A/B-with-bank campaign, then **graduate to B for v2.3
only if the miss telemetry says coherence — not physics — is still the bottleneck.**

The single most important source-verified correction to design.md: **"GenSim-style
retrieval" is not keyword retrieval.** GenSim's *exploratory* mode injects a
**random** sample of prior (description, code) pairs as few-shot exemplars, and its
*goal-directed* mode uses **cached embedding similarity** to a target task. The
themed-keyword menu design.md proposes is actually closer to **ToolLLM's dense API
retriever** than to GenSim. This matters for how we frame the retriever and what we
log (§A.1, §A.2).

---

# PART A — Prior art (source-verified)

## A.1 GenSim's retrieval-augmented generation, read from the source

GenSim (Wang et al., *Generating Robotic Simulation Tasks via Large Language
Models*, ICLR 2024, arXiv 2310.01361) is the paper the project already references.
It maintains a **task library** (a "base task library" shipped on disk plus a
"generated task library" grown online) and composes new tasks over the **fixed
Ravens/CLIPort asset pool** — a bounded vocabulary of named objects (blocks, bowls,
pallets, bins, letters, containers, ...) that tasks reference **by name**. It runs
in two modes ("bottom-up"/exploratory and "top-down"/goal-directed). What was
unclear from the abstract — *how exactly* reference tasks reach the prompt — I
resolved by reading the repository code (`gensim/agent.py`, `gensim/memory.py`):

- **The library is a memory buffer of (description, code) pairs.** `memory.py`
  keeps `online_task_buffer` (descriptions) and `online_code_buffer` (the task's
  generated Python), loads a base library from disk, and **caches precomputed
  task-code embeddings** (`self.task_code_embedding = np.load(cache_embedding_path)`).
- **Exploratory generation retrieves by RANDOM sampling, not similarity.** The
  prompt is filled from the buffer via
  `format_dict_prompt(self.memory.online_task_buffer, self.cfg['task_description_candidate_num'])`,
  and past tasks are drawn with `random.sample(total_tasks.items(), MAX_NUM)` under
  `MAX_NUM = 10`. So the few-shot context is **up to ~10 randomly-drawn prior task
  descriptions**, with `task_description_candidate_num` (the CLI examples use `=10`)
  as the knob. Selected **code** is inlined as fenced blocks:
  `task_code_reference_replace_prompt += f'```\n{self.memory.online_code_buffer[key]}\n```\n\n'`.
  Diversity is bought by the *random draw over a broad library*, not by matching.
- **Goal-directed generation uses the cached embeddings** for similarity toward a
  named target task (curriculum), which is where the `task_code_embedding` cache is
  consumed. A separate `task_asset_candidate_num` controls how many **asset** names
  are offered.
- **What enters the prompt:** a set of prior **task descriptions**, a subset of
  their **full code** as few-shot exemplars, and a list of **available asset
  names** — never the whole library, and never raw physics parameters (the assets
  already carry their geometry/behavior in the CLIPort environment).

**Takeaways for us.** (1) The pattern that scaled GenSim 10× (to 100+ tasks, +25%
sim-to-real over baselines) is *inject a bounded, named vocabulary + a handful of
prior exemplars, and keep the whole library out of context.* That is exactly the
two-tier injection design.md already proposes (+~380 tokens vs +~3600 for a full
catalog). (2) GenSim's own retrieval is **random for diversity / embedding for
goal-direction** — so if we want *coherence* (relevance to the prompt) we are past
GenSim and into the tool-retrieval literature (§A.2), and we should label our menu
as a **relevance retrieval** (ToolLLM-shaped), reserving "GenSim-style" for the
*keep-it-a-subset* principle, not the selection mechanism. (3) GenSim proves parts
should be **referenced by name over a fixed pool** — which is precisely
`world.part("wrecking_ball", ...)`.

## A.2 Tool-retrieval literature — the same problem shape

Selecting a small relevant subset from a large vocabulary before generation is the
**tool-retrieval** problem, and there is a mature line of work with the exact
mechanics we need.

- **ToolLLM / ToolBench** (Qin et al., arXiv 2307.16789, ICLR 2024; 16,000+ real
  APIs). The **API Retriever** is a **Sentence-BERT dense retriever** over
  BERT-BASE: it encodes the instruction and each API document into embeddings and
  **selects the top-k tools by embedding cosine similarity**. It is trained
  contrastively (relevant APIs = positives, sampled others = negatives); reported
  baselines are **BM25** and **text-embedding-ada-002**. At inference the retriever
  recommends APIs to ToolLLaMA, which then composes multi-round calls. This is the
  canonical **Option A** shape: retrieval is an *external, deterministic step that
  runs before generation*, and the generator only ever sees the top-k subset.
- **Gorilla** (Patil et al., NeurIPS 2024). Each API is a **document**; a retriever
  (**BM25** or GPT-Index) fetches relevant API docs and **appends them to the
  prompt at both training and inference** ("Retriever-Aware Training"). APIBench =
  ~1,600 APIs (95 TorchHub, 696 TensorHub, 925 HuggingFace) with AST sub-tree
  matching for correctness. The load-bearing result: retrieve-and-generate **lets
  the model adapt to test-time documentation changes** (new/renamed signatures) and
  **substantially reduces hallucination** vs prompting the model bare. This is our
  argument for pinning `bank_version` and letting the bank evolve: the *retrieval
  step, not the model weights, carries the current vocabulary.*
- **Anthropic tool-search — the same shape at the API layer, which we are using
  right now.** The Claude API exposes `tool_search_tool_bm25_20251119` and
  `tool_search_tool_regex_20251119`: most tools are marked `defer_loading: true`
  (surfaced by *name only*, no schema in context), and the model loads a tool's
  full schema **on demand** via search. Two properties matter for us: (i)
  **discovered schemas are APPENDED, not swapped** — this preserves the prompt
  cache, the exact reason design.md's Tier-1 legend + Tier-2-on-demand split is
  cache-friendly; (ii) there is a hard floor — the search tool itself must not be
  deferred and **at least one tool must stay non-deferred** (else `400: All tools
  have defer_loading set`). This session is literally running that pattern
  (deferred tools + `ToolSearch`), which is the concrete analogue of our
  **Tier-1 legend (always present) + Tier-1b themed menu (retrieved) + Tier-2 full
  schema (on demand)**. The "at least one non-deferred" floor maps directly to our
  **"the category legend is always in the system prompt"** rule — the generator is
  never left with nothing.

**Takeaway.** The retrieved-subset pattern is not exotic; it is the industry
default for "large vocabulary, few relevant per request." Options A and C are just
*where* the retriever sits: A = external/before (ToolLLM/Gorilla), C = in-call
(Anthropic tool-search / ToolBench multi-round). The determinism and repair-loop
consequences of that placement are the whole decision (PART B).

## A.3 RAG for code generation — established gains AND failure modes

Retrieving API docs / snippets into a codegen prompt is well studied, and the
failure modes are the reason we must design guards (PART D), not just a happy path.

- **Gains are real but conditional.** *CodeRAG-Bench* (Wang et al., arXiv
  2406.14497) shows retrieval augments code generation when the retrieved context
  is *relevant and canonical*; *On Mitigating Code LLM Hallucinations with API
  Documentation* (arXiv 2407.09726) shows doc-grounding cuts API hallucination —
  the same effect Gorilla reports. Our physics-calibration failures are the
  analogue of API hallucination: a `wrecking_ball` part is "canonical
  documentation" the generator can't mis-derive.
- **Over-anchoring / distraction is the headline risk.** *When Retrieval Hurts
  Code Completion: A Diagnostic Study of Stale Repository Context* (arXiv
  2605.14478) and the environment-aware-codegen literature find: models
  **over-rely on partially-relevant retrieved context**, get **distracted by
  additional context and fail the actual task**, and retrieval can **introduce new
  failure modes (indentation, type errors) that are rare without it**. Standard
  retrieval carries **high-noise distractors** (distinct functions with identical
  names, deprecated utilities).
- **The anchor finding is the guard we exploit.** The diagnostic study's key
  observation: *"When prompts explicitly name the target API or intended state,
  strong models treat retrieved distractors as advisory and follow the explicit
  anchor; when the prompt removes that anchor, the retrieved snippets become the
  model's only source of evidence."* **Our prompt always carries a strong anchor**
  — the user's game prompt plus the "invent a mechanic, do NOT default to a
  platformer" instruction. That is exactly the regime in which strong models treat
  a retrieved menu as *advisory*. So the over-anchoring risk is manageable **iff we
  keep the prompt anchor dominant and explicitly label the menu optional** (PART D).
  If we ever weakened the prompt to "just use these parts," we would flip into the
  regime where the menu becomes the only evidence and distraction dominates.

## A.4 PCG selecting from an element vocabulary conditioned on a description

The design-pattern / vocabulary side (Angry-Birds stability oracle, PuzzleScript
OBJECTS/LEGEND, Dahlskog–Togelius patterns) is already covered in design.md §A.1.
The **description-conditioned** selection — the part specific to a *two-stage
prompt→vocabulary* pipeline — has recent, directly-relevant work:

- **Narrative-to-Scene Generation** (arXiv 2509.04481) builds a **semantic
  embedding index of annotated game tiles** (each tile encoded with the
  `all-MiniLM-L6-v2` Sentence-Transformer), then at generation time **embeds the
  narrative entities and matches them to tiles by cosine similarity** over *name,
  category, and affordance*, before rule-based spatial placement (cellular-automata
  terrain first). This is *precisely* the two-stage pattern the user proposes — a
  description drives retrieval of a **named element from a fixed, annotated
  vocabulary**, then a second stage composes them — and it validates the
  "name + category + one-line summary" fields our Tier-1b menu already carries.
- **GameTileNet** (arXiv 2507.02941) is a semantic dataset built to *label* game
  art for exactly this kind of description-conditioned PCG retrieval — external
  evidence that "assets need a semantic key to be retrievable," which is our
  `summary`+`category` metadata (physics is separate — design.md's core finding).

**Takeaway.** Description-conditioned vocabulary selection is an active, working
PCG pattern; it uses **cosine over name/category/affordance embeddings** and a
**thin second-stage composer** — a near-exact structural match to our Option A +
`world.part` composition, and independent confirmation that the retrievable half of
a part is its *semantic key*, cosmetically decoupled from physics.

## A.5 What the cross-cut dictates for the pipeline

| Source-verified principle | Where it lands in our design |
|---|---|
| Retrieve a **bounded subset**, keep the full vocabulary out of context (GenSim, ToolLLM, tool-search's `defer_loading`) | Tier-1 legend + Tier-1b themed menu (~8–15 parts), never the full ~60-part catalog (+~380 tokens, not +~3600) |
| Relevance retrieval is **dense top-k or BM25**, run **externally before generation** (ToolLLM, Gorilla) | Option A: harness-side deterministic retrieval, single generation call |
| **Discovered/retrieved items append, never swap** — preserves cache (Anthropic tool-search) | On repair, *append* a re-retrieval, never rebuild the menu (§B.3) |
| **Always leave one non-deferred anchor** (tool-search 400 floor) | Category legend is always in the system prompt; menu can fall back to legend-only |
| Retrieval **reduces hallucination when docs are canonical** (Gorilla, 2407.09726) | Pre-certified parts eliminate the physics-calibration failure class by construction |
| **Over-anchoring/distraction** is the failure; a **strong prompt anchor makes retrieved items advisory** (2605.14478) | Keep the game prompt dominant; label the menu "optional"; keep `world.add` escape hatch (PART D) |
| Description→named-element selection is **cosine over name/category/affordance** + thin composer (2509.04481) | Menu fields = name + category + one-line summary + override ranges; `world.part` is the thin composer |

---

# PART B — Architecture decision: where does stage-1 live?

## B.1 The three options

- **(A) Harness-side retrieval only — no extra LLM call.** The harness embeds/BM25s
  the prompt against the pinned bank index, selects the top-k themed menu, inlines
  it into the first user message, and makes **one** generation call. Maps to
  ToolLLM/Gorilla (external retriever) and to design.md's current plan.
- **(B) Dedicated small stage-1 LLM call — "game designer picks the kit."** A cheap
  first call takes the prompt and emits a **parts manifest** (names + why) plus a
  2-line concept; stage-2 generates with that manifest **pinned** into its prompt.
  Maps to GenSim's goal-directed/top-down mode (LLM proposes a curriculum) and to
  planner→executor agent decomposition. The manifest becomes a machine-readable
  artifact, sibling to the DESIGN block.
- **(C) In-call tool use — the generator calls `search_parts` mid-generation.** The
  generation model itself queries the bank while writing, tool-loop style. Maps to
  Anthropic tool-search / ToolBench multi-round. Interactive, but non-deterministic
  ordering, and it breaks the single-shot `_repair_loop`.

## B.2 Assessment against the hard criteria

Numbers from design.md B.3/B.5 and the current `gamegen._SYSTEM_PROMPT`
(~7060 chars ≈ 1765 tokens). "Determinism" here is the retrieval + telemetry
reproducibility layer — note the *generator itself is already stochastic* (an LLM
call with adaptive thinking); the OBJECTIVES/CONTRACTS **hard** determinism
requirement is on the **physics/verifier** (seeded rng, G1 two-run snapshot
equality, integrity manifest), which none of A/B/C touch. What varies across A/B/C
is whether the *retrieved set* is a reproducible function of `(prompt, bank_version)`.

| Criterion | (A) harness retrieval | (B) stage-1 LLM | (C) in-call tool |
|---|---|---|---|
| **Retrieval determinism** (reproducible retrieved set per prompt+bank_version) | **Pure function** — BM25/embedding over a pinned index; fully reproducible, A/B-fair | Stochastic (2nd LLM call), but the manifest is **produced once, pinned, logged** → the *run* is reproducible from the logged manifest | **Worst** — retrieved set depends on the model's mid-gen queries; differs per attempt; not reproducible |
| **Token cost / run** | +~380 input tokens (Tier-1 ~180 + Tier-1b ~200), prompt-cached across a batch → amortized ≈ 0; generated `build()` *shrinks* (~150–250 fewer output tokens) | A's cost **plus one extra call**: input ~ system+prompt+catalog-slice, output ~ manifest (~100–200 tok) ≈ a *fraction of one repair iteration* (repair iter ≈ 2–4k tok). Can run on a cheaper model / lower effort | A's cost **plus** N tool round-trips inside generation (each a full re-send of the growing context) — the most expensive |
| **Repair-loop interaction** | **Drop-in.** `_repair_loop` is unchanged: `produce(feedback)` still returns `(code, design)` from one call. Menu is pinned for the run (§B.3) | Needs a stage-1 call before the loop; the loop is otherwise unchanged. Manifest pinned for the run | **Breaks the single-shot loop.** Generation becomes a multi-turn tool conversation; `_extract_code`/`_extract_design` assume one assistant message; menu changes per attempt |
| **Telemetry** (ledger) | Clean: `retrieved_set`, `parts_used`, `misses`, `bank_version`, `retriever` are all harness-owned facts | Richest: also logs the **manifest** (names+why+budget) and manifest-vs-used compliance as a gate | Muddy: retrieved set is buried in tool-call traffic; per-attempt variance confounds `game stats` |
| **Bank-growth signal** | Strong — escape-hatch `world.add` + retrieval misses + legend-only fallbacks are all cleanly attributable | Strong + the manifest exposes *intended* parts the bank lacked (planning-time demand) | Weak — demand is scattered across tool queries |
| **Coherence gain** (the user's hoped-for "solid base") | Menu constrains nouns; the model still self-selects → some mis-selection expected | **Highest** — a committed manifest + concept pins a coherent kit before a line of code | High, but bought at determinism/telemetry cost B captures more cheaply |
| **Implementation blast radius** | `gamegen.py` only (retriever + Tier-1b), guarded so free-code path is byte-identical | `gamegen.py` + a new stage-1 prompt/call | `gamegen.py` architecture change (tool loop) + verifier assumptions |

## B.3 Repair-loop interaction — the one subtle design point

**On repair, does the menu change? Default NO — pin the retrieved set for the whole
run.** Rationale, all three ledger-motivated: (1) reproducibility — the ledger's
`retrieved_set` must be a stable fact for a `(prompt, bank_version)` so A/B numbers
are fair; (2) stability — the RAG literature (§A.3) shows a *moving* retrieved
context destabilizes the model; a repair should change the *code*, not the world it
draws from; (3) telemetry — a per-attempt menu confounds "did the bank help?".

**The single designed exception: one "missing-kind" re-retrieval.** If a verifier
hint names a part-kind the game needed but the menu didn't surface — concretely:
the model's DESIGN "Parts used" or a G0 `unknown part` / escape-hatch usage shows it
reached for a category the bank *covers* but the themed menu *omitted* — allow
**exactly one** re-retrieval that **APPENDS** parts of the named kinds to the menu
(never rebuilds it — mirroring Anthropic tool-search's "append, don't swap," so the
prompt-cached prefix survives). Cap: **one re-retrieval per run**, logged as a
distinct ledger field (`re_retrieval: {trigger, added_kinds, added_parts}`). This
keeps determinism (the trigger is itself a deterministic function of the verifier
report) and gives the model a second, bounded chance to find the right noun rather
than hand-building it and re-exploding.

## B.4 Telemetry — ledger fields the pipeline adds

Extend each `runs/ledger.jsonl` line (on top of design.md's `bank_version`):

```jsonc
"pipeline": {
  "retriever": "bm25|embedding|keyword",        // which retriever ran (sibling agent owns the choice)
  "retrieved_set": [ {"kind":"wrecking_ball","category":"mobile","score":0.71},
                     {"kind":"ledge","category":"terrain","score":0.55}, ... ],  // the pinned Tier-1b menu
  "menu_mode": "themed|legend_only",            // legend_only = score threshold fallback (§D.2)
  "re_retrieval": null | {"trigger_hint":"...", "added_kinds":["hazard"], "added_parts":["spike_strip"]},
  "parts_used":  [ {"kind":"wrecking_ball","in_menu":true,  "overrides":{"chain":250}},
                   {"kind":"pit_zone",     "in_menu":true} ],       // from DESIGN "Parts used" ∩ G0-recorded world.part calls
  "escape_hatch": [ {"role":"custom_spring_trap","looks_like_category":"mobile"} ], // world.add calls that resemble a would-be part
  "misses": { "out_of_menu_used": ["seesaw"],   // real bank parts the model used that retrieval didn't surface -> retriever recall miss
              "unknown_requested": [] }          // names the model named that don't exist -> hallucination, already an ENV_ERROR
}
```

`harness game stats` then answers: does menu presence lower repairs? which
retriever recalls best (`out_of_menu_used` rate)? which categories drive
`escape_hatch` (bank-coverage gaps)?

## B.5 Bank-growth feedback artifact — closing the loop

Escape-hatch usage + retrieval misses + legend-only fallbacks are a **demand
signal for new parts**. Add `harness game bank-demand` that aggregates the ledger
across a batch into a ranked authoring queue:

- **Hand-built subassembly demand** — `escape_hatch` entries whose `world.add` +
  joint pattern resembles a would-be `mobile`/`hazard` part (the highest-leverage
  candidates, since these are the NaN-prone hand-built joints — exactly the
  wrecking-ball class). One `random.sample`-free, deterministic heuristic: cluster
  escape-hatch entries by (shape set, joint types) and rank by frequency × mean
  repair cost of the runs they appear in.
- **Recall misses** — high `out_of_menu_used` for a kind ⇒ the *retriever* needs
  that kind's keywords/synonyms, not a new part.
- **Coverage gaps** — prompts that fell to `legend_only` (no confident match) and
  then used `world.add` heavily ⇒ a *theme* the bank doesn't cover yet.

This makes the bank grow toward **observed demand**, not speculation — the concrete
mechanism behind design.md B.5's "seed the bank with the ~12–15 parts the corpus
already implies," and it feeds the effective-semantic-diversity workstream with the
part-multiset per game.

## B.6 Recommendation

**Adopt (A) for v2.2. Keep (B) as the pre-designed v2.3 upgrade. Defer/reject (C).**

- **(A) now** because it is the *only* option that is a pure-function retrieval
  step (fair A/B, clean telemetry), costs ~0 amortized tokens, and is a **drop-in**
  to `_repair_loop` — `produce(feedback)` keeps returning `(code, design)` from one
  call; the free-code path stays byte-identical (guarded on "did retrieval run?").
  It is the ToolLLM/Gorilla-proven placement.
- **(B) as a gated upgrade** because its coherence gain (a committed manifest +
  concept before any code) is real (GenSim goal-directed, planner→executor) but
  only worth an extra call **if the data says coherence is the bottleneck** — i.e.
  the A campaign shows persistent repairs on multi-part/joint-heavy prompts *and*
  the `misses`/`parts_used` telemetry shows the model **mis-selecting from the
  menu** (picking wrong parts, ignoring relevant ones) rather than mis-calibrating
  physics. If instead A already kills the physics class and residual repairs are
  verb mistakes (dead `wait` actions — the other 50% of the ledger), B adds cost
  for nothing; fix those in the prompt-rules harvest.
- **(C) deferred** because it trades determinism, clean telemetry, and the
  single-shot repair loop for a coherence gain B buys more cheaply. Revisit only at
  rung-4 (real engines) if the bank outgrows any static menu — the one regime where
  in-call search earns its cost.

---

# PART C — Stage-1 output contract

## C.1 The manifest / retrieved menu

In **Option A** the "manifest" IS the retrieved Tier-1b menu — a harness-produced,
ledger-logged artifact (no LLM in the loop). Format (compact, one line per part):

```
Parts available for this prompt (pick by name, or ignore and use world.add):
  wrecking_ball (mobile) — heavy ball on a rigid chain; swings as a pendulum
    overrides: mass 2–40, chain 120–320, scale 0.6–1.6   [bodies: 2]
  ledge (terrain) — static rectangular shelf; size/pos overridable            [bodies: 1]
  pit_zone (trigger) — sensor rectangle; fires a contact flag when a body enters [bodies: 1]
  crate (prop) — light stackable box; mass 0.2–2, size 20–60                  [bodies: 1]
  ... (~8–15 lines, selected by the retriever, score ≥ threshold)
```

Fields per part: `name`, `category`, one-line `summary`, bounded `overrides`,
`[bodies: n]` (so the model can respect the ≤14-body cap — design.md open Q2).
This is enough to *choose*; full JSON (`overridable` paths, sub-body roles) is
Tier-2, expanded inline only when the model needs a non-primary sub-body.

In **Option B** the manifest is richer and LLM-authored: `names + why (1 clause
each) + a count budget + a 2-line concept`, e.g.:

```
KIT
Concept: a pendulum smashes a crate stack off a ledge into a pit.
Parts: wrecking_ball×1 (the smasher), crate×3 (the stack), ledge×1 (the perch),
       pit_zone×1 (the goal). Budget: ≤6 bank bodies, ≤14 total.
```

The manifest is pinned into stage-2 and logged; stage-2's DESIGN "Parts used" must
be ⊆ manifest (a **gate** in B; advisory in A — see C.3).

## C.2 How it enters stage-2

Unchanged from design.md B.3, with the pipeline made explicit:

- **Tier-1 (always, system prompt, +~180 tok):** `world.part(...)` signature +
  the noun/verb rule + the 6-category legend. **This is the always-present anchor**
  (the tool-search "≥1 non-deferred" floor): even with no menu, the model knows the
  categories and that `world.add` is the escape hatch.
- **Tier-1b (retrieved, first user message, +~150–250 tok):** the C.1 menu, or
  **omitted** (legend-only) when no part clears the score threshold (§D.2).
- **DESIGN block gains one machine-readable line** (already in design.md):
  `Parts used: wrecking_ball(chain=250, mass=10) as "wrecker"; pit_zone as "pit"`.
  This is the model's usage *declaration* — the bridge between the retrieved menu
  and G0 verification.

Ordering for prompt-cache: Tier-1 lives in the frozen system prompt (stable
prefix); Tier-1b and the game prompt are the volatile suffix — so a batch shares
the Tier-1 + `_SYSTEM_PROMPT` cache prefix and only pays fresh tokens for the menu
(design.md's "amortized ≈ 0").

## C.3 G0 manifest-compliance verification

The **hard gate stays what design.md B.4 already specifies**: every
`world.part(kind, ...)` recorded during `build` must exist in the *pinned bank
version* and every override must be in range (else `ENV_ERROR` with a precise
hint). Manifest compliance layers on top as **telemetry, not a gate, in Option A**,
and as a **gate in Option B**:

- **Used ⊆ bank ∪ escape-hatch (HARD, both options).** `world.part` kinds must be
  real certified parts; `world.add` is always allowed and always logged as
  escape-hatch. This is the "the menu is optional" guarantee — the model is never
  *forced* to stay inside the retrieved set.
- **Used ⊆ menu (SOFT in A / HARD in B).** In A, a used part that was *not* in the
  retrieved menu is **not an error** — it is a certified part the model recalled
  from the legend/experience — but it IS logged (`misses.out_of_menu_used`) as a
  retriever recall miss. In B, the manifest is a commitment the *same model* made,
  so stage-2 using a part not in its own manifest is a `GOAL_ERROR`-class
  inconsistency worth flagging (the model contradicted its own plan).
- **DESIGN "Parts used" ↔ recorded `world.part` calls must agree (SOFT, both).**
  A mismatch (declared a part it never instantiated, or vice-versa) is a
  non-fatal `warnings` entry — the same treatment G3 gives declared-vs-empirical
  checkpoint-order mismatches. It keeps the machine-readable line honest for the
  diversity logger without failing an otherwise-valid game.

Net rule, stated once: **the game may use `⊆ manifest ∪ bank ∪ escape-hatch`; every
escape-hatch and every out-of-menu use is logged, never (in A) rejected.**

---

# PART D — Failure modes & guards

## D.1 Over-anchoring (the primary risk)

**Symptom:** the model builds *only* with retrieved parts even when the prompt
needs none — force-fitting a `wrecking_ball` into a prompt about soap bubbles
because it was on the menu. This is the RAG-for-codegen "distraction / over-reliance
on partially-relevant context" failure (§A.3), and it is the exact way a parts bank
could *lower* originality (design.md's PCG-sameness caveat, made concrete at the
pipeline layer).

**Guards (all three, layered):**
1. **Keep `world.add` as a first-class escape hatch** — never removed, always in
   Tier-1. The model always has a way out of the menu.
2. **Explicit optionality line in the prompt:** *"The parts menu is a convenience,
   not a requirement. If this prompt is better served by custom bodies, use
   `world.add` freely and ignore the menu — do not force-fit menu parts."* This is
   not decoration: §A.3's diagnostic study shows that **with a strong instructional
   anchor present, strong models treat retrieved items as advisory**. Our anchor
   (the game prompt + "invent a mechanic") is strong; the optionality line makes
   the menu explicitly advisory, putting us in the safe regime by construction.
3. **Never weaken the game-prompt anchor.** The failure regime in §A.3 is the one
   where the prompt *removes* the anchor and retrieval becomes the only evidence.
   So the pipeline must never degrade the user prompt into "just assemble these" —
   the menu is always *in addition to* a dominant, open-ended game prompt.

## D.2 Retrieval junk → score-threshold fallback

**Symptom:** for an exotic/off-theme prompt the retriever returns low-similarity
"distractors" (the §A.3 identical-name / deprecated-utility noise), and injecting
them *invites* the over-anchoring of D.1.

**Guard:** a **score threshold τ**. If the top match's score < τ, **omit Tier-1b
entirely** and fall back to **legend-only** (Tier-1's 6-category legend + the
escape hatch). The model then sees *no* themed parts and composes freely with
`world.add`. Log `menu_mode: "legend_only"`. This is the tool-search
"≥1 non-deferred anchor" floor doing real work: the fallback is never *nothing*,
it is *the legend*. (τ is a tuning constant `[eng.]`; the sibling retriever agent
calibrates it. Recommend erring high — a missed relevant part costs one
`out_of_menu_used` telemetry entry; an injected junk part risks a distracted run.)

## D.3 Exotic-prompt coverage test set

To *measure* D.1/D.2 rather than hope, ship a small held-out **exotic-prompt set**
— prompts deliberately far from the bank's day-1/day-2 themes, e.g.:

- "a game where magnetism reverses every 3 seconds"
- "a soap bubble that grows until it pops, and you steer the growth"
- "juggle three balls whose gravity flips when they touch the ceiling"
- "a rope you cut segment-by-segment to drop a weight onto a switch"

The set is the guard-rail metric for the A/B-with-bank campaign. It must show:
(a) **retrieval falls to `legend_only`** (no forced junk) on the truly off-theme
ones; (b) **escape-hatch usage rises** appropriately (the model builds custom, as
it should); (c) **completion rate does NOT regress** versus the frozen free-code
baseline on these prompts. If (c) fails, the bank is *lowering the ceiling* on
exotic prompts — raise τ and/or strengthen the optionality line before shipping.
This directly answers the user's belief that "prompt exoticism gets carried by the
bank through retrieval": for genuinely exotic prompts the correct behavior is the
bank **stepping aside**, not stretching to cover — and the test set proves it does.

---

# PART E — Recommendation & sequencing

**Recommended option: (A) — harness-side retrieval, single generation call — for
v2.2; (B) as the data-gated v2.3 upgrade; (C) deferred to rung-4.**

**v2.2 (minimal, ship with the bank):**
1. Harness-side deterministic retrieval over the pinned bank index. Start with
   **keyword/BM25** (design.md Appendix Q1: cheap, deterministic, no new
   dependency; the embedding-vs-keyword choice is the sibling agent's slice — this
   design is retriever-agnostic and only requires that retrieval be a *pure
   function* of `(prompt, bank_version)`).
2. Tier-1 legend always in `_SYSTEM_PROMPT`; Tier-1b themed menu when top score ≥ τ,
   else legend-only. **Menu pinned for the whole run**; exactly **one** append-only
   "missing-kind" re-retrieval on a qualifying repair hint (§B.3).
3. DESIGN "Parts used" line; G0 hard gate = `used ⊆ bank`; menu compliance +
   escape-hatch as **telemetry** (§C.3).
4. Ledger `pipeline` block (§B.4); `harness game bank-demand` (§B.5).
5. Exotic-prompt coverage test set wired into the campaign harness (§D.3).

**Measure (the A/B-with-bank campaign, design.md verdict step 2):** re-run the same
prompt set (a) free-code baseline vs (b) bank+pipeline, on the same backend/seeds.
Primary metric: **repair iterations to COMPLETED**, sliced by failure class. The
bank should erase the ~50% physics/noun class (the NaN-from-joint iterations,
incl. the never-solved wrecking ball); the ~50% verb class (dead `wait`) is
untouched and stays a prompt-rules-harvest job. Secondary: exotic-set
non-regression (§D.3); `out_of_menu_used` (retriever recall); `escape_hatch`
category histogram (bank-demand).

**v2.3 (gated on the data):** if residual repairs concentrate on
multi-part/joint-heavy prompts AND `misses`/`parts_used` telemetry shows *menu
mis-selection* (not physics, not verb mistakes), add **(B)**: a small stage-1
"kit-picker" call emitting a pinned, logged manifest + 2-line concept, with G0
manifest-compliance promoted to a gate. Budget it against the measured repair
savings (B is worth it only if it saves ≳0.2 repair iterations/game or unlocks
otherwise-non-converging prompts — the wrecking-ball-class economics from
design.md B.5).

**Do not build (C)** for this pipeline: it forfeits determinism, clean telemetry,
and the single-shot `_repair_loop` for a coherence gain (B) captures more cheaply.

---

## Three design rules to carry forward

1. **Retrieve external and deterministic; keep the vocabulary out of context.**
   Stage-1 is a pure function of `(prompt, bank_version)` producing a bounded menu
   (Tier-1 legend always present, Tier-1b themed subset ≤15 parts) — the
   ToolLLM/Gorilla/tool-search-proven placement. No LLM in stage-1 for v2.2.
2. **Pin the retrieved set for the whole run; on repair, append, never rebuild.**
   One bounded append-only re-retrieval on a "missing-kind" hint. This keeps
   reproducibility, prompt-cache, and fair A/B — and mirrors Anthropic tool-search's
   "append, don't swap."
3. **The menu is advisory, never a cage.** Keep the game prompt the dominant anchor
   (the regime where strong models treat retrieval as advisory), keep `world.add`
   as the escape hatch, fall back to legend-only below the score threshold, and
   **log every escape-hatch and out-of-menu use as bank-growth demand rather than
   rejecting it.**

---

## Sources (verified this session)

- GenSim — *Generating Robotic Simulation Tasks via Large Language Models*, Wang
  et al., ICLR 2024 — arXiv:2310.01361 (abstract/HF page); retrieval mechanism read
  from the source: `github.com/liruiw/GenSim` (`gensim/agent.py`:
  `format_dict_prompt(... task_description_candidate_num)`, `random.sample(..., MAX_NUM=10)`,
  fenced-code few-shot insertion; `gensim/memory.py`: `online_task_buffer` /
  `online_code_buffer`, cached `task_code_embedding`).
- ToolLLM / ToolBench — Qin et al., arXiv:2307.16789 (ICLR 2024): Sentence-BERT
  dense API retriever, top-k cosine, contrastive training, BM25 / ada-002 baselines,
  16,000+ APIs.
- Gorilla — Patil et al., NeurIPS 2024 (arXiv:2305.15334): Retriever-Aware
  Training, per-API documents, BM25/GPT-Index retrieval appended at train+inference,
  APIBench (~1,600 APIs), test-time doc adaptation, hallucination reduction.
- Anthropic tool-search — loaded `claude-api` skill reference:
  `tool_search_tool_bm25_20251119` / `tool_search_tool_regex_20251119`,
  `defer_loading: true`, "schemas appended not swapped (preserves cache)",
  "≥1 non-deferred tool / search tool not deferred (400 otherwise)"; observed
  directly this session (deferred tools + `ToolSearch`).
- RAG-for-codegen — *CodeRAG-Bench* (arXiv:2406.14497); *On Mitigating Code LLM
  Hallucinations with API Documentation* (arXiv:2407.09726); *When Retrieval Hurts
  Code Completion: A Diagnostic Study of Stale Repository Context* (arXiv:2605.14478)
  and environment-aware-codegen work (arXiv:2601.12262) — over-reliance/distraction,
  retrieval-induced new failure modes, high-noise distractors, and the anchor
  finding (explicit prompt anchor ⇒ retrieved items treated as advisory).
- PCG description-conditioned selection — *Narrative-to-Scene Generation*
  (arXiv:2509.04481): all-MiniLM-L6-v2 tile index, cosine over name/category/
  affordance, thin composer; *GameTileNet* (arXiv:2507.02941): semantic dataset for
  tile PCG retrieval. (Angry-Birds stability oracle, PuzzleScript OBJECTS/LEGEND,
  Dahlskog–Togelius patterns already covered in `design.md` §A.1.)
