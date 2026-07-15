"""Parts bank — loader, validator, content hasher, and resolver (CONTRACTS §9).

The bank is versioned DATA (``banks/parts/<version>/parts.json``), not code. This
module is the harness-side machinery that:

* loads and JSON-schema-ishly VALIDATES a bank catalog (``load_bank``);
* content-HASHES it for integrity pinning (``content_hash`` + ``bank.lock``);
* RESOLVES a bank entry + bounded overrides into concrete ``World.add``/joint
  calls (``resolve_part``), the thing ``World.part`` delegates to.

A "part" is a NOUN: a single calibrated body OR a pre-jointed subassembly
(anchor + ball + a correctly-anchored joint) whose physics were certified once,
offline, by ``harness.bank_ci``. The game keeps writing every VERB itself; the
bank only supplies objects that were already proven correct. See CONTRACTS §9.

Nothing here reads pixels, imports the engine, or mutates a World: the resolver
returns a plain plan of add/joint calls that ``World.part`` executes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os

# --- Vocabulary ---------------------------------------------------------- #
CATEGORIES = frozenset({"terrain", "prop", "hazard", "mobile", "trigger", "decor"})
SHAPES = frozenset({"box", "circle", "segment", "poly"})
JOINT_TYPES = frozenset({"pin", "pivot", "spring"})

# Category -> physical invariants a part MUST satisfy (mirrored, and ENFORCED at
# runtime, by harness.bank_ci). Kept here so validation can cross-check the
# static shape of each entry against its category before certification ever runs.
CATEGORY_INVARIANTS = {
    "terrain": ("all_static",),
    "prop": ("is_dynamic",),
    "hazard": ("is_sensor",),
    "mobile": ("joint_present",),
    "trigger": ("is_sensor",),
    "decor": (),
}

# --- v2 vocabulary (ASSET_BANK_V2.md §3) --------------------------------- #
# ``physics_class`` is the v1 ``category`` renamed: the CI FLOOR, keyed exactly
# like CATEGORY_INVARIANTS (static/dynamic/sensor/jointed). ``role`` is the added
# first-class objective the runner instantiates. The two split the one v1 axis.
PHYSICS_CLASSES = CATEGORIES
ROLES = frozenset({
    "obstacle", "platform", "hazard", "collectible", "goal", "gate",
    "mover", "movable", "vehicle", "decor",
})
FOOTPRINT_SHAPES = SHAPES

# Machine-checkable role_contract promises (the semantic CEILING above the
# physics_class floor). Each token has a DATA-level cross-check here (in
# ``_validate_role_contract``) and a live re-assertion in ``harness.bank_ci``.
ROLE_CONTRACT_TOKENS = frozenset({
    "primary_static", "primary_non_sensor", "primary_dynamic", "primary_sensor",
    "pushable", "removable", "pairs_with_got_flag", "non_lethal", "controllable",
    "walkable_slope", "joint_present", "posts_static", "span_sensor",
    "span_reach_flag",
})

_SCHEMA_VERSION = "1.0"
_SCHEMA_VERSION_V2 = "2.0"
_LOCK_NAME = "bank.lock"
_CATALOG_NAME = "parts.json"

# Module-level cache so many Worlds share one parsed+validated bank.
_CACHE: dict[str, "Bank"] = {}


# ======================================================================== #
# Errors (all ValueError subclasses so an out-of-whitelist override raised
# inside build() surfaces through the verifier's G0 as an ENV_ERROR).
# ======================================================================== #
class BankError(ValueError):
    """Base class for every parts-bank failure."""


class BankValidationError(BankError):
    """The catalog is malformed or violates a category invariant at load time."""


class BankOverrideError(BankError):
    """An override key is unknown or its value is out of the declared range."""


# ======================================================================== #
# Paths
# ======================================================================== #
def _repo_root() -> str:
    """Repo root = grandparent of this module's package dir (harness/core/)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def bank_dir(version: str = "v1") -> str:
    """Absolute directory of the bank catalog for ``version``."""
    return os.path.join(_repo_root(), "banks", "parts", version)


