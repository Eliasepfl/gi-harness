"""Reward-invariant unit tests for the POTENTIAL-BASED G3' reward (harness/rl/env.py).

Checkpoint shaping is potential-based (Ng, Harada & Russell 1999): F = γ·Φ(s') − Φ(s) with
Φ(s) = SHAPING_MASS·(latched/n_cp). The terminal decayed success bonus and the failure penalty
live OUTSIDE Φ. These pure tests pin:

  * the PBRS TELESCOPING identity: Σ_t γ^t F_t = γ^T·Φ(end) − Φ(start), and its boundedness —
    which is WHY shaping-farming and do-nothing basins are impossible by construction;
  * the four terminal-dominance invariants on realistic episodes:
    (a) the whole farmable shaping mass < the success payoff at ANY tick;
    (b) an earlier success yields a strictly greater episode return than a later one;
    (c) any success return > any no-success return;
    (d) for EQUAL progress, a failure return < a timeout (no-success) return.

No node / Godot / sb3, and NO monkeypatching — the reward is a pure function of
(c_before, c_after, n_cp, result, tick, horizon). The three env step() paths (PlanckEnv,
GodotServeEnv, GodotBatchVecEnv) all call the SAME step_reward, so pinning it here pins all three.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.rl import env as E  # noqa: E402

H = 300  # decision-tick horizon (env.HORIZON)


def _shaping_terms(latch_ticks, n_cp, absorbing, term_tick):
    """Yield (t, F_t, c_after) for step t=0..term_tick-1. ``latch_ticks`` = 1-based ticks at
    which a NEW checkpoint latches; ``absorbing`` = the final step ends in a success/failure
    terminal (Φ(s')=0) rather than a truncation (Φ(s') kept)."""
    latch_ticks = list(latch_ticks)
    c = 0
    for tick in range(1, term_tick + 1):
        c_before = c
        c += latch_ticks.count(tick)
        c_after = c
        is_term = absorbing and tick == term_tick
        yield tick - 1, E.shaping_reward(c_before, c_after, n_cp, is_term), c_after


def _episode_return(latch_ticks, n_cp, terminal, term_tick, horizon=H):
    """UNDISCOUNTED episode return (what the trainer logs as episodic return): the sum of
    ``step_reward`` over the episode. ``terminal`` in {"success","failure",None(=timeout)}."""
    latch_ticks = list(latch_ticks)
    total = 0.0
    c = 0
    for tick in range(1, term_tick + 1):
        c_before = c
        c += latch_ticks.count(tick)
        result = terminal if tick == term_tick else None
        total += E.step_reward(c_before, c, n_cp, result, tick, horizon)
    return total


# ====================================================================== #
# 0. Constant sizing + PBRS gamma matches the trainer
# ====================================================================== #
def test_constant_sizing_gives_terminal_dominance():
    # the MINIMUM success payoff (decay floor) strictly dominates the whole PBRS shaping mass
    # (max Φ = SHAPING_MASS) — this is what makes invariants (a) and (c) hold with margin.
    assert E.R_SUCCESS * E.SUCCESS_TIME_FLOOR > E.SHAPING_MASS
    assert 0.0 < E.SUCCESS_TIME_FLOOR < 1.0
    assert E.R_SUCCESS > 0.0
    assert E.R_FAILURE < 0.0
    assert E.SHAPING_MASS > 0.0
    assert 0.0 < E.PBRS_GAMMA < 1.0
    assert E.LIVING_COST_TOTAL >= 0.0    # the non-potential living cost is off by default


def test_pbrs_gamma_matches_trainer_gamma():
    # PBRS invariance is exact only when the shaping γ equals the trainer's discount.
    from harness.rl.ppo import DEFAULTS
    assert E.PBRS_GAMMA == pytest.approx(DEFAULTS["gamma"])


# ====================================================================== #
# 1. Potential Φ and single-step PBRS shaping
# ====================================================================== #
@pytest.mark.parametrize("n_cp", [1, 2, 3, 5, 8])
def test_potential_is_bounded_and_normalized(n_cp):
    assert E.potential(0, n_cp) == 0.0                       # start: nothing latched
    assert E.potential(n_cp, n_cp) == pytest.approx(E.SHAPING_MASS)   # all latched: max Φ
    for c in range(n_cp + 1):
        assert 0.0 <= E.potential(c, n_cp) <= E.SHAPING_MASS
        assert E.potential(c, n_cp) == pytest.approx(E.SHAPING_MASS * c / n_cp)


def test_potential_zero_when_no_checkpoints():
    assert E.potential(0, 0) == 0.0
    assert E.potential(1, 0) == 0.0        # n_cp<=0 -> no shaping (never divides by zero)


def test_shaping_reward_form():
    g = E.PBRS_GAMMA
    # latching a checkpoint (non-terminal): F = γ·Φ(c+1) − Φ(c)
    assert E.shaping_reward(0, 1, 2, False) == pytest.approx(g * E.potential(1, 2))
    assert E.shaping_reward(1, 2, 2, False) == pytest.approx(
        g * E.potential(2, 2) - E.potential(1, 2))
    # no new latch (rent): F = (γ−1)·Φ(c) <= 0
    assert E.shaping_reward(1, 1, 2, False) == pytest.approx((g - 1.0) * E.potential(1, 2))
    assert E.shaping_reward(1, 1, 2, False) <= 0.0
    # absorbing terminal: Φ(s')=0 so F = −Φ(c_before)
    assert E.shaping_reward(1, 2, 2, True) == pytest.approx(-E.potential(1, 2))
    assert E.shaping_reward(0, 0, 2, True) == 0.0


# ====================================================================== #
# 2. THE PBRS PROPERTY — telescoping + boundedness (Ng/Harada/Russell 1999)
# ====================================================================== #
@pytest.mark.parametrize("latch_ticks, n_cp, absorbing, T", [
    ([10, 30], 2, True, 30),        # win: latch both, absorbing terminal at the win tick
    ([5], 3, True, 40),             # partial progress then an absorbing failure
    ([10, 200], 2, False, 300),     # latch both, then a truncation (Φ kept)
    ([], 2, False, 300),            # never latch, truncation
    ([1, 2, 3], 3, False, 300),     # all latched early, truncation
    ([1, 2, 3], 3, True, 120),      # all latched early, absorbing terminal
])
def test_pbrs_shaping_telescopes_and_is_bounded(latch_ticks, n_cp, absorbing, T):
    g = E.PBRS_GAMMA
    total = 0.0
    c_final = 0
    for t, F, c_after in _shaping_terms(latch_ticks, n_cp, absorbing, T):
        total += (g ** t) * F
        c_final = c_after
    phi_start = E.potential(0, n_cp)                          # 0 (episode starts unlatched)
    phi_end = 0.0 if absorbing else E.potential(c_final, n_cp)
    expected = (g ** T) * phi_end - phi_start
    # THE telescoping identity: Σ γ^t F_t = γ^T·Φ(end) − Φ(start).
    assert total == pytest.approx(expected, abs=1e-9)
    # ... and bounded by the potential scale (why no shaping-farming can ever pay off).
    assert abs(total) <= E.SHAPING_MASS + 1e-9


def test_pbrs_full_success_episode_adds_zero_discounted_shaping():
    # a complete episode that starts unlatched and ends in an absorbing terminal accrues ZERO
    # net discounted shaping (γ^T·0 − 0) — PBRS changes the LEARNING signal, not the objective.
    g = E.PBRS_GAMMA
    total = sum((g ** t) * F for t, F, _ in _shaping_terms([10, 30], 2, True, 30))
    assert total == pytest.approx(0.0, abs=1e-9)


def test_pbrs_no_basin_do_nothing_and_camp_are_neutral():
    # The failure that a non-potential living cost caused was a do-nothing basin. Under PBRS the
    # do-nothing policy and the "camp on the first checkpoint" policy have (near-)equal, bounded
    # discounted shaping — neither is an attractor, so PPO cannot be lured off the goal.
    g = E.PBRS_GAMMA

    def disc_shaping(latch_ticks):
        return sum((g ** t) * F for t, F, _ in _shaping_terms(latch_ticks, 2, False, H))

    do_nothing = disc_shaping([])                 # never latch, run to truncation
    camp = disc_shaping([10])                     # latch got_first then dither to truncation
    assert do_nothing == pytest.approx(0.0, abs=1e-9)
    # camp's discounted shaping telescopes to γ^H·Φ(1) − 0 — vanishingly small, and bounded.
    assert camp == pytest.approx((g ** H) * E.potential(1, 2), abs=1e-9)
    assert abs(camp) <= E.SHAPING_MASS


# ====================================================================== #
# 3. Terminal payoff decay, living cost, step composition
# ====================================================================== #
def test_success_payoff_decays_with_floor():
    assert E.success_payoff(0, H) == pytest.approx(E.R_SUCCESS)
    assert E.success_payoff(H, H) == pytest.approx(E.R_SUCCESS * E.SUCCESS_TIME_FLOOR)
    assert E.success_payoff(H + 50, H) == pytest.approx(E.R_SUCCESS * E.SUCCESS_TIME_FLOOR)
    assert E.success_payoff(-5, H) == pytest.approx(E.R_SUCCESS)
    payoffs = [E.success_payoff(t, H) for t in range(0, H + 1)]
    assert all(a > b for a, b in zip(payoffs, payoffs[1:]))   # strictly decreasing


def test_tick_cost_default_off():
    assert E.tick_cost(H) <= 0.0
    assert E.tick_cost(H) * H == pytest.approx(-E.LIVING_COST_TOTAL)
    assert E.tick_cost(H) == pytest.approx(-E.LIVING_COST_TOTAL / H)


def test_step_reward_composition():
    # non-terminal: PBRS shaping + living cost
    assert E.step_reward(0, 1, 2, None, 10, H) == pytest.approx(
        E.shaping_reward(0, 1, 2, False) + E.tick_cost(H))
    # success: absorbing PBRS shaping + living cost + decayed terminal bonus
    assert E.step_reward(1, 2, 2, "success", 10, H) == pytest.approx(
        E.shaping_reward(1, 2, 2, True) + E.tick_cost(H) + E.success_payoff(10, H))
    # failure/error: absorbing PBRS shaping + living cost + flat negative terminal
    assert E.step_reward(1, 1, 2, "failure", 10, H) == pytest.approx(
        E.shaping_reward(1, 1, 2, True) + E.tick_cost(H) + E.R_FAILURE)
    assert E.step_reward(1, 1, 2, "error", 10, H) == E.step_reward(1, 1, 2, "failure", 10, H)
    # the shaping γ passes through
    assert E.step_reward(0, 1, 2, None, 10, H, gamma=0.5) == pytest.approx(
        E.shaping_reward(0, 1, 2, False, 0.5) + E.tick_cost(H))


# ====================================================================== #
# (a) whole farmable shaping mass < success payoff at ANY tick
# ====================================================================== #
@pytest.mark.parametrize("n_cp", [1, 2, 3, 5, 8])
def test_invariant_a_shaping_mass_below_success_payoff(n_cp):
    assert E.potential(n_cp, n_cp) == pytest.approx(E.SHAPING_MASS)   # the whole shaping mass
    for tick in range(0, H + 1):
        assert E.SHAPING_MASS < E.success_payoff(tick, H)


# ====================================================================== #
# (b) earlier success > later success (strictly)
# ====================================================================== #
def test_invariant_b_earlier_win_yields_greater_return():
    def win_at(t):
        return _episode_return([10, t], 2, "success", t)      # got_first@10, got_both+win@t
    assert win_at(30) > win_at(100) > win_at(299)


# ====================================================================== #
# (c) any success return > any no-success return
# ====================================================================== #
@pytest.mark.parametrize("n_cp", [1, 2, 3])
def test_invariant_c_success_beats_no_success(n_cp):
    # WORST success: win at the buzzer (max rent, min decayed payoff), got_first early.
    worst_win = _episode_return([10, H], n_cp, "success", H)
    bare_win = _episode_return([], n_cp, "success", H)         # a win with no shaping at all
    # BEST no-success: latch EVERY checkpoint at the last (truncation) tick — the most shaping
    # mass a non-winning episode can show.
    best_timeout = _episode_return([H] * n_cp, n_cp, None, H)
    farmed_fail = _episode_return([10, H], n_cp, "failure", H)
    assert worst_win > best_timeout
    assert bare_win > best_timeout
    assert worst_win > farmed_fail


# ====================================================================== #
# (d) for equal progress, failure < timeout (no-success)
# ====================================================================== #
@pytest.mark.parametrize("n_latched", [0, 1, 2])
@pytest.mark.parametrize("fail_tick", [1, 50, 150, 299])
def test_invariant_d_failure_below_timeout_at_equal_progress(n_latched, fail_tick):
    n_cp = 2
    latch = list(range(1, n_latched + 1))                     # same shaping prefix for both
    fail_ret = _episode_return(latch, n_cp, "failure", fail_tick)
    timeout_ret = _episode_return(latch, n_cp, None, H)
    assert fail_ret < timeout_ret


# ====================================================================== #
# The DIAGNOSIS, encoded: on mini_collect (n_cp=2) FINISHING crushes farming — the terminal
# payoff dominates the whole (bounded) PBRS shaping mass.
# ====================================================================== #
def test_mini_collect_finishing_dominates_farming():
    n_cp = 2                                                  # got_first, got_both
    farm = _episode_return([10], n_cp, None, H)               # camp got_first, never win
    win = _episode_return([10, 30], n_cp, "success", 30)      # got_first@10, got_both+win@30
    best_farm = _episode_return([H, H], n_cp, None, H)        # even farming BOTH at truncation
    assert win > farm + 5.0, "finishing must crush single-checkpoint camping"
    assert win > best_farm, "finishing beats farming every checkpoint (terminal dominance)"
    assert E.success_payoff(H, H) > E.SHAPING_MASS            # payoff alone > whole shaping mass
