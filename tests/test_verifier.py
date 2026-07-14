"""Tests for module B (sandbox + verifier).

Independent of module A: a tiny `FakeSDK` engine (just what the layers need) and
inline scene sources let us test the AST scan and the whole L0->L1->L2 funnel
in-process, plus the subprocess timeout.
"""

from __future__ import annotations

import os
import sys

import pytest

# Make `harness` importable regardless of the pytest rootdir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.core.sandbox import run_sandboxed, scan_source  # noqa: E402
from harness.legacy.verifier import make_report, verify_scene  # noqa: E402

WORLD = (800, 600)
GRAV = 900.0
DT = 1.0 / 60.0
CONTACT_TOL = 1.0
SUPPORT_EPS = 2.0


# ====================================================================== #
# Test engine (duck-typed on the SceneSDK contract)
# ====================================================================== #
class _Entity:
    def __init__(self, name, pos, hw, hh, body_type, mass=1.0,
                 is_agent=False, sensor=False):
        self.name = name
        self.pos = [float(pos[0]), float(pos[1])]
        self.vel = [0.0, 0.0]
        self.angle = 0.0
        self.angular_vel = 0.0
        self.hw = float(hw)
        self.hh = float(hh)
        self.body_type = body_type
        self.mass = mass
        self.is_agent = is_agent
        self.sensor = sensor

    def bbox(self):
        return [self.pos[0] - self.hw, self.pos[1] - self.hh,
                self.pos[0] + self.hw, self.pos[1] + self.hh]

    def bottom(self):
        return self.pos[1] - self.hh

    def top(self):
        return self.pos[1] + self.hh