def catalog_path(version: str = "v1") -> str:
    return os.path.join(bank_dir(version), _CATALOG_NAME)


def lock_path(version: str = "v1") -> str:
    return os.path.join(bank_dir(version), _LOCK_NAME)


# ======================================================================== #
# Content hashing (integrity pinning)
# ======================================================================== #
def content_hash(data: dict) -> str:
    """SHA-256 over the CANONICAL serialization of the catalog.

    Canonical = sorted keys, no insignificant whitespace, ASCII — so the hash is
    stable across reformatting and reserialization (only the semantic content
    matters). This is the value pinned in ``bank.lock`` and, in a later wave,
    folded into ``integrity.snapshot`` so a mid-run bank change invalidates a run
    exactly like a base-code change.
    """
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ======================================================================== #
# Validation (JSON-schema-ish)
# ======================================================================== #
def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise BankValidationError(msg)


def _validate_body(part_name: str, spec: dict, roles: set) -> None:
    where = f"part {part_name!r} body {spec.get('role')!r}"
    role = spec.get("role")
    _require(isinstance(role, str) and role, f"{where}: missing 'role'")
    _require(role not in roles, f"{where}: duplicate role")
    roles.add(role)

    shape = spec.get("shape")
    _require(shape in SHAPES, f"{where}: shape {shape!r} not in {sorted(SHAPES)}")
    if shape == "box":
        _require(_is_vec2(spec.get("size")), f"{where}: box needs size=[w,h]")
    elif shape == "circle":
        _require(_is_num(spec.get("radius")), f"{where}: circle needs radius")
    elif shape == "segment":
        _require(_is_vec2(spec.get("a")) and _is_vec2(spec.get("b")),
                 f"{where}: segment needs a=[x,y] and b=[x,y]")
    elif shape == "poly":
        verts = spec.get("vertices")
        _require(isinstance(verts, list) and len(verts) >= 3
                 and all(_is_vec2(v) for v in verts),
                 f"{where}: poly needs >=3 [x,y] vertices")

    _require(_is_vec2(spec.get("offset", [0, 0])), f"{where}: offset must be [x,y]")
    for key in ("mass", "friction", "elasticity", "angle"):
        if key in spec:
            _require(_is_num(spec[key]), f"{where}: {key} must be a number")
    for key in ("static", "sensor", "locked_rotation"):
        if key in spec:
            _require(isinstance(spec[key], bool), f"{where}: {key} must be a bool")


def _validate_joint(part_name: str, spec: dict, roles: set) -> None:
    where = f"part {part_name!r} joint"
    jtype = spec.get("type")
    _require(jtype in JOINT_TYPES, f"{where}: type {jtype!r} not in {sorted(JOINT_TYPES)}")
    for end in ("a", "b"):
        _require(spec.get(end) in roles, f"{where}: {end}={spec.get(end)!r} is not a body role")
    if jtype in ("pin", "spring"):
        for anc in ("anchor_a", "anchor_b"):
            if anc in spec:
                _require(_is_vec2(spec[anc]), f"{where}: {anc} must be [x,y]")
    if jtype == "pivot":
        _require(_is_vec2(spec.get("point")), f"{where}: pivot needs point=[x,y] (offset from pos)")
    if jtype == "spring":
        for key in ("rest_length", "stiffness", "damping"):
            _require(_is_num(spec.get(key)), f"{where}: spring needs numeric {key}")


