# SB3_OFF_THE_SHELF — what stable-baselines3 gives us for free, and what to actually adopt

Date: 2026-07-16 · Author: research agent (READ-ONLY: no repo code changed, no commits; in-image checks only)
Question (Elias): *"because we use the godot rl agents framework, research what StableBaselines3 supports and see if
we could use it to test better agents on these games — so we don't have to code anything ourselves and the end
product stays clearer."* Clarity criterion: challenge judges value a self-explanatory harness → **off-the-shelf > custom**.

## TL;DR
Our `harness/rl/sb3_trainer.py` already drives SB3 directly (PPO default / A2C / DQN off a registry, `MlpPolicy` 2×64,
our own `GodotBatchVecEnv`, a plateau early-stop callback). That direct approach is **already the clean one** — it beats
adopting the godot_rl_agents SB3 wrapper. The only *evidence-based* new algorithm is **RecurrentPPO** for the upcoming
raycast/FPS partial-obs games, and even that should wait behind a cheaper zero-rebuild move (`VecFrameStack`). Everything
else is either inapplicable to our Discrete/vector games or violates "zero custom code". Per `ARCH_3D_ANALYSIS.md`, gains
are **obs-side, not algo-side** — do not oversell algorithms.

## IMAGE REALITY CHECK (verified in `gi-certifier.sif`, 2026-07-16)
| pkg | version | note |
|---|---|---|
| stable_baselines3 | **2.9.0** | task brief said 2.4.0 — image is newer; all core algos + wrappers below present |
| sb3_contrib | **MISSING** | RecurrentPPO/QRDQN/MaskablePPO/TRPO/ARS all need an **image rebuild** |
| gymnasium | 1.3.0 | our `wrap_gym` already yields a Box vector obs — no gymnasium extras needed |
| torch | 2.13.0+cpu | `torch.onnx.export` present (discrete-PPO ONNX would work standalone) |
| onnx / onnxruntime | **MISSING** | gdrl's `verify_onnx_export` can't run in-image (differential check) |
| godot_rl | **MISSING** | gdrl's SB3 wrapper is **not installed in the image** — only in the clone. Our direct-SB3 path is what actually ships. |

Base SB3 algos in image: `A2C, DDPG, DQN, HER, PPO, SAC, TD3`.
Vec wrappers in image: `DummyVecEnv, SubprocVecEnv, VecMonitor, VecNormalize, VecFrameStack, VecTransposeImage,
VecExtractDictObs, VecCheckNan, VecVideoRecorder`. Utils: `evaluate_policy`, `EvalCallback`, `CheckpointCallback`,
schedules (`get_linear_fn`, `LinearSchedule`, …). All zero-rebuild.

---

## 1. INVENTORY — usable for our Discrete-action, vector-obs (soon +raycasts) games with ZERO custom code

### Core SB3 — IN IMAGE TODAY (no rebuild)
| Item | Discrete? | Zero-code fit | Verdict for us |
|---|---|---|---|
| **PPO** | ✔ | our default; on-policy, robust | **KEEP** (bench-proven default) |
| **A2C** | ✔ | already registered | keep as registry option |
| **DQN** | ✔ (only) | already registered w/ small-budget knobs | keep |
| **HER** (HerReplayBuffer) | ✔ | **needs a GoalEnv**: Dict obs w/ `achieved_goal`/`desired_goal` + `compute_reward()`. Our envs are **not** goal-conditioned. | **SKIP** (not zero-code; would need a GoalEnv rewrite) |
| SAC / TD3 / DDPG | ✗ Box-only | continuous actions | **N/A** — our spaces are `Discrete` |
| **VecMonitor / Monitor** | — | records ep return/len + our `info_keywords` | already used |
| **VecNormalize** | — | running obs/reward normalization | low priority: obs already hand-normalized+clipped (`ARCH §2`); could steady value-fn scale only. Must persist running stats w/ the model → **>3 lines**, config-gate if ever needed |
| **VecFrameStack** | — | stacks N frames → cheap short-horizon memory | **useful for raycast games** (see §3); ~3-line venv wrap; skip on the Markov 2D/3D obs |
| VecTransposeImage / VecExtractDictObs / AtariWrapper | — | image / Dict-obs helpers | **N/A** — we use flat Box + MlpPolicy |
| SubprocVecEnv | — | process-parallel envs | **SKIP** — forking around the Godot serve socket breaks it (documented in `sb3_trainer.py:311`); `GodotBatchVecEnv` is our fix |
| **Schedules** (`get_linear_fn`, LinearSchedule…) | — | any hp can take a `progress_remaining→val` callable | already used for LR anneal; free for `clip_range`/`ent_coef` too |
| **policy_kwargs** (`net_arch`, `activation_fn`, `ortho_init`) | — | width/depth/init as kwargs | already used; `hidden=256` is a free screen knob |
| **evaluate_policy / EvalCallback / StopTrainingOnNoModelImprovement** | — | canned eval + early-stop | our `_CurveCallback` is justified (rebuilds the vendored witness curves); canned EvalCallback doesn't emit our curve dicts → keep ours |

