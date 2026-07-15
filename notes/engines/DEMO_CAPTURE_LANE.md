# Demo-capture lane — real Godot camera renders for working-run GIFs

> 2026-07-15, commissioned by Elias: use Godot's own camera + renderer to make
> demo GIFs of a WORKING (certified) run, even from the headless cluster.
> Verified against docs.godotengine.org/en/4.7 (Movie Maker + command-line).

## The hard fact

`--headless` == `--display-driver headless --audio-driver Dummy` — a DUMMY
rasterizer that renders **zero pixels** (`Viewport.get_image()` is blank).
Every certification run is pixel-blind by design (the moat). Real capture must
NOT use `--headless`; it needs a display + a software renderer.

## Architecture: capture is a SEPARATE lane from certification

- **Certification** stays headless / deterministic / typed-state → the certificate.
- **Capture** is a SECOND pass that REPLAYS the already-certified witness
  (same `seed` + `actions[]`) through a rendered run. What you see is provably
  the certified game; the render pass being non-deterministic (software-GPU
  timing) never touches the verdict. Capture reads pixels, never writes them
  into any oracle.

## The recipe

1. **Virtual display + software GPU** (no physical GPU): `xvfb` (headless X
   server) + Mesa **lavapipe** (software Vulkan) or `--rendering-driver
   opengl3` on **llvmpipe**. Standard Godot-CI-on-Actions pattern.
2. **Movie Maker** (docs/animation/creating_movies + command_line):
   ```
   xvfb-run -a "$GODOT" --path godotworld \
       --write-movie demo.avi --fixed-fps 60 --quit-after <witness_ticks>
   ```
   - `--fixed-fps` is FORCED in movie mode → deterministic frame timing that
     matches the certified tick sequence.
   - `--quit-after N` bounds output to exactly the witness length.
   - `--disable-vsync` speeds writing. Output: AVI (MJPEG) or PNG sequence.
   - `ffmpeg` converts AVI/PNG → GIF (`fps`, `scale`, palettegen for quality).
3. **Camera2D following the player** (docs knobs, from GODOT_DOCS_MINING §5):
   `zoom` is a MULTIPLIER (>1 zooms in); `position_smoothing_speed` ≈5.0 px/s
   default; `limit_*` bounds; **`process_callback = CAMERA2D_PROCESS_PHYSICS`**
   so the camera tracks in lockstep with the replay. 3D: a Camera3D rig
   (follow + look-at the controlled body).

## Image delta (a VARIANT .sif, base stays lean)

Add to a `gi-capture.sif` (NOT the certifier): `xvfb`,
`mesa-vulkan-drivers` + `libgl1-mesa-dri` (lavapipe/llvmpipe),
`libegl1`, `ffmpeg`. The base `gi-certifier.sif` stays pixel-free and small.
Cost: software rasterization of simple 2D scenes is cheap (~real-time/episode
on a CPU node); run SLURM-side, never on the login node.

## Build order

1. `gi-capture.def` = certifier def + the packages above; build on a login node.
2. A `harness game capture <game> --gif <out>` verb: verify → get the witness →
   replay it under `xvfb-run … --write-movie` (GameAPI serve host in a
   camera-equipped scene, or the runner in a render mode) → ffmpeg → GIF.
3. The GameAPI contract gains an OPTIONAL `camera()` hook (a game may hint
   framing; default = auto-frame the controlled body). Capture-only; the
   certifier ignores it.

## Gotchas (docs-confirmed)

- Movie mode needs a rendering CONTEXT — the docs give no headless-render flag;
  xvfb+software-Mesa is the supported substitute, not an engine feature.
- OGV writer is editor-only; use AVI or PNG from the command line.
- Determinism of the RENDER is not guaranteed (nor needed) — the WITNESS is the
  determinism anchor; capture just visualizes it.

## Camera: FIT-TO-SCENE overview, not follow (Elias, 2026-07-15)

To JUDGE a game you must see the WHOLE scene; a tight follow-cam on the agent
shows only a window. Default demo camera = a static fit-to-scene overview
computed from the AABB/bounding-sphere of all bodies at build time.

- **2D:** `Camera2D.zoom` is a MULTIPLIER (<1 zooms OUT / shows more). Center on
  the bodies' AABB centre; `zoom = min(vw/scene_w, vh/scene_h)` with ~10% margin;
  `position_smoothing_enabled=false`; `anchor_mode=DRAG_CENTER`.
- **3D:** `Camera3D.projection=PROJECTION_ORTHOGONAL`, `size=scene_extent+margin`,
  placed above-and-back at an isometric angle, then `look_at(centre, Vector3.UP)`.
  Perspective alt: distance `d = radius / tan(fov/2)`, `look_at(centre)`.
- Follow-with-generous-margin only as a fallback for courses too large for one
  frame; never the default. Docs (429'd this pass) to confirm the exact
  Camera2D.zoom/limit + Camera3D.size semantics before implementing.
