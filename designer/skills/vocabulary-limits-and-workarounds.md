---
id: vocabulary-limits-and-workarounds
kind: skill
created_by: human-seed (fable-orchestrator)
run_id: seed-2026-07-14
wave: 0
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
rationale: Seed the designer with exactly what the declarative DSL CANNOT express and the honest passive-physics workaround for each, so it never promises in title/name/prompt what the frozen runner ignores.
provenance: mined from godotworld/SPEC.md, harness/gen/prompts/api_godot.md, banks/parts/v1/parts.json, godot_rl_agents_examples (CrossTheRoad, Racer, DownFall), godotworld/examples/*.spec.json
---

# Vocabulary limits & honest workarounds

Load when: a seed prompt implies motion/behavior you're unsure the runner can do. The rule:
NEVER narrate in title/name/prompt a behavior the runner ignores. Re-cast the seed into a
passive-physics equivalent, or drop it. Commit to ONE mechanic — do not blend three.

## The hard ceiling (unexpressible — never promise these)
`on_step` has ONLY 4 behaviors — `velocity_clamp / timer_flag / remove_when / rising_level` —
and NO verb writes a body's position. So these DO NOT exist:
- continuously-translating / patrolling / moving platforms (no body-mover)
- traffic streams, falling-object rain, any spawner (no spawner)
- per-region gravity / wind / current / updraft pockets (gravity is fixed `(0,-900)`, unretunable)
- buoyancy / swimming (water is a LETHAL sensor, not a fluid)
- breakage / destructible-into-pieces (no fracture primitive; a body can only VANISH whole)
- heading / steering / aim / turn verbs (you only apply impulse/force/set_velocity — no rotation verb)
- strict multi-switch A-then-B lock sequences (flags latch unconditionally, cannot enforce order)
`[src: godotworld/SPEC.md:99-112,220-224; api_godot.md:110,112,133]`

## Re-cast table (turn an unexpressible seed into passive physics)
| seed asks for | re-cast to |
|---|---|
| "moving / patrolling platform" | a `pivot` pin-joint swinging trapeze (bobs, doesn't travel) |
| "dodge the traffic" | time hops across a run of static `sensor` hazard strips |
| "swim / cross the river" | stepping stones (static boxes) over a wide `water` sensor |
| "wind gust / updraft" | a `bounce_pad`, or a launched dynamic body that shoves the hero |
| "falling rocks / rain of X" | a `rising_level` hazard line OR timed hops (no spawner exists) |
| "combination lock, switch A then B" | ONE meaningful `remove_when` gate (not a sequence) |
| "destroy / smash the wall" | `remove_when`-delete the whole `door_slab` (it vanishes, not shatters) |
`[src: api_godot.md:110-112,133; SPEC.md:99-112; godot_rl_agents_examples/examples/{CrossTheRoad,Racer,DownFall}]`

## Joints give scenery-WITH-behavior but only PASSIVE motion
The bank's "mobile" scenery is realized purely as joint + passive dynamics on a STATIC anchor —
these REACT to gravity/contact but do NOT travel a path:
- swinging chandelier/`wrecking_ball`/`hammer` = dynamic bob + `{type:pin, a:anchor(static), b:bob}`
- push-through `swing_gate`/flap = `{type:pivot, a:anchor, b:flap, point:[..]}` hinged at the top
- tipping `seesaw` = plank on a center `pivot` fulcrum
- bobbing lift = `{type:spring, a:base(static), b:platform, rest_length:~120, stiffness:~1200, damping:~80}`
The bank literally marks continuous kinematic travel as DEFERRED. Use joints for hanging/swinging/
bobbing/tipping decor, NEVER for a patrol. `[src: banks/parts/v1/parts.json (moving_platform/wrecking_ball/swing_gate/seesaw); SPEC.md:61-68]`

## Joints are fragile — prototype before committing a level to one
A joint is inert unless BOTH named bodies exist AND its anchor resolves; a `pivot` must anchor to
a STATIC point. Godot-2D pin/spring mapping is APPROXIMATE and jitters at extreme mass ratios — keep
the swung/sprung mass within ~1-10x its neighbors. NEVER make a swing the SOLE path to the goal
unless verified; treat pendulum levels as advanced/optional. None of the three shipped godot specs
needs a joint. `[src: api_godot.md:117,131,164; SPEC.md:61-68]`

## The ONLY in-world structural change is deleting a whole body
`remove_when(flag, body)` on a latched flag is the sole state-change to the world layout. Model a
portcullis/drawbridge/vault as a static `door_slab` removed when a `button_plate`/`lever_switch`
sensor trips an `on_contact` flag (SWITCH-GATED PATH). The door VANISHES rather than breaking —
never narrate breakables/shatter. And because flags latch unconditionally, design ONE gate, not a
combination lock. `[src: banks/parts/v1/parts.json (door_slab); api_godot.md:129; SPEC.md:108]`

## Collectibles / removals read the FLAG, never the removed body
After `remove_when` deletes a body it reads as `pos=vel=0, contacts=false` — any predicate still
querying that body silently breaks. Always read the latched flag, not the gone body. (See
certification-survival for the full collect-N primitive.) `[src: SPEC.md:108,179; collect2.spec.json:24-40]`

## Sensor zones are narrative punctuation (invisible physics that carries a beat)
A `sensor:true` body is an overlap-only zone read via `contacts()`. Use it to place the ONE win
(a single `goal_zone` ~100-130px box, read `contacts(hero,goal_zone)`), a shrine/beacon
(`checkpoint`/`star_target` sensor), a collectible offering (`key_gem` sensor + on_contact flag +
remove_when), or a hazard biome (wide `lava`/`water`/`fire_pit`/`spike` sensor read as `failure`).
The zone is invisible physics that carries the story beat and the milestone.
`[src: banks/parts/v1/parts.json (trigger/hazard categories); api_godot.md:110,142,162]`
