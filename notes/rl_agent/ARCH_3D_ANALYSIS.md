# ARCH_3D_ANALYSIS — should the agent's internal architecture change for harder 3D games?

Date: 2026-07-16 · Author: analysis agent (READ-ONLY: no repo code changed; scratch driver + sbatch under `~/orcd/scratch/gi/`)
Question (Elias, FR): *"vu qu'on va essayer des jeux plus durs en 3D, est-ce qu'on devrait changer l'architecture interne de notre agent pour quelque chose de plus performant ?"*

## TL;DR — CHANGE THE OBS, NOT THE NET (yet)
The binding constraint for 3D is **not** the policy network — it is the **observation vector**, which is
**2D-only and literally crashes on a true-3D game**. No architecture change can matter until the agent can
*see* the third axis. Concretely:

1. **Obs is the cheapest + necessary win.** `build_obs_vector` (`harness/rl/env.py`) reads `px, py = q.get("pos")`
   — a 2-element unpack. A true-3D game reports `pos:[x,y,z]`, so the obs builder raises
   `ValueError: too many values to unpack` **before the first learning step**. Fix the obs (add z, vz, real
   3-axis orientation; ideally relative-to-target + next-checkpoint direction hints). ~10-line change, but it
   touches obs sizing + the fingerprint reconstruct — see "Adoption cost".
2. **Architecture: don't change it — confirmed by bench.** On a valid-obs, hard-but-learnable drone, **256×256 was
   statistically identical to 2×64** (steps-to-first-success 8360 vs 8336; greedy success 0.25 = 0.25) — ~16× the
   params bought nothing. Our obs is tiny, low-dim, and (near-)Markov, so the net is not the bottleneck. Wider MLP
   stays a *free hyperparameter* (`hidden=256`, no code change) to keep in the toolbox, but it is not the lever.
   **LSTM is unjustified** (obs is full code-state, not pixels — minimal partial observability) **and unavailable
   in-image** (RecurrentPPO is `sb3-contrib`, gated on an image rebuild — `sb3_trainer.py:54`). Frame-stacking is
   pointless (velocity is already in the obs).
3. **The drone is DESIGN-broken, not RL-hard.** The `0/2088` was the **tree solver** (`treesolve.py` /
   `gameverify.py`), which searches checkpoints off the wire and uses **zero** `build_obs_vector` — dimension-agnostic,
   which is why it ran on a true-3D game at all. Its own verdict string is *"make the first stage easier."* That is
   the **difficulty auto-tuner** (piste E), not a better net.

**Answer to Elias in one line: change the OBS, not the architecture.**
Confidence: **high** on (1) and (3) (deterministic crash + ledger/tree-solver evidence); **high** on (2) now that
the bounded bench shows 256×256 = 2×64 on a valid-obs game (single game, small n — call it high-not-certain).

---

## 1. OBS AUDIT (`harness/rl/env.py::build_obs_vector`)

Layout, frozen at first reset — per body (sorted, controlled first), padded to body count:
```
[present, x/W, y/H, vx/VS, vy/VS, sin(angle), cos(angle), is_static, is_sensor, is_controlled]   # 10 floats/body
... then appended once: latched-checkpoint one-hot (declared order) + normalized tick (min(1, tick/horizon))
```
Normalization: pos by world_size `(W,H)`, vel by `VEL_SCALE=1000`, clip `[-10,10]`. `PER_BODY=10`.

| Property | Finding | 3D verdict |
|---|---|---|
| **z position** | absent — only `px,py`. `world_size` is a 2-tuple; no depth scale. | **MISSING** |
| **z velocity** | absent — only `vx,vy`. | **MISSING** |
| **orientation** | a single scalar 2D `angle` → `sin/cos`. No roll/pitch/yaw, no quaternion. | **INSUFFICIENT for 3D** |
| **frame of reference** | **absolute** (world-normalized), not egocentric. | recoverable but not free |
| **relative-to-target** | none. Target bodies (rings/beacons) appear as *other bodies'* absolute pos → the net must subtract. | no hint |
| **checkpoint direction/distance hint** | none — latched is a single bit; no "where is next cp". | no hint |
| **hidden game state** (fuel, lifetime, wind phase, crash flag) | NOT in obs; only `tick` as a clock proxy. | mild partial-obs |

