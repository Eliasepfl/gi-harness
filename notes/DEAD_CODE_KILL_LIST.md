# DEAD CODE KILL LIST

**Date:** 2026-07-15
**Commissioned by:** Elias, post-GDScript-pivot (`notes/engines/GDSCRIPT_LANE.md`)
**Scope:** consolidates four dead-code hunts (A gen/bank/prompts, B verify/legacy/core/godotworld, C rl/render/designer, D scenes/notes/top-level). Read-only evidence only — no source touched to produce this list.
**Sequencing:** every `git rm` here runs **AFTER** the reconciliation agent commits its test-deletion pass (see §5). Do not race main.

Verdict legend: rows in §1 are `certain-dead` with zero **non-test** references in a retired subsystem. §2 items die together with tests the reconciliation agent removes. §3 survives. §4 is ambiguous / needs a human edit or annotation.

---

## 1. CERTAIN-DELETE — zero non-test references (git rm)

Each row verified by bounded grep for `from X import` / `import X` / dotted-name use across `harness/ godotworld/ scenes/` excluding `tests/`. "grep-none" = that search returns empty (matches only in comments/docstrings/tests, or nothing).

| # | Path | Kind | importers = none (evidence) | Why dead |
|---|------|------|------------------------------|----------|
| 1 | `exit.py` | 0-byte tracked stray | grep `exit.py` / `import exit` / `from exit` / console_scripts across repo (non-test) → none; no `pyproject.toml`/`setup.py`; real entrypoint is `harness/__main__.py`→`harness.cli.main` | Empty tracked file, never an entrypoint. (Sibling `env.py` is a real gitignored secrets file — DO NOT touch.) |
| 2 | `harness/world.py` | flat-path compat shim → `harness.core.world` | no live `from harness import world` / `import harness.world`; flat name appears only in `render.py:547` docstring + `tests/test_gamegen.py:70` | self-labeled DEPRECATED shim; live code uses `harness.core.world` |
| 3 | `harness/gameverify.py` | flat-path shim → `harness.verify.gameverify` | no live import of flat path; callers use `harness.verify.gameverify` (e.g. `core/sandbox.py:123`) | DEPRECATED shim; real module `harness/verify/gameverify.py` |
| 4 | `harness/sdk.py` | flat-path shim → `harness.legacy.sdk` | live code imports `harness.legacy.sdk.SceneSDK` directly (`legacy/verifier/__init__.py:20`, `legacy/navigator.py:411`); `tests/test_sdk.py` imports `harness.legacy.sdk`, not the shim | DEPRECATED shim; legacy reached via `harness.legacy.*` |
| 5 | `harness/gamegen.py` | flat-path shim → `harness.gen.gamegen` | every non-test caller uses `from harness.gen import gamegen`; grep flat `harness.gamegen` (non-test) → none | DEPRECATED shim (`from harness.gen.gamegen import *`) |
| 6 | `harness/generator.py` | flat-path shim → `harness.legacy.generator` | `cli.py:58` imports `harness.legacy.generator.generate` directly; `tests/test_generator.py` imports legacy path; flat shim never imported | DEPRECATED shim; legacy reached directly |
| 7 | `harness/integrity.py` | flat-path shim → `harness.core.integrity` | flat `harness.integrity` appears only as prose in `gen/gamegen.py:21` + `tests/test_integrity.py:1`; live code uses `harness.core.integrity` | DEPRECATED shim |
| 8 | `harness/navigator.py` | flat-path shim → `harness.legacy.navigator` | `cli.py:97,131` + `core/sandbox.py:127` import `harness.legacy.navigator` directly; flat shim never imported | DEPRECATED shim |
| 9 | `harness/sandbox.py` | flat-path shim → `harness.core.sandbox` | live code imports `harness.core.sandbox`; flat `harness.sandbox` only in `legacy/navigator.py:40,42` docstrings | DEPRECATED shim |
| 10 | `harness/telemetry.py` | flat-path shim → `harness.core.telemetry` | flat `harness.telemetry` only as prose in `gen/gamegen.py:64,874` + `tests/test_telemetry.py:1`; live uses `harness.core.telemetry` | DEPRECATED shim |
| 11 | `harness/templates.py` | flat-path shim → `harness.legacy.templates` | `legacy/generator.py:25` imports `harness.legacy.templates` directly; `tests/test_generator.py:18` too; flat shim never imported | DEPRECATED shim |
| 12 | `harness/bank.py` | flat-path shim → `harness.core.bank` | grep `harness\.bank\b` (excl. `bank_tools`, non-test) → no live import; every consumer imports `harness.core.bank` directly (`world.py:220`, `integrity.py:130`, `retrieval.py:39`, `gamegen.py:803`, `designer/tools.py:488`, `cli.py:671`) | DEPRECATED shim; corroborated `PIPELINE_MAP.md:96` flat-shim kill-list |
| 13 | `harness/verifier/` (pkg, `__init__.py`) | flat-path shim PACKAGE → `harness.legacy.verifier` | live code imports `harness.legacy.verifier.verify_scene` directly (`cli.py:75,127`, `core/sandbox.py:120`, `legacy/generator.py:166`); `tests/test_generator.py:36-39` MOCKS `harness.legacy.verifier`, not this pkg | DEPRECATED shim pkg; real pkg `harness/legacy/verifier/` |
| 14 | `godotworld/diag.gd` (+ `diag.gd.uid`) | Godot Rapier physics prober | referenced only by `godotworld/bench.py:71,190` (itself a zero-importer dead spike); not in `project.godot`; no live `.py`/`.gd` ref | Rapier abandoned for stock Godot Physics 2D (day-log gate 6 / `PIPELINE_MAP §3`); reachable only via dead `bench.py` |
| 15 | `harness/bank_tools/build_v2.py` | offline v2 parts-bank grower / `python -m` CLI | grep `bank_tools` (non-test) → only `retrieval.py:318` **comment**; no CLI verb wires it; `cli.py`/`__main__.py` zero refs | retired bank subsystem (`banks/parts/` gone everywhere). **Also broken:** line 25 imports deleted `harness.bank_tools.parametric` (only stale `.pyc` remains) |
| 16 | `harness/bank_tools/migrate.py` | v1→v2 bank migrator | sole non-test importer is `build_v2.py:24` (row 15, dead). No other ref | **transitively certain:** becomes zero-importer the instant row 15 is removed; migrates bank DATA that no longer exists |
| 17 | `harness/bank_tools/__init__.py` | pkg init (`ROLE_CONTRACT`, `derive_volume`, `render_binding`) | only imported by `migrate.py:17` + `build_v2.py:24-26` (both dead); grep those symbols (non-test) hits ONLY `core/bank.py`'s OWN schema fields, never `from harness.bank_tools` | **transitively certain:** whole `bank_tools/` pkg orphaned after bank retirement; `__init__` only runs when a dead submodule imports it |
| 18 | `harness/gen/prompts/__init__.py:195-221` | **symbol excision** — unreachable `compose()` engine-branch body | `compose()` is called, but the 2026-07-15 PURGE sentinel does an unconditional `return` at 192-193; lines 195-221 never execute | every branch (`_engine_key`, py/js/godot/gdscript, `_read(<section>.md)`) is unreachable, and its `.md` section files are deleted. `compose()` now always returns the `[SPEC-LANE PARKED]` string. **NOT a git rm — in-file edit; see §5 step 6** |

