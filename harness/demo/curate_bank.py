"""Curate the real-3D-asset demo bank.

This is the *reproducible rebuild script* for the asset bank. It copies a
license-recorded selection of ``.glb`` / ``.gltf`` files out of a local
``godot_rl_agents_examples`` checkout into the bank directory under normalized
kebab-case ids, and writes ``manifest.json`` with per-asset provenance,
description and tags.

The heavy binary meshes live under ``<bank>/models/`` which is *gitignored*; the
manifest (``<bank>/manifest.json``) and this script are committed, so the bank is
fully reproducible from the (public) source repo alone -- no network required.

Two phases:

* ``curate`` (default) -- copy assets + write manifest (dimensions left null).
* ``merge-dims`` -- fold the Godot-measured AABBs (see ``measure_aabb.gd``) back
  into the manifest and derive a suggested collision primitive per asset.

Assets are RENDER-ONLY. Nothing here authors physics/collision for the game; the
``collision`` block is an *advisory suggestion* for a dresser that might want a
proxy. The generated game always keeps its own physics (see notes/engines/ASSET_BANK.md).

Usage
-----
    python -m harness.demo.curate_bank --src <repo> --bank <bankdir>
    python -m harness.demo.curate_bank --bank <bankdir> --merge-dims aabb.json
    python -m harness.demo.curate_bank --bank <bankdir> --list

Licenses are recorded honestly. Where the source example ships no license file
and none is embedded, the license is recorded as ``"unknown"`` (see README).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Provenance constants
# ---------------------------------------------------------------------------

SOURCE_REPO = "godot_rl_agents_examples"
SOURCE_REPO_URL = "https://github.com/edbeeching/godot_rl_agents_examples"

# Attribution strings for CC-BY sources (kept verbatim so re-audit is trivial).
ATTR_IVAN = (
    "Graphical assets by Ivan Dodic (https://github.com/Ivan-267), "
    "licensed CC-BY-4.0 (https://creativecommons.org/licenses/by/4.0/)."
)
ATTR_ANTONMOEK = (
    '"Cartoon Plane" by antonmoek (https://sketchfab.com/antonmoek), '
    "licensed CC-BY-4.0 (http://creativecommons.org/licenses/by/4.0/)."
)


@dataclass(frozen=True)
class AssetSpec:
    """One curated bank entry, resolved from the source repo at curate time."""

    id: str
    src: str  # path relative to the source repo root
    description: str
    tags: tuple[str, ...]
    archetype: str
    license: str = "unknown"
    attribution: Optional[str] = None


# ---------------------------------------------------------------------------
# THE CURATED SELECTION
# ---------------------------------------------------------------------------
# Chosen for archetype coverage (vehicle / tree / crate / ball / robot + track,
# goal, platform, prop, marker). One object per file; whole-level "map" scenes
# and multi-object "cars"/"forest" collections are deliberately excluded.
ASSET_TABLE: tuple[AssetSpec, ...] = (
    # --- CC-BY-4.0, Ivan Dodic ------------------------------------------------
    AssetSpec("car", "examples/CrossTheRoad/Completed/assets/car.glb",
              "Low-poly car body", ("vehicle", "car", "automobile", "cart"),
              "vehicle", "CC-BY-4.0", ATTR_IVAN),
    AssetSpec("robot", "examples/CrossTheRoad/Completed/assets/robot.glb",
              "Boxy walking robot character", ("robot", "character", "agent", "bot", "mech"),
              "robot", "CC-BY-4.0", ATTR_IVAN),
    AssetSpec("robot-drone", "examples/MultiAgentSimple/assets/robot.glb",
              "Small hovering robot / drone", ("robot", "drone", "bot", "agent", "flyer"),
              "robot", "CC-BY-4.0", ATTR_IVAN),
    AssetSpec("tree-tile", "examples/CrossTheRoad/Completed/assets/tree_tile.glb",
              "Stylized tree on a ground tile", ("tree", "plant", "foliage", "nature", "bush"),
              "tree", "CC-BY-4.0", ATTR_IVAN),
    AssetSpec("road-tile", "examples/CrossTheRoad/Completed/assets/road_tile.glb",
              "Flat road / lane tile", ("road", "track", "ground", "tile", "floor", "lane"),
              "track", "CC-BY-4.0", ATTR_IVAN),
    AssetSpec("goal-tile", "examples/CrossTheRoad/Completed/assets/goal_tile.glb",
              "Goal / finish tile marker", ("goal", "finish", "target", "tile", "checkpoint"),
              "goal", "CC-BY-4.0", ATTR_IVAN),
    AssetSpec("turret", "examples/DefendTheGoal/assets/turret.glb",
              "Defensive turret / tower", ("turret", "tower", "cannon", "defense", "prop"),
              "prop", "CC-BY-4.0", ATTR_IVAN),
    AssetSpec("goal-net", "examples/DefendTheGoal/assets/goal.glb",
              "Sports goal net", ("goal", "net", "target", "soccer", "score"),
              "goal", "CC-BY-4.0", ATTR_IVAN),
    AssetSpec("launcher", "examples/RobotFPS/assets/launcher.glb",
              "Projectile launcher / weapon prop", ("launcher", "weapon", "cannon", "shooter", "gun", "prop"),
              "prop", "CC-BY-4.0", ATTR_IVAN),
    AssetSpec("platform", "examples/MultiAgentSimple/assets/platform.glb",
              "Square floor platform", ("platform", "floor", "ground", "pad", "tile"),
              "platform", "CC-BY-4.0", ATTR_IVAN),
    AssetSpec("flying-platform", "examples/MultiAgentSimple/assets/flying_platform.glb",
              "Floating platform pad", ("platform", "floating", "pad", "hover", "flyingplatform"),
              "platform", "CC-BY-4.0", ATTR_IVAN),
    # --- CC-BY-4.0, antonmoek (Sketchfab), multi-file gltf --------------------
    AssetSpec("plane", "examples/FlyBy/cartoon_plane/scene.gltf",
              "Cartoon propeller airplane", ("plane", "aircraft", "airplane", "vehicle", "flyer", "jet"),
              "vehicle", "CC-BY-4.0", ATTR_ANTONMOEK),
    # --- Racer kit (Kenney-derived per repo notes; no license file bundled) ---
    AssetSpec("jeep", "examples/Racer/assets/glb_files/green_jeep.glb",
              "Green off-road jeep", ("vehicle", "jeep", "car", "truck", "suv", "cart"),
              "vehicle", "unknown", None),
    AssetSpec("convertible", "examples/Racer/assets/glb_files/red_convertible.glb",
              "Red convertible car", ("vehicle", "car", "convertible", "automobile", "cart"),
              "vehicle", "unknown", None),
    AssetSpec("tree", "examples/Racer/assets/glb_files/tree.glb",
              "Low-poly single tree", ("tree", "plant", "foliage", "nature", "pine"),
              "tree", "unknown", None),
    AssetSpec("rock", "examples/Racer/assets/glb_files/rock.glb",
              "Rounded boulder / rock", ("rock", "boulder", "stone", "ball", "round", "sphere", "obstacle"),
              "ball", "unknown", None),
    AssetSpec("waypoint", "examples/Racer/assets/glb_files/waypoint.glb",
              "Waypoint / checkpoint marker", ("waypoint", "marker", "checkpoint", "ring", "gate", "flag"),
              "marker", "unknown", None),
    AssetSpec("track-piece-1", "examples/Racer/assets/kenny_track1.glb",
              "Racing track segment (Kenney Racing Kit)", ("track", "road", "circuit", "course", "tile"),
              "track", "unknown", None),
    AssetSpec("track-piece-2", "examples/Racer/assets/kenny_track2.glb",
              "Racing track corner segment (Kenney Racing Kit)", ("track", "road", "circuit", "corner", "tile"),
              "track", "unknown", None),
    # --- Ships kit (no license file bundled), multi-file gltf -----------------
    AssetSpec("chest", "examples/Ships/assets/chest.gltf",
              "Treasure chest / crate box", ("chest", "crate", "box", "container", "treasure", "cargo", "block"),
              "crate", "unknown", None),
    AssetSpec("ship", "examples/Ships/assets/ship_light.gltf",
              "Small sailing ship / boat", ("ship", "boat", "vessel", "vehicle", "sailboat"),
              "vehicle", "unknown", None),
)

MANIFEST_VERSION = 1
_URI_RE = re.compile(rb'"uri"\s*:\s*"([^"]+)"')


# ---------------------------------------------------------------------------
# Curate phase
# ---------------------------------------------------------------------------

def _gltf_external_uris(gltf_path: Path) -> list[str]:
    """Relative sibling files a .gltf references via ``uri`` (its .bin, images).

    data: URIs (embedded) are skipped. Only local relative paths are returned.
    """
    data = gltf_path.read_bytes()
    out: list[str] = []
    for m in _URI_RE.finditer(data):
        uri = m.group(1).decode("utf-8")
        if uri.startswith("data:") or "://" in uri:
            continue
        if uri not in out:
            out.append(uri)
    return out


def _copy_asset(spec: AssetSpec, src_root: Path, models_dir: Path) -> str:
    """Copy one asset into ``models/`` and return its bank-relative file path."""
    src = src_root / spec.src
    if not src.is_file():
        raise FileNotFoundError(f"asset '{spec.id}': missing source {src}")
    ext = src.suffix.lower()
    if ext == ".glb":
        dst = models_dir / f"{spec.id}.glb"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return f"models/{spec.id}.glb"
    if ext == ".gltf":
        # Multi-file: keep the original filenames inside a per-id subdir so the
        # gltf's internal relative uris still resolve.
        sub = models_dir / spec.id
        sub.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, sub / src.name)
        for uri in _gltf_external_uris(src):
            sib = (src.parent / uri).resolve()
            if not sib.is_file():
                raise FileNotFoundError(
                    f"asset '{spec.id}': gltf references missing sibling {sib}")
            dst_sib = sub / uri
            dst_sib.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sib, dst_sib)
        return f"models/{spec.id}/{src.name}"
    raise ValueError(f"asset '{spec.id}': unsupported extension {ext}")


def build_manifest(src_root: Path, bank_dir: Path) -> dict:
    """Copy every asset and return the manifest dict (dimensions left null)."""
    models_dir = bank_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    for spec in ASSET_TABLE:
        rel = _copy_asset(spec, src_root, models_dir)
        fmt = "gltf" if rel.endswith(".gltf") else "glb"
        entry = {
            "id": spec.id,
            "file": rel,
            "format": fmt,
            "archetype": spec.archetype,
            "description": spec.description,
            "tags": list(spec.tags),
            "license": spec.license,
            "attribution": spec.attribution,
            "source": {
                "repo": SOURCE_REPO,
                "url": SOURCE_REPO_URL,
                "path": spec.src,
            },
            "dimensions": None,   # filled by merge-dims
            "collision": None,    # advisory; filled by merge-dims
        }
        entries.append(entry)

    licenses = sorted({e["license"] for e in entries})
    return {
        "schema": "gi-asset-bank",
        "version": MANIFEST_VERSION,
        "generated_by": "harness/demo/curate_bank.py",
        "source_repo": SOURCE_REPO,
        "source_repo_url": SOURCE_REPO_URL,
        "render_only": True,
        "note": (
            "Assets are RENDER-ONLY cosmetic dressing. Physics/collision belongs "
            "to the generated game; the per-asset 'collision' block is an advisory "
            "primitive suggestion only. Licenses recorded as-found; 'unknown' means "
            "the source example shipped no license file and none was embedded."
        ),
        "licenses_present": licenses,
        "count": len(entries),
        "assets": entries,
    }


# ---------------------------------------------------------------------------
# Dimension / collision merge phase
# ---------------------------------------------------------------------------

def suggest_collision(size: list[float], tags: list[str]) -> dict:
    """Advisory collision primitive from AABB size + semantic tags.

    Deterministic. Not used for game physics -- purely a hint for a dresser that
    wants a cheap render/proxy shape.
    """
    w, h, d = (max(float(v), 1e-6) for v in size)
    # A cube-ish AABB is far more likely a box than a sphere, so only an explicit
    # round/ball tag forces a sphere; shape alone never does.
    roundish = any(t in tags for t in ("ball", "round", "sphere"))
    if roundish:
        r = max(w, h, d) / 2.0
        return {"primitive": "sphere", "radius": round(r, 6)}
    footprint = max(w, d)
    if h > 1.6 * footprint and min(w, d) / max(w, d) > 0.55:
        return {"primitive": "capsule",
                "radius": round(footprint / 2.0, 6),
                "height": round(h, 6)}
    return {"primitive": "box",
            "half_extents": [round(w / 2.0, 6), round(h / 2.0, 6), round(d / 2.0, 6)]}


def merge_dims(manifest: dict, aabb: dict) -> dict:
    """Fold measured AABBs (keyed by asset id) into the manifest in place."""
    for entry in manifest["assets"]:
        m = aabb.get(entry["id"])
        if m is None:
            continue
        size = [float(x) for x in m["size"]]
        entry["dimensions"] = {
            "aabb_min": [round(float(x), 6) for x in m["aabb_min"]],
            "aabb_max": [round(float(x), 6) for x in m["aabb_max"]],
            "size": [round(x, 6) for x in size],
            "center": [round(float(x), 6) for x in m["center"]],
        }
        entry["collision"] = suggest_collision(size, entry["tags"])
    manifest["measured"] = True
    return manifest


# ---------------------------------------------------------------------------
# IO helpers / CLI
# ---------------------------------------------------------------------------

def write_manifest(bank_dir: Path, manifest: dict) -> Path:
    path = bank_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n")
    return path


def load_manifest_file(bank_dir: Path) -> dict:
    return json.loads((bank_dir / "manifest.json").read_text())


def _cmd_list(bank_dir: Path) -> None:
    manifest = load_manifest_file(bank_dir)
    print(f"{'id':<16} {'archetype':<10} {'license':<10} {'dims (WxHxD)':<22} desc")
    for e in manifest["assets"]:
        dims = "-"
        if e.get("dimensions"):
            s = e["dimensions"]["size"]
            dims = f"{s[0]:.2f}x{s[1]:.2f}x{s[2]:.2f}"
        print(f"{e['id']:<16} {e['archetype']:<10} {e['license']:<10} {dims:<22} {e['description']}")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Curate the real-3D-asset demo bank.")
    ap.add_argument("--src", default=os.environ.get(
        "GI_ASSET_SRC", "/home/enaha/GI/godot_rl_agents_examples"),
        help="path to a godot_rl_agents_examples checkout")
    ap.add_argument("--bank", required=True, help="bank output directory")
    ap.add_argument("--merge-dims", metavar="AABB_JSON",
                    help="merge Godot-measured AABBs into an existing manifest")
    ap.add_argument("--list", action="store_true", help="print the bank inventory")
    args = ap.parse_args(argv)

    bank_dir = Path(args.bank)

    if args.list:
        _cmd_list(bank_dir)
        return 0

    if args.merge_dims:
        manifest = load_manifest_file(bank_dir)
        aabb = json.loads(Path(args.merge_dims).read_text())
        manifest = merge_dims(manifest, aabb)
        write_manifest(bank_dir, manifest)
        measured = sum(1 for e in manifest["assets"] if e["dimensions"])
        print(f"merged dimensions for {measured}/{manifest['count']} assets")
        return 0

    src_root = Path(args.src)
    if not (src_root / "examples").is_dir():
        ap.error(f"--src {src_root} is not a godot_rl_agents_examples checkout")
    manifest = build_manifest(src_root, bank_dir)
    path = write_manifest(bank_dir, manifest)
    print(f"curated {manifest['count']} assets -> {bank_dir / 'models'}")
    print(f"manifest -> {path}")
    print("next: measure AABBs in-image, then re-run with --merge-dims")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
