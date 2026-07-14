"""Live pygame viewer — WATCH v2 generated games play in a real window.

The GIF renderer (`harness.render`) bakes a replay to disk; this module is its
real-time sibling: it replays the same G3 witness in an interactive pygame
window at real-time cadence (60 physics steps/second, scaled by ``speed``).

Visual parity with the GIF path is kept by REUSING `harness.render`'s single
source of truth — the palette, the sensor-colour rules, the entity
classification/z-order, and the witness-resolution helpers are all imported
from there. Only the drawing primitives are reimplemented with pygame calls
(crisper than baked frames) following the exact same rules render.py uses.

pygame is imported lazily: importing this module never requires pygame, and a
missing install yields a clean "pip install pygame" error only when a window is
actually requested. Everything runs on the main thread (Windows-friendly): the
event pump, the physics stepper and the draw share one clock-paced loop.
"""

from __future__ import annotations

from pathlib import Path

from harness import render  # single source of truth for palette + witness rules

# ---- cadence / display constants (eng.) ----------------------------------
BASE_FPS = 60.0          # physics steps per real second at speed 1.0
FPS_CAP = 1000.0         # clamp so an extreme --speed cannot wedge the clock
HOLD_SECONDS = 0.6       # keep the terminal frame on screen this long before looping/exiting
FLASH_SECONDS = 0.9      # how long a freshly latched milestone name stays on screen


# ==========================================================================
#  Lazy pygame import (clean error when the optional dep is missing)
# ==========================================================================
def _import_pygame():
    """Import pygame or raise a clean, actionable RuntimeError."""
    try:
        import pygame  # noqa: PLC0415  — intentionally lazy (optional dependency)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "pygame is not installed - run 'pip install pygame' to use the live viewer"
        ) from exc
    return pygame


# ==========================================================================
#  Pure helpers (unit-testable without a window)
# ==========================================================================
def _fps_for_speed(speed: float) -> float:
    """Physics frames-per-second for a speed multiplier (clamped, never zero)."""
    return max(1.0, min(FPS_CAP, BASE_FPS * float(speed)))


def _frame_delay_ms(speed: float) -> float:
    """Real milliseconds one physics step should occupy at ``speed``."""
    return 1000.0 / _fps_for_speed(speed)


def _window_size(world_size, scale: float) -> tuple[int, int]:
    """World dimensions -> pixel window size at ``scale`` (min 1x1)."""
    w, h = world_size
    return max(1, int(w * float(scale))), max(1, int(h * float(scale)))


def _frames_for_seconds(seconds: float, speed: float) -> int:
    """How many clock-paced frames make up ``seconds`` of real time at ``speed``."""
    return max(1, int(round(seconds * _fps_for_speed(speed))))


# ==========================================================================
#  Drawing (pygame primitives, following render.py's exact rules)
# ==========================================================================
def _font(pygame, size: int):
    """Best-effort default font; None if the font subsystem is unavailable."""
    try:
        if not pygame.font.get_init():
            pygame.font.init()
        return pygame.font.Font(None, size)
    except Exception:  # noqa: BLE001
        return None


def _draw_one(pygame, surf, q: dict, *, fill, outline, width: int,
              scale: float, world_h: float, inset: bool = True) -> None:
    """Draw one entity onto ``surf``, mirroring render._draw_shape.

    Prefers the true world-space ``verts`` (rotation-correct) exactly as the
    GIF path does; falls back to the axis-aligned bbox only when verts are
    unavailable. ``inset`` shrinks the bbox by 1px for on-screen bodies (the
    static erase-mask draws un-inset)."""
    shape = q.get("shape", "box")
    box = render._screen_box(q["bbox"], scale, world_h)
    if inset:
        box = render._inset(box)
    verts = q.get("verts")

    if shape == "circle":
        cx = (box[0] + box[2]) / 2.0
        cy = (box[1] + box[3]) / 2.0
        rad = max(1, int(round((box[2] - box[0]) / 2.0)))
        if fill is not None:
            pygame.draw.circle(surf, fill, (cx, cy), rad)
        if outline is not None:
            pygame.draw.circle(surf, outline, (cx, cy), rad, width)
    elif shape == "segment":
        col = outline or fill
        if verts and len(verts) == 2:
            (x0, y0), (x1, y1) = render._screen_pts(verts, scale, world_h)
        else:  # bbox diagonal fallback (matches render.py)
            x0, y0, x1, y1 = box[0], box[3], box[2], box[1]
        pygame.draw.line(surf, col, (x0, y0), (x1, y1), max(3, width + 1))
    elif verts and len(verts) >= 3:  # true rotated polygon
        pts = render._screen_pts(verts, scale, world_h)
        if fill is not None:
            pygame.draw.polygon(surf, fill, pts)
        if outline is not None:
            pygame.draw.polygon(surf, outline, pts, width)
    else:  # unknown / no verts -> rectangle from bbox
        rect = pygame.Rect(box[0], box[1], max(1.0, box[2] - box[0]),
                           max(1.0, box[3] - box[1]))
        if fill is not None:
            pygame.draw.rect(surf, fill, rect)
        if outline is not None:
            pygame.draw.rect(surf, outline, rect, width)


