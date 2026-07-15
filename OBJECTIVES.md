# OBJECTIVES — living roadmap (updated 2026-07-15, day 3)

> Working doc so no context is lost between sessions. Strategy is Elias's; the
> harness serves it. See `STATE.md` (snapshot), `CONTRACTS.md` (interfaces).

## Current focus (15 juil., Elias)

1. **Make the feedback loop the engine of quality.** "This looped approach is
   the way to actually make the thing work": every oracle outcome becomes a
   personalized directive against the game's CURRENT code (engine facts for
   reachability, checkpoint-pair localization for stalls, broken-game feedback
   for degenerate wins), revised with the orchestrator + directive-routed
   skills, re-certified, ledgered. `harness game harden` is live — drive every
   certified game to `HARDENED`+ and every near-miss (parking) to certified.
2. **Demos that don't depress.** Real in-engine renders only (capture lane
   live). 2D games stay 2D — no fake-3D. The path to beautiful demos is
   (a) bank assets on 3D games, (b) the model choosing 3D when the prompt
   evokes it (generation nudge, open), (c) primitives+lighting polish.
3. **Adversarial depth.** Inverse-value attacker (anti-optimal search seeded
   from winning-trajectory prefixes, freeze-detect, tree-refutation confirm)
   as the primary smart G4 tier — in flight.
4. **Model economics.** GLM 5.2 with measured token budgets; thinking-off mode
   (5× cheaper) pending a quality A/B; never a silent fallback anywhere.

## The pyramid strategy (unchanged)

1. **Reliable base of games** across varied prompts; every repair is a LESSON.
2. **Harvest lessons → general rules** — but rules live in the SKILLS library
   and the feedback taxonomy now, not in hand-grown prompt sections; the
   contract stays signatures + hard rules only (anti-anchoring).
3. **Exotic prompts** once the base is solid.
4. **Full engine breadth** last (assignment: "produce playable environments in
   a game or physics engine" — execution context free).

## Hard rules (2026-07-13, still binding)

- **Base code FROZEN during generation runs** — repairs touch generated code
  only; integrity manifest invalidates violated runs; base changes = explicit
  commits between runs.
- **No hardcoded game-specific values in prompts or harness functions.**
  Extended 2026-07-15: no hardcoded taxonomies/mappings on ANY LLM-facing
  surface — light LLM routing over a menu of descriptions (skills, assets),
  deterministic fallbacks only for offline tests.
- **Demos generated from machine-readable results** — no narration beyond the
  stored DESIGN block; the certified witness IS the demo input.
- **Count every failure** (ledger; per-task shards on the cluster, merged with
  dedupe). The ledger decides escalations, not vibes.
- **Secrets:** `env.py` at repo root, gitignored, never committed. Env vars
  override.
- **Cluster citizenship:** ≤200 array tasks, `mit_preemptable`, no bare Godot
  on login nodes, scoped filesystem ops only.

## Generation backends

- `openrouter` / `z-ai/glm-5.2` — THE designer (generation + revise). 32k
  output ceiling, thinking-off salvage; explicit-backend failures = hard
  `ENV_ERROR`, never a silent template.
- `anthropic` — auto-chain fallback only; SDK failures fall through cleanly.
- `template` — offline test fixture only (2 canned games).

## Definition of done (challenge bar)

Prompt in → certified game out, with: replayable witness, G4 grade, RL
learnability grade, real rendered demo, and an auditable repair history —
all produced without a human or a VLM in the loop.
