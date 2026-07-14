# Godot RL merge — LLM world-gen (certified parts bank) → RL training, sequentially

> Author: research agent, 2026-07-14. Deep-dive #2 of the Godot double-dive (companion to
> `GODOT_MIGRATION.md` = the GodotExecutor plan, and `notes/rl_agent/LLM_RL_SYSTEMS.md` = the G3'
> RL-learnability design). Question (Elias): the two halves — *LLM generates a Godot world* and
> *RL agent learns to play a Godot world* — are each well-documented, but **nobody has merged
> them**; the merge should be cheap for us because our pipeline is already sequential
> (generate → certify → train). This note verifies godot_rl_agents live, maps it onto our
> contract, designs THE MERGE, and delivers an honest prior-art verdict. Every repo/stat/issue
> below was fetched live 2026-07-14.

## VERDICT: the merge is real, cheap, and (specifically) unclaimed — but train via our EXECUTOR, not their TCP

The two halves genuinely have not been joined *in this specific form* (LLM-generated 2D **physics**
games in Godot, code-certified, then RL-trained). The nearest neighbour, **GG-Bench**, did the
general shape (LLM→games→RL) but for turn-based abstract Gym games, for LLM evaluation — not
physics, not Godot, not solvability certification (§4). Our differentiator holds. The engineering
recommendation: **reuse godot_rl_agents' *idioms and tooling*, but run the inner G3' certifier
through our own `GodotExecutor` (batched JSONL), not their per-step TCP bridge** — same reasoning
`LLM_RL_SYSTEMS.md §4.3` used to keep OpenEnv's HTTP transport out of the hot probe loop.

---

## 1. godot_rl_agents — deep card (verified live 2026-07-14)

| Fact | Value (source) |
|---|---|
| Repo / stars / licence | `edbeeching/godot_rl_agents`, **~1.5k★, MIT** |
| Maintenance NOW | **Active** — last commits June 2026 (PR #257, SB3-contrib `RecurrentPPO`/PPO-LSTM example, by Ivan-267). Multi-contributor, Discord. |
| pip release | `godot-rl` **v0.8.2 (2025-02-25)** on PyPI — **the pip tag lags HEAD by >1 yr**; vendor a pinned commit, don't trust the release tag. Python 3.8–3.10. |
| Paper / headline throughput | arXiv **2112.03636** (AAAI-22 Workshop on RL in Games), Beeching, Debangoye, Simonin, Wolf. **~12k interactions/s** on a high-end laptop, **4 CPU cores**, Jumper env (their number, one env). |
| Trainers (all live) | **StableBaselines3** (Win/Mac/Linux), **Sample Factory** (Mac/Linux **only**, no Windows), **Ray RLlib** (Win/Mac/Linux), **CleanRL** (Win/Mac/Linux). >20 algos via SB3/RLlib. |
| ONNX export | Experimental, via **SB3 / RLlib / CleanRL** (`--onnx_export_path`); Sync node has an "Onnx Inference" mode to run the policy back inside Godot. |
| Godot target | Godot **4.x**; in-editor training path wants the **.NET/mono** build (for the ONNX inference node). Plugin = `edbeeching/godot_rl_agents_plugin`. |

**Architecture (the two nodes).** Verified from `docs/CUSTOM_ENV.md`, the plugin `sync.gd`, and the
examples DeepWiki:

- **`AIController2D`/`AIController3D`** (abstract) = the per-agent RL interface. You implement:
  `get_obs() -> {"obs":[floats]}`, `get_reward() -> float`, `get_action_space() -> {name:{"size":n,
  "action_type":"discrete"|"continuous"}}`, `set_action(action)`, plus manage the `reward`, `done`,
  `needs_reset` member vars and `reset()`. **Discrete AND continuous action heads are first-class.**
- **`Sync`** = one node per scene that owns the **TCP** link to Python (`StreamPeerTCP`, JSON
  messages). `handle_message()` dispatches `"reset" | "action" | "call" | "close"`. Each step the
  tree is **paused**, obs+reward are sent, the action is received, physics is resumed — i.e. **Python
  drives stepping in lockstep** (no free-running wall clock).
- **Speed-up (the determinism-critical detail, read from `sync.gd`):**
  `Engine.physics_ticks_per_second = speedup * 60` **and** `Engine.time_scale = speedup * 1.0`, set
  together (recommended ≤ **8×**). Because both scale together, **per-tick dt stays 1/60** — this is
  exactly Godot's documented recipe for "speed up without losing precision" (raise ticks
  proportionally to time_scale). So speed-up adds *more identical fixed-dt ticks per wall-second*, it
  does **not** enlarge dt. **`action_repeat`** (frame-skip) is a separate `n_action_steps % k`
  counter that holds the last action across k physics frames.