**The crash is real and deterministic** (verified in-process, pure Python — no subprocess):
- `px, py = [1.0, 2.0, 3.0]` → `ValueError: too many values to unpack (expected 2)`.
- `build_obs_vector({'craft':{'pos':[1,5,30],...}}, ...)` → **CRASH**; the same call with a 2-vector pos → OK (dim 11).
- The Godot serve side already emits full 3D: `_vec_json` is "dimension-agnostic" and true-3D games return
  `"pos":[p.x,p.y,p.z]` (`tumble_3d.gd:128`, `a_3d_game_fly...gd:331`, +16 such games in `scenes/games/`).
  Only the **Python obs consumer** is 2D-locked. `godot_env.py` even documents that bounds use "the FIRST TWO
  position components" — the z-drop is baked in.
- **Both** RL lanes hit this: `GodotServeEnv._observe` and `stale_seek.fingerprint_from_obs` call `build_obs_vector`.
  The tree solver (`treesolve.run_batch`) does not → it is the only lane that has ever "run" a true-3D game.
- **Why `a_3d_game_fly` certified anyway:** RL/`g3_prime` is a **post-cert probe** (called from `harden.py --g3`
  and `g4.py`), *not* the COMPLETED gate (that is `gameverify` G0–G3 tree-solve + witness bridge, which is
  dimension-agnostic). So true-3D games certify fine while the RL lane would crash on them.
- **Concrete near-term hit:** the queued `harden --g3` on the recent 3D certifieds (`RESUME_TONIGHT.md`) will crash
  at this exact line the moment it points G3′ at a true-3D game. The obs fix unblocks that work too.

Two failure modes for 3D, depending on how the generated game reports pose:
- **true-3D `[x,y,z]`** (e.g. `a_3d_game_fly`, `tumble_3d`, `a_3d_drone_course`) → RL/G3' **crashes at probe** (`certify.g3_prime:272`).
- **2D-projected `[x,y]`** (e.g. `pilot_a_drone_through_a_canyon` reports `[position.x, position.y]`) → runs, but the
  agent is **blind on the dropped axis** (for a canyon flythrough the forward/depth axis can literally be the one omitted).

## 2. ARCHITECTURE OPTIONS — cost/benefit for OUR small-obs setting

Current learner (`ppo.py::DEFAULTS`, mirrored in `sb3_trainer.py`): PPO, **separate 2×64 tanh** actor/critic,
ortho-init, CPU, `num_envs=8`, `num_steps=128`, γ0.99, plateau patience 40 / window 10 / min_delta 0.05.

| Option | Cost | Expected benefit here | Verdict |
|---|---|---|---|
| **Wider MLP 256×256** | **zero code** — `hidden=256` kwarg | low-dim Markov obs rarely needs width; may help once obs carries real 3D geometry | **Screen it** (benched below); adopt only if it moves steps-to-first-latch |
| **Separate value/policy net** | already the default (`net_arch dict(pi,vf)`) | n/a | already on |
| **Observation normalization (VecNormalize)** | ~15 lines venv wrapper (not a kwarg) | obs already hand-normalized+clipped; could help **value-fn scale** only. Rewards already O(1–5). | Low priority; redundant with hand-norm |
| **Frame-stacking** | wrapper | **none** — velocity already in obs; obs is Markov for point-mass dynamics | Skip |
| **LSTM / RecurrentPPO** | **image rebuild** (`sb3-contrib`, `sb3_trainer.py:54`) + real cost | partial-obs is *mild* (only fuel/timers hidden; `tick` is a clock). Weak justification. | **Skip** until a game proves true partial-obs |
| **Reward scale vs plateau-patience** | tune | on a game that never latches, return is flat → plateau trips at 40 updates = "UNSOLVABLE-BY-RL, move on". Correct behavior; not a 3D lever. | Leave as-is |

