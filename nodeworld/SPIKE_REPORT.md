# Spike report — Planck.js substrate ("rung-4 step 1")

**Question under test.** `notes/GAME_ENGINE_INTEGRATIONS.md` recommends, as the first
real-engine-family step, porting the harness's pymunk substrate to **Planck.js (Box2D)
in pure Node** — claiming it keeps the engine↔agent loop *at parity* with today
(deterministic, headless, faster-than-realtime) while adding a second generation
LANGUAGE (JS) to test the harness's generality. This spike PROVES or REFUTES that with
measurements, in a self-contained `nodeworld/` directory. The frozen `harness/` code is
untouched.

**Verdict up front: PROVEN.** All four decisive properties hold. The port is viable; the
open items are engineering choices, not blockers.

Environment: Windows 11, Node v22.14.0, npm 10.9.2, **planck 1.5.0** (local `node_modules`),
anaconda Python 3.12.7 + pymunk 7.3.0. Measured 2026-07-13 (single sequential run; the
generation campaign was left undisturbed — bursts were short and never parallel).

---

## PASS/FAIL table

| Criterion | Result | Key numbers |
|---|---|---|
| **(a) Startup** (cold `node runner.js`) | **PASS** | median **67.2 ms** (5 runs: 62–69 ms) |
| **(b) Throughput parity** vs pymunk | **PASS** | Node **223 eps/s** vs pymunk **379 eps/s** = **0.59×** raw; **~0.94×** net of cold-start |
| **(c) Determinism (bitwise)** — the decisive test | **PASS** | cross-process **byte-identical** (19 624 B ≡ 19 624 B); within-process same-seed **identical** |
| **(d) Solvability** ≤ 40 episodes | **PASS** | solved @ episode 0 in 61 ticks; **34/40** episodes solved; **no dead milestones** |
| **Sandbox story** | PASS (spike-level) | AST-equivalent token scan + `node:vm`; honest limits documented below |
| **API parity** | PASS with noted gaps | pixel-scale retune + engine-specific numbers/rng (see table) |

**OVERALL: PASS.** Reproduce with `python bench.py` (writes `bench_results.json`).

---

## The numbers, in detail

### (a) Startup — 67 ms median
A cold `node runner.js` (spawn + `require('planck')` + a trivial 1-episode job) runs in
**67 ms median**. This is a non-issue for the harness: G3 batches *all* episodes into ONE
process (the runner takes a whole `episodes[]` array on stdin), so cold-start is paid once
per verification, not once per episode — exactly the "batch all G3 episodes in one
invocation" discipline the notes prescribe for engine ports.

### (b) Throughput — parity with pymunk
Identical batch on both substrates: **40 episodes × up to 120 decision ticks × 6 physics
steps/tick**, seeded by `random.Random(episode)` macro-actions built *byte-for-byte* like
`gameverify._macro_plan` (`choice` + `randint(1,4)` holds).

| Substrate | Wall (40 eps) | eps/s | solved |
|---|---|---|---|
| Node / Planck | 179 ms | 223 | 34/40 |
| pymunk / Python | 106 ms | 379 | 35/40 |

Raw ratio **0.59×**. But the Node wall *includes* its ~67 ms process cold-start, whereas
the pymunk figure is measured after imports; netting out cold-start gives ~112 ms → **~357
eps/s ≈ 0.94× pymunk**. Since G3 always runs as one batched process, the amortized figure
is the honest one: **Node is at throughput parity with pymunk**, and both are ~3 orders of
magnitude faster than realtime (≈160 k physics steps/s in Node). The recommendation's
"faster than realtime, loop at parity" claim holds.

(The 34 vs 35 solved is expected: the two engines are different — same *plans*, different
*physics numbers* — so the exact set of solving seeds differs by one. Both comfortably clear
the "solvable" bar.)

