# Web replayer — what landed (watchable demos, step 0 + 1)

*Built 2026-07-14. Implements the audit's recommendation in
`notes/compute/WATCHABLE_DEMOS.md`: don't build the pygame viewer to parity —
persist the frames substrate and ship a self-contained canvas replayer on the
gated site. Steps 0 (substrate) and 1 (web replayer) are done.*

---

## 1. What landed

### Harness (this repo)

- **`nodeworld/world.js` `query()` now emits world-space `verts`** for box/poly/
  segment and `radius` for circle/segment, mirroring `harness/core/world.py`
  (`world.py:342-349`). Verts are DERIVED from the body transform (pure query,
  not simulated state) so determinism is untouched — the JS golden-witness and
  determinism tests still pin the same values. This is the audit's highest-
  leverage fidelity fix: a rotated body used to render as its bloated
  axis-aligned bbox in both the JS GIF path and any client player; now it draws
  as the true polygon. Benefits the JS GIF path too (`render_js_replay`).

- **`game replay` is wired for both engines** (`harness/cli.py`,
  `cmd_game_replay`). It dispatches by engine (`detect_engine`): a `.py` game
  renders through `render.replay_gif` exactly as before; a `.js` game renders
  through `executors.render_js_replay` — previously it had **no CLI verb** and
  `game replay foo.js` crashed in the py-only loader. Same UX, one command.

- **`game replay --frames <out.json>` persists the replay SUBSTRATE** (both
  engines), via the new `executors.replay_frames_doc`. Runs ONE every-frame
  (`frames_every=1`) episode of the witness through the existing executor seam,
  assembles `{meta, frames}`, rounds floats to 2 decimals, writes it, and
  reports raw + gzip byte sizes. With `--frames` and no explicit `--gif`, only
  the JSON is written (fast, no PIL render).

- **`nodeworld/runner.js`** echoes `title`/`prompt` in a frames-bearing episode
  record (only when `frames_every>0`, so verify batches stay lean) — that's the
  meta the substrate needs from the JS side; the py side harvests it from the
  loaded `Game`.

- **Tests** (full suite green — 383 collected: 374 prior + 9 new; the only reds
  are 2 pre-existing `test_viewer.py` pygame-dummy-driver failures that also
  fail on the clean tree, unrelated to this work). New:
  `tests/test_cli_replay.py` (extension dispatch py/js, marker dispatch,
  frames-only skips GIF, both-outputs) and additions to `tests/test_executors.py`
  (frames-doc schema + gzip budget, verts/radius presence in JS frames,
  py title-harvest, `_round_floats`). JS tests skip when `node` is absent.

### Site (`gi-site`, left uncommitted for review)

- **`_src/replayer/player.js` + `player.css`** — a self-contained canvas
  replayer, zero deps, zero network. Mirrors `harness/render.py`'s visual
  language (see §4).
- **`_src/day2/replayer_demo.html`** — a day2-styled preview embedding the
  player twice, with `boulder_run` and `meteor_gauntlet` inlined as
  `<script type="application/json">` runs and the player CSS/JS inlined. One
  static, offline-safe, encryptable file.

---

## 2. The frames-JSON contract

`game replay <game> --frames out.json` writes exactly:

```jsonc
{
  "meta": {
    "title":  "Boulder Run",
    "prompt": "…",
    "world_size": [2000, 600],
    "engine": "js",                     // or "py"
    "witness": {                        // from a fresh verify
      "seed": 0,                        // world seed the frames were generated on
      "ticks": 199,
      "checkpoints": { "gate_open": 6, "rolling": 32, … }   // name -> latch tick
    }
  },
  "frames": [                           // every=1, one per decision tick
    { "tick": 0, "entities": { "<name>": <query-dict>, … } },
    { "tick": 1, "entities": { … } },
    …
  ]
}
```

Each **query-dict** is what `world.query(name)` returns (identical across
engines): `pos, vel, angle, angular_vel, bbox [l,b,r,t], shape
("box"|"circle"|"segment"|"poly"), static, sensor, controlled`, plus `verts`
(world-space outline for box/poly/segment) and `radius` (circle/segment). All
floats are rounded to 2 decimals. `checkpoints` is the witness latch map
(milestone name → the decision tick it first became true) — the player uses it
to light/flash milestones.

---

## 3. Measured sizes (the payoff)

Every-frame (`every=1`) substrate vs the pre-baked GIF, for the two 100+ tick
pilots:

| game            | witness ticks | frames | raw JSON | **gzip** | GIF | **gzip vs GIF** |
|-----------------|:-------------:|:------:|:--------:|:--------:|:---:|:---------------:|
| boulder_run     | 199           | 200    | 536 KB   | **16.7 KB** | 1317 KB | **79× smaller** |
| meteor_gauntlet | 98            | 99     | 241 KB   | **9.8 KB**  | 570 KB  | **58× smaller** |

Repetitive rounded floats compress hard. Both gzip payloads are far under the
audit's ~60 KB inline budget, and the whole scrubbable replay is a fraction of
its single-shot GIF. (The demo inlines the *raw* JSON — ~0.8 MB of the 0.8 MB
page — but the AES-gated blob it becomes is still smaller than the two GIFs, and
a plain CDN would gzip the raw JSON to the numbers above.)

Optional future shrink (not needed yet): drop 1 decimal, and re-emit only
movers after frame 0 (statics are constant).

---

## 4. Visual parity with `render.py`

The player is a direct port of `harness/render.py`'s conventions so a replay
reads the same as its GIF:

- **Ground + grid**: BG `#12131a`, 40-unit grid (`GRID`).
- **Palette** (`render.py:59-83`): controlled `#5ecd82`; statics gray `#5f6478`;
  other dynamics cycle 3 blues/violets by `crc32(name)` (same function → same
  colour as the GIF); sensors coloured by name — **goal-green / hazard-red /
  neutral-amber** via the same word lists (`_sensor_colour`).
- **Z-order**: sensors → static → dynamic → controlled (`_Z`).
- **Shapes**: `verts` polygons when present (rotation-correct), else bbox rect /
  circle from the bbox; segments as thick round-capped lines.
- **Follow camera** (`render.FollowCamera`): worlds > 800×600 crop to an 800×600
  view that lerps (0.35) toward the controlled body and clamps to the world;
  the camera centre is precomputed per frame so scrubbing anywhere is exact.
- **HUD**: title top-left, tick top-right, a milestone name flashes amber when it
  latches (from the witness checkpoint map); a DOM row shows `k/M milestones`.

Controls: **play/pause · scrub bar · speed 0.5/1/2/4 · restart**. Playback base
rate is real-time (1 decision tick = 6 steps @ 1/60 = 0.1 s → 10 fps at 1×).
Sprites are intentionally out of scope for v1 (flat shapes, like the pygame
viewer) — the GIFs remain the pretty layer.

---

## 5. How to embed a replay in a day page

The site build (`gi-site/_build.py`) inlines only `src="…"` image attrs and then
AES-encrypts the page. A canvas replay therefore ships as **inline blocks — no
`_build.py` change, no external refs, no fetch**:

1. Generate the substrate into the day folder (use the main checkout's
   `node_modules` in a worktree):

   ```bash
   NODE_PATH=<checkout>/nodeworld/node_modules \
     python -m harness game replay <game>/game.js --frames <slug>.frames.json
   ```

2. In `_src/dayN/index.html`, inline once: the contents of
   `_src/replayer/player.css` inside `<style>`, and `_src/replayer/player.js`
   inside a `<script>`. (They are the canonical, reusable source; the demo page
   inlines copies.)

3. For each run add a container + its data block (guard `</` as `<\/` so the JSON
   can't break out of the script tag):

   ```html
   <div class="gi-replay" data-frames="boulder_run-data"></div>
   …
   <script type="application/json" id="boulder_run-data">{ …frames json… }</script>
   ```

The player self-mounts every `.gi-replay[data-frames]` on load — and also under
the gate's `document.write` path (it re-checks `readyState`). See
`_src/day2/replayer_demo.html` for a complete working page; the orchestrator
decides how to fold it into `day2/index.html`.

---

## 6. Follow-ups

- **Sprite skinning in canvas (v2).** Bake a game's resolved slicemap crops into
  one small data-URI'd PNG atlas and skin bodies client-side; name→region
  resolution mirrors `spritebank._candidates`. Gets visual parity with the GIFs
  without giving up scrubbability.
- **Live-policy view.** The frames substrate is witness replay. The real
  live-local payoff (audit §6, option 1e) is watching a *trained policy* play in
  real time — trivial py-in-process, needs a streaming episode mode for JS.
  Build alongside training, once a policy exists.
- **mp4/webm export** (audit step 2) is now cheap on the same frames, but the
  scrubbable replayer largely supersedes it. Low priority.
- **Payload shrink** (drop a decimal, re-emit only movers) if inline pages get
  heavy with many runs.
- **Parity eyeball**: render the same witness as GIF (`render_js_replay`) and in
  the player side by side to confirm palette/camera/z-order match (a headless
  DOM smoke test already checks draw/camera/milestone/scrub logic; a browser
  eyeball is the last mile).
