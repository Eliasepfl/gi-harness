# DECISION: the GDScript lane (Elias, 2026-07-15 ~midnight)

> "GDScript has to be the thing we use. Easier with the agentic library; we
> keep our verificators (setup stays the same); good guardrails justify more
> room; in a real GI setup a Claude key + an agent loop corrects code fast;
> world feasibility/reliability stays on our setup. The spec approach kills
> variety too much — a minus for the project."

## Architecture: contract-GDScript, verified through the serve seam

- Agent writes ONE .gd game implementing the GameAPI CONTRACT = the serve
  vocabulary (init/reset(seed)/act(action, n_ticks) -> typed state,
  checkpoints, done_term/done_trunc). Any game speaking it is verifiable.
- G0 (new species): parse gate (validate_script pattern) + BANNED-API
  scanner (OS.*, FileAccess writes, network, threads, reflection escapes,
  wall-clock time, unseeded RNG) + contract-compliance probe.
- G1-G4, G3', witnesses, curriculum, ledger, farms: UNCHANGED, driven
  through GodotServeEnv/executor against the contract.
- Determinism: tested per game (G1 two-run drift gate), contract bans the
  nondeterminism sources; not guaranteed-by-construction anymore - accepted.
- SECURITY hard rules: generated code runs ONLY in-container on compute
  nodes; game processes get a SCRUBBED env (no OPENROUTER/ANTHROPIC keys,
  env.py never readable from the game process); scanner is a hard G0 fail.
- Knowledge: gd-agentic quarry (paraphrased) + examples repo feed the
  writing agent's skills; parts bank -> reference snippets.
- The SPEC lane PARKS (not deleted): lane A of the head-to-head + the
  certified-parts substrate. detect_engine grows a 'gdscript' route (.gd).
- Designer brain: deepseek now; backend 'anthropic' already exists in
  gamegen for the Claude-key real setup.
