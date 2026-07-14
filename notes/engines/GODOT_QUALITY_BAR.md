# GODOT QUALITY BAR — how we reach examples-folder look without losing the moat

*2026-07-14 · exhaustive quality research, Fable orchestrator + Opus agents · commissioned after Elias rejected the batch output as samey/terrible.* Sibling to `GODOT_AI_TOOLING_AUDIT.md` (that one killed MCPs as **runtime**; this one mines them for **capability + output quality**).

Moat is frozen throughout: **LLM emits DATA, typed-state verifies, witness replays byte-exact.** Everything below is either cosmetic DATA the verifier never runs, or a new *frozen* runner brick with its own tests. The RENDER path and the VOCABULARY are the only negotiable surfaces.

---

## 1. THE DIAGNOSIS — why every game looks the same

Three stacked causes, all confirmed on the cluster today.

**(a) Renderer gap — the mechanical root.** `harness/render.py` is pure PIL and redraws every game from **one** hardcoded palette (BG `#12131a`, one 40px grid, one blue/violet dynamic-body cycle; constants `render.py:58-72`) with **no** background depth. It reads zero Godot pixels — so no matter what the LLM designs, output is design-invariant. Worse: `spritebank.available()` returns False on the cluster because `banks/sprites/raw/` (the CC0 Kenney atlases) is **gitignored and absent** — only `manifest.json` + `slicemap.v1.json` ship (confirmed: `ls banks/sprites/raw/` is empty). The sprite-skinning path that already exists silently falls back to flat shapes.

**(b) DSL ceiling — the design-diversity root.** `on_step` has exactly 4 kinds — `velocity_clamp`, `timer_flag`, `remove_when`, `rising_level` (`api_godot.md:73-78`) — **no** body-mover, oscillator, patrol, or spawner. `api_godot.md:133` honestly advertises this as a blocklist ("NOT yet expressible... patrolling or moving platforms, spawners, per-region gravity..."). So every game collapses to a static world + one controlled body + passive sensor zones. Five radically different themed prompts (glacial cavern, redwood canopy, volcanic foundry, scrapyard, jungle) produced only **2 skeletons** — precision-hops ×3, pendulum/topple ×2.

**(c) Prompt monotony — the attractor.** `rules.md:3` "At most 14 bodies total" is a **soft cap** — NOT verifier-enforced; a shipped 20-body ice-cavern spec passed. No skinnable-name vocabulary, no palette/biome section, no spatial-composition or cross-game-variety mandate. The **single** worked example (`api_godot.md:166-205` "Ledge Hop") IS the dominant failure skeleton and its structure leaks into 3/5 shipped specs. Invented thematic names (`crystal`,`worker`,`squirrel`,`grate`) don't resolve in `spritebank` → flat shapes even when atlases are present; there is **no** `player` sprite key at all, so the controlled body is always flat.

Contrast (aspirational): `godot_rl_agents_examples` = 21 projects, 21 distinct mechanics AND distinct art. Our lane: 2 skeletons on one grey grid.

---

## 2. THE QUALITY BAR — mechanical checklist from scene forensics

**Load-bearing fact:** 20/21 examples are **flat-3D**; the repo has ZERO TileMap, Parallax, Light2D, CanvasModulate, 2D particles, custom fonts. The look is **asset-driven**, not code-driven. So "examples quality" splits in two:

- **BallChase-grade 2D** (the only 2D example) — *reachable by us.* Recipe (`BallChase.tscn:31-48`): WorldEnvironment glow/bloom (`glow_intensity=0.1`, `glow_strength=1.17`, `glow_bloom=0.09`, tonemap ACES) + full-screen dark bg on `CanvasLayer layer=-2` (`ColorRect #332C50` indigo) + textured `Sprite2D` actors (not flat) + walls in ONE accent `#46878F` teal + MSAA=2. A tiny 2-colour palette + bloom is what makes flat sprites pop.
- **DownFall/Racer-grade 3D** (PBR, shadows, particles, rigged chars) — needs real 3D assets + Godot's renderer; on a GPU-less node that's the lavapipe/Vulkan path, **documented crash-prone**. **Out of scope for the pilot** — don't let the work be judged against it.