- **Parallelism (two axes, both live):** (i) *in-scene duplication* — many AIControllers/agents in
  one running instance; (ii) *multiprocessing* — several exported env executables via
  `--n_parallel=N`, trajectories gathered over TCP. TCP means **local or distributed** placement.
- **Headless:** export the env as an executable → "accelerated, faster-than-real-time, headless
  training"; on a cluster just **drop `--viz`**. This is the intended production path.

---

## 2. Fit with our contract + the adapter sketch (Q2)

Our games (`CONTRACTS §1-2`): **2–8 game-declared discrete actions**, pure state queries (≤14 bodies
× pos/vel/angle/ω/bbox), **1–6 runner-latched checkpoints** = dense reward, episodes ~100–300
decision ticks at 10 Hz decisions (K=6 physics substeps). Mapping onto godot_rl idioms:

| Our contract | godot_rl_agents idiom |
|---|---|
| `ACTIONS` (2–8 strings) | `get_action_space() = {"act":{"size":N,"action_type":"discrete"}}`; the head's index → our action string |
| decision tick = `act` + K=6 `step` | **`action_repeat = 6`** (Sync frame-skips 6 physics frames per decision) — 1:1 with our K |
| code-state feature vector (`LLM_RL_SYSTEMS §4.1`, ~110 dims) | `get_obs() -> {"obs":[…]}` — our per-body relative (dx,dy,vx,vy,sinθ,cosθ,ω) + checkpoint one-hot + tick |
| checkpoint-latch reward (`+1` per new milestone, `±B` terminal) | `get_reward()` returns the **latch delta** this tick — BUT godot_rl has **no runner-side latch**, so the latch bookkeeping must live in the AIController (or the shared interpreter), not the game predicate |
| `success`/`failure`/budget | `done = success or failure or over-budget`; `needs_reset` on episode end |

