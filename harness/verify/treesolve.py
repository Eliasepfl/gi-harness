"""G3 solver — Go-Explore state-action tree search (default since v2.4).

Replaces pure random macro-action search (``gameverify.run_g3``) as G3's default
solver. Random search cannot chain precise stages: it re-rolls a fresh full-horizon
plan every episode, so a game whose win needs "reach A, THEN topple B, THEN climb
C" is only solved if a single lucky roll nails all three at once. The tree instead
RESTARTS from its best leaves and NEVER re-creates an action combo it already has
(design: ``notes/adversarial/STATE_TREE.md``; prior art: Go-Explore, Ecoffet et al.
2019/2021 — archive reached states, RETURN to a promising one, EXPLORE from there).
A milestone reached once becomes a leaf the search builds on rather than one it must
re-discover from scratch.

How it uses ``harness.core.statetree`` (the shared substrate):

* **Macro edges.** A tree edge is a base action HELD 1..``MACRO_MAX`` ticks; the
  hold length is part of the edge identity (``"<action>*<hold>"``). Depth stays
  manageable at horizon 300 and a node's macro prefix flattens to a per-tick action
  list for replay. The design's "RESTORE = replay the prefix" is exactly this.
* **Explore = a random rollout.** Expanding a frontier leaf runs ONE random-macro
  rollout to the horizon from that leaf's state (like a random-search episode, but
  starting deep). The rollout's macro tail is inserted as a chain of single-macro
  nodes; :meth:`StateTree.record` dedups shared prefixes, so two rollouts that share
  a path merge into one subtree — "never explore the same combination twice".
* **Frontier = restart-from-leaf.** Selection prioritises most-checkpoints-latched,
  then least-visited, then shallowest (most horizon room left), with an ``EPSILON``
  fraction chosen uniformly to avoid tunnel vision — the dense milestone signal the
  design asks lanes to exploit.
* **No-effect rule off (``eps=0``).** The stuck/no-effect heuristic needs the true
  state at every macro boundary; a rollout only yields the final snapshot, so we
  disable it (rollouts escape inert regions via their random tail, and the milestone
  gradient already steers the frontier away from dead ends). Each node's checkpoints
  are still exact — derived from the rollout's cumulative first-latch ticks.

Shape contract: :func:`run_g3_tree` returns the SAME ``layer`` dict as
``gameverify.run_g3`` (checks ``episodes / solvable / non_trivial /
milestones_latched / replayable / solidity``, ``warnings``, ``progress``,
``witness``), so ``gameverify._finish_g3`` and every downstream consumer are
unchanged. The witness dict shape is identical; ``witness["seed"]`` is the WORLD
seed (``gameverify.WORLD_SEED``) — render/replay already replay on the world seed,
never on a plan-provenance seed.

Determinism: a single seeded RNG (``SOLVER_SEED``) drives frontier tie-breaking,
epsilon exploration and rollout tails; no wall-clock. Same game + executor + seed
-> same witness. (Py streams one rollout per round and early-stops; Js batches
``BATCH_SIZE`` rollouts per node process, so the two engines may reach different
witnesses — determinism holds per executor kind.)

Budget: total simulated ticks capped at ``TICK_BUDGET`` (comparable to the legacy
(40+30) episodes x 300 ticks = 21000); each rollout costs ~one horizon like a
random-search episode, so the tree gets a comparable episode count spent far more
purposefully.
"""

from __future__ import annotations

import random

from harness.core.statetree import StateTree
from harness.verify import gameverify as gv

# --- Constants ([eng.] = engineering choice to calibrate) ---------------- #
MACRO_SEP = "*"            # macro-edge name = "<base_action>*<hold_ticks>"
TICK_BUDGET = 21000        # total simulated ticks, ~= legacy (40+30)*300 [eng.]
BATCH_SIZE = 24            # rollouts replayed per batched (Js) node process; also the
                           # per-leaf commitment (like the guided pass's 30 continuations) [eng.]
EPSILON = 0.2              # fraction of rollouts started from a uniform-random leaf [eng.]
SOLVER_SEED = 0            # RNG seed for frontier/action tie-breaking (deterministic) [eng.]


# ======================================================================== #
# Macro-edge helpers
# ======================================================================== #
def _macro_names(actions) -> list:
    """The macro action set: each base action held ``MACRO_MIN..MACRO_MAX`` ticks."""
    return [f"{a}{MACRO_SEP}{h}"
            for a in actions
            for h in range(gv.MACRO_MIN, gv.MACRO_MAX + 1)]


def _split_macro(name: str):
    """``"right*3"`` -> ``("right", 3)``. Splits on the LAST separator, so base
    action names may themselves contain the separator without ambiguity."""
    base, _, hold = name.rpartition(MACRO_SEP)
    return base, int(hold)


def _macro_ticks(prefix) -> int:
    """Nominal flat per-tick length of a macro prefix (sum of hold lengths)."""
    return sum(_split_macro(m)[1] for m in prefix)


