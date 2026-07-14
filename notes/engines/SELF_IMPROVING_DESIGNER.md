# Self-improving designer for gi-harness — does a learning loop scale expertise with the runs?

> Multi-agent research, 2026-07-14 (Fable orchestrator + Opus agents; 5 web/code dives → 1 design).
> **Elias's thesis under test:** *a self-learning loop bolted onto the designer scales expertise with
> the runs, and therefore delivers BOTH more varied AND more robust environments.* Grading lens:
> what the primary-source landscape actually demonstrates about compounding loops, mapped onto our
> substrate (prompt → deepseek → JSON spec → frozen G0–G3 witness-replay funnel → G4 adversarial →
> G3′ RL grade → curriculum). Every ★/delta/date below was fetched live or read from local code on
> 2026-07-14; nothing invented. Standing principle throughout: **the VERIFIER is frozen and never
> self-modified** — the loop trains the designer, never the reward.

---

## 1. VERDICT on the thesis (10 lines)

1. **HOLDS on robustness.** The reward that anchors the loop is our frozen typed-state certifier — top of the RSI-survey verification hierarchy (*formal > execution/tests > learned judge > intrinsic*), rule "no external signal, no reliable improvement" (arXiv:2607.07663). We already sit where compounding is demonstrated.
2. **HOLDS on "scales with runs" — but only as accumulating external artifacts,** not a smarter model. Every shipped exemplar freezes the base LLM and grows a library/prompt/DB (Voyager, Eureka, GEPA, ACE, Hermes). Our bank+prompts+curriculum are exactly that kind, which is fine but must be stated plainly.
3. **NEEDS QUALIFICATION on variety.** The closed-loop literature is unanimous that novelty is a *consumable resource a self-referential proposer depletes* — a designer that learns what it can certify narrows to an easily-certified band (robust↑, varied↓). The frozen verifier protects robustness unconditionally; **variety needs an exogenous signal and a separately-measured axis**, or the loop quietly trades it away.
4. **NEEDS QUALIFICATION on "closed."** Nobody closes an online weight loop; the productive pattern is export-then-train-offline (Hermes/Atropos). Set expectations that our loop is batched/offline.
5. **Net:** thesis is realistic *at the memory/skills level* today; the weight-RL half is a heavy, separable, offline add-on — and only defensible if the variety axis is instrumented first.

---

## 2. HERMES-AGENT — repo facts vs blog claims

- **Provenance.** The assigned URL `hermes-agent.ai` is a **third-party SEO/affiliate site**, not Nous property. Authoritative sources are only `github.com/NousResearch/hermes-agent` (214,843★ / 39,966f / 23,919 open issues, MIT, Python, created 2025-07-22, actively pushed) and `hermes-agent.nousresearch.com/docs`.
- **What it actually is:** the agent does **NOT train weights or run RL at runtime.** Its "self-improvement" is entirely **in-context** — file-based skills (`~/.hermes/skills/SKILL.md`), agent-curated memory + periodic nudges, FTS5 cross-session recall, Honcho user-modeling — over a **frozen, swappable** base model.
- **The real RL is a *separate* project, Atropos** — an offline "environment microservice framework for async RL," now **ARCHIVED read-only (2026-07-04)**, v0.4.0, 1,333★. Marketing **conflates** the two to imply the agent self-trains. It does not. The only bridge is ShareGPT trajectory export → offline fine-tune (post-hoc, manual, un-closed).
- **Atropos wins are real but NARROW single-task specialists** of Llama-3.1-8B: BFCL Parallel 10%→46%, Simple 21%→51.75%; financial prediction 20%→50%. Model card: *"experimental… not for broad, general-purpose use."* **Zero** measured number anywhere shows the *agent* improving from its own experience — Nous's own copy dodges it ("not a synthetic score; whether Hermes needs less steering").
- **Worth taking (2–3):** (a) **export-then-offline-train is the honest flywheel shape** — no magic online loop (→ our v3). (b) The **verifiable-reward branch** (code/math/tool-call correctness) is the transfer; the RLAIF/LLM-judge branch (Egregore) is exactly what our no-VLM/no-judge stance rejects. (c) Hermes's **skills+FTS5 memory over a frozen model, at 214k★, validates our "library-first for LEARNING machinery"** principle: the non-gradient in-context loop is the thing that ships and scales; weight-RL is the separable heavy add-on.

