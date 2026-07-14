# Claude skills & ready-made plugins for game generation — the July-2026 landscape

> Research agent, 2026-07-14. **Extends** `notes/engines/GODOT_SKILLS_WORLDGEN.md` (Godot-only,
> same day) to **any engine/framework usable for text→2D-game generation**, and goes **deeper on
> the SKILLS / plugin angle**: Claude Code skill packs, plugin marketplaces, MCP servers, and
> end-to-end generation pipelines. Every ★/date/license below was fetched **live from the GitHub
> API or the source page on 2026-07-14**; nothing is invented. Grading lens throughout: fit to our
> pipeline (text → JSON spec → typed-state verify + witness) and to Elias's **two-prompt
> certified-parts MENU** hypothesis (`harness/gen/retrieval.py`, `CONTRACTS §9`, `godotworld/SPEC.md`).
>
> **Headline update vs the Godot-only dive:** the broader survey moves the menu verdict. The prior
> note said *"nobody does our pattern."* Broadened, the **component-library-reuse** idea is now
> real in **production** (Phaser Game Agent, closed/hosted) and in **research** (OpenGame, open) —
> but always with **human/VLM/acceptance-test** certification, never **offline physics-invariant
> certification + deterministic retrieval + typed-state witness replay**. That exact conjunction is
> still unoccupied as an open, vendorable artifact. Our moat holds; the "novel idea" claim softens.

---

## Axis 1 — Claude Code SKILLS ecosystem (SKILL.md packs) for game dev/gen

**Official repo has NO game skill.** `anthropics/skills` top-level skills (verified live): `algorithmic-art,
brand-guidelines, canvas-design, claude-api, doc-coauthoring, docx, frontend-design, internal-comms,
mcp-builder, pdf, pptx, skill-creator, slack-gif-creator, theme-factory, web-artifacts-builder,
webapp-testing, xlsx`. Closest adjacents are **webapp-testing** and **web-artifacts-builder** (both
browser-app oriented) — neither is game-shaped. So every game skill below is **community**, not first-party.

| Skill / pack | Live stats (2026-07-14) | What it automates | Engine | Fit / signal |
|---|---|---|---|---|
| **gamedev-skills/awesome-gamedev-agent-skills** | **265★ / 20f**, Apache-2.0, Python, created 2026-06-24, **pushed 2026-07-13** (active), 1 issue | **66 version-pinned SKILL.md authoring playbooks + a master router** that detects engine from project files (`project.godot`→Godot…) and loads the right skill. Portable across Claude Code/Cursor/Codex/Gemini | Godot, Unity, Unreal, web | Best-in-class **playbook menu**, not a component menu. Mine for **generator-prompt text** + the **router = engine-detect→load** pattern. (grade unchanged vs prior note; refreshed +1★) |
| **opusgamelabs/game-creator** (aka PlayableIntelligence/game-creator) | **285★ / 36f**, **NO LICENSE file** (verified), JS, created 2026-01-28, pushed 2026-05-25 (~7wk stale) | Full plugin: **9 skills, 8 commands, 4 autonomous agents**; pipeline **scaffold → design → audio → deploy → monetize** with **build/visual gates** instead of manual confirm. Has `skills/ agents/ templates/ examples/ assets/` | **Phaser 2D + Three.js 3D**, browser | Also an Axis-3 pipeline. **`templates/` = project skeletons, not certified parts.** **License risk is disqualifying for vendoring** (no LICENSE = all-rights-reserved). Idea-mine only. |
| **Yakoub-ai/phaser4-gamedev** | **14★ / 0f**, **no license**, Shell, created 2026-04-07, pushed 2026-05-01 (stale) | Claude Code plugin: **4 agents + 6 skills** encoding Phaser 4 API; commands `/phaser-new /phaser-run /phaser-validate /phaser-build` | Phaser 4 | Early/tiny. The `/phaser-validate`+`/phaser-run` command split mirrors our gen→verify cadence. Low maturity. |
| **HermeticOrmus/claude-code-game-development** | **48★ / 8f**, MIT, Python, created 2025-11-16, pushed 2026-05-25 (stale) | "Game development **patterns & workflows** for Claude Code" — prose playbooks | engine-agnostic | Docs/patterns, no generation machinery. Minor. |
| **gked2121 Game Builder** (skillsdirectory) | **8★**, security "Grade A", added 2025-12-21, scanned 2026-02-10 | Turns **any topic → playable browser game** (trivia/matching/word/adventure) | **Phaser.js / Kaboom.js** | Education-focused, tiny. Proof that topic→game one-shot skills exist; not reusable substance. |
| **game-asset-generate** (skills.rest) | listing only (page 403 to fetch; snippet-verified) | **"Library-first"** asset mass-production: search/route to best tool per type (sprite/VFX/3D/UI/icon/audio), 12-element "Soul" prompts, **artifact manifests**; FLUX/Gemini/Replicate adapters | engine-agnostic assets | Axis-4 relevant: **"library-first" = dedup/reuse of *assets*, not certified gameplay components.** Useful for the cosmetic sprite step; manifest idea aligns with our integrity manifest. |
| **Randroids-Dojo/Godot-Claude-Skills** | 35★, MIT, pushed 2026-01-19 (**stale ~6mo**) | 1 skill: GdUnit4 tests, PlayGodot E2E, exports, CI/CD | Godot | Cross-ref (prior note): CI/testing, not generation. |