**A flat-3D examples scene has:** (a) WorldEnvironment + ProceduralSkyMaterial (`sky_horizon #A5A7AB`, background_mode=2, tonemap 2/3, optional glow/ssao); (b) exactly ONE DirectionalLight3D `shadow_enabled=true` at the standard editor-sun transform; (c) meshes are real GLB/Blender OR primitives wearing StandardMaterial3D `albedo+roughness(+metallic)` — the matte-body/glossy-trim roughness contrast does the work (AirHockey table `#232323` rough 0.817 vs blue trim `#7F9AFF` rough 0.17); (d) composition = a few prefab scenes instanced repeatedly (DownFall 10-prop bank → 522 nodes; CrossTheRoad 4 GLB tiles on a script grid); (e) a deliberate camera (ortho-tilt size 12.748 pitch~52°, spring-follow len 5, or static fov 69.1); (f) a coordinated 3–6 colour palette (DownFall primaries `#3740FF`/`#FF4037`; FPS glow projectile albedo `#14971C` emission `#2BE442`); (g) optional GPUParticles + glow. UI = default SystemFont Labels 32–60px, anchored — **no custom fonts anywhere**; large default-font labels read as polish.

**Our 2D pass/fail bar (BallChase + juice):** coordinated palette actually applied · multi-layer parallax backdrop with depth · every actor a skinned sprite (controlled body included) · ground reads as ground (textured / bright top-rim) not grey boxes · 2–5 non-interactive decor · juice on events we already track (squash/stretch, motion trail, contact particle burst, restrained impact shake, floating milestone label) · deliberate camera framing · a minimal HUD label · **distinctiveness test:** next to the previous game, a stranger calls them *different games*.

---

## 3. MCP / TUTORIAL FINDINGS — what the communities actually ship

**Blunt headline:** the Godot-MCP ecosystem competes on capability-list length (43/120-op godot-ai, 157-tool tugcantopaloglu, 162-tool "MCP Pro", 42-tool tomyud1), **not** verifiable good-looking output. READMEs are tool inventories with no screenshots. A DEV.to reviewer flagged "without screenshots readers cannot verify whether *yes* means adequate or barely functional."

**The one reproducible good artifact** is `hi-godot`'s cyberpunk HUD — "~2 hours, zero coding, zero image-gen, all programmatically drawn." Every element (panels, gauges, radar sweep, waveform, CRT scanlines) is layered vector primitives + `_process()` animation, rendered by `control_draw_recipe`: an array of op-dicts (`line/rect/arc/circle/polyline/polygon/string`) interpreted by a **frozen ~90-line dispatcher** `draw_recipe.gd`. **That is our exact moat pattern — LLM emits DATA, a frozen audited interpreter renders it — pointed at the render path.** Its asset-free recipe (`prompt-hud-v2.md`) is the most liftable doc found: thin accent separator rules, dim flavor text (serials/hex/coords), nested bracket frames, 4px section accent bars, a 4-step type scale (10/14/20/32px), opacity gradients, corner ticks, a fixed NAMED palette. The `apply_preset` pattern (particles/materials/cameras/themes) and data-driven `tilemap_set_cell` recur across the ecosystem — each is *select a named skin / place a tile* = DATA the model picks, not code it writes.

**godogen** (the AI-builds-Godot video system) gets variety from two things our lane can't express: a distinct **per-game palette/parallax** in the prompt (Amsterdam Cyclist = flat filled colours + thick outlines + 3-layer parallax + eye-candy boats; CartoRally = topographic cream/sage/tan palette + symbolic tree markers), and **real per-game generated art** (Gemini 7¢, Grok 2¢, Tripo3D GLB). Its killer QA step is **vision iteration**: screenshot the running game → Gemini Flash detects z-fighting/missing-textures/"flat-looking" → auto re-iterates. Transferable: a cheap vision pass over our GIF that flags "looks like every other game" → triggers re-design.

**Juice numbers** (from written companions, since the videos were unreachable): squash/stretch damped spring (stiffness 360, damping 16), jump scale `(0.7,1.3)` recover `move_toward(scale,1,1*delta)`; Godot's own 5-layer parallax ladder (scroll 0.1 sky → 0.7 nearest); CPUParticles2D 12–22 @95–150px/s explosiveness 0.9; camera look-ahead 16px + zoom-punch decay 5.0; trauma shake `intensity^2-3` with OpenSimplex noise, **restrained** ("if every action rattles the camera, big moments have nothing left to say"); hit-stop `time_scale=0` for 0.08s; floating labels drift 14px fade 0.6s.

