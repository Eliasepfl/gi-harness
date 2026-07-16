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

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.verify.executors import find_godot_exe  # noqa: E402
from harness.verify.gameverify import verify_game  # noqa: E402
from harness.verify.gd_exec import GdExecutor  # noqa: E402

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