class FakeSDK:
    """Tiny deterministic 2D engine covering the API the layers depend on.

    gravity=False freezes the scene (useful to test agent_supported / L2).
    nan_at triggers a numerical explosion at the given step.
    """

    def __init__(self, seed=0, world=WORLD, *, gravity=True, nan_at=None):
        self.world = world
        self.gravity = gravity
        self.nan_at = nan_at
        self._ents = {}
        self._flags = {}
        self._events = []
        self._step_count = 0
        self._exploded = False

    # ---- construction (subset of the contract) ----
    def add_ground(self, friction=0.9):
        w = self.world[0]
        # Top at y=0; overflows slightly like the real segment (radius).
        self._ents["ground"] = _Entity("ground", (w / 2, -1.0), w / 2, 1.0, "static")
        return "ground"

    def add_wall(self, name, a, b, friction=0.9):
        cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        hw, hh = abs(b[0] - a[0]) / 2 + 1, abs(b[1] - a[1]) / 2 + 1
        self._ents[name] = _Entity(name, (cx, cy), hw, hh, "static")
        return name

    def add_box(self, name, pos, size=(40, 40), mass=1.0, *, body="dynamic",
                friction=0.7, elasticity=0.1):
        bt = "static" if body == "static" else "dynamic"
        self._ents[name] = _Entity(name, pos, size[0] / 2, size[1] / 2, bt, mass)
        return name

    def add_ball(self, name, pos, radius=15.0, mass=1.0, *, body="dynamic",
                 friction=0.7, elasticity=0.5):
        bt = "static" if body == "static" else "dynamic"
        self._ents[name] = _Entity(name, pos, radius, radius, bt, mass)
        return name

    def add_platform(self, name, pos, size=(120, 12)):
        return self.add_box(name, pos, size=size, body="static")

    def spawn_agent(self, pos, size=(24, 36), mass=1.0):
        self._ents["agent"] = _Entity("agent", pos, size[0] / 2, size[1] / 2,
                                       "dynamic", mass, is_agent=True)
        return "agent"

    def add_zone(self, name, pos, size):
        self._ents[name] = _Entity(name, pos, size[0] / 2, size[1] / 2,
                                    "static", sensor=True)
        return name

    def on_contact(self, a, b, flag, once=True):
        pass  # not required by the layers

    def set_flag(self, key, value):
        self._flags[key] = value
        self._events.append({"type": "flag_set", "key": key, "step": self._step_count})

    def get_flag(self, key, default=None):
        return self._flags.get(key, default)

    # ---- instrumentation ----
    def list_entities(self):
        return list(self._ents)

    def query(self, name):
        e = self._ents[name]
        return {
            "pos": list(e.pos),
            "vel": list(e.vel),
            "angle": e.angle,
            "angular_vel": e.angular_vel,
            "bbox": e.bbox(),
            "body_type": e.body_type,
            "is_agent": e.is_agent,
        }

    def _overlap(self, a, b):
        al, ab, ar, at = self._ents[a].bbox()
        bl, bb, br, bt = self._ents[b].bbox()
        ox = min(ar, br) - max(al, bl)
        oy = min(at, bt) - max(ab, bb)
        return ox, oy

    def penetration_depth(self, a, b):
        if self._ents[a].sensor or self._ents[b].sensor:
            return 0.0
        ox, oy = self._overlap(a, b)
        if ox > 0 and oy > 0:
            return min(ox, oy)
        return 0.0

    def contacts(self, a, b):
        ox, oy = self._overlap(a, b)
        return ox > -CONTACT_TOL and oy > -CONTACT_TOL

    def in_bounds(self, name):
        l, b, r, t = self._ents[name].bbox()
        w, h = self.world
        return l >= 0 and b >= 0 and r <= w and t <= h

    def total_kinetic_energy(self, names=None):
        names = names or list(self._ents)
        total = 0.0
        for n in names:
            e = self._ents[n]
            if e.body_type != "dynamic":
                continue
            total += 0.5 * e.mass * (e.vel[0] ** 2 + e.vel[1] ** 2)
        return total

    def teleport(self, name, pos):
        self._ents[name].pos = [float(pos[0]), float(pos[1])]

    def set_state(self, name, *, pos=None, vel=None, angle=None, angular_vel=None):
        e = self._ents[name]
        if pos is not None:
            e.pos = [float(pos[0]), float(pos[1])]
        if vel is not None:
            e.vel = [float(vel[0]), float(vel[1])]
        if angle is not None:
            e.angle = angle
        if angular_vel is not None:
            e.angular_vel = angular_vel

    def events(self):
        return list(self._events)

    def snapshot(self):
        return {n: {"pos": list(e.pos), "vel": list(e.vel),
                    "angle": e.angle, "angular_vel": e.angular_vel}
                for n, e in self._ents.items()}

    # ---- time ----
    def _supported(self, e):
        for other in self._ents.values():
            if other is e:
                continue
            ox = min(e.pos[0] + e.hw, other.pos[0] + other.hw) - \
                max(e.pos[0] - e.hw, other.pos[0] - other.hw)
            if ox <= 0:
                continue
            if other.pos[1] < e.pos[1] and abs(other.top() - e.bottom()) <= SUPPORT_EPS:
                return True
        return False

    def step(self, n=1):
        for _ in range(n):
            if self._exploded:
                return
            self._step_count += 1
            if self.nan_at is not None and self._step_count >= self.nan_at:
                self._exploded = True
                for e in self._ents.values():
                    if e.body_type == "dynamic":
                        e.pos[1] = float("inf")
                self._events.append({"type": "nan_detected", "step": self._step_count})
                return
            if not self.gravity:
                continue
            for e in self._ents.values():
                if e.body_type != "dynamic":
                    continue
                if self._supported(e):
                    e.vel = [0.0, 0.0]
                    e.angular_vel = 0.0
                    continue
                e.vel[1] -= GRAV * DT
                e.pos[1] += e.vel[1] * DT
                if e.bottom() <= 0.0:
                    e.pos[1] = e.hh
                    e.vel = [0.0, 0.0]


def factory(**kw):
    return lambda: FakeSDK(**kw)


def _write(tmp_path, name, source):
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return str(p)


# ====================================================================== #
# Inline scene sources
# ====================================================================== #
SCENE_VALID = '''
SCENE_DESCRIPTION = "bring the agent all the way to the right"
AVAILABLE_ACTIONS = ["left", "right", "jump", "noop"]

def build_scene(sdk):
    sdk.add_ground()
    sdk.spawn_agent((100, 18))
    sdk.add_box("crate", (300, 20), size=(40, 40))

def get_success(sdk):
    return sdk.query("agent")["pos"][0] > 700
'''

SCENE_NO_AGENT = '''
SCENE_DESCRIPTION = "no agent"
AVAILABLE_ACTIONS = ["noop"]

def build_scene(sdk):
    sdk.add_ground()
    sdk.add_box("crate", (300, 20), size=(40, 40))

def get_success(sdk):
    return False
'''

