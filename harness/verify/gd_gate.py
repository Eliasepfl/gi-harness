"""G0 code gates for the GDScript (GameAPI) lane — the NEW static species.

Where the pymunk/JS/spec lanes carry the game as data a frozen interpreter reads,
the GDScript lane carries it as CODE (``godotworld/GAME_API.md``). Running untrusted
GDScript demands a static layer the data lanes never needed. This module is the
python side of that layer — the part that runs BEFORE a single line of generated
code is compiled or executed:

    scan_gd_source(source) -> list[Finding]     # (b) BANNED-API scanner

It is a coarse, high-recall token/regex net (comments stripped so a banned name in
prose is not a false positive) over the escape hatches the GameAPI contract forbids:
``OS.*``, ``FileAccess``/``DirAccess``, network peers, threads, reflection escapes
(``ClassDB``/``Expression``/``Engine.get_singleton``/``set_script``/``GDScript``),
``load``/``preload``, wall-clock ``Time.*``, the unseeded global RNG
(``randi``/``randf``/``randomize`` — a game MUST draw from ``self.rng``), and
``get_tree().quit``. Any hit is a HARD G0 fail with a line number.

The other two G0 gates: (a) the parse gate — a STANDALONE ``godot --headless
--check-only --script <file>`` compile-check (a duck-typed plain-Node game has no base
class to resolve, so it compiles standalone; ``harness/verify/gd_exec.py``), surfaced
as ``facts["load"]``; and (c) the contract probe — ``has_method`` over the required
method-convention methods (run inside ``serve_game.gd``'s ``check`` op), surfaced as
``facts["contract"]``. ``gameverify.run_g0_gd`` folds all three into the shared G0
report shape.

The scanner is defense-IN-DEPTH, not the sole boundary: generated code additionally
runs ONLY in-container on compute nodes, in a process whose environment is scrubbed
of every credential (``godot_exec.scrubbed_env``). A banned token disguised inside a
string/comment cannot execute without ``load``/``exec`` — themselves banned.
"""

from __future__ import annotations

import re

# The GameAPI contract methods a generated game MUST implement (GAME_API.md). The
# serve host's contract probe reports has_method for each; run_g0_gd checks them.
GD_REQUIRED_METHODS = ("build", "act", "state", "checkpoints",
                       "is_success", "is_failure", "actions")


