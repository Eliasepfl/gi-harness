---
id: certification
kind: reference
created_by: human-seed wave-1
run_id: reseed-2026-07-14
wave: 1
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
load_when: Predicates phase — when writing success/failure/checkpoints and choosing a goal shape; NOT during Concept
rationale: What the G0-G4 gates punish, phrased as design guidance so a spec is born certifiable. Understanding the gates as design pressure (compound goals, real agency, no shortcuts) rather than a checklist keeps the designer from narrowing to an easily-gamed band.
provenance: godotworld/SPEC.md §9 (G0-G3 funnel); harness/verify/gameverify.py (agency/escape/efficacy/dead-milestone checks) and harness/verify/g4.py (attacker: unintended_success, shortcut, softlock, avoidance). Thresholds are the verifier's, never a spec parameter.
---

# Certification — design so the gates pass

The gates are held-out judges, not authors. They punish degenerate design; a good
game satisfies them by construction. Design FOR the gate's intent, never to a
number (the number is the verifier's, never yours).

## G0 — well-formedness

Punishes malformed structure. Guarantee: required sections present; every body has a
valid shape; **exactly one** dynamic body with `control: true`; **at least two**
bodies; nothing interpenetrating at spawn (analytic AABB); every dynamic body inside
`world_size`. Design guidance: place bodies with clearance; use `inspect_world`
(`world-composition.md`) to catch spawn overlaps and out-of-bounds before you certify.

## G1 — agency, safety, determinism

Punishes a world that plays itself. Under a NOOP rollout the gate checks:
- **No NaN / explosion** — no runaway forces; keep masses and impulses sane.
- **No escape** — a body leaving the bound + margin fails; bound the arena.
- **AGENCY: no success under noop.** If doing nothing wins, the goal is degenerate.
  This is the NO-SINGLE-ACTION-WIN law — the deepest design pressure. The goal must
  require the agent to ACT.
- **Determinism** — two seeded runs must be byte-identical; randomness only from
  `rng`.
- **Per-action efficacy (dead-action check)** — every action in `act` must change
  the world. Never declare an action bound to `[]` or to a no-op verb.

## G2 — predicate hygiene

Punishes ill-typed goals. `success`/`failure` must be pure bools, **False at t=0**;
`checkpoints` is 1-6 `snake_case` bools, each False at t=0, ordered by intended
progression. A goal true at t=0 is dead on arrival. (Grammar rules live in
`engine-truths.md`.)

## G3 — solvability & honest progression

Punishes goals that can't be reached or that skip their own milestones. A Go-Explore
solver must find a REPLAYABLE witness, and on the winning path:
- **Not trivially fast** — a win under ~5 ticks is degenerate; make the goal earn
  several decisions.
- **No dead milestones** — every checkpoint MUST latch somewhere on the path. If a
  checkpoint never fires, it's mis-wired or unreachable. Order checkpoints so a real
  playthrough hits them in sequence.
- **Solid & replayable** — the witness replays byte-exact; no ghosting through walls.

## G4 — the attacker (adversarial hardening)

Punishes exploits. The attacker actively tries to break the goal and reports:
- **Unintended success / degenerate goal** — if the goal is reachable WITHOUT
  playing (unavoidable, or true from a trivial state), HARD fail. Compound latches
  (pose AND stillness AND state) are the defence.
- **Shortcut** — a win far faster than the intended witness means an unintended path;
  close it with the compound goal, not with more walls.
- **Softlock** — a reachable state from which the goal can no longer be reached is a
  finding. Avoid one-way traps (a removed body you later need contained; a hazard
  that strands the agent).
- **Avoidance** — when the attacker tries NOT to win and still does, the goal is
  unavoidable — degenerate again.

## The one habit that satisfies all five

Make success a **compound latch the agent must actively hold** — pose AND stillness
AND state — reachable in several decisions, with each checkpoint on the honest path.
That single shape clears agency (G1), triviality and dead-milestones (G3), and the
degenerate/shortcut/avoidance families (G4) at once.
