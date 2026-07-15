# Blender as an offline asset factory + engine-backed inspect_world

> 2026-07-15, commissioned by Elias, answering the two standing asks: (1) is
> there an MCP to pair with Blender? (2) we still need an inspect_world for
> physics/feasibility. Short answers: yes — two — but neither belongs in our
> loop; and here is the engine-backed inspect_world spec, ready to build.

## 1. BLENDER VERDICT — split the call along the consumption ladder

The call is **BOTH, by lane** — not one tool doing two jobs:

- **ADOPT headless-scripted for the asset factory.** Blender needs NO MCP to be
  an offline bank-grower. A pinned portable Linux tarball + our own deterministic
  `blender -b --python` scripts is fully viable and matches our ladder exactly:
  offline, outputs cached/committed/versioned, nothing from it runs in the harness.
- **MCP is Elias's human-authoring lane only** — on his own workstation, never ours.
- **REJECT the MCP/addon from the harness, the loop, and the cluster.**

**Cluster recipe (factory):** vendor Blender **4.5.x LTS** portable tarball
(`blender-4.5-linux-x64.tar.xz`, ~358 MB, no root) into `~/orcd/scratch` (HOME is
87% full — extract to SCRATCH). Run `blender -b --python gen_asset.py -- <seed/params>`
to procedurally build meshes, then `bpy.ops.export_scene.gltf(...)` → GLB. Pure
geometry+GLB export needs **no GPU/display**. Bake a **provenance manifest**
(seed, script hash, param set, license) beside each GLB. Heavy runs go on
**SLURM compute nodes, never the login node**. Pin ONE LTS or GLBs drift across
Blender point releases.

**Security line (hard block).** ahujasid/blender-mcp is unfit for us: its addon
runs raw `exec(code, {"bpy": bpy})` with no sandbox (addon.py:491), makes outbound
cloud calls (PolyHaven/Sketchfab/Hyper3D/Hunyuan), ships **default-on telemetry**
of prompts/code/screenshots, and is GUI-socket-bound. That violates every standing
rule (nothing from packs executes in the harness; external tools are offline-only;
determinism-first). Never run it on the cluster.

**Q1, honest answer:** yes, two MCPs exist. **ahujasid/blender-mcp** (MIT, v1.6.0,
actively maintained — the de-facto major one) and the **official Blender Lab MCP**
(v1.0.0, needs Blender 5.1+). BOTH are interactive addons on the same running-Blender
GUI-socket model; neither is a headless asset factory. Fine on Elias's box, not ours.
MIT license lets us **quarry** their import/bbox patterns as references without
running their code.

## 2. THE EXAMPLES RECIPE — their Blender→Godot workflow, and what we adopt

The premise "the examples use many addons" is **false for authoring**: across all
20 envs the only addon anywhere is the RL runtime `godot_rl_agents` — zero
importer/editor/tool addons. 3D content is **Godot-core import only**. Three
workflows coexist: (A) native `.blend` import (needs Blender at import time,
nondeterministic); (B) GLB kit (Racer sets `import/blender/enabled=false`, ships
Kenney CC0 GLBs, keeps `.blend` source-only); (C) zero external assets (FPS =
100% Godot primitive meshes + CSG).

**Bank conventions we adopt:**

- **Standardize on GLB, not native `.blend`.** Native import shells out to the
  Blender binary at import time → version/OS-dependent, needs an install, violates
  determinism-first. Commit `.glb` + a manifest (uid/hash/license); keep `.blend`
  as **offline source only**.
- **Layout:** `<kit>/<object>.glb`, flat, one object per file, lowercase_snake
  role-named (`road_tile.glb`, `sedan.glb`); +Y up, meters, pivot at logical base;
  one `LICENSE`/`asset-license.md` per kit dir. **Prefer CC0 (Kenney)** so no
  attribution obligation propagates into every generated game.
- **Collision is authored in-engine, never imported.** ConvexPolygonShape3D for
  dynamic bodies, Area3D+shape for triggers, trimesh StaticBody for static kit
  meshes. This keeps the GameAPI serve contract authoritative over physics.
- **Default tier = zero-asset primitives + CSG (the FPS model)** — no pipeline,
  fully deterministic. Treat GLB kits as an optional visual upgrade, never a
  requirement.
- Blender collision-name suffixes (`-col`/`-colonly`/`-convcol`/`-rigid`,
  `use_node_type_suffixes`) matter **bank-side only**; our runtime collision wins.

## 3. ADDON POLICY for generated games — the decision, framed

The question is **blanket ban vs allowlist**. Recommendation: **keep the blanket
ban.** It costs nothing on the asset side — no importer/tool addon exists in the
whole corpus; GLB and `.blend` import are Godot-core. The single addon that appears,
`godot_rl_agents`, is a **runtime library** (sync/sensors/onnx/controllers) —
exactly the role our GameAPI serve contract already fills.

So do NOT open a general addon allowance. Instead carve **ONE narrow exception**:
a vetted, versioned, **first-party "harness runtime" library** we inject (our
equivalent of `godot_rl_agents`), so generated games get reusable sensor/sync
helpers without ever loading third-party addons. The ban stays; a first-party
runtime is not an addon in the banned sense. Inject and version it **centrally**
(the examples' per-env vendoring drifted — don't repeat that).

## 4. INSPECT_WORLD-GD — engine-backed tool spec

