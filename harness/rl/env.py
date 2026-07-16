"""PlanckEnv — a Gymnasium-style RL environment over a game's "serve" subprocess.

One `PlanckEnv` owns ONE long-lived `node nodeworld/runner.js` process in the
additive interactive "serve" mode: `reset` rebuilds the world, each `step`
advances exactly ONE decision tick (act + K=6 physics steps + latch + terminal
checks — identical to `gameverify.run_episode`). Because the semantics match the
batch `episodes` mode bit-for-bit, a greedy action sequence recorded here replays
to success through `JsExecutor.run_batch` — the certificate bridge in certify.py.

OBSERVATION (code-state, NOT pixels — the challenge's "code-defined truth"):
a fixed-layout flat float32 vector, frozen at the first reset. The DIMENSION (2D
vs true-3D) is detected from the first frame's ``pos`` arity and PINNED for the
game's lifetime (a game may not flip dimension mid-run — asserted). Per body,
sorted by name with the CONTROLLED body first, padded to the game's body count:

  2D  (PER_BODY_2D = 10 floats/body) — UNCHANGED, byte-for-byte the legacy layout:
    [present, x/W, y/H, vx/VS, vy/VS, sin(angle), cos(angle),
     is_static, is_sensor, is_controlled]

  3D  (PER_BODY_3D = 14 floats/body):
    [present, x/W, y/H, z/D, vx/VS, vy/VS, vz/VS,
     qx, qy, qz, qw,                              (unit quaternion, see below)
     is_static, is_sensor, is_controlled]

`present` is 1.0 while the body exists and 0.0 once a game removes it (gems,
gates) or for pad slots — a clean, Markov-preserving way to encode disappearance
(the raw serve frame simply omits removed bodies). Positions are normalized by
world size (depth ``D`` = world_size[2] if present, else max(W,H) — the wire only
declares a 2D world box), velocities by VEL_SCALE; everything is clipped to
[-OBS_CLIP, OBS_CLIP].

3D ORIENTATION is a CANONICAL UNIT QUATERNION (qx,qy,qz,qw): the minimal complete
rotation encoding (4 floats vs 6 for two basis vectors), already bounded in
[-1,1] so it needs no extent scaling, NaN-safe (any non-finite -> identity), and
sign-canonicalized (w>=0) so one orientation has exactly ONE encoding (kills the
q/-q double cover -> a stable, learnable input). Source, in priority order and
WITHOUT any new wire field: an explicit body ``quat`` [x,y,z,w] if the game emits
one (forward-compatible — obs picks it up at zero layout change); else derived
from the scalar ``angle`` games emit today as a yaw about the world up-axis Y
(q = [0, sin(a/2), 0, cos(a/2)] — faithful to Godot ``rotation.y``/``euler().y``).

3D EGOCENTRIC HINTS (fixed 16-float block, appended once after the per-body
block; 3D ONLY so the 2D vector stays byte-identical): for the controlled body,
the relative position (other - controlled, world-normalized) of the K_EGO_NEIGHBORS
NEAREST non-controlled bodies (each slot: present, dx/W, dy/H, dz/D), then ONE
next-unlatched-checkpoint relative vector (present, dx/W, dy/H, dz/D). The
checkpoint target is INFERRED honestly from data already in the frame — a
non-controlled body whose name is associated (case-insensitive substring) with the
first unlatched checkpoint key (e.g. body ``ring_2`` <- cp ``threaded_ring_2``),
else the nearest non-controlled sensor body; zero when nothing is inferable.

Appended once at the end (both dims): the latched-checkpoint one-hot (declared
order) and the normalized tick — the stateful progress signal that makes gated
multi-stage games (latched switches open doors) observable to a feed-forward
policy.

OBS PROFILES (Elias 2026-07-16 — one knob, ``obs_profile``):
* ``"positions"`` (DEFAULT) — the vector above, byte-for-byte today's obs.
* ``"positions+rays"`` — the vector above + the egocentric raycast tail.
* ``"rays"`` (the PURE, honest profile) — PROPRIOCEPTION ONLY (the controlled body's
  own velocity + own orientation — 2D: ``vx,vy,sin,cos``; 3D: ``vx,vy,vz,qx,qy,qz,qw``),
  then the raycast tail, then the cp one-hot + tick. NO global positions of other
  bodies, NO K-nearest ego block: the agent knows how it is moving/pointing and sees
  the world ONLY through the rays.

EGOCENTRIC RAYCAST TAIL (OPT-IN; the godot_rl_agents FPS reference sensor — a SEMANTIC
retina, NO pixels/camera). When the serve host is asked for rays (init key ``rays``), each
frame carries a flat ``rays`` array cast FROM the controlled body IN ITS LOCAL FRAME. Per
ray: a normalized distance (``1.0`` = nothing within range, else ``hit_dist/range`` in
``[0,1]``) PLUS, when ``class_bits`` is on (default), a ``{static, dynamic, sensor}``
one-hot from the collider type (all-zero on no hit). So each ray is ``ray_stride`` floats
(1, or 1+3=4 with class bits), and the tail is ``n_rays * ray_stride`` = ``rays_obs_width``.
2D is a planar fan of ``n`` rays across ``fov_deg`` (the world IS a plane); 3D is the
reference WIDE grid of ``n_h x n_v`` (25x5) rays across ``fov_h x fov_v`` (a single fan is
vertically blind). The tail is appended at the very END of the vector, so the offsets ahead
of it are byte-identical and the tail is absent (byte-for-byte the no-rays vector) when off.
FIRST-FRAME NOTE: the reset frame's rays read all-clear (``1.0``) because the physics
broadphase is only populated after the first step; every subsequent frame is faithful.
Rays are DERIVED from body positions, so they are deliberately EXCLUDED from the stale-seek
softlock fingerprint (``fingerprint_from_obs`` stops before the tail; the pure profile falls
back to the raw serve snapshot). See ``godotworld/serve_game.gd`` for the fan/grid layout.

REWARD (the OMNI-EPIC lesson, LLM_RL_SYSTEMS §4.1): `+1.0 per NEWLY latched
checkpoint + 5.0 on success - 1.0 on failure`. `success` stays the unshaped binary
certificate — the "solved?" decision never reads this shaped reward. Episode ends
on a terminal `result` or at HORIZON (300) decision ticks.

======================================================================
godot_rl_agents AIController mapping  (GODOT_RL_MERGE.md §2 — pin this)
----------------------------------------------------------------------
The obs/action surface deliberately MIRRORS godot_rl_agents' AIController so the
Godot lane can replace this Node shell with zero retraining-code changes:

  AIController member / method          | PlanckEnv equivalent
  --------------------------------------|-------------------------------------
  get_obs() -> {"obs":[float,...]}      | reset()/step() return this flat vector
  get_action_space() ->                 | action_space = Discrete(n)
    {"act":{"size":n,                   |   n = len(ACTIONS); head index i maps
            "action_type":"discrete"}}  |   to ACTIONS[i] (the game's own string)
  set_action(action)                    | step(i) sends {"op":"act",
                                        |   "action": ACTIONS[i]} (one serve op)
  action_repeat == 6                    | one serve "act" = act + K=6 steps (== our K)
  get_reward() -> float                 | reward computed env-side (godot_rl has no
                                        |   runner latch, so the latch bookkeeping
                                        |   lives in the controller — here in step())
  done / needs_reset                    | terminated (success/failure) / truncated
  reset()                               | reset() sends {"op":"reset","seed":...}

Swapping in godot_rl_agents' Sync/TCP AIController later is thus a shell change,
not a policy or training-code change (the outer rung, GODOT_RL_MERGE.md §3/Phase 4).
======================================================================
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from dataclasses import dataclass

import numpy as np

# --- Constants ([eng.] = engineering choice) ---------------------------------
HORIZON = 300              # decision ticks per episode (matches PROBE_HORIZON) [eng.]
K_STEPS = 6                # physics steps per decision tick (CONTRACTS §2)
PER_BODY_2D = 10          # 2D obs features per body (see module docstring) [eng.]
PER_BODY_3D = 14          # 3D obs features per body (+z,+vz, quat vs sin/cos) [eng.]
PER_BODY = PER_BODY_2D    # backward-compat alias (importers pin the 2D width)
K_EGO_NEIGHBORS = 3       # nearest non-controlled bodies to hint egocentrically (3D) [eng.]
EGO_SLOT = 4              # floats per egocentric hint: present, dx/W, dy/H, dz/D
EGO_BLOCK_3D = (K_EGO_NEIGHBORS + 1) * EGO_SLOT  # K neighbours + 1 checkpoint hint = 16
# --- Obs profiles (Elias 2026-07-16): one knob, three exteroception regimes -------
# "positions"       -> today's obs (global per-body block, +3D ego hints). DEFAULT, and
#                      byte-for-byte the pre-rays vector.
# "positions+rays"  -> today's obs + the egocentric raycast tail.
# "rays"            -> the PURE, honest profile: PROPRIOCEPTION ONLY (own velocity + own
#                      orientation) + the raycast grid + cp one-hot + tick. NO global
#                      positions of other bodies, NO K-nearest ego block (the removed "cheat").
OBS_PROFILES = ("positions", "positions+rays", "rays")
RAYS_PROFILES = ("positions+rays", "rays")
PROPRIO_2D = 4            # pure-profile proprioception width: vx,vy, sin,cos [eng.]
PROPRIO_3D = 7            # pure-profile proprioception width: vx,vy,vz, qx,qy,qz,qw [eng.]
# Default egocentric raycast config (opt-in; every field overridable) [eng.]. Standardized
# on the godot_rl_agents FPS reference sensor (examples player.tscn WideRaycastSensor +
# ExtendedRaycastSensor.gd): a RECTANGULAR grid with a per-ray HIT-CLASS channel, no camera.
# 2D is a planar fan (n across fov_deg — the world IS a plane); 3D is the reference WIDE
# DEPTH-RETINA grid (n_h x n_v = 25x5 across fov_h x fov_v — a single fan is vertically blind
# to obstacles above/below). Each ray reports a normalized distance (1.0 = nothing within
# range) PLUS, when class_bits is on, a {static, dynamic, sensor} one-hot from the collider
# type (the reference's collision-layer class bits; ours are team-free) — the SEMANTIC retina.
# range is WORLD units; default 80.0 is the reference ray_length (the 3D world box is not
# wired, so per-world extent scaling is a follow-up — callers override range per game).
DEFAULT_RAYS = {"n": 16, "fov_deg": 180.0,
                "n_h": 25, "n_v": 5, "fov_h": 120.0, "fov_v": 60.0,
                "range": 80.0, "class_bits": True}
RAY_CLASS_BITS = 3        # {static, dynamic, sensor} one-hot per ray when class_bits on [eng.]
VEL_SCALE = 1000.0        # px/s velocity normalizer [eng.]
OBS_CLIP = 10.0           # clip normalized obs into [-OBS_CLIP, OBS_CLIP] [eng.]
R_CHECKPOINT = 1.0        # reward per newly latched checkpoint [eng.]
R_SUCCESS = 5.0           # terminal bonus on the unshaped success certificate [eng.]
R_FAILURE = -1.0          # terminal penalty on failure/error [eng.]
SERVE_TIMEOUT_S = 60.0    # per-op read budget before declaring the node dead [eng.]


# --- Minimal Gymnasium-compatible spaces (duck types; no gymnasium dep) ------
@dataclass
class Discrete:
    """Gymnasium-compatible discrete space (exposes ``.n``)."""
    n: int

    def sample(self, rng: np.random.Generator) -> int:
        return int(rng.integers(0, self.n))


@dataclass
class Box:
    """Gymnasium-compatible continuous box space (exposes ``.shape``/``.low``/``.high``)."""
    low: float
    high: float
    shape: tuple

    @property
    def size(self) -> int:
        return int(np.prod(self.shape))


# --- dimension helpers (single source of truth for obs sizing) ---------------
def _finite(x) -> float:
    """Float, NaN/inf coerced to 0.0 (obs must never carry a non-finite value)."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0
    return x if math.isfinite(x) else 0.0


