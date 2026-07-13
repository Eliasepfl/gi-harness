# MCP & AI-tooling scan for the parts bank

> Research note (no code changes). Scope: does any MCP server or AI asset API help
> build/serve the proposed **bank of parts** (named items/obstacles with predictable
> physics + sprites)? Verified against live repos/products on 2026-07-13. Every
> candidate below was checked for existence, maturity (stars, last push, license) and
> fit against **our** pipeline. Hype-resistant by design.

---

## 0. TL;DR verdict

**Ship a local, versioned bank folder first. No MCP is needed for the bank itself.**

The parts bank is fundamentally a `name -> {physics params, sprite region}` map. The
*physics* is the load-bearing half (collision shape, mass, friction, elasticity — the
only half our verifier ever reads); the *sprite* is decoration bound to each part.
Both belong in the repo as data, not behind a network call:

- **Physics + index:** a hand-authored `parts.json` (or `.py`) keyed by part name.
- **Sprites:** slice a **CC0 (public-domain) Kenney spritesheet** committed to the repo.

This is deterministic, free, offline, byte-reproducible, and license-clean — which is
exactly what the rest of the project already demands (frozen base code, integrity
manifest, `G1` determinism check that two identical seeded runs produce identical
snapshots). An MCP call is the opposite of all four properties.

**The one genuinely useful, *optional*, *later* tool is PixelLab.ai** (as an API or via
its official MCP) for **on-demand themed sprites** when a prompt's fiction isn't covered
by the CC0 bank — run **build-time, offline, after certification**, and **cache the PNG
into the versioned bank**. Never at generation-time, never in the verification path.
Everything else (retrieval MCPs, Aseprite MCPs, ComfyUI MCPs, Scenario/Layer/Recraft)
is either an interactive human-authoring tool or a heavier version of that same idea,
and is premature for us today.

Why this is the honest answer, in one line: **our certificate never reads a pixel**
(CONTRACTS.md §1: "No pixels anywhere in verification: everything reads engine state";
render.py already draws generic shapes from `world.query()`), so any asset tool only
buys demo/site polish and future-engine skinning — polish does not justify a network
dependency in a determinism-first loop.

---

## 1. Where an asset tool could plug into *our* pipeline

Three, and only three, insertion points — they have wildly different constraints:

| Point | What happens | Determinism need | Cost sensitivity | Verdict |
|---|---|---|---|---|
| **(a) Generation-time** — the LLM picks parts by name while writing the game | LLM references `crate`, `spike`, `bouncer` | **Absolute** (repairs re-run; runs are sandboxed + manifest-checked) | High (many games in the base campaign) | **Local index only.** Never a live API here. |
| **(b) Build-time** — after a game is certified, skin its demo GIF | harness renders the witness replay with sprites | Cache the artifact → determinism preserved | Medium (one pass per certified game) | **Local bank; PixelLab cache-fill optional.** |
| **(c) Render-time** — `render.py` draws frames | currently generic circles/boxes/polys/segments | n/a (visual only) | Low | **Local sprite atlas.** No network. |

The proposal ("model pulls parts by name") is squarely point (a). Point (a) must be a
**local lookup**: a network asset call mid-generation would inject latency into every
attempt, make the repair loop non-reproducible, add per-call cost across the whole
base-of-games campaign, and drag license ambiguity into every generated game. The
sprite for a named part can be resolved lazily at (b)/(c); the LLM only needs the
*name* and the *physics contract* of each part, both of which are local data.

Note on "semantic richness of the bank": the bank's semantics (what a part *is*, how it
behaves) come from the physics params we author, not from any sprite. No asset tool adds
semantic richness to physics — it only paints the parts we already defined.

---

## 2. MCP servers — landscape (verified)

### 2.1 Asset **generation** MCPs (text -> sprite)

| Server | Stars | Last push | License | What it is | Fit for us |
|---|---:|---|---|---|---|
| **pixellab-code/pixellab-mcp** (official) | 37 | 2025-08-10 | none / proprietary ("© 2025 PixelLab, all rights reserved") | Thin MCP bridge to the **PixelLab.ai** API: characters w/ 4/8-dir views, animations, Wang tilesets, isometric tiles, transparent-bg objects. Needs a **paid PixelLab API token**. | The *API behind it* is the only compelling generator (see §3.1). The MCP wrapper itself is stale (~11 mo) and low-traction; we'd more likely call the REST API build-time than run the MCP. |
| **MubarakHAlketbi/game-asset-mcp** | 146 | 2026-05-29 | MIT | JS MCP; 2D via HF `gokaygokay/Flux-2D-Game-Assets-LoRA`, 3D via InstantMesh/Hunyuan3D spaces. Runs on a free Hugging Face token (rate-limited; heavy use needs paid HF). Tools: `generate_2d_asset`, `generate_3d_asset`. | Actively maintained, permissive license. But Flux-LoRA output quality/consistency is well below PixelLab for *pixel* sprites, and it's still a live network model call. Same determinism/cost objections as any generator. |
| ComfyUI family: **ConstantineB6/comfy-pilot** (in awesome-mcp-servers), **tuannguyen14/ComfyAI-MCP-GameAssets**, **PurlieuStudios/comfyui-mcp**, lalanikarim/jonpojonpo/samuraibuddha variants | varies (mostly < 100) | varies | mostly MIT | Drive a **local ComfyUI** graph (SD/Flux + LayerDiffuse for transparent PNGs, background-remove/resize nodes). "ComfyMCP Studio" adds `generate_sprite` / `process_image`. | Only makes sense if we already run ComfyUI locally with a curated sprite workflow. Big infra + GPU + model-management burden for demo polish. Not now. |