Takeaway: for a fully-observed, low-dim obs the **network is almost never the bottleneck**; representation
(what's in the vector) and task difficulty are. This is textbook for these agents.

## 3. BENCH (bounded, honest) — separate sbatch jobs, `mit_preemptable --requeue`, in-image, num_envs=8, speedup=8

Driver `~/orcd/scratch/gi/rl_probe_arch.py` (wraps `g3_prime`, captures crashes as data); jobs `arch_bench.sbatch`.
Requested arms `tumble_3d` + `a_3d_game_fly` are **true-3D → they crash at the obs probe** (proven deterministically
above), so burning full jobs on them adds nothing; one fast `tumble_3d` job is included purely to capture the
in-image crash artifact. The real architecture comparison runs on the **valid-obs, hard-but-solvable** 2D-projected
drone (`pilot_a_drone_through_a_canyon`, ledger: COMPLETED; tree solver stuck at milestone 2 → headroom).

| Arm | Game | Net | Budget | steps→1st success | greedy succ | stoch succ | cp1 / cp2 / beacon latch | notes |
|---|---|---|---|---|---|---|---|---|
| base | drone (2D-proj) | 2×64 | 120k (118 upd) | **8336** | **0.25** | 0.188 | 0.969 / 0.406 / 0.219 | still_improving; bridge_ok |
| wide | drone (2D-proj) | 256×256 | 120k (118 upd) | **8360** | **0.25** | 0.250 | 0.969 / 0.438 / 0.250 | still_improving; bridge_ok |
| crash3d | tumble_3d (true-3D) | 2×64 | 6k | **CRASH 5.2s** | — | — | — | `ValueError` @ `env.py:113`, in-image |

**Result — width is NOT the lever.** The two valid-obs arms are **statistically identical**: steps-to-first-success
8336 vs 8360 (Δ0.3%), greedy success 0.25 = 0.25, and the cp2/beacon latch-rate deltas (+0.03) sit inside eval
noise (n≈32). ~16× more policy parameters bought **nothing measurable**. Both curves were **still improving** at
the 120k budget cut (not converged) — so headroom here is unlocked by **more budget / better obs / easier design**,
not a bigger net. The arms did **not** flatline (they latch cp1 ~97%, reach beacon ~22–25%), so this 2D-projected
drone is a legitimately **RL-learnable** game, not a broken one.

**crash3d (job 18068681): obs blocker CONFIRMED in-image.** `ValueError: too many values to unpack (expected 2)`
at `harness/rl/env.py:113` (`px, py = q.get("pos", (0.0, 0.0))`) — the true-3D crash reproduces inside
`gi-certifier.sif`, before any training, in 5.2s.

## 4. DRONE: RL-hard or DESIGN-broken?  → **two different drones, two answers**
- **`a_3d_drone_course` (the `0/2088` one) = DESIGN-broken.** The `0/2088` (and `1935/2616 → final_stretch`,
  `2466/3360 → spire_field`, determinism deltas 0.001276/5.9e-5, repeated load errors) are all the **tree solver /
  G3 verifier** (`gameverify.py`), an optimizing checkpoint search — a learned PPO net will not beat a search that
  already explored thousands of episodes and never reached milestone 1. The verifier's own remedy is *"make the first
  stage easier"* and the run also shows **non-determinism** (a G1 gate). **Fix = difficulty auto-tuner (piste E) +
  the 3D determinism pin** (`RESUME_TONIGHT.md`), not an agent-architecture change. It is *also* true-3D, so it would
  crash the RL obs path regardless.
- **`pilot_a_drone_through_a_canyon` (the benched, 2D-projected one) = fine + RL-learnable.** Both arms reach ~25%
  greedy success and latch cp1 at 97% — not flatline. So this game is neither broken nor net-limited; it just needs
  more budget (still improving) and would benefit from the obs/geometry hints in §5.
- **Net verdict on the drone question:** where a 3D game "won't solve," look first at **obs (crash / dropped axis)**
  and **design/difficulty**, not the network. A flatline-at-0 (as the `0/2088` case) is the difficulty tuner's job.

## 5. RECOMMENDATION
1. **CHANGE OBS FIRST (blocker + cheapest win).** Make the obs 3D-aware: add `z`, `vz`, a real 3-axis orientation
   (forward+up vectors or quaternion), and a depth scale in `world_size`. Strongly consider **egocentric +
   next-checkpoint direction/distance** features — the single highest-leverage learnability change for harder
   nav games, independent of dimension.
2. **Keep the 2×64 PPO** as the default — the bench already screened `hidden=256` and it matched 2×64 exactly, so
   there is nothing to adopt. Hold LSTM/VecNormalize/frame-stack unless a specific game demonstrates true
   partial-observability. If a hard game stalls with *good* obs, spend **budget** (curves were still improving), not width.
3. **Route "unsolved-but-not-a-crash" 3D games to the difficulty tuner**, not to a bigger net.

### Adoption cost
- **Obs 3D fix:** `build_obs_vector` layout + `PER_BODY` (10→~14–16), the `obs_dim` sizing in
  `env.py::_freeze_layout` **and** `godot_env.py::_freeze_layout`, the `world_size` depth, and
  `stale_seek.fingerprint_from_obs` (reconstructs a fingerprint from the same vector). Retraining is required but
  cheap (fresh policies; budgets are 100k–2M). Tests: `test_gd_wiggle`, obs-layout unit tests. ~½ day incl. tests.
- **`hidden=256`:** zero code — one kwarg; a screen job. Adopt/revert on the bench number.
- **LSTM:** image rebuild (`sb3-contrib`) — do **not** pay this without a partial-observability game that needs it.

---

## 6. IMPLEMENTATION — dimension-aware OBS shipped (2026-07-16)

The recommendation in §5.1 is implemented: **OBS changed, net untouched.** The
`px, py = q.get("pos")` crash is gone; true-3D games now train. Branch
`worktree-agent-aa45db2b12e51d933`, commits `87d66ba` (obs + unit tests),
`7d056f7` (in-image 3D regression tests).

### 6.1 The exact 3D layout (the frozen vector)
Dimension is detected from the **first frame's `pos` arity** and **PINNED** for the
game's lifetime (a mid-run arity flip raises a clear `AssertionError`, not the old
silent unpack crash). Two layouts, dimension-selected:

