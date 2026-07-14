---
id: fun-mechanics-and-tension
kind: skill
created_by: human-seed (fable-orchestrator)
run_id: seed-2026-07-14
wave: 0
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
rationale: Seed the designer with what makes a certified world FUN and PRECISE — one committed mechanic, near-miss clearances, playable timing windows, escalation, and forced two-axis play.
provenance: mined from harness/gen/prompts/api_godot.md, the showcase games (gem_cavern, demolition_yard, two_switch_vault, meteor_gauntlet), DownFall, ScoreTheGoal, godotworld/SPEC.md, notes/adversarial/G3_TREE_WIRING.md
---

# Fun mechanics & tension

Load when: picking the MECHANIC (what the player's hands do every tick) and tuning feel.
Fun and the G1 agency check are the same lever: geometry where no single held action wins.

## Pick ONE mechanic archetype and COMMIT
The objective is the win-shape (traverse / collect-N / escape); the MECHANIC is what the hands
do. Choose ONE from the menu, build the whole level around it, put its name on `meta.archetype`.
Do NOT blend three archetypes — that is exactly how the DSL collapses onto the same mush.
- **PRECISION HOPS** — grounded-gated impulse jumps across a run of `sensor` hazard strips; a
  `velocity_clamp` keeps you controllable; a mistimed jump touches a hazard → failure. Twitchy.
- **HEAVY-BODY MOMENTUM** — massive body driven by sustained `force` (not bursts); friction+mass
  make it slow to start/stop; thread a narrow gap where over/undershoot fails. Weighty.
- **RISING-HAZARD ESCAPE** — a `rising_level` flood/lava line; `failure: pos_y(hero)<flag(water)`;
  race up a tall world. Mounting pressure.
- **COLLECT-UNDER-PRESSURE** — collectibles (each `on_contact` flag + `remove_when`); a
  `timer_flag`/`rising_level` deadline; success = every flag AND exit. Greed vs safety.
- **SWITCH-GATED PATH** — press a switch (flag) that `remove_when`-deletes a gate wall. Cause→effect.
- **TOPPLE/KNOCKDOWN** — stack modest-mass dynamic boxes, knock over; success reads `angle(...)`
  past a threshold or `pos_y(...)` on the floor. Physics payoff.
- **PENDULUM SWING** (advanced) — `pivot` a dynamic arm to a STATIC anchor, pump with alternating
  impulses, ride/release. Joints are approximate — prototype the swing first.
`[src: harness/gen/prompts/api_godot.md:119-133]`

## Forced two-axis objective (the fun lever AND the agency gate)
Design geometry so no single held/repeated action can win — this is simultaneously FUN and
what satisfies G1's dead-action/single-action check. Tactics from the corpus: put the collect-
stair where it needs BOTH horizontal push AND grounded jump (gem_cavern); put the demolition
target LEFT of the pivot but the goal far RIGHT so winning demands a left-swing then a full
rightward drive — "a forced reversal"; center the start slab so a hop must alternate left/right
to climb. Bind every declared action to a distinct real effect.
`[src: gem_cavern/game.js:9-10,49-52; demolition_yard/game.js:18-20; api_godot.md:148]`

## Minimal orthogonal action set (3 default, cap at 3-4)
Use the smallest move-set covering the puzzle's DOFs: 3 is default (right/left/jump); 2 for a
pure alternator. Add a 4th only when a genuinely DISTINCT strong verb enables a lever (boulder's
`shove`=300 vs `push`=200; two_switch's `dash`=300). Watch the degenerate-fling risk: a too-
strong single impulse can cheese a low goal sensor without doing the intended work — a shove-
fling was flagged GOAL_ERROR (trivial, 5-tick witness). Keep every action orthogonal + effective.
`[src: gem_cavern/game.js:4,49-52; boulder_run/game.js:5,36-42; G3_TREE_WIRING.md:51-56; SPEC.md:44]`

## Near-miss tension — clearance ≈ one body-width
Make hazard clearances about 1-1.5x the controlled body's size, NOT a barn door. A gap you
cannot miss is not a challenge; a clearance the width of the player is where skill lives. Sink
spike pits and lava gaps as narrow `sensor` boxes between footholds. `[src: api_godot.md:138]`

## Timing in ticks — a playable window
Express every window in decision ticks (1 tick = 6 physics steps = 0.1s). A jump that clears a
hazard should have a launch window of ~4-10 ticks: 1 tick is unhittable, 60 is trivial. Tune
survival/collapse deadlines the same way. `[src: api_godot.md:140]`

## Tight feedback — visible within ~3 ticks
Every action must visibly change state within a few ticks. Size its impulse so peak velocity
(≈ impulse/mass) lands in the SPEED-PRIORS band (run ~200-220, jump kick ~400-450, shove
~150-250, all under the ~600 px/s cap) so the player SEES the input land. An effect not visible
within ~3 ticks reads as dead. `[src: api_godot.md:113,137]`

## Grounded-gated jump (the canonical platformer verb)
Bind jump as `{verb:impulse, body, vec:[0, ~430-460], when:"grounded(\"hero\")"}` so the agent
can't fly (breaks G1 no-escape and trivializes vertical puzzles otherwise). Under gravity -900,
peak clearance ≈ J²/1800 px — pick J so peak height slightly exceeds the tallest gap you require.
Horizontal moves stay ungated. `[src: gem_cavern/game.js:52; collect2.spec.json:31; DownFall/scripts/player.gd:65-67]`

## Telegraphed trap — a grace window, not a gotcha
Turn a trap into a timing puzzle by telegraphing it. DownFall's falling_tile changes color on
step, waits on a FallTimer, THEN drops. SPEC analog: `on_contact(hero,tile)→flag`, a
`timer_flag(delay, after_steps=N)` reaction window, then `remove_when(delay, tile)`. Tune N so a
skilled agent steps-and-leaves but a hesitant one falls — fair tension, not an instant gotcha.
`[src: DownFall/objects/falling_tile.gd:12-34; SPEC.md:107-108]`

## Escalating survival pressure (shrink the safe zone)
For a timed-survival world, create mounting pressure that CONTRACTS the reachable region rather
than relying on spawns (unexpressible). Use `on_step rising_level(flag='lava', rate=r, start=y0)`
as a rising line with `failure: pos_y(hero) < flag('lava')`, or a `timer_flag` collapse deadline.
Survival time becomes the challenge. `[src: DownFall/objects/bomb_spawner.gd:5-12; escape.spec.json:30; api_godot.md:127]`

## One-shot commitment latch (aim-then-release)
Model irreversible commitment with a latched flag: set `flag('committed')` via `on_contact`/
`timer_flag`, then gate every act verb with `when:"not flag(\"committed\")"`. Creates a crisp
skill (position, then commit) and a clean success/failure fork instead of endless nudging.
`[src: ScoreTheGoal/scenes/robot/robot.gd:82-83,111]`

## Distractor look-alike goals (discrimination difficulty)
Add difficulty by placing 2+ look-alike sensor zones but writing `success` to reference only ONE.
CRITICAL constraint: the geometry/sensor fan MUST give the agent a cue to tell the real goal from
the decoys (e.g. a positional asymmetry, a required flag), else the task collapses to a coin-flip
and is unlearnable. `[src: ScoreTheGoal/scenes/robot/robot_ai_controller.gd:37-48]`
