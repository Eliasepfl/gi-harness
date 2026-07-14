# Model selection — volume generation backend + G4 attacker pool

> Research synthesis, 2026-07-13. Source-verified against OpenRouter model pages,
> the OpenRouter API/blog, and public arenas (all URLs in **Sources**). Answers
> Elias's question: *"GLM 5.2 is slow and inefficient as an agent but good in
> itself — maybe not the optimal model for our task."* Short version: **correct.**
> Good model, wrong fit. This file backs that with numbers and picks the lane.

## TL;DR

- **GLM 5.2 is genuinely a top model** — #1 on Design Arena, top open-weight on the
  Artificial Analysis Intelligence Index (51). But it is a **reasoning model**
  (`high`/`xhigh` efforts) tuned for *long-horizon agentic coding*. Our task is the
  opposite: one-shot codegen, no tools, no multi-turn. Its strengths don't score
  for us; its cost (reasoning tokens → 3× wall-time, the null-content burnout) does.
  **Verdict: not the volume default.** Keep it as an optional smart/hard-prompt lane.
- **Default volume lane: `tencent/hy3:free`** — our own ledger already ranks it #1
  (4/5 completed, ~1.75 tries, ~203 s, $0), it runs **no-think by default** (no
  reasoning-budget risk), 262K context, tools. **Caveat: the free window ends
  2026-07-21** (8 days). After that it is `tencent/hy3` paid at **$0.14 / $0.58 per
  M** — still bottom-tier cost. Plan the cutover now.
- **Second lane: `qwen/qwen3-coder:free`** (strongest free coder, 1M ctx, non-reasoning
  instruct) or **`deepseek/deepseek-v4-flash`** (frontier-class coding at the lowest
  paid cost on the board, ~$0.05–0.09/M in). Different model family = diversity +
  separate free rate limits.
- **G4 attacker pool:** fan out the **free tier** across families — `hy3:free`,
  `qwen3-coder:free`, `llama-3.3-70b:free` (⚠ retires 2026-07-19),
  `gemma-4-31b:free`, `nemotron-3-super-120b:free`, `cohere/north-mini-code:free`.
  Attackers emit **pure JSON attacks, not code**, so weak-but-cheap-and-diverse wins.
- **Tie-breaker for any close top-2:** run our 5-prompt A/B ledger protocol (below).

---

## 1. Our task profile (this drives every decision)

From `OBJECTIVES.md` (Generation backends + telemetry) and `notes/PARTS_BANK.md`:

| Dimension | Our reality | Consequence for model choice |
|---|---|---|
| Shape | **ONE-SHOT**: ~1.8k-token system prompt (measured ~1765 tok) → `DESIGN` block + ~100-line Python module | Single-shot code quality + format compliance is everything |
| Iteration | 0–3 **repairs** fed a JSON report (not a conversation) | Mild multi-turn, but each turn is still one-shot-style, report-driven |
| Agentic? | **NO** tool-calling, **NO** multi-turn planning, no long-horizon context | Agentic/tool-use benchmark leadership is **irrelevant to us** |
| Latency | Volume campaigns → wall-time per run matters | Fast decode + low TTFT + few emitted tokens |
| Cost | Matters at scale | Want ≤ ~$0.15/M in; free is ideal for the base campaign |
| Reasoning budget | We **cap reasoning tokens** | **Reasoning models are a liability** unless they respect a no-think/low mode |
| Output discipline | Must obey a strict format (DESIGN block, forbidden imports, declared symbols) | Instruction-following > raw IQ |

**Re-read of "bad agent / good model" against this:** agentic weakness is a
non-issue — we never let the model drive tools or plan across turns. What we buy is
(1) clean single-shot code, (2) format compliance, (3) speed, (4) **not** blowing the
reasoning budget. A model can be #1 at multi-hour SWE marathons and still be a poor
fit here; conversely a fast no-think model that writes a correct 100-line pymunk game
first try is ideal.

---

## 2. GLM 5.2 — characterization (good model, wrong fit)

**Why it's genuinely good (the benchmark tab Elias pointed at):**
- **Design Arena:** **#1**. Design Arena *Website* leaderboard **~1356–1360 Elo**, ahead
  of Claude Opus 4.6 (1337); leads the *Code Categories* board ~10 Elo over Claude
  Fable 5. (Human head-to-head preference, not synthetic.)
- **Artificial Analysis Intelligence Index v4.1: 51** — **top of all open-weight models.**
- **SWE-bench Pro 62.1** (> GPT-5.5 58.6; < Opus 4.8 69.2); **FrontierSWE 74.4%**
  (~tie with Opus 4.8 75.1); **Terminal-Bench 2.1 81.0**. Open weights, MIT, released
  2026-06-13.

