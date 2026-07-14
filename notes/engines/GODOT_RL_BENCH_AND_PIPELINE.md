# Godot next: examples-as-smoke-bench + the full prompt→verified pipeline

> Implementation plans (Elias, 14 juil. late). Sources already in repo:
> GODOT_RL_EXAMPLES_AUDIT.md (per-env cards + file paths, HEAD d659636),
> GODOT_RL_MERGE.md (godot_rl_agents architecture), G3_PRIME_SPIKE.md,
> GODOT_LANE.md + godotworld/SPEC.md, ORCD_GODOT_RL_PLAN.md.

## A. Import godot_rl_agents_examples as the RL SMOKE-BENCH

Goal: known-good, third-party RL envs to (1) smoke-test our training stack
independently of our generated games, (2) later A/B *many RL methods* on
stable ground (PPO vs SAC vs RLlib vs CleanRL, seeds, budgets).

1. Clone `edbeeching/godot_rl_agents_examples` **pinned at `d659636`** (the
   audited HEAD). Skip mono/.NET envs; start set per the audit: **BallChase,
   SimpleMemoryTest (true 2D), CrossTheRoad, DownFall, JumperHard** (flat-3D,
   idioms transfer; JumperHard = the ordered-gating reference).
2. Per env: open once in the 4.7 editor (project import), then export
   headless: `godot --headless --export-debug "Linux/X11" build/<env>.x86_64`
   (Windows preset for local runs). Export presets per audit §ORCD; assets are
   MIT but do NOT vendor art into our repo — keep the clone external
   (`external/godot_rl_examples/`, gitignored, pin recorded in the note).
3. Wire via **godot_rl_agents pinned to a git commit** (pip tag is a year
   stale — GODOT_RL_MERGE §1): SB3 wrapper + `Sync` TCP, `speedup 8`,
   `n_parallel 4` (audit's defaults). This is the OUTER/TCP rung — correct
   here because these envs ship AIControllers; our executor path stays for
   OUR games.
4. SMOKE = 1 PPO run/env reaching the audit's reported reward within its
   reported step budget (audit lists 50k→50M per env — start with the small
   ones). Record a table (env, method, steps, wall, final reward) in
   `notes/rl_agent/RL_BENCH_RESULTS.md` + ledger events.
5. BENCH (after smoke): grid = env × method (SB3-PPO, SB3-SAC where
   continuous, CleanRL-PPO ours) × 3 seeds, fixed budget. ORCD array per
   ORCD_GODOT_RL_PLAN template (b); exports baked into the `.sif` (audit
   §ORCD: in-scene 16x batching + `--n_parallel`).
6. Risks: env Godot-version drift (audit: 4.1→4.5 per env — test-load each
   under 4.7, drop incompatibles); TCP port-per-job on Slurm (plan §risks);
   mono envs excluded.

## B. Close the FULL prompt→verified-game pipeline on the Godot lane

Today: verify (G0-G3 tree) + render work end-to-end on specs; generation,
G4 confirmation and G3' are not yet wired for engine="godot". The pass table
target: **one deepseek prompt → spec JSON → G0/G1/G2/G3(tree) → G4 hardened →
G3' grade → curriculum directive → revise round — every stage green.**

1. **Prompts**: add `harness/gen/prompts/api_godot.md` = condensed
   godotworld/SPEC.md (body/joint/act/on_step tables + predicate DSL +
   worked mini-example) and teach `prompts.compose("godot")`; `rules.md` /
   `orientation.md` are engine-neutral (reuse); bank menu works as-is (spec
   body NAMES drive sprite skinning exactly like js).
2. **gamegen engine="godot"**: the model returns ONE JSON object (the spec)
   instead of code — extraction = first `{`..last `}` (the openrouter padding
   parser already does this); repair loop unchanged (verify hints are
   engine-agnostic); artifact written as `<slug>.spec.json`; CLI
   `game new --engine godot`. Ledger records engine.
3. **G4**: `attack_game` should already route (detect_engine + actions
   recovered from the G1 efficacy report — the fix from the js lane). Add one
   smoke test attacking a certified example spec; fix surprises.
4. **G3' on godot**: add a `serve` mode to `godotworld/runner.gd` mirroring
   `nodeworld/runner.js` serve ops (`init/reset/act/close`, same JSON shapes —
   protocol documented in G3_PRIME_SPIKE.md §serve). Then either a `GodotEnv`
   sibling of `PlanckEnv` or (better) generalize `harness/rl/env.py` to take
   an executor/serve-command — the obs/action layout is already
   engine-neutral (flat body vector + discrete actions).
5. **Acceptance run** (the deliverable Elias asked for): one live prompt
   through every stage; record the full pass table (stage, verdict, ticks,
   wall) in `notes/engines/GODOT_PIPELINE_PASS.md` + publishable GIF.
6. Test additions: compose("godot") sections, spec extraction round-trip,
   serve-mode determinism (skipif exe absent), G4-on-spec smoke.

## C. godot_rl_agents demos (reference rung)

Their showcase demos (GODOT_RL_MERGE §3: ONNX export → in-editor inference)
are the model for OUR future "trained agent plays the certified game" videos:
train on a certified spec game → export ONNX → record in-engine. Site-worthy;
after A+B.

Order of execution: B1-B2 first (closes the generation loop — highest value),
then A smoke (validates training stack), B4 (G3' on godot), A bench on ORCD,
B5 acceptance, C last.
