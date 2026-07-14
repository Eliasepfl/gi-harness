"""Tests for the G3' RL-learnability spike (harness/rl + runner.js serve mode).

Three groups:
  1. Pure obs-layout unit tests (no node) — build_obs_vector + the spaces.
  2. Serve-mode protocol tests (skipif node/planck absent) — reset/act
     determinism, latch reporting, terminal semantics, and parity with the batch
     "episodes" mode (the certificate-bridge foundation).
  3. A TINY smoke train on an inline corridor game asserting the return improves
     and the emitted witness replays through JsExecutor.run_batch.
"""

import json
import math
import os
import shutil
import subprocess

import numpy as np
import pytest

from harness.rl import env as rlenv
from harness.rl.env import Box, Discrete, build_obs_vector
from harness.verify.executors import default_runner_path

# --- node / serve availability -------------------------------------------- #
NODE = shutil.which(os.environ.get("HARNESS_NODE", "node"))
RUNNER = default_runner_path()

# A trivial inline corridor game (JS): roll the ball right to the goal. Fully
# G-contract compliant (2 actions, 3 entities, one controlled dynamic, success
# false at t0, 2 snake_case checkpoints false at t0, both actions live).
CORRIDOR_JS = """
const TITLE = "Test Corridor";
const PROMPT = "roll the ball right into the goal";
const WORLD_SIZE = [800, 200];
const ACTIONS = ["right", "left"];
function build(world) {
    world.set_gravity(0, -900);
    world.add("floor", "box", { pos: [400, 20], size: [800, 40], static: true, friction: 0.9 });
    world.add("ball", "circle", { pos: [60, 60], radius: 15, mass: 1, friction: 0.5 });
    world.control("ball");
    world.add("goal_zone", "box", { pos: [740, 70], size: [80, 80], static: true, sensor: true });
}
function act(world, action) {
    if (action === "right") world.impulse("ball", [40, 0]);
    else if (action === "left") world.impulse("ball", [-40, 0]);
}
function on_step(world) {
    const v = world.query("ball").vel;
    const vx = Math.max(-250, Math.min(250, v[0]));
    if (vx !== v[0]) world.set_velocity("ball", [vx, v[1]]);
}
function success(world) { return world.contacts("ball", "goal_zone"); }
function failure(world) { const p = world.query("ball").pos; return p[0] < -30 || p[1] < -30; }
function checkpoints(world) {
    const x = world.query("ball").pos[0];
    return { halfway: x > 380, at_goal: world.contacts("ball", "goal_zone") };
}
"""


def _serve(source, ops, timeout=30):
    """Run a serve session: init + ops, return (ready_dict, [reply_dicts])."""
    lines = [json.dumps({"mode": "serve", "source": source})]
    lines += [json.dumps(o) for o in ops]
    lines.append(json.dumps({"op": "close"}))
    payload = "\n".join(lines) + "\n"
    proc = subprocess.run([NODE, RUNNER], input=payload, capture_output=True,
                          text=True, encoding="utf-8", timeout=timeout,
                          cwd=os.path.dirname(RUNNER) or None)
    out = [json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip()]
    return out[0], out[1:]


def _serve_ready():
    if NODE is None:
        return False
    try:
        ready, _ = _serve(CORRIDOR_JS, [])
        return bool(ready.get("ready"))
    except Exception:
        return False


SERVE_OK = _serve_ready()
requires_serve = pytest.mark.skipif(not SERVE_OK,
                                    reason="node/planck serve mode unavailable")


@pytest.fixture
def corridor(tmp_path):
    p = tmp_path / "corridor.js"
    p.write_text(CORRIDOR_JS, encoding="utf-8")
    return str(p)


# ======================================================================== #
# 1. Pure obs-layout unit tests (no node)
# ======================================================================== #
def test_spaces_shapes():
    d = Discrete(3)
    assert d.n == 3
    b = Box(-10.0, 10.0, (7,))
    assert b.shape == (7,) and b.size == 7