**Total CERTAIN-DELETE: 18 entries** = 17 files/dirs for `git rm` (rows 1-17; rows 15-17 are the whole `harness/bank_tools/` package) + 1 in-file symbol excision (row 18). The entire `harness/bank_tools/` package (rows 15-17) is retired as a unit.

---

## 2. DELETE-WITH-TESTS — only remaining refs are tests the reconciliation agent removes

These have **zero non-test, non-dead-code references**; their sole live reach is via the now-unreachable `compose()` body (row 18) or test-only introspection. Once the reconciliation agent deletes their tests, they orphan and should be removed in the same commit. Do NOT remove standalone before the tests go, or the suite breaks.

| Path / symbols | Kind | Only-refs evidence | Note |
|----------------|------|--------------------|------|
| `harness/gen/prompts/__init__.py` helpers `_engine_key`, `_read`, `_render`, `section_text` | private helpers | `section_text` has zero non-test callers; `_engine_key`/`_read`/`_render` called only from the dead `compose()` body (row 18) or from `section_text` | die with row 18 + their tests |
| `harness/gen/prompts/__init__.py` consts `_SUBS`, `SECTIONS`, `CONTRACT`, `API_PY`, `API_JS`, `API_GODOT`, `RULES`, `ORIENTATION`, `DESIGN_BLOCK`, `RULES_GODOT`, `ORIENTATION_GODOT`, `DESIGN_BLOCK_GODOT`, `BANK_MENU_TMPL` | module constants naming DELETED `.md` sections | `SECTIONS` docstring says "used by tests"; zero non-test refs; each const names a purged `.md` (only `api_gdscript.md` + `design_block_gdscript.md` survive) | remove with the helpers above |
| **KEEP-BACK (do NOT delete in this pass):** `API_GDSCRIPT`, `DESIGN_BLOCK_GDSCRIPT`, `ESCAPE_HATCH_CODE`/`_GODOT`/`_GDSCRIPT`, `render_bank_menu()` | live constants/fn in same file | `ESCAPE_HATCH_*` used by `retrieval.py:408/421/424/439`; `API_GDSCRIPT`/`DESIGN_BLOCK_GDSCRIPT` name surviving live files; `render_bank_menu()` feeds the gdscript advisory volume menu | genuinely live — leave in the trimmed `__init__.py` |

