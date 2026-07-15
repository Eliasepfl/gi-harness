"""Tier-1b parts retrieval — deterministic lexical selection over the parts bank.

Pipeline Option A (see ``notes/parts_bank/pipeline.md`` and ``retrieval.md``): the
harness picks a small THEMED MENU of pre-certified bank parts from the game prompt
*before* the generation call, injects it into the system prompt, and pins it for
the whole run. There is NO extra LLM call and NO network: retrieval is a pure
function of ``(prompt, bank_version)`` — reproducible by construction, which the
research note makes the hard gate for the pinned path.

At this corpus size (30 parts now, ~500 later) the whole computation is a handful
of microseconds, so nothing here needs a vector DB or an ANN index. This module
ships the sparse channel the note recommends *first*: a clean, dependency-free
BM25 over each part's ``name`` + ``tags`` + ``summary``, with light plural
stemming and a small query-synonym map that bridges the day-1/day-2 themes the
bank was calibrated from (e.g. "volcano" -> lava). The dense (embedding) channel
is the documented later upgrade when rung-3 exotic prompts arrive; the public
surface here (``retrieve_menu``) is stable across that change.

Public surface:
* ``score(prompt)``        -> ranked ``[(name, score), ...]`` over the whole bank.
* ``retrieve(prompt)``     -> the selected part names (top-K above threshold, or
                              ``[]`` for a legend-only fallback).
* ``build_menu(names, engine)`` -> the rendered Tier-1b menu text.
* ``retrieve_menu(prompt, engine)`` -> ``(menu_text|None, menu_mode, names)`` —
                              the one call ``gamegen.generate_game`` makes.

The menu is ADVISORY: ``build_menu`` frames it with an "optional, invent freely,
raw-body escape hatch stays open" footer (``prompts/bank_menu.md.tmpl``; the escape
clause is engine-specific so the declarative godot menu never advertises
``world.add``), and every out-of-menu / escape-hatch use is telemetry, never an
error (pipeline.md D.1).
"""

from __future__ import annotations

import math
import re

from harness.core import bank as _bank
from harness.gen import prompts as _prompts

# --- Tuning constants ([eng.] — calibrated on the base-of-games prompt shapes) -
K_MAX = 15                 # never offer more than this many parts (menu ceiling)
K_MIN = 8                  # target menu size; fewer is fine when few parts match
SCORE_THRESHOLD = 0.6      # top score below this -> legend-only fallback (§D.2)
_REL_FLOOR = 0.18          # keep a part only if its score >= this fraction of the top

# Field weighting: the curated ``name``/``tags`` are stronger retrieval keys than
# the prose ``summary`` (they are what a themed prompt is expected to hit).
_W_NAME = 3
_W_TAGS = 3
_W_SUMMARY = 1

# BM25 (Okapi) parameters — standard defaults; robust at this corpus size.
_K1 = 1.5
_B = 0.75

# Words carrying no retrieval signal in a ~30-token game prompt. Deliberately
# small: over-pruning throws away real cues ("ball", "ice", "pit" stay).
_STOPWORDS = frozenset("""
a an the this that these those it its is are be am was were being been
of to in on at by for from into onto over under up down out off with without
and or but nor so as than then also just only very more most less
you your yours we our i me my he she they them their who whose which what
do does did done doing make makes made get gets got put puts
game games level levels play plays playing player players round
one two some any each all every no not never must should would could can
where when while here there now new small little big large
""".split())

