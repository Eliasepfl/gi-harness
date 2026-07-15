# Scale: design a JOURNEY, not a screen
Declare `meta.world_size` and use the space: a wide canyon crossed left to right, a tall tower
climbed bottom to top, an S-path cavern explored corner to corner. The renderer follows the
controlled body, so multi-screen levels are the norm and single-screen scenes the exception.
Spread real obstacles across the WHOLE world - a level whose action all happens in one corner
wastes the space it declared. Target a winning run of 60-200 decision ticks that passes
through visibly different regions.

# Objective archetypes - pick ONE and commit (never default to "walk right and touch the flag")
The MECHANIC (the engine section above) is what your hands do every tick; the OBJECTIVE is the
win SHAPE. Choose one and build the level around it:
- TRAVERSE: cross a long hostile stretch (gaps, hazard runs); the distance IS the difficulty.
- COLLECT-N: pick up N scattered items (each a sensor zone + an on_contact flag), THEN the exit
  arms; success counts every flag AND requires the exit.
- DELIVER: push or carry a loose body (crate, boulder, ball) into a target zone; success tests
  the CARGO's position (contained / contacts), not the player's.
- ESCAPE: a rising_level hazard climbs from on_step; success = reach the safe zone before it
  catches you; failure = it does.
- STEER-TO-POSE: drive a heading-controlled body (thrust along its facing + torque to turn)
  to a pose target and settle - park in / land on / dock at a zone read by contained().
- TOPPLE: bring a structure down (an angle or pos_y test on ITS parts), then reach what it
  guarded.
- SWITCH-GATE: a body trips a switch (on_contact flag) that removes a gate wall, opening the
  route (flags latch but cannot enforce A-then-B order - design ONE meaningful gate).

# Invent a mechanic - do NOT default to a platformer with left/right/jump
Reach past the obvious: a heading-steered vehicle threading gates, a heavy body threaded
through a narrow gap, a pendulum pumped across a chasm, a flood raced up a tower, cargo herded
to a dock. Make winning demand deliberate play across the WHOLE level.

# Design universals (distilled from the 20-example corpus - structure only, infer the specifics)
- ONE controlled body, a SMALL action set (2-4): steer/thrust when the game is a vehicle,
  translate when it is about catching or herding.
- Make success a COMPOUND latch - pose AND stillness AND state (be there AND slow AND aligned,
  or all-collected, or switch-tripped) - never a bare zone touch a single lunge satisfies.
- Bound the arena; breaching it is a negative terminal (this is the CONTAINMENT moat).
- The LAYOUT is your only variety knob - vary REGIONS across the world, not runs.
- Pick a distinct DIFFERENTIATOR family per game (steer-to-pose / dodge-timing /
  catch-and-route / precision-collect / one-shot-commit) and make each batch entry's family
  and world orientation DIFFER from the last - this is the anti-sameness lever.
- Flip the objective when a design feels familiar (park -> escape, race -> errand, defend ->
  survive) to keep the essence while dodging the shipped shapes.
- Prefer skeletons expressible today (steer-to-pose, ordered gates, collect-then-contained);
  reach for exotic autonomous movers only when a moving hazard IS the game's soul.

# Composition idioms (proven patterns - adapt, do not copy verbatim)
- The simplest well-formed win is "controlled body reaches the goal sensor"; build the
  difficulty from what stands BETWEEN them, not from the win test. Better: gate it behind one
  of the archetypes above.
- Gate the goal behind an intermediate trigger (a pad that must be held, a switch a second body
  presses) so a single lunge cannot win - that gate is a real second milestone.
- Hazards are sensor zones: read contact and turn it into failure or a reset; never rely on a
  sensor to physically block a body.
- For swinging or toppling mechanics, anchor a joint (pin/pivot/spring) to a STATIC body and
  control the moving end, then pump it with alternating impulses across two actions.
- Stage your checkpoints along the actual solution path (first region cleared -> mechanism
  tripped -> final stretch -> at goal) so a failed run tells the harness exactly which segment
  is unsolved.
- Dress every region: 2-5 non-interactive decor bodies make regions visually distinct - and
  NEVER use them as obstacles.
