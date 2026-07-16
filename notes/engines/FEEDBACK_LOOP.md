# Personalized feedback loop — repair directives from oracle outcomes (Elias's spec)

> 2026-07-15. "This looped approach is the way to actually make the thing work."
> The model must always be corrected AGAINST ITS CURRENT CODE (revise the sandbox,
> minimal edit — the existing `_revise_user_msg` entry), never regenerated blind.

## The full loop

```
prompt ──> generate ──> G0 parse/contract ──> G0.5 reachability ──> G1..G3 ──┐
              ^                                                              │ certified
              │                                                              v
              │                                        post-cert oracles: G4 attack + G3' RL
              │                                                              │
              └──────────── FEEDBACK COMPILER (outcome -> directive) <───────┘
                            re-enter via revise-from-current-source, re-certify
```

Two entry modes, both already exist:
- **in-loop repair** (game not yet certified): the conversation carries the model's
  previous code as its own assistant turn + `_repair_user_msg(report)`.
- **post-cert revise** (game certified, oracle found a defect): `_revise_user_msg`
  embeds the CURRENT module + a directive, demands a minimal edit, then the result
  goes through the SAME verify loop. This is the re-entry for G4/RL findings.

## Feedback taxonomy (the compiler)

The **Severity** column tiers each compiled directive (`feedback.severity_of`, the field on
`Directive`): **DEFECT** = a proof-carrying brokenness, worth the FULL repair budget; **DIFF**
= HARD-TO-LEARN, not broken (it re-certifies unchanged), worth a small nudge only. Only the
two G3' learnability-CURVE rows are soft; every proof-carrying defect is a DEFECT. See the
severity-budget rule below.

| Oracle outcome | Severity | Directive fed back (personalized, against current code) |
|---|---|---|
| G0.5 walled-off checkpoint | gate | **Engine facts raised**: "checkpoint '<key>' is geometrically unreachable from spawn — enclosed by static bodies <AABBs/names>. Open a corridor or move the checkpoint." Facts, not vibes. (Pre-cert G0.5 gate, not a post-cert budgeted directive.) |
| G3 tree UNSOLVED w/ progress | gate | (EXISTS, v2.1) "stuck between '<k>' and '<next>' — fix that segment." (Pre-cert G3 gate.) |
| G3' RL **progressing** | — | **NO feedback — keep training.** Progress-gated budget up to 1M steps (plateau-patience already encodes this: early-stop only fires on stall). |
| G3' RL **plateaued mid-course** (`g3_plateau`) | **DIFF** | Checkpoint-pair directive from the latch curve: "a trained agent latches '<k>' reliably (rate r) but never '<next>' — the defect is between these two checkpoints." Same shape as the tree lane's `stuck_after`, fed from RL. Hard-to-learn -> nudge budget only. |
| G3' RL **all latched, never wins** (`g3_difficulty`) | **DIFF** | "reaches every milestone yet success stays 0 — the final win condition is mis-gated/too strict; loosen it just past the last milestone." Hard-to-learn -> nudge budget only. |
| G3' RL **zero progress** (full budget) (`g3_unsolvable`) | **DEFECT** | "Unsolvable by a trained agent: no checkpoint ever latched — the first objective is unreachable or the controls cannot produce progress." NOTHING latched = broken, NOT merely hard. (Payoff run confirmed this class is real: drive-cart/hop latched 0.) |
| G4 `single_action_win` | **DEFECT** | **Broken game** directive: "winnable by repeating one action — add a real obstacle/choice." (Cheap probe runs in-loop, pre-cert.) |
| G4 shortcut w/ **broken gating** | **DEFECT** | "success reachable WITHOUT checkpoint '<key>' — gate the win on it or remove the checkpoint." Informational shortcuts (merely easier than witness) do NOT re-enter the loop. |
| G4 `softlock` (tree-refutation confirmed) | **DEFECT** | "dead-end state reachable — quote the frozen-state reproducer; add an escape/reset." Only the CERTIFIED class compiles; heuristic `stuck` is informational. |
| PRESSURE gate **`no_pressure`** (WAVE 1) | **DEFECT** | **No stakes** (static proof): "the game cannot be lost — is_failure() is hardcoded false; add a real failure condition (hazard, timeout, out-of-bounds, resource depletion) so play has stakes." ADVISORY — warn + directive, never a hard cert-block (see below). |
| PRESSURE gate **`failure_unreachable`** (WAVE 1) | **DEFECT** | "is_failure() never fires from any reachable state under a broad adversarial rollout — the win always resolves first (the race), or the detector never triggers. Make failure a condition a real player could actually trigger." ADVISORY; a bounded reproducer-ABSENCE. Distinct from `no_pressure`. |
| PRESSURE gate **`has_pressure`** | — | NO directive — a reachable failure was WITNESSED; the game has stakes. |
| body leaves play bounds | — | NOT a defect — episode truncation in serve (bounds termination). Never fed back. |

