# Asset Bank v2 — objects with defined volumes + defined objectives

**Dated 2026-07-14. Commissioned by Elias.** Pivot: the sprite bank is retired (e6336a6).
Sprites forced a *style* and were hard for an LLM to manipulate. What we want instead is an
asset bank of **objects with clear definitions** — a defined **volume** (shape/dims) plus an
already-defined **objective/role** (cone = static obstacle, gate = posts + sensor span, coin =
collectible sensor, ramp, crate, pad). Playability > style. This note is the binding design.
Reading order: `design.md` (contract) → `banks/parts/v1/parts.json` (spine) → `GODOT_ONLY_PIVOT.md`.

---

## 1) THE REFRAME (5 lines)

1. Old bank: `sprite:null` slot → a *skin* keyed by name. A skin encodes a look, and a look is
   the one thing we decided the LLM must not choose.
2. New bank: each entry is a **volume + a role**. Volume = a physics footprint we can attach a
   collider to; role = an objective the runner *instantiates* (obstacle blocks, coin collects).
3. That is LLM-composable because the composition surface becomes **objectives + positions**
   ("cone here, coin there, goal at the exit"), never style. An LLM places reliably; it styles badly.
4. v1 is already 90% of this: 60 pre-certified nouns, each with a physics contract, bounded overrides,
   per-category invariants, offline `bank_ci` cert. v2 is a *re-view*, not a rewrite.
5. Load-bearing move: split the one `category` axis into two — `physics_class` (CI floor) and `role`
   (game objective) — and replace `sprite` with a style-neutral `render_binding`.

---

## 2) SOURCES

| source | objects | semantics shipped | license | extraction needed | verdict |
|---|---|---|---|---|---|
| **Kenney Car+Racing** (already vendored, `examples/Racer/.../kenny/` +`/track_pieces/`) | 22 vehicles + 112 track (pylon, barrier, fence, flag, roadRamp, grandstand) | filename only | CC0-1.0 | bbox reader + filename→role table | **PRIMARY** — steer cluster end-to-end, already local |
| **Kenney Platformer/Prototype/Blaster** | crate, coin, spikes, spring, door, lever, switch, saw, ladder, platform | filename only | CC0-1.0 | same two extractors | **PRIMARY** — fills prop/hazard/trigger nouns |
| **Quaternius** (Ultimate Platformer, Nature) | trees, rocks, bushes, platformer props (+ .blend source) | filename only | CC0-1.0 | same | **BREADTH** — decor/anti-sameness |
| **KayKit** (Platformer/Prototype/Resource Bits) | props, dungeon dressing | filename only | CC0-1.0 | same | **SUPPLEMENT** — stylized; use vocab, be wary of meshes |
| **Poly Pizza API** (api.poly.pizza/v1.1, token) | one-offs kits lack | **category + tags** (only source with real metadata) | mixed CC0+CC-BY → hard-filter CC0 | API pull + bbox | **OFFLINE GROWER only** — never in the gen loop; vendored/committed |
| **Hunyuan3D 2.1** (self-host, SLURM GPU) | generated meshes | none (mesh only) | open weights, permissive commercial | scale-normalize + collider-fit + full role author | **DEFER** — only after CC0 kits exhausted; $0/asset, watertight |
| **Meshy/Tripo/Rodin** (SaaS) | generated meshes | none | tiered (free = CC-BY) | same + fees + network | **REJECT** — per-asset cost + network for a skin the verifier never reads |
| **Objaverse / Sketchfab** | 800K / large | noun labels / license only | mixed CC | per-model compliance + role author | **REJECT for now** — noun ≠ role, mixed-license swamp |

Two facts hold across *every* source: (a) **no kit ships colliders or physics** — that half stays
hand-authored/calibrated exactly as `parts.json` already is; (b) **volume is always derivable the
same way** (glTF POSITION min/max → AABB), **role arrives only as a filename** (ZIP kits) or
category+tags (Poly Pizza). So the bank needs exactly two extractors + a provenance stamp. The
certifier reads *engine state, never pixels* (CONTRACTS §1): the mesh is presentation, the
volume+role+invariant is the load-bearing definition — hand-authored and certified.

