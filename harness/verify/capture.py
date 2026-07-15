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

# Render env knobs (overridable; defaults match the empirically-verified in-image path).
DISPLAY_DRIVER = os.environ.get("HARNESS_CAPTURE_DISPLAY_DRIVER", "x11")
RENDER_DRIVER = os.environ.get("HARNESS_CAPTURE_RENDER_DRIVER", "opengl3")
DEFAULT_WIDTH = 960
DEFAULT_HEIGHT = 540
DEFAULT_FPS = 20            # GIF playback fps (one captured frame == one decision tick)
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


def capture_gif(game_path: str, out_gif: str, *, actions, seed: int = 0,
                follow: bool = False, width: int = DEFAULT_WIDTH,
                height: int = DEFAULT_HEIGHT, fps: int = DEFAULT_FPS,
                max_frames: int = DEFAULT_MAX_FRAMES, frames_dir: str | None = None,
                downscale_to: int | None = 640, timeout_s: float = 300.0,
                exe: str | None = None, project: str | None = None) -> dict:
    """Render a certified ``.gd`` game's witness replay to a GIF. ``actions`` is the
    winning plan (from a fresh verify). Returns ``{result, ticks, n_frames, out_path,
    frames_dir?}``. Raises ``CaptureError`` on an infra failure."""
    from harness.verify.godot_exec import default_godot_project, find_godot_exe

    exe = exe or find_godot_exe()
    project = project or default_godot_project()
    if not exe or not os.path.isfile(exe):
        raise CaptureError(f"Godot binary not found (set HARNESS_GODOT_EXE): {exe!r}")
    host = os.path.join(project, "capture_host.gd")
    if not os.path.isfile(host):
        raise CaptureError(f"capture_host.gd not found at {host}")

    work = tempfile.mkdtemp(prefix="gicap_")
    frames_out = frames_dir or os.path.join(work, "frames")
    Path(frames_out).mkdir(parents=True, exist_ok=True)
    actions_file = os.path.join(work, "witness.json")
    Path(actions_file).write_text(
        json.dumps({"seed": int(seed), "actions": [str(a) for a in actions]}),
        encoding="utf-8")

    user_args = [
        "--capture",
        "--game-file=%s" % os.path.abspath(game_path),
        "--actions-file=%s" % actions_file,
        "--out=%s" % frames_out,
        "--width=%d" % int(width),
        "--height=%d" % int(height),
        "--max-frames=%d" % int(max_frames),
    ]
    if follow:
        user_args.append("--follow")

    argv = _capture_argv(exe, project, user_args, int(width), int(height))

    try:
        with _Xvfb(int(width), int(height)):
            env = _child_env()
            log = tempfile.TemporaryFile(mode="w+b")
            try:
                proc = subprocess.run(argv, stdout=log, stderr=log,
                                      stdin=subprocess.DEVNULL, env=env,
                                      timeout=timeout_s)
            except subprocess.TimeoutExpired:
                raise CaptureError("capture host timed out (%.0fs)" % timeout_s)
            rc = proc.returncode

        meta = _read_meta(frames_out)
        pngs = sorted(Path(frames_out).glob("frame_*.png"))
        if not pngs:
            log.seek(0)
            tail = log.read().decode("utf-8", "replace")[-1500:]
            raise CaptureError(
                "capture produced no frames (rc=%s). Godot log tail:\n%s" % (rc, tail))

        gif_info = _assemble_gif(pngs, out_gif, fps=fps, downscale_to=downscale_to)
        result = {
            "result": meta.get("result", "unknown"),
            "ticks": meta.get("ticks"),
            "n_frames": gif_info["n_frames"],
            "out_path": str(out_gif),
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


def _assemble_gif(pngs, out_gif, *, fps: int, downscale_to: int | None) -> dict:
    from PIL import Image

    frames = []
    for p in pngs:
        im = Image.open(p).convert("RGB")
        if downscale_to and im.width > downscale_to:
            h = int(im.height * downscale_to / im.width)
            im = im.resize((downscale_to, h), Image.LANCZOS)
        frames.append(im)
    frames.extend([frames[-1]] * HOLD_FRAMES)
    out = Path(out_gif)
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    duration = max(1, int(round(1000.0 / max(1, int(fps)))))
    frames[0].save(str(out), save_all=True, append_images=frames[1:],
                   duration=duration, loop=0, optimize=True, disposal=2)
    return {"n_frames": len(frames)}
