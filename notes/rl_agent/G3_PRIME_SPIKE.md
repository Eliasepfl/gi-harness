# G3' (G3-prime) RL-learnability spike — Phase 0 report

> Author: RL spike agent, 2026-07-14 (worktree). Executes Phase 0 of
> `notes/rl_agent/LLM_RL_SYSTEMS.md`: prove that **"solvable = learnable by a small
> RL policy"** is *measurable* on our real games, on CPU, tonight. It is. All code,
> tests, and the calibration below live in this worktree (committed, not pushed).
> The showcase games read from the MAIN checkout (`scenes/games/v23_showcase/`,
> absolute paths) since the worktree lacks them.

---

## 1. What landed

| File | Role |
|---|---|
| `nodeworld/runner.js` (**+ additive `serve` mode**) | interactive per-decision-tick stepping; the `episodes`/`check` batch modes are untouched (byte-identical) |
| `harness/rl/env.py` | `PlanckEnv` — a Gymnasium-style `Env` over one serve subprocess; obs = code-state float vector; reward = checkpoint-latch deltas + terminal bonus; mirrors godot_rl's AIController (§4) |
| `harness/rl/ppo.py` | vendored single-file CleanRL-style PPO (torch, **CPU-forced**): 2×64 MLP, categorical head, GAE, clipped surrogate, **smoothed plateau early-stop** |
| `harness/rl/certify.py` | `g3_prime(game_path, budget_steps=2_000_000)` — train → greedy+stochastic eval → RL witness → **assert the witness replays to success via `JsExecutor.run_batch`** (the certificate bridge) |
| `tests/test_rl_env.py` | 14 tests: pure obs-layout units, serve protocol (determinism/latch/terminal/parity), PlanckEnv reward, and a ≤30 s smoke train |

**Toolchain (pinned).** `torch 2.7.0+cu118` was **already installed**; the spike
forces `device="cpu"` everywhere (no reinstall, no GPU assumptions). `numpy 1.26.4`,
`node v22.14.0`, `planck 1.5.0`. `gymnasium` is **not** required — the env exposes
gym-compatible `reset/step/observation_space/action_space` with lightweight
`Box`/`Discrete` duck-types, and PPO drives a small threaded subprocess vector loop
directly (the design note's "SyncVectorEnv or the batched executor" — we chose the
former, threaded).

---

## 2. The stepping problem → serve mode (protocol summary)

Our showcase games are **JS**, and `run_batch` is episode-batched, but PPO needs
per-step interaction. Solution: an **additive `serve` mode** in `runner.js` that
keeps ONE game+world alive across line-delimited JSON ops on stdin. One `act` =
exactly one decision tick with **identical semantics to `run_episode`** (act +
K=6 [`step`;`on_step`] + latch + failure + success).

```
init (first line):  {"mode":"serve","source":<game JS>}
     -> {"ready":true,"actions":[...],"world_size":[w,h],"title":...}
op:  {"op":"reset","seed":int}
     -> {"obs_state":{name:<full query dict>,...},"world_size":[w,h],
         "latched":{name:null,...},"result":null,"tick":0,"error":null}
op:  {"op":"act","action":str|null}     # ONE decision tick
     -> same shape; "latched":{name:tick|null}; "result":null|success|failure|error
op:  {"op":"close"} -> exit 0
```

Guarantees (all tested): **deterministic** (identical `(seed, actions)` → byte-
identical replies), latch ticks recorded once and never regress, terminal `result`
latches and further `act`s echo the terminal frame (no re-stepping), and **parity
with the batch `episodes` runner** — same final body positions + same latch map.
That parity is the whole point: **a witness recorded in serve mode replays bit-for-
bit through `JsExecutor.run_batch`**, so RL slots into the determinism-first harness
with zero change to the replay/witness machinery.

---

## 3. Calibration table — three showcase games

Budget **1.2 M env-steps/game**, PPO 2×64, num_envs 8, plateau patience 40
(smoothed over 10 updates), `learnable` iff **stochastic greedy-policy success rate
≥ 0.50** over 32 fixed-seed eval rollouts. Every run's witness was replayed through
`JsExecutor.run_batch` and **asserted to reach `success`** (bridge = OK).

| Game | actions / bodies / obs-dim | tree witness (ticks) | RL 1st success (steps) | trained steps (stopped early) | **greedy** succ | **stochastic** succ | RL witness (ticks) | bridge | verdict |
|---|---|---|---|---|---|---|---|---|---|
| **gem_cavern** | 3 / 14 / 146 | 107 | **1 832** | 86 016 | 0.00 | **0.656** | 53 | OK | **LEARNABLE** |
| **two_switch_vault** | 4 / 14 / 146 | 102 | 1 136 | 112 640 | 0.00 | 0.188 | 66 | OK | not learnable @1.2M |
| **meteor_gauntlet** | 3 / 12 / 126 | 98 | 840 | 318 464 | **1.00** | **0.625** | 99 | OK | **LEARNABLE** |

