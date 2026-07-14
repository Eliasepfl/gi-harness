# Scale: design a JOURNEY, not a screen
Declare WORLD_SIZE and use the space: a [2000, 700] canyon crossed left to right, a
[900, 1500] tower climbed bottom to top, a [1600, 1000] cavern explored in an S-path.
The renderer has a follow camera - multi-screen levels are expected, single-screen
scenes are the exception. Spread real obstacles across the whole world: a level whose
action all happens in one corner wastes the space it declared. Target a winning run
of 60-200 decision ticks that passes through visibly different regions.

# Objective archetypes - pick ONE and commit (never default to "walk right and touch the flag")
- TRAVERSE: cross a long hostile stretch (gaps, movers, hazard runs). The distance IS the difficulty.
- COLLECT-N: pick up N scattered items (sensor + world.on_contact flag each), THEN unlock the exit; success counts the flags AND requires the exit.
- DELIVER: push/carry a loose body (crate, boulder, ball) to a target zone; success tests the CARGO's position, not the player's.
- ACTIVATE-SEQUENCE: hit switch A then B (order enforced via flags), each opening a gate (world.remove a wall slab) toward the goal.
- ESCAPE: a rising/advancing hazard driven from on_step (lava line, crusher, flood); success = reach the safe zone before it catches you; failure = it does.
- TOPPLE/DESTROY: bring a structure down (rest-state or position test on ITS parts) using contraptions, then reach what it was guarding.
- SURVIVE-THEN-EXIT: outlast a timed onslaught (world.steps timer + moving threats), then the exit sensor arms (flag) and you must still reach it.

# Invent a mechanic - do NOT default to a platformer with left/right/jump
Reach into the substrate: custom or flipping gravity (world.set_gravity); pin/pivot/spring
joints for pendulums, catapults, wrecking balls, tethers; sensors as triggers, checkpoints,
or hazards; timers and rhythm via world.steps; moving obstacles driven from on_step;
counters, combos, and multi-stage goals via flags. A slingshot, a gravity maze, a juggling
act, a swinging pendulum puzzle - anything but the obvious. Make winning require deliberate
play across the WHOLE level.

# Composition idioms (proven patterns - adapt, do not copy verbatim)
- A goal sensor plus a controlled body whose success() is "controlled body overlaps the goal sensor" is the simplest well-formed win; build the difficulty from what stands between them, not from the win test. Better: gate it behind one of the archetypes above.
- Gate the goal behind an intermediate trigger (a pressure pad that must be held, a switch a second body presses) so a single lunge cannot win - that gate is a real second milestone.
- Hazards are sensors: read contact with world.touching / world.on_contact and turn it into failure() or a reset; never rely on a sensor to physically block a body.
- For swinging or toppling mechanics, anchor a joint (pin/pivot/spring) to a STATIC body and control the moving end, then pump it with alternating impulses in act().
- Stage your checkpoints along the actual solution path (first region cleared -> mechanism triggered -> final stretch -> at goal) so a failed run tells the harness exactly which segment is unsolved.
- Decor parts (tree, bush, fence, sign_arrow...) are non-colliding scenery: use 2-4 to make regions visually distinct, and NEVER as obstacles.
