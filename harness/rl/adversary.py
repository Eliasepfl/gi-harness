"""Inverse-value softlock hunter — the G4 smart-tier SEARCH + DETECT (Elias's idea).

Design: ``notes/adversarial/INVERSE_VALUE_G4.md`` (three layers SEARCH / DETECT /
CONFIRM) and ``notes/adversarial/FEASIBILITY_LITERATURE.md`` (citations, the A/B vs
random-fuzz protocol, the ``Engine.time_scale`` determinism caveat).

This module owns the FIRST TWO layers; layer 3 (CONFIRM) is the existing
``harness.verify.g4.refute_prefix`` tree-refutation oracle, driven by the g4 ladder.

    SEARCH  — anti-optimal rollouts STEERED by the G3'-trained PPO artifacts.
    DETECT  — a sliding-window state-freeze / cycle test over the steered rollout.
    (CONFIRM lives in g4: the frozen prefix P is refuted by the G3 tree solver.)

Why steer, and with WHAT (honest choice). PPO gives a policy ``pi`` (max value) and a
critic ``V(s)`` — NOT ``Q(s, a)``. So there is no exact ``argmin_a Q(s, a)`` to take.
The sound, cheap steering we actually use, and DOCUMENT here:

  * PRIMARY — **anti-policy action selection**: ``a = argmin_a pi(a | s)`` (the move the
    trained policy is LEAST likely to take). This is the model-free mirror of the
    optimal policy: where ``pi`` flows toward the goal, its argmin flows away from it,
    steering toward low-value / dead regions with ZERO extra training and one forward
    pass per tick. A small ``eps`` of uniform-random ticks keeps many parallel seeds
    from collapsing onto the same anti-greedy path.
  * SECONDARY — **V(s)-guided frontier**: among the states VISITED across the
    anti-policy rollouts, expand (re-seed rollouts) from the LOWEST-``V`` reachable
    states first, and ORDER the softlock candidates handed to the expensive CONFIRM
    oracle by ``V`` (lowest first — most likely already dead). ``V(s)`` is exact from
    the critic; this is the "expand from the lowest-V reachable states first" the
    design asks for, without needing ``Q``.
  * SEEDING — **trajectory-prefix (Backplay / Go-Explore) seeding**: replay a PREFIX of
    the winning witness through the SAME serve stepping, then hand control to the
    anti-policy attacker at tick ``t`` for several ``t``. Attacks that start deep on the
    intended path probe the pockets that open up only mid-trajectory (Resnick et al.
    2018 "Backplay"; Ecoffet et al. 2019/2021 "Go-Explore").

The critic is only as good as the G3' training (honest limit): a weak critic steers
worse, but DETECT + CONFIRM stay SOUND — a certified softlock is certified regardless
of how the prefix was found. The steering only changes the HIT-RATE (see the A/B).

Env contract (duck-typed, so a fake env unit-tests this with no engine):
    ``.actions``            list[str]     the discrete action vocabulary
    ``.horizon``            int           decision-tick cap
    ``.reset(seed) -> (obs, info)``       ``info['latched']`` = {cp: tick|None}
    ``.step(idx)  -> (obs, r, term, trunc, info)``  info as above + ``result``
    ``.last_snapshot``      dict          {name: {pos:[x,y], vel:[vx,vy], angle}}
                                          the CURRENT world pose (for the fingerprint)

Critic contract (duck-typed):
    ``.action_probs(obs) -> np.ndarray[n_actions]``   the policy's action distribution
    ``.value(obs) -> float``                          V(s)
    ``.source`` str                                   provenance tag
:class:`SB3PolicyCritic` wraps a trained stable-baselines3 model to this contract.
"""

from __future__ import annotations

import random

import numpy as np

from harness.core.statetree import fingerprint, fp_delta
from harness.verify.gameverify import EFFICACY_EPS

# --- Constants ([eng.] = calibrated engineering choice) ------------------- #
DETECT_WINDOW = 6          # N decision ticks that must ALL be frozen/cyclic to fire (5..10) [eng.]
DEFAULT_SEEDS = 12         # parallel anti-policy rollout seeds [eng.]
DEFAULT_EPS = 0.1          # fraction of ticks taken uniformly-random (breadth) [eng.]
PREFIX_HANDOFF = (2, 4, 8, 16)  # witness-prefix backplay handoff ticks [eng.]
V_FRONTIER_K = 4           # lowest-V visited states re-seeded per search [eng.]
DEFAULT_MAX_TICKS = 120    # per-rollout decision-tick cap (matches g4 PROBE_HORIZON) [eng.]

