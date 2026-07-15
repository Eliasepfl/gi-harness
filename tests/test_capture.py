"""Godot-free unit tests for the capture lane (harness/verify/capture.py).

The full render path needs a display + Godot + software GL (exercised on the cluster
via scripts/capture_demo.sh, not here). These tests lock the determinism-critical +
security-critical invariants that DON'T need Godot: the capture invocation never goes
headless and always pins --fixed-fps 60; the child env carries the software-GL hint but
no secrets; and the PIL GIF assembler turns a PNG sequence into a looping GIF.
"""
from __future__ import annotations

import os

import pytest

from harness.verify import capture


def test_capture_argv_is_never_headless_and_pins_fixed_fps():
    argv = capture._capture_argv("/opt/godot/godot", "/proj",
                                 ["--capture", "--game-file=/g.gd"], 960, 540)
    # Headless is the pixel-blind dummy rasterizer -- capture must NEVER use it.
    assert "--headless" not in argv
    # Determinism pin: the fixed physics rate must be present (mirrors serve/capture host).
    i = argv.index("--fixed-fps")
    assert argv[i + 1] == "60"
    # The render path + resolution + the capture host script + the passthrough user args.
    assert "--rendering-driver" in argv and "opengl3" in argv
    assert "res://capture_host.gd" in argv
    assert argv[-2:] == ["--capture", "--game-file=/g.gd"]
    assert "960x540" in argv


def test_child_env_has_software_gl_and_no_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-should-not-leak")
    monkeypatch.setenv("DISPLAY", ":42")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = capture._child_env()
    assert env.get("LIBGL_ALWAYS_SOFTWARE") == "1"
    assert env.get("DISPLAY") == ":42"          # the render display is passed through
    # Default-deny scrub: no credential reaches the untrusted capture host process.
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENROUTER_API_KEY" not in env
    assert not any(k.endswith("_API_KEY") for k in env)


def test_assemble_gif_from_png_sequence(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    paths = []
    for i in range(5):
        p = frames_dir / ("frame_%05d.png" % i)
        # a distinct solid colour per frame so the GIF is genuinely multi-frame
        Image.new("RGB", (64, 48), (10 * i, 20, 30)).save(p)
        paths.append(p)
    out = tmp_path / "out.gif"
    info = capture._assemble_gif(sorted(paths), str(out), fps=20, downscale_to=None)
    assert out.is_file() and out.stat().st_size > 0
    # frames + the end-hold tail
    assert info["n_frames"] == 5 + capture.HOLD_FRAMES
    with Image.open(out) as im:
        assert im.format == "GIF"
        assert getattr(im, "is_animated", False)


def test_assemble_gif_downscales_wide_frames(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    p = tmp_path / "frame_00000.png"
    Image.new("RGB", (1200, 800), (50, 60, 70)).save(p)
    out = tmp_path / "out.gif"
    capture._assemble_gif([p], str(out), fps=15, downscale_to=640)
    with Image.open(out) as im:
        assert im.size[0] == 640            # downscaled to the cap width
        assert im.size[1] == int(800 * 640 / 1200)
