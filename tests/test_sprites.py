"""Sprite-bank + render-skinning tests.

Two layers:

* Resolution / caching against the COMMITTED slicemap (always runnable).
* Crop + render skinning against the vendored raw atlases, which are gitignored;
  those asserts are skipped when ``spritebank.available()`` is False, and the
  fallback-to-flat behaviour is exercised explicitly by repointing the raw root.
"""

from pathlib import Path

import pytest
from PIL import Image

from harness import render
from harness.core import spritebank

_REPO = Path(__file__).resolve().parents[1]
_SEESAW = _REPO / "scenes" / "games" / "row2b_seesaw.py"


@pytest.fixture(autouse=True)
def _fresh_bank():
    """Isolate module-level bank config + caches around every test."""
    orig_dir, orig_raw = spritebank.BANK_DIR, spritebank.RAW_ROOT
    spritebank.clear_cache()
    yield
    spritebank.BANK_DIR, spritebank.RAW_ROOT = orig_dir, orig_raw
    spritebank.clear_cache()


def _raw_present() -> bool:
    spritebank.clear_cache()
    return spritebank.available()


# ==========================================================================
#  A tiny FakeWorld + inline game (no real World needed for skinning smoke)
# ==========================================================================
class FakeWorld:
    def __init__(self, seed: int = 0, size=(800, 600)):
        self.seed, self.size = seed, size
        self._bodies: dict = {}
        self._controlled = None

    def add(self, name, shape="box", *, pos, size=None, radius=None,
            static=False, sensor=False, mass=1.0, **_kw):
        if shape == "circle":
            r = radius or 10.0
            w = h = 2.0 * r
        else:
            w, h = size or (20.0, 20.0)
        self._bodies[name] = {"pos": [float(pos[0]), float(pos[1])], "vel": [0.0, 0.0],
                              "w": float(w), "h": float(h), "shape": shape,
                              "static": bool(static), "sensor": bool(sensor)}
        return name

    def control(self, name):
        self._controlled = name

    def set_velocity(self, name, vec):
        self._bodies[name]["vel"] = [float(vec[0]), float(vec[1])]

    def step(self, n=1):
        for bd in self._bodies.values():
            if not bd["static"]:
                bd["pos"][0] += bd["vel"][0] * n
                bd["pos"][1] += bd["vel"][1] * n

    def entities(self):
        return list(self._bodies)

    def query(self, name):
        bd = self._bodies[name]
        x, y = bd["pos"]
        w, h = bd["w"], bd["h"]
        return {"pos": [x, y], "vel": list(bd["vel"]), "angle": 0.0, "angular_vel": 0.0,
                "bbox": [x - w / 2, y - h / 2, x + w / 2, y + h / 2],
                "shape": bd["shape"], "static": bd["static"], "sensor": bd["sensor"],
                "controlled": name == self._controlled}


INLINE_GAME = '''
TITLE = "Skin Smoke"
PROMPT = "slide the crate toward the goal"
ACTIONS = ["go", "wait"]

def build(world):
    world.add("ground", "box", pos=(400, 10), size=(800, 20), static=True)
    world.add("goal", "box", pos=(700, 60), size=(60, 60), static=True, sensor=True)
    world.add("ball0", "circle", pos=(250, 60), radius=15)
    world.add("crate", "box", pos=(100, 60), size=(30, 30), mass=1.0)
    world.control("crate")

def act(world, action):
    world.set_velocity("crate", (6, 0) if action == "go" else (0, 0))

def success(world):
    return world.query("crate")["pos"][0] >= 350
'''


def _write_game(tmp_path):
    p = tmp_path / "skin.py"
    p.write_text(INLINE_GAME, encoding="utf-8")
    return str(p)


# ==========================================================================
#  Resolution rules (slicemap only -> always runnable)
# ==========================================================================
def test_resolution_exact():
    for part in ("crate", "ball", "ground", "wall", "ledge", "lava", "boulder", "seesaw"):
        ref = spritebank.resolve(part)
        assert ref is not None and ref.part == part


def test_resolution_strips_digits_and_underscores():
    assert spritebank.resolve("crate_2").part == "crate"
    assert spritebank.resolve("crate2").part == "crate"
    assert spritebank.resolve("ball3").part == "ball"
    assert spritebank.resolve("rock1").part == "rock"
    assert spritebank.resolve("block_0") is None  # 'block' is not a part / alias


def test_resolution_strips_letter_and_positional_suffixes():
    assert spritebank.resolve("wall_l").part == "wall"
    assert spritebank.resolve("wall_r").part == "wall"
    assert spritebank.resolve("crate_a").part == "crate"
    assert spritebank.resolve("rock_left").part == "rock"
    assert spritebank.resolve("wall_right").part == "wall"


def test_resolution_singular_plural():
    assert spritebank.resolve("spikes").part == "spike"
    assert spritebank.resolve("crates").part == "crate"


def test_resolution_aliases():
    assert spritebank.resolve("box").part == "crate"
    assert spritebank.resolve("plank").part == "seesaw"
    assert spritebank.resolve("puck").part == "ball"
    assert spritebank.resolve("floor").part == "ground"
    assert spritebank.resolve("stone").part == "boulder"


def test_resolution_none_and_null_parts():
    # Unknown names resolve to nothing.
    for name in ("hero", "ship", "raft", "wibblewobble", ""):
        assert spritebank.resolve(name) is None
    # Slicemap parts that are explicitly null (no CC0 match) are "no sprite".
    for null_part in ("ice_floor", "target_zone", "pit_zone"):
        assert spritebank.resolve(null_part) is None
    # A star must NOT alias to goal_zone (design rule).
    assert spritebank.resolve("star") is None


def test_resolution_cached_same_object():
    a = spritebank.resolve("crate")
    b = spritebank.resolve("crate")
    assert a is b  # cached per name


