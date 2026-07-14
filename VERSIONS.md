# VERSIONS — module map (which code belongs to which version, active vs frozen)

Maintained henceforth. One page. Companion to `OBJECTIVES.md` (roadmap) and
`CONTRACTS.md` (normative interfaces). The repository is organised by version:

```
harness/
  core/     world.py  sandbox.py  integrity.py  telemetry.py  bank.py   (version-spanning)
  verify/   gameverify.py                                                (v2 oracles)
  gen/      gamegen.py                                                   (v2 generator)
  legacy/   sdk.py  verifier/  generator.py  templates.py  navigator.py  (v1, frozen)
  cli.py  render.py  bank_ci.py  __init__.py  __main__.py                (span versions)
```

Thin **compatibility shims** remain at the old flat paths (`harness/world.py`,
`harness/gameverify.py`, `harness/sdk.py`, …). Each re-exports from the new
location and is marked deprecated. Nothing in the repo imports through them any
more — they exist only for external/back-compat callers and can be dropped later.

---

## v1 — curated SceneSDK pipeline (FROZEN)

Powers the day-1 row-1 demos and the legacy test suite. The harness pre-decided
the genre; the LLM filled in parameters. No new work targets v1 — it stays green.

| Module | Role |
|---|---|
| `harness/legacy/sdk.py` | `SceneSDK`: instrumented pymunk wrapper (the only API v1 scene code sees). |
| `harness/legacy/verifier/` | L0 static → L1 settling → L2 goal funnel (`verify_scene`, `run_l0/1/2`, `make_report`). |
| `harness/legacy/generator.py` | Command → scene; `anthropic` / offline `template` backend; bounded repair loop. |
| `harness/legacy/templates.py` | Offline scene templates (no network) for the `template` backend. |
| `harness/legacy/navigator.py` | Greedy closed-loop solvability probe (no LLM); LLM policy stubbed. |
| CLI verbs `generate` / `verify` / `play` / `demo` | v1 entry points (in `harness/cli.py`). |

Status: **ACTIVE but frozen.** Scenes live under `scenes/examples/` and
`scenes/generated/`.

## v2.0 / v2.1 — open-ended games on a minimal substrate (ACTIVE)

The design shift: the LLM designs a WHOLE game against a minimal physics
substrate (`World`) — its own actions, per-tick rules, and win/lose — and the
harness keeps only substrate + sandbox + universal oracles + solvability probe.

| Module | Role |
|---|---|
| `harness/core/world.py` | `World`: minimal instrumented pymunk substrate; the only object game code sees. |
| `harness/core/sandbox.py` | AST scan + `spawn` subprocess isolation (worker jobs `verify` / `gameverify` / `navigate`). |
| `harness/verify/gameverify.py` | Universal oracles G0 static · G1 rollout (agency, action efficacy, determinism) · G2 goal · G3 random-search solvability; shared §2 `run_episode`. |
| `harness/gen/gamegen.py` | Open-ended generator + write→verify→repair loop; `anthropic` / `openrouter` / `template` backends. |
| `harness/render.py` | Generic witness→GIF replay renderer (PIL only, drawn from `world.query()`). |
| CLI group `game new/verify/replay/demo/stats` | v2 entry points (in `harness/cli.py`). |

v2.1 amendment: **checkpoints** — 1..6 game-declared milestone predicates,
runner-latched, giving a dense progress signal and "stuck between X and Y"
repair diagnosis. `success` stays a binary, unshaped certificate.

Status: **ACTIVE (current phase: base-of-games campaign).** Games live under
`scenes/games/`.

## v2.2 step 1 — bank, telemetry, integrity (ACTIVE)

| Module | Role |
|---|---|
| `harness/core/bank.py` | Parts-bank loader/validator/hasher/resolver behind `world.part()`; catalog is versioned DATA at `banks/parts/<v>/`. |
| `harness/bank_ci.py` | Offline certification pass (`python -m harness.bank_ci`) — v1 bank: 30/30 certified. |
| `harness/core/telemetry.py` | Runs ledger (`runs/ledger.jsonl`): failures/repairs as first-class stats; `game stats` aggregates per model. |
| `harness/core/integrity.py` | Run-integrity manifest (`harness/**/*.py` + `CONTRACTS.md`); freezes base code during a generation run. |
| `openrouter` backend (in `gamegen.py`) | Volume backend for the base-of-games campaign (free model, key in gitignored `env.py`). |

Status: **ACTIVE.** Bank prompt-integration, ledger `bank_version` pinning, and
sprites were deferred to step 2 (below) — now landed.

## v2.2 step 2 — modular prompts, retrieval menu, sprites (LANDED 13 juil., c02de14)

| Module | Role |
|---|---|
| `harness/gen/prompts/` | System prompt split into separately editable sections (`contract.md`, `api_py.md`/`api_js.md`, `rules.md`, `orientation.md`, `design_block.md`, `bank_menu.md.tmpl`); `compose(engine)` builds the final prompt; `prompts_js.py` and `_SYSTEM_PROMPT` are shims. Integrity manifest freezes `prompts/*.md`. |
| `harness/gen/retrieval.py` | Deterministic BM25 over the bank (name+tags+summary, synonym map); top-K ≤15 advisory menu spliced into the system prompt, **pinned for the whole run** (repairs reuse it); report gains a `pipeline` block (`retrieved`, `menu_mode`, `parts_used`). |
| `harness/core/spritebank.py` + `render.py` | Kenney CC0 sprite skinning at render time by body NAME (exact → enumerator-strip → singular → alias table; null = flat shape); `replay_gif(..., sprites=True)`. Sprites are cosmetic only — physics stays bank-certified. |
| `notes/parts_bank/kenney_usage.md` | Source-verified prompt-orientation guidance from real games using these packs (skeleton-first, one-strip ground, sensor hazards, joint contraptions). |

## Spike — nodeworld/ (VALIDATED, PORTED 13 juil. late: executor seam (PyExecutor/JsExecutor), JS verify funnel + generation prompt + GIF adapter, --engine flag; first live JS game generated by hy3 (2 attempts))

`nodeworld/` is the rung-4 real-engine exploration (Planck.js / Matter.js in
pure Node): a throughput/loop-parity spike, validated, not yet wired into the
harness. `nodeworld/bench.py` benchmarks against the pymunk template games.
**Not** part of the tracked base manifest (integrity ignores it). Frozen until
the executor port lands.

## What lands next

- **Bank expansion 30 → ~60 parts** (decor, surface variants, machines,
  interactive) — in flight; bank-CI certifies every addition, bank.lock moves.
- **Sprite-skinned Planck showcase** — one clean generated game with bank
  sprites, added to the day-1 page (pre-departure deliverable).
- **G4 adversarial suite + shared state-action tree** — designs frozen on the
  shelf (`notes/adversarial/G4_DESIGN.md`, `notes/adversarial/STATE_TREE.md`);
  implementation explicitly deferred until Elias relaunches for an overnight run.

---

### Active vs frozen at a glance

| Area | State |
|---|---|
| `core/`, `verify/`, `gen/`, `render.py`, `bank_ci.py` | ACTIVE |
| `legacy/` (v1 SceneSDK stack) | FROZEN (kept green) |
| Old flat-path shims (`harness/*.py`) | DEPRECATED (back-compat only) |
| `nodeworld/` spike | VALIDATED, PORTED 13 juil. late: executor seam (PyExecutor/JsExecutor), JS verify funnel + generation prompt + GIF adapter, --engine flag; first live JS game generated by hy3 (2 attempts) |
