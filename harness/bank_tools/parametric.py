"""PARAMETRIC volume families — hundreds of certified entries, zero external assets.

The v2 bank must NOT depend on a kit download to be useful: "just volumes is
fine" (ASSET_BANK_V2.md DECISION 3b). This module is a pure, DETERMINISTIC
generator that emits one certified entry per (family, dimensions) cell across
box / disc / capsule / ramp / arc / gate families crossed with the roles each
shape can honour. Names are stable and geometry-derived (``box_2x1``,
``ramp_27deg_4x2``), so two generations are byte-identical and a name always
denotes the same volume.

Every field is chosen so ``harness.bank_ci`` certifies the entry on the live
settle-grid: static families spawn free (no ground, no motion), dynamic families
spawn resting on the CI ground so they settle without escaping or penetrating,
and every ``role_contract`` is satisfied by construction.
"""

from __future__ import annotations

import math

from harness.bank_tools import ROLE_CONTRACT, _r, derive_volume, render_binding

UNIT = 20            # px per grid cell for box/ramp/arc/gate linear dims
DISC_UNIT = 12       # px per radius step for discs / capsule radii
_GROUND_TOP = 20.0   # top edge of the bank_ci test ground (box at (400,10), h=20)

# Physics_class -> free-form invariant list (the certifier re-checks the floor;
# this is the declared, human-readable promise, mirroring the v1 style).
_INVARIANTS = {
    "terrain": ["all_static", "stays_put"],
    "prop": ["is_dynamic"],
    "hazard": ["is_sensor", "lethal"],
    "trigger": ["is_sensor", "non_lethal"],
    "decor": ["is_sensor", "cosmetic"],
    "mobile": ["joint_present"],
}

# Role -> behavior hook the frozen runner reads (advisory; None for inert bodies).
_BEHAVIOR = {
    "collectible": {"kind": "trigger", "role": "pickup"},
    "goal": {"kind": "trigger", "role": "goal"},
    "gate": {"kind": "trigger", "role": "checkpoint"},
}

_SCALE_OV = {"scale": {"range": [0.5, 2.0]}}
_DYNAMIC_OV = {"mass": {"range": [0.2, 8.0], "path": "body.mass"},
               "friction": {"range": [0.0, 1.0], "path": "body.friction"}}


def _static_body(shape, **geom):
    return {"role": "body", "shape": shape, "offset": [0, 0], "static": True,
            "sensor": False, "friction": 0.9, "elasticity": 0.0, **geom}


def _sensor_body(shape, **geom):
    return {"role": "body", "shape": shape, "offset": [0, 0], "static": True,
            "sensor": True, "friction": 0.0, "elasticity": 0.0, **geom}


def _dynamic_body(shape, **geom):
    return {"role": "body", "shape": shape, "offset": [0, 0], "static": False,
            "sensor": False, "friction": 0.6, "elasticity": 0.0, "mass": 1.0, **geom}


def _entry(name, physics_class, role, summary, tags, assembly, *,
           cert, overridable, extra_contract=()):
    contract = list(ROLE_CONTRACT[role]) + list(extra_contract)
    return {
        "name": name,
        "physics_class": physics_class,
        "role": role,
        "summary": summary,
        "tags": tags,
        "volume": derive_volume(assembly, "body" if len(assembly) == 1 else "span"),
        "assembly": assembly,
        "joints": [],
        "primary": "body" if len(assembly) == 1 else "span",
        "control_candidate": "body" if len(assembly) == 1 else "span",
        "behavior": _BEHAVIOR.get(role),
        "role_contract": contract,
        "overridable": overridable,
        "invariants": _INVARIANTS[physics_class],
        "render_binding": render_binding(),
        "provenance": {"author": "harness-parametric", "license": "CC0-1.0",
                       "source": f"parametric/{name}"},
        "cert": cert,
    }


def _rest_cert(footprint_h):
    """Spawn a dynamic body resting on the CI ground (bottom ~1px above it)."""
    return {"pos": [400, _r(_GROUND_TOP + footprint_h / 2.0 + 2)], "ground": True}


_FREE_CERT = {"pos": [400, 300], "ground": False}