**Why it's the wrong fit for our loop:**
- It is a **reasoning model** exposing only `high` / `xhigh` efforts (per the OpenRouter
  page) — i.e. it is *built to think a lot*. Every benchmark it wins is a **long-horizon,
  multi-hour, agentic** coding task. That is the exact profile we don't run.
- **Speed:** launched ~300 tok/s, now **~78–191 tok/s** on OpenRouter under load;
  TTFT ~0.89 s. But throughput isn't the killer — **token volume** is: reasoning traces
  balloon the run. This matches our ledger: **~630 s/run, 3× slower** than hy3.
- **The burnout blind spot** you saw (returns `null` content on one prompt) is a known
  failure mode of heavy reasoning models when the effort/token budget interacts badly
  with a capped reasoning window — for an *automated volume* pipeline that is an
  availability bug, not a quality quirk.
- **Cost:** heavier per run (OpenRouter lists it around **$0.45–$1.40 / M in**,
  **$3–$4.40 / M out** depending on provider route Balanced/Nitro/Exacto and time —
  our runs measured "~cents"). ~10–25× hy3 paid input, and it emits far more tokens.

**Net:** GLM 5.2's entire value proposition (planning, long-horizon agentic coding,
"closest open drop-in for Opus-style work") is orthogonal to a capped-reasoning,
one-shot, no-tools codegen loop. **Elias's instinct is right.**

---

## 3. Candidate matrix (OpenRouter, verified July 2026)

Prices are per **1M tokens (in / out)**. Throughput is OpenRouter's rolling estimate
(varies by provider/route). "Reason?" = is it a reasoning model / does it have a
no-think or low mode we can pin. "Fit" is for **our one-shot capped-reasoning codegen**.

| Model (OpenRouter id) | In / Out | Context | Throughput | Arena / coding signal | Reason? | Free? | Fit for our task |
|---|---|---|---|---|---|---|---|
| **tencent/hy3:free** | **$0 / $0** (free window→ 2026‑07‑21) | 262K | (our ledger ~203 s/run) | Agentic idx 63.4, near-SOTA SWE‑Verified; 295B MoE (21B active) | Yes, **no‑think default** + low/high | ✅ | ★★★★★ Incumbent winner; no-think = no budget risk |
| **tencent/hy3** (paid) | $0.14 / $0.58 | 262K | — | same weights as :free | Yes, no‑think default | — | ★★★★★ Cutover target after free window |
| tencent/hy3-preview | $0.063 / $0.21 | 262K | — | preview build | Yes | — | ★★★★ Cheapest hy3 variant, worth an A/B |
| **qwen/qwen3-coder:free** | **$0 / $0** | 1.0M | ~ (480B A35B) | "strongest free coding model on OpenRouter" | **No** (instruct) | ✅ | ★★★★★ Best free 2nd lane; non-reasoning = predictable |
| qwen/qwen3-coder (paid) | $0.22 / $1.80 | 1.0M | — | 480B A35B | No | — | ★★★★ Paid fallback for the free coder |
| qwen/qwen3-coder-next | $0.11 / $0.80 | 262K | ~94 tok/s | newer coder line | No | — | ★★★★ Fast, cheap, non-reasoning |
| **deepseek/deepseek-v4-flash** | ~$0.05–0.09 / $0.18–0.24 | 1.0M | ~84 tok/s | SWE‑Verified **79.0%**, AA idx 40; "lowest cost on the board" | Yes, `high`/`xhigh` (**run low**) | — | ★★★★★ Cheapest paid frontier coder; pin low effort |
| deepseek/deepseek-v4-pro | $0.44 / $0.87 | 1.0M | — | SWE‑Verified 80.6, LiveCodeBench 93.5 | Yes | — | ★★★ Overkill/slower for one-shot |
| **moonshotai/kimi-k2.6** | $0.66 / $3.41 | 262K | — | coding idx 47.1, IFBench 0.760 | mixed | — | ★★★ Good IF but pricey out |
| google/gemma-4-31b-it:free | $0 / $0 | 262K | — | vision+tools, free | No | ✅ | ★★★ Diversity/attacker; lighter codegen |
| google/gemini-2.5-flash-lite | $0.10 / $0.40 | 1.0M | — | fast Flash-class | optional | — | ★★★ Cheap closed option, format-obedient |
| google/gemini-2.5-flash | $0.30 / $2.50 | 1.0M | — | strong Flash | optional | — | ★★★ Solid but pricier out |
| **meta-llama/llama-3.3-70b:free** | $0 / $0 (⚠ retires 2026‑07‑19) | 131K | — | classic free workhorse | No | ✅ | ★★★ Attacker-pool only; retiring |
| meta-llama/llama-3.3-70b (paid) | $0.10 / $0.32 | 131K | — | — | No | — | ★★★ Cheap non-reasoning fallback |
| nvidia/nemotron-3-super-120b:free | $0 / $0 | 1.0M | ~75 tok/s (Ultra) | tools; US-built | No | ✅ | ★★★ Attacker-pool diversity |
| cohere/north-mini-code:free | $0 / $0 | 256K | — | code-tuned, free | No | ✅ | ★★★ Attacker-pool diversity |
| minimax/minimax-m3 | $0.098 / $1.21 | 1.0M | ~59 tok/s | AA idx 44, multimodal | — | — | ★★ Value in screenshot→code, not our loop |
| mistralai/mistral-medium-3 | $0.40 / $2.00 | 131K | — | STEM/coding | No | — | ★★ Priced above the cheap tier |
| mistralai/mistral-medium-3.5 | $1.50 / $7.50 | 262K | — | agentic/coding | No | — | ★ Too expensive for volume |
| **z-ai/glm-5.2** (incumbent) | ~$0.45–1.40 / $3–4.40 | 1.0M | ~78–191 tok/s, TTFT 0.89s | **#1 Design Arena**, AA idx **51**, SWE‑Pro 62.1 | **Yes, heavy** (high/xhigh only) | — | ★★ Great model, wrong fit; 3× slower, burnout risk |

