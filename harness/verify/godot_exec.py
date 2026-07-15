"""GodotExecutor — the THIRD engine seam (Godot Physics 2D), twin of JsExecutor.

The Godot lane's per-game artifact is a declarative JSON game-spec (``godotworld/
SPEC.md``); a single FROZEN ``godotworld/runner.gd`` interprets it. This executor is
the Python side of that seam: it batches a whole verification layer's episodes into
ONE headless Godot process (amortising the ~0.19 s boot — SPIKE_REPORT.md gate (a)),
writes the job to a temp file (the robust ``--job=<file>`` route from the spike),
and parses the framed ``__JSONL_BEGIN__ ... __JSONL_END__`` payload back into the
same episode-dict shape the JS/Py executors return.

Surface (identical to ``JsExecutor``)::

    run_batch(game_source, episodes, max_ticks, frames_every=0, escape_margin=None)
        -> list[episode_dict]           # result/ticks/checkpoints/final_snapshot/...
    run_check(game_source) -> dict       # raw G0/G2 facts (mirrors runner.js check)
    batched = True                       # one process per batch (G3 batches, no early-stop)

``game_source`` is the spec JSON *string*. Engine/infra failures (binary missing,
crash, timeout, unparseable output) raise ``VerifyError`` (imported lazily to keep
this module free of an import cycle with ``executors``); ``.as_report()`` yields the
VERIFY_ERROR-shaped dict the repair loop already recognises.

Provisioning: a GDExtension-free stock project still needs a one-time
``--headless --import`` to generate ``res://.godot`` on a fresh checkout (the same
gotcha the spike hit for rapier, #1 in SPIKE_REPORT.md). This is done automatically,
once, when ``.godot`` is absent.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile

BEGIN = "__JSONL_BEGIN__"
END = "__JSONL_END__"

# Console build streams stdout cleanly to a pipe (SPIKE_REPORT.md gotcha #4).
_DEFAULT_EXE_NAME = "Godot_v4.7-stable_win64_console.exe"

# The physics step rate. `--fixed-fps 60` "disables real-time synchronization" so dt is
# a fixed 1/60 instead of wall-clock dependent — MANDATORY on every stepping invocation
# or byte-identical replay voids (GODOT_DOCS_MINING.md section 3). Nothing in
# project.godot can enforce it (it is a CLI flag), so we pin it here and refuse to trust
# callers to pass it.
FIXED_FPS = "60"


def stepping_argv(exe: str, project: str, runner_rel: str,
                  user_args: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Build the argv for a physics-STEPPING headless runner invocation, GUARANTEEING
    ``--fixed-fps 60`` regardless of the caller. Both seams that step physics — the
    batch/check executor and the serve env — must route through this so determinism
    can never regress by a dropped flag. Asserts the flag is present (belt-and-braces
    against a future edit reordering the list)."""
    assert FIXED_FPS.isdigit() and int(FIXED_FPS) > 0, \
        "FIXED_FPS must be a positive integer string (the pinned physics rate)"
    argv = [exe, "--headless", "--fixed-fps", FIXED_FPS,
            "--path", project, "-s", runner_rel, "--", *user_args]
    i = argv.index("--fixed-fps")
    assert i >= 0 and argv[i + 1] == FIXED_FPS, \
        "stepping invocation MUST pin --fixed-fps 60 (determinism pin)"
    return argv


def _dotgodot_present(project: str) -> bool:
    """Whether the ``res://.godot`` import artifact exists — the EFFECT a one-time
    ``--headless --import`` is supposed to produce. Import returncodes lie (GH #77508
    quits early, #83449 returns 1 on success), so provisioning verifies THIS, never the
    exit code (GODOT_DOCS_MINING.md section 3 CI gotchas)."""
    return os.path.isdir(os.path.join(project, ".godot"))