# --- S1.5 policy-guided descent ([eng.]) ---------------------------------- #
# The descent tier (Elias's "return-then-descend": navigate the working policy INTO a
# low-V basin, THEN freeze-seek). See notes/adversarial/STALE_SEEKING_PLAN.md §3.1.
DESCENT_TICKS = 30         # descent-phase length; alpha ramps 0->1 over this [eng.]
WAYPOINT_K = 6             # low-V waypoints selected for descent rollouts [eng.]
WAYPOINT_FRONTIER_K = 3    # lowest-V states kept per exploratory frontier rollout [eng.]
EXPLORE_SEEDS = (0, 1, 2)  # exploratory anti-policy rollout seeds for the frontier pool [eng.]


# ======================================================================== #
# Critic adapter — wrap a trained SB3 model to the (action_probs, value) contract
# ======================================================================== #
class SB3PolicyCritic:
    """Adapt a trained stable-baselines3 model to the critic contract this module
    steers by. Actor-critic algos (PPO / A2C) expose BOTH a categorical policy
    (``get_distribution``) and the critic (``predict_values``); DQN exposes neither,
    so it falls back to its Q-net (``argmin_a Q`` for anti-policy, ``max_a Q`` for V).

    Imports torch lazily inside the calls so the vendored lane (no SB3/torch on the
    critic path) never pays for it at import time."""

    def __init__(self, model, *, source: str = "sb3_policy"):
        self.model = model
        self.policy = getattr(model, "policy", model)
        # DQN's policy has a q_net and NO value/distribution head — detect once.
        self.is_qnet = hasattr(self.policy, "q_net") and not hasattr(
            self.policy, "predict_values")
        algo = type(model).__name__.lower()
        self.source = f"{source}:{algo}" if source == "sb3_policy" else source

    def _obs_tensor(self, obs):
        arr = np.asarray(obs, dtype=np.float32)
        obs_t, _ = self.policy.obs_to_tensor(arr)
        return obs_t

    def action_probs(self, obs) -> np.ndarray:
        """The policy's action distribution ``pi(.|s)`` as a 1-D probability vector.
        The anti-policy chooser takes ``argmin`` of this — the move the trained policy
        is LEAST likely to make. DQN has no explicit policy, so we return the induced
        GREEDY policy ``softmax(Q)`` (high-Q actions get high weight); ``argmin`` of it
        is then the lowest-Q action, i.e. ``argmin_a Q(s, a)`` — the worst / anti-optimal
        move, exactly mirroring the actor-critic ``argmin pi`` case."""
        import torch

        with torch.no_grad():
            obs_t = self._obs_tensor(obs)
            if self.is_qnet:
                q = self.policy.q_net(obs_t).detach().cpu().numpy().reshape(-1)
                # softmax(Q) = the induced greedy policy; argmin(probs) == argmin_a Q.
                e = np.exp(q - q.max())
                return e / max(e.sum(), 1e-12)
            dist = self.policy.get_distribution(obs_t)
            probs = dist.distribution.probs.detach().cpu().numpy().reshape(-1)
        return probs

    def value(self, obs) -> float:
        """V(s) from the critic (actor-critic) or ``max_a Q(s, a)`` (DQN)."""
        import torch

        with torch.no_grad():
            obs_t = self._obs_tensor(obs)
            if self.is_qnet:
                q = self.policy.q_net(obs_t).detach().cpu().numpy().reshape(-1)
                return float(q.max())
            v = self.policy.predict_values(obs_t)
        return float(np.asarray(v.detach().cpu()).reshape(-1)[0])


# ======================================================================== #
# Snapshot / latch helpers
# ======================================================================== #
def _n_latched(info) -> int:
    if info is None:
        return 0
    if "n_latched" in info:
        return int(info["n_latched"])
    latched = info.get("latched") or {}
    return sum(1 for v in latched.values() if v is not None)


def _snapshot(env) -> dict:
    snap = getattr(env, "last_snapshot", None)
    return snap or {}


# ======================================================================== #
# Policies — the per-tick action choosers (steering seam)
# ======================================================================== #
def anti_policy_chooser(critic, eps: float = DEFAULT_EPS):
    """A per-tick chooser: ``argmin_a pi(a|s)`` with an ``eps`` uniform-random tick
    for breadth. The PRIMARY inverse-value steering (documented in the module
    docstring). Ties break to the lowest index (deterministic under the rng)."""

    def choose(obs, actions, rng):
        if rng.random() < eps:
            return rng.randrange(len(actions))
        probs = np.asarray(critic.action_probs(obs), dtype=np.float64).reshape(-1)
        if probs.size != len(actions):     # defensive: mismatched head -> uniform
            return rng.randrange(len(actions))
        return int(np.argmin(probs))

    return choose