def _vec3(v) -> tuple[float, float, float]:
    """(x, y, z) from a 2- or 3-vector (or None), NaN-safe, missing axes -> 0.0."""
    if not v:
        return (0.0, 0.0, 0.0)
    x = _finite(v[0]) if len(v) > 0 else 0.0
    y = _finite(v[1]) if len(v) > 1 else 0.0
    z = _finite(v[2]) if len(v) > 2 else 0.0
    return (x, y, z)


def _pos_arity(obs_state: dict, body_order) -> int | None:
    """Arity (2 or 3) of the first present body's ``pos``; None if no body has one."""
    for name in body_order:
        q = obs_state.get(name)
        if q is None:
            continue
        pos = q.get("pos")
        if pos is not None:
            try:
                return 3 if len(pos) >= 3 else 2
            except TypeError:
                return 2
    return None


def detect_dim(obs_state: dict) -> int:
    """Pin 2 or 3 from the first frame's pos arity (the layout dimension). Default 2."""
    arity = _pos_arity(obs_state or {}, list((obs_state or {}).keys()))
    return 3 if arity == 3 else 2


def per_body_width(dim: int) -> int:
    return PER_BODY_3D if int(dim) == 3 else PER_BODY_2D


def ego_block_width(dim: int) -> int:
    return EGO_BLOCK_3D if int(dim) == 3 else 0


