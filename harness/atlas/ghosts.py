"""GHOST reference points for THE ATLAS: honest GEOMETRY-ONLY descriptors for the
human-authored demo games (godot_rl_agents examples), extracted from their ``.tscn``
scene sources (ATLAS D1, read-only).

These games are NOT run through our funnel, so their *behavioural* axes — solver effort,
witness entropy, pressure — are UNCOMPUTABLE here. We do NOT fake them. We compute ONLY
what a ``.tscn``/``.gd`` source honestly yields:

  * ``dimension``   — 2D vs 3D (node type / transform suffix vote),
  * ``n_nodes``     — total authored scene nodes (a content-density proxy),
  * ``n_bodies``    — physics-body nodes (Static/Rigid/Character/Vehicle/... + Area),
    split into ``n_static`` / ``n_sensor`` / ``n_dynamic``,
  * ``world_extent``— the largest per-axis span of node origins, WHERE DERIVABLE
    (>= 2 distinct positions), else ``None``,
  * ``proportion``  — that scene's max/min axis-span aspect ratio, else ``None``,
  * ``n_scenes``    — how many ``.tscn`` files the game is authored across.

The point is to SEE the structural distance between our certified library and this
human-authored quality bar — so ghosts render as a DISTINCT marker class, never mistaken
for a certified point.

DETERMINISM — pure text parsing, no engine, no randomness: same source -> same dict.
"""

from __future__ import annotations

import glob
import math
import os
import re

# Ghost descriptor schema (geometry-only), stable order — the ghost analogue of
# ``descriptors.DESCRIPTOR_KEYS``. Every value is a JSON scalar or None.
GHOST_KEYS = (
    "dimension",       # "2D" | "3D" | None
    "n_nodes",         # total authored scene nodes (content density)
    "n_scenes",        # number of .tscn files the game spans
    "n_bodies",        # physics-body nodes (static + sensor + dynamic)
    "n_static",        # StaticBody* nodes
    "n_sensor",        # Area* nodes (sensors / triggers)
    "n_dynamic",       # Rigid/Character/Vehicle/Animatable/PhysicalBone bodies
    "world_extent",    # largest per-axis span of node origins (None if not derivable)
    "proportion",      # max/min axis-span aspect ratio (None if not derivable)
)

# Physics-body node types, classified. Matched by the class name in a node's ``type="..."``
# — an instanced sub-scene (``instance=ExtResource(...)``, no explicit type) is counted as
# a node but NOT as a typed body (its own bodies are counted when its .tscn is parsed).
_STATIC_TYPES = frozenset({"StaticBody2D", "StaticBody3D"})
_SENSOR_TYPES = frozenset({"Area2D", "Area3D"})
_DYNAMIC_TYPES = frozenset({
    "RigidBody2D", "RigidBody3D", "CharacterBody2D", "CharacterBody3D",
    "AnimatableBody2D", "AnimatableBody3D", "VehicleBody3D", "PhysicalBone3D",
    "PhysicsBody2D", "PhysicsBody3D",
})

_NODE_RE = re.compile(r'^\[node\s+(.*)\]\s*$')
_ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
_TRANSFORM3D_RE = re.compile(r'Transform3D\s*\(([^)]*)\)')
_TRANSFORM2D_RE = re.compile(r'Transform2D\s*\(([^)]*)\)')
_VECTOR3_RE = re.compile(r'(?:position|global_position)\s*=\s*Vector3\s*\(([^)]*)\)')
_VECTOR2_RE = re.compile(r'(?:position|global_position)\s*=\s*Vector2\s*\(([^)]*)\)')

_MAX_TSCN_BYTES = 400_000        # per-file read cap (bounded I/O on a shared node)


def _floats(s):
    out = []
    for tok in s.split(","):
        tok = tok.strip()
        try:
            out.append(float(tok))
        except ValueError:
            pass
    return out


