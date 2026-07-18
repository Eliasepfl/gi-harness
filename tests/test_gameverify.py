"""Tests for module F (gameverify — universal oracles for v2 games, v2.1 checkpoints).

Module E (harness.world.World) is developed in parallel and may not exist yet, so
these tests run against a tiny deterministic `FakeWorld` implementing exactly the
CONTRACTS §1 surface the verifier layers touch, injected via `world_factory`.
Games are inline source strings; the tiny ones are solvable by random search.

A `test_smoke_real_world` end-to-end test runs against the real World when it is
importable, and is skipped otherwise.
"""

from __future__ import annotations

import os
import random
import sys

import pytest

# Make `harness` importable regardless of the pytest rootdir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.verify import gameverify as gv  # noqa: E402
from harness.verify.gameverify import (  # noqa: E402
    GUIDED_SEED_BASE, K_STEPS, load_game, run_episode, verify_game,
)

DT = 1.0 / 60.0


@pytest.fixture()
def legacy_thresholds(monkeypatch):
    """Pin the v2.2-scale G3 thresholds for fixtures calibrated on them.

    The v2.3 bar (TRIVIAL_TICKS=20, PROBE_HORIZON=300) targets real generated
    games; these unit fixtures are deliberately tiny fast-win worlds that test
    OTHER oracle behaviours (progress diagnosis, dead milestones, ordering,
    guided pass), so they run under the thresholds they were calibrated for."""
    monkeypatch.setattr(gv, "TRIVIAL_TICKS", 5)
    monkeypatch.setattr(gv, "PROBE_HORIZON", 120)


# ====================================================================== #
# FakeWorld — deterministic stand-in for harness.world.World (CONTRACTS §1)
# ====================================================================== #
class _Body:
    def __init__(self, name, pos, hw, hh, *, static, sensor):
        self.name = name
        self.pos = [float(pos[0]), float(pos[1])]
        self.vel = [0.0, 0.0]
        self.angle = 0.0
        self.angular_vel = 0.0
        self.hw = float(hw)
        self.hh = float(hh)
        self.mass = 1.0
        self.static = static
        self.sensor = sensor
        self.controlled = False

    def bbox(self):
        return [self.pos[0] - self.hw, self.pos[1] - self.hh,
                self.pos[0] + self.hw, self.pos[1] + self.hh]