def proprio_width(dim: int) -> int:
    """Pure-profile proprioception width (own velocity + own orientation)."""
    return PROPRIO_3D if int(dim) == 3 else PROPRIO_2D


def obs_dim_for(n_bodies: int, n_cp: int, dim: int,
                obs_profile: str = "positions", n_ray_floats: int = 0) -> int:
    """The frozen obs width. ``positions``/``positions+rays`` carry the global per-body
    block (+3D ego hints); the pure ``rays`` profile carries proprioception ONLY. The
    raycast tail (``n_ray_floats`` = n_rays * :func:`ray_stride`) is added for both rays
    profiles — use :func:`rays_obs_width` to compute it."""
    if obs_profile == "rays":
        base = proprio_width(dim) + n_cp + 1
    else:
        base = n_bodies * per_body_width(dim) + ego_block_width(dim) + n_cp + 1
    if obs_profile in RAYS_PROFILES:
        base += int(n_ray_floats)
    return base


def n_rays_of(rays, dim: int = 2) -> int:
    """Number of RAYS the fan/grid casts for a ``dim``-D game (``0`` when off). A 2D game
    is a planar fan of ``n`` rays; a 3D game is an ``n_h * n_v`` depth-retina grid. Missing
    fields fall back to :data:`DEFAULT_RAYS`. (For the obs FLOAT width, which multiplies by
    the per-ray stride, use :func:`rays_obs_width`.)"""
    if not rays:
        return 0
    try:
        if int(dim) == 3:
            nh = max(0, int(rays.get("n_h", DEFAULT_RAYS["n_h"])))
            nv = max(0, int(rays.get("n_v", DEFAULT_RAYS["n_v"])))
            return nh * nv
        return max(0, int(rays.get("n", DEFAULT_RAYS["n"])))
    except (TypeError, ValueError, AttributeError):
        return 0