def test_build_obs_vector_layout():
    body_order = ["ball", "goal"]     # controlled first, by convention
    cp_keys = ["halfway", "at_goal"]
    world_size = (800, 200)
    obs_state = {
        "ball": {"pos": [400, 100], "vel": [500, -100], "angle": math.pi / 2,
                 "static": False, "sensor": False, "controlled": True},
        "goal": {"pos": [740, 70], "vel": [0, 0], "angle": 0.0,
                 "static": True, "sensor": True, "controlled": False},
    }
    latched = {"halfway": 3, "at_goal": None}
    vec = build_obs_vector(obs_state, latched, body_order, cp_keys, world_size,
                           tick=30, horizon=300)
    # dim = 2 bodies * 10 + 2 cp + 1 tick
    assert vec.shape == (2 * rlenv.PER_BODY + 2 + 1,)
    # ball slot: present, x/W, y/H, vx/VS, vy/VS, sin, cos, static, sensor, ctrl
    assert vec[0] == 1.0
    assert vec[1] == pytest.approx(400 / 800)
    assert vec[2] == pytest.approx(100 / 200)
    assert vec[3] == pytest.approx(500 / rlenv.VEL_SCALE)
    assert vec[5] == pytest.approx(math.sin(math.pi / 2), abs=1e-6)  # sin
    assert vec[7] == 0.0 and vec[8] == 0.0 and vec[9] == 1.0          # static/sensor/ctrl
    # goal slot flags
    g = rlenv.PER_BODY
    assert vec[g + 7] == 1.0 and vec[g + 8] == 1.0 and vec[g + 9] == 0.0
    # checkpoint one-hot: halfway latched (1.0), at_goal not (0.0)
    assert vec[2 * rlenv.PER_BODY] == 1.0
    assert vec[2 * rlenv.PER_BODY + 1] == 0.0
    # normalized tick
    assert vec[-1] == pytest.approx(30 / 300)


def test_build_obs_vector_missing_body_is_zeroed():
    body_order = ["ball", "gem"]
    obs_state = {"ball": {"pos": [10, 10], "vel": [0, 0], "angle": 0.0,
                          "controlled": True}}  # 'gem' removed by the game
    vec = build_obs_vector(obs_state, {}, body_order, [], (800, 600),
                           tick=0, horizon=300)
    gem = rlenv.PER_BODY
    assert np.all(vec[gem:gem + rlenv.PER_BODY] == 0.0)  # present bit 0 -> all zero


def test_build_obs_vector_clips_extremes():
    body_order = ["ball"]
    obs_state = {"ball": {"pos": [0, 0], "vel": [1e9, -1e9], "angle": 0.0,
                          "controlled": True}}
    vec = build_obs_vector(obs_state, {}, body_order, [], (800, 600),
                           tick=0, horizon=300)
    assert vec.max() <= rlenv.OBS_CLIP and vec.min() >= -rlenv.OBS_CLIP


# ======================================================================== #
# 2. Serve-mode protocol tests (skipif node/planck absent)
# ======================================================================== #
@requires_serve
def test_serve_handshake_declares_action_space():
    ready, _ = _serve(CORRIDOR_JS, [])
    assert ready["ready"] is True
    assert ready["actions"] == ["right", "left"]
    assert ready["world_size"] == [800, 200]
    assert ready["title"] == "Test Corridor"


@requires_serve
def test_serve_reset_returns_full_obs_state():
    _, frames = _serve(CORRIDOR_JS, [{"op": "reset", "seed": 0}])
    f = frames[0]
    assert f["tick"] == 0 and f["result"] is None
    assert set(f["obs_state"]) == {"floor", "ball", "goal_zone"}
    # latch keys seeded (declared order), all null at t0
    assert f["latched"] == {"halfway": None, "at_goal": None}
    assert f["obs_state"]["ball"]["controlled"] is True


@requires_serve
def test_serve_determinism_byte_identical():
    ops = [{"op": "reset", "seed": 7}] + [{"op": "act", "action": "right"}] * 20
    r1 = _serve(CORRIDOR_JS, ops)
    r2 = _serve(CORRIDOR_JS, ops)
    assert json.dumps(r1) == json.dumps(r2)


@requires_serve
def test_serve_latch_reporting_records_tick():
    ops = [{"op": "reset", "seed": 0}] + [{"op": "act", "action": "right"}] * 60
    _, frames = _serve(CORRIDOR_JS, ops)
    acts = frames[1:]
    # halfway must latch at some tick and then stay latched (monotone)
    latch_tick = next((f["tick"] for f in acts if f["latched"]["halfway"] is not None), None)
    assert latch_tick is not None
    after = [f["latched"]["halfway"] for f in acts if f["tick"] >= latch_tick]
    assert all(v == latch_tick for v in after)  # latched once, never regresses


