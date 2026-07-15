"""GdExecutor — the GDScript (GameAPI) lane's episode executor, over the serve host.

The engine seam for ``engine == "gdscript"``: a drop-in sibling of ``JsExecutor`` /
``GodotExecutor`` (``run_check`` + ``run_batch`` + ``batched = True``) that drives a
generated ``.gd`` game through ``godotworld/serve_game.gd`` instead of interpreting a
data spec. ONE long-lived headless-Godot serve process backs the whole funnel:

    run_check(source)  -> the G0/G2 fact dict (parse gate + contract probe + t=0
                          purity probes), from the host's ``check`` op.
    run_batch(source, episodes, max_ticks, frames_every=0, escape_margin=None)
                       -> episode dicts (result/ticks/checkpoints/final_snapshot/…),
                          one ``reset``+``act`` round-trip per episode, byte-for-byte
                          the shape ``run_episode`` returns — so G1/G3 and the tree
                          solver eat them UNCHANGED.

Because the serve stepping mirrors the host's own semantics (act + K=6 physics frames
+ latch + terminal, at full %.17f precision), two runs of the same (seed, actions)
are byte-identical — the G1 two-run drift gate and the witness replay both hold.

WIRE + SPAWN reuse the serve-lane plumbing (``harness.rl.godot_env``): Python
binds/listens on loopback, the host connects out, 4-byte-BE length-prefixed UTF-8
JSON. SECURITY: the host is spawned with a SCRUBBED environment
(``godot_exec.scrubbed_env`` — no credentials reach the process running generated
code), and only ever AFTER the python-side banned-API scan has passed.
"""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time


def _parse_error_line(check_only_output: str) -> str:
    """The actionable line from a failed ``--check-only`` run. Godot prints
    ``SCRIPT ERROR: Parse Error: <msg>`` (and/or ``Failed to load script ...``); return
    the first such line so the G0 ``loads`` hint points the repair loop at the syntax
    fault, falling back to a compact tail if the shape is unexpected."""
    lines = [ln.strip() for ln in (check_only_output or "").splitlines() if ln.strip()]
    for ln in lines:
        low = ln.lower()
        if "parse error" in low or "script error" in low:
            return ln
    for ln in lines:
        if "error" in ln.lower():
            return ln
    tail = " ".join(lines[-3:])
    return ("parse/compile failed (--check-only): " + tail) if tail \
        else "parse/compile failed (--check-only)"