class FakeWorld:
    """Minimal deterministic 2D world exposing the CONTRACTS §1 surface used by
    the verifier. Pure integrator (velocity persists, no damping) so a fresh
    world + a fixed action sequence is perfectly reproducible.

    nondeterministic=True injects unseeded global-random jitter per STEP to
    simulate a game/engine that leaks unseeded randomness (breaks determinism on
    BOTH the noop and the action path).

    act_nondeterministic=True injects unseeded global-random jitter only inside
    ``impulse`` (which the game calls from ``act``), so the NOOP path stays
    deterministic while the ACTION path drifts — the own-RNG-in-act residual that
    the host global-RNG pin does not cover and that the noop-only twin misses.
    """

    def __init__(self, seed=0, size=(800, 600), gravity=(0.0, 0.0), *,
                 nondeterministic=False, act_nondeterministic=False):
        self.size = size
        self.gravity = list(gravity)
        self.nondeterministic = nondeterministic
        self.act_nondeterministic = act_nondeterministic
        self._rng = random.Random(seed)
        self._bodies = {}
        self._flags = {}
        self._events = []
        self._steps = 0
        self._controlled = None

    # ---- construction ----
    def add(self, name, shape="box", *, pos, size=None, radius=None, a=None,
            b=None, vertices=None, mass=1.0, static=False, sensor=False,
            friction=0.7, elasticity=0.3, velocity=(0, 0), angle=0.0,
            locked_rotation=False):
        if shape == "circle":
            hw = hh = float(radius or 1.0)
        elif size is not None:
            hw, hh = float(size[0]) / 2, float(size[1]) / 2
        else:
            hw = hh = 10.0
        body = _Body(name, pos, hw, hh, static=static, sensor=sensor)
        body.vel = [float(velocity[0]), float(velocity[1])]
        body.angle = float(angle)
        body.mass = float(mass)
        self._bodies[name] = body
        return name

    def remove(self, name):
        self._bodies.pop(name, None)
        if self._controlled == name:
            self._controlled = None

    def pin(self, a, b, anchor_a=None, anchor_b=None):
        pass

    def pivot(self, a, b, point):
        pass

    def spring(self, a, b, rest_length, stiffness, damping,
               anchor_a=None, anchor_b=None):
        pass

    def set_gravity(self, gx, gy):
        self.gravity = [float(gx), float(gy)]

    def control(self, name):
        for bdy in self._bodies.values():
            bdy.controlled = False
        self._bodies[name].controlled = True
        self._controlled = name

    # ---- dynamics ----
    def impulse(self, name, vec):
        b = self._bodies[name]
        jx = jy = 0.0
        if self.act_nondeterministic:
            # UNSEEDED global RNG on the ACT path only (impulse is called from act,
            # never from a noop tick) -> two identical seeded action rollouts diverge.
            jx = random.uniform(-0.5, 0.5)
            jy = random.uniform(-0.5, 0.5)
        b.vel[0] += (float(vec[0]) + jx) / b.mass
        b.vel[1] += (float(vec[1]) + jy) / b.mass

    def force(self, name, vec):
        b = self._bodies[name]
        b.vel[0] += float(vec[0]) * DT / b.mass
        b.vel[1] += float(vec[1]) * DT / b.mass

    def set_velocity(self, name, vec):
        self._bodies[name].vel = [float(vec[0]), float(vec[1])]

    def set_flag(self, key, value):
        self._flags[key] = value
        self._events.append({"type": "flag_set", "key": key, "step": self._steps})

    def flag(self, key, default=None):
        return self._flags.get(key, default)

    def on_contact(self, a, b, flag, once=True):
        pass

    @property
    def rng(self):
        return self._rng

    @property
    def steps(self):
        return self._steps

    # ---- queries ----
    def entities(self):
        return list(self._bodies)

    def query(self, name):
        b = self._bodies[name]
        return {
            "pos": list(b.pos), "vel": list(b.vel), "angle": b.angle,
            "angular_vel": b.angular_vel, "bbox": b.bbox(), "shape": "box",
            "static": b.static, "sensor": b.sensor, "controlled": b.controlled,
        }

    def _overlap(self, a, b):
        al, ab, ar, at = self._bodies[a].bbox()
        bl, bb, br, bt = self._bodies[b].bbox()
        return (min(ar, br) - max(al, bl), min(at, bt) - max(ab, bb))

    def contacts(self, a, b):
        ox, oy = self._overlap(a, b)
        return ox > -1.0 and oy > -1.0

    def touching(self, name):
        out = []
        for other in self._bodies:
            if other == name or self._bodies[other].sensor:
                continue
            if self.contacts(name, other):
                out.append(other)
        return out

    def grounded(self, name):
        b = self._bodies[name]
        for other in self._bodies.values():
            if other is b:
                continue
            ox = min(b.pos[0] + b.hw, other.pos[0] + other.hw) - \
                max(b.pos[0] - b.hw, other.pos[0] - other.hw)
            if ox > 0 and other.pos[1] < b.pos[1]:
                return True
        return False

    def in_bounds(self, name, margin=0.0):
        l, bo, r, t = self._bodies[name].bbox()
        w, h = self.size
        return (l >= -margin and bo >= -margin
                and r <= w + margin and t <= h + margin)

    def penetration_depth(self, a, b):
        if self._bodies[a].sensor or self._bodies[b].sensor:
            return 0.0
        ox, oy = self._overlap(a, b)
        return min(ox, oy) if (ox > 0 and oy > 0) else 0.0

    # ---- harness side ----
    def step(self, n=1):
        for _ in range(n):
            self._steps += 1
            for b in self._bodies.values():
                if b.static:
                    continue
                b.vel[0] += self.gravity[0] * DT
                b.vel[1] += self.gravity[1] * DT
                b.pos[0] += b.vel[0] * DT
                b.pos[1] += b.vel[1] * DT
                if self.nondeterministic:
                    # Unseeded global RNG: two fresh worlds diverge.
                    b.pos[0] += random.uniform(-0.01, 0.01)
                    b.pos[1] += random.uniform(-0.01, 0.01)

    def snapshot(self):
        return {n: {"pos": list(b.pos), "vel": list(b.vel), "angle": b.angle}
                for n, b in self._bodies.items()}

    def events(self):
        return list(self._events)

    def teleport(self, name, pos):
        self._bodies[name].pos = [float(pos[0]), float(pos[1])]

    def kinetic_energy(self, names=None):
        names = names or list(self._bodies)
        return sum(0.5 * self._bodies[n].mass
                   * (self._bodies[n].vel[0] ** 2 + self._bodies[n].vel[1] ** 2)
                   for n in names if not self._bodies[n].static)

    def controlled(self):
        return self._controlled


def factory(**kw):
    """Build a world_factory(seed=0)->FakeWorld, forwarding kwargs."""
    return lambda seed=0: FakeWorld(seed=seed, **kw)


def _write(tmp_path, name, source):
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return str(p)


# ====================================================================== #
# Inline games (tiny; solvable by RANDOM search)
# ====================================================================== #
# Shared skeleton: "right" pushes the controlled block right (velocity
# accumulates), "left" pushes it left; goals are x-thresholds.
_BODY = '''
def build(world):
    world.add("ground", shape="box", pos=(400, 10), size=(800, 20), static=True)
    world.add("player", shape="box", pos=(100, 60), size=(20, 20))
    world.control("player")

def act(world, action):
    if action == "right":
        world.impulse("player", (60, 0))
    elif action == "left":
        world.impulse("player", (-60, 0))
'''