def _blit_text(pygame, screen, font, text: str, pos, colour) -> None:
    if not font or not text:
        return
    try:
        screen.blit(font.render(str(text), True, colour), pos)
    except Exception:  # noqa: BLE001 — text is decorative, never break a frame
        pass


def _draw_scene(pygame, screen, world, world_size, scale: float, label: str,
                tick: int, flash, hud, fonts) -> None:
    """Redraw the whole scene from world.query() — same palette/z-order as the GIF."""
    world_h = float(world_size[1])
    W, H = _window_size(world_size, scale)
    font, small = fonts

    screen.fill(render.BG)
    for gx in range(0, W, render.GRID_PX):
        pygame.draw.line(screen, render.GRID, (gx, 0), (gx, H))
    for gy in range(0, H, render.GRID_PX):
        pygame.draw.line(screen, render.GRID, (0, gy), (W, gy))

    ents = []
    for name in world.entities():
        try:
            q = dict(world.query(name))
        except Exception:  # noqa: BLE001 — a removed/missing body must not break a frame
            continue
        if q.get("bbox"):
            ents.append((name, q))
    ents.sort(key=lambda e: render._Z.get(render._kind(e[1]), 2))

    # Pass 1 — sensors (translucent) on their own layer, painted UNDER solids.
    sensor_layer = pygame.Surface((W, H), pygame.SRCALPHA)
    for name, q in ents:
        if render._kind(q) == "sensor":
            col = render._sensor_colour(name)
            _draw_one(pygame, sensor_layer, q, fill=(*col, 40), outline=(*col, 230),
                      width=2, scale=scale, world_h=world_h)
    screen.blit(sensor_layer, (0, 0))

    # Pass 2 — static solids straight onto the screen; build the erase mask.
    mask = pygame.Surface((W, H), pygame.SRCALPHA)
    mask.fill((255, 255, 255, 255))          # opaque = keep dynamic pixels here
    for name, q in ents:
        if render._kind(q) == "static":
            _draw_one(pygame, screen, q, fill=render.C_STATIC,
                      outline=render._shade(render.C_STATIC, 1.35), width=1,
                      scale=scale, world_h=world_h)
            if not q.get("sensor"):        # zero the mask alpha where solids sit
                _draw_one(pygame, mask, q, fill=(255, 255, 255, 0),
                          outline=(255, 255, 255, 0), width=1,
                          scale=scale, world_h=world_h, inset=False)

    # Pass 3 — dynamics + controlled on a layer, then erase static overlap.
    dyn_layer = pygame.Surface((W, H), pygame.SRCALPHA)
    for name, q in ents:                     # ents sorted -> dynamic(2) before controlled(3)
        kind = render._kind(q)
        if kind in ("dynamic", "controlled"):
            colour = render.C_CONTROLLED if kind == "controlled" else render._dyn_colour(name)
            _draw_one(pygame, dyn_layer, q, fill=(*colour, 255),
                      outline=(*render._shade(colour, 0.65), 255), width=2,
                      scale=scale, world_h=world_h)
    dyn_layer.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    screen.blit(dyn_layer, (0, 0))

    # HUD text — label (top-left), milestone flash (below it), tick (top-right).
    _blit_text(pygame, screen, font, label, (8, 6), render.C_TEXT)
    if flash:
        _blit_text(pygame, screen, font, f"* {flash}", (8, 24), render.C_SENSOR)
    tick_str = f"tick {tick}"
    tw = font.size(tick_str)[0] if font else 7 * len(tick_str)
    _blit_text(pygame, screen, font, tick_str, (max(8, W - tw - 8), 6), render.C_TEXT)
    if hud:
        _blit_text(pygame, screen, small, hud, (8, H - 20), render.C_TEXT)