SCENE_PENETRATION = '''
SCENE_DESCRIPTION = "overlapping boxes"
AVAILABLE_ACTIONS = ["noop"]

def build_scene(sdk):
    sdk.add_ground()
    sdk.spawn_agent((100, 18))
    sdk.add_box("a", (300, 20), size=(40, 40))
    sdk.add_box("b", (310, 20), size=(40, 40))

def get_success(sdk):
    return False
'''

SCENE_FLOATING = '''
SCENE_DESCRIPTION = "floating object that will fall"
AVAILABLE_ACTIONS = ["noop"]

def build_scene(sdk):
    sdk.add_ground()
    sdk.spawn_agent((100, 18))
    sdk.add_box("floater", (400, 400), size=(40, 40))

def get_success(sdk):
    return sdk.query("floater")["pos"][1] < 5
'''

SCENE_TRIVIAL_GOAL = '''
SCENE_DESCRIPTION = "goal already reached"
AVAILABLE_ACTIONS = ["noop"]

def build_scene(sdk):
    sdk.add_ground()
    sdk.spawn_agent((100, 18))
    sdk.add_box("crate", (300, 20), size=(40, 40))

def get_success(sdk):
    return True
'''

SCENE_IMPURE_GOAL = '''
SCENE_DESCRIPTION = "get_success has a side effect"
AVAILABLE_ACTIONS = ["noop"]

def build_scene(sdk):
    sdk.add_ground()
    sdk.spawn_agent((100, 18))
    sdk.add_box("crate", (300, 20), size=(40, 40))

def get_success(sdk):
    sdk.teleport("crate", (500, 20))
    return False
'''

SCENE_BAD_IMPORT = '''
import os
SCENE_DESCRIPTION = "malicious"
AVAILABLE_ACTIONS = ["noop"]

def build_scene(sdk):
    os.system("echo pwned")
    sdk.add_ground()

def get_success(sdk):
    return False
'''

SCENE_HANG = '''
SCENE_DESCRIPTION = "infinite loop at load time"
AVAILABLE_ACTIONS = ["noop"]

while True:
    pass

def build_scene(sdk):
    sdk.add_ground()

def get_success(sdk):
    return False
'''


# ====================================================================== #
# 1. AST scan
# ====================================================================== #
def test_scan_clean_source_passes():
    assert scan_source(SCENE_VALID) == []


def test_scan_rejects_import_os():
    v = scan_source("import os\n")
    assert any("forbidden import" in s and "os" in s for s in v)


def test_scan_rejects_open_and_eval_and_exec():
    assert any("open" in s for s in scan_source("open('x')\n"))
    assert any("eval" in s for s in scan_source("eval('1')\n"))
    assert any("exec" in s for s in scan_source("exec('x=1')\n"))


def test_scan_rejects_dunder_access():
    v = scan_source("x = ().__class__.__bases__\n")
    assert any("dunder" in s for s in v)


def test_scan_allows_math_import():
    assert scan_source("import math\ny = math.sqrt(2)\n") == []


def test_scan_reports_syntax_error():
    assert any("syntax" in s for s in scan_source("def f(:\n  pass\n"))


# ====================================================================== #
# 2. In-process pipeline (full funnel)
# ====================================================================== #
def test_valid_scene_passes(tmp_path):
    path = _write(tmp_path, "valid.py", SCENE_VALID)
    rep = verify_scene(path, sandboxed=False, sdk_factory=factory())
    assert rep["passed"] is True, rep
    assert rep["failure_class"] is None
    assert rep["layers"]["L0_static"]["passed"]
    assert rep["layers"]["L1_settling"]["passed"]
    assert rep["layers"]["L2_goal"]["passed"]


def test_missing_agent_fails_l0(tmp_path):
    path = _write(tmp_path, "noagent.py", SCENE_NO_AGENT)
    rep = verify_scene(path, sandboxed=False, sdk_factory=factory())
    assert rep["passed"] is False
    assert rep["failure_class"] == "ENV_ERROR"
    assert rep["layers"]["L0_static"]["checks"]["has_agent"]["pass"] is False
    assert "agent" in rep["hint"]


def test_initial_penetration_fails_l0(tmp_path):
    path = _write(tmp_path, "pen.py", SCENE_PENETRATION)
    rep = verify_scene(path, sandboxed=False, sdk_factory=factory())
    assert rep["failure_class"] == "ENV_ERROR"
    np = rep["layers"]["L0_static"]["checks"]["no_penetration"]
    assert np["pass"] is False and np["offenders"]