GAME_VALID = '''
TITLE = "Push Right"
PROMPT = "drive the block past the marker on the right"
ACTIONS = ["right", "left"]
''' + _BODY + '''
def success(world):
    return world.query("player")["pos"][0] > 300

def checkpoints(world):
    x = world.query("player")["pos"][0]
    return {"halfway": x > 200, "almost": x > 260}
'''

# Missing the required `success` symbol -> G0.
GAME_MISSING_SYMBOL = '''
TITLE = "Broken"
PROMPT = "no success predicate"
ACTIONS = ["right", "left"]
''' + _BODY + '''
def checkpoints(world):
    return {"halfway": world.query("player")["pos"][0] > 200}
'''

# Missing the required v2.1 `checkpoints` symbol -> G0.
GAME_NO_CHECKPOINTS = '''
TITLE = "No Milestones"
PROMPT = "forgot to declare checkpoints"
ACTIONS = ["right", "left"]
''' + _BODY + '''
def success(world):
    return world.query("player")["pos"][0] > 300
'''

# "idle" does nothing -> G1 efficacy (dead action).
GAME_DEAD_ACTION = '''
TITLE = "Idle Trap"
PROMPT = "one action does nothing"
ACTIONS = ["right", "idle"]

def build(world):
    world.add("ground", shape="box", pos=(400, 10), size=(800, 20), static=True)
    world.add("player", shape="box", pos=(100, 60), size=(20, 20))
    world.control("player")

def act(world, action):
    if action == "right":
        world.impulse("player", (60, 0))

def success(world):
    return world.query("player")["pos"][0] > 300

def checkpoints(world):
    return {"halfway": world.query("player")["pos"][0] > 200}
'''

# "brake" zeroes the car's velocity: INERT at t=0 (the car is parked, vel 0) but
# LIVE the moment the car is moving. The pre-fix efficacy check probed only t=0 and
# wrongly flagged it dead ("dead action ... brake") — the real parking-game false
# positive from 2026-07-15. It must PASS G1 now (certified live from a dynamic
# context reached by a short burst of another action).
GAME_BRAKE = '''
TITLE = "Parking"
PROMPT = "drive the car right past the marker"
ACTIONS = ["right", "left", "brake"]

def build(world):
    world.add("ground", shape="box", pos=(400, 10), size=(2000, 20), static=True)
    world.add("car", shape="box", pos=(100, 60), size=(20, 20))
    world.control("car")

def act(world, action):
    if action == "right":
        world.impulse("car", (60, 0))
    elif action == "left":
        world.impulse("car", (-60, 0))
    elif action == "brake":
        world.set_velocity("car", (0, 0))

def success(world):
    return world.query("car")["pos"][0] > 300

def checkpoints(world):
    return {"halfway": world.query("car")["pos"][0] > 200}
'''

# success is true at t=0 (steps == 0) but false after any step -> passes G1
# agency, fails G2 (success_false_at_t0).
GAME_SUCCESS_AT_T0 = '''
TITLE = "Instant"
PROMPT = "already won at t=0"
ACTIONS = ["right", "left"]
''' + _BODY + '''
def success(world):
    return world.steps == 0

def checkpoints(world):
    return {"halfway": world.query("player")["pos"][0] > 200}
'''

# A milestone already true at t=0 -> G2 (checkpoints_false_at_t0).
GAME_CP_AT_T0 = '''
TITLE = "Pre-latched"
PROMPT = "a milestone starts already reached"
ACTIONS = ["right", "left"]
''' + _BODY + '''
def success(world):
    return world.query("player")["pos"][0] > 300

def checkpoints(world):
    x = world.query("player")["pos"][0]
    return {"already_here": x > 50, "halfway": x > 200}
'''

# Unreachable goal; the first milestone IS reachable -> UNSOLVED + progress
# diagnosis ("stuck between 'started' and 'deep'").
GAME_IMPOSSIBLE = '''
TITLE = "Impossible"
PROMPT = "goal out of reach"
ACTIONS = ["right", "left"]
''' + _BODY + '''
def success(world):
    return world.query("player")["pos"][0] > 100000

def checkpoints(world):
    x = world.query("player")["pos"][0]
    return {"started": x > 200, "deep": x > 50000}
'''

# Both actions push right; success within one tick -> G3 "trivial".
GAME_INSTANT_WIN = '''
TITLE = "Trivial"
PROMPT = "won in one move whatever you press"
ACTIONS = ["push_a", "push_b"]

def build(world):
    world.add("ground", shape="box", pos=(400, 10), size=(800, 20), static=True)
    world.add("player", shape="box", pos=(100, 60), size=(20, 20))
    world.control("player")

def act(world, action):
    world.impulse("player", (60, 0))

def success(world):
    return world.query("player")["pos"][0] > 104

def checkpoints(world):
    return {"nudged": world.query("player")["pos"][0] > 101}
'''

