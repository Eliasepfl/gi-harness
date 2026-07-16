"""GodotBatchVecEnv — an SB3 ``VecEnv`` over ONE headless-Godot serve process that
holds N in-scene game instances (the multi-CPU-per-game learner, Elias 2026-07-15).

WHY not SubprocVecEnv. Each :class:`harness.rl.godot_env.GodotServeEnv` already spawns
its OWN Godot serve subprocess + TCP socket; wrapping an SB3 Python worker (fork OR
spawn) around that breaks those sockets (confirmed BrokenPipe). SB3's DummyVecEnv is
correct but steps its N ``GodotServeEnv`` slots SEQUENTIALLY — one Godot process busy
at a time, N socket round-trips per vec-step, so N-1 cores idle.

WHAT this does instead (the godot_rl_agents in-scene idea, GODOT_RL_MERGE.md). ONE
serve process runs ``serve_game.gd`` with ``n_instances=N``: N independent copies of the
game, each in its OWN SubViewport physics world (seeded ``base_seed + i``), stepped
TOGETHER — a single ``await physics_frame`` advances all N worlds — and returned as ONE
batched frame over ONE socket. The per-tick engine loop and the round-trip are shared
across N worlds, so throughput scales far past the sequential DummyVecEnv (see
``tests/test_gd_rl.py::test_batch_vec_env_faster_than_dummy``).

CONTRACT PARITY. The obs vector (``build_obs_vector``), the discrete action indexing
(``actions[i]``), and the reward shaping (``R_CHECKPOINT`` per NEW latch + ``R_SUCCESS``
/``R_FAILURE`` terminal) are byte-for-byte those of ``GodotServeEnv.step`` — so the
trainer, its callback (``info["episode"]`` with ``success`` + ``n_latched``), and the
witness ORACLE are unchanged. SB3 autoreset is done per-instance in ``step_wait``: when
instance i's done fires, it is rebuilt in-engine at its FIXED seed ``base_seed + i`` (the
DummyVecEnv per-slot scheme), the terminal obs stashed in ``info["terminal_observation"]``.

This env is the TRAINING seam only; greedy/sampled EVAL + the certificate bridge still
run through the single-instance ``GodotServeEnv`` / ``GdExecutor``, so batched↔single
byte-identity is a determinism GUARANTEE we test, never a correctness dependency.

GDScript lane only. ``serve_game.gd`` is the batched host; the ``.spec.json`` /
``runner.gd`` (godot) lane is not yet batched — construction rejects a non-gdscript game
with a typed error, and ``certify`` only routes gdscript games here.
"""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time

import numpy as np

from harness.rl.env import (
    OBS_CLIP, R_CHECKPOINT, R_FAILURE, R_SUCCESS, HORIZON,
    DEFAULT_RAYS, OBS_PROFILES, RAYS_PROFILES,
    build_obs_vector, detect_dim, normalize_rays, obs_dim_for, rays_obs_width,
)
from harness.rl.godot_env import (
    CONNECT_TIMEOUT_S, DEFAULT_PORT_BASE, SERVE_TIMEOUT_S, SPAWN_RETRIES,
    SPAWN_RETRY_DELAY_S, GodotServeError, _recv_frame, _send_frame,
)
from harness.verify.gd_exec import parse_runtime_errors, read_stderr_delta


