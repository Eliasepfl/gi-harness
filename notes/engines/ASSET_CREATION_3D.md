# 3D asset/scene creation on our stack — decision-grade analysis (LONG3D Track A)

> 2026-07-16, commissioned by Elias. Question: now that the 2D-hardcode bug is fixed and
> 3D games are unlocked, can 3D OBJECT/SCENE CREATION be managed on our stack — via an MCP,
> headless Blender, or pure code — so generated 3D games stop looking like naked primitives
> and start looking like the `godot_rl_agents_examples` demos?
>
> Companions: `LONG3D_GAP_ANALYSIS.md` (the gap this closes), `ASSET_BANK.md` (the existing
> render-only bank + dresser), `BLENDER_ASSETS_AND_INSPECT.md` (Blender factory verdict),
> `MCP_FEEDBACK_TOOLS.md` (MCP-server verdict). This note re-verifies those quickly and adds
> the missing measurement: **how far pure-GDScript code-built low-poly geometry actually goes**.

## 0. The constraint that shapes every answer

Generated games are **ONE self-contained `.gd` file**; `load()`/`preload()` and external
assets are **BANNED** in game code (`api_gdscript.md:46,64`) — certification is pixel-blind
(reads only `state()`), and the ban keeps games hermetic. There are exactly **three**
insertion points where richer geometry can enter, and they differ in *what* they enrich:

| # | Insertion point | Enriches the… | Runs where |
|---|---|---|---|
| 1 | CAPTURE-TIME dressing (`visual_dress.gd` + asset bank) | render **overlay** only | in-image capture lane |
| 2 | IN-CODE meshes (`SurfaceTool`/`ArrayMesh`/CSG in `build()`) | **artifact itself** (hermetic) | serve + capture, in-image |
| 3 | EDITOR-TIME MCP server | (nothing that fits) | — |

The load-bearing fact, dug out of the contract this pass: **api_gdscript.md line 46 already
BLESSES insertion point 2 by name** —

> "you MAY attach render-only nodes, built in code from primitives … a `MeshInstance3D` +
> `StandardMaterial3D` on a body … no external assets and no `load()`/`preload()` (both
> banned — **construct any mesh/material in code**)."

So the sandbox is not the obstacle for (2); it is an explicit invitation.

## 1. Quick re-verification of the environment (don't relitigate)

Measured this pass on the production stack (`gi-certifier.sif`, Godot 4.7):

- **Blender: ABSENT.** `module avail blender` → nothing; no `blender` on the login-node PATH
  or in the image. A factory would first need the pinned portable 4.5-LTS tarball vendored to
  `~/orcd/scratch` per `BLENDER_ASSETS_AND_INSPECT.md §1` (HOME is 84% full — SCRATCH only).