*Pricing note:* OpenRouter routes each model across multiple providers (Balanced /
Nitro / Exacto) so a single model shows a **range**; DeepSeek V4 Flash and GLM 5.2 in
particular appear at different rates across the model page vs the API/blog. Ranges above
reflect that spread. Cache discounts (60–80% on repeated context) apply to our static
1.8k system prompt — a real lever at volume.

---

## 4. Recommendation

**(a) Default volume lane — `tencent/hy3:free` now, `tencent/hy3` paid after 07-21.**
It already won our own A/B on the metric that matters (task completion at lowest cost),
and structurally it fits: **no-think by default** means zero reasoning-budget risk — the
single thing that broke GLM. Set `OPENROUTER_MODEL=tencent/hy3:free` (one-line change in
`env.py`, per the telemetry directive). **Action item: schedule the flip to
`tencent/hy3` ($0.14/$0.58) on/before 2026-07-21** so the campaign doesn't stall when the
free eval window closes. Consider A/B-ing `tencent/hy3-preview` ($0.063/$0.21) as an even
cheaper same-family variant.

**(b) Second lane — `qwen/qwen3-coder:free`** (primary) with
**`deepseek/deepseek-v4-flash`** as the paid heavyweight-value option. Rationale: a
different model family de-correlates failure modes (diversity of lessons for the
prompt-harvest step) and gives a second free rate-limit bucket for parallelism. Qwen3-coder
is a **non-reasoning instruct** model (predictable, no budget knob to mis-set) and the
"strongest free coder" on OpenRouter with 1M context. DeepSeek V4 Flash is frontier-class
coding (SWE-Verified 79%) at the lowest paid cost on the board — **pin it to low reasoning
effort** so it behaves like a fast coder, not a thinker.

**(c) G4 attacker pool (cheap + fast + diverse >> smart).** Per OBJECTIVES Tier-1,
attackers read certified source + report and emit **pure JSON action sequences, never
code** — generator-verifier asymmetry means weak models suffice and wrong attacks cost
nothing. Optimize for **breadth of behavior and free parallelism** (separate rate limits
per provider): 

- `tencent/hy3:free`, `qwen/qwen3-coder:free`, `google/gemma-4-31b-it:free`,
  `nvidia/nemotron-3-super-120b:free`, `cohere/north-mini-code:free`, and
  `meta-llama/llama-3.3-70b:free` **until it retires 2026-07-19** (then drop to the paid
  `$0.10/$0.32` tier or replace with another free family).
- Diversity is the goal, so *do not* dedupe families down to one — the point is
  uncorrelated attack ideas. GLM 5.2 can sit here as an occasional **Tier-2 "smart
  attacker"** on games that survive Tiers 0–1, where its planning depth is an asset and
  latency/cost don't dominate.

**(d) Does GLM 5.2 keep a role? Yes — a narrow one, not the volume default.**
Reserve it for (i) **hard/exotic prompt generation** later in the pyramid (rung 3), where
its #1-design-quality and planning earn their token cost, and (ii) **Tier-2 adversarial
attacks** in G4. For the base-of-games volume campaign it is disqualified by the
capped-reasoning constraint and the null-content burnout. If we ever want a *smart* volume
lane, prefer a fast model at low effort (DeepSeek V4 Flash) over a heavy reasoner.