Guidance for the excision: after §1 row 18 removes lines 195-221, the file's live surface is `compose()` (sentinel), `render_bank_menu()`, and the `ESCAPE_HATCH_*` / `API_GDSCRIPT` / `DESIGN_BLOCK_GDSCRIPT` constants. Everything else in the helper/constant layer is DELETE-WITH-TESTS.

---

## 3. KEEP — frozen-legacy or live (one reason each)

| Path | Verdict | One-line reason it survives |
|------|---------|------------------------------|
| `harness/gen/retrieval.py` | KEEP | live importers `gamegen.py:35` + `designer/tools.py:176`; degrades to legend-only when bank DATA missing (`gamegen.py:939-940` try/except) |
| `harness/core/bank.py` | KEEP | 6 live importers (`cli.py:671`, `core/world.py:220`, `core/integrity.py:130`, `gen/retrieval.py:39`, `gen/gamegen.py:803`, `designer/tools.py:488`); only its DATA is gone, not the code |
| `harness/gen/curriculum.py` | KEEP | CLI-reachable `game curriculum` (`cli.py:607`, verb `cli.py:953`); all deps resolve to live modules |
| `harness/gen/gamegen.py` py/js/godot spec branches | KEEP | SPEC lane is **PARKED not deleted** (`GDSCRIPT_LANE.md:26`); inline templates DORMANT-KEEP offline test backend (`PIPELINE_MAP.md:107`) |
| `harness/gen/prompts_js.py` | KEEP | live module-level import `gamegen.py:36` (produces the parked sentinel via `compose('js')`); frozen-lane shim |
| `harness/legacy/**` (`sdk.py`, `generator.py`, `navigator.py`, `templates.py`, `verifier/`) | KEEP | LIVE non-test importers `cli.py:58,75,97,127,131` + `core/sandbox.py:120,127`; frozen v1 regression floor |
| `harness/verify/executors.py` (`PyExecutor`/`JsExecutor`) | KEEP | reachable via `detect_engine` + live RL/G4 (`g4.py:344`, `certify.py:63-64`, `rl/env.py`, `sb3_trainer.py:336`, `ppo.py:320`); FROZEN-LEGACY policy = KEEP |
| `harness/verify/gameverify.py` `run_g3` (random solver) | KEEP | env-selectable live fallback `HARNESS_G3_SOLVER=random` (`gameverify.py:86,92`) |
| `harness/verify/gameverify.py` 21 module constants | KEEP | every one has ≥1 live reference outside its def; hunt found NO dead constants |
| `harness/rl/env.py` (`PlanckEnv`, `wrap_gym`) | KEEP | frozen-legacy JS env, live dep of `sb3_trainer.py:43`, `certify.py:21,143`, `godot_env.py:60` |
| `harness/rl/godot_env.py` (`GodotServeEnv`) | KEEP | CORE to the GDScript lane — reused by `harness/verify/gd_exec.py:45,63,177,257`; also `certify.py:134,140` |
| `harness/rl/certify.py` (`g3_prime`) | KEEP | G3′ entrypoint; `cli.py:763,767`, `curriculum.py:384-385,541`, `designer/tools.py:158-159` |
| `harness/rl/ppo.py` (vendored PPO) | KEEP | live dep: `certify.py:37`, `sb3_trainer.py:44` (`ppo.DEFAULTS`) |
| `harness/rl/sb3_trainer.py` | KEEP | default RL trainer; live via `certify.py:40-41` backend switch |
| `harness/render.py` | KEEP | live GIF/replay renderer (`cli.py:343,582`, `viewer.py:23`, `executors.py:333`); single source of palette/witness rules |
| `harness/viewer.py` | KEEP | CLI operator commands `game watch`/`game demo-live` (`cli.py:412,542`); imports only `harness.render` |
| `harness/designer/tools.py` `inspect_world`/`design`/`certify`/`REGISTRY`/`TOOL_SCHEMAS` | KEEP | deliberately build-ahead frozen typed designer cage (task LIVE list); `inspect_world` engine-free and explicitly live |
| `scenes/` | KEEP | empty on checkout but the live runtime OUTPUT-dir default (`cli.py:16,838,877,950`, `gamegen.py:852,889`, `curriculum.py:470`, `designer/tools.py:47`); nothing tracked to delete |
| `requirements.txt` | KEEP | every pin imported by live or frozen-legacy code (pymunk, anthropic, requests, pillow, pygame, stable-baselines3) |