**In-engine capture reality** (Dive 4): `--headless` **disables all rendering** (dummy rasterizer), so `--write-movie` / `get_viewport().get_image()` return blank. To capture Godot's real renderer on a GPU-less node you MUST run a real DisplayServer under **xvfb + Mesa software driver** — Godot's own CI pattern (`lihop/godot-setup` → `xvfb-run --auto-servernum`). OpenGL3+**llvmpipe** is mature for flat 2D; Vulkan+**lavapipe** is documented crash-prone on CPU (godot#82435/#43444/#38428/#66359). MovieWriter has native `.ogv`/`.avi`(MJPEG)/`.png` — **no ffmpeg** for AVI/OGV. Blocker: `scenes/games/` holds only `*.py`+`*.spec.json`+`*.gif` — **no `project.godot`/`.tscn`** — so in-engine capture requires *building* a spec→Godot scene first; a project, not a flag flip.

**UNREACHABLE (honest list):** every YouTube transcript scraper is Cloudflare/anti-bot gated here — youtubetotranscript.com (403), youtubetranscript.com (403), tactiq (404), downsub (JS-only), r.jina.ai (security interstitial), raw youtube.com/watch (truncated footer). So the godogen video (eUz19GROIpY), "I Built a Godot Game Using AI" (kPbxKeM2848/f2fGgT2gbYc), the Ziva endless-runner, and the Godot-MCP/GDAI clips were **not transcribed verbatim** — substance recovered from godogen's repo + `demo_prompts.md` and the juice/parallax creators' written tutorials (codingquests, kidscancode, sproutkid) which carry the real numbers. Also gated: Godot MCP Pro's 2 YouTube demos (thumbnails only), GDAI (gdaimcp.com, Discord/docs-gated), yelzkizi.org parallax (403, used official Godot docs). Not fetched (binary): hi-godot demo GIFs/PNGs — relied on source (`draw_recipe.gd`, `cyberpunk_hud.gd`, `prompt-hud-v2.md`). Scene forensics was local but inferred "what looks good" from `.tscn`/`.tres`/material values, not rendered frames; `.glb`/`.blend` are binary (provenance + instancing confirmed, geometry not decoded). GitHub comment threads (#43444/#38428) lazy-load/truncate — xvfb flags inferred from titles + the lihop wrapper + Mesa/Godot docs. Godot 4.7 release notes not independently confirmable (Jan-2026 cutoff); `--headless`-disables-rendering is unchanged across 4.x. llvmpipe fps not benchmarked (would consume slice quota + run cloned software) — 10–25 s/episode is an estimate.

**Citations:** hi-godot `draw_recipe.gd` / `github.com/hi-godot/godot-ai` (docs/TOOLS.md) / `cyberpunk-hud-demo` (`docs/prompt-hud-v2.md`); `github.com/Coding-Solo/godot-mcp`, `tugcantopaloglu/godot-mcp`, `tomyud1/godot-mcp` (SVG-to-sprite), `forum.godotengine.org` MCP-Pro thread, `gdaimcp.com`, `ee0pdt`/`bradypp` forks; `summerengine.com` surveys + `dev.to/ziva/i-tested-every-godot-ai-plugin`; `github.com/htdt/godogen` (engines/godot.md, prompts/runtime.md, asset-gen/SKILL.md, docs/demo_prompts.md); `codingquests.io` game-juice, `docs.godotengine.org` 2d_parallax, `kidscancode.org` screen_shake, `sproutkid.itch.io` devlog; Godot Movie Maker + MovieWriter docs, godot-proposals#5790, `lihop/godot-setup`, godot#82435/#43444/#38428/#66359/#106957, Mesa llvmpipe docs, `gigazine.net`/`topaiproduct.com` godogen recaps.

---

## 4. RENDER PATH DECISION — souped-up `render.py` daily, in-engine 2D for hero demos, BOTH

| | souped-up `render.py` (PIL) | in-engine 2D capture (Godot/xvfb) |
|---|---|---|
| use for | **every generate/batch** (iteration) | **site/hero demos only** |
| cost | ~1–3 s/ep, 1 core, no sif change | ~10–25 s/ep, 2–4 cores |
| ceiling | good Kenney 2D (sprites/parallax/palette/juice) | **BallChase-grade** (glow/bloom, MSAA, shaders) |
| build | extend existing PIL (machinery exists) | build NEW spec→Godot `.tscn`+GDScript |
| sif | none | +xvfb +xauth +libgl1-mesa-dri (~150–350 MB; 1.18→~1.4 GB) |
| moat | zero risk | zero risk (separate renderer, same witness) |

`render.py` is the correct loop — cheap, container-free, no crash surface; hard ceiling = flat 2D. In-engine reaches BallChase-grade but costs real slice-CPU/episode and needs a new scene. So: **`render.py` for the design loop; in-engine only for the final render of games that already passed.**

**In-engine recipe (Dive 4 R1, CONFIDENCE MEDIUM-HIGH):**
```
LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe \
xvfb-run -a -s "-screen 0 1280x720x24" \
godot --path <proj> --rendering-driver opengl3 --write-movie out.avi --fixed-fps 60
```
NEVER combine with `--headless` (blank output). OpenGL3+llvmpipe, not Vulkan/lavapipe. AVI/OGV native, no ffmpeg. `--fixed-fps` = deterministic → safe for witness replay.

---

## 5. VOCABULARY ADDITIONS — ranked by impact ÷ cost

SPEC.md gets a §7b-style isolation clause: *"cosmetic DATA selects fixed renderer behavior, is never executed, never affects physics/predicates/obs; determinism and witness replay are byte-unchanged; specs without these keys render identically."*

**Bucket A — scene-dressing (pure cosmetic DATA):**
- **A1 `meta.palette` {bg,ground,player,accent} + `meta.theme`** — HUGE / TINY. Kills "all look the same"; replaces the single global BG/GRID/C_DYNAMIC with a per-spec `Palette` (default = today).
- **A2 `background[]` parallax** `{kind:gradient|band|sprite, color/tint, scroll:0.1..0.9, y_range}` — HUGE / LOW. Distinct sky/depth; draw at `x = camera_x*scroll` before world.
- **A3 `decor[]` eye-candy** `{shape,pos,size,sprite?,tint,layer}`, never simulated — HIGH / LOW.
- **A4 skinnable-name vocab** (data+prompt, not a block) — HIGH / TINY. Enumerate the 57 slicemap keys; add `player/hero→ball` to `spritebank.ALIASES`; teach suffix-strip theming.
- **A5 `tile_terrain[]`** `{cell:[cx,cy], tile:'<id>'}` or run-length — HIGH / MEDIUM. Grey statics → textured ground; `render.py` already `_fill_tiled`s. Needs `ground_dirt/ground_stone/bg` in bank.
- **A6 `decor` draw-recipe op-list** (hi-godot pattern) — MED-HIGH / MEDIUM. Whitelist op names + require numeric-literal geometry + palette-token colours (same posture as the predicate DSL); ops map ~1:1 to `ImageDraw`; a new frozen ~90-line `_draw_recipe(d,ops,tick,palette)`.
- **A7 `camera:` preset token** {pan2d_zoom, static, follow} — MED / LOW.
- **A8 JUICE** (no new spec — off state we already track) — MED-HIGH / LOW. Squash/stretch on grounded-transition/jump, motion-trail deque, particle dot-sprays on fresh contacts/`remove_when`/checkpoint latches, camera look-ahead + restrained impact shake, floating fading milestone label.

**Bucket B — mechanics bricks (frozen runner + own verifier tests; these unlock NEW skeletons):**
- **B1 `path_mover`/`oscillator` `on_step`** → moving platforms = **CrossTheRoad** lanes, dodge games. ~1–2 days.
- **B2 heading-control verb** (rotate + thrust-along-heading) → the **largest example cluster** (Racer, 3DLander, Hovercraft, BallChase, ItemSortingCart are all *steer* games; `control()` today is translational only). ~2–3 days (witness format bump).
- **B3 `spawner`/`stream` `on_step`** → falling-object streams = **DownFall**. ~1–2 days.
- **B4 ordered/latched switch sequence** → combination locks. ~1–2 days, lower visual impact.

As each brick lands, **delete its line from the `api_godot.md:133` blocklist** and add a worked example. **B1+B2 break the "2 skeletons" collapse — do them first.**

---

## 6. PROMPT REVISIONS — quote → rewrite, ready to apply

**KEEP verbatim (verifier-required moat, none cause sameness):** one `world.control()` on a dynamic body; `rng`-only randomness; `success` false@t=0 & pure; SOLIDITY/CONTAINMENT/SPEED-CAP; NO-SINGLE-ACTION-WIN; 1–6 pure snake_case checkpoints.

**RELAX:** `rules.md:3` "At most 14 bodies total." → **"Up to ~16 gameplay bodies + up to ~16 decor/dressing bodies."** (soft cap, unenforced; a 20-body spec passed.) · `api_godot.md:133` blocklist → **delete incrementally** as B1–B4 land. · Archetype "don't blend three archetypes" → keep intent, bolt on the variety mandate.

**ADD sections** (to `api_godot.md` + `design_block.md` + `orientation.md`):
1. **SKINNABLE NAMES** — list the 57 keys by role; *"name every body from this vocabulary; theme it with a suffix the skinner strips (`gem_ruby`→gem, `ledge_ice`→ledge); the controlled body has no default sprite — name it after a prop it resembles (`ball`/`crate`/`marble`)."* Highest-value pure-prompt fix — it fires the bank that already renders.
2. **PALETTE & BIOME** — mandatory `meta.palette` (4 named/hex) + biome; choose a ground material + themed decor; *dress every region.* Lift `orientation.md:32` "2–4 decor, NEVER obstacles" and `api_godot.md:147` "at most a couple of decor" to "dress generously."
3. **SCENE-DRESSING** — require `background[]` (2–4 parallax layers, each a scroll speed) + `decor[]` (2–5 non-interactive).
4. **SPATIAL COMPOSITION** — verticality vs horizontality, layered depth, region-N-to-N+1 silhouette contrast (counters "numbered boxes in a row").
5. **CROSS-GAME VARIETY MANDATE** — *"your archetype, world orientation, and palette must differ from the previous games in this batch; never re-emit the numbered-ledges-plus-goal-sensor layout."*
6. **Replace the single "Ledge Hop" example** (it IS the attractor — 3/5 specs mirror it) with 2–3 structurally different ones (DELIVER, TOPPLE, RISING-HAZARD), then a DODGE-THE-PATROLLER and a STEER-THE-VEHICLE after B1/B2.

---

## 7. THE PILOT — one game, shepherded, checklist-gated, NO batches

Quality-first, human-in-the-loop, single game. Elias IS the human in the loop.

1. Pick ONE concept exercising the new capabilities that maps to a real example (recommended: a CrossTheRoad-style dodge once B1 lands, or a BallChase-style steer-and-collect once B2 lands — both 2D examples-genre).
2. **Loop (one testable change per iteration):** generate → render (`render.py` path) → Elias critiques against the §2 checklist → apply ONE targeted fix → repeat.
3. Optional loop-closer: cheap vision pass over the output GIF auto-flags "flat / looks like the others" → feeds a re-design request (godogen's proof-over-claims loop on our replay).
4. **Gate:** if `render.py`-2D clears the checklist → pilot passes, iteration lane done. If the designer still wants engine glow/particles/shader feel → escalate THAT ONE game to the in-engine 2D path for its final render (P4).
5. Only after the pilot passes do we resume batch generation.

**Phasing / sign-off:** **P0 ship the sprite bank** — vendor `banks/sprites/raw/` CC0 Kenney atlases → `available()` flips True; ~hours, packaging not code — the single highest-ROI unblocked win, DO FIRST, only needs "CC0 vendoring OK?". **P1 variety milestone** — A1/A2/A3/A5/A8 + SPEC vocab + prompt §1–5 + `ALIASES player→ball`; ~2–4 days; Elias approves the cosmetic-vocab direction. **P2 the pilot** — ~1–3 days interactive; Elias calls pass/fail + the gate. **P3 new skeletons** — B1+B2 first then B3/B4; ~1 wk + ~2–4 days; delete blocklist lines incrementally. **P4 site-demo lane** — only if the P2 gate demands engine fidelity: build the spec→Godot 2D scene, BallChase-grade, xvfb+OpenGL3/llvmpipe+Movie Maker AVI; sif +xvfb/+xauth/+libgl1-mesa-dri; **3D/lavapipe explicitly OUT — crash-prone**; ~1–2 wks; the big commitment (sif change + per-episode slice-CPU + a second renderer to maintain).

---

## 8. NON-NEGOTIABLE — the moat (unchanged by any of the above)

- **LLM emits DATA only** — never code, never runtime authority. Cosmetic fields are DATA that select fixed renderer behavior; never executed.
- **Typed-state verification (G0–G3) owns physics** — no cosmetic/vocab addition touches `world.step` or the predicate solver. Bucket-B bricks are frozen runner code with their own verifier tests.
- **Witness replay is byte-exact** — every cosmetic field is off-by-default; a spec without it renders byte-identically (the §7b sensor/obs isolation precedent). Determinism preserved.

The render path and the vocabulary are fully negotiable and are where all the quality lives; the moat is where none of it does. Keep them separate and we get examples-folder polish for free.