---

## 5. Tie-breaker protocol (our ledger A/B) — for any close top-2

Sample size is honest-small: our current read is 5 prompts, hy3 4/5 vs GLM 3/4. Don't
over-trust it. For any two candidates within noise on the matrix, run the **5-prompt A/B**
already wired into the telemetry stack:

1. Same 5 fixed prompts, same frozen base code (integrity manifest enforced), same
   reasoning cap, `template` excluded.
2. Each run appends one line to `runs/ledger.jsonl` (prompt, backend, model, verdict,
   attempts, per-attempt failure_class + failed checks, repairs, wall-time, integrity).
3. `harness game stats` aggregates per model: **completion rate, mean repairs-to-COMPLETED,
   failure-class histogram, flagrant errors** (format non-compliance, forbidden imports,
   ignored symbols).
4. **Decision metrics, in priority order for our task:** (1) completion rate, (2) flagrant
   format/instruction violations (should be ~0), (3) mean repairs, (4) wall-time/run,
   (5) $/run. Speed and format-discipline break ties before raw quality — that's the whole
   point of the task profile.
5. Expand to 10–15 prompts before making cost commitments at scale.

Suggested first A/B once the default is set: **hy3:free vs qwen3-coder:free** (the two
free lanes) and **hy3-preview vs hy3 paid** (cheapest viable paid variant), so we enter
the post-07-21 paid window with data, not a guess.

---

## 6. Operational alerts (time-sensitive)

- ⚠ **`tencent/hy3:free` free window ends 2026-07-21.** Cut over to `tencent/hy3` paid
  (or hy3-preview) before then.
- ⚠ **`meta-llama/llama-3.3-70b:free` retires 2026-07-19.** Remove from the attacker pool
  or move to paid `$0.10/$0.32`.
- Free tier rate limits: **20 req/min, 200 req/day per key** across all free models — a
  hard ceiling on single-lane volume; multi-family lanes multiply it.

---

## Sources

- OpenRouter — GLM 5.2 model page & benchmarks: https://openrouter.ai/z-ai/glm-5.2
- OpenRouter — Tencent Hy3 (free / paid / preview):
  https://openrouter.ai/tencent/hy3:free · https://openrouter.ai/tencent/hy3 ·
  https://openrouter.ai/tencent/hy3-preview
- OpenRouter — Qwen3 Coder (free / paid / next):
  https://openrouter.ai/qwen/qwen3-coder:free · https://openrouter.ai/qwen/qwen3-coder ·
  https://openrouter.ai/qwen/qwen3-coder-next
- OpenRouter — DeepSeek V4 Flash / V4 Pro:
  https://openrouter.ai/deepseek/deepseek-v4-flash · https://openrouter.ai/deepseek/deepseek-v4-pro
- OpenRouter — Kimi K2.6: https://openrouter.ai/moonshotai/kimi-k2.6
- OpenRouter — Gemini 2.5 Flash / Flash Lite: https://openrouter.ai/google/gemini-2.5-flash ·
  https://openrouter.ai/google/gemini-2.5-flash-lite
- OpenRouter — Llama 3.3 70B (free/paid): https://openrouter.ai/meta-llama/llama-3.3-70b-instruct:free
- OpenRouter — Mistral Medium 3 / 3.5: https://openrouter.ai/mistralai/mistral-medium-3 ·
  https://openrouter.ai/mistralai/mistral-medium-3-5
- OpenRouter blog — "The Open Weight Models that Matter: June 2026":
  https://openrouter.ai/blog/insights/the-open-weight-models-that-matter-june-2026/
- OpenRouter free-models roster (Jul 2026): https://costgoat.com/pricing/openrouter-free-models
- GLM 5.2 benchmarks/press: https://venturebeat.com/technology/z-ais-open-weights-glm-5-2-beats-gpt-5-5-on-multiple-long-horizon-coding-benchmarks-for-1-6th-the-cost ·
  https://kie.ai/blog/glm-5-2-benchmark-deep-dive · https://emergent.sh/learn/glm-5-2-benchmark
- GLM 5.2 throughput/TTFT: https://www.baseten.co/blog/how-we-built-the-worlds-fastest-api-for-glm-52/ ·
  https://pricepertoken.com/pricing-page/model/z-ai-glm-5.2
- Design Arena / coding arena rankings (Jul 2026): https://benchlm.ai/benchmarks/designArenaWebsite ·
  https://llm-stats.com/leaderboards/best-ai-for-coding
- Tencent Hy3 background: https://www.explainx.ai/blog/tencent-hy3-295b-moe-open-source-agentic-model-2026
