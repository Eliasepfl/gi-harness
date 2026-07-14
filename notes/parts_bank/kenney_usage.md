# How real projects USE our Kenney packs — usage study → prompt orientation (2026-07-13)

> Scope: research-only. Question asked (Elias): our generation prompts are "pas très
> bons mais au moins restrictifs" — how do real humans compose games out of *exactly*
> the three CC0 packs we vendor (`banks/sprites/manifest.json`: **Physics Assets**,
> **Platformer Pack Redux**, **Abstract Platformer**), so we can orient our prompts
> with real composition vocabulary instead of guessing?
> Companion docs: `notes/parts_bank/assets.md` (the sprite-vs-physics finding),
> `banks/parts/v1/parts.json` (our 30 bank NOUNS), `harness/gen/gamegen.py`
> (`_SYSTEM_PROMPT` + the DESIGN block this study feeds).
> Two evidence streams below: (A) real projects/tutorials using these packs, verified
> against primary sources; (B) the packs' OWN sprite names, read straight from the
> vendored Sparrow XML under `banks/sprites/raw/`. Naming (B) is ground truth (local
> files), not web-scraped.

---

## TL;DR — the load-bearing findings

1. **Everyone composes the same skeleton.** Across Phaser, Arcade, pygame and LÖVE
   projects the level is: *one ground strip → a few floating platforms → short hazard
   runs → sparse pickups → a single goal marker.* That skeleton, not any genre, is the
   reusable orientation.
2. **The pack names ARE a design vocabulary.** Kenney ships parts already named for
   their game role (`boxCrate`, `spikes`, `flagRed`, `coinGold`, `doorOpen`, `spring`,
   `leverLeft`, `switchGreen`, `saw`, `lava`, `weight`, `chain`). Real projects keep
   those names verbatim. Most map 1:1 onto our bank NOUNS — so we can hand the model a
   *lexicon* that is simultaneously Kenney-authentic and bank-valid.
3. **Levels are authored in Tiled, almost universally** (LDtk second). The recurring
   pattern is **semantic layers**: a tile layer for solid terrain, an *object* layer
   per interactive kind (`Coins`, `Spikes`/`Don't Touch`, `Moving Platforms`). That
   layer taxonomy is a clean checklist of the *categories* a level should contain.
4. **Several beloved idioms FIGHT our oracles** — decorative overlap, ground-as-many-
   tiles, tile-index coin swarms, path-travelling platforms, breakable/destructible
   blocks, autonomous enemies. Each is called out in §7 so we orient the model *toward*
   the compatible idioms and *away* from the traps.

---

## 1. The finds (source-verified)