# Query-side synonym expansion: bridges fictional/thematic vocabulary to the
# literal tokens the bank uses. Kept tight and curation-cheap — the dense channel
# is the general fix later (retrieval.md §1.2); this only covers the themes the
# v1 bank was calibrated from so the base-of-games prompts land.
_QUERY_SYNONYMS = {
    "volcano": ("lava", "magma"),
    "volcanic": ("lava", "magma"),
    "magma": ("lava",),
    "eruption": ("lava",),
    "molten": ("lava",),
    "fiery": ("lava", "hazard"),
    "wreck": ("wrecking", "demolition"),
    "wrecker": ("wrecking", "demolition"),
    "demolish": ("wrecking", "demolition"),
    "demolition": ("wrecking",),
    "catapult": ("seesaw", "lever", "spring"),
    "lever": ("seesaw", "teeter"),
    "trampoline": ("bouncy", "elastic"),
    "bounce": ("bouncy", "elastic"),
    "elevator": ("platform", "vertical", "spring"),
    "gate": ("checkpoint",),
    "switch": ("pressure", "switch"),
    "button": ("pressure", "switch"),
    "spike": ("spikes",),
    "saw": ("saw", "blade"),
    "boulder": ("boulder", "rock"),
    "hockey": ("puck", "disc", "glide"),
}


# --------------------------------------------------------------------------- #
# Tokenisation
# --------------------------------------------------------------------------- #
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _stem(tok: str) -> str:
    """Cheap deterministic plural stem: drop a trailing 's' on longer tokens.

    Guards ``ss`` (glass, press) so it stays a no-op there, and only touches
    tokens > 3 chars so short words (ice, gas) are untouched. Applied to BOTH
    the corpus and the query, so "spikes" and "spike" collapse to one key.
    """
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def tokenize(text: str) -> list[str]:
    """Lowercase -> alphanumeric tokens -> drop stopwords -> plural-stem."""
    out = []
    for raw in _TOKEN_RE.findall((text or "").lower()):
        if raw in _STOPWORDS:
            continue
        out.append(_stem(raw))
    return out


def _expand_query(tokens: list[str]) -> list[str]:
    """Append synonym tokens for any query token that has a mapping."""
    expanded = list(tokens)
    for tok in tokens:
        for syn in _QUERY_SYNONYMS.get(tok, ()):  # note: syn is already literal
            expanded.append(_stem(syn))
    return expanded


def _doc_tokens(entry: dict) -> list[str]:
    """Weighted bag of tokens for one bank entry (name/tags heavier than summary)."""
    name_toks = tokenize(entry.get("name", "").replace("_", " "))
    tag_toks = []
    for tag in entry.get("tags", []) or []:
        tag_toks.extend(tokenize(str(tag).replace("_", " ")))
    summary_toks = tokenize(entry.get("summary", ""))
    return (name_toks * _W_NAME) + (tag_toks * _W_TAGS) + (summary_toks * _W_SUMMARY)


# --------------------------------------------------------------------------- #
# BM25 index (built once per bank content hash)
# --------------------------------------------------------------------------- #
class _Index:
    """A tiny BM25 index over the bank; rebuilt only when the catalog changes."""

    __slots__ = ("names", "tf", "df", "doc_len", "avgdl", "n", "idf")

    def __init__(self, bank: "_bank.Bank"):
        self.names = list(bank.parts)  # deterministic order (JSON order)
        self.tf: dict[str, dict[str, int]] = {}
        self.df: dict[str, int] = {}
        self.doc_len: dict[str, int] = {}
        for name in self.names:
            toks = _doc_tokens(bank.parts[name])
            counts: dict[str, int] = {}
            for t in toks:
                counts[t] = counts.get(t, 0) + 1
            self.tf[name] = counts
            self.doc_len[name] = len(toks)
            for t in counts:
                self.df[t] = self.df.get(t, 0) + 1
        self.n = len(self.names)
        self.avgdl = (sum(self.doc_len.values()) / self.n) if self.n else 0.0
        # Smoothed BM25 idf (always > 0 thanks to the +1 inside the log).
        self.idf = {
            t: math.log(1.0 + (self.n - df + 0.5) / (df + 0.5))
            for t, df in self.df.items()
        }

    def score_doc(self, name: str, query: list[str]) -> float:
        counts = self.tf[name]
        dl = self.doc_len[name]
        denom_norm = _K1 * (1.0 - _B + _B * (dl / self.avgdl if self.avgdl else 0.0))
        total = 0.0
        for t in query:
            f = counts.get(t)
            if not f:
                continue
            total += self.idf.get(t, 0.0) * (f * (_K1 + 1.0)) / (f + denom_norm)
        return total