# Winnable, but "reached_sky" can never latch (no vertical dynamics)
# -> dead milestone -> GOAL_ERROR naming it.
GAME_DEAD_MILESTONE = '''
TITLE = "Dead Milestone"
PROMPT = "declares a milestone that can never fire"
ACTIONS = ["right", "left"]
''' + _BODY + '''
def success(world):
    return world.query("player")["pos"][0] > 300

def checkpoints(world):
    q = world.query("player")
    return {"halfway": q["pos"][0] > 200, "reached_sky": q["pos"][1] > 500}
'''

# Milestones declared in the WRONG order (empirically "halfway" latches first)
# -> non-fatal warning, game still passes.
GAME_MISORDERED = '''
TITLE = "Misordered"
PROMPT = "milestones declared out of order"
ACTIONS = ["right", "left"]
''' + _BODY + '''
def success(world):
    return world.query("player")["pos"][0] > 300

def checkpoints(world):
    x = world.query("player")["pos"][0]
    return {"almost": x > 260, "halfway": x > 150}
'''

# Import rejected by the sandbox scan -> G0.
GAME_BAD_IMPORT = '''
import os
TITLE = "Malicious"
PROMPT = "tries to import os"
ACTIONS = ["right", "left"]

def build(world):
    os.system("echo pwned")
    world.add("ground", shape="box", pos=(400, 10), size=(800, 20), static=True)
    world.add("player", shape="box", pos=(100, 60), size=(20, 20))
    world.control("player")

def act(world, action):
    world.impulse("player", (60, 0))

def success(world):
    return world.query("player")["pos"][0] > 300

def checkpoints(world):
    return {"halfway": world.query("player")["pos"][0] > 200}
'''

# Two-stage combo lock, tuned so pure random search (seeds 0..39) never wins
# but the checkpoint-guided second pass does: arming needs 22 CONSECUTIVE
# "tap" ticks ("hold" while unarmed resets the combo), then armed "hold"
# blasts the player toward a far goal. First pass empirically: 4/40 episodes
# arm, max x ~7469 < GOAL_X; guided pass (prefix of the earliest-arming
# episode + random continuations) reaches ~10538 > GOAL_X.
GAME_TWO_STAGE = '''
TITLE = "Combo Lock"
PROMPT = "arm the lock with a long tap combo, then hold to blast right"
ACTIONS = ["tap", "hold"]
COMBO_NEEDED = 22
GOAL_X = 9000

def build(world):
    world.add("ground", shape="box", pos=(400, 10), size=(800, 20), static=True)
    world.add("player", shape="box", pos=(100, 60), size=(20, 20))
    world.control("player")

def act(world, action):
    if action == "tap":
        combo = world.flag("combo", 0) + 1
        world.set_flag("combo", combo)
        if combo >= COMBO_NEEDED:
            world.set_flag("armed", 1)
        world.impulse("player", (0.5, 0))
    elif action == "hold":
        if world.flag("armed", 0):
            world.impulse("player", (80, 0))
        else:
            world.set_flag("combo", 0)
            world.impulse("player", (-0.5, 0))

def success(world):
    return world.flag("armed", 0) == 1 and world.query("player")["pos"][0] > GOAL_X

def checkpoints(world):
    return {"combo_10": world.flag("combo", 0) >= 10,
            "armed": world.flag("armed", 0) == 1}
'''


# ====================================================================== #
# Full-funnel tests
# ====================================================================== #
# ====================================================================== #
# Runner contract
# ====================================================================== #
def test_run_episode_noop_contract():
    # None actions => noop ticks: act is never called, world just steps.
    game = load_game(GAME_VALID)
    world = FakeWorld(seed=0)
    game.build(world)
    ep = run_episode(game, world, iter([None, None, None]), 3)
    assert ep["result"] == "budget"
    assert ep["ticks"] == 3
    assert world.steps == 3 * K_STEPS
    # Player did not move (no action applied); no milestone latched.
    assert abs(world.query("player")["pos"][0] - 100.0) < 1e-9
    assert ep["checkpoints"] == {"halfway": None, "almost": None}


def test_run_episode_reports_game_errors():
    src = '''
TITLE = "Boom"
PROMPT = "act raises"
ACTIONS = ["a", "b"]

def build(world):
    world.add("ground", shape="box", pos=(400, 10), size=(800, 20), static=True)
    world.add("player", shape="box", pos=(100, 60), size=(20, 20))
    world.control("player")

def act(world, action):
    boom = 1 / 0

def success(world):
    return False

def checkpoints(world):
    return {"moved": world.query("player")["pos"][0] > 200}
'''
    game = load_game(src)
    world = FakeWorld(seed=0)
    game.build(world)
    ep = run_episode(game, world, iter(["a"]), 1)
    assert ep["result"] == "error"
    assert "ZeroDivisionError" in ep["error"]