## Dimension-awareness (Elias)

The G0.5 occupancy flood and the play-bounds box MUST match the game's own
dimensionality — inferred from the game (Node3D/state width), never assumed:
a flying game needs the 3D box; a planar game the 2D grid. Wrong-dimension
geometry checks produce false walls.

## Skill routing on repair turns (Elias, 2026-07-15; corrected same day)

Revise turns keep the **godot-master orchestrator leading** — the README routes
"Auditing an existing project to find anti-patterns or standards violations" to
godot-master, and a revise turn IS an audit-and-fix of an existing project.
(Earlier draft said domain-skills-only; Elias corrected it.) What changes vs
fresh generation is the ROUTING QUERY: the domain-skill layer is selected on the
**DIRECTIVE/ERROR text**, not the original game prompt —
`render_skill_context(<directive text>, orchestrator=True)` — and the message
carries (a) the directive and (b) the CURRENT game source. Route on the error,
keep the current state authoritative, avoid drifting into a from-scratch rebuild.

## WAVE 1 — PRESSURE: the failure-witness gate + terminal reachability

Built per `notes/engines/DEMO_GAP_ANALYSIS.md` (the #1 ranked gap: 4/6 games are
unfailable, so idling is free and ANTI-IDLING has no in-game meaning).

- **Failure-witness gate** (`gameverify._failure_witness_gate`, gdscript funnel, after
  the single-action gate). Confirms `is_failure()` can fire from a REACHABLE state — a
  failure witness, dual to the G3 success witness. Two no-stakes verdicts:
  `no_pressure` (static: `gd_gate.is_failure_constant_false` — a literal `return false`)
  and `failure_unreachable` (dynamic: `reachability.failure_reachable` — a broad
  adversarial sweep, coverage + random + inverted-objective tree, that never loses).
- **Gate decision — ADVISORY, not a hard block** (the documented choice). Rationale:
  (1) false-reject discipline — the sweep is a BOUNDED negative, so a genuinely
  hard-to-trigger failure must never be wrongly rejected (the same "err toward passing"
  stance G0.5 takes); (2) it must not preempt the blocking gates (single-action, G0.5)
  that own a broken fixture's real defect; (3) Wave-1 acceptance ("0 constant-false
  is_failure in the CERTIFIED set") is delivered by the REVISE loop, not a reject — the
  finding is proof-carrying and ALWAYS compiles a directive, driving the final set to
  have stakes. Recorded as a NON-GATING `failure_witness` sub-check under G3_solve
  (always pass=True; the signal is `has_failure_witness`) + a report warning, so
  `report["passed"]`/`failure_class` and the top-level report key set are untouched.
  All existing certifying fixtures are constant-false, so the static check
  short-circuits BEFORE any extra rollout — the gate adds ZERO Godot cost to them.
- **`terminal_reachable(state)`** (`reachability.terminal_reachable`, via the certified
  Go-Explore tree solver over a prefix-wrapped executor). The principled
  stuck-vs-refusal separator: from a non-terminal state, can play still reach EITHER
  success OR failure? `reachable` is PROVEN (a replayable witness); `env_softlock`
  (neither reachable in budget) is a real environment softlock. A state that IS
  terminal-reachable but whose own trajectory idles is AGENT-refusal, not a game defect.
  Exposed for BOTH the adversary CONFIRM layer and this feedback loop to read.
