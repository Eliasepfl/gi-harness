"""v1 legacy — see gamegen.py for v2.

Offline backend: library of parameterised 2D scene templates.

Each template emits the SOURCE CODE of a scene module conforming to
CONTRACTS §2 (SCENE_DESCRIPTION, AVAILABLE_ACTIONS, build_scene, get_success).
The emitted code imports NOTHING and only talks to the SDK passed as argument.
Positions and sizes vary deterministically (hash of the command) for variety,
while keeping scenes valid (inside the world, at rest).

Keyword lists are intentionally bilingual (French + English): v1 accepts legacy
French commands, so the words are matching DATA, not prose — kept as-is to
preserve behaviour.
"""
from __future__ import annotations

import hashlib

WORLD = (800, 600)
_ACTIONS = '["left", "right", "jump", "noop"]'

# Selection keywords (French + English).
_PUSH_KEYS = ("pousse", "pousser", "push", "balle", "ball", "ballon")
_STACK_KEYS = ("empile", "empiler", "stack", "boîte", "boite", "caisse", "cube")
_CLIMB_KEYS = ("atteins", "atteindre", "reach", "plateforme", "platform",
               "climb", "grimpe", "grimper", "monte", "escalade")


def _seeds(command):
    """Deterministic list of ints 0..65535 derived from the command."""
    h = hashlib.md5((command or "").encode("utf-8")).hexdigest()
    return [int(h[i * 4:i * 4 + 4], 16) for i in range(8)]


def _pick(seed, lo, hi):
    """Deterministic integer in [lo, hi]."""
    return lo + seed % (hi - lo + 1)


def select_template(command):
    """Choose a template from the command's keywords."""
    c = (command or "").lower()
    if any(k in c for k in _PUSH_KEYS):
        return "push_ball"
    if any(k in c for k in _STACK_KEYS):
        return "stack"
    if any(k in c for k in _CLIMB_KEYS):
        return "climb"
    return "reach"


def build_scene_source(command):
    """Emit the source code of a scene module for the given command."""
    s = _seeds(command)
    kind = select_template(command)
    if kind == "push_ball":
        return _push_ball(command, s)
    if kind == "stack":
        return _stack(command, s)
    if kind == "climb":
        return _climb(command, s)
    return _reach(command, s)


# --- Common skeleton ----------------------------------------------------------

_MODULE = '''SCENE_DESCRIPTION = {desc}
AVAILABLE_ACTIONS = {actions}


def build_scene(sdk):
{build}


def get_success(sdk):
{success}
'''

# Pure predicate: is an entity's centre inside the zone's bbox.
_AGENT_IN_GOAL = '''    a = sdk.query("agent")
    z = sdk.query("goal")
    cx = (a["bbox"][0] + a["bbox"][2]) / 2.0
    cy = (a["bbox"][1] + a["bbox"][3]) / 2.0
    return (z["bbox"][0] <= cx <= z["bbox"][2]) and (z["bbox"][1] <= cy <= z["bbox"][3])'''

_BALL_IN_GOAL = '''    b = sdk.query("ball")
    z = sdk.query("goal")
    cx = (b["bbox"][0] + b["bbox"][2]) / 2.0
    cy = (b["bbox"][1] + b["bbox"][3]) / 2.0
    return (z["bbox"][0] <= cx <= z["bbox"][2]) and (z["bbox"][1] <= cy <= z["bbox"][3])'''

_CRATE_IN_GOAL = '''    z = sdk.query("goal")
    for name in ("crate_a", "crate_b"):
        e = sdk.query(name)
        cx = (e["bbox"][0] + e["bbox"][2]) / 2.0
        cy = (e["bbox"][1] + e["bbox"][3]) / 2.0
        if (z["bbox"][0] <= cx <= z["bbox"][2]) and (z["bbox"][1] <= cy <= z["bbox"][3]):
            return True
    return False'''


# --- Templates ----------------------------------------------------------------