def test_run_episode_latches_survive_regression():
    # Latching is runner-side: a milestone stays passed after its underlying
    # state regresses (dash past the threshold, then dash back below it).
    src = '''
TITLE = "There And Back"
PROMPT = "cross the line, then retreat"
ACTIONS = ["dash", "back"]

def build(world):
    world.add("ground", shape="box", pos=(400, 10), size=(800, 20), static=True)
    world.add("player", shape="box", pos=(100, 60), size=(20, 20))
    world.control("player")

def act(world, action):
    if action == "dash":
        world.set_velocity("player", (400, 0))
    elif action == "back":
        world.set_velocity("player", (-400, 0))

def success(world):
    return world.query("player")["pos"][0] > 1000

def checkpoints(world):
    return {"halfway": world.query("player")["pos"][0] > 200}
'''
    game = load_game(src)
    world = FakeWorld(seed=0)
    game.build(world)
    plan = ["dash"] * 4 + ["back"] * 4  # crosses 200, then falls back below
    ep = run_episode(game, world, iter(plan), len(plan))
    assert ep["result"] == "budget"
    assert world.query("player")["pos"][0] < 200
    assert ep["checkpoints"]["halfway"] is not None


# ====================================================================== #
# Sandbox / real-World integration
# ====================================================================== #
@pytest.mark.parametrize("job", ["gameverify"])
def test_sandboxed_smoke(tmp_path, job):
    # Sandboxed path routes through the subprocess with the real World factory;
    # World may be absent, so we only require a well-formed report/error dict.
    path = _write(tmp_path, "valid.py", GAME_VALID)
    from harness.core.sandbox import run_sandboxed
    rep = run_sandboxed(path, job, timeout_s=30)
    assert isinstance(rep, dict)
    assert "error" in rep or "passed" in rep


def test_smoke_real_world(tmp_path):
    # End-to-end against the real World once module E exists; skip otherwise.
    try:
        from harness.core.world import World  # noqa: F401
    except ImportError:
        pytest.skip("harness.core.world not available yet")
    path = _write(tmp_path, "valid.py", GAME_VALID)
    rep = verify_game(path, sandboxed=False,
                      world_factory=lambda seed=0: World(seed=seed))
    assert isinstance(rep, dict)
    assert set(rep) == {"passed", "failure_class", "layers", "hint",
                        "warnings", "progress", "witness"}


# ====================================================================== #
# Failure-witness / PRESSURE gate (WAVE 1) — advisory, engine-agnostic
# ====================================================================== #
class _GateExecutor:
    """A tiny executor for the pressure gate's failure sweep: any plan CONTAINING
    ``fail_on`` ends in a ``failure`` terminal, everything else runs to budget. Enough
    to drive the failure_reachable sweep (coverage / random / inverted tree) without a
    physics engine — the gate's constant-false path reads only the source string."""

    def __init__(self, fail_on=None, batched=False):
        self.batched = batched
        self.fail_on = fail_on

    def run_batch(self, src, episodes, max_ticks, frames_every=0, escape_margin=None):
        out = []
        for ep in episodes:
            acts = list(ep.get("actions", []))
            if self.fail_on is not None and self.fail_on in acts:
                res, applied = "failure", acts.index(self.fail_on) + 1
            else:
                res = "budget"
                applied = len(acts) if max_ticks is None else min(len(acts), max_ticks)
            out.append({"result": res, "ticks": applied, "checkpoints": {},
                        "final_snapshot": {}, "actions": acts[:applied]})
        return out


_CONST_FALSE_SRC = "extends Node2D\nfunc is_failure() -> bool:\n\treturn false\n"
_HAS_LOGIC_SRC = "extends Node2D\nfunc is_failure() -> bool:\n\treturn _dead\n"


@pytest.fixture()
def small_pressure(monkeypatch):
    monkeypatch.setattr(gv, "PROBE_HORIZON", 30)
    monkeypatch.setattr(gv, "MACRO_MAX", 3)
    import harness.verify.treesolve as _ts
    monkeypatch.setattr(_ts, "TICK_BUDGET", 400)


def _certified_report():
    rep = gv.make_report()
    rep["passed"] = True
    rep["failure_class"] = None
    rep["layers"]["G3_solve"] = {"passed": True, "checks": {}}
    return rep


def test_failure_witness_gate_flags_constant_false(small_pressure):
    """is_failure() hardcoded false -> advisory `no_pressure`: a warning + a machine-
    readable finding, but certification is NOT blocked (passed stays True)."""
    rep = gv._failure_witness_gate(_GateExecutor(), _CONST_FALSE_SRC, ["go", "stay"],
                                   _certified_report())
    fw = rep["layers"]["G3_solve"]["checks"]["failure_witness"]
    assert fw["has_failure_witness"] is False
    assert fw["outcome"] == "no_pressure" and fw["constant_false"] is True
    assert fw["finding"]["outcome"] == "no_pressure"
    assert any("PRESSURE" in w for w in rep["warnings"])
    # ADVISORY: never blocks certification.
    assert rep["passed"] is True and rep["failure_class"] is None
    assert fw["pass"] is True                         # the sub-check is non-gating


