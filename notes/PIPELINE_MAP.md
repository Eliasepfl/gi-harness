# PIPELINE_MAP — the whole stack, so we don't get lost

*Dated 2026-07-14 (late). Commissioned by Elias after the heavy build day (Godot-only pivot,
sprite-bank retirement, designer-agent P0/P1, SB3 default, tree G3, raycast sensors, godot GIF, G4 stale tier).*
*Read-only review of MAIN at `~/gi`; `.claude/worktrees/` ignored. Every claim cites `file:line`.*
*Companion normative docs (STATE/CONTRACTS/VERSIONS, all mtime 14:56) are STALE — see §6.*

---

## 1. THE SCHEMA

`[stack | DET / NON-DET]`. Dashed orange = incoming 3D rung (Path B).

```mermaid
flowchart TD
  P["prompt<br/>human / CLI · N/A"] --> DIS
  subgraph GEN["Generation · harness/gen/"]
    RET["retrieve_menu BM25<br/>retrieval.py · DET (pure of prompt+bank_ver)"] --> DIS
    PR["compose(engine)<br/>gen/prompts/*.md · DET (integrity-frozen)"] --> DIS
    DIS["designer LLM · gamegen.generate/revise<br/>OpenRouter deepseek-v4-flash · NON-DET (network, no seed)"] --> ART{{"game artifact"}}
  end
  ART -->|".spec.json godot / .js / .py"| SEAM
  SEAM["detect_engine + executor seam<br/>gameverify.py + executors.py · DET routing"] --> G0
  subgraph VER["Verify funnel · verify/gameverify.py"]
    G0["G0 static · sandbox AST + build · DET"] --> G1
    G1["G1 rollout · agency/efficacy/determinism · DET"] --> G2
    G2["G2 goal · success/checkpoint purity · DET"] --> G3
    G3["G3 solve · Go-Explore TREE default<br/>treesolve.run_g3_tree · DET*seeded (random selectable)"]
  end
  subgraph EXE["Executor seam · verify/executors.py  &lt;&lt;== 3D INSERTION #1"]
    PYX["PyExecutor pymunk · FROZEN · DET"]
    JSX["JsExecutor Planck.js/node · FROZEN · DET"]
    GDX["GodotExecutor 4.7 headless · runner.gd · ACTIVE · DET*conditional(.sif)"]
    G3D["Godot3DExecutor / runner_3d.gd · INCOMING · DET*conditional"]:::plan
  end
  G0 -. run_check/run_batch .-> EXE
  G1 -. .-> EXE
  G3 -. .-> EXE
  G3 --> G4
  G4["G4 adversarial · g4.py + statetree/treesolve<br/>tier0 mechanical DET · stale-state softlock oracle DET · tier1 LLM/RL NON-DET<br/>grade open|hardened|bulletproof"] --> G3P
  G3P["G3' learnability · rl/certify.g3_prime · SB3(default)/PPO CPU<br/>env=PlanckEnv JS-ONLY  &lt;&lt;== GODOT GAP · NON-DET (seeded, stoch eval)"] --> CUR
  CUR["curriculum round · gen/curriculum.py<br/>profile:DET · directive:DET · revise:NON-DET(LLM)"] -->|revise directive| DIS
  G3 --> DEMO
  subgraph OUT["Demos / site"]
    DEMO["replay -> frames.json + GIF · render.py PIL + executors frames · DET"] --> SITE["gh-pages gallery · GIF default; canvas replayer beta"]
  end
  subgraph DES["Designer-agent tool cage · harness/designer/ (P0/P1)"]
    TOOLS["tools.py: design/certify/retrieve_parts · frozen typed spine"] --> WRITE["write.py designer_write · TierA/B/C allowlist · flag-OFF"]
    WRITE --> WS[("designer/ workspace · skills/memory/proposals · gitignored")]
  end
  TOOLS -. wraps .-> DIS
  TOOLS -. wraps .-> SEAM
  subgraph FARM["ORCD cluster farm · scratch sbatch"]
    CF["certify farm · verify+attack · DET"] --> LM["ledger merge · telemetry.merge_shards<br/>dedupe(game_id,seed,hash) · DET idempotent"]
    PF["G3' probe farm · 200k/2M · NON-DET"] --> LM
    CB["curriculum batch · local<->cluster ping-pong"]
  end
  SEAM -. .-> CF
  G3P -. .-> PF
  LM --> DEMO
  classDef plan stroke-dasharray:5 5,stroke:#e67e22;
```

---

## 2. STACK + DETERMINISM TABLE

