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
import re
import socket
import subprocess
import tempfile
import time

from harness.verify.chord import wire_actions

# ---------------------------------------------------------------------------
# Runtime SCRIPT ERROR capture (ADOPT #1, notes/engines/MCP_FEEDBACK_TOOLS.md).
#
# A generated GDScript game that PARSES but CRASHES AT RUNTIME (a null deref in
# act(), a build() that raises) is invisible to the repair loop today: GDScript
# has no catchable exceptions, so serve_game.gd's `_game.act()` / `.build()` calls
# silently abort and the wire keeps reporting `"error": null`; the funnel then sees
# a MISLEADING downstream symptom ("no controlled body", a dead action). The engine
# DOES print the truth to stderr, which all three spawners already tee to an
# anonymous tempfile — read only on fatal aborts, then discarded. These helpers mine
# that stream: a per-op DELTA read (never the whole log) that parses the SCRIPT ERROR
# blocks into structured records the funnel/feedback compiler can name.
#
# Godot 4.7 runtime block (verified in-image, gdscript://<hash>.gd == the in-memory
# game; its LINE numbers map 1:1 to the generated source):
#     SCRIPT ERROR: Invalid access to property or key 'position' on a base object ...
#               at: act (gdscript://-9223371885574610264.gd:6)
#               GDScript backtrace (most recent call first):
#                   [0] act (gdscript://-9223371885574610264.gd:6)
#                   [1] _initialize (res://serve_game.gd:456)
# A parse block (in-memory reload) instead reads `Parse Error: ...` at
# `GDScript::reload (...)`.
# ---------------------------------------------------------------------------
_SCRIPT_ERROR_RE = re.compile(r"SCRIPT ERROR:\s*(.*)")
# `at: <method> (<script>:<line>)` — script may be gdscript://… (colons in the URI)
# or res://…, so the path is matched greedily up to the FINAL ':<digits>)'.
_AT_RE = re.compile(r"\bat:\s*(?P<method>[^\s(]+)\s*\((?P<script>.+):(?P<line>\d+)\)")
# `[N] <method> (<script>:<line>)` backtrace frame.
_FRAME_RE = re.compile(r"\[\d+\]\s*(?P<method>[^\s(]+)\s*\((?P<script>.+):(?P<line>\d+)\)")
# Trailing " at: method (script:line)" that Godot sometimes folds onto the message line.
_AT_SUFFIX_RE = re.compile(r"\s*\bat:\s+\S+\s*\(.+:\d+\).*$")


def parse_runtime_errors(text: str, max_records: int = 8) -> list[dict]:
    """Extract Godot runtime/parse SCRIPT ERROR blocks from tee'd stderr text.

    Returns structured records ``{message, line, method, kind, count}`` — ``kind`` is
    ``"runtime"`` (a crash mid-call) or ``"parse"`` (an in-memory reload failure). The
    reported ``line`` is the GAME frame's line (the first ``gdscript://`` frame in the
    block, whose numbers map 1:1 to the generated .gd); if no game frame is present it
    falls back to the innermost ``at:`` frame. Deduped by (message, line) — the FIRST N
    unique blocks are kept, each with an occurrence ``count`` (a crash that fires every
    tick collapses to one record). PURE + deterministic: identical text -> identical
    records, so the same crash always yields the same record."""
    if not text:
        return []
    lines = text.splitlines()
    n = len(lines)
    heads = [(i, m.group(1).strip())
             for i, ln in enumerate(lines)
             for m in (_SCRIPT_ERROR_RE.search(ln),) if m]
    records: list[dict] = []
    seen: dict = {}
    for bi, (idx, raw_msg) in enumerate(heads):
        end = heads[bi + 1][0] if bi + 1 < len(heads) else n
        message = _AT_SUFFIX_RE.sub("", raw_msg).strip() or raw_msg
        frames = []  # (method, script, line) in appearance order within the block
        for k in range(idx, min(end, idx + 30)):
            fm = _AT_RE.search(lines[k]) or _FRAME_RE.search(lines[k])
            if fm:
                frames.append((fm.group("method"), fm.group("script"),
                               int(fm.group("line"))))
        game = next((f for f in frames if "gdscript://" in f[1]), None)
        chosen = game or (frames[0] if frames else None)
        method = chosen[0] if chosen else None
        line = chosen[2] if chosen else None
        kind = ("parse" if message.lower().startswith("parse error")
                or (method and "reload" in method.lower()) else "runtime")
        key = (message, line)
        if key in seen:
            records[seen[key]]["count"] += 1
            continue
        seen[key] = len(records)
        records.append({"message": message, "line": line, "method": method,
                        "kind": kind, "count": 1})
    return records[:max_records]