def test_failure_witness_gate_passes_with_reachable_failure(small_pressure):
    """is_failure() has real logic AND a reachable loss -> `has_pressure`: the gate
    records the failure witness, no warning, certification intact."""
    ex = _GateExecutor(fail_on="sink")
    rep = gv._failure_witness_gate(ex, _HAS_LOGIC_SRC, ["go", "sink"],
                                   _certified_report())
    fw = rep["layers"]["G3_solve"]["checks"]["failure_witness"]
    assert fw["has_failure_witness"] is True and fw["outcome"] == "has_pressure"
    assert fw["witness"] is not None
    assert not any("PRESSURE" in w for w in rep["warnings"])
    assert rep["passed"] is True and rep["failure_class"] is None


def test_failure_witness_gate_flags_unreachable_failure(small_pressure):
    """is_failure() has logic but NO adversarial rollout ever loses (the race case) ->
    advisory `failure_unreachable`, distinct from the constant-false `no_pressure`."""
    ex = _GateExecutor(fail_on=None)                  # nothing ever fails
    rep = gv._failure_witness_gate(ex, _HAS_LOGIC_SRC, ["go", "stay"],
                                   _certified_report())
    fw = rep["layers"]["G3_solve"]["checks"]["failure_witness"]
    assert fw["has_failure_witness"] is False
    assert fw["outcome"] == "failure_unreachable" and fw["constant_false"] is False
    assert any("PRESSURE" in w for w in rep["warnings"])
    assert rep["passed"] is True and rep["failure_class"] is None


def test_failure_witness_gate_finding_compiles_to_directive(small_pressure):
    """The gate's finding is proof-carrying: it feeds the feedback compiler's pressure
    row (end-to-end, the revise-loop path)."""
    from harness.gen import feedback as F
    rep = gv._failure_witness_gate(_GateExecutor(), _CONST_FALSE_SRC, ["go", "stay"],
                                   _certified_report())
    ds = F.compile_directives({"pressure": F.pressure_finding(rep)})
    assert [d.source for d in ds] == ["no_pressure"]


# ====================================================================== #
# Dead-space / PROPORTION gate (WAVE 2) — advisory, geometry-only
# ====================================================================== #
def _geom_facts(world_size, bodies):
    return {"world_size": {"declared": list(world_size)}, "geometry": list(bodies)}


def _gb(name, pos, *, controlled=False, static=False):
    return {"name": name, "pos": pos, "static": static, "controlled": controlled}


def test_dead_space_gate_flags_over_empty_world():
    """A tiny scene in a huge world -> advisory `dead_space`: a warning + the top-level
    finding, but certification is NOT blocked (passed stays True)."""
    facts = _geom_facts([2000, 1400], [
        _gb("mote", [200, 200], controlled=True),
        _gb("gem_down", [200, 380], static=True),
        _gb("gem_right", [420, 200], static=True)])
    rep = gv._dead_space_gate(facts, _certified_report())
    dsc = rep["layers"]["G3_solve"]["checks"]["dead_space"]
    assert dsc["pass"] is True and dsc["advisory"] is True   # NON-gating sub-check
    assert dsc["dead_space"] is True and dsc["linear_ratio"] > 5.0
    assert rep["dead_space"]["outcome"] == "dead_space"       # top-level finding when flagged
    assert any("PROPORTION" in w for w in rep["warnings"])
    # ADVISORY: never blocks certification.
    assert rep["passed"] is True and rep["failure_class"] is None


def test_dead_space_gate_passes_proportioned_world():
    """mini_collect-shaped geometry -> no flag: sub-check records the measurement, but no
    warning, no top-level `dead_space` key (so the report schema is unchanged)."""
    facts = _geom_facts([800, 600], [
        _gb("player", [300, 300], controlled=True),
        _gb("gem_a", [300, 165], static=True),
        _gb("gem_b", [560, 340], static=True)])
    rep = gv._dead_space_gate(facts, _certified_report())
    dsc = rep["layers"]["G3_solve"]["checks"]["dead_space"]
    assert dsc["dead_space"] is False
    assert "dead_space" not in rep                            # only set when flagged
    assert not any("PROPORTION" in w for w in rep["warnings"])
    assert rep["passed"] is True


def test_dead_space_gate_no_geometry_is_a_noop():
    """No controlled spawn -> nothing to measure -> the verdict is untouched (no key added)."""
    facts = _geom_facts([800, 600], [_gb("gem", [300, 300], static=True)])
    rep = gv._dead_space_gate(facts, _certified_report())
    assert "dead_space" not in rep
    assert "dead_space" not in rep["layers"]["G3_solve"]["checks"]


def test_dead_space_gate_finding_compiles_to_directive():
    """The gate's finding is proof-carrying: it feeds the feedback compiler's dead_space
    row (the revise-loop path), at DIFFICULTY severity."""
    from harness.gen import feedback as F
    facts = _geom_facts([2000, 1400], [
        _gb("mote", [200, 200], controlled=True),
        _gb("gem_down", [200, 380], static=True),
        _gb("gem_right", [420, 200], static=True)])
    rep = gv._dead_space_gate(facts, _certified_report())
    ds = F.compile_directives({"dead_space": F.dead_space_finding(rep)})
    assert [d.source for d in ds] == ["dead_space"]
    assert ds[0].severity == F.DIFFICULTY