def test_bad_import_fails_sandbox_scan(tmp_path):
    path = _write(tmp_path, "bad.py", SCENE_BAD_IMPORT)
    rep = verify_scene(path, sandboxed=False, sdk_factory=factory())
    assert rep["failure_class"] == "ENV_ERROR"
    assert rep["layers"]["L0_static"]["checks"]["sandbox_scan"]["pass"] is False
    assert "sandbox" in rep["hint"]


def test_floating_object_fails_l1(tmp_path):
    path = _write(tmp_path, "float.py", SCENE_FLOATING)
    rep = verify_scene(path, sandboxed=False, sdk_factory=factory())
    assert rep["layers"]["L0_static"]["passed"] is True
    assert rep["layers"]["L1_settling"]["passed"] is False
    assert rep["failure_class"] == "ENV_ERROR"
    moved = rep["layers"]["L1_settling"]["checks"]["no_displacement"]
    assert moved["pass"] is False
    assert any(m[0] == "floater" for m in moved["moved"])
    assert "floater" in rep["hint"]


def test_agent_unsupported_fails_l1(tmp_path):
    # Floating agent + frozen gravity => stays in the air => agent_supported False.
    src = '''
SCENE_DESCRIPTION = "agent in the air"
AVAILABLE_ACTIONS = ["noop"]

def build_scene(sdk):
    sdk.add_ground()
    sdk.spawn_agent((400, 300))
    sdk.add_box("crate", (200, 20), size=(40, 40))

def get_success(sdk):
    return False
'''
    path = _write(tmp_path, "airagent.py", src)
    rep = verify_scene(path, sandboxed=False, sdk_factory=factory(gravity=False))
    assert rep["failure_class"] == "ENV_ERROR"
    assert rep["layers"]["L1_settling"]["checks"]["agent_supported"]["pass"] is False
    assert "support" in rep["hint"]


def test_nan_explosion_fails_l1(tmp_path):
    path = _write(tmp_path, "valid.py", SCENE_VALID)
    rep = verify_scene(path, sandboxed=False, sdk_factory=factory(nan_at=10))
    assert rep["failure_class"] == "ENV_ERROR"
    assert rep["layers"]["L1_settling"]["checks"]["no_nan"]["pass"] is False
    assert "NaN" in rep["hint"]


def test_trivial_goal_fails_l2(tmp_path):
    path = _write(tmp_path, "trivial.py", SCENE_TRIVIAL_GOAL)
    rep = verify_scene(path, sandboxed=False, sdk_factory=factory())
    assert rep["layers"]["L0_static"]["passed"] is True
    assert rep["layers"]["L1_settling"]["passed"] is True
    assert rep["layers"]["L2_goal"]["passed"] is False
    assert rep["failure_class"] == "GOAL_ERROR"
    assert rep["layers"]["L2_goal"]["checks"]["not_trivially_true"]["pass"] is False


def test_impure_goal_fails_l2(tmp_path):
    path = _write(tmp_path, "impure.py", SCENE_IMPURE_GOAL)
    rep = verify_scene(path, sandboxed=False, sdk_factory=factory())
    assert rep["failure_class"] == "GOAL_ERROR"
    assert rep["layers"]["L2_goal"]["checks"]["pure"]["pass"] is False


def test_missing_scene_file():
    rep = verify_scene("does_not_exist_xyz.py", sandboxed=False, sdk_factory=factory())
    assert rep["passed"] is False
    assert rep["failure_class"] == "ENV_ERROR"


def test_report_schema():
    rep = make_report()
    assert set(rep) == {"passed", "failure_class", "layers", "hint"}
    assert set(rep["layers"]) == {"L0_static", "L1_settling", "L2_goal"}


# ====================================================================== #
# 3. Sandbox / subprocess
# ====================================================================== #
def test_sandbox_timeout(tmp_path):
    path = _write(tmp_path, "hang.py", SCENE_HANG)
    rep = run_sandboxed(path, "verify", timeout_s=3)
    assert "error" in rep
    assert rep["error"]["type"] == "timeout"


def test_sandbox_navigate_missing_or_clean(tmp_path):
    # navigator may not exist / may depend on pymunk: we only require a clean
    # response (report or error dict), never a silent crash.
    path = _write(tmp_path, "valid.py", SCENE_VALID)
    rep = run_sandboxed(path, "navigate", timeout_s=15)
    assert isinstance(rep, dict)
    assert "error" in rep or "success" in rep


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
