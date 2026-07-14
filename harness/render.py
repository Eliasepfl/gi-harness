"""Generic GIF replay renderer for arbitrary v2 generated games (PIL only).

No pixels are read from the engine: every frame is redrawn from `world.query()`
bbox/shape data. The renderer runs the shared section-2 runner semantics (a
decision tick = act, then K physics steps, then win/lose checks) and captures
frames along the way.

Shape-aware, palette-consistent, and lightly anti-clipped: dynamic bodies are
masked by static solids so the solver's penetration into walls is not shown.
Physics is never touched — masking is purely visual.
"""

from __future__ import annotations

import math
import zlib
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFilter

try:  # cosmetic sprite skinning (raw atlases are gitignored -> optional)
    from harness.core import spritebank
except Exception:  # noqa: BLE001 - never let a bank import break rendering
    spritebank = None

try:  # Pillow >= 9.1 resampling enum, with an old-Pillow fallback
    _LANCZOS = Image.Resampling.LANCZOS
    _BICUBIC = Image.Resampling.BICUBIC
except AttributeError:  # pragma: no cover
    _LANCZOS, _BICUBIC = Image.LANCZOS, Image.BICUBIC

# ---- runner / capture constants (eng.) -----------------------------------
K = 6              # physics steps per decision tick (section-2 runner)
HOLD_FRAMES = 15   # duplicate the final frame this many times (readable end)
FRAME_MS = 60      # per-frame duration in the GIF

# ---- sprite skinning options (cosmetic; verification never sees pixels) ---
SPRITE_SKINNING = True   # module default; auto-off when the bank is unavailable
SPRITE_SHADOW = True     # soft drop shadow under sprites (dark-bg harmony)
HAZARD_SPRITE_ALPHA = 0.6  # overlay alpha for a sprite inside a hazard sensor


def _sprites_ok(sprites) -> bool:
    """Resolve the effective sprite toggle: explicit arg else module default,
    always gated on the bank actually being usable."""
    want = SPRITE_SKINNING if sprites is None else bool(sprites)
    if not want or spritebank is None:
        return False
    try:
        return spritebank.available()
    except Exception:  # noqa: BLE001
        return False

# ---- palette (see CONTRACTS section 5) -----------------------------------
BG = (18, 19, 26)          # #12131a  dark background
GRID = (30, 32, 42)        # subtle 40px grid
C_CONTROLLED = (94, 205, 130)   # #5ecd82  the controlled body
C_SENSOR = (240, 190, 70)       # #f0be46  neutral sensor zones (translucent)
C_GOAL = (110, 220, 140)        # goal-ish sensors: green (distinct from hazards!)
C_HAZARD = (235, 100, 90)       # hazard-ish sensors: red
C_STATIC = (95, 100, 120)       # #5f6478  static geometry
C_TEXT = (150, 155, 170)
# other dynamic bodies cycle through 3 stable blues/violets (per-name hash)
C_DYNAMIC = [(94, 164, 255), (122, 132, 220), (168, 124, 240)]

# Sensor semantics by name — a stopgap until bank categories carry semantics.
_GOAL_WORDS = ("goal", "exit", "star", "flag", "finish", "target", "dock", "pad_zone")
_HAZARD_WORDS = ("spike", "lava", "hazard", "saw", "danger", "pit", "acid", "fire")


def _sensor_colour(name: str):
    n = name.lower()
    if any(w in n for w in _HAZARD_WORDS):
        return C_HAZARD
    if any(w in n for w in _GOAL_WORDS):
        return C_GOAL
    return C_SENSOR

GRID_PX = 40


# ==========================================================================
#  Small helpers
# ==========================================================================
def _shade(c, f):
    """Multiply an RGB colour by `f`, clamped to [0, 255]."""
    return tuple(max(0, min(255, int(v * f))) for v in c)


def _dyn_colour(name: str):
    """Stable blue/violet for a non-controlled dynamic body (deterministic)."""
    return C_DYNAMIC[zlib.crc32(name.encode("utf-8")) % len(C_DYNAMIC)]


def _screen_box(bbox, scale: float, world_h: float):
    """World bbox [l, b, r, t] (y up) -> screen box [x0, y0, x1, y1] (y down)."""
    l, b, r, t = bbox
    return [l * scale, (world_h - t) * scale, r * scale, (world_h - b) * scale]


def _inset(box, px: int = 1):
    """Shrink a screen box inward by `px` on every side, guarding against flip."""
    x0, y0, x1, y1 = box
    if x1 - x0 > 2 * px:
        x0, x1 = x0 + px, x1 - px
    if y1 - y0 > 2 * px:
        y0, y1 = y0 + px, y1 - px
    return [x0, y0, x1, y1]