**2D — `PER_BODY_2D = 10`, byte-for-byte the legacy vector (regression-pinned):**
```
[present, x/W, y/H, vx/VS, vy/VS, sin(angle), cos(angle), is_static, is_sensor, is_controlled]
```

**3D — `PER_BODY_3D = 14` floats/body:**
```
[present, x/W, y/H, z/D, vx/VS, vy/VS, vz/VS, qx, qy, qz, qw, is_static, is_sensor, is_controlled]
```
then a **fixed 16-float egocentric block** (`EGO_BLOCK_3D`), then the shared tail:
```
per-body block (N·14)
  + [ K=3 × (present, dx/W, dy/H, dz/D) ]   # nearest non-controlled bodies, rel. to controlled
  + [ present, dx/W, dy/H, dz/D ]           # next-UNLATCHED-checkpoint target, rel. to controlled
  + latched-checkpoint one-hot (C)
  + normalized tick (1)
```
So `obs_dim = N·14 + 16 + C + 1` (e.g. a_3d_game_fly: 6 bodies, 5 cp → 6·14+16+5+1 = **106**;
tumble_3d: 13 bodies, 1 cp → **200**). Sizing is one function, `env.obs_dim_for(N, C, dim)`,
shared by all three `_freeze_layout`s.

- **Depth `D`** = `world_size[2]` if the wire ever declares it, else `max(W, H)` — the serve
  handshake currently ships a 2-tuple world box only, so z is normalized isotropically.
- **Orientation = a canonical unit quaternion** (chosen over basis vectors): minimal complete
  rotation (4 floats vs 6), already bounded in [-1,1] so it needs no extent scale, **NaN-safe**
  (any non-finite → identity), and **sign-canonicalized `w≥0`** so one orientation has exactly
  one encoding (kills the q/-q double cover → stable, learnable). Sourced, with **no new wire
  field**: an explicit body `quat` [x,y,z,w] if a game emits one (forward-compatible — picked up
  at zero layout change), else a **yaw-about-Y** quaternion from the scalar `angle` games emit
  today (`q = [0, sin(a/2), 0, cos(a/2)]`, faithful to Godot `rotation.y`/`euler().y`).
