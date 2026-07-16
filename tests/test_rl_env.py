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
# 1b. Dimension-aware obs — 3D layout + 2D no-regression (no node)
# ======================================================================== #
def _legacy_2d_obs(obs_state, latched, body_order, cp_keys, world_size, tick, horizon):
    """A FROZEN, independent transcription of the pre-3D 2D obs builder — the
    reference the new dimension-aware builder must match byte-for-byte on a 2D state.
    If this ever drifts from ``build_obs_vector(dim=2)`` a trained 2D model breaks."""
    w, h = world_size
    vel_scale, clip = rlenv.VEL_SCALE, rlenv.OBS_CLIP
    vec = np.zeros(len(body_order) * 10 + len(cp_keys) + 1, dtype=np.float32)
    i = 0
    for name in body_order:
        q = obs_state.get(name)
        if q is not None:
            px, py = q.get("pos", (0.0, 0.0))
            vx, vy = q.get("vel", (0.0, 0.0))
            ang = float(q.get("angle", 0.0))
            vec[i + 0] = 1.0
            vec[i + 1] = px / w
            vec[i + 2] = py / h
            vec[i + 3] = vx / vel_scale
            vec[i + 4] = vy / vel_scale
            vec[i + 5] = math.sin(ang)
            vec[i + 6] = math.cos(ang)
            vec[i + 7] = 1.0 if q.get("static") else 0.0
            vec[i + 8] = 1.0 if q.get("sensor") else 0.0
            vec[i + 9] = 1.0 if q.get("controlled") else 0.0
        i += 10
    for key in cp_keys:
        vec[i] = 1.0 if latched.get(key) is not None else 0.0
        i += 1
    vec[i] = min(1.0, tick / float(horizon))
    np.clip(vec, -clip, clip, out=vec)
    return vec


def test_2d_obs_is_byte_for_byte_the_legacy_layout():
    """NO REGRESSION on 2D: the new builder must reproduce the frozen legacy vector
    exactly (a mix of a present body, a removed body, latched + unlatched cps)."""
    body = ["ball", "gem", "goal"]
    cp = ["halfway", "at_goal"]
    ws = (800, 600)
    s = {"ball": {"pos": [123.4, 55.1], "vel": [900, -1200], "angle": 1.3,
                  "static": False, "sensor": False, "controlled": True},
         "goal": {"pos": [740, 70], "vel": [0, 0], "angle": 0.0,
                  "static": True, "sensor": True, "controlled": False}}  # 'gem' removed
    lat = {"halfway": 5, "at_goal": None}
    old = _legacy_2d_obs(s, lat, body, cp, ws, 42, 300)
    auto = build_obs_vector(s, lat, body, cp, ws, 42, 300)             # auto-detect -> 2D
    pinned = build_obs_vector(s, lat, body, cp, ws, 42, 300, dim=2)    # explicit dim
    assert old.tobytes() == auto.tobytes() == pinned.tobytes()
    assert rlenv.detect_dim(s) == 2


def test_3d_layout_exact_vector():
    """Documents the EXACT 3D per-body layout: [present, x/W, y/H, z/D, vx/VS, vy/VS,
    vz/VS, qx, qy, qz, qw, static, sensor, controlled] (14 floats), then the egocentric
    block, the checkpoint one-hot, and the normalized tick."""
    body = ["glider", "ring_1"]
    cp = ["threaded_ring_1"]
    W, H = 800.0, 600.0
    D = max(W, H)                                        # depth == max(W,H) (no wire depth)
    s = {"glider": {"pos": [80, 60, 40], "vel": [100, -200, 300], "angle": 0.0,
                    "controlled": True, "static": False},
         "ring_1": {"pos": [80, 60, 240], "vel": [0, 0, 0], "angle": 0.0,
                    "controlled": False, "static": True}}
    lat = {"threaded_ring_1": None}
    v = build_obs_vector(s, lat, body, cp, (W, H), tick=30, horizon=300, dim=3)
    assert rlenv.detect_dim(s) == 3
    assert v.shape == (2 * rlenv.PER_BODY_3D + rlenv.EGO_BLOCK_3D + 1 + 1,)
    assert v.shape[0] == rlenv.obs_dim_for(2, 1, 3)
    # glider slot 0: present, normalized pos (incl z/D), normalized vel (incl vz)
    assert v[0] == 1.0
    assert v[1] == pytest.approx(80 / W) and v[2] == pytest.approx(60 / H)
    assert v[3] == pytest.approx(40 / D)                 # z normalized by depth
    assert v[6] == pytest.approx(300 / rlenv.VEL_SCALE)  # vz present
    # identity orientation (angle 0) -> quaternion (0,0,0,1)
    assert (v[7], v[8], v[9], v[10]) == pytest.approx((0.0, 0.0, 0.0, 1.0))
    assert v[13] == 1.0                                  # controlled flag
    # ring_1 static flag (slot 1, offset 11)
    assert v[rlenv.PER_BODY_3D + 11] == 1.0
    # checkpoint one-hot + tick tail
    assert v[-2] == 0.0                                  # threaded_ring_1 not latched
    assert v[-1] == pytest.approx(30 / 300)