**Can ONE game-spec serve BOTH funnels (Elias's question)? YES — one spec, one interpreter, two thin
front-ends.** The frozen `runner.gd` `GodotWorld` interpreter (parts-bank `.tscn` instantiation +
allowlisted predicate DSL + ported constants/RNG, per `GODOT_MIGRATION §2.3/§2.5`) is shared. Only
the **outer shell** differs:

- **Batch-verify + G3' front-end** = read one JSON job → run episodes → **emit JSONL to stdout** →
  `quit()` (the existing `GodotExecutor.run_batch` protocol). No TCP.
- **Live-agent front-end** = wrap the *same* `GodotWorld` in an `AIController` + `Sync` node; `get_obs`
  / `set_action` / `get_reward` delegate straight into the interpreter's query/act/checkpoint layer.

So the answer to "does runner.gd double as the AIController host?" is: **the interpreter does; the I/O
shell is swapped.** This preserves the single-runner discipline (`GODOT_MIGRATION` risk #4: two
runners drift) — the physics/predicate/parts code has exactly one implementation.

**Determinism × speed-up (the interaction Elias flagged).** Because speed-up keeps dt = 1/60 and the
Sync loop is **lockstep** (tree paused between actions — no wall-clock tick-dropping), speed-up is
**determinism-preserving in principle**. Guardrails: (a) **fix one canonical speed-up** for both
witness-record and replay; (b) add a spike gate = *byte-identical final snapshots across
speedup ∈ {1, 8}*; (c) note that on the **executor path the whole question is moot** — WE drive
stepping under `--fixed-fps`, so speed-up never enters the certifier. The determinism risk is confined
to the outer TCP rung, where the emitted certificate isn't a byte-exact witness anyway.

---

## 3. THE MERGE — sequential pipeline + phased plan (Q3, heart of the deliverable)

```
  LLM two-prompt bank-menu gen  ─►  G0–G4 certification  ─►  G3' RL learnability  ─►  [outer] serve certified
  (certified parts → .tscn/spec)     (GodotExecutor,          (vendored CleanRL PPO,     games to external
   Parts used: line, CONTRACTS §9)    batched JSONL)           EXECUTOR path)            trainers via Sync/TCP
        generate                        certify                  train (inner)             train (outer, optional)
```

**Executor path vs TCP path — the recommendation (argued).** Two ways to run the RL training:

- **(A) Executor path — RECOMMENDED for the inner G3' certifier.** Wrap `GodotExecutor.run_batch`
  (or an in-proc Gymnasium `Env` over the same `run_episode`, exactly as `LLM_RL_SYSTEMS §4.3`
  prescribes for the pymunk/Planck lanes) and point **vendored CleanRL PPO** at it. **Pros:** no TCP;
  determinism-first (byte-exact JSONL witness replay we already own); batched throughput matched to a
  thousands-of-episodes probe; **one frozen runner**, zero drift; the plateau/"UNSOLVABLE-BY-RL"
  early-stop logic stays in our editable single-file PPO. **Con:** we forgo godot_rl's trainer
  tooling/ONNX/parallel infra for the inner loop.
- **(B) TCP path — godot_rl_agents.** **Pros:** SB3/CleanRL/SF/RLlib for free, ONNX export, ready
  parallel/distributed harness, community. **Cons:** per-step TCP handshake is the wrong overhead
  profile for a batch probe; a **second runner** (AIController host) reintroduces the drift risk;
  reward moves game-side (`get_reward`) losing the runner-enforced latch guarantee; adds a
  nondeterminism surface (sockets, `time_scale`) to a determinism-first stage.

**Decision (mirrors the OpenEnv split in `LLM_RL_SYSTEMS §4.3`): Executor path for the INNER
certifier; godot_rl_agents' Sync/TCP for the OUTER rung** — serving *already-certified* games to
external trainers, ONNX inference, demos, and (later) the GI-style vision-policy consumer. Their
tooling is worth its transport cost only once, downstream, where witnesses aren't the certificate.

**Phased plan** (composes the two companion notes; each phase gated):

- **Phase 0 — prerequisites (parallel, already scoped).** (i) Godot spike gates pass
  (`GODOT_MIGRATION §8`: boot <2s, 40-ep batch <5s, **byte-identical same-seed snapshots**,
  Rapier2D or stock). (ii) G3' spike on the pymunk lane (`LLM_RL_SYSTEMS Phase 0`: CleanRL PPO clears
  easy certified games on CPU in ~15 min). Neither needs Godot RL yet.
- **Phase 1 — GodotExecutor batch-verify (the "certify" half).** `godotworld/runner.gd` (frozen
  interpreter, JSONL + check modes) + `GodotExecutor` + `_verify_godot` funnel (`GODOT_MIGRATION §8
  PM`). G0–G4 parity with the JS/Py lanes.
- **Phase 2 — LLM generation on the Godot lane (the "generate" half).** Bank-menu two-prompt gen
  (`CONTRACTS §9.6` step-2 prompt wave) emitting the **Godot declarative spec** + `Parts used:` line;
  each bank noun → a **certified `.tscn` template** (`GODOT_MIGRATION §4`). Repair loop reuses the
  gameverify JSON report verbatim.
- **Phase 3 — G3' learnability on the Godot lane (the "train" half, inner).** Reuse the Phase-0 Gym
  wrapper + vendored CleanRL PPO, now over `GodotExecutor`. Emit `learnable ∈ {y,n}`, difficulty
  metrics (steps-to-solve, AUC, final rate, plateau milestone, PLR regret proxy), and the
  **greedy fixed-seed RL witness** (`LLM_RL_SYSTEMS §4.1`). **This is the merge's payload:** a Godot
  physics game the LLM invented, certified, and now graded by whether a policy can *learn* it.
