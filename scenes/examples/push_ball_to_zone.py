"""Canonical example: push the ball into the zone.

Flat ground, agent on the left, light ball in the middle, sensor zone on the
right. Success = the ball enters the zone (flag set by on_contact ball/zone).
Solvable by walking right to push the ball.
"""

SCENE_DESCRIPTION = "push the ball into the zone"
AVAILABLE_ACTIONS = ["left", "right", "jump", "noop"]


def build_scene(sdk):
    """Populate the scene: ground, agent, ball, sensor zone."""
    sdk.add_ground(friction=0.9)
    # Placed at rest on the ground surface (radius-1 segment -> surface at y=1).
    sdk.spawn_agent((80, 19))
    sdk.add_ball("ball", (400, 19), radius=18.0, mass=0.5, friction=0.6)
    sdk.add_zone("zone", (700, 60), (90, 120))
    sdk.on_contact("ball", "zone", "ball_in_zone")


def get_success(sdk) -> bool:
    """True if the ball reached the zone (flag) or is inside its bbox."""
    if sdk.get_flag("ball_in_zone"):
        return True
    ball = sdk.query("ball")["pos"]
    l, b, r, t = sdk.query("zone")["bbox"]
    return l <= ball[0] <= r and b <= ball[1] <= t
