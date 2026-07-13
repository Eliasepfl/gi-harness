"""Renderer tests — FakeWorld + inline game source, no real World/gameverify.

Checks: replay_gif produces a valid multi-frame GIF from an explicit action
list; an unknown game path returns a clean error dict; colour assignment is
stable and deterministic across calls.
"""

from PIL import Image

from harness import render
from harness.render import replay_gif


# ==========================================================================
#  FakeWorld — 2D toy substrate implementing only what the renderer reads
# ==========================================================================
class FakeWorld:
    """Minimal World stand-in: kinematic bodies, section-1 query() shape."""

    def __init__(self, seed: int = 0, size=(800, 600)):
        self.seed = seed
        self.size = size
        self._bodies: dict = {}
        self._controlled = None

    # ---- construction (used by build/act) ----
    def add(self, name, shape="box", *, pos, size=None, radius=None,
            static=False, sensor=False, mass=1.0, **_kw):
        if shape == "circle":
            r = radius or 10.0
            w = h = 2.0 * r
        else:
            w, h = size or (20.0, 20.0)
        self._bodies[name] = {
            "pos": [float(pos[0]), float(pos[1])], "vel": [0.0, 0.0],
            "w": float(w), "h": float(h), "shape": shape,
            "static": bool(static), "sensor": bool(sensor),
        }
        return name

    def control(self, name):
        self._controlled = name

    def set_velocity(self, name, vec):
        self._bodies[name]["vel"] = [float(vec[0]), float(vec[1])]

    def step(self, n=1):
        for bd in self._bodies.values():
            if bd["static"]:
                continue
            bd["pos"][0] += bd["vel"][0] * n
            bd["pos"][1] += bd["vel"][1] * n

    # ---- queries (used by success/renderer) ----
    def entities(self):
        return list(self._bodies)

    def query(self, name):
        bd = self._bodies[name]
        x, y = bd["pos"]
        w, h = bd["w"], bd["h"]
        return {
            "pos": [x, y], "vel": list(bd["vel"]), "angle": 0.0, "angular_vel": 0.0,
            "bbox": [x - w / 2, y - h / 2, x + w / 2, y + h / 2],
            "shape": bd["shape"], "static": bd["static"], "sensor": bd["sensor"],
            "controlled": name == self._controlled,
        }


# A tiny section-2 game: slide the hero right into a sensor goal.
INLINE_GAME = '''
TITLE = "Slide To Goal"
PROMPT = "slide the block toward the goal"
ACTIONS = ["go", "wait"]

def build(world):
    world.add("ground", "box", pos=(400, 10), size=(800, 20), static=True)
    world.add("goal", "box", pos=(700, 60), size=(60, 60), static=True, sensor=True)
    world.add("ball0", "circle", pos=(250, 60), radius=15)
    world.add("hero", "box", pos=(100, 60), size=(30, 30), mass=1.0)
    world.control("hero")

def act(world, action):
    if action == "go":
        world.set_velocity("hero", (6, 0))
    else:
        world.set_velocity("hero", (0, 0))

def success(world):
    return world.query("hero")["pos"][0] >= 350
'''


def _write_game(tmp_path):
    p = tmp_path / "slide.py"
    p.write_text(INLINE_GAME, encoding="utf-8")
    return str(p)


# ==========================================================================
#  A valid replay produces a real multi-frame GIF
# ==========================================================================
def test_replay_gif_produces_valid_gif(tmp_path):
    game_path = _write_game(tmp_path)
    out = tmp_path / "slide.gif"
    res = replay_gif(game_path, str(out), actions=["go"] * 12,
                     world_factory=FakeWorld, every=2)

    assert res["result"] == "success"
    assert res["ticks"] >= 5
    assert out.exists() and out.stat().st_size > 0

    with Image.open(str(out)) as im:
        assert im.format == "GIF"
        assert getattr(im, "n_frames", 1) > 2


def test_replay_gif_accepts_witness_dict(tmp_path):
    game_path = _write_game(tmp_path)
    out = tmp_path / "witness.gif"
    witness = {"seed": 0, "actions": ["go"] * 12, "ticks": 12}
    res = replay_gif(game_path, str(out), actions=witness, world_factory=FakeWorld)
    assert res["result"] == "success"
    assert out.exists()


# ==========================================================================
#  Unknown game path -> clean error dict (no exception)
# ==========================================================================
def test_unknown_game_path_returns_error_dict(tmp_path):
    out = tmp_path / "nope.gif"
    res = replay_gif(str(tmp_path / "does_not_exist.py"), str(out),
                     actions=["go"], world_factory=FakeWorld)
    assert res["result"] == "error"
    assert "error" in res and isinstance(res["error"], str)
    assert not out.exists()


# ==========================================================================
#  Colour assignment is stable and deterministic
# ==========================================================================
def test_dynamic_colour_is_stable():
    first = render._dyn_colour("ball0")
    for _ in range(5):
        assert render._dyn_colour("ball0") == first
    assert first in render.C_DYNAMIC
    # a different name may map elsewhere, but always within the palette
    assert render._dyn_colour("crate7") in render.C_DYNAMIC


def test_replay_is_reproducible(tmp_path):
    game_path = _write_game(tmp_path)
    out_a = tmp_path / "a.gif"
    out_b = tmp_path / "b.gif"
    replay_gif(game_path, str(out_a), actions=["go"] * 12, world_factory=FakeWorld)
    replay_gif(game_path, str(out_b), actions=["go"] * 12, world_factory=FakeWorld)

    with Image.open(str(out_a)) as a, Image.open(str(out_b)) as b:
        assert a.n_frames == b.n_frames
        assert a.convert("RGB").tobytes() == b.convert("RGB").tobytes()
