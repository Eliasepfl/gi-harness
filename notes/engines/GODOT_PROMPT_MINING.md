# Godot prompt-mining — can we reuse their prompts?

> **prompt-mining pass, Fable orchestrator + Opus agents, 2026-07-14.** Answers Elias's follow-up
> to the tooling audit (`GODOT_AI_TOOLING_AUDIT.md` §2/§3-B1): *can we reuse their prompts?* Mined
> the two prose-bearing clones — **godogen** (`~/orcd/scratch/gi/tool-audit/godogen`, MIT) and
> **awesome-gamedev-skills** (`~/orcd/scratch/gi/tool-audit/awesome-gamedev-skills`, Apache-2.0) —
> for text that seeds a future `api_godot.md`, the verify-hint repair DB, and the offline bank-CI
> `build_part.gd` guide. **Track A owns `harness/gen/prompts/` right now — this note does NOT
> touch it; blocks below are staged prose to hand over, not edits.** Nothing run; static read only.

---

## 1. VERDICT — what is reusable, from whom

Yes, at the edges — **prose and structure, never files**. From **awesome-gamedev-skills**
(Apache-2.0, version-pinned Godot 4.3+, audited zero-hallucination): the bulk of a `## Physics
guidance` block for `api_godot.md` (body/verb/joint mapping, mass intuition, speed priors,
sensor+grounded rules) and a signature→fix pitfalls corpus — the higher-value, more directly
transferable haul. From **godogen** (MIT, a 3D/C#/pixel code-gen system, so after the filter only
three designer facts survive): primitives-over-poly, thick-solid supports, exp-decay determinism —
plus its one genuinely reusable engineering technique, the build-time serialize + **node-count
pre-save gate** for the bank-CI. Both are paraphrase (we take ideas, copy no files), so no notice
is strictly compelled — but any file lifting their wording MUST carry the credit line. Apache-2.0
§4(d) makes the NOTICE reproduction mandatory once we ship awesome-gamedev prose, so bake both in:

```
<!-- Portions paraphrased from awesome-gamedev-agent-skills (Copyright 2026 Abhishek Barali
     and the awesome-gamedev-agent-skills contributors), Apache-2.0. "Godot" is used
     descriptively; no affiliation. -->
<!-- Portions adapted from godogen (MIT License), Copyright 2026 Alex Ermolov. -->
```

Do NOT `npx skills add` / marketplace-install — hand-copy the paraphrase only (audit §5).

---

## 2. READY-TO-MERGE BLOCK for `api_godot.md`

Drop-in, in `harness/gen/prompts` voice (terse imperative, SPEC-anchored: mass=1.0, friction=0.7,
elasticity=0.3, gravity (0,-900), 800×600 y-up, fixed 60 Hz). Hand to Track A with the header
comment above.

