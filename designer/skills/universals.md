---
id: universals
kind: reference
created_by: human-seed wave-1
run_id: reseed-2026-07-14
wave: 1
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
load_when: Concept phase — pair with every archetype card; the 8 non-negotiables hold for every family
rationale: The 8 ingredients present in essentially every good RL game, phrased as design mandates for our JSON spec. These are what make a spec a GAME rather than a physics demo; the archetype cards specialise them, they never override them.
provenance: notes/engines/EXAMPLES_STRUCTURE_GUIDE.md §1 (the universals) + §5 (distillation), forensics over the 20 godot_rl_agents examples; godotworld/SPEC.md §3/§6/§8.
---

# The 8 universals — non-negotiable ingredients

Present in essentially every example. An archetype card may emphasise some over
others, but none of these may be dropped. Design specifics (sizes, counts,
positions) are the model's job — these are the shape, never the numbers.

1. **One controlled body, small action set.** Exactly one dynamic body has
   `control: true`; the action set is 1-3 dims (2 continuous is the mode:
   move/steer, or `thrust`+`torque`). Single-agent is the rule — a second
   controlled body is inexpressible today.

2. **Layered reward, three parts.** A big terminal success (often scaled DOWN by
   leftover error — residual distance, time, or speed) + dense potential-shaping
   that pays ONLY when a distance/velocity/alignment metric strictly improves + a
   small per-step time penalty + a fixed negative on failure/timeout. Never a lone
   terminal bonus.

3. **Success is a COMPOUND latch, never "reach a zone."** Winning requires POSE
   AND STILLNESS AND state together: be there AND aligned AND slow AND
   (engines-off / all-collected / correct-label). A bare zone touch is a
   single-action win and G1/G4 will punish it.

4. **Per-reset domain randomization.** Every episode re-rolls spawn pose, goal
   location, and scene contents (which bay is free, goal colour, hazard placement)
   so the policy generalises instead of memorising. Randomness comes only from the
   seeded `rng` — never wall-clock, never `randi`.

5. **Egocentric, normalized, time-aware observations.** Obs live in the agent's
   own frame, clamp to ~[-1,1], include normalized time (steps / limit), express
   the goal RELATIVE to the agent, and append the spatial sensor's read LAST.

6. **The SENSOR is the game's identity.** Each game is a harness for exactly one
   observation abstraction (our only sensor is the raycast fan). Pick the obs
   first; build the world so that obs is decisive.

7. **Bounded arena, breach = negative terminal.** Invisible walls or an `Area`
   bound; leaving ends the episode with a penalty (G1's escape check). Size the
   arena so the goal is reachable but the edge is a real threat.

8. **Asset-light, dressing-driven look.** Primitives/sprites wearing a coordinated
   palette under simple lighting; polish is palette + skinned sprites + textured
   ground + decor, NOT code. (See `dressing.md` — but only in the Polish phase.)

## The two distillation rules the whole library obeys

- **Pick a distinct differentiator family per game** and make a batch's families
  differ. This is the single strongest anti-sameness lever.
- **Flip the objective when reconstructing** a known shape (park → exit, race →
  errand, defend → survive) — keep the essence, change the goal.