def ray_stride(rays) -> int:
    """Floats a single ray contributes to the obs: the normalized distance, plus a
    {static, dynamic, sensor} class one-hot (``RAY_CLASS_BITS``) when ``class_bits`` is on
    (default, matching the FPS reference's class channel). ``1`` when class bits are off."""
    if not rays:
        return 1
    try:
        return 1 + (RAY_CLASS_BITS if rays.get("class_bits", DEFAULT_RAYS["class_bits"])
                    else 0)
    except (TypeError, AttributeError):
        return 1


def rays_obs_width(rays, dim: int = 2) -> int:
    """Total raycast floats appended to the obs tail: ``n_rays * ray_stride`` (``0`` off)."""
    return n_rays_of(rays, dim) * ray_stride(rays)


def normalize_rays(rays) -> dict | None:
    """Fill the opt-in raycast config with the :data:`DEFAULT_RAYS` [eng.] defaults (the
    2D-fan params, the 3D-grid params, and range) so the serve host and the Python obs
    sizer agree on every field. None/empty -> None (rays off)."""
    if not rays:
        return None
    out = dict(DEFAULT_RAYS)
    try:
        for k in out:
            if k in rays:
                out[k] = rays[k]
    except TypeError:
        return None
    return out


def _world_extents(world_size) -> tuple[float, float, float]:
    ws = tuple(world_size) if world_size else ()
    w = float(ws[0]) if len(ws) >= 1 else 800.0
    h = float(ws[1]) if len(ws) >= 2 else 600.0
    d = float(ws[2]) if len(ws) >= 3 else max(w, h)   # wire declares no depth -> isotropic
    return w, h, d


def build_obs_vector(obs_state: dict, latched: dict, body_order: list[str],
                     cp_keys: list[str], world_size, tick: int,
                     horizon: int, dim: int | None = None,
                     rays=None, obs_profile: str = "positions") -> np.ndarray:
    """Pure obs-vector builder (see the module docstring for the layout). Kept a
    free function so the layout can be unit-tested without a serve subprocess.

    ``dim`` is the PINNED layout dimension (2 or 3). When None it is auto-detected
    from ``obs_state`` (so a 2D state yields the byte-identical legacy vector); the
    envs pass their frozen ``_dim`` explicitly. When ``dim`` is given, the first
    present body's pos arity is asserted to match it — a game may not change
    dimension mid-run.

    ``obs_profile`` selects the vector BODY: ``"positions"`` (default; the global
    per-body block + 3D ego hints — byte-for-byte today's vector), or ``"rays"`` (the
    PURE profile: proprioception ONLY — own velocity + own orientation — no global
    positions of other bodies, no ego block). ``"positions+rays"`` is ``"positions"``
    with the raycast tail.

    ``rays`` (opt-in) is the frame's already-normalized egocentric raycast list. When
    ``None`` the vector carries no tail; when a list, its floats are appended at the
    VERY END (after the cp one-hot + tick), never disturbing the frozen offsets ahead of
    them. Ray values are in ``[0,1]`` (``1.0`` = nothing within range) so the
    ``[-OBS_CLIP, OBS_CLIP]`` clip is a no-op on them."""
    obs_state = obs_state or {}
    latched = latched or {}
    if dim is None:
        dim = 3 if (_pos_arity(obs_state, body_order) == 3) else 2
    else:
        dim = int(dim)
        arity = _pos_arity(obs_state, body_order)
        if arity is not None:
            expected = 3 if arity >= 3 else 2
            assert expected == dim, (
                f"obs pos arity {arity} != pinned dim {dim}: a game may not change "
                f"dimension mid-run")
    w, h, d = _world_extents(world_size)
    if obs_profile == "rays":
        vec = _build_obs_pure(obs_state, latched, body_order, cp_keys, tick, horizon, dim)
    elif dim == 3:
        vec = _build_obs_3d(obs_state, latched, body_order, cp_keys, w, h, d,
                            tick, horizon)
    else:
        vec = _build_obs_2d(obs_state, latched, body_order, cp_keys, w, h, tick,
                            horizon)
    if rays is not None:
        rv = np.asarray(rays, dtype=np.float32).reshape(-1)
        np.clip(rv, -OBS_CLIP, OBS_CLIP, out=rv)
        vec = np.concatenate([vec, rv])
    return vec