def _validate_overridable(part_name: str, over: dict, roles: set) -> None:
    _require(isinstance(over, dict), f"part {part_name!r}: overridable must be an object")
    for key, spec in over.items():
        where = f"part {part_name!r} override {key!r}"
        _require(isinstance(spec, dict), f"{where}: must be an object")
        rng = spec.get("range")
        _require(_is_vec2(rng) and rng[0] <= rng[1],
                 f"{where}: range must be [lo, hi] with lo<=hi")
        path = spec.get("path")
        if key == "scale":
            _require(path is None, f"{where}: scale takes no path (it is uniform)")
            continue
        _require(isinstance(path, str) and "." in path,
                 f"{where}: needs a 'role.field' path")
        role = path.split(".", 1)[0]
        _require(role in roles, f"{where}: path role {role!r} is not a body role")


def validate_bank(data: dict) -> None:
    """Validate a parsed catalog; raise BankValidationError on any problem.

    Dispatches on ``schema_version``: ``"1.0"`` -> the v1 shape (``category`` +
    ``sprite:null``); ``"2.0"`` -> the v2 shape (``physics_class`` + ``role`` +
    ``volume`` + ``role_contract`` + ``render_binding``). The two share the
    assembly/joints/overridable machinery verbatim (ASSET_BANK_V2.md §3).
    """
    _require(isinstance(data, dict), "catalog root must be an object")
    sv = data.get("schema_version")
    if sv == _SCHEMA_VERSION_V2:
        _validate_bank_v2(data)
        return
    _validate_bank_v1(data)


def _validate_bank_v1(data: dict) -> None:
    """Validate a parsed v1 catalog; raise BankValidationError on any problem."""
    _require(data.get("schema_version") == _SCHEMA_VERSION,
             f"schema_version must be {_SCHEMA_VERSION!r}")
    _require(isinstance(data.get("bank_version"), str) and data["bank_version"],
             "bank_version must be a non-empty string")
    parts = data.get("parts")
    _require(isinstance(parts, list) and parts, "'parts' must be a non-empty list")

    seen: set = set()
    for part in parts:
        _require(isinstance(part, dict), "each part must be an object")
        name = part.get("name")
        _require(isinstance(name, str) and name, "part missing 'name'")
        _require(name not in seen, f"duplicate part name {name!r}")
        seen.add(name)

        cat = part.get("category")
        _require(cat in CATEGORIES, f"part {name!r}: category {cat!r} not in {sorted(CATEGORIES)}")
        _require(isinstance(part.get("summary"), str) and part["summary"],
                 f"part {name!r}: missing 'summary'")
        _require(isinstance(part.get("tags"), list)
                 and all(isinstance(t, str) for t in part["tags"]),
                 f"part {name!r}: tags must be a list of strings")
        _require("sprite" in part and part["sprite"] is None,
                 f"part {name!r}: sprite must be null in v1 (sprites are lazy)")

        prov = part.get("provenance")
        _require(isinstance(prov, dict)
                 and all(k in prov for k in ("author", "license", "source")),
                 f"part {name!r}: provenance needs author/license/source")

        assembly = part.get("assembly")
        _require(isinstance(assembly, list) and assembly,
                 f"part {name!r}: assembly must be a non-empty list")
        roles: set = set()
        for body in assembly:
            _validate_body(name, body, roles)

        primary = part.get("primary")
        _require(primary in roles, f"part {name!r}: primary {primary!r} is not a body role")

        joints = part.get("joints", [])
        _require(isinstance(joints, list), f"part {name!r}: joints must be a list")
        for joint in joints:
            _validate_joint(name, joint, roles)

        _validate_overridable(name, part.get("overridable", {}), roles)

        # Category invariants at the DATA level (bank_ci re-checks on live bodies).
        by_role = {b["role"]: b for b in assembly}
        if cat == "terrain":
            _require(all(b.get("static") for b in assembly),
                     f"terrain part {name!r}: every body must be static")
        elif cat == "prop":
            _require(not by_role[primary].get("static", False),
                     f"prop part {name!r}: primary body must be dynamic")
        elif cat in ("hazard", "trigger"):
            _require(by_role[primary].get("sensor", False),
                     f"{cat} part {name!r}: primary body must be a sensor")
        elif cat == "mobile":
            _require(len(joints) >= 1, f"mobile part {name!r}: needs at least one joint")


