# VERSIONS — module map (which code belongs to which version, active vs frozen)

> **[2026-07-15] PARTIALLY HISTORICAL.** The ACTIVE lane is v3: agent-written
> GDScript (`harness/gen` gamegen+feedback+harden, `harness/verify` gd lane +
> G0.5 + G4 + capture, `harness/rl` serve/batched-vec/SB3, `godotworld/` serve
> + capture + dresser + asset loader, `assets/` bank). The v1 py lane and the
> v2 planck/godot-spec lanes described below are FROZEN regression floors —
> still tested, no longer defaults. Retired subsystems referenced below
> (parts bank menus, sprite bank, prompt sections beyond `api_gdscript.md`)
> no longer exist on main. Current truth: `README.md` + `STATE.md`.

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

## v2 parts bank — volume+role objects (ACTIVE, `banks/parts/v2/`, 260 entries)

The sprite bank is retired: v2 entries are a style-neutral **volume** (physics
footprint) + a first-class **role** (game objective). Binding is ADVISORY (the
designer just reads the menu); the mis-wiring class stays covered by
verification. Schema is shaped-for-both lanes (2D now, flat-3D Path B is a
reserved-null slot). See `notes/parts_bank/ASSET_BANK_V2.md` (+ its DECISIONS
addendum).

| Module | Role |
|---|---|
| `banks/parts/v2/parts.json` (+`bank.lock`) | v2 catalog, schema `2.0`: `{physics_class, role, volume, role_contract, render_binding}` superset of v1. 60 migrated nouns + 200 parametric = **260** certified entries. Committed, content-hashed, folded into the integrity freeze as `bank:v2`. |
| `harness/core/bank.py` | v2 vocabulary (`PHYSICS_CLASSES`, `ROLES`, `ROLE_CONTRACT_TOKENS`) + `_validate_bank_v2` (`category`→`physics_class` floor, role, volume sanity, machine-checked `role_contract`, style-neutral `render_binding`); `Bank.by_physics_class/by_role/is_v2`. v1 path unchanged; `resolve_part`/`world.part` are version-agnostic. |
| `harness/bank_tools/` | OFFLINE bank-grower (never in the gen loop): `migrate.py` (mechanical, fidelity-preserving v1→v2), `parametric.py` (deterministic box/disc/capsule/ramp/arc/gate families across dims×roles, stable names `box_2x1`/`ramp_27deg_4x2`), `build_v2.py` (assemble + validate + write; `--check` CI guard). |
| `harness/bank_ci.py` | Certifies v1 AND v2 through one live settle-grid (resolves entries directly): adds v2 VOLUME (realized AABB vs declared footprint) and ROLE (`role_contract` re-asserted on settled bodies). v2: **260/260**. |
| `harness/gen/retrieval.py` | v2 menu: one advisory `name \| volume: shape WxH \| role: <role> - <objective>` line (DATA payload only; `bank_menu.md.tmpl` untouched). v1 menu paths unchanged. |
| CLI `bank list` / `bank certify` | List entries (name/physics_class/role/volume) and run the certification pass (`--version`, `--json`). |

Status: **ACTIVE.** Path A (2D footprints) only; Path B (flat-3D `.tscn`/`.glb`)
insertion points reserved but null. Generative/Poly-Pizza growers deferred.

## Spike — nodeworld/ (VALIDATED, PORTED 13 juil. late: executor seam (PyExecutor/JsExecutor), JS verify funnel + generation prompt + GIF adapter, --engine flag; first live JS game generated by hy3 (2 attempts))

`nodeworld/` is the rung-4 real-engine exploration (Planck.js / Matter.js in
pure Node): a throughput/loop-parity spike, validated, not yet wired into the
harness. `nodeworld/bench.py` benchmarks against the pymunk template games.
**Not** part of the tracked base manifest (integrity ignores it). Frozen until
the executor port lands.

## v2.3 — quality bar: solidity, big worlds, duration (LANDED 14 juil., 9f510c2)

Response to the day-2 quality directive (player passing through obstacles,
cramped field, monotone objectives, 7-tick wins):

| Change | Where |
|---|---|
| CCD bullets on all dynamic bodies; maxTranslation 1000→200 px/substep (measured: worst overlap 15.4→7.6px transient) | `nodeworld/world.js` |
| `solidity` oracle: witness replayed with frames; sustained deep interpenetration (>50% of thinner body, ≥2 ticks) → ENV_ERROR + surgical hint | `gameverify` G3 |
| `WORLD_SIZE` declarable up to 2400x1600 (G0 `world_size` check, both engines) + smoothed clamped `FollowCamera` at render | contract §2 / render.py |
| Duration bar: PROBE_HORIZON 120→300, TRIVIAL_TICKS 5→20, GUIDED_EPISODES 20→30 | `gameverify` |
| Prompts: 7 objective archetypes (traverse/collect/deliver/activate/escape/topple/survive), journey-scale design, solidity speed guidance, 4-6 spread milestones | `gen/prompts/` |
| Decor sensors render faded (0.55) behind everything — scenery never reads as an obstacle | render.py |

## G4 adversarial suite + state-action tree (LANDED 14 juil., built by Opus agents)

- `harness/core/statetree.py` — Go-Explore-style shared tree: node = action
  prefix, dedup guaranteed, no-effect edges never spawn children, K=8
  consecutive no-effect → terminal_stuck, atomic edge claiming for async lanes,
  versioned JSON persistence. 31 tests.
- `harness/verify/g4.py` + CLI `game attack` — Tier 0 mechanical (avoidance
  probes, single-action-win, breaker fuzz families, faster-than-witness
  shortcut) + Tier 1 cheap-LLM attacker lane (OpenRouter, PURE-DATA attack
  records, incomprehension/misconception/hit classification, full attacker
  history). Grades: open / hardened / bulletproof. 25 tests.
- Wiring the tree in as G3's solver (replacing pure random search) is the next
  step — the tree API was built for that seam.

## What lands next

- **State-tree-driven G3/G4 search** — replace random probing with the shared
  tree (never explore a branch twice; restart from reached leaves).
- **Template-backend games below the v2.3 bar** — upgrade the offline template
  games to 60+ tick wins (backlog; tests pin legacy thresholds meanwhile).
- **v2.3 showcase batch** — strong-model-authored diverse games (in flight).

---

### Active vs frozen at a glance

| Area | State |
|---|---|
| `core/`, `verify/`, `gen/`, `render.py`, `bank_ci.py` | ACTIVE |
| `legacy/` (v1 SceneSDK stack) | FROZEN (kept green) |
| Old flat-path shims (`harness/*.py`) | DEPRECATED (back-compat only) |
| `nodeworld/` spike | VALIDATED, PORTED 13 juil. late: executor seam (PyExecutor/JsExecutor), JS verify funnel + generation prompt + GIF adapter, --engine flag; first live JS game generated by hy3 (2 attempts) |