_INDEX_CACHE: dict[str, _Index] = {}


def _index(bank=None) -> _Index:
    b = bank if bank is not None else _bank.load_bank("v1")
    key = f"{b.version}:{b.content_hash}"
    idx = _INDEX_CACHE.get(key)
    if idx is None:
        idx = _Index(b)
        _INDEX_CACHE[key] = idx
    return idx


# --------------------------------------------------------------------------- #
# Scoring / selection
# --------------------------------------------------------------------------- #
def score(prompt: str, *, bank=None) -> list[tuple[str, float]]:
    """Rank every bank part for ``prompt`` — ``[(name, score), ...]`` desc.

    Deterministic: ties break on name so the order is stable for a fixed bank.
    """
    idx = _index(bank)
    query = _expand_query(tokenize(prompt))
    scored = [(name, idx.score_doc(name, query)) for name in idx.names]
    scored.sort(key=lambda ns: (-ns[1], ns[0]))
    return scored


def retrieve(prompt: str, *, bank=None, k_max: int = K_MAX,
             threshold: float = SCORE_THRESHOLD) -> list[str]:
    """The selected Tier-1b part names for ``prompt`` (``[]`` = legend-only).

    Gate: if the top part's score is below ``threshold`` there is no confident
    thematic match, so we return nothing and the caller falls back to the
    legend-only prompt (erring high is safe — a missed part costs one telemetry
    entry, an injected junk part risks a distracted run; pipeline.md §D.2). Above
    the gate we keep parts scoring within ``_REL_FLOOR`` of the top, capped at
    ``k_max`` — a themed subset, not the whole catalog.
    """
    ranked = score(prompt, bank=bank)
    if not ranked or ranked[0][1] < threshold:
        return []
    top = ranked[0][1]
    floor = top * _REL_FLOOR
    selected = [name for name, sc in ranked if sc > 0.0 and sc >= floor]
    return selected[:k_max]


# --------------------------------------------------------------------------- #
# Menu rendering
# --------------------------------------------------------------------------- #
def _num(x) -> str:
    """Compact number: drop a trailing '.0' so ranges read cleanly."""
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def _overrides_summary(entry: dict) -> str:
    """One-line 'key lo-hi, ...' over an entry's whitelisted overrides."""
    parts = []
    for key, spec in (entry.get("overridable") or {}).items():
        rng = spec.get("range")
        if isinstance(rng, (list, tuple)) and len(rng) == 2:
            parts.append(f"{key} {_num(rng[0])}-{_num(rng[1])}")
        else:
            parts.append(key)
    return ", ".join(parts)


def _primary_body(entry: dict) -> dict:
    primary = entry.get("primary")
    for body in entry.get("assembly", []):
        if body.get("role") == primary:
            return body
    return (entry.get("assembly") or [{}])[0]


def _js_add_kwargs(entry: dict) -> str:
    """Canonical world.add options object for an entry's PRIMARY body (from the
    bank JSON), so a JS game can rebuild the calibrated preset by hand."""
    b = _primary_body(entry)
    shape = b.get("shape", "box")
    fields = [f"pos: [x, y]"]
    if shape == "box":
        size = b.get("size", [0, 0])
        fields.append(f"size: [{_num(size[0])}, {_num(size[1])}]")
    elif shape == "circle":
        fields.append(f"radius: {_num(b.get('radius', 0))}")
    elif shape == "segment":
        fields.append(f"a: [{_num(b.get('a', [0, 0])[0])}, {_num(b.get('a', [0, 0])[1])}]")
        fields.append(f"b: [{_num(b.get('b', [0, 0])[0])}, {_num(b.get('b', [0, 0])[1])}]")
    elif shape == "poly":
        fields.append("vertices: [...]")
    if not b.get("static", False):
        fields.append(f"mass: {_num(b.get('mass', 1.0))}")
    else:
        fields.append("static: true")
    if b.get("sensor", False):
        fields.append("sensor: true")
    fields.append(f"friction: {_num(b.get('friction', 0.7))}")
    fields.append(f"elasticity: {_num(b.get('elasticity', 0.3))}")
    return f'world.add("{entry["name"]}", "{shape}", {{ ' + ", ".join(fields) + " })"