# ======================================================================== #
# v2 validation (ASSET_BANK_V2.md §3) — superset of v1, forward-compatible.
# ======================================================================== #
def _footprint_extent(fp: dict) -> tuple:
    """(width, height) of a footprint_2d block (its axis-aligned bounding box)."""
    shape = fp.get("shape")
    if shape == "box":
        w, h = fp["size"]
        return float(w), float(h)
    if shape == "circle":
        d = 2.0 * float(fp["radius"])
        return d, d
    if shape == "segment":
        (ax, ay), (bx, by) = fp["a"], fp["b"]
        return abs(bx - ax), abs(by - ay)
    if shape == "poly":
        xs = [v[0] for v in fp["vertices"]]
        ys = [v[1] for v in fp["vertices"]]
        return max(xs) - min(xs), max(ys) - min(ys)
    return 0.0, 0.0


def _validate_footprint(name: str, fp: dict) -> None:
    where = f"part {name!r} volume.footprint_2d"
    _require(isinstance(fp, dict), f"{where}: must be an object")
    shape = fp.get("shape")
    _require(shape in FOOTPRINT_SHAPES,
             f"{where}: shape {shape!r} not in {sorted(FOOTPRINT_SHAPES)}")
    if shape == "box":
        sz = fp.get("size")
        _require(_is_vec2(sz) and sz[0] > 0 and sz[1] > 0,
                 f"{where}: box needs size=[w>0, h>0]")
    elif shape == "circle":
        r = fp.get("radius")
        _require(_is_num(r) and r > 0, f"{where}: circle needs radius>0")
    elif shape == "segment":
        _require(_is_vec2(fp.get("a")) and _is_vec2(fp.get("b")),
                 f"{where}: segment needs a=[x,y] and b=[x,y]")
    elif shape == "poly":
        verts = fp.get("vertices")
        _require(isinstance(verts, list) and len(verts) >= 3
                 and all(_is_vec2(v) for v in verts),
                 f"{where}: poly needs >=3 [x,y] vertices")
    # Volume sanity: a footprint must enclose positive area on both axes.
    w, h = _footprint_extent(fp)
    _require(w > 0 and h > 0, f"{where}: degenerate footprint (extent {w}x{h})")


def _validate_volume(name: str, vol: dict) -> None:
    where = f"part {name!r} volume"
    _require(isinstance(vol, dict), f"{where}: must be an object")
    _require("footprint_2d" in vol, f"{where}: missing 'footprint_2d'")
    _validate_footprint(name, vol["footprint_2d"])
    # The 3D slot is reserved but MUST stay null in v2 (Path B deferred, §4).
    _require(vol.get("glb", None) is None,
             f"{where}: glb must be null (flat-3D Path B is deferred)")


def _validate_render_binding(name: str, rb: dict) -> None:
    where = f"part {name!r} render_binding"
    _require(isinstance(rb, dict), f"{where}: must be an object")
    prim = rb.get("primitive_2d")
    _require(isinstance(prim, dict), f"{where}: primitive_2d must be an object")
    _require(isinstance(prim.get("shape"), str) and prim["shape"],
             f"{where}: primitive_2d.shape must be a non-empty string")
    _require(isinstance(prim.get("color_by"), str) and prim["color_by"],
             f"{where}: primitive_2d.color_by must be a non-empty string")
    # Style-neutral: the reserved .tscn slot stays null in v2 (Path B deferred).
    _require(rb.get("tscn", None) is None,
             f"{where}: tscn must be null (flat-3D Path B is deferred)")