def _screen_pts(verts, scale: float, world_h: float):
    """World-space vertex list -> flat screen point list (y flipped)."""
    return [(x * scale, (world_h - y) * scale) for x, y in verts]


def _draw_shape(draw, shape: str, box, *, fill=None, outline=None, width: int = 2,
                verts=None, scale: float = 1.0, world_h: float = 600.0):
    """Draw one entity. Prefers true world-space `verts` (rotation-correct);
    falls back to the axis-aligned bbox when verts are unavailable."""
    if shape == "circle":
        draw.ellipse(box, fill=fill, outline=outline, width=width)
    elif shape == "segment":
        col = outline or fill
        if verts and len(verts) == 2:
            (x0, y0), (x1, y1) = _screen_pts(verts, scale, world_h)
            draw.line([x0, y0, x1, y1], fill=col, width=max(3, width + 1))
        else:
            draw.line([box[0], box[3], box[2], box[1]], fill=col, width=max(3, width + 1))
    elif verts and len(verts) >= 3:  # box / poly with a real outline -> true polygon
        draw.polygon(_screen_pts(verts, scale, world_h), fill=fill,
                     outline=outline, width=width)
    else:  # unknown / no verts -> rectangle from bbox
        draw.rectangle(box, fill=fill, outline=outline, width=width)


def _kind(q: dict) -> str:
    """Classify an entity for colouring and z-order."""
    if q.get("sensor"):
        return "sensor"
    if q.get("controlled"):
        return "controlled"
    if q.get("static"):
        return "static"
    return "dynamic"


_Z = {"sensor": 0, "static": 1, "dynamic": 2, "controlled": 3}


# ==========================================================================
#  Sprite skinning (cosmetic overlay drawn from the sprite bank)
# ==========================================================================
def _dist(p, q) -> float:
    return math.hypot(q[0] - p[0], q[1] - p[1])


def _fill_tiled(sprite: Image.Image, W: int, H: int) -> Image.Image:
    """Tile a (roughly square) sprite to fill a W x H box without distortion.

    Kenney surface tiles are square, so a stretched ground/wall reads poorly;
    tiling with square tiles keeps them crisp. Used only for axis-aligned static
    boxes whose aspect ratio is far from 1."""
    side = max(1, min(W, H))
    tile = sprite.resize((side, side), _LANCZOS)
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for x in range(0, W, side):
        for y in range(0, H, side):
            out.paste(tile, (x, y), tile)
    return out


