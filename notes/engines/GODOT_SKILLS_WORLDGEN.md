# Godot world-generation AI tooling — deep dive #1 (evaluated vs OUR bank/menu pipeline)

> Research agent, 2026-07-14. Deep-dive #1 of the Godot double-dive: the
> CLAUDE-SKILLS / AI-tooling ecosystem for **world/scene generation**, graded against
> OUR two-prompt parts-bank pattern (`harness/gen/retrieval.py`, `CONTRACTS §9`) and the
> Godot migration verdict (`notes/engines/GODOT_MIGRATION.md`). Every stat below was
> fetched live from the GitHub API or source pages on 2026-07-14; no invented repos.
> Companion (dive #2, separate agent) covers the RL-agent/runtime half.
>
> **Hypothesis under test (Elias):** "world-gen skills and RL-agent tooling exist
> separately with good docs, and the merge is easy because it's sequential." This half's
> finding: the world-gen skills **do** exist and are well-documented, but they are
> **authoring generators aimed at pixels/playability**, not certified-component pipelines —
> so the merge is NOT free: our menu + typed-state verify is exactly the missing middle.

---

## 1. Per-tool deep cards (world-generation relevant only)

Grading lens: what it GENERATES · from what INPUT · GUARDRAILS · MATURITY (live) · fit to
OUR pipeline. Editor-authoring-only / RL-runtime tools already covered in `GODOT_MIGRATION §3`
and `youtube_scan.md` are not re-carded here except where they touch *world generation*.

### godogen (htdt) — the reference autonomous Godot generator
- **Live:** 4713★, 415 forks, MIT, Python 91%, created 2026-02-06, **pushed 2026-07-13**
  (active), 4 open issues. Uses Claude Code w/ **Opus 4.6** (Sonnet 4.6 with more steering).
  Demo (real gameplay): youtu.be/eUz19GROIpY. Show HN: news.ycombinator.com/item?id=47400868.
- **Generates:** a whole Godot 4 **C#/.NET project**. Crucially for us, scenes are NOT
  hand-written .tscn and NOT free GDScript. From `engines/godot.md` (verbatim): *"Write
  scenes as **C# `SceneTree` scripts** that run once headless and emit a `.tscn`"* — a
  builder instantiates the node hierarchy, sets props, attaches scripts, then
  `PackedScene.Pack()` + `ResourceSaver.Save()`. Runtime logic = C# scripts. Jolt for 3D.
- **Input:** one natural-language game description. No component library, no menu, no
  retrieval — generation is **first-principles** from a one-page engine guide the agent
  re-derives scaffolding from.
- **Guardrails (directly reusable):** (a) `.tscn` serialization is treated as a
  *"silent-failure system"* — nodes silently drop / files bloat if the owner chain is wrong
  or GLB nodes are traversed recursively; (b) so it **counts nodes before packing,
  `Instantiate()`s the `PackedScene` after, compares counts, and gates `ResourceSaver.Save()`**
  on the match; (c) build sequence `dotnet build → godot --headless --import →
  godot --headless --quit`; (d) **C# over GDScript** because *"the compiler catches what
  GDScript lets through silently"* and *"dotnet build replaces per-file --check-only
  pre-validation"*; (e) proof-over-claims: **screenshots of the running game → Gemini Flash
  visual QA → fix**, judged on a live URL / 15-20s clip, never on a clean compile.
- **Fit:** the single most important external data point for our Godot lane. It independently
  converged on **build-the-graph-programmatically-then-serialize**, exactly the mechanism we
  should use to *build our certified `.tscn` templates offline* (bank-CI), and its
  **node-count pre-save gate** is a concrete guardrail to adopt. But it proves via PIXELS; we
  prove via typed state + witness replay — that is our differentiator, unchanged.

### Randroids-Dojo/Godot-Claude-Skills — testing/CI, NOT world-gen
- **Live:** 35★, 2 forks, MIT, **pushed 2026-01-19 (stale ~6 months)**, created 2025-12-03,
  0 issues, **1 skill**. Installs via `/plugin marketplace add Randroids-Dojo/Godot-Claude-Skills`.
- **Generates:** nothing world-shaped. The one skill wraps **GdUnit4 unit/scene tests,
  PlayGodot E2E automation, web/desktop exports, CI/CD (GitHub Actions → Vercel/itch.io)**,
  helper scripts `run_tests.py / parse_results.py / export_build.py / validate_project.py`.
- **Fit:** minor — **GdUnit4 headless test invocation + `parse_results.py`** is a reusable
  harness-CI idea (structured test results back to the agent), nothing for generation. The
  migration note's "Minor" grade stands; confirm it is effectively unmaintained.

### Coding-Solo/godot-mcp — imperative scene tools, no palette
- **Live:** ~4.7k★, MIT, JS 60% / GDScript 40%, no releases (`npx @coding-solo/godot-mcp`).
- **Generates:** via MCP tools, not a generator per se — `create_scene` (root node type),
  `add_node` (typed props), `load_sprite` (Sprite2D+texture), `save_scene`, export MeshLibrary
  for GridMap; plus launch/run + **capture debug/console output** and UID management (4.4+).
- **Input:** the LLM passes node params **directly** — **no prefab/template menu**.
- **Fit:** its **run → capture stdout/errors** loop is the error-feedback pattern for our
  repair loop (already noted in migration §3). Scene tools are live-editor authoring, not a
  headless batch substrate. No menu.

### RodZill4/godot-procedural3d — the CLOSEST structural analog to a "menu" (but algorithmic)
- **Live:** 77★, 13 forks, MIT, GDScript 99%, no releases.
- **Generates:** procedural 3D dungeons by assembling a **curated library of modular room
  prefabs** (each with typed **exits/connection points**) + an **objects library** keyed by
  type (doors/traps/treasure/enemy-spawn), placed into per-room "object locations".
- **Input:** algorithmic — exit-matching + object-slot filling. **Not AI, not retrieval, not
  physics-certified.** Assets from OpenGameArt.
- **Fit:** structurally the nearest thing in the ecosystem to a *component palette with
  placement constraints* — proof the "library of pre-authored parts + connection contract"
  idea is natural in Godot. But it is a hand-coded generator, not an LLM-facing retrieved
  menu, and its "certification" is only geometric fit, not physics invariants like our bank-CI.

### godot-llm (Adriankhl) — RAG, but over LORE not components
- **Live:** 248★, MIT, **v1.0 May 2024 (stale)**. In-editor GGUF LLM + a vector DB.
- **RAG scope (verbatim):** *"information about your game world or your character into the
  vector database, retrieve relevant texts to enrich your prompt, then generate text"* —
  retrieval over **narrative text for long-term memory / text generation**, NOT over scene
  components. Generates text, not scenes/GDScript.
- **Fit:** important negative result for the MENU question — the one repo that pairs "Godot"
  + "RAG" retrieves lore, not certified parts. Confirms our pattern is unoccupied.

### Godot AI Assistant (champ-gaming) & AI Autonomous Agent (asset 4583) — LLM writes .tscn text
- **Godot AI Assistant:** in-editor, Groq **LLaMA 3.3 70B**; a **Scene Generator produces
  valid Godot 4 `.tscn` from text descriptions and *auto-sanitizes the output to fix common
  AI formatting mistakes so scenes load correctly*.** The existence of a mandatory sanitizer
  is **direct evidence that LLM-hand-written `.tscn` is fragile** (RQ3).
- **AI Autonomous Agent (asset 4583):** editor agent that programmatically creates/modifies
  `.tscn` (nodes, properties, hierarchy) with filesystem access.
- **Fit:** both are the "LLM emits raw .tscn" school — the approach godogen *rejected*. Their
  sanitize/repair machinery is the cost of that choice; we avoid it by construction (§3).

### Also-relevant, non-worldgen (cross-ref, not re-carded)
- **hi-godot/godot-ai** (150+ editor ops, **pixel/screenshot feedback**) — authoring, pixels,
  opposite of our no-pixels design (`GODOT_MIGRATION §3`).
- **gamedev-skills/awesome-gamedev-agent-skills** (264★, Apache-2.0, 66 SKILL.md + a router) —
  a "menu" of **authoring playbooks**, not components; a good source of generator-prompt text.
- **Erodenn/godot-mcp-runtime** (runtime McpBridge) — RL/runtime half, dive #2.

---

## 2. THE MENU QUESTION — verdict: NOBODY does our pattern (evidence)

**Our pattern (the thing to match):** prompt-1 is a **deterministic, no-LLM, no-network BM25
retrieval** over a **versioned + SHA-hashed bank of ~30-60 PRE-CERTIFIED physics parts**
(`bank-CI` proves masses/joints/invariants offline), producing a **pinned advisory MENU**;
prompt-2 generates against that menu; the bank is DATA, sprites are cosmetic, escape hatch
(`world.add`) always open. (`retrieval.py`, `CONTRACTS §9`, `bank_menu.md.tmpl`.)

**Finding: no surveyed tool implements this, or anything close.** Mapped against the three
ingredients (curated+certified library · per-request retrieval · constrained menu to the
generator):

| Tool | Curated library? | Certified (physics)? | Retrieval? | Menu to LLM gen? |
|---|---|---|---|---|
| godogen | no (first-principles) | n/a | no | no |
| godot-procedural3d | **yes** (rooms+objects, geometric) | geometric-fit only | no (algorithmic) | no (not LLM) |
| godot-llm RAG | lore text only | no | **yes** (over lore) | no (text-gen) |
| Coding-Solo/godot-mcp | no | no | no | no |
| Godot AI Assistant | no | no | no | no |
| awesome-gamedev-skills | **yes** (66 playbooks) | no (authoring docs) | router-selected | menu of *skills*, not parts |

**Closest analogs & the gap:** (1) **godot-procedural3d** has the *library-with-connection-
contract* half but assembles algorithmically and certifies only geometric fit — no physics
invariants, no LLM, no retrieval. (2) **godot-llm** has the *retrieval* half but over lore
for text-gen. (3) **godogen's one-page engine guide + build-time templates** is the closest
*generator-side* analog: a fixed advisory context the agent builds from — but it is a single
static guide, not a per-prompt retrieved subset of certified parts. (4) The **awesome-gamedev
skill router** selects *playbooks*, the nearest thing to "retrieve a relevant subset and offer
it," but the items are prose authoring guides, not instantiable certified components.

**What we'd add on top (net-new, our moat):** the *conjunction* — (a) a bank of parts with
**physics invariants certified offline by bank-CI**, (b) **deterministic reproducible
retrieval** (pure function of `(prompt, bank_version)`, hashable into the integrity manifest),
(c) an **advisory constrained menu** with a real escape hatch, (d) the parts realized as
**certified `.tscn` templates** the frozen `runner.gd` instantiates. No one in the ecosystem
combines these; the migration note's plan (`§2.3`, bank noun → certified `.tscn`) is genuinely
differentiated, not a reinvention.

---

## 3. `.tscn` as target format vs JSON-spec + frozen runner.gd — recommendation

**Format facts:** `.tscn` is human-readable, text, diff-able, and widely praised as
"LLM-friendly" (`[gd_scene ... format=3]`; the `format=3` header is stable across all of
Godot 4.x; **4.4 added `uid://` references** — the mcp servers ship explicit "UID management
for 4.4+"). So on paper it *is* a plausible LLM target.

**But the two hardest-evidence data points cut the other way:**
- **godogen (the most mature autonomous generator, 4.7k★) refuses to hand-write `.tscn`** and
  calls the serializer a *"silent-failure system"* (nodes drop / files bloat with no error);
  it builds the graph programmatically and **validates node counts before saving**.
- **Godot AI Assistant must run an auto-sanitizer** on every LLM-produced `.tscn` "to fix
  common AI formatting mistakes so scenes load" — i.e. raw LLM `.tscn` frequently *doesn't
  load*.

**Sandbox angle (decisive for us):** a `.tscn` is not inert data — `[ext_resource
type="Script" path="res://..."]` attaches **arbitrary GDScript**, and `ext_resource` paths /
`[node]` `script =` fields let the *text* pull in executable code and resources. Letting the
LLM emit raw `.tscn` per game reintroduces exactly the GDScript ambient-authority hole the
migration note closed (`§2.3`, GodLoader-class). Validation tooling for `.tscn` is also weak:
load-time errors surface as engine log spew, not a structured capability/lint report, so our
repair loop would parse stderr rather than a typed verdict.

**Recommendation (confirms `GODOT_MIGRATION §2.3`):** for the per-game path, **LLM emits a
JSON game-spec (bodies/joints/sensors/actions/predicates); a single frozen, audited
`runner.gd` interprets it; parts resolve to PRE-CERTIFIED `.tscn` templates.** Reserve `.tscn`
generation for the **offline template-authoring path only** — and there, adopt godogen's
exact technique: build the node graph in a headless script and **serialize + node-count-gate**,
never hand-author. Net: `.tscn` is the *storage format of the certified bank*, never the
*LLM's per-game output*. This keeps the LLM emitting pure data (safe-by-construction) and
gives the repair loop a typed report (our gameverify JSON), not log-scraping.

---

## 4. Godot 4.7 / 4.8-dev check — what's usable now vs wait

Live from release notes / dev-snapshot / Jolt docs (2026-07-14). Current stable line: **4.6**
(Jan 2026, Jolt = default **3D** physics); **4.7 beta** 2026-04-24; **4.8 dev1** 2026-07-06.

**Nothing in 4.7/4.8-dev changes our headless-batch or determinism calculus.** Concretely:

| Item | Version | Usable for THIS pipeline? |
|---|---|---|
| **Jolt-2D?** | — | **No — does not exist.** Jolt is 3D-only; *"GodotPhysics2D is unchanged and remains the default for 2D."* Our 2D determinism lever stays **Rapier2D** (`MIGRATION §2.4`), untouched by 4.7/4.8. |
| Jolt determinism (context) | 4.4-4.8 | Within-build only; **Godot Jolt makes no determinism guarantee**, and minor Godot patches bump the Jolt version and break cross-version determinism. Reinforces "pin the engine + prefer Rapier for 2D." |
| Physics fixes | 4.8 dev1 | Bypass physics command queue during physics processing; fix GodotPhysics **missing area overlaps**; fix Jolt gravity init. The area-overlap fix is worth tracking (our sensors = `Area2D`) but is a bugfix, not a capability. |
| Physics-interpolation thread-safety | 4.7 | GH-116192, a fix not a feature; 2D interpolation itself dates to 4.3 and is a *rendering* smoothing, irrelevant to our fixed-step state reads. |
| **Embedded / interactive game view default** | 4.8 dev1 | **Editor-only** — the "interactive streaming/embedding work" is the *in-editor* docked game view, NOT a headless streaming API. **Not usable** for our headless batch path; ignore for the executor. |
| TileMapLayer cell-shape merge | 4.5 (shipped) | **Usable now** — big worlds with cheap merged collision (`MIGRATION §4`). Nothing new in 4.7/4.8 needed. |
| `--headless` / `--script` / `--fixed-fps` | stable, unchanged | **Usable now** — the CLI surface the runner depends on is stable across 4.6→4.8; no 4.8 improvements *and none needed*. |
| GridMap axis override, FuzzySearch API, HDR, AreaLight3D, VirtualJoystick | 4.7/4.8 | **N/A** — editor UX / 3D / mobile-input / rendering; nothing for a no-pixels 2D state pipeline. |

**Bottom line for RQ4:** build on **4.6 stable** (or pin whatever the spike installs), Rapier2D
for 2D determinism; **wait for nothing** in 4.7/4.8 and **skip** the embedded game view (it is
not headless). Watch item only: the 4.8 `Area2D` overlap fix, since our sensors depend on it.

---

## 5. Video / tutorial ecosystem (generation workflows, not authoring fluff)

Caveat (same as `youtube_scan.md`): **YouTube pages are not scrapable here** — titles/snippets
+ HN only, not transcripts. For *world-generation* specifically (vs the MCP-setup dogpile), the
genuinely useful few:

1. **godogen demo — youtu.be/eUz19GROIpY** (+ "AI Builds Complete Godot Games Autonomously —
   Here's How", watch?v=zuXjkn_dy8k): shows the full **describe → design → generate → run →
   screenshot-QA → fix** pipeline on real gameplay. Reusable for our loop: the **cadence**
   (generate → run headless → capture structured result → feed back → regenerate) is identical
   to our gameverify repair loop; the difference is their signal is **pixels via Gemini**, ours
   is **typed state + witness**. Steal the *cadence and the pre-save node-count gate*, not the
   pixel oracle.
2. **Show HN thread — news.ycombinator.com/item?id=47400868**: more useful than most videos —
   maintainer explains the C#/build-time-scene choice and the four rewrites; corroborates the
   ".tscn is a silent-failure system" rationale and the compile-time-safety argument for C#.
3. **Chyshkala write-up — "Godogen's Four Rewrites…"**: high-level but confirms the pain
   (850+ Godot classes overflow context; a "quirks database" patches training gaps; two split
   skills for orchestration vs execution). Signal: **generation quality came from guardrails
   and context engineering, not model horsepower** — validates our menu-constrains-the-model bet.

The rest of the Claude+Godot video space remains ~90% MCP-plugin setup tutorials with no
generation/feedback substance (documented in `youtube_scan.md`); nothing to add there.

---

## 6. What we VENDOR vs what we BUILD (feeds the GodotExecutor plan)

| Concern | Vendor / copy | Build ourselves | Source |
|---|---|---|---|
| 2D determinism | **godot-rapier-physics** (fast local variant; xplat variant as CI insurance) | thin `--physics=rapier2d` arg + witness-replay assertion | `MIGRATION §2.4` |
| Certified `.tscn` template authoring | **godogen's technique**: headless build-graph → `PackedScene.Pack()`+`ResourceSaver.Save()` + **node-count pre-save gate** | our **bank-CI** wrapper that runs it per part + asserts physics invariants (`CONTRACTS §9.5`) | godogen `engines/godot.md` |
| Per-game scene format | *nothing* — reject raw-`.tscn` generators | **JSON game-spec + frozen `runner.gd` interpreter**; parts → certified `.tscn` templates | `MIGRATION §2.3` |
| The MENU | *nothing exists to vendor* | our **deterministic BM25 retrieval → advisory menu**, already built for Py/JS (`retrieval.py`); re-target renderer to emit Godot part handles | this note §2 |
| Error feedback | run→capture pattern from **Coding-Solo/godot-mcp**; GdUnit4 `parse_results.py` from **Randroids** | `runner.gd` surfaces parse/build/runtime errors as **structured in-band JSON** (mirror `runner.js`) → existing repair loop | `MIGRATION §3` |
| Test harness in CI | **GdUnit4** headless invocation (Randroids) | our parity test `tests/test_godot.py` (spec-game through the funnel) | `MIGRATION §8` |
| Visual proof (site demos) | godogen's screenshot loop *idea only* | our `Camera2D`-follow demo capture; **never** a pixel verification oracle | `MIGRATION §4` |

**One-line synthesis for the plan:** vendor Rapier + godogen's *serialize-and-validate* build
technique + the run/capture error idiom; **build the two things nobody else has** — the
deterministic **certified-parts MENU** and the **typed-state verify + witness replay** — and
wire them into `GodotExecutor` exactly as the migration note scaffolds it. Elias's "merge is
sequential/easy" hypothesis is **half right**: the world-gen half is well-served by mature
tools, but every one of them stops at pixels/playability, so the certified-menu → typed-verify
middle is *ours to build* — that's the non-trivial seam, not a bolt-on.

---

## Sources (fetched live 2026-07-14)
- godogen — https://github.com/htdt/godogen (API: 4713★/415f, MIT, pushed 2026-07-13, created 2026-02-06);
  engine guide https://raw.githubusercontent.com/htdt/godogen/master/engines/godot.md ;
  C#-vs-GDScript https://raw.githubusercontent.com/htdt/godogen/master/docs/gdscript-vs-csharp.md ;
  demo youtu.be/eUz19GROIpY ; Show HN https://news.ycombinator.com/item?id=47400868 ;
  analysis https://chyshkala.com/blog/godogen-s-four-rewrites-reveal-the-hidden-cost-of-teaching-ai-to-code-games
- Randroids-Dojo/Godot-Claude-Skills — https://github.com/Randroids-Dojo/Godot-Claude-Skills (API: 35★/2f, MIT, pushed 2026-01-19, stale)
- Coding-Solo/godot-mcp — https://github.com/Coding-Solo/godot-mcp (~4.7k★, MIT; scene tools: create_scene/add_node/load_sprite/save_scene)
- RodZill4/godot-procedural3d — https://github.com/RodZill4/godot-procedural3d (77★/13f, MIT, GDScript; modular rooms+objects w/ connectors, algorithmic)
- Adriankhl/godot-llm — https://github.com/Adriankhl/godot-llm (248★, MIT, v1.0 2024; RAG over lore text)
- Godot AI Assistant (champ-gaming) — https://store.godotengine.org/asset/champ-gaming/godot-ai-assistant/ (LLaMA-3.3-70B; .tscn from text + sanitizer) ;
  AI Autonomous Agent — https://godotengine.org/asset-library/asset/4583
- gamedev-skills/awesome-gamedev-agent-skills — https://github.com/gamedev-skills/awesome-gamedev-agent-skills (264★, 66 SKILL.md + router)
- TSCN format (format=3, uid) — https://docs.godotengine.org/en/stable/engine_details/file_formats/tscn.html ;
  LLM-friendliness commentary — https://dev.classmethod.jp/en/articles/godot-text-based-development-with-llm/
- Godot 4.8 dev1 — https://godotengine.org/article/dev-snapshot-godot-4-8-dev-1/ ;
  4.7 — https://godotengine.org/releases/4.7/ ;
  Jolt (3D-only, determinism) — https://docs.godotengine.org/en/stable/tutorials/physics/using_jolt_physics.html ,
  https://github.com/godot-jolt/godot-jolt/discussions/548
- Local lens: `harness/gen/retrieval.py`, `harness/gen/prompts/bank_menu.md.tmpl`,
  `banks/parts/v1/parts.json`, `CONTRACTS.md §9`, `notes/engines/GODOT_MIGRATION.md`, `notes/engines/youtube_scan.md`