def _flatten(prefix) -> list:
    """Expand a macro prefix to a flat per-tick base-action list for replay."""
    flat: list = []
    for name in prefix:
        base, hold = _split_macro(name)
        flat.extend([base] * hold)
    return flat


def _random_tail(rng, base_actions, remaining) -> list:
    """A random macro tail whose flat length covers ``remaining`` ticks (the last
    macro may overshoot; the executor caps the replay at the horizon). Uniform over
    actions and holds — no per-game bias (games differ: some want sustained pushes,
    others alternation), matching the legacy ``_macro_plan``'s distribution."""
    macros: list = []
    total = 0
    while total < remaining:
        base = rng.choice(base_actions)
        hold = rng.randint(gv.MACRO_MIN, gv.MACRO_MAX)
        macros.append(f"{base}{MACRO_SEP}{hold}")
        total += hold
    return macros


# ======================================================================== #
# Frontier selection (restart-from-leaf, milestone-greedy + epsilon explore)
# ======================================================================== #
def _select_leaves(tree, rng, n, horizon, deaths) -> list:
    """Pick ``n`` frontier leaves (with repetition) to restart rollouts from.

    Frontier = open, realised nodes with untried actions AND room left under the
    horizon. The single BEST leaf is hammered by all non-epsilon slots; ``EPSILON``
    of slots pick a uniform-random leaf for breadth. Deterministic given ``rng``.

    Best-leaf priority (a productivity-scored generalisation of the legacy guided
    pass's ``_best_prefix``):
      1. most milestones latched (the dense progress signal),
      2. FEWEST unproductive deaths — a leaf whose restart rollouts keep dying/
         stalling without advancing a milestone is a dead end (e.g. a milestone
         latched mid-air, then a fall); this steers off deceptive leaves toward
         ones a continuation can actually build on,
      3. fewest ticks to reach it (most horizon/time left — decisive for races),
      4. least-visited, then stable prefix.
    A leaf's children are strictly deeper, so the milestone BOUNDARY stays best and
    is hammered with varied full tails until a rollout genuinely advances."""
    frontier = [nd for nd in tree.frontier() if _macro_ticks(nd.prefix) < horizon]
    if not frontier:
        return []
    best = min(frontier,
               key=lambda nd: (-nd.n_latched(), deaths.get(nd.prefix, 0),
                               _macro_ticks(nd.prefix), nd.visits, nd.prefix))
    picks: list = []
    while len(picks) < n:
        if rng.random() < EPSILON:
            picks.append(rng.choice(frontier))             # explore: uniform leaf
        else:
            picks.append(best)                             # exploit: the best leaf
    return picks


# ======================================================================== #
# Rollout insertion (Go-Explore "explore from the returned cell")
# ======================================================================== #
def _insert_rollout(tree, leaf, tail_macros, full_flat, base_ticks, ep) -> None:
    """Insert a rollout's macro tail as a chain of single-macro nodes under ``leaf``
    (up to the tick the rollout ended — success/failure/horizon).

    Each node is credited with EXACT checkpoints (the rollout's cumulative
    first-latch ticks restricted to the node's boundary) and the applied flat
    actions for that segment; only the terminal node carries the real final
    snapshot (intermediate fingerprints are unused — ``eps=0``).
    :meth:`StateTree.record` dedups shared prefixes, so overlapping rollouts merge —
    a milestone reached once becomes a single leaf every later rollout restarts from."""
    total_ticks = int(ep.get("ticks", 0) or 0)
    latches = ep.get("checkpoints", {}) or {}
    node = leaf
    cur_tick = base_ticks
    for macro in tail_macros:
        if cur_tick >= total_ticks:
            break
        hold = _split_macro(macro)[1]
        boundary = min(cur_tick + hold, total_ticks)
        is_last = boundary >= total_ticks
        sub_cp = {k: (t if (t is not None and t <= boundary) else None)
                  for k, t in latches.items()}
        sub_ep = {
            "result": ep["result"] if is_last else "budget",
            "ticks": boundary - cur_tick,
            "checkpoints": sub_cp,
            "final_snapshot": ep.get("final_snapshot", {}) if is_last else {},
            "actions": full_flat[cur_tick:boundary],
        }
        exp = tree.record(node, macro, sub_ep)
        if exp.child is None:                              # eps=0 -> never no_effect
            break
        node = exp.child
        cur_tick = boundary
        if is_last:
            break


