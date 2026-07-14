# Spike report — Godot headless physics lane ("rung-4 step 2")

**Question under test.** `notes/engines/GODOT_MIGRATION.md` returned **GO-with-spike** for
moving the rung-4 "real engine" lane to Godot. Before writing a `GodotExecutor`, the note
demands four hard, *measured* gates: steady-state boot cost, batched throughput, and — the
decisive one — **byte-identical determinism** across fresh processes, for stock Godot Physics
2D and/or the Rapier2D GDExtension, plus a plausible per-tick state readback. This spike
installs Godot 4.7, builds a ~200-line `runner.gd` that shadows `nodeworld/runner.js` (JSON
job in / JSONL out, K=6 steps per decision tick, fresh world per episode, y-UP px), and
measures all four in a self-contained `godotworld/`. The frozen `harness/` and `nodeworld/`
are untouched.

**Verdict up front: GO.** All four gates PASS, with margin. Both stock Godot Physics 2D **and**
Rapier2D are byte-deterministic across fresh processes on this machine — so we get the
determinism guarantee *and* can defer the stock-vs-rapier dependency choice to the full lane.

Environment: Windows 11 Pro (26200), Intel Core Ultra 7 155H (22 logical cores), Python 3.12.
**Godot 4.7.stable.official (`5b4e0cb0f`)**, portable console build. **godot-rapier-physics
v0.8.39**, 2D `single-simd-parallel` variant (GDExtension, `compatibility_minimum = 4.7`).
Measured 2026-07-14 with `python godotworld/bench.py` (writes `bench_results.json`).

---

## PASS/FAIL table

