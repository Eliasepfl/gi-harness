"""In-image CHORD tests (CHORD pivot, Phase 1 -- HOST-ONLY). Gated by the Godot binary.

Multiple keys may be pressed in ONE decision tick. On the wire a chord is a JSON array
(``["thrust_forward","thrust_up"]``); a single verb stays a plain string. These tests
drive array chords through the SAME batch executor the funnel uses
(``GdExecutor.run_batch`` -> ``serve_game.gd``) and through the render/replay
``capture_host.gd``, asserting:

* twin determinism (Godot-run-twice) on a 2D and a 3D probe -> bit-exact trajectories;
* canonicalization: ``["a","b"]`` and ``["b","a"]`` sequences -> identical trajectories;
* legacy byte-identity: an existing single-verb witness still certifies unchanged, and a
  1-verb chord ``["up"]`` is bit-identical to the plain ``"up"``;
* physics composition: a one-tick chord's velocity is the EXACT sum of the two single
  impulses and differs from either single tick (both inputs hit the SAME physics state);
* capture parity: a chord sequence replayed through ``capture_host.gd`` reaches the same
  final state as serve.

The composition/determinism probes (``chord_probe_2d.gd`` / ``chord_probe_3d.gd``) are
TEST-ONLY fixtures with linear, uncapped, undamped physics so the sum is bit-exact; the
shipped collect fixtures cap speed (an isotropic clamp couples the axes). No production
game or the GameAPI contract is touched -- chords are a pure host capability.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.verify.capture import _child_env  # noqa: E402
from harness.verify.chord import wire_actions  # noqa: E402
from harness.verify.executors import find_godot_exe  # noqa: E402
from harness.verify.godot_exec import default_godot_project  # noqa: E402
from harness.verify.gd_exec import GdExecutor  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FIX = os.path.join(_ROOT, "tests", "fixtures", "gd_games")
MINI = os.path.join(_FIX, "mini_collect.gd")
PROBE_2D = os.path.join(_FIX, "chord_probe_2d.gd")
PROBE_3D = os.path.join(_FIX, "chord_probe_3d.gd")

GODOT_EXE = find_godot_exe()
requires_godot = pytest.mark.skipif(GODOT_EXE is None, reason="Godot binary not present")

# The certified single-verb winning witness for mini_collect.gd @ seed 0 (from
# tests/test_gd_rl.py) -- the legacy-acceptance regression guard.
WITNESS_SEED = 0
WITNESS_ACTIONS = ["up"] * 8 + ["right"] * 8 + ["down", "right"] * 8


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _run(src: str, episodes, max_ticks: int) -> list:
    """Run episodes through GdExecutor.run_batch on one fresh serve process."""
    ex = GdExecutor(port_base=_free_port())
    try:
        return ex.run_batch(src, episodes, max_ticks=max_ticks)
    finally:
        ex.close()


def _snap_equal(a: dict, b: dict) -> bool:
    """Bit-exact equality of two final_snapshot dicts (same engine, %.17f obs)."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---------------------------------------------------------- capture-host parity helper
def _capture_final_state(game_path: str, actions, seed: int = 0) -> dict:
    """Replay a witness through capture_host.gd (HEADLESS fingerprint scan, the same
    invocation capture.py's pre-scan uses) and return the FINAL tick's per-body state:
    ``{name: {"pos": [...], "vel": [...], "angle": float}}`` parsed from the %.17f
    fingerprint. A chord element in ``actions`` is preserved as a JSON array via
    wire_actions (exactly as the render lane now writes witnesses)."""
    work = tempfile.mkdtemp(prefix="chordcap_")
    try:
        witness = os.path.join(work, "w.json")
        with open(witness, "w", encoding="utf-8") as fh:
            json.dump({"seed": int(seed), "actions": wire_actions(actions)}, fh)
        fp = os.path.join(work, "fp.txt")
        argv = [GODOT_EXE, "--headless", "--path", default_godot_project(),
                "-s", "res://capture_host.gd", "--", "--capture",
                "--game-file=%s" % os.path.abspath(game_path),
                "--actions-file=%s" % witness,
                "--out=%s" % os.path.join(work, "frames"),
                "--fingerprint=%s" % fp, "--no-frames", "--no-dress", "--speedup=1"]
        subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       stdin=subprocess.DEVNULL, env=_child_env(), timeout=180)
        assert os.path.isfile(fp), "capture_host wrote no fingerprint"
        text = open(fp, "r", encoding="utf-8").read().strip()
        last = [ln for ln in text.splitlines() if "|" in ln][-1]
        _tick, body_str = last.split("|", 1)
        out: dict = {}
        for part in body_str.split(";"):
            if not part:
                continue
            name, pos_s, vel_s, ang_s = part.split(":")
            out[name] = {
                "pos": [float(x) for x in pos_s.split(",") if x != ""],
                "vel": [float(x) for x in vel_s.split(",") if x != ""],
                "angle": float(ang_s),
            }
        return out
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)


# ====================================================================== #
# Twin determinism (Godot-run-twice) -- 2D + 3D
# ====================================================================== #
@requires_godot
def test_chord_twin_determinism_2d():
    """Same chord sequence, two independent serve sessions -> bit-exact trajectory."""
    src = _read(PROBE_2D)
    seq = [["vx", "vy"], "vx", ["vy", "vx"], "vy", ["vx", "vy"]]
    r1 = _run(src, [{"seed": 0, "actions": seq}], max_ticks=len(seq))[0]
    r2 = _run(src, [{"seed": 0, "actions": seq}], max_ticks=len(seq))[0]
    assert r1["ticks"] == r2["ticks"] == len(seq)
    assert _snap_equal(r1["final_snapshot"], r2["final_snapshot"])