def _validate_role_contract(name: str, contract, by_role: dict,
                            primary_body: dict, n_joints: int) -> None:
    """Cross-check each role_contract token against the STATIC entry shape.

    bank_ci re-asserts the physically-observable subset on live, settled bodies;
    this rejects a mis-declared promise at load time (ASSET_BANK_V2.md §5.4).
    """
    where = f"part {name!r} role_contract"
    _require(isinstance(contract, list)
             and all(isinstance(t, str) for t in contract),
             f"{where}: must be a list of strings")
    for tok in contract:
        _require(tok in ROLE_CONTRACT_TOKENS,
                 f"{where}: unknown token {tok!r}; "
                 f"allowed: {sorted(ROLE_CONTRACT_TOKENS)}")
    p = primary_body
    static = bool(p.get("static", False))
    sensor = bool(p.get("sensor", False))
    c = set(contract)
    if "primary_static" in c:
        _require(static, f"{where}: primary_static but primary is dynamic")
    if "primary_non_sensor" in c:
        _require(not sensor, f"{where}: primary_non_sensor but primary is a sensor")
    if "primary_dynamic" in c:
        _require(not static, f"{where}: primary_dynamic but primary is static")
    if "primary_sensor" in c:
        _require(sensor, f"{where}: primary_sensor but primary is not a sensor")
    if "pushable" in c:
        _require(not static and not sensor,
                 f"{where}: pushable needs a dynamic, non-sensor primary")
    if "controllable" in c:
        _require(not static, f"{where}: controllable needs a dynamic primary")
    if "removable" in c:
        _require(sensor, f"{where}: removable needs a sensor primary")
    if "pairs_with_got_flag" in c:
        _require(sensor, f"{where}: pairs_with_got_flag needs a sensor primary")
    if "non_lethal" in c:
        _require(sensor, f"{where}: non_lethal needs a sensor primary (no impulse)")
    if "joint_present" in c:
        _require(n_joints >= 1, f"{where}: joint_present but no joints declared")
    if "walkable_slope" in c:
        _require(p.get("shape") == "poly",
                 f"{where}: walkable_slope needs a poly primary")
        ys = [v[1] for v in p.get("vertices", [])]
        _require(len(set(ys)) > 1,
                 f"{where}: walkable_slope needs a sloped (non-flat) footprint")
    if "posts_static" in c:
        posts = [b for r, b in by_role.items() if str(r).startswith("post")]
        _require(posts and all(b.get("static") and not b.get("sensor")
                               for b in posts),
                 f"{where}: posts_static needs >=1 static, non-sensor post body")
    if "span_sensor" in c or "span_reach_flag" in c:
        span = by_role.get("span")
        _require(span is not None and span.get("sensor"),
                 f"{where}: span must be a sensor body")