- **Egocentric hints — the cheap learnability win, 3D-only** (2D stays byte-identical so trained
  2D models/tests are untouched). The next-checkpoint target is inferred **honestly, from data
  already in the frame**: a non-controlled body whose name is associated (case-insensitive
  substring, either direction) with the first *unlatched* checkpoint key — which on
  **a_3d_game_fly fires for real** (`ring_2` ← `threaded_ring_2` → the glider gets a vector to
  the next ring to thread), else the nearest non-controlled **sensor** body, else a zero (present
  bit off) when nothing is inferable (mini_collect_3d/tumble_3d → the K-nearest block still
  carries the goal directions).

### 6.2 fingerprint_from_obs (stale_seek) compat
Kept working and made faithful: `fingerprint_from_obs(obs, body_order, world_size, dim=2)` now
inverts the pinned layout. The 2D path is unchanged (same `statetree.fingerprint` tuple shape,
so the existing 2D freeze/motion tests hold). The 3D path returns an extended per-body tuple
`(name, x, y, z, vx, vy, vz, qx, qy, qz, qw)` at the same `FP_DECIMALS` — a **strictly more
faithful** freeze test that also catches pure z-motion and rotation the 2D digest drops.
`fp_delta` is arity-generic and these fingerprints are only ever compared to each other, so the
wider tuple is safe. Float32-resolution property preserved (~1e-4 px reconstructed ≪ EFFICACY_EPS
1e-3). `dim` is threaded from the env (`_dim`) through `StaleSeekEnv` and the batched vec wrapper;
stubs default to 2 → the whole stale-seek suite is unchanged. The shared DETECT constants
(`EFFICACY_EPS`, `STUCK_WINDOW`, `STUCK_MOVE_MIN`) are still imported, never re-hardcoded.