Wall-clock (CPU, this machine): gem 44.5 s, two_switch 47.5 s, meteor 135.7 s
(each stopped early on the smoothed plateau, well before 1.2 M). Throughput held at
**~2 500 env-steps/s** (num_envs 8, threaded serve subprocesses).

**Reading the table.** *All three games reached `success` at least once during
training* (first success at 840–1 832 steps — the checkpoint-latch reward finds the
goal fast), and *all three emitted a witness that bridged cleanly*. **Two of three
(gem_cavern, meteor_gauntlet) climb a 2×64 PPO to a majority success rate** —
`meteor_gauntlet`'s greedy policy even solves its own reactive dodge deterministically
(greedy 1.00). The lone hold-out, **`two_switch_vault`** (strict **ordered** two-switch
gating + pixel-precise jumps over two spike pits), sits squarely in the **"hard but
learnable?"** band the design note is about: solvable by search (the tree finds a
102-tick witness), latched-into by PPO exploration (first success at 1 136 steps,
stochastic rate climbing to 0.19), but NOT yet crackable to ≥50 % by a tiny feed-
forward policy under this budget. **That negative is a valid, useful datapoint** — it
is exactly the game whose difficulty a pure existence-prober cannot grade, and whose
learnability grade + per-update `checkpoints_curve` localize *where* the difficulty
lives (it stalls between `switch_a`→`cleared_gap1` and the second gate).

### 3.1 The determinism degeneracy (important measurement finding)