def _build_obs_2d(obs_state, latched, body_order, cp_keys, w, h, tick, horizon):
    """The legacy 2D layout — kept byte-for-byte identical (regression-pinned)."""
    obs_dim = len(body_order) * PER_BODY_2D + len(cp_keys) + 1
    vec = np.zeros(obs_dim, dtype=np.float32)
    i = 0
    for name in body_order:
        q = obs_state.get(name)
        if q is not None:
            px, py = q.get("pos", (0.0, 0.0))
            vx, vy = q.get("vel", (0.0, 0.0))
            ang = float(q.get("angle", 0.0))
            vec[i + 0] = 1.0                              # present
            vec[i + 1] = px / w
            vec[i + 2] = py / h
            vec[i + 3] = vx / VEL_SCALE
            vec[i + 4] = vy / VEL_SCALE
            vec[i + 5] = math.sin(ang)
            vec[i + 6] = math.cos(ang)
            vec[i + 7] = 1.0 if q.get("static") else 0.0
            vec[i + 8] = 1.0 if q.get("sensor") else 0.0
            vec[i + 9] = 1.0 if q.get("controlled") else 0.0
        # else: removed/pad slot stays all-zero (present == 0)
        i += PER_BODY_2D
    for key in cp_keys:                                  # latched one-hot
        vec[i] = 1.0 if latched.get(key) is not None else 0.0
        i += 1
    vec[i] = min(1.0, tick / float(horizon))             # normalized tick
    np.clip(vec, -OBS_CLIP, OBS_CLIP, out=vec)
    return vec


def _orientation_quat(q) -> tuple[float, float, float, float]:
    """Canonical unit quaternion (qx,qy,qz,qw) for a 3D body — see the module
    docstring. Prefers an explicit ``quat`` field, else a yaw-about-Y quaternion
    from the scalar ``angle``. NaN-safe (identity fallback); sign-canonical (w>=0)."""
    quat = q.get("quat")
    if quat is not None and len(quat) >= 4:
        x, y, z, wq = (_finite(quat[0]), _finite(quat[1]),
                       _finite(quat[2]), _finite(quat[3]))
    else:
        half = _finite(q.get("angle", 0.0)) * 0.5       # yaw about world up-axis Y
        x, y, z, wq = 0.0, math.sin(half), 0.0, math.cos(half)
    nrm = math.sqrt(x * x + y * y + z * z + wq * wq)
    if nrm < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)                      # degenerate -> identity
    x, y, z, wq = x / nrm, y / nrm, z / nrm, wq / nrm
    if wq < 0.0:                                         # canonicalize the q/-q cover
        x, y, z, wq = -x, -y, -z, -wq
    return (x, y, z, wq)


def _controlled_body(obs_state, body_order):
    """The controlled body's dict (first with the ``controlled`` flag), else the first
    present body, else None — the proprioception source for the pure ``rays`` profile."""
    for name in body_order:
        q = obs_state.get(name)
        if q is not None and q.get("controlled"):
            return q
    for name in body_order:
        q = obs_state.get(name)
        if q is not None:
            return q
    return None


def _controlled_pos(obs_state, body_order) -> tuple[float, float, float]:
    """3D position of the controlled body (first with the flag), else the first
    present body, else the origin."""
    for name in body_order:
        q = obs_state.get(name)
        if q is not None and q.get("controlled"):
            return _vec3(q.get("pos"))
    for name in body_order:
        q = obs_state.get(name)
        if q is not None:
            return _vec3(q.get("pos"))
    return (0.0, 0.0, 0.0)


def _next_checkpoint_target(obs_state, body_order, cp_keys, latched):
    """3D position of the next-checkpoint target, inferred WITHOUT any new wire
    field, or None. (1) a non-controlled body whose name is associated
    (case-insensitive substring, either direction) with the FIRST unlatched
    checkpoint key; else (2) the nearest non-controlled sensor body (goal pad)."""
    for key in cp_keys:                                  # first unlatched cp only
        if latched.get(key) is not None:
            continue
        kl = str(key).lower()
        for name in body_order:
            q = obs_state.get(name)
            if q is None or q.get("controlled"):
                continue
            nl = str(name).lower()
            if nl and (nl in kl or kl in nl):
                return _vec3(q.get("pos"))
        break                                            # no match -> sensor fallback
    cx, cy, cz = _controlled_pos(obs_state, body_order)
    best = None
    for name in body_order:
        q = obs_state.get(name)
        if q is None or q.get("controlled") or not q.get("sensor"):
            continue
        px, py, pz = _vec3(q.get("pos"))
        dist2 = (px - cx) ** 2 + (py - cy) ** 2 + (pz - cz) ** 2
        if best is None or dist2 < best[0]:
            best = (dist2, (px, py, pz))
    return best[1] if best else None