def read_stderr_delta(log, offset: int) -> tuple[str, int]:
    """Read the bytes appended to the tee'd ``log`` file since ``offset`` WITHOUT
    disturbing the shared write position, and return ``(text, new_offset)``.

    The child's stdout/stderr are ``dup2``'d onto ``log``'s open file description, so
    parent and child SHARE one kernel file offset — a plain ``seek(0)+read()`` (what
    ``_read_log`` does on the fatal path) would move that offset out from under the
    still-writing child. ``os.pread`` does a POSITIONED read that leaves the offset
    put; the current size (``fstat``) is the child's furthest sequential append, so
    ``[offset, size)`` is exactly the new stderr. Read-only + Python-side: zero effect
    on the wire, determinism, or a clean run (no SCRIPT ERROR -> empty parse)."""
    if log is None:
        return "", offset
    try:
        fd = log.fileno()
        size = os.fstat(fd).st_size
        if size <= offset:
            return "", offset
        data = os.pread(fd, size - offset, offset)
        return data.decode("utf-8", "replace"), size
    except Exception:
        return "", offset


def _runtime_error_summary(rec: dict) -> str:
    """A one-line ``<method>() crashed at line N: <message>`` from a parsed record —
    the human-readable reproducer the G0 build hint / runtime_error finding carry."""
    method = rec.get("method") or "a game method"
    line = rec.get("line")
    where = f"line {line}" if line is not None else "an unknown line"
    kind = "parse error" if rec.get("kind") == "parse" else "crashed"
    return f"{method}() {kind} at {where}: {rec.get('message') or 'runtime script error'}"


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


# --------------------------------------------------------------------------- #
# WARNING-as-error reclassification (2026-07-17 parser-friction lever, item 1).
#
# Godot 4.7's standalone ``--check-only`` runs GDScript with the type-inference/Variant
# WARNINGS promoted to hard errors, so a game whose only fault is e.g.
#   Parse Error: The variable type is being inferred from a Variant value, so it will be
#                typed as Variant. (Warning treated as error.)
# aborts the parse gate on att1 — even though the code is VALID GDScript. A warning is
# NEITHER a determinism nor a sandbox violation (both of those are enforced by the banned-
# API scanner + the pinned physics, never by a style/type warning), so it must not sink a
# G0 load. This reclassifier lets a WARNING-ONLY ``--check-only`` failure through; the
# companion project setting (godotworld/project.godot: gdscript/warnings/
# inference_on_variant + treat_warnings_as_errors=false) relaxes the SAME warning at the
# serve host, so the game the parse gate now admits also loads + runs there byte-identically.
#
# GROUND TRUTH (in-image probe, Godot v4.7.stable, 2026-07-17):
#   * ``var v := d.get("x")``  -> "... (Warning treated as error.)"   [BENIGN warning]
#   * ``var x := arr[0]``      -> "Cannot infer the type ... doesn't have a set type."
#                                                                     [GENUINE hard error]
#   * missing ``:``            -> "Unexpected \"Indent\" in class body." [GENUINE syntax]
# Only the FIRST carries the "(Warning treated as error.)" tag; the other two are real
# parse errors with no such tag. So keying on that exact tag makes exactly the benign
# warning class non-fatal while every genuine parse/type/syntax error stays fatal.
_WARNING_AS_ERROR_TAG = "(Warning treated as error.)"
_SCRIPT_ERROR_LINE_RE = re.compile(r"SCRIPT ERROR:\s*(.*)")