# --- One batched serve process (spawn/connect mirrors GodotServeEnv's seam) ----
# NB: this replicates GodotServeEnv's provision+bind+spawn+accept sequence rather than
# refactoring it, to keep the frozen single-instance path byte-for-byte untouched. The
# shared low-level plumbing (framing, port/spawn helpers, godot_exec argv builders) is
# reused; a DRY extraction of the spawn loop is a followup that would touch GodotServeEnv.
def _launch_batch_serve(port: int, exe: str, project: str,
                        connect_timeout_s: float):
    """Provision the project, bind the loopback listener, spawn the headless
    ``serve_game.gd`` (scrubbed env — it runs generated code) and accept its outbound
    connection. Returns ``(proc, conn, listener, log)``; raises ``GodotServeError`` on a
    port collision (``port_in_use``) or a runner that never connects (``stale``)."""
    from harness.verify.godot_exec import (
        _dotgodot_present, scrubbed_env, speedup_user_args, stepping_argv,
    )

    # One-time headless --import so res://.godot exists (effect-checked, not returncode).
    if not _dotgodot_present(project):
        for _ in range(2):
            try:
                subprocess.run([exe, "--headless", "--import", "--path", project],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=180.0)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                break
            if _dotgodot_present(project):
                break

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("127.0.0.1", port))
    except OSError as exc:
        listener.close()
        raise GodotServeError("port_in_use",
                              f"cannot bind batch serve port {port}: {exc}")
    listener.listen(1)

    from harness.verify.godot_exec import speedup_from_env
    speedup = speedup_from_env()
    argv = stepping_argv(exe, project, "res://serve_game.gd",
                         ["--serve", "--port=%d" % port,
                          *speedup_user_args(speedup)])
    child_env = scrubbed_env()
    last_log = ""
    for attempt in range(SPAWN_RETRIES):
        log = tempfile.TemporaryFile(mode="w+b")
        proc = subprocess.Popen(argv, stdout=log, stderr=log,
                                stdin=subprocess.DEVNULL, env=child_env)
        conn = _accept_with_liveness(listener, proc, connect_timeout_s)
        if conn is not None:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return proc, conn, listener, log
        # Died before connecting (transient pthread_create EAGAIN) -> reap + retry.
        try:
            log.seek(0)
            last_log = "GODOT LOG: " + log.read().decode("utf-8", "replace")[-2000:]
        except Exception:
            last_log = ""
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass
        log.close()
        if attempt + 1 < SPAWN_RETRIES:
            time.sleep(SPAWN_RETRY_DELAY_S * (attempt + 1))
    listener.close()
    raise GodotServeError(
        "stale", f"serve_game.gd did not connect within {connect_timeout_s}s after "
        f"{SPAWN_RETRIES} attempt(s) on port {port}\n{last_log}")


def _accept_with_liveness(listener, proc, connect_timeout_s: float):
    """Accept within the budget, polling process liveness so a startup crash returns
    None (retry) instead of blocking the whole deadline."""
    deadline = time.monotonic() + connect_timeout_s
    listener.settimeout(0.5)
    while time.monotonic() < deadline:
        try:
            conn, _addr = listener.accept()
            return conn
        except socket.timeout:
            if proc.poll() is not None:
                return None
    return None


# --- Lazy VecEnv subclass (stable_baselines3 is an OPTIONAL dep) --------------
_BATCH_CLS = None


