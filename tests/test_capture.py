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
from harness.demo import asset_bank


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


# --------------------------------------------------------------------------- #
# Parametric camera + multi-view plumbing (Godot-free). The host/dresser side
# (capture_host.gd / visual_dress.gd) is exercised by the cluster Validate lane.
# --------------------------------------------------------------------------- #
def test_cam_user_args_default_appends_nothing():
    # The byte-identity contract: no explicit cam param -> NOTHING added to the argv
    # (same append-nothing-when-default pattern as speedup_user_args).
    assert capture._cam_user_args() == []
    assert capture._cam_user_args(None, None, None, None) == []
    assert capture._cam_user_args(cam_view_target="") == []


def test_cam_user_args_renders_all_four():
    args = capture._cam_user_args(25.0, 30.5, 45.0, "puck,goal_left")
    assert args == ["--cam-elevation=25.0", "--cam-azimuth=30.5",
                    "--cam-fov=45.0", "--cam-target=puck,goal_left"]


def test_view_user_args_default_is_byte_identical_to_legacy():
    # With every new knob unset, the host user-args are EXACTLY the pre-parametric list.
    got = capture._view_user_args(
        "/g/game.gd", "/tmp/w.json", "/tmp/frames", width=960, height=540,
        max_frames=300, speedup=1, follow=False, assets_file="", manifest="")
    want = ["--capture",
            "--game-file=%s" % os.path.abspath("/g/game.gd"),
            "--actions-file=/tmp/w.json",
            "--out=/tmp/frames",
            "--width=960", "--height=540", "--max-frames=300"]
    assert got == want


def test_view_user_args_ordering_follow_cam_assets():
    # New cam args slot between --follow and the asset args; both neighbours unchanged.
    got = capture._view_user_args(
        "/g/game.gd", "/w.json", "/f", width=640, height=360, max_frames=100,
        speedup=1, follow=True, assets_file="/a.json", manifest="/m.json",
        cam_elevation=60.0, cam_view_target="puck")
    assert got[-5:] == ["--follow", "--cam-elevation=60.0", "--cam-target=puck",
                        "--assets-file=/a.json",
                        "--assets-manifest=%s" % os.path.abspath("/m.json")]


def _rv(views, **over):
    kw = dict(follow=False, cam_dist=None, cam_elevation=None, cam_azimuth=None,
              cam_fov=None, cam_view_target=None)
    kw.update(over)
    return capture._resolve_views(views, **kw)


def test_resolve_views_none_is_single_view_legacy():
    assert _rv(None) is None


def test_resolve_views_inherits_toplevel_and_overrides_per_view():
    vs = _rv([{}, {"id": "top", "elevation": 90.0, "fov": 45.0}],
             follow=True, cam_dist=2.5, cam_azimuth=10.0, cam_view_target="puck")
    v0, v1 = vs
    assert v0 == {"id": "view0", "follow": True, "cam_dist": 2.5, "elevation": None,
                  "azimuth": 10.0, "fov": None, "view_target": "puck"}
    assert v1["id"] == "top" and v1["elevation"] == 90.0 and v1["fov"] == 45.0
    assert v1["azimuth"] == 10.0 and v1["follow"] is True     # inherited
    assert v1["view_target"] == "puck"                        # inherited


def test_resolve_views_rejects_empty_list_and_duplicate_ids():
    with pytest.raises(capture.CaptureError):
        _rv([])
    with pytest.raises(capture.CaptureError):
        _rv([{"id": "a"}, {"id": "a"}])


def test_capture_gif_default_signature_has_new_knobs_off():
    # The public surface: every new kwarg defaults to None (byte-identical behaviour).
    import inspect
    sig = inspect.signature(capture.capture_gif)
    for name in ("cam_elevation", "cam_azimuth", "cam_fov", "cam_view_target", "views"):
        assert name in sig.parameters
        assert sig.parameters[name].default is None


# --------------------------------------------------------------------------- #
# Bank-asset routing call-site (Godot-free plumbing; 3D games only get dressed)
# --------------------------------------------------------------------------- #
def test_use_llm_off_when_harness_offline(monkeypatch):
    # HARNESS_OFFLINE forces the offline fallback regardless of any key present.
    monkeypatch.setenv("HARNESS_OFFLINE", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-present")
    assert capture._use_llm() is False


def test_use_llm_on_with_key_and_no_offline(monkeypatch):
    monkeypatch.delenv("HARNESS_OFFLINE", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-present")
    assert capture._use_llm() is True


def test_game_context_is_slug_as_words():
    assert capture._game_context("/x/y/drive_a_cart_one_lap.gd") == "drive a cart one lap"
    assert capture._game_context("/x/mini_collect_3d.gd") == "mini collect 3d"


def test_default_manifest_points_at_the_bank():
    mf = capture._default_manifest()
    assert mf and os.path.isfile(mf)
    man = asset_bank.load_manifest(mf)
    assert len(man["assets"]) >= 8


def test_route_for_game_returns_empty_when_manifest_missing(tmp_path):
    # A bad manifest short-circuits BEFORE any Godot pre-pass -> "" (dresser uses primitives).
    out = capture._route_assets_for_game(
        "/no/such/godot", "/no/such/project", str(tmp_path / "g.gd"),
        game_context="g", manifest_path=str(tmp_path / "nope.json"))
    assert out == ""


def test_route_for_game_empty_when_dump_fails(tmp_path):
    # A valid manifest but an unresolvable Godot exe -> the dump pre-pass yields no bodies,
    # so routing declines gracefully (no crash, no cache written).
    mf = capture._default_manifest()
    out = capture._route_assets_for_game(
        "/no/such/godot", "/no/such/project", str(tmp_path / "g.gd"),
        game_context="g", manifest_path=mf)
    assert out == ""


def test_offline_route_consumes_3d_body_names():
    # The call-site's offline contract: route_assets covers every t=0 body name with a valid
    # bank id or None, and semantically-named goals do map to a real asset.
    manifest = asset_bank.load_manifest(capture._default_manifest())
    ids = {a["id"] for a in manifest["assets"]}
    bodies = [{"name": "puck", "controlled": True}, {"name": "goal_left"},
              {"name": "goal_right"}, {"name": "table"}]
    mapping = asset_bank.route_assets("mini collect 3d", bodies, manifest, use_llm=False)
    assert set(mapping) == {"puck", "goal_left", "goal_right", "table"}
    for v in mapping.values():
        assert v is None or v in ids
    assert mapping["goal_left"] is not None and mapping["goal_left"] in ids