def classify_check_output(output: str) -> dict:
    """Turn a NON-zero ``--check-only`` run into a G0 load verdict.

    Returns ``{"ok": True, "error": None, "warnings": [...]}`` IFF EVERY primary
    ``SCRIPT ERROR:`` diagnostic Godot printed is a warning-treated-as-error escalation
    (the type-inference/Variant warning class); otherwise ``{"ok": False, "error": <line>}``
    with the genuine parse-error line. Conservative by construction: a single diagnostic
    WITHOUT the ``(Warning treated as error.)`` tag — a real syntax error, an unresolved
    identifier, a hard ``Cannot infer ... doesn't have a set type`` — keeps the whole load
    FATAL, so no real parse error is ever laundered into a pass. Only the ``SCRIPT ERROR:``
    diagnostic lines are inspected; the generic ``ERROR: Failed to load script ...``/``at:``
    follow-ups (mere consequences of the abort) are ignored."""
    diagnostics = [m.group(1).strip()
                   for ln in (output or "").splitlines()
                   for m in (_SCRIPT_ERROR_LINE_RE.search(ln),) if m]
    if diagnostics and all(_WARNING_AS_ERROR_TAG in d for d in diagnostics):
        return {"ok": True, "error": None,
                "warnings": [d.replace(_WARNING_AS_ERROR_TAG, "").strip()
                             for d in diagnostics]}
    return {"ok": False, "error": _parse_error_line(output)}


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
        self._log_offset = 0            # our stderr read cursor (os.pread delta, not the
                                        # shared write offset); advances monotonically so
                                        # successive ops never re-report an earlier crash
        self.runtime_errors: list[dict] = []   # accumulated across run_check + run_batch
        self._inited = False
        self._idle_inited = False        # True once init carried the allow_idle capability
                                         # (a Phase-2 demo with empty-chord IDLE ticks)
        self._verbs: list[str] = []      # the game's declared action verbs (its actions()),
                                         # stashed from the init reply -- the SAME signal the
                                         # RL env's self.actions reads (see declared_verbs)

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
            self._log_offset = 0        # fresh tee per attempt -> reset the read cursor
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

    def _capture_runtime_errors(self) -> list[dict]:
        """Parse the stderr emitted since the last capture (an ``os.pread`` delta on the
        tee'd fd — never the whole log, never the shared offset) and accumulate any
        runtime/parse SCRIPT ERROR records. Returns just this op's new records; empty on
        a clean op, so it is zero-overhead on healthy runs."""
        text, self._log_offset = read_stderr_delta(self._log, self._log_offset)
        errs = parse_runtime_errors(text)
        if errs:
            self.runtime_errors.extend(errs)
        return errs

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
        # SURFACE ON THE WIRE (python-side): the check op builds at seed 0 and probes
        # t=0; GDScript can't raise a catchable error, so serve_game.gd hardcodes
        # build.ok=true even when build() crashed at runtime. Read the tee delta: a
        # build-scoped crash must NOT report ok:true (it masquerades as "no controlled
        # body"); other-method crashes attach as a runtime_error finding.
        errs = self._capture_runtime_errors()
        if errs:
            facts["runtime_error"] = errs[0]
            facts["runtime_errors"] = errs
            build_crash = next((e for e in errs if (e.get("method") or "") == "build"),
                               None)
            if build_crash and (facts.get("build") or {}).get("ok"):
                facts["build"] = {"ok": False,
                                  "error": _runtime_error_summary(build_crash)}
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
            # A benign type-inference/Variant WARNING that Godot's strict --check-only
            # promoted to an error is not a real parse failure (nor a determinism/sandbox
            # one) — reclassify a warning-ONLY abort as a pass so the game reaches the
            # serve host, which relaxes the same warning via project.godot. Genuine
            # syntax/type errors keep ok=False. See classify_check_output.
            return classify_check_output(out)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    # -- surface: run_batch ------------------------------------------------
    @staticmethod
    def _episodes_have_idle(episodes) -> bool:
        """True IFF any episode carries an empty-chord IDLE tick (a ``[]``/``()`` action) --
        the Phase-2 press-nothing tick. Used to AUTO-enable the serve host's allow_idle
        capability so an idle demo replays, while a legacy batch (no empties) stays
        byte-identical (the capability key never touches the wire)."""
        for ep in (episodes or []):
            for a in ep.get("actions", []):
                if isinstance(a, (list, tuple)) and len(a) == 0:
                    return True
        return False

    def _ensure_inited(self, game_source, want_idle: bool) -> None:
        """Idempotently init the serve host (load the game + seed-0 build) and stash the
        game's declared action verbs (``self._verbs``) from the init reply -- the SAME
        ``actions()`` signal the RL env's ``self.actions`` reads. No-op when the host is
        already inited at the required idle capability, so the wire stays byte-identical
        to before for every existing caller (this only ADDS the verb stash)."""
        from harness.verify.executors import VerifyError
        self._ensure_connected()
        if (not self._inited) or (want_idle and not self._idle_inited):
            init_op = {"op": "init", "source": game_source,
                       "seed": 0, "horizon": 100000000}
            if want_idle:
                init_op["allow_idle"] = True
            ready = self._exchange(init_op)
            if ready.get("ok") is False:
                raise VerifyError("gd_init_failed", str(ready.get("error")))
            self._inited = True
            self._idle_inited = want_idle
            self._verbs = list(ready.get("actions") or [])

    def declared_verbs(self, game_source) -> list[str]:
        """The game's declared action verbs (its ``actions()``), read from the serve init
        reply -- the discrete action space a random policy samples over, and exactly the
        list the RL env pins as ``self.actions``. Inits the host on first use; cheap and
        idempotent thereafter."""
        self._ensure_inited(game_source, want_idle=False)
        return list(self._verbs)

    def run_batch(self, game_source, episodes, max_ticks, frames_every=0,
                  escape_margin=None, allow_idle: bool | None = None) -> list[dict]:
        self._ensure_connected()
        # PHASE-2 IDLE replay: a demo whose actions carry an empty chord [] is an IDLE
        # (press-nothing) tick, legal only when the serve host is initialised with the
        # allow_idle capability AND wire_actions is told allow_empty. `allow_idle=None`
        # (default) AUTO-DETECTS it from the episodes, so a legacy (no-empty) batch stays
        # byte-identical (no allow_idle key on the wire) and an idle demo just replays.
        want_idle = (self._episodes_have_idle(episodes) if allow_idle is None
                     else bool(allow_idle))
        # Horizon disabled (a huge cap): the per-episode n_ticks bounds each run so
        # batch semantics match runner.gd's episode mode exactly (min(max_ticks, len)).
        # Re-init if we now need the idle capability but the live host was inited without it.
        self._ensure_inited(game_source, want_idle)

        max_ticks = int(max_ticks)
        out: list[dict] = []
        for ep in episodes:
            seed = int(ep.get("seed", 0))
            # Single boundary: canonicalize each action to its wire form. A single
            # verb str stays a str (byte-identical to pre-chord batches); a chord
            # (list of verbs) is validated + sorted; None noop ticks pass through; an
            # empty chord [] is the idle tick, kept only when allow_empty (want_idle).
            actions = wire_actions(ep.get("actions", []), allow_empty=want_idle)
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
            rec = self._rec_from_frame(frame, actions, max_ticks, escape_margin)
            # Per-episode runtime crash capture: an act()/build() that raised aborted the
            # call silently (the wire error stays null), so mine the tee delta and attach
            # the real cause. Monotonic offset -> episode i never re-reports episode i-1's
            # crash. Clean episodes add nothing (rec stays byte-identical to before).
            errs = self._capture_runtime_errors()
            if errs:
                rec["runtime_error"] = errs[0]
                rec["runtime_errors"] = errs
                if not rec.get("error"):
                    rec["error"] = _runtime_error_summary(errs[0])
            out.append(rec)
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
            "done_trunc": bool(frame.get("done_trunc", False)),
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
