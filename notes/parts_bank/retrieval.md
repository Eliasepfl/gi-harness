# Retrieval technology for the parts bank — study

> Research note (no code changes). Scope: how the generator picks an ~8-15-part
> **themed menu** from the parts bank (~60 archetypes now, ~500 later) given a game
> prompt, per the open question in `design.md` §Appendix.1 ("keyword vs embedding
> match; start with keyword match over name+summary+category"). Every library, model
> card, and number below was checked against a live source on 2026-07-13 (sources §9).
> Framing is deliberate and load-bearing: **this is TINY-CORPUS retrieval** — 60-500
> documents of ~30 tokens each (`name` + `summary` + `category` + `tags`), not web-scale
> RAG. Everything is evaluated at that scale, and the note is hostile to
> over-engineering: at 500 docs, most of the "RAG stack" is dead weight.

---

## 0. TL;DR verdict

**Brute-force hybrid over the whole bank, embedded once, deterministic, offline, no
vector DB. Ship BM25 first; add a static-embedding dense channel when fictional prompts
arrive (pyramid rung 3).**

The task is "given ~30 tokens of prompt, rank ~60-500 tiny docs." At that size the entire
retrieval computation — sparse scoring **and** an exact cosine over *every* embedding —
runs in **single-digit milliseconds on a laptop CPU**, versus the 100-700 s each
generation attempt already takes in the ledger. So the design axes that matter are **not**
speed or indexing structures; they are **(1) determinism**, **(2) robustness to fictional
vocabulary** ("volcano hopper" → lava/stone/hazard parts), and **(3) not adding a
dependency the project's determinism-first spine can't tolerate.** Concretely:

- **No vector database.** faiss / chroma / a Qdrant server exist to make *approximate*
  nearest-neighbour tractable over millions of vectors. Over 500 vectors an **exact**
  `numpy` matmul is faster than building the ANN index — you'd be paying setup and an
  approximation error to avoid a linear scan you can trivially afford. This mirrors the
  bank's own "local-first, no MCP" verdict (`mcp_tools.md` §0): the network/DB dependency
  buys nothing at our scale and costs determinism.
- **Sparse (BM25) is the right *first* channel** — deterministic, ~1 tiny dependency,
  and it nails the exact-term case (a prompt saying "crate" and "pit" should retrieve
  `crate` and `pit_zone`). Use **`bm25s`** (pure NumPy/Numba), not `rank_bm25`.
- **Its one failure is exactly the interesting one:** fictional / synonymous vocabulary.
  BM25 cannot connect "volcano hopper" to a `lava_hazard` + `stone_platform` unless the
  words literally overlap. The fix is a **dense** channel (semantic embeddings) or a
  synonym map; embeddings are the lower-maintenance option and the reason to go hybrid.
- **Fuse the two ranked lists with Reciprocal Rank Fusion (RRF).** No cross-encoder
  reranker — at 60-500 docs it adds 100 ms-2 s of latency and, off-domain, often *hurts*
  ranking (-0.3 % to -3.1 % NDCG in one measured eval; §3).
- **No separate buzzword-extraction step.** The prompt is already ~30 keyword-dense
  tokens; embed it raw. YAKE/KeyBERT/LLM extraction is a lossy bottleneck built for *long*
  documents, and it does nothing for the fictional-vocab gap (§4).
- **The determinism story is a hard gate, and it kills the LLM-as-selector baseline for
  the pinned path** (§5): a `world.part` menu chosen by a Haiku/Opus call is subject to
  provider-side non-determinism and silent model updates, so "same prompt + same
  `bank_version` ⇒ same menu" (a stated hard requirement) cannot be guaranteed without
  caching the mapping anyway. Embedding/BM25 retrieval is a **pure function of the prompt
  bytes + the pinned index** — deterministic by construction.

Everything below is the evidence for those five lines.

---

## 1. Sparse retrieval (BM25, keyword/tag matching)

### 1.1 The libraries (verified)

| Library | What it is | Speed | Dependency weight | Fit |
|---|---|---|---|---|
| **`rank_bm25`** | The most-used pure-Python BM25 (Okapi/BM25+/BM25L). Scores each query against each doc at query time in Python loops. | Slow — on BEIR benchmarks its throughput is often **< 1 query/sec**; it is the baseline everyone else beats. | Tiny (numpy). | Works at 60 docs, but it's the slow reference impl — no reason to pick it over `bm25s`. |
| **`bm25s`** (`xhluca/bm25s`, HF blog + arXiv 2407.03618) | BM25 that **eagerly computes scores at index time into a sparse matrix**, so query time is a sparse lookup. Pure **NumPy + optional Numba**; no torch, no server. | **Up to ~500× faster than `rank_bm25`**; >100× on 10 of 14 BEIR datasets; e.g. **~1196 QPS on NFCorpus** single-threaded. | Tiny (numpy/scipy; optional numba). | **Recommended sparse impl.** Deterministic, offline, trivially fast at our scale. |
| **`fastembed`** BM25 (Qdrant) | ONNX-runtime embedding lib that *also* ships a sparse BM25 (and SPLADE++). No PyTorch — designed for serverless. | Fast; ONNX-backed. | Medium (onnxruntime), but one lib for both sparse and dense. | Attractive if we also take its dense models — one dependency covers both channels (§7). |

**Determinism:** BM25 is a deterministic scoring function of (query tokens, corpus token
statistics). Same pinned corpus + same tokenizer + same prompt ⇒ identical scores and
identical ranking (ties broken by a stable sort). This satisfies the hard requirement with
zero extra machinery.

### 1.2 Stemming / synonym expansion — and what breaks without them

The `design.md` appendix proposes "keyword match over `name`+`summary`+`category`." Naive
**substring** matching is the zero-dependency floor, but it breaks on two axes that BM25
with a normal analyzer partially fixes:

- **Morphology.** "hopping platform" vs a part summarised "hop pad"; "spikes" vs `spike`.
  A stemmer (Snowball/Porter) or lemmatiser normalises these. `bm25s` supports a stemmer
  hook (e.g. PyStemmer). Cheap, deterministic, worth having.
- **Synonymy / fictional vocabulary — the real hole.** BM25 is *lexical*: it matches
  tokens, not meaning. A prompt "**volcano hopper**" shares **zero tokens** with a bank
  entry `lava_hazard` ("pool that kills on contact") or `stone_platform`. Stemming does
  not help — there is no shared stem. The only lexical fixes are:
  - a **hand-maintained synonym map** (`volcano→{lava, magma, eruption}`,
    `hopper→{jump, bounce, hop}`) expanded into the query. This works but is
    **curation debt that grows with every new theme** — exactly the "bank is only as good
    as its curation" tax (`design.md` A.1, Roblox-Toolbox lesson), now paid on the
    *retriever* too; and
  - tag richness: authoring generous `tags` per part (`lava_hazard` tagged
    `fire, volcano, magma, heat, lethal`). This is the cheapest real mitigation and should
    happen regardless — but it is still a finite, hand-authored vocabulary.

**Verdict.** BM25 (`bm25s`) + a stemmer + rich `tags` is the correct, deterministic,
near-zero-dependency **first** retriever, and for the current base-of-games campaign
(literal prompts like "stack two crates", "steer a raft to the dock") it is likely
*sufficient* — those prompts share tokens with the parts they need. The synonym hole only
bites on the **exotic prompts of pyramid rung 3**, which is precisely where a dense channel
earns its place (§2, §7).

---

## 2. Dense local embeddings (CPU-only, Windows)

A bi-encoder embeds the **bank once** (offline, at part-admission / bank-CI time) into a
fixed `(N, d)` float array, and embeds only the **~30-token prompt per run**. Retrieval is
then one cosine/matmul. The bank vectors are pinned data; the prompt vector is the only
per-run computation. Candidate models, all runnable CPU-only on Windows with no GPU:

| Model | Params | Dim | Max tokens | License | Runtime | Notes (verified) |
|---|---:|---:|---:|---|---|---|
| **`sentence-transformers/all-MiniLM-L6-v2`** | ~22.7 M | 384 | 256 | Apache-2.0 | torch **or** ONNX | The default small English encoder; ~80-90 MB. Ubiquitous, well-understood. |
| **`BAAI/bge-small-en-v1.5`** | 33.4 M | 384 | 512 | MIT | torch/ONNX | Retrieval-tuned; generally tops MiniLM on MTEB retrieval at ~same size. Longer 512-token window (irrelevant for 30-token prompts). |
| **`thenlper/gte-small`** (Alibaba) | ~30 M class | 384 | 512 | MIT | torch/ONNX | Same small-encoder class as the two above; a fine third option. (Exact MTEB/param figures not re-verified here — treat as "peer of bge-small".) |
| **`nomic-ai/nomic-embed-text-v1.5`** | 137 M | 768 (Matryoshka 64-768) | 8192 | Apache-2.0 | torch/ONNX/GGUF | ~274 MB; long-context + Matryoshka (truncate the vector to 64-256 dims at negligible quality loss). **Overkill for 30-token docs** — the 8192 context and 137 M params buy nothing here. |
| **`minishlab/potion-retrieval-32M`** (Model2Vec) | 32 M table | 512 | n/a (token lookup) | MIT | **pure NumPy — no torch** | **Static** embeddings: the "model" is a lookup table; encoding = token-vector lookup + mean-pool. Retrieval-tuned. Reaches **86.65 % of all-MiniLM-L6-v2** (retrieval score 36.35). |
| **`minishlab/potion-base-8M` / `-32M`** (Model2Vec) | 8 M / 32 M | 256 / 512 | n/a | MIT | pure NumPy | General static embeddings. `-8M` = 51.32 MTEB avg (91.96 % of MiniLM); `-32M` = 52.83 (94.66 %). |

### 2.1 Why the static (Model2Vec / "potion") models matter most here

Model2Vec (`MinishLab/model2vec`, MIT) distils a sentence-transformer into a **static
token→vector table** (via Tokenlearn + PCA + SIF re-weighting). At inference there is **no
attention and no matrix-multiply stack** — embedding a string is a token lookup plus a
mean, doable "in pure NumPy or even C" **without PyTorch**. Consequences that line up
exactly with this project:

- **Latency: sub-millisecond per short string, ~ms to embed the whole 500-doc bank.**
  (The project's tagline is "shrink 50×, run 500× faster" vs the transformer teacher.)
- **No torch install.** For a determinism-first, offline harness on Windows, dropping the
  ~2 GB torch/CUDA surface is a real operational win and matches the "local-first" ethos.
- **Determinism is structural, not incidental (see §6).** The artifact *is* a fixed array
  of vectors; there is no float-nondeterminism from attention kernels or thread scheduling,
  and no quantization ambiguity. Byte-identical input ⇒ byte-identical vector.
- **Quality cost is small and — critically — it still closes the fictional-vocab gap.**
  ~87-95 % of MiniLM on general benchmarks. Since our win condition is "connect *volcano*
  to *lava*," not "win MTEB," this is more than enough. If a measured gap appears on the
  exotic test set (§7), swap up to `bge-small-en-v1.5` behind the same interface.

**Recommendation within the dense channel:** default to **`potion-retrieval-32M`** (static,
no torch, deterministic, ms latency); keep **`bge-small-en-v1.5`** as the drop-in
"more-quality" alternative if the eval demands it. Both are 384-512-dim, both CPU-trivial.

### 2.2 Embed-the-bank-once vs embed-the-prompt-per-run

This is the standard bi-encoder property and it's why dense retrieval is cheap here:

- **Bank (docs):** embedded **once**, at part admission / bank-CI, into
  `banks/parts/<version>/index/embeddings.npy` (float32, shape `(N, d)`, L2-normalised).
  Committed as versioned data and hashed into `bank.lock` (§6).
- **Prompt (query):** embedded **once per run** — a single ~30-token string. Static model:
  <1 ms. Transformer on CPU: ~10-30 ms `[est.]`. Negligible against a 100-700 s generate.

Cross-encoders (§3) do **not** have this property — they must jointly encode
(query, doc) for *every* doc at query time, which is why they cost 100 ms-2 s even at our
scale. That asymmetry is the whole argument against a reranker here.

---

## 3. Hybrid fusion + reranking

### 3.1 Reciprocal Rank Fusion (RRF)

To combine the BM25 ranking and the dense ranking, use **RRF** (Cormack, Clarke, Büttcher,
*SIGIR 2009*, "Reciprocal Rank Fusion outperforms Condorcet and Individual Rank Learning
Methods"). For each doc, `score = Σ_retrievers 1 / (k + rank)`, `k ≈ 60` (the empirical
sweet spot from the paper; `k ∈ [40, 80]` performs comparably). It is the default hybrid
method in OpenSearch, Elasticsearch, Azure AI Search, MongoDB Atlas, and Weaviate.

Why RRF and not score-weighting: it operates on **rank positions**, so it needs **no score
normalisation** between the (unbounded) BM25 scores and (cosine ∈ [-1, 1]) dense scores —
a one-line, parameter-light, deterministic fusion. Perfect for our scale.

### 3.2 Is a cross-encoder reranker worth it at 60-500 docs? — No.

A cross-encoder (e.g. `cross-encoder/ms-marco-MiniLM-L-6-v2`, 6 layers, 512 max tokens,
MS-MARCO-trained; or `BAAI/bge-reranker-base` / `-v2-m3`) jointly scores (query, doc) pairs
for higher precision. Measured reality against our constraints:

- **Latency:** ~**100-300 ms to rerank 50 candidates** with the MiniLM cross-encoder on
  CPU; other evals report **560-2100 ms**. That's 20-2000× the cost of the entire
  bi-encoder retrieval it's supposed to refine.
- **Off-domain, it can *hurt*.** In one evaluation on a technical corpus, off-the-shelf
  cross-encoders (`ms-marco-MiniLM`, `bge-reranker-base`) **degraded NDCG by -0.3 % to
  -3.1 %** because web-search-trained rerankers don't transfer to the domain. Our
  "documents" are 30-token calibrated-physics blurbs in invented game vocabulary —
  about as far from MS-MARCO web passages as it gets. High risk of the same non-transfer.
- **The job is easy.** We retrieve an **8-15-item menu out of 60-500**; we are not chasing
  the single best passage out of millions. RRF over BM25+dense already puts the right parts
  in a generous top-15 window; a reranker's precision-at-1 gains are irrelevant when the
  model then picks from the whole menu anyway.

**Verdict: skip the reranker.** Revisit only if the eval (§7) shows RRF systematically
burying used parts below rank 15 — unlikely at this corpus size.

### 3.3 Is a vector DB (faiss/chroma) worth it? — No.

An exact cosine is `normalize(bank) @ normalize(prompt)` — a `(500, 384) × (384,)` matmul,
**microseconds** in NumPy, plus an argpartition for top-k. faiss/chroma exist to make
*approximate* NN tractable at 10⁶-10⁹ vectors; at 500 vectors the ANN index build costs
more than the exact scan and introduces approximation error for no latency benefit. Adding
a vector DB here is the retrieval equivalent of the MCP-server over-reach the bank already
rejected. **Store the bank matrix as a committed `.npy`; scan it exactly.**

---

## 4. Is a separate "buzzword extraction" step needed? — No.

Candidate extractors and what the evidence says:

- **YAKE** — unsupervised, statistical (term frequency, position, co-occurrence);
  lightweight, language/domain-agnostic, **no training**, fast.
- **KeyBERT** — embeds the doc and candidate phrases with BERT, ranks by cosine. **9-14 %
  better keyphrases than YAKE** in some studies, but **much slower** (~360 s vs ~80 s on
  one corpus) and it *is* an embedding model — so if we're embedding anyway, running
  KeyBERT first is redundant work.
- **LLM extraction** — a Haiku call to pull "keywords" — adds latency, cost, and
  non-determinism for a 30-token input.

Two reasons extraction is the wrong move for **short-query → short-doc** matching:

1. **The prompt is already the keywords.** Extraction exists to compress *long* documents
   down to their salient terms. A 30-token game prompt ("swing a wrecking ball to knock a
   tower into a pit") has no filler to strip — embedding or BM25-scoring it **raw** keeps
   every signal. Extracting first throws away context ("swing", "knock", "into") that the
   dense channel uses to disambiguate.
2. **Extraction doesn't fix the actual gap.** The hard case is *fictional-vocab synonymy*
   ("volcano hopper"). Pulling the keyword "volcano" out of the prompt still leaves you
   needing to map *volcano → lava* — which is the synonym/embedding problem, untouched by
   extraction. So extraction adds a step and solves nothing that BM25+dense doesn't already
   handle better.

**Verdict: embed / BM25-score the raw prompt.** No YAKE/KeyBERT/LLM pre-step. (Light,
deterministic normalisation — lowercase, punctuation strip, stopword drop for the BM25
channel — is fine and is not "extraction".)

---

## 5. LLM-as-selector — the alternative baseline

Instead of retrieval, let an LLM pick the parts. Two shapes:

**(a) The generation call itself picks from the full compact catalog.** Inline all ~60
entries' one-liners (~**+3600 tokens**, per `design.md` B.3) and let Opus choose while it
writes the game. No separate call, no retriever.

**(b) A cheap pre-call selects the menu.** A `claude-haiku-4-5` call
(**$1 / 1M input, $5 / 1M output**, 200 K context) reads the full catalog + prompt and
returns the 8-15 names, which are then injected as the themed menu.

### 5.1 Prior art: Anthropic's own Tool Search Tool (this is the pattern, verified)

Anthropic's **Tool Search Tool** (Advanced tool use, `tool-search-tool`) is *exactly*
"retrieve capabilities into a model's context," and it ships **BM25 and regex** variants —
strong prior art that lexical retrieval is the right default even inside Anthropic's own
stack:

- Tools you mark **`defer_loading: true`** are discoverable but kept out of context until
  the model searches. `tool_search_tool_bm25_20251119` takes a natural-language query and
  ranks by **BM25**; `tool_search_tool_regex_20251119` uses Python `re.search` patterns.
- Measured: **~85 % context reduction** (77 K → 8.7 K tokens on a large tool library);
  MCP-eval accuracy **Opus 4: 49 % → 74 %**, **Opus 4.5: 79.5 % → 88.1 %**. Recommended
  "when tool definitions consume > 10 K tokens" / "10+ tools." Anthropic explicitly notes
  you can **implement a custom search tool using embeddings** instead.
- **This is not hypothetical for us — it is the mechanism running this very research
  session:** most tools were deferred and surfaced by name only, and a `ToolSearch` step
  had to fetch their schemas before use. The parts-bank menu is the same problem
  one level down: retrieve the relevant *nouns* into the generator's context instead of
  dumping the whole catalog.

The lesson we take: BM25/lexical retrieval into context is a **proven, Anthropic-endorsed
default**; embeddings are the documented upgrade when lexical misses (our fictional-vocab
case). It does **not** argue for an LLM *judging* the selection — Tool Search is retrieval,
then the model consumes the retrieved set.

### 5.2 Cost / latency / quality / determinism vs embedding retrieval

| Axis | Embedding/BM25 retrieval (recommended) | (a) Full catalog in the generate call | (b) Haiku selector pre-call |
|---|---|---|---|
| **Per-run $** | **$0** (offline, no API) | +3600 input tokens on the *Opus* generate: ~$0.018 uncached, ~**$0.0018** prompt-cached (~0.1× read) | catalog+prompt in ≈3.8 K tokens → ~**$0.004** ($0.0007 cached); Batch API halves it |
| **Latency** | **< 5 ms** static / ~30-50 ms transformer, local | ~0 extra (folded into the generate) but grows the prompt | +0.5-2 s round-trip + network |
| **Quality** | Good; tunable via hybrid + threshold | Highest ceiling (model sees everything) but "more than doubles the prompt and blunts the model" — the verb-shaped-API concern in `design.md` B.3 | Good; a capable judge — but a second failure surface |
| **Determinism (HARD requirement)** | **Pure function of prompt bytes + pinned index — reproducible by construction** | Non-deterministic selection *and* it's inside the generate, so it can't be pinned separately | **Non-deterministic**; provider-side sampling + silent model updates ⇒ "same prompt+bank_version ⇒ same menu" not guaranteed without caching the mapping |
| **Offline / integrity** | Yes — no network in the loop (matches `mcp_tools.md` §5) | n/a (already calling the model) | Adds a network call to the pinned path |

**Reading:** option (a) is a legitimate *fallback for exotic prompts* (spend the 3600
tokens when the bank clearly won't cover the fiction — the escape hatch already exists) and
its cost is largely amortised by prompt caching across a batch. But as the **default pinned
retrieval path it fails the determinism gate**, and it re-inflates the prompt the two-tier
design was built to keep small. Option (b) is the closest LLM analogue to our retriever and
is cheap, but it trades the project's one non-negotiable — reproducibility — for a
capability (semantic matching) that a static embedding delivers deterministically and for
free. **Keep the LLM strictly as the escape hatch / bank-miss handler, not the selector.**

---

## 6. Determinism & ops

Determinism is the project's spine (seeded runs, integrity manifest, G1 "two identical
seeded runs ⇒ identical snapshots"). The retriever must not become the weak link.

### 6.1 What "deterministic retrieval" requires

- **Same prompt + same `bank_version` ⇒ same retrieved set.** Achieved iff the retriever is
  a pure function of (prompt bytes, pinned index, pinned model, fixed tie-break). BM25 and a
  bi-encoder cosine both satisfy this; an LLM call does not (§5).
- **Pin the model, not just the catalog.** `bank.lock` already carries
  `sha256(catalog)`. Extend it: `retriever: { kind: "bm25s"|"potion-retrieval-32M"|...,
  model_sha256: "...", index_sha256: "..." }`, and fold the embedding array + retriever
  config into `integrity.snapshot()` exactly like the catalog (`design.md` B.4). A change
  to the embedding model file then invalidates a mid-run swap the same way base-code changes
  do — because it *would* change retrieved sets, i.e. it is semantically a new bank version.

### 6.2 Floating-point / quantization pitfalls (why static wins)

- **Transformer encoders (MiniLM/bge/gte via torch or ONNX):** the same model file + same
  input on the **same machine + same thread/backend config** is deterministic; but results
  can differ across CPU microarchitectures, thread counts, or BLAS/ONNX-runtime versions at
  the last-decimal level — usually harmless to ranking, occasionally flips a near-tie. If we
  use one, **pin the exact ONNX artifact and runtime**, set single-thread deterministic
  execution, and store the fp32 (not a re-quantized) vectors.
- **Quantization is a real trap.** An int8-quantized MiniLM produces a *different* vector
  than the fp32 model — so silently switching the artifact changes retrieved sets. If ever
  quantizing for speed (unnecessary at our scale), the quantized file must be the hashed,
  pinned artifact.
- **Static embeddings sidestep all of this.** `potion-*` encoding is table-lookup +
  mean-pool over integers/floats with no attention kernels — **byte-reproducible across
  machines**, no quantization ambiguity, nothing to pin beyond the (small) table itself.
  This is the strongest determinism story and the reason it's the default recommendation.

### 6.3 Cache / index layout

```
banks/parts/<version>/
  catalog/…                      # the JSON entries (already planned)
  index/
    retriever.json               # {kind, model_sha256, bm25_params:{k1,b}, tokenizer, rrf_k:60, threshold}
    embeddings.npy               # float32 (N, d), L2-normalised — the "embed the bank once" artifact
    bm25/                        # bm25s serialized index (or rebuilt deterministically from catalog at load)
  bank.lock                      # {version, sha256(catalog), retriever.model_sha256, index_sha256}
```

The index is **built once when a part is admitted** (piggy-backing bank-CI), committed, and
hashed. Loading is a memory-map; per run we only embed the prompt and scan.

### 6.4 Fallback when retrieval returns junk ("bank miss" as a growth signal)

- **Score threshold / escape hatch.** If the fused top-1 (or top-k mean) is below a tuned
  floor — dense cosine below `τ`, and/or BM25 top score below a floor — treat it as a
  **bank miss**: inject a smaller/empty menu (or none) and let the generator fall back to
  the `world.add` free-code escape hatch that already exists for exotic prompts
  (`design.md` B.2). The generator is never *blocked* by a bad retrieval.
- **Log it.** Append `{prompt, bank_version, top_scores, retrieved, miss:true}` to the
  ledger. A recurring bank-miss cluster is a **precise, data-driven signal of which
  archetype to author next** — the retriever's misses directly feed the bank-growth
  workstream (and the effective-diversity metric). This turns the failure mode into the
  roadmap input, in the spirit of the ledger-driven "count every failure" directive
  (OBJECTIVES, 2026-07-13).

---

## 7. Recommendation for OUR scale

### 7.1 The concrete architecture

```
prompt ─▶ normalize (lowercase, strip punct, drop stopwords)
        ├─▶ BM25 (bm25s, stemmed)  over name+summary+category+tags  ─▶ ranked list A
        └─▶ dense (potion-retrieval-32M, static)  cosine vs embeddings.npy ─▶ ranked list B
                                                    (exact numpy matmul; NO vector DB)
        ─▶ RRF fuse(A, B, k=60) ─▶ top-15
        ─▶ threshold gate: fused top scores < τ ?  ──yes──▶ bank-miss: escape hatch + ledger log
                                                  ──no───▶ inject 8-15-part themed menu (design.md Tier-1b, ~+200 tok)
```

- **No reranker, no vector DB, no keyword-extraction step, no network.** Everything runs
  locally in NumPy in single-digit ms.
- **Bank embedded once** at admission (hashed into `bank.lock`); **prompt embedded per
  run** (<1 ms static).
- **Deterministic** end-to-end: pure function of prompt + pinned index.

### 7.2 Decision table — 60 now vs 500 later

| Concern | Bank = ~60 (now) | Bank = ~500 (later) |
|---|---|---|
| **Sparse channel** | `bm25s` — likely *sufficient alone* for literal base-of-games prompts | `bm25s` — still instant; needed as the exact-match anchor |
| **Dense channel** | Optional at first; add when rung-3 exotic prompts land | **Yes** — synonym/fictional coverage matters more as breadth grows |
| **Fusion** | RRF (or BM25-only) | RRF |
| **Reranker** | No | No (still 8-15-of-500; RRF window suffices) |
| **Vector DB** | No | **No** — exact scan of 500×384 is still µs |
| **Index build** | trivial (<0.1 s) | ~ms (static) / ~1-5 s once (transformer) `[est.]` |
| **Per-run retrieval latency** | **< 5 ms** | **< 5 ms** static / ~30-50 ms transformer `[est.]` |
| **Per-run $ / tokens** | **$0**, 0 retrieval tokens (menu injection ~+200 tok is separate) | same |

**Sequencing (matches the pyramid):** ship **BM25-only** with the base-of-games campaign
(literal prompts, zero new heavy deps); add the **static dense channel + RRF** as the bank
grows and rung-3 exotic prompts arrive (where fictional vocab appears). This is a one-time,
non-breaking addition behind the same `retrieve()` interface.

### 7.3 Expected latency / cost summary

- **Retrieval latency per run:** < 5 ms (static) to ~50 ms (transformer) on CPU —
  **negligible** vs the 100-700 s generate seen in the ledger.
- **Retrieval $ per run:** **$0** (offline). The only token cost is the injected menu
  (~+200 tokens, prompt-cached across a batch ⇒ amortised ≈0), vs +3600 for a naive full
  catalog and vs a ~$0.004 Haiku selector call.
- **Dependencies added:** `bm25s` (numpy/numba) now; `model2vec` (numpy) later — both
  torch-free, offline, license-clean (MIT). No server, no DB, no MCP.

### 7.4 Evaluation protocol

Two complementary evals; both cheap and reusable.

1. **Coverage on ledger data (once the `Parts used:` DESIGN line lands, `design.md` B.3).**
   For each certified game, compute **retrieved ∩ actually-used-parts**:
   `recall@15 = |used ∩ retrieved| / |used|` (did the menu contain everything the model
   actually used?) and `precision` / menu-efficiency (how much of the 8-15 menu got used).
   Target: **recall@15 ≈ 1.0** (a used part that wasn't offered is a retrieval miss the
   escape hatch had to paper over). Aggregate per `bank_version` in `harness game stats`.
2. **Hand-built exotic-prompt test set (the fictional-vocab stress test).** ~20-40 pairs of
   `prompt → expected parts`, deliberately including invented vocabulary:
   `"volcano hopper" → {lava_hazard, stone_platform, launch_pad}`,
   `"haunted seesaw" → {seesaw(mobile), trigger_zone, ...}`, etc. Report recall@15 for
   **BM25-only vs dense-only vs hybrid(RRF)** on this set. The expected result — and the
   justification for the dense channel — is that **BM25-only collapses on the fictional
   pairs while hybrid recovers them**; if it doesn't, we've saved ourselves the dense
   dependency. This set doubles as a regression guard when the bank or model is re-pinned.

Tune the **threshold `τ`** (§6.4) on set (2): pick the floor that flags the truly-uncovered
prompts as bank-misses without false-flagging covered ones.

---

## 8. What we are deliberately NOT doing (over-engineering ledger)

| Tempting | Why it's wrong at 60-500 docs |
|---|---|
| faiss / chroma / Qdrant server | ANN for millions of vectors; exact NumPy scan is faster here and has no approximation error or network/DB dependency. |
| Cross-encoder reranker | 100 ms-2 s latency; off-domain it *degrades* NDCG (-0.3 to -3.1 %); our menu is 8-15-of-500, not top-1-of-millions. |
| YAKE / KeyBERT / LLM keyword extraction | The 30-token prompt is already keywords; extraction is for long docs and doesn't fix the synonym gap. |
| LLM-as-selector on the pinned path | Non-deterministic; violates "same prompt+bank_version ⇒ same menu." Fine only as the bank-miss escape hatch. |
| nomic-embed / large 137 M+ encoders | 8192-context, 137 M params buy nothing for 30-token docs; small (or static) encoders match quality at a fraction of the cost. |
| int8-quantizing the encoder for speed | Unnecessary (already ms) and it changes the vectors → a determinism/versioning trap. |

---

## 9. Sources (fetched/verified 2026-07-13)

**Sparse / BM25**
- BM25S — HF blog https://huggingface.co/blog/xhluca/bm25s ; repo https://github.com/xhluca/bm25s ; paper "BM25S: Orders of magnitude faster lexical search via eager sparse scoring" https://arxiv.org/abs/2407.03618 (up to ~500× vs rank_bm25; ~1196 QPS NFCorpus)
- `rank_bm25` (baseline, sub-1 QPS on BEIR) — https://github.com/dorianbrown/rank_bm25
- FastEmbed (Qdrant, ONNX, no PyTorch; dense + BM25 sparse) — https://github.com/qdrant/fastembed ; https://qdrant.tech/documentation/fastembed/

**Dense embedding models (model cards)**
- all-MiniLM-L6-v2 (22.7 M, 384-dim, 256 max, Apache-2.0) — https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2
- bge-small-en-v1.5 (33.36 M, 384-dim, 512 max, MIT) — https://huggingface.co/BAAI/bge-small-en-v1.5
- gte-small (thenlper/Alibaba, 384-dim, MIT) — https://huggingface.co/thenlper/gte-small
- nomic-embed-text-v1.5 (137 M, 768-dim Matryoshka 64-768, 8192 ctx, Apache-2.0) — https://huggingface.co/nomic-ai/nomic-embed-text-v1.5
- Model2Vec (MIT; static, no-torch NumPy inference) — https://github.com/MinishLab/model2vec ; results https://github.com/MinishLab/model2vec/blob/main/results/README.md
- potion-retrieval-32M (86.65 % of MiniLM, retrieval 36.35) — https://huggingface.co/minishlab/potion-retrieval-32M
- potion-base-8M (51.32 MTEB / 91.96 %) — https://huggingface.co/minishlab/potion-base-8M ; potion-base-32M (52.83 / 94.66 %) — https://huggingface.co/minishlab/potion-base-32M

**Fusion / reranking**
- RRF — Cormack, Clarke, Büttcher, SIGIR 2009 (k≈60; default in OpenSearch/Elasticsearch/Azure AI Search/MongoDB Atlas/Weaviate) — https://plg.uwaterloo.ca/~gvcormack/cormacksigir09-rrf.pdf ; overview https://bigdataboutique.com/blog/reciprocal-rank-fusion-how-it-works-and-when-to-use-it
- Cross-encoder rerankers — cross-encoder/ms-marco-MiniLM-L-6-v2 https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2 ; SBERT pretrained rerankers https://sbert.net/docs/cross_encoder/pretrained_models.html ; CPU latency + off-domain NDCG regression figures https://docs.bswen.com/blog/2026-02-25-best-reranker-models/

**Keyword extraction**
- YAKE vs KeyBERT (statistical vs embedding; speed/quality trade-off) — KeyBERT https://github.com/MaartenGr/KeyBERT ; YAKE https://github.com/LIAAD/yake ; comparison e.g. https://pmc.ncbi.nlm.nih.gov/articles/PMC9202614/

**LLM-as-selector / prior art**
- Anthropic Advanced tool use (Tool Search Tool: BM25 + regex, defer_loading; 85 % token cut 77K→8.7K; Opus 4 49→74 %, Opus 4.5 79.5→88.1 %; >10K tokens / 10+ tools) — https://www.anthropic.com/engineering/advanced-tool-use ; docs https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool
- Tool Search tool types `tool_search_tool_bm25_20251119` / `tool_search_tool_regex_20251119`, Haiku 4.5 pricing ($1/$5 per 1M), Batch API 50 % / prompt-cache ~0.1× read — Anthropic `claude-api` skill reference (bundled), 2026-06-24 cache.
```
