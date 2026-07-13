TITLE = "Drift"
PROMPT = "guide the puck across the ice onto the glowing pad"
ACTIONS = ["left", "right", "up", "down"]


def build(world):
    world.set_gravity(0.0, 0.0)
    world.add("puck", "circle", pos=(180.0, 150.0), radius=16.0,
              mass=1.0, friction=0.2, elasticity=0.6)
    world.control("puck")
    world.add("pad", "box", pos=(560.0, 430.0), size=(200.0, 200.0),
              static=True, sensor=True)
    world.add("w_left", "segment", pos=(0.0, 0.0), a=(8.0, 0.0), b=(8.0, 600.0),
              static=True, elasticity=0.9)
    world.add("w_right", "segment", pos=(0.0, 0.0), a=(792.0, 0.0),
              b=(792.0, 600.0), static=True, elasticity=0.9)
    world.add("w_bottom", "segment", pos=(0.0, 0.0), a=(0.0, 8.0), b=(800.0, 8.0),
              static=True, elasticity=0.9)
    world.add("w_top", "segment", pos=(0.0, 0.0), a=(0.0, 592.0),
              b=(800.0, 592.0), static=True, elasticity=0.9)


def act(world, action):
    j = 70.0
    if action == "left":
        world.impulse("puck", (-j, 0.0))
    elif action == "right":
        world.impulse("puck", (j, 0.0))
    elif action == "up":
        world.impulse("puck", (0.0, j))
    elif action == "down":
        world.impulse("puck", (0.0, -j))


def success(world):
    p = world.query("puck")
    z = world.query("pad")
    cx = (p["bbox"][0] + p["bbox"][2]) / 2.0
    cy = (p["bbox"][1] + p["bbox"][3]) / 2.0
    return (z["bbox"][0] <= cx <= z["bbox"][2]) and (z["bbox"][1] <= cy <= z["bbox"][3])


def checkpoints(world):
    p = world.query("puck")["pos"]
    dx = p[0] - 180.0
    dy = p[1] - 150.0
    return {
        "moved_off_start": (dx * dx + dy * dy) > 1600.0,
        "crossed_midline": p[0] > 400.0,
        "entered_upper_half": p[1] > 300.0,
    }
