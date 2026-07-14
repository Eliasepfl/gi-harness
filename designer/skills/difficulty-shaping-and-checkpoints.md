---
id: difficulty-shaping-and-checkpoints
kind: skill
created_by: human-seed (fable-orchestrator)
run_id: seed-2026-07-14
wave: 0
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
rationale: Seed the designer with how to hit a TARGET difficulty deliberately (not too easy, not unlearnable) and how to build checkpoints that mark real subgoals and encode order.
provenance: mined from the 6 G4-hardened showcase games (scenes/games/v23_showcase/*), notes/rl_agent/DIFFICULTY_MAP_R1.md, notes/rl_agent/G3_PRIME_SPIKE.md, notes/adversarial/G3_TREE_WIRING.md, JumperHard, godotworld/SPEC.md
---

# Difficulty shaping & checkpoints

Load when: setting the challenge level, writing the `checkpoints` map, or deciding how many
sub-objectives a game has. The six showcase games form ONE graded corpus that all pass G0-G4
yet spread across PPO difficulty — use them as calibration anchors.

## The graded-corpus anchors (aim for TARGET)
| game | grade | PPO success-rate | what makes it that grade |
|---|---|---|---|
| boulder_run | EASY | 1.000 | success = one x-threshold band along ONE axis |
| demolition_yard | EASY | 0.969 | single mostly-monotone push |
| **gem_cavern** | **TARGET** | **0.656 (stable @500k & 1.2M)** | compound: collect-4 AND exit, off-axis |
| meteor_gauntlet | HARD | 0.062→0.625 @1.2M | reactive/timed pressure |
| two_switch_vault | HARD | 0.0 @500k | ordered 2-switch gate + precise spike-jumps |
| flood_tower | NOT-LEARNABLE | 0.0 (stalls on_l2) | 3 learnability-killers stacked (see rl-learnability) |
`[src: notes/rl_agent/DIFFICULTY_MAP_R1.md:8-15]`

## Target-difficulty recipe (gem_cavern anatomy)
To land at TARGET, not easy, not hard: **3 orthogonal actions** (right/left/jump), a moderate
**~1800×700** world, **exactly 5 ordered checkpoints**, **NO time pressure**, a solution path
of **~100-200 decision ticks**. The core move is a **COMPOUND objective** — success requires
collecting 4 separated items AND then reaching an exit — so no single repeated action can win.
String the sub-objectives ALONG the natural travel corridor (one item per region, sitting just
above the path) so RL exploration physically trips each reward on the way through; this is why
gem_cavern's first RL success lands fast (~1832 env-steps) and its grade is stably learnable.
`[src: scenes/games/v23_showcase/gem_cavern/game.js:3-10,29-36,81-83; DIFFICULTY_MAP_R1.md:12,32; G3_PRIME_SPIKE.md:70]`

## Compound objective is what separates TARGET from EASY
The gap between easy and target is the **dimensionality of the success predicate**, not world
size. EASY = success collapses to ONE spatial condition on ONE axis (boulder_run:
`gate_open→rolling→crossed→at_blockade→delivered` are all x-thresholds, so a monotone push
wins). TARGET = success is a **conjunction of ≥2 subgoal TYPES** placed OFF a single axis
(gem_cavern: `allGems() AND contacts(exit)` — 4 collectibles at 4 heights plus a jump-gated
lava gap, so a monotone policy misses gems). RULE: for target, make success `collect-set AND
reach-zone`; for easy, one reach-zone threshold.
`[src: gem_cavern/game.js:81-83,97-105; boulder_run/game.js:51-55,68-76]`

## Checkpoints mark REAL physical subgoals
Use ~5 checkpoints (SPEC allows 1-6; all six showcase games + all three godot specs use
exactly 5). Each must be a DISTINCT physical state on the intended progression, listed in
insertion order = intended order, evaluate to a bool, and be **False at t=0** (G2 rejects
otherwise). Map them 1:1 to the real subgoals: gem_cavern = `got_gem1..got_gem4 + at_exit`;
boulder = the physical stages `gate_open→rolling→crossed_bridge→at_blockade→delivered`. They
are the harness's stuck-diagnosis probe — make them meaningful stages, NOT four restatements
of success ("moved right a bit"). `[src: gem_cavern/game.js:97-105; boulder_run/game.js:68-76; SPEC.md:150-154]`

### Event milestones vs region milestones
- EVENT (got a gem, threw a switch) → use a flag-read or `contacts(...)`.
- REACHED-A-REGION → use a `pos_x`/`pos_y` threshold.
Both patterns appear in the shipped specs (Quarry Shelves mixes `pos_y>130 and pos_x>290`
region checks with a final `contacts(marble,goal_zone)`).
`[src: godotworld/examples/traverse.spec.json:35-41; collect2.spec.json:41-46]`

## Encode ORDER by gating later milestones on earlier flags
Flags latch unconditionally, so you cannot force strict A-then-B with flags alone. Encode
intended order in the predicate: `cleared_gap1 = flag("a") and pos_x("hero")>720`. Ordered
subgoal gating turns one long episode into dense ordered progress — keep a single frontier
subgoal active (JumperHard keys the whole distance signal to just the current pad), chain 3-6
sensor waypoint zones, latch each with `on_contact`, and require the final checkpoint in
`success`. `[src: JumperHard/Player.gd:21,146-149,176-182; two_switch_vault/game.js:67-76]`

## Switch/gate as a difficulty lever (one = target, two-ordered = hard)
A gate is: `on_step` — if `contacts(body,plate)` then set flag + `remove_when` the door. Make
the door a TALL static box the body can't jump over (height ~240) so the switch is the only
route. To push toward HARD, add STRICT ORDER (`if flag(a) and not flag(b) and contacts(body,
plate_b): set flag(b)`) and gate the goal on both — but note the cost: ordered two-switch
gating + two precise spike-jumps grades HARD (sr 0.0 @500k, stalls between switch_a and
cleared_gap1). Use one switch for target; two ordered switches only when you intend hard.
`[src: boulder_run/game.js:45-48; two_switch_vault/game.js:17-19,48-52; DIFFICULTY_MAP_R1.md:14]`

## Reachable-gap sizing & the difficulty arc
Size the gap between consecutive platforms/milestones to ~1.5-2x the body width and strictly
within one action's ballistic reach (JumperHard: 8×8 pads 12-16 units apart). Under gravity
-900 a hop of impulse J peaks at ≈J²/1800 px — compute reach BEFORE placing the next platform,
or G3 finds no witness. Build an ESCALATION arc across the declared world (each region harder:
wider gaps, thinner ledges, tighter timing), not a flat plateau. Raise difficulty by ADDING
ordered subgoals — never by shrinking tolerances into pixel-precision.
`[src: JumperHard/Player.gd:166-173; api_godot.md:139]`

## Wide one-axis worlds tend EASY — add vertical structure for difficulty
Boulder's 2000px single-axis traverse is the longest witness (199 ticks) yet grades EASY.
Don't reach for difficulty by widening; add vertical structure (a jump-gated gap, a shelf to
climb) so the success predicate leaves a single axis. `[src: G3_TREE_WIRING.md:39-44; WATCHABLE_DEMOS.md:22-25]`
