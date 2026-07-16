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