@requires_godot
def test_chord_twin_determinism_3d():
    """The 3D twin -- true-3D probe, chords across x/y/z, bit-exact across sessions."""
    src = _read(PROBE_3D)
    seq = [["vx", "vy"], "vz", ["vx", "vz"], ["vy", "vz"], ["vx", "vy"]]
    r1 = _run(src, [{"seed": 0, "actions": seq}], max_ticks=len(seq))[0]
    r2 = _run(src, [{"seed": 0, "actions": seq}], max_ticks=len(seq))[0]
    assert r1["ticks"] == r2["ticks"] == len(seq)
    assert _snap_equal(r1["final_snapshot"], r2["final_snapshot"])


# ====================================================================== #
# Canonicalization -- element order must not matter
# ====================================================================== #
@requires_godot
def test_chord_canonicalization_order_independent():
    """["up","right"] and ["right","up"] sequences -> IDENTICAL trajectories on an
    existing certified fixture (mini_collect)."""
    src = _read(MINI)
    ab = [["up", "right"]] * 6
    ba = [["right", "up"]] * 6
    rab = _run(src, [{"seed": 0, "actions": ab}], max_ticks=len(ab))[0]
    rba = _run(src, [{"seed": 0, "actions": ba}], max_ticks=len(ba))[0]
    assert _snap_equal(rab["final_snapshot"], rba["final_snapshot"])


# ====================================================================== #
# Legacy byte-identity -- single verbs untouched
# ====================================================================== #
@requires_godot
def test_legacy_single_verb_witness_still_certifies():
    """The existing single-verb winning witness MUST still replay to SUCCESS through the
    chord-aware host -- the regression guard that single-verb traffic is unchanged."""
    src = _read(MINI)
    rec = _run(src, [{"seed": WITNESS_SEED, "actions": WITNESS_ACTIONS}],
               max_ticks=len(WITNESS_ACTIONS))[0]
    assert rec["result"] == "success", rec


@requires_godot
def test_singleton_chord_is_byte_identical_to_single_verb():
    """A 1-verb chord ["up"] and the plain "up" produce bit-identical trajectories:
    the boundary collapses a singleton chord to the legacy single-verb wire form."""
    src = _read(MINI)
    plain = ["up"] * 5 + ["right"] * 5
    chord = [["up"]] * 5 + [["right"]] * 5
    rp = _run(src, [{"seed": 0, "actions": plain}], max_ticks=len(plain))[0]
    rc = _run(src, [{"seed": 0, "actions": chord}], max_ticks=len(chord))[0]
    assert _snap_equal(rp["final_snapshot"], rc["final_snapshot"])


# ====================================================================== #
# Physics composition -- both impulses hit the SAME physics state
# ====================================================================== #
@requires_godot
def test_chord_physics_composition_2d():
    """One tick of the chord ["vx","vy"] yields velocity == (vx-tick vel) + (vy-tick vel),
    bit-exactly, and differs from either single tick -- the one-tick diagonal that only a
    same-tick chord can produce."""
    src = _read(PROBE_2D)
    recs = _run(src, [
        {"seed": 0, "actions": ["vx"]},
        {"seed": 0, "actions": ["vy"]},
        {"seed": 0, "actions": [["vx", "vy"]]},
    ], max_ticks=1)
    vx = recs[0]["final_snapshot"]["body"]["vel"]
    vy = recs[1]["final_snapshot"]["body"]["vel"]
    ch = recs[2]["final_snapshot"]["body"]["vel"]
    # Exact component-wise sum.
    assert ch == [vx[0] + vy[0], vx[1] + vy[1]], (vx, vy, ch)
    # Both components present; distinct from either single-verb tick.
    assert ch[0] != 0.0 and ch[1] != 0.0
    assert ch != vx and ch != vy


@requires_godot
def test_chord_physics_composition_3d():
    """The 3D composition: ["vx","vz"] velocity == vx-tick + vz-tick, exactly."""
    src = _read(PROBE_3D)
    recs = _run(src, [
        {"seed": 0, "actions": ["vx"]},
        {"seed": 0, "actions": ["vz"]},
        {"seed": 0, "actions": [["vx", "vz"]]},
    ], max_ticks=1)
    vx = recs[0]["final_snapshot"]["body"]["vel"]
    vz = recs[1]["final_snapshot"]["body"]["vel"]
    ch = recs[2]["final_snapshot"]["body"]["vel"]
    assert ch == [vx[i] + vz[i] for i in range(3)], (vx, vz, ch)
    assert ch[0] != 0.0 and ch[2] != 0.0
    assert ch != vx and ch != vz


# ====================================================================== #
# Capture parity -- render/replay host reaches the same final state as serve
# ====================================================================== #
@requires_godot
def test_chord_capture_parity():
    """A chord-containing sequence replayed through capture_host.gd reaches the SAME final
    state as serve (both apply the identical ChordUtil boundary + same physics)."""
    src = _read(MINI)
    seq = [["up", "right"], "up", ["down", "right"], "left", ["up", "left"], "right"]
    serve = _run(src, [{"seed": 0, "actions": seq}], max_ticks=len(seq))[0]
    serve_player = serve["final_snapshot"]["player"]
    cap = _capture_final_state(MINI, seq, seed=0)
    assert "player" in cap, cap
    assert np.allclose(serve_player["pos"], cap["player"]["pos"], atol=1e-9, rtol=0), \
        (serve_player["pos"], cap["player"]["pos"])
    assert np.allclose(serve_player["vel"], cap["player"]["vel"], atol=1e-9, rtol=0), \
        (serve_player["vel"], cap["player"]["vel"])
