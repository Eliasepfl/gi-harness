# 3D physics determinism — the reused-World3D reset leak (and its host-level fix)

**Status:** RESOLVED, bit-exact. Fix in `godotworld/serve_game.gd` (`_rebuild` +
`_batch_build_game`). Regression fixture `tests/fixtures/gd_games/tumble_3d.gd`; pins in
`tests/test_gd_lane.py`. Godot 4.7-stable (official), stock GodotPhysics (2D + 3D DEFAULT),
`gi-certifier.sif`.

## Symptom

The first real 3D games through the fix3d funnel FAILED G1's twin-rollout determinism gate:

| game (attempt)                         | G1 `determinism.delta` | where            |
|----------------------------------------|------------------------|------------------|
| `a_3d_parking_challenge…` (a4, car)    | **0.046478**           | car pos/vel      |
| `a_3d_drone_course…` (a2/a3, quadcopter)| **0.001276**          | drone            |
| `a_3d_drone_course…` (a4, quadcopter)  | **5.9e-05**            | drone **angle**  |

2D games had been byte-identical for weeks on the same serve host. The tiny epsilons looked
like SIMD/threading noise, and the repair loop wasted rounds telling innocent games to "fix"
it. It was **ours to fix**, at the host — but the cause was **not** a threading/SIMD/solver
setting.

## Reproduction (evidence-first)

`run_g1` (CONTRACTS §4) checks determinism by running **two seeded episodes back-to-back on
ONE serve host** and comparing final snapshots:
`r1, r2 = executor.run_batch(src, [noop_spec, noop_spec], NOOP_TICKS)` at `WORLD_SEED=0`.

Driving the exact wild `.gd` through `GdExecutor` in-image reproduced it precisely
(`drone_a4` → delta `5.89727933e-05` at the drone's **angle**, episode ends tick 12 at a
floor crash; `parking_a4` → `0.046478`).

The decisive experiment — **within-session vs cross-session**:

| mode                                              | delta      |
|---------------------------------------------------|------------|
| within-session (two resets, SAME process) = G1    | 5.9e-05    |
| cross-session (two SEPARATE processes, same seed) | **0.0**    |

Two independent processes are **byte-identical**; two resets in one process **diverge**. The
divergence is therefore **not** SIMD/threading/floating-point run-to-run noise (that would hit
cross-session too) — it is **state that leaks across the reset boundary within a process.**

## Root cause

The single-instance reset path adds the game straight under `root` (the main viewport), so a
3D game's `RigidBody3D` dynamics live in **`root`'s World3D — a physics space the path REUSES
across every episode**. `_rebuild` frees the old game (`queue_free` + `await process_frame`)
and builds a new one **in the same space**. GodotPhysics3D keeps **per-space** state that a
body `free()` does not scrub — the **broadphase structure** (built from the sequence of
add/move/remove ops; each static body is inserted at the origin on tree-entry, then MOVED to
its position) and solver/contact caches. The sequence differs between:

* **episode 1**, built over the **unstepped** `init` space (bodies at spawn, no contacts), and
* **episode 2**, built over episode 1's **stepped** end-state (bodies moved/collided).

So the two episodes' first collision resolves with a different broadphase pair-iteration order
→ a tiny, growing floating-point divergence. It only surfaces on games whose trajectory never
settles to a rest attractor that would mask it (force-driven drone/car; a settling pile-up or a
zero-g floater like `mini_collect_3d` hides it) — which is exactly why the fixtures looked fine
while the wild games failed. 2D never showed it because the failing games happened to be 3D and
2D's reused `World2D` was not exercised into the sensitive regime; the fix leaves 2D untouched.

The trigger is delicate: a hand-built fixture only reproduced once its static bodies were
registered in the **wild game's order** — `add_child(body)` (→ broadphase insert at origin)
**before** setting `position` — confirming the broadphase-structure residual as the mechanism.

## Fix (host-level, every 3D game inherits it)

Hand **every 3D episode a FRESH `World3D`** so ZERO physics-space state crosses the reset
boundary. In `serve_game.gd`:

* `_rebuild` (single-instance path, used by G1/G3): before `root.add_child(inst)`,
  `if inst is Node3D: root.world_3d = World3D.new()`.
* `_batch_build_game` (batched RL/vec path, reuses a per-instance SubViewport): alongside
  `vp.own_world_3d = inst is Node3D`, `if inst is Node3D: vp.world_3d = World3D.new()`.

2D is deliberately untouched (`root.world_2d` / `vp.world_2d` unchanged) → 2D replays stay
byte-for-byte identical. No project setting can fix a cross-episode leak; a fresh space is the
principled, engine-agnostic pin. Games need know nothing (the `PhysicsServer3D.set_active(true)`
contract is unchanged).

