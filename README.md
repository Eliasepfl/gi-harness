# Harness — text to a playable, physically-verified 2D world

Turn a natural-language command (*"push the ball into the zone"*) into a small 2D physics
scene that an agent can actually play. Every scene is checked by a **100% programmatic**
verifier that reads the engine state directly — positions, velocities, contacts — and never
looks at a rendered pixel or asks a VLM. Generation, verification, and a no-LLM "greedy"
solvability probe all run offline in a closed loop.

## Quickstart

```bash
pip install -r requirements.txt

# Full offline pipeline on the bundled example scenes: verify -> play -> summary table
python -m harness demo

# Generate a scene from a command (offline "template" backend by default)
python -m harness generate "pousse la balle dans la zone"

# Verify a single scene (static -> settling -> goal funnel)
python -m harness verify scenes/examples/push_ball_to_zone.py

# Play a scene with the greedy policy (add --render for a pygame/ASCII view)
python -m harness play scenes/examples/push_ball_to_zone.py --policy greedy
```

Add `--json` to any command for machine-readable output.

**LLM generation is optional.** With no API key, `generate` uses the offline `template`
backend and the whole harness runs end-to-end. If an Anthropic client is configured
(`ANTHROPIC_API_KEY`), `--backend anthropic` (or `auto`) drives scene generation with
`claude-opus-4-8` and a code-only repair loop. No key is needed for `demo`, `verify`, or `play`.

## Architecture

Five modules, one shared contract (`CONTRACTS.md`). Scene code — whether hand-written or
LLM-generated — only ever talks to `SceneSDK`; it never imports the physics engine.

| Module | Role |
|---|---|
| `harness/legacy/sdk.py` | `SceneSDK`: instrumented wrapper over pymunk. Build API + white-box queries. |
| `harness/core/sandbox.py` | AST scan (import/`open`/`exec` whitelist) + subprocess isolation with a hard timeout. |
| `harness/legacy/verifier/` | The L0 → L1 → L2 funnel; emits a 4-way verdict + JSON feedback. |
| `harness/legacy/generator.py` | Command → scene, `anthropic` or offline `template` backend, bounded repair loop. |
| `harness/legacy/navigator.py` | Closed-loop play: observe state → act → step. Greedy policy (no LLM); LLM policy stubbed. |
| `harness/cli.py` | `python -m harness {generate,verify,play,demo}`. |

> Layout note: the table above is the v1 (legacy) stack. The repo is now split into
> `harness/core/` (world, sandbox, integrity, telemetry, bank), `harness/verify/`
> (gameverify), `harness/gen/` (gamegen), and `harness/legacy/` (the v1 modules above).
> Thin shims remain at the old flat paths. See `VERSIONS.md` for the full version map.

**Verification funnel** (cost-ordered, stops at the first failure):

| Layer | Question | Cost |
|---|---|---|
| **L0 static** | Sandbox-clean? Builds? Agent present? No initial penetration, in bounds? | ~ms |
| **L1 settling** | Under gravity with no input, does the scene come to rest without NaN, drift, or interpenetration? | ~0.1 s |
| **L2 goal** | Is `get_success` a pure boolean that is *not* already true at t=0? | ~ms |

The verdict is 4-way — `COMPLETED / PARTIAL / AGENT_ERROR / ENV_ERROR` — so the repair loop
knows *what* to regenerate rather than just *that* something failed.

## Design decisions

**Verification is code-only, by choice.** In a simulated 2D world the true state is fully
observable through code: every position, velocity, angle and contact is available exactly,
not inferred from a screenshot. OMNI-EPIC (ICLR 2025) reaches for exactly this domain — it
rejects VLM judges as *"not yet accurate enough"* and validates success in code *"where
information can be readily accessed through code."* Published pipelines that close the
*physical* correctness loop still lean on a VLM over rendered frames (Code2Worlds,
GameGen-Verifier, SimWorld Studio); ours does not. See `SPEC_VERIFIER.md` for the full
positioning and citations.

**`get_success` is a pure, side-effect-free predicate.** It only reads state and participates
in no reward, which makes it robust to reward hacking — the load-bearing distinction from
OMNI-EPIC. The verifier enforces purity (two successive calls, snapshot unchanged) and
non-triviality (not already satisfied at t=0 or after settling).

**The sandbox is a hard prerequisite.** LLM-generated scene code is untrusted, so it is
AST-scanned against an import/builtin whitelist *before* execution and then run in a dedicated
subprocess with a kill-on-timeout watchdog — never in the orchestrator.

**Greedy play is a solvability probe, not a planner.** It aims at the relevant target
(the goal zone, or — for a push task — a spot behind the object relative to the zone), walks
toward it, jumps when horizontal progress stalls against a frontal obstacle or a higher target,
and injects a small seeded perturbation after prolonged blockage. It solves the bundled
`push_ball_to_zone` and `climb_platforms` scenes; it is deliberately simple and deterministic.

## Roadmap

The current funnel implements L0–L2. The design (see `SPEC_VERIFIER.md`) extends to:

- **L3 — Solvability.** State-injection Hoare-triple keypoints (`(P, a, Q)`, judged in pure
  code) plus a reachability oracle that escalates geometric A* → bounded kinodynamic search.
  The greedy navigator here is the seed of that oracle.
- **L4 — Intent contract.** An Intent Compiler that sees *only* the command (never the scene
  code) freezes a hashed contract of typed properties; the observed contract is reconstructed
  deterministically from the scene graph and rollout, and the two are compared in 100% code.
  This targets the failure mode where a scene is executable yet unfaithful to intent
  (~40% of "exec-repaired" outputs still run wrong physics, per Intent Fidelity).
- **Effective semantic diversity.** Move beyond a fixed template library toward measured,
  non-trivial variety in generated tasks.

Honest limits (inherited from the literature): a passing solvability check is *"no violation
exposed,"* not a proof; and intent fidelity certifies that a scene matches the command's
described structure, not that the command describes realistic physics.