def _reach(command, s):
    """Default: reach a ground zone on the other side."""
    ax = _pick(s[0], 70, 120)
    gx = _pick(s[1], 600, 700)
    gw = _pick(s[2], 90, 140)
    gh = _pick(s[3], 80, 130)
    build = (
        '    sdk.add_ground()\n'
        f'    sdk.spawn_agent(({ax}, 18))\n'
        f'    sdk.add_zone("goal", ({gx}, {gh // 2}), ({gw}, {gh}))'
    )
    return _MODULE.format(desc=repr(command), actions=_ACTIONS,
                          build=build, success=_AGENT_IN_GOAL)


def _push_ball(command, s):
    """Push a ball toward a ground zone."""
    ax = _pick(s[0], 70, 110)
    br = _pick(s[1], 15, 24)
    bx = _pick(s[2], 340, 430)
    gx = _pick(s[3], 620, 700)
    gw = _pick(s[4], 90, 130)
    gh = _pick(s[5], 70, 110)
    build = (
        '    sdk.add_ground()\n'
        f'    sdk.spawn_agent(({ax}, 18))\n'
        f'    sdk.add_ball("ball", ({bx}, {br}), radius={br})\n'
        f'    sdk.add_zone("goal", ({gx}, {gh // 2}), ({gw}, {gh}))'
    )
    return _MODULE.format(desc=repr(command), actions=_ACTIONS,
                          build=build, success=_BALL_IN_GOAL)


def _stack(command, s):
    """Boxes to stack + a high zone."""
    ax = _pick(s[0], 60, 100)
    c1 = _pick(s[1], 300, 360)
    c2 = _pick(s[2], 420, 480)
    gx = _pick(s[3], 360, 460)
    gy = _pick(s[4], 120, 170)
    gw = _pick(s[5], 80, 120)
    gh = _pick(s[6], 60, 90)
    build = (
        '    sdk.add_ground()\n'
        f'    sdk.spawn_agent(({ax}, 18))\n'
        f'    sdk.add_box("crate_a", ({c1}, 20), size=(40, 40))\n'
        f'    sdk.add_box("crate_b", ({c2}, 20), size=(40, 40))\n'
        f'    sdk.add_zone("goal", ({gx}, {gy}), ({gw}, {gh}))'
    )
    return _MODULE.format(desc=repr(command), actions=_ACTIONS,
                          build=build, success=_CRATE_IN_GOAL)


def _climb(command, s):
    """Staircase of SOLID steps + a zone at the top.

    Thin high platforms are unreachable (jump clearance ~75 px, cf. SDK
    calibration): we emit solid steps resting on the ground, each rise <= 45 px
    — the pattern validated empirically by the example scene.
    """
    ax = _pick(s[0], 60, 100)
    rise = _pick(s[4], 38, 45)      # height of one step, below the jump clearance
    w = _pick(s[5], 100, 120)       # step width (comfortable foothold)
    x1 = _pick(s[1], 200, 240)
    x2, x3 = x1 + w, x1 + 2 * w
    gw = _pick(s[7], 80, 110)
    h1, h2, h3 = rise, 2 * rise, 3 * rise
    build = (
        '    sdk.add_ground()\n'
        f'    sdk.spawn_agent(({ax}, 19))\n'
        f'    sdk.add_box("step1", ({x1}, {h1 / 2}), size=({w}, {h1}), body="static", friction=0.9)\n'
        f'    sdk.add_box("step2", ({x2}, {h2 / 2}), size=({w}, {h2}), body="static", friction=0.9)\n'
        f'    sdk.add_box("step3", ({x3}, {h3 / 2}), size=({w}, {h3}), body="static", friction=0.9)\n'
        f'    sdk.add_zone("goal", ({x3}, {h3 + 30}), ({gw}, 60))'
    )
    return _MODULE.format(desc=repr(command), actions=_ACTIONS,
                          build=build, success=_AGENT_IN_GOAL)
