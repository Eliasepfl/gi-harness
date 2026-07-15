"""Asset bank matcher -- map game bodies to real 3D assets for render-only dressing.

Pure python, offline-testable. The generated game names its bodies for what they
*represent* ("car", "tree", "player_bot"); this module maps those names to bank
asset ids a dresser can load render-only. Assets are cosmetic; physics stays the
game's own.

Routing is LLM-FIRST, reusing the existing generation machinery
(``harness.gen.gamegen._openrouter_complete``) exactly the way
``harness.gen.skill_context._llm_route`` routes skills: one light call reads the
manifest MENU (id + description + dimensions) plus the game's prompt and body
list, and returns a ``body -> asset-id`` mapping (or ``null`` for primitive
dressing). No hand-coded archetype taxonomy -- a light prompt does the semantics.

The mapping is cached beside the game (``<game>.assets.json``) so a demo capture
is reproducible without re-calling the model.

Offline FALLBACK (``use_llm=False`` or the backend being unavailable) is
deliberately trivial: exact / substring name-vs-id match, else ``None``. It is
for offline tests and degraded runs only -- never a keyword taxonomy.

Public API
----------
    manifest = load_manifest()
    mapping  = route_assets(game_prompt, bodies, manifest)       # {name: id|None}
    asset_id = match("car")                                      # single, offline
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Optional, Sequence, Union

# Default bank manifest: <repo>/assets/manifest.json  (harness/demo -> up 2).
DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / "assets" / "manifest.json"

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_JSON_OBJ = re.compile(r"\{.*\}", re.DOTALL)

CompleteFn = Callable[[str, list], str]


# --------------------------------------------------------------------------- #
# Manifest loading
# --------------------------------------------------------------------------- #
def load_manifest(path: Union[str, Path, None] = None) -> dict:
    """Load and return the bank manifest dict (default: the committed bank)."""
    p = Path(path) if path is not None else DEFAULT_MANIFEST
    return json.loads(p.read_text())


def _asset_ids(manifest: dict) -> list:
    return [a["id"] for a in manifest["assets"]]


# --------------------------------------------------------------------------- #
# Body normalisation
# --------------------------------------------------------------------------- #
def _norm_bodies(bodies: Sequence) -> list:
    """Accept ``["car", ...]`` or ``[{"name","shape","size"}, ...]`` -> dicts."""
    out = []
    for b in bodies:
        if isinstance(b, str):
            out.append({"name": b})
        elif isinstance(b, dict) and b.get("name"):
            out.append(dict(b))
    return out


def _tokens(name: str) -> list:
    """Lowercase word tokens of a body name (camelCase / separators aware)."""
    spaced = _CAMEL.sub(" ", name or "")
    return [t for t in _TOKEN_SPLIT.split(spaced.lower()) if t]


# --------------------------------------------------------------------------- #
# Offline fallback -- trivial, no taxonomy
# --------------------------------------------------------------------------- #
def _offline_match(body_name: str, manifest: dict) -> Optional[str]:
    """Exact-or-substring name<->id match, else None. Deterministic (id order).

    Intentionally dumb: no synonyms, no archetype table. The LLM route is the
    production semantics; this only keeps offline tests and degraded runs sane.
    """
    ids = _asset_ids(manifest)
    id_set = set(ids)
    toks = _tokens(body_name)
    joined = "".join(toks)  # e.g. "race_car" -> "racecar"

    # 1) whole-name exact id (hyphen/underscore-insensitive).
    hyphen = "-".join(toks)
    for cand in (hyphen, joined):
        if cand in id_set:
            return cand

    # 2) a body token equals an asset id exactly.
    for tok in toks:
        if tok in id_set:
            return tok

    # 3) substring either direction (id within a token, or token within id).
    subs = []
    for aid in ids:
        acomp = aid.replace("-", "")
        for tok in toks:
            if len(tok) >= 3 and (tok in acomp or acomp in tok):
                subs.append(aid)
                break
    if subs:
        return sorted(subs)[0]
    return None


# --------------------------------------------------------------------------- #
# LLM route -- one light call over the manifest menu
# --------------------------------------------------------------------------- #
def _menu(manifest: dict) -> str:
    lines = []
    for a in manifest["assets"]:
        dims = ""
        if a.get("dimensions"):
            s = a["dimensions"]["size"]
            dims = f" [{s[0]:.2f}x{s[1]:.2f}x{s[2]:.2f}]"
        lines.append(f"- {a['id']}: {a.get('description', '')}{dims}")
    return "\n".join(lines)


def _bodies_block(bodies: list) -> str:
    lines = []
    for b in bodies:
        extra = []
        if b.get("shape"):
            extra.append(str(b["shape"]))
        if b.get("size"):
            sz = b["size"]
            try:
                extra.append("x".join(f"{float(v):.2f}" for v in sz))
            except (TypeError, ValueError):
                pass
        suffix = f" ({', '.join(extra)})" if extra else ""
        lines.append(f"- {b['name']}{suffix}")
    return "\n".join(lines)


def _parse_mapping(raw: str, valid_ids: set, body_names: list) -> dict:
    """Parse the model's JSON object; keep only known bodies + valid ids/None."""
    if not raw:
        return {}
    text = raw.strip()
    if "```" in text:  # strip code fences
        text = re.sub(r"```[a-zA-Z0-9]*", "", text).replace("```", "")
    m = _JSON_OBJ.search(text)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    name_set = set(body_names)
    out = {}
    for k, v in data.items():
        if k not in name_set:
            continue
        if isinstance(v, str) and v in valid_ids:
            out[k] = v
        else:
            out[k] = None
    return out