# ==========================================================================
#  Episode state machine (shared §2 runner semantics, one physics step/frame)
# ==========================================================================
def _fresh_episode(make_world, game, seed: int) -> dict:
    """Build a fresh world+game and return the mutable episode state."""
    world = make_world(seed=seed)
    game.build(world)
    return {"world": world, "act_i": 0, "sub": 0, "tick": 0,
            "result": "timeout", "done": False, "latched": set(),
            "flash_text": None, "flash_left": 0, "hold_left": None}


def _latch_ep(ep: dict, game, flash_frames: int) -> None:
    """Latch newly-True checkpoints and arm the on-screen flash (display only)."""
    if game.checkpoints is None:
        return
    try:
        state = game.checkpoints(ep["world"])
    except Exception:  # noqa: BLE001 — decorative, never break a run
        return
    fresh = [k for k, v in (state or {}).items() if v and k not in ep["latched"]]
    if fresh:
        ep["latched"].update(fresh)
        ep["flash_text"] = ", ".join(fresh)
        ep["flash_left"] = flash_frames


def _advance(ep: dict, game, action_list, K: int, flash_frames: int) -> None:
    """Advance ONE physics step, respecting decision-tick boundaries (§2 runner).

    A decision tick = act(action) then K × [step(1); on_step]; failure then
    success are checked once the K steps complete. Raises propagate to the
    caller (NaN/explosion sentinel from World.step)."""
    if ep["done"]:
        return
    world = ep["world"]
    if ep["act_i"] >= len(action_list):      # witness exhausted -> episode ends
        ep["result"] = "timeout"
        ep["done"] = True
        return
    if ep["sub"] == 0:                        # tick boundary -> apply the decision
        game.act(world, action_list[ep["act_i"]])
    world.step(1)
    if game.on_step is not None:
        game.on_step(world)
    ep["sub"] += 1
    if ep["sub"] >= K:                        # tick complete -> latch + win/lose
        ep["sub"] = 0
        ep["tick"] = ep["act_i"] + 1
        ep["act_i"] += 1
        _latch_ep(ep, game, flash_frames)
        if game.failure is not None and game.failure(world):
            ep["result"] = "failure"
            ep["done"] = True
        elif game.success(world):
            ep["result"] = "success"
            ep["done"] = True


# ==========================================================================
#  Public entry point — watch()
# ==========================================================================
def watch(game_path: str, *, actions=None, seed: int = 0, speed: float = 1.0,
          scale: float = 1.0, loop: bool = False, hud=None) -> dict:
    """Replay a game's G3 witness live in a pygame window.

    ``actions`` is an explicit action list, a G3 witness dict, or None (re-verify
    the game to fetch its witness) — resolved exactly like render.replay_gif.
    Cadence is real-time: ``speed`` scales the 60 steps/second physics clock
    (2.0 = double time, 0.5 = slow-mo). ``scale`` scales the window. ``loop``
    restarts the episode when it ends; otherwise the window closes shortly after.
    ``hud`` is an optional status line drawn along the bottom edge (used by the
    live demo).

    Controls: window close / ESC exit · SPACE pause · R restart the episode.
    Returns {"result": "success|failure|timeout|error", "ticks": int,
             "closed_by": "end|quit|escape|error"}.
    """
    pygame = _import_pygame()  # clean error BEFORE any heavy setup / world build

    # Resolve the witness and load the game exactly as the GIF path does.
    try:
        game = render._load_game(game_path)
    except Exception as exc:  # noqa: BLE001
        return {"result": "error", "ticks": 0, "closed_by": "error",
                "error": f"load failed: {exc}"}
    try:
        action_list, seed = render._resolve_actions(game_path, actions, seed)
    except Exception as exc:  # noqa: BLE001
        return {"result": "error", "ticks": 0, "closed_by": "error",
                "error": f"witness unavailable: {exc}"}
    try:
        make_world = render._import_world()
    except Exception as exc:  # noqa: BLE001
        return {"result": "error", "ticks": 0, "closed_by": "error",
                "error": f"world unavailable: {exc}"}

    try:
        ep = _fresh_episode(make_world, game, seed)
    except Exception as exc:  # noqa: BLE001
        return {"result": "error", "ticks": 0, "closed_by": "error",
                "error": f"build failed: {exc}"}

    world_size = render._world_size(ep["world"])
    label = getattr(game, "TITLE", "") or ""
    K = render.K
    fps = _fps_for_speed(speed)
    flash_frames = _frames_for_seconds(FLASH_SECONDS, speed)
    hold_frames = _frames_for_seconds(HOLD_SECONDS, speed)

    pygame.init()
    W, H = _window_size(world_size, scale)
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption(label or "harness live viewer")
    fonts = (_font(pygame, 18), _font(pygame, 15))
    clock = pygame.time.Clock()

    running, paused, closed_by = True, False, None
    try:
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running, closed_by = False, "quit"
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running, closed_by = False, "escape"
                    elif event.key == pygame.K_SPACE:
                        paused = not paused
                    elif event.key == pygame.K_r:
                        ep = _fresh_episode(make_world, game, seed)
                        paused = False
            if not running:
                break

            if not paused and not ep["done"]:
                try:
                    _advance(ep, game, action_list, K, flash_frames)
                except Exception as exc:  # noqa: BLE001 — NaN/explosion from World.step
                    ep["result"], ep["done"], ep["error"] = "error", True, str(exc)

            if ep["done"] and ep["hold_left"] is None:   # arm the end-of-episode hold
                ep["hold_left"] = hold_frames

            flash = None
            if ep["flash_left"] > 0:
                flash = ep["flash_text"]
                ep["flash_left"] -= 1
            _draw_scene(pygame, screen, ep["world"], world_size, scale, label,
                        ep["tick"], flash, hud, fonts)
            pygame.display.flip()
            clock.tick(fps)

            if ep["done"]:
                ep["hold_left"] -= 1
                if ep["hold_left"] <= 0:
                    if loop:
                        ep = _fresh_episode(make_world, game, seed)
                    else:
                        running = False
                        closed_by = closed_by or "end"
    finally:
        pygame.quit()

    return {"result": ep["result"], "ticks": ep["tick"], "closed_by": closed_by}