- **Phase 4 — outer rung (optional).** Add the `AIController`+`Sync` front-end on the same
  interpreter → publish certified games as godot_rl_agents envs / ONNX / HF; feed the curriculum
  metrics back to the designer prompt (ACCEL/GenEnv edit-operator loop, `LLM_RL_SYSTEMS §7 Phase 2`).

---

## 4. Prior-art verdict — is Elias right that nobody merged these? (searched hard)

**Verdict: for the specific triple — (LLM-generated 2D *physics* games) × (code/parts-bank
certification) × (godot_rl_agents RL training) — nothing exists. The claim holds.** The literature
splits cleanly into the two unjoined halves plus one engine-agnostic near-neighbour:

- **Nearest neighbour — GG-Bench, "Measuring General Intelligence with Generated Games", arXiv
  2505.07215** (Verma, Huang, Chen, Klein, Tomlin; UC Berkeley; repo `vivek3141/gg-bench`). LLM writes
  NL game descriptions → implements each as a **Gym** env → trains **RL agents by self-play** → scores
  LLMs by win-rate vs those agents. This is the *general shape* of our merge (LLM→games→RL,
  sequential) and its title is strikingly adjacent to "General Intuition" — **cite it honestly**. But
  it is **turn-based abstract console strategy** games, **engine-agnostic Python/Gym (no Godot, no
  physics)**, and its purpose is **LLM evaluation**, not solvability/learnability certification or
  training-env supply. Different substrate, different goal.
- **LLM-generates-Godot, but no RL:** `htdt/godogen` (build-time C#/.NET scene gen, **judged by
  video**, `GODOT_MIGRATION §0`); the "Godot Games" Claude skill (one-way authoring). None feed a
  learner.
- **LLM-generates-env + RL, but not Godot:** **EnvGen** (arXiv 2403.12014, Crafter), **OMNI-EPIC**
  (arXiv 2405.15568, PyBullet) — the canonical "solvable = an RL agent learns it" systems
  (`LLM_RL_SYSTEMS §1`), but neither in a game engine, neither physics-in-Godot.
- **RL-in-Godot, but hand-authored envs:** godot_rl_agents itself; its example zoo is human-built.
- **PCG in Godot / PCGRL** (arXiv 2001.09212 and successors): procedural *level* generation and
  RL-as-generator, but **not LLM-driven and not the generate→certify→train pipeline**.

Repeated targeted searches ("LLM generated Godot levels + RL", "godot_rl_agents + LLM/procedural
level gen") surfaced only the two halves separately — one result literally concluded the field treats
them as *"distinct applications rather than an integrated pipeline."* **The differentiator for the
submission: we are joining the LLM-designer and the godot_rl-player over a *physics* substrate with a
*code certificate* — GG-Bench's shape, but physics + Godot + solvability instead of abstract + Gym +
LLM-eval.**

---

## 5. Cluster angle — headless Godot RL on Slurm CPU (Q4)

- **Container story:** package headless-Godot export template + Python trainer in an **Apptainer**
  (ex-Singularity) image — the HPC-standard, multi-user, root-less runtime; Slurm has first-class
  `--container` (salloc/srun/sbatch, Slurm ≥ 21.08) and Apptainer runs Docker images too. Godot's
  **MIT licence ⇒ license-free horizontal scaling** (no per-seat cost, unlike some engines).
- **Parallelism:** Slurm **array jobs**, each job an independent set of headless env executables;
  our **executor path needs no sockets** (pure subprocess → JSONL), which is the *cleanest* fit on a
  batch scheduler. If instead using godot_rl's TCP path, bind the Sync port **per job** (env var
  offset) to avoid collisions on shared nodes.
- **Throughput anchor:** ~12k interactions/s / 4 cores (arXiv 2112.03636) is the per-node order; our
  games are far smaller than their Jumper env (≤14 bodies, ~110-dim obs), so expect better per-core.
- **Known blockers:** (1) export template must match the cluster's glibc/arch (build the template into
  the container); (2) the pip release lags HEAD >1 yr → **pin a git commit** in the image; (3)
  first-run `--import` cache warm-up must happen at image-build time, not per job (`GODOT_MIGRATION
  §5`); (4) X-less is fine (`--headless` = dummy display) — no Xvfb needed.

---

## 6. Godot 4.7 / 4.8-dev findings (Q5)