```markdown
## Physics guidance (mined)
- BODY TYPE follows the SPEC flag. `static:true` → StaticBody2D: collides, never simulated;
  gravity/forces will NOT move it — use for perimeter walls, floors, ramps, fixed obstacles.
  default (dynamic) → RigidBody2D: drive it ONLY with impulse/force/set_velocity — NEVER by
  writing `pos`; setting position on a simulated body fights the solver (jitter/tunnelling).
  `sensor:true` → Area2D: overlap detection, NO solid collision — goals, triggers, checkpoints,
  kill/hurt zones, and force/gravity regions.
- DRIVE VERBS: `impulse` = instantaneous Δv at the center of mass (an OFF-center impulse adds
  spin). `force` = continuous per-step push — re-apply it every step in on_step, never once in
  build. `set_velocity` overwrites linear velocity — use sparingly; prefer impulse so the solver
  stays consistent.
- MASS is ONLY relative weight in collision response; it does NOT change fall speed (gravity
  accelerates every mass equally). Do not raise mass to fall faster — snappier/floatier feel comes
  from gravity_scale + damping. Keep mass RATIOS between stacked/jointed/interacting bodies modest
  (1..~10); extreme ratios make stacks explode and joints jitter. elasticity=bounce[0,1],
  friction=grip — set both per-body in the SPEC, don't expect code to fake a bounce.
- SPEED PRIORS (800×600, all under the ~600 px/s cap): run ~200-220, jump kick ~-400,
  accel ~1500, decel/friction ~1800 px/s; a movement gravity of ~1200 (SPEC default 900 is
  gentler). Size impulses so peak velocity lands in this band.
- WHY THE SPEED CAP: at 60 Hz a body at V px/s advances V/60 px per step; that must stay under the
  thinnest solid wall. With walls ≥12 px, speeds over ~600 px/s skip clean through in ONE step.
  The clamp lives in on_step (fixed tick), not act().
- COLLISION SHAPES: reach for `box`/`circle` before `poly`. Keep every `poly` convex and
  low-vertex — a concave or many-vertex poly is silently mis-solved, destabilises contacts, and
  tunnels. Reserve `poly` for genuinely angular bodies (ramps, wedges).
- grounded()/contacts() need SOLID THICK SUPPORTS. A body counts as supported only when a solid,
  non-sensor floor ≥12 px thick sits directly beneath it. A `segment` floor or a `sensor` zone does
  NOT register — the support contact flickers out and a `"when":"grounded(...)"` gate misfires.
- JOINTS: `pin`→PinJoint2D (rigid link); `pivot(a,b,point)`→PinJoint2D at a world anchor
  (pendulums, swinging platforms, levers); `spring`→DampedSpringJoint2D (rest length + stiffness +
  damping). Every joint MUST connect TWO bodies with a resolvable anchor or it does nothing.
- CHEAP RICH HAZARDS: a `sensor` region can override gravity/damping inside it — low-grav pocket,
  updraft, wind, water — affecting any dynamic body inside, no per-body logic. A one-way collision
  lets bodies pass through from a single side (jump-through platforms).
- DETERMINISM: all per-step logic — moving hazards, timers on `world.steps`, scoring, the velocity
  clamp — belongs in on_step on the fixed 60 Hz tick. Model any decay multiplicatively
  (`v *= exp(-rate*dt)`), NEVER per-tick `v *= (1-k)` (substep-count-dependent, breaks replay).
```

---

## 3. REPAIR-LOOP QUIRKS TABLE (verify-hint entries)

Signature→hint, keyed to our G0-G3 / bank checks. Terse, one runtime fix each — the shape the
verify-hint mechanism wants.

| signature | hint |
|---|---|
| **G3 SOLIDITY reject / body half inside a crate** | Solver overwhelmed: cut impulse magnitude (size to mass, peak <~600 px/s), keep the on_step clamp, keep mass ratios modest, never move the controlled body by writing pos. |
| **G0 initial interpenetration / bodies overlap at rest** | Prefer primitive box/circle over poly; give solid bodies real thickness; keep every poly convex+low-vertex (concave mis-solves and tunnels). |
| **CONTAINMENT escape / body crosses a wall in one tick** | Tunnelling. Clamp velocity in on_step so speed/60 < thinnest wall; thicken solid walls ≥12 px; (bank-side) enable CCD on the fast body; raise physics rate last. |
| **grounded-gated action never fires / grounded() stays false on a resting body** | The support must be a SOLID non-sensor body ≥12 px thick directly under it — a `segment` or `sensor` floor never registers. Replace with a static box. |
| **goal/trigger predicate never true although bodies overlap** | Detection gap: the goal body must be a `sensor`, not accidentally solid; sensor fires only when layers/masks overlap (one-directional) and a simulated body needs contact_monitor + max_contacts>0. |
| **controlled body ignores impulses / won't move / drifts** | Don't move a RigidBody by writing pos; check it isn't frozen and gravity_scale isn't 0 by accident; drive via impulse/force. |
| **joint has no effect / swinging platform falls apart** | A joint does nothing unless BOTH bodies are assigned and its anchor resolves inside the world. Verify both endpoints exist. |
| **G1 non-determinism localized to a body that bleeds off speed** | Express decay as `v *= exp(-rate*dt)`, never per-tick `v *= (1-k)`; the linear form is substep-count-dependent and defeats byte-identical replay. |
| **jittery / frame-rate-dependent motion in replay** | Per-step logic (hazards, timers, clamp) must run in on_step on the physics tick, not per render frame; don't multiply an already-per-step velocity by delta. |

