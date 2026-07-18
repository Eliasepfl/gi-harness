"""capture.py -- the CAPTURE lane's Python driver: real in-engine GIFs of a certified
GDScript game, rendered headlessly on the cluster.

This is a SECOND pass, wholly separate from certification. It REPLAYS an already-certified
witness (seed + actions) through ``godotworld/capture_host.gd`` -- a render host that steps
physics with the EXACT discipline of the frozen serve host (act + K=6 frames + latch +
terminal, same pins/speedup) and writes one PNG per decision tick. Visuals come from the
ZERO-CONTACT overlay dresser (``visual_dress.gd``); the game tree/physics are never touched,
so what you watch is provably the certified witness. PIL assembles the PNG sequence into a
GIF -- ffmpeg is not required.

Rendering path (see notes/engines/DEMO_CAPTURE_LANE.md for the empirical write-up):
  Godot 4.7 with ``--display-driver x11 --rendering-driver opengl3`` on Mesa **llvmpipe**
  (software GL, ``LIBGL_ALWAYS_SOFTWARE=1``) against an X display. ``--headless`` is the
  pixel-blind dummy rasterizer and is NEVER used here. The X display can be:
    * an already-exported ``DISPLAY`` (an Xvfb the caller started -- the ORCD path,
      ``scripts/capture_demo.sh`` handles the Xvfb + host-lib binds), or
    * an Xvfb this module auto-starts, when ``HARNESS_CAPTURE_XVFB=1`` and ``Xvfb`` is on
      PATH (e.g. inside a gi-capture.sif that bundles xvfb).

SECURITY: like the serve host, the capture host compiles + runs UNTRUSTED game code, so it
is spawned with the SAME scrubbed environment (``godot_exec.scrubbed_env``) plus only the
render vars it needs (DISPLAY / LD_LIBRARY_PATH / LIBGL_ALWAYS_SOFTWARE). Only certified
games (their winning witness) are ever captured.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from harness.verify.chord import wire_actions

# Render env knobs (overridable; defaults match the empirically-verified in-image path).
DISPLAY_DRIVER = os.environ.get("HARNESS_CAPTURE_DISPLAY_DRIVER", "x11")
RENDER_DRIVER = os.environ.get("HARNESS_CAPTURE_RENDER_DRIVER", "opengl3")
DEFAULT_WIDTH = 960
DEFAULT_HEIGHT = 540
DEFAULT_FPS = 20            # GIF playback fps (one captured frame == one decision tick)
GIF_MAX_FRAMES = 120        # demo GIF frame cap: long episodes are strided to this (COSMETIC
                            # only — the dataset keeps every per-tick PNG in frames_dir) [eng.]
GIF_COLORS = 96             # demo GIF palette size (shared adaptive palette) — the dominant
                            # size lever for a palette-based format [eng.]
DEFAULT_MAX_FRAMES = 300    # auto-subsample longer witnesses to keep the GIF light
HOLD_FRAMES = 12            # duplicate the final frame so the end reads


class CaptureError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Optional Xvfb management (for a container that bundles Xvfb; the ORCD wrapper
# instead exports DISPLAY itself, in which case this is a no-op).
# --------------------------------------------------------------------------- #
class _Xvfb:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.proc = None
        self.display = None

    def __enter__(self):
        want = os.environ.get("HARNESS_CAPTURE_XVFB", "").strip() in ("1", "true", "yes")
        if os.environ.get("DISPLAY") and not want:
            return self  # caller already provides a display
        if not shutil.which("Xvfb"):
            if not os.environ.get("DISPLAY"):
                raise CaptureError(
                    "no DISPLAY and no Xvfb on PATH -- start an X server first "
                    "(on ORCD use scripts/capture_demo.sh, which manages Xvfb + host libs)")
            return self
        disp = self._free_display()
        # A screen at least as big as the window so the framebuffer is never clipped.
        sw = max(self.width, 1280)
        sh = max(self.height, 960)
        self.proc = subprocess.Popen(
            ["Xvfb", disp, "-screen", "0", f"{sw}x{sh}x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.display = disp
        time.sleep(1.5)
        os.environ["DISPLAY"] = disp
        return self

    def __exit__(self, *exc):
        if self.proc is not None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass

    @staticmethod
    def _free_display() -> str:
        for n in range(80, 200):
            if not os.path.exists(f"/tmp/.X11-unix/X{n}"):
                return f":{n}"
        return ":99"


# --------------------------------------------------------------------------- #
# Capture entry point
# --------------------------------------------------------------------------- #
def _child_env() -> dict:
    from harness.verify.godot_exec import scrubbed_env
    env = scrubbed_env()  # keeps PATH/DISPLAY/LD_LIBRARY_PATH/locale, drops secrets
    # The render vars the software-GL path needs (scrubbed_env keeps DISPLAY +
    # LD_LIBRARY_PATH already; add the software-GL hint + pass DISPLAY through).
    if os.environ.get("DISPLAY"):
        env["DISPLAY"] = os.environ["DISPLAY"]
    if os.environ.get("LD_LIBRARY_PATH"):
        env["LD_LIBRARY_PATH"] = os.environ["LD_LIBRARY_PATH"]
    env["LIBGL_ALWAYS_SOFTWARE"] = os.environ.get("LIBGL_ALWAYS_SOFTWARE", "1")
    # Godot's user:// cache dir (needs a writable HOME).
    if os.environ.get("HOME"):
        env["HOME"] = os.environ["HOME"]
    return env


# --------------------------------------------------------------------------- #
# Bank-asset routing (cosmetic, 3D games): map bodies -> asset ids once per game.
# The mapping only ever dresses the render overlay; physics/oracles are untouched.
# --------------------------------------------------------------------------- #
def _default_manifest() -> str:
    """<repo>/assets/manifest.json (this file is harness/verify/, so up two to root)."""
    p = Path(__file__).resolve().parents[2] / "assets" / "manifest.json"
    return str(p) if p.is_file() else ""


def _use_llm() -> bool:
    """LLM routing unless HARNESS_OFFLINE, or no OpenRouter key is resolvable (offline)."""
    if os.environ.get("HARNESS_OFFLINE", "").strip().lower() in ("1", "true", "yes"):
        return False
    try:
        from harness.gen.gamegen import _resolve_secret
        return _resolve_secret("OPENROUTER_API_KEY") is not None
    except Exception:  # noqa: BLE001
        return False


def _game_context(game_path: str) -> str:
    """A light semantic anchor for routing: the game's slug as spaced words."""
    return Path(game_path).stem.replace("_", " ").strip()