### 2.2 Sprite **editing / authoring** MCPs (drive Aseprite)

These automate the pixel-art *editor* for a **human in the loop** — they are not
pipeline components.

| Server | Stars | Last push | License | Notes |
|---|---:|---|---|---|
| **diivi/aseprite-mcp** | 295 | 2026-07-02 | MIT | Most mature. Python, **104 tools / 17 categories**; requires a **local Aseprite install** (`ASEPRITE_PATH`). |
| **willibrandon/pixel-mcp** | 94 | 2025-10-18 | present | Animation, retro palettes, dithering, shading, spritesheet export. |
| **youichi-uda/aseprite-mcp-pro** | 18 | 2026-04-01 | present | 121 tools, Godot export. **One-day repo** — immature. |
| **ayigityol/aseprite-mcp** | 6 | 2026-03-07 | present | 43 tools. **One-day repo** — immature. |
| rkdfx/aseprite-mcp, Dizzd/aseprite_mcp (Rust), ilhamdoanggg/... | low | varies | varies | Long tail; all require a running Aseprite + are interactive-authoring oriented. |

**Relevance:** diivi/aseprite-mcp is genuinely good if *Elias* wants to hand-produce a
handful of bespoke bank sprites conversationally. It is **not** an automated
build-pipeline component (needs a licensed Aseprite GUI app + a human directing it).

### 2.3 Asset **retrieval / search** MCPs (pull existing art)

| Server | Verified? | What it is | Fit |
|---|---|---|---|
| **MCP Find Assets** (`find-assets.xyz`, closed/hosted) | Yes (docs live; site 403s to bots) | Single tool `mcp-find-assets`; an **autonomous agent** that chains ~19 capabilities: DuckDuckGo/Google search across "trusted asset sources" incl. **itch.io**, download, unzip, background-remove, quality-check, organize. | **Anti-fit.** Autonomous web search = non-deterministic and non-reproducible by construction, and it drags in whatever license the found file carries. Exactly what a determinism-first loop must avoid. |
| **ochowei/itch-market-mcp** | Yes (GitHub) | itch.io **market research** (top categories, price distributions) — analytics, not asset delivery. | Irrelevant to the bank. |
| (searched) dedicated OpenGameArt / Kenney retrieval MCP | **None found** | No maintained MCP wraps OpenGameArt or Kenney for programmatic pull. | — |

### 2.4 Map / atlas / engine MCPs (adjacent)

| Server | Stars | Last push | License | Notes |
|---|---:|---|---|---|
| **subzerox9/tiled-mcp-server** | (via lobehub) | — | — | Read/write TMX/TMJ/TSX/TSJ + AutoMapping; **43 tools**. Relevant only if we adopt Tiled tilemaps — we don't (our worlds are pymunk bodies, not tile grids). |
| **phaserjs/editor-mcp-server** (official Phaser) | 34 | 2025-09-19 | none | Authoring MCP for Phaser Editor v5 scenes. **Bookmark for rung-4** (Phaser is our step-1 visual on-ramp per OBJECTIVES.md), but it's an editor-authoring layer, not the per-tick loop we need — same structural finding as `notes/GAME_ENGINE_INTEGRATIONS.md`. |
| No dedicated **spritesheet/atlas-packer MCP** found | — | — | — | TexturePacker/`free-tex-packer` are CLIs; trivial to shell out at build-time. No MCP required. |

Registries checked: **punkpeye/awesome-mcp-servers** (90.7k★; has "Art & Culture" +
"Gaming" categories — game-asset entries are the ones tabled above), **glama.ai**,
**mcp.so**, **smithery**, **lobehub**, **mcpservers.org**, **pulsemcp.com**. No
game-asset MCP outside the set above reaches meaningful maturity.

---

## 3. AI asset-generation APIs (non-MCP, wrappable) — verified

