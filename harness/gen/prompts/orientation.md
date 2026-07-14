# Invent a mechanic - do NOT default to a platformer with left/right/jump
Reach into the substrate: custom or flipping gravity (world.set_gravity); pin/pivot/spring
joints for pendulums, catapults, wrecking balls, tethers, ragdolls; sensors as triggers,
checkpoints, or hazards; timers and rhythm via world.steps; moving obstacles driven from
on_step; counters, combos, and multi-stage goals via flags. A slingshot, a gravity maze, a
juggling act, a falling-sand catcher, a swinging pendulum puzzle - anything but the obvious.
Make winning require deliberate play.

# Composition idioms (proven patterns - adapt, do not copy verbatim)
- A goal sensor plus a controlled body whose success() is "controlled body overlaps the goal sensor" is the simplest well-formed win; build the difficulty from what stands between them, not from the win test.
- Gate the goal behind an intermediate trigger (a pressure pad that must be held, a switch a second body presses) so a single lunge cannot win - that gate is a real second milestone.
- Hazards are sensors: read contact with world.touching / world.on_contact and turn it into failure() or a reset; never rely on a sensor to physically block a body.
- For swinging or toppling mechanics, anchor a joint (pin/pivot/spring) to a STATIC body and control the moving end, then pump it with alternating impulses in act().
- Stage your checkpoints along the actual solution path (left region reached -> obstacle cleared -> at goal) so a failed run tells the harness exactly which segment is unsolved.
