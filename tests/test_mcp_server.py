"""Offline unit tests for harness.mcp_server (the funnel-as-MCP-server, Phase 3).

Run under an env that has fastmcp (the godot-ai uv env) with the harness importable::

    PYTHONPATH=<worktree> ~/.local/bin/uv --project /home/enaha/GI/godot-ai \
        run --no-sync --with pytest python -m pytest tests/test_mcp_server.py -q

ENGINE PER TEST (stated so there is zero ambiguity about what certifies what):
  * The CERTIFICATION lane is GDScript compiled + run inside serve_game.gd's Godot host
    (engine "gdscript"). ``test_verify_green_gdscript_route`` is the deliverable GREEN proof;
    it needs a Godot binary (HARNESS_GODOT_EXE) so it SKIPS offline and runs in-image / PoC-3.
  * ``test_verify_hints_on_banned_api`` also exercises the real gdscript lane offline: the
    harness's own G0 sandbox scanner (rejects OS.*, FileAccess, ResourceSaver, network, ...;
    guardrails v2 made load/preload ALLOWED, and round 2 made the global randi/randf/seed
    family ALLOWED — the host pins the global RNG — while randomize() stays HARD)
    short-circuits BEFORE any Godot spawn, so it needs no binary yet still returns the real
    typed hint.
  * ``test_verify_green_plumbing_legacy_lane_only`` runs the FROZEN LEGACY python lane
    (pymunk) purely to prove the tool's write->funnel->witness->hints WIRING; it is NOT a
    certification path and the payload is asserted to carry ``legacy_lane: true``. pymunk is
    a test-only shim for this one wiring check -- it is nowhere in the server's run path.
  * extract_game / trust-boundary / async-surface tests are engine-free (static + sandbox).

The spawn note: verify_game(sandboxed=True) uses multiprocessing "spawn", which re-imports
__main__. pytest's console entry is guarded, so the spawn is safe here; a bare `python -`
heredoc is NOT (its __main__ re-runs) -- always drive these through pytest / a guarded main.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("fastmcp")  # the server needs fastmcp; skip cleanly if absent.

import harness.mcp_server as S  # noqa: E402


# --------------------------------------------------------------------------- #
# Capability probes -> graceful skips
# --------------------------------------------------------------------------- #
def _pymunk_ok() -> bool:
    try:
        import pymunk  # noqa: F401
        return True
    except Exception:
        return False


PYMUNK = pytest.mark.skipif(not _pymunk_ok(),
                            reason="pymunk not installed (python funnel route unavailable)")
GODOT = pytest.mark.skipif(not S._godot_available(),
                           reason="Godot binary not found (set HARNESS_GODOT_EXE)")


# --------------------------------------------------------------------------- #
# Fixtures: GameAPI sources
# --------------------------------------------------------------------------- #
COMPLIANT_GD = """extends Node2D

func build(world_seed: int) -> void:
\tpass

func act(action: String) -> void:
\tpass

func state() -> Dictionary:
\treturn {}

func checkpoints() -> Dictionary:
\treturn {}

func is_success() -> bool:
\treturn false

func is_failure() -> bool:
\treturn false

func actions() -> Array:
\treturn ["a"]
"""

# Missing state()/checkpoints(); act() declared with the WRONG arity (0, expected 1).
BROKEN_GD = """extends Node2D

func build(world_seed: int) -> void:
\tpass

func act() -> void:
\tpass

func is_success() -> bool:
\treturn false

func is_failure() -> bool:
\treturn false

func actions() -> Array:
\treturn ["a"]
"""

# GDScript that shells out via OS.* -> tripped by the pure-python banned-API scan
# at G0 BEFORE any Godot spawn (deterministic, offline typed hint). Guardrails v2 round 2:
# the global randi()/randf()/seed() family is ALLOWED now (the host pins the global RNG),
# so the fixture uses OS.* — an unambiguously HARD rule that short-circuits offline.
BANNED_GD = COMPLIANT_GD.replace(
    "func build(world_seed: int) -> void:\n\tpass",
    "func build(world_seed: int) -> void:\n\tOS.execute(\"ls\", [])")

# A known-good python (pymunk) game: certifies G0-G3 through the real funnel.
GAME_VALID = """
TITLE = "Push Right"
PROMPT = "drive the block past the marker on the right"
ACTIONS = ["right", "left"]

def build(world):
    world.add("ground", shape="box", pos=(400, 10), size=(800, 20), static=True)
    world.add("player", shape="box", pos=(100, 60), size=(20, 20))
    world.control("player")

def act(world, action):
    if action == "right":
        world.impulse("player", (60, 0))
    elif action == "left":
        world.impulse("player", (-60, 0))

def success(world):
    return world.query("player")["pos"][0] > 300

def checkpoints(world):
    x = world.query("player")["pos"][0]
    return {"halfway": x > 200, "almost": x > 260}