The old inspect_world (`harness/designer/tools.py`) was a frozen, engine-FREE
static tool recomputing AABBs from spec fields; it died with the spec lane, because
GDScript games are code, not a parseable spec. The replacement rides the **same
serve wire both lanes speak** (init/reset/act/state → obs_state{pos,vel,angle,
angular_vel,bbox,shape,static,sensor,controlled}, checkpoints, done). The **engine
becomes the source of geometric truth**; Python only does set-ops on wire-provided
boxes plus differential probes. It is a harness designer-tool (a serve probe),
allowed in the loop; the only untrusted code that runs is the agent's own game.

**Signature:** `inspect_world(game_path, *, tier='settle', settle_ticks=90,
action_probe_ticks=8, rollout_reps=4, rollout_ticks=60, seed=0, sandboxed=True)
-> dict`. Model-facing schema exposes **only `{game_path, tier}`**; harness knobs
stay hidden. `tier` is a certify-style depth ladder: snapshot | settle(default) |
actions | full.

**Cost-tier checks** (reuse frozen tolerances verbatim: _OVERLAP_EPS 0.5,
_BOUNDS_EPS 0.5, _SUPPORT_TOL 4.0, _REACH_TOL 40.0, _STILL_EPS 30):

- **Tier A — init snapshot** (0 extra ticks): entity table from obs_state; read the
  engine's own `bbox` (don't recompute). Checks: `out_of_bounds`,
  `overlap_solid_statics`, `isolated_sensor`, `floating_static` (advisory),
  handshake sanity; emit `spawn_failed`/`init_failed` from caught GodotServeError.
- **Tier B — null-action settle** (default; +settle_ticks of empty-action physics):
  `never_settling` (the MEASURED replacement for static `unsatisfiable_park`/
  `forecast_oob` — observe, don't predict), `out_of_bounds`-after-settle,
  `physics_blowup` (act frame `result=='error'` from `_sane()`: NaN or speed>1e5).
  **This tier is the MVP** — it alone restores the layout+settle signal.
- **Tier C — action probe:** null baseline vs per-action `reset→act`, differential
  compare; `dead_action` if every body's delta < DEAD_EPS. **Advisory only** — proves
  dead FROM THE INITIAL STATE, so context-gated actions can false-positive.
- **Tier D — determinism smoke + reachability sketch:** exact SHA-256 equality of
  the `%.17f` state digest across two seeded loads → `nondeterministic` (catches
  gross unseeded-RNG/wall-clock/cross-reset leakage only). Random-rollout
  reachability is a coverage **SKETCH, never a solvability claim**.

**Taxonomy — repair loop UNCHANGED.** Keep the `{kind,bodies,detail}` shape so the
repair-context splice (ASYMMETRY §2) needs no change. Preserved (now engine-backed):
out_of_bounds, overlap_solid_statics, isolated_sensor, floating_static. Renamed to
measured: unsatisfiable_park→never_settling, forecast_oob→oob-after-settle. New
engine-only: dead_action, nondeterministic, unreachable, physics_blowup,
spawn/init_failed. **Dropped:** predicate_lint (predicates are now code → G0's job),
rotatable_containment (goal is code).

**2D/3D/view.** Branch on an init-handshake descriptor `{dim:2|3, view:side|topdown,
gravity_vector, world_bounds}` — never hardcode 2D-side (the old tool hardcoded
`(0,-900)`). Set-ops generalize to 6-float 3D AABBs. Gravity-dependent checks
(floating vs supported) run ONLY along a real gravity axis; zero-gravity topdown →
that check is meaningless, replaced by `never_settling`. 3D `PhysicsServer3D.set_active`
quirk: a body frozen at init surfaces as **advisory** (intentional-static vs forgotten
activation), not a hard fail.

**Security (hard requirement).** The serve subprocess spawns **env-scrubbed** —
minimal allowlist to Popen, strip OPENROUTER/ANTHROPIC keys, `env.py` unreadable from
the game process. This is a **delta** from today's env-inheriting Popen
(godot_env.py:256-264). ONE process per call, reused across tiers, closed in
`finally`; caps small; heavy tiers under SLURM.

**What stays with the G-gates** (unrecoverable from `state()`): `duplicate_name`
(obs_state is a dict — dup names collapse), `layer_mask_mismatch`, `nonconvex_poly`,
`contact_cap` — these need a proposed read-only `describe` verb, else they fall to
the **G0 parse + banned-API scan**. Solvability/winnability = **G3/G3'** solver, not
this tool. Full two-run determinism drift = **G1**. inspect_world is **not the
security boundary** — it presumes G0 passed and still spawns scrubbed because it
DOES execute the game.

## 5. BUILD ORDER

1. **Asset factory:** vendor pinned Blender 4.5 LTS to `~/orcd/scratch`; write
   deterministic (seeded + content-hashed) `blender -b --python` generators emitting
   GLB + provenance manifest with Godot collision-name conventions. Offline only,
   SLURM for heavy runs.
2. **inspect_world MVP = Tier A+B:** env-scrub wrapper over the existing
   GodotServeEnv serve seam; read wire bbox/pos/vel; port the 5 geometry checks onto
   live boxes; add never_settling + oob-after-settle + physics_blowup + spawn/init_failed.
   Old taxonomy preserved; **touches no frozen file**.
3. **Register** inspect_world in the designer REGISTRY with the frozen `{game_path,
   tier}` schema; wire warnings into the repair context (the ASYMMETRY §2 feedback lever).
4. **Tier C (dead_action) + Tier D (determinism smoke + reachability sketch)**,
   both budget-gated with small per-call caps.
5. **GameAPI contract obligations:** init handshake declares `{dim,view,
   gravity_vector,world_bounds}`; null-action = step-only; add a read-only `describe`
   verb to reclaim duplicate_name/layer_mask/nonconvex_poly/contact_cap.
6. **First-party harness-runtime library** (the addon exception): injected and
   versioned centrally.
