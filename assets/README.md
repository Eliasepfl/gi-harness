# Demo asset bank — real low-poly 3D assets for render-only dressing

This bank lets a generated game be *dressed* with real low-poly 3D meshes (like
the `godot_rl_agents_examples` demos) instead of plain primitives. **Assets are
render-only cosmetic dressing** — the generated game keeps its own
physics/collision; nothing here is ever imported as a collider (see
`notes/engines/ASSET_BANK.md`).

## Layout

```
assets/
  manifest.json     # COMMITTED — the bank index (provenance + dims + collision hint)
  README.md         # COMMITTED — this file
  models/           # GITIGNORED — the copied .glb/.gltf meshes (rebuildable)
  aabb.json         # GITIGNORED — transient Godot AABB dump (build artifact)
```

Only the manifest and this README are committed. The meshes are gitignored and
fully reproducible from the (public) source repo via the curation script, so the
bank is reproducible without vendoring binaries.

## Rebuild (reproducible, offline)

The bank is curated from a local `godot_rl_agents_examples` checkout
(https://github.com/edbeeching/godot_rl_agents_examples). No network is needed
for the rebuild itself.

```bash
# 1. copy assets + write manifest (dimensions null)
python -m harness.demo.curate_bank --src /path/to/godot_rl_agents_examples --bank assets

# 2. measure AABBs headless, in-image (needs a project context -> --path godotworld)
srun -p mit_quicktest -c 4 -t 10 --mem=8G apptainer exec -B /orcd -B /home/enaha \
  ~/gi/gi-certifier.sif bash -lc \
  '/opt/godot/godot --headless --path godotworld --script harness/demo/measure_aabb.gd -- <abs>/assets'

# 3. fold measured dims + suggested collision primitive into the manifest
python -m harness.demo.curate_bank --bank assets --merge-dims assets/aabb.json

# inspect
python -m harness.demo.curate_bank --bank assets --list
```

`--src` also reads `$GI_ASSET_SRC`. Adding assets = extend `ASSET_TABLE` in
`harness/demo/curate_bank.py`, then re-run steps 1–3.

### Optional: growing with fetched CC0 packs

The archetypes (vehicle / tree / crate / ball / robot) are covered by the local
set, so no external fetch is required. To grow the bank later, drop CC0 `.glb`
files from Kenney (https://kenney.nl) or KayKit (https://kaykit.itch.io) into a
kit dir and add `ASSetSpec` rows pointing at them (record `license="CC0-1.0"`).
Keep single-object, +Y-up GLBs; whole-scene collections are excluded by design.

## Licenses & attribution

Licenses are recorded **as found**. Per project direction (test project) the bank
includes assets whose source shipped no license; those are recorded
`license: "unknown"` so a future re-audit is trivial. **If this ever ships
publicly, re-audit every `unknown` entry before distribution.**

| License | Assets | Attribution (required for CC-BY) |
|---|---|---|
| **CC-BY-4.0** | car, robot, robot-drone, tree-tile, road-tile, goal-tile, turret, goal-net, launcher, platform, flying-platform | Graphical assets by **Ivan Dodic** (https://github.com/Ivan-267), CC-BY-4.0 (https://creativecommons.org/licenses/by/4.0/). From the CrossTheRoad, DefendTheGoal, MultiAgentSimple, RobotFPS example licenses. |
| **CC-BY-4.0** | plane | **"Cartoon Plane"** by **antonmoek** (https://sketchfab.com/antonmoek), CC-BY-4.0 — license embedded in the glTF `asset.extras`. |
| **unknown** | jeep, convertible, tree, rock, waypoint, track-piece-1, track-piece-2 | Racer example (Kenney-derived per repo notes: `import/blender/enabled=false`, `kenny_*` filenames) but **no license file bundled** in the source repo. Likely Kenney CC0-1.0 — re-fetch from https://kenney.nl to confirm before public use. |
| **unknown** | chest, ship | Ships example — no license file bundled and no embedded glTF metadata. |

Per-asset provenance (source repo path + license + attribution) lives in
`manifest.json`. **Excluded on technical grounds** (not licensing): whole-level
`map.glb` scenes, multi-object `cars.glb`/`forest.glb`/`trees.glb` collections,
and `.blend` sources (we standardize on GLB — see `notes/engines/BLENDER_ASSETS_AND_INSPECT.md`).

## Inventory

Dimensions are the native mesh AABB in metres (W×H×D); the loader rescales to any
target. `plane` is in the source's own large unit scale — harmless, the loader
normalizes it.

| id | archetype | dims (W×H×D) | collision hint | license | description |
|---|---|---|---|---|---|
| car | vehicle | 1.10×1.25×1.91 | box | CC-BY-4.0 | Low-poly car body |
| robot | robot | 0.39×0.65×0.44 | box | CC-BY-4.0 | Boxy walking robot character |
| robot-drone | robot | 1.12×2.04×1.10 | capsule | CC-BY-4.0 | Small hovering robot / drone |
| tree-tile | tree | 2.00×4.61×2.00 | box | CC-BY-4.0 | Stylized tree on a ground tile |
| road-tile | track | 2.00×2.00×2.00 | box | CC-BY-4.0 | Flat road / lane tile |
| goal-tile | goal | 2.00×3.80×2.00 | box | CC-BY-4.0 | Goal / finish tile marker |
| turret | prop | 2.00×1.47×2.00 | box | CC-BY-4.0 | Defensive turret / tower |
| goal-net | goal | 7.20×3.00×2.00 | box | CC-BY-4.0 | Sports goal net |
| launcher | prop | 2.22×2.35×6.57 | box | CC-BY-4.0 | Projectile launcher / weapon prop |
| platform | platform | 2.00×1.05×4.00 | box | CC-BY-4.0 | Square floor platform |
| flying-platform | platform | 3.93×0.51×2.94 | box | CC-BY-4.0 | Floating platform pad |
| plane | vehicle | 868.9×279.7×811.1 | box | CC-BY-4.0 | Cartoon propeller airplane |
| jeep | vehicle | 2.56×1.73×4.28 | box | unknown | Green off-road jeep |
| convertible | vehicle | 2.56×0.96×4.08 | box | unknown | Red convertible car |
| tree | tree | 3.40×4.89×3.96 | box | unknown | Low-poly single tree |
| rock | ball | 2.76×1.67×2.60 | sphere | unknown | Rounded boulder / rock |
| waypoint | marker | 8.88×4.44×0.75 | box | unknown | Waypoint / checkpoint marker |
| track-piece-1 | track | 110.5×4.0×62.5 | box | unknown | Racing track segment (Kenney Racing Kit) |
| track-piece-2 | track | 110.5×4.0×62.5 | box | unknown | Racing track corner segment |
| chest | crate | 0.49×0.55×0.44 | box | unknown | Treasure chest / crate box |
| ship | vehicle | 2.85×6.55×8.10 | box | unknown | Small sailing ship / boat |

21 assets. Archetype coverage: **vehicle** (car, plane, jeep, convertible, ship),
**tree** (tree, tree-tile), **crate** (chest), **ball** (rock), **robot** (robot,
robot-drone), plus track, goal, platform, prop, marker.