---

## 3) THE v2 SCHEMA (the binding design)

Superset of v1, forward-compatible. **Rename** `category`→`physics_class` (CI floor:
static/dynamic/sensor/jointed, keyed identically to `CATEGORY_INVARIANTS`). **Add** `role`
(first-class objective), `volume` (explicit block + reserved GLB slot), `role_contract` (the
machine-checkable promise), `render_binding` (replaces `sprite:null`). Everything else in v1 is
preserved verbatim: `assembly`/`joints`/`primary`/`control_candidate` (sub-body `assembly[].role`
stays *structural* — anchor/post/flap — distinct from the part-level semantic `role`),
`overridable` whitelist (reject-not-clamp), `invariants`, `provenance`, `cert`.

Role table (role → physics_class → what the runner instantiates): `obstacle`→terrain(static,
non-sensor)→blocks · `platform`→terrain(static)→foothold · `hazard`→hazard(sensor)→latches failure
flag on contact · `collectible`→trigger(sensor)→auto-wires `on_contact`+`remove_when`+got-flag ·
`goal`→trigger(sensor)→reach-flag read by success predicate · `gate`→composite(static posts + sensor
span) · `mover`→mobile(≥1 joint)→behavior hook · `movable`→prop(dynamic)→pushable ·
`vehicle`→prop(dynamic, control_candidate)→natural controlled body (LLM still declares `control`;
bank does not seize the verb) · `decor`→decor(sensor,cosmetic)→Polygon2D only.

```jsonc
// cone — Kenney pylon.glb → static obstacle
{ "name": "cone", "physics_class": "terrain", "role": "obstacle",
  "volume": { "footprint_2d": { "shape": "box", "size": [28, 40] }, "glb": null },
  "assembly": [{ "role":"body", "shape":"box", "static":true, "sensor":false, "friction":0.8, "size":[28,40] }],
  "primary":"body", "role_contract": ["primary_static","primary_non_sensor"],
  "overridable": { "scale": {"range":[0.6,1.6]} },
  "invariants": ["all_static","stays_put"],
  "render_binding": { "primitive_2d": { "shape":"from_volume", "color_by":"role" }, "tscn":null },
  "provenance": { "license":"CC0-1.0", "source":"kenney/racing-kit/pylon" },
  "cert": { "pos":[400,20], "ground":true } }
// gate — COMPOSITE: two static posts + one sensor span (checkpoint/goal)
{ "name": "gate", "physics_class": "trigger", "role": "gate",
  "volume": { "footprint_2d": { "shape":"box", "size":[160,120] }, "glb":null },
  "assembly": [
    { "role":"post_l", "shape":"box", "static":true, "sensor":false, "offset":[-80,0], "size":[12,120] },
    { "role":"post_r", "shape":"box", "static":true, "sensor":false, "offset":[ 80,0], "size":[12,120] },
    { "role":"span",   "shape":"box", "static":true, "sensor":true,  "offset":[0,0],   "size":[148,120] }],
  "primary":"span",
  "role_contract": ["posts_static","span_sensor","span_reach_flag"],
  "invariants": ["is_sensor","non_lethal"],
  "render_binding": { "primitive_2d": {"shape":"from_volume","color_by":"role"}, "tscn":null },
  "provenance": { "license":"CC0-1.0", "source":"kenney/racing-kit/flagCheckers+hand-composed" },
  "cert": { "pos":[400,60], "ground":true } }
// coin — collectible sensor (runner auto-wires on_contact + remove_when + got-flag)
{ "name":"coin", "physics_class":"trigger", "role":"collectible",
  "volume": { "footprint_2d": {"shape":"circle","radius":14}, "glb":null },
  "assembly": [{ "role":"body","shape":"circle","static":true,"sensor":true,"radius":14 }],
  "primary":"body",
  "role_contract": ["primary_sensor","removable","pairs_with_got_flag"],
  "invariants": ["is_sensor","non_lethal"],
  "render_binding": { "primitive_2d": {"shape":"from_volume","color_by":"role"}, "tscn":null },
  "provenance": { "license":"CC0-1.0", "source":"kenney/platformer-kit/coinGold" },
  "cert": { "pos":[400,120], "ground":false } }
// crate — dynamic movable (pushable RigidBody2D)
{ "name":"crate", "physics_class":"prop", "role":"movable",
  "volume": { "footprint_2d": {"shape":"box","size":[48,48]}, "glb":null },
  "assembly": [{ "role":"body","shape":"box","static":false,"sensor":false,"mass":2.0,"friction":0.6,"size":[48,48] }],
  "primary":"body",
  "role_contract": ["primary_dynamic","pushable"],
  "overridable": { "mass":{"range":[0.5,8.0],"path":"body.mass"}, "friction":{"range":[0.0,1.0],"path":"body.friction"} },
  "invariants": ["is_dynamic"],
  "render_binding": { "primitive_2d": {"shape":"from_volume","color_by":"role"}, "tscn":null },
  "provenance": { "license":"CC0-1.0", "source":"kenney/platformer-kit/boxCrate" },
  "cert": { "pos":[400,60], "ground":true } }
// ramp — sloped static foothold (platform role, poly footprint)
{ "name":"ramp", "physics_class":"terrain", "role":"platform",
  "volume": { "footprint_2d": {"shape":"poly","vertices":[[-90,-14],[90,-14],[90,14]]}, "glb":null },
  "assembly": [{ "role":"body","shape":"poly","static":true,"sensor":false,"friction":0.9,"vertices":[[-90,-14],[90,-14],[90,14]] }],
  "primary":"body",
  "role_contract": ["primary_static","primary_non_sensor","walkable_slope"],
  "invariants": ["all_static","stays_put"],
  "render_binding": { "primitive_2d": {"shape":"from_volume","color_by":"role"}, "tscn":null },
  "provenance": { "license":"CC0-1.0", "source":"kenney/racing-kit/roadRamp" },
  "cert": { "pos":[400,20], "ground":true } }
```