def random_chooser():
    """The A/B baseline: uniform-random action every tick (no critic). Same rollout
    + DETECT machinery, so candidates/1k-ticks is an apples-to-apples comparison."""

    def choose(obs, actions, rng):
        return rng.randrange(len(actions))

    return choose


# ======================================================================== #
# S1.5 — policy-guided DESCENT chooser (Elias's return-then-descend idea)
# The literature's return-then-explore / PEG plan-then-command pattern (see
# notes/adversarial/STALE_SEEKING_PLAN.md §3.1): instead of a HARD switch from the
# working policy to the anti-policy, blend them with an alpha that RAMPS 0->1 over the
# descent phase — start near pi (competent navigation that keeps the body ALIVE and
# MOBILE, travelling deeper into the low-V basin) and hand off SMOOTHLY to the freeze-
# seeking argmin(pi). This travels INTO committed low-value territory the cold anti-
# policy dies short of (INVERSE_VALUE_G4 weak-critic failure: pure argmin dives OOB =
# a LOSS, which DETECT correctly refuses). EFFICIENCY-ONLY: CONFIRM is the sole
# certifier, so the alpha schedule only shapes the search yield, never validity.
# ======================================================================== #
def linear_alpha_schedule(descent_ticks: int = DESCENT_TICKS):
    """A per-descent-tick alpha ramp ``0 -> 1`` over ``descent_ticks`` ticks:
    ``alpha(0) = 0`` (pure competent pi) up to ``alpha(descent_ticks) = 1`` (pure anti-
    policy argmin). Monotone non-decreasing and clamped to ``[0, 1]``. Documented as
    [eng.] — a smooth handoff, not a hard switch (STALE_SEEKING_PLAN.md §3.1)."""
    n = max(1, int(descent_ticks))

    def sched(t: int) -> float:
        return min(1.0, max(0.0, float(t) / float(n)))

    return sched


def _sample_from_probs(probs, rng) -> int:
    """Sample an action index from a probability vector using a ``random.Random`` (so
    the descent chooser shares the rollout's rng seam). A degenerate (all-zero) vector
    falls back to ``argmax`` — never raises."""
    total = float(np.sum(probs))
    if not (total > 0.0):
        return int(np.argmax(probs))
    u = rng.random() * total
    acc = 0.0
    for i in range(len(probs)):
        acc += float(probs[i])
        if u < acc:
            return i
    return len(probs) - 1


def descent_chooser(critic, alpha_schedule=None, *, descent_ticks: int = DESCENT_TICKS):
    """The S1.5 alpha-ramped DESCENT chooser. At descent-tick ``t`` (0-indexed from the
    FIRST post-return call — the counter is per-chooser state), ``alpha =
    alpha_schedule(t)``: with prob ``alpha`` take the ANTI-POLICY ``argmin(pi(.|s))``
    (freeze-seek), else SAMPLE from ``pi(.|s)`` (competent navigation that keeps the
    body alive + mobile). ``alpha`` ramps ``0 -> 1`` (default ``linear_alpha_schedule``)
    so control hands off SMOOTHLY from competent pi to the freeze-seeker.

    At ``alpha = 0`` the chooser is pure ``pi`` (sample); at ``alpha = 1`` it is pure
    ``argmin(pi)``; the probability of the argmin action rises monotonically with alpha
    — the ramp correctness the unit tests pin. Ties in ``argmin`` break to the lowest
    index (deterministic under the rng)."""
    sched = alpha_schedule or linear_alpha_schedule(descent_ticks)
    counter = {"t": 0}

    def choose(obs, actions, rng):
        t = counter["t"]
        counter["t"] += 1
        alpha = float(sched(t))
        probs = np.asarray(critic.action_probs(obs), dtype=np.float64).reshape(-1)
        if probs.size != len(actions):        # defensive: mismatched head -> uniform
            return rng.randrange(len(actions))
        if rng.random() < alpha:
            return int(np.argmin(probs))       # anti-policy freeze-seek
        return _sample_from_probs(probs, rng)  # competent navigation (sample pi)

    return choose