def _build_obs_3d(obs_state, latched, body_order, cp_keys, w, h, d, tick, horizon):
    """The 3D layout: per-body (z, vz, quaternion) block + a fixed egocentric-hint
    block + the shared checkpoint one-hot and tick tail."""
    n = len(body_order)
    obs_dim = n * PER_BODY_3D + EGO_BLOCK_3D + len(cp_keys) + 1
    vec = np.zeros(obs_dim, dtype=np.float32)
    i = 0
    for name in body_order:
        q = obs_state.get(name)
        if q is not None:
            px, py, pz = _vec3(q.get("pos"))
            vx, vy, vz = _vec3(q.get("vel"))
            qx, qy, qz, qw = _orientation_quat(q)
            vec[i + 0] = 1.0                              # present
            vec[i + 1] = px / w
            vec[i + 2] = py / h
            vec[i + 3] = pz / d
            vec[i + 4] = vx / VEL_SCALE
            vec[i + 5] = vy / VEL_SCALE
            vec[i + 6] = vz / VEL_SCALE
            vec[i + 7] = qx
            vec[i + 8] = qy
            vec[i + 9] = qz
            vec[i + 10] = qw
            vec[i + 11] = 1.0 if q.get("static") else 0.0
            vec[i + 12] = 1.0 if q.get("sensor") else 0.0
            vec[i + 13] = 1.0 if q.get("controlled") else 0.0
        # else: removed/pad slot stays all-zero (present == 0)
        i += PER_BODY_3D

    # -- egocentric hints (fixed 16-float block) --------------------------
    cx, cy, cz = _controlled_pos(obs_state, body_order)
    neighbours = []
    for name in body_order:
        q = obs_state.get(name)
        if q is None or q.get("controlled"):
            continue
        px, py, pz = _vec3(q.get("pos"))
        dx, dy, dz = px - cx, py - cy, pz - cz
        neighbours.append((math.sqrt(dx * dx + dy * dy + dz * dz), str(name),
                           dx, dy, dz))
    neighbours.sort(key=lambda t: (t[0], t[1]))          # nearest first, name tie-break
    for k in range(K_EGO_NEIGHBORS):
        if k < len(neighbours):
            _, _, dx, dy, dz = neighbours[k]
            vec[i + 0] = 1.0
            vec[i + 1] = dx / w
            vec[i + 2] = dy / h
            vec[i + 3] = dz / d
        i += EGO_SLOT
    tgt = _next_checkpoint_target(obs_state, body_order, cp_keys, latched)
    if tgt is not None:
        vec[i + 0] = 1.0
        vec[i + 1] = (tgt[0] - cx) / w
        vec[i + 2] = (tgt[1] - cy) / h
        vec[i + 3] = (tgt[2] - cz) / d
    i += EGO_SLOT

    for key in cp_keys:                                  # latched one-hot
        vec[i] = 1.0 if latched.get(key) is not None else 0.0
        i += 1
    vec[i] = min(1.0, tick / float(horizon))             # normalized tick
    np.clip(vec, -OBS_CLIP, OBS_CLIP, out=vec)
    return vec


def _build_obs_pure(obs_state, latched, body_order, cp_keys, tick, horizon, dim):
    """The PURE ``rays`` profile body: proprioception of the CONTROLLED body only (own
    velocity + own orientation — NO global position, NO other bodies), then the shared cp
    one-hot + tick tail. The raycast grid is appended by ``build_obs_vector`` after this.
    Egocentric + honest: the agent knows how it is moving/pointing and sees the world only
    through the rays (Elias' honest profile), never through absolute coordinates."""
    pw = proprio_width(dim)
    obs_dim = pw + len(cp_keys) + 1
    vec = np.zeros(obs_dim, dtype=np.float32)
    q = _controlled_body(obs_state, body_order)
    if q is not None:
        vx, vy, vz = _vec3(q.get("vel"))
        if dim == 3:
            qx, qy, qz, qw = _orientation_quat(q)
            vec[0] = vx / VEL_SCALE
            vec[1] = vy / VEL_SCALE
            vec[2] = vz / VEL_SCALE
            vec[3] = qx
            vec[4] = qy
            vec[5] = qz
            vec[6] = qw
        else:
            ang = _finite(q.get("angle", 0.0))
            vec[0] = vx / VEL_SCALE
            vec[1] = vy / VEL_SCALE
            vec[2] = math.sin(ang)
            vec[3] = math.cos(ang)
    i = pw
    for key in cp_keys:                                  # latched one-hot
        vec[i] = 1.0 if latched.get(key) is not None else 0.0
        i += 1
    vec[i] = min(1.0, tick / float(horizon))             # normalized tick
    np.clip(vec, -OBS_CLIP, OBS_CLIP, out=vec)
    return vec