def _soft_shadow(spr: Image.Image, pad: int = 4) -> tuple:
    """Wrap ``spr`` in a padded canvas with a soft dark shadow behind it.

    Returns (canvas, (ox, oy)) where the sprite sits at (ox, oy) in the canvas.
    Keeps the sprite visually centered (shadow is only slightly offset down)."""
    w, h = spr.size
    cw, ch = w + 2 * pad, h + 2 * pad
    alpha = spr.getchannel("A")
    shadow_a = Image.new("L", (cw, ch), 0)
    shadow_a.paste(alpha, (pad, pad + 2))                 # 2px downward offset
    shadow_a = shadow_a.filter(ImageFilter.GaussianBlur(2))
    shadow_a = shadow_a.point(lambda a: (a * 130) // 255)  # dark, semi-opaque
    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    canvas.putalpha(shadow_a)                              # black RGB + shadow alpha
    canvas.paste(spr, (pad, pad), spr)
    return canvas, (pad, pad)


def _oriented_sprite(cropped: Image.Image, q: dict, scale: float,
                     world_h: float) -> tuple | None:
    """Return (image, center_xy) for a sprite scaled/rotated to match entity ``q``,
    or None if the target size is degenerate. Coordinates are screen-space."""
    shape = q.get("shape", "box")
    verts = q.get("verts")
    angle = float(q.get("angle", 0.0) or 0.0)
    l, b, r, t = q["bbox"]
    static = bool(q.get("static"))

    # Screen center (default: the body position; segments recentre on their span).
    px, py = q.get("pos", [(l + r) / 2.0, (b + t) / 2.0])
    cx, cy = px * scale, (world_h - py) * scale
    rot_deg = -math.degrees(angle)

    if shape == "segment" and verts and len(verts) == 2:
        (ax, ay), (bx, by) = verts
        w_local = _dist((ax, ay), (bx, by))
        h_local = 2.0 * float(q.get("radius", 4.0))
        sax, say = ax * scale, (world_h - ay) * scale
        sbx, sby = bx * scale, (world_h - by) * scale
        rot_deg = -math.degrees(math.atan2(sby - say, sbx - sax))
        cx, cy = (sax + sbx) / 2.0, (say + sby) / 2.0
    elif verts and len(verts) >= 3:            # box / poly: true side lengths
        w_local = _dist(verts[0], verts[1])
        h_local = _dist(verts[1], verts[2])
    else:                                       # circle / no verts: use the bbox
        w_local, h_local = (r - l), (t - b)

    W, H = max(1, round(w_local * scale)), max(1, round(h_local * scale))
    if W < 1 or H < 1:
        return None

    axis_aligned = abs(angle) < 0.02
    if static and axis_aligned and shape in ("box", "poly") and max(W, H) >= 2 * min(W, H):
        spr = _fill_tiled(cropped, W, H)       # wide/tall static -> tile, don't stretch
    else:
        spr = cropped.resize((W, H), _LANCZOS)

    if abs(rot_deg) > 0.05:
        spr = spr.rotate(rot_deg, expand=True, resample=_BICUBIC)
    return spr, (cx, cy)


def _paste_sprite(target: Image.Image, cropped: Image.Image, q: dict,
                  scale: float, world_h: float, *, alpha: float = 1.0,
                  shadow: bool = True) -> bool:
    """Composite ``cropped`` onto ``target`` sized/rotated for entity ``q``.

    Returns True on success, False if nothing was drawn (caller falls back to the
    flat shape)."""
    oriented = _oriented_sprite(cropped, q, scale, world_h)
    if oriented is None:
        return False
    spr, (cx, cy) = oriented
    if alpha < 1.0:
        a = spr.getchannel("A").point(lambda v: int(v * alpha))
        spr = spr.copy()
        spr.putalpha(a)
    if shadow and SPRITE_SHADOW and alpha >= 1.0:
        spr, _ = _soft_shadow(spr)
    x0 = round(cx - spr.width / 2.0)
    y0 = round(cy - spr.height / 2.0)
    target.paste(spr, (x0, y0), spr)
    return True


# ==========================================================================
#  Frame drawing
# ==========================================================================
def _render_frame(world, tick: int, label: str, scale: float, world_size,
                  flash: str | None = None, sprites: bool | None = None) -> Image.Image:
    """Redraw the whole scene from world.query() into one RGB frame.

    `flash` is an optional short string (a freshly latched milestone name)
    drawn in sensor-amber under the label. `sprites` toggles cosmetic sprite
    skinning (None -> module default `SPRITE_SKINNING`, always gated on the bank
    being usable); when a sprite cannot be produced the flat shape is drawn."""
    use_sprites = _sprites_ok(sprites)
    world_w, world_h = world_size
    W, H = max(1, int(world_w * scale)), max(1, int(world_h * scale))
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img, "RGBA")

    for gx in range(0, W, GRID_PX):
        d.line([(gx, 0), (gx, H)], fill=GRID, width=1)
    for gy in range(0, H, GRID_PX):
        d.line([(0, gy), (W, gy)], fill=GRID, width=1)

    ents = []
    for name in world.entities():
        try:
            q = dict(world.query(name))
        except Exception:  # noqa: BLE001  — a missing/removed body must not break a frame
            continue
        if q.get("bbox"):
            ents.append((name, q))

    # True-shape mask of static solids: used to erase dynamic-body penetration.
    # (Rect-based masking mangled rotated statics like ramps.)
    static_mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(static_mask)
    for _, q in ents:
        if q.get("static") and not q.get("sensor"):
            _draw_shape(md, q.get("shape", "box"),
                        _screen_box(q["bbox"], scale, world_h),
                        fill=255, outline=255, width=1,
                        verts=q.get("verts"), scale=scale, world_h=world_h)
    transparent = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    ents.sort(key=lambda e: _Z.get(_kind(e[1]), 2))

    for name, q in ents:
        kind = _kind(q)
        shape = q.get("shape", "box")
        box = _inset(_screen_box(q["bbox"], scale, world_h))
        verts = q.get("verts")

        cropped = None
        if use_sprites:
            try:
                cropped = spritebank.crop(spritebank.resolve(name))
            except Exception:  # noqa: BLE001 - any bank hiccup -> flat fallback
                cropped = None

        if kind == "sensor":
            col = _sensor_colour(name)
            # A NEUTRAL sensor with a sprite is scenery (bank decor: bush, tree,
            # fence...): draw the sprite alone, no zone box. Goal zones stay bare
            # (semantic green); hazards keep the zone + translucent overlay.
            if cropped is not None and col == C_SENSOR:
                overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                if _paste_sprite(overlay, cropped, q, scale, world_h, shadow=False):
                    img.paste(overlay, (0, 0), overlay)
                    continue
            _draw_shape(d, shape, box, fill=(*col, 40),
                        outline=(*col, 230), width=2,
                        verts=verts, scale=scale, world_h=world_h)
            if cropped is not None and col == C_HAZARD:
                overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                if _paste_sprite(overlay, cropped, q, scale, world_h,
                                 alpha=HAZARD_SPRITE_ALPHA, shadow=False):
                    img.paste(overlay, (0, 0), overlay)
        elif kind == "static":
            if cropped is not None and _paste_sprite(img, cropped, q, scale, world_h):
                pass  # skinned in place (static bodies define the mask themselves)
            else:
                _draw_shape(d, shape, box, fill=C_STATIC,
                            outline=_shade(C_STATIC, 1.35), width=1,
                            verts=verts, scale=scale, world_h=world_h)
        else:
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            drawn = False
            if cropped is not None:
                drawn = _paste_sprite(layer, cropped, q, scale, world_h)
            if not drawn:
                colour = C_CONTROLLED if kind == "controlled" else _dyn_colour(name)
                ld = ImageDraw.Draw(layer)
                _draw_shape(ld, shape, box, fill=(*colour, 255),
                            outline=(*_shade(colour, 0.65), 255), width=2,
                            verts=verts, scale=scale, world_h=world_h)
            # erase the true-shape overlap with statics (anti-clip, dynamic only)
            layer = Image.composite(transparent, layer, static_mask)
            img.paste(layer, (0, 0), layer)

    if label:
        d.text((8, 6), label, fill=C_TEXT)
    if flash:
        d.text((8, 20), f"* {flash}", fill=C_SENSOR)
    tick_str = f"tick {tick}"
    try:
        tw = d.textlength(tick_str)
    except Exception:  # noqa: BLE001
        tw = 7 * len(tick_str)
    d.text((max(8.0, W - tw - 8), 6), tick_str, fill=C_TEXT)
    return img


