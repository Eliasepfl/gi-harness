# EXAMPLES STRUCTURE GUIDE — what the 20 godot_rl_agents examples teach our designer

*2026-07-14 · commissioned by Elias after the Godot-only pivot (`GODOT_ONLY_PIVOT.md`). Structure-forensics over all 20 examples in `/home/enaha/GI/godot_rl_agents_examples/examples/`. Sibling to `GODOT_QUALITY_BAR.md` (that one prices the render/vocab path; this one extracts the STRUCTURE these games share, how they differ, where their art comes from, and which we can rebuild today). High-level structure only — the specifics are the model to infer, not to copy.*

Frozen throughout: LLM emits DATA, typed-state verifies, witness replays byte-exact. "Today's bricks" = the three that just landed for the designer — **raycast `sensors`** (`SPEC.md §7b`), **thrust/torque verbs** (= quality-bar brick **B2** heading-control, `GODOT_QUALITY_BAR.md §5`), and the **`contained()` landing predicate**. Still blocklisted (`api_godot.md:133`): **B1** path_mover/oscillator, **B3** spawner/stream, **B4** ordered-latch switch, a 2nd `control:true` body (`SPEC.md §3`), counters beyond flags, colored/pixel obs.

---

## 1. THE UNIVERSALS — non-negotiable ingredients of a good RL game

Present in essentially every example; phrased as design mandates for OUR spec.

- **One controlled body, small action set.** 1 dynamic `control:true` body with a 1–3-dim action set (2 continuous is the mode: move/steer, or thrust+torque). Single-agent is the rule; the 3 exceptions (AirHockey, RobotVolleyball, MultiAgentSimple) mirror ONE controller and we can't express their 2nd body yet.
- **Layered reward = sparse terminal + dense shaping + fixed penalty.** A big terminal success bonus (often scaled DOWN by residual distance/time/speed, e.g. `Car.gd:190-195`, `Lander.gd:170-174`) + a per-step potential-shaping delta that pays only when a distance/velocity/alignment metric strictly improves (`BallChase Player.gd:98-112`, `Racer AIController3D.gd:72-86`) + a small per-step time penalty + a fixed negative on failure/timeout (the ±10 / ±1 / −5 conventions).
- **Success is a compound predicate, never "reach a zone."** Winning latches POSE + STILLNESS + state: be there AND aligned AND slow AND (engines-off / all-collected / correct-label). `NO-SINGLE-ACTION-WIN` is baked into the examples themselves.
- **Per-reset domain randomization.** Every episode re-rolls spawn pose, goal location, and scene contents (which bay is free, goal color, hazard placement) so the policy generalizes instead of memorizing — leans on our reseed-on-reset + seeded rebuild.
- **Egocentric, normalized, time-aware observations with a sensor tail.** Obs live in the agent's own frame (`to_local`/`basis.inverse`), clamp to ~[−1,1], include normalized time (`n_steps/reset_after`) and the goal expressed RELATIVE to the agent, and append the spatial sensor's read (raycast fan / grid / camera) **last**.
- **The SENSOR is the game's identity.** Each example is a harness for exactly one observation abstraction (raycast fan, grid radar, RGB camera, [dir,id] grid). Pick the obs first; the world exists to make that obs meaningful.
- **Bounded arena, breach = negative terminal.** Invisible walls / an `Area` bound; leaving ends the episode with a penalty (our G1 escape check + a failure predicate).
- **Asset-light, lighting-driven look.** Primitives/GLB wearing `StandardMaterial3D` under a procedural sky + ONE shadowed `DirectionalLight3D`; polish is asset/palette/lighting, not code (`GODOT_QUALITY_BAR.md §2`).

---

## 2. THE DIFFERENTIATORS — the distinct ingredient per game, by family

The variety vocabulary. Each family = one load-bearing mechanic that makes those games feel different.