---

## 3. LANDSCAPE — what compounds, and how strong the evidence

| Mechanism | Exemplar (source) | What compounds | Evidence strength | Fit to our substrate |
|---|---|---|---|---|
| Evolve programs vs automated evaluator cascade | **AlphaEvolve** (arXiv:2506.13131) | solution/program DB | **Strongest** — 4×4 complex matmul 48 mults (first < Strassen's 49 in 56 yrs), 0.7% Borg fleet, prod kernels | **Exact grader class = ours.** Its stated limit "needs machine-gradable eval" IS our home regime. Bank = its accreting DB. |
| Evolve reward code, fitness = TRUE unshaped metric | **Eureka** (arXiv:2310.12931) | reward functions | **Strong** — clean reward-vs-iter curve, beats human on 83%/29, +52% | Its "shaping can't pass a false task" safeguard is **verbatim our principle**. Ledger diagnostics = its "reward reflection". |
| Coevolve envs+agents under minimal-criterion band | **POET / Enhanced-POET** (arXiv:1901.01753, 2003.08536) | env population | **Strong** — provably unsolvable-from-scratch, transfer counts; PATA-EC novelty rank | **Structural twin of our curriculum.** G3′ learnability band = its MC; import PATA-EC as deterministic diversity metric. |
| Reflective Pareto prompt evolution | **GEPA** (arXiv:2507.19457) | prompts (frontier) | **Strong** — beats GRPO +6–19pp at up to 35× fewer rollouts | Drop-in for designer prompt + BM25 MENU; Pareto-over-genres = varied AND robust at once. |
| Structured incremental context playbook | **ACE** (arXiv:2510.04618) | context/lessons | Medium — +10.6% agent, +8.6% finance | Append **deltas, never summarize-rewrite** (names "context collapse"). Governs how `rules.md` grows. |
| Self-edit agent codebase vs benchmark utility | **SICA** (arXiv:2504.15228) | agent's own code | Medium — 17%→53% SWE-Bench, but **tooling-only**, edits its own grader | **Cautionary inverse of our freeze.** Transfer the outer loop; never let it touch the verifier. |
| Verified skill library + self-verify critic | **Voyager** (arXiv:2305.16291) | skill code | Medium (soft grader) — −73% without self-verification | Bank/Debug-Skill analog; **swap its LLM critic for witness replay** (kills #1 failure: false-positive skills). |
| Verbal-reflection memory | **Reflexion/ExpeL** (arXiv:2303.11366, 2308.10144) | text memory | Weak — saturates after few retries | Cap repair attempts in revise mode (ledger already counts). |
| FM "interestingness" task tree | **OMNI-EPIC** (arXiv:2405.15568) | task distribution | Weakest (subjective FM) — qualitative tree, no curve | **Do not import the FM judge.** Replace with our deterministic `difficulty_profile`. |
| VLM-graded fix/skeleton library | **OpenGame Debug Skill** (local CLAUDE_GAMEGEN_SKILLS.md) | fix DB | Weak (non-det pixel/VLM) | Domain twin; our typed-state + witness replay is the deterministic upgrade of its grader. |

**Headline:** the objective-grader cluster (AlphaEvolve/Eureka/POET/GEPA) gives the only *primary-sourced, multi-iteration compounding curves* — and it is exactly the grader class our funnel occupies. The famous soft-grader systems (Voyager/OMNI/OpenGame) are **weaker** evidence for "compounds under an automated verifier" than their fame suggests; our frozen deterministic verifier is precisely the fix for the rot that afflicts them.

---

## 4. THE DESIGN — ranked loops, guardrails, phasing, metrics

**Invariant (mechanical, not conventional).** `integrity.snapshot/violations` hashes every `harness/**/*.py` + `CONTRACTS.md` + `gen/prompts/*.md`; any mid-run mutation forces `verdict=INVALIDATED` (`core/integrity.py:46-106`; `gen/gamegen.py:899-928`). **Frozen (loop NEVER writes):** G0–G3 `gameverify` + `[eng.]` thresholds, `run_episode`, G4 `referee.classify`+`refute_prefix`+`treesolve`, `bank_ci`, `g3_prime`, `integrity.py` (`verify/gameverify.py:1297`, `verify/g4.py:287,929`, `rl/certify.py:72`). **Data-only (loop MAY write between runs, each gated by a frozen eval):** 7 prompt sections `gen/prompts/*.md`; hint tables `_REPAIR_HINTS`/`_UNSOLVED_HINT` (`verify/g4.py:1242`, `gen/gamegen.py:67`); retrieval config + `_QUERY_SYNONYMS` (`gen/retrieval.py:42-96`); `banks/parts/v1/parts.json` (gated by `bank_ci`); curriculum thresholds + directive templates (`gen/curriculum.py:50,281`); G4 fuzz sizing + attacker prompt + `STRATEGY_VOCAB` (`verify/g4.py:92,125`).

Loops ranked by **evidence-of-compounding × substrate-readiness** (V-lean=variety, R-lean=robustness):

| # | Loop | Analog | Readiness | Lean | Phase |
|---|---|---|---|---|---|
| 1 | **Curriculum revise** — verify→G3′→difficulty_profile→directive→minimal-edit revise, K rounds | POET | **WIRED** (`gen/curriculum.py:468`, `cli.py:600`) | R (+V via novelty) | v1 |
| 2 | **Prompt-section evolution** — append lessons to `rules.md`/`orientation.md` | GEPA/ACE | **WIRED** (freeze gates drift; `gen/prompts/__init__.py:127`) | Both | v1→v2 |
| 3 | **Parts-bank growth** — mine recurring cleared sub-assemblies → propose parts | AlphaEvolve/Voyager | **GATE READY, GROWTH ABSENT** (`bank_ci.py:92`; no mining) | Both (top-leverage V) | **v2** |
| 4 | **G4 strategy up-weighting** — reweight fuzz families from findings-per-family | Eureka | MEDIUM (`verify/g4.py:493`) | R | v1.5 |
| 5 | **Repair-hint phrasing** — fewer repairs | OpenGame (but HARD gate) | WIRED (`core/telemetry.py:276`) | R | v1 (low prio) |
| 6 | **Retrieval tuning** — auto-tune synonyms/threshold | BM25 menu | NEEDS v2 (`menu_mode` not rolled up) | V-support | v2 |

**The paradox to plan around:** the highest-evidence loop (#3 bank growth) is the *lowest-readiness* (ready oracle, zero growth machinery); the highest-readiness loop (#1) already closes one iteration. v1 harvests readiness; v2 builds the telemetry that unlocks #3.

**Metrics each loop is graded on — BOTH axes, deterministic, no VLM:**
- **ROBUSTNESS (already computable):** `completion_rate`, `mean_attempts_to_completed` (`core/telemetry.py:275-277`); **hardening rate** = fraction surviving G4 with zero `_HARD_OUTCOMES` (`verify/g4.py:110`); falling flagrant-rate (`core/telemetry.py:43-75`). Compounding test = these improve *across runs*.
- **VARIETY (must be built as first-class):** archetype-coverage histogram over `orientation.md` idiom vocab; mechanic-coverage over `pipeline.parts_used` categories (`core/telemetry.py:132`); **Enhanced-POET PATA-EC** novelty scalar over the certified-spec set; **MAP-Elites/Pareto archive keyed (archetype × difficulty grade)** so the loop is rewarded for filling empty niches, not re-winning easy ones (GEPA frontier + POET transfer-if-better). **The thesis is proven only if BOTH curves rise together.**

**Clearability (versioned/hashed/attributable/revertible).** All data surfaces are files → git-tracked; a wave = a tagged commit of prompts + `parts.json` + config; a revert = `git revert`. Prompt-set hashes into the run manifest (`core/integrity.py`); parts hash via `bank.content_hash` (`core/bank.py:92-103`). **Two gaps blocking clean attribution (fix in v2):** (a) ledger line carries `prompt`+`parts_used` but **no section-set hash, no variant tag**, and `stats()` groups **only by (backend, model)** (`core/telemetry.py:248`) — verified; (b) `bank.content_hash` is **not yet folded into `integrity.snapshot`** ("in a later wave", `core/bank.py:97-99`) — verified, so the advisory MENU isn't reproducibility-pinned mid-run.

**Phasing:** v1 = a batch **controller** over already-wired, already-gated knobs (the dive-5 missing piece: "no controller reads the ledger back into the knobs"). v2 = the attribution telemetry that turns knobs into a learner and unlocks bank growth. v3 = offline verifiable-reward fine-tune of a *small* designer (§7-adjacent).

---

## 5. FAILURE MODES + guardrails

| Failure | Mechanism | Guardrail |
|---|---|---|
| **Reward hacking on G-gates** (Goodhart on what the verifier UNDER-checks) | loop finds specs passing G0–G3 via a missing oracle | Verifier un-writable by construction (`core/integrity.py:93`); Eureka principle already ours (fitness = TRUE unshaped certificate, shaping can't *pass* a false game); counter-pressure = **G4 stale-state/softlock oracle** (`verify/g4.py:929`, reuses frozen `treesolve`) + witness bit-replay (`rl/certify.py:141`). SICA cautionary: never let the loop edit verifier thresholds. |
| **Variety collapse into easy archetypes** | closed proposer narrows to an easily-certified band | POET minimal-criterion band **rejects `degenerate`/`easy`** (`gen/curriculum.py:50`); variety is a **separate gated axis** (§4) — reject any edit that raises `completion_rate` but lowers coverage; QD archive + **exogenous fresh prompts** (the outside signal the loop can't manufacture). |
| **Eval contamination** | tuning+gating on one small set overfits (SICA/GEPA caveat) | **held-out prompt suite gates edits, never authors them**; dev/gate/exogenous-test splits, rotate fresh prompts per wave; `merge_shards` dedup `(game_id, seed, verdict_hash)` (`core/telemetry.py:172`). |
| **Prompt bloat vs the curated one-pager** | `rules.md`/`orientation.md` grow unbounded (ACE "context collapse"/"brevity bias") | **append structured deltas, never summarize-rewrite** (ACE); token/line budget with **earn-your-place eviction** — a lesson survives only if per-section attribution (v2) shows it moved `completion_rate`, else revert; GEPA Pareto keeps the *diverse minimal* set. Resolves append-only-vs-one-pager tension. |
| **Skill/insight false positives** | soft-graded libraries admit wrong entries (Voyager −73%) | every bank/hint insertion gated on **deterministic witness replay / `bank_ci`**, not an LLM critic — removes the field's #1 failure mode. |

---

## 6. BUILD ORDER — v1, this week, on existing hooks

1. **Batch controller (the missing piece).** Wrap the already-wired curriculum loop (`gen/curriculum.py:468`, `cli.py:600`) to run over a held-out prompt suite; promote only re-certified `target`-grade games into a **Pareto archive — do not overwrite** (CURRICULUM_LOOP.md §8.2 round-chaining overwrite bug discards the trajectory).
2. **Gated prompt-section edit.** Propose one lesson append to `rules.md`/`orientation.md`; run the suite; keep iff `completion_rate`↑ AND flagrant↓ AND variety-coverage not↓. Integrity freeze already forbids mid-run drift (`core/integrity.py:46`).
3. **G4 strategy up-weighting** from `families{}` counts (`verify/g4.py:493`); **repair-hint phrasing** gated by `mean_attempts_to_completed` (`core/telemetry.py:276`).
4. **Offline variety analyzer** over `runs/` + certified specs (archetype × mechanic histogram + PATA-EC novelty) — reads existing ledger, writes nothing new; produces the first variety-vs-runs plot.
5. *(Attribution is coarse in v1:* whole-prompt-set hash, A/B by swap-and-compare `stats()`. Fine for a handful of controlled edits. Revert = git.)

**v2 build gap (unlocks the high-evidence loops):** add to `record_run`/`stats()` a **prompt-section-set hash + variant tag**, **per-part & per-`menu_mode` rollups**, a **hint→next-attempt-cleared flag**, and a **curriculum-event aggregator** (events already share the ledger but `stats()` ignores them, `gen/curriculum.py:434`); then **fold `bank.content_hash` into `integrity.snapshot`** and add **parts-bank mining → `bank_ci`-gated auto-propose** (the AlphaEvolve/Voyager loop, safe by construction).

---

## 7. NOT TAKEN (and why)

- **LLM/VLM/FM judge on the pass/fail path** (talk's "is it good?", OMNI interestingness, Voyager/OpenGame critic) — the most gameable tier (self-confirming loop, confidence-coupled reward). Our absence of a judge is a **strength**; keep pass/fail deterministic.
- **SICA-style self-modification of grading machinery** — the inverse of our frozen-verifier principle; transfer the outer loop only, expect tooling-class gains, never touch the verifier.
- **Memory consolidation via summarizer** (talk's "summarize episodic→semantic") — precisely ACE's "context collapse"; use structured incremental deltas instead.
- **Hermes user-modeling/Honcho + multi-platform chat plumbing** — irrelevant to spec generation.
- **The "agent self-trains at runtime" framing** — marketing conflation (Atropos is separate + archived); nobody closes it online.
- **v3 online weight-RL on OpenRouter-deepseek — INFEASIBLE, deferred to offline:** OpenRouter is inference-only routing with **no fine-tuning / no custom-model hosting** — you cannot train the hosted deepseek. DeepSeek-V3/R1 are **671B MoE (37B active), MIT weights**; full FT needs a large multi-GPU cluster, and even 4-bit resident (~350 GB) blows a considerate user-slice. **Realistic v3 (flagged, not taken now):** LoRA/QLoRA a **small open designer** (DeepSeek distilled 7B/14B/32B or Qwen/Llama-8B) on a single A100, self-host, point the harness at it; **GRPO/DPO with certification-pass as the verifiable reward** (Atropos's *verifiable* branch, not RLAIF) — offline, export-then-train, under the v2 variety gate, with G3′/G4 as counter-pressure, and **the designer trained, never the verifier.** Atropos caveat: specialists go narrow/experimental/regress off-task — so v3 is the heavy add-on, not the headline.

---

**Load-bearing citations.** Local: `core/integrity.py:46-106`; `core/telemetry.py:43-75,81-141,172,238-281,248`; `core/bank.py:92-103,97-99`; `gen/curriculum.py:50,281,434,468`; `gen/retrieval.py:42-96`; `gen/prompts/__init__.py:127`, `rules.md`, `orientation.md`; `verify/g4.py:92,110,125,287,493,929,1242`; `verify/gameverify.py:1297`; `rl/certify.py:72,141`; `bank_ci.py:92`; `cli.py:600`; CURRICULUM_LOOP.md §8.2; CLAUDE_GAMEGEN_SKILLS.md. Papers: arXiv:2607.07663 (RSI survey), 2506.13131 (AlphaEvolve), 2310.12931 (Eureka), 1901.01753 / 2003.08536 (POET), 2507.19457 (GEPA), 2510.04618 (ACE), 2305.16291 (Voyager), 2504.15228 (SICA), 2303.11366 (Reflexion), 2308.10144 (ExpeL), 2405.15568 (OMNI-EPIC). External: `github.com/NousResearch/hermes-agent` (214,843★, MIT) + Atropos (archived 2026-07-04) + DeepHermes-ToolCalling-Specialist (BFCL 10%→46% / 21%→51.75%, financial 20%→50%, Llama-3.1-8B); OpenRouter (no fine-tuning); DeepSeek-V3 HF card (671B MoE, MIT, distilled FT only).
