"""GodotShardVecEnv — an SB3 ``VecEnv`` of M INDEPENDENT :class:`GodotBatchVecEnv`
shards, stepped CONCURRENTLY, so ONE learner saturates MANY cores (Elias 2026-07-16:
"upgrade the budget ... ask for 32 cores per run — more budget and more steps/ticks").

WHY. :class:`harness.rl.godot_vec_env.GodotBatchVecEnv` is already the multi-CPU-per-game
seam: ONE headless-Godot ``serve_game.gd`` process holds K in-scene worlds over ONE socket,
stepped together in the engine tick loop — brilliant amortization (~8.8x over the sequential
DummyVecEnv, ``tests/test_gd_batch_vec.py::test_batch_vec_env_faster_than_dummy``). But a
SINGLE Godot process cannot use 32 cores: its engine loop + the socket collect are one
serialized pipeline. The unlock is SHARDING — run M such processes side by side.

WHAT. This composes (never reimplements) M ``GodotBatchVecEnv`` shards of K in-scene
instances each = ``M*K`` logical envs. ``step_async`` fans the ``M*K`` action vector out to
the M shards (each shard's ``step_async`` only stashes its K action strings — pure, no IO);
``step_wait`` drives the M shards' socket round-trips CONCURRENTLY on a thread pool and
collects the M batched replies in shard order. The round-trips are IO-bound (``socket.recv``
releases the GIL) and the M Godot processes compute on separate cores, so the M engine loops
genuinely overlap — GIL-friendly threads suffice, no subprocess/pickle dance.

DETERMINISM / SEED SCHEME. Shard i is seeded ``base_seed + i*K`` and holds instances
``base_seed + i*K + j`` (j in 0..K-1) — so global slot ``i*K + j`` gets seed
``base_seed + (i*K + j)``: the SAME per-slot fixed-seed scheme the single batch env uses,
extended across ``M*K`` slots. Results are gathered in shard order regardless of thread
completion order, and each shard is deterministic (fixed seed, deterministic game), so the
same ``(M, K, base_seed)`` yields BYTE-IDENTICAL rollouts run-to-run. In particular ``M == 1``
is byte-identical to a bare ``GodotBatchVecEnv(game_path, K, seed=base_seed)`` — the
regression pin (both asserted in ``tests/test_gd_shard_vec.py``).

PORT SAFETY. Each shard is one process = one loopback listener. Shard i binds
``port_base + port_offset_base + i*PORT_STRIDE`` (``port_base`` defaults to ``GIP_PORT_BASE``,
exactly as ``GodotServeEnv``/``GodotBatchVecEnv`` derive it), so the M shards occupy DISJOINT
strided sub-bands of the task's loopback band. The stride leaves headroom (a shard uses one
port today; the sub-band keeps bands disjoint for any future multi-port shard). On a Slurm
array give each task a disjoint ``GIP_PORT_BASE`` (see ``GodotServeEnv``'s note); within a
task the shard cluster owns ``[port_offset_base, port_offset_base + (M-1)*PORT_STRIDE]``.

GDScript lane only. Each shard is a ``GodotBatchVecEnv`` (the ``serve_game.gd`` batched host),
so a non-gdscript game is rejected by the shard constructor with the same typed error — this
class never widens the batched lane, it only replicates it M-fold.
"""

from __future__ import annotations

import os

from harness.rl.env import HORIZON
from harness.rl.godot_env import (
    CONNECT_TIMEOUT_S, DEFAULT_PORT_BASE, SERVE_TIMEOUT_S,
)
from harness.rl.godot_vec_env import GodotBatchVecEnv

# --- Constants ([eng.] = engineering choice) ---------------------------------
# Disjoint loopback sub-band per shard. A shard is ONE process = ONE port today, but a
# strided sub-band keeps shard port bands non-overlapping (and leaves headroom for a future
# multi-port shard). M shards need M*PORT_STRIDE ports inside the task's loopback band, so
# with the g3p_farm's 64-port-per-task band this comfortably fits M<=8 (8*8==64). [eng.]
PORT_STRIDE = 8
# One shard (one Godot serve process + K in-scene worlds) is served over one socket and one
# engine loop; the bench (notes/rl_agent/SHARDED_VEC_ENV.md) sizes ~8 cores per shard on the
# farm (num_shards*8 cores). Auto-sizing is ADVISORY (never mandatory) — the caller may pass
# any num_shards; this only caps a request to what the box can host. [eng.]
CORES_PER_SHARD_ESTIMATE = 8


