---
id: certification-survival
kind: skill
created_by: human-seed (fable-orchestrator)
run_id: seed-2026-07-14
wave: 0
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
rationale: Seed the designer with the concrete patterns that pass G0-G4 by construction and the failure signatures (silent at load, surface at replay) to design out up front.
provenance: mined from godotworld/SPEC.md §9, harness/gen/prompts/api_godot.md (common-failures + physics), the showcase games (gem_cavern, two_switch_vault, meteor_gauntlet, flood_tower), godotworld/examples/*.spec.json, BallChase, DownFall, JumperHard
---

# Certification survival (G0-G4)

Load when: finalizing a spec so it survives the verifier. G0-G3 is the universal funnel; G4 is
the adversarial attacker. Most failures are silent at load and only surface when the verifier
REPLAYS the winning run — design them out.

## The containment triple (mandatory for any freely-impulsed body)
Every G4-hardened game with a control body carries the SAME three-layer cage; specs without it
fail G1's no-escape/anti-explosion check:
1. a per-step `velocity_clamp` so per-tick displacement is bounded — the shipped kit uses
   `vx_max 250, vy_min -900, vy_max ~520-540` (gem_cavern clamps vx∈[-450,450], vy∈[-900,500]);
2. a kill-plane failure on EVERY open edge — `pos_x<-20 or pos_x>W+20 or pos_y<-30 or pos_y>H+50`
   — so any drift out ends the episode within the world+~200px escape margin;
3. sealed static `wall` boxes (≥12px, use 20) up to the world top on any side the body can be flung.
Bind the clamp BEFORE relying on the kill-plane — the clamp keeps the fire-distance inside the
margin. `[src: gem_cavern/game.js:56-63,87-93; flood_tower/game.js:36-37,52-57; SPEC.md:196-211; collect2.spec.json:34]`

## Collectible primitive (collect-N)
Three declarative pieces per item:
(a) `on_contact {a:hero, b:item, flag:got_x, once:true}` — latch on first overlap;
(b) `on_step remove_when {flag:got_x, body:item}` — delete the item once latched;
(c) success/checkpoints read ONLY the flag, never the (removed) item body.
Chain N and AND the flags. Place each item as a static `sensor` box/circle just above the corridor.
Exact form: collect2.spec.json latches `got_a`/`got_b`, removes both, success = `flag(got_a) and
flag(got_b) and contacts(marble, goal_zone)`. `[src: collect2.spec.json:24-40; gem_cavern/game.js:43-46,66-83; SPEC.md:72-76,108]`

## Hazards are SENSORS read by a failure predicate — never solids
Model lava/spikes/pits as static `sensor:true` boxes (non-colliding overlap zones). A SOLID hazard
would physically deflect the body and corrupt the run; a sensor just reports overlap. Wire lethality
only through `failure: contacts(hero, lava)` [or spike]. For a bottomless pit prefer a kill-plane
(`pos_y(hero) < threshold`) over a sensor box. `[src: gem_cavern/game.js:27,86; two_switch_vault/game.js:14-15,60-61; escape.spec.json:12,33]`

## Fall-plane failure gives gaps real stakes
For any platforming/descent world, add `failure: pos_y(hero) < Y_floor` with Y_floor just below the
lowest platform (DownFall ends at y<-10). Cheaper and cleaner than a physical kill-body, and makes
every gap genuinely punishing. `[src: DownFall/scripts/player.gd:45-47; JumperHard/Player.gd:194-196]`

## Spawn-clearance budget (bake it in — the runner can't resample)
G0 rejects initial interpenetration analytically. Hand-place bodies so centers are ≥~4x the largest
body radius apart and NO AABB overlaps any wall or other body. Keep every dynamic spawn inset from
the walls. `[src: BallChase/Player.gd:58-75; api_godot.md:156]`

## grounded() needs a SOLID ≥12px non-sensor floor directly below
A body counts as grounded only over a solid, non-sensor floor ≥12px thick. A `segment` floor or a
`sensor` zone does NOT register — the contact flickers and a `when:"grounded(...)"` jump misfires
(a common G3 failure). Stand bodies on static boxes. `[src: api_godot.md:116,161]`

## Predicates: bool + False at t=0 (G2)
`success`/`failure` and every checkpoint must evaluate to a bool and be FALSE at t=0. Don't test a
condition the start state already satisfies. Checkpoints are a 1..6 snake_case ordered map; the
runner latches each at its first true tick, and on the winning path every one must fire at/before the
win. `[src: SPEC.md:148-154; api_godot.md:83,160]`

## G1 agency: no dead action, no single-action win, no escape, byte-determinism
- DEAD ACTION — bind every declared action to a verb with a real effect; an empty `[]` or zero-net
  push fails the efficacy check.
- SINGLE-ACTION WIN — G4 hurls each action alone; a goal reachable by holding one input is rejected.
  Force a reversal / timed stop / a distinct second action mid-level.
- Keep peak velocity `≈impulse/mass` under ~600 px/s (at 60Hz a body over that skips through a 12px
  wall in one step → tunnelling → containment break). `[src: api_godot.md:114,146-148,158-159; SPEC.md:206-208]`

## G3 solidity & goal wiring
- The goal body must be `sensor:true` (not accidentally solid); read the win as `contacts(hero,goal)`.
- SOLIDITY — a body sitting deep inside another on the win path fails; cut impulse magnitudes (peak
  <~600), keep the clamp, keep mass ratios modest (≤~10x). `[src: api_godot.md:162-163; G3_TREE_WIRING.md:51-56]`

## Failure-signature quick table (silent at load → surface at replay)
| signature | fix |
|---|---|
| G0 interpenetration | space bodies apart at build; real thickness; box/circle over poly; polys convex+low-vertex |
| G1 containment escape | perimeter walls ≥12px (+ ceiling if it launches up); `velocity_clamp` the body |
| G1 dead action | bind every action to a real impulse/force/set_velocity |
| G1 single-action win | add a forced reversal / timed stop / second distinct action |
| G2 already-true-at-t=0 | make every predicate read FALSE at start |
| G3 grounded jump never fires | solid non-sensor static box ≥12px under the body |
| G3 goal never true | make the goal `sensor:true`; read `contacts(hero,goal)` |
| G3 solidity | cut impulses (<~600), keep clamp + modest mass ratios; enlarge/slow bodies |
| joint no effect | both named bodies must exist; anchor a `pivot` to a STATIC body |
`[src: api_godot.md:150-164]`

## G4 trivialization guards
G4 refutes cheap wins: a degenerate fling into a low goal sensor was flagged GOAL_ERROR (trivial,
5-tick witness). Keep the win behind a genuine two-axis task and modest impulses so no single kick
reaches it. `[src: G3_TREE_WIRING.md:51-56]`
