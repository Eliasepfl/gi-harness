"""episode.py -- export ONE (game, trajectory) pair to an EPISODE PACKAGE.

An episode package (``<out>/<slug>/<seed>/``) has three artifacts, all describing
the SAME deterministic certified replay of a game's winning witness:

  * ``episode.json``  -- meta: game id/slug, dimension (2D/3D), objective_text (the
    game's generation prompt + the ordered checkpoint names), seed, witness_source
    (rl|tree), ticks, outcome, harness version (git sha), reward-scheme constants,
    the tick-0 build state, and the checkpoint latch map.
  * ``steps.jsonl``   -- ONE line per decision tick t (1..T): the action, the wire
    body STATE dict (per-body pos/vel/angle/controlled/static), the per-tick latched
    checkpoint map, the reward ``{shaping, terminal, total}`` and the done flag.
  * ``frames/t%05d.png`` -- the in-engine rendered PIXEL frame at each tick t.

TWO CHANNELS, ONE REPLAY. Both the STATE trail and the PIXEL trail come from the
identical ``{seed, actions}`` witness:

  * STATE  -- ``run_batch(..., frames_every=1)`` on the game's executor gives a
    per-tick ``{tick, entities:{name: {pos,vel,angle,controlled,static}}}`` trail
    PLUS an episode-level checkpoint latch map ``{name: latch_tick|None}``.
  * PIXELS -- :func:`harness.verify.capture.capture_gif` with ``frames_dir=`` drives
    ``capture_host.gd`` (software-GL, non-headless) which writes one PNG per decision
    tick with the EXACT serve stepping discipline (act + K=6 + latch + terminal). The
    PNG ordinal equals the tick number as long as no subsampling happens, which the
    exporter guarantees by sizing ``max_frames >= n_actions + 2`` (stride stays 1).

Both channels emit a t=0 frame (the settle/build state) followed by ticks 1..T. The
exporter drops the t=0 pixel (the "gray settle frame") from ``frames/`` and stores
the t=0 STATE as ``episode.json:build_state``, so the package has exactly ``T`` steps
== ``T`` frames == ``ticks`` -- the alignment the loader and the validation assert.

REWARD is recomputed per tick through :func:`harness.rl.env.step_reward` -- the SINGLE
SOURCE OF TRUTH the three RL env step() paths call. The exporter imports it (and the
scheme constants); it NEVER reimplements the reward math. The per-tick
``(c_before, c_after)`` latched counts are reconstructed from the latch-tick map
(``latched at tick t  <=>  latch_tick is not None and latch_tick <= t``), exactly the
count env's step() would have observed, so a label here is the training label.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

# The reward is imported from the ONE source of truth -- never reimplemented here.
from harness.rl import env as rl_env

SCHEMA_VERSION = "1.0"

# Some checkpoint maps carry a latch value that is falsey-but-latched historically;
# we treat "latched" strictly as "latch_tick is an int" (serve/py both store the
# 1-based decision tick at which the checkpoint first became true, else None).


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _repo_root() -> Path:
    """The harness repo root (this file is harness/export/episode.py -> up three)."""
    return Path(__file__).resolve().parents[2]


def harness_version(repo_root: Path | None = None) -> str:
    """The harness git sha (``git rev-parse HEAD``), or ``"unknown"`` off a checkout."""
    root = str(repo_root or _repo_root())
    try:
        out = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15)
        sha = out.stdout.strip()
        return sha or "unknown"
    except Exception:  # noqa: BLE001 -- provenance is best-effort, never fatal
        return "unknown"


def deslug(slug: str) -> str:
    """De-slugify a game directory name into the human generation prompt.

    ``report.json`` carries no explicit ``prompt`` field for gdscript games (and the
    replay meta prompt is blank), so the slug -- which IS the sanitized prompt -- is
    the honest source: ``a_2d_window_washer_winch_your_platform_u`` -> ``a 2d window
    washer winch your platform u``."""
    return slug.replace("_", " ").strip()


def _latched_count(cp_latch: dict, upto_tick: int) -> int:
    """How many declared checkpoints are latched at (i.e. by the end of) tick ``upto_tick``.
    A checkpoint counts iff its recorded latch tick is an int and ``<= upto_tick`` -- the
    same latched-set size the env's ``step`` observes at that tick."""
    n = 0
    for v in cp_latch.values():
        if isinstance(v, bool):        # guard: a stray True/False is not a latch tick
            continue
        if isinstance(v, int) and v <= upto_tick:
            n += 1
    return n


def _latched_map(cp_latch: dict, upto_tick: int) -> dict:
    """The per-tick boolean latched map at tick ``upto_tick`` (declared order preserved)."""
    out = {}
    for key, v in cp_latch.items():
        out[key] = bool(isinstance(v, int) and not isinstance(v, bool) and v <= upto_tick)
    return out


def _outcome(result) -> str:
    """Map a replay ``result`` to the episode outcome vocabulary (success/failure/timeout)."""
    if result == "success":
        return "success"
    if result in ("failure", "error"):
        return "failure"
    return "timeout"          # exhausted / budget / None -> ran out of witness/horizon