def test_3d_orientation_quaternion_from_yaw_is_canonical():
    """Absent a `quat` field, orientation is a yaw-about-Y quaternion derived from the
    scalar `angle` games emit; unit-norm and sign-canonical (w>=0)."""
    a = 1.2
    s = {"b": {"pos": [1, 2, 3], "vel": [0, 0, 0], "angle": a, "controlled": True}}
    v = build_obs_vector(s, {}, ["b"], [], (800, 600), 0, 300, dim=3)
    qx, qy, qz, qw = v[7], v[8], v[9], v[10]
    assert (qx, qy, qz, qw) == pytest.approx(
        (0.0, math.sin(a / 2), 0.0, math.cos(a / 2)), abs=1e-6)
    assert qx * qx + qy * qy + qz * qz + qw * qw == pytest.approx(1.0, abs=1e-5)
    # A yaw past pi flips w negative pre-canonicalization -> canonical form keeps w>=0.
    s2 = {"b": {"pos": [1, 2, 3], "vel": [0, 0, 0], "angle": 4.0, "controlled": True}}
    v2 = build_obs_vector(s2, {}, ["b"], [], (800, 600), 0, 300, dim=3)
    assert v2[10] >= 0.0


def test_3d_orientation_prefers_explicit_quat_field():
    """A body `quat` [x,y,z,w] (forward-compat wire field) is used directly, normalized
    and sign-canonicalized — no layout change."""
    s = {"b": {"pos": [1, 2, 3], "vel": [0, 0, 0], "quat": [0.0, 0.0, 0.0, -2.0],
               "controlled": True}}
    v = build_obs_vector(s, {}, ["b"], [], (800, 600), 0, 300, dim=3)
    # (0,0,0,-2) -> normalized (0,0,0,-1) -> canonical (0,0,0,1)
    assert (v[7], v[8], v[9], v[10]) == pytest.approx((0.0, 0.0, 0.0, 1.0))


def test_dimension_pin_asserts_on_mid_run_arity_change():
    """A game may not change dimension mid-run: a 3-vector pos under a pinned dim=2
    (or vice versa) raises a CLEAR assertion rather than the old silent unpack crash."""
    s3 = {"b": {"pos": [1, 2, 3], "vel": [0, 0, 0], "angle": 0.0, "controlled": True}}
    with pytest.raises(AssertionError, match="may not change dimension"):
        build_obs_vector(s3, {}, ["b"], [], (800, 600), 0, 300, dim=2)
    s2 = {"b": {"pos": [1, 2], "vel": [0, 0], "angle": 0.0, "controlled": True}}
    with pytest.raises(AssertionError, match="may not change dimension"):
        build_obs_vector(s2, {}, ["b"], [], (800, 600), 0, 300, dim=3)


def test_3d_obs_is_nan_safe():
    """Non-finite pos/vel/angle (a mis-behaving physics tick) never leak into the obs —
    coerced to 0, quaternion falls back to identity."""
    s = {"b": {"pos": [float("nan"), 2.0, float("inf")],
               "vel": [float("nan"), 0.0, 0.0], "angle": float("nan"),
               "controlled": True}}
    v = build_obs_vector(s, {}, ["b"], [], (800, 600), 0, 300, dim=3)
    assert np.all(np.isfinite(v))
    assert (v[7], v[8], v[9], v[10]) == pytest.approx((0.0, 0.0, 0.0, 1.0))  # identity