# ==========================================================================
#  Live demo — generate + watch each prompt in the window
# ==========================================================================
_FALLBACK_PROMPTS = [
    "a catapult that must fling a stone over a wall into a bucket",
    "roll a ball across a gap onto a goal platform",
]


def _final_witness(result: dict):
    """The witness dict of the final generation attempt, or None."""
    attempts = result.get("attempts") or []
    if not attempts or not isinstance(attempts[-1], dict):
        return None
    report = attempts[-1].get("report")
    witness = report.get("witness") if isinstance(report, dict) else None
    return witness if isinstance(witness, dict) else None


def demo_live(prompts=None, backend: str = "auto") -> dict:
    """Live variant of ``game demo``: generate each prompt, then WATCH it.

    For every prompt: generate a whole game (harness.gen.gamegen.generate_game),
    then replay its witness in the pygame window with a HUD line (verdict,
    attempts, backend) before moving on to the next. Returns a structured
    summary. Raises a clean RuntimeError if pygame is missing.
    """
    _import_pygame()  # fail fast with the clean hint before generating anything
    from harness.gen.gamegen import generate_game

    if not prompts:
        try:
            from harness.cli import DEFAULT_DEMO_PROMPTS as prompts
        except Exception:  # noqa: BLE001
            prompts = _FALLBACK_PROMPTS

    demos = []
    for prompt in prompts:
        summary = {"prompt": prompt, "verdict": None, "attempts": 0,
                   "backend": None, "game_path": None, "watch": None, "error": None}
        try:
            result = generate_game(prompt, backend=backend)
        except Exception as exc:  # noqa: BLE001 — one bad prompt must not sink the demo
            summary["verdict"], summary["error"] = "ERROR", f"generate failed: {exc}"
            demos.append(summary)
            continue

        summary["verdict"] = result.get("verdict")
        summary["backend"] = result.get("backend")
        summary["attempts"] = len(result.get("attempts") or [])
        summary["game_path"] = result.get("game_path")
        hud = (f"verdict={summary['verdict']}  attempts={summary['attempts']}  "
               f"model={summary['backend']}")
        print(f"[demo] {prompt}\n       {hud}")

        game_path = summary["game_path"]
        if not game_path:
            summary["error"] = "no game produced"
            demos.append(summary)
            continue
        witness = _final_witness(result)
        try:
            summary["watch"] = watch(game_path,
                                     actions=witness if isinstance(witness, dict) else None,
                                     hud=hud)
        except Exception as exc:  # noqa: BLE001
            summary["error"] = f"watch failed: {exc}"
        demos.append(summary)

    return {"backend": backend, "demos": demos}