def _repo_root() -> str:
    """Repo root = grandparent of this module's package dir (harness/verify/)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _main_checkout_root(repo_root: str) -> str:
    """The MAIN checkout root. In a git worktree the tree lives under
    ``<main>/.claude/worktrees/<id>/``; the gitignored ``godotworld/tools/`` (the
    Godot binary) exists only in the main checkout, so we strip the worktree
    suffix to reach it. Outside a worktree this is ``repo_root`` unchanged."""
    marker = os.path.join(".claude", "worktrees")
    norm = repo_root.replace("/", os.sep)
    idx = norm.find(marker)
    if idx > 0:
        return norm[: idx].rstrip(os.sep)
    return repo_root


def default_godot_project(repo_root: str | None = None) -> str:
    return os.path.join(repo_root or _repo_root(), "godotworld")


def find_godot_exe(repo_root: str | None = None) -> str | None:
    """Locate the Godot console binary: ``HARNESS_GODOT_EXE`` env override, else the
    gitignored ``godotworld/tools/`` binary (worktree checkout falls back to the
    main checkout, where ``tools/`` actually lives). Returns None if unfound."""
    env = os.environ.get("HARNESS_GODOT_EXE")
    if env:
        return env if os.path.isfile(env) else None
    root = repo_root or _repo_root()
    candidates = [
        os.path.join(root, "godotworld", "tools", _DEFAULT_EXE_NAME),
        os.path.join(_main_checkout_root(root), "godotworld", "tools", _DEFAULT_EXE_NAME),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


class GodotExecutor:
    """Out-of-process executor spawning one headless Godot per batch."""

    batched = True  # one Godot process per batch -> G3 runs the batch, no early-stop

    def __init__(self, exe: str | None = None, project: str | None = None,
                 timeout_s: float = 120.0):
        self.exe = exe or find_godot_exe()
        self.project = project or default_godot_project()
        self.runner_rel = "res://runner.gd"
        self.timeout_s = timeout_s
        self._provisioned = False

    # -- provisioning ------------------------------------------------------
    def _ensure_provisioned(self) -> None:
        """Generate ``res://.godot`` once (one-time headless import) so the project
        loads cleanly on a fresh checkout. Idempotent; skipped once ``.godot`` exists.

        The import returncode is NOT trusted (GH #77508/#83449): success is confirmed by
        the EFFECT — the ``.godot`` artifact appearing — with one retry if the first
        ``--import`` quit before finishing. A persistent absence is left for the actual
        run below to surface as a load error."""
        if self._provisioned:
            return
        if not _dotgodot_present(self.project):
            for _ in range(2):
                try:
                    subprocess.run(
                        [self.exe, "--headless", "--import", "--path", self.project],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=max(self.timeout_s, 180.0))
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    break  # a real failure surfaces on the actual run below
                if _dotgodot_present(self.project):
                    break  # artifact present -> import truly took (effect, not returncode)
        self._provisioned = True

    # -- process plumbing --------------------------------------------------
    def _run_godot(self, job: dict) -> str:
        from harness.verify.executors import VerifyError  # lazy: avoid import cycle

        if not self.exe or not os.path.isfile(self.exe):
            raise VerifyError("godot_missing",
                              f"Godot binary not found (set HARNESS_GODOT_EXE): {self.exe!r}")
        runner = os.path.join(self.project, "runner.gd")
        if not os.path.isfile(runner):
            raise VerifyError("godot_runner_missing",
                              f"runner.gd not found at {runner}")
        self._ensure_provisioned()

        job_fd, job_path = tempfile.mkstemp(suffix=".json", prefix="godot_job_")
        try:
            with os.fdopen(job_fd, "w", encoding="utf-8") as fh:
                json.dump(job, fh)
            argv = stepping_argv(self.exe, self.project, self.runner_rel,
                                 ["--job=" + job_path])
            try:
                proc = subprocess.run(argv, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace",
                                      timeout=self.timeout_s)
            except FileNotFoundError:
                raise VerifyError("godot_missing",
                                  f"Godot binary not executable: {self.exe!r}")
            except subprocess.TimeoutExpired:
                raise VerifyError("godot_timeout",
                                  f"Godot runner exceeded {self.timeout_s}s",
                                  timeout_s=self.timeout_s)
        finally:
            try:
                os.remove(job_path)
            except OSError:
                pass

        payload = _slice_payload(proc.stdout or "")
        if payload is None:
            # No framed payload -> the runner never produced output (crash / bad load).
            tail = (proc.stderr or proc.stdout or "").strip()[-500:]
            raise VerifyError("godot_crash",
                              f"Godot exit {proc.returncode}, no JSONL payload: {tail}")
        return payload

    # -- episode batch -----------------------------------------------------
    def run_batch(self, game_source, episodes, max_ticks, frames_every=0,
                  escape_margin=None) -> list[dict]:
        from harness.verify.executors import VerifyError  # lazy

        specs = [{"seed": int(e.get("seed", 0)), "actions": list(e.get("actions", []))}
                 for e in episodes]
        job = {"mode": "episodes", "source": game_source, "episodes": specs,
               "max_ticks": int(max_ticks), "frames_every": int(frames_every or 0)}
        if escape_margin is not None:
            job["escape_margin"] = float(escape_margin)
        payload = self._run_godot(job)

        lines = [ln for ln in payload.splitlines() if ln.strip()]
        if len(lines) != len(specs):
            raise VerifyError(
                "godot_bad_output",
                f"expected {len(specs)} episode line(s), got {len(lines)}")
        recs: list[dict] = []
        for spec, line in zip(specs, lines):
            try:
                rec = json.loads(line)
            except ValueError as exc:
                raise VerifyError("godot_bad_output", f"unparseable JSONL: {exc}")
            # The runner does not echo actions; attach the applied prefix so the
            # dict matches run_episode's contract (G3 reads ep["actions"]).
            ticks = int(rec.get("ticks", 0))
            rec.setdefault("actions", spec["actions"][:ticks])
            recs.append(rec)
        return recs

    # -- static + goal check (G0/G2 facts) --------------------------------
    def run_check(self, game_source) -> dict:
        from harness.verify.executors import VerifyError  # lazy

        payload = self._run_godot({"mode": "check", "source": game_source})
        line = next((ln for ln in payload.splitlines() if ln.strip()), "")
        try:
            obj = json.loads(line)
        except ValueError as exc:
            raise VerifyError("godot_bad_output", f"unparseable check output: {exc}")
        if obj.get("error"):
            raise VerifyError("godot_check_fatal", str(obj["error"]))
        return obj


def _slice_payload(stdout: str) -> str | None:
    """Return the text framed between the JSONL markers (banner/log noise excluded),
    or None if the frame is absent."""
    i = stdout.find(BEGIN)
    j = stdout.find(END)
    if i < 0 or j < 0 or j < i:
        return None
    return stdout[i + len(BEGIN):j]
