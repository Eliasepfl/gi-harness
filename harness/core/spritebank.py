"""Sprite bank -- cosmetic atlas-crop resolution for the GIF renderer (v2.2).

Sprites are a PURELY COSMETIC overlay: no physics, verification, or engine state
is ever read from a pixel. This module maps an ENTITY NAME (as chosen by the
game code) onto a concrete CC0 Kenney atlas region declared in
``banks/sprites/slicemap.v1.json``, and crops that region out of the vendored
atlas PNG under ``banks/sprites/raw/`` on demand.

Everything is loaded once and cached at module scope:

- ``load_slicemap()`` reads + caches the slicemap (part -> atlas region).
- ``resolve(name) -> SpriteRef | None`` maps a name to a region, caching results.
- ``crop(ref) -> PIL RGBA | None`` crops (and caches) the atlas region; returns
  ``None`` when the raw atlas is absent or the crop is degenerate.
- ``available()`` says whether skinning can run at all (slicemap + raw/ present).

The raw atlases are gitignored, so callers MUST degrade gracefully: when the
bank cannot produce a crop, the renderer falls back to flat-shape drawing.

Resolution rules (checked in order; first hit wins):

    1. exact         entity name == a slicemap part name          ("crate" -> crate)
    2. suffix-strip  drop trailing enumerators / positional tags   ("crate_2" -> crate,
                     (digits, single letters, left/right/top/...)    "ball3" -> ball,
                                                                     "wall_l" -> wall,
                                                                     "rock_left" -> rock)
    3. singular      drop a trailing plural 's'/'es'               ("spikes" -> spike)
    4. alias         a small, conservative synonym table           ("box" -> crate,
                                                                     "plank" -> seesaw)

A slicemap part that is present but declared ``null`` (no CC0 match) is treated
as "no sprite" and resolution continues to the next candidate.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

# Resampling filters (Pillow >= 9.1 exposes the enum; keep an old-Pillow fallback).
try:  # pragma: no cover - trivial version shim
    _LANCZOS = Image.Resampling.LANCZOS
except AttributeError:  # pragma: no cover
    _LANCZOS = Image.LANCZOS


# --------------------------------------------------------------------------- #
# Locations (overridable by tests / env)
# --------------------------------------------------------------------------- #
def _repo_root() -> str:
    """Repo root = grandparent of this module's package dir (harness/core/)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Directory holding slicemap.v1.json + manifest.json + raw/. Overridable so tests
# can point the loader at a fixture (or at a tree with a missing raw/).
BANK_DIR: str = os.environ.get(
    "HARNESS_SPRITE_BANK", os.path.join(_repo_root(), "banks", "sprites"))

# Root of the vendored atlas PNGs. ``None`` -> ``<BANK_DIR>/raw``. Tests set this
# to an empty dir to simulate the gitignored raw/ being absent while the slicemap
# (a committed contract) still loads.
RAW_ROOT: str | None = None

_SLICEMAP_NAME = "slicemap.v1.json"


def _slicemap_path() -> Path:
    return Path(BANK_DIR) / _SLICEMAP_NAME


def _raw_root() -> Path:
    return Path(RAW_ROOT) if RAW_ROOT is not None else Path(BANK_DIR) / "raw"


# --------------------------------------------------------------------------- #
# Resolution config
# --------------------------------------------------------------------------- #
# Conservative synonyms only -- each is an unambiguous cosmetic stand-in. NOTE
# (per design): a "star" is NOT aliased to goal_zone (a star sprite is a pickup,
# not a flag); keep this table tight.
ALIASES: dict[str, str] = {
    "box": "crate",          # a box is the canonical wooden crate
    "plank": "seesaw",       # the seesaw part IS a wooden plank/beam
    "beam": "seesaw",
    "lever": "seesaw",
    "puck": "ball",          # a round slider reads as the metal ball
    "stone": "boulder",      # the boulder part is a stone ball
    "floor": "ground",       # a floor is the ground surface tile
    "flag": "goal_zone",     # the goal_zone sprite is a flag
    "goal": "goal_zone",
}

# Trailing enumerator / positional decorations to strip (repeatedly) in step 2:
#   _2 / 2  (digits)   _a _b _l _r  (single-letter tags)
#   _left _right _top _bottom _up _down _mid _middle _center _centre _inner _outer
_SUFFIX_RE = re.compile(
    r"(_?\d+|_[a-z]|_(?:left|right|top|bottom|up|down|mid|middle|center|centre|inner|outer))$"
)


@dataclass(frozen=True)
class SpriteRef:
    """A resolved, immutable pointer into an atlas region (cosmetic only)."""

    part: str            # the slicemap part key that matched
    pack: str            # pack id (subdir under raw/)
    atlas_png: str       # atlas PNG path, relative to raw/<pack>/
    rect: tuple          # (x, y, w, h) top-left origin, pixels into the atlas
    pivot: tuple         # (px, py) fractional anchor
    region: str          # SubTexture name (provenance)


# --------------------------------------------------------------------------- #
# Module caches
# --------------------------------------------------------------------------- #
_slicemap_cache: dict | None = None
_resolve_cache: dict[str, SpriteRef | None] = {}
_atlas_cache: dict[str, Image.Image | None] = {}
_crop_cache: dict[tuple, Image.Image | None] = {}


