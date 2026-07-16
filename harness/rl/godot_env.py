"""GodotServeEnv — a Gymnasium-style RL environment over a headless Godot
"serve" subprocess. The Godot-lane sibling of :class:`harness.rl.env.PlanckEnv`.

Same obs/action CONTRACT as PlanckEnv (flat per-body float vector + discrete
actions, seeded reset semantics) — it reuses ``build_obs_vector`` and the reward
constants verbatim — but the world lives in a headless Godot process. TWO game
dialects share the identical framed serve protocol (init/reset/act/close), and the
env auto-routes by :func:`harness.verify.gameverify.detect_engine` on the game path:

* ``engine == "godot"``    — a declarative ``.spec.json`` interpreted by the FROZEN
  ``godotworld/runner.gd`` (init key ``spec``; data, so the parent env is inherited).
* ``engine == "gdscript"`` — a generated ``.gd`` GameAPI game (a plain Node
  implementing build/act/state/checkpoints/is_success/is_failure/actions — see
  ``godotworld/GAME_API.md``) compiled + driven by ``godotworld/serve_game.gd``
  (init key ``source``). Because that host runs UNTRUSTED generated code, the child
  process is spawned under the SCRUBBED env (``godot_exec.scrubbed_env`` — no
  credential reaches it), exactly as :class:`harness.verify.gd_exec.GdExecutor` does.

One env owns ONE long-lived Godot process in serve mode: ``reset`` reseeds + rebuilds
the world, each ``step`` advances exactly ONE decision tick (act + K=6 physics steps +
latch + terminal checks — byte-identical to the host's episode loop). Because the
serve stepping mirrors the batch runner, a greedy action sequence recorded here
replays to success through the MATCHING batch executor — ``GodotExecutor.run_batch``
(godot) or ``GdExecutor.run_batch`` (gdscript) — the certificate bridge in
:mod:`harness.rl.certify`.

WIRE (INNER dual-dialect, GODOT_RL_AGENTS_CAPABILITIES.md §3): 4-byte
BIG-ENDIAN length prefix + UTF-8 JSON. Python **binds/listens** on loopback;
the runner **connects out** (the §3 inversion of the stdio sketch — stdout log
spam never corrupts the wire). Verbs, determinism-first, no script/eval/``call``:

    init  {spec, seed, horizon} -> handshake + first frame (seeded full rebuild)
    reset {seed}                -> reseed-on-reset, seeded full world rebuild
    act   {actions, n_ticks}    -> run n_ticks decision ticks synchronously
                                   -> {obs_state, checkpoints, done_term, done_trunc}
    close                       -> ack + quit

The runner speaks ``obs_state`` (mirrors PlanckEnv) and ``checkpoints``
(name -> latch-tick | null, mirrors ``runner.gd``'s batch field); this env
re-exports the latch map as ``latched`` in ``step``'s info so the trainer/witness
code (`ppo`, `sb3_trainer`, `certify`) is engine-agnostic.

PORT SAFETY (capabilities note §6.2): the listener binds ``port = port_base +
port_offset``, bind-checked with a clear typed error. ``port_base`` defaults to
the ``GIP_PORT_BASE`` env var (else :data:`DEFAULT_PORT_BASE`). On a Slurm array,
give each TASK a disjoint base — e.g. in the job script::

    export GIP_PORT_BASE=$(( 47000 + SLURM_ARRAY_TASK_ID * 64 ))

so task *t* owns ports ``[47000+64t, 47000+64t+63]``; ``port_offset`` (0,1,2,…)
then separates the concurrent in-task vec-env slots. ``SO_REUSEADDR`` is set for
TIME_WAIT hygiene but two LIVE listeners on the same address still collide — the
intended safety trip.

STALE deadline: every request/reply exchange carries a per-op read deadline;
a hung runner surfaces as :class:`GodotServeError` (``kind == "stale"``) rather
than a silent hang (inverting the shipped protocol's disabled recv timeout).
"""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import tempfile
import time

import numpy as np

from harness.rl.env import (
    OBS_CLIP, R_CHECKPOINT, R_FAILURE, R_SUCCESS, HORIZON,
    Box, Discrete, build_obs_vector, detect_dim, obs_dim_for,
)
from harness.verify.gd_exec import parse_runtime_errors, read_stderr_delta

