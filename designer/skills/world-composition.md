---
id: world-composition
kind: reference
created_by: human-seed wave-1
run_id: reseed-2026-07-14
wave: 1
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
load_when: Layout phase — placing bodies, sizing the arena, positioning the sensor fan, and validating with inspect_world
rationale: How to lay out the world once the archetype is chosen: spatial composition that reads as a place (not numbered boxes), the sensor as the spine, and the inspect_world analyzer as a cheap pre-certify placement check.
provenance: notes/engines/EXAMPLES_STRUCTURE_GUIDE.md §1/§5; notes/engines/GODOT_QUALITY_BAR.md §2 (spatial composition); harness/designer/tools.py inspect_world warning taxonomy; godotengine docs, paraphrased.
---

# World composition — lay out a place, then check it

Concept is settled; now place the bodies and size the arena. Two jobs: make the
space READ as a place, and make the sensor decisive.

## Compose a place, not a row of boxes

- **Bound the arena deliberately.** Static walls (thick — see the tunneling truth)
  or an `Area` bound; the goal reachable but the edge a real threat (G1 escape).
- **Silhouette contrast between regions.** The documented sameness failure is
  "numbered ledges in a row plus a goal sensor." Vary orientation region to region:
  verticality vs horizontality, a chokepoint then an opening, near clutter vs a
  clear run. A stranger should read two consecutive regions as different shapes.
- **Randomize contents per reset** (seeded): which bay is free, where the goal sits,
  where hazards fall. The layout is a distribution, not a fixed diorama.
- **Clearance at spawn.** Nothing interpenetrating (G0); leave the controlled body
  room to accelerate before it meets the first obstacle.

## The sensor is the spine

- One raycast fan, attached to the controlled body, riding along its local +x. Build
  the world so the fan DISCRIMINATES the thing that matters — obstacle gaps,
  hazard proximity, the goal direction. If the sensor can't tell the goal apart from
  the wall, the world is mis-composed, not the policy.
- Widen `cone_width_deg` toward 360 for surround-awareness (herding, dodging);
  narrow it forward for a driving/aiming feel. `n_rays` is the obs length — more rays
  = finer angular resolution, at obs cost.
- Read the proximity + latency rows of `engine-truths.md` before you rely on a
  sensor read (0 = no hit; one settle frame before first read).

## Validate with `inspect_world` BEFORE you certify

`inspect_world(spec_or_fragment)` is an engine-free static analyzer (in the tool
REGISTRY). Give it the spec or just a `bodies` fragment; it returns:

- **`entities`** — each body's AABB (mirrors the runner's `_bbox`, the same box
  `contained()` uses), so you can eyeball whether a body actually fits a zone.
- **`warnings`** — the placement taxonomy: `overlap_solid_statics` (two solid
  statics penetrating — a G0 risk), `out_of_bounds` (a body outside `world_size`),
  and isolated sensor zones (a `sensor` zone with no body within reach — a dead
  goal that can never fire).
- **`summary`** — counts for a quick sanity read.

Treat every warning as a design bug to fix before the gate sees it: clear overlaps,
pull bodies inside the bound, and make sure each sensor zone has something that can
actually reach it. This is the cheapest possible pre-G0/G3 check — no Godot process.

## Layout hand-off

Once `inspect_world` is clean and the arena reads as a place, move to the Predicates
phase (`certification.md`). Do not start dressing yet.