- **Node.js: PRESENT in-image** (`/opt/conda/envs/gi/bin/node` v22.23.1, npm 10.9.8), even in
  a non-login shell — **a correction to `MCP_FEEDBACK_TOOLS.md §1.1/§4`** ("no Node.js … inside
  gi-certifier.sif"). The login node still lacks it, but the image no longer does.
- **Godot 4.7** in-image; `SurfaceTool`/`ArrayMesh` and the software-GL capture lane work
  (this note's prototype renders through them).

The node correction does **not** move the MCP verdict — see §2 (3).

## 2. Per insertion point

### (1) Capture-time dressing + a Blender factory
- **Feasibility on our cluster:** the dresser **already exists and ships** — `visual_dress.gd`
  builds colored primitive proxies matched to collision shapes, and optionally swaps in
  render-only bank GLBs (21 curated assets, `AssetLoader` strips all physics; proven by
  `test_asset_loader.gd` → 0 physics nodes). A **factory** to mint NEW per-fantasy assets is
  feasible only OFFLINE (vendor Blender to scratch, seeded `blender -b --python` → GLB +
  provenance; CPU-only, SLURM for batches). Putting Blender **in the generation loop** for
  live per-fantasy assets is rejected (latency + determinism; `BLENDER_ASSETS_AND_INSPECT.md`).
- **Cost:** one-time Blender vendoring (~358 MB) + writing deterministic generators; per-asset
  offline SLURM minutes. **Zero per-game runtime cost** (assets pre-baked + committed).
- **Who writes the code:** a **factory tool** (deterministic `bpy` scripts), not the designer
  model; the curator commits GLB + manifest. The dresser consumes them via the existing router.
- **Certification impact: ZERO** — render-only, physics-stripped, identical to today's bank lane.
- **What the demos gain:** artist-grade low-poly meshes — **but only in the captured overlay;
  the game artifact stays naked primitives.** And gain is capped by bank coverage of the
  fantasy; a factory *grows* coverage but each asset is offline-authored. The **matcher already
  routes existing assets**, so the factory is a coverage play, not a new capability.
- **Verdict:** valuable as a bank-grower and a natural P2 of the asset lane (`ASSET_BANK.md §5`),
  but it needs an install that isn't there, improves only the render, and duplicates coverage
  the matcher already provides. **Not tonight's unlock.**

### (2) In-code procedural meshes  ← the answer
- **Feasibility on our cluster: MAXIMAL.** Pure GDScript, runs in the lane we already own. **No
  new toolchain, no Blender, no Node, no vendoring.** The contract explicitly permits it (§0).
  `SurfaceTool`→`ArrayMesh` verified working in-image (this prototype).
- **Cost:** two deployment modes, two cost profiles —
  - **host-side dresser library** (`mesh_lib.gd`, the recommended first move): **~0 extra
    designer tokens**, ~0 latency (mesh build is microseconds), model-independent, deterministic.
  - **inline in `build()`** (the designer model writes it): **~1–1.5k generation tokens/game**
    (~50–90 lines of mesh code); the reward is a hermetic artifact that is itself richer.
- **Who writes the code:** EITHER the harness/dresser (host-side `mesh_lib.gd`, free & universal)
  OR the designer model (inline, hermetic — proven to compile standalone by `mesh_demo_game.gd`).
- **Certification impact: ZERO.** Meshes are render-only children (no collision, absent from
  `state()`); pixel-blind cert can't see them. The inline form's mesh code is G0-scanned but uses
  only whitelisted APIs (`SurfaceTool`/`ArrayMesh`/`StandardMaterial3D`) — no `load`/`preload`/
  reflection. Test asserts the exemplar game's 4 render meshes carry **0 physics nodes**.
- **What the demos gain (MEASURED, see §3):** recognizable low-poly **car** (body+cabin+4
  wheels), **rock spire**, and **ring gate** from ~50 lines each, all ≤200 tris, rendered
  in-image. This is exactly the "stop looking like naked primitives" ask — and it can upgrade the
  **artifact**, not just a capture overlay.
- **Polygon budgets & writeability:** car **72 tris / 216 verts**, spire **12 tris**, ring **196
  tris** — trivially low-poly (the demos' meshes run 100s–1000s). The geometry is parametric loops
  + a reusable box/torus/cone helper; a model already emitting 250–320-line games at 79% mechanic
  fidelity can emit this. The helpers are the shareable core (host-side lib) or the inlined twin.
- **Verdict: RECOMMENDED.** Highest visual-uplift-per-dollar on the board, zero cert risk, no new
  infra, and the only density lever that lives INSIDE the contract.

### (3) Editor-time MCP servers
- **Re-verify (quick):** Node.js is now present in-image (§1) — the prior "no Node on cluster"
  premise is partly outdated. **The verdict is unchanged anyway, for structural reasons:**
  (a) **no seam** — the lane is a scene-less, one-file, in-memory-compiled artifact; MCP servers
  are editor / scene-CRUD tools with nothing to attach to; (b) they bypass the env-scrub +
  `--fixed-fps` determinism pins our three spawners enforce; (c) they are RCE-class exec surfaces
  pointed at generated content; (d) `blender-mcp` specifically ships default-on telemetry +
  unsandboxed `exec`. **Confirmed workstation-only** (Elias's human-authoring lane). Not a path.

## 3. Prototype — pure GDScript builds recognizable low-poly, in-image

Built and run this pass (all files NEW; no frozen/gen/verify/rl file touched):

| File | Role |
|---|---|
| `godotworld/mesh_lib.gd` | `class_name MeshLib` — static `car()`/`spire()`/`ring()` → `ArrayMesh` with baked vertex colours. The reusable host-side library (for the dresser first experiment) and the reference the inline form mirrors. |
| `harness/demo/mesh_demo_game.gd` | The **insertion-2 exemplar**: a fully self-contained game (no `class_name`, no `load`/`preload`) that dresses ITSELF with the inlined mesh builders. Compiles standalone under `godot --check-only`. |
| `godotworld/tests/render_showcase.gd` | Minimal render host (software-GL, no dresser) → one PNG of the three shapes. |
| `godotworld/tests/test_mesh_lib.gd` | In-image unit test: mesh budgets, proportions, determinism, and that the exemplar game exposes 1 controlled body + ≥4 render meshes carrying 0 physics. |
| `harness/demo/render_meshes.sh` | Xvfb + x11-lib staging (mirrors `scripts/capture_demo.sh`) → runs the render host in-image. |

**Test result** (`godot --headless --script res://tests/test_mesh_lib.gd`):

```
MESHTEST_OK car   tris=72  verts=216 aabb=(4.00,1.40,2.50)
MESHTEST_OK spire tris=12  verts=36  aabb=(2.62,6.00,2.49)
MESHTEST_OK ring  tris=196 verts=588 aabb=(4.80,0.78,4.68)
MESHTEST_OK spire deterministic per seed
MESHTEST_OK game meshes=4 bodies=3 controlled=1
MESHLIB_DONE pass=5 fail=0
```

`mesh_demo_game.gd` also passes `godot --check-only --script …` with **no project context** —
i.e. it is a legal, hermetic, `load()`/`preload()`-free artifact.

**Proof image:** `harness/demo/mesh_proof.png` — rendered in-image via x11 + opengl3 + llvmpipe
(the capture lane's exact mechanics). It shows a recognizable red car (body + dark cabin + 4
black wheels), a grey tapered rock spire, and a green ring gate on a ground plane. Regenerate
with `bash harness/demo/render_meshes.sh`. (Cosmetic note: the ring sits at a shallow angle in
this shot; a per-shape orientation is trivial to set at placement time.)

## 4. THE ONE RECOMMENDED PATH + first experiment

**Recommended: insertion point (2) — in-code procedural low-poly meshes — deployed FIRST as a
host-side dresser library (`mesh_lib.gd`) that the zero-contact capture dresser calls to replace
naked-primitive proxies, and OFFERED to the designer model as the inline `api_gdscript.md:46`
pattern once the library shapes prove out.**

Over (1): needs no install (Blender is absent), upgrades EVERY demo not just bank-covered ones,
and can enrich the artifact — not only the render. Over (3): no seam, workstation-only.

**First experiment (for the dresser owner — I did not modify `visual_dress.gd`; new files only):**
add a THIRD 3D-proxy option in `visual_dress.gd` beside "primitive box/sphere" and "bank GLB": a
`MeshLib` shape chosen by the **same LLM router that already routes bank assets**
(`asset_bank.route_assets`), keyed by body role (vehicle→`car`, obstacle/tower→`spire`,
gate/goal/ring→`ring`). **Measure:** (a) do the three long3d 3D demos (gen_0 fly-rings, gen_1
parking, gen_2 drone) read as "a car / a spire / a gate" vs. a box; (b) the capture-lane identity
test stays byte-identical (zero cert impact); (c) zero added designer tokens. It reuses two
existing seams (the dresser + the router) and touches ONE non-frozen file.

## 5. Honest verdict — is this worth Elias's budget?

- **YES for (2) as a dresser library.** A few hundred lines of deterministic GDScript, no new
  infra, zero cert risk, closes the "naked primitives" gap for **every** demo at **~zero per-game
  cost**. Because "looking like the demos" to a human/judge is largely the capture render — which
  the dresser controls — swapping box proxies for recognizable `MeshLib` shapes is the exact
  leverage point. It is a strict, cheap improvement over the primitives+palette we render today.
- **YES-but-later for the inline-in-game form.** It enriches the artifact itself, but it is gated
  on the model actually choosing 3D — and per `LONG3D_GAP_ANALYSIS` Track C the model still
  collapses every 3D prompt to 2D (the stale `_first_user_msg` "2D" bug). **Fix dimension first
  (Track C), ship the free dresser-library uplift now, invite inline meshes as a stretch.** Don't
  ask a model that won't pick 3D to also hand-author meshes.
- **NO for the Blender factory tonight.** It needs an install that isn't there, improves only the
  render, and duplicates coverage the bank matcher already gives. It is the asset lane's future
  P2, not the unlock.
- **NO for MCP.** No seam; workstation-only; the node-in-image correction doesn't change that.

**Bottom line:** the widest visual gap vs. the demos is real but mostly *contract-imposed* (no
assets, one file — `LONG3D §4`). Insertion (2) is the **one** density lever that lives inside the
contract, so it is the only path that narrows the gap without touching the sandbox — and the
prototype proves pure GDScript already clears the "recognizable, not a primitive" bar in ≤200
tris per shape. Ship it at the dresser first (free), then invite it inline once 3D is unlocked.

Proof image: `harness/demo/mesh_proof.png`.