| Component | Tech | Deterministic? (pin) | Status | Tests |
|---|---|---|---|---|
| retrieval BM25 menu | py, rank-bm25-ish | DET — pure fn(prompt,bank_ver); ties by name (`retrieval.py:213`); idx cached by ver:content_hash (`:195`) | active | test_retrieval |
| prompt compose | py, `.md` sections | DET — byte-identical, integrity-frozen (`prompts/__init__.py:112,148`) | active | test_prompts |
| designer LLM call | OpenRouter deepseek-v4-flash | NON-DET — network, no seed (`gamegen.py:271-444`); correctly isolated | active | test_gamegen |
| integrity snapshot | py sha256 + canonical JSON | DET — sorted tracked files + bank content_hash, depth-1 scan (`integrity.py:145,166`) | active | (in bank/designer) |
| bank load/validate/resolve | py, canonical JSON | DET — pure; CAVEAT `_CACHE` keyed by version only, not hash (`bank.py:49`) | active | test_bank |
| G0/G1/G2 funnel | py + sandbox AST | DET — WORLD_SEED=0, dt=1/60; G1 gates determinism ≤1e-6 (`gameverify.py:440`) | active | test_gameverify |
| G3 solver (tree) | py Go-Explore, statetree | DET*seeded — SOLVER_SEED=0, monotonic counter (`treesolve.py:42`); default since v2.4 | active | test_treesolve, test_statetree |
| PyExecutor | pymunk in-proc | DET — seeded World(0), pymunk pinned | frozen-legacy | test_executors |
| JsExecutor | Planck.js / node subprocess | DET per-proc — byte-parity gate (64KiB flush bug fixed) | frozen-legacy | test_js_engine, test_executors |
| GodotExecutor | Godot 4.7 headless, runner.gd | DET*conditional — %.17f + fixed-fps 60 + pinned `.sif`; cross-arch NOT guaranteed | active | test_godot_exec, test_godot_sensors |
| G4 adversarial | py fuzz + treesolve + LLM | MIXED — tier0/stale DET; tier1 LLM/RL NON-DET, off-by-default | active | test_g4 |
| G3' RL | SB3 (default) / vendored PPO, CPU torch | NON-DET (seeded band) — witness cert DET by (seed,action) replay + assert (`certify.py:141`) | active (JS-only) | test_rl_env, test_sb3_trainer |
| PlanckEnv serve | node runner.js serve mode | DET by construction — busy-wait, world frozen between ops | active (JS-only) | test_rl_env |
| GodotServeEnv | godot serve, `rl/godot_env.py` | DET by construction — mirrors serve; present but **unvalidated on cluster** | active? (see §6) | (thin) |
| curriculum profile/directive | py pure fn | DET — thresholds are `[eng.]` constants (`curriculum.py:232`); revise step NON-DET | active | test_curriculum |
| render core (`_render_frame`, camera, gif) | py PIL | DET — replays fixed witness; shared by js+godot gifs (`executors.py:348`) | active | test_render |
| render `replay_gif` py-driver | py exec game module | DET | frozen-legacy | test_render |
| pygame viewer | pygame | DET but display-bound | dormant (headless-dead) | test_viewer |
| ledger merge | py jsonl | DET idempotent — dedupe strips ts/wall_s (`telemetry.py:151`) | active | test_telemetry |
| designer_write cage | py, env-flag gated | DET given inputs; fs side-effects audited via ledger (`write.py:42`) | active (flag-OFF) | test_designer_* |

---

## 3. DEAD-CODE KILL-LIST  (sign-off checklist — delete only after Elias OK)

**CERTAIN-DEAD (safe to delete now):**
- [ ] `harness/{world,gameverify,sdk,gamegen,generator,integrity,navigator,sandbox,telemetry,templates,bank}.py` + `harness/verifier/` pkg — 11 flat-path shims + pkg, **zero importers** (grep), VERSIONS.md:15-18 flags droppable. Only real `harness/viewer` is imported.
- [ ] `harness/gen/retrieval.py:329-331` sprite-binding menu rule ("NAME the primary entity … renderer binds sprites by name") — sprite bank retired; still injected into every godot run. ASSET_BANK_V2.md:176-178 lists for deletion. *(text-delete; keep the menu builder)*
- [ ] `godotworld/diag.gd` — Rapier prober; Rapier abandoned for stock Godot Physics 2D (day-log gate 6).