# --------------------------------------------------------------------------- #
# Grids (explicit tuples -> deterministic iteration order)
# --------------------------------------------------------------------------- #
_BOX_W = (1, 2, 3, 4, 5, 6, 8, 10, 12)
_BOX_H = (1, 2, 3, 4, 5, 6)
_PLAT_W = (2, 3, 4, 5, 6, 8, 10, 12, 14)
_PLAT_H = (1, 2, 3)
_CRATE_D = (1, 2, 3, 4)
_BALL_R = (1, 2, 3, 4, 5, 6, 8)
_COIN_R = (1, 2, 3, 4)
_ORB_R = (1, 2, 3, 4)
_CAP_L = (2, 3, 4, 5, 6)
_CAP_R = (1, 2, 3)
_LOZ_L = (3, 4, 5, 6, 8, 10)
_LOZ_R = (1, 2, 3)
_RAMP_W = (2, 3, 4, 5, 6, 8, 10)
_ARC_DEG = (60, 90, 120, 150)
_ARC_R = (2, 3, 4, 6, 8)
_GATE_W = (6, 8, 10)
_GATE_H = (4, 6, 8)


def _boxes():
    out = []
    for w in _BOX_W:
        for h in _BOX_H:
            out.append(_entry(
                f"box_{w}x{h}", "terrain", "obstacle",
                f"solid static block, {w}x{h} units; a wall/step/pillar volume",
                ["box", "block", "static", "obstacle"],
                [_static_body("box", size=[w * UNIT, h * UNIT])],
                cert=_FREE_CERT, overridable=_SCALE_OV))
    return out


def _platforms():
    out = []
    for w in _PLAT_W:
        for h in _PLAT_H:
            out.append(_entry(
                f"platform_{w}x{h}", "terrain", "platform",
                f"static foothold slab, {w}x{h} units; stand/land on it",
                ["platform", "slab", "static", "foothold"],
                [_static_body("box", size=[w * UNIT, h * UNIT])],
                cert=_FREE_CERT, overridable=_SCALE_OV))
    return out


def _crates():
    out = []
    for w in _CRATE_D:
        for h in _CRATE_D:
            if max(w, h) > 3 * min(w, h):
                continue  # keep aspect <=3:1 so it settles upright, no toppling
            fh = h * UNIT
            out.append(_entry(
                f"crate_{w}x{h}", "prop", "movable",
                f"pushable dynamic box, {w}x{h} units",
                ["crate", "box", "dynamic", "pushable"],
                [_dynamic_body("box", size=[w * UNIT, h * UNIT])],
                cert=_rest_cert(fh), overridable=_DYNAMIC_OV))
    return out


def _balls():
    out = []
    for r in _BALL_R:
        rad = r * DISC_UNIT
        out.append(_entry(
            f"ball_r{r}", "prop", "movable",
            f"pushable dynamic disc, radius {r} ({rad}px)",
            ["ball", "disc", "circle", "dynamic", "pushable"],
            [_dynamic_body("circle", radius=rad)],
            cert=_rest_cert(2 * rad), overridable=_DYNAMIC_OV))
    return out


def _coins():
    out = []
    for r in _COIN_R:
        rad = r * DISC_UNIT
        out.append(_entry(
            f"coin_r{r}", "trigger", "collectible",
            f"collectible sensor disc, radius {r} ({rad}px)",
            ["coin", "collectible", "pickup", "sensor", "disc"],
            [_sensor_body("circle", radius=rad)],
            cert=_FREE_CERT, overridable=_SCALE_OV))
    return out


def _orbs():
    out = []
    for r in _ORB_R:
        rad = r * DISC_UNIT
        out.append(_entry(
            f"orb_r{r}", "decor", "decor",
            f"cosmetic sensor disc, radius {r} ({rad}px)",
            ["orb", "decor", "cosmetic", "sensor", "disc"],
            [_sensor_body("circle", radius=rad)],
            cert=_FREE_CERT, overridable=_SCALE_OV))
    return out


def _capsule_verts(length_px, radius_px):
    """Convex chamfered-rectangle (octagon) stadium with a flat top and bottom."""
    hx = length_px / 2.0
    hy = float(radius_px)
    c = hy * 0.5  # corner chamfer
    return [[_r(hx), _r(hy - c)], [_r(hx - c), _r(hy)],
            [_r(-(hx - c)), _r(hy)], [_r(-hx), _r(hy - c)],
            [_r(-hx), _r(-(hy - c))], [_r(-(hx - c)), _r(-hy)],
            [_r(hx - c), _r(-hy)], [_r(hx), _r(-(hy - c))]]