| # | Project / source | Stack | Pack(s) | What it demonstrates |
|---|---|---|---|---|
| 1 | **Morrism1/phaser-platformer** | Phaser 3 + **Matter.js** + Tiled | Kenney sprites | player ("Kim"), collectible = *cherries* (+10), hazard = *spikes* (−40% hp), *health kits* (+10). Tilemap base layer + hazard placement. Physics engine = Matter (not Arcade). |
| 2 | **AGabtni/Kenney-s-World** | JS (web) | **Platformer Pack Redux** | a **2D endless-runner**: same art, genre swapped by *scrolling the world past a fixed runner* — shows one pack affords more than one genre. |
| 3 | **StackAbuse "Phaser 3 and Tiled: Building a Platformer"** | Phaser 3 + Tiled | Kenney tilesheet | tileset `platformPack_tilesheet.png` (98 tiles, 7×14), embedded as `kenny_simple_platformer`; **`spike.png` on a dedicated object layer**; layers **`Platforms`** (solid) + **`Spikes`**; `setCollisionByExclusion`; spikes fire a `playerHit` callback → respawn. Ground + floating platforms + spike hazards + goal. |
| 4 | **GameDevAcademy "Mario-style Phaser 3"** | Phaser 3 + Tiled JSON | Kenney | tiles `tiles.png` (70×70); player atlas frames `p1_walk01..11` + `p1_stand`; collectible `coinGold.png`; layers **`World`** (terrain) + **`Coins`**; coins are *tiles* (index 17) collected via `setTileIndexCallback` — the "collectible-as-tile" idiom. |
| 5 | **"Kenney Platformer Base Pack for Tiled"** (OpenGameArt) | Tiled `.tsx` | Kenney platformer | **70×70** tiles; ships an external tileset **with Tile Collisions** baked in; "hills" made tileable. Confirms colliders are HAND-AUTHORED into Tiled, never shipped by the pack (matches `assets.md`). |
| 6 | **pvcraven/pygame_platformer** | pygame | Kenney | `tiles_spritesheet.png`+`.xml`, `p1_walk.png`; levels in **Tiled `.tmx`**; code split `player.py` / `platforms.py` / `levels.py` — solid terrain vs obstacles vs level data as separate concerns. |
| 7 | **gonvalhector/finite-platformer** (CS50 GD) | LÖVE2D + STI + Windfield | Kenney art + Input-Prompts | entity kinds `Coin`, `Crate`, `Enemy`, `Goal` (an *ice-cream* end marker), `Heart`, `Player`. Level objectives escalate: reach goal → collect all coins → clear all enemies → **reach an ELEVATED goal** → combined. Goal-at-elevation is a recurring difficulty knob. |
| 8 | **Arcade "Platformer" tutorial** (api.arcade.academy) | Python Arcade + Tiled | Kenney (Arcade's stock art *is* Kenney) | teaches, verbatim, "add **coins, ramps, moving platforms, enemies**"; levels are Tiled maps read by **named layers** (canonical set: `Platforms`, `Coins`, `Don't Touch`, `Moving Platforms`, `Ladders`, `Background`, `Foreground`). The **`Don't Touch`** layer = the hazard-row idiom named. |
| 9 | **KenneyNL/Starter-Kit-3D-Platformer** | Godot | Kenney (author's own) | canonical mechanic set from the source himself: **double-jump** controller, **collectable coins**, **falling platforms**, goal. "Falling platform" = terrain that reacts to being stood on. |
| 10 | **Kenney KB "Importing and using tilemaps"** | any | all packs | each pack ships a `tilemap.txt` giving **tile width/height/spacing** only — the pack's own guidance is import mechanics, *zero* design/composition advice. The composition knowledge lives in the community projects above, not in Kenney docs. |
| 11 | **Kenney Physics Assets** product semantics | Box2D/physics engines | **Physics Assets** | "for physics games like **Angry Birds / Totem Destroyer**"; **55 material elements × 3 variants** + **damaged/broken tiles**. Read from our XML: `elementWood/Metal/Stone/Glass/Explosive/Debris` families in a grid of sizes (70², 140×70, 220×70, 70×140, 70×220, 140×140, 140×220 …) → **stackable blocks in many footprints** for towers/structures, plus explosives, breakable glass, and pre-broken debris. |
| 12 | **Platformer level-design canon** (idTech, RetroStyle write-ups) | genre-agnostic | — | the three interactive-element classes everyone teaches: **platforms** (solid / moving / breakable / bouncy), **obstacles**, **hazards**; plus *multi-threading* (a safe low-reward path beside a risky high-reward path) and *enemy placement for intensity pacing*. Vocabulary we can borrow neutrally. |

---

## 2. Naming vocabularies (what humans actually call these parts)

### 2A. From the packs' own XML (ground truth, read from `banks/sprites/raw/`)

**Platformer Pack Redux — `spritesheet_complete.xml`** (the richest role vocabulary):
- **terrain families**, each with a `*Mid / *Left / *Right / *Center / *Cliff_/*Hill_/*Half_/*Corner_` tile grammar: `grass*`, `dirt*`, `stone*`, `sand*`, `snow*`, `planet*`. (The `Mid/Left/Right` suffixes are how a *strip* is built from tiles.)
- **props / blocks**: `boxCrate`, `boxCrate_single/_double/_warning`, `boxItem`, `boxCoin`, `boxExplosive`, `brickBrown/Grey`, `rock`, `weight`, `weightAttached`, `chain`, `bridgeA/B`, `fence`.
- **hazards**: `spikes`, `saw` (+`saw_move`, `sawHalf`), `lava` (+`lavaTop_high/low`), `water`, `fireball`.
- **triggers / interactables**: `flag{Red,Blue,Green,Yellow}1/2/_down`, `doorOpen_top/_mid` + `doorClosed_*`, `signExit`, `key{Blue…}` + `lock{Blue…}`, `switch{Blue…}` + `_pressed`, `lever{Left,Mid,Right}`, `spring` + `sprung`, `ladderTop/Mid`.
- **pickups**: `coin{Gold,Silver,Bronze}`, `gem{Blue,Green,Red,Yellow}`, `star`, `hudCoin`, `hudKey_*`, `hudHeart_full/half/empty`.
- **agents/enemies**: `alien{Beige,Blue,Green,Pink,Yellow}_{stand,walk1/2,jump,duck,climb,hit,swim}` (player); enemies `bee, fly, frog, slime{Blue,Green,Purple}, snail, worm, fishBlue, ladybug, mouse, barnacle` (+`_move/_dead/_hit`).

**Physics Assets** — material blocks `element{Wood,Metal,Stone,Glass,Explosive,Debris}NNN`, plus `spritesheet_explosive` (bombs/TNT) and `aliens`. The *variants* encode intact → damaged → broken.

**Abstract Platformer** — `tile{Blue,Brown,…}_NN` (64×64 geometric tiles) + `enemies`, `items`, `players`, `other` sheets. Same roles, flat/geometric skin (matches our primitive renderer *well* — see §6).

### 2B. From the projects (role words that recur in code/design)

`player`, `ground`/`world`, `platform` (solid/floating/moving/falling), `crate`/`box`,
`coin`/`gem`/`star` (pickup), `spike`/`saw`/`lava`/`water` (hazard, often on a
`Don't Touch`/`Spikes` layer), `flag`/`door`/`goal`/`exit` (goal), `spring`/`bounce`
(launcher), `key`+`lock`, `switch`/`lever`/`button` (trigger), `enemy`, `ladder`,
`heart`/`health` (life).

### 2C. Alignment to OUR bank NOUNS (`banks/parts/v1/parts.json`)

| Kenney/community word | Our bank part | Notes |
|---|---|---|
| ground / `grassMid` strip / `World` layer | **`ground`**, `platform_wide` | ONE strip body, not many tiles (§7.2) |
| floating platform / ledge | **`platform_thin`**, `platform_wide`, `ledge`, `step_block` | |
| ramp / hill / slope | **`ramp15`**, `ramp30` | |
| wall / boundary | **`wall`** | |
| `boxCrate`, `boxCrate_double`, `brick` | **`crate_light`**, `crate_heavy` | our crates topple, don't shatter (§7.5) |
| `rock`, boulder | **`boulder`** | |
| barrel / drum | **`barrel`** | |
| plank / board / `bridgeA` | **`plank`** | |
| ball / `weight` | **`ball_light`**, `ball_dense`, `ball_bouncy` | |
| `puck` / disc (ice) | **`puck`** | |
| `spikes`, `Don't Touch` row | **`spike_strip`**, `spike_pit` | sensors, non-physical (§7.4) |
| `lava`, `water` (kill) | **`lava_pool`** | sensor |
| `saw` | **`saw_disc`** | sensor |
| `weight`+`chain` / wrecking crane | **`wrecking_ball`** | pin joint, calibrated from day2_wrecking |
| pendulum / swing | **`pendulum`** | |
| seesaw / `lever` / teeter | **`seesaw`** | |
| moving platform (h) / trapeze | **`moving_platform_h`** | **sways**, doesn't travel (§7.6) |
| elevator / falling platform (v) | **`moving_platform_v`** | **bobs on a spring**, no path travel (§7.6) |
| `flag`, `doorOpen`, `signExit`, goal | **`goal_zone`** | sensor win region |
| `switch`/`button`/pressure plate | **`pressure_zone`** | sensor + flag |
| checkpoint gate | **`checkpoint_zone`** | milestone sensor |
| coin/gem/star target, aim target | **`target_zone`** | small sensor |

**No bank NOUN for:** `key`/`lock`, `ladder`, `spring`/`sprung` launcher, breakable
glass, autonomous `enemy`. These are *behaviours to compose in code* (flags, impulses,
on_step), not parts — see §7.

---

## 3. Recurring level-composition idioms (the actual answer to "how humans build with these")

Distilled from finds #3, #4, #7, #8, #9, #12 and the pack tile-grammars:

- **The strip + floats + hazards + goal skeleton.** Nearly every level = a continuous
  ground strip, 2-5 floating platforms above it at jump-reachable spacing, a few short
  hazard runs, sparse pickups, one goal at the far side and usually *elevated*.
- **Semantic layering.** Interactive kinds live on their own named layers (`Platforms`,
  `Coins`, `Spikes`/`Don't Touch`, `Moving Platforms`, `Ladders`) — i.e. each *role* is
  a distinct set of objects, never mixed into the terrain blob.
- **Ground built from a `Left–Mid…Mid–Right` run.** The tile grammar itself says ground
  is a *repeated middle* capped by end tiles → conceptually a single horizontal surface.
- **Hazard rows, short.** Spikes/saws/lava appear as *runs of a few* with safe gaps, on
  the floor or on a ledge lip, tuned to force a jump — not as wall-to-wall carpets.
- **Elevation as difficulty.** Raising the goal, or gating it behind a high platform, is
  the standard way to make a level "harder" (finds #7, #9).
- **Pickups as a secondary objective / breadcrumb trail.** Coins line the intended path
  or bait a risky detour (multi-threading, #12). Collecting *all* of them is a common
  win condition variant (#7).
- **Launcher beats.** `spring`/bounce pads and seesaw/lever catapults convert a step
  into a jump — a momentum event placed deliberately.
- **Contraptions from the physics pack.** Crate **towers/totems** to knock down or keep
  standing; a `weight`+`chain` **wrecking pendulum**; a **seesaw/lever** to fling a ball;
  a **stack** that must survive or collapse onto a target. Goal = a body reaching/resting
  in a target region, or a structure's state (standing/toppled).
- **Gate → key → door chains.** `switch`/`lever`/pressure-plate arms a `door`/`flag`;
  the multi-stage lock is the classic "do X then reach Y" structure (maps cleanly to our
  `pressure_zone` + flags + `goal_zone`, and to checkpoints).
- **Moving/falling platform as a timing gate.** A platform that moves (or drops when
  stood on) forces a timed traversal — a rhythm beat between two static footholds.
- **Enemies/hazards for intensity pacing** (#12): sprinkle to raise tension between
  rest points; never a continuous wall.
- **Bridges & planks over gaps.** `bridgeA/B`/`plank` span a pit — a see-through
  foothold that may wobble (dynamic `plank`) or hold (static).
- **Backgrounds are inert.** Bushes, clouds, fences, signs are pure decoration behind
  the play plane — they carry no collider (and in our world must not be spawned as
  overlapping bodies, §7.1).

---

## 4. Genres each pack naturally affords

- **Physics Assets** → **physics-puzzle / construction-destruction**: Angry-Birds-style
  launch-and-knockdown, Totem-Destroyer block-removal, tower stacking/balancing,
  demolition, contraption/Rube-Goldberg. Material blocks in many footprints + explosives
  + breakable glass + debris. *Best fit for our joint/impulse substrate and our
  day2_wrecking-calibrated parts.*
- **Platformer Pack Redux** → **side-view platformer** (traverse, jump, collect, avoid,
  reach flag) and its cousins: **auto-runner** (#2), collectathon, key-and-door puzzle-
  platformer, single-screen obstacle course. Richest role vocabulary (flags, doors,
  keys, switches, levers, springs, saws, enemies).
- **Abstract Platformer** → same platformer/arcade affordances in a **flat geometric
  skin**; also clean for **minimal arcade** (dodge, reach, timing). Its flat look is the
  closest match to our current primitive renderer, so it is the *cheapest* pack to skin
  demos with (§6).

---

## 5. Machine-readable level formats they pair with

- **Tiled (`.tmx`/JSON) is the default** across #3, #4, #6, #8 and the OGA `.tsx` (#5).
  Pattern: an embedded/external tileset (70×70 here) + a **tile layer for terrain** +
  **object layers for interactives**, colliders either baked into the `.tsx` (Tiled
  Tile Collisions, #5) or set in code by tile-index exclusion (#3, #4).
- **LDtk** is the modern alternative (entity defs + typed fields + tags); pairs with the
  same packs. Per `assets.md`, LDtk gives great metadata ergonomics but **no per-entity
  collider geometry** (IntGrid only).
- **Neither ships physics** — mass/friction/restitution/colliders are always
  hand-authored, confirming `assets.md`. **We do not consume these formats**; we lift
  their *semantic-layer taxonomy* as a category checklist for prompts, nothing more.

---

## 6. Renderer note (cheap win, non-blocking)

The **Abstract Platformer** flat-geometric tiles (`tileBlue_/tileBrown_…`, 64×64) are
the closest existing art to `render.py`'s colored-primitive style. If we ever want demo
GIFs to read as "real games" with near-zero effort, that pack skins our boxes/circles
most naturally — no need for the detailed Redux art. (Cosmetic only; changes zero
oracle bits, per `assets.md` §3.)

---

## 7. CRITICAL — idioms that FIGHT our oracles (orient the model AWAY from these)

These are popular in the real projects but would trip G0-G3 / the bank invariants /
the 14-body & agency rules. Each pairs a trap with the compatible substitute.

1. **Decorative overlap.** Real levels layer bushes/plants/signs/`grassCenter` *on top
   of* terrain and overlap background tiles freely. Our world forbids initial overlap
   ("avoid initial overlaps"; bank `no_self_penetration`; G0 static-overlap). → **Every
   spawned body must be a spaced, non-overlapping collider; decoration is not modeled.**
2. **Ground as many tiles.** Kenney ground is a `Left–Mid×N–Right` run of 70×70 tiles;
   a screen is dozens of tiles. We cap at **14 bodies**. → **Ground = one `ground`/
   `platform_wide` strip body; a "floor of tiles" is a single wide box.**
3. **Coin/collectible swarms as tiles** (#4 tile-index 17; #7 "collect all coins").
   Dozens of coins blow the body budget and add little agency. → **A handful of
   `target_zone`/sensor pickups (≤ a few), counted via a flag; not a field of coins.**
4. **Hazards physically kill.** In Kenney art spikes/saws/lava are *solid-looking*, and
   tutorials give them collider bodies. Our hazards are **sensors** (`is_sensor`,
   `non_lethal_physically`); lethality is a *rule the game reads on contact*, bodies pass
   through. → **Spawn `spike_strip`/`lava_pool`/`saw_disc` as sensors; wire death in
   `failure()`/`on_contact`, never as a solid wall.**
5. **Breakable / destructible / explosive blocks & glass** (Physics pack `Glass`,
   `Explosive`, `Debris`; #1 destructible crates). We have **no fracture/health model**;
   crates are rigid dynamics. → **Use topple / knock-over / displace, not shatter;
   "destroy the tower" = make it fall or leave its region, not delete bodies.**
6. **Path-travelling / continuously-moving platforms & elevators** (#8 Moving Platforms,
   #9 falling platforms). Our `moving_platform_h` **sways** (pin) and `moving_platform_v`
   **bobs on a spring** — *continuous kinematic travel is explicitly deferred* (see the
   part summaries). → **Frame moving platforms as sway/bob timing beats, or drive motion
   from `on_step`; do not assume a platform that translates along a track.**
7. **Autonomous patrolling enemies** (#7 `Enemy`, Redux `slime/bee/frog…`). We have no
   AI agents; only the ONE controlled body plus scripted bodies. → **"Enemies" = moving
   hazards driven from `on_step` (or static sensors); never self-deciding actors.**
8. **Continuous hazard carpets / unavoidable goals.** A wall-to-wall spike floor, or a
   goal you reach by holding one direction, reads great visually but is exactly what the
   coming **G4 avoidance probe** and the **single-action-win** check flag as degenerate.
   → **Hazards in short runs of 2-4 with gaps; the goal must need a deliberate multi-step
   sequence (agency rule: idling or one repeated action must never win).**
9. **Ladders / climbing** (#8 `Ladders`, Redux `ladderMid`). No ladder mechanic in the
   substrate. → **Model vertical progress with platforms/jumps/`moving_platform_v`, not
   a climb state.**
10. **Springs as a tile you touch** (Redux `spring`/`sprung`). No spring body-type. →
    **Emulate with high `elasticity` (`ball_bouncy`) or an impulse fired on contact via
    `on_step`/`on_contact`.**

---

## 8. PROMPT-ORIENTATION (the deliverable core)

Guidance for `gamegen._SYSTEM_PROMPT` / the DESIGN block. Phrased as **neutral design
vocabulary**, never as a genre mandate (the prompt already forbids defaulting to a
platformer — this must *widen* the palette, not narrow it to platformers).

### 8.1 Twelve composition idioms (neutral, bank-aligned, oracle-safe)

1. **A surface to act on** — a single wide static strip (`ground`/`platform_wide`) is
   the usual floor everything rests on; make it one body, not a row of tiles.
2. **A few reachable footholds** — 2-5 spaced static ledges (`platform_thin`/`ledge`/
   `step_block`) at jump-reachable gaps, not a dense grid.
3. **Short hazard runs** — hazards (`spike_strip`/`lava_pool`/`saw_disc`) read best in
   runs of 2-4 with safe gaps that force a deliberate move; never a continuous carpet.
4. **A goal marker, often elevated** — one sensor region (`goal_zone`) at the far side
   or up high; elevation is the standard difficulty knob.
5. **Sparse pickups as a path signal** — a few sensor targets (`target_zone`) tracing
   the intended route or baiting a risky detour; count them with a flag, don't swarm.
6. **A launcher beat** — a `seesaw`/`plank` catapult, a `spring`-like impulse, or a
   bouncy body (`ball_bouncy`) that converts a small input into a big move.
7. **A knock-down / balance structure** — a stack of `crate_light`/`crate_heavy`/
   `boulder` to topple, keep standing, or collapse onto a `target_zone` (physics-pack
   flavour); win = a body reaching a region or a structure's rest state.
8. **A swinging demolisher** — a `wrecking_ball`/`pendulum` (pin joint) whose timed
   swing clears or strikes something.
9. **A teeter puzzle** — a `seesaw` where landing weight off-centre lifts or launches
   another body.
10. **A timed traversal** — a `moving_platform_h` (sway) or `moving_platform_v` (bob)
    between two static footholds as a rhythm gate; motion may also be driven in
    `on_step`.
11. **An arm-then-reach chain** — a `pressure_zone`/switch that sets a flag which
    unlocks the win, so the goal needs *do X then reach Y* (natural checkpoints).
12. **A bridge/lever over a gap** — a `plank`/`bridge` spanning a `spike_pit` that may
    hold (static) or wobble (dynamic).

### 8.2 Naming lexicon to hand the model (Kenney-authentic ⇄ bank-valid)

> "Prefer these role words; each maps to a calibrated bank part. Use `world.add()` only
> when a role has no part."

- **surfaces:** ground, platform (thin/wide), ledge, step, ramp, wall
  → `ground` `platform_thin` `platform_wide` `ledge` `step_block` `ramp15` `ramp30` `wall`
- **movable props:** crate (light/heavy), barrel, plank/board, ball (light/dense/bouncy),
  boulder/rock, puck
  → `crate_light` `crate_heavy` `barrel` `plank` `ball_light` `ball_dense` `ball_bouncy`
  `boulder` `puck`
- **hazards (sensors):** spikes/spike-pit, lava/water, saw
  → `spike_strip` `spike_pit` `lava_pool` `saw_disc`
- **contraptions (jointed):** wrecking-ball, pendulum, seesaw/lever, moving platform
  (sway/bob) → `wrecking_ball` `pendulum` `seesaw` `moving_platform_h` `moving_platform_v`
- **triggers (sensors):** goal/flag/door/exit, switch/plate/button, checkpoint gate,
  target → `goal_zone` `pressure_zone` `checkpoint_zone` `target_zone`
- **compose in code, not as parts:** key+lock, spring launch, breakable, enemy, ladder,
  score/coins-collected (use flags, impulses, `on_step`, `on_contact`).

### 8.3 Five drop-in "orientation lines" for the DESIGN block

Neutral, quotable, non-prescriptive — inject as hints, each ≤1 line:

1. "A world usually has one wide surface to rest on, a few spaced footholds above it,
   and a single goal region — build the skeleton first, then the twist."
2. "Hazards read best in short runs of two to four with gaps between; a wall-to-wall
   hazard, or a goal reached by one held input, is a degenerate design."
3. "Goals and switches are sensor regions (flags/doors/plates), typically at elevation
   or gated behind a step the player must set up — winning should take a sequence."
4. "Hazards, goals, switches and checkpoints pass through bodies (they are detectors);
   make death or victory a RULE you read on contact, never a solid wall."
5. "Contraptions come from joints: a swing (pin), a teeter (pivot), a bob (spring); a
   crate stack topples or holds — it never shatters, so win on position or rest-state."

---

## 9. Sources (verified)

- Kenney packs (product pages): Physics Assets https://kenney.nl/assets/physics-assets ·
  Platformer Pack Redux https://kenney.nl/assets/platformer-pack-redux · Abstract
  Platformer https://kenney.nl/assets/abstract-platformer
- Kenney KB, importing tilemaps —
  https://kenney.nl/knowledge-base/game-assets-2d/importing-and-using-tilemaps
- Morrism1/phaser-platformer (Phaser3+Matter+Tiled) — https://github.com/Morrism1/phaser-platformer
- AGabtni/Kenney-s-World (runner, Redux) — https://github.com/AGabtni/Kenney-s-World
- StackAbuse Phaser3+Tiled platformer — https://stackabuse.com/phaser-3-and-tiled-building-a-platformer/
- GameDevAcademy Mario-style Phaser3 — https://gamedevacademy.org/how-to-make-a-mario-style-platformer-with-phaser-3/
- Kenney Platformer Base Pack for Tiled (colliders in `.tsx`) — https://opengameart.org/content/kenney-platformer-base-pack-for-tiled
- pvcraven/pygame_platformer — https://github.com/pvcraven/pygame_platformer
- gonvalhector/finite-platformer (LÖVE2D+STI+Windfield) — https://github.com/gonvalhector/finite-platformer
- Arcade platformer tutorial (Kenney art, Tiled layers) — https://api.arcade.academy/en/latest/tutorials/platform_tutorial/index.html
- KenneyNL/Starter-Kit-3D-Platformer (double-jump/coins/falling platforms) — https://github.com/KenneyNL/Starter-Kit-3D-Platformer
- Phaser Editor 2D — Kenney + Tiled tutorial — https://phasereditor2d.com/blog/2017/10/kenny-assets-tiled-map-phaser-editor-tutorial-by-wachirawut-thamviset/
- Platformer level-design canon — https://www.idtech.com/blog/platformer-level-design-made-simple ·
  https://retrostylegames.com/blog/platformer-level-design-tips/
- Local ground truth: pack XMLs under `banks/sprites/raw/**/Spritesheet(s)/*.xml`;
  our NOUNs `banks/parts/v1/parts.json`; sprite-vs-physics finding `notes/parts_bank/assets.md`.