def _dump_bodies(exe: str, project: str, game_path: str, timeout_s: float = 90.0) -> list:
    """Routing pre-pass: build the game HEADLESS (no dressing/render -> safe + cheap) and
    return its t=0 state() body list ([{name, controlled}, ...]). [] on any failure."""
    work = tempfile.mkdtemp(prefix="gidump_")
    dump = os.path.join(work, "bodies.json")
    argv = [exe, "--headless", "--path", project, "-s", "res://capture_host.gd", "--",
            "--game-file=%s" % os.path.abspath(game_path),
            "--dump-state=%s" % dump]
    try:
        log = tempfile.TemporaryFile(mode="w+b")
        subprocess.run(argv, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                       env=_child_env(), timeout=timeout_s)
        if os.path.isfile(dump):
            data = json.loads(Path(dump).read_text(encoding="utf-8"))
            bodies = data.get("bodies", [])
            if isinstance(bodies, list):
                return bodies
    except Exception:  # noqa: BLE001 - routing is best-effort; primitives are the fallback
        pass
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return []


def _route_assets_for_game(exe: str, project: str, game_path: str,
                           game_context: str | None, manifest_path: str) -> str:
    """Route this game's bodies to bank assets and cache the mapping beside the game.
    Returns the cache path (fed to the capture host via ``--assets-file``), or "" on any
    failure -- in which case the dresser simply uses primitive proxies."""
    if not manifest_path or not os.path.isfile(manifest_path):
        return ""
    try:
        from harness.demo.asset_bank import route_assets, load_manifest
    except Exception:  # noqa: BLE001
        return ""
    bodies = _dump_bodies(exe, project, game_path)
    names = [b.get("name") for b in bodies if isinstance(b, dict) and b.get("name")]
    if not names:
        return ""
    cache = str(Path(game_path).with_suffix(".assets.json"))
    ctx = game_context or _game_context(game_path)
    try:
        manifest = load_manifest(manifest_path)
        route_assets(ctx, bodies, manifest, use_llm=_use_llm(), cache_path=cache)
    except Exception:  # noqa: BLE001
        return ""
    return cache