# --------------------------------------------------------------------------- #
# Reward labelling -- decomposed through step_reward (the training source of truth)
# --------------------------------------------------------------------------- #
def step_reward_parts(c_before: int, c_after: int, n_cp: int, result, tick: int,
                      horizon: int) -> dict:
    """``{shaping, terminal, total}`` for one tick, computed ONLY through
    :func:`harness.rl.env.step_reward` (never reimplemented):

      * ``total``    = ``step_reward(..., result, ...)``  -- the exact training label.
      * ``shaping``  = ``step_reward(..., result=None, ...)`` -- the same call with NO
        terminal payoff, i.e. the checkpoint shaping + (default-off) living cost.
      * ``terminal`` = ``total - shaping`` -- the decayed success payoff / failure penalty.

    The decomposition is exact because ``step_reward`` adds the terminal payoff OUTSIDE
    the shaping (see env.step_reward); ``total`` is always byte-identical to the label
    the RL env step() would have emitted for this tick."""
    # FULL float precision -- the ``total`` label must be byte-identical to the RL training
    # signal (no rounding); ``shaping``/``terminal`` are the exact additive decomposition.
    total = rl_env.step_reward(c_before, c_after, n_cp, result, tick, horizon)
    shaping = rl_env.step_reward(c_before, c_after, n_cp, None, tick, horizon)
    terminal = total - shaping
    return {"shaping": float(shaping),
            "terminal": float(terminal),
            "total": float(total)}


# --------------------------------------------------------------------------- #
# State trail (code-defined truth) -- one deterministic every-frame replay
# --------------------------------------------------------------------------- #
def _trail_from_rec(rec: dict, engine: str, source: str, round_dp: int = 4) -> dict:
    """Map ONE ``run_batch`` record (frames_every=1) to the code-state trail dict::

        {"frames":     [{"tick": int, "entities": {name: body_dict}}, ...],  # tick 0..T
         "checkpoints": {name: latch_tick|None},   # episode-level latch map
         "result":      "success"|"failure"|...,   # terminal classification
         "world_size":  [W, H],  "ticks": T,  "error": ...}

    Shared by the single-episode :func:`_state_trail` and the batched
    ``harness.export.rollouts._state_trails_batch`` so both channels parse an executor
    record identically. Floats in the body dicts are rounded to ``round_dp`` decimals for
    compact, deterministic storage (the reward labels do NOT depend on this rounding --
    they are recomputed from the integer latch ticks)."""
    from harness.verify.executors import _round_floats
    if engine == "godot":
        from harness.verify.executors import normalize_godot_record
        _title, _prompt, frames_raw = normalize_godot_record(rec, source)
    else:
        frames_raw = rec.get("frames", [])
    frames = _round_floats(frames_raw, int(round_dp))
    return {
        "frames": frames,
        "checkpoints": dict(rec.get("checkpoints") or {}),
        "result": rec.get("result"),
        "world_size": list(rec.get("world_size") or (800, 600)),
        "ticks": int(rec.get("ticks", 0)),
        "error": rec.get("error"),
    }


def _state_trail(source: str, engine: str, actions: list, seed: int,
                 max_ticks: int, round_dp: int = 4) -> dict:
    """Replay ONE witness with ``frames_every=1`` and return its code-state trail (see
    :func:`_trail_from_rec` for the shape).

    Engine-agnostic: dispatches to the same executors the verify/replay lanes use
    (godot/gdscript) and calls their ``run_batch`` -- pure reuse, no
    reimplementation."""
    from harness.verify.executors import GodotExecutor

    gd_ex = None
    if engine == "godot":
        ex = GodotExecutor()
    elif engine == "gdscript":
        from harness.verify.gd_exec import GdExecutor
        ex = gd_ex = GdExecutor()
    else:
        raise ValueError(f"unsupported engine {engine!r} (only godot / gdscript)")
    try:
        recs = ex.run_batch(source, [{"seed": int(seed), "actions": list(actions)}],
                            int(max_ticks), frames_every=1)
    finally:
        if gd_ex is not None:       # the serve host is a persistent process
            gd_ex.close()
    return _trail_from_rec(recs[0], engine, source, round_dp)


def _entities_of(frame: dict) -> dict:
    """The per-body state dict of one trail frame (``entities``)."""
    return dict(frame.get("entities") or {})


def build_steps(state_frames: list, cp_latch: dict, n_cp: int, actions: list,
                result, horizon: int) -> tuple:
    """Assemble the per-tick step records from a code-state trail. PURE (no engine, no
    IO) so it is unit-testable offline: given the every-frame ``state_frames`` (tick 0..T,
    frame 0 = build), the checkpoint latch-tick map, the witness ``actions`` and the
    terminal ``result``, it emits one record per decision tick t=1..T::

        {t, action, state, checkpoints (latched bool map), reward{shaping,terminal,total},
         done, n_latched}

    The reward is recomputed through :func:`step_reward_parts` (i.e. env.step_reward), and
    the terminal result is attached ONLY to the final tick -- exactly as the env's ``step``
    labels a rollout. Returns ``(steps, episode_return, T)``."""
    T = len(state_frames) - 1                  # decision ticks (frame 0 is the build)
    steps = []
    episode_return = 0.0
    for t in range(1, T + 1):
        c_before = _latched_count(cp_latch, t - 1)
        c_after = _latched_count(cp_latch, t)
        result_t = result if t == T else None
        reward = step_reward_parts(c_before, c_after, n_cp, result_t, t, horizon)
        episode_return += reward["total"]
        action = actions[t - 1] if (t - 1) < len(actions) else None
        done = bool(t == T and result in ("success", "failure", "error"))
        steps.append({
            "t": t,
            "action": action,
            "state": _entities_of(state_frames[t]),
            "checkpoints": _latched_map(cp_latch, t),
            "reward": reward,
            "done": done,
            "n_latched": c_after,
        })
    return steps, float(episode_return), T


