"""End-to-end tests for the GDScript (GameAPI) lane (skipped without the Godot binary).

Driven through ``serve_game.gd`` + ``GdExecutor``:

* the ``mini_collect.gd`` fixture certifies G0-G3 (all four layers pass, a non-trivial
  replayable witness collects both gems, every milestone latches);
* the serve stepping is DETERMINISTIC — the same (seed, actions) replays byte-for-byte
  across two independent sessions (the G1 two-run drift gate);
* the parse gate rejects a syntax-broken game and the contract probe rejects a game
  missing a required method — both surface as a clean G0 ENV_ERROR;
* Godot starts and verifies correctly under the SCRUBBED env even when the parent
  process holds an OPENROUTER_API_KEY (the scrub does not break the lane).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.verify.executors import find_godot_exe  # noqa: E402
from harness.verify.gameverify import verify_game  # noqa: E402
from harness.verify.gd_exec import GdExecutor  # noqa: E402
from harness.verify.godot_exec import (  # noqa: E402
    default_godot_project, scrubbed_env,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GD = os.path.join(_ROOT, "tests", "fixtures", "gd_games")
_MINI = os.path.join(_GD, "mini_collect.gd")
_MINI_3D = os.path.join(_GD, "mini_collect_3d.gd")
_TUMBLE_3D = os.path.join(_GD, "tumble_3d.gd")
_SINGLE_ACTION = os.path.join(_GD, "single_action_win.gd")
_WALLED = os.path.join(_GD, "walled_goal.gd")
_FLYOFF = os.path.join(_GD, "flyoff.gd")
_NO_PRESSURE = os.path.join(_GD, "no_pressure.gd")
_LOSABLE = os.path.join(_GD, "losable.gd")
_SOFTLOCK_PIT = os.path.join(_GD, "softlock_pit.gd")
_DEAD_SPACE = os.path.join(_GD, "dead_space.gd")
_BOUNCE_2D = os.path.join(_GD, "bounce_2d.gd")


def _src(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()

GODOT_EXE = find_godot_exe()
requires_godot = pytest.mark.skipif(GODOT_EXE is None, reason="Godot binary not present")

_REPORT_KEYS = {"passed", "failure_class", "layers", "hint", "warnings",
                "progress", "witness"}


def _free_port() -> int:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ====================================================================== #
# 1. Full G0-G3 certification of the fixture game
# ====================================================================== #
@requires_godot
def test_mini_collect_certifies_g0_g3():
    rep = verify_game(_MINI, sandboxed=False)
    assert rep["passed"] is True, rep
    assert rep["failure_class"] is None
    assert rep["engine"] == "gdscript"
    assert set(rep) == _REPORT_KEYS | {"engine"}
    for layer in ("G0_static", "G1_rollout", "G2_goal", "G3_solve"):
        assert rep["layers"][layer]["passed"], (layer, rep["layers"][layer])
    w = rep["witness"]
    assert w is not None and w["ticks"] >= 20, w        # a real, non-trivial play
    assert set(w["checkpoints"]) == {"got_first", "got_both"}
    assert all(t is not None for t in w["checkpoints"].values()), w["checkpoints"]


@requires_godot
def test_mini_collect_g0_contract_probe_facts():
    """G0 exposes the three code gates: banned-scan clean, parse gate passed, and the
    contract probe confirming every GameAPI method — plus one controlled dynamic body."""
    rep = verify_game(_MINI, sandboxed=False)
    checks = rep["layers"]["G0_static"]["checks"]
    assert checks["sandbox_scan"]["pass"] is True
    assert checks["loads"]["pass"] is True          # parse gate (compile-check)
    assert checks["symbols"]["pass"] is True        # contract probe (has_method)
    assert checks["controlled"]["controlled"] == ["player"]
    assert checks["counts"]["n"] == 3


# ====================================================================== #
# 1a. Single-action anti-triviality gate (Elias directive 3)
# ====================================================================== #
@requires_godot
def test_single_action_win_rejected_with_broken_hint():
    """A game winnable by SPAMMING one action ("right") certifies G0-G3 on its own
    terms but is rejected by the single-action gate: GOAL_ERROR + the BROKEN repair
    hint the generation loop consumes."""
    rep = verify_game(_SINGLE_ACTION, sandboxed=False)
    assert rep["engine"] == "gdscript"
    assert rep["passed"] is False, rep
    assert rep["failure_class"] == "GOAL_ERROR", rep
    assert "BROKEN" in rep["hint"] and "single action" in rep["hint"], rep["hint"]
    sa = rep["layers"]["G3_solve"]["checks"]["single_action"]
    assert sa["pass"] is False
    assert any(w["action"] == "right" for w in sa["wins"]), sa


@requires_godot
def test_mini_collect_passes_single_action_gate():
    """mini_collect needs two DIFFERENT gems -> no single action wins; the gate's
    check passes and the game still certifies."""
    rep = verify_game(_MINI, sandboxed=False)
    assert rep["passed"] is True, rep
    sa = rep["layers"]["G3_solve"]["checks"].get("single_action")
    assert sa is not None and sa["pass"] is True, sa


# ====================================================================== #
# 1c. G0.5 geometric reachability pre-filter (Elias directive 1)
# ====================================================================== #
@requires_godot
def test_walled_goal_rejected_by_reachability_prefilter():
    """A gem SEALED inside a box of walls is geometrically unreachable: the cheap G0.5
    pre-filter rejects it (GOAL_ERROR + 'walled off' hint) BEFORE the G3 solve runs, so
    G3_solve never even executes on this game."""
    rep = verify_game(_WALLED, sandboxed=False)
    assert rep["engine"] == "gdscript"
    assert rep["passed"] is False, rep
    assert rep["failure_class"] == "GOAL_ERROR", rep
    g05 = rep["layers"]["G0_5_reach"]
    assert g05["passed"] is False
    assert "gem" in g05["checks"]["reachable"]["unreachable"], g05
    assert "walled off" in rep["hint"] or "unreachable" in rep["hint"], rep["hint"]
    # The pre-filter short-circuited BEFORE G3 (its layer stays unrun/empty).
    assert not rep["layers"]["G3_solve"].get("checks"), rep["layers"]["G3_solve"]


@requires_godot
def test_reachable_fixtures_pass_reachability_prefilter():
    """The reachable fixtures (mini_collect 2D + 3D) clear G0.5 and still certify — the
    pre-filter is necessary-not-sufficient, never a false reject on a solvable game."""
    for path in (_MINI, _MINI_3D):
        rep = verify_game(path, sandboxed=False)
        assert rep["passed"] is True, (path, rep)
        assert rep["layers"]["G0_5_reach"]["passed"] is True, (path, rep)


# ====================================================================== #
# 1e. Failure-witness / PRESSURE gate (WAVE 1, DEMO_GAP_ANALYSIS §Gap 1+2)
# ====================================================================== #
@requires_godot
def test_no_pressure_fixture_certifies_but_gate_flags_no_stakes():
    """The canonical UNFAILABLE fixture certifies G0-G3 (the gate is ADVISORY, not a
    block) yet the pressure gate flags it `no_pressure` with a warning, and the finding
    compiles to a repair directive (the revise-loop hook — Wave-1 acceptance b)."""
    rep = verify_game(_NO_PRESSURE, sandboxed=False)
    assert rep["passed"] is True, rep                    # ADVISORY: still certifies
    assert rep["engine"] == "gdscript"
    fw = rep["layers"]["G3_solve"]["checks"]["failure_witness"]
    assert fw["has_failure_witness"] is False
    assert fw["outcome"] == "no_pressure" and fw["constant_false"] is True
    assert any("PRESSURE" in w for w in rep["warnings"]), rep["warnings"]
    from harness.gen import feedback as F
    ds = F.compile_directives({"pressure": F.pressure_finding(rep)})
    assert [d.source for d in ds] == ["no_pressure"]
    assert "cannot be lost" in ds[0].text.lower()


@requires_godot
def test_losable_fixture_certifies_and_gate_witnesses_a_failure():
    """A game WITH real stakes (a lethal hazard) certifies AND the pressure gate finds
    a reachable failure -> outcome `has_pressure`, no warning, no directive."""
    rep = verify_game(_LOSABLE, sandboxed=False)
    assert rep["passed"] is True, rep
    fw = rep["layers"]["G3_solve"]["checks"]["failure_witness"]
    assert fw["has_failure_witness"] is True and fw["outcome"] == "has_pressure"
    assert fw["witness"] is not None
    assert not any("PRESSURE" in w for w in rep["warnings"]), rep["warnings"]
    from harness.gen import feedback as F
    assert F.compile_directives({"pressure": F.pressure_finding(rep)}) == []


# ====================================================================== #
# 1e2. Dead-space / PROPORTION gate (WAVE 2, DEMO_GAP_ANALYSIS §Gap 3)
# ====================================================================== #
@requires_godot
def test_dead_space_fixture_certifies_but_gate_flags_over_empty_world():
    """The dead_space fixture (a tiny scene in a 2000x1400 world) certifies G0-G3 (the
    gate is ADVISORY, not a block) yet the proportion gate flags it `dead_space` with a
    warning, and the finding compiles to a repair directive (the revise-loop hook)."""
    rep = verify_game(_DEAD_SPACE, sandboxed=False)
    assert rep["passed"] is True, rep                    # ADVISORY: still certifies
    assert rep["engine"] == "gdscript"
    wit = rep["witness"]
    assert wit is not None and wit["ticks"] >= 20, wit   # a real, non-trivial play
    dsc = rep["layers"]["G3_solve"]["checks"]["dead_space"]
    assert dsc["dead_space"] is True and dsc["linear_ratio"] > 5.0, dsc
    assert rep["dead_space"]["outcome"] == "dead_space", rep.get("dead_space")
    assert any("PROPORTION" in w for w in rep["warnings"]), rep["warnings"]
    from harness.gen import feedback as F
    ds = F.compile_directives({"dead_space": F.dead_space_finding(rep)})
    assert [d.source for d in ds] == ["dead_space"]
    assert ds[0].severity == F.DIFFICULTY
    assert "empty" in ds[0].text.lower()


@requires_godot
def test_mini_collect_proportioned_no_dead_space_key():
    """A reasonably-proportioned game records the measurement in a non-gating sub-check
    but does NOT flag: no warning, and no top-level `dead_space` key (report schema
    unchanged, so the strict-key certification test still holds)."""
    rep = verify_game(_MINI, sandboxed=False)
    assert rep["passed"] is True, rep
    dsc = rep["layers"]["G3_solve"]["checks"].get("dead_space")
    assert dsc is not None and dsc["dead_space"] is False, dsc
    assert "dead_space" not in rep, rep.get("dead_space")
    assert not any("PROPORTION" in w for w in rep["warnings"]), rep["warnings"]


# ====================================================================== #
# 1f. terminal_reachable — the stuck-vs-refusal separator (Elias)
# ====================================================================== #
_MOVES = ["up", "down", "left", "right"]


@requires_godot
def test_terminal_reachable_env_softlock_in_softlock_pit_pocket():
    """Drive the body INTO softlock_pit's frozen pocket: from there NO terminal
    (success OR failure) is reachable -> a real ENVIRONMENT-softlock verdict."""
    from harness.verify.reachability import terminal_reachable
    ex = GdExecutor(port_base=_free_port())
    try:
        v = terminal_reachable(ex, _src(_SOFTLOCK_PIT), _MOVES,
                               prefix=["right"] * 40, horizon=60, budget=3000)
    finally:
        ex.close()
    assert v["reachable"] is False and v["verdict"] == "env_softlock", v
    assert v["prefix_len"] == 40


@requires_godot
def test_terminal_reachable_reachable_is_agent_refusal_not_stuck():
    """From losable.gd's start a terminal IS reachable (a diligent player can win, or
    lose in the hazard) -> reachable. An idle agent here is REFUSING, not softlocked —
    the separator the ANTI-IDLING decay grounds on."""
    from harness.verify.reachability import terminal_reachable
    ex = GdExecutor(port_base=_free_port())
    try:
        v = terminal_reachable(ex, _src(_LOSABLE), _MOVES, horizon=60, budget=3000)
    finally:
        ex.close()
    assert v["reachable"] is True and v["verdict"] == "reachable", v


# ====================================================================== #
# 1b. The 3D fixture certifies through the SAME funnel (3D-through-pipeline)
# ====================================================================== #
@requires_godot
def test_mini_collect_3d_certifies_g0_g3():
    """A duck-typed plain-Node3D game (PhysicsServer3D.set_active + RigidBody3D/
    StaticBody3D/Area3D + Vector3 state) certifies G0-G3 through the serve contract —
    proving 3D works THROUGH the pipeline, not just as a standalone script, and guarding
    the serve host / oracles against 2D-only assumptions (Vector2, xy-bounds, xy-sanity)."""
    rep = verify_game(_MINI_3D, sandboxed=False)
    assert rep["passed"] is True, rep
    assert rep["failure_class"] is None
    assert rep["engine"] == "gdscript"
    for layer in ("G0_static", "G1_rollout", "G2_goal", "G3_solve"):
        assert rep["layers"][layer]["passed"], (layer, rep["layers"][layer])
    w = rep["witness"]
    assert w is not None and w["ticks"] >= 20, w        # a real, non-trivial play
    assert set(w["checkpoints"]) == {"reached_first", "reached_both"}
    assert all(t is not None for t in w["checkpoints"].values()), w["checkpoints"]
    # G0 sees one controlled body among the 4 (puck + two Area3D goals + the table).
    checks = rep["layers"]["G0_static"]["checks"]
    assert checks["controlled"]["controlled"] == ["puck"]
    assert checks["counts"]["n"] == 4


@requires_godot
def test_gd_serve_emits_three_component_vectors_for_3d():
    """The serve obs is dimension-agnostic: a 3D game's pos/vel come back as 3-vectors
    ([x, y, z]) through the same seam a 2D game's 2-vectors do (the _vec_json fix)."""
    with open(_MINI_3D, "r", encoding="utf-8") as fh:
        src = fh.read()
    ex = GdExecutor(port_base=_free_port())
    try:
        rec = ex.run_batch(src, [{"seed": 0, "actions": ["left"] * 6}], 6)[0]
    finally:
        ex.close()
    puck = rec["final_snapshot"]["puck"]
    assert len(puck["pos"]) == 3 and len(puck["vel"]) == 3, puck
    # The z axis is locked at 0; x moved left under the impulses.
    assert abs(puck["pos"][2]) < 1e-6, puck
    assert puck["pos"][0] < 400.0, puck