**Binding — how role becomes real.** Today the Godot lane consumes the bank as *advisory menu text
only*: `retrieve_menu` injects preset numbers and the LLM hand-writes every `bodies` entry
(`gamegen.py:891`, `SPEC.md:47`). That prevents mis-set densities but **not** a mis-wired collectible
(sensor + `on_contact` + `remove_when` + flag read in a predicate — fiddly, per-game). Two options:
(Advisory) a per-body `"role"` string the frozen runner reads to auto-wire the contract; (Resolve)
a spec-level `{"part":"coin","pos":[..],"overrides":{..}}` reference the runner expands via
`resolve_part` at load. **Resolve is the concrete cash-out of open decision #1** (bank→tscn templates)
and the recommendation; it must stay pure DATA→fixed-behavior (a role string selects a *vendored*
wiring, never executes a spec string) to keep the no-untrusted-GDScript posture.

---

## 4) THE FLAT-3D QUESTION (for Elias to decide)

The examples are flat-3D with GLB volumes; our runner is 2D (y-up px, box/circle/segment/poly,
gravity (0,-900)). The v2 schema is shaped so **both** lanes read one bank (`volume.footprint_2d`
+ reserved `volume.glb`; `render_binding.primitive_2d` + reserved `render_binding.tscn/glb`).

- **PATH A — 2D-footprint bank feeding the current lane.** Volume = 2D footprint, render = colored
  primitive. **Cost ≈ zero new engine work**: `runner.gd` unchanged, G0–G3 unchanged, no GLB import,
  no camera, no 3D collision. Ships now, fully certifiable by existing `bank_ci`. Ceiling: it *looks*
  like colored 2D primitives, not the examples' 3D look.