### sb3-contrib — NEEDS IMAGE REBUILD (MISSING today)
Confirmed contrib set: `ARS, CrossQ, MaskablePPO, RecurrentPPO, QRDQN, TQC, TRPO`.
| Algo | Discrete? | What it buys us | Verdict |
|---|---|---|---|
| **RecurrentPPO** (LSTM) | ✔ | memory for genuine partial observability (instantaneous raycasts, off-screen FPS opponent) | **the one worth the rebuild** — but only once a game *proves* it needs memory (`ARCH §2`), and after trying `VecFrameStack` first |
| **QRDQN** (distributional DQN) | ✔ (only) | often > vanilla DQN on discrete control; drop-in | **free registry entry** if we rebuild — same predict surface as DQN |
| **MaskablePPO** | ✔ | masks contextually-invalid actions | **SKIP** — (a) needs the env to expose `action_masks()`; our Godot games emit none → not zero-code; (b) it would **suppress the dead-action signal the certifier exists to detect** (G1 dead-action). Philosophical mismatch for the probe |
| TRPO | ✔ | trust-region on-policy | low value — rarely beats tuned PPO on RL Zoo; include only if already rebuilding |
| ARS | ✔ | evolutionary random search, shallow/linear MLP | niche, wants many envs → **skip** |
| TQC / CrossQ | ✗ Box-only | continuous SOTA | **N/A** — Discrete games |

---

## 2. GODOT_RL_AGENTS SB3 WRAPPER vs OUR DIRECT SB3
Their integration (`godot_rl/wrappers/stable_baselines_wrapper.py` + `examples/stable_baselines3_example.py`):
- **PPO only**, `MultiInputPolicy` (Dict obs), `VecMonitor`, `n_parallel` = *multiple exported-game processes each on its
  own TCP port*, linear-LR schedule, `CheckpointCallback`, resume/inference modes, **ONNX export**
  (`export_model_as_onnx`, PPO+SAC).

What it has that ours doesn't, and whether to adopt:
- **ONNX export (in-engine inference)** — the only genuinely new idea. **Don't adopt now:** (i) `onnx`/`onnxruntime` are
  absent in the image (torch-only export works for *discrete* PPO since its verify path is skipped, but); (ii) the
  capabilities audit records that **in-engine ONNX inference is C#/.NET/Mono-only** (`GODOT_RL..CAPABILITIES.md:252`) —
  it won't load in a stock non-Mono Godot; and (iii) our witness is a replayed `(seed, action-string)` list, **not** an
  in-engine net, so an ONNX file buys the certifier nothing. Revisit only if we ship a Mono in-engine-agent product.
- **`n_parallel` (N game processes/ports)** — **worse** than our `GodotBatchVecEnv` (N in-scene instances over ONE
  socket), and it would hit the exact fork/socket breakage our trainer avoids. `CAPABILITIES.md:43` already tags their
  wrapper "**adapt**: one SB3 proc per array task, NOT its n_parallel" — matches the farm-level parallelism we have.
- **`MultiInputPolicy`** assumes Dict obs; our flat Box + `MlpPolicy` is simpler and matches `build_obs_vector`.
- **resume / CheckpointCallback / inference mode** — not needed for short screening probes.

**Honest verdict:** our direct-SB3 trainer is the clearer harness product. Adopt **nothing** structural from the gdrl
wrapper; it's an example script, not a cleaner abstraction than what we already have.

---

## 3. MATCH TO OUR GAME CLASSES (evidence-based first choice)
| Class | First choice (off-the-shelf) | Why / cite | `sb3_trainer` change | Honest benefit |
|---|---|---|---|---|
| **Reactive 2D** (dodge, e.g. `meteor_gauntlet`) | **PPO 2×64 MlpPolicy** (current default) | fully observed, Markov; RL-Zoo uses PPO/DQN defaults for such discrete tasks | none | none from algos; already solved greedily |
| **Steering / vehicle** | **PPO 2×64** | discretized controls; on-policy handles it | none (opt. `hidden=256` free screen) | net not the lever (`ARCH bench`) |
| **3D flight** | **PPO 2×64 after the obs fix** | `ARCH_3D_ANALYSIS`: 256×256 ≡ 2×64; obs is the blocker | **none in trainer** (fix is in `env.py` obs) | win is obs-side, not algo |
| **Upcoming raycast partial-obs** | **VecFrameStack + PPO first; RecurrentPPO only if it stalls** | rays can be instantaneous → short history recovers motion; frame-stack is zero-rebuild, LSTM is not | frame-stack: ~3-line venv wrap (config flag). RecurrentPPO: 1 registry entry **+ LSTM state in `_rollout`** + rebuild | modest; try cheap memory before paying the rebuild |
| **Upcoming FPS-with-opponent** | **RecurrentPPO** (strongest case); PPO+frame-stack as zero-rebuild baseline | off-screen opponent = real partial-obs + longer memory | registry entry after rebuild (state-aware `_rollout`) | real, but self-play/opponent is an **env-design** concern, not an SB3 knob |