# ====================================================================== #
# 1d. Play-bounds termination (Elias directive 2)
# ====================================================================== #
@requires_godot
def test_flyoff_controlled_body_truncates_not_escapes():
    """A "fly" tick hurls the controlled player out of the play area: the episode
    TRUNCATES cleanly (done_trunc, result not error) at the tick it leaves — and the
    controlled body is NOT reported as an escape. The NON-controlled "debris" body,
    which also leaves, IS still reported in oob (a required-containment escape)."""
    with open(_FLYOFF, "r", encoding="utf-8") as fh:
        src = fh.read()
    ex = GdExecutor(port_base=_free_port())
    try:
        rec = ex.run_batch(src, [{"seed": 0, "actions": ["fly"] * 10}], 10,
                           escape_margin=200.0)[0]
    finally:
        ex.close()
    assert rec["done_trunc"] is True, rec           # runaway -> truncation
    assert rec["result"] != "error", rec            # a clean end, not a break
    assert rec["ticks"] < 10, rec                    # stepping stopped at the fly-off
    assert "player" not in rec["oob"], rec           # controlled leaving is NOT an escape
    assert rec["oob"] == ["debris"], rec             # non-controlled containment escape stands


@requires_godot
def test_mini_collect_no_trunc_and_byte_identical_when_in_bounds():
    """An in-bounds plan never truncates (done_trunc False) and two independent serve
    sessions stay byte-identical — the play-bounds change is inert where nothing leaves."""
    with open(_MINI, "r", encoding="utf-8") as fh:
        src = fh.read()
    plan = ["up"] * 12 + ["right"] * 20
    snaps = []
    for _ in range(2):
        ex = GdExecutor(port_base=_free_port())
        try:
            rec = ex.run_batch(src, [{"seed": 0, "actions": plan}], len(plan),
                               escape_margin=200.0)[0]
            snaps.append(rec["final_snapshot"])
            assert rec["done_trunc"] is False, rec
            assert rec["oob"] == [], rec
        finally:
            ex.close()
    assert snaps[0] == snaps[1]