class PlanckEnv:
    """Gymnasium-style single env over one game's serve-mode node subprocess.

    API: ``reset(seed=None) -> (obs, info)`` and
    ``step(action_idx) -> (obs, reward, terminated, truncated, info)``, plus
    ``observation_space`` / ``action_space`` (available after construction) and
    ``close()``. One process is spawned per env and reused across episodes.
    """

    def __init__(self, game_path: str, *, runner_path: str | None = None,
                 node: str | None = None, horizon: int = HORIZON):
        self.game_path = game_path
        self.horizon = int(horizon)
        with open(game_path, "r", encoding="utf-8") as fh:
            self._source = fh.read()

        self._node = node or os.environ.get("HARNESS_NODE", "node")
        if runner_path is None:
            from harness.verify.executors import default_runner_path
            runner_path = default_runner_path()
        self._runner_path = runner_path

        self._stderr = tempfile.TemporaryFile(mode="w+")
        self._proc = subprocess.Popen(
            [self._node, self._runner_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr,
            text=True, encoding="utf-8", bufsize=1,
            cwd=os.path.dirname(self._runner_path) or None,
        )
        # Handshake: send the init line, read the ready line.
        ready = self._exchange({"mode": "serve", "source": self._source})
        if not ready.get("ready"):
            self.close()
            raise RuntimeError(f"serve init failed for {game_path}: {ready.get('error')}")
        self.actions: list[str] = list(ready.get("actions") or [])
        self.title: str = ready.get("title") or os.path.basename(game_path)
        self.world_size = tuple(ready.get("world_size") or (800, 600))

        # Layout is discovered on the FIRST reset and then frozen.
        self._body_order: list[str] | None = None
        self._cp_keys: list[str] | None = None
        self._dim: int = 2                          # pinned in _freeze_layout (2 or 3)
        self.action_space = Discrete(len(self.actions))
        self.observation_space: Box | None = None  # set after first reset

        self._tick = 0
        self._done = True
        self._prev_latched: set[str] = set()

        # Priming reset: freezes the body layout / obs space so observation_space
        # and action_space are available right after construction (gym convention).
        self.reset(seed=0)

    # -- process I/O ------------------------------------------------------
    def _exchange(self, op: dict) -> dict:
        """Send one op line, read exactly one reply line. Raises if node died."""
        if self._proc.poll() is not None:
            raise RuntimeError(f"serve process exited (code {self._proc.returncode})"
                               f"\n{self._read_stderr()}")
        try:
            self._proc.stdin.write(json.dumps(op) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError(f"serve stdin write failed: {exc}\n{self._read_stderr()}")
        line = self._proc.stdout.readline()
        if line == "":
            raise RuntimeError("serve process closed stdout unexpectedly"
                               f"\n{self._read_stderr()}")
        return json.loads(line)

    def _read_stderr(self) -> str:
        try:
            self._stderr.seek(0)
            return "STDERR: " + self._stderr.read()[-2000:]
        except Exception:
            return ""

    # -- layout / observation --------------------------------------------
    def _freeze_layout(self, frame: dict) -> None:
        obs_state = frame.get("obs_state", {})
        controlled = [n for n, q in obs_state.items() if q.get("controlled")]
        others = sorted(n for n in obs_state if n not in controlled)
        # Controlled body first (LLM_RL_SYSTEMS §4.1), then the rest sorted by name.
        self._body_order = list(controlled) + others
        self._cp_keys = list((frame.get("latched") or {}).keys())
        self._dim = detect_dim(obs_state)               # 2D vs true-3D, then PINNED
        obs_dim = obs_dim_for(len(self._body_order), len(self._cp_keys), self._dim)
        self.observation_space = Box(-OBS_CLIP, OBS_CLIP, (obs_dim,))

    def _observe(self, frame: dict) -> np.ndarray:
        return build_obs_vector(
            frame.get("obs_state", {}), frame.get("latched") or {},
            self._body_order, self._cp_keys, self.world_size, self._tick,
            self.horizon, dim=self._dim)

    @staticmethod
    def _latched_set(frame: dict) -> set[str]:
        return {k for k, v in (frame.get("latched") or {}).items() if v is not None}

    # -- Gymnasium API ----------------------------------------------------
    def reset(self, seed: int = 0):
        frame = self._exchange({"op": "reset", "seed": int(seed)})
        if self._body_order is None:
            self._freeze_layout(frame)
        self._tick = 0
        self._done = False
        self._prev_latched = self._latched_set(frame)
        return self._observe(frame), {"latched": dict(frame.get("latched") or {})}

    def step(self, action_idx: int):
        if self._done:
            raise RuntimeError("step() after episode end — call reset() first")
        action = self.actions[int(action_idx)]
        frame = self._exchange({"op": "act", "action": action})
        self._tick = int(frame.get("tick", self._tick + 1))
        result = frame.get("result")

        latched_now = self._latched_set(frame)
        new_latches = len(latched_now - self._prev_latched)
        self._prev_latched = latched_now
        reward = R_CHECKPOINT * new_latches

        terminated = False
        truncated = False
        if result == "success":
            reward += R_SUCCESS
            terminated = True
        elif result in ("failure", "error"):
            reward += R_FAILURE
            terminated = True
        elif self._tick >= self.horizon:
            truncated = True
        self._done = terminated or truncated

        info = {
            "result": result,
            "tick": self._tick,
            "latched": dict(frame.get("latched") or {}),
            "n_latched": len(latched_now),
            "success": result == "success",
        }
        return self._observe(frame), float(reward), terminated, truncated, info

    def close(self) -> None:
        proc = getattr(self, "_proc", None)
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.write(json.dumps({"op": "close"}) + "\n")
                proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        stderr = getattr(self, "_stderr", None)
        if stderr is not None:
            try:
                stderr.close()
            except Exception:
                pass

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


# --- Gymnasium adapter (OPTIONAL dep — the SB3 trainer lane only) -------------
# `gymnasium` is not needed on the vendored PPO lane (ppo.py drives PlanckEnv's
# gym-compatible surface directly with the Box/Discrete duck-types above), so it
# is an optional dependency that arrives only with stable-baselines3
# (GODOT_RL_AGENTS_CAPABILITIES.md §6.7, the [LF] migration). To keep env.py
# importable WITHOUT gymnasium, the real gymnasium.Env subclass is built on first
# use rather than at module import.
_GYM_ENV_CLS = None


def _gym_env_cls():
    """Lazily define (and cache) the gymnasium.Env adapter subclass over PlanckEnv."""
    global _GYM_ENV_CLS
    if _GYM_ENV_CLS is not None:
        return _GYM_ENV_CLS

    import gymnasium as gym
    from gymnasium import spaces

    class GymPlanckEnv(gym.Env):
        """A thin gymnasium.Env WRAPPER over one live PlanckEnv (wrap, don't
        rewrite — PlanckEnv already mirrors the Gym API). Its only jobs are to
        re-export PlanckEnv's frozen spaces as gymnasium ``spaces`` (sized from
        the env's own ``obs_dim``/``n_actions``) and to thread gymnasium's
        keyword-only ``reset(*, seed=...)`` contract into PlanckEnv's
        deterministic per-episode seeding.

        The seed is LATCHED: a ``reset(seed=None)`` — the form SB3's VecEnv uses
        on autoreset — reuses the last explicit seed, so every episode of a given
        env replays the SAME deterministic world. That exactly reproduces the
        vendored VecEnv contract (base_seed+i, reused on autoreset) the RL witness
        depends on, which is why the seed plumbing is witness-relevant.
        """

        metadata = {"render_modes": []}

        def __init__(self, planck_env: "PlanckEnv"):
            super().__init__()
            self._env = planck_env
            self._seed = 0                         # latched seed (see class docstring)
            obs_dim = int(planck_env.observation_space.shape[0])
            self.observation_space = spaces.Box(
                low=-OBS_CLIP, high=OBS_CLIP, shape=(obs_dim,), dtype=np.float32)
            self.action_space = spaces.Discrete(planck_env.action_space.n)
            # Convenience passthroughs the eval rollouts read (action strings, horizon).
            self.actions = planck_env.actions
            self.horizon = planck_env.horizon

        def reset(self, *, seed=None, options=None):
            if seed is not None:
                self._seed = int(seed)            # remember it for autoreset
            obs, info = self._env.reset(seed=self._seed)
            return obs, info

        def step(self, action):
            return self._env.step(int(action))

        def close(self):
            self._env.close()

    _GYM_ENV_CLS = GymPlanckEnv
    return _GYM_ENV_CLS


def wrap_gym(planck_env: "PlanckEnv"):
    """Wrap a live PlanckEnv in the gymnasium.Env adapter (see ``_gym_env_cls``)."""
    return _gym_env_cls()(planck_env)


def make_gym_env(game_path: str, **kwargs):
    """Construct a PlanckEnv for ``game_path`` and return it wrapped as a
    gymnasium.Env. Factory form so SB3's ``make_vec_env``/``DummyVecEnv`` thunks
    (`lambda: make_gym_env(path)`) work, while env.py stays importable when
    gymnasium is absent (the vendored lane never calls this)."""
    return wrap_gym(PlanckEnv(game_path, **kwargs))