**Method (whole DB):** keep ONE short "silent-failure" section — entries scoped strictly to
things that pass a load/compile but fail at runtime — and prune as the model improves (godogen's
quirks doc shrank 100→79 lines; `gdscript-vs-csharp.md:53`).

---

## 4. BANK-CI AUTHORING NOTES (future `build_part.gd` guide)

godogen's build-time scene generation maps ~1:1 onto our OFFLINE .tscn template author; bank-CI is
allowed a headless Godot (it's not the frozen runtime). Corroborated by primary Godot docs in
awesome-gamedev (`tree-and-instancing.md`, `godot-resources`, `godot-export`).

- **Emit .tscn programmatically, never hand-edit.** A headless SceneTree script builds the
  hierarchy, sets properties, attaches the frozen runner script LAST, packs, saves, quits — no
  runtime logic in the builder (no `_ready`/`_process`/signals). Build LEAF scenes first.
- **Owner chain (silent trap #1):** after building, set `child.owner = root` on EVERY descendant
  or the node is silently dropped from the saved .tscn. Do NOT recurse into instantiated
  sub-scenes (`SceneFilePath` non-empty) — that inlines them as text and bloats to 100MB+.
- **Node-count parity gate (highest-leverage lesson):** count nodes → `PackedScene.pack(root)` →
  `Instantiate()` → recount → refuse `ResourceSaver.save` unless the two counts match AND every
  Owner is the root. A silent drop otherwise looks like a clean save. Make this CI-mandatory.
- **Shapes as external `.tres`** by res:// (headless can't cleanly build inline sub-resources).
  A `.tres` is shared BY REFERENCE — `duplicate(true)` before any per-body mutation or you mutate
  every body. A custom Resource needs `class_name` to instance from tooling; res:// is read-only
  in a packed context, so author to res:// only in CI, write runtime data to user://.
- **Headless invocation:** `godot --headless --path <proj> --import` FIRST (rebuild the gitignored
  cache) THEN `--export-pack "<preset>" <out.pck>`; preset name is case/space-sensitive; check the
  non-zero exit; `--quit-after N` counts FRAMES; pass op+json via `-- <args>` →
  `OS.get_cmdline_user_args()`. Keep `.gdignore` OUT of any template dir or it's silently skipped.
- **Defer mid-step tree mutation** (`add_child.call_deferred`) or you hit "flushing queries".
  DROP godogen's C# `SetScript()`-disposal caveat — GDScript-irrelevant; keep only "scripts last".

---

## 5. PATTERNS ADOPTED (prompt-engineering structure)

- **Curated one-pager:** carry only what the model can't infer — non-obvious, failure-preventing text; let the gate funnel surface the rest ("don't give obvious guidance").
- **Silent-failure quirks DB as a distinct terse section:** symptom→cause→one-line fix, scoped to load-passing/runtime-failing only — exactly our repair-loop shape.
- **Orchestration/execution split, one-source-many-targets:** one shared `rules.md`/`contract.md` core rendered per lane via token substitution, not divergent `api_*` copies.
- **Proof over claims:** a spec that merely loads is NOT verified — verification is the G0-G3 funnel + a replayable witness; trust the gate message over assumptions.
- **Progressive stripping + version pin:** measure each guide by what breaks without it and prune as the model improves; pin one tight engine version atop the guide; keep every claim anchored to a named SPEC verb + Godot mechanism so it stays checkable.

---

## 6. NOT TAKEN

- godogen's 3D/C#/editor/pixel-QA corpus — off-lane noise (trimesh, `SetScript` disposal, Tripo asset-gen).
- Its frame-rate-independent-damping verb — LOW priority until the DSL grows a decay/damp verb; folded into §2 determinism only.
- awesome-gamedev's 6 NOISE skills (3d-essentials, csharp, shaders, ui-control, audio, multiplayer) — grep-confirmed no 2D-physics content.
- Kinematic-controller / move_and_slide / tilemap idioms — belong to a code lane, not our declarative bodies/joints SPEC.
- Any file copy, `npx skills add`, marketplace install — hand-copied paraphrase only.
- Editing `harness/gen/prompts/` — Track A owns it; these blocks are staged for handover.
