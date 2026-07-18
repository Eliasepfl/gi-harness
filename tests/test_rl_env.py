"""Pure obs-layout unit tests for the shared RL primitives (harness/rl/env.py).

build_obs_vector + the Gymnasium-compatible space duck-types + the obs sizing /
raycast helpers — no engine or subprocess, so these pin the obs CONTRACT the Godot
serve envs reuse.
"""

import math

import numpy as np
import pytest

from harness.rl import env as rlenv
from harness.rl.env import Box, Discrete, build_obs_vector


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


def test_3d_ego_block_has_no_checkpoint_hint_tail():
    """REMOVED (Elias 2026-07-16): the name-matched next-checkpoint direction hint. The 3D
    ego block is now the K-nearest-body slots ONLY (16 -> 12 floats); there is no goal-hint
    slot, and the block is immediately followed by the checkpoint one-hot + tick tail."""
    assert rlenv.EGO_BLOCK_3D == rlenv.K_EGO_NEIGHBORS * rlenv.EGO_SLOT   # no +1 hint slot
    assert not hasattr(rlenv, "_next_checkpoint_target")                  # helper deleted
    body = ["ctrl", "ring_1", "ring_2"]
    cp = ["threaded_ring_1", "threaded_ring_2"]
    s = {"ctrl": {"pos": [0, 0, 0], "vel": [0, 0, 0], "angle": 0, "controlled": True},
         "ring_1": {"pos": [5, 0, 0], "vel": [0, 0, 0], "angle": 0, "static": True},
         "ring_2": {"pos": [0, 0, 50], "vel": [0, 0, 0], "angle": 0, "static": True}}
    v = build_obs_vector(s, {"threaded_ring_1": 9, "threaded_ring_2": None},
                         body, cp, (800, 600), 0, 300, dim=3)
    # width == per-body + K-nearest ego block (no hint) + cp one-hot + tick
    assert v.shape[0] == 3 * rlenv.PER_BODY_3D + rlenv.EGO_BLOCK_3D + len(cp) + 1
    assert v.shape[0] == rlenv.obs_dim_for(3, len(cp), 3)
    # the slot right after the K ego neighbours is the FIRST checkpoint one-hot, NOT a hint.
    cp_base = 3 * rlenv.PER_BODY_3D + rlenv.K_EGO_NEIGHBORS * rlenv.EGO_SLOT
    assert v[cp_base + 0] == 1.0        # threaded_ring_1 latched -> one-hot 1.0 (not a hint dx)
    assert v[cp_base + 1] == 0.0        # threaded_ring_2 unlatched
    assert v[-1] == pytest.approx(0 / 300)   # tick tail directly after the cp one-hot


def test_obs_dim_for_and_detect_dim_helpers():
    assert rlenv.obs_dim_for(3, 2, 2) == 3 * rlenv.PER_BODY_2D + 2 + 1
    assert rlenv.obs_dim_for(3, 2, 3) == 3 * rlenv.PER_BODY_3D + rlenv.EGO_BLOCK_3D + 2 + 1
    assert rlenv.detect_dim({"b": {"pos": [1, 2]}}) == 2
    assert rlenv.detect_dim({"b": {"pos": [1, 2, 3]}}) == 3
    assert rlenv.detect_dim({}) == 2                      # empty -> default 2


# ======================================================================== #
# 1c. Egocentric raycast obs TAIL (opt-in; appended AFTER the frozen blocks)
# ======================================================================== #
def test_rays_n_helper():
    assert rlenv.n_rays_of(None) == 0
    assert rlenv.n_rays_of({}) == 0
    # 2D game -> planar fan of n rays
    assert rlenv.n_rays_of({"n": 12, "fov_deg": 120, "range": 900}, 2) == 12
    assert rlenv.n_rays_of({"n": 0}, 2) == 0
    assert rlenv.n_rays_of({"n": -3}, 2) == 0              # clamped to >= 0
    # 3D game -> n_h * n_v grid (the depth retina)
    assert rlenv.n_rays_of({"n_h": 9, "n_v": 5}, 3) == 45
    assert rlenv.n_rays_of({"n_h": 4, "n_v": 3}, 3) == 12
    # missing grid fields fall back to DEFAULT_RAYS (25x5), non-empty config stays on
    assert rlenv.n_rays_of({"range": 100}, 3) == rlenv.DEFAULT_RAYS["n_h"] * rlenv.DEFAULT_RAYS["n_v"]
    assert rlenv.DEFAULT_RAYS["n_h"] == 25                 # reference wide sensor default