# --- Constants ([eng.] = engineering choice) ---------------------------------
DEFAULT_PORT_BASE = 47000     # loopback serve base when GIP_PORT_BASE is unset [eng.]
SERVE_TIMEOUT_S = 60.0        # per-op (send+reply) read budget before STALE [eng.]
CONNECT_TIMEOUT_S = 60.0      # budget for the runner's outbound connect [eng.]
# A fresh headless Godot occasionally aborts on startup with pthread_create EAGAIN
# on a thread-saturated shared node (ulimit -u). That is transient, so respawn a few
# times (detecting a process that died before connecting) before declaring STALE. [eng.]
SPAWN_RETRIES = 6
SPAWN_RETRY_DELAY_S = 0.75
_MAX_FRAME = 16 * 1024 * 1024  # 16 MiB frame cap (matches runner.gd SERVE_MAX_FRAME)


class GodotServeError(RuntimeError):
    """A typed serve-lane failure. ``kind`` is one of ``port_in_use``,
    ``godot_missing``, ``host_missing``, ``stale``, ``closed``, ``protocol``,
    ``init_failed``, ``dead``, ``write_failed``, ``bad_speedup`` — so callers (and the
    STALE deadline path) can branch without string-matching."""

    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(f"{kind}: {message}")


# --- Framed wire I/O (module functions so they unit-test over a socketpair) ---
def _send_frame(sock: socket.socket, obj: dict) -> None:
    """Send one 4-byte-BE length-prefixed UTF-8 JSON frame."""
    body = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">I", len(body)) + body)


def _recv_exactly(sock: socket.socket, n: int, deadline: float) -> bytes:
    """Read exactly ``n`` bytes before ``deadline`` (a ``time.monotonic()`` stamp),
    else raise ``GodotServeError('stale')``. A clean peer close mid-frame raises
    ``GodotServeError('closed')``."""
    chunks: list[bytes] = []
    got = 0
    while got < n:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GodotServeError("stale", f"read timed out waiting for {n} bytes")
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(n - got)
        except socket.timeout:
            raise GodotServeError("stale", f"read timed out waiting for {n} bytes")
        if chunk == b"":
            raise GodotServeError("closed", "serve socket closed mid-frame")
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def _recv_frame(sock: socket.socket, deadline: float) -> dict:
    """Read one framed JSON reply before ``deadline``. Raises ``GodotServeError``
    (``stale``/``closed``/``protocol``) on any failure — never hangs."""
    header = _recv_exactly(sock, 4, deadline)
    (length,) = struct.unpack(">I", header)
    if length == 0 or length > _MAX_FRAME:
        raise GodotServeError("protocol", f"bad frame length {length}")
    body = _recv_exactly(sock, length, deadline)
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError as exc:
        raise GodotServeError("protocol", f"unparseable frame: {exc}")