# ====================================================================== #
# 2. Determinism — the G1 two-run drift gate, direct
# ====================================================================== #
@requires_godot
def test_gd_serve_two_run_drift_is_zero():
    """Two independent serve sessions, the SAME seed + action plan -> byte-identical
    final snapshots (the deterministic contract G1 gates)."""
    with open(_MINI, "r", encoding="utf-8") as fh:
        src = fh.read()
    plan = ["up"] * 12 + ["right"] * 20 + ["down"] * 8
    snaps = []
    for _ in range(2):
        ex = GdExecutor(port_base=_free_port())
        try:
            rec = ex.run_batch(src, [{"seed": 0, "actions": plan}], len(plan))[0]
            snaps.append(rec["final_snapshot"])
        finally:
            ex.close()
    assert snaps[0] == snaps[1], (snaps[0], snaps[1])


# ====================================================================== #
# 2b. 3D reset-determinism — the WITHIN-session two-run gate (the leak G1 caught)
#
# G1's determinism check runs two seeded episodes back-to-back on ONE serve host (init,
# reset, reset) and compares final snapshots. For 3D games the single-instance reset path
# reused root's World3D across episodes, leaking GodotPhysics3D solver/broadphase residual
# so episode 2 (built over episode 1's stepped space) diverged from episode 1 (built over
# the unstepped init) -- the class of failure the fix3d wave hit on real drone/car games.
# The host now hands every 3D episode a FRESH World3D; these pin that, at both speedups.
# Note: CROSS-session (separate processes) was already byte-identical even WITHOUT the fix
# (each builds over a clean space), so only a WITHIN-session twin exercises the leak.
# ====================================================================== #
def _reset_twin_snapshots(src, actions, ticks, speedup=None):
    """Two seeded episodes back-to-back on ONE serve host -> (snap1, snap2, result1,
    ticks1). This is exactly G1's repeated-reset determinism probe: init(seed 0), then
    reset(seed 0) twice, each stepped over `actions`. `speedup` overrides
    HARNESS_GODOT_SPEEDUP for the spawned host (restored afterwards)."""
    old = os.environ.get("HARNESS_GODOT_SPEEDUP")
    if speedup is not None:
        os.environ["HARNESS_GODOT_SPEEDUP"] = str(speedup)
    try:
        ex = GdExecutor(port_base=_free_port())
        try:
            spec = {"seed": 0, "actions": list(actions)}
            r1, r2 = ex.run_batch(src, [dict(spec), dict(spec)], ticks)
            return r1["final_snapshot"], r2["final_snapshot"], r1["result"], r1["ticks"]
        finally:
            ex.close()
    finally:
        if speedup is not None:
            if old is None:
                os.environ.pop("HARNESS_GODOT_SPEEDUP", None)
            else:
                os.environ["HARNESS_GODOT_SPEEDUP"] = old