# ======================================================================== #
# SEARCH — a single steered rollout (optionally backplay-seeded by a prefix)
# ======================================================================== #
def rollout(env, choose, *, seed: int, prefix=None, rng=None, critic=None,
            max_ticks: int = DEFAULT_MAX_TICKS):
    """One steered rollout. Optionally REPLAY ``prefix`` (witness-prefix backplay
    seed) first, then hand control to ``choose`` at ``handoff_tick = len(prefix)``.

    Returns a trail dict:
      ``actions``       flat per-tick action strings actually taken
      ``fps``           fingerprints, ``fps[k]`` = state AFTER ``k`` actions
                        (``fps[0]`` = the freshly-reset pose); len == ticks + 1
      ``latched``       ``latched[k]`` = cumulative checkpoints latched after k acts
      ``values``        ``values[k]`` = ``critic.value`` at that state (None if no critic)
      ``terminal_tick`` first tick the episode terminated (else None)
      ``handoff_tick``  the tick control passed from the prefix to ``choose``

    When ``critic`` is supplied, ``V(s)`` is recorded at every state — the data the
    V(s)-frontier and the low-V candidate ordering ride."""
    rng = rng or random.Random(seed)
    actions = list(env.actions)
    prefix = list(prefix or [])
    obs, info = env.reset(seed)

    fps = [fingerprint(_snapshot(env))]
    latched = [_n_latched(info)]
    values = [_value_of(critic, obs)]
    taken: list[str] = []
    terminal_tick = None

    def _do(idx):
        nonlocal obs
        obs, _r, term, trunc, _info = env.step(int(idx))
        taken.append(actions[int(idx)])
        fps.append(fingerprint(_snapshot(env)))
        latched.append(_n_latched(_info))
        values.append(_value_of(critic, obs))
        return term, trunc

    # 1) Backplay: replay the witness prefix through the SAME serve stepping.
    for a in prefix:
        try:
            idx = actions.index(a)
        except ValueError:
            break                                   # prefix action not in vocab -> stop
        term, trunc = _do(idx)
        if term or trunc:
            terminal_tick = len(taken) - 1
            break
    handoff_tick = len(taken)

    # 2) Anti-policy (or random) control to the horizon.
    while terminal_tick is None and len(taken) < max_ticks:
        idx = choose(obs, actions, rng)
        term, trunc = _do(idx)
        if term or trunc:
            terminal_tick = len(taken) - 1
            break

    return {
        "actions": taken, "fps": fps, "latched": latched, "values": values,
        "terminal_tick": terminal_tick, "handoff_tick": handoff_tick,
        "seed": seed, "prefix": prefix, "ticks": len(taken),
    }


def _value_of(critic, obs):
    if critic is None:
        return None
    try:
        return float(critic.value(obs))
    except Exception:      # noqa: BLE001 - a degenerate critic must not sink the search
        return None