# --- CPU-affinity pinning (the M=4 collapse fix) -----------------------------
# WHY. A headless Godot process sizes its WorkerThreadPool from the visible processor count;
# on a 448-core login node it can spawn ~host-count worker threads PER process. One process
# (M=1) is fine (the pool is mostly idle for a cheap game), but M concurrent processes then
# fight over the SAME cores in the SLURM cgroup — cross-process scheduler thrash that collapsed
# 4x8 to ~44 sps (25x SLOWER than 1x8) in the first bench (notes/rl_agent/SHARDED_VEC_ENV.md).
# FIX. Pin shard i's Godot process to a DISJOINT contiguous slice of the allocated cores (set
# the parent's affinity right before the child is spawned, so the child inherits the slice and
# — if Godot reads sched_getaffinity — sizes its pool to the slice too). Parent affinity is
# restored afterwards so the collector threads still float across all cores. Linux-only,
# timing-only (never changes rollout bytes), and skipped for M==1 (byte-identical batch path).
def _cpu_cores():
    """The cores this process may run on (the SLURM -c cgroup slice), or None if the platform
    has no ``sched_getaffinity`` (non-Linux) — in which case pinning is silently skipped."""
    if hasattr(os, "sched_getaffinity"):
        try:
            return sorted(os.sched_getaffinity(0))
        except OSError:
            return None
    return None


def _core_slices(cores, m):
    """Split ``cores`` into ``m`` DISJOINT contiguous slices (the last takes any remainder).
    Returns None when there are fewer cores than shards (cannot give each shard its own core),
    so the caller falls back to no pinning."""
    n = len(cores)
    per = n // m
    if per < 1:
        return None
    return [set(cores[i * per: n if i == m - 1 else (i + 1) * per]) for i in range(m)]


def plan_num_shards(requested: int, *, cpus: int | None = None,
                    cores_per_shard: int = CORES_PER_SHARD_ESTIMATE) -> int:
    """ADVISORY shard count: ``max(1, min(requested, (cpus-2)//cores_per_shard))`` — cap the
    requested shard count to what ``cpus`` cores can host (reserving 2 cores for the python
    collect loop + OS). ``cpus`` defaults to ``SLURM_CPUS_PER_TASK`` (else ``os.cpu_count()``).
    NEVER mandatory — the trainer/CLI use ``requested`` as-is unless the caller opts into this
    helper; it exists so a farm preset can auto-fit a box (see the notes' recommended presets).
    """
    if cpus is None:
        env_cpus = os.environ.get("SLURM_CPUS_PER_TASK")
        cpus = int(env_cpus) if env_cpus else (os.cpu_count() or 2)
    cores_per_shard = max(1, int(cores_per_shard))
    fit = (int(cpus) - 2) // cores_per_shard
    return max(1, min(int(requested), fit))


# --- Lazy VecEnv subclass (stable_baselines3 is an OPTIONAL dep) --------------
_SHARD_CLS = None