## Result — bit-exact, both speedups

Real `run_g1` determinism check, in-image, post-fix:

```
drone_a4.gd   sp=1  determinism = {"pass": true, "delta": 0.0}
parking_a4.gd sp=1  determinism = {"pass": true, "delta": 0.0}
drone_a4.gd   sp=8  determinism = {"pass": true, "delta": 0.0}
parking_a4.gd sp=8  determinism = {"pass": true, "delta": 0.0}
```

**Bit-exact (delta = 0.0)**, not epsilon-bounded. Held at `HARNESS_GODOT_SPEEDUP` 1 and 8.

## Regression tests (`tests/test_gd_lane.py`)

* `tumble_3d.gd` — a purpose-built reproducer: a force-driven `RigidBody3D` dropped into an
  enclosed canyon, episode ending AT its floor contact (sampling the divergent transient).
  Diverged **8.8e-05** on the pre-fix host, **0.0** after — a within-session twin
  (`test_tumble_3d_reset_determinism_is_byte_identical`, speedups {1, 8}) that re-fails if the
  fresh-World3D pin is reverted.
* `test_mini_collect_3d_reset_determinism_is_byte_identical` (speedups {1, 8}) — the mandated
  broad 3D pin.
* `test_2d_reset_determinism_unchanged_within_session` (`mini_collect`, `walled_goal`) — proves
  the pin does not perturb 2D resets.

A within-session twin is **required** — cross-session was byte-identical even unfixed, so a
two-process determinism test would have missed the bug entirely (a trap worth remembering).

## Files

* `godotworld/serve_game.gd` — `_rebuild`, `_batch_build_game` (the fresh-World3D pins).
* `tests/fixtures/gd_games/tumble_3d.gd` — regression fixture.
* `tests/test_gd_lane.py` — `_reset_twin_snapshots` helper + four pins.

---

# Capture-lane tick parity — the settle-frame phase bug (and its host-level fix)

**Status:** RESOLVED, bit-exact. Fix in `godotworld/capture_host.gd` (`_run`). Parity pins in
`tests/test_gd_lane.py`. Same engine/image as above.

## Symptom

The FIRST CERTIFIED 3D GAME (`scenes/games/a_3d_game_fly_a_small_craft_through_a_se`, a
239-tick witness that certifies SUCCESS through serve post-World3D-fix) **FAILED at tick 18**
when its own certified witness was replayed through the CAPTURE host
(`~/orcd/scratch/gi/logs/capfirst3d-18041841.log`: `FAILURE ticks:18 frames:31`). The demo
must BE the witness, so a divergence is disqualifying.

## Reproduction (evidence-first)

Drove the certified witness (seed 0, 239 actions) through BOTH hosts, logging the per-tick
`state()` fingerprint (serve via `GdExecutor.run_batch(frames_every=1)`; capture via
`capture_host.gd --fingerprint --no-frames`), and diffed at parsed-double precision:

| host / config                    | result       | first divergent tick        |
|----------------------------------|--------------|-----------------------------|
| serve (reference), sp 1 **and** 8| SUCCESS@239  | — (sp1 trail == sp8 trail)  |
| capture, sp1, undressed (pre-fix)| FAILURE@18   | **tick 1** (glider z)       |

The raw glider trail was decisive — **t=0 byte-identical**, then at t=1 capture led serve by
**exactly one physics step**: capture z = 3.55153 vs serve 3.30045, a gap of 0.2511 ≈
`v_z(15.06)·(1/60)`; the y-velocity gap 0.0374 ≈ one extra `VERT_GRAVITY·dt`. The whole replay
was one physics frame ahead of the witness from tick 1.

## Which candidate causes were REAL

* **Speedup pairing — NOT a cause.** serve's sp1 and sp8 trails are byte-identical (the paired
  `physics_ticks_per_second = 60·N` / `time_scale = N` scaling makes the per-step dt = 1/60 for
  any N), and capture matches serve at both. capture.py never threaded the speedup, though —
  threaded now for faithfulness (below), not to fix a divergence.
* **Fresh World3D — NOT the cause here (kept for context parity).** capture renders ONE episode
  in a FRESH process, so there is no cross-reset leak for a fresh World3D to prevent (assigning
  one left the trail bit-for-bit unchanged). It is kept only so capture's physics-space context
  matches serve's exactly (and defends a future multi-episode capture). 3D-guarded; 2D untouched.
* **Dresser — NOT a cause.** dressed and undressed capture trails are byte-identical (the
  zero-contact overlay never mutates the game tree).
