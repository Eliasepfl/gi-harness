# Inverse-value G4 attacker — critic-guided softlock hunting (Elias's idea)

> 2026-07-15. Replaces the LLM "negative-prompt" attacker (slow, non-real-time,
> extra LLM call/attack — DROPPED). Reuses the PPO critic G3' already trains.
> Supplies the SMART SEARCH the stale-state G4 tier (notes/engines/
> GODOT_RL_AGENTS_CAPABILITIES.md §4 + the g4 stale-state code) was missing.

## The idea (Elias)
To drive a game into a stalemate, don't search randomly — go the LEAST-optimal
direction using the value function INVERTED, run many agents in parallel from
different leaves, and flag when an agent takes actions for ~5-10 steps with NO
evolution of the state.

## Why it works
G3' trains PPO → we get BOTH a policy π (max value) AND a critic V(s)/Q(s,a),
for free. The attacker is the SAME critic inverted: `a = argmin_a Q(s,a)` (or a
low-value frontier selector). Random fuzz finds stuck regions by luck; a
critic-guided anti-optimal attacker STEERS toward dead/low-value regions
directly — higher hit-rate, ~zero extra cost, embarrassingly parallel.

## The subtlety (and its fix)
min-value covers TWO outcomes: getting STUCK (softlock) AND LOSING (hazard).
We want only stuck. Detection separates them: a LOSS terminates; a SOFTLOCK
FREEZES. So flag on state-frozen-while-acting, not on low value alone.

## Three layers
1. **SEARCH (new, Elias):** inverse-V / min-Q attacker over the trained critic
   steers toward low-value regions. Parallel: many seeds/leaves (Slurm array),
   each an anti-optimal rollout. Cheap — greedy argmin over the existing critic,
   no new training. Fallback when no critic (tree-solvable games): use the tree
   solver's milestone-value estimate inverted, or a quick shallow critic.
2. **DETECT (Elias's criterion, = stale-state trigger 1a):** over a window of
   N=5..10 decision ticks WITH actions applied: statetree.fingerprint() delta
   < EFFICACY_EPS (state frozen) OR a closed fingerprint cycle, AND no new
   checkpoint latched, AND not terminal. False-positive guard: legit "push into
   wall" moments are short; require the FULL window + no checkpoint progress.
3. **CONFIRM (existing 1c oracle):** from the frozen prefix P, run the G3
   Go-Explore solver on continuations at horizon len(P)+H, TICK_BUDGET. No
   TERMINAL_SUCCESS under P -> certified softlock witness (deterministic,
   replayable {seed, actions}); subtree saturation is stronger. This is what
   makes it a real finding, not a heuristic.

## Grading + integration
- softlock (1c-certified) -> hard outcome -> G4 grade `open` (existing
  `_HARD_OUTCOMES`); heuristic-only (1a/2 without 1c) stays soft.
- Slots into the g4 attacker ladder as the PRIMARY smart search, ahead of
  random fuzz. Reproducer = P + provenance {oracle:"inverse_value+tree_refute",
  critic_source, H, budget, seed}.

## Dependency + cost
- Needs the G3' critic -> composes with the G3'-on-gdscript wiring (in flight).
- Per game: the inverse-V rollouts are as cheap as greedy eval (seconds at
  speedup 8); the 1c confirm is one G3-solve budget per candidate (cap top-M).
- Parallel: one game per Slurm task; within a task, many inverse-V seeds.

## Honest limits
- The critic is only as good as the G3' training; a weak critic -> weaker
  steering (but detection+confirm are still sound, just less efficient search).
- Distinguishing "stuck" from "slow legit progress" is threshold-tuned (the
  N-window + no-checkpoint guard); the 1c oracle is the backstop against FPs.

## Measured (2026-07-15, softlock_pit.gd, in-image, speedup 8)
A/B at the SAME 1600-tick budget (6 seeds, eps 0.1, window 6, backplay handoffs
8/16/32/48 from the certified G3 witness; CONFIRM = refute_prefix H=30/3000):

| arm                          | detect/1k | certified/1k |
|------------------------------|-----------|--------------|
| inverse-value, competent critic | 10.80  | 2.70 (3/3 cap) |
| random fuzz                  | 2.08      | 2.08 (1)     |
| inverse-value, WEAK critic*  | 0.0       | 0.0          |

*the weak-critic honest limit made concrete: a quicktest-budget PPO (8-24k steps,
greedy success 0.0) has a near-uniform policy, so `argmin pi` degenerates to a
CONSTANT action that dives into a terminal (play-bounds loss) — a LOSS, which
DETECT correctly refuses to flag. With a competent critic (one that avoids the
pit dive — what a real G3'-certified artifact looks like) the steering is ~5x
random on detections and beats it on certified findings at the same budget.
Follow-up worth taking: gate the ladder tier on g3_prime SUCCESS (final_success
_rate > 0), not artifact existence alone. Zero false certifications in any arm.