def _shard_vec_env_cls():
    """Define (once) and return the ``VecEnv`` subclass. Built lazily so this module stays
    importable when stable_baselines3 is absent (the vendored/JS lanes)."""
    global _SHARD_CLS
    if _SHARD_CLS is not None:
        return _SHARD_CLS

    from concurrent.futures import ThreadPoolExecutor

    import numpy as np
    from stable_baselines3.common.vec_env.base_vec_env import VecEnv

    class _GodotShardVecEnv(VecEnv):
        def __init__(self, game_path: str, num_shards: int, num_envs: int, *,
                     base_seed: int = 0, port_base: int | None = None,
                     port_offset_base: int = 0, port_stride: int = PORT_STRIDE,
                     exe: str | None = None, project: str | None = None,
                     horizon: int = HORIZON, timeout_s: float = SERVE_TIMEOUT_S,
                     connect_timeout_s: float = CONNECT_TIMEOUT_S,
                     pin_affinity: bool = True, shard_factory=None,
                     env_kwargs: dict | None = None):
            self._shards: list = []
            self._pool = None
            self.num_shards = int(num_shards)
            self.num_envs_per_shard = int(num_envs)
            if self.num_shards < 1:
                raise ValueError(f"num_shards must be >= 1 (got {num_shards})")
            if self.num_envs_per_shard < 1:
                raise ValueError(f"num_envs must be >= 1 (got {num_envs})")

            self.game_path = game_path
            self._base_seed = int(base_seed)
            self.port_stride = int(port_stride)
            self.port_offset_base = int(port_offset_base)
            if port_base is None:
                port_base = int(os.environ.get("GIP_PORT_BASE", DEFAULT_PORT_BASE))
            self.port_base = int(port_base)

            # CPU-affinity plan: pin shard i's Godot process to a disjoint core slice so M
            # concurrent processes do not thrash the cgroup (the M=4 collapse fix). Only for
            # real shards (M>1, Linux, enough cores); skipped when a test injects a fake
            # factory (no real process to pin) and for M==1 (byte-identical batch path).
            do_pin = (pin_affinity and self.num_shards > 1 and shard_factory is None)
            cores = _cpu_cores() if do_pin else None
            slices = _core_slices(cores, self.num_shards) if cores else None
            self.core_slices = slices           # None -> not pinned (reported in notes/logs)
            saved_affinity = set(cores) if slices else None

            # Shard factory is injectable (tests supply a fake); default is the real batched
            # in-scene serve env. Build shards SEQUENTIALLY (ordered) — construction order
            # does not affect rollout bytes (seeds are fixed) but keeps port binding orderly.
            factory = shard_factory or GodotBatchVecEnv
            K = self.num_envs_per_shard
            try:
                for i in range(self.num_shards):
                    # Set the PARENT affinity to shard i's slice right before the spawn so the
                    # child Godot inherits it (and sizes its worker pool to the slice if it
                    # reads sched_getaffinity); restored to all cores after the loop.
                    if slices is not None:
                        try:
                            os.sched_setaffinity(0, slices[i])
                        except OSError:
                            pass
                    # env_kwargs flows verbatim into every shard (e.g. rays/obs_profile);
                    # None keeps each shard byte-identical to a bare batch env.
                    shard = factory(
                        game_path, K,
                        port_base=self.port_base,
                        port_offset=self.port_offset_base + i * self.port_stride,
                        seed=self._base_seed + i * K,
                        exe=exe, project=project, horizon=horizon,
                        timeout_s=timeout_s, connect_timeout_s=connect_timeout_s,
                        **(env_kwargs or {}))
                    self._shards.append(shard)
            except BaseException:
                # A later shard failing must not leak the earlier shards' processes.
                self._close_shards()
                raise
            finally:
                # Restore the collector/parent to the full core set so step_wait's threads
                # float across all cores (each shard's Godot stays pinned to its own slice).
                if saved_affinity is not None:
                    try:
                        os.sched_setaffinity(0, saved_affinity)
                    except OSError:
                        pass

            s0 = self._shards[0]
            self.actions = list(getattr(s0, "actions", []))
            self.title = getattr(s0, "title", os.path.basename(game_path))
            self.world_size = getattr(s0, "world_size", (800, 600))
            # One worker per shard so all M socket round-trips overlap on step_wait.
            self._pool = ThreadPoolExecutor(max_workers=self.num_shards,
                                            thread_name_prefix="gd-shard")
            super().__init__(self.num_shards * K, s0.observation_space,
                             s0.action_space)

        # -- concurrency helper -------------------------------------------
        def _map_shards(self, fn):
            """Run ``fn(shard)`` on every shard CONCURRENTLY, returning results in shard
            order (deterministic regardless of thread completion order). A single worker
            (M==1) still goes through the pool — one submit — so the path is uniform."""
            futures = [self._pool.submit(fn, s) for s in self._shards]
            return [f.result() for f in futures]

        # -- VecEnv API ----------------------------------------------------
        def reset(self):
            K = self.num_envs_per_shard
            results = self._map_shards(lambda s: s.reset())
            obs = np.zeros((self.num_envs, results[0].shape[1]), dtype=np.float32)
            for i, r in enumerate(results):
                obs[i * K:(i + 1) * K] = r
            return obs

        def step_async(self, actions) -> None:
            acts = np.asarray(actions)
            # DISCRETE actions arrive as a flat (M*K,) index vector; CHORD (MultiBinary)
            # actions as (M*K, n_actions) rows. Flatten ONLY the discrete case so a
            # per-shard slice ``acts[i*K:(i+1)*K]`` yields K indices (discrete) or K rows
            # (chord) -- flattening the chord case would shred the per-key bits.
            if acts.ndim <= 1:
                acts = acts.reshape(-1)
            K = self.num_envs_per_shard
            # Pure fan-out: each shard's step_async only stashes its K action strings (no
            # IO), so slicing + dispatch here is cheap and needs no threads — the overlap
            # happens in step_wait, where the socket round-trips actually block.
            for i, shard in enumerate(self._shards):
                shard.step_async(acts[i * K:(i + 1) * K])

        def step_wait(self):
            K = self.num_envs_per_shard
            results = self._map_shards(lambda s: s.step_wait())
            obs = np.zeros((self.num_envs, results[0][0].shape[1]), dtype=np.float32)
            rewards = np.zeros(self.num_envs, dtype=np.float32)
            dones = np.zeros(self.num_envs, dtype=bool)
            infos: list = [None] * self.num_envs
            for i, (o, r, d, inf) in enumerate(results):
                lo = i * K
                obs[lo:lo + K] = o
                rewards[lo:lo + K] = r
                dones[lo:lo + K] = d
                infos[lo:lo + K] = inf
            return obs, rewards, dones, infos

        def close(self) -> None:
            self._close_shards()
            pool = getattr(self, "_pool", None)
            if pool is not None:
                try:
                    pool.shutdown(wait=True)
                except Exception:
                    pass
                self._pool = None

        def _close_shards(self) -> None:
            for shard in getattr(self, "_shards", []):
                try:
                    shard.close()
                except Exception:
                    pass
            self._shards = []

        def __del__(self):
            try:
                self.close()
            except Exception:
                pass

        # -- required VecEnv plumbing --------------------------------------
        def seed(self, seed=None):
            """Reseed the cluster: shard i -> ``seed + i*K`` (preserving the per-slot fixed
            seed scheme). Returns the flat list of per-slot seeds (base_seed + global slot)."""
            if seed is not None:
                self._base_seed = int(seed)
            K = self.num_envs_per_shard
            for i, shard in enumerate(self._shards):
                shard.seed(self._base_seed + i * K)
            return [self._base_seed + i for i in range(self.num_envs)]

        def env_is_wrapped(self, wrapper_class, indices=None):
            return [False] * self.num_envs

        def get_attr(self, attr_name, indices=None):
            n = self.num_envs
            if attr_name == "render_mode":
                return [None] * n
            if hasattr(self, attr_name):
                return [getattr(self, attr_name)] * n
            raise AttributeError(
                f"GodotShardVecEnv has no per-env attribute {attr_name!r}")

        def set_attr(self, attr_name, value, indices=None):
            raise NotImplementedError("GodotShardVecEnv.set_attr is not supported")

        def env_method(self, method_name, *method_args, indices=None, **method_kwargs):
            raise NotImplementedError("GodotShardVecEnv.env_method is not supported")

    _SHARD_CLS = _GodotShardVecEnv
    return _SHARD_CLS


def GodotShardVecEnv(*args, **kwargs):
    """Construct the sharded SB3 ``VecEnv`` (lazy so the module imports without sb3).

    ``GodotShardVecEnv(game_path, num_shards, num_envs, *, base_seed=0, port_base=None,
    port_offset_base=0, port_stride=PORT_STRIDE, pin_affinity=True, ...)`` — M independent
    ``GodotBatchVecEnv`` shards of K in-scene instances each = ``M*K`` logical envs, stepped
    concurrently. ``pin_affinity`` (Linux, M>1) confines each shard's Godot process to a
    disjoint core slice so M processes do not thrash the cgroup (the M=4-collapse fix; timing
    only, never rollout bytes). See the module docstring for the seed scheme, port bands, and
    the determinism guarantees."""
    return _shard_vec_env_cls()(*args, **kwargs)
