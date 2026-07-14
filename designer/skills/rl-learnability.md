---
id: rl-learnability
kind: skill
created_by: human-seed (fable-orchestrator)
run_id: seed-2026-07-14
wave: 0
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
rationale: Seed the designer with what makes a spec actually LEARNABLE by PPO (not merely certifiable) — monotone progress, dense-in-action-space milestones, observable threats — and the flood_tower anti-pattern to avoid.
provenance: mined from notes/rl_agent/DIFFICULTY_MAP_R1.md, notes/rl_agent/G3_PRIME_SPIKE.md, notes/adversarial/G3_TREE_WIRING.md, scenes/games/v23_showcase/{flood_tower,gem_cavern}, godot_rl_agents_examples (BallChase, CrossTheRoad, JumperHard, FlyBy)
---

# RL learnability

Load when: sanity-checking that a design will TRAIN, not just certify. A world can pass
G0-G4 and still be unlearnable (flood_tower: tree solves it in 124 ticks, yet PPO sr 0.0).
Certifiable ≠ learnable — this skill closes that gap.

## Monotone distance-to-goal must be able to DECREASE
Lay out geometry so the straight-line `dist(hero,goal)` can decrease monotonically along the
intended solution. Every shaped-reward env rewards only progress toward best-so-far distance.
Avoid dead-ends and U-shaped detours where getting closer first requires moving FARTHER — that
non-monotonicity is the single biggest reason a hand-built world becomes un-witnessable by G3
and un-learnable by PPO. `[src: BallChase/Player.gd:98-131; DIFFICULTY_MAP_R1.md]`

## String subgoals ALONG the travel corridor
Place each sub-objective one region apart, sitting just above/on the natural path, so random
exploration physically trips each reward on the way through. This is why gem_cavern's first RL
success lands at ~1832 env-steps and it's stably learnable. A collectible off the corridor that
demands a dedicated detour is a sparse-reward trap. `[src: gem_cavern/game.js:29-36,81-83; G3_PRIME_SPIKE.md:70]`

## Milestone spacing is measured in ACTIONS, not pixels
Learnability depends on how far apart consecutive checkpoints are in ACTION-space. In the
learnable games each next milestone is reachable within ~840-1832 exploration steps (meteor 840,
two_switch 1136, gem 1832). flood_tower's milestones are only 150px apart yet unlearnable because
each requires a precise multi-step jump — "far in action-space." Practical test: from a checkpoint,
could a SHORT RANDOM BURST plausibly satisfy the next one? If it needs a specific timed sequence
(jump-at-the-right-x, then air-steer), it is too far — insert an intermediate milestone or widen
the landing so partial credit is reachable by fumbling. `[src: G3_PRIME_SPIKE.md:70-72; DIFFICULTY_MAP_R1.md:30-31]`

## THE anti-pattern — never stack these three (flood_tower)
This produces a certifiable-but-unlearnable world (passes G0-G4, tree solves in 124 ticks, PPO
sr 0.0, stalls at milestone 2). Diagnosis in the profiler: "no gradient — milestones too far
apart in action-space."
1. a rising-hazard CLOSING TIME WINDOW that punishes exploration (lava climbs 0.9px/physics-step);
2. sparse milestones separated by PRECISE ballistic maneuvers — height-only checkpoints (y>230/
   380/530) each 150px apart but each demanding a full timed jump across a ~420px horizontal ledge
   gap (ledge centers x=240 vs 660);
3. WEAK air control (ground impulse 100 vs air 22) so the mid-jump trajectory is un-correctable.
FIXES: add intermediate ledges (denser milestones) OR coarsen the hop, AND drop the time pressure.
`[src: flood_tower/game.js:2,26-27,45-46,76-90; DIFFICULTY_MAP_R1.md:15,30-31]`

## Tree replay-count is an early warning of no-gradient
The G3 Go-Explore tree's replay count is a difficulty proxy: boulder 2, demolition 5, gem 54,
meteor 10, two_switch 26 — flood_tower needed 128 (the outlier). A witness that takes the tree far
more replays than its peers is the early sign PPO will get no gradient. If your design feels like
it needs a very specific sequence to solve, expect this. `[src: G3_TREE_WIRING.md:39-44]`

## Keep control uniform + clamped (the learnability dial)
How you scale control by state is a direct learnability knob. Uniform horizontal impulse (gem: 120
always) + a `velocity_clamp` keeps the body correctable everywhere → learnable at target. Cutting
air control weak (flood: ground 100/air 22) makes mid-jump trajectories un-fixable and, combined
with big gaps, tips into unlearnable. RULE for target grade: keep control uniform and BOUND it with
`on_step velocity_clamp`, don't starve it in the air. Reserve weak-air-control for deliberate hard/
precision difficulty. `[src: gem_cavern/game.js:50-51,60-63; flood_tower/game.js:87-90]`

## Make threats observable — forward look-ahead sensor sized to reaction time
When a hazard must be dodged, make it observable with a forward-aimed `raycast2d` fan sized to
reaction time, not the whole board (CrossTheRoad exposes exactly 2 rows ahead, 0 behind). Attach
to the hero, `ray_length` ≈ 2x the threat's per-tick travel, `cone_width_deg` ~120-180 pointed
along the progress axis. Too short → dodging unlearnable; too long → obs flooded with irrelevant
hits. `[src: CrossTheRoad/robot_ai_controller.gd:14-23; godotworld/SPEC.md:114-146]`

## Anti-memorization offset — deny the constant policy
Place the control body noticeably OFF the straight line to the goal (and optionally a small
nonzero initial velocity orthogonal to the goal) so a noop/constant-drift cannot succeed — this
guards G1 agency AND forces a sense-and-steer policy. Every reference env spawns off dead-center
and off-axis. `[src: FlyBy/Plane.gd:42-44; JumperHard/Player.gd:132; api_godot.md:159]`

## Interleave hazard + rest bands (staging room)
Between start and goal, interleave hazard lanes with hazard-free REST bands ~1 body-height deep so
the agent can stage its crossing (CrossTheRoad never stacks hazards back-to-back). Scale difficulty
by ADDING hazard bands, not by deleting the rest bands — removing all breathing room makes the
world unsolvable rather than harder. `[src: CrossTheRoad/grid_map.gd:66-72]`
