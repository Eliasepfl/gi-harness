"""The visual dresser FOLLOWS state() for shapeless bodies (skipped without Godot).

Drives ``godotworld/tests/test_dress_state.gd`` in-image and asserts on its OK/FAIL markers.
The GDScript side is where the assertions live (it needs a live scene tree); this module is
the pytest seam that runs it and surfaces the failures.

The bug this pins: a hand-sim body that exists ONLY in state() (no CollisionShape to ride)
never animated -- the demo GIF was frozen. The dresser now synthesizes a render-only proxy
for such a body and drives it from the per-tick state() trail the capture host passes to
sync(positions), while shape-backed bodies keep node-riding by default (byte-identical).
See godotworld/tests/test_dress_state.gd for the properties pinned here.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.verify.executors import find_godot_exe  # noqa: E402
from harness.verify.godot_exec import default_godot_project, scrubbed_env  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GD = os.path.join(_ROOT, "tests", "fixtures", "gd_games")
_HANDSIM_3D = os.path.join(_GD, "handsim_3d.gd")     # shapeless controlled craft + shaped gate
_HANDSIM_2D = os.path.join(_GD, "handsim_2d.gd")     # shapeless controlled craft + shaped wall

GODOT_EXE = find_godot_exe()
requires_godot = pytest.mark.skipif(GODOT_EXE is None, reason="Godot binary not present")


def _run_state() -> str:
    """Run the in-image dresser-follows-state test; return its full log."""
    project = default_godot_project()
    work = tempfile.mkdtemp(prefix="dstate_")
    try:
        logf = os.path.join(work, "dstate.log")
        argv = [GODOT_EXE, "--headless", "--path", project,
                "--script", "res://tests/test_dress_state.gd", "--",
                _HANDSIM_3D, _HANDSIM_2D, logf]
        env = scrubbed_env()
        env["HARNESS_GODOT_EXE"] = GODOT_EXE
        # The dresser must not be steered by an inherited knob: this suite pins the DEFAULT.
        env.pop("HARNESS_DRESS_MODE", None)
        env.pop("HARNESS_DRESS_STATE_FOLLOW", None)
        proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, env=env, timeout=240)
        out = proc.stdout.decode("utf-8", "replace")
        if os.path.isfile(logf):
            with open(logf, encoding="utf-8") as fh:
                out += "\n" + fh.read()
        assert "DSTATE_DONE" in out, "state test did not complete:\n%s" % out[-3000:]
        return out
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)


@pytest.fixture(scope="module")
def state_log() -> str:
    return _run_state()


@requires_godot
def test_dress_state_all_pass(state_log):
    """Every property the in-image test pins holds (and it pinned a real number of them)."""
    m = re.search(r"DSTATE_DONE pass=(\d+) fail=(\d+)", state_log)
    assert m, state_log[-3000:]
    passed, failed = int(m.group(1)), int(m.group(2))
    fails = [ln for ln in state_log.splitlines() if ln.startswith("DSTATE_FAIL")]
    assert failed == 0, "dresser-follows-state failures:\n  " + "\n  ".join(fails)
    # Non-vacuous: all 18 checks ran (a fixture that silently failed to build would pass 0).
    assert passed == 18, "expected 18 checks, got %d:\n%s" % (passed, state_log[-3000:])


@requires_godot
@pytest.mark.parametrize("marker", [
    "pure_3d_pos", "pure_3d_yaw", "pure_3d_euler", "pure_2d_pos", "pure_2d_rot",
    "synth_craft_is_synthesized", "synth_craft_follows_trail",
    "shape_gate_node_rides_by_default", "empty_pos_synth_holds",
    "empty_pos_shape_node_rides", "unified_gate_follows_trail",
    "synth2d_craft_is_synthesized", "synth2d_craft_follows_trail",
    "shape2d_wall_node_rides_by_default",
])
def test_named_property_holds(state_log, marker):
    """Each headline property, named individually so a regression report says WHICH one broke."""
    assert ("DSTATE_OK " + marker) in state_log, \
        "property not proven: %r\n%s" % (marker, state_log[-2000:])