def parse_tscn(text):
    """Parse a ``.tscn`` text into a list of node dicts::

        {"name": str, "type": str|None, "instanced": bool, "origin": [..]|None, "dim": 2|3|None}

    ``origin`` is the node's translation (Transform3D/2D origin, or a position=Vector).
    Pure string parsing — never raises on a malformed scene (best-effort)."""
    nodes = []
    cur = None
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        m = _NODE_RE.match(line.strip())
        if m:
            attrs = dict(_ATTR_RE.findall(m.group(1)))
            ntype = attrs.get("type")
            cur = {"name": attrs.get("name"), "type": ntype,
                   "instanced": "instance=" in m.group(1) and ntype is None,
                   "origin": None, "dim": None}
            if ntype:
                if ntype.endswith("3D"):
                    cur["dim"] = 3
                elif ntype.endswith("2D"):
                    cur["dim"] = 2
            nodes.append(cur)
            continue
        if cur is None or line.startswith("["):
            # a property line before any node, or a new section header -> ignore/close
            if line.startswith("["):
                cur = None
            continue
        # property line of the current node: pull an origin + dimension signal
        if cur["origin"] is None:
            mt = _TRANSFORM3D_RE.search(line)
            if mt:
                nums = _floats(mt.group(1))
                if len(nums) >= 3:
                    cur["origin"] = nums[-3:]
                    cur["dim"] = 3
                continue
            mt = _TRANSFORM2D_RE.search(line)
            if mt:
                nums = _floats(mt.group(1))
                if len(nums) >= 2:
                    cur["origin"] = nums[-2:]
                    cur["dim"] = 2
                continue
            mv = _VECTOR3_RE.search(line)
            if mv:
                nums = _floats(mv.group(1))
                if len(nums) >= 3:
                    cur["origin"] = nums[:3]
                    cur["dim"] = 3
                continue
            mv = _VECTOR2_RE.search(line)
            if mv:
                nums = _floats(mv.group(1))
                if len(nums) >= 2:
                    cur["origin"] = nums[:2]
                    cur["dim"] = 2
    return nodes


def _extent_proportion(origins):
    """The largest per-axis span and the max/min aspect ratio over a list of origins.
    Returns ``(extent, proportion)`` — both ``None`` when fewer than 2 distinct origins
    make an extent underivable."""
    uniq = {tuple(o) for o in origins if o}
    if len(uniq) < 2:
        return (None, None)
    ndim = max(len(o) for o in uniq)
    spans = []
    for ax in range(ndim):
        col = [o[ax] for o in uniq if len(o) > ax]
        spans.append((max(col) - min(col)) if col else 0.0)
    extent = max(spans) if spans else 0.0
    pos_spans = [s for s in spans if s > 1e-9]
    proportion = (max(pos_spans) / min(pos_spans)) if len(pos_spans) >= 2 else None
    if extent <= 0.0:
        return (None, None)
    return (round(extent, 3), round(proportion, 3) if proportion is not None else None)


def describe_ghost_tscn(text):
    """Geometry-only descriptors for ONE ``.tscn`` text -> a dict over :data:`GHOST_KEYS`
    (``n_scenes`` is always 1 here). Missing/underivable fields are ``None``/0 as apt."""
    nodes = parse_tscn(text)
    n_static = n_sensor = n_dynamic = 0
    dim3 = dim2 = 0
    origins = []
    for nd in nodes:
        t = nd.get("type")
        if t in _STATIC_TYPES:
            n_static += 1
        elif t in _SENSOR_TYPES:
            n_sensor += 1
        elif t in _DYNAMIC_TYPES:
            n_dynamic += 1
        if nd.get("dim") == 3:
            dim3 += 1
        elif nd.get("dim") == 2:
            dim2 += 1
        if nd.get("origin"):
            origins.append(nd["origin"])
    dimension = "3D" if dim3 > 0 else ("2D" if dim2 > 0 else None)
    extent, proportion = _extent_proportion(origins)
    return {
        "dimension": dimension,
        "n_nodes": len(nodes),
        "n_scenes": 1,
        "n_bodies": n_static + n_sensor + n_dynamic,
        "n_static": n_static,
        "n_sensor": n_sensor,
        "n_dynamic": n_dynamic,
        "world_extent": extent,
        "proportion": proportion,
    }