# ==========================================================================
#  Game loading (gameverify loader if present, else local restricted exec)
# ==========================================================================
def _symbols(obj) -> SimpleNamespace:
    """Extract the section-2 game symbols from a module / namespace / dict."""
    get = obj.get if isinstance(obj, dict) else (lambda k, dflt=None: getattr(obj, k, dflt))
    build, act, success = get("build"), get("act"), get("success")
    for req, fn in (("build", build), ("act", act), ("success", success)):
        if not callable(fn):
            raise RuntimeError(f"invalid game module: '{req}' missing or not callable")
    on_step, failure = get("on_step"), get("failure")
    checkpoints = get("checkpoints")
    return SimpleNamespace(
        TITLE=get("TITLE", "") or "",
        PROMPT=get("PROMPT", "") or "",
        ACTIONS=list(get("ACTIONS", []) or []),
        build=build,
        act=act,
        on_step=on_step if callable(on_step) else None,
        success=success,
        failure=failure if callable(failure) else None,
        checkpoints=checkpoints if callable(checkpoints) else None,
    )


def _local_load(game_path: str) -> dict:
    """Execute a game module in a restricted namespace (sandbox scan if available)."""
    src = Path(game_path).read_text(encoding="utf-8")
    try:
        from harness.core.sandbox import scan_source  # lazy import
    except Exception:
        scan_source = None
    if scan_source is not None:
        violations = scan_source(src)
        if violations:
            raise RuntimeError(f"game rejected by the sandbox: {violations}")
    ns: dict = {"__name__": "game", "__builtins__": __builtins__}
    exec(compile(src, str(game_path), "exec"), ns)
    return ns


def _load_game(game_path: str) -> SimpleNamespace:
    """Load a game via harness.gameverify's loader if present, else local exec."""
    try:
        from harness.verify import gameverify  # lazy import (module F may not exist yet)
        for fname in ("load_game", "_load_game", "load_game_module"):
            loader = getattr(gameverify, fname, None)
            if callable(loader):
                return _symbols(loader(game_path))
    except Exception:  # noqa: BLE001  — fall back to local exec
        pass
    return _symbols(_local_load(game_path))


def _import_world():
    """Lazy import of the real World (module E may not exist yet)."""
    from harness.core.world import World
    return World


def _world_size(world) -> tuple[int, int]:
    """Best-effort read of the world dimensions (default 800x600)."""
    size = getattr(world, "size", None)
    if callable(size):
        try:
            size = size()
        except Exception:  # noqa: BLE001
            size = None
    if isinstance(size, (tuple, list)) and len(size) == 2:
        return int(size[0]), int(size[1])
    return 800, 600