def test_ray_stride_and_obs_width():
    # class bits on (default) -> distance + {static,dynamic,sensor} one-hot = 4 floats/ray
    assert rlenv.ray_stride({"class_bits": True}) == 1 + rlenv.RAY_CLASS_BITS == 4
    assert rlenv.ray_stride({"class_bits": False}) == 1
    assert rlenv.ray_stride(None) == 1
    assert rlenv.ray_stride({}) == 1                        # empty -> off
    # 3D grid 25x5 with class bits -> 125 rays * 4 = 500 obs floats
    r = {"n_h": 25, "n_v": 5, "class_bits": True}
    assert rlenv.n_rays_of(r, 3) == 125
    assert rlenv.rays_obs_width(r, 3) == 500
    # class off -> 1 float/ray
    assert rlenv.rays_obs_width({"n_h": 25, "n_v": 5, "class_bits": False}, 3) == 125
    # 2D fan n=16, class off -> 16 floats
    assert rlenv.rays_obs_width({"n": 16, "class_bits": False}, 2) == 16


def test_normalize_rays_fills_defaults():
    assert rlenv.normalize_rays(None) is None
    assert rlenv.normalize_rays({}) is None                # empty -> off
    r = rlenv.normalize_rays({"n_h": 7})
    assert r["n_h"] == 7 and r["n_v"] == rlenv.DEFAULT_RAYS["n_v"]
    assert r["range"] == rlenv.DEFAULT_RAYS["range"]
    assert r["class_bits"] is True                         # reference default carried in


def test_obs_dim_for_profiles():
    # positions (default) unchanged; positions+rays adds the ray-float tail; rays is
    # proprio-only. The last arg is the tail FLOAT count (rays_obs_width), not ray count.
    assert rlenv.obs_dim_for(3, 2, 3) == 3 * rlenv.PER_BODY_3D + rlenv.EGO_BLOCK_3D + 2 + 1
    assert rlenv.obs_dim_for(3, 2, 3, "positions+rays", 500) == \
        3 * rlenv.PER_BODY_3D + rlenv.EGO_BLOCK_3D + 2 + 1 + 500
    assert rlenv.obs_dim_for(3, 2, 3, "rays", 500) == rlenv.PROPRIO_3D + 2 + 1 + 500
    assert rlenv.obs_dim_for(3, 2, 2, "rays", 16) == rlenv.PROPRIO_2D + 2 + 1 + 16


def test_pure_rays_profile_is_proprioception_only():
    """The pure 'rays' profile carries own velocity + own orientation (+ cp + tick + ray
    tail) and NO global positions of any body."""
    body = ["glider", "ring_1"]
    cp = ["threaded_ring_1"]
    ws = (800, 600)
    s = {"glider": {"pos": [123, 456, 789], "vel": [100, -200, 311], "angle": 0.0,
                    "controlled": True},
         "ring_1": {"pos": [701, 71, 555], "vel": [0, 0, 0], "static": True,
                    "controlled": False}}
    rays = [1.0, 0.5, 0.25]
    v = build_obs_vector(s, {"threaded_ring_1": None}, body, cp, ws, 30, 300,
                         dim=3, rays=rays, obs_profile="rays")
    assert v.shape[0] == rlenv.obs_dim_for(2, 1, 3, "rays", len(rays))
    # proprioception: own vel (normalized), then quaternion (identity here: qw=1)
    assert v[0] == pytest.approx(100 / rlenv.VEL_SCALE)
    assert v[2] == pytest.approx(311 / rlenv.VEL_SCALE)
    assert v[6] == pytest.approx(1.0)                       # qw of identity yaw
    # NO absolute position of ANY body leaks into the pure profile.
    for bad in (123 / 800, 456 / 600, 789 / max(ws), 701 / 800, 71 / 600, 555 / max(ws)):
        assert not np.any(np.isclose(v, bad)), f"leaked global position {bad}"
    # cp one-hot + tick sit right after proprio, rays are the tail
    assert v[rlenv.PROPRIO_3D] == 0.0                       # unlatched cp
    assert v[rlenv.PROPRIO_3D + 1] == pytest.approx(30 / 300)  # tick
    assert np.allclose(v[-len(rays):], rays)