The showcase games use **no `world.rng`** → they are *fully deterministic*. So all 32
**greedy** eval episodes are the SAME trajectory, and the greedy success rate is
**binary — 0.00 or 1.00** (gem_cavern's argmax had not sharpened onto a winning path
at its plateau → 0.00; meteor_gauntlet's had → 1.00). The **graded** learnability
signal therefore comes from **stochastic (sampled) rollouts** over the fixed seeds;
the RL **witness** prefers a greedy winner but falls back to the shortest *sampled*
winner — either way a concrete `(seed, actions)` pair that **replays deterministically**
through the batch executor (the bridge does not care how the actions were chosen; gem
& two_switch emitted sampled witnesses, meteor a greedy one). This is a genuine
amendment to the design note's "final success rate over 32 greedy eval episodes": on
deterministic envs that greedy metric is degenerate; **report the stochastic rate as
the learnability grade** and keep greedy as the witness's preferred, determinism-first
form.

---

## 4. obs / action ↔ godot_rl_agents AIController mapping (pin this)

Per `GODOT_RL_MERGE.md §2` (Elias's constraint): the wrapper's obs/action surface
**mirrors godot_rl_agents' `AIController`** so that when the Godot lane lands,
`godot_rl_agents` can replace the Node shell with **zero retraining-code changes**.

| godot_rl_agents `AIController` | `PlanckEnv` equivalent |
|---|---|
| `get_obs() -> {"obs":[float,...]}` | `reset()`/`step()` return the flat float32 vector |
| `get_action_space() -> {"act":{"size":n,"action_type":"discrete"}}` | `action_space = Discrete(n)`, `n = len(ACTIONS)`; head index `i` → `ACTIONS[i]` (the game's own string) |
| `set_action(action)` | `step(i)` sends `{"op":"act","action":ACTIONS[i]}` (one serve op) |
| `action_repeat == 6` | one serve `act` = act + **K=6** physics steps (== our K) |
| `get_reward() -> float` | reward computed env-side (godot_rl has **no** runner latch → the latch bookkeeping lives in the controller; here in `PlanckEnv.step`) |
| `done` / `needs_reset` | `terminated` (success/failure) / `truncated` (horizon) |
| `reset()` | `reset()` sends `{"op":"reset","seed":...}` |

**Observation layout** (frozen at the first reset; per body sorted by name,
**controlled body first**, padded to the game's body count):

```
per body (10 floats): [present, x/W, y/H, vx/VS, vy/VS, sin θ, cos θ,
                        is_static, is_sensor, is_controlled]
appended once:        [latched-checkpoint one-hot (declared order)] + [tick/HORIZON]
```

`present` is 1.0 while the body exists, 0.0 once a game removes it (gems, gates) or
for pad slots — a Markov-preserving encoding of disappearance (the raw serve frame
omits removed bodies). The latched one-hot is the **stateful progress signal** that
makes gated multi-stage games (a latched switch opens a door) observable to a feed-
forward policy. Positions ÷ world size, velocities ÷ VEL_SCALE (1000), all clipped to
±10. (Deliberately **code-state, not pixels** — the challenge's "code-defined truth";
their vision policy is theirs and unavailable. Cf. LLM_RL_SYSTEMS §5.) When the Godot
lane swaps in, `get_obs` returns this same vector and nothing in `ppo.py`/`certify.py`
changes — the outer TCP `Sync` rung (GODOT_RL_MERGE §3, Phase 4) is a shell change.

---

## 5. Honest verdict on the LLM_RL_SYSTEMS.md budget assumptions

- **"~2 M env-steps ≈ 10–20 min CPU" — HOLDS numerically, for a different reason.**
  Measured throughput is **~2 500 env-steps/s** → 2 M ≈ **13 min**, inside the band.
  BUT the note assumed *batched-executor sim throughput* (~0.3–0.5 M steps/min pure
  sim = 5–8 k/s). Interactive serve mode is **IPC-bound**: one stdin/stdout JSON
  round-trip per decision tick dominates, not the physics. 8 threaded serve
  subprocesses recover most of the loss (threads overlap Node compute) and land at
  2.5 k/s. The estimate is right; the bottleneck named in the note is not.
- **"PPO on CPU is comfortable at our tiny scale" — YES.** 2×64 MLP over 126–146-dim
  obs / 3–4 actions trains at 2.5 k steps/s; gem_cavern is *solved-to-majority* in
  **~35 s / 86 k steps**, far under budget. The kill-the-idea-early bar ("can PPO
  clear even easy certified games on CPU in ~15 min?") is **cleared with margin**.
- **"Declare UNSOLVABLE-BY-RL and move on, never hang" — implemented, load-bearing,
  and the plateau metric materially changes the verdict.** The early-stop halts all
  three games well before 1.2 M (86 k–318 k). Lesson from run 1: plateau on the
  *instantaneous* per-update mean return is trigger-happy on noisy returns — it cut
  meteor at **63 k steps** and mislabelled it **not-learnable (0.12)**. The shipped
  version plateaus on a **10-update smoothed** return; with it meteor ran to **318 k**
  and flipped to **LEARNABLE (greedy 1.00 / stochastic 0.62)**, and two_switch rose
  0.06 → 0.19. **Takeaway: a premature stop is a false UNSOLVABLE-BY-RL** — treat
  `patience`/`plateau_window` as `[eng.]` knobs the ledger arbitrates, and prefer a
  budget-exhaustion or genuinely-flat-curve negative over a plateau-cut one.
- **Checkpoint-latch reward is the right dense signal.** First success at 840–1 832
  steps on every game shows the `+1 per newly-latched checkpoint` shaping guides PPO
  to the goal quickly; `success` stays the unshaped binary certificate (never read for
  the "solved?" decision), so the shaping can only *accelerate* learning, never pass a
  false game (LLM_RL_SYSTEMS §6 risk 1).
- **Certificate bridge is robust.** Every emitted witness — including from the two
  *not-learnable* games — replayed to `success` through `JsExecutor.run_batch`. The
  determinism-first pipeline absorbs RL with zero witness-machinery change (§6 risk 2).

---

## 6. Follow-ups

- **Phase 1 (integrate as G3').** Gate `g3_prime` on a G3 (tree) witness — never spend
  PPO on an unsolvable game; optionally warm-start PPO from the tree witness prefix
  (behaviour-clone). Append `learnable`, difficulty metrics (steps-to-solve, AUC,
  final rate, plateau milestone, PLR regret proxy = mean positive value loss — the
  value head already computes it) and the RL witness to `runs/ledger.jsonl`.
- **Get `two_switch_vault` over the line (the lone hold-out) before calling it
  unlearnable for real.** meteor already flipped learnable once given room, so this is
  plausibly budget/capacity, not impossibility. Cheap levers, in order: (a) bigger net
  / full budget (2×256, 2 M as the note's default); (b) entropy-coef anneal so greedy
  sharpens onto the sampled winner; (c) reward the *distance-to-next-milestone* between
  latches (denser guidance for the ordered gating stages, where it stalls). Re-grade
  after (a).
- **Throughput.** If 2 M/game × many games gets heavy: multiplex several envs inside
  ONE node process (amortise IPC), or bind an on-policy batched-executor rollout. The
  executor path stays socket-free — the cleanest fit for the cluster.
- **Cluster (Slurm array).** Per-game PPO is embarrassingly parallel and socket-free
  → one game per array task. Recipe + Apptainer/glibc caveats:
  `notes/compute/ORCD_DEPLOYMENT.md` (and GODOT_RL_MERGE §5 for the headless-Godot
  variant). Pin torch-CPU + the runner's `node_modules` (planck 1.5.0) into the image.
- **Godot lane swap.** The mapping table (§4) is the contract: land `GodotExecutor`
  batch-verify + a `runner.gd` `serve`-equivalent, then reuse `PlanckEnv`'s obs/action
  conventions verbatim. `godot_rl_agents`' `AIController`+`Sync` is the OUTER rung
  (GODOT_RL_MERGE Phase 4) — serving *certified* games to external trainers — not the
  inner certifier.

---

## 7. Reproduce

```bash
# serve-mode + obs + smoke-train tests (needs node + planck in nodeworld/node_modules)
python -m pytest tests/test_rl_env.py -q

# certify one game (reduced budget for a quick look; default is 2_000_000)
python - <<'PY'
from harness.rl.certify import g3_prime
r = g3_prime(r"c:/Users/Elias/OneDrive/Bureau/Projet GI/scenes/games/v23_showcase/gem_cavern/game.js",
             budget_steps=200_000, log=print)
print(r["learnable"], r["stochastic_success_rate"], r["rl_witness"]["ticks"], r["bridge_ok"])
PY
```