# Vendored / framework directories that are NOT the game's authored content. Their
# scenes (e.g. the godot_rl_agents addon's example sensors) would pollute both the node
# counts and the world extent, so we count ONLY the game author's own scenes.
_SKIP_DIRS = frozenset({"addons", ".godot", ".import", ".git"})


def _iter_tscn_paths(game_dir, max_files=200):
    """Bounded (maxdepth 4) walk of a ghost game dir for its AUTHORED ``.tscn`` scenes,
    skipping vendored/framework dirs (``addons/`` etc.)."""
    out = []
    for root, dirs, files in os.walk(game_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        depth = root[len(game_dir):].count(os.sep)
        if depth >= 4:
            dirs[:] = []
        for f in files:
            if f.endswith(".tscn"):
                out.append(os.path.join(root, f))
                if len(out) >= max_files:
                    return sorted(out)
    return sorted(out)


def describe_ghost_game(game_dir):
    """Aggregate honest geometry descriptors across ALL ``.tscn`` scenes of a ghost game.

    Node/body counts SUM across authored scenes (each instanced sub-scene's own bodies are
    counted once, where that scene is defined). ``world_extent`` / ``proportion`` come from
    the single scene with the LARGEST derivable extent (the authored play area). Returns a
    dict over :data:`GHOST_KEYS`, or ``None`` if the dir has no scenes."""
    paths = _iter_tscn_paths(game_dir)
    if not paths:
        return None
    agg = {"n_nodes": 0, "n_static": 0, "n_sensor": 0, "n_dynamic": 0}
    dim3 = dim2 = 0
    best_extent = None
    best_proportion = None
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read(_MAX_TSCN_BYTES)
        except OSError:
            continue
        d = describe_ghost_tscn(text)
        agg["n_nodes"] += d["n_nodes"]
        agg["n_static"] += d["n_static"]
        agg["n_sensor"] += d["n_sensor"]
        agg["n_dynamic"] += d["n_dynamic"]
        if d["dimension"] == "3D":
            dim3 += 1
        elif d["dimension"] == "2D":
            dim2 += 1
        ext = d["world_extent"]
        if ext is not None and (best_extent is None or ext > best_extent):
            best_extent = ext
            best_proportion = d["proportion"]
    dimension = "3D" if dim3 > 0 else ("2D" if dim2 > 0 else None)
    return {
        "dimension": dimension,
        "n_nodes": agg["n_nodes"],
        "n_scenes": len(paths),
        "n_bodies": agg["n_static"] + agg["n_sensor"] + agg["n_dynamic"],
        "n_static": agg["n_static"],
        "n_sensor": agg["n_sensor"],
        "n_dynamic": agg["n_dynamic"],
        "world_extent": best_extent,
        "proportion": best_proportion,
    }


def ghost_slug(game_dir):
    """The ghost's display slug: its example directory name (e.g. ``3DCarParking``)."""
    return os.path.basename(str(game_dir).rstrip("/")) or str(game_dir)


def build_ghosts(patterns):
    """Resolve ``patterns`` (dirs or globs of example game dirs) into ghost rows::

        {"slug": str, "game_dir": abspath, "descriptors": {<GHOST_KEYS>}, "kind": "ghost"}

    A dir with no ``.tscn`` scenes is skipped. Sorted by slug for determinism."""
    dirs = []
    seen = set()
    for pat in patterns or []:
        pat = str(pat)
        cands = [pat] if os.path.isdir(pat) else sorted(glob.glob(pat))
        for c in cands:
            if os.path.isdir(c):
                ap = os.path.abspath(c)
                if ap not in seen:
                    seen.add(ap)
                    dirs.append(ap)
    rows = []
    for d in dirs:
        desc = describe_ghost_game(d)
        if desc is None:
            continue
        rows.append({"slug": ghost_slug(d), "game_dir": d,
                     "descriptors": desc, "kind": "ghost"})
    return sorted(rows, key=lambda r: r["slug"])