- **Wiring**: the gate stashes a machine-readable finding under
  `layers.G3_solve.checks.failure_witness.finding`; `feedback.pressure_finding(report)`
  lifts it into `oracle_results["pressure"]`, which `feedback._compile_pressure` maps to
  the directive row above (order: G4 → PRESSURE → G3'). The report→oracle_results bridge
  is a one-liner for the harden driver (out of this wave's scope).
- **LLM surfaces** (anti-anchoring: principle only, no hazard list, no values): a STAKES
  principle in `api_gdscript.md` and a `Pressure:` field in `design_block_gdscript.md`.

## Convergence guard

- Cap post-cert revise rounds (start: 3/oracle-finding); every directive +
  resulting verdict goes to the ledger (auditable repair history).
- Each revise is followed by FULL re-certification (G0..G3) — a fix that breaks
  an earlier gate is caught immediately, no oscillation goes unrecorded.
- Directives are idempotent-checked: the same finding twice in a row = the model
  failed to fix it → stop, mark `REPAIR_STALLED`, keep the last certified version.

## Severity tiers & the difficulty budget (2026-07-15 harden wave)

Observed failure: `fly_a_craft` burned all 3 rounds on `g3_plateau` — each revise
RE-CERTIFIED (the game stayed valid) but the plateau persisted, because a plateau means
HARD-TO-LEARN, not BROKEN. Treating difficulty as a defect wastes rounds and can DEGRADE a
good game chasing a phantom fix. Fix — tier the directives by severity and budget by tier
(`feedback.severity_of`; `harden.harden_game`):

- **DEFECT** (`single_action_win`, `broken_gating`, `softlock`, `g3_unsolvable`,
  `no_pressure`, `failure_unreachable`): worth the FULL `max_rounds` (default 3) + the
  `REPAIR_STALLED`/`REPAIR_FAILED` convergence guard. A real, proof-carrying brokenness.
- **DIFFICULTY** (`g3_plateau`, `g3_difficulty`): the two G3' learnability-curve rows.
  Hard-to-learn, not broken — it re-certifies unchanged, so it earns a small
  `difficulty_budget` of BONUS nudge rounds (default **1**; **0** disables nudging entirely).
  A nudge is attempted in the sandbox but **NEVER advances the deliverable** — the certified,
  defect-clean game is preserved (a phantom fix must not overwrite a good game). One nudge,
  no grind.
- **Priority**: when a round carries BOTH, the DEFECTS are revised FIRST (difficulty is
  deferred, recorded under `deferred_difficulty`); a difficulty nudge fires only once the
  game is defect-clean and certified.

### Terminal verdicts + CLI exit code (`harden.HARDEN_SUCCESS_VERDICTS`)

| Verdict | Meaning | Exit |
|---|---|---|
| `HARDENED` / `BULLETPROOF` | No findings — clean/bulletproof. | 0 |
| `HARDENED_HARD` | Defect-clean + certified; a DIFFICULTY persists past its nudge budget. A SUCCESS-ish terminal (valid + hardened, merely hard to learn) — **not** a failed harden. | **0** |
| `CONTINUE_TRAINING` | G3' curve was still climbing at budget — give more steps, no repair. | 1¹ |
| `REPAIR_STALLED` | A DEFECT fingerprint recurred after its fix — the model could not remove it; last certified version kept. | 1 |
| `REPAIR_FAILED` | A DEFECT fix did not re-certify (verdict != COMPLETED); last certified version kept. | 1 |
| `MAX_ROUNDS` | DEFECTs still unresolved when the defect budget ran out. | 1 |
| `OPEN_UNMAPPED` / `G4_ERROR` | A hard G4 finding outside the taxonomy / a G4 error. | 1 |

¹ `CONTINUE_TRAINING` is not in `HARDEN_SUCCESS_VERDICTS` (it is a "needs more training",
not a "hardened" terminal). The key change vs the old vocabulary: a difficulty-only remainder
now reads `HARDENED_HARD` (exit 0), not `MAX_ROUNDS`/`REPAIR_STALLED` (exit 1).

## Status

- EXISTS: multi-turn repair w/ current code in context; tree-lane checkpoint-pair
  diagnosis; revise-from-current-source entry; plateau-patience trainer.
- IN FLIGHT (worktree agent): G0.5 reachability gate + facts, bounds termination,
  single-action probe -> repair hint, shortcut/broken-gating split.
- TO BUILD (this note's implementation): the feedback compiler (G3'/G4 outcome ->
  directive), RL latch-curve -> checkpoint-pair localization, progress-gated 1M
  budget, post-cert revise wiring + convergence guard.