"""


# --------------------------------------------------------------------------- #
# extract_game
# --------------------------------------------------------------------------- #
def _write_project(root, files: dict) -> str:
    for rel, content in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)
    return root


def test_extract_finds_compliant_script(tmp_path):
    proj = _write_project(str(tmp_path), {
        "project.godot": "[application]\n",
        "src/game.gd": COMPLIANT_GD,
        "src/helper.gd": "extends Node\nfunc noise() -> void:\n\tpass\n",
        ".godot/junk.gd": "func build(x):\n\tpass\n",         # editor cache -> skipped
        "addons/godot_ai/plugin.gd": COMPLIANT_GD,            # vendored plugin -> skipped
    })
    out = S._do_extract(proj)
    assert out["game_source"] == COMPLIANT_GD
    assert out["script_path"].endswith("src/game.gd")
    assert out["diagnostics"]["compliant"] is True
    assert out["diagnostics"]["misses"] == []
    # the skipped dirs must not have been scanned
    scanned = {s["rel"] for s in out["diagnostics"]["scanned"]}
    assert not any(r.startswith(".godot") or r.startswith("addons") for r in scanned)
    assert "advisory" in out


def test_extract_reports_typed_misses(tmp_path):
    proj = _write_project(str(tmp_path), {
        "project.godot": "[application]\n",
        "game.gd": BROKEN_GD,
    })
    out = S._do_extract(proj)
    assert out["diagnostics"]["compliant"] is False
    misses = {m["method"]: m for m in out["diagnostics"]["misses"]}
    # state + checkpoints missing entirely
    assert misses["state"]["problem"] == "missing"
    assert misses["checkpoints"]["problem"] == "missing"
    # act present but wrong arity (0, expected 1)
    assert misses["act"]["problem"] == "wrong_arity"
    assert misses["act"]["expected_arity"] == 1
    assert misses["act"]["actual_arity"] == 0
    # source still handed back so the agent can repair it
    assert out["game_source"] == BROKEN_GD


def test_extract_no_scripts(tmp_path):
    proj = _write_project(str(tmp_path), {"project.godot": "[application]\n"})
    out = S._do_extract(proj)
    assert out["game_source"] is None
    assert out["diagnostics"]["n_scripts"] == 0


def test_extract_bad_path():
    out = S._do_extract("/no/such/project/xyz")
    assert "error" in out and out["game_source"] is None


# --------------------------------------------------------------------------- #
# verify_game
# --------------------------------------------------------------------------- #
def test_verify_hints_on_banned_api():
    """The real gdscript lane's typed HINTS, offline: the harness's own G0 sandbox
    scanner (OS.*, FileAccess, ResourceSaver, network, ...) short-circuits G0 BEFORE
    any Godot spawn and returns a typed, actionable hint."""
    out = S._do_verify(BANNED_GD, None)
    assert out["verdict"] == "ENV_ERROR"
    assert out["failure_class"] == "ENV_ERROR"
    assert out["passed"] is False
    assert out["hint"] and "banned" in out["hint"].lower()
    g0 = out["per_gate"].get("G0_static")
    assert g0 is not None and g0["passed"] is False
    assert "sandbox_scan" in g0["failing_checks"]
    assert out["advisory"] == S.ADVISORY


def test_verify_level_is_inert():
    """`level` is advisory metadata; it must never change the verdict or gate execution."""
    base = S._do_verify(BANNED_GD, None)
    leveled = S._do_verify(BANNED_GD, 7)
    assert leveled["level"] == 7
    assert leveled["level_note"] == S._LEVEL_NOTE
    assert leveled["verdict"] == base["verdict"]
    assert leveled["failure_class"] == base["failure_class"]


@PYMUNK
def test_verify_green_plumbing_legacy_lane_only():
    """PLUMBING ONLY -- proves the tool's write->funnel->witness->hints wiring produces a
    well-formed CERTIFIED payload end-to-end. It runs the FROZEN LEGACY python lane and is
    NOT a certification path: the real green proof is the GDScript->Godot route
    (test_verify_green_gdscript_route + PoC-3). The response must flag the legacy lane so a
    caller can never mistake this for a real verdict."""
    out = S._do_verify(GAME_VALID, None)
    assert out["passed"] is True
    assert out["verdict"] == "CERTIFIED"
    assert out["failure_class"] is None
    assert "valid game" in out["hint"].lower()
    ws = out["witness_summary"]
    assert ws is not None and isinstance(ws["ticks"], int) and ws["ticks"] > 0
    assert out["advisory"] == S.ADVISORY
    # explicitly flagged as the frozen-legacy lane, NOT the gdscript certification lane
    assert out["legacy_lane"] is True
    assert out["certification_lane"].endswith("-legacy")
    # directives machinery ran (may legitimately be empty on a clean pass)
    assert isinstance(out["directives"], list)


@GODOT
def test_verify_green_gdscript_route(tmp_path):
    """THE DELIVERABLE PROOF: the REAL GDScript->Godot lane certifies the mini_collect
    fixture GREEN through serve_game.gd's host, via the tool. Runs wherever a Godot binary
    is available (HARNESS_GODOT_EXE) -- the PoC-3 compute job, or any in-image validation.
    This -- not the legacy python plumbing test -- is the certification proof."""
    fx = os.path.join(os.path.dirname(__file__), "fixtures", "gd_games", "mini_collect.gd")
    src = open(fx, encoding="utf-8").read()
    out = S._do_verify(src, None)
    assert out["passed"] is True
    assert out["verdict"] == "CERTIFIED"
    assert out["engine"] == "gdscript"
    assert out["certification_lane"] == "gdscript-godot"
    assert out["legacy_lane"] is False
    assert out["witness_summary"] and out["witness_summary"]["ticks"] > 0


# --------------------------------------------------------------------------- #
# capture_demo
# --------------------------------------------------------------------------- #
@PYMUNK
def test_capture_state_only_when_no_render():
    """With no X display / non-gd source, capture returns a state-only replay summary
    (outcome + ticks), never a gif path."""
    old = os.environ.pop("DISPLAY", None)
    try:
        out = S._do_capture(GAME_VALID, None)
    finally:
        if old is not None:
            os.environ["DISPLAY"] = old
    assert out["mode"] == "state_only"
    assert out["gif_path"] is None
    assert out["outcome"] == "success"
    assert isinstance(out["ticks"], int) and out["ticks"] > 0
    assert "state-only" in out["render_skipped_reason"].lower()


# --------------------------------------------------------------------------- #
# atlas_place
# --------------------------------------------------------------------------- #
@PYMUNK
def test_atlas_place_returns_descriptors():
    from harness.atlas.descriptors import DESCRIPTOR_KEYS
    out = S._do_atlas(GAME_VALID)
    row = out["descriptors"]
    assert isinstance(row, dict)
    # the descriptor row spans the atlas DESCRIPTOR_KEYS schema
    assert set(DESCRIPTOR_KEYS).issubset(set(row.keys()))
    assert out["certified"] is True
    # no library configured in a clean offline run -> descriptors-only, with a note
    assert out["would_be_cell"] is None
    assert "HARNESS_ATLAS_JSONL" in out["placement_note"]
    assert out["descriptor_keys"] == list(DESCRIPTOR_KEYS)


# --------------------------------------------------------------------------- #
# Trust boundary
# --------------------------------------------------------------------------- #
def _repo_snapshot(root: str) -> dict:
    """Map relpath -> (size, mtime_ns) for every tracked-ish file under `root`, skipping
    caches. Used to prove a tool call mutates NOTHING in the repo."""
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in {".git", "__pycache__", ".pytest_cache"}]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            snap[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns)
    return snap


def test_trust_boundary_no_writes_outside_sandbox(tmp_path, monkeypatch):
    """A tool call (a) mutates no file in the gi-harness repo, and (b) writes only inside
    its throwaway sandbox, which is removed afterwards (nothing left behind)."""
    sandbox_base = tmp_path / "sb"
    monkeypatch.setenv("HARNESS_MCP_SANDBOX", str(sandbox_base))

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(S.__file__)))
    before = _repo_snapshot(repo_root)

    # A verify + an extract + an atlas call: none may touch the repo or leave a sandbox.
    S._do_verify(BANNED_GD, None)          # gd route, offline (banned-API short-circuit)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "game.gd").write_text(COMPLIANT_GD, encoding="utf-8")
    S._do_extract(str(proj))

    after = _repo_snapshot(repo_root)
    assert before == after, "a tool call mutated the gi-harness repo tree"

    # The sandbox base exists but holds no leftover per-call dirs (each cleaned itself).
    leftovers = [n for n in os.listdir(sandbox_base)
                 if n.startswith(("verify-", "extract-", "capture-", "atlas-", "call-"))]
    assert leftovers == [], f"sandbox dirs not cleaned up: {leftovers}"


# --------------------------------------------------------------------------- #
# MCP surface (async decorated tools resolve + run)
# --------------------------------------------------------------------------- #
def test_async_tool_surface(tmp_path):
    """The FastMCP-decorated async tools are registered and callable end-to-end."""
    import asyncio

    async def go():
        tools = await S.mcp.list_tools()
        names = {getattr(t, "name", getattr(t, "key", None)) for t in tools}
        assert {"extract_game", "verify_game", "capture_demo", "atlas_place"} <= names
        proj = tmp_path / "p"
        proj.mkdir()
        (proj / "game.gd").write_text(COMPLIANT_GD, encoding="utf-8")
        # FastMCP 3.4.4 `@mcp.tool` leaves the plain async function bound at module scope.
        res = await S.extract_game(str(proj))
        return res

    res = asyncio.run(go())
    assert res is not None and res["diagnostics"]["compliant"] is True