@requires_godot
@pytest.mark.parametrize("speedup", [1, 8])
def test_tumble_3d_reset_determinism_is_byte_identical(speedup):
    """REGRESSION (notes/engines/DETERMINISM_3D.md): tumble_3d is a force-driven 3D
    RigidBody3D that drops into an enclosed canyon and ENDS at its floor contact -- the
    shape that exposes the reused-World3D leak. Two back-to-back seeded resets on ONE serve
    host diverged by ~9e-5 in the body's contact-tick velocity BEFORE the host pinned a
    fresh World3D per 3D episode (the wild drone diverged ~5.9e-5, the car ~0.046). This
    MUST be byte-identical now, at both speedups; reverting the fresh-World3D pin re-fails
    it. A within-session twin is required -- cross-session was clean even unfixed."""
    s1, s2, result, nticks = _reset_twin_snapshots(_src(_TUMBLE_3D), [None] * 40, 40, speedup)
    assert s1 == s2, (speedup, s1, s2)
    # the fixture actually drives a contact-terminated episode (not a trivial no-op that
    # would pass the twin vacuously) -- guards the reproducer from silently going inert.
    assert result == "failure" and nticks <= 20, (result, nticks)


@requires_godot
@pytest.mark.parametrize("speedup", [1, 8])
def test_mini_collect_3d_reset_determinism_is_byte_identical(speedup):
    """The mandated 3D determinism pin: mini_collect_3d stays byte-identical across two
    back-to-back seeded resets on one serve host, at both speedups (a broad 3D-determinism
    guard alongside the sharper tumble_3d reproducer)."""
    s1, s2, _, _ = _reset_twin_snapshots(_src(_MINI_3D), ["right"] * 30, 30, speedup)
    assert s1 == s2, (speedup, s1, s2)