def _validate_bank_v2(data: dict) -> None:
    """Validate a parsed v2 catalog; raise BankValidationError on any problem."""
    _require(data.get("schema_version") == _SCHEMA_VERSION_V2,
             f"schema_version must be {_SCHEMA_VERSION_V2!r}")
    _require(isinstance(data.get("bank_version"), str) and data["bank_version"],
             "bank_version must be a non-empty string")
    parts = data.get("parts")
    _require(isinstance(parts, list) and parts, "'parts' must be a non-empty list")

    seen: set = set()
    for part in parts:
        _require(isinstance(part, dict), "each part must be an object")
        name = part.get("name")
        _require(isinstance(name, str) and name, "part missing 'name'")
        _require(name not in seen, f"duplicate part name {name!r}")
        seen.add(name)

        pc = part.get("physics_class")
        _require(pc in PHYSICS_CLASSES,
                 f"part {name!r}: physics_class {pc!r} not in {sorted(PHYSICS_CLASSES)}")
        role = part.get("role")
        _require(role in ROLES,
                 f"part {name!r}: role {role!r} not in {sorted(ROLES)}")
        # v2 drops the v1 sprite:null hard-require in favour of render_binding.
        _require("sprite" not in part,
                 f"part {name!r}: v2 has no 'sprite' (use render_binding)")
        _require(isinstance(part.get("summary"), str) and part["summary"],
                 f"part {name!r}: missing 'summary'")
        _require(isinstance(part.get("tags"), list)
                 and all(isinstance(t, str) for t in part["tags"]),
                 f"part {name!r}: tags must be a list of strings")

        _require("volume" in part, f"part {name!r}: missing 'volume'")
        _validate_volume(name, part["volume"])
        _require("render_binding" in part, f"part {name!r}: missing 'render_binding'")
        _validate_render_binding(name, part["render_binding"])

        prov = part.get("provenance")
        _require(isinstance(prov, dict)
                 and all(k in prov for k in ("author", "license", "source")),
                 f"part {name!r}: provenance needs author/license/source")

        assembly = part.get("assembly")
        _require(isinstance(assembly, list) and assembly,
                 f"part {name!r}: assembly must be a non-empty list")
        roles: set = set()
        for body in assembly:
            _validate_body(name, body, roles)

        primary = part.get("primary")
        _require(primary in roles, f"part {name!r}: primary {primary!r} is not a body role")

        joints = part.get("joints", [])
        _require(isinstance(joints, list), f"part {name!r}: joints must be a list")
        for joint in joints:
            _validate_joint(name, joint, roles)

        _validate_overridable(name, part.get("overridable", {}), roles)

        # physics_class FLOOR at the DATA level (bank_ci re-checks on live bodies).
        by_role = {b["role"]: b for b in assembly}
        if pc == "terrain":
            _require(all(b.get("static") for b in assembly),
                     f"terrain part {name!r}: every body must be static")
        elif pc == "prop":
            _require(not by_role[primary].get("static", False),
                     f"prop part {name!r}: primary body must be dynamic")
        elif pc in ("hazard", "trigger"):
            _require(by_role[primary].get("sensor", False),
                     f"{pc} part {name!r}: primary body must be a sensor")
        elif pc == "mobile":
            _require(len(joints) >= 1, f"mobile part {name!r}: needs at least one joint")

        # role_contract SEMANTIC ceiling.
        _require("role_contract" in part, f"part {name!r}: missing 'role_contract'")
        _validate_role_contract(name, part["role_contract"], by_role,
                                by_role[primary], len(joints))


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _is_vec2(x) -> bool:
    return isinstance(x, (list, tuple)) and len(x) == 2 and all(_is_num(v) for v in x)


# ======================================================================== #
# The resolver: entry + overrides -> concrete add/joint plan
# ======================================================================== #
class ResolvedPart:
    """A concrete, engine-neutral plan produced from an entry + overrides.

    ``bodies`` — ordered list of ``{"name", "shape", "kwargs"}`` for World.add.
    ``joints`` — ordered list of ``{"verb", "args", "kwargs"}`` for pin/pivot/spring.
    ``primary`` — the world entity name the caller should treat as the handle.
    ``roles``   — role -> world entity name (for controlling a non-primary body).
    """

    __slots__ = ("bodies", "joints", "primary", "roles")

    def __init__(self, bodies, joints, primary, roles):
        self.bodies = bodies
        self.joints = joints
        self.primary = primary
        self.roles = roles


def _apply_overrides(entry: dict, overrides: dict) -> dict:
    """Validate overrides against the whitelist; return {key: float value}.

    Unknown keys and out-of-range values are REJECTED (design decision: a clear
    error teaches the model the bounds; silent clamping would hide intent). This
    is why ``density`` — never in any whitelist — is rejected outright.
    """
    allowed = entry.get("overridable", {})
    out: dict = {}
    for key, raw in overrides.items():
        if key not in allowed:
            raise BankOverrideError(
                f"unknown override {key!r} for part {entry['name']!r}; "
                f"allowed: {sorted(allowed) or '(none)'}")
        try:
            val = float(raw)
        except (TypeError, ValueError):
            raise BankOverrideError(
                f"override {key!r} for part {entry['name']!r} must be a number, got {raw!r}")
        lo, hi = allowed[key]["range"]
        if not (lo <= val <= hi):
            raise BankOverrideError(
                f"override {key}={val} out of range [{lo}, {hi}] for part {entry['name']!r}")
        out[key] = val
    return out


