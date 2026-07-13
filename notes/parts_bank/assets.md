# Parts Bank — the asset landscape (research, source-verified, 2026-07-13)

> Slice of the "bank of parts" exploration. Question asked: what sprite/asset
> libraries and what physics-part definition formats exist in the wild, and which
> (if any) are worth pulling into a machine-usable BANK OF PARTS that the
> generation model can reference by name instead of hand-calibrating every body?
> Companion docs: `CONTRACTS.md` §1 (the `World.add()` substrate a bank entry must
> map onto), `OBJECTIVES.md` (scaling = reduce load on the code-writing model).
> All claims below are checked against primary sources (linked at the end).

## TL;DR — the load-bearing finding

**No sprite library on the market ships collider or physics metadata. Every place
where a sprite is paired with a collider is an AUTHORING/EDITOR format that a human
fills in by hand** (Tiled, LDtk, Godot TileSet, PhysicsEditor, R.U.B.E.). This is
the exact same structural finding as the game-engine research: the ecosystem is an
authoring layer, not a ready-made physics-parts layer.

Consequences for us:

1. **Sprites and physics are two separate acquisitions.** A sprite gives you pixels
   + an atlas rectangle (x/y/w/h). It never gives you mass, friction, elasticity,
   or a collision polygon. Those we author ourselves regardless of where art comes
   from.
2. **Sprites are cosmetic to OUR loop.** Verification reads engine state, never
   pixels (`CONTRACTS.md` line 8: "No pixels anywhere in verification"). So the
   part of a bank that reduces load on the code-writing model is the **physics
   archetype** (named presets of `World.add()` kwargs + collider), *not* the sprite.
3. Therefore the highest-leverage v1 bank is **~40-60 hand-written physics
   archetypes** keyed by name, with sprites as an *optional cosmetic layer* bolted
   on later where they actually pay off (site demos, engine ports). Recommended
   strategy = a pragmatic blend of (a) and (c) below. Details in §3-§6.

---

## 1. Sprite / asset libraries

### 1.1 Kenney.nl — the reference, and it earns it

- **License: CC0 across the board.** Every free pack page states "Creative Commons
  CC0 – freely usable with no restrictions." No attribution, no share-alike, no
  per-asset variation. This is the single most important property for automation:
  a bank can ingest, remix, re-atlas, and redistribute Kenney art with zero legal
  bookkeeping. (Nothing else below matches this.)
- **Breadth:** the "Kenney Game Assets All-in-1" bundle is **60,000+ assets** in one
  download (2D sprites, 3D, audio, fonts). The 2D catalogue alone is **~140 packs**
  (site pagination: ~9 pages × 16 packs). Directly relevant to our genres:

  | Pack | Assets | Genre fit | License |
  |---|---:|---|---|
  | Physics Assets | 215 | physics-puzzle (Angry-Birds/Totem-Destroyer style; ~55 material elements × 3 variants + damaged/broken tiles) | CC0 |
  | New Platformer Pack | 440 | platformer | CC0 |
  | Abstract Platformer | 370 | platformer / arcade (geometric look) | CC0 |
  | Platformer Pack Redux | 360 | platformer | CC0 |
  | Platformer Kit | 150 | platformer (3D-ish tiles) | CC0 |
  | Platformer Characters | 150 | platformer (agents) | CC0 |
  | Jumper Pack | 110 | arcade / vertical platformer | CC0 |
  | Puzzle Pack | 75 | puzzle / match | CC0 |

  Plus Pixel Platformer, Simple/Shape packs, Brick Pack, etc. — more than enough
  breadth for platformer / physics-puzzle / arcade.
- **File formats:** each pack ships as separate PNGs, **PNG spritesheet(s)**, and
  usually **vector (SVG)** — historically also SWF. The "Physics Assets" pack
  explicitly bundles 215 individual PNGs + spritesheets + vector files.