@requires_godot
def test_2d_reset_determinism_unchanged_within_session():
    """The fresh-World3D pin touches ONLY World3D; a 2D game's back-to-back resets on one
    serve host stay byte-identical (root.world_2d is never refreshed) -> 2D replays are
    unchanged. Covered on the certified mini_collect + walled_goal fixtures."""
    for path, plan in ((_MINI, ["up"] * 12 + ["right"] * 16),
                       (_WALLED, ["right"] * 20 + ["up"] * 10)):
        s1, s2, _, _ = _reset_twin_snapshots(_src(path), plan, len(plan))
        assert s1 == s2, (path, s1, s2)


# ====================================================================== #
# 2c. Capture-lane tick parity -- the NEW invariant (notes/engines/DETERMINISM_3D.md,
#     "Capture-lane tick parity"). A witness replayed through the CAPTURE host
#     (godotworld/capture_host.gd) produces the SAME per-tick state trail as through the
#     SERVE host (serve_game.gd) -- so "the demo IS the certified witness", byte-for-byte.
#     Guards the two capture-host pins (settle with an IDLE frame BEFORE build so ZERO
#     physics steps run before the first act; fresh World3D for the 3D context).
# ====================================================================== #
def _canon_serve_frames(frames) -> dict:
    """A serve frames trail ({tick, entities:{name:{pos,vel,angle}}}) -> a comparable
    {tick: {name: (pos_tuple, vel_tuple, angle)}} at parsed-double precision."""
    out = {}
    for fr in frames:
        ents = {}
        for name, q in fr["entities"].items():
            ents[name] = (tuple(float(x) for x in q.get("pos", [])),
                          tuple(float(x) for x in q.get("vel", [])),
                          float(q.get("angle", 0.0)))
        out[int(fr["tick"])] = ents
    return out


