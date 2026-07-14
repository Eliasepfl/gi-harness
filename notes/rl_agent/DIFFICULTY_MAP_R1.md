# Difficulty map — round 1 (14 juil., G3' @500k steps, PPO CPU)

First full G3'-profiling pass over the six hardened showcase games
(`harness.gen.curriculum.difficulty_profile`, budget 500k env-steps each,
verify = tree solver). Profile JSONs archived per game during the run; the
grades and stall points below are the durable signal.

| game | grade | stochastic sr | stalls at | last mastered | wall |
|---|---|---|---|---|---|
| boulder_run | **easy** | 1.000 | delivered (end) | at_blockade | 72.5s |
| demolition_yard | **easy** | 0.969 | at_goal (end) | crossed_pit | 40.5s |
| gem_cavern | **target** | 0.656 | at_exit | got_gem4 | 46.5s |
| meteor_gauntlet | **hard** | 0.062 | survived_two_thirds | survived_third | 42.7s |
| two_switch_vault | **hard** | 0.0 (500k run) | cleared_gap1 | switch_a | (round-1 run) |
| flood_tower | **not_learnable** | 0.0 | on_l2 | on_l1 | 27.3s |

## Readings

- **The metric SPREADS the set** — {easy x2, target, hard x2, not_learnable}
  across six games that all look "certified and hardened" to G0-G4. This is
  the curriculum signal working: G4 robustness and RL difficulty are
  different axes (boulder_run survived every adversary yet a PPO masters it
  outright).
- **Budget sensitivity is real**: meteor_gauntlet grades hard at 500k but was
  learnable at 1.2M in the spike (greedy 1.0 / sr 0.62). Grades are
  budget-relative by design (screening philosophy, ORCD plan §strategy);
  campaign-grade decisions should quote the budget with the grade.
- **flood_tower not_learnable at on_l2**: the no-overhang zig-zag of precise
  hops (already the random-solver's hardest case, 128 tree replays) gives PPO
  no gradient — milestones too far apart in action-space. Candidate fixes via
  directive: intermediate ledges (denser milestones) or coarser hop physics.
- **gem_cavern sr identical at 500k and 1.2M (0.656)** — a stable target-grade
  anchor for threshold calibration.

## Routing consequence (model policy, Elias 14 juil.)

Grade-based routing: easy -> hy3 variants/volume; target -> ship untouched;
hard -> revision (revise mode; strong model if hy3 fails); not_learnable ->
ease directive or redesign. First live rounds: hy3 from-scratch regen failed
5/5 on the vault (see CURRICULUM_LOOP.md); revise-mode live round in flight.
