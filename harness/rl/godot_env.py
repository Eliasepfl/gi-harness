"""GodotServeEnv — a Gymnasium-style RL environment over a headless Godot
"serve" subprocess.

It follows the shared RL obs/action CONTRACT (flat per-body float vector + discrete
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

The runner speaks ``obs_state`` (the shared obs contract) and ``checkpoints``
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
    OBS_CLIP, HORIZON,
    DEFAULT_RAYS, OBS_PROFILES, RAYS_PROFILES,
    Box, Discrete, MultiBinary, build_obs_vector, detect_dim, normalize_rays,
    obs_dim_for, rays_obs_width, step_reward,
)
from harness.verify.chord import chord_from_mask
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

    API follows the shared RL env contract: ``reset(seed=0) -> (obs, info)``,
    ``step(action_idx) -> (obs, reward, terminated, truncated, info)``,
    ``observation_space`` / ``action_space`` (available after construction), and
    ``close()``. One Godot process is spawned per env and reused across episodes.
    """

    def __init__(self, game_path: str, *, port_base: int | None = None,
                 port_offset: int = 0, exe: str | None = None,
                 project: str | None = None, horizon: int = HORIZON,
                 timeout_s: float = SERVE_TIMEOUT_S,
                 connect_timeout_s: float = CONNECT_TIMEOUT_S,
                 rays: dict | None = None, obs_profile: str = "positions",
                 chord_mode: bool = False, allow_idle: bool | None = None,
                 ban_contradictions: bool = True, oppose_pairs=None):
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
        # CHORD mode (Phase 2, opt-in). OFF (default) -> Discrete action space + a single verb
        # string per tick on the wire: byte-identical to every pre-chord run. ON -> a
        # MultiBinary(n_actions) space; step() maps the 0/1 vector to the sorted chord wire
        # form (a lone pressed key stays a plain str -> the legacy singleton wire is preserved).
        # allow_idle is the serve-init capability that legalises the all-keys-off IDLE tick
        # (empty chord []): default None -> the chord env turns it ON (General Intuition's own
        # controller can output all-keys-off, so parity argues for idle; STAKES/game pressure,
        # not the action-space shape, is what punishes idling). Idle is meaningless without
        # chords, so it is force-OFF outside chord mode -> the discrete wire stays untouched.
        self.chord_mode = bool(chord_mode)
        if allow_idle is None:
            allow_idle = self.chord_mode
        self.allow_idle = self.chord_mode and bool(allow_idle)
        # Obs profile (Elias 2026-07-16): "positions" (default; byte-identical to today) |
        # "positions+rays" | "rays" (pure, proprioception-only + rays). The two rays
        # profiles cast an egocentric fan/grid (the examples' RaycastSensor pattern, no
        # pixels); rays config fills DEFAULT_RAYS so the host and obs sizer agree. n_rays is
        # sized at _freeze_layout, once the game's dimension is pinned.
        self._obs_profile = obs_profile if obs_profile in OBS_PROFILES else "positions"
        self._rays = (normalize_rays(rays or DEFAULT_RAYS)
                      if self._obs_profile in RAYS_PROFILES else None)
        self._n_ray_floats = 0          # n_rays * ray_stride; sized at _freeze_layout
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
        init_op = {"op": "init", self._init_key: self._source, "seed": 0,
                   "horizon": self.horizon}
        if self._rays is not None:       # only include the key when opted in (wire stays
            init_op["rays"] = self._rays  # byte-identical to the pre-rays init otherwise)
        if self.allow_idle:              # opt-in capability: legalise the empty-chord IDLE
            init_op["allow_idle"] = True  # tick (absent -> host rejects [] as a protocol error)
        ready = self._exchange(init_op)
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
        # MultiBinary(n) in chord mode (per-key Bernoulli policy); Discrete(n) otherwise --
        # both expose ``.n`` (the declared-verb count) uniformly.
        self.action_space = (MultiBinary(len(self.actions)) if self.chord_mode
                             else Discrete(len(self.actions)))
        self.observation_space: Box | None = None
        self._freeze_layout(ready)

        self._tick = 0
        self._done = False
        self._prev_latched: set[str] = self._latched_set(ready)
        # Current world pose ({name:{pos,vel,angle}}); kept fresh on reset/step for the
        # inverse-value attacker's fingerprint trail (harness.rl.adversary). Read-only.
        self.last_snapshot: dict = self._snapshot_of(ready)

        # CONTRADICTORY-CHORD projection (Phase 2, Elias). In chord mode, mechanically probe
        # each action's effect vector on the controlled body and record near-antiparallel pairs
        # (chord_probe.antiparallel_pairs) so a both-pressed self-cancelling combo projects to
        # NEITHER key in the mask->wire mapping (see chord_from_mask/oppose_pairs). Opposition is
        # derived from MEASURED physics, NEVER action names. Explicit `oppose_pairs` (from
        # g3_prime's ONE shared probe) skips the per-env probe; ban_contradictions=False or a
        # non-chord env -> no pairs (byte-identical to before).
        self.ban_contradictions = bool(ban_contradictions)
        if oppose_pairs is not None:
            self.oppose_pairs = [tuple(p) for p in oppose_pairs]
        elif self.chord_mode and self.ban_contradictions:
            self.oppose_pairs = self._discover_opposition()
        else:
            self.oppose_pairs = []

    # -- contradictory-chord probe / discovery ----------------------------
    def probe_effect_vectors(self, *, k_ticks: int = 8, seed: int = 0) -> list:
        """Measure each action's EFFECT VECTOR: from a fresh reset at ``seed``, apply the
        action ALONE for ``k_ticks`` decision ticks and record the controlled body's net
        displacement (one 3-vector per action). Bypasses ``step``/the chord projection (raw
        single-verb wire), so it is safe to call before ``oppose_pairs`` exists. It leaves the
        serve world dirty -- the caller restores a clean build (``_discover_opposition`` does)."""
        from harness.rl.env import _controlled_pos
        vecs = []
        for verb in self.actions:
            f0 = self._exchange({"op": "reset", "seed": int(seed)})
            p0 = np.asarray(_controlled_pos(f0.get("obs_state", {}), self._body_order),
                            dtype=float)
            fn = self._exchange({"op": "act", "actions": [verb], "n_ticks": int(k_ticks)})
            p1 = np.asarray(_controlled_pos(fn.get("obs_state", {}), self._body_order),
                            dtype=float)
            vecs.append(p1 - p0)
        return vecs

    def _discover_opposition(self, *, k_ticks: int = 8, seed: int = 0) -> list:
        """Probe effect vectors and return the mechanically-discovered near-antiparallel
        action-index pairs, then RESTORE a clean seed-``seed`` build (the probe stepped the
        world). Names are never consulted -- only the measured physics."""
        from harness.rl.chord_probe import antiparallel_pairs
        vecs = self.probe_effect_vectors(k_ticks=k_ticks, seed=seed)
        pairs = antiparallel_pairs(vecs)
        self.reset(seed=int(seed))              # discard the probe's dirty world
        return pairs

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
        self._n_ray_floats = rays_obs_width(self._rays, self._dim)  # n_rays * ray_stride
        obs_dim = obs_dim_for(len(self._body_order), len(self._cp_keys), self._dim,
                              self._obs_profile, self._n_ray_floats)
        self.observation_space = Box(-OBS_CLIP, OBS_CLIP, (obs_dim,))

    def _frame_rays(self, frame: dict):
        """The frame's flat egocentric raycast list (length ``self._n_ray_floats`` =
        n_rays*stride, per ray: distance [+ class one-hot]), or None when rays are off
        (obs stays the no-rays vector). Defends against a short/missing array by padding
        with 1.0 (nothing seen) so the frozen obs width always holds."""
        if self._n_ray_floats <= 0:
            return None
        r = frame.get("rays")
        if not isinstance(r, list):
            return [1.0] * self._n_ray_floats
        if len(r) < self._n_ray_floats:
            return list(r) + [1.0] * (self._n_ray_floats - len(r))
        return r[:self._n_ray_floats]

    def _observe(self, frame: dict) -> np.ndarray:
        # runner.gd's `checkpoints` map (name -> tick|null) is exactly the `latched`
        # shape build_obs_vector consumes (non-null -> latched one-hot bit).
        return build_obs_vector(
            frame.get("obs_state", {}), frame.get("checkpoints") or {},
            self._body_order, self._cp_keys, self.world_size, self._tick,
            self.horizon, dim=self._dim, rays=self._frame_rays(frame),
            obs_profile=self._obs_profile)

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

    def step(self, action):
        if self._done:
            raise RuntimeError("step() after episode end — call reset() first")
        # CHORD: `action` is a MultiBinary 0/1 vector -> the sorted chord wire form (a lone
        # pressed key stays a plain str; all-keys-off -> [] idle when allow_idle). A both-pressed
        # measured-antiparallel pair projects to neither (oppose_pairs). DISCRETE: `action` is an
        # index -> the single verb string (byte-identical to the pre-chord wire).
        if self.chord_mode:
            wire = chord_from_mask(action, self.actions, allow_empty=self.allow_idle,
                                   oppose_pairs=self.oppose_pairs)
        else:
            wire = self.actions[int(action)]
        # One decision tick per step (n_ticks=1) — keeps per-step semantics identical
        # to the batch witness replay (one action per tick through run_batch).
        frame = self._exchange({"op": "act", "actions": [wire], "n_ticks": 1})
        self._tick = int(frame.get("tick", self._tick + 1))
        result = frame.get("result")

        latched_now = self._latched_set(frame)
        c_before = len(self._prev_latched)
        c_after = len(latched_now)
        self._prev_latched = latched_now
        self.last_snapshot = self._snapshot_of(frame)

        # term/trunc split comes straight off the wire (INNER dialect); the realigned
        # reward reads `result`/`tick` (single source of truth: PBRS shaping + per-tick living
        # cost + time-decayed terminal — the single shared step_reward).
        terminated = bool(frame.get("done_term"))
        truncated = bool(frame.get("done_trunc"))
        reward = step_reward(c_before, c_after, len(self._cp_keys or []), result,
                             self._tick, self.horizon)
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

    def serve_replay(self, action_names):
        """Backplay WARMSTART FAST PATH: replay a whole action-name prefix in ONE serve
        round-trip (``n_ticks=len``), reusing serve_game.gd's multi-action ``act`` — the SAME
        in-engine stepping a single-action ``act`` uses, so the post-prefix state is
        byte-identical to replaying the prefix one :meth:`step` at a time. Profiled 4.3x
        faster on station's 199-action prefix (339 ms -> 79 ms/reset: the 199 TCP round-trips
        collapse to 1; the in-engine physics is unchanged). This is what makes the
        single-instance warmstart lane usable — the per-reset replay was the throughput sink.

        Applies each name in order, STOPPING at the first name not in the action vocab
        (mirrors the generic per-step replay's early-break). Advances tick/latched/done to the
        post-prefix state and returns ``(obs, info, terminated)``. Reward is deliberately NOT
        computed: the learner never sees the replayed transitions — they only SEED the episode,
        and control is handed off after. Returns ``None`` (having sent NOTHING) when no valid
        prefix action is present, so the caller can fall back to the generic path cleanly."""
        if self._done:
            raise RuntimeError("serve_replay() after episode end — call reset() first")
        wires = []
        for a in action_names:
            if a in self.actions:
                wires.append(a)
            else:
                break                       # unknown action -> stop (matches generic replay)
        if not wires:
            return None                     # nothing valid to replay -> caller falls back
        frame = self._exchange({"op": "act", "actions": wires, "n_ticks": len(wires)})
        self._tick = int(frame.get("tick", self._tick))
        latched_now = self._latched_set(frame)
        self._prev_latched = latched_now
        self.last_snapshot = self._snapshot_of(frame)
        terminated = bool(frame.get("done_term"))
        truncated = bool(frame.get("done_trunc"))
        self._done = terminated or truncated
        info = {
            "result": frame.get("result"),
            "tick": self._tick,
            "latched": dict(frame.get("checkpoints") or {}),
            "n_latched": len(latched_now),
            "success": frame.get("result") == "success",
        }
        return self._observe(frame), info, (terminated or truncated)

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