**PROBABLY-DEAD (inert but still wired / defaulted-on — remove with the matching work):**
- [ ] `harness/core/spritebank.py` (275L) + render sprite branch (`render.py:200-303,359-404`) + `.gitignore banks/sprites/raw/` — `available()` hardwired False (`spritebank.py:155`); still `try/except` imported, `sprites=True` default. Fold into render_binding work; render tests still touch it.
- [ ] `godotworld/bench.py` + `godotworld/boot.gd` — spike-gate harness; imported by nothing under `harness/` (grep). Windows-exe + RAPIER refs. Keep as spike-repro only.
- [ ] `harness/verify/gameverify.py:587 run_g3` (random G3) + `runner.gd DEFAULT_SPEC` — non-default fallbacks; DEFAULT_SPEC dies with bench.py.

**DORMANT-KEEP (frozen-legacy / fallback — do NOT delete, do NOT extend):**
- [ ] `harness/legacy/**` (v1 SceneSDK: verifier L0-L2, generator, templates, navigator, sdk) — CLI-reachable `verify/play/demo`; green regression floor. (test_sdk.py docstring still French.)
- [ ] `harness/gen/gamegen.py:940-1202` inline py/js/godot template fixtures — offline/no-network test backend + frozen regression.
- [ ] `harness/rl/ppo.py` vendored PPO — **live dep**: `sb3_trainer.py:35` imports `ppo.DEFAULTS`; retire only after parity_sb3 gate passes + DEFAULTS moved out.
- [ ] `harness/verify/executors.py` PyExecutor/JsExecutor + `nodeworld/` — JsExecutor is the live JS engine; certified JS bank replayable; **run_batch is the 3D-executor template**.
- [ ] `harness/gen/prompts_js.py` + `prompts/api_js.md` + `api_py.md` — frozen-lane prompt shims (still imported at `gamegen.py:36`).
- [ ] pygame `cmd_game_watch`/`cmd_game_demo_live` + `harness/viewer.py` — valid on a local seat w/ display; operator-only, never wire into farm/designer.

---

## 4. REDESIGN TOP-10 (ranked · why · effort)

