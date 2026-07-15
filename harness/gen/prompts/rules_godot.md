# Hard constraints (a spec that breaks these is rejected)

These are the moat the verifier replays your winning run against (G0-G4). None is about how the game LOOKS - break one and the whole environment is thrown out. State everything as DATA in the one JSON object; there is no code to lean on.

- DETERMINISTIC DATA ONLY: you emit ONE JSON object and nothing executable (the only expressions allowed are the whitelisted predicate DSL). There is no randomness source, so build variety into the LAYOUT itself - never a faked jitter or a hidden roll.
- Between 2 and 8 actions, and EVERY action must DO something. The runner has no built-in idle move, so a do-nothing action just wastes the move set and trips the agency check; there is no "wait" / "idle" / "noop" / "stay" move. If you want a "hold position" feel, make even that action apply a real force or velocity.
- EXACTLY one controlled body, and it must be DYNAMIC (never static) - that is the single body the solver drives through the act verbs.
- The success predicate MUST be false at t=0 and stay pure: a read-only expression over engine state with no side effects.
- Checkpoints are the same 1..6 snake_case keys on every read, all false at t=0, pure, and every milestone reachable on the way to success. Aim for 4-6 that mark genuinely distinct stages spread across the run - not four ways to say "moved a bit".
- Player agency is mandatory: doing nothing, or repeating one action forever, must NEVER win (the adversarial suite tries the noop and each single action alone). Force a reversal, a timed stop, or a distinct second action mid-level.
- SOLIDITY: the verifier replays the winning run and rejects the spec if solid bodies interpenetrate deeply for multiple ticks (a body half inside a wall = broken game). Keep gameplay speeds moderate (roughly under ~600 px/s; size impulses to mass), and never use paper-thin (<12 px) solid bodies as walls.
- CONTAINMENT: no body may EVER leave the world under ANY input sequence - the adversarial suite hurls every body around with hostile inputs, and a single escape rejects the environment. Close the play space with static perimeter walls (and a ceiling if anything can be launched upward), and make falling into any open gap a failure kill-plane, never a silent exit.
- SPEED CAP: clamp the controlled body's velocity in on_step (a velocity_clamp). Impulses stack across ticks - hostile spam builds thousands of px/s and crosses any wall or kill-plane within one tick, breaking containment. The clamp is two fields and makes the whole game robust.
- Body budget is a SOFT target, not enforced: up to ~16 gameplay bodies plus up to ~16 decor/dressing bodies. Keep every body inside YOUR declared world at rest, and avoid initial overlaps.
- Steering/vehicle games (thrust+turn control): exploration is far harder than
  hopping - a random searcher rarely produces coherent driving. Place the FIRST
  checkpoint within a few seconds of near-straight thrust from spawn, and each
  later gate one short curve from the previous one. Long or twisty first legs
  make the game unsolvable in search budget even when a human could drive it.
  [lesson: pilot-2026-07-15, 3/5 attempts died at the first milestone]
- Steering chains must be SHORT: at most 2-3 gates between spawn and goal, and
  the final approach into any parking/stop zone must be a short, near-straight
  glide. Every extra gate multiplies the search cost; a 4-gate chain that a
  human drives easily is beyond the certification search budget.
  [lesson: pilot-2026-07-15 iter-2, 5/5 attempts stuck between consecutive gates]
