"""G0 code gates for the GDScript (GameAPI) lane — the NEW static species.

Where the pymunk/JS/spec lanes carry the game as data a frozen interpreter reads,
the GDScript lane carries it as CODE (``godotworld/GAME_API.md``). Running untrusted
GDScript demands a static layer the data lanes never needed. This module is the
python side of that layer — the part that runs BEFORE a single line of generated
code is compiled or executed:

    scan_gd_source(source) -> list[Finding]     # (b) BANNED-API scanner

It is a coarse, high-recall token/regex net (comments stripped so a banned name in
prose is not a false positive) over the escape hatches the GameAPI contract forbids:
``OS.*``, ``FileAccess``/``DirAccess``, ``ResourceSaver``, network peers, threads,
reflection escapes (``ClassDB``/``Expression``/``Engine.get_singleton``/
``set_script``/``GDScript``), wall-clock ``Time.*``, and ``get_tree().quit``.
Every rule carries a SEVERITY: a HARD hit fails G0 with a line number; an ADVISORY
hit only surfaces as a warning/hint. The ADVISORY machinery (is_hard / scan_advisories)
is retained for future use, but NO rule currently uses it (see the RNG note below).

ALLOWED since guardrails v2: ``load()``/``preload()``/``ResourceLoader``. They are
builtins confined to ``res://`` (the harness's own godotworld project) + an empty
``user://`` in the disposable sandbox — no network backend, no OS paths, and byte-
identical reads across runs (no G1 drift). The WRITE half stays hard: the serve
host runs from a SOURCE project (``--path godotworld``), so ``res://`` is a real
writable directory and ``ResourceSaver`` keeps its own hard rule.

ALLOWED since guardrails v2 (round 2): the global RNG READ family —
``randi``/``randf``/``randi_range``/``randf_range``/``randfn`` — and bare ``seed()``.
The serve HOST now PINS the global RNG: it calls ``seed(world_seed)`` immediately
before every ``build()`` (both the single-instance ``_rebuild`` and the batched/vec
``_batch_build_game`` paths in ``godotworld/serve_game.gd``), so the ``@GlobalScope``
generator those functions draw from is a deterministic stream keyed by ``world_seed``,
re-run on every reset. Their lexical rules are GONE. ``randomize()`` stays HARD: it
reseeds that same generator from the WALL CLOCK, defeating the host pin — pure
nondeterminism no legit game needs (the earlier round's red-team confirmed a bare
``randi()`` in ``act()``/a success-predicate certified GREEN while nondeterministic,
because the host did not pin the RNG and G1 only twins the NOOP rollout).

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
# Each rule: (name, compiled regex, human message, severity). Regexes run on
# comment-stripped source lines. Negative lookbehind ``(?<![\w.])`` lets a method
# call on an allowed receiver through (e.g. ``self.rng.randf(...)`` is fine; bare
# ``randf(...)`` is not).
#
# Severity: HARD findings fail the G0 gate (sandbox/determinism boundaries the
# dynamic gates cannot re-derive); ADVISORY findings are hints only. The ADVISORY
# severity is retained for future use, but as of guardrails v2 round 2 NO rule uses
# it: the global RNG READ family (randi/randf/.../seed) is now DETERMINISTIC because
# the serve host pins the global RNG (seed(world_seed) before every build()), so those
# rules are removed outright rather than downgraded to advisory.
HARD = "hard"
ADVISORY = "advisory"

_RULES: list[tuple[str, re.Pattern, str, str]] = [
    ("os", re.compile(r"(?<![\w])OS\s*\."),
     "OS.* is banned (no process/env/clipboard/shell access)", HARD),
    ("file_access", re.compile(r"(?<![\w])FileAccess\b"),
     "FileAccess is banned (no filesystem)", HARD),
    ("dir_access", re.compile(r"(?<![\w])DirAccess\b"),
     "DirAccess is banned (no filesystem)", HARD),
    ("http", re.compile(r"(?<![\w])HTTP[A-Za-z]*"),
     "HTTP* is banned (no network)", HARD),
    ("tcp", re.compile(r"(?<![\w])TCPServer\b"),
     "TCPServer is banned (no network)", HARD),
    ("udp", re.compile(r"(?<![\w])UDPServer\b"),
     "UDPServer is banned (no network)", HARD),
    ("stream_peer", re.compile(r"(?<![\w])StreamPeer[A-Za-z]*"),
     "StreamPeer* is banned (no sockets)", HARD),
    ("packet_peer", re.compile(r"(?<![\w])PacketPeer[A-Za-z]*"),
     "PacketPeer* is banned (no sockets)", HARD),
    ("websocket", re.compile(r"(?<![\w])WebSocket[A-Za-z]*"),
     "WebSocket* is banned (no network)", HARD),
    ("enet", re.compile(r"(?<![\w])ENet[A-Za-z]*"),
     "ENet* is banned (no network)", HARD),
    ("thread", re.compile(r"(?<![\w])Thread\b"),
     "Thread is banned (single-thread determinism)", HARD),
    ("mutex", re.compile(r"(?<![\w])Mutex\b"),
     "Mutex is banned (single-thread determinism)", HARD),
    ("semaphore", re.compile(r"(?<![\w])Semaphore\b"),
     "Semaphore is banned (single-thread determinism)", HARD),
    ("worker_pool", re.compile(r"(?<![\w])WorkerThreadPool\b"),
     "WorkerThreadPool is banned (single-thread determinism)", HARD),
    ("engine_singleton", re.compile(r"(?<![\w])Engine\s*\.\s*get_singleton\b"),
     "Engine.get_singleton is banned (reflection escape)", HARD),
    ("class_db", re.compile(r"(?<![\w])ClassDB\b"),
     "ClassDB is banned (reflection escape)", HARD),
    ("expression", re.compile(r"(?<![\w])Expression\b"),
     "Expression is banned (dynamic eval)", HARD),
    # Guardrails v2: ResourceLoader (a READ, equivalent to the now-allowed load())
    # dropped; ResourceSaver stays — the serve host runs from a SOURCE project, so
    # res:// is a real writable directory and ResourceSaver is the write vector.
    ("resource_saver", re.compile(r"(?<![\w])ResourceSaver\b"),
     "ResourceSaver is banned (writes into the host's res:// source dir)", HARD),
    ("gdscript_class", re.compile(r"(?<![\w])GDScript\b"),
     "GDScript is banned (dynamic script compile)", HARD),
    ("set_script", re.compile(r"(?<![\w])set_script\s*\("),
     "set_script() is banned (dynamic script swap)", HARD),
    # Guardrails v2: 'load'/'preload' rules removed — res://-confined reads, sandbox-
    # contained and deterministic; a broken/dynamic load still fails the parse gate
    # or crashes at the serve host (and any drift fails the G1 two-run gate).
    ("time", re.compile(r"(?<![\w])Time\s*\."),
     "Time.* (wall clock) is banned (nondeterminism)", HARD),
    # Guardrails v2 round 2: the global RNG READ family (randi/randf/randi_range/
    # randf_range/randfn) and bare seed() are now ALLOWED and their rules removed —
    # the serve host pins the global RNG with seed(world_seed) before every build()
    # (single-instance _rebuild + batched _batch_build_game), so their stream is
    # deterministic. randomize() stays HARD: it reseeds the global RNG from the WALL
    # CLOCK, defeating the host pin -> pure nondeterminism no legit game needs.
    ("randomize", re.compile(r"(?<![\w])randomize\s*\("),
     "randomize() reseeds the global RNG from the wall clock, defeating the host's "
     "seed(world_seed) pin — banned (global randi/randf are already deterministic)", HARD),
    ("scene_tree", re.compile(r"(?<![\w])get_tree\s*\("),
     "get_tree() is banned (get_tree().quit / timers escape determinism)", HARD),
    ("quit", re.compile(r"\.\s*quit\s*\("),
     ".quit() is banned (the host owns process lifetime)", HARD),
]


class Finding(dict):
    """A single scanner hit. A dict subclass so it JSON-serialises and reads as a
    plain record: ``rule`` / ``line`` / ``col`` / ``token`` / ``message`` /
    ``source`` / ``severity`` (``"hard"`` fails G0; ``"advisory"`` only warns).
    ``str(finding)`` renders the one-line report the sandbox_scan check surfaces."""

    def __str__(self) -> str:  # noqa: D105
        kind = "advisory" if self.get("severity") == ADVISORY else "banned"
        return "line %d: %s %s (%s) — '%s'" % (
            self["line"], kind, self["rule"], self["message"], self["token"])


def is_hard(finding) -> bool:
    """True iff this finding FAILS the G0 gate. The severity predicate the gate
    plumbing keys on: hard findings reject the game; advisory findings are routed
    to warnings/hints (mcp_server's ``verify_game`` payload) and never fail."""
    return (finding.get("severity") or HARD) == HARD


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
        for name, pattern, message, severity in _RULES:
            for m in pattern.finditer(code):
                findings.append(Finding(
                    rule=name, line=lineno, col=m.start() + 1,
                    token=m.group(0), message=message, source=raw.rstrip(),
                    severity=severity))
    return findings


def scan_violations(source: str) -> list[str]:
    """The HARD findings as a flat list of one-line strings (the ``violations``
    payload the shared G0 ``sandbox_scan`` check carries, mirroring
    ``sandbox.scan_source``). Advisory findings are NOT violations — they go
    through ``scan_advisories`` into the report's warnings instead."""
    return [str(f) for f in scan_gd_source(source) if is_hard(f)]


def scan_advisories(source: str) -> list[str]:
    """The ADVISORY findings as one-line strings — non-fatal hints (e.g. 'prefer a
    RandomNumberGenerator seeded from world_seed') the funnel surfaces in the
    report's ``warnings`` (and mcp_server's ``verify_game`` payload) while the
    G1 two-run drift gate stays the empirical judge of determinism."""
    return [str(f) for f in scan_gd_source(source) if not is_hard(f)]


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
