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

    nondeterministic=True injects unseeded global-random jitter per step to
    simulate a game/engine that leaks unseeded randomness (breaks determinism).
    """

    def __init__(self, seed=0, size=(800, 600), gravity=(0.0, 0.0), *,
                 nondeterministic=False):
        self.size = size
        self.gravity = list(gravity)
        self.nondeterministic = nondeterministic
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
        b.vel[0] += float(vec[0]) / b.mass
        b.vel[1] += float(vec[1]) / b.mass

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
def test_valid_game_passes_and_yields_replayable_witness(tmp_path, legacy_thresholds):
    path = _write(tmp_path, "valid.py", GAME_VALID)
    rep = verify_game(path, sandboxed=False, world_factory=factory())
    assert rep["passed"] is True, rep
    assert rep["failure_class"] is None
    for layer in ("G0_static", "G1_rollout", "G2_goal", "G3_solve"):
        assert rep["layers"][layer]["passed"], (layer, rep["layers"][layer])

    w = rep["witness"]
    assert w is not None and w["ticks"] >= gv.TRIVIAL_TICKS
    assert isinstance(w["actions"], list) and len(w["actions"]) == w["ticks"]

    # v2.1: the witness carries the milestone latch map, in declared order,
    # every milestone latched at/before the success tick, in order.
    cps = w["checkpoints"]
    assert list(cps) == ["halfway", "almost"]
    assert all(t is not None for t in cps.values())
    assert cps["halfway"] <= cps["almost"] <= w["ticks"]
    assert rep["warnings"] == []
    assert rep["progress"] is None

    # Independently replay the witness on a fresh seeded world -> success again.
    game = load_game(GAME_VALID)
    world = FakeWorld(seed=0)
    game.build(world)
    replay = run_episode(game, world, iter(w["actions"]), len(w["actions"]))
    assert replay["result"] == "success"
    assert replay["checkpoints"] == cps


def test_missing_symbol_fails_g0(tmp_path):
    path = _write(tmp_path, "missing.py", GAME_MISSING_SYMBOL)
    rep = verify_game(path, sandboxed=False, world_factory=factory())
    assert rep["failure_class"] == "ENV_ERROR"
    assert rep["layers"]["G0_static"]["checks"]["symbols"]["pass"] is False
    assert "success" in rep["layers"]["G0_static"]["checks"]["symbols"]["missing"]


def test_missing_checkpoints_fails_g0(tmp_path):
    # v2.1: checkpoints is REQUIRED; its absence is a clean G0 report.
    path = _write(tmp_path, "nocp.py", GAME_NO_CHECKPOINTS)
    rep = verify_game(path, sandboxed=False, world_factory=factory())
    assert rep["failure_class"] == "ENV_ERROR"
    symbols = rep["layers"]["G0_static"]["checks"]["symbols"]
    assert symbols["pass"] is False
    assert symbols["missing"] == ["checkpoints"]
    assert "checkpoints" in rep["hint"]


def test_dead_action_fails_g1_efficacy(tmp_path):
    path = _write(tmp_path, "dead.py", GAME_DEAD_ACTION)
    rep = verify_game(path, sandboxed=False, world_factory=factory())
    assert rep["layers"]["G0_static"]["passed"] is True
    assert rep["failure_class"] == "ENV_ERROR"
    eff = rep["layers"]["G1_rollout"]["checks"]["efficacy"]
    assert eff["pass"] is False
    assert "idle" in eff["dead"]
    assert "idle" in rep["hint"]


def test_success_true_at_t0_fails_g2(tmp_path):
    path = _write(tmp_path, "t0.py", GAME_SUCCESS_AT_T0)
    rep = verify_game(path, sandboxed=False, world_factory=factory())
    assert rep["layers"]["G0_static"]["passed"] is True
    assert rep["layers"]["G1_rollout"]["passed"] is True
    assert rep["failure_class"] == "GOAL_ERROR"
    assert rep["layers"]["G2_goal"]["checks"]["success_false_at_t0"]["pass"] is False


def test_checkpoint_true_at_t0_fails_g2(tmp_path):
    path = _write(tmp_path, "cpt0.py", GAME_CP_AT_T0)
    rep = verify_game(path, sandboxed=False, world_factory=factory())
    assert rep["failure_class"] == "GOAL_ERROR"
    cp = rep["layers"]["G2_goal"]["checks"]["checkpoints_false_at_t0"]
    assert cp["pass"] is False
    assert cp["offenders"] == ["already_here"]
    assert "already_here" in rep["hint"]


def test_impossible_goal_is_unsolved_with_progress(tmp_path, legacy_thresholds):
    path = _write(tmp_path, "impossible.py", GAME_IMPOSSIBLE)
    rep = verify_game(path, sandboxed=False, world_factory=factory())
    assert rep["layers"]["G2_goal"]["passed"] is True
    assert rep["failure_class"] == "UNSOLVED"
    assert rep["layers"]["G3_solve"]["checks"]["solvable"]["pass"] is False
    assert rep["witness"] is None

    # v2.1: progress diagnosis names the boundary where the probe stalls.
    progress = rep["progress"]
    assert progress is not None
    assert progress["reach_counts"]["started"] > 0
    assert progress["reach_counts"]["deep"] == 0
    assert progress["stuck_after"] == "started"
    assert "'started'" in rep["hint"] and "'deep'" in rep["hint"]
    assert "stuck between" in rep["hint"]


def test_instant_win_is_trivial(tmp_path):
    path = _write(tmp_path, "instant.py", GAME_INSTANT_WIN)
    rep = verify_game(path, sandboxed=False, world_factory=factory())
    # Trivial goal is classified as a degenerate GOAL_ERROR, not UNSOLVED.
    assert rep["failure_class"] == "GOAL_ERROR"
    assert rep["layers"]["G3_solve"]["checks"]["non_trivial"]["pass"] is False
    assert "trivial" in rep["hint"].lower()


def test_dead_milestone_is_goal_error(tmp_path, legacy_thresholds):
    # The game is winnable, but "reached_sky" never latches on the witness.
    path = _write(tmp_path, "deadcp.py", GAME_DEAD_MILESTONE)
    rep = verify_game(path, sandboxed=False, world_factory=factory())
    assert rep["failure_class"] == "GOAL_ERROR"
    ml = rep["layers"]["G3_solve"]["checks"]["milestones_latched"]
    assert ml["pass"] is False
    assert ml["dead"] == ["reached_sky"]
    assert "reached_sky" in rep["hint"]


def test_misordered_milestones_warn_but_pass(tmp_path, legacy_thresholds):
    # Declared [almost, halfway]; empirically halfway latches first.
    path = _write(tmp_path, "misordered.py", GAME_MISORDERED)
    rep = verify_game(path, sandboxed=False, world_factory=factory())
    assert rep["passed"] is True, rep
    assert rep["warnings"], rep
    assert "order" in rep["warnings"][0]
    assert "halfway" in rep["warnings"][0] and "almost" in rep["warnings"][0]


def test_guided_second_pass_solves_two_stage_game(tmp_path, legacy_thresholds,
                                                  monkeypatch):
    # Pure random search misses the combo-lock game (empirically, seeds 0..39
    # never both arm early enough AND blast far enough); the checkpoint-guided
    # second pass reuses the best arming prefix and finds a witness. This asserts
    # RANDOM-search specifics (guided flag, guided probe seed), so pin the legacy
    # solver; the tree solver is exercised in tests/test_treesolve.py.
    monkeypatch.setenv("HARNESS_G3_SOLVER", "random")
    path = _write(tmp_path, "twostage.py", GAME_TWO_STAGE)
    rep = verify_game(path, sandboxed=False, world_factory=factory())
    assert rep["passed"] is True, rep
    assert rep["layers"]["G3_solve"]["checks"]["episodes"]["guided"] is True
    w = rep["witness"]
    # A guided witness carries a guided probe seed, not a first-pass one.
    assert w["seed"] >= GUIDED_SEED_BASE
    # Both milestones latched, in declared order, before the success tick.
    assert w["checkpoints"]["combo_10"] is not None
    assert w["checkpoints"]["armed"] is not None
    assert w["checkpoints"]["combo_10"] <= w["checkpoints"]["armed"] <= w["ticks"]

    # The guided witness replays exactly, latches included.
    game = load_game(GAME_TWO_STAGE)
    world = FakeWorld(seed=0)
    game.build(world)
    replay = run_episode(game, world, iter(w["actions"]), len(w["actions"]))
    assert replay["result"] == "success"
    assert replay["checkpoints"] == w["checkpoints"]


def test_sandbox_rejection_fails_g0(tmp_path):
    path = _write(tmp_path, "bad.py", GAME_BAD_IMPORT)
    rep = verify_game(path, sandboxed=False, world_factory=factory())
    assert rep["failure_class"] == "ENV_ERROR"
    assert rep["layers"]["G0_static"]["checks"]["sandbox_scan"]["pass"] is False
    assert "sandbox" in rep["hint"]


def test_determinism_catches_unseeded_randomness(tmp_path):
    # The game is fine; the (fake) engine leaks unseeded randomness.
    path = _write(tmp_path, "valid.py", GAME_VALID)
    rep = verify_game(path, sandboxed=False,
                      world_factory=factory(nondeterministic=True))
    assert rep["layers"]["G0_static"]["passed"] is True
    assert rep["failure_class"] == "ENV_ERROR"
    assert rep["layers"]["G1_rollout"]["checks"]["determinism"]["pass"] is False
    assert "deterministic" in rep["hint"]


def test_missing_game_file():
    rep = verify_game("does_not_exist_zzz.py", sandboxed=False,
                      world_factory=factory())
    assert rep["passed"] is False
    assert rep["failure_class"] == "ENV_ERROR"


def test_report_schema(tmp_path):
    path = _write(tmp_path, "valid.py", GAME_VALID)
    rep = verify_game(path, sandboxed=False, world_factory=factory())
    assert set(rep) == {"passed", "failure_class", "layers", "hint",
                        "warnings", "progress", "witness"}
    assert set(rep["layers"]) == {"G0_static", "G1_rollout", "G2_goal", "G3_solve"}


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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
