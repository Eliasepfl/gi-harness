# Demo asset bank — real-3D render-only dressing (design)

> 2026-07-15, commissioned by Elias. Goal: our generated games should be
> *dressable* with real low-poly 3D assets — like `godot_rl_agents_examples` —
> "with properties attached as well as a description", while **still passing all
> verification**. The load-bearing rule: **assets are RENDER-ONLY**; physics and
> collision stay the game's own. This note is the design + the API two concurrent
> lanes agree on (this lane = the bank+matcher+loader library; the sibling lane =
> the visual dresser + capture).

Companion: `notes/engines/BLENDER_ASSETS_AND_INSPECT.md` (bank conventions:
standardize on GLB, collision authored in-engine never imported, GLB kits are an
*optional visual upgrade never a requirement*). This bank is the concrete build
of that "optional visual upgrade", decoupled from physics entirely.

Distinct from the 2D **parts bank** (`banks/parts/`, `notes/parts_bank/`): that is
volume+role for the 2D runner's *physics*; this is real 3D *meshes* for cosmetic
dressing. They do not overlap.

## 1. The four pieces

```
             curate_bank.py  ──copies──►  assets/models/*.glb        (gitignored)
 source repo ───────────────►  writes  ►  assets/manifest.json       (committed)
                                 ▲
             measure_aabb.gd ────┘  (headless Godot: AABB per asset -> merge-dims)

 DRESSER (sibling lane)
   asset_bank.route_assets(prompt, bodies) ─► {body_name: asset_id | None}   (py, LLM)
   AssetLoader.load_asset(id, manifest, aabb) ─► render-only Node3D           (gd)
```

1. **Curation** (`harness/demo/curate_bank.py`) — copies a license-recorded
   selection of `.glb`/`.gltf` from a local `godot_rl_agents_examples` checkout
   into `assets/models/` under kebab-case ids, and writes `manifest.json` with
   per-asset provenance (source path + license + attribution), a one-line
   description, and tags. Reproducible + offline (the source is a public repo);
   the meshes are gitignored, the manifest + script are committed.
2. **Properties** (`harness/demo/measure_aabb.gd`) — headless Godot loads each
   asset with `GLTFDocument.append_from_file`, unions every `MeshInstance3D`
   AABB, and dumps `aabb.json`. `curate_bank.py --merge-dims` folds the AABB +
   size + centre + a **suggested collision primitive** (box/sphere/capsule, from
   dims + round/ball tags) into the manifest. This is the "properties attached".
3. **Matcher** (`harness/demo/asset_bank.py`) — maps game bodies to asset ids.
4. **Loader** (`godotworld/asset_loader.gd`) — the render-only runtime loader the
   dresser calls.

### Manifest schema (per asset)

```jsonc
{ "id": "car", "file": "models/car.glb", "format": "glb",
  "archetype": "vehicle", "description": "Low-poly car body",
  "tags": ["vehicle","car","automobile","cart"],
  "license": "CC-BY-4.0",
  "attribution": "Graphical assets by Ivan Dodic ... CC-BY-4.0",
  "source": { "repo": "godot_rl_agents_examples", "url": "...", "path": "examples/.../car.glb" },
  "dimensions": { "aabb_min":[..], "aabb_max":[..], "size":[w,h,d], "center":[..] },
  "collision": { "primitive": "box", "half_extents": [..] } }   // ADVISORY only
```

`collision` is a **hint** (e.g. for a cheap proxy); it is never the game's
physics. `license: "unknown"` = the source shipped no license and none was
embedded (recorded honestly for a future re-audit).

## 2. Matcher — LLM-routed, no keyword taxonomy

Per Elias: *"stop hard-coding links between the bank and prompts… a light prompt
gives near-perfect results, especially if the machinery is already here."* So the
matcher reuses the generation lane's machinery exactly like
`harness/gen/skill_context.py::_llm_route`:

- **`route_assets(game_context, bodies, manifest=None, *, use_llm=True,
  cache_path=None, complete_fn=None) -> {name: id|None}`** — one light
  `_openrouter_complete` call. The model reads the manifest MENU (id +
  description + dims) plus the game's prompt and body list (name + shape/size),
  and returns a body→asset-id JSON mapping (or `null` → primitive dressing).
  Unknown/invalid ids are validated out to `None`. The mapping is **cached to
  `<game>.assets.json`** so demo captures reproduce without re-calling the model.
- **`match(name, shape_info=None, manifest=None, *, use_llm=False, …) -> id|None`**
  — single-body convenience.