@requires_serve
def test_serve_terminal_success_then_echo():
    ops = [{"op": "reset", "seed": 0}] + [{"op": "act", "action": "right"}] * 120
    _, frames = _serve(CORRIDOR_JS, ops)
    acts = frames[1:]
    successes = [f for f in acts if f["result"] == "success"]
    assert successes, "rolling right should reach the goal"
    first_win = successes[0]["tick"]
    # after the terminal tick, further acts echo the terminal frame (no re-step):
    tail = [f for f in acts if f["tick"] >= first_win]
    assert all(f["result"] == "success" and f["tick"] == first_win for f in tail)


@requires_serve
def test_serve_failure_terminal():
    ops = [{"op": "reset", "seed": 0}] + [{"op": "act", "action": "left"}] * 40
    _, frames = _serve(CORRIDOR_JS, ops)
    assert any(f["result"] == "failure" for f in frames[1:])


@requires_serve
def test_serve_parity_with_batch_episodes():
    """Serve stepping must equal the batch 'episodes' runner (the bridge)."""
    from harness.verify.executors import JsExecutor
    actions = ["right", "right", "left", "right", "right", "right"] * 6
    ops = [{"op": "reset", "seed": 3}] + [{"op": "act", "action": a} for a in actions]
    _, frames = _serve(CORRIDOR_JS, ops)
    serve_last = frames[-1]
    rec = JsExecutor().run_batch(CORRIDOR_JS, [{"seed": 3, "actions": actions}],
                                 max_ticks=len(actions))[0]
    assert serve_last["obs_state"]["ball"]["pos"] == rec["final_snapshot"]["ball"]["pos"]
    # latched map (non-null entries) agrees
    serve_latched = {k: v for k, v in serve_last["latched"].items() if v is not None}
    batch_latched = {k: v for k, v in rec["checkpoints"].items() if v is not None}
    assert serve_latched == batch_latched


# ======================================================================== #
# 3. PlanckEnv obs-layout + reward, and a tiny smoke train
# ======================================================================== #
@requires_serve
def test_planckenv_spaces_and_layout(corridor):
    env = rlenv.PlanckEnv(corridor)
    try:
        assert env.action_space.n == 2
        n_bodies = len(env._body_order)
        assert env.observation_space.shape == (n_bodies * rlenv.PER_BODY + 2 + 1,)
        # controlled body ('ball') is slot 0
        assert env._body_order[0] == "ball"
        obs, info = env.reset(seed=0)
        assert obs[9] == 1.0  # slot-0 controlled flag
        assert obs.dtype == np.float32
    finally:
        env.close()


@requires_serve
def test_planckenv_reward_shaping(corridor):
    env = rlenv.PlanckEnv(corridor, horizon=200)
    try:
        env.reset(seed=0)
        got_checkpoint_reward = False
        reached_goal = False
        for _ in range(200):
            obs, r, term, trunc, info = env.step(0)  # action 0 == "right"
            if info["latched"].get("halfway") is not None and r >= rlenv.R_CHECKPOINT - 1e-6:
                got_checkpoint_reward = True
            if info["result"] == "success":
                assert r >= rlenv.R_SUCCESS - 1e-6  # terminal bonus present
                assert term is True
                reached_goal = True
                break
            if term or trunc:
                break
        assert got_checkpoint_reward, "latching 'halfway' must pay +1"
        assert reached_goal, "rolling right must solve the corridor"
    finally:
        env.close()


@requires_serve
def test_smoke_train_improves_and_witness_replays(corridor):
    """<=30s: PPO on the corridor must improve return and reach success; then the
    emitted witness must replay to success through the batch executor."""
    from harness.rl import ppo
    from harness.rl.certify import g3_prime

    def make():
        return rlenv.PlanckEnv(corridor, horizon=150)

    probe = make()
    obs_dim = probe.observation_space.shape[0]
    n = probe.action_space.n
    probe.close()

    res = ppo.train(make, obs_dim, n, total_steps=12000, seed=0,
                    num_envs=4, num_steps=64, patience=999)
    curve = res["curve_return"]
    assert res["steps_to_first_success"] is not None, "corridor should be solved in training"
    assert max(curve) > curve[0], "return must improve over training"

    # And the full certifier emits a witness that bridges to the batch executor.
    cert = g3_prime(corridor, budget_steps=12000, num_envs=4, num_steps=64,
                    patience=999, n_eval=8)
    assert cert["learnable"] is True
    assert cert["rl_witness"] is not None
    assert cert["bridge_ok"] is True  # replayed to success via JsExecutor
