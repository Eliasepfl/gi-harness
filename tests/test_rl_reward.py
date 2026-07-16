"""Reward-invariant unit tests for the REALIGNED G3' reward (harness/rl/env.py, 2026-07-16).

The realignment restructured the per-step reward so the TERMINAL success payoff strictly
dominates any farmable shaping and so success earned EARLIER is worth more (Elias's decaying
reward). These pure tests pin the four invariants the design guarantees (see env.step_reward
and the env.py module docstring "REWARD"):

  (a) the total farmable shaping < the success payoff at ANY tick;
  (b) an earlier success yields a strictly greater episode return than a later success;
  (c) any success return > any no-success return;
  (d) for EQUAL progress, a failure return < a timeout (no-success) return.

No node / Godot / sb3 — the reward is a pure function of
(n_new_latched, n_cp, result, tick, horizon), so this is the offline safety net for the
in-image convergence probe. The three env step() paths (PlanckEnv, GodotServeEnv,
GodotBatchVecEnv) all call the SAME step_reward, so pinning it here pins all three.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.rl import env as E  # noqa: E402

H = 300  # decision-tick horizon (env.HORIZON)


def _episode_return(latch_ticks, n_cp, terminal, term_tick, horizon=H):
    """Sum ``step_reward`` over a full episode (the env's per-tick semantics).

    ``latch_ticks`` — 1-based ticks at which a NEW checkpoint latches (a game latches
    checkpoints as the agent reaches them; each contributes one new latch that tick).
    ``terminal`` — ``"success"`` / ``"failure"`` / ``None`` (timeout). ``term_tick`` — the
    tick the episode ends (== ``horizon`` for a timeout). The terminal result is attached to
    the final tick only; every prior tick carries ``result=None`` (matching the wire)."""
    latch_ticks = list(latch_ticks)
    total = 0.0
    for tick in range(1, term_tick + 1):
        n_new = latch_ticks.count(tick)
        result = terminal if tick == term_tick else None
        total += E.step_reward(n_new, n_cp, result, tick, horizon)
    return total


# ====================================================================== #
# 0. Constant sizing — the terminal-dominance inequality the design rests on
# ====================================================================== #
def test_constant_sizing_gives_terminal_dominance():
    # the MINIMUM success payoff (floor) strictly dominates the MAXIMUM no-win return
    # (all farmable shaping accrued, minus at least... well, plus zero cost) — this single
    # inequality is what makes invariants (a) and (c) hold with margin.
    assert E.R_SUCCESS * E.SUCCESS_TIME_FLOOR > E.SHAPING_MASS + E.LIVING_COST_TOTAL
    assert 0.0 < E.SUCCESS_TIME_FLOOR < 1.0
    assert E.R_SUCCESS > 0.0
    assert E.R_FAILURE < 0.0
    assert E.LIVING_COST_TOTAL > E.SHAPING_MASS   # a never-finishing episode nets negative
    assert E.SHAPING_MASS > 0.0


# ====================================================================== #
# Pure-function shape: shaping cap, decay, living cost, composition
# ====================================================================== #
@pytest.mark.parametrize("n_cp", [1, 2, 3, 5, 8])
def test_checkpoint_shaping_is_normalized_capped(n_cp):
    # latching ALL n_cp checkpoints accrues EXACTLY SHAPING_MASS, whatever n_cp is.
    total = sum(E.checkpoint_shaping(1, n_cp) for _ in range(n_cp))
    assert total == pytest.approx(E.SHAPING_MASS)
    # each new latch pays SHAPING_MASS/n_cp; two at once pays double.
    assert E.checkpoint_shaping(2, n_cp) == pytest.approx(2 * E.SHAPING_MASS / n_cp)


def test_checkpoint_shaping_zero_when_no_checkpoints():
    assert E.checkpoint_shaping(0, 0) == 0.0
    assert E.checkpoint_shaping(1, 0) == 0.0     # n_cp<=0 -> no shaping (never divides by 0)


def test_success_payoff_decays_with_floor():
    # instant win pays the full base; a buzzer-beater floors at SUCCESS_TIME_FLOOR*R_SUCCESS.
    assert E.success_payoff(0, H) == pytest.approx(E.R_SUCCESS)
    assert E.success_payoff(H, H) == pytest.approx(E.R_SUCCESS * E.SUCCESS_TIME_FLOOR)
    # never below the floor even past the horizon (clamped), never above the base.
    assert E.success_payoff(H + 50, H) == pytest.approx(E.R_SUCCESS * E.SUCCESS_TIME_FLOOR)
    assert E.success_payoff(-5, H) == pytest.approx(E.R_SUCCESS)


def test_tick_cost_totals_living_cost_over_horizon():
    assert E.tick_cost(H) < 0.0
    assert E.tick_cost(H) * H == pytest.approx(-E.LIVING_COST_TOTAL)


def test_step_reward_composition():
    # non-terminal: shaping + living cost only
    assert E.step_reward(1, 2, None, 10, H) == pytest.approx(
        E.checkpoint_shaping(1, 2) + E.tick_cost(H))
    # success: + decayed terminal
    assert E.step_reward(1, 2, "success", 10, H) == pytest.approx(
        E.checkpoint_shaping(1, 2) + E.tick_cost(H) + E.success_payoff(10, H))
    # failure/error: + flat negative terminal
    assert E.step_reward(0, 2, "failure", 10, H) == pytest.approx(
        E.tick_cost(H) + E.R_FAILURE)
    assert E.step_reward(0, 2, "error", 10, H) == E.step_reward(0, 2, "failure", 10, H)


# ====================================================================== #
# (a) total farmable shaping < success payoff at ANY tick
# ====================================================================== #
@pytest.mark.parametrize("n_cp", [1, 2, 3, 5, 8])
def test_invariant_a_shaping_below_success_payoff_at_every_tick(n_cp):
    max_shaping = sum(E.checkpoint_shaping(1, n_cp) for _ in range(n_cp))
    assert max_shaping == pytest.approx(E.SHAPING_MASS)
    for tick in range(0, H + 1):
        assert max_shaping < E.success_payoff(tick, H), (
            f"farmable shaping {max_shaping} must stay below the success payoff "
            f"({E.success_payoff(tick, H)}) even at tick {tick}")


# ====================================================================== #
# (b) earlier success > later success (strictly)
# ====================================================================== #
def test_invariant_b_success_payoff_strictly_decreasing():
    payoffs = [E.success_payoff(t, H) for t in range(0, H + 1)]
    assert all(a > b for a, b in zip(payoffs, payoffs[1:])), "payoff must strictly decrease"


def test_invariant_b_earlier_win_yields_greater_return():
    # identical shaping (both gems by tick 20), win at 30 vs 100 vs 299 -> strictly less.
    latch = [10, 20]
    r30 = _episode_return(latch, 2, "success", 30)
    r100 = _episode_return(latch, 2, "success", 100)
    r299 = _episode_return(latch, 2, "success", 299)
    assert r30 > r100 > r299


# ====================================================================== #
# (c) any success return > any no-success return
# ====================================================================== #
@pytest.mark.parametrize("n_cp", [0, 1, 2, 3, 5])
def test_invariant_c_success_beats_no_success(n_cp):
    # WORST success: win at the buzzer with ALL farmable shaping already collected.
    win_ticks = list(range(1, n_cp + 1))                 # latch each cp on ticks 1..n_cp
    worst_win = _episode_return(win_ticks, n_cp, "success", H)
    # a no-shaping late win (a game with 0 declared checkpoints still wins big)
    bare_win = _episode_return([], n_cp, "success", H)
    # BEST no-success: farm ALL shaping ASAP, then run the clock out (timeout).
    best_timeout = _episode_return(win_ticks, n_cp, None, H)
    # a no-success FAILURE (even one that farmed all shaping and failed instantly).
    farmed_fail = _episode_return(win_ticks, n_cp, "failure", max(1, n_cp))
    assert worst_win > best_timeout
    assert worst_win > farmed_fail
    assert bare_win > best_timeout


# ====================================================================== #
# (d) for equal progress, failure < timeout (no-success)
# ====================================================================== #
@pytest.mark.parametrize("n_latched", [0, 1, 2])
@pytest.mark.parametrize("fail_tick", [1, 50, 150, 299])
def test_invariant_d_failure_below_timeout_at_equal_progress(n_latched, fail_tick):
    n_cp = 2
    latch = list(range(1, n_latched + 1))                # same shaping prefix for both
    fail_ret = _episode_return(latch, n_cp, "failure", fail_tick)
    timeout_ret = _episode_return(latch, n_cp, None, H)  # same progress, ran the clock out
    assert fail_ret < timeout_ret


# ====================================================================== #
# The DIAGNOSIS, encoded: on mini_collect (n_cp=2) farming the first checkpoint and
# dithering must be a NET-NEGATIVE trap that finishing crushes — the exact failure the
# realignment fixes (a 400k-step probe found the old reward converged to never-winning).
# ====================================================================== #
def test_mini_collect_farming_is_a_net_negative_trap():
    n_cp = 2                                             # got_first, got_both
    # Never-win policy: latch got_first @ tick 10, then dither to the horizon.
    farm = _episode_return([10], n_cp, None, H)
    # Winning policy: got_first @ 10, got_both+success @ 30.
    win = _episode_return([10, 30], n_cp, "success", 30)
    assert farm < 0.0, "farming the first checkpoint then dithering must net NEGATIVE"
    assert win > 0.0
    assert win > farm + 5.0, "finishing must crush farming by a wide margin"