1. **P0 — G3'-on-Godot serve env** (`rl/env.py` PlanckEnv is JS-only): the sole engine gets no learnability grade / no curriculum. Blocks the whole learnability arm. *Effort: L* (`GODOT_ONLY_PIVOT.md:26-35`).
2. **P0 — Godot-lane prompt coherence** (`rules.md`, `orientation.md`, `retrieval.py:326-331`): default path injects py/js idioms (`world.rng/world.control/act()/world.add`) + retired sprite rule into the declarative godot spec. Wrong instructions shipping today. *Effort: M.*
3. **P0 — CONTRACTS.md godot `.spec.json` normative section**: the one interface doc describes only the pymunk module, never the sole active artifact (lives un-normalized in `godotworld/SPEC.md`). *Effort: M.*
4. **P0 — Executor seam as ABC/Protocol** (`verify/executors.py`): codify `run_batch` so `Godot3DExecutor` plugs in with zero funnel edits (3D insertion #1). *Effort: S.*
5. **P1 — Collapse 3-engine funnel dup** (`gameverify.py` `_verify_py/_js/_godot`, "line-for-line twins"): one `_verify_via_executor`; a 3D rung would clone a 4th copy. *Effort: M.*
6. **P1 — `_repair_user_msg` + `_extract_prompt` engine-aware** (`gamegen.py:242,188-198`; dup in `curriculum.py:353`): repair hardcodes ` ```python `; godot revise/regenerate lose provenance (slug='game'). One shared engine-aware extractor. *Effort: S.*
7. **P1 — Parts bank v2** (physics_class+role, render_binding, drop `sprite:null`): prereq for Path B + de-anchoring prompts from bank size (`ASSET_BANK_V2.md`). *Effort: M.*
8. **P1 — Split gamegen.py monolith** (1240L): extract OpenRouter backend + template fixtures (frozen DATA) from the loop. *Effort: M.*
9. **P1 — Delete flat-path shims + CLI decomposition** (cli.py 919L, legacy verbs at top level collide with `game *`; stale `--trainer` help). Cheap subtractive. *Effort: S.*
10. **P2 — G4 stale tier CLI wiring + horizon fix** (`cli.py:378` hardcodes tiers, no `--stale`; `g4.py:76 PROBE_HORIZON=120` vs G3's 300): ~250L oracle unreachable interactively + attacks short. *Effort: S.*

*(Also watch P2/P3: batch↔serve Godot provisioning dup `godot_exec.py:97`≡`godot_env.py:216`; Windows-exe default `godot_exec.py:40`; retrieval calibration as bank grows; curriculum per-round versioned dirs; STATE/VERSIONS auto-sync.)*

---

## 5. 3D INSERTION POINTS (Path B — ships EOW, per ASSET_BANK_V2 §4)

Ranked by minimal blast radius. Everything above the executor seam eats plain episode dicts and needs **zero change**.

1. **Executor** — add `Godot3DExecutor` (or `mode=3d`) behind the SAME `run_batch(game_source, episodes, max_ticks, frames_every, escape_margin)` surface (`executors.py:1-36,159`). G1/G3/G4/render untouched.
2. **Runner** — `godot_exec.py:92 runner_rel` selects `runner_3d.gd` (Node3D + camera/projection). Frozen `runner.gd` (2D) untouched.
3. **Engine token / schema** — new token (`godot3d`) or spec `version` field (`gamegen.py:138-176 detect_engine`); `spec.schema.json` gains `volume.glb` + `render_binding.tscn` (slots already reserved).
4. **Bank** — drop `sprite:null` hard-require (`bank.py:205-206`); add `physics_class/role/volume.footprint_2d(+reserved volume.glb)/render_binding.primitive_2d(+reserved tscn/glb)`; new `banks/parts/v2/` catalog — `integrity._bank_hashes` + `load_bank(version)` are version-generic so v2 is auto-tracked.
5. **Menu / prompt** — `retrieval.build_menu` new `name|volume|role|objective|overrides` line + delete dead sprite rule; `prompts/api_godot.md` gains a 3D section (`compose` is already section-split).
6. **Renderer** — a 3D→2D projection step; `executors.normalize_godot_record` already bbox-fallbacks, so projection slots in without touching `render.py`.
7. **Designer** — add the placement-feedback static-spec-analyzer as a 4th frozen tool in `designer/tools.py REGISTRY` (Elias decision).

**THE REAL COST (not a seam):** re-port G0-G3 bounds/solidity/world_size to 3D — the 2D AABB scan (`gameverify.py:692 _solidity_scan`, 4-tuple bbox), scalar `angle` (`statetree.fingerprint`), and `g4._speed` (vel[0]²+vel[1]² only) are hard-2D. Contract §2 world is a 2D rect. Verifier/runner scope, separately priced.

**Untouched:** retrieval/prompt/integrity/telemetry/ledger/curriculum/designer-cage core; py/js frozen lanes; the whole above-seam funnel skeleton.

---

## 6. STALE DOCS TO FIX (docs ⟶ reality, all normative docs mtime 14:56, pre-build-day)

- **STATE.md:16-30** — frames py|js|godot as co-equal targets; pivot made godot sole + py/js frozen (`GODOT_ONLY_PIVOT.md:9-22`, `gamegen.py:713`, `cli.py:789`).
- **STATE.md:117 / VERSIONS.md:116-121** — call "wire the tree as G3 solver" a *next step*; already DONE, default (`gameverify.py:79 G3_SOLVER='tree'`).
- **GODOT_ONLY_PIVOT.md:26 / ORCD_DAY1_LOG.md #3** — "G3'-on-godot does not exist" and "G4/replay-on-godot not wired"; but `certify.py:118` routes godot→GodotServeEnv (+`:72 _bridge_replay_godot`), `g4.py:343` routes godot→GodotExecutor, `executors.render_godot_replay`+`cli.py:334` wire godot GIF. **RECONCILE: is the godot serve path validated on-cluster, or is the code present-but-unvalidated?** (day-log gates were JS-only) — must settle before the campaign leans on godot learnability.
- **CONTRACTS.md** — entirely pymunk-python framed (World 800×600, §2 python module); **never describes the godot `.spec.json`** (the sole active artifact). §6 CLI lists only new/verify/replay — missing attack/curriculum/watch/demo/stats/rl/ledger.
- **STATE.md:52 / ORCD_DAY1_LOG.md:137** — cite `godotworld/examples/*.spec.json`; path does NOT exist. Specs live at `tests/fixtures/godot_specs/*.spec.json`.
- **VERSIONS.md:88-90** — says nodeworld "not yet wired / frozen until executor port lands"; contradicted by `executors.JsExecutor` (live JS engine) and its own line 83.
- **certify.py:35,79-80 docstrings** + **cli.py:883-884 help** — still call `vendored` the default trainer; SB3 is the real default (`certify.py:74`, `cli.py:882`).
- **gamegen.py:814-815 docstring** — says engine "defaults to … `py`"; code defaults to `godot` (`:720`).
- **g4.py:76** comment "matches G3 PROBE_HORIZON" — G3 is 300 since v2.3, G4 still 120.

---
*Full evidence in the Dive A–D findings; this map is the index, not the archive.*