* **Settle-frame placement — THE cause.** capture settled t=0 with `await physics_frame` AFTER
  `build()`, stepping ONE physics tick over the just-built RigidBodies. serve steps **ZERO**
  physics between build and the first act: `_rebuild` settles with `await process_frame` **with
  no live game in the tree** (it flushes the prior episode's free), and its wire read then
  **busy-waits with the world FROZEN** (no `await`, so the SceneTree does not advance) until the
  `act` op — `act`'s K=6 `await physics_frame` burst is the ONLY physics that ever runs. capture's
  post-build settle put every replay a frame ahead.

## Fix (host-level, `capture_host.gd::_run`)

Two pins, both mirroring `serve_game.gd::_rebuild`:

1. **Settle with an IDLE frame BEFORE `add_child`/`build`** (`await process_frame` when no
   dynamic body exists yet) and **no `await` between `build()` and the t=0 fingerprint/grab** —
   so ZERO physics steps run before the first act, and the t=0 frame is the raw post-build state,
   exactly as serve reports it. (A `process_frame` AFTER build was *worse* — under the capture
   host's `--fixed-fps 60` it forces a physics step that integrates the live body, moving t=0.)
2. **Fresh `World3D` for a 3D game** (`if inst is Node3D: root.world_3d = World3D.new()` before
   `add_child`) — same physics-space context as serve. 2D (`root.world_2d`) untouched.

`harness/verify/capture.py` + `scripts/capture_demo.sh` now thread `HARNESS_GODOT_SPEEDUP`
(via `speedup_user_args`, so N==1 stays byte-identical to the old argv) so the capture replays
at the certification speedup.

## Result — bit-exact, both speedups

Certified witness replayed through capture vs serve, per-tick trail diff, post-fix:

```
capture sp1 undressed  -> SUCCESS@239   IDENTICAL (240/240 ticks)
capture sp8 undressed  -> SUCCESS@239   IDENTICAL
capture sp1 DRESSED    -> SUCCESS@239   IDENTICAL
capture sp8 DRESSED    -> SUCCESS@239   IDENTICAL
```

## Rendering the demo — a SEPARATE, camera-framing gotcha (NOT a determinism bug)

With physics fixed, the real capture reached SUCCESS@239 but the GIF had only **2 distinct
frames** (240 PNGs written, frames 1..239 byte-identical → PIL `optimize` collapses them). This
is NOT a stepping/parity bug — it is the **default fit-to-scene camera**. Evidence (per-PNG md5
hashes, real X11 render on a compute node):

| render config              | distinct PNGs / 240 |
|----------------------------|---------------------|
| speedup 1, no `--follow`   | **2**               |
| speedup 8, no `--follow`   | **2** (identical to sp1 → not speedup, not a flush-starvation) |
| speedup 1, **`--follow`**  | **240**             |

The fit-to-scene overview is fixed at t=0; this game is a craft that flies far DOWN a canyon and
leaves the initial frame, so a static camera sees an unchanging scene. `--follow` parents the
Camera3D to the controlled proxy (a render-only, zero-physics change in the dresser — the state
trail is unaffected, proven by the dressed==undressed pin), tracks the craft, and yields a
full-length animation. The 240-distinct follow result ALSO proves the render updates per frame
through the fresh World3D (the swap does not freeze the render). **The certified demo is captured
with `--follow`.** (Speedup is left at 1 for the render: the trail is speedup-invariant, so sp1 is
byte-faithful to the sp8 certification, and it is the render config proven end-to-end.)

## Regression tests (`tests/test_gd_lane.py`, section 2c)

* `test_capture_replay_matches_serve_tumble_3d` (sp {1, 8}) — the force-driven contact fixture
  (free-falls from tick 1, so it exposes the one-frame lead); re-fails if the settle regresses.
* `test_capture_replay_matches_serve_mini_collect_3d` (sp {1, 8}) — the broad 3D parity guard.
* `test_capture_replay_matches_serve_2d_untouched` (`mini_collect`) — proves the pins keep 2D
  byte-identical through capture vs serve.
* `test_capture_dressed_equals_undressed_tumble_3d` — the dresser is inert on the state trail.

Helpers `_serve_trail` / `_capture_trail` compare the two hosts' per-tick `%.17f` `state()`
trails; the capture host runs HEADLESS in `--fingerprint --no-frames` mode (no display/GL), so
the parity invariant is CI-checkable without the render path.

## Files

* `godotworld/capture_host.gd` — `_run` (the settle-before-build + fresh-World3D pins).
* `harness/verify/capture.py`, `scripts/capture_demo.sh` — thread `HARNESS_GODOT_SPEEDUP`.
* `tests/test_gd_lane.py` — `_serve_trail`/`_capture_trail` helpers + four capture-parity pins.