---

## 4. NEEDS-HUMAN — ambiguous code, stale notes to annotate, doc contradictions

### 4a. Ambiguous code (do NOT git rm unilaterally)

| Item | Status | Action |
|------|--------|--------|
| `harness/cli.py:707-727` `cmd_bank_certify` + argparse reg `cli.py:991` (`bank certify` verb) | probably-dead | body always falls into `except → _module_missing('bank_ci')` (its `from harness.bank_ci import ...` targets a DELETED module). Needs a **code edit** (remove verb + its `set_defaults`), not a `git rm`. Human review — it edits live `cli.py` |
| `godotworld/bench.py` | probably-dead, RETAINED | zero importers anywhere, but `PIPELINE_MAP §3` says keep the bench cluster as **spike-repro only**. Confirm before deleting |
| `godotworld/boot.gd` (+ `boot.gd.uid`) | probably-dead, RETAINED | only `bench.py` invokes it; retained-by-policy with `bench.py`. Dies WITH bench, not standalone |
| `godotworld/runner.gd:62` `DEFAULT_SPEC` const | probably-dead branch | bench.py-compat fallback inside LIVE `runner.gd` (self-ref `:14`, `:525`). Excise WITH bench.py, never standalone |
| `harness/core/spritebank.py` | probably-dead, guarded | inert (`available()` hardwired False — raw atlas + slicemap gone), but still try/except-imported by live `render.py:23`. Retire WITH the render sprite-skinning branch during render_binding/parts-v2 work |
| `harness/render.py` sprite-skinning branches (`_sprites_ok`, `_fill_tiled`, `_soft_shadow`, `_oriented_sprite`, `_paste_sprite`, `spritebank.crop/resolve`) | probably-dead, guarded no-op | gated on `spritebank.available()` (always False) → unreachable at runtime but cannot crash; render tests still exercise it. KEEP until render_binding work |
| `harness/designer/tools.py::retrieve_parts` (169-186, REGISTRY 1230, schema 1172) | probably-dead | parts-bank DATA git-confirmed gone; no live caller; but sits inside the intentionally-staged designer cage. Remove WITH the parts-bank retirement, not unilaterally |

### 4b. Stale notes — MARK SUPERSEDED (annotate with a banner, do NOT delete)

| Note(s) | Why superseded |
|---------|----------------|
| `notes/PARTS_BANK.md`, `notes/parts_bank/{ASSET_BANK_V2,assets,design,kenney_usage,mcp_tools,pipeline,retrieval}.md` | parts/asset/sprite-bank + BM25 retrieval subsystem; `banks/` data gone from git+disk; `spritebank.available()` False. Caveat: `render_bank_menu()` survives to feed the gdscript advisory menu — the *menu concept* is not fully dead |
| `notes/PIPELINE_MAP.md` | pre-GDScript spec-lane map; §1 schema / §2 prompt-compose / §4 items 2-3-7 / §5 3D-insertion superseded. **KEEP §3 kill-list + §6 stale-docs** (cross-hunt evidence). Add one-line "SUPERSEDED by GDScript pivot" banner |
| `notes/engines/GODOT_ONLY_PIVOT.md`, `notes/engines/GODOT_LANE.md` | describe the declarative godot `.spec.json` lane replaced by agent-written `.gd` + GameAPI serve contract; all `api_godot.md`/`rules.md`/`orientation.md` rewrite planning is moot. Keep as history + banner |
| `DESIGNER_AGENT_PLAN.md`, `SELF_IMPROVING_DESIGNER.md` | describe the RETIRED designer skills/lessons library. Flag SUPERSEDED — but `harness/designer/` cage CODE is LIVE, do NOT touch it |

### 4c. Top-level doc contradictions vs the pivot (needs-human; do NOT edit — listed only)