def _set_path(by_role: dict, path: str, value: float) -> None:
    """Assign ``value`` at a ``role.field`` or ``role.field[index]`` path."""
    role, _, field = path.partition(".")
    spec = by_role[role]
    if field.endswith("]"):
        base, idx = field[:-1].split("[")
        spec[base][int(idx)] = value
    else:
        spec[field] = value


def _scale_pt(pt, scale: float):
    return [pt[0] * scale, pt[1] * scale]


def _body_add_kwargs(spec: dict, pos, scale: float):
    """(shape, kwargs) for World.add from a resolved body spec at ``pos``/``scale``."""
    off = spec.get("offset", [0, 0])
    apos = (pos[0] + off[0] * scale, pos[1] + off[1] * scale)
    shape = spec["shape"]
    static = bool(spec.get("static", False))
    kwargs = {
        "pos": apos,
        "static": static,
        "sensor": bool(spec.get("sensor", False)),
        "friction": float(spec.get("friction", 0.7)),
        "elasticity": float(spec.get("elasticity", 0.3)),
        "locked_rotation": bool(spec.get("locked_rotation", False)),
        "angle": float(spec.get("angle", 0.0)),
    }
    if not static:
        kwargs["mass"] = float(spec.get("mass", 1.0))
    if shape == "box":
        kwargs["size"] = [spec["size"][0] * scale, spec["size"][1] * scale]
    elif shape == "circle":
        kwargs["radius"] = spec["radius"] * scale
    elif shape == "segment":
        kwargs["a"] = _scale_pt(spec["a"], scale)
        kwargs["b"] = _scale_pt(spec["b"], scale)
    elif shape == "poly":
        kwargs["vertices"] = [_scale_pt(v, scale) for v in spec["vertices"]]
    return shape, kwargs


def resolve_part(entry: dict, instance: str, pos, overrides: dict) -> ResolvedPart:
    """Turn a bank entry + bounded overrides into a concrete add/joint plan.

    The primary sub-body registers under the bare instance ``name``; every other
    sub-body registers under ``name.<role>``. Geometry, offsets, joint anchors and
    spring rest length all scale uniformly by ``scale`` (default 1.0).
    """
    ov = _apply_overrides(entry, overrides)
    scale = ov.get("scale", 1.0)

    assembly = copy.deepcopy(entry["assembly"])
    by_role = {b["role"]: b for b in assembly}
    for key, val in ov.items():
        if key == "scale":
            continue
        path = entry["overridable"][key].get("path")
        if path:
            _set_path(by_role, path, val)

    primary = entry["primary"]

    def name_of(role: str) -> str:
        return instance if role == primary else f"{instance}.{role}"

    roles = {b["role"]: name_of(b["role"]) for b in assembly}

    bodies = []
    for spec in assembly:
        shape, kwargs = _body_add_kwargs(spec, pos, scale)
        bodies.append({"name": name_of(spec["role"]), "shape": shape, "kwargs": kwargs})

    joints = []
    for jspec in entry.get("joints", []):
        a, b = roles[jspec["a"]], roles[jspec["b"]]
        jtype = jspec["type"]
        if jtype == "pin":
            joints.append({"verb": "pin", "args": (a, b), "kwargs": {
                "anchor_a": _scale_pt(jspec.get("anchor_a", [0, 0]), scale),
                "anchor_b": _scale_pt(jspec.get("anchor_b", [0, 0]), scale)}})
        elif jtype == "pivot":
            pt = jspec["point"]
            world_pt = (pos[0] + pt[0] * scale, pos[1] + pt[1] * scale)
            joints.append({"verb": "pivot", "args": (a, b), "kwargs": {"point": world_pt}})
        elif jtype == "spring":
            joints.append({"verb": "spring", "args": (a, b), "kwargs": {
                "rest_length": jspec["rest_length"] * scale,
                "stiffness": jspec["stiffness"],
                "damping": jspec["damping"],
                "anchor_a": _scale_pt(jspec.get("anchor_a", [0, 0]), scale),
                "anchor_b": _scale_pt(jspec.get("anchor_b", [0, 0]), scale)}})

    return ResolvedPart(bodies, joints, name_of(primary), roles)


