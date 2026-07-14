# Watchable demos — audit + pipeline recommendation

*Audited 2026-07-14. Question posed: "Is a watchable LIVE LOCAL demo the right first
step (before/alongside cluster-scale training)?" Short answer at the bottom: **no —
build the web replayer first.** All claims below are quoted against the code.*

---

## 1. What renders today — three separate paths

The repo has **three** replay renderers, not one, and they do NOT share a witness
source or a CLI. Understanding the split is the whole audit.

| Path | Engine | Entry | Camera | Sprites | Big worlds | On the site? | Cluster-safe |
|------|--------|-------|:------:|:-------:|:----------:|:------------:|:------------:|
| **GIF (py)** `render.replay_gif` | py / pymunk (in-proc) | CLI `game replay` | yes | yes | yes | yes (GIF) | yes (PIL) |
| **GIF (js)** `executors.render_js_replay` | js / Planck (node) | **none — ad-hoc only** | yes | yes | yes | yes (GIF) | yes (PIL+node) |
| **LIVE viewer** `viewer.watch` | py / pymunk (in-proc) | CLI `game watch`, `demo --live` | **no** | **no** | **no** | no (pygame window) | **no (pygame)** |

The 6 showcase games are **all JS + all big-world** (`grep WORLD_SIZE`): boulder_run
`[2000,600]`, demolition_yard `[2000,800]`, flood_tower `[900,1000]`, gem_cavern
`[1800,700]`, meteor_gauntlet `[1200,800]`, two_switch_vault `[1800,800]`. Their GIFs
(`scenes/games/v23_showcase/*/*.gif`, 537 KB–1.3 MB) were produced by the **js GIF
path**, which is the one path with **no CLI verb** — `render_js_replay` is referenced
only in `harness/verify/executors.py` and `tests/test_executors.py`.

---

## 2. The live viewer today — precise capabilities & gaps

`harness/viewer.py` is the day-1 pygame viewer. What it actually does:

- **Replays a witness** (G3 witness dict / explicit action list / re-verify to fetch
  one) by **re-running the physics in-process, one `world.step(1)` per frame** at a
  real-time cadence (`_advance`, `viewer.py:231-261`; `world.step(1)` line 246,
  `game.act` line 245). It is a real-time twin of the GIF runner, not a frame player.
- **py engine only.** It builds the world with `render._import_world()` →
  `harness.core.world.World` (`viewer.py:296`) and loads the game with
  `render._load_game` (`viewer.py:286`) which, for a local file, `exec()`s the source
  **as Python** (`render._local_load`, `render.py:456-469`). A `game.js` file cannot
  load or step here — **the viewer cannot run any of the 6 showcase games.**
- **Interactive but not human-play.** Controls are pause / restart / speed / ESC
  (`viewer.py:324-334`, docstring `:278`). There is no key→action mapping; the human
  cannot *play*, only watch the witness. `demo_live` (`viewer.py:389-439`) generates a
  prompt then watches its witness (wired at `cli.py:448-474`).

### Concrete gaps vs v2.3 (each is a real defect for the showcase)

| # | Gap | Evidence | Impact |
|---|-----|----------|--------|
| G1 | **No JS engine.** Loads/steps a pymunk world only. | `viewer.py:286,296`; `render.py:456-469` | Cannot show any showcase game (all JS). |
| G2 | **Ignores `WORLD_SIZE`.** `_fresh_episode` calls `make_world(seed=seed)` with **no `size=`** (`viewer.py:209`). Contrast `render.replay_gif` which passes `size=tuple(ws)` (`render.py:567-572`). | `grep WORLD_SIZE harness/viewer.py` → none | A `[2000,600]` game is built at default 800×600 → world truncated / physics wrong. |
| G3 | **No FollowCamera.** `_draw_scene` draws the whole world at `scale` with no crop/follow. | `grep -i camera harness/viewer.py` → none; cf. `render.FollowCamera` `render.py:158-197,406-414` | Even if G2 fixed, a big world = a giant window with no tracking. |
| G4 | **No sprite skinning.** Draws pygame primitives only (`_draw_one`, `viewer.py:83-124`); never imports `spritebank`. | `grep -i sprite harness/viewer.py` → none; cf. `render.py:352-404` | Flat shapes, no visual parity with the GIFs/site. |

Net: **the live viewer is blind to every v2.3 feature and to the entire engine the
showcase runs on.** Bringing it to parity means re-implementing render.py's camera +
sprite advances a *second time* in pygame primitives, plus a JS frame-playback mode.

---

## 3. The frames substrate (this is the key asset)

