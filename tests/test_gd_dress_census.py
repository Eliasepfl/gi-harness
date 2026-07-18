"""The visual dresser RESPECTS what a game authored for itself (skipped without Godot).

Drives ``godotworld/tests/test_dress_census.gd`` in-image and asserts on its OK/FAIL markers.
The GDScript side is where the assertions live (it needs a live scene tree, a viewport and a
current camera); this module is the pytest seam that runs it and surfaces the failures.

The audited bug: the dresser read ONLY collision geometry, so it recoloured every body from a
fixed 4-role palette, stamped one procedural sky + gray ground + sun on every 3D game, and
make_current()'d its own camera -- discarding the fiction-specific visuals 14 of the 22
certified games author for themselves. A related symptom on a game that DID author its visuals
but no camera (KNOCKDOWN): nothing was hidden, but the dresser's own overview camera framed the
entire collision AABB -- an 80-wide world-bounds ground slab -- so the gameplay near the origin
rendered as a distant speck under the game's own sky ("authored game renders gray"). The fix
frames on a gameplay-content box that drops outsized static ground/backdrop from the zoom. See
godotworld/tests/test_dress_census.gd for the six properties pinned here.
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
_BARE_3D = os.path.join(_GD, "mini_collect_3d.gd")          # authors NOTHING (the fallback path)
_AUTHORED_3D = os.path.join(_GD, "dressed_arena_3d.gd")     # meshes + camera + sun + sky
_AUTHORED_2D = os.path.join(_GD, "dressed_lander_2d.gd")    # polygons + camera
_AUTHORED_NOCAM_3D = os.path.join(_GD, "dressed_ground_3d.gd")  # meshes+sun+sky, NO camera, big floor

GODOT_EXE = find_godot_exe()
requires_godot = pytest.mark.skipif(GODOT_EXE is None, reason="Godot binary not present")


def _run_census() -> str:
    """Run the in-image census test; return its full log."""
    project = default_godot_project()
    work = tempfile.mkdtemp(prefix="census_")
    try:
        logf = os.path.join(work, "census.log")
        argv = [GODOT_EXE, "--headless", "--path", project,
                "--script", "res://tests/test_dress_census.gd", "--",
                _BARE_3D, _AUTHORED_3D, _AUTHORED_2D, _AUTHORED_NOCAM_3D, logf]
        env = scrubbed_env()
        env["HARNESS_GODOT_EXE"] = GODOT_EXE
        # The dresser must not be steered by an inherited knob: this suite pins the DEFAULT.
        env.pop("HARNESS_DRESS_MODE", None)
        proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, env=env, timeout=240)
        out = proc.stdout.decode("utf-8", "replace")
        if os.path.isfile(logf):
            with open(logf, encoding="utf-8") as fh:
                out += "\n" + fh.read()
        assert "CENSUS_DONE" in out, "census test did not complete:\n%s" % out[-3000:]
        return out
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)


@pytest.fixture(scope="module")
def census_log() -> str:
    return _run_census()


@requires_godot
def test_dress_census_all_pass(census_log):
    """Every property the in-image census test pins holds (and it pinned a real number of them)."""
    m = re.search(r"CENSUS_DONE pass=(\d+) fail=(\d+)", census_log)
    assert m, census_log[-3000:]
    passed, failed = int(m.group(1)), int(m.group(2))
    fails = [ln for ln in census_log.splitlines() if ln.startswith("CENSUS_FAIL")]
    assert failed == 0, "dresser census failures:\n  " + "\n  ".join(fails)
    # Non-vacuous: all 28 checks ran (a fixture that silently failed to build would pass 0).
    assert passed == 28, "expected 28 census checks, got %d:\n%s" % (passed, census_log[-3000:])


@requires_godot
@pytest.mark.parametrize("marker", [
    "authored puck is NOT proxied",
    "authored guard is NOT proxied",
    "un-authored pad IS still proxied",
    "the game's OWN Camera3D is still the current camera",
    "no generic sky over authored env",
    "no generic sun over authored light",
    "no generic ground over the authored arena floor",
    "authored probe is NOT proxied and NOT haloed",
    "the game's OWN Camera2D is still the current camera",
    "no slate backdrop over the authored terrain",
    "bare game: auto == proxy",
    "proxy mode: the dresser's camera is current again",
    "authored block is NOT proxied",
    "un-authored zone IS still proxied",
    "no generic sky over the authored env (no-camera game)",
    "no generic sun over the authored light (no-camera game)",
    "dresser owns the overview camera when the game authored none",
])
def test_named_property_holds(census_log, marker):
    """Each headline property, named individually so a regression report says WHICH one broke."""
    assert ("CENSUS_OK " + marker) in census_log, \
        "property not proven: %r\n%s" % (marker, census_log[-2000:])
