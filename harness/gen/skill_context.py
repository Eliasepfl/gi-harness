"""Deterministic skill retrieval + prompt injection for the GDScript lane.

The GDScript generation lane (``harness/gen/gamegen.py`` ``engine="gdscript"``)
writes ONE ``.gd`` game implementing the GameAPI contract. The contract teaches
the *format*; it carries no craft KNOWLEDGE. The sibling ``gd-agentic-skills``
library (96 SKILL.md playbooks + genre blueprints + a ``skills_index.json``
routing table, LGPLv3, pinned ``e9e20ff``) is exactly that knowledge.

This module is the parts-bank retrieval pattern (see the retired
``harness.gen.retrieval``) re-pointed at the skills library: a deterministic,
dependency-free, no-LLM, no-network selection over each skill's ``name`` +
``description`` + ``keywords`` (the routing table), returning an injectable,
attributed reference block. It is a pure function of ``(prompt, library state)``,
so a run is reproducible.

License hygiene: the library is LGPLv3, so we READ it at runtime (use carries no
copy obligation) and NEVER vendor its files into this repo. The rendered block
is explicitly attributed and framed as paraphrase-only advisory craft guidance —
the binding contract lives elsewhere.

Locating the library (in order):
* ``$GD_AGENTIC_SKILLS_DIR`` if set;
* else the sibling default ``<repo_root>/../gd-agentic-skills``.
If neither is a usable library (dir missing, or no ``skills_index.json``), every
public function no-ops gracefully: ``select_skills`` -> ``[]``,
``render_skill_context`` -> ``""``. A missing library can never break generation.

Public surface:
* ``library_root(root=None)``      -> the resolved library dir, or ``None``.
* ``load_index(root=None)``        -> the parsed routing table (``[]`` if absent).
* ``select_skills(prompt, k=3)``   -> the top-k ``Skill`` (name/description/body),
                                      deterministic, genre + physics/architecture
                                      preferred.
* ``render_skill_context(prompt, k=3, max_tokens=4000)`` -> the attributed,
                                      budget-bounded injectable text block (``""``
                                      when the library is absent or nothing matches).
* ``estimate_tokens(text)``        -> the same rough token estimate the budget uses.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import namedtuple

# --- Attribution / framing ---------------------------------------------------
# The upstream repo, license, and pinned commit. Surfaced verbatim in every
# rendered block so provenance travels with the knowledge (LGPLv3 hygiene).
LIBRARY_SLUG = "thedivergentai/gd-agentic-skills"
LIBRARY_LICENSE = "LGPLv3"
LIBRARY_PIN = "e9e20ff"
ATTRIBUTION = (f"Reference knowledge (paraphrase, do not copy verbatim) from "
               f"{LIBRARY_SLUG}, {LIBRARY_LICENSE}, pinned {LIBRARY_PIN}")

# Sibling-clone default, relative to the repo root (this file is harness/gen/).
_SIBLING_DIRNAME = "gd-agentic-skills"
_INDEX_FILE = "skills_index.json"
_SKILLS_SUBDIR = "skills"
_SKILL_FILE = "SKILL.md"

# --- Budget tuning -----------------------------------------------------------
_CHARS_PER_TOKEN = 4          # rough English estimate; only used for the budget
_MIN_BODY_CHARS = 200         # never truncate a body below this (a usable stub)
_TRUNCATION_MARK = "\n... [truncated]"

# --- Selection tuning --------------------------------------------------------
# The description + keywords are the curated retrieval keys (that is their whole
# purpose in the routing table); the name is a strong secondary key.
_W_NAME = 2
_W_DESC = 2
_W_KEYWORDS = 3

# BM25 (Okapi) parameters — standard defaults, robust at this corpus size (~96).
_K1 = 1.5
_B = 0.75

# A matched genre blueprint anchors the design; a physics/architecture skill
# grounds the mechanics. select_skills promotes one of each into the top-k.
_GENRE_PREFIX = "godot-genre-"
_PHYS_ARCH_HINTS = (
    "physics", "characterbody", "raycast", "collision",
    "architecture", "autoload", "composition", "signal", "state-machine",
    "scene-management", "navigation", "tilemap",
)

# Words carrying no retrieval signal in a short game prompt (mirrors the retired
# parts retrieval's list; deliberately small so real cues survive).
_STOPWORDS = frozenset("""
a an the this that these those it its is are be am was were being been
of to in on at by for from into onto over under up down out off with without
and or but nor so as than then also just only very more most less
you your yours we our i me my he she they them their who whose which what
do does did done doing make makes made get gets got put puts
game games level levels play plays playing player players round
one two some any each all every no not never must should would could can
where when while here there now new small little big large into where
""".split())

_TOKEN_RE = re.compile(r"[a-z0-9]+")

Skill = namedtuple("Skill", ("name", "description", "body"))


# --------------------------------------------------------------------------- #
# Locating the library
# --------------------------------------------------------------------------- #
def _repo_root() -> str:
    """Repo root = grandparent of this package dir (harness/gen/)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def library_root(root: str | None = None):
    """Resolve the gd-agentic-skills library dir, or ``None`` if unusable.

    Order: explicit ``root`` arg > ``$GD_AGENTIC_SKILLS_DIR`` > the sibling
    default ``<repo_root>/../gd-agentic-skills``. A path only counts when it is a
    directory that contains ``skills_index.json`` (the routing table), so a bare
    or half-cloned directory degrades to "absent" rather than erroring.
    """
    cand = root or os.environ.get("GD_AGENTIC_SKILLS_DIR")
    if not cand:
        cand = os.path.join(os.path.dirname(_repo_root()), _SIBLING_DIRNAME)
    cand = os.path.abspath(cand)
    if os.path.isdir(cand) and os.path.isfile(os.path.join(cand, _INDEX_FILE)):
        return cand
    return None