# ====================================================================== #
# G0.5 geometric reachability pre-filter wiring (Elias directive 1)
# ====================================================================== #
def _walled_geometry_facts():
    """Serve-host-shaped t=0 geometry facts: a gem sealed in a box of four wall
    footprints, player spawned outside."""
    return {"world_size": {"declared": [800, 600]}, "geometry": [
        {"name": "player", "pos": [100, 300], "controlled": True, "static": False},
        {"name": "gem", "pos": [400, 300], "static": True},
        {"name": "wall_top", "pos": [400, 240], "static": True, "half_extents": [68, 8]},
        {"name": "wall_bottom", "pos": [400, 360], "static": True, "half_extents": [68, 8]},
        {"name": "wall_left", "pos": [340, 300], "static": True, "half_extents": [8, 68]},
        {"name": "wall_right", "pos": [460, 300], "static": True, "half_extents": [8, 68]},
    ]}


def test_run_reachability_rejects_walled_goal():
    from harness.verify.gameverify import _run_reachability
    layer = _run_reachability(_walled_geometry_facts())
    assert layer["passed"] is False
    assert layer["checks"]["reachable"]["pass"] is False
    assert "gem" in layer["checks"]["reachable"]["unreachable"]
    assert "walled off" in layer["hint"]


def test_run_reachability_passes_open_and_footprint_free_scenes():
    from harness.verify.gameverify import _run_reachability
    # No walls at all (mini_collect-shaped: two bare markers) -> passes, defers to G3.
    open_facts = {"world_size": {"declared": [800, 600]}, "geometry": [
        {"name": "player", "pos": [300, 300], "controlled": True, "static": False},
        {"name": "gem_a", "pos": [300, 165], "static": True},
        {"name": "gem_b", "pos": [560, 340], "static": True}]}
    assert _run_reachability(open_facts)["passed"] is True
    # A box with an OPENING (drop the right wall) -> reachable, passes.
    open_box = _walled_geometry_facts()
    open_box["geometry"] = [b for b in open_box["geometry"] if b["name"] != "wall_right"]
    assert _run_reachability(open_box)["passed"] is True


# ====================================================================== #
# One-action games (MIN_ACTIONS=1) x the single-action anti-triviality gate
# ====================================================================== #
# MIN_ACTIONS was lowered 2 -> 1 so a legit one-button game (flappy-style timed
# taps) is well-formed. The anti-degeneracy job is done ORTHOGONALLY by the POLICY
# gates, which are independent of the action COUNT:
#   * G1 "agency": the NOOP rollout must not win -> a win-by-IDLE game hard-fails.
#   * single_action gate: holding any one action must not win -> a win-by-HOLD game
#     hard-fails to GOAL_ERROR.
# These tests show a 1-action game passes G0, and that the two policy gates still
# bite for a 1-action game (trivial) while leaving a game where holding the one
# action does NOT win (skill/timing required) certified.

# Holding the single action "right" drives the block to the goal -> a TRIVIAL
# 1-action game (win by holding one input); the single-action gate must reject it.
GAME_ONE_ACTION_TRIVIAL = '''
TITLE = "Slide Right"
PROMPT = "hold right to reach the marker"
ACTIONS = ["right"]

def build(world):
    world.add("ground", shape="box", pos=(1000, 10), size=(4000, 20), static=True)
    world.add("player", shape="box", pos=(100, 60), size=(20, 20))
    world.control("player")

def act(world, action):
    if action == "right":
        world.impulse("player", (60, 0))

def success(world):
    return world.query("player")["pos"][0] > 300

def checkpoints(world):
    return {"halfway": world.query("player")["pos"][0] > 200}
'''

# "dash" advances the runner but banks HEAT; heat bleeds off only between dashes.
# Dashing every tick (the constant-hold policy the single-action probe runs)
# OVERHEATS and LOSES (is_failure) before reaching the goal, so holding the one
# action does NOT win -> the single-action gate must leave a cert intact. (The
# winning policy needs rests, which the G3 solver's noop-free alphabet cannot
# emit, so this fixture is used ONLY for the gate/probe unit checks, not end-to-end.)
GAME_ONE_ACTION_SKILL = '''
TITLE = "Sprint"
PROMPT = "advance in bursts; sprinting non-stop overheats"
ACTIONS = ["dash"]

def build(world):
    world.add("ground", shape="box", pos=(1000, 10), size=(4000, 20), static=True)
    world.add("runner", shape="box", pos=(100, 60), size=(20, 20))
    world.control("runner")

def act(world, action):
    if action == "dash":
        world.impulse("runner", (40, 0))
        world.set_flag("heat", world.flag("heat", 0.0) + 2.0)

def on_step(world):
    h = world.flag("heat", 0.0)
    if h > 0.0:
        world.set_flag("heat", max(0.0, h - 0.05))

def failure(world):
    return world.flag("heat", 0.0) >= 5.0

def success(world):
    return world.query("runner")["pos"][0] > 300

def checkpoints(world):
    return {"midway": world.query("runner")["pos"][0] > 200}
'''

