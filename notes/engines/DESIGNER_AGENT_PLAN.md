# Designer-agent for gi-harness — implementation plan (highest-precaution)

> Implementation plan, 2026-07-14 (Fable orchestrator + Opus agents).
> **Commissioned by Elias:** a repo-encapsulated designer agent — frozen human-authored tools,
> agent-grown skills, bounded prompt self-modification. Cage-before-animal phasing; every phase gate
> is a non-regression guard. Companion to `SELF_IMPROVING_DESIGNER.md` (§4 frozen/data-only split,
> §5 failure modes, §6 build order) — this note turns that design into a P0→P6 build with gates,
> rollbacks, and review checkpoints. Every `path:line` below is resolved against the live repo.

---

## 1. PRINCIPLE (6 lines)

1. The research payoff is **accumulating artifacts, not a smarter model** — the frozen deepseek brain stays in context and is given a *principled* way to create its own skills and tools.
2. **Tool LAYER is frozen + human-authored; the agent grows SKILLS (repo markdown).** The agent may *propose* the tool decomposition; humans *freeze* it.
3. **Everything the agent creates lives in-repo and is readable** — every skill, memory delta, proposal, and DB touch is versioned/hashed/attributable/revertible in git.
4. The **verifier + G-gate/`[eng.]` thresholds are never agent-writable** — enforced by the integrity manifest, not by good behavior; thresholds are never a tool *parameter*.
5. The agent **may** edit our game prompts (could be good) but **must not go overboard** — bounded, staged, gated, human-merged.
6. **Library-first** for all learning machinery; the inner code-writer stays wrapped inside `generate_game`/`revise_game` and is never a tool.

---

## 2. SUBSTRATE — RESOLVED: hybrid thin-loop, not a Hermes fork

Build the designer as a **thin OpenAI-SDK loop** on the exact primitive Hermes sits on (`openai==2.24.0`, hermes `pyproject.toml`), pointed at **deepseek via OpenRouter** (`provider: custom` + `base_url https://openrouter.ai/api/v1`; `cli-config.yaml.example:37,43,47,763`; provider inferred from base_url `model_metadata.py:445`). Dispatch is native OpenAI function-calling on `function.name`/`arguments` (hermes `tool_executor.py:363-366`). **Vendor three self-contained MIT modules, adopt no runtime:**
- `agent/skill_utils.py` — SKILL.md detect + frontmatter parse + progressive-disclosure of `references/templates/assets/scripts` + platform/env scoping (`:50,73,95,123,188,272`). This is our skill loader.
- `agent/file_safety.py` — `HERMES_WRITE_SAFE_ROOT` allowlist + sensitive-path write-deny + realpath/symlink-escape guard (`:29-64,81-148,205`). Informs (does not become) `designer_write`.
- `hermes_cli/oneshot.py` **stdout contract only** (prompt in → final text out) for cluster batch — **without** its footgun (oneshot auto-sets `HERMES_YOLO_MODE=1`; default backend is the unsandboxed host shell, `tools/environments/local.py:9`). We never inherit that posture.