Notes on trainer wiring cost:
- **QRDQN / TRPO** = pure one-line `ALGO_METHODS` + `_algo_registry` entries (import from `sb3_contrib`) — identical
  `predict()` surface, so `_rollout`/witness untouched.
- **RecurrentPPO** is the *only* algo that touches `_rollout` — it carries LSTM hidden state across `predict()` calls
  (`episode_start` + `state`), so the witness rollout needs a few extra lines. Still off-the-shelf, not custom RL.
- **VecFrameStack / VecNormalize** = wrap `venv` inside `train()` behind an env flag; frame-stack ~3 lines,
  VecNormalize more (must save/load running stats with the model).

---

## 4. RECOMMENDATION

**ADOPT NOW — config-only, zero rebuild (sb3 2.9.0 is already in the image):**
1. Keep **PPO 2×64 MlpPolicy** as default (bench-proven; `ARCH §2`).
2. Keep `hidden=256` as a free screen knob (already a kwarg).
3. Add **VecFrameStack behind a flag** for the raycast class — the cheap partial-obs answer to try *before* any LSTM.
4. Keep our **GodotBatchVecEnv + direct SB3**; do **not** adopt gdrl's `n_parallel`/`MultiInputPolicy`.
5. (Optional, low priority) VecNormalize behind a flag only if the value-fn scale ever looks off.

**BEHIND ONE IMAGE REBUILD (sb3-contrib) — pay only when a queued game *proves* it needs memory:**
- **RecurrentPPO** — the real prize for raycast-partial-obs + FPS-opponent (registry entry + LSTM state in `_rollout`).
- **QRDQN** — free registry entry, a stronger discrete off-policy option than DQN.
- (TRPO — include only because you're already rebuilding; low expected value.)

**SKIP (breaks "zero custom code", or inapplicable to Discrete/vector games):**
- **MaskablePPO** — needs env action-masks we don't emit; and would hide the dead-action signal the certifier detects.
- **HER** — needs a GoalEnv (Dict `achieved/desired_goal` + `compute_reward`); our games aren't goal-conditioned.
- **SAC / TD3 / DDPG / TQC / CrossQ** — continuous (Box) only; our action spaces are `Discrete`.
- **ARS** — niche evolutionary; wants many envs.
- **gdrl ONNX export** — onnx/onnxruntime absent + in-engine inference is Mono-only; no benefit to our replay witness.
- **SubprocVecEnv**, frame-stack on fully-observed 2D/3D, gdrl `n_parallel`.

---

## DIGEST (≤25 lines)
- Image truth: **sb3 2.9.0** (not 2.4.0), gymnasium 1.3.0, torch 2.13+cpu. **sb3-contrib, onnx, onnxruntime, godot_rl all MISSING.**
- Our `sb3_trainer.py` already drives SB3 directly (PPO/A2C/DQN, MlpPolicy 2×64, GodotBatchVecEnv, plateau callback). It is the clean product — keep it.
- **Adopt the gdrl wrapper? No.** Its `n_parallel` (N processes) is worse than our in-scene batch env; `MultiInputPolicy` needs Dict obs we don't use; ONNX export is Mono-only in-engine and useless to our `(seed,actions)` replay witness. Nothing structural to take.
- **Zero-rebuild wins (in image today):** PPO default (bench-proven), `hidden=256` free screen knob, **VecFrameStack behind a flag for raycast games** (cheap memory to try before any LSTM), optional VecNormalize. Schedules/policy_kwargs already used.
- **One rebuild buys (sb3-contrib):** **RecurrentPPO** (the real prize for raycast + FPS partial-obs; needs LSTM state in `_rollout`), **QRDQN** (free registry entry, > DQN on discrete), optionally TRPO. Pay the rebuild only once a game *demonstrably* needs memory — per `ARCH_3D_ANALYSIS`, gains are obs-side, algos second-order.
- **Skip:** MaskablePPO (needs env masks we don't emit + would hide the dead-action signal the certifier detects), HER (needs a GoalEnv), SAC/TD3/DDPG/TQC/CrossQ (continuous-only vs our Discrete), ARS (niche), gdrl ONNX export, SubprocVecEnv.
- **Per class:** reactive-2D / steering / 3D-flight → PPO 2×64, **no trainer change** (3D win is the obs fix, not the net). raycast → VecFrameStack+PPO first, RecurrentPPO if it stalls. FPS-opponent → RecurrentPPO (after rebuild); opponent is env-design, not an SB3 knob.
- **Trainer cost:** QRDQN/TRPO = one registry line each (same predict surface). RecurrentPPO = registry line + LSTM state in `_rollout`. VecFrameStack = ~3-line venv wrap. All off-the-shelf; no bespoke RL.
- **Don't oversell:** most learnability comes from the observation vector, not the algorithm.
