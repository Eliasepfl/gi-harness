"""Sandbox — isolated execution of LLM-generated scene/game code.

Two services:
    scan_source(source)  — static AST analysis, rejects dangerous constructs;
    run_sandboxed(...)   — runs a verification/navigation job in a subprocess
                           (multiprocessing spawn, kill on timeout).

Legitimate scene/game code needs NO imports and only talks to the `sdk`/`world`
argument it is handed.
"""

from __future__ import annotations

import ast
import builtins
import math
import multiprocessing
import traceback

# --- Static policy ------------------------------------------------------- #
ALLOWED_IMPORTS = {"math"}
# Forbidden builtins (file access / exec / dynamic introspection).
FORBIDDEN_NAMES = {
    "open", "exec", "eval", "compile", "__import__", "globals", "breakpoint",
    "input", "getattr", "setattr", "delattr", "vars", "locals", "memoryview",
    "help", "exit", "quit",
}
# Builtins allowed inside the scene execution namespace.
_ALLOWED_BUILTIN_NAMES = (
    "abs", "min", "max", "len", "range", "float", "int", "bool", "dict",
    "list", "tuple", "enumerate", "zip", "round", "sorted", "sum",
    "True", "False", "None",
)


class SandboxViolation(Exception):
    """Raised when scene code breaks the sandbox policy."""

    def __init__(self, violations):
        self.violations = list(violations)
        super().__init__("; ".join(self.violations))


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def scan_source(source: str) -> list[str]:
    """AST analysis: returns the list of violations (empty = compliant code).

    Rejects any import outside {math}, dangerous calls (open/exec/eval/...),
    and any access to a dunder attribute or identifier (`__class__`, `__subclasses__`...).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error: {exc.msg} (line {exc.lineno})"]

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_IMPORTS:
                    violations.append(f"line {node.lineno}: forbidden import '{alias.name}'")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in ALLOWED_IMPORTS:
                violations.append(f"line {node.lineno}: forbidden import 'from {node.module}'")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_NAMES:
                violations.append(f"line {node.lineno}: forbidden call '{func.id}(...)'")
        elif isinstance(node, ast.Attribute):
            if _is_dunder(node.attr):
                violations.append(f"line {node.lineno}: dunder access '.{node.attr}'")
        elif isinstance(node, ast.Name):
            if _is_dunder(node.id):
                violations.append(f"line {node.lineno}: dunder identifier '{node.id}'")
    return violations


def _safe_import(name, *args, **kwargs):
    """Restricted __import__: only allows the whitelist (defense in depth)."""
    if name in ALLOWED_IMPORTS:
        return __import__(name, *args, **kwargs)
    raise ImportError(f"forbidden import in the sandbox: {name}")


def _restricted_globals() -> dict:
    """Execution namespace: whitelisted builtins + injected `math`."""
    safe_builtins = {}
    for bname in _ALLOWED_BUILTIN_NAMES:
        if hasattr(builtins, bname):
            safe_builtins[bname] = getattr(builtins, bname)
    safe_builtins["__import__"] = _safe_import
    return {"__builtins__": safe_builtins, "math": math}


def load_scene_namespace(source: str, *, scan: bool = True) -> dict:
    """Execute `source` in a restricted namespace and return that namespace.

    If `scan` is true, raises SandboxViolation on any AST violation.
    The code runs with whitelisted builtins (no file/exec access).
    """
    if scan:
        violations = scan_source(source)
        if violations:
            raise SandboxViolation(violations)
    ns = _restricted_globals()
    exec(compile(source, "<scene>", "exec"), ns)  # noqa: S102 - restricted namespace
    return ns


# --- Subprocess ---------------------------------------------------------- #
def _sandbox_worker(scene_path: str, job: str, conn) -> None:
    """Top-level function (picklable for spawn) executed in the subprocess."""
    try:
        if job == "verify":
            from harness.legacy.verifier import verify_scene
            result = verify_scene(scene_path, sandboxed=False)
        elif job == "gameverify":
            from harness.verify.gameverify import verify_game
            result = verify_game(scene_path, sandboxed=False)
        elif job == "navigate":
            try:
                from harness.legacy.navigator import navigate
            except ImportError as exc:
                result = {"error": {"type": "not_implemented",
                                    "message": f"navigator unavailable: {exc}"}}
            else:
                result = navigate(scene_path)
        else:
            result = {"error": {"type": "bad_job", "message": f"unknown job: {job}"}}
    except Exception:  # noqa: BLE001 - forward any error to the parent
        result = {"error": {"type": "exception", "traceback": traceback.format_exc()}}
    try:
        conn.send(result)
    except Exception:  # noqa: BLE001 - pipe closed on the parent side
        pass
    finally:
        conn.close()


def run_sandboxed(scene_path: str, job: str, timeout_s: float = 20.0) -> dict:
    """Run `job` on `scene_path` in an isolated subprocess.

    job in {"verify", "gameverify", "navigate"}. Timeout -> terminate + error report.
    Returns the report dict (verify/gameverify job) or {"error": {...}} on trouble.
    """
    ctx = multiprocessing.get_context("spawn")
    recv_conn, send_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_sandbox_worker, args=(scene_path, job, send_conn))
    proc.start()
    send_conn.close()  # only the child writes

    proc.join(timeout_s)
    if proc.is_alive():
        proc.terminate()
        proc.join(5)
        if proc.is_alive():
            proc.kill()
            proc.join(5)
        recv_conn.close()
        return {"error": {"type": "timeout", "timeout_s": timeout_s,
                          "job": job, "scene_path": scene_path}}

    result = None
    if recv_conn.poll():
        try:
            result = recv_conn.recv()
        except EOFError:
            result = None
    recv_conn.close()
    if result is None:
        return {"error": {"type": "crash", "exitcode": proc.exitcode, "job": job}}
    return result