- **Current stable is Godot 4.7 (released 2026-06-18)** — this supersedes the "4.6" in
  `GODOT_MIGRATION`. **4.8-dev-1** shipped ~2 weeks later (early July 2026) and is **editor-UX focused**
  (embedded game view default, `FuzzySearch` exposed to GDScript) — **nothing RL/headless/determinism-
  specific** yet. Cadence: minor release every 3–6 months.
- **Determinism:** issue **#112976** (2D physics nondeterministic same-machine, 3+ colliding bodies)
  carries **milestone 4.7**, and the confirmed workaround — **reset in `_physics_process`, not
  `_process`** — is a discipline our frozen runner already follows (fresh deterministic world per
  episode, all mutation in `_physics_process`). Treat 4.7 as likely-fixed but **still spike-verify**;
  **Rapier2D stays the guaranteed fallback** (`GODOT_MIGRATION §2.4`).
- **Jolt** is the **default 3D** physics from 4.6 — **3D only**; our **2D** lane keeps Godot Physics
  2D or **Rapier2D**, unaffected. **Physics interpolation** (2D since 4.3, 3D added recently) is a
  *rendering-smoothness* feature that decouples physics tick from display frame — **irrelevant to
  headless determinism** (no rendering in the loop), and must not be confused with a physics change.
- Net for us: **4.7 is the target**; 4.8-dev offers no RL lever worth waiting for; the determinism
  bet rides on Rapier2D + reset-discipline, not on a 4.8 feature.

---

## 7. Sources (fetched live 2026-07-14)

- godot_rl_agents (repo, ~1.5k★ MIT, active Jun 2026) — https://github.com/edbeeching/godot_rl_agents ; plugin (sync.gd speed-up) — https://github.com/edbeeching/godot_rl_agents_plugin
- Paper (AAAI-22 Workshop, 12k interactions/s) — arXiv 2112.03636 https://arxiv.org/abs/2112.03636
- CUSTOM_ENV (AIController get_obs/get_reward/get_action_space/set_action) — https://github.com/edbeeching/godot_rl_agents/blob/main/docs/CUSTOM_ENV.md ; Sample Factory / RLlib docs — https://github.com/edbeeching/godot_rl_agents/blob/main/docs/ADV_SAMPLE_FACTORY.md , https://github.com/edbeeching/godot_rl_agents/blob/main/docs/ADV_RLLIB.md
- HF Deep-RL course (speed-up ≤8, workflow) — https://huggingface.co/learn/deep-rl-course/en/unitbonus3/godotrl ; PyPI v0.8.2 — https://pypi.org/project/godot-rl/
- GG-Bench (nearest prior art) — arXiv 2505.07215 https://arxiv.org/abs/2505.07215 ; repo https://github.com/vivek3141/gg-bench
- EnvGen — arXiv 2403.12014 ; OMNI-EPIC — arXiv 2405.15568 ; PCGRL — arXiv 2001.09212
- Godot 4.8-dev-1 — https://godotengine.org/article/dev-snapshot-godot-4-8-dev-1/ ; 4.7 release date — https://endoflife.date/godot ; determinism issue #112976 (milestone 4.7) — https://github.com/godotengine/godot/issues/112976
- Slurm containers / Apptainer on HPC — https://slurm.schedmd.com/containers.html
- Local: `notes/engines/GODOT_MIGRATION.md`, `notes/rl_agent/LLM_RL_SYSTEMS.md`, `CONTRACTS.md §1-2/§9`, `harness/verify/executors.py`

> Honesty flags: the **12k interactions/s** figure is godot_rl's own single-env number (arXiv
> 2112.03636), not independently reproduced here. The **pip v0.8.2 (Feb 2025)** tag lags active HEAD
> (Jun 2026 commits) — pin a commit. The speed-up dt-invariance is inferred from `sync.gd`
> (`ticks=60·speedup`, `time_scale=speedup`) + Godot's precision guidance; **make byte-equality across
> speed-up a spike gate**, do not assume it. #112976's exact closing state could not be scraped (GitHub
> render error) — milestone 4.7 is confirmed from metadata; verify the fix landed before relying on
> stock 2D physics.