def clear_cache() -> None:
    """Drop every module cache (tests call this after repointing BANK_DIR/RAW_ROOT)."""
    global _slicemap_cache
    _slicemap_cache = None
    _resolve_cache.clear()
    _atlas_cache.clear()
    _crop_cache.clear()


def load_slicemap() -> dict:
    """Load + cache the slicemap ``parts`` mapping (part name -> entry dict).

    Returns ``{}`` if the slicemap file is missing or malformed -- callers then
    resolve nothing and the renderer draws flat shapes.
    """
    global _slicemap_cache
    if _slicemap_cache is not None:
        return _slicemap_cache
    try:
        data = json.loads(_slicemap_path().read_text(encoding="utf-8"))
        parts = data.get("parts", {})
        _slicemap_cache = parts if isinstance(parts, dict) else {}
    except Exception:  # noqa: BLE001 - missing/corrupt slicemap => no sprites
        _slicemap_cache = {}
    return _slicemap_cache


def available() -> bool:
    """True if skinning can run: the slicemap loads AND a raw/ tree is present."""
    return bool(load_slicemap()) and _raw_root().is_dir()


# --------------------------------------------------------------------------- #
# Name -> part resolution
# --------------------------------------------------------------------------- #
def _candidates(name: str) -> list[str]:
    """Ordered candidate part keys for an entity name (see module docstring)."""
    base = (name or "").strip().lower()
    out: list[str] = []

    def add(x: str) -> None:
        if x and x not in out:
            out.append(x)

    add(base)
    # 2. progressively strip enumerator / positional suffixes.
    cur = base
    while True:
        m = _SUFFIX_RE.search(cur)
        if not m or m.start() == 0:
            break
        cur = cur[: m.start()]
        add(cur)
    # 3. singular forms of every candidate produced so far.
    for c in list(out):
        if c.endswith("es") and len(c) > 4:
            add(c[:-2])
        if c.endswith("s") and len(c) > 3:
            add(c[:-1])
    # 4. aliases of every candidate produced so far.
    for c in list(out):
        alias = ALIASES.get(c)
        if alias:
            add(alias)
    return out


def _entry_ref(part: str, entry: dict) -> SpriteRef | None:
    """Build a SpriteRef from a slicemap entry, or None if it is a null part."""
    rect = entry.get("rect")
    pack = entry.get("pack")
    atlas = entry.get("atlas_png")
    if not (pack and atlas and isinstance(rect, (list, tuple)) and len(rect) == 4):
        return None  # explicit null part (no CC0 match) or malformed entry
    pivot = entry.get("pivot") or [0.5, 0.5]
    return SpriteRef(
        part=part, pack=pack, atlas_png=atlas,
        rect=tuple(int(v) for v in rect), pivot=tuple(float(v) for v in pivot),
        region=entry.get("region", ""),
    )


def resolve(name: str) -> SpriteRef | None:
    """Resolve an entity name to a SpriteRef, or None. Cached per name."""
    if name in _resolve_cache:
        return _resolve_cache[name]
    parts = load_slicemap()
    ref: SpriteRef | None = None
    if parts:
        for cand in _candidates(name):
            entry = parts.get(cand)
            if isinstance(entry, dict):
                built = _entry_ref(cand, entry)
                if built is not None:
                    ref = built
                    break
                # part exists but is a declared null -> keep scanning candidates
    _resolve_cache[name] = ref
    return ref


# --------------------------------------------------------------------------- #
# Atlas cropping
# --------------------------------------------------------------------------- #
def _load_atlas(pack: str, atlas_png: str) -> Image.Image | None:
    """Open + cache a raw atlas PNG as RGBA, or None when the file is absent."""
    key = f"{pack}/{atlas_png}"
    if key in _atlas_cache:
        return _atlas_cache[key]
    path = _raw_root() / pack / atlas_png
    img: Image.Image | None
    try:
        with Image.open(path) as im:
            img = im.convert("RGBA")
    except Exception:  # noqa: BLE001 - raw/ gitignored: absence is expected
        img = None
    _atlas_cache[key] = img
    return img


def crop(ref: SpriteRef | None) -> Image.Image | None:
    """Return the cached RGBA crop for ``ref``, or None if unavailable/degenerate.

    Caching is keyed on (pack, atlas, rect) so identical regions share one image;
    two calls for the same ref return the same object (byte-stable).
    """
    if ref is None:
        return None
    key = (ref.pack, ref.atlas_png, ref.rect)
    if key in _crop_cache:
        return _crop_cache[key]
    result: Image.Image | None = None
    atlas = _load_atlas(ref.pack, ref.atlas_png)
    if atlas is not None:
        x, y, w, h = ref.rect
        aw, ah = atlas.size
        # Clamp to the atlas bounds so a stale rect never raises.
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(aw, x + w), min(ah, y + h)
        if x1 > x0 and y1 > y0:
            region = atlas.crop((x0, y0, x1, y1))
            # Validate non-empty: some visible alpha must remain.
            if region.width > 0 and region.height > 0:
                alpha = region.getchannel("A")
                if alpha.getbbox() is not None:
                    result = region
    _crop_cache[key] = result
    return result