def _parse_capture_fingerprint(path: str) -> dict:
    """capture_host.gd's --fingerprint file ('tick|name:pos_csv:vel_csv:angle;...', all at
    %.17f) -> the SAME {tick: {name: (pos, vel, angle)}} shape as _canon_serve_frames."""
    out = {}
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    for line in text.splitlines():
        if "|" not in line:
            continue
        ts, bs = line.split("|", 1)
        ents = {}
        for part in (bs.split(";") if bs else []):
            if not part:
                continue
            f = part.split(":")
            ents[f[0]] = (tuple(float(x) for x in f[1].split(",") if x != ""),
                          tuple(float(x) for x in f[2].split(",") if x != ""),
                          float(f[3]))
        out[int(ts)] = ents
    return out


def _serve_trail(src, seed, actions, speedup):
    """The serve per-tick trail for (seed, actions) at `speedup`."""
    old = os.environ.get("HARNESS_GODOT_SPEEDUP")
    os.environ["HARNESS_GODOT_SPEEDUP"] = str(speedup)
    try:
        ex = GdExecutor(port_base=_free_port())
        try:
            rec = ex.run_batch(src, [{"seed": int(seed), "actions": list(actions)}],
                               len(actions), frames_every=1)[0]
        finally:
            ex.close()
    finally:
        if old is None:
            os.environ.pop("HARNESS_GODOT_SPEEDUP", None)
        else:
            os.environ["HARNESS_GODOT_SPEEDUP"] = old
    return _canon_serve_frames(rec["frames"]), rec["result"], rec["ticks"]


def _capture_trail(game_path, seed, actions, speedup, dress=False, no_frames=True):
    """The capture-host per-tick trail: run capture_host.gd HEADLESS and parse its fingerprint,
    stepping the SAME act+K discipline serve uses. `--speedup=N` pins the same paired physics
    scaling.

    ``no_frames`` (default) is the fingerprint-only fast path (no _grab, so the dresser's per-tick
    sync() never runs). Set ``no_frames=False`` to exercise the REAL frame-grabbing path headless:
    _grab runs sync() every tick (the software-GL force_draw/readback are inert under the dummy
    rasteriser, but the mirror READ still happens) -- this is the path a float-sensitive replay
    used to diverge on, so it is what a zero-contact regression test must drive."""
    project = default_godot_project()
    work = tempfile.mkdtemp(prefix="parity_")
    try:
        witness = os.path.join(work, "witness.json")
        with open(witness, "w", encoding="utf-8") as fh:
            json.dump({"seed": int(seed), "actions": [str(a) for a in actions]}, fh)
        fp = os.path.join(work, "fp.txt")
        argv = [GODOT_EXE, "--headless", "--path", project, "-s",
                "res://capture_host.gd", "--",
                "--capture", "--game-file=%s" % os.path.abspath(game_path),
                "--actions-file=%s" % witness,
                "--out=%s" % os.path.join(work, "frames"),
                "--fingerprint=%s" % fp,
                "--speedup=%d" % int(speedup)]
        if no_frames:
            argv.append("--no-frames")
        if not dress:
            argv.append("--no-dress")
        env = scrubbed_env()
        env["HARNESS_GODOT_EXE"] = GODOT_EXE
        subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       stdin=subprocess.DEVNULL, env=env, timeout=240)
        assert os.path.isfile(fp), "capture host wrote no fingerprint"
        meta = {}
        mp = os.path.join(work, "frames", "meta.json")
        if os.path.isfile(mp):
            meta = json.loads(open(mp, encoding="utf-8").read())
        return _parse_capture_fingerprint(fp), meta
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _first_divergent(serve, cap, speedup):
    for t in sorted(serve):
        if cap.get(t) != serve.get(t):
            return "speedup=%d first divergent tick=%d:\n  serve=%r\n  cap  =%r" % (
                speedup, t, serve.get(t), cap.get(t))
    return "trails differ (serve %d ticks, capture %d ticks)" % (
        len(serve), len(cap))