**Vehicle / heading-control (steer to a POSE)** — the LARGEST cluster.
`3DCarParking · 3DLander · FlyBy · HovercraftRacing · Racer · ScoreTheGoal · Ships`. Ingredient: real steering (VehicleBody engine_force+steer, or thrust-along-heading + torque) to a pose target, not translational nudging. Sub-flavors: combinatorial randomized bays (CarParking), attitude+articulation on procedural terrain (Lander), throttle-removed pure 2-DOF waypoint loop (FlyBy), spline-arclength lap progress + 1v1 (Racer/Hovercraft), one-shot commit + color-match target select (ScoreTheGoal), grid-radar hazard sea (Ships). Needs our **B2** (landed today).

**Projectile / ranged combat.**
`FPS · RobotFPS · DefendTheGoal`. Ingredient: a discrete "fire" verb that SPAWNS a damaging body along the aim, an HP/damage economy, and sensors carrying a friend/foe CLASS channel (`ExtendedRaycastSensor.gd:24-32`). DefendTheGoal inverts it to pure avoidance (block a scripted turret's stream). Needs **B3** spawner + a fire verb + (for aim≠travel) an aim brick — all deferred.

**Multi-body coordination / self-play.**
`AirHockey · RobotVolleyball · MultiAgentSimple`. Ingredient: two policies interacting through a shared free body — adversarial mirror self-play (AirHockey MiddleBarrier partition; RobotVolleyball hit-count fault + shot-clock) or cooperative heterogeneous agents where one agent IS the level geometry (MultiAgentSimple flying bridge). Blocked by the one-`control`-body rule; ports collapse to single-agent (opponent → a B1 mover once it lands).

**Timing-gauntlet / dodge.**
`CrossTheRoad · DownFall`. Ingredient: a field of independently-moving hazards (lane-bouncing cars, y-spinning sweepers, spike rollers) + drop-away tiles + bomb rain — survival is TIMING your crossing between periodic movers. Needs **B1** (movers) and, for the rain, **B3**.

**Sorting / conditional logistics.**
`ItemSortingCart`. Ingredient: catch a free-falling body, THEN route it to bin-A-vs-B per its per-drop random category — one motor skill forked by a label observation. Fully expressible today (1-DOF translational + `on_contact` latch + `velocity_clamp`).

**Perception-defined navigation.**
`BallChase · JumperHard · VirtualCamera`. Ingredient: the obs abstraction itself is the challenge — potential-shaped precision-collect with hazard walls (BallChase, our 2D render benchmark), a 144-ray depth grid + self-relocating goal (JumperHard), a 36×36 RGB camera forcing visual localization from wall colors (VirtualCamera — the ONE we cannot sense, raycast returns proximity not color).

**Progression / curriculum.**
`MultiLevelRobot · DownFall`. Ingredient: one persistent episode chains N authored difficulty tiers (MLR's 8 levels + stochastic 1/6 demotion = spaced review; DownFall cycles 4 tiers). Multi-level chaining is out of scope (single-arena episodes); each single level is portable.

---

## 3. ASSET PROVENANCE — banks, licenses, and how to extend our 57-name bank

**Two provenance patterns across the repo.** (a) ~13 games are **bespoke CC-BY-4.0** Blender/GLB by **Ivan-267 / Ivan Dodic** (a godot_rl_agents contributor) — every "custom" example (CarParking, Lander, AirHockey, all the robot games, Hovercraft, ItemSortingCart, MLR, Volleyball, ScoreTheGoal…); source `.blend` ship, reusable **with attribution**, NOT a browsable atlas. A single `robot.glb` (134992 B) is byte-shared across CrossTheRoad / DefendTheGoal / MLR / RobotFPS / VirtualCamera = the repo's house character. (b) The rest lift **CC0 banks** — **Kenney** (BallChase Puzzle Pack; Racer Car Kit + Racing Kit; Ships Pirate Kit; FPS Prototype Textures + Blaster Kit; DownFall/FPS particle sprites), **KayKit** (DownFall animated character, CC0), **Poly Haven** HDRIs + PBR textures (FlyBy, Racer, Ships — CC0), and one **Sketchfab CC-BY** plane (FlyBy, antonmoek).

**What this means for us.** The reusable banks are the CC0 ones — Kenney and Poly Haven. The Ivan-267 art is reference-only (CC-BY, attribution, and it's 3D GLB, not 2D sprites). We already vendored the right tier: `banks/sprites/raw/` now holds **3 CC0 Kenney packs** — `kenney_physics-assets`, `kenney_platformer-pack-redux`, `kenney_abstract-platformer` (all CC0-1.0, `manifest.json`), sliced into **57 part keys** (`slicemap.v1.json`; 46 mapped, 11 still null). This flips `spritebank.available()` True and unblocks the skinning path (`GODOT_QUALITY_BAR.md §1(a)` diagnosis).

**Concrete recommendation to extend the bank.** (1) **Fill the 11 null keys** first — `ice_floor, target_zone, pit_zone, rolling_log, heavy_anvil, ice_block, wedge_block, mine, swing_gate, button_plate, cloud_puff` — from the Kenney packs already vendored before adding any new pack. (2) **The steer cluster (the largest example family) has no vehicle sprite** — add a **Kenney Car Kit / Racing Kit** top-down car + cone/pad/checkpoint set (CC0, `kenney.nl/assets/car-kit` + `racing-kit`, same source Racer uses) so cone-slalom / courier reconstructions can skin their controlled body. (3) **Add `player`→`ball`/`marble` to `spritebank.ALIASES`** (quality-bar A4) — there is still no default sprite for the controlled body, so it always renders flat. (4) Keep the bank **Kenney/Poly-Haven-only** for reuse; treat the Ivan-267 look (matte-body/glossy-trim roughness contrast, HSV-per-category coloring, sway-grass + procedural-cloud shaders) as INSPIRATION for `render.py`, not files to lift.

---

## 4. RECONSTRUCTION LADDER — all 20, ranked expressible-now → needs-brick

"Now" = today's bricks (raycast + thrust/torque/B2 + `contained()`). Each keeps the structural essence but flips the objective (Elias's CarParking→gates→land move).

| # | example | our variant | tier | gating brick |
|---|---|---|---|---|
| 1 | ItemSortingCart | Chute Deflector (catch+route, single item) | **NOW** | none (1-DOF translational) |
| 2 | RobotVolleyball | Keep-It-Over (solo bat-to-target) | **NOW** | none (translational + grounded jump) |
| 3 | BallChase | ordered gate-run / coin-dash | **NOW** | none — closest to native 2D |
| 4 | JumperHard | Beacon Hop (traverse to landing pad) | **NOW** | none; drop self-teleport goal (B1-family) |
| 5 | 3DCarParking | impound-escape (line up + exit a bay) | **NOW** | B2; combinatorial goal = honest stretch |
| 6 | 3DLander | precision-drop (upright dead-stop on pad) | **NOW** | B2 |
| 7 | FlyBy | mail-run flyer (thread gates, then land) | **NOW** | B2 (thrust pinned const); B4 for strict order |
| 8 | HovercraftRacing | cargo rover (collect pads, then dock) | **NOW** | B2; drop opponent+spline |
| 9 | Racer | Courier Circuit (ordered pickups) | **NOW** | B2; drop rival+lap topology |
| 10 | ScoreTheGoal | one-shot bank-shot (freeze after strike) | **NOW** | B2; color-match = deferred stretch |
| 11 | Ships | Salvage Run (collect crates, reach harbor) | **NOW** | B2; health-3 → first-hit=fail (no counters) |
| 12 | AirHockey | puck-herding through gates to a zone | **NOW** | B2/translational; drop 2nd agent+barrier |
| 13 | MultiLevelRobot | Sweep & Exit (collect-all-then-`contained` exit) | **NOW** | none for 1 level; hazards need B1 |
| 14 | CrossTheRoad | Lane Courier (dodge oscillating movers) | needs **B1** | path_mover/oscillator |
| 15 | MultiAgentSimple | Ferry Hop (ride an oscillating raft across) | needs **B1** | oscillating platform |
| 16 | FPS | Arena Tag / king-of-hill (capture reframe) | needs **B1** | moving rival (shooter also needs B2+B3+fire) |
| 17 | RobotFPS | Arena Sweep (tag N patrolling targets) | needs **B1** | patrolling movers (shoot variant needs B3) |
| 18 | DefendTheGoal | Save the Net (block a shot stream) | needs **B3** | spawner/stream |
| 19 | DownFall | Gauntlet Sweep (sweepers + falling bolts) | needs **B1+B3** | movers AND stream |
| 20 | VirtualCamera | Color Compass (visual localization) | needs **NEW** | colored-ray / pixel-obs (beyond B1–B4) |

**Tally: 13 expressible NOW · 4 need B1 · 1 needs B3 · 1 needs B1+B3 · 1 needs a new perception brick.** This is the concrete argument for doing **B1 first** (unlocks 4–6 games), then B3.

**Top-3 pilot candidates** (steer + collect + a distinct non-nav skeleton, per `GODOT_QUALITY_BAR.md §7`):
1. **Cone-slalom (Elias's variant)** — steer a thrust/torque car through an ordered cone-gate slalom and settle on a landing pad. Flagship for ALL three of today's bricks (thrust/torque + raycast obstacle sensing + `contained()` finish); maps to the FlyBy/Racer/Hovercraft cluster = our biggest example family. #1 pick.
2. **BallChase coin-dash** — near-1:1 with our native 2D form AND the render-quality benchmark (`GODOT_QUALITY_BAR.md §2`); potential-shaped precision-collect with hazard walls, single controlled body. Best render-checklist proving ground.
3. **ItemSortingCart Chute-Deflector** — fully expressible with ZERO new bricks; proves a NON-navigation skeleton (catch-then-conditional-route via `on_contact` latch + `velocity_clamp` + sensor zones), guarding against the "steer-to-goal is the only shape" collapse.

---

## 5. HIGH-LEVEL GUIDELINES — distillation for prompt/skill material

*(structure only — never dictate specifics; the model infers those)*

1. One controlled body; a small (1–3) action set; steer/thrust when the game is about a vehicle, translate when it is about catching/herding.
2. Reward = big terminal bonus (scaled down by leftover error) + dense potential-shaping that pays only on strict improvement + small step penalty + fixed failure penalty.
3. Make success a COMPOUND latch: pose AND stillness AND state — never a bare zone touch.
4. Randomize spawn, goal, and scene contents every reset; seed it.
5. Observations: egocentric, normalized to ~[−1,1], time-aware, goal-relative, with the spatial sensor read appended last.
6. Choose ONE sensor as the game's spine (raycast fan is what we have) and build the world so that sensor is decisive.
7. Bound the arena; breaching it is a negative terminal.
8. Pick a distinct differentiator family per game (steer-to-pose / dodge-timing / catch-and-route / precision-collect / one-shot-commit) and make the batch's families differ — this is the anti-sameness lever.
9. Flip the objective when reconstructing (park→exit, race→errand, defend→survive) to stay clear of the shipped specs while keeping the essence.
10. Prefer skeletons expressible now (steer-to-pose, ordered gates, collect-then-`contained`); reach for B1 movers only when the game's soul is a moving hazard.
11. Dress every region: coordinated 3–6 colour palette, skinned sprites for EVERY actor incl. the controlled body, textured ground, 2–5 non-interactive decor (`GODOT_QUALITY_BAR.md §2`).

*Citations: 20 structure cards (evidence arrays); `GODOT_QUALITY_BAR.md §2/§5/§7`; `SPEC.md §3/§6/§7b/§8/§10`; `api_godot.md:133`; `banks/sprites/manifest.json`, `slicemap.v1.json`; `GODOT_ONLY_PIVOT.md`.*