class GodotServeEnv:
    """Gymnasium-style single env over one game's headless-Godot serve subprocess.

    API mirrors :class:`harness.rl.env.PlanckEnv`: ``reset(seed=0) -> (obs, info)``,
    ``step(action_idx) -> (obs, reward, terminated, truncated, info)``,
    ``observation_space`` / ``action_space`` (available after construction), and
    ``close()``. One Godot process is spawned per env and reused across episodes.
    """

    def __init__(self, game_path: str, *, port_base: int | None = None,
                 port_offset: int = 0, exe: str | None = None,
                 project: str | None = None, horizon: int = HORIZON,
                 timeout_s: float = SERVE_TIMEOUT_S,
                 connect_timeout_s: float = CONNECT_TIMEOUT_S):
        # Set teardown-relevant handles first so close() is safe on any early raise.
        self._listener = None
        self._conn = None
        self._proc = None
        self._log = None
        self._log_offset = 0            # os.pread cursor for the runtime SCRIPT ERROR
                                        # delta (never the shared write offset)
        self.runtime_errors: list[dict] = []

        # Resolve the game-tick SPEEDUP first (HARNESS_GODOT_SPEEDUP, default 1) so an
        # invalid value fails fast BEFORE binding a port or spawning Godot. The runner
        # scales physics_ticks_per_second AND time_scale by this so per-tick delta stays
        # 1/60 -- serve stepping stays byte-identical to the batch replay at any speedup.
        from harness.verify.godot_exec import speedup_from_env
        try:
            self.speedup = speedup_from_env()
        except ValueError as exc:
            raise GodotServeError("bad_speedup", str(exc))

        self.game_path = game_path
        self.horizon = int(horizon)
        self.timeout_s = float(timeout_s)
        with open(game_path, "r", encoding="utf-8") as fh:
            self._source = fh.read()

        # Engine dialect decides the serve HOST script, the init frame's source key,
        # and whether the child runs under a scrubbed env. 'gdscript' (.gd GameAPI game)
        # -> serve_game.gd, key "source", SCRUBBED (untrusted generated code, mirrors
        # GdExecutor); anything else keeps the original 'godot' spec behaviour byte for
        # byte -> runner.gd, key "spec", inherited env (declarative data, not code).
        from harness.verify.gameverify import detect_engine
        self.engine = detect_engine(game_path, self._source)
        if self.engine == "gdscript":
            self._host_rel = "res://serve_game.gd"
            self._init_key = "source"
            self._scrub = True
        else:
            self._host_rel = "res://runner.gd"
            self._init_key = "spec"
            self._scrub = False

        if port_base is None:
            port_base = int(os.environ.get("GIP_PORT_BASE", DEFAULT_PORT_BASE))
        self.port_base = int(port_base)
        self.port_offset = int(port_offset)
        self.port = self.port_base + self.port_offset

        # 1) Bind the listener FIRST — a port collision surfaces before any spawn.
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", self.port))
        except OSError as exc:
            listener.close()
            raise GodotServeError(
                "port_in_use",
                f"cannot bind serve port {self.port} "
                f"(GIP_PORT_BASE base {self.port_base} + offset {self.port_offset}): {exc}")
        listener.listen(1)
        self._listener = listener

        # 2) Locate + spawn the headless Godot serve process.
        from harness.verify.godot_exec import default_godot_project, find_godot_exe
        self._exe = exe or find_godot_exe()
        if not self._exe or not os.path.isfile(self._exe):
            self.close()
            raise GodotServeError(
                "godot_missing",
                f"Godot binary not found (set HARNESS_GODOT_EXE): {self._exe!r}")
        self._project = project or default_godot_project()
        # The chosen serve host must actually be present in the project (a fresh checkout
        # ships both runner.gd and serve_game.gd) — fail with a clear typed error rather
        # than a confusing spawn-timeout STALE if it is missing.
        self._host_path = os.path.join(self._project, os.path.basename(self._host_rel))
        if not os.path.isfile(self._host_path):
            self.close()
            raise GodotServeError(
                "host_missing",
                f"serve host {os.path.basename(self._host_rel)} not found at {self._host_path}")
        self._ensure_provisioned()

        # 3) Spawn the runner and accept its outbound connection (bounded -> STALE,
        #    never a hang; transient startup crashes are respawned).
        self._conn = self._spawn_and_accept(connect_timeout_s)

        # 4) init: load the game (spec/source per dialect) + seeded build at seed 0;
        #    freeze the obs layout from the priming frame.
        ready = self._exchange({"op": "init", self._init_key: self._source, "seed": 0,
                                "horizon": self.horizon})
        if not ready.get("ok", False) or ready.get("error"):
            self.close()
            raise GodotServeError(
                "init_failed",
                f"serve init failed for {game_path}: {ready.get('error')}")
        self.actions: list[str] = list(ready.get("actions") or [])
        self.title: str = ready.get("title") or os.path.basename(game_path)
        self.world_size = tuple(ready.get("world_size") or (800, 600))

        # Layout is frozen from the init frame (the priming seed-0 build).
        self._body_order: list[str] | None = None
        self._cp_keys: list[str] | None = None
        self._dim: int = 2                          # pinned in _freeze_layout (2 or 3)
        self.action_space = Discrete(len(self.actions))
        self.observation_space: Box | None = None
        self._freeze_layout(ready)

        self._tick = 0
        self._done = False
        self._prev_latched: set[str] = self._latched_set(ready)
        # Current world pose ({name:{pos,vel,angle}}); kept fresh on reset/step for the
        # inverse-value attacker's fingerprint trail (harness.rl.adversary). Read-only.
        self.last_snapshot: dict = self._snapshot_of(ready)

    # -- provisioning -----------------------------------------------------
    def _ensure_provisioned(self) -> None:
        """One-time headless ``--import`` so a fresh checkout loads ``res://.godot``
        (the same gotcha GodotExecutor handles). Idempotent; skipped once present.

        Confirms success by the EFFECT (``.godot`` appearing), never the import
        returncode (GH #77508/#83449 lie); retries once if the first import quit early."""
        from harness.verify.godot_exec import _dotgodot_present
        if _dotgodot_present(self._project):
            return
        for _ in range(2):
            try:
                subprocess.run(
                    [self._exe, "--headless", "--import", "--path", self._project],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180.0)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                break  # a real failure surfaces on the actual serve run below
            if _dotgodot_present(self._project):
                break  # artifact present -> import truly took (effect, not returncode)

    # -- spawn / connect --------------------------------------------------
    def _spawn_and_accept(self, connect_timeout_s: float):
        """Spawn the headless Godot serve process and accept its outbound loopback
        connection. Retries the spawn if the process dies before connecting (a
        transient pthread_create EAGAIN on a thread-saturated node); a still-alive
        process that misses the deadline surfaces as ``stale``."""
        # `--fixed-fps 60` (as the batch executor uses) decouples the main loop from
        # wall-clock so `await physics_frame` steps as fast as the CPU allows instead
        # of real-time 60 Hz — the difference between ~10 and hundreds of steps/s. Route
        # through the shared builder so the flag is GUARANTEED on the serve seam too
        # (GODOT_DOCS_MINING.md section 3: enforce, don't trust the caller).
        from harness.verify.godot_exec import (
            scrubbed_env, stepping_argv, speedup_user_args,
        )
        argv = stepping_argv(self._exe, self._project, self._host_rel,
                             ["--serve", "--port=%d" % self.port,
                              *speedup_user_args(self.speedup)])
        # The gdscript host runs UNTRUSTED generated code -> spawn under the scrubbed,
        # allow-listed env (no credential reachable), exactly as GdExecutor does. The
        # godot/spec host interprets data only, so it inherits the parent env (env=None).
        child_env = scrubbed_env() if self._scrub else None
        last_log = ""
        for attempt in range(SPAWN_RETRIES):
            self._log = tempfile.TemporaryFile(mode="w+b")
            self._log_offset = 0        # fresh tee per attempt -> reset the read cursor
            self._proc = subprocess.Popen(argv, stdout=self._log, stderr=self._log,
                                          stdin=subprocess.DEVNULL, env=child_env)
            conn = self._accept_with_liveness(connect_timeout_s)
            if conn is not None:
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                return conn
            # Process died (or timed out) before connecting -> reap and retry.
            last_log = self._read_log()
            self._reap_proc()
            if attempt + 1 < SPAWN_RETRIES:
                time.sleep(SPAWN_RETRY_DELAY_S * (attempt + 1))
        self.close()
        raise GodotServeError(
            "stale",
            f"Godot did not connect within {connect_timeout_s}s after "
            f"{SPAWN_RETRIES} attempt(s) on port {self.port}\n{last_log}")

    def _accept_with_liveness(self, connect_timeout_s: float):
        """Accept within ``connect_timeout_s``, polling process liveness so a startup
        crash fails fast (returns None) instead of blocking the whole budget."""
        deadline = time.monotonic() + connect_timeout_s
        self._listener.settimeout(0.5)
        while time.monotonic() < deadline:
            try:
                conn, _addr = self._listener.accept()
                return conn
            except socket.timeout:
                if self._proc.poll() is not None:
                    return None  # exited before connecting (transient crash)
        return None

    def _reap_proc(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass

    # -- process / socket I/O ---------------------------------------------
    def _exchange(self, op: dict) -> dict:
        """Send one op frame, read exactly one reply frame within the per-op STALE
        deadline. Raises ``GodotServeError`` if the runner died / the write failed /
        the deadline elapsed."""
        proc = self._proc
        if proc is not None and proc.poll() is not None:
            raise GodotServeError(
                "dead",
                f"serve process exited (code {proc.returncode})\n{self._read_log()}")
        deadline = time.monotonic() + self.timeout_s
        try:
            _send_frame(self._conn, op)
        except OSError as exc:
            raise GodotServeError(
                "write_failed", f"serve send failed: {exc}\n{self._read_log()}")
        return _recv_frame(self._conn, deadline)

    def _read_log(self) -> str:
        log = self._log
        if log is None:
            return ""
        try:
            log.seek(0)
            data = log.read()
            if isinstance(data, bytes):
                data = data.decode("utf-8", "replace")
            return "GODOT LOG: " + data[-2000:]
        except Exception:
            return ""

    def _runtime_error_delta(self) -> list[dict]:
        """Runtime/parse SCRIPT ERROR records emitted since the last step (an os.pread
        tee delta). Monotonic offset -> successive steps never double-count one crash;
        clean steps return []."""
        text, self._log_offset = read_stderr_delta(self._log, self._log_offset)
        errs = parse_runtime_errors(text)
        if errs:
            self.runtime_errors.extend(errs)
        return errs

    # -- layout / observation ---------------------------------------------
    def _freeze_layout(self, frame: dict) -> None:
        obs_state = frame.get("obs_state", {})
        controlled = [n for n, q in obs_state.items() if q.get("controlled")]
        others = sorted(n for n in obs_state if n not in controlled)
        # Controlled body first (LLM_RL_SYSTEMS §4.1), then the rest sorted by name.
        self._body_order = list(controlled) + others
        self._cp_keys = list((frame.get("checkpoints") or {}).keys())
        self._dim = detect_dim(obs_state)               # 2D vs true-3D, then PINNED
        obs_dim = obs_dim_for(len(self._body_order), len(self._cp_keys), self._dim)
        self.observation_space = Box(-OBS_CLIP, OBS_CLIP, (obs_dim,))

    def _observe(self, frame: dict) -> np.ndarray:
        # runner.gd's `checkpoints` map (name -> tick|null) is exactly the `latched`
        # shape build_obs_vector consumes (non-null -> latched one-hot bit).
        return build_obs_vector(
            frame.get("obs_state", {}), frame.get("checkpoints") or {},
            self._body_order, self._cp_keys, self.world_size, self._tick,
            self.horizon, dim=self._dim)

    @staticmethod
    def _latched_set(frame: dict) -> set[str]:
        return {k for k, v in (frame.get("checkpoints") or {}).items() if v is not None}

    @staticmethod
    def _snapshot_of(frame: dict) -> dict:
        """The frame's per-body pose in the shape ``statetree.fingerprint`` reads
        (``{name: {pos, vel, angle}}``) — the current world snapshot. Read-only; used
        by the inverse-value attacker (``harness.rl.adversary``) to fingerprint the
        steered rollout's state trail for its softlock DETECT window."""
        obs = frame.get("obs_state") or {}
        return {n: {"pos": q.get("pos"), "vel": q.get("vel"), "angle": q.get("angle")}
                for n, q in obs.items()}

    # -- Gymnasium API ----------------------------------------------------
    def reset(self, seed: int = 0):
        frame = self._exchange({"op": "reset", "seed": int(seed)})
        if self._body_order is None:
            self._freeze_layout(frame)
        self._tick = 0
        self._done = False
        self._prev_latched = self._latched_set(frame)
        self.last_snapshot = self._snapshot_of(frame)
        return self._observe(frame), {"latched": dict(frame.get("checkpoints") or {})}

    def step(self, action_idx: int):
        if self._done:
            raise RuntimeError("step() after episode end — call reset() first")
        action = self.actions[int(action_idx)]
        # One decision tick per step (n_ticks=1) — keeps per-step semantics identical
        # to the batch witness replay (one action per tick through run_batch).
        frame = self._exchange({"op": "act", "actions": [action], "n_ticks": 1})
        self._tick = int(frame.get("tick", self._tick + 1))
        result = frame.get("result")

        latched_now = self._latched_set(frame)
        new_latches = len(latched_now - self._prev_latched)
        self._prev_latched = latched_now
        self.last_snapshot = self._snapshot_of(frame)
        reward = R_CHECKPOINT * new_latches

        # term/trunc split comes straight off the wire (INNER dialect); the reward
        # bonus reads `result` (matching PlanckEnv's shaping exactly).
        terminated = bool(frame.get("done_term"))
        truncated = bool(frame.get("done_trunc"))
        if result == "success":
            reward += R_SUCCESS
        elif result in ("failure", "error"):
            reward += R_FAILURE
        self._done = terminated or truncated

        info = {
            "result": result,
            "tick": self._tick,
            "latched": dict(frame.get("checkpoints") or {}),
            "n_latched": len(latched_now),
            "success": result == "success",
        }
        # A runtime SCRIPT ERROR in the generated act() aborts the call silently (the
        # wire error stays null); attach the parsed cause so a crashing game is visible
        # rather than misreported. Only added when present -> clean steps are unchanged.
        errs = self._runtime_error_delta()
        if errs:
            info["runtime_errors"] = errs
        return self._observe(frame), float(reward), terminated, truncated, info

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
        listener = getattr(self, "_listener", None)
        if listener is not None:
            try:
                listener.close()
            except Exception:
                pass
            self._listener = None
        log = getattr(self, "_log", None)
        if log is not None:
            try:
                log.close()
            except Exception:
                pass
            self._log = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