# --- Banned-API rule table -------------------------------------------------- #
# Each rule: (name, compiled regex, human message). Regexes run on comment-stripped
# source lines. Negative lookbehind ``(?<![\w.])`` lets a method call on an allowed
# receiver through (e.g. ``self.rng.randf(...)`` is fine; bare ``randf(...)`` is not).
_RULES: list[tuple[str, re.Pattern, str]] = [
    ("os", re.compile(r"(?<![\w])OS\s*\."),
     "OS.* is banned (no process/env/clipboard/shell access)"),
    ("file_access", re.compile(r"(?<![\w])FileAccess\b"),
     "FileAccess is banned (no filesystem)"),
    ("dir_access", re.compile(r"(?<![\w])DirAccess\b"),
     "DirAccess is banned (no filesystem)"),
    ("http", re.compile(r"(?<![\w])HTTP[A-Za-z]*"),
     "HTTP* is banned (no network)"),
    ("tcp", re.compile(r"(?<![\w])TCPServer\b"),
     "TCPServer is banned (no network)"),
    ("udp", re.compile(r"(?<![\w])UDPServer\b"),
     "UDPServer is banned (no network)"),
    ("stream_peer", re.compile(r"(?<![\w])StreamPeer[A-Za-z]*"),
     "StreamPeer* is banned (no sockets)"),
    ("packet_peer", re.compile(r"(?<![\w])PacketPeer[A-Za-z]*"),
     "PacketPeer* is banned (no sockets)"),
    ("websocket", re.compile(r"(?<![\w])WebSocket[A-Za-z]*"),
     "WebSocket* is banned (no network)"),
    ("enet", re.compile(r"(?<![\w])ENet[A-Za-z]*"),
     "ENet* is banned (no network)"),
    ("thread", re.compile(r"(?<![\w])Thread\b"),
     "Thread is banned (single-thread determinism)"),
    ("mutex", re.compile(r"(?<![\w])Mutex\b"),
     "Mutex is banned (single-thread determinism)"),
    ("semaphore", re.compile(r"(?<![\w])Semaphore\b"),
     "Semaphore is banned (single-thread determinism)"),
    ("worker_pool", re.compile(r"(?<![\w])WorkerThreadPool\b"),
     "WorkerThreadPool is banned (single-thread determinism)"),
    ("engine_singleton", re.compile(r"(?<![\w])Engine\s*\.\s*get_singleton\b"),
     "Engine.get_singleton is banned (reflection escape)"),
    ("class_db", re.compile(r"(?<![\w])ClassDB\b"),
     "ClassDB is banned (reflection escape)"),
    ("expression", re.compile(r"(?<![\w])Expression\b"),
     "Expression is banned (dynamic eval)"),
    ("resource_loader", re.compile(r"(?<![\w])Resource(Loader|Saver)\b"),
     "ResourceLoader/ResourceSaver is banned (arbitrary resource I/O)"),
    ("gdscript_class", re.compile(r"(?<![\w])GDScript\b"),
     "GDScript is banned (dynamic script compile)"),
    ("set_script", re.compile(r"(?<![\w])set_script\s*\("),
     "set_script() is banned (dynamic script swap)"),
    ("load", re.compile(r"(?<![\w.])load\s*\("),
     "load() is banned (only self's own scene; no dynamic resource loads)"),
    ("preload", re.compile(r"(?<![\w.])preload\s*\("),
     "preload() is banned (the game is a self-contained node; no dynamic resource loads)"),
    ("time", re.compile(r"(?<![\w])Time\s*\."),
     "Time.* (wall clock) is banned (nondeterminism)"),
    ("randomize", re.compile(r"(?<![\w])randomize\s*\("),
     "randomize() is banned (reseeds from wall clock) — use self.rng"),
    ("global_rng", re.compile(r"(?<![\w.])(randi|randf|randi_range|randf_range|randfn)\b\s*\("),
     "the global randi/randf family is banned (unseeded) — use self.rng"),
    ("global_seed", re.compile(r"(?<![\w.])seed\s*\("),
     "the global seed() is banned — the harness seeds self.rng"),
    ("scene_tree", re.compile(r"(?<![\w])get_tree\s*\("),
     "get_tree() is banned (get_tree().quit / timers escape determinism)"),
    ("quit", re.compile(r"\.\s*quit\s*\("),
     ".quit() is banned (the host owns process lifetime)"),
]


class Finding(dict):
    """A single banned-API hit. A dict subclass so it JSON-serialises and reads as a
    plain record: ``rule`` / ``line`` / ``col`` / ``token`` / ``message`` / ``source``.
    ``str(finding)`` renders the one-line report the sandbox_scan check surfaces."""

    def __str__(self) -> str:  # noqa: D105
        return "line %d: banned %s (%s) — '%s'" % (
            self["line"], self["rule"], self["message"], self["token"])


def _strip_noncode(line: str) -> str:
    """Return ``line`` reduced to its CODE: trailing ``#`` comments dropped and every
    string-literal interior (and its quotes) blanked to spaces, with column positions
    preserved. A banned name inside a string or comment is inert — it cannot execute
    without a banned ``load``/``exec``/``Expression`` — so blanking it avoids false
    positives on action names/labels without opening a hole. Backslash escapes inside
    a string are honoured; triple-quoted strings are not tracked (a benign miss)."""
    out: list[str] = []
    quote = ""
    escaped = False
    for ch in line:
        if quote:
            if escaped:
                escaped = False
                out.append(" ")
            elif ch == "\\":
                escaped = True
                out.append(" ")
            elif ch == quote:
                quote = ""
                out.append(" ")
            else:
                out.append(" ")
        elif ch in ("'", '"'):
            quote = ch
            out.append(" ")
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out)