### (c) Determinism — the decisive test, PASS
- **Cross-process:** the same job piped to **two independent `node` processes** produced
  **byte-identical** stdout (19 624 bytes each), snapshots and all. Planck is pure-JS
  IEEE-754 with no threading/SIMD reordering, we fix `dt`, velocity/position iterations,
  and disable body sleeping, and we drive iteration order through insertion-ordered `Map`s
  → V8's shortest-round-trip float formatting makes the JSONL reproducible.
- **Within-process:** two episodes with identical `(seed, actions)` produced identical
  output lines.

This is the property the whole harness rests on (G1's determinism oracle, G3's replayable
witness). It transfers to Planck cleanly.

### (d) Solvability — PASS, with a benign order note
The seeded random probe solves the ported `sample_drift` at **episode 0 in 61 ticks**;
**34/40** episodes reach the pad. On the witness, all three declared checkpoints latched
before the success tick → **no dead milestones**:

```
witness checkpoints = { moved_off_start: 3, crossed_midline: 29, entered_upper_half: 20 }
declared order      = [moved_off_start, crossed_midline, entered_upper_half]
empirical order     = [moved_off_start, entered_upper_half, crossed_midline]
```

`entered_upper_half` (y>300) latched before `crossed_midline` (x>400) because the random
impulse path went up before right. This is **non-fatal by design** — it is exactly the
"declared-vs-empirical order mismatch → non-fatal `warnings` entry" that
`gameverify.run_g3` already models (`_order_mismatch`). The milestone *set* is correct; the
model merely mis-ordered two of them. The dead-milestone check (the fatal one) passes.

---

## Sandbox story (spike-level honesty)

Two cooperative layers in `runner.js`, mirroring the intent of `harness/sandbox.py`:

1. **Static token scan.** The source is stripped of comments and string/template literals
   (so a game whose `PROMPT` legitimately contains "import" is not a false positive — the
   JS analogue of the Python side's AST scan), then matched against
   `require · import · process · globalThis · eval · Function( · constructor.constructor ·
   child_process · fs · __proto__ · Reflect · Proxy`. Verified: `require("fs")` in a build
   function is rejected in-band (`"sandbox scan rejected source: require"`).
2. **`node:vm` context** exposing ONLY a **frozen `Math`** (plus vm's own JS builtins). No
   Node globals reach the game; `world` is passed as a function argument. Verified: a game
   using `Math.cos`, `world.rng.uniform`, `on_step`, and `failure` runs correctly.

**What this does NOT defend against (stated plainly):** `node:vm` is *not* a security
boundary for adversarial code (realm/prototype tricks, unbounded CPU/allocation the scan
can't see). The stripper is naive. **The real isolation is the Python side's job**: a
separate OS process with a hard timeout + kill, exactly as `harness/sandbox.run_sandboxed`
already does. This runner is the inner, cooperative layer only; the outer process-level kill
is out of scope for the spike (and belongs in Python later).

---

## API parity — `nodeworld/world.js` vs `harness/world.py`

Every §1 method is mirrored (construction, dynamics, pure queries, harness-side). Gaps are
engine-family differences, not missing capability:

| Area | pymunk (today) | Planck port | Note |
|---|---|---|---|
| Units | pixels, native | pixels external; **retuned** `Settings.lengthUnitsPerMeter=50`, `maxTranslation=1000` | Box2D is meter-tuned and would clamp a px-scale world; a production port should instead pick an explicit px↔m scale. |
| Mass | explicit `mass` + `moment_for_*` | `density = mass/area` so `getMass()==mass`; inertia is Box2D-derived | `locked_rotation` → `fixedRotation` maps exactly. |
| `contacts/touching/grounded/penetration_depth` | `shapes_collide` + distance ≤ 1 px | live contact list + `WorldManifold` (`isTouching`, `separations`, normal) | Validated: ball rests `grounded`, penetration 0.25 px, sensor pad excluded from `touching`. |
| Joints | PinJoint / PivotJoint / DampedSpring | `pin`→rigid `DistanceJoint`, `pivot`→`RevoluteJoint`, `spring`→soft `DistanceJoint` | spring `(stiffness,damping)`→`(frequencyHz,dampingRatio)` is a heuristic remap, not physically identical. |
| RNG | `random.Random` (Mersenne) | `mulberry32` | Deterministic within JS; **not** value-identical to Python. A game that *reads* rng rolls different numbers on Node vs pymunk (drift uses none; the `drop` template's `rng.uniform` would differ). |
| NaN/explosion sentinel | `_sane()` freeze + event | same (`nan_detected` event, freeze) | identical semantics. |
| snapshot / events / frames | dict, insertion order | `Map`-ordered; runner also emits `frames` (tick + per-entity `query`) for the Python renderer | matches the "Python renderer consumes frames" plan. |

**Consequence to state in the submission:** a *game module* is engine-specific in its
physics numbers and its randomness — games are not bit-portable between pymunk and Planck.
That is expected and healthy: it is precisely why the notes want a second engine *family*
to test the harness's generality. What transfers is the **structure** (the four loop legs,
the §2 runner semantics, determinism, the oracle contract) — and that transfers cleanly.

---

## Integration sketch (NOT implemented — for the full-parity work later)

The clean seam is an **episode-executor abstraction** in `gameverify`. Today
`run_episode(game, world, actions_iter, max_ticks)` is Python-native and is reused by G1,
G3, replay, and future policies. For a JS backend:

- **`gameverify` — add an executor interface** `execute(game_source, [episode specs]) →
  [episode dicts]`. The pymunk path stays as-is (loop `run_episode` in-process). The JS path
  batches all episodes into ONE `node runner.js` job and parses the JSONL back into the
  *same* episode-dict shape (`result / ticks / checkpoints / snapshot`). This sits one level
  above the existing `world_factory=` seam (the whole build+step+read moves to Node, not just
  world construction). **Every oracle stays in Python** and keeps eating dicts: G0 static,
  G2 goal purity, and all of G3's logic — determinism check, dead-milestone check,
  declared-vs-empirical order warning, checkpoint-guided second pass, UNSOLVED progress
  diagnosis — work unchanged on the returned dicts. They never touch the engine.
- **G0 static scan for JS.** `harness/sandbox.py`'s AST scan is Python-specific; route JS
  games to the runner's token scan (or, better, a real JS parser such as `acorn`) plus a
  `node --check` and a load-probe for the required symbols.
- **`gamegen` — a JS prompt variant.** Same open-ended DESIGN-then-code structure and repair
  loop; only the World-API reference and §2 format are re-expressed in JS terms
  (options-object `add(...)`, `function` declarations, `Math` available, no imports). Tag
  each generated game with its engine (`"py"｜"js"`) so `gameverify` routes to the right
  executor.
- **`render.py`.** Already draws from `world.query()` only; point it at the runner's emitted
  `frames` (JSONL) instead of stepping a live world — a thin change.

None of the above is implemented here; it is the map for the ~real port.

---

## Files created (all under `nodeworld/`)

| File | Role |
|---|---|
| `package.json` | pins `planck@1.5.0`; `npm install` → local `node_modules/` (no global installs) |
| `world.js` | `World` class over Planck mirroring CONTRACTS §1 (construction / dynamics / queries / harness-side); seeded `mulberry32` RNG |
| `runner.js` | stdin-job → per-episode JSONL CLI; §2 decision-tick semantics (act → 6×step+on_step → latch → failure → success); the `node:vm` sandbox |
| `sample_drift.js` | hand-port of `harness/gamegen._DRIFT` (ice puck, 4 impulse actions, sensor pad, 3 checkpoints) in the JS §2 format |
| `bench.py` | stdlib-only driver: startup / throughput-vs-pymunk / determinism / solvability; writes `bench_results.json` |
| `bench_results.json` | machine-readable results from the last `bench.py` run |
| `SPIKE_REPORT.md` | this document |

**Known footnote:** planck 1.5.0 declares `engines.node >= 24`; it runs correctly on the
installed Node 22.14 (npm emits a non-fatal `EBADENGINE` warning). A production setup should
either bump Node to ≥24 or pin a planck line that supports Node 22.