# --------------------------------------------------------------------------- #
# Pixel trail -- capture_gif with frames kept, sized so the PNG ordinal == tick
# --------------------------------------------------------------------------- #
def _capture_frames(game_path: str, out_gif: str, actions: list, seed: int,
                    frames_dir: str, *, follow: bool, width: int, height: int,
                    fps: int, max_frames: int, cam_dist=None, views=None) -> dict:
    """Render the witness to PNGs (kept in ``frames_dir``) + a companion GIF, via the
    certified capture lane. ``max_frames`` MUST be ``>= len(actions) + 2`` so the capture
    host keeps stride==1 and each PNG's ordinal equals its decision tick (the alignment the
    exporter relies on). ``views`` (optional) is capture_gif's multi-view spec list -- one
    deterministic replay per camera, view 0 primary. Returns capture_gif's result dict."""
    from harness.verify.capture import capture_gif

    return capture_gif(
        game_path, out_gif, actions=actions, seed=seed, follow=follow,
        width=width, height=height, fps=fps, max_frames=max_frames,
        frames_dir=frames_dir, cam_dist=cam_dist, views=views)


def _export_view_specs(views, ep_dir: Path, raw_primary: Path):
    """Normalise the exporter's ``views`` request: assign default ids (``view<k>``), reject
    duplicates/unsafe ids, and pin each view's RAW frames dir under the episode dir (view 0
    -> the legacy ``_frames_raw``, extras -> ``_frames_raw_<id>``). Camera keys (follow /
    cam_dist / elevation / azimuth / fov / view_target) pass through untouched --
    ``capture_gif`` resolves their inheritance from the top-level knobs. ``None`` -> None
    (the single-view legacy path, byte-identical package)."""
    if views is None:
        return None
    if not views:
        raise ValueError("views=[] is ambiguous -- pass None for the single-view default")
    specs = []
    for k, v in enumerate(views):
        v = dict(v or {})
        vid = str(v.get("id") or f"view{k}")
        if "/" in vid or "\\" in vid or vid.startswith(".") or not vid:
            raise ValueError(f"unsafe view id {vid!r} (it names a directory under frames/)")
        if any(s["id"] == vid for s in specs):
            raise ValueError(f"duplicate view id {vid!r}")
        v["id"] = vid
        v["frames_dir"] = str(raw_primary if k == 0 else ep_dir / f"_frames_raw_{vid}")
        specs.append(v)
    return specs


def _keep_tick_frames(raw_dir: Path, dest_dir: Path, n_state: int, slug: str,
                      label: str, capture_result) -> tuple:
    """Move ONE view's raw ``frame_%05d.png`` sequence (host ordinal == decision tick at
    stride 1) into ``dest_dir/t%05d.png`` for ticks 1..T, dropping the settle frame
    (ordinal 0) and any trailing surplus past the state trail's terminal. A DEFICIT
    (fewer PNGs than state frames -> some tick has no pixel) is a hard error. Deletes
    ``raw_dir``. Returns ``(n_kept, surplus)``."""
    pngs = sorted(Path(raw_dir).glob("frame_*.png"))
    if len(pngs) < n_state:
        shutil.rmtree(raw_dir, ignore_errors=True)
        raise ValueError(
            f"pixel/state tick-count DEFICIT for {slug} ({label}): {len(pngs)} PNG frames "
            f"< {n_state} state frames (subsampling? capture result={capture_result})")
    surplus = len(pngs) - n_state          # trailing frames past the reward trail's terminal
    for png in pngs:
        ordinal = int(png.stem.split("_")[-1])
        if ordinal <= 0 or ordinal >= n_state:
            continue                       # settle frame (0) + surplus tail (>= n_state)
        shutil.move(str(png), str(Path(dest_dir) / f"t{ordinal:05d}.png"))
    shutil.rmtree(raw_dir, ignore_errors=True)
    return len(sorted(Path(dest_dir).glob("t*.png"))), surplus