def test_3d_egocentric_nearest_neighbours_sorted_and_padded():
    """The K nearest non-controlled bodies contribute relative (dx,dy,dz) hints, nearest
    first; fewer than K bodies -> zero-padded present bits."""
    body = ["ctrl", "far", "near"]
    s = {"ctrl": {"pos": [0, 0, 0], "vel": [0, 0, 0], "angle": 0, "controlled": True},
         "near": {"pos": [0, 0, 10], "vel": [0, 0, 0], "angle": 0, "static": True},
         "far": {"pos": [0, 0, 500], "vel": [0, 0, 0], "angle": 0, "static": True}}
    v = build_obs_vector(s, {}, body, [], (800, 600), 0, 300, dim=3)
    eb = 3 * rlenv.PER_BODY_3D                            # ego block base (3 body slots)
    D = 800.0
    # slot 0 == 'near' (nearest), slot 1 == 'far', slot 2 == pad (present 0)
    assert v[eb + 0] == 1.0 and v[eb + 3] == pytest.approx(10 / D)
    assert v[eb + rlenv.EGO_SLOT + 0] == 1.0 and v[eb + rlenv.EGO_SLOT + 3] == pytest.approx(500 / D)
    assert v[eb + 2 * rlenv.EGO_SLOT + 0] == 0.0         # only 2 neighbours -> 3rd padded


def test_3d_egocentric_checkpoint_hint_first_unlatched_then_fallbacks():
    """The checkpoint hint points at the first UNLATCHED checkpoint's associated body
    (name substring); falls back to the nearest sensor body; else stays zero."""
    body = ["ctrl", "ring_1", "ring_2"]
    cp = ["threaded_ring_1", "threaded_ring_2"]
    s = {"ctrl": {"pos": [0, 0, 0], "vel": [0, 0, 0], "angle": 0, "controlled": True},
         "ring_1": {"pos": [5, 0, 0], "vel": [0, 0, 0], "angle": 0, "static": True},
         "ring_2": {"pos": [0, 0, 50], "vel": [0, 0, 0], "angle": 0, "static": True}}
    hint_base = 3 * rlenv.PER_BODY_3D + rlenv.K_EGO_NEIGHBORS * rlenv.EGO_SLOT
    D = 800.0
    # ring_1 already latched -> hint points at ring_2 (name-matched to threaded_ring_2)
    v = build_obs_vector(s, {"threaded_ring_1": 9, "threaded_ring_2": None},
                         body, cp, (800, 600), 0, 300, dim=3)
    assert v[hint_base + 0] == 1.0 and v[hint_base + 3] == pytest.approx(50 / D)
    # nothing latched -> first unlatched is threaded_ring_1 -> points at ring_1
    v0 = build_obs_vector(s, {"threaded_ring_1": None, "threaded_ring_2": None},
                          body, cp, (800, 600), 0, 300, dim=3)
    assert v0[hint_base + 1] == pytest.approx(5 / 800.0) and v0[hint_base + 3] == 0.0
    # No name match + no sensor bodies -> hint is zero (honest: not inferable)
    body2 = ["ctrl", "wall"]
    s2 = {"ctrl": {"pos": [0, 0, 0], "vel": [0, 0, 0], "angle": 0, "controlled": True},
          "wall": {"pos": [9, 0, 0], "vel": [0, 0, 0], "angle": 0, "static": True}}
    hb2 = 2 * rlenv.PER_BODY_3D + rlenv.K_EGO_NEIGHBORS * rlenv.EGO_SLOT
    vn = build_obs_vector(s2, {"reached_goal": None}, body2, ["reached_goal"],
                          (800, 600), 0, 300, dim=3)
    assert vn[hb2 + 0] == 0.0                             # present bit off -> zero hint
    # Sensor fallback: an unlatched cp with no name match points at the nearest sensor.
    body3 = ["ctrl", "pad"]
    s3 = {"ctrl": {"pos": [0, 0, 0], "vel": [0, 0, 0], "angle": 0, "controlled": True},
          "pad": {"pos": [0, 7, 0], "vel": [0, 0, 0], "angle": 0, "sensor": True}}
    vs = build_obs_vector(s3, {"win": None}, body3, ["win"], (800, 600), 0, 300, dim=3)
    assert vs[hb2 + 0] == 1.0 and vs[hb2 + 2] == pytest.approx(7 / 600.0)


def test_obs_dim_for_and_detect_dim_helpers():
    assert rlenv.obs_dim_for(3, 2, 2) == 3 * rlenv.PER_BODY_2D + 2 + 1
    assert rlenv.obs_dim_for(3, 2, 3) == 3 * rlenv.PER_BODY_3D + rlenv.EGO_BLOCK_3D + 2 + 1
    assert rlenv.detect_dim({"b": {"pos": [1, 2]}}) == 2
    assert rlenv.detect_dim({"b": {"pos": [1, 2, 3]}}) == 3
    assert rlenv.detect_dim({}) == 2                      # empty -> default 2


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