@requires_godot
@pytest.mark.parametrize("speedup", [1, 8])
def test_capture_replay_matches_serve_tumble_3d(speedup):
    """CAPTURE-LANE PARITY on the force-driven contact fixture: a witness replayed through
    capture_host.gd is byte-for-byte the serve_game.gd trail. tumble_3d falls under gravity
    from tick 1, so a capture host that steps ONE extra physics frame before the first act
    (the pre-fix `await physics_frame` settle) led serve by exactly one v*dt step and diverged
    from tick 1 -- this re-fails if that regresses. Held at both speedups."""
    src = _src(_TUMBLE_3D)
    actions = ["push"] * 30                      # act() is a no-op; the body free-falls
    serve, result, nticks = _serve_trail(src, 0, actions, speedup)
    cap, _meta = _capture_trail(_TUMBLE_3D, 0, actions, speedup)
    # not a vacuous no-op: the fixture drives a real contact-terminated episode
    assert result == "failure" and 0 < nticks <= 25, (result, nticks)
    assert cap == serve, _first_divergent(serve, cap, speedup)


@requires_godot
@pytest.mark.parametrize("speedup", [1, 8])
def test_capture_replay_matches_serve_mini_collect_3d(speedup):
    """The mandated broad 3D capture-parity guard: the impulse-driven mini_collect_3d replays
    byte-identical through capture vs serve, at both speedups."""
    src = _src(_MINI_3D)
    actions = ["right"] * 30
    serve, _r, _n = _serve_trail(src, 0, actions, speedup)
    cap, _meta = _capture_trail(_MINI_3D, 0, actions, speedup)
    assert cap == serve, _first_divergent(serve, cap, speedup)


@requires_godot
def test_capture_replay_matches_serve_2d_untouched():
    """The capture-lane pins keep 2D byte-identical: the World3D pin is 3D-guarded (a 2D game
    has no World3D), and the pre-build idle settle steps zero physics for 2D too. A 2D witness
    replays identically through capture vs serve (the 2D path stays untouched)."""
    src = _src(_MINI)
    actions = ["up"] * 12 + ["right"] * 16
    serve, _r, _n = _serve_trail(src, 0, actions, 1)
    cap, _meta = _capture_trail(_MINI, 0, actions, 1)
    assert cap == serve, _first_divergent(serve, cap, 1)


@requires_godot
def test_capture_dressed_equals_undressed_tumble_3d():
    """The visual dresser is a ZERO-CONTACT overlay (proxies in a sibling subtree, transforms
    mirrored read-only): a dressed capture and an undressed one share an identical state trail,
    because the game tree/physics are never mutated."""
    plain, _ = _capture_trail(_TUMBLE_3D, 0, ["push"] * 20, 1, dress=False)
    dressed, _ = _capture_trail(_TUMBLE_3D, 0, ["push"] * 20, 1, dress=True)
    assert plain == dressed


@requires_godot
def test_capture_framegrab_dressed_matches_serve_float_sensitive_2d():
    """ZERO-CONTACT under the REAL frame-grabbing path on a FLOAT-SENSITIVE 2D game.

    The frame-grab lane calls the dresser's sync() every tick. sync() must mirror from the body's
    stored .position/.rotation, NOT read the shape's .global_transform: a .global_transform read
    mid-physics forces a transform-notification flush that perturbs a chaotic replay (the pachinko
    ball's bounce cascade diverges within ~50 ticks; a certified debris-docking game diverged at
    tick 43 and crashed at 130 that docks at 275). Driving _capture_trail with no_frames=False
    runs that exact mirror-during-stepping path headless, and it must stay byte-for-byte on serve
    -- INCLUDING well past the tick where the old .global_transform read diverged. Guards the fix
    in visual_dress.gd sync()/_precompute_mirror_2d(); this whole trail is identical to the
    --no-frames one, proving the mirror read is inert."""
    actions = ["noop"] * 160
    serve, result, nticks = _serve_trail(_src(_BOUNCE_2D), 0, actions, 1)
    cap, _meta = _capture_trail(_BOUNCE_2D, 0, actions, 1, dress=True, no_frames=False)
    # A genuinely long, physics-driven episode (not a vacuous 1-tick trail).
    assert nticks >= 120, nticks
    assert cap == serve, _first_divergent(serve, cap, 1)
    # Explicitly assert parity holds PAST the tick the old bug first diverged on (43) and crashed
    # (130) -- the regression this test exists for.
    for t in (44, 80, 130, 160):
        if t in serve:
            assert cap.get(t) == serve.get(t), "divergence at tick %d" % t


@requires_godot
def test_gd_serve_collect_mechanic_latches():
    """Driving straight up to gem_a latches the first milestone through the serve seam
    (the collect mechanic + per-step latching work). got_both is proven end-to-end by
    the G3 witness in test_mini_collect_certifies_g0_g3."""
    with open(_MINI, "r", encoding="utf-8") as fh:
        src = fh.read()
    ex = GdExecutor(port_base=_free_port())
    try:
        rec = ex.run_batch(src, [{"seed": 0, "actions": ["up"] * 14}], 14)[0]
    finally:
        ex.close()
    assert rec["checkpoints"]["got_first"] is not None, rec["checkpoints"]
    assert rec["checkpoints"]["got_both"] is None       # one gem only from a pure "up"


