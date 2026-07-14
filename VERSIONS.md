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
sprites are explicitly NOT in this wave (see CONTRACTS §9.6).

## Spike — nodeworld/ (VALIDATED, port pending)

`nodeworld/` is the rung-4 real-engine exploration (Planck.js / Matter.js in
pure Node): a throughput/loop-parity spike, validated, not yet wired into the
harness. `nodeworld/bench.py` benchmarks against the pymunk template games.
**Not** part of the tracked base manifest (integrity ignores it). Frozen until
the executor port lands.

## What lands next

- **Planck executor port** — the rung-4 step-1 engine loop (Planck/Matter in
  Node), reusing the `World` verb surface and the §2 runner; `nodeworld/` graduates.
- **G4 adversarial suite** — avoidance probes, single-action-win check, and
  breaker fuzzing (tiers 0-2: mechanical fuzz → cheap-LLM attack proposers →
  smart-model attacks), yielding a "bulletproof" grade. See OBJECTIVES.
- **Retrieval stage-1** — two-stage bank selection (BM25 + dense) and the
  two-tier prompt integration of the parts bank (v2.2 step 2).

---

### Active vs frozen at a glance

| Area | State |
|---|---|
| `core/`, `verify/`, `gen/`, `render.py`, `bank_ci.py` | ACTIVE |
| `legacy/` (v1 SceneSDK stack) | FROZEN (kept green) |
| Old flat-path shims (`harness/*.py`) | DEPRECATED (back-compat only) |
| `nodeworld/` spike | VALIDATED, port pending |
