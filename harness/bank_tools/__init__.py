"""Bank-tools — the OFFLINE bank-grower for the v2 parts bank (ASSET_BANK_V2.md).

Nothing here runs in the generation loop. These are curator/CI utilities that
*author* certified v2 entries and materialise them into
``banks/parts/v2/parts.json``:

* ``migrate``   — mechanically re-view the 60 v1 nouns into the v2 schema
                  (``category`` -> ``physics_class`` + ``role``; derive ``volume``
                  from the assembly geometry; drop ``sprite:null`` for
                  ``render_binding``). Fidelity is the contract: every v1 physics
                  field is preserved verbatim, so a migrated part resolves and
                  certifies identically to its v1 twin.
* ``parametric``— a DETERMINISTIC generator for volume families
                  (boxes / discs / capsules / ramps / arcs / gates) across
                  sensible dimension ranges x roles. Zero external assets: the
                  bank grows to hundreds of certified entries with stable names
                  (``box_2x1``, ``ramp_30deg_4x2``) purely from geometry.
* ``build_v2``  — assemble migrated + parametric into one catalog dict, validate
                  it, and write the committed, content-hashed catalog + lock.

This module holds the shared vocabulary (role -> objective phrase, role ->
default role_contract, the style-neutral render_binding, volume derivation from
an assembly) both authors reuse so the two lanes emit byte-identical shapes.
"""

from __future__ import annotations

# Role -> one-line objective phrase (the runner-facing "what it does"), shown in
# the advisory menu. Mirrors the role table in ASSET_BANK_V2.md §3.
ROLE_OBJECTIVE = {
    "obstacle": "blocks the body",
    "platform": "static foothold to stand on",
    "hazard": "latches the failure flag on contact",
    "collectible": "collects (runner auto-wires on_contact + remove)",
    "goal": "reach-flag read by the success predicate",
    "gate": "static posts + a sensor span (checkpoint)",
    "mover": "behaviour-driven moving part",
    "movable": "pushable dynamic body",
    "vehicle": "natural controlled body (you still declare control)",
    "decor": "cosmetic only",
}

# Role -> the default machine-checkable role_contract (ASSET_BANK_V2.md §3/§5.4).
# A ramp adds "walkable_slope" on top of the platform base; callers extend as
# needed. Every token is in ``bank.ROLE_CONTRACT_TOKENS``.
ROLE_CONTRACT = {
    "obstacle": ["primary_static", "primary_non_sensor"],
    "platform": ["primary_static", "primary_non_sensor"],
    "hazard": ["primary_sensor"],
    "collectible": ["primary_sensor", "removable", "pairs_with_got_flag"],
    "goal": ["primary_sensor", "non_lethal"],
    "gate": ["posts_static", "span_sensor", "span_reach_flag"],
    "mover": ["primary_dynamic", "joint_present"],
    "movable": ["primary_dynamic", "pushable"],
    "vehicle": ["primary_dynamic", "controllable"],
    "decor": ["primary_sensor"],
}

_ROUND = 3  # decimals for every derived float (keeps generation byte-stable)


def render_binding() -> dict:
    """The style-neutral v2 render binding (replaces v1's ``sprite:null``).

    ``primitive_2d`` = draw a coloured primitive derived from the volume, hued by
    role. The 3D ``.tscn`` slot is reserved but null (flat-3D Path B deferred).
    """
    return {"primitive_2d": {"shape": "from_volume", "color_by": "role"},
            "tscn": None}


def _r(x):
    """Round to the shared precision, returning a clean int when the value is
    integral (so ``box_2x1`` sizes stay ``[40, 20]``, not ``[40.0, 20.0]``) and a
    rounded float otherwise. Deterministic -> two generations are byte-identical.
    """
    v = round(float(x), _ROUND)
    iv = int(v)
    return iv if v == iv else v


def _body_aabb(body: dict) -> tuple:
    """Axis-aligned (x0, y0, x1, y1) of one assembly body, offset included."""
    ox, oy = body.get("offset", [0, 0])
    shape = body["shape"]
    if shape == "box":
        w, h = body["size"]
        return (ox - w / 2, oy - h / 2, ox + w / 2, oy + h / 2)
    if shape == "circle":
        r = body["radius"]
        return (ox - r, oy - r, ox + r, oy + r)
    if shape == "segment":
        (ax, ay), (bx, by) = body["a"], body["b"]
        return (ox + min(ax, bx), oy + min(ay, by),
                ox + max(ax, bx), oy + max(ay, by))
    # poly
    xs = [ox + v[0] for v in body["vertices"]]
    ys = [oy + v[1] for v in body["vertices"]]
    return (min(xs), min(ys), max(xs), max(ys))


def _footprint_from_body(body: dict) -> dict:
    """A footprint_2d that mirrors a single body's own shape (offset assumed 0)."""
    shape = body["shape"]
    if shape == "box":
        return {"shape": "box", "size": [_r(body["size"][0]), _r(body["size"][1])]}
    if shape == "circle":
        return {"shape": "circle", "radius": _r(body["radius"])}
    if shape == "segment":
        return {"shape": "segment",
                "a": [_r(body["a"][0]), _r(body["a"][1])],
                "b": [_r(body["b"][0]), _r(body["b"][1])]}
    return {"shape": "poly", "vertices": [[_r(v[0]), _r(v[1])] for v in body["vertices"]]}


def derive_volume(assembly: list, primary: str) -> dict:
    """Derive ``volume`` from the assembly geometry (ASSET_BANK_V2.md §5.3).

    A single-body part mirrors its body's shape verbatim (so a cone stays a box,
    a coin stays a circle, a ramp stays a poly). A composite collapses to the
    axis-aligned bounding box over all sub-bodies — the footprint a collider
    attaches to, the load-bearing definition the certifier reads.
    """
    if len(assembly) == 1 and assembly[0].get("offset", [0, 0]) == [0, 0]:
        fp = _footprint_from_body(assembly[0])
    else:
        aabbs = [_body_aabb(b) for b in assembly]
        x0 = min(a[0] for a in aabbs)
        y0 = min(a[1] for a in aabbs)
        x1 = max(a[2] for a in aabbs)
        y1 = max(a[3] for a in aabbs)
        fp = {"shape": "box", "size": [_r(x1 - x0), _r(y1 - y0)]}
    return {"footprint_2d": fp, "glb": None}