def scan_gd_source(source: str) -> list[Finding]:
    """Static banned-API scan. Returns findings (empty == clean), most rules first
    in source order. Each finding carries a 1-based line + column and the matched
    token so the repair loop gets an actionable pointer."""
    findings: list[Finding] = []
    for lineno, raw in enumerate(str(source).splitlines(), start=1):
        code = _strip_noncode(raw)
        if not code.strip():
            continue
        for name, pattern, message in _RULES:
            for m in pattern.finditer(code):
                findings.append(Finding(
                    rule=name, line=lineno, col=m.start() + 1,
                    token=m.group(0), message=message, source=raw.rstrip()))
    return findings


def scan_violations(source: str) -> list[str]:
    """The scan as a flat list of one-line strings (the ``violations`` payload the
    shared G0 ``sandbox_scan`` check carries, mirroring ``sandbox.scan_source``)."""
    return [str(f) for f in scan_gd_source(source)]


# ======================================================================== #
# WAVE 1 — PRESSURE: static 'is_failure is hardcoded false' detector.
# ======================================================================== #
# A GameAPI game must implement ``is_failure()``, but the contract lets it
# ``return false`` when there is no lose condition — which is exactly the
# DEMO_GAP_ANALYSIS §Gap-1 defect: a game that cannot be lost has no stakes, idling
# is free, and Elias's ANTI-IDLING principle has no in-game meaning. The dynamic
# failure-witness gate (gameverify) catches an UNREACHABLE failure empirically; this
# STATIC check catches the sharpest, most-certain subcase — a literal constant-false
# body — so the compiled directive can say "you wrote no lose condition at all"
# instead of the weaker "no rollout lost". Conservative by design: it flags ONLY a
# body that is exactly ``return false`` (optionally ``return false`` on the def line),
# so a real predicate — even a trivial-looking one — is never mis-flagged.

_IS_FAILURE_DEF = re.compile(r"^(\s*)func\s+is_failure\s*\([^)]*\)\s*"
                             r"(?:->\s*[A-Za-z_][A-Za-z0-9_]*\s*)?:")
_RETURN_FALSE = re.compile(r"^return\s+false$")


def is_failure_constant_false(source: str) -> bool:
    """True IFF ``is_failure()`` is DEFINITELY constant-false — its whole body is a
    single ``return false`` (inline on the def line, or the only statement of an
    indented block; comments and blank lines ignored). Any variable read, branch, or
    extra statement makes it 'has real logic' -> not flagged here (the dynamic gate
    still judges whether that logic can ever fire). A cheap, high-precision static
    pre-read for the pressure directive; no compile, no run."""
    lines = str(source).splitlines()
    for i, raw in enumerate(lines):
        m = _IS_FAILURE_DEF.match(raw)
        if not m:
            continue
        indent = len(m.group(1))
        # (a) Inline body on the def line: ``func is_failure() -> bool: return false``.
        inline = _strip_noncode(raw[m.end():]).strip()
        if inline:
            return bool(_RETURN_FALSE.match(_norm_ws(inline)))
        # (b) Indented block: gather the real statements below, up to the next line
        # at or below the def's indentation.
        body: list[str] = []
        for nxt in lines[i + 1:]:
            code = _strip_noncode(nxt)
            if not code.strip():
                continue                                   # blank / comment-only
            n_indent = len(code) - len(code.lstrip())
            if n_indent <= indent:
                break                                      # dedent -> end of body
            body.append(_norm_ws(code.strip()))
        return body == ["return false"]
    return False                                           # no is_failure found here


def _norm_ws(s: str) -> str:
    """Collapse internal whitespace runs to single spaces (so ``return   false``
    and ``return false`` compare equal)."""
    return " ".join(s.split())