| Gate | Result | Key numbers |
|---|---|---|
| **(a) BOOT** — steady-state `godot --headless -s boot.gd` | **PASS** | median **0.190 s**, min 0.187 s, 5 warmed runs (0.187–0.202); gate ≤ 2 s → **~10× margin** |
| **(b) BATCH** — 20 ep × 120 ticks in ONE process | **PASS** | median **2.04 s**, min **1.85 s** (incl. boot); 14 400 physics steps; ~7.8k steps/s wall; gate ≤ 5 s |
| **(c) DETERMINISM** — same job, fresh processes, ×5 | **PASS** | **stock byte-identical** (9663 B) **AND rapier byte-identical** (9667 B); stock ≠ rapier (distinct engines confirmed); gate = ≥1 byte-stable |
| **(d) STATE READBACK** — per-tick state under gravity | **PASS** | ball falls 400 → **79.70** (stock) / **79.87** (rapier), monotone then settles (|v_y|<1); rest slop **0.30 px** (stock) / **0.13 px** (rapier) vs ideal 80 |
| stdin delivery (report note) | works | piped-stdin job delivered a valid JSONL record on Windows headless (contra the note's caution) |

**OVERALL: PASS → GO.** Reproduce with `python godotworld/bench.py`.

---

## The numbers, in detail

### (a) BOOT — 0.190 s median
A trivial `boot.gd` (`extends SceneTree`; print one line; `quit()`) launched via the portable
console exe runs in **0.190 s median** after two warmup runs. Even the *very first* cold run in
this session (an earlier probe, before any warmup) was **0.73 s** — the scary 30 s+ headless-boot
reports in the note's §5 (Defender/AMD-driver, "can't repro on Linux") **did not materialize** on
this box. Boot is a non-issue: the executor batches a whole verification layer into one process,
so this tax is paid once per layer, not once per episode.

### (b) BATCH — 20 episodes, one process, 2.0 s
One `--headless --fixed-fps 60 -s runner.gd -- --job=<file>` process ran **20 episodes ×
120 decision ticks × 6 physics steps = 14 400 steps**, each episode building a fresh 3-body scene
(static floor + dynamic ball + dynamic box) and applying a scripted impulse per tick. Wall:
**median 2.04 s, min 1.85 s including boot** — comfortably inside the 5 s gate. 20/20 JSONL
records returned and parsed.

Throughput is **~7.8k physics-steps/s wall**. That is *not* physics-bound (3 bodies is trivial);
it is the cost of the main-loop-driven stepping model — one `await physics_frame` == one physics
step == one full `SceneTree` iteration (~120 µs of engine/signal/coroutine overhead per step).
See the throughput caveat below; it clears the gate but shapes the full-lane scaling story.

### (c) DETERMINISM — the decisive gate, PASS on BOTH engines
The same 20 ep × 60 tick job was run in **5 fresh processes** per engine; the JSONL payload
(sliced between `__JSONL_BEGIN__/__END__`, banner excluded) was compared byte-for-byte:

- **Stock Godot Physics 2D:** all 5 runs **byte-identical** (9663 B each).
- **Rapier2D (simd-parallel):** all 5 runs **byte-identical** (9667 B each).
- **stock ≠ rapier** (the two payloads differ), confirming the rapier server is genuinely active
  and not silently falling back to stock.

This is the property the whole harness rests on (G1 determinism oracle, G3 replayable witness).
It holds — and holds for *both* backends under our discipline (fixed 60 Hz tick, jitter-fix off,
physics on the main thread, fresh deterministic world per episode, all mutation inside the physics
loop, `%.17f` full-precision float output). The note predicted stock *might* pass if we never reset
from `_process`; it does. Rapier stays available as the cross-machine-replay insurance (the
`enhanced-determinism` variant is downloaded but not needed for same-machine byte-stability).

### (d) STATE READBACK — plausible gravity, tight rest
A pure-drop episode (40 ticks of `none`, `frames_every=1`) with full per-tick state
(`pos`/`vel`/`angle` for floor, ball, box):

```
ball.y by tick: 400.0, 396.25, 383.5, 361.75, 331.0, 291.25, 242.5, 184.75, ... -> rest
```

Monotone descent under gravity (0,−900), then settles. Floor top = 60, ball radius = 20 →
ideal rest center **y = 80**.

| Body | Engine | rest y | penetration/slop vs ideal 80 |
|---|---|---|---|
| ball (r=20) | stock | 79.700 | **0.300 px** |
| ball (r=20) | rapier | 79.875 | **0.125 px** |
| box (40×40) | stock | 79.700 | 0.300 px |
| box (40×40) | rapier | 79.875 | 0.125 px |

Both are physically sane resting states (comparable to the Planck spike's 0.25 px slop), with
final |v_y| < 1. Positions are **float32** (`real_t` in the standard build), which is why they
quantize cleanly and why byte-determinism is exact.

---

## Gotchas hit (the honest part)

1. **GDExtension does NOT load in a fresh headless `-s` run** — the #1 setup gotcha. A brand-new
   project runs `res://runner.gd` fine but rapier never registers: `ClassDB.class_exists(
   "RapierPhysicsServer2D")` is **false** even though `physics/2d/physics_engine == "Rapier2D"`,
   and there is **no error printed** (silent stock fallback). The fix: GDExtensions are loaded from
   `res://.godot/extension_list.cfg`, which is generated by a one-time **`godot --headless --import
   --path <proj>`**. After that import, rapier loads headless on every subsequent run. `bench.py`
   does this import automatically when `extension_list.cfg` is absent; the `GodotExecutor`
   provisioner must do the same once per project checkout.

2. **`JSON.stringify` silently rounds floats → would MASK determinism drift.** Godot 4.7's
   `JSON.stringify` prints `0.1 + 0.2` as `"0.3"` and truncates to ~15 sig figs. Using it for the
   numeric payload could hide low-bit non-determinism and make a FAIL look like a PASS. `runner.gd`
   therefore emits every float with `"%.17f"` (full float64 precision, verified round-trippable)
   and builds JSON by hand. **Any engine port must control float formatting itself** for the
   determinism gate to mean anything.

3. **stdin actually works headless on Windows** (contra `GODOT_MIGRATION.md` §2.2's caution). A
   job piped to `OS.read_string_from_stdin()` in a loop-to-EOF delivered a valid record. The
   temp-file `--job=<path>` route remains the recommended robust default (and is what the gates
   used), but stdin is a viable fallback here, not a dead end.

4. **The `*_console.exe` is bundled, not a separate download.** The standard
   `Godot_v4.7-stable_win64.exe.zip` extracts to both `Godot_v4.7-stable_win64.exe` (GUI subsystem)
   and `Godot_v4.7-stable_win64_console.exe` (198 KB console wrapper). The console variant streams
   stdout to a subprocess pipe cleanly — use it for the executor.

5. **Engine log noise on stdout/stderr.** Godot prints a version banner, and rapier (godot-rust)
   prints a hot-reload `TypedSignal` warning during import. `runner.gd` frames its payload between
   `__JSONL_BEGIN__` / `__JSONL_END__` markers so the Python side slices the exact bytes and
   ignores everything else — essential for the byte-compare in gate (c).

6. **Godot 4.7 vs the note's 4.6.** Current stable moved to 4.7 (2026-06-18); rapier v0.8.39's
   `compatibility_minimum = 4.7` matches exactly, so no version friction.

---

## Throughput caveat (a scaling note, not a blocker)

Stepping is driven by the `SceneTree` main loop (no user-callable `space_step`), so each physics
step costs one full loop iteration (~120 µs here), not just the ~µs of 3-body solve. That caps a
single process at **~7.8k steps/s** regardless of body count being trivial. The spike gate passes
with margin, but the full lane must lean on exactly what `GODOT_MIGRATION.md` §5 prescribes:
**batch many episodes per process** (boot amortized to ~0.19 s) **and parallelize across cores**
(22 logical here). G3 (40 ep × 120 tick ≈ 29k steps ≈ 3.7 s/process) is fine single-core; G4
fuzzing (thousands of episodes) needs the core fan-out. A later optimization — driving multiple
physics sub-steps per idle frame, or a custom `PhysicsServer2D` step pump — could lift the
single-process ceiling, but is not needed to graduate the lane.

---

## GO / NO-GO

**GO.** The one true blocker the note flagged — *neither* stock nor rapier giving byte-stable
same-seed snapshots — is decisively refuted: **both** are byte-identical across fresh processes.
Boot is ~10× under gate, batched throughput is ~2.5× under gate, state readback is plausible and
tight, rapier loads headless (with the one-time import), and stdin even works. Proceed to the PM
plan (§8): scaffold `GodotExecutor` (copy `JsExecutor`, swap `node`→godot console exe + `--job`
temp-file argv), the `_verify_godot` funnel twin, and a `tests/test_godot.py` parity test that
skips when the godot binary is absent — exactly how `nodeworld/` graduated.

---

## Files created (all under `godotworld/`; `tools/` is gitignored)

| File | Role |
|---|---|
| `project.godot` | stock-physics project (fixed 60 Hz, jitter-fix off, gravity (0,−900), damping 0, main-thread physics) |
| `runner.gd` | the spike main (`extends SceneTree`): JSON job → per-episode JSONL; K=6 `await physics_frame` steps/tick; fresh floor+ball+box scene per episode; `%.17f` float output; `--job=`/stdin input |
| `boot.gd` | trivial print-and-quit script for the BOOT gate |
| `diag.gd` | reports which physics backend is live (proves rapier registers headless) |
| `bench.py` | stdlib-only driver measuring gates (a)–(d) + stdin probe; writes `bench_results.json`; builds/imports the rapier project variant |
| `bench_results.json` | machine-readable results from the last run |
| `SPIKE_REPORT.md` | this document |
| `tools/` *(gitignored)* | portable Godot 4.7 console binary, rapier v0.8.39 2D addon zips + extracted addon, and the derived `rapier_project/` (stock project + `physics_engine="Rapier2D"` + imported `.godot/`) |

**Provisioning note for the full lane:** the Godot binary and rapier addon are ~130 MB
multi-platform artifacts kept out of git (gitignored `tools/`). The `GodotExecutor` will need a
small fetch/verify step (download the official `Godot_v4.7-stable_win64.exe.zip` from
`github.com/godotengine/godot/releases` + the rapier `v0.8.39` 2D asset, run the one-time
`--import`) — the analogue of `nodeworld`'s `npm install`.