def _resolve_actions(game_path: str, actions, seed: int):
    """Return (action_list, seed): explicit list, witness dict, or verify witness."""
    if isinstance(actions, dict):  # a G3 witness
        return list(actions.get("actions", [])), int(actions.get("seed", seed))
    if actions is not None:
        return list(actions), seed
    from harness.verify import gameverify  # lazy import
    report = gameverify.verify_game(game_path)
    witness = report.get("witness") or {}
    return list(witness.get("actions", [])), int(witness.get("seed", seed))


def _save_gif(frames, out_path: str) -> None:
    out = Path(out_path)
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(str(out), save_all=True, append_images=frames[1:],
                   duration=FRAME_MS, loop=0, optimize=True, disposal=2)


# ==========================================================================
#  Public entry point
# ==========================================================================
def replay_gif(game_path: str, out_path: str, *, actions=None, seed: int = 0,
               label=None, max_ticks: int = 400, scale: float = 0.6,
               every: int = 2, world_factory=None, sprites: bool = True) -> dict:
    """Render a game replay to an animated GIF.

    `actions` is an explicit list of action strings, a G3 witness dict, or None
    (re-verify the game to fetch the witness). `world_factory(seed=...)` overrides
    the real World (used by tests); it defaults to harness.world.World.
    `sprites` enables cosmetic sprite skinning (default on; auto-off when the
    sprite bank / raw atlases are unavailable, falling back to flat shapes).

    Returns {"ticks": int, "result": "success|failure|timeout|error", ...}.
    """
    every = max(1, int(every))

    try:
        game = _load_game(game_path)
    except Exception as exc:  # noqa: BLE001
        return {"ticks": 0, "result": "error", "error": f"load failed: {exc}"}

    try:
        action_list, seed = _resolve_actions(game_path, actions, seed)
    except Exception as exc:  # noqa: BLE001
        return {"ticks": 0, "result": "error", "error": f"witness unavailable: {exc}"}

    try:
        make_world = world_factory or _import_world()
        world = make_world(seed=seed)
    except Exception as exc:  # noqa: BLE001
        return {"ticks": 0, "result": "error", "error": f"world unavailable: {exc}"}

    try:
        game.build(world)
    except Exception as exc:  # noqa: BLE001
        return {"ticks": 0, "result": "error", "error": f"build failed: {exc}"}

    world_size = _world_size(world)
    label = game.TITLE if label is None else label

    frames: list = []
    last_snap = -1
    latched: set = set()          # milestones already latched (runner-side, like gameverify)
    flash_text: str | None = None
    flash_until = -1              # tick until which the flash stays visible
    FLASH_TICKS = 8               # (eng.) how long a latched milestone stays on screen

    def snap(t: int) -> None:
        nonlocal last_snap
        cur_flash = flash_text if t <= flash_until else None
        frames.append(_render_frame(world, t, label, scale, world_size,
                                    flash=cur_flash, sprites=sprites))
        last_snap = t

    def latch_new(t: int) -> bool:
        """Evaluate checkpoints (guarded); latch and arm the flash for new ones."""
        nonlocal flash_text, flash_until
        if game.checkpoints is None:
            return False
        try:
            state = game.checkpoints(world)
        except Exception:  # noqa: BLE001 — display-only feature, never break replay
            return False
        fresh = [k for k, v in (state or {}).items() if v and k not in latched]
        if not fresh:
            return False
        latched.update(fresh)
        flash_text = ", ".join(fresh)
        flash_until = t + FLASH_TICKS
        return True

    snap(0)
    result = "timeout"
    tick = 0
    try:
        for i, action in enumerate(action_list[:max_ticks]):
            game.act(world, action)
            for _ in range(K):
                world.step(1)
                if game.on_step is not None:
                    game.on_step(world)
            tick = i + 1
            newly_latched = latch_new(tick)
            ended = None
            if game.failure is not None and game.failure(world):
                ended = "failure"
            elif game.success(world):
                ended = "success"
            if ended is not None:
                result = ended
                snap(tick)
                break
            if newly_latched or tick % every == 0:
                snap(tick)
    except Exception as exc:  # noqa: BLE001  — NaN/explosion surfaced by World.step
        if last_snap != tick and frames:
            snap(tick)
        if frames:
            _save_gif(frames, out_path)
        return {"ticks": tick, "result": "error", "error": f"runtime: {exc}"}

    if last_snap != tick:
        snap(tick)
    frames.extend([frames[-1]] * HOLD_FRAMES)

    _save_gif(frames, out_path)
    return {"ticks": tick, "result": result, "frames": len(frames),
            "out_path": str(out_path)}