Both GIF paths already emit the exact structure a client-side player wants: a list of
`{tick, entities:{name: query}}` snapshots.

- Py: `_run_episode_with_frames` (`executors.py:151-210`, `frame_of` `:162-169`).
- Js: `runner.js:213-262` (`frames.push({tick, entities: frameOf(world)})` `:219,:239`).
- `render_js_replay` (`executors.py:320-358`) already turns js frames → GIF by feeding
  each frame to `render._render_frame` through a read-only `_FrameWorld` shim, **with
  FollowCamera** (`:342`) and sprites (module default on). This is the template a web
  replayer mirrors client-side.

**Per-entity query dict** (`world.py:323-352`, `nodeworld/world.js:388-404`): `pos, vel,
angle, angular_vel, bbox, shape, static, sensor, controlled` (+`radius`).

**Measured payload** (ran the node runner on boulder_run, 14 bodies, every frame):
~**138 B/entity**, ~**2.4 KB/frame**. A 200-tick episode at `every=2` (≈100 frames) ≈
**244 KB raw JSON → ~30–50 KB gzipped** (repetitive floats compress hard; rounding to
1 decimal and dropping unchanged statics after frame 0 cuts it further). **Smaller than
every showcase GIF** (537 KB–1.3 MB) and it's scrubbable.

⚠ **One fidelity asymmetry to fix:** the **js `query()` omits `verts`**
(`world.js:388-404`) whereas py emits world-space `verts` for poly/segment
(`world.py:342-349`). Without verts, both `render_js_replay` and a web replayer fall
back to the **axis-aligned bbox** for boxes/polys → a *tilted* body renders as a bloated
slab. The showcase games are mostly axis-aligned statics + circles so today's GIFs look
fine, but any rotating body is wrong. Emitting `verts` from `world.js:query()` (a ~6-line
change mirroring `world.py`) is the single highest-leverage fidelity fix and benefits the
js GIF path too.

---

## 4. The site pipeline (the distribution channel we already own)

`gi-site/_build.py`: each `_src/**/index.html` is made self-contained by inlining local
media as **data URIs** (`inline_media`, `:122-131`; MIME table is image-only, `:119`),
then **AES-256-GCM encrypted** behind a PBKDF2 WebCrypto gate (`encrypt_page` `:134-141`).
Published pages are opaque blobs; the browser decrypts client-side.

Implications for a replayer:
- `inline_media` only rewrites `src="…"` attrs. A canvas player embeds its frames JSON in
  an **inline `<script type="application/json">`** block and its player code inline — **no
  `_build.py` change needed**, and the whole thing is still one encryptable static file.
- The site is **static + client-side only**: this *rules out* any server/pygame approach
  and *rules in* the web replayer. Whatever we ship there must be self-contained JS.

---

## 5. Options matrix (effort in half-days = ~3–4 h each; opinionated)

| # | Option | Effort | Depends on | Wow | Cluster | Site | Verdict |
|---|--------|:------:|-----------|:---:|:-------:|:----:|---------|
| **0** | **Wire js replay into CLI + persist witness/frames** next to each game | **1** | — | low | ✅ | enables | **DO FIRST (substrate)** |
| 1a | Live viewer: **JS frame-playback** (consume executor frames, cursor instead of stepping) | 1.5 | 0 | med | ❌ | ❌ | later |
| 1b | Live viewer: **FollowCamera** (port `render.FollowCamera` → pygame subsurface) | 1 | 1a | med | ❌ | ❌ | later |
| 1c | Live viewer: **sprite parity** (PIL crop → `pygame.image.fromstring`, orient, blit) | 1.5 | — | med | ❌ | ❌ | later |
| 1d | Live viewer: **honor WORLD_SIZE** (pass `size=` in `_fresh_episode`) | 0.25 | — | low | ❌ | ❌ | cheap, do with 1b |
| 1e | Live viewer: **live-policy view** (py in-proc trivial; js needs streaming episode mode) | 0.5 py / 2 js | policy | **high** | ❌ | ❌ | **the real payoff — but needs a policy** |
| 2 | **Video (mp4/webm)**: swap `_save_gif` for imageio/ffmpeg on the same PIL frame list | 0.5–1 | 0 | low-med | ✅ | yes | nice-to-have; replayer supersedes |
| **3** | **Web replayer**: self-contained canvas player from frames JSON, shipped on the site | **2–3** | 0, verts | **high** | ✅ | **native** | **RECOMMENDED first watchable** |
| 4 | Cluster-side rendering | ~0.5 (mostly free) | — | — | ✅ | — | PIL+node already headless; pygame never |
| 5 | Live remote (X-forward / OnDemand desktop) | high | — | low | ❌ | — | **skip** |