- **Offline fallback** (`use_llm=False`, or backend unavailable) — deliberately
  trivial: exact / substring name↔id, else `None`. Not a taxonomy; it exists only
  so offline tests and degraded runs stay sane (mirrors skill_context's BM25
  fallback). Tests inject a fake `complete_fn` — no network (see
  `tests/test_asset_bank.py`).

There is intentionally **no hand-coded `"cart"→vehicle` alias table** in the
production path; the tags in the manifest are curation data for the menu, not a
code taxonomy.

## 3. Loader — the render-only API the dresser consumes

`godotworld/asset_loader.gd`, `class_name AssetLoader`:

```gdscript
static func load_asset(asset_id: String, manifest_path: String,
        target_size: Vector3 = Vector3.ZERO,   # desired world AABB; ZERO = native
        scale_mode: String = "fit",            # "fit" | "fill" | "stretch"
        anchor: String = "center") -> Node3D   # "center" | "base"
static func read_manifest(manifest_path: String) -> Dictionary
```

Guarantees:
- **Render-only, provably physics-free.** The scene is loaded via
  `GLTFDocument.append_from_file` + `generate_scene`, then **flattened**: only
  `MeshInstance3D` meshes are copied into a fresh `Node3D`, each carrying its
  composed transform. Every `CollisionObject3D` / `CollisionShape3D` / `Area3D` /
  light / camera / empty is simply never copied. The returned tree has **zero
  physics nodes** (asserted by the in-image test).
- **Scaled to the target AABB.** `stretch` = per-axis exact; `fit` = uniform, the
  largest axis touches the target; `fill` = uniform cover. `anchor` recentres the
  pivot (`center`, or `base` = min-Y at origin for ground placement).
- Returns a plain `Node3D` (`AssetDress_<id>`) the dresser positions/rotates and
  adds as a **child of the game body's node** — cosmetic, so G0–G4 and the serve
  `state()` (physics-only) are unaffected.

**Hard-won gotcha:** runtime GLTF loading only works when the GLTF import
subsystem is initialised — i.e. Godot must boot a real project. Run the tools
with `--path <godotworld>`; a bare `--script` run has `append_from_file` fail on
every asset (and crash on multi-file `.gltf`). The RID-leak warnings the dummy
renderer prints at exit are harmless.

## 4. Integration (after both lanes merge)

The dresser owns `godotworld/visual_dress.gd` + the capture command. Per-game flow:

1. From the loaded game, collect body `{name, shape, size}` (from `state()` / the
   scene). Call `asset_bank.route_assets(game_prompt, bodies,
   cache_path="<game>.assets.json")` → `{name: id|None}`.
2. For each body with a non-null id, call `AssetLoader.load_asset(id,
   manifest_path, body_aabb_size)` and add the returned node as a child of that
   body (hide/ghost the primitive mesh if desired). `None` → keep the primitive.
3. Verification is untouched: assets carry no colliders; the game's own bodies
   drive all physics and every oracle. Capture renders the dressed scene.

Clean seam: this lane ships the library + manifest + a stable API; the dresser
consumes `route_assets` (py) and `AssetLoader.load_asset` (gd). No shared mutable
state; the cache file is the only handoff artifact.

## 5. P2 — designer-agent MCP retrieval (future)

Today the matcher is a one-shot router over a small committed menu. The forward
path (aligns with `notes/parts_bank/mcp_tools.md` + `retrieval.md`): expose the
bank as an **MCP retrieval tool** the designer agent queries mid-generation
("give me a vehicle ~4 m long, round obstacle, a goal marker"), returning
id + description + dims. That makes the bank a *live* catalog the designer draws
from, and lets it scale to hundreds of assets (offline CC0 growers: Kenney/KayKit
packs, then a Blender factory per `BLENDER_ASSETS_AND_INSPECT.md §1`) without the
prompt ever anchoring on bank contents — the menu is retrieved, not hard-listed.
The manifest schema (id/description/tags/dims) is already the retrieval record;
`route_assets` is the shot-caller that an MCP tool would wrap.

## 6. Status / tests

- `tests/test_asset_bank.py` — 15 tests: offline fallback (semantic-ish names →
  sensible ids; junk → None), LLM route with a mocked `complete_fn` (mapping,
  id-validation, code-fence tolerance, cache reproducibility, failure→fallback),
  real-manifest smoke (≥8 assets, all archetypes, measured props). All green.
- `godotworld/tests/test_asset_loader.gd` — in-image: loads car/robot/chest,
  asserts meshes present + **0 physics nodes** + correct scaling, unknown id →
  null. All green (`LOADER_DONE pass=4 fail=0`).
- Manifest: 21 assets, AABBs measured in-image, covering all five named
  archetypes.
