---
id: world-composition-and-landscape
kind: skill
created_by: human-seed (fable-orchestrator)
run_id: seed-2026-07-14
wave: 0
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
rationale: Seed the designer with how to make each certified world read as a distinct PLACE (landscape/setting), not a reskinned box, using only names + static geometry.
provenance: mined from godot_rl_agents_examples (BallChase, DownFall, CrossTheRoad), godotworld/examples/*.spec.json, harness/core/spritebank.py, banks/sprites/slicemap.v1.json, banks/parts/v1/parts.json, harness/gen/prompts/api_godot.md
---

# World composition & landscape

Load when: choosing a SETTING, naming bodies, or laying out the static scenery/terrain.
Richness is a first-class goal — vary the WORLD, not just the objective. You dress a
world with three free levers: recognized NAMES (skin), static GEOMETRY (shape the
place), and meta.title/prompt (the fiction the runner ignores but the reader reads).

## Theme rides on RECOGNIZED part-names (not fuzzy strings)
The renderer skins a body only if its `name` resolves against a fixed 57-name vocabulary
via 4 ordered rules — exact → strip trailing enumerator/positional suffix (`_2`,`_l`,
`_top`) → singularize (`gems`→`gem`) → tiny alias table (`box`→crate, `stone`→boulder,
`plank/beam/lever`→seesaw, `puck`→ball, `floor`→ground, `flag/goal`→goal_zone). It is
NOT substring/fuzzy: an invented compound like `quarry_shelf` or `lava_field` falls back
to a flat untextured shape. RULE: name every entity EXACTLY one recognized word (+ an
optional numeric/positional suffix); put all other fiction in meta.title/meta.prompt.
`[src: harness/core/spritebank.py:20-33,87-104,182-226; banks/sprites/slicemap.v1.json]`

### The 57 recognized names (pick from these)
- control/dynamic: `marble ball boulder crate crate_double rock roller rolling_log wrecking_ball hammer tnt heavy_anvil coin gem key_gem star_target`
- terrain/static: `ground ground_dirt ground_stone ledge platform pillar wall block_tall block_wide bridge ramp ramp45 wedge_block ice_block ice_floor mud_patch`
- hazard sensors: `lava water water_pool fire_pit spike saw mine pit_zone`
- trigger/goal sensors: `goal_zone target_zone checkpoint button_plate lever_switch door_slab bounce_pad`
- joint-mobile: `moving_platform seesaw swing_gate`
- decor: `tree bush fence torch sign sign_arrow cloud_puff`
11 names render as a plain flat shape today (`button_plate cloud_puff heavy_anvil ice_block
ice_floor mine pit_zone rolling_log swing_gate target_zone wedge_block`) — still valid
physics + semantics, just untextured. `[src: banks/sprites/slicemap.v1.json parts]`

## Ground-skin picks the biome (free)
`ground`, `ground_dirt`, `ground_stone` skin to different tiles at identical physics cost.
Set the full-width floor's NAME to declare the material — `ground_stone` for quarry/cave/
mountain, `ground_dirt` for field/forest, plain `ground` for a neutral stage — and set the
hazard biome by naming a wide sensor `lava`/`water`/`water_pool`/`fire_pit`. Material
identity is a naming choice, not a geometry change. `[src: godotworld/examples/escape.spec.json:11-12]`

## Frame every world (mandatory infrastructure that doubles as border)
Close the space with 2 static `wall` bodies at x≈10 and x≈W-10, each ≥12px wide (use 20)
× full height, `friction:0.2` so bodies don't cling; add a top wall/ceiling if anything
launches up. Name them `wall` and `wall_2` (suffix-strip skins `wall_2`→wall). This
satisfies G1's no-escape guard AND frames the scene. Keep every dynamic spawn inset ~10-14%
of the smaller dimension so nothing starts touching a wall.
`[src: godotworld/examples/traverse.spec.json:15-16; api_godot.md:157; BallChase.tscn:15-24]`

## Terraced ledges ARE the landscape AND the climb
Build vertical depth with 3-4 same-named `ledge` bodies (`ledge`,`ledge_2`,`ledge_3` all
skin to the ledge tile), rising ~65-80px per tier (just under the ~65px hop clearance) and
staggered horizontally ~300-370px so each tier is one hop from the last, across a widened
1200-1400px world. Pair with hop impulse ~430-460 and `velocity_clamp vy_max ~520-540`.
Shipped Quarry Shelves: ledge y=105, ledge_2 y=185, ledge_3 y=265, each 260×30, climbing
left→right. One composition, two payoffs (a terraced quarry AND a precision-hop mechanic).
`[src: godotworld/examples/traverse.spec.json:12-14]`

## Depth geometry must earn its keep
Every static depth piece must ALSO drive the mechanic — a body no action/hazard/predicate
touches is forbidden noise. `pillar` = temple pedestal AND path-gate/target-holder; `bridge`
= scenery AND sole foothold over a pit; `ramp`/`ramp45`/`wedge_block` = slope AND a roll/slide
surface; a wide `ledge`/`platform` above the play line = canopy the hero passes under. Wire
each one in. `[src: banks/parts/v1/parts.json; api_godot.md:147]`

## Decor = static AND sensor, 2-3 pieces max
Purely-scenic bodies MUST set BOTH `static:true` AND `sensor:true` so they never collide,
never catch `grounded()`, never cause G0 interpenetration. Draw from `tree bush torch fence
sign sign_arrow cloud_puff`; sizes: tree ~65×130, bush ~55×45, torch ~30×70. Cap at ~2-3 —
the brief forbids decor-that-never-matters, but a couple of named sensor pieces are the
expected way to theme. `[src: godotworld/examples/traverse.spec.json:19-20, escape.spec.json:18-19; api_godot.md:147]`

## Enumerated motifs multiply into landscape AND drive variety
Because suffix-strip skins `pillar_2 pillar_3`, `spike_2`, `step_block_2` all to their base
tile while each stays a distinct physics body, one enumerated name gives you colonnades,
spike-runs, stepping-stone paths, picket fences cheaply. Build rhythm by enumerating a single
recognized name. Then deliberately VARY the motif set across games (quarry ledges here, a
bridge+pillar viaduct there, a bounce_pad playground elsewhere) — the name-set a game uses is
what the variety-coverage metric reads, so reusing the same handful collapses diversity.
`[src: harness/core/spritebank.py:99-104,163-192; notes/engines/DESIGNER_AGENT_PLAN.md:70,90]`

## Environmental story lives in title/prompt + flag names
Narrative pressure comes from `meta.title`+`meta.prompt` and the four on_step behaviors, not
new primitives. Name a countdown flag for the fiction (`doom`,`collapse`), a rising line flag
`flood`/`tide`/`molten`. The title carries the setting; the mechanic stays the four behaviors.
`[src: godotworld/examples/escape.spec.json:30; godotworld/SPEC.md:107-109]`

## Aspect ratio to setting; opposite-corner spawn
Match world_size to the motion axis: horizontal traverse/roll → wide ~[1800-2400,700];
vertical descent/climb → tall ~[800,1200-1600]; arena → square-ish ~[1000,1000]. Size worlds
900-2000px wide (the shipped target sat at 1800×700). Place the control body and its goal in
OPPOSITE quadrants so initial `dist` is near the diagonal — never adjacent or axis-aligned,
which would trivialize the solve. `[src: WATCHABLE_DEMOS.md:22-25; BallChase.tscn:46,65; DownFall/down_fall.gd:3]`