# ======================================================================== #
# 4. Gymnasium adapter over PlanckEnv (the SB3 lane — skipif gymnasium absent)
# ======================================================================== #
# gymnasium ships WITH stable-baselines3 (requirements.txt, the [LF] migration);
# it is absent from the current certifier image, so these SKIP here and the
# orchestrator re-runs them in the sb3 image.
@requires_serve
def test_gym_adapter_spaces_mirror_planckenv(corridor):
    gym = pytest.importorskip("gymnasium")
    from harness.rl.env import make_gym_env

    genv = make_gym_env(corridor)
    try:
        # Spaces are gymnasium spaces sized from the env's own obs_dim / n_actions.
        assert isinstance(genv.observation_space, gym.spaces.Box)
        assert isinstance(genv.action_space, gym.spaces.Discrete)
        assert genv.action_space.n == 2
        n_bodies = len(genv._env._body_order)
        assert genv.observation_space.shape == (n_bodies * rlenv.PER_BODY + 2 + 1,)
        assert genv.observation_space.dtype == np.float32
        obs, info = genv.reset(seed=0)
        assert obs.dtype == np.float32
        assert genv.observation_space.contains(obs)
    finally:
        genv.close()


@requires_serve
def test_gym_adapter_seed_flows_into_planckenv_reset(corridor):
    """reset(seed=) must forward the EXACT int into PlanckEnv's seeded reset, and a
    following reset(seed=None) (SB3's autoreset form) must REUSE the latched seed —
    the vendored VecEnv contract (fixed per-env seed, reused) the witness needs."""
    pytest.importorskip("gymnasium")
    from harness.rl.env import make_gym_env

    genv = make_gym_env(corridor)
    try:
        seen: list[int] = []
        planck = genv._env
        orig = planck.reset

        def spy(seed=0):
            seen.append(int(seed))
            return orig(seed=seed)

        planck.reset = spy
        genv.reset(seed=13)     # explicit -> latched
        genv.reset()            # None -> reuse 13
        genv.reset(seed=4)      # new explicit -> latched
        genv.reset()            # None -> reuse 4
        assert seen == [13, 13, 4, 4]
    finally:
        genv.close()


@requires_serve
def test_gym_adapter_obs_matches_raw_planckenv(corridor):
    """The adapter returns PlanckEnv's obs UNCHANGED (same seed -> same vector)."""
    pytest.importorskip("gymnasium")
    from harness.rl.env import make_gym_env

    genv = make_gym_env(corridor)
    raw = rlenv.PlanckEnv(corridor)
    try:
        a_obs, _ = genv.reset(seed=5)
        r_obs, _ = raw.reset(seed=5)
        assert np.array_equal(a_obs, r_obs)
    finally:
        genv.close()
        raw.close()


# ======================================================================== #
# 5. Trainer-switch regression guard — the vendored path is provably unchanged
# ======================================================================== #
# NB: no sb3 needed here — this RUNS in the current image. `default` and
# `vendored` are two INDEPENDENT g3_prime invocations at the same seed; their
# byte-identity proves BOTH that the default trainer is still the vendored PPO AND
# that the vendored path is deterministic run-to-run (the refactor perturbs
# nothing). Only wall-clock keys are excused.
_TIMING_KEYS = ("wall_clock_s", "throughput_sps")


def _stable(result: dict) -> dict:
    return {k: v for k, v in result.items() if k not in _TIMING_KEYS}


@requires_serve
def test_trainer_default_is_sb3_and_vendored_stays_deterministic(corridor):
    # Default flipped to sb3 after parity R1 (notes/rl_agent/SB3_PARITY_R1.md);
    # the vendored path must stay byte-identical run-to-run until it is retired.
    pytest.importorskip("stable_baselines3")
    from harness.rl.certify import g3_prime

    kw = dict(budget_steps=6000, seed=0, n_eval=4,
              num_envs=4, num_steps=64, patience=999)
    default = g3_prime(corridor, **kw)                       # trainer defaults here
    vendored = g3_prime(corridor, trainer="vendored", **kw)
    vendored2 = g3_prime(corridor, trainer="vendored", **kw)

    assert default["trainer"] == "sb3"                       # default IS sb3 (post-parity)
    assert _stable(vendored) == _stable(vendored2)           # vendored: deterministic, unchanged
