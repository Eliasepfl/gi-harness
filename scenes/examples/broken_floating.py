"""NEGATIVE fixture (intentionally invalid) for the verifier's L1 layer.

A dynamic box floats in mid-air with no support: during settling it falls
(massive displacement, never comes to rest at its initial position). The scene
compiles and get_success is well-formed, but the physics is incoherent -- which
is exactly what L1 (settling) must reject.
"""

SCENE_DESCRIPTION = "a box balanced in mid-air (broken scene)"
AVAILABLE_ACTIONS = ["left", "right", "jump", "noop"]


def build_scene(sdk):
    """Ground + agent on the ground, plus a floating box with no support (invalid)."""
    sdk.add_ground(friction=0.9)
    sdk.spawn_agent((80, 40))
    # Box suspended in the void: nothing holds it -> falls during settling.
    sdk.add_box("floating", (400, 450), size=(40, 40), mass=1.0)


def get_success(sdk) -> bool:
    """True if the box stays perched high (never the case after settling)."""
    return sdk.query("floating")["pos"][1] > 400