def _py_line(entry: dict) -> str:
    over = _overrides_summary(entry)
    tail = f" | overrides: {over}" if over else ""
    return (f'  {entry["name"]} ({entry["category"]}) - {entry["summary"]}'
            f'{tail} | bodies: {len(entry.get("assembly", []))}')


def _godot_line(entry: dict) -> str:
    """Advisory godot menu line: name + category + summary + tunable ranges.

    No world.add / world.part call — the declarative godot spec builds bodies as
    DATA, so the menu is pure NAME + PHYSICS vocabulary, never a construction
    snippet (and never anchors the design to a bank part).
    """
    over = _overrides_summary(entry)
    tail = f" | tune: {over}" if over else ""
    return (f'  {entry["name"]} ({entry["category"]}) - {entry["summary"]}'
            f'{tail} | bodies: {len(entry.get("assembly", []))}')


# --- v2 menu (name | volume | role - objective | overrides) ------------------
# Role -> one-line objective phrase, mirroring ASSET_BANK_V2.md §3's role table.
# Local (not imported from the offline bank_tools grower) so the gen loop stays
# decoupled from the bank-authoring code.
_ROLE_OBJECTIVE = {
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

_V2_USAGE = (
    "Each line is an advisory VOLUME + ROLE: a certified footprint (shape and "
    "size in px) and the objective the runner instantiates for that role. Treat "
    "them as vocabulary for placing objects by objective and position - not a "
    "catalog to exhaust, and never a constraint on your design. Build bodies "
    "yourself; the bank only supplies shapes already proven correct.")


def _volume_str(entry: dict) -> str:
    """Compact one-line rendering of an entry's volume.footprint_2d."""
    fp = (entry.get("volume") or {}).get("footprint_2d") or {}
    shape = fp.get("shape")
    if shape == "box":
        w, h = fp.get("size", [0, 0])
        return f"box {_num(w)}x{_num(h)}"
    if shape == "circle":
        return f"circle r={_num(fp.get('radius', 0))}"
    if shape == "segment":
        (ax, ay), (bx, by) = fp.get("a", [0, 0]), fp.get("b", [0, 0])
        return f"segment {_num(abs(bx - ax))}x{_num(abs(by - ay))}"
    if shape == "poly":
        verts = fp.get("vertices", [])
        xs = [v[0] for v in verts] or [0]
        ys = [v[1] for v in verts] or [0]
        return f"poly {len(verts)}v {_num(max(xs) - min(xs))}x{_num(max(ys) - min(ys))}"
    return "n/a"


def _v2_line(entry: dict) -> str:
    role = entry.get("role", "?")
    objective = _ROLE_OBJECTIVE.get(role, "")
    over = _overrides_summary(entry)
    tail = f" | overrides: {over}" if over else ""
    return (f'  {entry["name"]} | volume: {_volume_str(entry)} '
            f'| role: {role} - {objective}{tail}')


def _js_line(entry: dict) -> str:
    n_bodies = len(entry.get("assembly", []))
    note = "" if n_bodies == 1 else f"  (+ {n_bodies - 1} more body/joint - see world.pin/pivot/spring)"
    return (f'  {entry["name"]} ({entry["category"]}) - {entry["summary"]}\n'
            f'    => {_js_add_kwargs(entry)}{note}')


def build_menu(names, engine="py", *, bank=None) -> str:
    """Render the Tier-1b menu text for the given part ``names`` and engine.

    py : ``world.part("<instance>", "<name>", ...)`` usage + description +
         category + key overridable ranges.
    js : world.js has no ``world.part()`` yet, so each line inlines the
         canonical ``world.add`` preset (physics params from the bank JSON) and
         states THE NAMING RULE — name the primary entity with the exact part
         name so the renderer can bind a sprite by name.
    godot : the declarative spec has no ``world.part()`` OR ``world.add`` — bodies
         are DATA — so the menu is ADVISORY name + physics VOCABULARY only. It
         never anchors the design to a bank part (Elias: menu is vocabulary, not a
         catalog) and its escape hatch is the spec's own raw body list.

    v2 catalog (``bank.is_v2``): one engine-agnostic ``name | volume: shape WxH |
    role: <role> - <objective> | overrides`` line — the advisory volume+role
    vocabulary of ASSET_BANK_V2.md §5.5 (no sprite-naming rule; there are no
    sprites in v2). The ``engine`` argument is ignored for a v2 bank.

    Returns ``""`` when ``names`` is empty (caller uses the legend-only prompt).
    """
    names = list(names or [])
    if not names:
        return ""
    b = bank if bank is not None else _bank.load_bank("v1")

    # v2 catalog: one advisory name | volume | role line, engine-agnostic
    # (ASSET_BANK_V2.md §5.5). The bank is ADVISORY vocabulary here; the designer
    # reads it and writes bodies itself (DECISIONS §1).
    if getattr(b, "is_v2", False):
        lines = [_v2_line(b.parts[n]) for n in names if n in b.parts]
        return _prompts.render_bank_menu("\n".join(lines), _V2_USAGE)

    eng = str(engine).lower()

    if eng == "godot":
        usage = ("Optional themed-NAME + calibrated-PHYSICS suggestions for this "
                 "prompt - advisory VOCABULARY, never a catalog to fit your design "
                 "to. Borrow a name (naming carries meaning) or a suggested number "
                 "if it serves your mechanic; otherwise ignore the whole list and "
                 "build the bodies your game needs.")
        lines = [_godot_line(b.parts[n]) for n in names if n in b.parts]
        return _prompts.render_bank_menu(
            "\n".join(lines), usage, escape_hatch=_prompts.ESCAPE_HATCH_GODOT)

    if eng == "js":
        usage = ("world.js has no world.part() yet: build each part with world.add "
                 "using the preset options below, and NAME the primary entity with "
                 "the EXACT part name shown (the renderer binds sprites by name). "
                 "Tune the numbers freely.")
        lines = [_js_line(b.parts[n]) for n in names if n in b.parts]
    else:
        usage = ('Instantiate any of these calibrated parts with '
                 'world.part("<your_instance_name>", "<part>", pos=(x, y), '
                 '<overrides>) - each is pre-certified, so the physics are already '
                 'correct. You still write control/act/success yourself.')
        lines = [_py_line(b.parts[n]) for n in names if n in b.parts]

    return _prompts.render_bank_menu("\n".join(lines), usage)


def retrieve_menu(prompt: str, engine: str = "py", *, bank=None):
    """One call for ``gamegen``: retrieve, then render the menu.

    -> ``(menu_text, menu_mode, names)`` where
       * ``menu_text`` is the rendered Tier-1b block, or ``None`` when nothing
         cleared the score gate (legend-only);
       * ``menu_mode`` is ``"menu"`` or ``"legend_only"``;
       * ``names`` is the pinned retrieved set (``[]`` for legend-only).
    """
    names = retrieve(prompt, bank=bank)
    if not names:
        return None, "legend_only", []
    return build_menu(names, engine, bank=bank), "menu", names