# ====================================================================== #
# 3. Parse gate + contract probe rejections
# ====================================================================== #
@requires_godot
def test_parse_gate_rejects_syntax_error(tmp_path):
    p = tmp_path / "broken.gd"
    p.write_text("extends Node2D\nfunc build(world_seed):\n\tvar x = = =\n",
                 encoding="utf-8")
    rep = verify_game(str(p), sandboxed=False)
    assert rep["engine"] == "gdscript"
    assert rep["failure_class"] == "ENV_ERROR"
    assert rep["layers"]["G0_static"]["checks"]["loads"]["pass"] is False


@requires_godot
def test_contract_probe_rejects_missing_method(tmp_path):
    # A syntactically valid game that FORGETS state() -> the contract probe rejects it.
    p = tmp_path / "no_state.gd"
    p.write_text(
        "extends Node2D\n"
        "func build(world_seed):\n"
        "\tvar b = RigidBody2D.new()\n"
        "\tadd_child(b)\n"
        "func act(action): pass\n"
        "func checkpoints(): return {\"done\": false}\n"
        "func is_success(): return false\n"
        "func is_failure(): return false\n"
        "func actions(): return [\"a\", \"b\"]\n",
        encoding="utf-8")
    rep = verify_game(str(p), sandboxed=False)
    assert rep["engine"] == "gdscript"
    assert rep["failure_class"] == "ENV_ERROR"
    symbols = rep["layers"]["G0_static"]["checks"]["symbols"]
    assert symbols["pass"] is False
    assert "state" in symbols["missing"], symbols


# ====================================================================== #
# 4. Env scrub does not break the lane (secret in the parent -> still certifies)
# ====================================================================== #
# ====================================================================== #
# 5. Per-tick frame capture (replay/render substrate)
# ====================================================================== #
@requires_godot
def test_gd_serve_emits_per_tick_frames():
    """frames_every=1 -> a per-tick {tick, entities:{name: query}} trail: a t=0
    frame, monotone ticks, one frame per applied tick ending at the final tick,
    every scene non-empty with positional queries. The final_snapshot and terminal
    keys are unchanged from the frame-free batch."""
    with open(_MINI, "r", encoding="utf-8") as fh:
        src = fh.read()
    plan = ["up"] * 8 + ["right"] * 6
    ex = GdExecutor(port_base=_free_port())
    try:
        rec = ex.run_batch(src, [{"seed": 0, "actions": plan}], len(plan),
                            frames_every=1)[0]
    finally:
        ex.close()
    frames = rec["frames"]
    assert frames[0]["tick"] == 0                       # fresh-world frame
    ticks = [fr["tick"] for fr in frames]
    assert ticks == sorted(ticks)                       # monotone
    assert frames[-1]["tick"] == rec["ticks"]           # ends at the applied tick
    for fr in frames:
        assert set(fr) == {"tick", "entities"}
        assert fr["entities"]                           # non-empty scene
        q = next(iter(fr["entities"].values()))
        assert "pos" in q and len(q["pos"]) >= 2         # positional query


@requires_godot
def test_gd_frames_off_is_byte_identical_batch():
    """frames_every=0 (the batch default) captures NO frames and emits no "frames"
    key, and the final_snapshot matches a framed run tick-for-tick (frames ride
    alongside; they never perturb the stepping)."""
    with open(_MINI, "r", encoding="utf-8") as fh:
        src = fh.read()
    plan = ["up"] * 8 + ["right"] * 6
    ex = GdExecutor(port_base=_free_port())
    try:
        plain = ex.run_batch(src, [{"seed": 0, "actions": plan}], len(plan))[0]
        framed = ex.run_batch(src, [{"seed": 0, "actions": plan}], len(plan),
                              frames_every=1)[0]
    finally:
        ex.close()
    assert "frames" not in plain                         # no key in batch mode
    assert plain["final_snapshot"] == framed["final_snapshot"]
    assert plain["checkpoints"] == framed["checkpoints"]
    assert plain["ticks"] == framed["ticks"]


@requires_godot
def test_gd_lane_verifies_under_scrubbed_env(monkeypatch):
    """With OPENROUTER_API_KEY set in the PARENT, the game still certifies — Godot
    starts fine under the minimal scrubbed env, and (per the pure-python wiring test)
    the child never receives the key."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-parent-should-not-leak")
    rep = verify_game(_MINI, sandboxed=False)
    assert rep["passed"] is True, rep
    assert rep["engine"] == "gdscript"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