class GdExecutor:
    """Out-of-process executor spawning one ``serve_game.gd`` per instance and
    reusing it across ``run_check`` + every ``run_batch`` of the funnel run."""

    batched = True  # one process, one batch at a time (no early-stop) -> G3 batches

    def __init__(self, exe: str | None = None, project: str | None = None, *,
                 port_base: int | None = None, port_offset: int = 0,
                 timeout_s: float = 120.0, connect_timeout_s: float = 60.0):
        from harness.rl.godot_env import DEFAULT_PORT_BASE
        from harness.verify.godot_exec import default_godot_project, find_godot_exe
        self._exe = exe or find_godot_exe()
        self._project = project or default_godot_project()
        self.timeout_s = float(timeout_s)
        self.connect_timeout_s = float(connect_timeout_s)
        if port_base is None:
            port_base = int(os.environ.get("GIP_PORT_BASE", DEFAULT_PORT_BASE))
        self.port = int(port_base) + int(port_offset)

        self._listener = None
        self._conn = None
        self._proc = None
        self._log = None
        self._inited = False

    # -- lazy connect ------------------------------------------------------
    def _ensure_connected(self) -> None:
        from harness.rl.godot_env import SPAWN_RETRIES, SPAWN_RETRY_DELAY_S
        from harness.verify.executors import VerifyError
        from harness.verify.godot_exec import (
            scrubbed_env, speedup_from_env, speedup_user_args, stepping_argv,
        )
        if self._conn is not None:
            return
        if not self._exe or not os.path.isfile(self._exe):
            raise VerifyError("godot_missing",
                              f"Godot binary not found (set HARNESS_GODOT_EXE): {self._exe!r}")
        host = os.path.join(self._project, "serve_game.gd")
        if not os.path.isfile(host):
            raise VerifyError("gd_host_missing", f"serve_game.gd not found at {host}")
        try:
            speedup = speedup_from_env()
        except ValueError as exc:
            raise VerifyError("godot_bad_speedup", str(exc))
        self._provision()

        # Bind the listener FIRST so a port collision surfaces before any spawn.
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", self.port))
        except OSError as exc:
            listener.close()
            raise VerifyError("gd_port_in_use",
                              f"cannot bind serve port {self.port}: {exc}")
        listener.listen(1)
        self._listener = listener

        argv = stepping_argv(self._exe, self._project, "res://serve_game.gd",
                             ["--serve", "--port=%d" % self.port,
                              *speedup_user_args(speedup)])
        child_env = scrubbed_env()
        last_log = ""
        for attempt in range(SPAWN_RETRIES):
            self._log = tempfile.TemporaryFile(mode="w+b")
            self._proc = subprocess.Popen(argv, stdout=self._log, stderr=self._log,
                                          stdin=subprocess.DEVNULL, env=child_env)
            conn = self._accept()
            if conn is not None:
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self._conn = conn
                return
            last_log = self._read_log()
            self._reap()
            if attempt + 1 < SPAWN_RETRIES:
                time.sleep(SPAWN_RETRY_DELAY_S * (attempt + 1))
        self.close()
        raise VerifyError("gd_stale",
                          f"serve_game.gd did not connect on port {self.port}\n{last_log}")

    def _provision(self) -> None:
        """One-time ``--headless --import`` so ``res://.godot`` exists — the SERVE host
        (G1-G3) runs in the project and needs the import artifact on a fresh checkout.
        The PARSE gate no longer needs it: a duck-typed plain-Node game (no base class,
        no ``class_name`` to resolve) compiles STANDALONE via ``--check-only --script``
        (see ``run_check``), so there is no global-class-cache dependency. Verified by
        the ARTIFACT, never the returncode (GH #77508/#83449 lie), mirroring
        GodotExecutor."""
        from harness.verify.godot_exec import _dotgodot_present
        if _dotgodot_present(self._project):
            return
        for _ in range(2):
            try:
                subprocess.run(
                    [self._exe, "--headless", "--import", "--path", self._project],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180.0)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                break
            if _dotgodot_present(self._project):
                break

    def _accept(self):
        deadline = time.monotonic() + self.connect_timeout_s
        self._listener.settimeout(0.5)
        while time.monotonic() < deadline:
            try:
                conn, _addr = self._listener.accept()
                return conn
            except socket.timeout:
                if self._proc.poll() is not None:
                    return None
        return None

    def _reap(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass

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

    # -- exchange ----------------------------------------------------------
    def _exchange(self, op: dict) -> dict:
        from harness.rl.godot_env import GodotServeError, _recv_frame, _send_frame
        from harness.verify.executors import VerifyError
        proc = self._proc
        if proc is not None and proc.poll() is not None:
            raise VerifyError("gd_dead",
                              f"serve_game.gd exited (code {proc.returncode})\n{self._read_log()}")
        deadline = time.monotonic() + self.timeout_s
        try:
            _send_frame(self._conn, op)
            return _recv_frame(self._conn, deadline)
        except GodotServeError as exc:
            raise VerifyError("gd_" + exc.kind, f"{exc}\n{self._read_log()}")
        except OSError as exc:
            raise VerifyError("gd_write_failed", f"serve send failed: {exc}\n{self._read_log()}")

    # -- surface: run_check ------------------------------------------------
    def run_check(self, game_source) -> dict:
        """G0/G2 facts: a STANDALONE parse gate then the serve host's contract probe.

        The parse gate is a bare ``godot --headless --check-only --script <file>`` on the
        source alone (no ``--path``, no project): a duck-typed plain-Node game has NO base
        class to resolve, so the compile-check stands alone and ``rc == 0`` iff it parses
        (the old ``extends GameAPI`` Error-43 wall is gone by construction). If it fails we
        return the load failure WITHOUT ever spawning the serve host or running the code.
        On success the serve ``check`` op supplies the contract probe (``has_method`` over
        the required methods) + the t=0 facts; the standalone parse result is authoritative
        for ``facts["load"]``."""
        from harness.verify.executors import VerifyError
        load = self._check_only(game_source)
        if not load.get("ok"):
            return {"mode": "check", "scan": [], "load": load}
        self._ensure_connected()
        facts = self._exchange({"op": "check", "source": game_source})
        if facts.get("ok") is False and facts.get("error"):
            raise VerifyError("gd_check_fatal", str(facts["error"]))
        facts["load"] = load
        return facts

    def _check_only(self, game_source) -> dict:
        """Standalone GDScript parse gate. Writes the source to a temp ``.gd`` and runs
        ``godot --headless --check-only --script <abs path>`` under the SCRUBBED env
        (untrusted code; ``--check-only`` parses without executing). ``rc == 0`` -> parses;
        non-zero -> the Godot ``Parse Error`` line is surfaced as the load hint. Returns
        ``{"ok": bool, "error": str|None}`` in the shape ``run_g0_gd`` consumes."""
        from harness.verify.executors import VerifyError
        from harness.verify.godot_exec import scrubbed_env
        if not self._exe or not os.path.isfile(self._exe):
            raise VerifyError("godot_missing",
                              f"Godot binary not found (set HARNESS_GODOT_EXE): {self._exe!r}")
        fd, path = tempfile.mkstemp(suffix=".gd", prefix="gdcheck_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(str(game_source))
            try:
                proc = subprocess.run(
                    [self._exe, "--headless", "--check-only", "--script", path],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    env=scrubbed_env(), timeout=self.timeout_s)
            except FileNotFoundError as exc:
                raise VerifyError("godot_missing", str(exc))
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": "parse gate timed out (--check-only)"}
            if proc.returncode == 0:
                return {"ok": True, "error": None}
            out = (proc.stdout or b"").decode("utf-8", "replace")
            return {"ok": False, "error": _parse_error_line(out)}
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    # -- surface: run_batch ------------------------------------------------
    def run_batch(self, game_source, episodes, max_ticks, frames_every=0,
                  escape_margin=None) -> list[dict]:
        from harness.verify.executors import VerifyError
        self._ensure_connected()
        # Horizon disabled (a huge cap): the per-episode n_ticks bounds each run so
        # batch semantics match runner.gd's episode mode exactly (min(max_ticks, len)).
        if not self._inited:
            ready = self._exchange({"op": "init", "source": game_source,
                                    "seed": 0, "horizon": 100000000})
            if ready.get("ok") is False:
                raise VerifyError("gd_init_failed", str(ready.get("error")))
            self._inited = True

        max_ticks = int(max_ticks)
        out: list[dict] = []
        for ep in episodes:
            seed = int(ep.get("seed", 0))
            actions = list(ep.get("actions", []))
            self._exchange({"op": "reset", "seed": seed})
            n_ticks = min(max_ticks, len(actions))
            act_msg = {"op": "act", "actions": actions, "n_ticks": n_ticks}
            if escape_margin is not None:
                act_msg["escape_margin"] = float(escape_margin)
            # frames_every>0 -> the host emits a per-tick frame trail on the act
            # reply; 0 (default) leaves the wire byte-identical (no "frames" key).
            if frames_every and int(frames_every) > 0:
                act_msg["frames_every"] = int(frames_every)
            frame = self._exchange(act_msg)
            out.append(self._rec_from_frame(frame, actions, max_ticks, escape_margin))
        return out

    @staticmethod
    def _rec_from_frame(frame: dict, actions: list, max_ticks: int,
                        escape_margin) -> dict:
        obs = frame.get("obs_state") or {}
        snap = {name: {"pos": q.get("pos"), "vel": q.get("vel"),
                       "angle": q.get("angle")}
                for name, q in obs.items()}
        result = frame.get("result")
        ticks = int(frame.get("tick", 0))
        if result is None:
            # Not terminal -> classify like runner.gd's batch episode mode.
            result = "exhausted" if len(actions) < max_ticks else "budget"
        rec = {
            "result": result,
            "ticks": ticks,
            "checkpoints": dict(frame.get("checkpoints") or {}),
            "final_snapshot": snap,
            "actions": actions[:ticks],
            "world_size": list(frame.get("world_size") or (800, 600)),
            "error": frame.get("error"),
        }
        if escape_margin is not None:
            rec["nan"] = bool(frame.get("nan", False))
            rec["oob"] = list(frame.get("oob") or [])
        # Per-tick frame trail (only present when frames_every>0 was requested);
        # the host already shapes each as {tick, entities:{name: query}}.
        frames = frame.get("frames")
        if frames is not None:
            rec["frames"] = frames
        return rec

    # -- teardown ----------------------------------------------------------
    def close(self) -> None:
        from harness.rl.godot_env import _send_frame
        conn, proc = self._conn, self._proc
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