# ======================================================================== #
# DETECT — sliding-window state-freeze / cycle test (pure function)
# ======================================================================== #
def detect_softlock_window(fps, latched, terminal_tick, *, window: int = DETECT_WINDOW,
                           eps: float = EFFICACY_EPS):
    """Slide a length-``window`` window over the post-action fingerprint trail and
    return the EARLIEST softlock window (Elias's DETECT criterion).

    A window opening at action index ``i`` covers the ``window`` decision ticks
    ``i+1 .. i+window`` (states ``fps[i] .. fps[i+window]``). It FIRES when, over that
    FULL window:
      * the state is FROZEN — every ``fps[j]`` within ``eps`` of ``fps[i]`` — OR a
        CLOSED CYCLE — some later state returns within ``eps`` of an earlier one, AND
      * NO new checkpoint latched (``latched[i+window] == latched[i]``), AND
      * the episode did NOT terminate inside the window.
    The full-window requirement is the false-positive guard: a legit "push into a
    wall" for 1-3 ticks is too short to fill the window.

    Returns ``(fired, cut, info)``. ``cut = i`` is where the suspect PREFIX is cut
    (``actions[:i]`` — the moves that led INTO the frozen region). ``info`` carries
    ``kind`` (``frozen``/``cycle``), ``freeze_index`` and ``window``."""
    n = len(fps)
    info = {"kind": None, "freeze_index": None, "window": window}
    if window < 1 or n <= window:
        return False, None, info
    last_i = n - 1 - window
    for i in range(0, last_i + 1):
        end = i + window
        if terminal_tick is not None and terminal_tick < end:
            # A terminal inside (or opening) the window disqualifies it; and once the
            # trail has terminated, no later full window exists either.
            if terminal_tick <= i:
                break
            continue
        if latched[end] != latched[i]:
            continue                                 # progress in the window -> not stale
        frozen = all(fp_delta(fps[i], fps[j]) < eps for j in range(i + 1, end + 1))
        if frozen:
            info.update(kind="frozen", freeze_index=i)
            return True, i, info
        # Closed cycle: a genuine oscillation RETURNS to where it began (the window's
        # first state recurs later) AND is CONFINED to few states it revisits (<= half
        # the window distinct, eps-clustered). Both conditions are the false-positive
        # guard: the ONSET of a freeze ([drift..., then flat]) never recurs to its first
        # state, and a monotone stall-then-escape ("push into wall" then move on) keeps
        # MANY distinct states — so neither trips it. Only a real period-<=window//2 loop
        # (e.g. period-2 churn in a pocket) both recurs and stays confined.
        recurs = any(fp_delta(fps[i], fps[j]) < eps for j in range(i + 1, end + 1))
        if recurs:
            reps: list = []
            for j in range(i, end + 1):
                if not any(fp_delta(fps[j], r) < eps for r in reps):
                    reps.append(fps[j])
            if 2 <= len(reps) <= max(2, window // 2):
                info.update(kind="cycle", freeze_index=i)
                return True, i, info
    return False, None, info


# ======================================================================== #
# SEARCH driver — many seeds + backplay + V-frontier -> ordered candidates
# ======================================================================== #
def _candidate_from_rollout(roll, *, window, eps, source, extra=None):
    """Run DETECT on one rollout; return a candidate dict or None."""
    fired, cut, info = detect_softlock_window(
        roll["fps"], roll["latched"], roll["terminal_tick"], window=window, eps=eps)
    if not fired:
        return None
    prefix = list(roll["actions"])[:max(1, cut)]     # never an empty prefix
    val = roll["values"][cut] if cut < len(roll["values"]) else None
    prov = {"source": source, "seed": roll["seed"], "handoff_tick": roll["handoff_tick"],
            "kind": info["kind"], "value_at_freeze": val}
    if extra:
        prov.update(extra)
    return {"prefix": prefix, "freeze_fp": roll["fps"][cut], "provenance": prov,
            "value": val}


def search(env, critic, *, seeds=None, eps=DEFAULT_EPS, window=DETECT_WINDOW,
           fp_eps=EFFICACY_EPS, witness_actions=None, handoffs=PREFIX_HANDOFF,
           v_frontier=True, v_frontier_k=V_FRONTIER_K,
           max_ticks=DEFAULT_MAX_TICKS, budget_ticks=None):
    """Run the inverse-value SEARCH + DETECT over one env, returning ordered softlock
    CANDIDATES (pre-CONFIRM). Layers:

      1. anti-policy rollouts from each of ``seeds`` (breadth via ``eps``),
      2. trajectory-prefix backplay: witness prefixes of length ``handoffs`` handed to
         the anti-policy attacker (only when ``witness_actions`` is supplied),
      3. V(s)-frontier: re-seed anti-policy rollouts from the ``v_frontier_k`` LOWEST-V
         states visited so far (deepest / most-committed low-value reachable states).

    Candidates are de-duplicated by their frozen-state fingerprint and ORDERED by
    ``V`` ascending (lowest-value / most-likely-dead first) — the V-frontier ordering
    that the confirm cap (top-M) then rides. ``budget_ticks`` caps total simulated
    decision ticks (for the A/B and cheap real runs). Deterministic under ``seeds``.
    """
    seeds = list(range(DEFAULT_SEEDS)) if seeds is None else list(seeds)
    choose = anti_policy_chooser(critic, eps=eps) if critic is not None else random_chooser()
    source = getattr(critic, "source", "random") if critic is not None else "random"

    candidates: list = []
    seen_fp = set()
    ticks_used = 0
    rollouts = 0
    detections = 0              # raw DETECT firings (PRE-dedup) — the A/B efficiency signal
    visited: list = []          # (value, obs, prefix) low-V frontier pool

    def _consider(roll, src, extra=None):
        nonlocal ticks_used, rollouts, detections
        ticks_used += roll["ticks"]
        rollouts += 1
        cand = _candidate_from_rollout(roll, window=window, eps=fp_eps, source=src,
                                       extra=extra)
        if cand is None:
            return
        detections += 1         # this rollout walked into a softlock (counted even if the
                                # frozen fingerprint was already seen — search EFFICIENCY,
                                # not the deduped CONFIRM worklist)
        if cand["freeze_fp"] not in seen_fp:
            seen_fp.add(cand["freeze_fp"])
            candidates.append(cand)

    def _over_budget():
        return budget_ticks is not None and ticks_used >= budget_ticks

    # 1) anti-policy rollouts from many seeds.
    for s in seeds:
        if _over_budget():
            break
        roll = rollout(env, choose, seed=s, rng=random.Random(s), critic=critic,
                       max_ticks=max_ticks)
        _consider(roll, source)
        _collect_frontier(critic, roll, visited)

    # 2) trajectory-prefix backplay (Go-Explore / Backplay seeding).
    if witness_actions:
        for t in handoffs:
            if _over_budget():
                break
            pref = list(witness_actions)[:t]
            if not pref:
                continue
            roll = rollout(env, choose, seed=1000 + t, prefix=pref,
                           rng=random.Random(1000 + t), critic=critic, max_ticks=max_ticks)
            _consider(roll, source, extra={"backplay_from": t})
            _collect_frontier(critic, roll, visited)

    # 3) V(s)-frontier: re-seed anti-policy rollouts from the lowest-V visited states.
    if v_frontier and critic is not None and visited:
        visited.sort(key=lambda vp: (vp[0], vp[1]))   # ascending V, then prefix
        for rank, (_v, pref) in enumerate(visited[:v_frontier_k]):
            if _over_budget() or not pref:
                continue
            roll = rollout(env, choose, seed=2000 + rank, prefix=pref,
                           rng=random.Random(2000 + rank), critic=critic, max_ticks=max_ticks)
            _consider(roll, source, extra={"v_frontier_rank": rank})

    # Order candidates by V ascending (lowest-value / most-likely-dead first). A None
    # value (no critic / random baseline) sorts last so the critic arm keeps priority.
    candidates.sort(key=lambda c: (c["value"] is None, c["value"] if c["value"] is not None else 0.0))
    return {"candidates": candidates, "ticks_simulated": ticks_used,
            "rollouts": rollouts, "detections": detections, "source": source}


def _collect_frontier(critic, roll, visited):
    """Record the LOWEST-``V`` state visited in this rollout as a re-seed frontier
    point ``(value, prefix_to_reach_it)`` — the "expand from the lowest-V reachable
    states first" seed. ``V`` is the exact critic value recorded per tick; the prefix
    replays the SAME serve stepping back to that state (Go-Explore RETURN)."""
    if critic is None:
        return
    vals = roll["values"]
    acts = roll["actions"]
    # values[k] is V AFTER k actions; a re-seed prefix of length k reaches values[k].
    best_k, best_v = None, None
    for k in range(1, len(acts)):        # skip the reset state (k=0) — nothing to seed
        v = vals[k]
        if v is None:
            continue
        if best_v is None or v < best_v:
            best_v, best_k = v, k
    if best_k is not None:
        visited.append((best_v, list(acts)[:best_k]))


# ======================================================================== #
# S1.5 — waypoint pool + policy-guided DESCENT search
# Design: notes/adversarial/STALE_SEEKING_PLAN.md §3.1 (build-first). Two-level
# return-then-explore: SELECT low-V waypoints (from the winning witness + an inverse-
# visitation frontier), RETURN to each by deterministic prefix replay, then DESCEND
# with the alpha-ramped chooser and DETECT the frozen window. Same candidate shape as
# ``search`` -> the same CONFIRM oracle certifies them (soundness unchanged).
# ======================================================================== #
def collect_low_v_states(roll, k: int = WAYPOINT_FRONTIER_K):
    """The ``k`` LOWEST-``V`` states visited in a rollout as ``(value, prefix)`` — the
    inverse-visitation frontier (generalises :func:`_collect_frontier` from the single
    best to the top-k). Each ``prefix`` replays the SAME stepping back to that state
    (Go-Explore RETURN); ``values[j]`` is ``V`` AFTER ``j`` actions, so a length-``j``
    prefix reaches it. Deterministic — sorted by ``V`` ascending then prefix.

    TERMINAL states are EXCLUDED: a body that has left the arena / lost is a dead-end,
    not a reachable RETURN target (the Go-Explore archive only keeps reachable cells) —
    and the critic is least reliable exactly on those OOD/terminal states, so returning
    to one wastes a descent rollout with no room to descend."""
    vals = roll["values"]
    acts = roll["actions"]
    term = roll.get("terminal_tick")
    hi = int(term) if term is not None else len(acts)   # exclude the terminal state
    scored: list = []
    for j in range(1, hi + 1):              # skip the reset state (j=0)
        v = vals[j] if j < len(vals) else None
        if v is None:
            continue
        scored.append((float(v), list(acts)[:j]))
    scored.sort(key=lambda vp: (vp[0], vp[1]))
    return scored[:max(1, int(k))]


def select_waypoints(env, critic, *, witness_actions=None, handoffs=PREFIX_HANDOFF,
                     explore_seeds=EXPLORE_SEEDS, explore_ticks=DEFAULT_MAX_TICKS,
                     n_waypoints=WAYPOINT_K, frontier_k=WAYPOINT_FRONTIER_K,
                     eps=DEFAULT_EPS):
    """Build the S1.5 waypoint pool and SELECT the lowest-``V`` targets (Elias's
    adaptation; the plan cites AVF as the adjacent precedent for inverse-critic
    targeting). Pool =

      (a) WINNING-witness cuts at ``handoffs`` ticks — replay the certified witness
          through the SAME stepping, record per-tick ``V`` (Backplay / CCPT winning-
          spine sampling); the deterministic ``prefix`` is ``witness[:t]``.
      (b) an INVERSE-VISITATION FRONTIER — the ``frontier_k`` lowest-``V`` states from
          each of ``explore_seeds`` short anti-policy rollouts; the ``prefix`` is the
          recorded action prefix that reaches the state.

    Each waypoint is ``{prefix, value, fp, source}``. Returned SORTED by ``V`` ascending
    (None sorts last), de-duplicated by frozen fingerprint (else prefix), capped at
    ``n_waypoints``. Deterministic under the seeds; EFFICIENCY-ONLY (feeds the search,
    never the certificate)."""
    if critic is None:
        return []
    pool: list = []

    # (a) winning-witness cuts.
    wit = list(witness_actions or [])
    if wit:
        max_t = min(len(wit), max(handoffs) if handoffs else len(wit))
        roll = rollout(env, random_chooser(), seed=0, prefix=wit[:max_t],
                       rng=random.Random(0), critic=critic, max_ticks=max_t)
        acts = roll["actions"]
        for t in handoffs:
            if t <= 0 or t > len(acts):
                continue
            v = roll["values"][t] if t < len(roll["values"]) else None
            fp = roll["fps"][t] if t < len(roll["fps"]) else None
            pool.append({"prefix": list(acts)[:t], "value": v, "fp": fp,
                         "source": "witness"})

    # (b) inverse-visitation frontier.
    choose = anti_policy_chooser(critic, eps=eps)
    for s in explore_seeds:
        roll = rollout(env, choose, seed=s, rng=random.Random(s), critic=critic,
                       max_ticks=explore_ticks)
        for v, pref in collect_low_v_states(roll, frontier_k):
            plen = len(pref)
            fp = roll["fps"][plen] if plen < len(roll["fps"]) else None
            pool.append({"prefix": pref, "value": v, "fp": fp, "source": "frontier"})

    # Select by LOW V (ascending; None last), dedup by fingerprint (else prefix).
    pool.sort(key=lambda wp: (wp["value"] is None,
                              wp["value"] if wp["value"] is not None else 0.0))
    seen, uniq = set(), []
    for wp in pool:
        key = wp["fp"] if wp["fp"] is not None else tuple(wp["prefix"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(wp)
    return uniq[:max(1, int(n_waypoints))]


def descent_search(env, critic, *, witness_actions=None, handoffs=PREFIX_HANDOFF,
                   n_waypoints=WAYPOINT_K, descent_ticks=DESCENT_TICKS,
                   frontier_k=WAYPOINT_FRONTIER_K, explore_seeds=EXPLORE_SEEDS,
                   eps=DEFAULT_EPS, window=DETECT_WINDOW, fp_eps=EFFICACY_EPS,
                   max_ticks=DEFAULT_MAX_TICKS, budget_ticks=None):
    """S1.5 POLICY-GUIDED DESCENT search. For each selected low-V waypoint: RETURN by
    deterministic prefix replay (``rollout(prefix=...)`` — the return phase), then
    DESCEND with the alpha-ramped :func:`descent_chooser`, and run DETECT on the trail.
    A frozen window yields a softlock CANDIDATE whose cut prefix (return moves + descent
    moves up to the freeze) is exactly what CONFIRM refutes — identical shape to
    :func:`search`, so the same tree oracle certifies it.

    Returns ``{candidates, waypoints, ticks_simulated, rollouts, detections, source}``.
    Candidates are de-duplicated by frozen fingerprint and ORDERED by ``V`` ascending.
    Requires a critic (the S1 model gate); ``budget_ticks`` caps total simulated ticks.
    Deterministic under the seeds. EFFICIENCY-ONLY."""
    if critic is None:
        return {"candidates": [], "waypoints": [], "ticks_simulated": 0,
                "rollouts": 0, "detections": 0, "source": "random"}
    source = getattr(critic, "source", "policy_descent")

    waypoints = select_waypoints(
        env, critic, witness_actions=witness_actions, handoffs=handoffs,
        explore_seeds=explore_seeds, explore_ticks=max_ticks, n_waypoints=n_waypoints,
        frontier_k=frontier_k, eps=eps)

    candidates: list = []
    seen_fp = set()
    ticks_used = 0
    rollouts = 0
    detections = 0

    for rank, wp in enumerate(waypoints):
        if budget_ticks is not None and ticks_used >= budget_ticks:
            break
        prefix = list(wp["prefix"])
        # A fresh descent_chooser per waypoint so the alpha ramp restarts at t=0 (the
        # return prefix is replayed WITHOUT calling the chooser, so the first descent
        # tick is alpha=0 — pure competent pi).
        choose = descent_chooser(critic, linear_alpha_schedule(descent_ticks))
        roll = rollout(env, choose, seed=3000 + rank, prefix=prefix,
                       rng=random.Random(3000 + rank), critic=critic,
                       max_ticks=len(prefix) + descent_ticks)
        ticks_used += roll["ticks"]
        rollouts += 1
        cand = _candidate_from_rollout(
            roll, window=window, eps=fp_eps, source=source,
            extra={"waypoint_source": wp["source"], "waypoint_value": wp["value"],
                   "waypoint_rank": rank, "descent": True,
                   "return_len": len(prefix)})
        if cand is None:
            continue
        detections += 1
        if cand["freeze_fp"] not in seen_fp:
            seen_fp.add(cand["freeze_fp"])
            candidates.append(cand)

    candidates.sort(key=lambda c: (c["value"] is None,
                                   c["value"] if c["value"] is not None else 0.0))
    wp_summary = [{"prefix_len": len(w["prefix"]), "value": w["value"],
                   "source": w["source"]} for w in waypoints]
    return {"candidates": candidates, "waypoints": wp_summary,
            "ticks_simulated": ticks_used, "rollouts": rollouts,
            "detections": detections, "source": source}


# ======================================================================== #
# A/B bench — inverse-value vs random fuzz at the SAME tick budget
# ======================================================================== #
def ab_bench(env_factory, critic, *, budget_ticks, seeds=None, window=DETECT_WINDOW,
             witness_actions=None, max_ticks=DEFAULT_MAX_TICKS, descent=False,
             descent_ticks=DESCENT_TICKS, handoffs=PREFIX_HANDOFF):
    """Run the arms at the SAME ``budget_ticks`` on FRESH envs and report softlocks
    found per 1000 simulated ticks (FEASIBILITY_LITERATURE.md req 5). The inverse-value
    arm (S1) uses ``critic``; the random arm uses none (uniform fuzz); the optional
    ``descent`` arm (S1.5) runs :func:`descent_search` with the SAME critic. Returns::

        {"inverse_value": {...}, "random": {...}[, "descent": {...}], "budget_ticks": B}

    with each arm carrying ``detections`` (raw DETECT firings, the search-efficiency
    signal), ``candidates`` (distinct frozen fingerprints -> the deduped CONFIRM
    worklist), ``ticks_simulated`` / ``rollouts`` / ``per_1k``. ``per_1k`` is on
    DETECTIONS: on a fixture with one pit the deduped candidate count saturates at 1, so
    raw detections-per-1k-ticks is the fair "how directly does the arm walk into a
    softlock" metric. CONFIRM is the same expensive oracle for both arms, so this
    pre-confirm hit-rate is the honest search comparison (MDPFuzz caveat: steering must
    beat ablated random, not just match it). The certified-per-1k headline (CONFIRM in
    the loop) is measured in-image on the real fixtures (scripts/descent_ab.py).
    """
    seeds = list(range(DEFAULT_SEEDS * 4)) if seeds is None else list(seeds)

    def _summary(res):
        ticks = max(1, res["ticks_simulated"])
        return {"detections": res["detections"],
                "candidates": len(res["candidates"]),
                "ticks_simulated": res["ticks_simulated"],
                "rollouts": res["rollouts"],
                "per_1k": round(1000.0 * res["detections"] / ticks, 3)}

    def _run(fn):
        env = env_factory()
        try:
            return fn(env)
        finally:
            close = getattr(env, "close", None)
            if callable(close):
                close()

    def _search_arm(crit, wit):
        return _summary(_run(lambda env: search(
            env, crit, seeds=seeds, window=window, witness_actions=wit,
            v_frontier=crit is not None, max_ticks=max_ticks,
            budget_ticks=budget_ticks)))

    out = {
        "inverse_value": _search_arm(critic, witness_actions),
        "random": _search_arm(None, None),
        "budget_ticks": budget_ticks,
    }
    if descent and critic is not None:
        out["descent"] = _summary(_run(lambda env: descent_search(
            env, critic, witness_actions=witness_actions, handoffs=handoffs,
            descent_ticks=descent_ticks, window=window, max_ticks=max_ticks,
            budget_ticks=budget_ticks)))
    return out
