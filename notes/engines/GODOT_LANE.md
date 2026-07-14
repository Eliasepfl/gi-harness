# Godot lane — what landed (rung-4, PM of the spike day)

> The half-day spike (`godotworld/SPIKE_REPORT.md`) returned **GO** on all four gates
> (boot, batch, byte-determinism, state readback) for stock Godot Physics 2D. This note
> records the REAL lane scaffolded on top of it: a `GodotExecutor` speaking the exact
> executor seam, driven by a **FROZEN, audited `runner.gd`** that interprets a
> **declarative JSON game-spec**. No untrusted GDScript ever runs (the posture decided in
> `GODOT_MIGRATION.md §2.3` / `GODOT_SKILLS_WORLDGEN.md §3`).

## What landed

| Piece | File |
|---|---|
| Game-spec v1 doc + JSON Schema | `godotworld/SPEC.md`, `godotworld/spec.schema.json` |
| Frozen interpreter (episodes + check modes, spec parser, predicate DSL) | `godotworld/runner.gd` |
| `GodotExecutor` (3rd engine seam, twin of `JsExecutor`) | `harness/verify/godot_exec.py` (re-exported from `executors.py`) |
| `_verify_godot` funnel + `.spec.json`/`"engine":"godot"` routing | `harness/verify/gameverify.py` |
| Three hand-written example games | `godotworld/examples/{traverse,collect2,escape}.spec.json` |
| Tests (schema + infra always-run, e2e skipif no binary) | `tests/test_godot_exec.py` (24 tests) |

The lane reuses the **universal oracles unchanged**: G0/G2 come from the runner's `check`
facts fed through the SAME `run_g0_js`/`run_g2_js` (the runner emits the identical fact
shape as `nodeworld/runner.js`), and G1/G3 batch through the executor's `run_batch`. The
**Go-Explore tree solver (`treesolve.py`) runs unchanged** — it only needs `run_batch`.
`report["engine"] == "godot"`; every other report field is byte-identical to the Py/JS
lanes, so hints, witness, and the repair loop never learn physics ran in GDScript.

## The safe-by-construction predicate DSL

Predicates (`success`/`failure`/`checkpoints`, plus per-verb `when` gates) are strings in
a tiny expression language evaluated by Godot's `Expression` over a **locked-down base
instance** (only the query methods). Before any evaluation, every predicate passes a
**strict allow-list token scan** (`runner.gd:_pred_error`): identifiers must be in a fixed
set (`pos_x/pos_y/vel_x/vel_y/speed/angle/grounded/contacts/dist/flag` + `steps` +
`abs/min/max/clamp/sqrt/floor/ceil` + `and/or/not`), and attribute access (`.`), indexing,
and every other token are rejected. This — not the base instance — is the real boundary: a
string like `OS.get_name()` or `set_script("res://x")` is rejected at spec-load, never
evaluated (covered by `test_forbidden_predicate_rejected_by_whitelist`).

## Spec-expressiveness vs the 7 objective archetypes

(`harness/gen/prompts/orientation.md` — traverse / collect / deliver / activate / escape /
topple / survive.)

| Archetype | v1 | How / the gap |
|---|---|---|
| **TRAVERSE** | ✅ full | reach-x / reach-zone predicate; `velocity_clamp` for controllable speed. **Shipped: `traverse.spec.json`.** |
| **COLLECT-N** | ✅ full | `sensor` body + `on_contact`→flag per item + `remove_when`; `success` counts flags (extend with a flag-gated exit `on_contact`). **Shipped: `collect2.spec.json`.** |
| **DELIVER** | ✅ full | push a loose cargo body; `success` tests the CARGO's pos, or `on_contact(cargo, zone)`→flag. Expressible; not shipped. |
| **ESCAPE** | ✅ full | `rising_level` hazard + `failure = pos_y(player) < flag(water)` + reach-exit `on_contact`. **Shipped: `escape.spec.json`.** |
| **ACTIVATE-SEQUENCE** | ⚠️ partial | switches (`sensor`+`on_contact`) and gates (`remove_when` a wall slab) work; **strict order-enforcement** (switch B inert until A) is NOT expressible — `on_contact` latches unconditionally, and there is no flag-gated contact/behavior. Latch-*order* is only reported as a warning. |
| **TOPPLE/DESTROY** | ⚠️ partial | direct-push topple + rest/position predicates on the parts work; **joint-driven contraptions** (wrecking ball, catapult) are approximate — Godot 2D has no distance joint, so `pin`/`pivot`→`PinJoint2D` and `spring`→`DampedSpringJoint2D` only. |
| **SURVIVE-THEN-EXIT** | ⚠️ partial | `timer_flag` arms the exit after N steps + reach it works; **actively moving threats** need a per-step body-motion behavior v1 lacks (`rising_level` moves a scalar line, not a body). |

