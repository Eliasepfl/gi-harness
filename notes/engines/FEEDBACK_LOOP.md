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

| Oracle outcome | Directive fed back (personalized, against current code) |
|---|---|
| G0.5 walled-off checkpoint | **Engine facts raised**: "checkpoint '<key>' is geometrically unreachable from spawn — enclosed by static bodies <AABBs/names>. Open a corridor or move the checkpoint." Facts, not vibes. |
| G3 tree UNSOLVED w/ progress | (EXISTS, v2.1) "stuck between '<k>' and '<next>' — fix that segment." |
| G3' RL **progressing** | **NO feedback — keep training.** Progress-gated budget up to 1M steps (plateau-patience already encodes this: early-stop only fires on stall). Optional variant: if latch is high but success never fires → difficulty-reduction directive. |
| G3' RL **plateaued mid-course** | Checkpoint-pair directive from the latch curve: "a trained agent latches '<k>' reliably (rate r) but never '<next>' — the defect is between these two checkpoints." Same shape as the tree lane's `stuck_after`, fed from RL. |
| G3' RL **zero progress** (full budget) | "Unsolvable by a trained agent: no checkpoint ever latched — the first objective is unreachable or the controls cannot produce progress." (Payoff run confirmed this class is real: drive-cart/hop latched 0.) |
| G4 `single_action_win` | **Broken game** directive: "winnable by repeating one action — add a real obstacle/choice." (Cheap probe runs in-loop, pre-cert.) |
| G4 shortcut w/ **broken gating** | "success reachable WITHOUT checkpoint '<key>' — gate the win on it or remove the checkpoint." Informational shortcuts (merely easier than witness) do NOT re-enter the loop. |
| body leaves play bounds | NOT a defect — episode truncation in serve (bounds termination). Never fed back. |

## Dimension-awareness (Elias)

The G0.5 occupancy flood and the play-bounds box MUST match the game's own
dimensionality — inferred from the game (Node3D/state width), never assumed:
a flying game needs the 3D box; a planar game the 2D grid. Wrong-dimension
geometry checks produce false walls.

## Skill routing on repair turns (Elias, 2026-07-15)

Per the gd-agentic-skills README split: **godot-master orchestrator leads only on
FRESH generation** ("starting a new Godot project from scratch"); **repair/revise
turns use Domain Skills** ("adding a specific feature to an existing codebase,
learning a targeted Godot API pattern"). Concretely: the revise/repair message
carries (a) the directive/error, (b) the CURRENT game source, and its skill
context is `render_skill_context(<directive text>, orchestrator=False)` — routed
on the ERROR, not the original game prompt. Keeps the current state authoritative
and avoids drift back toward a from-scratch rebuild.

## Convergence guard

- Cap post-cert revise rounds (start: 3/oracle-finding); every directive +
  resulting verdict goes to the ledger (auditable repair history).
- Each revise is followed by FULL re-certification (G0..G3) — a fix that breaks
  an earlier gate is caught immediately, no oscillation goes unrecorded.
- Directives are idempotent-checked: the same finding twice in a row = the model
  failed to fix it → stop, mark `REPAIR_STALLED`, keep the last certified version.

## Status

- EXISTS: multi-turn repair w/ current code in context; tree-lane checkpoint-pair
  diagnosis; revise-from-current-source entry; plateau-patience trainer.
- IN FLIGHT (worktree agent): G0.5 reachability gate + facts, bounds termination,
  single-action probe -> repair hint, shortcut/broken-gating split.
- TO BUILD (this note's implementation): the feedback compiler (G3'/G4 outcome ->
  directive), RL latch-curve -> checkpoint-pair localization, progress-gated 1M
  budget, post-cert revise wiring + convergence guard.