# --------------------------------------------------------------------------- #
# Loading the routing table + skill bodies
# --------------------------------------------------------------------------- #
# Cache the parsed index + BM25 stats per (root, index mtime) so repeated calls
# in one run do not re-read the 66 KB routing table; keyed on mtime so an edited
# library is picked up. Bodies are read fresh on demand (only for the top-k).
_INDEX_CACHE: dict = {}


def _read_text(path: str) -> str:
    """Read a UTF-8 file, tolerating a BOM and normalising newlines."""
    with open(path, "r", encoding="utf-8-sig") as fh:
        return fh.read().replace("\r\n", "\n").replace("\r", "\n")


def load_index(root: str | None = None) -> list:
    """The parsed ``skills_index.json`` routing table (``[]`` if absent/broken).

    Each entry is a dict with at least ``name``; ``description`` / ``keywords``
    are the retrieval keys. Any load or parse problem degrades to ``[]`` so a
    library hiccup can never break generation.
    """
    d = library_root(root)
    if not d:
        return []
    path = os.path.join(d, _INDEX_FILE)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    cached = _INDEX_CACHE.get(d)
    if cached and cached["mtime"] == mtime:
        return cached["index"]
    try:
        data = json.loads(_read_text(path))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    index = [e for e in data if isinstance(e, dict) and e.get("name")]
    _INDEX_CACHE[d] = {"mtime": mtime, "index": index, "bm25": _build_bm25(index)}
    return index


def _bm25_stats(root: str, index: list):
    """The cached BM25 stats for ``index`` (built alongside it in load_index)."""
    cached = _INDEX_CACHE.get(root)
    if cached and cached["index"] is index:
        return cached["bm25"]
    return _build_bm25(index)


def _skill_body(root: str, name: str) -> str:
    """The SKILL.md body (markdown after the YAML frontmatter) for ``name``.

    Returns ``""`` when the file is missing/unreadable — a selected skill whose
    body we cannot read is simply dropped by the caller.
    """
    path = os.path.join(root, _SKILLS_SUBDIR, name, _SKILL_FILE)
    if not os.path.isfile(path):
        return ""
    try:
        text = _read_text(path)
    except OSError:
        return ""
    return _strip_frontmatter(text).strip()


def _strip_frontmatter(text: str) -> str:
    """Drop a leading ``---``-delimited YAML frontmatter block, if present."""
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return text
    # Find the closing '---' on its own line after the opener.
    lines = stripped.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1:])
    return text


# --------------------------------------------------------------------------- #
# Tokenisation
# --------------------------------------------------------------------------- #
def _stem(tok: str) -> str:
    """Cheap deterministic plural stem (drop a trailing 's' on longer tokens)."""
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def _tokenize(text: str) -> list:
    """Lowercase -> alphanumeric tokens -> drop stopwords -> plural-stem."""
    out = []
    for raw in _TOKEN_RE.findall((text or "").lower()):
        if raw in _STOPWORDS:
            continue
        out.append(_stem(raw))
    return out


def _entry_tokens(entry: dict) -> list:
    """Weighted bag of tokens for one routing-table entry."""
    name_toks = _tokenize(str(entry.get("name", "")).replace("-", " ").replace("_", " "))
    desc_toks = _tokenize(str(entry.get("description", "")))
    kw_toks = []
    for kw in entry.get("keywords", []) or []:
        kw_toks.extend(_tokenize(str(kw).replace("_", " ")))
    return (name_toks * _W_NAME) + (desc_toks * _W_DESC) + (kw_toks * _W_KEYWORDS)