# --------------------------------------------------------------------------- #
# The exporter
# --------------------------------------------------------------------------- #
# Every episode declares HOW its trajectory was produced. This is the behavioral-diversity
# axis a reward model needs: wins are not the only truth -- failures/timeouts are free,
# perfectly-labelled NEGATIVES (see the dataset README "Behavioral diversity" section).
#   * "demo"      -- a trained-policy demo trajectory (witness_source rl): a clean WIN.
#   * "witness"   -- the tree solver's certified witness (witness_source tree): a clean WIN.
#   * "random"    -- a seeded random-policy rollout: whatever outcome it reaches (mostly a
#                    fast failure/timeout on a STAKES game -- that is the point).
#   * "perturbed" -- the winning witness with K seeded action corruptions: a NEAR-MISS, the
#                    most informative negative (it looks like progress, then is not).
TRAJECTORY_KINDS = ("demo", "witness", "random", "perturbed")


def kind_for_witness_source(witness_source: str) -> str:
    """Map a clean-win ``witness_source`` (rl|tree) to its ``trajectory_kind`` (demo|witness).
    The trained-policy demo is ``demo``; the tree solver's witness is ``witness``."""
    return "demo" if witness_source == "rl" else "witness"


def _write_package(game_path: str, out_dir: str, source: str, engine: str,
                   actions: list, seed: int, trail: dict, *,
                   trajectory_kind: str, witness_source: str,
                   witness_path: str | None, episode_key: str | None = None,
                   render_frames: bool = True, follow: bool | None = None,
                   width: int = 960, height: int = 540, fps: int = 20,
                   cam_dist: float | None = None, dimension_hint: str | None = None,
                   extra_meta: dict | None = None, views: list | None = None,
                   capture_fn=None) -> dict:
    """Write ONE episode package from an ALREADY-REPLAYED code-state ``trail`` (the shape
    :func:`_trail_from_rec` returns). Engine/witness-agnostic core shared by the winning-demo
    path (:func:`export_episode`) and the negative-mining paths
    (``harness.export.rollouts``): given the trail + its ``actions``/``seed`` + the honest
    ``trajectory_kind``/``witness_source``, it recomputes the reward per tick through
    ``env.step_reward``, optionally renders the PIXEL channel (``render_frames``), and writes
    ``<out>/<slug>/<episode_key>/{episode.json,steps.jsonl,frames/}``.

    ``episode_key`` is the per-episode sub-directory (defaults to ``str(seed)`` -- the legacy
    one-episode-per-seed layout); negatives pass a namespaced key (``random-<seed>``,
    ``perturbed-<seed>-<i>``) so many episodes of the same game/seed never collide. Raises
    ``ValueError`` on an empty/degenerate trail or a STATE/PIXEL tick-count mismatch (the
    alignment contract must hold or the package is not written).

    MULTI-VIEW (``views=[{...}, ...]``, opt-in): N camera angles of the SAME deterministic
    replay (one render pass per view; the capture lane's fingerprint identity machinery is
    what makes frame j of every view the same tick). View 0 is the PRIMARY and keeps
    today's layout byte-for-byte (``frames/t%05d.png`` + ``<slug>.gif``); each extra view
    lands in ``frames/<view_id>/t%05d.png`` (default ids ``view1``, ``view2``, ...) with
    the SAME settle-drop/surplus-drop/deficit rules and an exact per-view ``n_png == T``
    check. ``episode.json:capture.views`` records every view's resolved camera params +
    relative path; ``n_frames`` stays the primary count. ``views=None`` (default) -> the
    single-view package, byte-identical to before the upgrade.

    ``capture_fn`` injects the frame producer (signature of :func:`_capture_frames`);
    None -> the real capture lane. It exists so offline tests can exercise the package
    layout without Godot -- never monkeypatch the module."""
    slug = Path(game_path).resolve().parent.name
    state_frames = trail["frames"]
    if not state_frames:
        raise ValueError(f"state replay produced no frames for {slug} "
                         f"(kind={trajectory_kind}, seed={seed})")
    T_pre = len(state_frames) - 1             # decision ticks (frame 0 is the build)
    if T_pre < 1:
        raise ValueError(f"trajectory too short for {slug} (kind={trajectory_kind}, "
                         f"seed={seed}): {T_pre} ticks -- nothing to export")

    cp_latch = trail["checkpoints"]           # {name: latch_tick|None}, declared order
    n_cp = len(cp_latch)
    result = trail["result"]

    # Dimension: the authoritative runtime detector (first body's pos arity), the SAME
    # signal env's obs machinery pins. dimension_hint only overrides for a headless test.
    dim_int = rl_env.detect_dim(_entities_of(state_frames[0]))
    dimension = dimension_hint or ("3D" if dim_int == 3 else "2D")

    key = episode_key or str(seed)
    ep_dir = Path(out_dir) / slug / key
    frames_dir = ep_dir / "frames"
    if ep_dir.exists():
        shutil.rmtree(ep_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    # -- PIXEL trail (capture; sized so PNG ordinal == tick) -------------------------
    n_png = 0
    frame_surplus = 0
    capture_meta = {}
    capture_views_meta: list = []
    n_actions = len(actions)
    follow_flag = bool(follow) if follow is not None else (dimension == "3D")
    if render_frames:
        capfn = capture_fn or _capture_frames
        raw_frames_dir = ep_dir / "_frames_raw"
        out_gif = str(ep_dir / f"{slug}.gif")
        view_specs = _export_view_specs(views, ep_dir, raw_frames_dir)
        # max_frames >= n_actions + 2 keeps the capture host at stride 1 (see _capture_frames).
        capture_meta = capfn(
            game_path, out_gif, actions, seed, str(raw_frames_dir),
            follow=follow_flag, width=width, height=height, fps=fps,
            max_frames=n_actions + 8, cam_dist=cam_dist, views=view_specs)
        # The code-STATE trail (with the reward labels) is authoritative for T: it is tick 0..T,
        # so n_state = T + 1 and the pixel channel must supply a frame for every tick 1..T. The
        # capture host emits t=0 (settle) + one PNG per tick, ordinal == tick. For a clean WIN both
        # channels stop at the same terminal tick (exact match). For a NON-terminating NEGATIVE the
        # capture host can render a SMALL trailing SURPLUS -- it and the serve host detect the
        # terminal one tick apart. The replay is byte-faithful up to that divergence (the winning
        # witness matches exactly), so frames 1..T align with the reward trail regardless; we KEEP
        # 1..T and DROP the settle frame + any surplus tail. A DEFICIT (fewer PNGs than the reward
        # trail -> a tick 1..T has no pixel, e.g. real subsampling) is a hard error. Every EXTRA
        # view is a replay of the SAME witness, so the identical rules apply per view.
        n_state = len(state_frames)            # tick 0..T
        n_png, frame_surplus = _keep_tick_frames(
            raw_frames_dir, frames_dir, n_state, slug, "primary",
            capture_meta.get("result"))
        if view_specs is not None:
            resolved = capture_meta.get("views") or []
            if len(resolved) != len(view_specs):
                raise ValueError(
                    f"multi-view capture for {slug} returned {len(resolved)} view records "
                    f"for {len(view_specs)} requested views")
            for k, (spec, rec) in enumerate(zip(view_specs, resolved)):
                vid = spec["id"]
                if k == 0:
                    rel, vn, vsurplus = "frames", n_png, frame_surplus
                else:
                    vdir = frames_dir / vid
                    vdir.mkdir(parents=True, exist_ok=True)
                    vn, vsurplus = _keep_tick_frames(
                        Path(spec["frames_dir"]), vdir, n_state, slug, vid,
                        capture_meta.get("result"))
                    rel = f"frames/{vid}"
                    if vn != n_png:
                        raise ValueError(
                            f"multi-view frame-count mismatch for {slug}: view {vid!r} "
                            f"kept {vn} frames vs primary {n_png}")
                entry = {key: rec.get(key) for key in (
                    "id", "follow", "cam_dist", "elevation", "azimuth", "fov",
                    "view_target")}
                entry.update({"path": rel, "n_frames": vn,
                              "trailing_frames_dropped": vsurplus})
                capture_views_meta.append(entry)

    # -- steps.jsonl (one line per decision tick t=1..T) -----------------------------
    horizon = rl_env.HORIZON
    checkpoint_names = list(cp_latch.keys())
    steps, episode_return, T = build_steps(
        state_frames, cp_latch, n_cp, actions, result, horizon)

    if render_frames and n_png != T:
        raise ValueError(
            f"frame/step count mismatch for {slug}: {n_png} frames vs {T} steps")

    # -- episode.json ----------------------------------------------------------------
    objective_text = deslug(slug)
    if checkpoint_names:
        objective_text += "\nCheckpoints (in order): " + ", ".join(checkpoint_names)

    episode = {
        "schema_version": SCHEMA_VERSION,
        "game_id": slug,
        "slug": slug,
        "game_file": game_path,
        "engine": engine,
        "dimension": dimension,
        "trajectory_kind": trajectory_kind,
        "objective_text": objective_text,
        "prompt": deslug(slug),
        "checkpoint_names": checkpoint_names,
        "checkpoints_latch": cp_latch,
        "seed": seed,
        "episode_key": key,
        "witness_source": witness_source,
        "witness_path": witness_path,
        "ticks": T,
        "n_steps": len(steps),
        "n_frames": n_png if render_frames else 0,
        "outcome": _outcome(result),
        "horizon": horizon,
        "world_size": trail["world_size"],
        "episode_return": round(episode_return, 6),
        "reward_scheme": {
            "mode": rl_env.REWARD_MODE,
            "n_checkpoints": n_cp,
            "shaping_mass": rl_env.SHAPING_MASS,
            "r_success": rl_env.R_SUCCESS,
            "success_time_floor": rl_env.SUCCESS_TIME_FLOOR,
            "r_failure": rl_env.R_FAILURE,
            "living_cost_total": rl_env.LIVING_COST_TOTAL,
        },
        "harness_version": harness_version(),
        "build_state": _entities_of(state_frames[0]),
        "capture": {
            "rendered": bool(render_frames),
            "result": capture_meta.get("result"),
            "width": width, "height": height, "follow": follow_flag,
            "trailing_frames_dropped": frame_surplus,
        },
        "paths": {"steps": "steps.jsonl", "frames": "frames"},
    }
    if capture_views_meta:
        # Multi-view provenance: resolved camera params + relative path per view (view 0 =
        # the primary "frames" dir). Absent for a single-view package (byte-identical).
        episode["capture"]["views"] = capture_views_meta
    if extra_meta:
        episode.update(extra_meta)

    (ep_dir / "episode.json").write_text(
        json.dumps(episode, ensure_ascii=False, indent=2), encoding="utf-8")
    with (ep_dir / "steps.jsonl").open("w", encoding="utf-8") as fh:
        for s in steps:
            fh.write(json.dumps(s, ensure_ascii=False, separators=(",", ":")) + "\n")

    # -- manifest-ready record -------------------------------------------------------
    record = {
        "slug": slug,
        "dim": dimension,
        "trajectory_kind": trajectory_kind,
        "outcome": episode["outcome"],
        "ticks": T,
        "n_steps": len(steps),
        "n_frames": episode["n_frames"],
        "witness_source": witness_source,
        "seed": seed,
        "episode_key": key,
        "episode_return": episode["episode_return"],
        "paths": {
            "dir": str(ep_dir),
            "episode": str(ep_dir / "episode.json"),
            "steps": str(ep_dir / "steps.jsonl"),
            "frames": str(frames_dir),
        },
    }
    if capture_views_meta:
        record["views"] = capture_views_meta
    return record


def export_episode(game_path: str, out_dir: str, *, actions_arg: str | None = None,
                   witness_mode: str = "auto", seed_default: int = 0,
                   follow: bool | None = None, width: int = 960, height: int = 540,
                   fps: int = 20, cam_dist: float | None = None,
                   dimension_hint: str | None = None,
                   render_frames: bool = True, views: list | None = None) -> dict:
    """Export ONE (game, WINNING-witness) pair to ``<out_dir>/<slug>/<seed>/``.

    Resolves the witness (explicit ``--actions`` > trained-policy ``demo_trajectory.json``
    > a fresh verify's tree witness -- honest ``witness_source``, mapped to a ``demo``/
    ``witness`` ``trajectory_kind``), replays it ONCE for the code-STATE trail, and hands off
    to :func:`_write_package` (which renders the PIXEL trail unless ``render_frames`` is False,
    recomputes the reward per tick through ``env.step_reward``, and writes the package).

    ``views`` (opt-in) exports N camera angles of the same replay -- see
    :func:`_write_package`; None keeps today's single-view package byte-identical.

    Returns a manifest-ready record; raises ``ValueError`` on an empty witness or a
    STATE/PIXEL tick-count mismatch (the alignment contract must hold or the package is
    not written)."""
    game_path = os.path.abspath(game_path)
    source = Path(game_path).read_text(encoding="utf-8")

    from harness.verify.gameverify import detect_engine
    engine = detect_engine(game_path, source)

    # -- witness resolution (reuse the capture lane's canonical resolver) -----------
    from harness.cli import _resolve_capture_witness
    wit = _resolve_capture_witness(
        game_path, actions_arg=actions_arg, seed_default=seed_default,
        auto_demo=(witness_mode != "tree"))
    actions, seed = wit["actions"], int(wit["seed"])
    if not actions:
        raise ValueError(
            f"no witness found for {game_path} (game does not certify?) -- nothing to export")

    # -- code-STATE trail (single deterministic every-frame replay) ------------------
    trail = _state_trail(source, engine, actions, seed, max_ticks=len(actions) + 4)

    return _write_package(
        game_path, out_dir, source, engine, actions, seed, trail,
        trajectory_kind=kind_for_witness_source(wit["witness_source"]),
        witness_source=wit["witness_source"], witness_path=wit["witness_path"],
        episode_key=str(seed), render_frames=render_frames, follow=follow,
        width=width, height=height, fps=fps, cam_dist=cam_dist,
        dimension_hint=dimension_hint, views=views)


DATASET_README = r"""# GI Episode Dataset -- code-defined truth bridged to pixels

Produced by **THE EXPORTER** (`harness/export`, `python -m harness game export`). This is
the training substrate for the General Intuition challenge's bullet 3: *generate many
environments in code space, train a reward model on the programmatic signals, then apply
that reward model to pixel-based observations -- bridging code-defined truth and visual
understanding.*

Every episode is ONE deterministic replay of a certified game's winning witness, recorded
in TWO aligned channels: the **code-state / reward truth** the harness certifies against, and
the **pixel frame** rendered by the in-engine capture host at the very same tick.

## Layout

```
<root>/
  manifest.jsonl              one line per episode (slug, trajectory_kind, outcome, ticks, paths, ...)
  README.md                   this file
  <slug>/<episode_key>/       <episode_key> = <seed> (win) | random-<seed> | perturbed-<seed>-<i>
    episode.json              episode meta (objective_text, dimension, trajectory_kind, reward scheme, build)
    steps.jsonl               one line per decision tick t (action, state, reward, done)
    frames/t%05d.png          the rendered pixel frame at tick t (t = 1..ticks) -- ABSENT for a state-only package
    frames/<view_id>/t%05d.png  EXTRA camera views (opt-in multi-view export). View 0 IS frames/
                              itself; each extra view is the SAME deterministic replay rendered
                              from another camera, one PNG per tick, params recorded in
                              episode.json:capture.views (id, elevation, azimuth, fov, view_target,
                              follow, path). Absent unless the export requested views.
```

`len(steps) == episode.json:ticks` always; `len(frames) == len(steps)` *wherever frames exist*
(a state-only negative has `n_frames == 0` and no `frames/*.png`). The pixel `frames/t00001.png`
and the `steps.jsonl` line with `t == 1` describe the SAME tick of the SAME replay (the exporter
asserts the two channels' tick counts match before writing the package).

## `episode.json`

| field | meaning |
|-------|---------|
| `slug` / `game_id` | game directory name (the sanitized generation prompt) |
| `game_file` | absolute path to the `.gd` game source |
| `engine` | `gdscript` / `godot` / `js` / `py` |
| `dimension` | `2D` or `3D` (from the runtime pos arity -- `env.detect_dim`) |
| `trajectory_kind` | `demo` / `witness` / `random` / `perturbed` -- HOW the trajectory was produced (the behavioral-diversity axis; see below) |
| `episode_key` | the episode sub-directory under `<slug>/` (`<seed>` for a win, `random-<seed>` / `perturbed-<seed>-<i>` for a negative) |
| `objective_text` | the generation prompt + the ordered checkpoint names (the reward-model text conditioning) |
| `checkpoint_names` / `checkpoints_latch` | declared checkpoints and the tick each latched (`null` = never) |
| `seed` | world seed of the replay |
| `witness_source` | `rl` (trained-policy demo) or `tree` (solver witness) -- honest provenance |
| `ticks` / `n_steps` / `n_frames` | decision ticks (all three equal) |
| `outcome` | `success` / `failure` / `timeout` |
| `horizon` | reward horizon (env.HORIZON) used for the decayed terminal payoff |
| `reward_scheme` | the exact env constants (mode, shaping mass, R_success, floor, ...) |
| `episode_return` | sum of per-tick `reward.total` |
| `world_size` | `[W, H]` |
| `harness_version` | git sha of the harness that produced the package |
| `build_state` | the tick-0 body dict (the game's build, before any action) |

## `steps.jsonl` (one JSON object per decision tick)

| field | meaning |
|-------|---------|
| `t` | 1-based decision tick |
| `action` | the wire action (a string verb, or an array chord) |
| `state` | the wire body dict `{name: {pos, vel, angle, controlled, static}}` after tick t |
| `checkpoints` | per-tick LATCHED map `{name: bool}` (sticky) |
| `reward` | `{shaping, terminal, total}` |
| `done` | terminal tick flag |
| `n_latched` | number of checkpoints latched by tick t |

**Reward labels are training truth.** `reward.total` is recomputed per tick by importing
`harness.rl.env.step_reward` -- the SINGLE function the three RL env `step()` paths call --
so a label here is byte-identical to the RL training signal. `total = shaping + terminal`:
`shaping` is the bounded checkpoint-shaping (+ the default-off living cost), `terminal` is the
time-decayed success payoff / failure penalty, attached ONLY to the terminal tick (`step_reward`
keeps the terminal OUTSIDE the shaping). The exporter never reimplements the reward math.

## Behavioral diversity -- NEGATIVES, not only wins

A reward model trained ONLY on winning trajectories learns that *everything is progress*.
The failure and timeout episodes are free, perfectly-labelled NEGATIVES -- the reward labels
come from the SAME `env.step_reward` import, so a losing frame's label is training truth too.
Every episode declares its `trajectory_kind`:

| kind | how it was produced | typical outcome |
|------|---------------------|-----------------|
| `demo` | a trained-policy demo trajectory (`witness_source=rl`) | **success** (a clean win) |
| `witness` | the tree solver's certified witness (`witness_source=tree`) | **success** (a clean win) |
| `random` | a seeded random-policy rollout (`--random-rollouts N --horizon H`), rolled through the same batch executor | mostly **failure/timeout** (rare lucky win) |
| `perturbed` | the winning witness with K seeded action corruptions -- swap/drop/replace at random ticks (`--perturb <witness> K`) | **near-miss** failure/timeout (the most informative negative) |

The reward geometry makes the negatives *mechanically* separable from the wins: a win carries
the time-decayed terminal payoff (`>= R_SUCCESS * SUCCESS_TIME_FLOOR = 5.0`), while the whole
shaping mass is bounded by `SHAPING_MASS = 1.0` and a failure adds `R_FAILURE = -2.0`. So every
`failure`/`timeout` episode's return is strictly below every `success` return -- by construction,
not by luck.

**Random rollouts on STAKES games mostly end in fast failure -- that is the point.** A random
policy on a marble maze tips the ball off in a handful of ticks; those fast-failure frames ARE
the negative class the reward model must learn to score low. Do not filter them out.

### Recommended mix for the reward model

Per game, aim for roughly: **all wins (`demo`/`witness`) + >= 2x `random` + >= 1x `perturbed`**.
The wins anchor the top of the reward scale; the random rollouts populate the low-reward /
early-failure regime; the perturbed near-misses are the hard, high-value examples that sit right
next to a winning trajectory and then diverge (they teach the boundary, not just the extremes).

### Frames cost tradeoff (the pixel channel is the expensive part)

Rendering the pixel channel dominates export cost, so the knobs are explicit:

| mode | frames by default | flag |
|------|-------------------|------|
| `demo` / `witness` | **rendered** | `--no-frames` for state-only |
| `random` | **state-only** (no frames) | `--random-frames` to render (bulk, expensive) |
| `perturbed` | **rendered** | `--no-perturb-frames` for state-only |

A **state-only** package (no `frames/`) still carries the full per-tick `state` + `reward` labels,
so it is directly useful for reward-label *pretraining* and dynamics-from-state `(state_t,
action_t) -> state_t+1`. Render pixels for the FEW, high-value perturbed near-misses; keep the
MANY bulk random negatives state-only. `--no-frames` is a global kill-switch (state-only for
everything). `len(frames) == len(steps)` holds *wherever frames exist*; a state-only package has
`n_frames == 0` and the loader's `Episode.validate(require_frames=False)` skips the pixel check.

## Loader (torch-free, numpy-free)

```python
from harness.export.loader import EpisodeDataset
ds = EpisodeDataset("<root>")
train, test = ds.split_by_game(frac=0.8, seed=0)   # split BY GAME slug (never by episode)
for ep in ds.episodes(slugs=test):                 # a held-out game the model never saw
    ep.validate(require_frames=(ep.meta["n_frames"] > 0))   # state-only packages skip pixels
    for s in ep.steps:
        y = s["reward"]["total"]                   # the training label (win OR loss)
        if ep.meta["n_frames"]:
            frame = ep.frame_path(s["t"])          # -> <root>/<slug>/<key>/frames/t00001.png

# Filter by behavioral kind (wins vs negatives) -- split stays BY GAME:
wins = list(ds.filter_by_kind(("demo", "witness")))
negatives = list(ds.filter_by_kind(("random", "perturbed"), slugs=train))
```

## The intended experiment (the reward-model bridge)

Train a reward model `R(frame_t, objective_text) -> reward_t` (or cumulative progress) on
episodes from a **TRAIN split of games**, and evaluate calibration on **HELD-OUT GAMES**
(split by game slug via `split_by_game`, NEVER by episode -- cross-game generalization IS the
"general intuition" claim). A held-out game is one the model has never seen a single frame of.

Metrics:
  * **reward correlation** per held-out game -- Pearson/Spearman of `R(frame_t, objective)` vs
    the stored `reward.total` (and vs cumulative return);
  * **checkpoint-latch detection AUC** from pixels alone -- can `R`'s features tell, from the
    frame, that a checkpoint just latched? (labels: the per-tick `checkpoints` map deltas).

The programmatic signal is the ground truth; the reward model learns to read it off pixels. The
model's encoder is the transferable "intuition" that survives the code->pixel gap.

**Roadmap extension.** The same package supports a dynamics / world-model consumer
(`(frame_t, action_t) -> frame_t+1`, with `action` and `state` already in `steps.jsonl`) as an
auxiliary objective; sharing the encoder with the reward model is the intended transfer path.

All labels are mechanical (from the engine's programmatic state + `step_reward`) -- there is NO
LLM anywhere in the data path.
"""


def ensure_readme(out_dir: str) -> None:
    """Write the dataset ``README.md`` (schema + experiment design) at the export root.
    Static and self-describing; rewritten on each export so the doc tracks the schema."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "README.md").write_text(DATASET_README, encoding="utf-8")


def append_manifest(out_dir: str, record: dict) -> None:
    """Append ONE episode record as a line to ``<out_dir>/manifest.jsonl`` (relative paths).
    Idempotent per (slug, episode_key): a re-export of the SAME episode replaces its prior
    line. The key is the episode sub-directory -- ``str(seed)`` for a legacy one-per-seed
    package, or a namespaced ``random-<seed>``/``perturbed-<seed>-<i>`` for a negative -- so
    many episodes of the same game (and even the same world seed) coexist without clobbering."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = out / "manifest.jsonl"
    slug, seed = record["slug"], record["seed"]
    key = str(record.get("episode_key") or seed)
    rel = {
        "slug": slug,
        "seed": seed,
        "episode_key": key,
        "trajectory_kind": record.get("trajectory_kind", "demo"),
        "dim": record["dim"],
        "outcome": record["outcome"],
        "ticks": record["ticks"],
        "n_frames": record["n_frames"],
        "witness_source": record["witness_source"],
        "episode_return": record["episode_return"],
        "paths": {
            "episode": f"{slug}/{key}/episode.json",
            "steps": f"{slug}/{key}/steps.jsonl",
            "frames": f"{slug}/{key}/frames",
        },
    }
    if record.get("views"):
        # Multi-view episodes carry their per-view camera params + relative paths in the
        # manifest line too (absent for single-view lines -- byte-identical legacy rows).
        rel["views"] = record["views"]
    lines = []
    if manifest.exists():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            row_key = str(row.get("episode_key") or row.get("seed"))
            if row.get("slug") == slug and row_key == key:
                continue                       # drop the stale line for this episode
            lines.append(line)
    lines.append(json.dumps(rel, ensure_ascii=False, separators=(",", ":")))
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