### 6.3 Model migration
Old saved SB3 `.zip` policies are **shape-incompatible on 3D games** — but that is a non-event:
those games *crashed at the obs builder before the first step*, so no 3D policy was ever trained.
2D models are byte-compatible (2D vector unchanged). No migration; retrain (budgets 100k–2M, cheap).
SB3 raises a clear shape error on a mismatched `.load` (the load path lives in
`certify.py`/`sb3_trainer.py`, a concurrent agent's files — left untouched).

### 6.4 Verification
- **2D no-regression (byte-identity):** `build_obs_vector(dim=2)` reproduces a frozen,
  independent transcription of the pre-3D builder **byte-for-byte** on a mixed 2D state
  (present + removed body, latched + unlatched cps) — `test_2d_obs_is_byte_for_byte_the_legacy_layout`.
  `tests/test_rl_env.py` + `tests/test_stale_seek.py`: **42 passed** (local, conda godot-rl);
  `tests/test_adversary.py`: **22 passed** — adversary detection untouched (it fingerprints the
  raw serve snapshot, not the obs).
- **Unit coverage added:** exact 3D layout, quaternion (yaw + explicit-field + canonicalization),
  the dimension-pin assert (both directions), NaN-safety, egocentric nearest-K (sort + pad) and
  the checkpoint hint (first-unlatched / name-match / sensor-fallback / none-inferable), plus
  3D-fingerprint depth+rotation+topology detection.
- **In-image (gi-certifier.sif, speedup 8):** a true-3D `tumble_3d` env constructs at **dim=3,
  obs_dim=200**, reset+step all-finite, **no pos-unpack crash** (the exact `env.py:113` failure
  the §3 `crash3d` job hit). Committed regression tests
  `test_gd_serve_env_true_3d_loads_and_steps` + `test_g3_prime_true_3d_trains_without_obs_crash`
  (which assert *training RUNS + a well-formed dict*, NOT an SR threshold — success curves stay
  near 0 for ~25k steps at default patience, so an SR gate would be flaky).
- **Full Godot-gated suite green in-image** (job 18070664, mit_preemptable): `test_gd_rl` +
  `test_stale_seek` + `test_stale_seek_godot` + `test_adversary` + `test_gd_wiggle` +
  `test_rl_env` → **94 passed in 1666s**, exit 0.

### 6.5 First true-3D training result — a_3d_game_fly
g3_prime, num_envs=8, speedup=8, in-image (gi-certifier.sif), mit_preemptable, job 18070646.
**Every arm `ok=True` — the `env.py:113` crash is gone; true-3D games TRAIN.**

| Game | dim | obs_dim | trained steps | first latch @upd | peak mean latch | greedy / stoch SR | eval latch rate | verdict |
|---|---|---|---|---|---|---|---|---|
| **a_3d_game_fly** (true-3D) | 3 | **106** | 98,312 (plateau-stop) | 1 (~2k steps) | **0.5 rings** | 0.0 / 0.0 | all rings 0.0 (greedy) | **TRAINS** — first true-3D training ever; partial (threads rings mid-training), plateaus w/o all-5 → difficulty/budget, not a crash |
| **tumble_3d** (true-3D, the §3 `crash3d` game) | 3 | **200** | 40,960 | 0 | 1.0 | 0.0 / 0.0 | `touched_down` **1.0** | **TRAINS** — the exact ValueError@113 arm now runs; latches the floor-contact cp 100%; un-winnable by design (is_success≡false) |
| **mini_collect** (2D parity) | 2 | **33** | 24,576 | 1 | 2.0 | 0.0 / 0.0 | `got_first` **0.594** | **PARITY** — 2D layout (10/body, no ego block); trains as before, reached success @step 1048, learns goal-1 (the goal-2 reversal is the hard part, unchanged) |

Reading it: **a_3d_game_fly**, which raised `ValueError: too many values to unpack` at
`env.py:113` *before the first learning step* (§3 `crash3d`/the bench's true-3D arms), now
trains a full ~98k-step PPO run over the 106-float 3D obs, threading rings during training
(peak mean 0.5). It plateaus below full success (all 5 rings) at 120k and early-stops — the
correct *"route unsolved-but-not-a-crash to the difficulty tuner / more budget"* behavior of
§5.3, **not** an architecture or obs failure. The greedy eval latch rate is 0 while training
peaked at 0.5 because these games are deterministic (§3: greedy is binary; the graded signal
is stochastic) and full ring-threading is genuinely hard at this budget. **mini_collect's
obs_dim=33** (3·10 + 2 + 1) confirms the 2D path is the untouched legacy layout — 2D parity holds.

### 6.6 Files
- `harness/rl/env.py` — dimension-aware `build_obs_vector` (+ `_build_obs_2d`/`_build_obs_3d`,
  `_orientation_quat`, egocentric helpers, `detect_dim`/`obs_dim_for`/`per_body_width`); PlanckEnv
  `_freeze_layout`/`_observe` pin+pass `_dim`. `PER_BODY` kept as a 2D alias for importers.
- `harness/rl/godot_env.py`, `harness/rl/godot_vec_env.py` — `_freeze_layout` detect+pin `_dim`,
  size via `obs_dim_for`, `_observe`/`_obs_of` pass `dim` (obs plumbing only).
- `harness/rl/stale_seek.py` — `fingerprint_from_obs` 3D inversion + `dim` threading (compat only).
- `tests/test_rl_env.py`, `tests/test_stale_seek.py`, `tests/test_gd_rl.py` — unit + in-image.

---

## 7. EGOCENTRIC RAYCAST OBS — "give the agent what it sees in front of it" (2026-07-16)

Elias-approved follow-on to §5/§6: an **OPT-IN egocentric raycast** obs (the godot_rl_agents
FPS reference sensor — `examples/.../player.tscn` WideRaycastSensor + `ExtendedRaycastSensor.gd`,
which is **rays-only, no camera**), plus a **profile knob** to remove the global-position
"cheat". OFF by default → the obs/wire stay byte-for-byte §6. Branch `agent-a364acf7e4517c294`.

### 7.1 The three obs profiles (one knob, `obs_profile`)
- **`positions`** (DEFAULT) — §6's obs exactly (global per-body block + 3D ego hints). Byte-identical.
- **`positions+rays`** — §6's obs + the raycast tail.
- **`rays`** (the PURE, honest profile Elias wants) — **proprioception ONLY** (the controlled
  body's own velocity + own orientation: 2D `vx,vy,sin,cos`; 3D `vx,vy,vz,quat`) + the raycast tail
  + the cp one-hot + tick. **NO global positions of other bodies, NO K-nearest ego block** — the
  agent knows only how *it* is moving/pointing and sees the world through the rays. Egocentric + honest.

### 7.2 The raycast (a SEMANTIC depth retina, no pixels)
A deterministic fan/grid cast FROM the controlled body IN ITS LOCAL FRAME via
`direct_space_state.intersect_ray` (host-side, in `serve_game.gd`), read-only and at the SAME
state()-sampling instant → twin-rollout byte-identity holds WITH rays.
- **2D** — a planar fan of `n` rays across `fov_deg` (the world IS a plane).
- **3D** — the reference **WIDE grid**, `n_h × n_v` (default **25×5**) across `fov_h × fov_v`,
  centered on forward (azimuth about local +Y, pitch about local +X, forward = local −Z). A single
  horizontal fan is vertically blind (obstacles above/below); the grid sees both.
- **Per ray**: a normalized distance (`1.0` = nothing within `range`, else `hit/range ∈ [0,1]`) PLUS,
  when `class_bits` (default on), a **{static, dynamic, sensor} one-hot** from the collider type —
  the reference's class channel (ours is team-free); areas are hit (`collide_with_areas=true`) so
  goal/sensor pads read the sensor class. So each ray is `ray_stride` floats (1, or 1+3=4). `range`
  default is the reference `ray_length=80` (world units); the 3D world box is not wired, so per-world
  extent scaling is a **follow-up** — callers override `range` per game.
- **First-frame caveat (verified in-image)**: the reset/init frame's rays read **all-clear** — the
  physics broadphase is populated only after the first step; every stepped frame is faithful (diag
  job 18079661: reset 0/45 hits, step 0 onward 45/45). Negligible for training (one frame/episode).
- **Config**: `rays:{n, fov_deg, n_h, n_v, fov_h, fov_v, range, class_bits}` (all defaulted). A
  **NARROW/fovea** second tier (reference NarrowRaycastSensor 25×25, tighter fov) is a documented
  **follow-up** — same machinery casts a second grid + concatenates; deferred to bound this change.

### 7.3 DETECT / fingerprint compat (all three profiles)
Rays are DERIVED from body positions, so they are **EXCLUDED from the stale-seek softlock
fingerprint** (double-counting motion the pose already carries): `fingerprint_from_obs` reads only
the per-body prefix and stops before the ray tail. The **pure `rays` profile** carries no positions,
so its freeze test **falls back to the raw serve snapshot** (`env.last_snapshot` / the batched env's
per-instance `last_snapshots`) — positions keep the freeze test faithful (a constant-velocity drift
is not "frozen", which a proprioception-only digest would miss). DETECT works on all three.

### 7.4 PARTIAL OBSERVABILITY — rays make games partially observable (memory fix)
Unlike §6's global-position obs (near-Markov), a raycast obs — and ESPECIALLY the **pure `rays`
profile** — is **partially observable**: the agent sees only what its rays currently touch (occlusion,
limited fov, no memory of what it passed). The **first-line fix is `VecFrameStack`** (in-image, SB3;
`notes/rl_agent/SB3_OFF_THE_SHELF.md`) — stack the last k obs so velocity/approach history is visible
to the feed-forward MLP; it is a trainer-venv wrapper, no image rebuild. **RecurrentPPO/LSTM** (true
memory) remains behind the `sb3-contrib` image rebuild (§2, `sb3_trainer.py:54`) — reach for it only
if frame-stacking proves insufficient on a demonstrably memory-hard game. The pure profile is where
frame-stacking matters MOST (it has the least instantaneous state).
- **Reward alignment tie-in**: the reference FPS reward carries an **anti-idle counter**
  (`n_steps_without_positive_reward`) that penalizes dithering — this aligns with the queued
  reward-realignment / time-pressure work; a partially-observed agent that can't see the goal is
  exactly the case where an anti-idle/time term keeps it exploring rather than camping.

### 7.5 Fly-3D three-arm bench — positions vs positions+rays vs pure rays
Same game (`a_3d_game_fly`), same budget **130k**, sb3 PPO, num_envs=8, speedup=8, in-image
(`gi-certifier.sif`), mit_preemptable, job **18081066**. Rays = the reference wide grid **25×5 +
class bits** (`range=40`, the canyon walls are the only colliders). Honest reporting: training RUNS +
latching, NO success thresholds asserted (the three arms just `ok=True` + well-formed dicts).

| Arm | obs_dim | trained steps | peak mean latch | eval ring-1 latch | eval ring 2/3 | first stochastic success | verdict |
|---|---|---|---|---|---|---|---|
| **positions** (baseline, §6.5) | **106** | 86,024 (plateau) | **0.333** | **0.0** | 0.0 / 0.0 | none | trains; barely threads a ring — the §6.5 control |
| **positions+rays** | **606** | 101,384 (plateau) | **1.0** | **0.562** | 0.0 / 0.0 | none | **rays HELP** — 3× the mean latch, threads ring-1 in 56% of eval eps (baseline never) |
| **rays** (PURE, honest) | **513** | 69,640 (plateau) | **1.0** | **0.625** | 0.031 / 0.031 | **step 61,056** | **competitive + honest** — no global positions, yet best ring-1 eval AND recorded a full stochastic success once |

Reading it (honestly): **rays help on this game** — both rays arms lift the peak mean latch from the
baseline's 0.333 to **1.0** and thread ring-1 in eval (0.56 / 0.62) where the position-only baseline
never does (0.0). The **pure `rays` profile is NOT the expected big loser** at this budget — with NO
global positions it matched/edged positions+rays on ring-1 eval and even hit a full success once
(stochastic, step 61k), though it plateau-stopped earlier (69k). No arm solves all-5 rings at 130k →
§5.3's "route unsolved-but-not-a-crash to the difficulty tuner / more budget", not a crash or obs bug.
Greedy SR is 0 everywhere (deterministic greedy is binary; the graded signal is the eval latch rate).

**Caveat (honest):** this glider has `lock_rotation=true` and never turns to face its +Z travel, so its
body-local retina (forward = local −Z) looks backward/sideways — yet the side/floor/ceiling wall rays
still inform the "stay-centered" fail condition, which is why rays help even here. Games whose craft
turn to face travel get travel-aligned rays for free. This is a property of the game's body, not the
sensor (the sensor is faithfully body-local per Elias' "in its local frame").

### 7.6 Files (this change)
- `godotworld/serve_game.gd` — opt-in `rays` parse + `direct_space_state.intersect_ray` fan (2D) /
  25×5 grid (3D) + per-ray {static,dynamic,sensor} class channel; emitted ONLY when opted in
  (frame byte-identical off). No effect on check/init/reset/act wire when off.
- `harness/rl/env.py` — `obs_profile` (3 profiles) + `_build_obs_pure`; `build_obs_vector` `rays`
  tail; `n_rays_of`/`ray_stride`/`rays_obs_width`/`normalize_rays`/`obs_dim_for` (profile+ray-float aware).
- `harness/rl/godot_env.py`, `harness/rl/godot_vec_env.py` — `rays`/`obs_profile` kwargs, tail sizing,
  per-instance `last_snapshots` (pure-profile DETECT).
- `harness/rl/stale_seek.py` — `fingerprint_from_obs` profile-aware (rays excluded; pure → snapshot).
- `harness/rl/certify.py` — `g3_prime` `rays`/`obs_profile` pass-through to the env factories (flag only).
- `tests/test_rl_env.py`, `tests/test_gd_rl.py` — unit (profiles/stride/pure layout) + in-image
  (grid casts + class bits + determinism + off-path width).