def _capsules():
    out = []
    for l in _CAP_L:
        for r in _CAP_R:
            length_px, radius_px = l * UNIT, r * DISC_UNIT
            if length_px - 2 * (radius_px * 0.5) <= 8:
                continue  # flat bottom edge must have real width
            out.append(_entry(
                f"capsule_{l}x{r}", "prop", "movable",
                f"pushable dynamic capsule, {l}x{r} units",
                ["capsule", "pill", "dynamic", "pushable", "poly"],
                [_dynamic_body("poly", vertices=_capsule_verts(length_px, radius_px))],
                cert=_rest_cert(2 * radius_px), overridable=_DYNAMIC_OV))
    return out


def _lozenges():
    out = []
    for l in _LOZ_L:
        for r in _LOZ_R:
            length_px, radius_px = l * UNIT, r * DISC_UNIT
            out.append(_entry(
                f"lozenge_{l}x{r}", "terrain", "platform",
                f"static rounded platform (capsule footprint), {l}x{r} units",
                ["lozenge", "capsule", "platform", "static", "poly"],
                [_static_body("poly", vertices=_capsule_verts(length_px, radius_px))],
                cert=_FREE_CERT, overridable=_SCALE_OV))
    return out


def _ramps():
    out = []
    for w in _RAMP_W:
        for h in range(1, min(w, 5) + 1):
            w_px, h_px = w * UNIT, h * UNIT
            deg = int(round(math.degrees(math.atan2(h, w))))
            verts = [[_r(-w_px / 2.0), _r(-h_px / 2.0)],
                     [_r(w_px / 2.0), _r(-h_px / 2.0)],
                     [_r(w_px / 2.0), _r(h_px / 2.0)]]
            out.append(_entry(
                f"ramp_{deg}deg_{w}x{h}", "terrain", "platform",
                f"walkable sloped ramp ~{deg}deg, {w}x{h} units",
                ["ramp", "slope", "incline", "static", "walkable"],
                [_static_body("poly", vertices=verts)],
                cert=_FREE_CERT, overridable=_SCALE_OV,
                extra_contract=("walkable_slope",)))
    return out


def _arc_verts(radius_px, deg, samples=6):
    """Convex circular-segment ('dome') poly: a chord at y=0, arc bulging up."""
    a = math.radians(deg / 2.0)
    base = radius_px * math.cos(a)
    verts = []
    for i in range(samples):
        phi = -a + (2 * a) * i / (samples - 1)
        verts.append([_r(radius_px * math.sin(phi)),
                      _r(radius_px * math.cos(phi) - base)])
    return verts


def _arcs():
    out = []
    for deg in _ARC_DEG:
        for r in _ARC_R:
            radius_px = r * UNIT
            out.append(_entry(
                f"arc_{deg}deg_r{r}", "terrain", "obstacle",
                f"convex curved bumper (~{deg}deg circular segment), radius {r} units",
                ["arc", "curve", "bumper", "dome", "static", "obstacle"],
                [_static_body("poly", vertices=_arc_verts(radius_px, deg))],
                cert=_FREE_CERT, overridable=_SCALE_OV))
    return out


def _gates():
    out = []
    post_w = 12
    for w in _GATE_W:
        for h in _GATE_H:
            w_px, h_px = w * UNIT, h * UNIT
            half = w_px / 2.0
            assembly = [
                {"role": "post_l", "shape": "box", "offset": [_r(-half), 0],
                 "static": True, "sensor": False, "friction": 0.9,
                 "elasticity": 0.0, "size": [post_w, h_px]},
                {"role": "post_r", "shape": "box", "offset": [_r(half), 0],
                 "static": True, "sensor": False, "friction": 0.9,
                 "elasticity": 0.0, "size": [post_w, h_px]},
                {"role": "span", "shape": "box", "offset": [0, 0],
                 "static": True, "sensor": True, "friction": 0.0,
                 "elasticity": 0.0, "size": [_r(w_px - post_w), h_px]},
            ]
            out.append(_entry(
                f"gate_{w}x{h}", "trigger", "gate",
                f"checkpoint gate: two static posts + a sensor span, {w}x{h} units",
                ["gate", "checkpoint", "posts", "span", "composite"],
                assembly, cert=_FREE_CERT, overridable=_SCALE_OV))
    return out


_FAMILIES = (_boxes, _platforms, _crates, _balls, _coins, _orbs,
             _capsules, _lozenges, _ramps, _arcs, _gates)


def generate_parametric() -> list:
    """Every parametric entry, in a fixed family/grid order (deterministic)."""
    out: list = []
    for family in _FAMILIES:
        out.extend(family())
    return out