# --------------------------------------------------------------------------- #
# BM25 over the routing table
# --------------------------------------------------------------------------- #
def _build_bm25(index: list) -> dict:
    """Precompute per-document term frequencies + smoothed IDF for ``index``."""
    names = [e["name"] for e in index]
    tf, df, doc_len = {}, {}, {}
    for entry in index:
        counts = {}
        for t in _entry_tokens(entry):
            counts[t] = counts.get(t, 0) + 1
        tf[entry["name"]] = counts
        doc_len[entry["name"]] = sum(counts.values())
        for t in counts:
            df[t] = df.get(t, 0) + 1
    n = len(names)
    avgdl = (sum(doc_len.values()) / n) if n else 0.0
    idf = {t: math.log(1.0 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
    return {"names": names, "tf": tf, "doc_len": doc_len, "avgdl": avgdl, "idf": idf}


def _score_doc(stats: dict, name: str, query: list) -> float:
    counts = stats["tf"][name]
    dl = stats["doc_len"][name]
    avgdl = stats["avgdl"]
    denom_norm = _K1 * (1.0 - _B + _B * (dl / avgdl if avgdl else 0.0))
    total = 0.0
    for t in query:
        f = counts.get(t)
        if not f:
            continue
        total += stats["idf"].get(t, 0.0) * (f * (_K1 + 1.0)) / (f + denom_norm)
    return total


def _rank(prompt: str, root: str, index: list) -> list:
    """Every skill scored for ``prompt`` -> ``[(name, score), ...]`` desc.

    Deterministic: ties break on name so the order is stable for a fixed library.
    """
    stats = _bm25_stats(root, index)
    query = _tokenize(prompt)
    scored = [(name, _score_doc(stats, name, query)) for name in stats["names"]]
    scored.sort(key=lambda ns: (-ns[1], ns[0]))
    return scored


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #
def _is_genre(name: str) -> bool:
    return name.startswith(_GENRE_PREFIX)


def _is_phys_arch(name: str) -> bool:
    return any(h in name for h in _PHYS_ARCH_HINTS)


def _llm_route(prompt: str, index: list, k: int) -> list:
    """Semantic skill routing via one cheap LLM call (the godot-master pattern).

    gd-agentic descriptions are API-keyword-stuffed (`CharacterBody2D`,
    `move_and_slide`), so lexical BM25 whiffs on game-concept prompts ("herd",
    "park", "dodge"). A model READS the name+description table and picks by
    relevance. Selection is reference-context only (never the certified
    artifact), so a non-deterministic-but-relevant pick beats a
    deterministic-but-wrong one. Returns [] on any failure -> BM25 fallback.
    """
    try:
        from harness.gen.gamegen import _openrouter_complete, _BackendUnavailable
    except Exception:  # noqa: BLE001
        return []
    catalog = "\n".join(f"- {e['name']}: {(e.get('description') or '')[:180]}"
                        for e in index)
    system = ("You route a game-design prompt to the most relevant Godot skills. "
              "Reply with ONLY the chosen skill names, one per line, most relevant "
              "first, at most " + str(k) + ". Names must be copied EXACTLY from the "
              "catalog. No prose.")
    user = f"PROMPT: {prompt}\n\nCATALOG:\n{catalog}"
    try:
        raw = _openrouter_complete(system, [{"role": "user", "content": user}])
    except Exception:  # noqa: BLE001 - _BackendUnavailable, network, etc.
        return []
    valid = {e["name"] for e in index}
    picked = []
    for line in (raw or "").splitlines():
        name = line.strip().lstrip("-*0123456789. ").strip()
        if name in valid and name not in picked:
            picked.append(name)
        if len(picked) >= k:
            break
    return picked


def select_skills(prompt: str, k: int = 3, *, root: str | None = None) -> list:
    """The top-``k`` skills for ``prompt`` (``[]`` when the library is absent).

    Routing is LLM-FIRST (``_llm_route``: a model reads the name+description
    catalog and picks by relevance — semantically correct across the API-keyword
    vs game-concept vocabulary gap), with a deterministic BM25 FALLBACK when
    OpenRouter is unavailable/offline. Selection is reference-context only, never
    the certified artifact, so its determinism is not load-bearing.

    Preference on the BM25 fallback (design anchoring, not pure score): a matched
    GENRE blueprint (``godot-genre-*``) is promoted to the front and a matched
    2D/3D physics/architecture skill is ensured a slot. Returns
    ``Skill(name, description, body)``; a skill whose body cannot be read is skipped.
    """
    if k <= 0:
        return []
    d = library_root(root)
    if not d:
        return []
    index = load_index(d)
    if not index:
        return []
    by_name = {e["name"]: e for e in index}

    # LLM-first routing; fall back to BM25 when it returns nothing.
    llm_names = _llm_route(prompt, index, k)
    if llm_names:
        out = []
        for name in llm_names[:k]:
            body = _skill_body(d, name)
            if body:
                out.append(Skill(name=name,
                                 description=(by_name.get(name) or {}).get("description", ""),
                                 body=body))
        if out:
            return out

    ranked = _rank(prompt, d, index)
    matched = [name for name, sc in ranked if sc > 0.0]
    if not matched:
        return []

    picked: list = []

    def _add(name):
        if name and name not in picked and len(picked) < k:
            picked.append(name)

    # Prefer the strongest matched genre blueprint, then a physics/architecture
    # skill, then fill from the ranking.
    _add(next((n for n in matched if _is_genre(n)), None))
    _add(next((n for n in matched if _is_phys_arch(n)), None))
    for name in matched:
        _add(name)

    out = []
    for name in picked[:k]:
        body = _skill_body(d, name)
        if not body:
            continue
        out.append(Skill(name=name,
                         description=_clean_description(by_name[name].get("description", "")),
                         body=body))
    return out


def _clean_description(desc) -> str:
    """Normalise a routing-table description (strip wrapping quotes/whitespace)."""
    s = str(desc or "").strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1].strip()
    return s


# --------------------------------------------------------------------------- #
# Rendering the injectable block
# --------------------------------------------------------------------------- #
def estimate_tokens(text: str) -> int:
    """Rough token estimate (the same one the budget enforces): ~4 chars/token."""
    return len(text or "") // _CHARS_PER_TOKEN


_MASTER_SKILL = "godot-master"


def _orchestrator_block(root: str) -> str:
    """The godot-master orchestrator body (the library's own entry point).

    Per the library README, godot-master is THE skill for "a new project from
    scratch / designing architecture / choosing 2D vs 3D" — i.e. exactly game
    GENERATION. Its Master Decision Matrix does positive AND negative routing to
    domain skills, so it is the orchestrating context we lead with; the routed
    domain skills (``select_skills``) follow for detailed patterns. Returns ""
    when the master skill is absent (older library layout) → domain-only.
    """
    return _skill_body(root, _MASTER_SKILL) or ""


def render_skill_context(prompt: str, k: int = 3, max_tokens: int = 4000,
                         *, root: str | None = None,
                         orchestrator: bool = True) -> str:
    """An attributed, budget-bounded reference block for ``prompt``.

    Layout: attribution header, then (when ``orchestrator``) the godot-master
    orchestrator body — the library's own decision-matrix entry point for a new
    project — then each routed DOMAIN skill's body for detail. Everything is
    truncated to stay under ``max_tokens``. The orchestrator gets ~half the
    budget (its decision frameworks are the high-value base), the domain skills
    share the rest. Returns ``""`` when the library is absent and nothing routes.
    """
    d = library_root(root)
    master = _orchestrator_block(d) if (orchestrator and d) else ""
    skills = [s for s in select_skills(prompt, k=k, root=root)
              if s.name != _MASTER_SKILL]  # the orchestrator is not a domain skill
    if not skills and not master:
        return ""

    header = ATTRIBUTION
    char_budget = max(0, max_tokens) * _CHARS_PER_TOKEN
    blocks = [header]

    # The orchestrator leads and gets ~half the body budget (its decision
    # frameworks are the base); domain skills share the remainder.
    master_budget = char_budget // 2 if (master and skills) else char_budget
    if master:
        m = master
        if len(m) > master_budget:
            m = m[:master_budget].rstrip() + _TRUNCATION_MARK
        blocks.append(f"### {_MASTER_SKILL} (orchestrator)\n{m}")

    if skills:
        remaining = char_budget - sum(len(b) for b in blocks)
        reserve = sum(len(f"### {s.name}\n") + 4 for s in skills)
        body_budget = remaining - reserve
        per_skill = max(_MIN_BODY_CHARS, body_budget // len(skills)) if body_budget > 0 \
            else _MIN_BODY_CHARS
        for s in skills:
            body = s.body
            if len(body) > per_skill:
                body = body[:per_skill].rstrip() + _TRUNCATION_MARK
            blocks.append(f"### {s.name}\n{body}")
    text = "\n\n".join(blocks)

    # Hard cap: if the reserved-title overhead still pushed us over (many skills,
    # tiny budget), truncate the whole block so the budget is never exceeded.
    # Reserve room for the mark first so appending it cannot overshoot char_budget.
    if char_budget and len(text) > char_budget:
        cut = max(0, char_budget - len(_TRUNCATION_MARK))
        text = text[:cut].rstrip() + _TRUNCATION_MARK
    return text