# The player starts with a rightward velocity and coasts to the goal on its own:
# the NOOP rollout wins, so this 1-action game must fail G1 "agency".
GAME_ONE_ACTION_IDLE_WIN = '''
TITLE = "Drift"
PROMPT = "it wins itself"
ACTIONS = ["nudge"]

def build(world):
    world.add("ground", shape="box", pos=(1000, 10), size=(4000, 20), static=True)
    world.add("player", shape="box", pos=(100, 60), size=(20, 20), velocity=(80, 0))
    world.control("player")

def act(world, action):
    if action == "nudge":
        world.impulse("player", (5, 0))

def success(world):
    return world.query("player")["pos"][0] > 300

def checkpoints(world):
    return {"halfway": world.query("player")["pos"][0] > 200}
'''


# ====================================================================== #
# Checkpoint count bounds (CP_MIN=0, CP_MAX=32)
# ====================================================================== #
# CP_MIN dropped 1 -> 0: a game with only is_success and NO intermediate milestones
# (checkpoints() -> {}) is valid. CP_MAX raised 6 -> 32: the old hard cap was
# artificial. The empty/large cases are exercised end-to-end and at the G2 gate.

# 0 milestones: checkpoints() returns {} -> must still certify (terminal-only game).
GAME_NO_MILESTONES = '''
TITLE = "Just Win"
PROMPT = "reach the marker; no intermediate milestones"
ACTIONS = ["right", "left"]
''' + _BODY + '''
def success(world):
    return world.query("player")["pos"][0] > 300

def checkpoints(world):
    return {}
'''

# 8 milestones (was rejected at the old CP_MAX=6): monotone x-thresholds all below
# the goal, so the winning trajectory latches all eight in declared order.
GAME_MANY_CHECKPOINTS = '''
TITLE = "Eight Marks"
PROMPT = "pass eight marks then the goal"
ACTIONS = ["right", "left"]
''' + _BODY + '''
def success(world):
    return world.query("player")["pos"][0] > 300

def checkpoints(world):
    x = world.query("player")["pos"][0]
    return {"m%d" % i: x > (110 + 20 * i) for i in range(8)}
'''

# 33 milestones: over the new CP_MAX=32 -> the cap must still bite.
GAME_OVER_CAP_CHECKPOINTS = '''
TITLE = "Too Many"
PROMPT = "declares 33 milestones"
ACTIONS = ["right", "left"]
''' + _BODY + '''
def success(world):
    return world.query("player")["pos"][0] > 300

def checkpoints(world):
    x = world.query("player")["pos"][0]
    return {"m%d" % i: x > (105 + i) for i in range(33)}
'''


def test_g2_accepts_empty_checkpoints():
    layer = gv.run_g2(factory(), load_game(GAME_NO_MILESTONES))
    assert layer["passed"] is True, layer
    assert layer["checks"]["checkpoints_wellformed"]["n"] == 0


def test_g2_accepts_eight_checkpoints():
    # Previously (CP_MAX=6) an 8-milestone game failed wellformed; now it passes.
    layer = gv.run_g2(factory(), load_game(GAME_MANY_CHECKPOINTS))
    assert layer["passed"] is True, layer
    cw = layer["checks"]["checkpoints_wellformed"]
    assert cw["pass"] is True and cw["n"] == 8


def test_g2_still_rejects_over_cap_checkpoints():
    # The relaxed cap is 32; 33 milestones must still be rejected as malformed.
    layer = gv.run_g2(factory(), load_game(GAME_OVER_CAP_CHECKPOINTS))
    assert layer["passed"] is False
    cw = layer["checks"]["checkpoints_wellformed"]
    assert cw["pass"] is False and cw["n"] == 33


def test_g2js_checkpoints_bounds_gd_lane():
    # The Godot/JS lane shares run_g2_js/_g2js_checkpoints: same 0..32 bounds from
    # the serve host's check facts (the production path for the gdscript engine).
    from harness.verify.gameverify import run_g2_js

    def g2(n):
        return {
            "success": {"is_bool": True, "value": False, "deterministic": True,
                        "state_unchanged": True},
            "failure": None,
            "checkpoints": {"is_dict": True, "keys": ["m%d" % i for i in range(n)],
                            "n": n, "non_bool_keys": [], "true_keys": [],
                            "deterministic": True, "state_unchanged": True},
        }

    assert run_g2_js(g2(0))["checks"]["checkpoints_wellformed"]["pass"] is True
    assert run_g2_js(g2(8))["checks"]["checkpoints_wellformed"]["pass"] is True
    assert run_g2_js(g2(33))["checks"]["checkpoints_wellformed"]["pass"] is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