Net: **4 of 7 fully, 3 partially**, with the exact missing primitives named below.

## How to run

```bash
# The Godot 4.7 console binary + rapier live in the MAIN checkout's godotworld/tools/
# (gitignored). The executor finds it via HARNESS_GODOT_EXE, else the tools/ default
# (a worktree falls back to the main checkout automatically).
python -m harness game verify godotworld/examples/traverse.spec.json --json
python -m harness game verify godotworld/examples/collect2.spec.json --json
python -m harness game verify godotworld/examples/escape.spec.json   --json
# Pure-python + e2e tests (e2e auto-skip when the binary is absent):
python -m pytest tests/test_godot_exec.py -q
```

First run on a fresh checkout does a one-time `--headless --import` to generate
`res://.godot` (handled automatically by the executor).

## Measured (this box, stock Godot Physics 2D)

- **End-to-end verify (G0→G3), sandboxed=False:** traverse ≈ 7 s, collect2 ≈ 7 s,
  escape ≈ 5 s. Sandboxed (CLI default, 180 s budget): escape ≈ 5 s — comfortable margin.
- **Witnesses (deterministic):** traverse 185 ticks, collect2 99, escape 87 — all
  non-trivial (≥ 20) with every declared checkpoint latched in order.
- **Determinism:** byte-identical JSONL across 3 independent batches AND across 3 full
  verifications (`test_batch_bytes_identical_x3`, `test_verify_is_deterministic_x3`).
- **Suite:** `python -m pytest tests/ -q` → 394 passed, 4 skipped (374 baseline + 24 new).

## Design choices worth knowing

- **`force` is re-applied each of the K=6 sub-steps** (a sustained push over the tick),
  while `impulse`/`set_velocity` fire once — so the two verbs read differently.
- **`on_contact` latches per physics-step** (not per decision-tick), so fast fly-throughs
  of a collectible/exit are caught reliably; predicate `contacts()` is per-tick.
- **grounded** = a non-sensor contact below the body (y-UP); the first decision tick reads
  `grounded=false` (no contacts computed yet), so a grounded-gated jump fires from tick 2.
- **Penetration** (G0 initial-overlap check) is computed **analytically** (AABB) from the
  declared geometry — no physics step, exact, deterministic.
- **Backward-compat:** a job with no `source` runs a built-in `DEFAULT_SPEC` (the spike's
  3-body scene), so `godotworld/bench.py` still reproduces the spike gates.

## Follow-ups

1. **Two on_step behaviors to close the partial archetypes:** a `move_body`/`patrol`
   driver (moving threats → SURVIVE; moving platforms → the `CONTRACTS §9.7` deviation) and
   a flag-gated variant of `on_contact`/`remove_when` (ordered switches → ACTIVATE-SEQUENCE).
2. **A real distance joint** (or a kinematic tether driver) so joint contraptions (wrecking
   ball, catapult) are exact rather than `PinJoint2D`-approximate → TOPPLE.
3. **Certified `.tscn` templates + the parts bank** — resolve `bodies` entries to
   pre-certified `.tscn` assemblies (`GODOT_MIGRATION §2.3`) instead of raw primitives.
4. **Sprites + Camera2D demo capture** — a `Camera2D`-follow render for the GI site
   (`GODOT_MIGRATION §4`), never a pixel verification oracle.
5. **Cross-machine replay** — swap to the Rapier cross-platform-deterministic build if
   witnesses must replay across dev boxes (same-machine byte-stability already holds).
6. **The Godot generator prompt** — teach `gamegen` to emit the spec + `Parts used:`
   (a later wave; this lane deliberately did not touch `prompts/`).