**Why not the full kernel:** no clean boundary (lateral imports across 150–340 KB files, `conversation_loop.py` 308 KB); security defaults fight our integrity model; daily upstream churn (HEAD `df5700e`, PR #64597) drifts a frozen fork immediately.
**Runner-up (documented, not chosen):** **Claude Agent SDK** — cleaner sandbox/permission model, native SKILL.md, genuinely library-first; rejected as primary *only* because the standing constraint pins the brain to deepseek. It becomes the better choice the moment that constraint relaxes. **The substrate decision is itself a P0 review item for Elias.**

---

## 3. THE TOOL SET v0 (frozen read/oracle spine)

Hybrid of Dive-B Set-2 (task-shaped) + the one good Set-3 idea (unified `design`). **No write verbs in the tool spine** — `designer_write` (§4) is the only write path.

| tool | typed in | typed out | wraps (FROZEN) | cost |
|---|---|---|---|---|
| `design` | `prompt_or_source, directive?, engine?, backend?` | generation report | `generate_game` ∪ `revise_game` (`gamegen.py:807`/`:844`); `source` present ⇒ revise | LLM |
| `certify` | `game_path, depth=verify\|harden\|grade\|full` | `{verdict,stage,hint,witness,g4_grade,learnable}` + verbose handle to full report | `verify_game` (`gameverify.py:1105`, `run_episode:128`, G0–G3, thresholds `:1297`); `harden`=`attack_game` (`g4.py:1199`, `classify:287`/`refute_prefix:929`); `grade`=`g3_prime` (`rl/certify.py:72`) | verify=cheap; harden/grade=RL/attacker |
| `retrieve_parts` | `prompt, engine` | `{menu_text,menu_mode,names,scores}` | `retrieval.retrieve_menu` (`retrieval.py:344`, `score:205`) — pure fn of `(prompt,bank_version)` | free |

`certify` is the load-bearing trust boundary — freeze it first; **thresholds are never a parameter**; default `depth=verify` (HPC guest). **What the agent CANNOT do through this set:** touch any verifier/threshold; call the inner deepseek except via `design`; write anything (spine is read/oracle-only); promote a game past `certify`; reach `harden`/`grade` by default (explicit + budgeted). **Excluded by design (headless-dead/operator-only):** `watch`/`demo`/`--live` (pygame, no display), `ledger merge`, `stats` — stay human-CLI.
**MCP wrap note:** each tool maps to a clean stateless typed MCP tool; the future `propose_*` verbs (§5 P4/P5) need MCP human-in-the-loop/elicitation and must enforce *staged-only + gate-run SERVER-SIDE*, never client-trusted.

---

## 4. WORKSPACE + WRITE BOUNDARY + BUDGETS

Repo-root `designer/` (sibling to `harness/ banks/ notes/` — deliberately **not** swept by integrity's `harness/**` walk, so inclusion is explicit/auditable):
`SYSTEM.md` (human-seeded prompt) · `skills/<slug>.md` (Tier-A direct) · `memory/LESSONS.md`+`attic/` (Tier-A append-only) · `proposals/<wave>/<id>.md` (staging) · `ledger/designer.jsonl` (tool-written append-only) · `workspace/<run_id>/` (**gitignored**, ephemeral) · `BUDGETS.md` (human-authored, **NOT agent-writable**). Every artifact carries frontmatter `id,kind,created_by,run_id,wave,created_ts,parent,status,rationale,provenance`; commit trailer `Co-Authored-By: designer-session-<id>`; each wave = one tagged, revertible commit.

**Three tiers** (cross-checked vs `SELF_IMPROVING_DESIGNER.md` §4):
- **Tier A — DIRECT:** `designer/skills/*.md`, `designer/memory/*.md` (append-only), `proposals/**`, `workspace/<run_id>/**`.
- **Tier B — PROPOSAL+GATE only:** `harness/gen/prompts/{rules,orientation,api_godot,api_py,api_js,design_block,contract}.md` + `bank_menu.md.tmpl`; `banks/parts/v1/parts.json` (gated by `bank_ci.certify_bank`, `bank_ci.py:165`); `designer/SYSTEM.md`.
- **Tier C — NEVER (allowlist-reject AND integrity-frozen):** all `harness/**/*.py` (incl. `gameverify.py`, `g4.py`, `rl/certify.py`, `core/integrity.py`, `bank.py`, `telemetry.py`, `curriculum.py`, `retrieval.py`), all G-gate/`[eng.]` thresholds, `CONTRACTS.md`/`OBJECTIVES.md`/`SPEC_VERIFIER.md`, CI/hooks, `BUDGETS.md`, the write-tool + ledger-writer. **v0 mechanical rule: anything resident in a `.py` file is Tier-C-for-direct-write** — data-only code knobs (`retrieval.py` SCORE_THRESHOLD/`_QUERY_SYNONYMS`, hint tables) may only be *proposed* and **human-transcribed**.

**Enforcement, four layers:** (1) **one** write tool `designer_write(path,content,mode)` — realpath-resolve, reject `..`/symlinks/out-of-repo, compiled Tier-A allowlist, Tier-B→`proposals/` only, Tier-C hard-reject, caps + append-only, logs every call (accepted/rejected+reason) to ledger; the agent has **no** general fs/bash write. (2) Extend `integrity.tracked_files()` with `designer/SYSTEM.md`+`skills/*.md`+`memory/*.md` and fold `bank.content_hash` (`core/bank.py:92`) into `snapshot()` — closes the gap at `bank.py:97-99`; wired at `core/integrity.py:46-106` / `gamegen.py:901,923-928`. (3) **git pre-commit hook** (+ CI mirror) — the *only* gate on between-run edits; blocks Tier-C, integrity/verifier/threshold mutation (golden-checksum), out-of-allowlist designer commits, budget overrun. (4) Ledger attribution + trailer.

**Budgets (numbers):** skills ≤200 lines / ~1500 tok each, ≤25 active, ~5000-tok total; `rules.md`/`orientation.md` ≤~120 lines each; memory + rules/orientation are **append-only delta** (ACE, arXiv:2510.04618 — a >5-line rewrite auto-rejected unless flagged eviction); ≤3 Tier-B proposals/wave, +15 lines/section, ≤2 sections/proposal, ≤40 lines/wave net; ≤2 repair iterations/proposal (Reflexion saturates, arXiv:2303.11366); **1 wave/day**; earn-your-place eviction → `memory/attic/` (v0 proxy: oldest-unreferenced, never hard-delete); auto-revert-on-regression (POET/GEPA transfer-if-better, arXiv:1901.01753/2507.19457).

**Global kill-switch:** `designer_write` is import-flag-gated; off ⇒ no write path exists. The agent is a separate entrypoint; disabling it falls back to the bare CLI. Verifier/thresholds/`integrity.py` are never in the loop, so no kill-switch is ever *needed* to protect them.

---

## 5. PHASES P0→P6 (the heart)

Strict prerequisite chain; no gate may be waived to reach the next. Single metric spine at every gate (§4 `SELF_IMPROVING_DESIGNER.md`): **completion_rate** (robustness) · **flagrant-rate** (safety) · **variety-coverage** (diversity), baselined at P0, seeded `--gen_seed`, on a **held-out suite that gates but never authors** (§5 eval-contamination guard).

**P0 — THE CAGE** (no LLM, no behavior change). Build: `designer/` tree + `designer_write` + the two integrity extensions + pre-commit hook + `BUDGETS.md`. **Gate:** path-traversal/symlink/Tier-C/append-only/budget unit tests; mid-run mutation of `skills/*.md` ⇒ `INVALIDATED`; `bank.content_hash` in snapshot; hook blocks a staged Tier-C edit; full existing G0–G3 suite green; golden checksum over `harness/**/*.py` unchanged; **capture the P0 baseline metrics**. **Rollback:** whole tree+tool+integrity edits = one tagged commit → `git revert`. **Review:** substrate decision, `SYSTEM.md` seed, `BUDGETS.md` numbers, the A/B/C allowlist, hook rules, ledger schema. ~4–6 dd.

**P1 — FROZEN TOOL LAYER v0** (§3). Build the three tools + function-calling registry; schemas frozen into the manifest; no agent yet. **Gate:** typed-I/O tests; `certify(depth=verify)` verdict ≡ `game verify` over a fixture corpus; `design` passes verify at ≥ baseline; `retrieve_parts` determinism; fixture metrics ≡ P0 baseline (tools transparent). **Rollback:** revert the module; CLI verbs untouched. **Review:** exact frozen signatures + typed schemas; confirms thresholds are not parameters. ~5–7 dd.

**P2 — AGENT ON SUBSTRATE, EXISTING TASK, ZERO SELF-MOD.** Thin loop (vendored `skill_utils.py` + `openai` + OpenRouter/deepseek), `HERMES_HOME`-analog → `designer/`; **run under `gi-certifier.sif` Singularity, no host shell**, `approvals.deny` hard-floor backstop; skills **human-seeded** from `harness/gen/prompts/{api_godot,api_py,api_js,rules,orientation}.md`; `designer_write` flag-**off**; curator/learning-loop disabled; SLURM etiquette (`--time`/`--requeue`, ≤4–5 jobs). **Gate (parity bar):** agent-through-tools vs bare-LLM baseline — completion within ε, flagrant/variety not worse; no mid-run integrity violation; repro; cost budget (`certify` default verify). **Rollback:** disable entrypoint ⇒ bare `generate_game`. **Review:** parity numbers, seeded skills, substrate/sandbox config, a sample run transcript (which skills + prompt-sections entered context — full context provenance). ~7–10 dd.

**P3 — GATED SELF-MOD: SKILLS + MEMORY ONLY.** Enable Tier-A `skills/*.md` (create/edit) + `memory/LESSONS.md` (append-only delta); skill verb with frontmatter validation + size caps + path guards; **skill support-dir hazard closed** — any `scripts/`/`assets/` is hashed AND gated (no executable payload past markdown review); curator stays disabled/propose-only; eviction → `attic/`. **Gate:** re-run held-out suite — completion ≥ P2 parity, flagrant/variety not worse; budget tests (26th skill rejected, append-only + >5-line-rewrite rejected); every artifact attributable + revertible; no smuggled payload; **auto-revert canary** on regression. **Rollback:** `git revert` the wave; canary automates the regression case. **Review (the "see what it creates for itself" checkpoint):** skills written, memory deltas, the **WHY** on every ledger write, eviction list, canary results. ~5–7 dd.

**P4 — PROPOSALS TO OUR GAME PROMPTS (Tier-B), eval-gated + HUMAN MERGE.** `propose_prompt_edit(section,delta)` stages a packet (append-structured-delta, never rewrite); a **separate human-triggered `apply_proposal`** does the real edit post-gate ⇒ human-frozen commit that re-hashes into the manifest — the agent **cannot** merge. Gate = the SAME G0–G3 witness-replay + G4 over the held-out suite; **per-surface** gates, not one omni-write. `parts.json` proposals stay deferred to P5. Graduation rule **STATED but OFF**. **Gate:** a proposal must PASS — completion up-or-flat, flagrant down-or-flat, **variety-coverage NOT down** (variety-collapse guard) — before a human may merge; post-merge canary must not auto-revert; **mid-run live prompt write ⇒ `INVALIDATED`** (proves between-run-only; hashed `gen/prompts/*.md` self-poison on live edit). **Rollback:** each wave = one tagged commit → `git revert`; staged proposals never touch live prompts pre-merge. **Review:** *every* proposal diff + rationale + gate result before merge; the variety delta specifically. ~6–9 dd.

**P5 — TOOL-SET EVOLUTION via agent PROPOSALS → HUMAN-IMPLEMENTED.** The agent writes a Tier-B design packet describing a wanted tool + typed I/O + rationale; a human ratifies + implements + freezes (re-enters the P1 gate). Candidates: `recall(query)` (FTS over own skills+ledger+certified specs), `reflect(scope)`, `render`, `iterate_curriculum` (`curriculum.py:468`), `revise_spec` as a separate verb, per-surface `propose_part` (gated by `bank_ci.certify_entry:156`). **Build the offline variety analyzer FIRST** (archetype-coverage + PATA-EC novelty) before exposing `reflect` — today `telemetry.stats` groups only by `(backend,model)` (`telemetry.py:242`), so `reflect` would return half a signal. **Gate:** any new tool re-passes P1 (typed-I/O, frozen, in-manifest) + P2 parity before the agent may use it; nothing agent-proposed lands without human implementation; analyzer validated on known-diverse/known-collapsed corpora. **Rollback:** new tools are additive; the frozen v0 spine is unaffected. **Review:** the proposed decomposition (does it match the retrieval/creation/verifier sketch?); ratify/reject each; the analyzer. ~6–10 dd.

**P6 — TIE-IN TO THE v1 CONTROLLER** (`SELF_IMPROVING_DESIGNER.md` §6). Controller orchestrates `design→certify→reflect→propose→gate→(human|auto)merge→canary` over waves; telemetry extended with **prompt-section-set hash + variant tag** (fixes the §4 attribution gap); `designer_stats` aggregator beside `telemetry.stats`. **Graduation enablement per surface class:** after **N=5 consecutive clean waves** (zero auto-reverts, zero human rejections) → auto-merge-**after-gate** (still frozen-eval + canary + auto-revert gated). `SYSTEM.md`, all `.py` code-knobs, and `parts.json` **NEVER** graduate. §6-v2: real per-lesson attribution replaces the oldest-unreferenced proxy; bank growth via `propose_part` behind `bank_ci` (`bank_ci.py:156/165`). **Gate:** a multi-wave run must show **BOTH axes rise-or-hold** (variety AND robustness — the thesis is proven only if both move), flagrant bounded, no unattributed merges, A/B reproducible via whole-prompt-set hash; each graduation flip needs the clean-wave criterion **and** Elias's sign-off. **Rollback:** disable controller ⇒ manual waves; every wave revertible; graduation is one revocable flag per class. **Review:** the both-axes trajectory, attribution telemetry, each graduation flip, bank-growth proposals. ~8–12 dd.

---

## 6. FAILURE MODES specific to an agent-in-the-repo

- **Prompt vandalism / bloat** (agent rewrites `rules.md`, trades variety for robustness) → append-only delta + per-section line caps + per-surface frozen eval gate that fails on variety-coverage drop + human-merge-only until P6 graduation; graduation stays OFF until the P5 variety analyzer exists.
- **Mid-run self-invalidation** (a live prompt/skill write poisons the run it means to help — hashed `gen/prompts/*.md`) → writes are strictly between-run on a staged copy; the P4 mid-run test asserts `INVALIDATED`.
- **Skill bloat / false-positive skills** (rationalize bad designs) → 200-line/25-file/5000-tok caps, earn-your-place eviction to `attic/`, and the held-out witness-replay gate (not an LLM critic, Voyager's #1 failure) decides whether a skill helps.
- **Executable-payload smuggling** (a skill `scripts/` support dir carries code a later turn runs) → support dirs are hashed AND gated, not markdown-only reviewed; the manifest covers skill files.
- **Memory poisoning / context collapse** (summarize-rewrite destroys prior lessons) → append-only-delta rule (ACE), >5-line rewrite auto-rejected, deltas frontmatter'd + revertible.
- **Gate gaming / eval contamination** (agent narrows to an easily-certified band, or judges itself) → the gate suite is held-out, gates-but-never-authors, and is the frozen witness-replay funnel — never the agent's own judgment; both-axes gate (P6) catches variety collapse.
- **Grader/boundary self-edit** (SICA inverse, arXiv:2504.15228) → verifier/thresholds/`integrity.py` are Tier-C AND integrity-frozen; a tool-decomposition proposal is just a Tier-B packet a human ratifies — never a day-one capability.
- **Second write path** (a generic bash/edit tool would leak the whole boundary) → hard requirement: `designer_write` is the *sole* write capability; no general fs/bash write, ever.

---

## 7. OPEN DECISIONS for Elias

1. **Substrate:** ratify hybrid thin-loop (vendor `skill_utils.py`/`file_safety.py` + oneshot stdout contract), or revisit Claude Agent SDK if the deepseek-brain constraint can relax?
2. **`SYSTEM.md` seed + `BUDGETS.md` numbers** — approve the caps in §4 (skill/prompt line limits, 3 proposals/wave, 1 wave/day) or retune.
3. **The Tier A/B/C allowlist** — sign off on exactly which prompt sections are Tier-B-writable (`rules`/`orientation` primary; `contract`/`design_block` tighter?), and confirm `parts.json` stays deferred until `bank.content_hash` is folded into integrity.
4. **Frozen tool signatures** — approve the three v0 tool schemas as the frozen contract; confirm `certify` thresholds are never a parameter.
5. **Graduation policy** — is auto-merge-after-gate ever acceptable, or human-merge-only indefinitely? If yes, which surface classes and after how many clean waves (default N=5)?
6. **Auto-revert-on-regression** — enable the canary auto-revert in P3, or keep revert human-triggered at first?
7. **Sandbox posture** — confirm `gi-certifier.sif` Singularity + `approvals.deny` floor + no host shell is the mandated batch posture on the shared login node.