def test_rays_absent_is_byte_for_byte_the_no_rays_vector():
    """rays=None must leave the obs byte-identical to today (2D AND 3D)."""
    body = ["ball", "goal"]
    cp = ["at_goal"]
    ws = (800, 600)
    s2 = {"ball": {"pos": [100, 200], "vel": [10, 20], "angle": 0.5,
                   "controlled": True},
          "goal": {"pos": [700, 70], "vel": [0, 0], "angle": 0.0,
                   "static": True, "controlled": False}}
    a = build_obs_vector(s2, {"at_goal": None}, body, cp, ws, 5, 300, dim=2)
    b = build_obs_vector(s2, {"at_goal": None}, body, cp, ws, 5, 300, dim=2, rays=None)
    assert a.tobytes() == b.tobytes()
    s3 = {"ball": {"pos": [100, 200, 50], "vel": [10, 20, 5], "angle": 0.5,
                   "controlled": True},
          "goal": {"pos": [700, 70, 90], "vel": [0, 0, 0], "static": True,
                   "controlled": False}}
    c = build_obs_vector(s3, {"at_goal": None}, body, cp, ws, 5, 300, dim=3)
    d = build_obs_vector(s3, {"at_goal": None}, body, cp, ws, 5, 300, dim=3, rays=None)
    assert c.tobytes() == d.tobytes()


def test_rays_appended_after_all_blocks_2d_and_3d():
    """A rays list is appended at the VERY END (after the cp one-hot + tick), so the
    per-body block and the cp/tick tail keep their frozen offsets."""
    body = ["ball", "goal"]
    cp = ["at_goal"]
    ws = (800, 600)
    rays = [1.0, 0.5, 0.25, 1.0]
    s2 = {"ball": {"pos": [100, 200], "vel": [10, 20], "angle": 0.5,
                   "controlled": True},
          "goal": {"pos": [700, 70], "vel": [0, 0], "static": True,
                   "controlled": False}}
    base2 = build_obs_vector(s2, {"at_goal": None}, body, cp, ws, 5, 300, dim=2)
    v2 = build_obs_vector(s2, {"at_goal": None}, body, cp, ws, 5, 300, dim=2, rays=rays)
    assert v2.shape[0] == base2.shape[0] + len(rays)
    assert np.array_equal(v2[:base2.shape[0]], base2)       # prefix untouched
    assert np.allclose(v2[-len(rays):], rays)
    s3 = {"ball": {"pos": [100, 200, 50], "vel": [10, 20, 5], "angle": 0.5,
                   "controlled": True},
          "goal": {"pos": [700, 70, 90], "vel": [0, 0, 0], "static": True,
                   "controlled": False}}
    base3 = build_obs_vector(s3, {"at_goal": None}, body, cp, ws, 5, 300, dim=3)
    v3 = build_obs_vector(s3, {"at_goal": None}, body, cp, ws, 5, 300, dim=3, rays=rays)
    assert v3.shape[0] == base3.shape[0] + len(rays)
    assert np.array_equal(v3[:base3.shape[0]], base3)
    assert np.allclose(v3[-len(rays):], rays)
    assert v3.dtype == np.float32