| Product | Price (verified) | Transparent sprites? | Style consistency | Output license | Status |
|---|---|---|---|---|---|
| **PixelLab.ai** | Subscription ~**$9/mo** (tier 1) to ~**$22/mo** (tier 2); credits: basic tools **1 credit/req**, newer models **~40 credits/req**; pay-per-credit API also. | **Yes** — purpose-built pixel-perfect transparent sprites | Reference-image style matching (good, but *no hard "Style Lock" enforcement*; admits weakness at ≤16×16) | **You own outputs**; commercial use on paid plans; Open RAIL-M framework (behavioral-use terms). Not trained on your data without notice. | Active; largest dedicated pixel-art user base; Aseprite plugin. **Best fit for on-demand themed sprites.** |
| **Scenario.com** | Freemium; paid from **$15/mo**; compute units shared across web+API; API-first (Unity/Unreal/custom). | Yes | **Custom LoRA training** (10–30 imgs/style) — strongest style lock here; Multi-LoRA merge. | Full commercial license on paid plans; **you are responsible for your training-data rights**. | Active ($6M raised). Heavier: general game-art, not pixel-specialised. |
| **Layer.ai** | Freemium; API (`docs.layer.ai`). | Yes | Style/model workspace | Commercial (paid). | Active; 200+ studios (incl. Zynga), $6.5M funding. General creative AI OS. |
| **Recraft** | **$0.04 / raster image**, $0.08 / vector (API); background-remove built in. | Yes (raster + true SVG vector) | Brand/style controls | **Full ownership** on paid API. | Active; cheapest per-image; vector output is unique but not pixel-art. |
| **OpenAI gpt-image-1** | **~$0.011–$0.25 / image** (quality/size) or token-based; transparent **PNG/WebP** supported. | Yes (set `background: transparent`) | Weak for consistent *pixel* sprites ("mixels", inconsistent) | You own outputs; commercial OK. | **Deprecating 2026-10-23** → GPT Image 1.5 / mini succeed it; general-purpose, not sprite-tuned. |

**Reading of the table for pixel sprites:** PixelLab is the clear specialist (pixel-
perfect, transparent, directional, animation, Aseprite integration). Scenario/Layer win
for high-res *painted* game art and hard style-locking via custom LoRAs, at higher cost
and setup. Recraft is cheapest and does vector, but isn't pixel-art. Generic image APIs
(OpenAI) are the weakest for consistent sprites and one is already sunsetting.

---

## 4. The local-bank baseline (what the MCPs must beat)

**Kenney.nl** — verified: **60,000+ assets**, **CC0 / public domain**, **no attribution
required**, **commercial use OK**. Perfect for a versioned local bank: one committed
spritesheet + a JSON index mapping `part_name -> {sprite_rect, physics: {shape, mass,
friction, elasticity, ...}}`. Zero network, zero cost, byte-reproducible, license-clean.

Contrast the license reality of the retrieval route — **OpenGameArt** is a **mixed-
license swamp**: CC0, CC-BY, CC-BY-SA, GPL all coexist; attribution is mandatory for
BY; mixing BY-SA contaminates the combined work to share-alike; GPL-on-art is
ambiguous. Automating retrieval across it at scale is a compliance liability. This is a
concrete reason the retrieval-MCP path (find-assets, itch.io scraping) is worse than a
curated CC0 bank, not just different.

---

## 5. Critical assessment against our loop