Notes on cluster split (option 4): the **GIF/mp4/frames-JSON producers are already
headless-safe** — `render.py` is PIL-only, the node runner is CLI. The **pygame viewer
can never run on the cluster** (needs a display). So the clean split is: **cluster emits
frames JSON (+ optional GIF/mp4) as run artifacts; local machine watches** (pygame for a
dev console, or a browser for the replayer). Nothing about watching needs to *block* on
cluster training — the frames JSON is a tiny by-product of verification.

---

## 6. Recommendation — answering Elias's question

**Is live-local (pygame) the right first step? No.** Two reasons, both from the audit:

1. **Wrong channel.** pygame is local-only: it cannot go on the password-gated site you
   already use to show people, and cannot run on the cluster. The viewer is also the one
   renderer that is blind to JS + camera + sprites + WORLD_SIZE (§2), so making it show
   the current showcase means re-doing render.py's advances a second time in pygame — the
   most work for an artifact only you can see.
2. **The witness-replay wow is better served in the browser.** The frames JSON we already
   emit is smaller than the GIFs, scrubbable, and drops straight into the existing
   encryptable static site with no `_build.py` change (§3–4).

**Live-local's real payoff is not witness replay — it's the LIVE-POLICY view** (option
1e): watching an RL agent play in real time, which py-in-process makes trivial. That is
genuinely worth building **once there is a trained policy to watch** — i.e. *alongside*
training, not before it. Until then it renders the same witnesses the browser shows,
locally and uglier.

### Recommended sequence

1. **Step 0 — substrate (1 half-day).** Wire the js GIF path into the CLI (auto-detect
   `// engine: js`, or `game replay --engine js` → `render_js_replay`) and **persist the
   winning witness + frames JSON** next to each game (e.g. `game.frames.json`). Today
   witnesses are ephemeral (re-run G3 every replay) and the js path has no CLI. This one
   step de-duplicates the render stack and feeds every downstream option.
2. **Step 1 — web replayer (2–3 half-days). ← the first thing to build.** Self-contained
   canvas player consuming the frames JSON, mirroring `render._render_frame`'s palette /
   z-order / camera conventions; ship it inline on a `gi-site` day page. Biggest wow,
   through the channel you already own, cluster-friendly, smaller than the GIFs.
3. **Step 2 — mp4/webm export (0.5–1 half-day, optional).** Cheap once frames exist; a
   fallback for contexts where an interactive player is overkill. Low priority — the
   replayer largely supersedes it.
4. **Step 3 — live viewer to parity + live-policy (later, alongside training).** Do 1d+1b+1a+1c
   only when you want a local "director's console," and 1e when a policy exists. This is
   where live-local earns its keep.

---

## 7. Build checklist — Step 1 (web replayer)

- [ ] **Emit `verts` from `nodeworld/world.js:query()`** for poly/segment (mirror
      `world.py:342-349`) so rotated bodies render truthfully in both the js GIF and the
      replayer. (~6 lines; highest fidelity/effort ratio.)
- [ ] **Frames export** (Step 0): a `game replay --emit-frames` writing
      `{world_size, frames:[{tick,entities}]}` JSON; round coords to 1 decimal; optionally
      drop statics after frame 0 (re-emit only movers) to shrink payload.
- [ ] **`replay.js` canvas player** (single file, no deps): load inline JSON, draw grid +
      BG, z-order sensors→static→dynamic→controlled, palette from `render.py:59-83`,
      sensor-colour rules (`_sensor_colour` `render.py:75-81`), FollowCamera port
      (`render.py:158-184`). Controls: play/pause, scrub bar, speed, tick + milestone HUD.
- [ ] **Sprites v1 = flat shapes** (matches viewer today). *v2 (optional):* bake the
      resolved slicemap crops for a game into one small PNG atlas (data-URI'd) and skin
      client-side; name→region resolution mirrors `spritebank._candidates`
      (`spritebank.py:163-192`).
- [ ] **Site integration:** embed frames JSON in an inline `<script type="application/json">`
      and the player inline in the `_src/dayN/index.html`; confirm `_build.py` encrypts it
      unchanged (it will — no `src=` involved).
- [ ] **Parity check:** render the same witness as GIF (`render_js_replay`) and in the
      replayer; eyeball that palette / camera / z-order match.
- [ ] **Payload budget:** keep a 200-tick episode under ~60 KB gzipped inline (measured
      raw ≈244 KB every-2 → ~30–50 KB gzipped; well within budget).
