---
id: rising-hazard-survive
kind: archetype
created_by: human-seed wave-1
run_id: reseed-2026-07-14
wave: 1
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
load_when: Concept phase — the game's soul is outlasting a rising line or surviving a timer while staying safe
rationale: The timing/survival family expressible today WITHOUT movers (a rising water/lava line via rising_level, or a timed survival via timer_flag). Inverts a goal-chase into "stay alive and keep climbing," a distinct skeleton from reach-a-zone.
provenance: notes/engines/EXAMPLES_STRUCTURE_GUIDE.md §2 (timing-gauntlet; note true movers need B1) + §4; godotworld/SPEC.md §7 (rising_level, timer_flag) + §8 (failure predicate); survival loop decomposition — knowledge derived from thedivergentai/gd-agentic-skills (LGPLv3), paraphrased.
---

# Archetype: rising-hazard-survive

The soul: a threat grows over time and the agent must stay ahead of it — climb above
a rising line, or hold out until a survival timer expires. Objective flip ideas:
flood-escape → climb to stay dry; last-stand → survive N steps in-bounds. (Moving
hazards proper need the B1 mover brick — not yet; use the rising line / timer.)

## Core loop

1. **Read the threat** — the current hazard level (rising line) or time remaining.
2. **Reposition** — move/climb to stay above the line or inside the safe zone.
3. **Hold** — maintain a safe margin as the threat escalates.
4. **Endure** — keep it up as the margin tightens.
5. **Clear** — reach the survival condition (timer done, or a high safe perch held).

## Skill-chain of subgoals

- get clear of the initial threat → checkpoint `off_the_floor`
- survive to the midpoint → checkpoint `halfway`
- reach the survival condition → success

## DSL expression

- **Control:** grounded-gated `impulse` to climb (if platforming up), or
  translational `force` to reposition — theme's choice.
- **The threat:** an `on_step` `rising_level` sets a numeric flag `hazard =
  start + rate*steps` — a rising line to compare against. Or a `timer_flag` sets
  `survived` true once `steps >= after_steps`.
- **Failure (the teeth):** `pos_y(body) < flag("hazard")` — caught by the rising
  line (down is -Y; being BELOW the line is bad if the line rises from the floor;
  orient the comparison to your theme). This must be a real, reachable danger.
- **Success:** `flag("survived")` (timer) — or a held high-perch:
  `contained(body, safe_perch) and pos_y(body) > flag("hazard")`.
- **Sensing:** a raycast fan to read distance to walls / the next foothold.

## Structural sketch (high-level, no dimensions)

A bounded vertical (or enclosed) arena with climbable static footholds or a safe
perch up high, and a rising hazard line (or a survival clock). Foothold layout and
spawn re-rolled each reset. Checkpoints ladder off-the-floor → halfway → survived;
success on the timer/perch condition, failure when the line catches the body or it
escapes. Rise rate, foothold spacing, and timer length are the model's to choose —
tight enough that standing still loses (preserving agency), loose enough that a good
policy survives.