def _batch_vec_env_cls():
    """Define (once) and return the ``VecEnv`` subclass. Built lazily so this module
    stays importable when stable_baselines3 is absent (the vendored/JS lanes)."""
    global _BATCH_CLS
    if _BATCH_CLS is not None:
        return _BATCH_CLS

    from gymnasium import spaces
    from stable_baselines3.common.vec_env.base_vec_env import VecEnv

    class _GodotBatchVecEnv(VecEnv):
        def __init__(self, game_path: str, n_instances: int, *,
                     port_base: int | None = None, port_offset: int = 0,
                     exe: str | None = None, project: str | None = None,
                     horizon: int = HORIZON, seed: int = 0,
                     timeout_s: float = SERVE_TIMEOUT_S,
                     connect_timeout_s: float = CONNECT_TIMEOUT_S,
                     rays: dict | None = None, obs_profile: str = "positions"):
            self._proc = None
            self._conn = None
            self._listener = None
            self._log = None
            self._log_offset = 0        # os.pread cursor for the runtime SCRIPT ERROR
                                        # delta (batched: interleaved -> run-level)
            self.runtime_errors: list[dict] = []
            if int(n_instances) < 1:
                raise ValueError(f"n_instances must be >= 1 (got {n_instances})")

            self.game_path = game_path
            self.horizon = int(horizon)
            self.timeout_s = float(timeout_s)
            self._base_seed = int(seed)
            # Obs profile + opt-in egocentric raycast obs (see harness.rl.godot_env).
            # "positions" (default) -> wire + obs byte-identical; the rays profiles cast an
            # egocentric fan/grid per instance. n_rays sized at _freeze_layout (dim-pinned).
            self._obs_profile = obs_profile if obs_profile in OBS_PROFILES else "positions"
            self._rays = (normalize_rays(rays or DEFAULT_RAYS)
                          if self._obs_profile in RAYS_PROFILES else None)
            self._n_ray_floats = 0      # n_rays * ray_stride; sized at _freeze_layout
            with open(game_path, "r", encoding="utf-8") as fh:
                self._source = fh.read()

            # Batched host is serve_game.gd (GDScript lane); the godot/spec runner.gd
            # serve is not batched -> reject a non-gdscript game with a typed error.
            from harness.verify.gameverify import detect_engine
            if detect_engine(game_path, self._source) != "gdscript":
                raise GodotServeError(
                    "protocol",
                    "GodotBatchVecEnv is the GDScript (serve_game.gd) lane only; the "
                    "godot/.spec.json runner.gd serve is not batched")

            from harness.verify.godot_exec import (
                default_godot_project, find_godot_exe, speedup_from_env,
            )
            try:
                self.speedup = speedup_from_env()
            except ValueError as exc:
                raise GodotServeError("bad_speedup", str(exc))

            if port_base is None:
                port_base = int(os.environ.get("GIP_PORT_BASE", DEFAULT_PORT_BASE))
            self.port = int(port_base) + int(port_offset)

            exe = exe or find_godot_exe()
            if not exe or not os.path.isfile(exe):
                raise GodotServeError(
                    "godot_missing",
                    f"Godot binary not found (set HARNESS_GODOT_EXE): {exe!r}")
            project = project or default_godot_project()
            host = os.path.join(project, "serve_game.gd")
            if not os.path.isfile(host):
                raise GodotServeError("host_missing",
                                      f"serve_game.gd not found at {host}")

            self._proc, self._conn, self._listener, self._log = _launch_batch_serve(
                self.port, exe, project, connect_timeout_s)

            n = int(n_instances)
            init_op = {"op": "init", "source": self._source,
                       "seed": self._base_seed, "base_seed": self._base_seed,
                       "n_instances": n, "horizon": self.horizon}
            if self._rays is not None:       # only when opted in (batched wire stays
                init_op["rays"] = self._rays  # byte-identical to the pre-rays init else)
            ready = self._exchange(init_op)
            if not ready.get("ok", False) or ready.get("error"):
                self.close()
                raise GodotServeError(
                    "init_failed",
                    f"batch serve init failed for {game_path}: {ready.get('error')}")

            self.actions = list(ready.get("actions") or [])
            self.title = ready.get("title") or os.path.basename(game_path)
            self.world_size = tuple(ready.get("world_size") or (800, 600))
            self._freeze_layout(ready)

            # Per-instance reward/episode bookkeeping (indexed 0..N-1).
            self._prev_latched = [set() for _ in range(n)]
            self._ep_return = [0.0] * n
            self._ep_len = [0] * n
            self._pending_action_strs = None
            # Per-instance raw pose snapshot ({name:{pos,vel,angle}}), kept fresh so the
            # stale-seek freeze test works on the PURE 'rays' profile (whose obs carries no
            # positions -> DETECT reads this snapshot). Mirrors GodotServeEnv.last_snapshot.
            self.last_snapshots = [{} for _ in range(n)]

            obs_space = spaces.Box(low=-OBS_CLIP, high=OBS_CLIP,
                                   shape=(self._obs_dim,), dtype=np.float32)
            act_space = spaces.Discrete(len(self.actions))
            super().__init__(n, obs_space, act_space)

        # -- layout --------------------------------------------------------
        def _freeze_layout(self, frame: dict) -> None:
            obs_state0 = (frame.get("obs_state") or [{}])[0] or {}
            controlled = [nm for nm, q in obs_state0.items() if q.get("controlled")]
            others = sorted(nm for nm in obs_state0 if nm not in controlled)
            self._body_order = list(controlled) + others
            cp0 = (frame.get("checkpoints") or [{}])[0] or {}
            self._cp_keys = list(cp0.keys())
            self._dim = detect_dim(obs_state0)          # 2D vs true-3D, then PINNED
            self._n_ray_floats = rays_obs_width(self._rays, self._dim)  # n_rays*ray_stride
            self._obs_dim = obs_dim_for(len(self._body_order), len(self._cp_keys),
                                        self._dim, self._obs_profile, self._n_ray_floats)

        def _rays_row(self, rays_frame, i):
            """Instance i's flat raycast list (length ``self._n_ray_floats``), or None when
            off. ``rays_frame`` is the frame's per-instance array-of-arrays (or None)."""
            if self._n_ray_floats <= 0:
                return None
            row = rays_frame[i] if isinstance(rays_frame, list) and i < len(rays_frame) else None
            if not isinstance(row, list):
                return [1.0] * self._n_ray_floats
            if len(row) < self._n_ray_floats:
                return list(row) + [1.0] * (self._n_ray_floats - len(row))
            return row[:self._n_ray_floats]

        def _obs_of(self, obs_state: dict, latched: dict, tick: int,
                    rays=None) -> np.ndarray:
            return build_obs_vector(obs_state or {}, latched or {}, self._body_order,
                                    self._cp_keys, self.world_size, int(tick),
                                    self.horizon, dim=self._dim, rays=rays,
                                    obs_profile=self._obs_profile)

        @staticmethod
        def _snapshot_of(obs_state: dict) -> dict:
            """One instance's per-body pose ({name:{pos,vel,angle}}) — the raw-snapshot
            path the stale-seek freeze test uses for the pure 'rays' profile."""
            obs = obs_state or {}
            return {n: {"pos": q.get("pos"), "vel": q.get("vel"),
                        "angle": q.get("angle")} for n, q in obs.items()}

        # -- wire ----------------------------------------------------------
        def _exchange(self, op: dict) -> dict:
            proc = self._proc
            if proc is not None and proc.poll() is not None:
                raise GodotServeError(
                    "dead", f"batch serve exited (code {proc.returncode})\n{self._read_log()}")
            deadline = time.monotonic() + self.timeout_s
            try:
                _send_frame(self._conn, op)
            except OSError as exc:
                raise GodotServeError("write_failed",
                                      f"batch serve send failed: {exc}\n{self._read_log()}")
            return _recv_frame(self._conn, deadline)

        def _read_log(self) -> str:
            log = self._log
            if log is None:
                return ""
            try:
                log.seek(0)
                return "GODOT LOG: " + log.read().decode("utf-8", "replace")[-2000:]
            except Exception:
                return ""

        def _runtime_error_delta(self) -> list[dict]:
            """Runtime/parse SCRIPT ERROR records emitted since the last step (an os.pread
            tee delta). N worlds share one process, so their stderr interleaves -> these
            are RUN-LEVEL diagnostics (no per-instance attribution). Monotonic offset ->
            no double-counting; clean steps return []."""
            text, self._log_offset = read_stderr_delta(self._log, self._log_offset)
            errs = parse_runtime_errors(text)
            if errs:
                self.runtime_errors.extend(errs)
            return errs

        # -- VecEnv API ----------------------------------------------------
        def reset(self):
            frame = self._exchange({"op": "reset",
                                    "instances": list(range(self.num_envs)),
                                    "base_seed": self._base_seed})
            obs = np.zeros((self.num_envs, self._obs_dim), dtype=np.float32)
            cps = frame["checkpoints"]
            obss = frame["obs_state"]
            ticks = frame["tick"]
            rays_frame = frame.get("rays")
            for i in range(self.num_envs):
                latched = cps[i] or {}
                self._prev_latched[i] = {k for k, v in latched.items() if v is not None}
                obs[i] = self._obs_of(obss[i], latched, ticks[i],
                                      rays=self._rays_row(rays_frame, i))
                self.last_snapshots[i] = self._snapshot_of(obss[i])
                self._ep_return[i] = 0.0
                self._ep_len[i] = 0
            return obs

        def reset_with_seeds(self, seeds):
            """Rebuild every instance at an EXPLICIT per-instance seed and return the
            stacked obs. Not part of SB3's VecEnv contract (``reset`` uses the fixed
            ``base_seed + i`` scheme) — it exists so isolation tests can seed all N
            worlds IDENTICALLY and prove they evolve without interfering."""
            seeds = [int(s) for s in seeds]
            if len(seeds) != self.num_envs:
                raise ValueError(f"expected {self.num_envs} seeds, got {len(seeds)}")
            frame = self._exchange({"op": "reset",
                                    "instances": list(range(self.num_envs)),
                                    "seeds": seeds, "base_seed": self._base_seed})
            obs = np.zeros((self.num_envs, self._obs_dim), dtype=np.float32)
            rays_frame = frame.get("rays")
            for i in range(self.num_envs):
                latched = frame["checkpoints"][i] or {}
                self._prev_latched[i] = {k for k, v in latched.items() if v is not None}
                obs[i] = self._obs_of(frame["obs_state"][i], latched, frame["tick"][i],
                                      rays=self._rays_row(rays_frame, i))
                self.last_snapshots[i] = self._snapshot_of(frame["obs_state"][i])
                self._ep_return[i] = 0.0
                self._ep_len[i] = 0
            return obs

        def step_async(self, actions) -> None:
            acts = np.asarray(actions).reshape(-1)
            self._pending_action_strs = [self.actions[int(a)] for a in acts]

        def step_wait(self):
            frame = self._exchange({"op": "act",
                                    "actions": self._pending_action_strs,
                                    "n_ticks": 1})
            n = self.num_envs
            obss = frame["obs_state"]
            cps = frame["checkpoints"]
            ticks = frame["tick"]
            results = frame["result"]
            dterm = frame["done_term"]
            dtrunc = frame["done_trunc"]
            rays_frame = frame.get("rays")

            obs = np.zeros((n, self._obs_dim), dtype=np.float32)
            rewards = np.zeros(n, dtype=np.float32)
            dones = np.zeros(n, dtype=bool)
            infos: list[dict] = [None] * n
            done_indices: list[int] = []

            for i in range(n):
                latched = cps[i] or {}
                latched_now = {k for k, v in latched.items() if v is not None}
                new_latches = len(latched_now - self._prev_latched[i])
                self._prev_latched[i] = latched_now
                result = results[i]
                terminated = bool(dterm[i])
                truncated = bool(dtrunc[i])
                reward = R_CHECKPOINT * new_latches
                if result == "success":
                    reward += R_SUCCESS
                elif result in ("failure", "error"):
                    reward += R_FAILURE
                rewards[i] = reward
                done = terminated or truncated
                dones[i] = done
                self._ep_return[i] += reward
                self._ep_len[i] += 1
                obs[i] = self._obs_of(obss[i], latched, ticks[i],
                                      rays=self._rays_row(rays_frame, i))
                self.last_snapshots[i] = self._snapshot_of(obss[i])

                info = {"result": result, "tick": int(ticks[i]),
                        "n_latched": len(latched_now),
                        "success": result == "success"}
                if truncated and not terminated:
                    info["TimeLimit.truncated"] = True
                if done:
                    # SB3 autoreset contract: terminal obs stashed; "episode" mimics
                    # the Monitor wrapper the DummyVecEnv slots carry (info_keywords
                    # success + n_latched — the trainer callback reads these).
                    info["terminal_observation"] = obs[i].copy()
                    info["episode"] = {"r": float(self._ep_return[i]),
                                       "l": int(self._ep_len[i]),
                                       "success": result == "success",
                                       "n_latched": len(latched_now)}
                    done_indices.append(i)
                infos[i] = info

            if done_indices:
                rframe = self._exchange({"op": "reset", "instances": done_indices,
                                         "base_seed": self._base_seed})
                r_obs = rframe["obs_state"]
                r_cps = rframe["checkpoints"]
                r_ticks = rframe["tick"]
                r_rays = rframe.get("rays")
                for i in done_indices:
                    latched = r_cps[i] or {}
                    self._prev_latched[i] = {k for k, v in latched.items()
                                             if v is not None}
                    obs[i] = self._obs_of(r_obs[i], latched, r_ticks[i],
                                          rays=self._rays_row(r_rays, i))
                    self.last_snapshots[i] = self._snapshot_of(r_obs[i])
                    self._ep_return[i] = 0.0
                    self._ep_len[i] = 0

            # Run-level runtime SCRIPT ERROR capture (interleaved N worlds); attach to
            # infos[0] only when present so a clean step's infos stay byte-identical.
            errs = self._runtime_error_delta()
            if errs and infos:
                infos[0]["runtime_errors"] = errs

            return obs, rewards, dones, infos

        def close(self) -> None:
            conn = getattr(self, "_conn", None)
            proc = getattr(self, "_proc", None)
            if conn is not None:
                try:
                    if proc is not None and proc.poll() is None:
                        _send_frame(conn, {"op": "close"})
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
                self._conn = None
            if proc is not None:
                try:
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                self._proc = None
            for attr in ("_listener", "_log"):
                obj = getattr(self, attr, None)
                if obj is not None:
                    try:
                        obj.close()
                    except Exception:
                        pass
                    setattr(self, attr, None)

        def __del__(self):
            try:
                self.close()
            except Exception:
                pass

        # -- required VecEnv plumbing --------------------------------------
        def seed(self, seed=None):
            if seed is not None:
                self._base_seed = int(seed)
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
                f"GodotBatchVecEnv has no per-env attribute {attr_name!r}")

        def set_attr(self, attr_name, value, indices=None):
            raise NotImplementedError("GodotBatchVecEnv.set_attr is not supported")

        def env_method(self, method_name, *method_args, indices=None, **method_kwargs):
            raise NotImplementedError("GodotBatchVecEnv.env_method is not supported")

    _BATCH_CLS = _GodotBatchVecEnv
    return _BATCH_CLS


def GodotBatchVecEnv(*args, **kwargs):
    """Construct the batched SB3 ``VecEnv`` (lazy so the module imports without sb3).

    ``GodotBatchVecEnv(game_path, n_instances, *, port_base=None, port_offset=0,
    seed=0, horizon=HORIZON, ...)`` — see the module docstring for the wire contract."""
    return _batch_vec_env_cls()(*args, **kwargs)