- **PATH B — a later flat-3D rung.** Volume carries `glb:{ref,aabb}`, render carries a `.tscn`, the
  runner becomes a 2.5D Node3D scene. **Cost is large and compounding**: new 3D runner, offline
  GLB vendor/import pipeline (cached/committed — determinism), collision-derivation from meshes,
  camera/projection, and a full **re-port of G0–G3 to 3D**. `GODOT_ONLY_PIVOT` now *permits* this
  (DSL perimeter unfrozen), but it is a rung, not a step.

Recommendation: **shape for both, build only Path A now, let Elias price Path B.** Do not let
"`volume.glb` is just a reserved slot" imply the render/verify half is cheap — it is not.

---

## 5) BUILD PLAN v1 (this week)

1. **Seed ~15–20 objects from Kenney only** (uniform CC0, already local, richest naming): the
   steer/vehicle family + cone(pylon)/barrier/fence/flagCheckers(gate)/roadRamp from Car+Racing,
   and crate/coin/spikes/spring/door from Platformer. Kenney first, everything else later.
2. **Extraction script** (offline bank-grower): (i) bbox reader — glTF POSITION min/max → AABB →
   2D footprint via a per-kit projection axis (top-down for Car/Racing, side for Platformer);
   (ii) curated filename→role alias table (the retired slicemap's shape); (iii) author a collider
   from the bbox (box/circle/hull) since no kit ships one; (iv) provenance stamp (CC0-1.0). Output
   vendored, content-hashed into `banks/parts/v2/bank.lock`, committed. **Nothing fetches in the gen loop.**
3. **Migrate the 60 v1 entries** by script + human review: `category`→`physics_class` (mechanical),
   derive `role` from `category`+`behavior`, `volume` from `assembly` geometry, drop the
   `sprite:null` hard-require (`bank.py:205`), add `render_binding`. Role for the ~15 dual-use
   entries (cone as obstacle vs movable, door as terrain vs removable) is **human review, not a script**.
4. **Extend `bank_ci`** with two check families on the live settle-grid: (a) VOLUME — realized AABB
   matches declared dims within tol, bounded under scale extremes; (b) ROLE — assert each
   `role_contract` (obstacle⇒static+non-sensor; collectible⇒sensor+removable+latches; goal⇒sensor,
   read-only, non-lethal; vehicle⇒dynamic+controllable; gate⇒posts static + span sensor).
   `physics_class` stays the floor; role is the added semantic ceiling.
5. **Menu rendering**: swap the line to `name | volume: shape WxH | role: <role> — <objective> |
   overrides: k lo-hi`, and **delete the dead rule** "NAME the primary entity exactly so a sprite
   binds" (`retrieval.py:317`). BM25 retriever + score gate unchanged.
6. **Generative lane: explicitly deferred until the CC0 kits are exhausted.** When that day comes,
   self-hosted Hunyuan3D 2.1 on our SLURM GPU — not the SaaS trio. Poly Pizza stays a strictly
   offline, CC0-filtered, human-run grower whose output gets a hand-authored definition on top.

---

## 6) OPEN DECISIONS

1. **Advisory vs Resolve binding** (the biggest one — it decides whether v2 actually *removes* the
   mis-wiring error class or just menu-text describes it). Recommend Resolve; needs a small
   audited change to the frozen runner.
2. **Path A only, or fund Path B** (flat-3D rung). Recommend A now, B priced separately.
3. **Projection axis per kit** — top-down vs side is *not* automatic; a curator picks it per kit
   before proportions are comparable. Also: normalize units cross-kit (Kenney modular ≠ metric).
4. **Poly Pizza now or later** — wire the offline CC0-filtered grower this cycle, or just download
   Kenney ZIPs directly and skip the API until the kits are tapped out?
5. **render_binding coloring** — HSV-by-role is the proposed default (guide: Ivan-267 look is
   *inspiration for `render.py`*, not files to lift). Confirm palette owner.
6. **Curation/admission gate** — the Roblox-Toolbox lesson (design.md A.1): certify every object
   before admission; ambiguous filenames (raceFuture, roadStraightSkew) reviewed by a human.