# ======================================================================== #
# Bank object + loader
# ======================================================================== #
class Bank:
    """A loaded, validated catalog with its content hash and (optional) lock."""

    def __init__(self, version: str, data: dict, digest: str, lock: dict | None):
        self.version = version
        self.data = data
        self.bank_version = data.get("bank_version")
        self.parts = {p["name"]: p for p in data["parts"]}
        self.content_hash = digest
        self.lock = lock

    @property
    def schema_version(self) -> str:
        return self.data.get("schema_version", _SCHEMA_VERSION)

    @property
    def is_v2(self) -> bool:
        return self.schema_version == _SCHEMA_VERSION_V2

    def names(self) -> list[str]:
        return list(self.parts)

    def by_category(self, category: str) -> list[str]:
        return [n for n, p in self.parts.items() if p.get("category") == category]

    def by_physics_class(self, physics_class: str) -> list[str]:
        return [n for n, p in self.parts.items()
                if p.get("physics_class") == physics_class]

    def by_role(self, role: str) -> list[str]:
        return [n for n, p in self.parts.items() if p.get("role") == role]

    def get(self, name: str) -> dict:
        if name not in self.parts:
            raise BankError(
                f"unknown part {name!r}; known parts: {sorted(self.parts)}")
        return self.parts[name]

    @property
    def hash_ok(self) -> bool:
        """True if no lock is present or the lock's hash matches the catalog."""
        return self.lock is None or self.lock.get("content_hash") == self.content_hash

    def resolve(self, kind: str, instance: str, pos, overrides: dict) -> ResolvedPart:
        return resolve_part(self.get(kind), instance, pos, overrides)


def load_bank(version: str = "v1", *, verify_hash: bool = False,
              use_cache: bool = True) -> Bank:
    """Load, validate, and hash the bank catalog for ``version``.

    ``verify_hash=True`` raises if a ``bank.lock`` is present and its hash does
    not match the catalog (integrity pin). ``use_cache`` shares one parsed bank
    across Worlds in the same process.
    """
    if use_cache and version in _CACHE:
        return _CACHE[version]

    path = catalog_path(version)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise BankError(f"no bank catalog at {path}") from exc
    except json.JSONDecodeError as exc:
        raise BankValidationError(f"catalog {path} is not valid JSON: {exc}") from exc

    validate_bank(data)
    digest = content_hash(data)

    lock = None
    lpath = lock_path(version)
    if os.path.isfile(lpath):
        try:
            with open(lpath, "r", encoding="utf-8") as fh:
                lock = json.load(fh)
        except (OSError, json.JSONDecodeError):
            lock = None
    if verify_hash and lock is not None and lock.get("content_hash") != digest:
        raise BankValidationError(
            f"bank {version!r} content hash {digest} does not match "
            f"pinned lock hash {lock.get('content_hash')}")

    bank = Bank(version, data, digest, lock)
    if use_cache:
        _CACHE[version] = bank
    return bank


def write_lock(version: str = "v1") -> dict:
    """(Re)compute ``bank.lock`` for ``version`` from the current catalog.

    Utility for bank authoring/certification: it re-derives the pinned hash and
    writes ``bank.lock``. Not called on the hot path.
    """
    bank = load_bank(version, use_cache=False)
    lock = {"schema_version": bank.schema_version,
            "bank_version": bank.bank_version,
            "content_hash": bank.content_hash,
            "n_parts": len(bank.parts)}
    with open(lock_path(version), "w", encoding="utf-8") as fh:
        json.dump(lock, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return lock


def clear_cache() -> None:
    """Drop the module-level bank cache (tests that reload a mutated catalog)."""
    _CACHE.clear()