# ======================================================================== #
# The search
# ======================================================================== #
def _tree_search(executor, game_source, actions, horizon):
    """Drive the state tree with ``executor`` until success or budget exhaustion.

    Returns ``(witness | None, episodes, replays, tree)`` where ``episodes`` is
    every rollout episode's dict (for the progress diagnosis) and ``witness`` is
    ``gameverify``'s witness dict for the first success (in batch order)."""
    tree = StateTree(_macro_names(actions), world_seed=gv.WORLD_SEED, eps=0.0)
    rng = random.Random(SOLVER_SEED)

    # Root = the initial (zero-tick) state; needed before any expansion.
    root_ep = executor.run_batch(game_source, [{"seed": gv.WORLD_SEED,
                                                "actions": []}], 0)[0]
    tree.init_root(root_ep)

    episodes: list = []
    deaths: dict = {}          # leaf prefix -> restart rollouts that stalled (no new milestone)
    witness = None
    replays = 0
    real_ticks = 0
    batch_n = BATCH_SIZE if executor.batched else 1

    while witness is None and real_ticks < TICK_BUDGET:
        leaves = _select_leaves(tree, rng, batch_n, horizon, deaths)
        if not leaves:
            break                                          # frontier saturated
        plans = []                                         # (leaf, tail_macros, full_flat, base_ticks)
        for leaf in leaves:
            base_flat = _flatten(leaf.prefix)
            base_ticks = len(base_flat)
            tail = _random_tail(rng, actions, horizon - base_ticks)
            full_flat = base_flat + _flatten(tail)
            plans.append((leaf, tail, full_flat, base_ticks))

        specs = [{"seed": gv.WORLD_SEED, "actions": p[2]} for p in plans]
        recs = executor.run_batch(game_source, specs, horizon)

        for (leaf, tail, full_flat, base_ticks), ep in zip(plans, recs):
            replays += 1
            real_ticks += int(ep.get("ticks", 0) or 0)
            episodes.append(ep)
            # Productivity: did restarting from this leaf advance a milestone?
            ep_latched = sum(1 for t in (ep.get("checkpoints") or {}).values()
                             if t is not None)
            if ep["result"] != "success" and ep_latched <= leaf.n_latched():
                deaths[leaf.prefix] = deaths.get(leaf.prefix, 0) + 1
            _insert_rollout(tree, leaf, tail, full_flat, base_ticks, ep)
            if witness is None and ep["result"] == "success":
                witness = gv._make_witness(gv.WORLD_SEED, ep)
                break                                      # first success (batch order) wins
    return witness, episodes, replays, tree


# ======================================================================== #
# G3 layer (drop-in replacement for gameverify.run_g3)
# ======================================================================== #
def run_g3_tree(executor, game_source, actions, declared):
    """Tree-search G3 solver. Same ``layer`` shape as ``gameverify.run_g3``.

    On success the witness is the winning flat plan replayed EXACTLY as the legacy
    path does (same anti-triviality, dead-milestone, order-mismatch, replayable and
    solidity checks). On failure it emits the same ``progress`` diagnosis
    (reach_counts + stuck_after) so hints/telemetry are unchanged.
    ``checks["episodes"]`` carries ``solver="tree"``."""
    layer = {"passed": False, "checks": {}, "warnings": [], "progress": None}
    checks = layer["checks"]
    actions = list(actions or [])
    horizon = gv.PROBE_HORIZON                             # read at call time (test-patchable)

    witness, episodes, replays, tree = _tree_search(executor, game_source, actions,
                                                     horizon)

    checks["episodes"] = gv.check(True, run=replays, solver="tree",
                                  nodes=len(tree), ticks=tree.ticks_simulated)

    # --- No witness -> UNSOLVED, with the same progress diagnosis as run_g3 ---
    checks["solvable"] = gv.check(witness is not None)
    if witness is None:
        layer["progress"] = gv._progress(declared, episodes)
        return layer
    layer["witness"] = witness

    # --- Anti-triviality (identical to run_g3) ---
    checks["non_trivial"] = gv.check(witness["ticks"] >= gv.TRIVIAL_TICKS,
                                     ticks=witness["ticks"])
    if not checks["non_trivial"]["pass"]:
        return layer

    # --- Dead milestones: every declared checkpoint must latch on the witness ---
    dead = [k for k, t in witness["checkpoints"].items() if t is None]
    checks["milestones_latched"] = gv.check(not dead, dead=dead)

    # --- Declared vs empirical latch order (non-fatal warning) ---
    mismatch = gv._order_mismatch(declared, witness["checkpoints"])
    if mismatch:
        layer["warnings"].append(
            f"checkpoint latch order differs from declared order: declared "
            f"[{', '.join(declared)}], observed [{', '.join(mismatch)}]")

    # --- The witness must replay EXACTLY to success from a fresh seeded world ---
    replay = executor.run_batch(
        game_source, [{"seed": gv.WORLD_SEED, "actions": witness["actions"]}],
        len(witness["actions"]), frames_every=1)[0]
    checks["replayable"] = gv.check(replay["result"] == "success",
                                    result=replay["result"])

    # --- Solidity: no sustained deep interpenetration on the winning path ---
    worst = gv._solidity_scan(replay.get("frames", []))
    checks["solidity"] = gv.check(worst is None, **(worst or {}))

    layer["passed"] = all(c["pass"] for c in checks.values())
    return layer
