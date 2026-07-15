---
id: dressing
kind: reference
created_by: human-seed wave-1
run_id: reseed-2026-07-14
wave: 1
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
load_when: Polish phase ONLY — the spec already certifies (G0-G3 green) and looks samey; never before certification
rationale: The cosmetic-DATA layer that breaks visual sameness — palette, parallax, decor, skinnable names, juice — all off-by-default and never executed, so a spec renders byte-identically without it. Kept strictly separate from mechanics so the moat is untouched.
provenance: notes/engines/GODOT_QUALITY_BAR.md §2 (quality bar) + §5 (vocabulary buckets A1-A8); notes/engines/EXAMPLES_STRUCTURE_GUIDE.md §5 guideline 11; hi-godot draw-recipe pattern; godotengine docs, paraphrased.
---

# Dressing — cosmetic DATA, after it certifies

**Precondition: the spec already passes G0-G3.** Everything here is cosmetic DATA
the verifier never runs — off-by-default, never affecting physics/predicates/obs, so
a spec without it renders byte-identically. Dressing cannot fix a broken game; it
makes a working one not look like every other working one.

## The distinctiveness bar (what "not samey" means)

Next to the previous game, a stranger should call them **different games.** Concretely:

- **Coordinated palette actually applied** — a 3-6 colour palette (bg / ground /
  player / accent), themed to the biome. This alone kills most of the sameness; it
  replaces the one hardcoded grey grid.
- **Every actor is a skinned sprite — including the controlled body.** Name each body
  from the skinnable vocabulary and theme it with a suffix the skinner strips
  (`gem_ruby` → gem, `ledge_ice` → ledge). The controlled body has NO default sprite,
  so name it after a prop it resembles (`ball` / `crate` / `marble`) or it renders
  flat.
- **Ground reads as ground** — textured or bright top-rim, not a grey box.
- **Depth behind the world** — 2-4 parallax background layers, each with its own
  scroll speed (slow far, fast near).
- **2-5 non-interactive decor** — dressing bodies that are never simulated and never
  obstacles. Dress every region, generously.

## Juice — reads off state we already track (no new mechanics)

Tie visual feedback to events already in the sim: squash/stretch on a
grounded-transition or jump, a motion-trail on the controlled body, a particle burst
on a fresh contact / `remove_when` / checkpoint latch, camera look-ahead with a
RESTRAINED impact shake, a floating fading milestone label. Restraint is the rule —
if every action rattles the camera, the big moment has nothing left to say.

## Hard boundary (why this stays cosmetic)

- Cosmetic fields are DATA that SELECT fixed renderer behavior — never code, never
  executed. A fixed `kind` maps to a fixed draw call; no spec string ever runs.
- No dressing key touches `world.step`, a predicate, or the obs. Determinism and
  witness replay stay byte-exact; a spec stripped of every cosmetic key certifies
  identically.
- Keep dressing OUT of the mechanics phases. If you're reaching for a palette while a
  predicate still fails, you routed wrong — go back to `certification.md`.

## Variety hand-off

Dressing is also a variety lever: in a batch, the palette and biome must differ from
the previous games (the cross-game mandate on the HUB). A distinct look on top of a
distinct archetype is what makes the batch read as a set of different games.