# ==========================================================================
#  Missing raw/ -> graceful fallback (repoint RAW_ROOT at a non-existent dir)
# ==========================================================================
def test_missing_raw_is_unavailable_but_resolves(tmp_path):
    spritebank.RAW_ROOT = str(tmp_path / "no_raw_here")   # does not exist
    spritebank.clear_cache()
    assert spritebank.available() is False
    ref = spritebank.resolve("crate")                     # slicemap still loads
    assert ref is not None
    assert spritebank.crop(ref) is None                   # no atlas -> no crop


def test_replay_falls_back_cleanly_when_bank_unavailable(tmp_path):
    spritebank.RAW_ROOT = str(tmp_path / "nope")
    spritebank.clear_cache()
    game = _write_game(tmp_path)
    out = tmp_path / "fb.gif"
    res = render.replay_gif(game, str(out), actions=["go"] * 12,
                            world_factory=FakeWorld, sprites=True)
    assert res["result"] == "success"
    assert out.exists() and out.stat().st_size > 0
    with Image.open(str(out)) as im:
        assert im.format == "GIF"


# ==========================================================================
#  Crop + cache determinism (needs the vendored raw atlases)
# ==========================================================================
@pytest.mark.skipif(not _raw_present(), reason="raw atlases absent (gitignored)")
def test_crop_non_empty_and_cache_determinism():
    ref = spritebank.resolve("crate")
    a = spritebank.crop(ref)
    b = spritebank.crop(ref)
    assert a is not None and a.mode == "RGBA"
    assert a.size[0] > 0 and a.size[1] > 0
    assert a.getchannel("A").getbbox() is not None      # some visible pixels
    assert a is b                                        # cached object
    assert a.tobytes() == b.tobytes()                   # byte-stable


@pytest.mark.skipif(not _raw_present(), reason="raw atlases absent (gitignored)")
def test_alias_shares_crop_cache_with_target():
    crate = spritebank.crop(spritebank.resolve("crate"))
    box = spritebank.crop(spritebank.resolve("box"))     # alias -> crate
    assert crate is not None and box is not None
    assert crate.tobytes() == box.tobytes()              # same region, same bytes


@pytest.mark.skipif(not _raw_present(), reason="raw atlases absent (gitignored)")
def test_sprites_change_render_output(tmp_path):
    game = _write_game(tmp_path)
    flat = tmp_path / "flat.gif"
    skin = tmp_path / "skin.gif"
    render.replay_gif(game, str(flat), actions=["go"] * 12,
                      world_factory=FakeWorld, sprites=False)
    render.replay_gif(game, str(skin), actions=["go"] * 12,
                      world_factory=FakeWorld, sprites=True)
    assert flat.read_bytes() != skin.read_bytes()        # skinning actually drew


@pytest.mark.skipif(not _raw_present(), reason="raw atlases absent (gitignored)")
def test_neutral_decor_sensor_renders_sprite_not_zone():
    # Bank decor is static+sensor (bush, tree, fence...). A NEUTRAL sensor whose
    # name resolves to a sprite must be drawn as scenery, not as a bare amber
    # zone box — before the fix the sprite path skipped neutral sensors and the
    # two frames below were byte-identical.
    w = FakeWorld()
    w.add("tree", "box", pos=(200, 120), size=(60, 90), static=True, sensor=True)
    flat = render._render_frame(w, 0, "", 0.6, w.size, sprites=False)
    skin = render._render_frame(w, 0, "", 0.6, w.size, sprites=True)
    assert flat.tobytes() != skin.tobytes()
    # Goal sensors stay bare semantic zones: sprites on/off must not differ.
    g = FakeWorld()
    g.add("goal", "box", pos=(700, 60), size=(60, 60), static=True, sensor=True)
    gflat = render._render_frame(g, 0, "", 0.6, g.size, sprites=False)
    gskin = render._render_frame(g, 0, "", 0.6, g.size, sprites=True)
    assert gflat.tobytes() == gskin.tobytes()


# ==========================================================================
#  Byte-stability with sprites OFF (deterministic flat render)
# ==========================================================================
def test_flat_render_is_byte_stable(tmp_path):
    game = _write_game(tmp_path)
    a, b = tmp_path / "a.gif", tmp_path / "b.gif"
    render.replay_gif(game, str(a), actions=["go"] * 12,
                      world_factory=FakeWorld, sprites=False)
    render.replay_gif(game, str(b), actions=["go"] * 12,
                      world_factory=FakeWorld, sprites=False)
    with Image.open(str(a)) as ia, Image.open(str(b)) as ib:
        assert ia.n_frames == ib.n_frames
        assert ia.convert("RGB").tobytes() == ib.convert("RGB").tobytes()


# ==========================================================================
#  Real seesaw game: sprites-on renders without crashing, >= flat frame count
# ==========================================================================
@pytest.mark.skipif(not _SEESAW.exists(), reason="seesaw demo game missing")
def test_seesaw_replay_sprites_no_crash_and_frame_count(tmp_path):
    actions = ["drop_boulder"] + [None] * 60
    flat = render.replay_gif(str(_SEESAW), str(tmp_path / "seesaw_flat.gif"),
                             actions=actions, sprites=False)
    skin = render.replay_gif(str(_SEESAW), str(tmp_path / "seesaw_skin.gif"),
                             actions=actions, sprites=True)
    assert flat["result"] != "error", flat
    assert skin["result"] != "error", skin
    assert "frames" in flat and "frames" in skin
    assert skin["frames"] >= flat["frames"]
    assert (tmp_path / "seesaw_skin.gif").stat().st_size > 0
    with Image.open(str(tmp_path / "seesaw_skin.gif")) as im:
        assert im.format == "GIF"
