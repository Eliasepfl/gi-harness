"""Live viewer tests — headless-safe (SDL dummy video/audio drivers).

pygame may not open real windows in CI, so we force the dummy SDL drivers
BEFORE pygame is ever imported. The tests then drive watch() to completion on a
real template game under that driver, unit-test the cadence/scale helpers
(no wall-clock dependence), and check the clean error when pygame is absent.
"""

import os

# Dummy drivers must be set before pygame initialises any subsystem.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pytest

from harness import viewer


# A nominal game path — the missing-pygame test surfaces its RuntimeError BEFORE the
# file is ever loaded, so this need not exist (the real fixture was retired 2026-07-15).
TEMPLATE_GAME = "scenes/games/_nonexistent.py"


# ==========================================================================
#  Pure cadence / scale helpers (no window, no wall time)
# ==========================================================================
def test_fps_scales_with_speed():
    assert viewer._fps_for_speed(1.0) == viewer.BASE_FPS
    assert viewer._fps_for_speed(2.0) == 2.0 * viewer._fps_for_speed(1.0)
    assert viewer._fps_for_speed(0.5) == 0.5 * viewer._fps_for_speed(1.0)
    # never zero / never runaway
    assert viewer._fps_for_speed(0.0) >= 1.0
    assert viewer._fps_for_speed(10_000.0) <= viewer.FPS_CAP


def test_frame_delay_is_inverse_of_speed():
    # 2x speed -> each physics step occupies half the real time
    assert viewer._frame_delay_ms(2.0) == pytest.approx(viewer._frame_delay_ms(1.0) / 2.0)
    assert viewer._frame_delay_ms(1.0) == pytest.approx(1000.0 / viewer.BASE_FPS)


def test_window_size_respects_scale():
    assert viewer._window_size((800, 600), 1.0) == (800, 600)
    assert viewer._window_size((800, 600), 0.5) == (400, 300)
    assert viewer._window_size((800, 600), 2.0) == (1600, 1200)
    # degenerate scale never yields a zero-size window
    assert viewer._window_size((800, 600), 0.0) == (1, 1)


# ==========================================================================
#  watch() runs a short explicit-actions episode to completion (dummy driver)
# ==========================================================================
# RETIRED (Elias, 2026-07-15): the watch()-to-completion tests are deleted with the
# generated game fixture they drove (scenes/games/row2b_seesaw.py). The pure
# cadence/scale helpers above still guard viewer's wall-clock-free logic.


def test_watch_bad_path_returns_error_dict():
    result = viewer.watch("scenes/games/__does_not_exist__.py",
                          actions=["wait"], speed=8.0)
    assert result["result"] == "error"
    assert "error" in result


# ==========================================================================
#  Missing pygame -> clean, actionable error
# ==========================================================================
def test_missing_pygame_raises_clean_error(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pygame" or name.startswith("pygame."):
            raise ImportError("No module named 'pygame'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError) as excinfo:
        viewer._import_pygame()
    assert "pip install pygame" in str(excinfo.value)


def test_watch_surfaces_missing_pygame(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pygame" or name.startswith("pygame."):
            raise ImportError("No module named 'pygame'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError) as excinfo:
        viewer.watch(TEMPLATE_GAME, actions=["wait"])
    assert "pip install pygame" in str(excinfo.value)