# --------------------------------------------------------------------------- #
# Trajectory pre-scan (3D framing): replay the witness ONCE headless (no display/GL, no
# dressing) to fingerprint every tick's body positions, then hand the dresser the whole
# trajectory's bounding box + the controlled body's travel direction. This is what makes
# fit-to-scene framing work for a FLY-THROUGH game (the craft leaves the t=0 static frame)
# and orients the chase cam to trail the craft along its path. Camera-only, physics-inert:
# it feeds the render overlay via env, never the capture host's stepping.
# --------------------------------------------------------------------------- #
def _traj_from_fingerprint(text: str):
    """Parse capture_host.gd's --fingerprint ('tick|name:pos:vel:angle;...' at %.17f) into
    a 3D framing hint {min, max, fwd}. Returns None for a 2D game (pos has <3 comps) or on
    any parse miss -- the dresser then falls back to its t=0 box (2D framing untouched)."""
    positions: dict = {}
    for line in text.splitlines():
        if "|" not in line:
            continue
        _ts, body_str = line.split("|", 1)
        if not body_str:
            continue
        for part in body_str.split(";"):
            if not part:
                continue
            f = part.split(":")
            if len(f) < 2:
                continue
            coords = [c for c in f[1].split(",") if c != ""]
            if len(coords) < 3:          # 2D (or malformed) -> not a 3D trajectory
                return None
            try:
                p = (float(coords[0]), float(coords[1]), float(coords[2]))
            except ValueError:
                continue
            positions.setdefault(f[0], []).append(p)
    if not positions:
        return None
    xs = [p[0] for pts in positions.values() for p in pts]
    ys = [p[1] for pts in positions.values() for p in pts]
    zs = [p[2] for pts in positions.values() for p in pts]
    tmin = (min(xs), min(ys), min(zs))
    tmax = (max(xs), max(ys), max(zs))
    # Travel direction = the largest first->last horizontal displacement (the mover is the
    # controlled craft; static bodies don't move). None if nothing meaningfully travels.
    fwd = None
    best = 1.0e-3
    for pts in positions.values():
        if len(pts) < 2:
            continue
        a, b = pts[0], pts[-1]
        d = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        mag = (d[0] * d[0] + d[2] * d[2]) ** 0.5
        if mag > best:
            best, fwd = mag, d
    return {"min": tmin, "max": tmax, "fwd": fwd}


