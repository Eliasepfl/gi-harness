"""Canonical example: climb the staircase up to the top zone.

Three static steps in a staircase (rise ~45 px, within reach of the calibrated
jump), sensor zone at the top. Success = the agent reaches the zone.
Solvable by moving right and jumping on each press.
"""

SCENE_DESCRIPTION = "climb the platforms up to the zone"
AVAILABLE_ACTIONS = ["left", "right", "jump", "noop"]


def build_scene(sdk):
    """Populate the scene: ground, agent, three-step staircase, top zone."""
    sdk.add_ground(friction=0.9)
    # Placed at rest on the ground surface (radius-1 segment -> surface at y=1).
    sdk.spawn_agent((60, 19))
    # Solid steps (static boxes on the ground): bump + jump = climb.
    sdk.add_box("step1", (210, 22.5), size=(110, 45), body="static", friction=0.9)
    sdk.add_box("step2", (320, 45.0), size=(110, 90), body="static", friction=0.9)
    sdk.add_box("step3", (430, 67.5), size=(110, 135), body="static", friction=0.9)
    sdk.add_zone("goal", (430, 165), (100, 60))
    sdk.on_contact("agent", "goal", "reached")


def get_success(sdk) -> bool:
    """True if the agent reached the zone (flag) or overlaps its bbox."""
    if sdk.get_flag("reached"):
        return True
    al, ab, ar, at = sdk.query("agent")["bbox"]
    gl, gb, gr, gt = sdk.query("goal")["bbox"]
    return al < gr and ar > gl and ab < gt and at > gb