def _default_complete() -> Optional[CompleteFn]:
    """The production completion fn (OpenRouter via gamegen), or None if absent."""
    try:
        from harness.gen.gamegen import _openrouter_complete
        return _openrouter_complete
    except Exception:  # noqa: BLE001
        return None


def _llm_route(game_context: str, bodies: list, manifest: dict,
               complete_fn: CompleteFn) -> dict:
    """One light call mapping each body to an asset id (or None). {} on failure."""
    valid = set(_asset_ids(manifest))
    system = (
        "You dress a physics game with COSMETIC low-poly 3D props. For each body, "
        "pick the best-fitting asset id from the MENU, or null when nothing fits "
        "(it will be drawn as a plain primitive). The choice is visual only and "
        "never changes gameplay. Reply with ONLY a JSON object mapping each body "
        "name to an asset id copied EXACTLY from the menu, or null. No prose."
    )
    user = (f"GAME: {game_context}\n\n"
            f"BODIES:\n{_bodies_block(bodies)}\n\n"
            f"MENU:\n{_menu(manifest)}")
    try:
        raw = complete_fn(system, [{"role": "user", "content": user}])
    except Exception:  # noqa: BLE001 - backend unavailable / network / etc.
        return {}
    return _parse_mapping(raw, valid, [b["name"] for b in bodies])


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #
def route_assets(game_context: str,
                 bodies: Sequence,
                 manifest: Optional[dict] = None,
                 *,
                 use_llm: bool = True,
                 cache_path: Union[str, Path, None] = None,
                 complete_fn: Optional[CompleteFn] = None) -> dict:
    """Map each game body to a bank asset id (or ``None``) for render-only dressing.

    Parameters
    ----------
    game_context : str
        The game's title / prompt -- the semantic anchor for the routing call.
    bodies : sequence
        Body names (``["car", ...]``) or dicts (``[{"name","shape","size"}, ...]``).
    manifest : dict, optional
        Pre-loaded manifest; loaded on demand if omitted.
    use_llm : bool, default True
        LLM-first routing. ``False`` forces the trivial offline fallback (tests).
    cache_path : path, optional
        If given, a cached mapping there is returned as-is (reproducible captures),
        and a freshly computed mapping is written to it.
    complete_fn : callable, optional
        ``(system, messages) -> str`` completion fn; defaults to the OpenRouter
        backend used by generation. Injectable for offline tests.

    Returns
    -------
    dict
        ``{body_name: asset_id_or_None}`` covering every input body.
    """
    if manifest is None:
        manifest = load_manifest()
    norm = _norm_bodies(bodies)
    names = [b["name"] for b in norm]

    if cache_path is not None and Path(cache_path).is_file():
        cached = json.loads(Path(cache_path).read_text())
        if isinstance(cached, dict):
            return {n: cached.get(n) for n in names}

    mapping: dict = {}
    if use_llm:
        fn = complete_fn or _default_complete()
        if fn is not None:
            mapping = _llm_route(game_context, norm, manifest, fn)

    # Fill any body the LLM did not confidently map with the offline fallback.
    result = {}
    for n in names:
        result[n] = mapping.get(n) if n in mapping else _offline_match(n, manifest)

    if cache_path is not None:
        Path(cache_path).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def match(body_name: str,
          body_shape_info: Optional[dict] = None,
          manifest: Optional[dict] = None,
          *,
          use_llm: bool = False,
          game_context: str = "",
          complete_fn: Optional[CompleteFn] = None) -> Optional[str]:
    """Best bank asset id for a single body, or ``None``.

    Offline by default (the trivial fallback) so it is safe in tests and cheap
    lookups. Set ``use_llm=True`` (with ``game_context``) to route this one body
    through the model. ``body_shape_info`` is accepted for API compatibility and
    forwarded to the router as the body's shape/size hint.
    """
    if manifest is None:
        manifest = load_manifest()
    if use_llm:
        body = {"name": body_name}
        if body_shape_info:
            if body_shape_info.get("shape"):
                body["shape"] = body_shape_info["shape"]
            if body_shape_info.get("size"):
                body["size"] = body_shape_info["size"]
        mapping = route_assets(game_context, [body], manifest,
                               use_llm=True, complete_fn=complete_fn)
        return mapping.get(body_name)
    return _offline_match(body_name, manifest)


if __name__ == "__main__":
    import sys
    _mf = load_manifest()
    for _name in sys.argv[1:]:
        print(f"{_name!r:>16} -> {match(_name, manifest=_mf)}  (offline)")
