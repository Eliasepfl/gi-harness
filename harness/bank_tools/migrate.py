"""Mechanical v1 -> v2 migration of the 60 pre-certified nouns (ASSET_BANK_V2.md §5.3).

The one load-bearing move: split v1's single ``category`` axis into the v2
``physics_class`` (the CI floor, an identity rename) and ``role`` (the game
objective). ``volume`` is derived from the assembly geometry; ``sprite:null`` is
dropped for a style-neutral ``render_binding``. EVERYTHING physical — assembly,
joints, primary, control_candidate, behavior, overridable, invariants, cert — is
preserved verbatim, so a migrated part resolves and certifies bit-for-bit like
its v1 twin. Role for the handful of dual-use terrain nouns is a curated table
(the "human review, not a script" the note calls out), not a guess.
"""

from __future__ import annotations

import copy

from harness.bank_tools import ROLE_CONTRACT, derive_volume, render_binding

# Terrain that BLOCKS (obstacle) vs terrain you stand on (platform). The curated
# split for the dual-use nouns; every other terrain noun is a foothold.
_OBSTACLE_TERRAIN = frozenset({"wall", "pillar", "door_slab"})

# Trigger sub-role (behavior.role) -> v2 role. A pickup is a collectible; every
# other trigger zone (goal/checkpoint/target/switch) is a sensor reach-flag.
_TRIGGER_ROLE = {"pickup": "collectible"}

# The physics fields carried over verbatim from a v1 entry (fidelity contract).
_PRESERVED = ("summary", "tags", "assembly", "joints", "primary",
              "control_candidate", "behavior", "overridable", "invariants", "cert")


def _role_for(entry: dict) -> str:
    """Derive the v2 role from a v1 entry's category (+ behavior for triggers)."""
    cat = entry["category"]
    if cat == "terrain":
        return "obstacle" if entry["name"] in _OBSTACLE_TERRAIN else "platform"
    if cat == "prop":
        return "movable"
    if cat == "hazard":
        return "hazard"
    if cat == "mobile":
        return "mover"
    if cat == "trigger":
        sub = (entry.get("behavior") or {}).get("role")
        return _TRIGGER_ROLE.get(sub, "goal")
    return "decor"  # cat == "decor"


def _primary_body(entry: dict) -> dict:
    return next(b for b in entry["assembly"] if b["role"] == entry["primary"])


def migrate_entry(entry: dict) -> dict:
    """Re-view one v1 entry into the v2 schema (mechanical + curated role)."""
    role = _role_for(entry)
    contract = list(ROLE_CONTRACT[role])
    # A sloped, poly-footed platform (a ramp/wedge) promises a walkable slope.
    if role == "platform":
        pb = _primary_body(entry)
        if pb["shape"] == "poly" and len({v[1] for v in pb["vertices"]}) > 1:
            contract.append("walkable_slope")

    out = {
        "name": entry["name"],
        "physics_class": entry["category"],
        "role": role,
        "summary": entry["summary"],
        "tags": list(entry["tags"]),
        "volume": derive_volume(entry["assembly"], entry["primary"]),
        "assembly": copy.deepcopy(entry["assembly"]),
        "joints": copy.deepcopy(entry.get("joints", [])),
        "primary": entry["primary"],
        "control_candidate": entry.get("control_candidate"),
        "behavior": copy.deepcopy(entry.get("behavior")),
        "role_contract": contract,
        "overridable": copy.deepcopy(entry.get("overridable", {})),
        "invariants": list(entry.get("invariants", [])),
        "render_binding": render_binding(),
        "provenance": {**copy.deepcopy(entry["provenance"]),
                       "migrated_from": f"v1/{entry['name']}"},
        "cert": copy.deepcopy(entry.get("cert", {})),
    }
    return out


def migrate_bank(v1_data: dict) -> list:
    """Migrate every entry of a parsed v1 catalog; preserves catalog order."""
    return [migrate_entry(e) for e in v1_data["parts"]]
