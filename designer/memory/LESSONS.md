# Designer memory — distilled lessons (append-only delta, ACE discipline)

- [2026-07-15, pilot iter-1, ledger: ~/orcd/scratch/gi/pilot/ledger.jsonl]
  steer-archetype: the first milestone must be reachable by near-straight
  thrust within a few seconds of spawn; later gates one short curve apart.
  Recurrent signature: UNSOLVED "no episode reached the first milestone"
  on 3/5 attempts of the cone-slalom pilot (plus one stuck-between-gates).
  Steering exploration is far harder for the tree solver than hop/impulse
  play. Surface applied: rules_godot.md (deepseek) + this memory (agent).

- [2026-07-15, pilot iter-2, ledger: ~/orcd/scratch/gi/pilot/ledger.jsonl]
  steer-archetype: chain LENGTH is the certification killer — every attempt
  now advances gate-by-gate but stalls one leg short (best: gate4/4 with
  60/96 episodes, failing only the park). Cap steering chains at 2-3 gates;
  final leg = short straight glide into the zone. Signature: UNSOLVED
  "stuck between <gateN> and <gateN+1>/success" on 5/5 attempts.

- [2026-07-15, CORRECTION on iter-2 lesson] Elias's challenge accepted: the
  "short chains" lesson was never tested independently of the 3x budget
  raise and is DEMOTED to hypothesis; reverted from rules_godot.md. Iter-3
  = budget-only vs the iter-2 baseline to isolate the variable. Lesson 1
  (near-straight first milestone) keeps its evidence: it was validated at
  the OLD 21k budget (start-failures 3/5 -> 0/5 on prompt change alone).

- [2026-07-15, pilot iter-3 OUTCOME] Budget-only experiment CONFIRMED
  Elias's hypothesis: at 63k ticks the same prompt certified (COMPLETED,
  4 attempts) with the iter-2 prompt unchanged. The binding constraint was
  solver budget, not chain length. "Short chains" hypothesis stays demoted.
- [2026-07-15, pilot iter-3, G4] Hard finding on the certified game:
  gates listed as checkpoints but NOT required by success - an avoidance
  probe reached the bay skipping all gates. Designer lesson: every "must
  pass through X" in the fiction needs X's flag ANDed into success, not
  just a checkpoint. Surface: revise directive (iter-4); candidate for
  rules_godot.md after one more occurrence (recurrence rule).
