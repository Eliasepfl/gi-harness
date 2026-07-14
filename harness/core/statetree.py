"""Shared state-action tree — Go-Explore style search substrate.

Implements the frozen design in ``notes/adversarial/STATE_TREE.md``: an archive
of *action prefixes* that async lanes (G3's witness search, G4's attackers)
populate cooperatively, never exploring the same action-combo twice, and
restarting exploration AT THE LEAF of an already-tried prefix.

Prior art (name it in the submission): **Go-Explore** (Ecoffet et al. 2019/2021)
— archive reached states, RETURN to a promising one, EXPLORE from there. Our
variant uses a deterministic simulator, so the RETURN is a pure *replay of the
action prefix* (no snapshot/restore of solver caches) and the archive key is the
prefix itself.

Core identity decision (design §"the node IS the action prefix"): worlds are
deterministic given (game, engine, world_seed, action_prefix) — proven bit-exact
on pymunk and Planck. Therefore:

* **Node identity = the action prefix** (a ``tuple`` of action names from the
  root). Collision-free, zero physics storage. The tree of prefixes IS the state
  tree. ``restore`` = REPLAY the prefix (``node.prefix``); we never snapshot the
  engine.
* Each node also carries a **state fingerprint** — rounded, hashable
  body positions/velocities/angles pulled from the episode's ``final_snapshot``.
  It exists ONLY for heuristics (novelty, the no-effect delta); it NEVER merges
  nodes and never serves as a restore point (design §"approximate state index").

Executor-agnostic BY CONSTRUCTION: this library never runs physics. Callers
replay an action prefix through an *episode executor* (``harness.verify.executors``:
``run_batch(game_source, [{"seed", "actions"}], max_ticks) -> [episode_dict]``)
and hand the resulting episode dict to :meth:`StateTree.record`. The convenience
:meth:`StateTree.expand` accepts a caller-owned executor and does the replay for
you, but the physics still runs in the caller's executor, never here.

The stuck rule (Elias's caveat, made mechanical — design §"the stuck rule"):
if replaying ``prefix + action`` yields a fingerprint within ``eps`` of the
parent's (``Δstate < eps``), the edge is recorded as ``no_effect`` and **no child
node is created**. ``k_stuck`` consecutive no-effect expansions from a node
(default 8) flip it to ``terminal_stuck``.

Dependency-free (stdlib only) and deterministic: no wall-clock, no unseeded
randomness. Edge ``tried_at`` is a monotonic integer counter, not a timestamp.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional

# --- Constants ([eng.] = engineering choice to calibrate) ----------------- #
SCHEMA_VERSION = 1

# Δstate below this reads as "no effect" — seeded from G1's efficacy epsilon
# (gameverify.EFFICACY_EPS = 1e-3), the same spirit the design asks for. [eng.]
EPS_DEFAULT = 1e-3
# Consecutive no-effect expansions from one node before it is terminal_stuck. [eng.]
K_STUCK_DEFAULT = 8
# Fingerprint rounding: fine enough that a real one-tick effect is never quantized
# away (well below EPS_DEFAULT would be self-defeating), coarse enough that JSON
# round-trips are byte-stable. 6 decimals matches gameverify.DETERMINISM_EPS. [eng.]
FP_DECIMALS_DEFAULT = 6
# Physics seed shared by every replayed episode (gameverify.WORLD_SEED). [eng.]
WORLD_SEED_DEFAULT = 0

# Node status enum (design §"node & edge record").
OPEN = "open"
TERMINAL_SUCCESS = "terminal_success"
TERMINAL_FAILURE = "terminal_failure"
TERMINAL_STUCK = "terminal_stuck"
EXHAUSTED = "exhausted"
_TERMINAL_STATUSES = frozenset({TERMINAL_SUCCESS, TERMINAL_FAILURE, TERMINAL_STUCK})

# Episode result -> child status. Anything else ("budget"/"exhausted") stays OPEN;
# "error" is a dead branch folded into terminal_failure (provenance kept on .result).
_RESULT_STATUS = {
    "success": TERMINAL_SUCCESS,
    "failure": TERMINAL_FAILURE,
    "error": TERMINAL_FAILURE,
}


class StateTreeError(Exception):
    """A misuse of the tree API (uninitialised root, foreign node, bad commit)."""


# ======================================================================== #
# Fingerprint helpers (the approximate state index — heuristic use only)
# ======================================================================== #
def fingerprint(snapshot: dict, decimals: int = FP_DECIMALS_DEFAULT) -> tuple:
    """Hashable rounded digest of a ``world.snapshot()`` dict.

    ``snapshot`` is ``{name: {"pos":[x,y], "vel":[vx,vy], "angle":a}}`` (missing
    fields default to zero). Returns a tuple sorted by body name, each entry
    ``(name, px, py, vx, vy, angle)`` with floats rounded to ``decimals`` — stable
    across runs and JSON-serialisable. NEVER used to merge nodes.
    """
    out = []
    for name in sorted(snapshot):
        body = snapshot[name] or {}
        pos = body.get("pos") or (0.0, 0.0)
        vel = body.get("vel") or (0.0, 0.0)
        angle = body.get("angle", 0.0) or 0.0
        out.append((
            str(name),
            round(float(pos[0]), decimals), round(float(pos[1]), decimals),
            round(float(vel[0]), decimals), round(float(vel[1]), decimals),
            round(float(angle), decimals),
        ))
    return tuple(out)


def fp_delta(a: Optional[tuple], b: Optional[tuple]) -> float:
    """Max absolute divergence between two fingerprints over shared bodies.

    ``inf`` when either is unknown or the body sets differ (a topology change is
    never "no effect"). Mirrors ``gameverify._snapshot_delta`` on quantised state.
    """
    if a is None or b is None:
        return float("inf")
    da = {t[0]: t[1:] for t in a}
    db = {t[0]: t[1:] for t in b}
    if da.keys() != db.keys():
        return float("inf")
    worst = 0.0
    for name, va in da.items():
        for x, y in zip(va, db[name]):
            worst = max(worst, abs(x - y))
    return worst


# ======================================================================== #
# Records
# ======================================================================== #
@dataclass
class Edge:
    """A tried ``(node, action)`` transition. ``outcome`` is ``"child"`` (points at
    ``child`` prefix) or ``"no_effect"`` (an edge FACT, no state). ``tried_at`` is a
    monotonic counter (deterministic, not a timestamp)."""

    action: str
    outcome: str                       # "child" | "no_effect"
    child: Optional[tuple] = None      # child prefix when outcome == "child"
    tried_at: int = 0


@dataclass
class Node:
    """A node = the action prefix reaching it. Stores visit metadata, latched
    checkpoints, the state fingerprint, terminal status and its tried edges.

    Claims (``_claims``: action -> lane) are transient in-flight markers for async
    edge claiming; they are runtime-only and NOT serialised."""

    prefix: tuple                                  # () for the root
    parent: Optional[tuple] = None                 # parent prefix (None for root)
    action_from_parent: Optional[str] = None
    depth: int = 0                                 # == len(prefix)
    fingerprint: Optional[tuple] = None            # None until the node is realised
    checkpoints: dict = field(default_factory=dict)  # name -> first-latch tick | None
    result: Optional[str] = None                   # episode result at this node
    status: str = OPEN
    visits: int = 0                                # times expanded-from
    no_effect_streak: int = 0                      # consecutive no-effect expansions
    created_by: Optional[str] = None               # provenance (lane)
    edges: dict = field(default_factory=dict)      # action -> Edge (committed)
    _claims: dict = field(default_factory=dict, repr=False)  # action -> lane (in-flight)

    @property
    def is_root(self) -> bool:
        return not self.prefix

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def n_latched(self) -> int:
        """Number of checkpoints that have latched (tick is not None)."""
        return sum(1 for t in self.checkpoints.values() if t is not None)

    def child_prefixes(self) -> list:
        return [e.child for e in self.edges.values() if e.outcome == "child"]


@dataclass
class Expansion:
    """Result of a recorded/attempted expansion.

    ``outcome``:
      * ``"created"``  — a new child node was created (see ``child``).
      * ``"existing"`` — the edge was already tried (dedup); ``child`` set if it was
        a child edge, else ``None``. No replay / budget was spent.
      * ``"no_effect"``— a brand-new no-effect edge (no child; ``child`` is ``None``).
      * ``"conflict"`` — the edge is claimed in-flight by another lane; skip it.
    """

    outcome: str
    child: Optional[Node]
    parent: Node
    stuck: bool = False                # this expansion flipped parent to terminal_stuck


# ======================================================================== #
# The tree
# ======================================================================== #
class StateTree:
    """A shared, append-only tree of action prefixes (design §"scope & lifecycle").

    Scope key = (game_hash, engine, world_seed, bank_version): any game repair →
    a new tree. Thread/asyncio-safe: every mutation is guarded by an ``RLock`` so
    concurrent lanes never corrupt the structure and edge claiming is atomic (one
    winner per ``(node, action)``). Cross-PROCESS coordination is out of scope for
    this in-memory library — processes cooperate by serialising/merging JSON
    artifacts (``runs/trees/``) or via an external lock file around them.
    """

    def __init__(self, actions: Iterable[str], *, game_hash: str = "",
                 engine: str = "py", world_seed: int = WORLD_SEED_DEFAULT,
                 bank_version: str = "", eps: float = EPS_DEFAULT,
                 k_stuck: int = K_STUCK_DEFAULT,
                 fp_decimals: int = FP_DECIMALS_DEFAULT):
        self.actions = tuple(actions)
        if not self.actions:
            raise StateTreeError("a state tree needs at least one action")
        self.game_hash = game_hash
        self.engine = engine
        self.world_seed = int(world_seed)
        self.bank_version = bank_version
        self.eps = float(eps)
        self.k_stuck = int(k_stuck)
        self.fp_decimals = int(fp_decimals)

        self._lock = threading.RLock()
        self._counter = 0                      # monotonic edge id (deterministic)
        self.episodes_replayed = 0             # budget: total episodes replayed
        self.ticks_simulated = 0               # budget: total physics ticks simulated

        root = Node(prefix=(), parent=None, action_from_parent=None, depth=0)
        self._nodes: dict = {(): root}

    # -- lookup ------------------------------------------------------------ #
    @property
    def root(self) -> Node:
        return self._nodes[()]

    def get(self, prefix) -> Optional[Node]:
        return self._nodes.get(tuple(prefix))

    def __contains__(self, prefix) -> bool:
        return tuple(prefix) in self._nodes

    def __len__(self) -> int:
        return len(self._nodes)

    def nodes(self) -> Iterator[Node]:
        """All nodes in a stable order (by depth, then prefix)."""
        return iter(self._ordered_nodes())

    def _ordered_nodes(self) -> list:
        return [self._nodes[k] for k in sorted(self._nodes, key=lambda p: (len(p), p))]

    def children(self, node: Node) -> list:
        return [self._nodes[c] for c in node.child_prefixes() if c in self._nodes]

    def child(self, node: Node, action: str) -> Optional[Node]:
        edge = node.edges.get(action)
        if edge is not None and edge.outcome == "child":
            return self._nodes.get(edge.child)
        return None

    # -- root / node realisation ------------------------------------------ #
    def init_root(self, episode: Optional[dict] = None, *, executor=None,
                  game_source=None) -> Node:
        """Give the root its fingerprint (the initial, zero-tick state).

        Pass an ``episode`` dict directly, or an ``executor`` + ``game_source`` to
        replay the empty prefix. Required before expanding the root (the no-effect
        rule needs the parent fingerprint)."""
        if episode is None:
            episode = self._replay(executor, game_source, (), max_ticks=0)
        with self._lock:
            self.episodes_replayed += 1
            self.ticks_simulated += int(episode.get("ticks", 0) or 0)
            root = self.root
            root.fingerprint = fingerprint(episode.get("final_snapshot", {}) or {},
                                           self.fp_decimals)
            root.checkpoints = dict(episode.get("checkpoints", {}) or {})
            root.result = episode.get("result")
            return root

    # -- expansion (single-lane convenience) ------------------------------ #
    def expand(self, node, action: str, episode: Optional[dict] = None, *,
               executor=None, game_source=None, max_ticks: Optional[int] = None,
               lane: Optional[str] = None) -> Expansion:
        """Expand ``(node, action)``: replay ``prefix + action`` and record it.

        Dedup guarantee: if the edge already exists this is a no-op returning the
        existing child (no replay, no budget). Otherwise the prefix is replayed
        through ``executor`` (unless ``episode`` is supplied) and recorded via
        :meth:`record`. ``node`` may be a ``Node`` or a prefix.
        """
        node = self._as_node(node)
        with self._lock:
            existing = node.edges.get(action)
            if existing is not None:
                return self._existing(node, existing)
            claimer = node._claims.get(action)
            if claimer is not None and claimer != lane:
                return Expansion("conflict", None, node)
        # Replay happens OUTSIDE the lock (physics is the caller's, and slow).
        if episode is None:
            episode = self._replay(executor, game_source, node.prefix, action,
                                   max_ticks=max_ticks)
        return self.record(node, action, episode, lane=lane)

    def record(self, node, action: str, episode: dict, *,
               lane: Optional[str] = None) -> Expansion:
        """Record an already-computed ``episode`` for ``(node, action)``.

        The executor-agnostic primitive: physics has already run, ``episode`` is
        its result dict (``result / ticks / checkpoints / final_snapshot``). Dedup
        and the no-effect / stuck rules live here. Increments the replay budget
        (a real episode was consumed), unless the edge already existed."""
        node = self._as_node(node)
        if action not in self.actions:
            raise StateTreeError(f"unknown action {action!r} (not in the action set)")
        with self._lock:
            existing = node.edges.get(action)
            if existing is not None:
                return self._existing(node, existing)          # dedup: no budget
            node._claims.pop(action, None)                     # clear any claim
            return self._record_locked(node, action, episode, lane)

    # -- async edge claiming ---------------------------------------------- #
    def claim(self, node, action: str, lane: str) -> bool:
        """Atomically claim ``(node, action)`` for ``lane``. One winner.

        Returns ``True`` if this lane won the claim (edge was neither tried nor
        already claimed), else ``False`` — parallel lanes never duplicate work by
        construction (design §"never the same action twice"). Follow a winning
        claim with :meth:`commit` (record the outcome) or :meth:`release` (abandon).
        """
        if action not in self.actions:
            raise StateTreeError(f"unknown action {action!r} (not in the action set)")
        node = self._as_node(node)
        with self._lock:
            if action in node.edges or action in node._claims:
                return False
            node._claims[action] = lane
            return True

    def release(self, node, action: str, lane: str) -> bool:
        """Abandon a claim held by ``lane`` (e.g. the worker failed) so the edge can
        be re-claimed. Returns ``True`` if a claim was released."""
        node = self._as_node(node)
        with self._lock:
            if node._claims.get(action) == lane:
                del node._claims[action]
                return True
            return False

    def commit(self, node, action: str, episode: dict, lane: str) -> Expansion:
        """Record the outcome of a claimed edge and clear the claim.

        ``lane`` must hold the claim (raises otherwise) — unless the edge was
        committed by someone else meanwhile, in which case the existing outcome is
        returned (dedup)."""
        node = self._as_node(node)
        with self._lock:
            existing = node.edges.get(action)
            if existing is not None:
                node._claims.pop(action, None)
                return self._existing(node, existing)
            if node._claims.get(action) != lane:
                raise StateTreeError(
                    f"lane {lane!r} does not hold the claim on {action!r}")
            del node._claims[action]
            return self._record_locked(node, action, episode, lane)

    def claimed(self, node, action: str) -> Optional[str]:
        """The lane currently claiming ``(node, action)``, or ``None``."""
        node = self._as_node(node)
        with self._lock:
            return node._claims.get(action)

    # -- frontier (restart-from-leaf) ------------------------------------- #
    def untried(self, node) -> list:
        """Actions never tried and not currently claimed at ``node`` (stable order)."""
        node = self._as_node(node)
        with self._lock:
            return [a for a in self.actions
                    if a not in node.edges and a not in node._claims]

    def is_frontier(self, node) -> bool:
        """``True`` if ``node`` is open (not terminal), realised, and has an untried
        action — a valid restart-from-leaf target."""
        node = self._as_node(node)
        return (node.status == OPEN and node.fingerprint is not None
                and bool(self.untried(node)))

    def frontier(self) -> list:
        """All frontier nodes in stable (depth, prefix) order."""
        with self._lock:
            return [n for n in self._ordered_nodes() if self.is_frontier(n)]

    def select_frontier(self, strategy: str = "uniform", *, rng=None) -> Optional[Node]:
        """Pick a frontier node to restart-from-leaf (the caller replays ``.prefix``).

        Strategies (design §"frontier"):
          * ``"uniform"``            — ``rng.choice`` if an RNG is given, else the
            first in stable order (deterministic default).
          * ``"most_checkpoints"``   — most checkpoints latched (then shallowest,
            then smallest prefix). Alias: ``"most-checkpoints-latched"``.
          * ``"deepest"``            — greatest depth (then smallest prefix).
        Returns ``None`` when the frontier is empty (the tree has saturated).
        """
        fr = self.frontier()
        if not fr:
            return None
        if strategy == "uniform":
            return rng.choice(fr) if rng is not None else fr[0]
        if strategy in ("most_checkpoints", "most-checkpoints-latched"):
            # fr is sorted (depth, prefix) ascending; max keeps the FIRST maximal,
            # so ties break to shallowest then smallest prefix — deterministic.
            return max(fr, key=lambda n: (n.n_latched(),))
        if strategy == "deepest":
            return max(fr, key=lambda n: (n.depth,))
        raise StateTreeError(f"unknown frontier strategy {strategy!r}")

    # -- budget ----------------------------------------------------------- #
    def budget(self) -> dict:
        """Accounting: total episodes replayed and physics ticks simulated."""
        with self._lock:
            return {"episodes": self.episodes_replayed,
                    "ticks": self.ticks_simulated, "nodes": len(self._nodes)}

    # ------------------------------------------------------------------ #
    # internals (assume the lock is held unless noted)
    # ------------------------------------------------------------------ #
    def _as_node(self, node) -> Node:
        if isinstance(node, Node):
            if self._nodes.get(node.prefix) is not node:
                raise StateTreeError("node does not belong to this tree")
            return node
        found = self._nodes.get(tuple(node))
        if found is None:
            raise StateTreeError(f"no node for prefix {tuple(node)!r}")
        return found

    def _existing(self, node: Node, edge: Edge) -> Expansion:
        if edge.outcome == "child":
            return Expansion("existing", self._nodes.get(edge.child), node)
        return Expansion("existing", None, node)               # existing no_effect

    def _record_locked(self, node: Node, action: str, episode: dict,
                       lane: Optional[str]) -> Expansion:
        if node.fingerprint is None:
            raise StateTreeError(
                "parent fingerprint unknown — init_root()/realise the node first")
        self.episodes_replayed += 1
        self.ticks_simulated += int(episode.get("ticks", 0) or 0)
        node.visits += 1
        self._counter += 1

        child_fp = fingerprint(episode.get("final_snapshot", {}) or {},
                               self.fp_decimals)
        delta = fp_delta(node.fingerprint, child_fp)

        # --- no-effect: an EDGE FACT, never a child (design §"the stuck rule") ---
        if delta < self.eps:
            node.edges[action] = Edge(action, "no_effect", None, self._counter)
            node.no_effect_streak += 1
            stuck = False
            if node.status == OPEN and node.no_effect_streak >= self.k_stuck:
                node.status = TERMINAL_STUCK
                stuck = True
            self._maybe_exhaust(node)
            return Expansion("no_effect", None, node, stuck=stuck)

        # --- real effect: create (or return existing) child node ---
        child_prefix = node.prefix + (action,)
        existing = self._nodes.get(child_prefix)
        if existing is not None:                               # defensive; unreachable
            node.edges[action] = Edge(action, "child", child_prefix, self._counter)
            return Expansion("existing", existing, node)
        result = episode.get("result")
        child = Node(
            prefix=child_prefix, parent=node.prefix, action_from_parent=action,
            depth=len(child_prefix), fingerprint=child_fp,
            checkpoints=dict(episode.get("checkpoints", {}) or {}),
            result=result, status=_RESULT_STATUS.get(result, OPEN),
            created_by=lane)
        self._nodes[child_prefix] = child
        node.edges[action] = Edge(action, "child", child_prefix, self._counter)
        node.no_effect_streak = 0                              # effect breaks the streak
        self._maybe_exhaust(node)
        return Expansion("created", child, node)

    def _maybe_exhaust(self, node: Node) -> None:
        """An open node with every action tried and none in-flight is exhausted."""
        if node.status != OPEN:
            return
        if all(a in node.edges for a in self.actions) and not node._claims:
            node.status = EXHAUSTED

    # ------------------------------------------------------------------ #
    # replay seam (the ONLY place an executor is touched)
    # ------------------------------------------------------------------ #
    def _replay(self, executor, game_source, prefix, action=None, *,
                max_ticks: Optional[int]) -> dict:
        """Replay ``prefix`` (optionally + ``action``) through the caller's executor.

        The tree runs NO physics: it just formats the batch spec and reads back the
        first episode dict. Raises if no executor was provided."""
        if executor is None:
            raise StateTreeError("expand/init_root need an episode or an executor")
        full = tuple(prefix) + ((action,) if action is not None else ())
        mt = max_ticks if max_ticks is not None else len(full)
        recs = executor.run_batch(
            game_source, [{"seed": self.world_seed, "actions": list(full)}], mt)
        if not recs:
            raise StateTreeError("executor returned no episode for the replay")
        return recs[0]

    # ================================================================== #
    # Serialization — versioned, stable ordering (runs/trees/ artifacts)
    # ================================================================== #
    def to_dict(self) -> dict:
        """A JSON-ready dict with stable ordering. In-flight claims are runtime-only
        and NOT serialised (a reload starts a fresh coordination epoch)."""
        with self._lock:
            return {
                "schema_version": SCHEMA_VERSION,
                "game_hash": self.game_hash,
                "engine": self.engine,
                "world_seed": self.world_seed,
                "bank_version": self.bank_version,
                "params": {"eps": self.eps, "k_stuck": self.k_stuck,
                           "fp_decimals": self.fp_decimals},
                "actions": list(self.actions),
                "counter": self._counter,
                "budget": {"episodes": self.episodes_replayed,
                           "ticks": self.ticks_simulated},
                "nodes": [self._node_to_dict(n) for n in self._ordered_nodes()],
            }

    @staticmethod
    def _node_to_dict(node: Node) -> dict:
        return {
            "prefix": list(node.prefix),
            "parent": list(node.parent) if node.parent is not None else None,
            "action_from_parent": node.action_from_parent,
            "depth": node.depth,
            "fingerprint": ([list(e) for e in node.fingerprint]
                            if node.fingerprint is not None else None),
            "checkpoints": dict(sorted(node.checkpoints.items())),
            "result": node.result,
            "status": node.status,
            "visits": node.visits,
            "no_effect_streak": node.no_effect_streak,
            "created_by": node.created_by,
            "edges": {a: {"outcome": e.outcome,
                          "child": list(e.child) if e.child is not None else None,
                          "tried_at": e.tried_at}
                      for a, e in sorted(node.edges.items())},
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.to_json())

    # -- load -------------------------------------------------------------- #
    @classmethod
    def from_dict(cls, data: dict) -> "StateTree":
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise StateTreeError(
                f"unsupported state-tree schema_version {version!r} "
                f"(this build reads {SCHEMA_VERSION})")
        params = data.get("params", {})
        tree = cls(
            data["actions"], game_hash=data.get("game_hash", ""),
            engine=data.get("engine", "py"),
            world_seed=int(data.get("world_seed", WORLD_SEED_DEFAULT)),
            bank_version=data.get("bank_version", ""),
            eps=float(params.get("eps", EPS_DEFAULT)),
            k_stuck=int(params.get("k_stuck", K_STUCK_DEFAULT)),
            fp_decimals=int(params.get("fp_decimals", FP_DECIMALS_DEFAULT)))
        tree._counter = int(data.get("counter", 0))
        budget = data.get("budget", {})
        tree.episodes_replayed = int(budget.get("episodes", 0))
        tree.ticks_simulated = int(budget.get("ticks", 0))
        tree._nodes = {}
        for nd in data.get("nodes", []):
            node = cls._node_from_dict(nd)
            tree._nodes[node.prefix] = node
        if () not in tree._nodes:                              # always keep a root
            tree._nodes[()] = Node(prefix=(), parent=None, depth=0)
        return tree

    @staticmethod
    def _node_from_dict(nd: dict) -> Node:
        fp = nd.get("fingerprint")
        fingerprint_val = (tuple(tuple(e) for e in fp) if fp is not None else None)
        edges = {a: Edge(action=a, outcome=e["outcome"],
                         child=tuple(e["child"]) if e.get("child") is not None else None,
                         tried_at=int(e.get("tried_at", 0)))
                 for a, e in (nd.get("edges") or {}).items()}
        return Node(
            prefix=tuple(nd["prefix"]),
            parent=tuple(nd["parent"]) if nd.get("parent") is not None else None,
            action_from_parent=nd.get("action_from_parent"),
            depth=int(nd.get("depth", len(nd["prefix"]))),
            fingerprint=fingerprint_val,
            checkpoints=dict(nd.get("checkpoints", {}) or {}),
            result=nd.get("result"),
            status=nd.get("status", OPEN),
            visits=int(nd.get("visits", 0)),
            no_effect_streak=int(nd.get("no_effect_streak", 0)),
            created_by=nd.get("created_by"),
            edges=edges)

    @classmethod
    def from_json(cls, text: str) -> "StateTree":
        return cls.from_dict(json.loads(text))

    @classmethod
    def load(cls, path: str) -> "StateTree":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_json(fh.read())