def _scan_trajectory(exe: str, project: str, game_path: str, actions, seed: int,
                     timeout_s: float = 180.0):
    """Headless fingerprint pre-scan (no display/GL, --no-dress) -> {min,max,fwd} or None.
    Best-effort: any failure returns None and the render pass frames on the t=0 box."""
    work = tempfile.mkdtemp(prefix="giscan_")
    try:
        witness = os.path.join(work, "w.json")
        Path(witness).write_text(
            json.dumps({"seed": int(seed), "actions": wire_actions(actions)}),
            encoding="utf-8")
        fp = os.path.join(work, "fp.txt")
        argv = [exe, "--headless", "--path", project, "-s", "res://capture_host.gd", "--",
                "--capture", "--game-file=%s" % os.path.abspath(game_path),
                "--actions-file=%s" % witness, "--out=%s" % os.path.join(work, "frames"),
                "--fingerprint=%s" % fp, "--no-frames", "--no-dress", "--speedup=1"]
        subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       stdin=subprocess.DEVNULL, env=_child_env(), timeout=timeout_s)
        if not os.path.isfile(fp):
            return None
        return _traj_from_fingerprint(Path(fp).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - framing is best-effort; the t=0 box is the fallback
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _cam_env(cam_dist, scan) -> dict:
    """The render overlay's camera-framing env (read by visual_dress.gd). Empty keys are
    simply absent -> the dresser uses its built-in defaults / t=0 box."""
    env: dict = {}
    if cam_dist is not None:
        env["HARNESS_CAM_DIST"] = repr(float(cam_dist))
    if scan is not None:
        env["HARNESS_CAM_TRAJ_MIN"] = "%r,%r,%r" % scan["min"]
        env["HARNESS_CAM_TRAJ_MAX"] = "%r,%r,%r" % scan["max"]
        if scan.get("fwd") is not None:
            env["HARNESS_CAM_FWD"] = "%r,%r,%r" % scan["fwd"]
    return env


def _capture_argv(exe: str, project: str, user_args: list[str], width: int,
                  height: int) -> list[str]:
    """A physics-STEPPING but NON-headless invocation of the capture host. Pins
    ``--fixed-fps 60`` (determinism) and selects the software-GL display path. Never
    passes ``--headless`` (that is the pixel-blind dummy rasterizer)."""
    argv = [exe,
            "--resolution", f"{width}x{height}",
            "--display-driver", DISPLAY_DRIVER,
            "--rendering-driver", RENDER_DRIVER,
            "--fixed-fps", "60",
            "--path", project,
            "-s", "res://capture_host.gd",
            "--", *user_args]
    assert "--headless" not in argv, "capture must NOT be headless (dummy rasterizer)"
    assert argv[argv.index("--fixed-fps") + 1] == "60", "capture must pin --fixed-fps 60"
    return argv


def _cam_user_args(cam_elevation=None, cam_azimuth=None, cam_fov=None,
                   cam_view_target=None) -> list[str]:
    """Parametric-camera argv for the capture host (render-only; consumed by
    visual_dress.gd). Semantics ported from godot-ai's ``editor_screenshot`` tool
    (godot-ai plugin editor_handler.gd): elevation 0=level/90=overhead, azimuth
    0=front/90=right, fov in degrees (20-30=zoom, 60-75=context), view_target =
    comma-separated state() body NAMES to frame on. PURE, and append-NOTHING when
    every knob is unset (the same pattern as speedup_user_args) -- the default
    invocation stays byte-identical to the pre-parametric lane."""
    args: list[str] = []
    if cam_elevation is not None:
        args.append("--cam-elevation=%r" % float(cam_elevation))
    if cam_azimuth is not None:
        args.append("--cam-azimuth=%r" % float(cam_azimuth))
    if cam_fov is not None:
        args.append("--cam-fov=%r" % float(cam_fov))
    if cam_view_target:
        args.append("--cam-target=%s" % str(cam_view_target))
    return args


def _view_user_args(game_path: str, actions_file: str, frames_out: str, *, width: int,
                    height: int, max_frames: int, speedup: int, follow: bool,
                    assets_file: str, manifest: str, cam_elevation=None,
                    cam_azimuth=None, cam_fov=None, cam_view_target=None) -> list[str]:
    """The capture host's user-arg list for ONE rendered view. PURE (no IO beyond
    path normalisation) so the default-byte-identity contract is unit-testable: with
    every ``cam_*`` at None the list is exactly the legacy single-view one."""
    from harness.verify.godot_exec import speedup_user_args

    user_args = [
        "--capture",
        "--game-file=%s" % os.path.abspath(game_path),
        "--actions-file=%s" % actions_file,
        "--out=%s" % frames_out,
        "--width=%d" % int(width),
        "--height=%d" % int(height),
        "--max-frames=%d" % int(max_frames),
        *speedup_user_args(speedup),
    ]
    if follow:
        user_args.append("--follow")
    user_args += _cam_user_args(cam_elevation, cam_azimuth, cam_fov, cam_view_target)
    if assets_file:
        user_args.append("--assets-file=%s" % assets_file)
        user_args.append("--assets-manifest=%s" % os.path.abspath(manifest))
    return user_args


def _resolve_views(views, *, follow, cam_dist, cam_elevation, cam_azimuth, cam_fov,
                   cam_view_target):
    """Normalise a ``views=[...]`` request into fully-resolved per-view camera specs.

    ``None`` -> ``None`` (the single-view legacy path, byte-identical to before the
    multi-view upgrade). Otherwise each entry is a dict whose camera keys (``follow``,
    ``cam_dist``, ``elevation``, ``azimuth``, ``fov``, ``view_target``) OVERRIDE the
    top-level kwargs of the same meaning; unset keys inherit them (which themselves
    default to None = today's framing). Optional per-view ``frames_dir``/``out_gif``
    name where that view's artifacts land; ``id`` defaults to ``view<k>``. PURE."""
    if views is None:
        return None
    if not views:
        raise CaptureError("views=[] is ambiguous -- pass None for the single-view default")
    out = []
    for k, v in enumerate(views):
        if v is None:
            v = {}
        if not isinstance(v, dict):
            raise CaptureError(f"views[{k}] must be a dict, got {type(v).__name__}")
        rv = {
            "id": str(v.get("id") or f"view{k}"),
            "follow": bool(v.get("follow", follow)),
            "cam_dist": v.get("cam_dist", cam_dist),
            "elevation": v.get("elevation", cam_elevation),
            "azimuth": v.get("azimuth", cam_azimuth),
            "fov": v.get("fov", cam_fov),
            "view_target": v.get("view_target", cam_view_target),
        }
        if v.get("frames_dir"):
            rv["frames_dir"] = str(v["frames_dir"])
        if v.get("out_gif"):
            rv["out_gif"] = str(v["out_gif"])
        out.append(rv)
    ids = [r["id"] for r in out]
    if len(set(ids)) != len(ids):
        raise CaptureError(f"duplicate view ids: {ids}")
    return out


def capture_gif(game_path: str, out_gif: str, *, actions, seed: int = 0,
                follow: bool = False, width: int = DEFAULT_WIDTH,
                height: int = DEFAULT_HEIGHT, fps: int = DEFAULT_FPS,
                max_frames: int = DEFAULT_MAX_FRAMES, frames_dir: str | None = None,
                downscale_to: int | None = 640, timeout_s: float = 300.0,
                exe: str | None = None, project: str | None = None,
                dress_assets: bool = True, game_context: str | None = None,
                assets_manifest: str | None = None, cam_dist: float | None = None,
                trajectory_overview: bool = True,
                cam_elevation: float | None = None, cam_azimuth: float | None = None,
                cam_fov: float | None = None, cam_view_target: str | None = None,
                views: list | None = None) -> dict:
    """Render a certified ``.gd`` game's witness replay to a GIF. ``actions`` is the
    winning plan (from a fresh verify). Returns ``{result, ticks, n_frames, out_path,
    frames_dir?}``. Raises ``CaptureError`` on an infra failure.

    When ``dress_assets`` (default), route the game's bodies to render-only bank assets once
    (``asset_bank.route_assets``, cached to ``<game>.assets.json``) and feed the mapping to
    the capture host; only 3D games' proxies are actually dressed (2D games stay flat). Asset
    dressing is purely cosmetic -- the certified physics/state trail is unaffected.

    PARAMETRIC CAMERA (3D; render-only; semantics ported from godot-ai's editor_screenshot):
    ``cam_elevation`` (deg; 0=level, 90=overhead), ``cam_azimuth`` (deg; 0=front, 90=right),
    ``cam_fov`` (deg), ``cam_view_target`` (comma-separated state() body NAMES to frame on).
    All default None -> nothing is appended to the host argv and framing is byte-identical
    to before. 2D games ignore all four (mirroring godot-ai's viewport_2d rule). Setting any
    of the four EXPLICITLY overrides an authored in-game camera (a conscious caller choice);
    when none is set an authored camera is honoured as before.

    MULTI-VIEW (``views=[{...}, ...]``): render the SAME witness N times, one deterministic
    replay per camera spec (the fingerprint identity machinery proves every replay's state
    trail is byte-identical, so view k's frame ordinal j is pixel-for-pixel the same tick as
    view 0's). Each view dict may set ``id``/``follow``/``cam_dist``/``elevation``/
    ``azimuth``/``fov``/``view_target`` (unset keys inherit the top-level kwargs) plus
    ``frames_dir``/``out_gif`` for where its artifacts land. View 0 is the PRIMARY: it uses
    the top-level ``out_gif``/``frames_dir`` defaults, and the returned dict's top-level
    keys describe it. Extra views without ``out_gif`` render frames only (no GIF). The
    trajectory pre-scan and asset routing run ONCE and are shared by every view. With
    ``views=None`` (default) behaviour is byte-identical to the single-view lane."""
    from harness.verify.godot_exec import (
        default_godot_project, find_godot_exe, speedup_from_env,
    )

    exe = exe or find_godot_exe()
    project = project or default_godot_project()
    if not exe or not os.path.isfile(exe):
        raise CaptureError(f"Godot binary not found (set HARNESS_GODOT_EXE): {exe!r}")
    host = os.path.join(project, "capture_host.gd")
    if not os.path.isfile(host):
        raise CaptureError(f"capture_host.gd not found at {host}")

    # Route bank assets once per game (best-effort; primitives are the fallback).
    manifest = assets_manifest or _default_manifest()
    assets_file = ""
    if dress_assets:
        assets_file = _route_assets_for_game(exe, project, game_path, game_context, manifest)

    work = tempfile.mkdtemp(prefix="gicap_")
    frames_out = frames_dir or os.path.join(work, "frames")
    Path(frames_out).mkdir(parents=True, exist_ok=True)
    actions_file = os.path.join(work, "witness.json")
    Path(actions_file).write_text(
        json.dumps({"seed": int(seed), "actions": wire_actions(actions)}),
        encoding="utf-8")

    # Trajectory-aware framing (3D): pre-scan the witness once headless so the render overlay
    # can frame the whole flight path, not the t=0 static box, and trail the craft along it.
    # Returns None for a 2D game (2D framing then stays byte-identical) or on any failure.
    # Hoisted ABOVE the (possibly multi-view) render loop: one scan serves every view.
    scan = _scan_trajectory(exe, project, game_path, actions, seed) \
        if trajectory_overview else None

    # Replay at the SAME game-tick speedup certification ran at (HARNESS_GODOT_SPEEDUP), so
    # the capture host's paired physics-rate/time-scale scaling matches the serve host that
    # produced the witness. The stepping is designed tick-identical across speedups, so this
    # never changes the certified trajectory -- it keeps the capture host byte-faithful to the
    # exact env that certified the game (and defends against any future non-tick-identical
    # game). N==1 appends nothing, so the default invocation stays byte-identical to before.
    speedup = speedup_from_env()

    resolved = _resolve_views(
        views, follow=follow, cam_dist=cam_dist, cam_elevation=cam_elevation,
        cam_azimuth=cam_azimuth, cam_fov=cam_fov, cam_view_target=cam_view_target)

    def _render_one(v: dict, v_frames_out: str, v_gif: str | None) -> dict:
        """Run the capture host ONCE for one camera spec; frames into ``v_frames_out``,
        optional GIF to ``v_gif``. Same argv/env discipline as the legacy single view."""
        user_args = _view_user_args(
            game_path, actions_file, v_frames_out, width=int(width), height=int(height),
            max_frames=int(max_frames), speedup=speedup, follow=bool(v["follow"]),
            assets_file=assets_file, manifest=manifest, cam_elevation=v["elevation"],
            cam_azimuth=v["azimuth"], cam_fov=v["fov"], cam_view_target=v["view_target"])
        argv = _capture_argv(exe, project, user_args, int(width), int(height))
        env = _child_env()
        env.update(_cam_env(v["cam_dist"], scan))   # render-only framing hints (see _cam_env)
        log = tempfile.TemporaryFile(mode="w+b")
        try:
            proc = subprocess.run(argv, stdout=log, stderr=log,
                                  stdin=subprocess.DEVNULL, env=env,
                                  timeout=timeout_s)
        except subprocess.TimeoutExpired:
            raise CaptureError("capture host timed out (%.0fs)" % timeout_s)
        rc = proc.returncode
        meta = _read_meta(v_frames_out)
        pngs = sorted(Path(v_frames_out).glob("frame_*.png"))
        if not pngs:
            log.seek(0)
            tail = log.read().decode("utf-8", "replace")[-1500:]
            raise CaptureError(
                "capture produced no frames (rc=%s). Godot log tail:\n%s" % (rc, tail))
        info = {"result": meta.get("result", "unknown"), "ticks": meta.get("ticks"),
                "n_pngs": len(pngs)}
        if v_gif:
            gif_info = _assemble_gif(pngs, v_gif, fps=fps, downscale_to=downscale_to)
            info["n_frames"] = gif_info["n_frames"]
            info["out_path"] = str(v_gif)
        return info

    try:
        with _Xvfb(int(width), int(height)):
            if resolved is None:
                # -- single view: the legacy lane, byte-identical argv/env/artifacts ------
                prim = {"follow": bool(follow), "cam_dist": cam_dist,
                        "elevation": cam_elevation, "azimuth": cam_azimuth,
                        "fov": cam_fov, "view_target": cam_view_target}
                info = _render_one(prim, frames_out, str(out_gif))
                result = {
                    "result": info["result"],
                    "ticks": info["ticks"],
                    "n_frames": info["n_frames"],
                    "out_path": str(out_gif),
                }
                if frames_dir:
                    result["frames_dir"] = str(frames_out)
                return result

            # -- multi-view: N deterministic replays of the SAME witness ------------------
            view_records = []
            prim_info: dict = {}
            for k, v in enumerate(resolved):
                v_frames = v.get("frames_dir") or \
                    (frames_out if k == 0 else os.path.join(work, "frames_%s" % v["id"]))
                v_gif = v.get("out_gif") or (str(out_gif) if k == 0 else None)
                Path(v_frames).mkdir(parents=True, exist_ok=True)
                info = _render_one(v, v_frames, v_gif)
                if k == 0:
                    prim_info = info
                rec = {"id": v["id"], "follow": v["follow"], "cam_dist": v["cam_dist"],
                       "elevation": v["elevation"], "azimuth": v["azimuth"],
                       "fov": v["fov"], "view_target": v["view_target"],
                       "result": info["result"], "ticks": info["ticks"],
                       "n_pngs": info["n_pngs"], "frames_dir": str(v_frames)}
                if info.get("out_path"):
                    rec["out_path"] = info["out_path"]
                view_records.append(rec)
                # Determinism tripwire: every replay of the same witness must land the same
                # outcome at the same tick (the cameras are render-only). A divergence means
                # the tick lock broke -- fail loudly, never ship misaligned views.
                r0 = view_records[0]
                if (rec["result"], rec["ticks"], rec["n_pngs"]) != \
                        (r0["result"], r0["ticks"], r0["n_pngs"]):
                    raise CaptureError(
                        "multi-view replay divergence: view %r got %r, view %r got %r"
                        % (rec["id"], (rec["result"], rec["ticks"], rec["n_pngs"]),
                           r0["id"], (r0["result"], r0["ticks"], r0["n_pngs"])))

            # Top-level keys mirror the single-view result and describe the PRIMARY view.
            result = {
                "result": prim_info["result"],
                "ticks": prim_info["ticks"],
                "n_frames": prim_info.get("n_frames", prim_info["n_pngs"]),
                "out_path": prim_info.get("out_path", str(out_gif)),
                "views": view_records,
            }
            if frames_dir:
                result["frames_dir"] = str(frames_out)
            return result
    finally:
        if not frames_dir:
            shutil.rmtree(work, ignore_errors=True)


def _read_meta(frames_out: str) -> dict:
    p = Path(frames_out) / "meta.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {}


def _assemble_gif(pngs, out_gif, *, fps: int, downscale_to: int | None,
                  max_frames: int = GIF_MAX_FRAMES, colors: int = GIF_COLORS) -> dict:
    from PIL import Image

    # The demo GIF is a COSMETIC artifact — the mother dataset keeps EVERY per-tick PNG
    # in frames_dir untouched, so a long episode's GIF may be strided down to `max_frames`
    # for a watchable, small file without ever touching the pixel trail the exporter reads.
    sel = pngs
    if max_frames and len(pngs) > max_frames:
        sel = [pngs[int(i * len(pngs) / max_frames)] for i in range(max_frames)]
    frames = []
    for p in sel:
        im = Image.open(p).convert("RGB")
        if downscale_to and im.width > downscale_to:
            h = int(im.height * downscale_to / im.width)
            im = im.resize((downscale_to, h), Image.LANCZOS)
        frames.append(im)
    frames.extend([frames[-1]] * HOLD_FRAMES)
    # Palette quantization is the dominant GIF-size lever (GIF is palette-based). Build ONE
    # shared palette from a MONTAGE of frames sampled across the whole episode (a single-frame
    # palette can miss colours other frames need and collapse the animation), then remap every
    # frame onto it — small file, no inter-frame flicker.
    if colors and frames:
        step = max(1, len(frames) // 12)
        sample = frames[::step] or frames[:1]
        montage = Image.new("RGB", (sum(f.width for f in sample),
                                    max(f.height for f in sample)))
        x = 0
        for f in sample:
            montage.paste(f, (x, 0)); x += f.width
        base = montage.quantize(colors=colors, method=Image.MEDIANCUT)
        frames = [f.quantize(palette=base, dither=Image.FLOYDSTEINBERG) for f in frames]
    out = Path(out_gif)
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    duration = max(1, int(round(1000.0 / max(1, int(fps)))))
    frames[0].save(str(out), save_all=True, append_images=frames[1:],
                   duration=duration, loop=0, optimize=True, disposal=2)
    return {"n_frames": len(frames)}
