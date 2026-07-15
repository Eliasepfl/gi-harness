# Demo-capture lane — real Godot camera renders for working-run GIFs

> 2026-07-15, commissioned by Elias: use Godot's own camera + renderer to make
> demo GIFs of a WORKING (certified) run, even from the headless cluster.
> Verified against docs.godotengine.org/en/4.7 (Movie Maker + command-line).

---

## ✅ SHIPPED 2026-07-15 — what ACTUALLY worked (measured in-image, supersedes the speculation below)

**The lane is live and needs NO .sif rebuild on ORCD.** Two proof GIFs of certified
games render end-to-end on the login node:
`~/orcd/scratch/gi/demos/fly_a_craft.gif` and `.../drive_a_cart.gif`.

**Empirical facts about `~/gi/gi-certifier.sif` (probed, not assumed):**
- Godot 4.7 present at `/opt/godot/godot`. Mesa **swrast/llvmpipe** DRI + `libGL` present.
- **NO Vulkan ICD** → the Forward+/Mobile renderers can't init; only `gl_compatibility`
  (OpenGL) is usable. **NO `libEGL`/`libgbm`** → no surfaceless offscreen GL.
  **NO Xvfb, NO ffmpeg, NO x11 client libs** in the image. PIL 12.3 IS present.
- `--headless` renders ZERO pixels (dummy rasterizer) — confirmed. `--display-driver x11`
  with no server fails: `libxkbcommon.so.0: cannot open shared object file` + `X11 Display
  is not available`, wayland fallback fails on `libwayland-client.so.0`.

**The working path (cheapest, zero build):**
- Run **Xvfb on the HOST** (ORCD login/compute nodes have Xvfb + all x11 libs in
  `/usr/lib64`). Bind `/tmp/.X11-unix` into the container + export `DISPLAY`.
- Stage the ~14 x11 **client** libs Godot dlopens (`libX11`, `libxkbcommon`, `libXcursor`,
  `libXi`, `libXrandr`, `libXinerama`, `libXext`, `libXrender`, `libxcb`, `libXau`,
  `libXdmcp`, `libXfixes`, …) from the host into a bind dir on `LD_LIBRARY_PATH`. Host
  glibc 2.28 < container 2.35, so they load fine. (One benign warning: el8 `libX11` lacks
  `XSetIOErrorExitHandler`; Godot dlsym-falls-back and renders anyway.)
- `godot --resolution WxH --display-driver x11 --rendering-driver opengl3 --fixed-fps 60
  -s res://capture_host.gd` with `LIBGL_ALWAYS_SOFTWARE=1` → **OpenGL 4.5 on llvmpipe**,
  real pixels. `scripts/capture_demo.sh` automates all of this.

**NOT movie-maker — manual per-tick viewport capture.** `--write-movie` records every
rendered frame, which is awkward to align to the K=6 decision-tick cadence. Instead the
capture host controls its own loop (mirroring `serve_game.gd`: `act` + K=6 `await
physics_frame`), then per decision tick does `RenderingServer.force_draw(false)` (a
**synchronous** render that steps NO physics) + `viewport.get_texture().get_image()` +
`save_png`. Exactly one PNG per decision tick; PIL assembles the GIF (no ffmpeg). This
decouples render from stepping so the physics trail is byte-identical to the witness.

**Dressing = ZERO-CONTACT overlay (`visual_dress.gd`), NOT children-on-bodies.** Per
Elias' safety rule: the dresser NEVER adds a node to the game or a physics node anywhere;
visual proxies (Polygon2D/Line2D 2D, MeshInstance3D 3D) live in a SIBLING subtree and each
rendered frame MIRROR the bodies' global transforms (read-only). The game tree/physics are
never touched → **dressed and undressed replays share a byte-identical `state()` trail**
(proven headless-free identity test: fly, drive, mini_collect 2D, mini_collect_3d — all
PASS). Static walls/segment-shapes ARE drawn (the dresser walks the tree for collision
shapes, since `state()` omits them), so the arena/track shows.

**Camera:** fit-to-scene overview by default (Camera2D `zoom` = min(view/scene) both axes;
Camera3D perspective at `d = radius/tan(fov/2)` isometric); `--follow` follows the agent.
An oversized backdrop polygon fills the letterbox (no gray border). A translucent halo
tracks the controlled body so it stays legible in a wide overview.

**Frame-rate/duration:** capture one frame per decision tick (a decision tick = K=6 physics
frames @ 60 Hz), GIF played at `--fps` (default 20). Long witnesses auto-subsample to
`--max-frames` (default 300; the drive lap used 150 to keep the GIF ~2 MB). Downscaled to
640 px wide. fly = 56 ticks → 0.29 MB; drive = 294 ticks → 2.1 MB.

**Deliverables:** `godotworld/{capture_host.gd, visual_dress.gd, demo_player.gd}`,
`harness/verify/capture.py`, `harness game capture <game.gd> [--follow] [--out] [--actions]
[--max-frames] …`, `scripts/capture_demo.sh` (ORCD host wrapper),
`godotworld/tools/gi-capture.def` (OPTIONAL portable image; only needed off-ORCD where the
host lacks Xvfb/x11 — bakes xvfb + x11 libs in, `HARNESS_CAPTURE_XVFB=1`, not built here).

**Local play (desktop, needs a GPU):** `godot --path godotworld -s res://demo_player.gd --
--game=<abs.gd> [--witness=<json>]` to watch, or `--drive` to play the agent with number
keys (1..N = `actions()`, R = reset, Esc = quit).

**Known limitation:** the overlay's rendering nodes stall the dummy display server under
`--headless` (an attach-time hang) — irrelevant, capture NEVER runs headless (that's the
pixel-blind certification driver). The identity test therefore runs under x11 too.

**Still not great / future:** GIF palette is flat (llvmpipe + PIL 256-color); 3D lighting is
minimal (no AO); the drive GIF is 2 MB (subsample harder or emit webp/mp4 when ffmpeg is
available); no per-game camera hint yet (a game could expose an optional `camera()` the
dresser reads).

---


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