- **Machine-readability — YES, but only geometry.** The spritesheet is accompanied
  by an XML atlas in the **Sparrow/Starling `TextureAtlas` format**:

  ```xml
  <TextureAtlas imagePath="spritesheet.png">
    <SubTexture name="dirt.png" x="650" y="130" width="128" height="128"/>
    ...
  </TextureAtlas>
  ```

  Trivially parseable (one flat list of named rectangles). Widely tooled: importers
  exist for Godot (Kenney Spritesheet Importer, asset-library #2875), Unity
  (Starling atlas), LibGDX (XML→TextureAtlas converters). **But the XML carries
  ONLY `name/x/y/width/height` — no pivot beyond that, no collision polygon, no
  physics data.** (Verified against Red Blob Games' technical write-up and the raw
  atlas: "No collision or physics data ... only spatial positioning.")
- **Automation-friendliness:** high but not turnkey. There is **no official JSON
  API / structured catalogue** for the pack index — you scrape `kenney.nl/assets`
  or use the itch.io "All-in-1" bundle, then parse the per-pack XML locally.
  Community mirrors exist (GitHub `iwenzhou/kenney`, download gists, a Godot
  `kenney-assets-helper` dock). For a bank we'd curate a fixed subset once and vendor
  it, not hit a live API.
- **Verdict:** the obvious and essentially only sane sprite source for an automated
  CC0 bank. Adopt for the cosmetic layer; do NOT expect it to supply physics.

### 1.2 OpenGameArt.org (OGA)

- **License: heterogeneous — the core problem.** Per-submission mix of CC0, CC-BY
  3.0/4.0, CC-BY-SA 3.0/4.0, OGA-BY, GPL v2/v3, LGPL, WTFPL, and public domain.
  Only the CC0 slice is safe for frictionless automated reuse; everything else drags
  attribution and/or copyleft/share-alike obligations into any derivative bank.
- **Breadth:** very large and varied, but uneven quality and inconsistent
  dimensions/style across contributors (no house style like Kenney).
- **Machine-readability:** page-level metadata (tags, license field) is filterable
  (there's a CC0 collection), and OGA can auto-generate a combined credits file for
  a collection — but individual submissions have no standard atlas or manifest; you
  get a zip of whatever the artist uploaded.
- **Physics metadata:** none.
- **Verdict:** usable **only if filtered hard to CC0**, and even then it's a
  supplement to Kenney for gaps, not a primary. The license-audit cost per asset
  kills "pull by name" automation.

### 1.3 itch.io asset packs

- **License: maximally heterogeneous — each creator sets their own terms.** Tags
  like `commercial-license` / `royalty-free` are creator-applied, not enforced.
  itch's own "General Paid Asset License" even limits a purchase to a *single
  product per purchase* (free/gifted listings exempt). "Royalty-free" only means no
  per-copy fee — "if in doubt ask the creator."
- **Breadth / quality:** enormous and often excellent, but there is no uniform
  license, no uniform format, no manifest.
- **Physics metadata:** none as a rule.
- **Verdict:** **not automation-friendly.** Fine for a human picking one pack for a
  showcase; hostile to a machine bank that must reason about licensing by name.
  (Note: Kenney also mirrors his CC0 packs on itch — that specific slice is fine
  because it's still CC0.)

### 1.4 Phaser asset packs

- **Important distinction:** the Phaser "Asset Pack File" is **a loader manifest,
  not an art library.** It's a JSON file listing files to load
  (`{type, key, url, ...}` sections + a `meta` block), plus multiatlas support that
  ingests **TexturePacker** JSON (a `textures[]` array of atlas images with frame
  rects). So Phaser contributes a *packaging/loading convention*, not sprites and
  not physics parts.
- **Relevance to a bank:** if we ever render demos in Phaser (the rung-4
  Planck/Matter + Phaser+CDP on-ramp already noted in `GAME_ENGINE_INTEGRATIONS.md`),
  emitting a Phaser asset-pack manifest + a TexturePacker/multiatlas from our bank
  is a clean, well-documented target. Worth knowing; nothing to "adopt" yet.
- **Physics metadata:** the loader has none; Phaser's Arcade/Matter bodies are
  defined in code or imported from PhysicsEditor (see §2.1).

### 1.5 Godot Asset Library (2D)

- **License:** mostly MIT (tools/plugins/demos), filterable by license/category/
  support-level in-editor. But note **it's largely code assets (tools, shaders,
  demo projects), not curated sprite banks** — the sprite art there is thin and
  often just re-hosts Kenney/Kenney-likes.
- **Machine-readability:** there's a REST-ish asset-library backend and in-editor
  browse/filter, but it indexes *projects/addons*, not individual physics parts.
- **Physics metadata:** not at the library level. (Godot's *own* TileSet/.tres does
  carry colliders — that's §2.5, an editor format, not the asset library.)
- **Verdict:** low value as a sprite source; relevant only as a distribution channel
  if we ever ship a Godot importer for our bank.

### 1.6 LPC — Liberated Pixel Cup

- **License: dual CC-BY-SA 3.0 + GPLv3 — copyleft + mandatory attribution.** Every
  use must credit title/author/copyright/license/link and flag modifications; any
  modification of the sprites must itself be released CC-BY-SA (ShareAlike). The
  Universal LPC generator can emit a per-sprite credits CSV precisely because the
  attribution graph is non-trivial.
- **Breadth:** deep, consistent, modular **character** spritesheets (the Universal
  LPC generator composes layered humanoids) + some tiles. Style is top-down/RPG,
  **not** a great fit for side-view physics-puzzle/platformer, and it's
  character-centric rather than "items/obstacles/elements."
- **Machine-readability:** the Universal-LPC generator repo has structured layer
  definitions (sheet grids, per-layer credits) — the most structured of the
  non-Kenney options — but it's a fixed 64×64 walk/slash frame grid, not physics
  parts.
- **Physics metadata:** none.
- **Verdict:** **avoid for the bank.** The ShareAlike obligation is exactly the kind
  of per-derivative legal state we do not want a code-writing model to have to
  track, and the genre fit is poor. CC0 (Kenney) sidesteps all of it.

---

## 2. Physics-ready part definitions in the wild

The question that actually matters for reducing model load: does anyone publish
*sprite + collider + physics presets* together? **Answer: only inside authoring
tools, and you author the physics part yourself.** Here's each format and what's
worth stealing for a bank-entry schema.

### 2.1 PhysicsEditor (code'n'web)

- **What it is:** a GUI that traces a sprite's outline into **convex collision
  polygons** (auto-decomposed, because Box2D/Chipmunk/most engines reject concave
  shapes) and exports fixtures. Targets Box2D, Chipmunk, cocos2d, Phaser P2/Arcade,
  Nape, etc.
- **Data model (from the custom-exporter docs):** `bodies[]` → each body has
  `size`, `anchorPointRel/Abs`, and `fixtures[]`. A fixture is `CIRCLE`
  (`radius`, `center`) or `POLYGON` (`hull` = original possibly-concave outline;
  `polygons[]` = array of convex sub-polygons of `Point{x,y}`). Physics params
  (`density`, `friction`, `restitution`, `isSensor`, filter groups) are **custom
  fields injected via an `exporter.xml` template** — the engine is format-agnostic
  and can emit JSON, XML, or source. This is the cleanest "sprite → collider"
  pipeline in existence.
- **Adopt?** The *concept* — a fixture list of convex polygons + circle, with
  density/friction/restitution/sensor — is exactly what a bank entry needs. We don't
  need the GUI (our shapes are simple: box/circle/segment/poly already in
  `World.add`), but its data model is a good sanity check that our schema is
  complete.

### 2.2 R.U.B.E. + b2dJson (iforce2d)

- **What it is:** a full Box2D scene editor whose native format is **b2dJson**, the
  most complete open Box2D-scene JSON spec available.
- **Structure (verified against the JSON-structure doc):**
  - root **world**: booleans/numerics + `gravity` vector; child arrays `body[]`,
    `joint[]`, `image[]`.
  - **body**: `name`, `type`, `position`, `angle`, `mass`, + `fixture[]`.
  - **fixture**: `density`, `friction`, `restitution`, and one `shape` child —
    `circle` (center+radius), `polygon` (parallel x/y vertex arrays), or `chain`
    (x/y arrays; `nextVertex/prevVertex` ⇒ closed loop).
  - **joint**: type + the two body indices it connects.
  - **image**: links a sprite to a body (body index or -1 for world-absolute) with
    corner coords — i.e. **R.U.B.E. is one of the very few formats that binds sprite
    ⇄ collider ⇄ body in one file.**
  - **custom properties** are supported on every object (b2dJson `customProperties`),
    and it uses parse-friendly conventions (missing bool ⇒ false, missing number ⇒
    0; floats optionally hex-encoded for exactness).
- **Adopt?** This is the closest existing thing to "a bank entry." It's Box2D-shaped
  (our substrate is pymunk/Chipmunk, but the vocabulary is 1:1: body/fixture/shape/
  density/friction/restitution/joint). **Worth adapting as the mental model for our
  schema**, but not adopting wholesale — it carries Box2D/rendering baggage
  (`glDrawElements`, image UV arrays) we don't need, and it's a whole-scene format,
  not a per-part catalogue.

### 2.3 Tiled (mapeditor.org)

- **What it is:** the most widely-tooled 2D map/object editor; JSON (and XML/Lua)
  native.
- **Relevant pieces:** **Object Templates** (`.tx`/`.tj`) = a reusable object saved
  to its own file and referenced by instances — literally "a part you place by
  reference," the interaction pattern we want. Collision shapes are first-class:
  rectangle, **polygon**, ellipse/**capsule**, point, drawn per-tile in the
  collision editor. **Custom Classes and Enums** (project-level typed properties)
  let you attach `mass/friction/material=...` to templates/tiles in a validated way.
- **Adopt?** The **template + typed-custom-property** model is a strong, boring,
  extremely well-supported pattern. If we ever want an *editable* bank a human can
  tweak visually, Tiled templates with a custom "PhysicsPart" class is the
  lowest-risk existing container. For a code-only bank it's heavier than needed.

### 2.4 LDtk (deepnight)

- **What it is:** a modern level editor with an excellent, explicitly
  **parse-friendly JSON** (`JSON_SCHEMA.json` published; quicktype bindings).
- **Relevant pieces:** **Entity definitions** with **typed custom fields**
  (`F_Int/F_Float/F_String/F_Bool/F_Color/F_Enum/F_Point/F_Path/F_EntityRef/F_Tile`,
  plus `Array<...>`), instances carry redundant `__`-prefixed resolved values
  (`__value`, `__identifier`) specifically to make parsing trivial, and entities
  carry **tags**. That typed-field + tag system is essentially a schema-validated
  "part with named physical properties."
- **Weakness:** LDtk has **no collision-shape editor** — colliders in LDtk are
  conventionally derived from tile/grid values or an IntGrid, not per-entity
  polygons. So it gives great *metadata ergonomics* but not the *collider geometry*.
- **Adopt?** Steal the **ergonomics**: typed fields, tags, and the `__`-resolved
  convenience-value convention are a good template for how our bank JSON should read
  to an LLM (self-describing, no lookups needed).

### 2.5 Godot TileSet / PhysicsMaterial (.tres)

- **What it is:** Godot's own resource format. A **TileSet** `.tres` can define
  **physics layers**; each tile carries collision polygons stored as
  `PackedVector2Array` (hand-drawable in-editor or editable directly in the `.tres`),
  plus **PhysicsMaterial** (friction, rough, bounce, absorbent) and modulation.
- **This is a real sprite ⇄ collider pairing** — but again, **you draw the polygons
  yourself**; importing Kenney tiles does not populate them (open Godot proposal
  #13895 asks merely to *expose* polygon vertices better).
- **Adopt?** Only relevant if Godot becomes a rung-4 target (it's the deliberate
  step-2 in `GAME_ENGINE_INTEGRATIONS.md`). Then emitting a TileSet `.tres` +
  PhysicsMaterial from our bank is a clean export. Not a source format for us.

### 2.6 box2d / planck.js "body libraries"

- **Reality check:** there is **no canonical library of pre-made Box2D/planck
  bodies.** Box2D and planck.js ship *shape primitives* (`Polygon`, `Circle`,
  `Edge`, `Chain`) and you build bodies programmatically
  (`body.createFixture({shape, density, friction})`). Constraints worth noting for
  our schema: **polygons must be convex, 3-8 vertices** (`B2_MAX_POLYGON_VERTICES`);
  concave outlines must be decomposed (what PhysicsEditor automates).
- **Adopt?** Confirms the design: a "part" is (shape primitive(s) + material
  scalars), and the reusable-library layer simply **does not exist upstream** — we'd
  be creating it, which is the whole point of the bank.

### 2.7 Format comparison — what to steal

| Format | Sprite↔collider bound? | Collider geometry | Physics scalars | Custom/typed props | Parse-friendliness | Steal for our schema |
|---|---|---|---|---|---|---|
| PhysicsEditor JSON | yes (per-sprite) | convex polys + circle | via template | yes (template) | good | fixture = convex-poly list + circle |
| R.U.B.E. b2dJson | **yes** (image↔body) | poly/circle/chain | density/friction/restitution | yes | good (hex floats aside) | **body→fixture→shape vocabulary + custom props** |
| Tiled template | via tile | rect/poly/ellipse/capsule | custom class | **typed classes/enums** | good (JSON) | template-by-reference + typed props |
| LDtk entity def | via tile field | none (IntGrid only) | custom fields | **strongly typed + tags** | **excellent (`__` values)** | **typed-field + tag ergonomics** |
| Godot TileSet .tres | yes | PackedVector2Array polys | PhysicsMaterial | limited | ok | export target only |
| box2d/planck | n/a | primitives (convex ≤8 v) | on fixture | n/a | n/a | convexity/vertex limits |

**Net:** no single format is "adopt as-is." The right bank schema = **R.U.B.E.'s
body/fixture/shape vocabulary + LDtk's typed-field/tag ergonomics**, expressed
directly as presets of our own `World.add()` kwargs (see §6).

---

## 3. Where do sprites actually pay off? (honest assessment)

Because verification reads state and never pixels, a sprite changes **zero** bits of
any oracle (G0-G3), witness, or checkpoint. So sprites are pure cosmetics to the
core loop. They pay off in exactly three downstream places, all optional:

1. **Site / public build log** (`eliasepfl.github.io`). Demo GIFs currently render
   from `world.query()` as colored primitives (`render.py`: green controlled, amber
   sensors, slate static, blue/violet dynamics). Sprites would make these look like
   real games instead of a physics debugger. **Real but purely presentational
   payoff**, and it competes with a cheaper win: just polish the primitive renderer.
2. **Real-engine ports (rung 4).** A Phaser/Godot/Roblox showcase of a *certified*
   game benefits from real art — that's the "runs on a real engine" credential. But
   this is a handful of hero demos, not the 100s-of-games campaign, so it needs a
   handful of sprites, not a 60k-asset pipeline.
3. **Prompt grounding / theme signal (marginal).** Naming a part "crate" vs "box"
   might nudge the model toward coherent themes. This is a metadata/naming effect,
   achievable with archetype names alone — **no pixels required.**

**Conclusion:** invest in the physics-archetype layer now; treat sprites as a thin,
CC0-only (Kenney) cosmetic overlay added lazily for site/engine demos. Do NOT let
sprite sourcing gate the bank.

---

## 4. Three candidate v1 bank strategies

### (a) Curate ~60-120 Kenney CC0 sprites + hand-write physics archetypes
- **What:** pick a fixed subset (say ~80 sprites across platformer/physics-puzzle/
  arcade), vendor them CC0, and hand-author ~40-60 named **physics archetypes**
  (`crate_wood`, `crate_steel`, `ball_bouncy`, `ball_heavy`, `platform_static`,
  `ramp_30`, `ice_floor`, `domino`, `seesaw_plank`, `spinner`, `bumper`,
  `hazard_spike`, `player_capsule`, ...). Each archetype = a preset of `World.add()`
  kwargs (shape, size, mass, friction, elasticity, static/sensor, locked_rotation)
  + an optional `sprite:` key pointing into the vendored atlas.
- **License:** trivial — Kenney is CC0, archetypes are ours.
- **Effort:** **low-medium.** The sprites are a one-time curation; the archetypes are
  the real work (~1-2 days to write + a fixture test that spawns each into a `World`
  and asserts it settles without penetration/explosion — reuse G0/G1 machinery).
- **Machine-usability:** high — archetypes are named, self-describing, and map 1:1 to
  the substrate the model already writes against.
- **Risk:** archetype coverage/quality is on us; but that's precisely the "lesson"
  we want to encode (§OBJECTIVES pyramid step 2).

### (b) Adopt an existing physics-shape format (PhysicsEditor / R.U.B.E. / LDtk) and fill it
- **What:** pick b2dJson or a PhysicsEditor exporter as the on-disk schema, then
  populate it with our parts.
- **License:** the *formats* are free; you still supply/curate the art & values.
- **Effort:** **medium-high, with negative ROI.** These formats are Box2D-centric or
  editor-centric; we'd write an importer/adapter to pymunk, carry fields we don't
  use (UV arrays, joint indices, hex floats), and gain an editor GUI we don't need
  for code-only parts. It buys interoperability we have no consumer for **yet**.
- **When it wins:** only if a human-editable visual bank or a Box2D/planck rung-4
  target becomes a priority — then b2dJson export is worth it. Not for v1.

### (c) Geometric primitives only + styling layer (no sprites)
- **What:** no art at all. Parts = named archetypes rendered as the current colored
  primitives, with a small **styling layer** (palette/label/outline per archetype
  name) so `ice_floor` is cyan, `hazard_spike` is red, `crate_wood` is a tan box,
  etc. Purely extends what `render.py` already does.
- **License:** none needed — everything is ours.
- **Effort:** **lowest.** It's archetypes (same as (a)) minus the sprite curation,
  plus ~a few hours of palette/labelling in the renderer.
- **Payoff:** captures ~100% of the *loop* benefit (reduced model load via named
  parts) and a decent chunk of the *demo* benefit (consistent, readable, themed
  visuals) for the least effort. Loses only photoreal art for hero showcases.

---

## 5. Inventory table

| Source | License | Breadth (our genres) | Machine-readability | Physics metadata | Automation | Verdict |
|---|---|---|---|---|---|---|
| **Kenney.nl** | **CC0** (uniform) | **High** (~140 2D packs; platformer/physics/puzzle/arcade all covered) | Good — Sparrow `TextureAtlas` XML (name/x/y/w/h) | **None** (geometry only) | High (scrape/vendor once; no live API) | **Primary sprite source** — CC0 is decisive |
| OpenGameArt | Mixed (CC0…GPL/BY-SA) | High but uneven | Page metadata + CC0 filter; no per-asset manifest | None | Medium (only if hard-filtered to CC0) | Supplement, CC0-only |
| itch.io | **Heterogeneous** (per-creator) | High, high quality | None (no manifest) | None | **Low** (license per pack) | Avoid for automation (Kenney's itch CC0 mirror aside) |
| Phaser asset pack | Format only | n/a (a loader, not art) | JSON manifest + TexturePacker multiatlas | None | High (as an export target) | Packaging/loading target for demos, not a source |
| Godot Asset Library | Mostly MIT | Low (tools/demos, thin art) | Library index, not parts | Only in TileSet .tres (editor, hand-drawn) | Medium | Export/distribution channel if Godot rung-4 |
| LPC | **CC-BY-SA 3.0 + GPLv3** | Medium (RPG chars, wrong genre) | Good (generator layer defs) | None | Low (ShareAlike + attribution graph) | Avoid — copyleft + genre mismatch |
| PhysicsEditor | Tool (free tier) | n/a (colliders) | JSON/XML via template | **Yes** (convex polys + circle + density/friction/restitution/sensor) | Medium | Steal the fixture data model |
| R.U.B.E. / b2dJson | Open spec | n/a (scenes) | JSON (well-documented) | **Yes** (body/fixture/shape + joints + image↔body + custom props) | Medium | **Steal the vocabulary** (best mental model) |
| Tiled templates | Free/open | n/a (containers) | JSON/XML | Collision shapes + typed custom classes | High | Container option if a visual bank is wanted |
| LDtk entity defs | Free | n/a (metadata) | **Excellent** (`__`-resolved, schema published) | Typed fields/tags; **no collider geometry** | High | **Steal the typed-field/tag ergonomics** |
| Godot TileSet .tres | MIT (engine) | n/a | .tres (Godot-only) | **Yes** (poly + PhysicsMaterial, hand-drawn) | Low (Godot-coupled) | Export target only |
| box2d / planck | n/a | n/a | code | Primitives on fixture (convex ≤8 v) | n/a | Confirms: no upstream part library exists |

---

## 6. Recommendation + proposed bank-entry schema

**Top recommendation: strategy (a) as the target, bootstrapped by (c).** Ship a
**code-first physics-archetype bank** (~40-60 named parts) that maps directly onto
`World.add()`, render it with the primitive+styling layer (c) immediately, and
**lazily** attach vendored **Kenney CC0** sprites (a) only for site/engine hero
demos. Explicitly do NOT adopt an external physics format (b) for v1 — no consumer
justifies the adapter cost yet; keep R.U.B.E.'s vocabulary and LDtk's ergonomics as
*design references* only.

**Why this order:** the bank exists to cut load on the code-writing model, and the
model writes `World.add(...)` calls — so the parts must *be* `World.add` presets,
nothing more exotic. Every scalar a physics part needs already exists as a
`World.add` kwarg (`mass`, `friction`, `elasticity`, `static`, `sensor`,
`locked_rotation`, `shape`, `size/radius/vertices`). A bank entry is therefore a
**named, tagged preset dict**, self-describing in LDtk style:

```jsonc
// parts/crate_wood.json  — illustrative, maps 1:1 to World.add()
{
  "id": "crate_wood",
  "tags": ["obstacle", "stackable", "pushable", "wood"],
  "__desc": "light wooden crate; stacks and topples; medium friction",
  "shape": "box", "size": [48, 48],
  "mass": 1.5, "friction": 0.7, "elasticity": 0.15,
  "static": false, "sensor": false, "locked_rotation": false,
  "sprite": { "atlas": "kenney/physics", "frame": "crate.png" }  // optional, cosmetic
}
```

The generator prompt then references parts by `id` ("spawn a `crate_wood` at
(400,300)"), the harness expands the preset into a `World.add()` call, and the
`sprite` key is ignored by every oracle and consumed only by the renderer/exporters.
A one-time **archetype self-test** (spawn each part alone into a `World`, step 120
frames, assert no NaN/explosion/self-penetration — reuse G0/G1) keeps the bank
honest.

**Key numbers to carry forward:** Kenney = **CC0**, **~140 2D packs**, **60k+
assets**, with directly usable packs of **215 (Physics) / 440 (New Platformer) / 370
(Abstract Platformer) / 150 (Platformer Kit) / 110 (Jumper) / 75 (Puzzle)**; atlas =
**Sparrow XML, geometry-only, zero physics**; target v1 bank = **~40-60 archetypes /
~80 curated sprites**; external physics formats to *reference not adopt* =
**R.U.B.E. b2dJson** (vocabulary) + **LDtk** (typed-field ergonomics).

---

## Sources (primary, verified)

- Kenney assets index & 2D category — https://kenney.nl/assets , https://www.kenney.nl/assets/category:2D
- Kenney Physics Assets (215, CC0) — https://kenney.nl/assets/physics-assets
- Kenney New Platformer (440) / Abstract Platformer (370) / Platformer Kit (150) / Jumper (110) / Puzzle (75) — https://kenney.nl/assets/new-platformer-pack , https://kenney.nl/assets/abstract-platformer , https://kenney.nl/assets/platformer-kit , https://kenney.nl/assets/jumper-pack , https://kenney.nl/assets/puzzle-pack
- Kenney All-in-1 (60k+) — https://kenney.itch.io/kenney-game-assets
- Kenney atlas format (Sparrow TextureAtlas, geometry only) — https://www.redblobgames.com/x/1608-kenney-sprites/ , Godot importer https://godotengine.org/asset-library/asset/2875
- OpenGameArt licenses/FAQ — https://opengameart.org/content/faq , https://opengameart.org/content/cc0-resources
- itch.io asset licensing — https://itch.io/blog/929708/general-paid-asset-license , https://itch.io/t/2626922/how-do-i-license-my-game-assets
- Phaser asset-pack + multiatlas — https://docs.phaser.io/phaser-editor/asset-pack-editor/asset-pack-file , https://newdocs.phaser.io/docs/3.80.0/focus/Phaser.Loader.LoaderPlugin-multiatlas
- Godot Asset Library / TileSet colliders / PhysicsMaterial — https://godotengine.org/asset-library/asset , https://docs.godotengine.org/en/stable/tutorials/2d/using_tilesets.html , https://docs.godotengine.org/en/stable/classes/class_tileset.html , proposal https://github.com/godotengine/godot-proposals/issues/13895
- LPC license & assets — https://lpc.opengameart.org/content/faq , https://opengameart.org/content/liberated-pixel-cup-lpc-base-assets-sprites-map-tiles , generator https://github.com/liberatedpixelcup/Universal-LPC-Spritesheet-Character-Generator
- PhysicsEditor data model / custom exporter — https://www.codeandweb.com/physicseditor , https://codeandweb.com/physicseditor/tutorials/customize_physicseditor_exporter
- R.U.B.E. / b2dJson JSON structure — https://www.iforce2d.net/rube/ , https://www.iforce2d.net/rubehelp/content/jsonstructure.html , https://github.com/iforce2d/b2dJson
- Tiled objects/templates/custom types (JSON) — https://doc.mapeditor.org/en/stable/manual/objects/ , https://doc.mapeditor.org/en/stable/manual/using-templates/ , https://doc.mapeditor.org/en/stable/manual/custom-properties/
- LDtk entity fields / JSON schema — https://ldtk.io/docs/game-dev/json-overview/entity-fields/ , https://github.com/deepnight/ldtk/blob/master/docs/JSON_SCHEMA.json
- box2d/planck shape primitives & convexity limits — https://box2d.org/documentation/md_collision.html , https://piqnt.github.io/planck.js/docs/