| Doc | Contradiction |
|-----|---------------|
| `README.md` | entire Quickstart/Architecture is v1 SceneSDK/pymunk (`generate/verify/play/demo`, L0-L1-L2, uncommitted `scenes/examples/push_ball_to_zone.py`); no games/godot/GDScript/GameAPI/G0-G4/RL |
| `VERSIONS.md` | lists purged `prompts/*.md` as active; claims `banks/parts/v2/` "260 entries committed" (dir absent git+disk); sprite skinning retired; nodeworld self-contradiction (74-110 vs 157); no GDScript lane. Lines 15-18 DO correctly flag the 11 flat shims |
| `STATE.md` | 3-engine framing (js/godot-JSON/py), no GDScript; dead sanity path `verify godotworld/examples/escape.spec.json`; `banks/` refs; "v2.4, 439 tests green" predates pivot |
| `OBJECTIVES.md` | pyramid step 2 cites purged `prompts/*.md`; PARTS BANK lines 122-137 marked GO/LANDED (bank retired); Rung-4 ladder still says "stay pymunk / Planck.js" |
| `CONTRACTS.md` | 100% pymunk-python (`pymunk 7.3.0`, world 800x600 y-UP); never describes the godot spec NOR the current GDScript GameAPI serve contract (`init/reset(seed)/act(action,n_ticks)->state`, checkpoints, `done_term`/`done_trunc`). Note: integrity manifest freezes this file — coordinate before edit |

### 4d. Other flagged (out-of-scope for git rm)
- `harness/designer/write.py:60-67` allowlist still lists the deleted prompt `.md` files (stale text; `write.py` is the live flag-OFF cage — annotate only).
- `scenes/examples/` + the `demo` verb point at uncommitted example scenes → `harness demo` and README's verify path are dead. Needs example scenes committed or the verb retired.

---

## 5. APPLY ORDER — exact sequence (runs AFTER reconciliation agent commits)

**Preconditions (avoid main races):**
0. Wait for the reconciliation agent to land its test-deletion commit on `main`. Then `git fetch && git status` — confirm a clean tree at the reconciliation HEAD before removing anything. Run all steps in **one** branch/PR so the tree is never half-deleted.

**File/dir removals (§1 rows 1-17):**
```
# 1. stray + 11 flat-path shims + verifier shim pkg (rows 1-13)
git rm exit.py
git rm harness/world.py harness/gameverify.py harness/sdk.py \
       harness/gamegen.py harness/generator.py harness/integrity.py \
       harness/navigator.py harness/sandbox.py harness/telemetry.py \
       harness/templates.py harness/bank.py
git rm -r harness/verifier/

# 2. godot Rapier prober (row 14)
git rm godotworld/diag.gd godotworld/diag.gd.uid

# 3. whole retired bank_tools/ package as a unit (rows 15-17;
#    remove build_v2.py FIRST conceptually — migrate.py/__init__.py
#    are only transitively dead until it goes, so drop the dir together)
git rm -r harness/bank_tools/
```

**In-file symbol excisions (NOT git rm — code edits, separate reviewed commit AFTER the removals above):**
```
# 4. §1 row 18: delete unreachable compose() body lines 195-221 in
#    harness/gen/prompts/__init__.py (keep the sentinel return + the live
#    surface: compose(), render_bank_menu(), API_GDSCRIPT,
#    DESIGN_BLOCK_GDSCRIPT, ESCAPE_HATCH_*).
# 5. §2: delete the dead prompts helpers/constants ONLY in the same commit
#    the reconciliation agent deletes their tests (section_text/SECTIONS
#    tests). Verify the trimmed __init__.py still imports (import-only check),
#    do NOT run pytest here.
# 6. §4a bank-certify verb: OPTIONAL follow-up PR — remove cmd_bank_certify
#    + its argparse set_defaults in cli.py. Human-reviewed; touches live cli.py.
```

**Order rationale:**
- Steps 1-3 are pure `git rm` of zero-non-test-reference files — safe once the reconciliation commit is in.
- `bank_tools/` is removed as a directory because rows 16-17 only become zero-importer after row 15 (build_v2) is gone; deleting the package atomically avoids a transient broken import.
- Step 4 (symbol excision) follows the file removals so any test still touching the flat shims/prompts is already gone.
- Step 5 must be co-committed with test deletion (§2) — never before.
- Step 6 is deferred: it edits live `cli.py` and needs a human, so it is a separate PR, not part of the bulk rm.

**Do NOT in this pass:** touch anything in §3, delete `godotworld/bench.py`/`boot.gd`/`runner.gd DEFAULT_SPEC` (§4a policy-retained spike cluster), delete `spritebank.py`/render sprite branch/`retrieve_parts` (retire with render_binding/parts-bank work), edit any top-level doc (§4c — needs-human), or delete stale notes (§4b — annotate only).