**Determinism (the project's spine).** `G1` asserts two identical seeded runs yield
identical final snapshots; runs are sandboxed with an integrity manifest. Any live
generator returns a different image per call; any autonomous retriever returns different
files per call. Both violate reproducibility *if placed in the loop*. The only way to
use them safely is **generate/fetch once, commit the artifact, then treat it as static
data** — at which point it's just a (fancier) way to fill the local bank, run offline
by a human, not a runtime dependency.

**Cost.** The base-of-games campaign generates *many* games (OBJECTIVES.md). Per-asset
fees (PixelLab credits; Recraft $0.04; OpenAI up to $0.25; Scenario/Layer subscriptions)
multiply across games and repair iterations if wired into generation. Local bank = $0.

**Latency.** Network round-trip per part vs. instant dict lookup. In a repair loop that
may re-run generation several times, this compounds.

**License at scale.** CC0 local bank = clean. AI-generated = you generally own paid-plan
outputs, **but** purely AI-generated art has unsettled copyright status (e.g. US
Copyright Office declines registration for non-human-authored works) and RAIL-M carries
behavioral-use terms — acceptable for a public build-log site, but a compliance surface
we don't need yet. Retrieval (OpenGameArt/itch) = worst case (mixed BY/SA/GPL).

**What actually touches the certificate:** nothing here. Sprites are never read by
G0–G3, checkpoints, the solvability probe, or the witness replay logic — `render.py`
draws primitives from `world.query()` today. So every tool in this note is, at best,
**presentation polish + a future-engine skinning convenience**, not a capability the
verification pipeline lacks.

---

## 6. Recommendation ladder

1. **Now — build the local bank.** `notes/parts_bank/` grows a `parts.json` (physics
   contract per named part) + a committed **Kenney CC0 spritesheet** + a slicing map.
   The LLM pulls parts *by name* against this local index at generation-time. Deterministic,
   free, offline, license-clean. **No MCP, no API.**
2. **Optional, build-time only — PixelLab for themed gaps.** When a prompt's fiction
   isn't covered by CC0 art (e.g. a very specific themed character), call **PixelLab.ai**
   (REST API preferred over the stale MCP wrapper) **after certification**, **cache the
   transparent PNG into the versioned bank**, and never let it touch generation or
   verification. This is the single "genuinely useful" tool and only in this narrow role.
3. **Human authoring aid (Elias, off-pipeline) — diivi/aseprite-mcp** (295★, MIT, most
   mature) if hand-crafting a few bespoke bank sprites conversationally is desired.
   Requires a local Aseprite; outputs get committed like any other asset.
4. **Rung-4 bookmark — phaserjs/editor-mcp-server** for when the Phaser visual on-ramp
   lands; revisit then, knowing it's an authoring layer (consistent with
   `GAME_ENGINE_INTEGRATIONS.md`), not the per-tick loop.
5. **Avoid — retrieval MCPs** (find-assets.xyz, itch scraping) and **in-loop generators**:
   non-deterministic, license-murky, and solving a problem (thematic sprites) that the
   local bank + optional PixelLab cache already solve without a runtime dependency.

**Bottom line:** the MCP answer for the parts bank is **"not needed / premature"** for
everything except a *possible, optional, cached, build-time* PixelLab.ai call to cover
themed sprites the CC0 bank lacks. Start local.

---

## 7. Sources (all fetched/verified 2026-07-13)

MCP servers (maturity via GitHub API — stars / last push):
- pixellab-code/pixellab-mcp — https://github.com/pixellab-code/pixellab-mcp (37★, pushed 2025-08-10, no license)
- MubarakHAlketbi/game-asset-mcp — https://github.com/MubarakHAlketbi/game-asset-mcp (146★, pushed 2026-05-29, MIT)
- diivi/aseprite-mcp — https://github.com/diivi/aseprite-mcp (295★, pushed 2026-07-02, 104 tools)
- willibrandon/pixel-mcp — https://github.com/willibrandon/pixel-mcp (94★, pushed 2025-10-18)
- youichi-uda/aseprite-mcp-pro — https://github.com/youichi-uda/aseprite-mcp-pro (18★, one-day repo)
- ayigityol/aseprite-mcp — https://github.com/ayigityol/aseprite-mcp (6★, one-day repo)
- phaserjs/editor-mcp-server — https://github.com/phaserjs/editor-mcp-server (34★, pushed 2025-09-19)
- ochowei/itch-market-mcp — https://github.com/ochowei/itch-market-mcp
- ConstantineB6/comfy-pilot, tuannguyen14/ComfyAI-MCP-GameAssets, PurlieuStudios/comfyui-mcp (ComfyUI MCP family)
- MCP Find Assets — https://find-assets.xyz/docs (hosted; single `mcp-find-assets` tool)
- Tiled MCP — https://lobehub.com/mcp/subzerox9-tiled-mcp-server
- Registries: https://github.com/punkpeye/awesome-mcp-servers (90.7k★), https://glama.ai/mcp/servers , https://mcp.so , https://smithery.ai , https://mcpservers.org

AI generation APIs:
- PixelLab API — https://www.pixellab.ai/pixellab-api ; MCP setup https://www.pixellab.ai/mcp ; ToS https://www.pixellab.ai/termsofservice ; review https://www.jonathanyu.xyz/2025/12/31/pixellab-review-the-best-ai-tool-for-2d-pixel-art-games/
- Scenario — https://www.scenario.com/pricing ; FAQ https://help.scenario.com/en/articles/frequently-asked-questions-faq/
- Layer.ai — https://www.layer.ai/ ; https://docs.layer.ai/
- Recraft API — https://www.recraft.ai/docs/api-reference/pricing ($0.04 raster / $0.08 vector)
- OpenAI image API — https://developers.openai.com/api/docs/models/gpt-image-1 (transparent PNG; deprecating 2026-10-23)

Local-bank / license:
- Kenney (CC0) — https://kenney.nl/ ; support/license https://kenney.nl/support
- OpenGameArt license mix — https://opengameart.org/content/faq ; https://opengameart.org/forumtopic/attribution-and-licenses-0