**Axis-1 verdict:** the game-skill layer is **entirely community, mostly Phaser/web, mostly authoring
playbooks or asset-gen** — not spec-generation-with-verification. The most credible pack
(`awesome-gamedev-agent-skills`, Apache-2.0) is a **router over prose playbooks**. Nothing here
emits a verifiable data spec or runs a typed check.

---

## Axis 2 — MCP servers for game engines + asset generation

Two tiers (per Summer Engine's roundup, corroborated): **file-level** (read/edit project files) and
**engine-level** (bridge to a running engine). All exposed as MCP tools, editor/authoring oriented.

| MCP server | Live stats (2026-07-14) | Exposes | Maturity |
|---|---|---|---|
| **CoplayDev/unity-mcp** | **12,469★ / 1,327f**, MIT, C#, created 2025-03-18, **pushed 2026-07-13**, 73 issues | **47 tools**: manage assets, control scenes/GameObjects, edit C# scripts, run tests, profile, build. Works with Claude Code/Desktop, Cursor, etc. | **Most mature engine-MCP by far.** Unity = weak fit for our code-first 2D lane, but the tool taxonomy is the reference. |
| **tugcantopaloglu/godot-mcp** | **336★ / 58f**, MIT, JS, created 2026-02-08, **pushed 2026-07-13** | **157 tools**, full Godot 4.x control (GDScript + C#/.NET), tested on **Godot 4.7** | Newest, most tool-rich Godot MCP; active. Superset of Coding-Solo. |
| **Coding-Solo/godot-mcp** | ~4.7k★, MIT, JS/GDScript (prior note) | launch editor, run project, `create_scene/add_node/load_sprite/save_scene`, **capture debug/console output** | Established; **run→capture-stderr** idiom = our repair-loop signal. |
| **bradypp/godot-mcp** | fork of Coding-Solo | same family, extended | Alt fork; no new capability class. |
| **Erodenn/godot-mcp-runtime**, **GDAI MCP** | runtime bridge (port 9090) / commercial polished | runtime interaction; hosted product | Cross-ref (RL/runtime dive #2); GDAI is closed. |
| **microsoft/playwright-mcp** | Microsoft, mature | Browser automation for **web games**: **accessibility-tree-first** (2-5KB vs 100KB screenshot) + `browser_take_screenshot` with `scale:device` (PR #41465, 2026-06-25) | **The de-facto playtest/screenshot substrate** for browser-game demos. Not game-specific; that's what people use. |

**Asset-generation MCPs (the sprite/tileset step):**

| MCP | Live stats | Generates | Note |
|---|---|---|---|
| **MubarakHAlketbi/game-asset-mcp** | **147★ / 38f**, MIT, JS, created 2025-03-17, pushed 2026-05-29 | 2D/3D assets from text via **Hugging Face models** | **Free/OSS**, self-hostable. Best license for the cosmetic asset step. |
| **PixelLab MCP** (pixellab.ai) | commercial (API token) | Characters, **Wang/platform/isometric tilesets**, animations; **framework-agnostic incl. Godot-ready** | Highest-quality tilesets, but **paid API** = dependency/cost risk. |
| **willibrandon/pixel-mcp** (+ `pixel-plugin`) | **94★ / 17f**, MIT, Go, pushed 2025-10-18 (**stale ~9mo**) | Aseprite-driven pixel art, animation, spritesheet export | OSS but dormant; needs Aseprite. |
| **Aseprite MCP Pro** (y1uda, itch) | commercial | **121 tools**, incl. 10 **Godot export** tools (SpriteFrames/AnimationPlayer/AtlasTexture/TileSet `.tres`) | Deepest Godot asset export; paid. |
| **SpriteCook / PixelMCPServer** | listings | sprites/tilesets/animations, autotile (blob47) | Sampled, not deep-verified. |

**Axis-2 verdict:** engine-MCPs are **live-editor authoring tools** (scene/asset/script mutation),
not headless batch generators — same finding as the Godot dive, now confirmed for Unity too. The
**asset-gen MCPs are the one genuinely plug-in-able layer for us** (sprites are cosmetic in our
design). Playwright-MCP is the demo-capture substrate if we ever render web demos.

---

## Axis 3 — End-to-end "prompt → playable game" pipelines (and do any VERIFY?)

The differentiator question: does the pipeline prove anything **beyond "it builds/renders"**?

| Pipeline | Live stats / status | Architecture | Verification (the key column) | License / reusable? |
|---|---|---|---|---|
| **godogen** (htdt) | **4,715★ / 415f**, MIT, Python, created 2026-02-06, **pushed 2026-07-13** (active); **now multi-engine: "Godot, Bevy, and Babylon.js"** (expanded since our Godot dive) | Claude Code/Codex agent; builds scene graph programmatically → serialize; **C# over GDScript** | **Screenshot → Gemini-Flash visual QA → fix**, + **node-count pre-save gate**. Proof = **pixels**, judged on a live clip | **MIT — reusable.** Adopt the serialize-and-validate technique + node-count gate (see prior note §6). |
| **OpenGame** (leigest519) | **2,703★ / 393f**, **Apache-2.0**, TypeScript, created 2026-04-20, pushed 2026-04-22 (**static research drop**, 2-day window) | Agentic framework + **GameCoder-27B** (RL on execution). **"Game Skill" = Template Skill (grows a library of project *skeletons*) + Debug Skill (living protocol of *verified fixes*)** | **OpenGame-Bench**: dynamically launches the game, **drives it with scripted interactions**, judges **Build Health / Visual Usability / Intent Alignment** via **headless browser + VLM**. Beyond "it runs" | **Apache-2.0 — reusable.** The **scripted-interaction-then-assert** loop and the learned skeleton/fix libraries are the most study-worthy open artifact. Web/JS games. |
| **OpusGameLabs game-creator** | 285★, **no license**, JS | scaffold→design→audio→deploy→monetize; 4 agents | **build/visual gates** only (compiles + renders) | **No license — not vendorable.** |
| **Phaser Game Agent** (phaser.io, **Superserve + Claude *Managed Agents***) | **hosted commercial beta** (June 2026) | Fleet of Claude managed agents author a **manifest**, **pull components** from a library, procedural art + synth SFX + chiptune, wire together | **Automated mechanic checks (e.g. meter-wrap) + acceptance tests + HUMAN playtest; only human-approved components published back to the library** | **Closed/hosted — not vendorable.** But see Axis 4: it's the strongest menu-pattern instance. |
| **Rosebud AI** | hosted commercial, 2.3M community games | "Vibe Coding" → JS/Three.js/React; multifile arch | none disclosed beyond "it plays" | Closed. Scale proof only. |
| **Summer Engine "Prompt to Game"** | hosted commercial | prompt→game, ship-to-Steam claims | none disclosed | Closed. |

**Axis-3 verdict:** verification-beyond-runs now exists in **two open forms** — godogen (pixel/VLM QA
+ node-count gate) and **OpenGame-Bench (headless-run + scripted-interaction + VLM on build/usability/
intent)** — plus **Phaser Game Agent** (acceptance tests + human playtest, closed). **All oracles are
pixel/VLM/human, none is typed-state + witness replay.** Our verify remains categorically different
(and cheaper/deterministic). See the research corroboration in Axis 4 / honesty block.

---

## Axis 4 — THE MENU QUESTION (Elias's hypothesis), re-verified harder & broader

Our pattern's three ingredients: **(A)** a curated, **physics-invariant-CERTIFIED** parts bank
(bank-CI proves masses/joints/invariants offline); **(B)** **deterministic reproducible retrieval**
(pure fn of `(prompt, bank_version)`, hashable into the manifest); **(C)** an **advisory constrained
MENU** to the generator with a real escape hatch — realized as **certified templates** a frozen runner
instantiates, then **typed-state verify + witness replay**.

| System | (A) curated lib? | Certified how? | (B) retrieval? | (C) menu→gen? | Verify = state+witness? |
|---|---|---|---|---|---|
| **Phaser Game Agent** (closed) | **YES** — reusable blocks by capability | **HUMAN playtest approval** + acceptance tests | agent "pulls components" (not a formal deterministic retriever) | **YES** (reuse over first-principles) | no (acceptance tests + human) |
| **OpenGame** Template/Debug Skill (open) | **YES** — project **skeletons** + verified **fixes** | execution-grounded (bench) | learned recall, not deterministic BM25 | partial (skeletons, not parts) | no (VLM bench) |
| **game-asset-generate** "library-first" | assets only | none | dedup/route by asset type | assets, not mechanics | no |
| **Native Claude-in-Unity RAG** (Kukharuk write-up) | your project's prefabs/assets | none (indexes what exists) | **YES** (RAG over project) | for **editing**, not generation-menu | no |
| **godot-procedural3d** (prior note) | yes (rooms+objects) | **geometric fit only** | algorithmic, not LLM | no | no |
| **godot-llm RAG** (prior note) | lore text only | no | yes (over lore) | text-gen | no |
| **OUR pipeline** | **YES** | **physics invariants, offline CI** | **YES, deterministic + hashed** | **YES, advisory + escape hatch** | **YES** |

**Refined verdict (this is the update):** the **component-library-reuse** half of Elias's hypothesis
is **no longer unprecedented** — the **Phaser Game Agent** ships it in production (pull-from-library +
grow-the-library + acceptance-gate) and **OpenGame** open-sources a learned skeleton/fix library. So
"reuse a growing bank instead of first-principles" is **validated, not novel**. **But the specific
conjunction we build is still unoccupied by any open, vendorable artifact:** (A) **offline
physics-invariant certification** (everyone else uses human approval, VLM, or geometric fit), (B)
**deterministic reproducible retrieval hashed into an integrity manifest** (everyone else uses
agent-driven or learned recall), and (C) **typed-state verify + witness replay** (everyone else uses
pixels/VLM/acceptance/human). The moat narrows from "the whole idea" to **"the certified-parts +
deterministic-retrieval + typed-witness conjunction."** That is still genuinely differentiated —
and, notably, the **research direction independently converges on our verify** (next section).

---

## Axis 5 — Adoption shortlist (top 5 to vendor/adapt into our pipeline)

| # | Artifact | What we'd take | Integration point | Effort | License | Risk |
|---|---|---|---|---|---|---|
| 1 | **OpenGame / OpenGame-Bench** (leigest519, Apache-2.0) | The **scripted-interaction-then-assert** verify loop + the **Debug Skill "living protocol of verified fixes"** concept (a fix-cache keyed by error signature) | **Repair loop**: a persistent verified-fix store feeding regeneration; study its bench gates to harden our G-gate messaging | Med (study TS, port concept) | Apache-2.0 | Static drop (unmaintained since 2026-04-22); VLM-based bench not our oracle |
| 2 | **godogen technique** (htdt, MIT) | Headless **build-graph → serialize + node-count pre-save gate**; now-broadened multi-engine structure (Bevy/Babylon confirm the approach generalizes) | **Offline certified-template authoring** (bank-CI) | Low-Med | MIT | It's an authoring technique, not drop-in; already scoped in prior note §6 |
| 3 | **microsoft/playwright-mcp** | Accessibility-tree-first drive + `scale:device` screenshots | **Demo capture / optional pixel sanity** for any web-rendered demo — never the verification oracle | Low (drop-in MCP) | OSS (MS) | Web-only; keep it out of the correctness path |
| 4 | **game-asset-mcp** (MubarakHAlketbi, MIT) *or* **PixelLab MCP** (commercial) | Text→sprite/tileset generation; PixelLab emits **Godot-ready Wang/platform tilesets** | **Cosmetic sprite/asset step** (sprites are cosmetic by design; keep off the verify path) | Low | MIT (game-asset-mcp) / paid (PixelLab) | PixelLab = paid API dependency; game-asset-mcp = HF model quality variance |
| 5 | **gamedev-skills/awesome-gamedev-agent-skills** (Apache-2.0) | **Generator-prompt text** for engine playbooks + the **router pattern** (detect engine → load skill) | **Spec-generation prompt assembly** (choose engine/archetype context per request) | Low (copy prose) | Apache-2.0 | Prose playbooks, not components; attribution hygiene |

Explicitly **NOT** adopting: `opusgamelabs/game-creator` (no license), Rosebud / Phaser Game Agent /
Summer Engine / GDAI (closed/hosted), Unity-MCP (wrong engine for our 2D code-first lane).

---

## Honesty block — what's inference, what was unreachable

- **Unreachable pages:** `skills.rest/skill/game-asset-generate` (403), `claudepluginhub.com/…game-creator`
  (403), `claudemarketplaces.com/skills/category/game-dev` (page renders skills client-side; enumeration
  came from search snippets, not a full DOM). `opusgamelabs/game-creator` **README 404'd on `main`** —
  its structure/license came from the **GitHub contents API** (confirmed **no LICENSE file**); pipeline
  stages are from search + the Phaser/plugin-hub snippets, **not** a first-hand README read.
- **OpenGame weights:** secondary sources claim **GameCoder-27B weights are released**; I verified the
  **GitHub framework** (Apache-2.0, TS) live but did **not** independently confirm the HF weights. The
  repo was **pushed only 2 days after creation** → treat as a **static research drop**, not a maintained tool.
- **Phaser Game Agent:** details from **one source** (the phaser.io engineering blog); it's a **hosted beta**
  I could not run. "Pulls components / grows the library / human-approved-back" is their description, taken
  at face value — I could not inspect the retrieval mechanism (may be agent-judgment, not a formal retriever).
- **Research corroboration (not adoptable, but load-bearing for our thesis):** **GameGen-Verifier**
  (arXiv 2605.07442) independently proposes **keypoint verification via runtime STATE INJECTION** — patch
  the runtime into a target state, run a bounded interaction, assert the keypoint — reporting **92.2%
  agreement with humans vs 58.8%** for Agent-as-a-Verifier and **up to 16.6× faster**. This is the
  academic mirror of **our typed-state verify**, and validates the direction over pixel/VLM oracles.
  Engine unspecified, **code-release status unconfirmed** — cited as evidence, not as an artifact to vendor.
- **Sampling limits:** the marketplaces claim 30k+ skills; I sampled the **game-dev slice** and the named
  repos, and hard-verified stars/dates/licenses via the GitHub API. Long-tail single-author skills
  (LobeHub/crossaitools/mcpmarket listings) were not each opened.
- **Stat freshness:** all ★/dates fetched **2026-07-14**; they drift.

---

## Sources (fetched live 2026-07-14)
- Official: https://github.com/anthropics/skills (skills/ list verified via contents API — no game skill)
- Skills packs: https://github.com/gamedev-skills/awesome-gamedev-agent-skills (265★/20f, Apache-2.0, pushed 2026-07-13) ·
  https://github.com/opusgamelabs/game-creator (285★/36f, **no LICENSE**, pushed 2026-05-25; mirror PlayableIntelligence/game-creator) ·
  https://github.com/Yakoub-ai/phaser4-gamedev (14★, no license, pushed 2026-05-01) ·
  https://github.com/HermeticOrmus/claude-code-game-development (48★, MIT, pushed 2026-05-25) ·
  https://www.skillsdirectory.com/skills/gked2121-game-builder (8★, Phaser/Kaboom) ·
  https://skills.rest/skill/game-asset-generate (library-first asset skill; page 403, snippet-verified)
- MCP: https://github.com/CoplayDev/unity-mcp (12,469★/1,327f, MIT, 47 tools, pushed 2026-07-13) ·
  https://github.com/tugcantopaloglu/godot-mcp (336★/58f, MIT, 157 tools, Godot 4.7, pushed 2026-07-13) ·
  https://github.com/Coding-Solo/godot-mcp · https://github.com/microsoft/playwright-mcp ·
  https://github.com/MubarakHAlketbi/game-asset-mcp (147★/38f, MIT, HF models, pushed 2026-05-29) ·
  https://github.com/willibrandon/pixel-mcp (94★, MIT, Go, pushed 2025-10-18) · https://www.pixellab.ai/mcp (commercial) ·
  https://aseprite-mcp.abyo.net/ (121 tools, Godot export, commercial)
- Pipelines: https://github.com/htdt/godogen (4,715★/415f, MIT, now Godot+Bevy+Babylon.js, pushed 2026-07-13) ·
  https://github.com/leigest519/OpenGame (2,703★/393f, Apache-2.0, TS, GameCoder-27B, pushed 2026-04-22) ·
  https://arxiv.org/abs/2604.18394 (OpenGame paper / OpenGame-Bench) ·
  https://phaser.io/news/2026/06/how-we-built-the-phaser-game-agent-with-claude-managed-agents-and-superserve (hosted) ·
  https://rosebud.ai/ (hosted) · https://www.summerengine.com/prompt-to-game (hosted)
- Verify research: https://arxiv.org/abs/2605.07442 (GameGen-Verifier — keypoint verify via runtime state injection)
- Local lens: `notes/engines/GODOT_SKILLS_WORLDGEN.md`, `godotworld/SPEC.md`, `harness/gen/retrieval.py`, `CONTRACTS.md §9`
