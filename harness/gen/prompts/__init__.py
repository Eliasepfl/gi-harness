"""Modular system prompt for the open-ended generator (CONTRACTS §2/§3).

The system prompt used to be one giant string literal per engine. It is now
assembled from single-concern SECTION FILES on disk so each concern can be
edited on its own (Elias: "mieux les organiser et les separer en differents
prompts, pour mieux les changer separement"):

    contract.md        - engine-neutral game-design contract: substrate, the
                         required module symbols, the checkpoints contract, how
                         the runner drives a game. Uses {lang}-style placeholders.
    api_py.md/api_js.md - the concrete per-engine World API reference + the
                         module-format signatures + a structure-only stub.
    api_godot.md       - the Godot lane's SELF-CONTAINED engine section: the
                         declarative game-spec contract + the body/joint/act/
                         on_step tables + the predicate DSL + a worked example.
                         The Godot artifact is DATA (a JSON spec interpreted by a
                         frozen runner), not code, so it carries its own contract
                         framing instead of reusing the code-centric contract.md.
    rules.md           - the hard constraints (our accumulating lessons, incl.
                         the no-dead-action rule).
    orientation.md     - composition idioms ("invent a mechanic", proven patterns).
    design_block.md    - the DESIGN output format (Milestones + Parts used lines).
    bank_menu.md.tmpl  - the Tier-1b themed parts-menu template (slots filled at
                         run time by harness.gen.retrieval.build_menu).

`compose(engine, menu_text=None)` assembles the final system prompt
deterministically: the same (engine, menu_text) always yields byte-identical
output. `gamegen._SYSTEM_PROMPT` and `prompts_js.SYSTEM_PROMPT_JS` are thin
shims over `compose(...)`. The section .md files are folded into the run-integrity
manifest (harness.core.integrity) so a mid-run prompt edit invalidates the run
exactly like a base-code edit.
"""

from __future__ import annotations

import os

_DIR = os.path.dirname(os.path.abspath(__file__))

# Section files, in composition order (the menu, when present, is spliced between
# orientation and the DESIGN output format — see compose()).
CONTRACT = "contract.md"
API_PY = "api_py.md"
API_JS = "api_js.md"
API_GODOT = "api_godot.md"
RULES = "rules.md"
ORIENTATION = "orientation.md"
DESIGN_BLOCK = "design_block.md"
BANK_MENU_TMPL = "bank_menu.md.tmpl"

# Every section a composed prompt draws from (used by tests to assert coverage).
SECTIONS = (CONTRACT, API_PY, API_JS, API_GODOT, RULES, ORIENTATION,
            DESIGN_BLOCK, BANK_MENU_TMPL)

# Per-engine substitutions applied to the placeholder-bearing sections. Keys are
# braced tokens ({lang}, ...) so they never collide with real code braces
# (JS object literals, f-strings) that appear literally in the API sections.
_SUBS = {
    "py": {
        "{lang}": "Python",
        "{fence}": "python",
        "{artifact}": "module",
        "{substrate}": "pymunk underneath",
        "{dict_word}": "a dict",
        "{false}": "False",
        "{import_rule}": "No imports whatsoever. Your only tool is `world`.",
        "{rng_forbid}": "import random, never fake it with constants",
    },
    "js": {
        "{lang}": "JavaScript",
        "{fence}": "javascript",
        "{artifact}": "module",
        "{substrate}": "Planck.js / Box2D underneath",
        "{dict_word}": "a plain object",
        "{false}": "false",
        "{import_rule}": ("No require, import, exports, process, eval, Function(, "
                          "fs, or any module system. Your only tool is `world` "
                          "(and Math)."),
        "{rng_forbid}": "fake it with constants",
    },
    # The Godot lane emits DATA, not code: the engine-neutral rules.md / design_block.md
    # sections still render for it, so provide spec-flavoured values for their
    # placeholders. api_godot.md is the self-contained engine section (its own
    # contract); contract.md (the code-module contract) is NOT composed for godot.
    "godot": {
        "{lang}": "JSON",
        "{fence}": "json",
        "{artifact}": "JSON spec",
        "{substrate}": "stock Godot Physics 2D",
        "{dict_word}": "a JSON object",
        "{false}": "false",
        "{import_rule}": ("No code at all: you emit ONE JSON object that the frozen "
                          "runner interprets. The only expressions allowed are the "
                          "whitelisted predicate DSL."),
        "{rng_forbid}": "hard-code the layout to fake it",
    },
}


def _engine_key(engine) -> str:
    e = str(engine).lower()
    if e == "js":
        return "js"
    if e == "godot":
        return "godot"
    return "py"


def _read(name: str) -> str:
    """Read a section file, normalising newlines so composition is OS-stable."""
    with open(os.path.join(_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read().replace("\r\n", "\n").replace("\r", "\n")


def _render(text: str, engine: str) -> str:
    """Substitute the {..} placeholders for the target engine (deterministic)."""
    for token, value in _SUBS[_engine_key(engine)].items():
        text = text.replace(token, value)
    return text


def section_text(name: str, engine="py") -> str:
    """One rendered section (for tests / introspection)."""
    return _render(_read(name), engine).strip()


def compose(engine="py", menu_text=None) -> str:
    """Assemble the full system prompt for `engine`, optionally with a parts menu.

    Order: contract -> World API (per engine) -> rules -> orientation ->
    [Tier-1b parts menu] -> DESIGN output format. `menu_text` is the already
    rendered menu (harness.gen.retrieval.build_menu); when None the prompt is the
    legend-only/no-bank baseline. Deterministic: same inputs -> identical bytes.

    The Godot lane emits DATA, not code, so it swaps the code-module contract.md +
    api_*.md pair for the SELF-CONTAINED api_godot.md (its own spec contract); the
    rest of the pipeline (rules, orientation, menu, DESIGN block) is shared.
    """
    key = _engine_key(engine)
    if key == "godot":
        parts = [_render(_read(API_GODOT), key)]
    else:
        api = API_JS if key == "js" else API_PY
        parts = [
            _render(_read(CONTRACT), key),
            _render(_read(api), key),
        ]
    parts += [
        _render(_read(RULES), key),
        _render(_read(ORIENTATION), key),
    ]
    if menu_text:
        parts.append(menu_text.replace("\r\n", "\n").replace("\r", "\n"))
    parts.append(_render(_read(DESIGN_BLOCK), key))
    return "\n\n".join(p.strip() for p in parts)


def render_bank_menu(parts_block: str, usage_line: str) -> str:
    """Fill the Tier-1b menu template with the usage line and the per-part lines.

    `usage_line` (how to consume the parts: world.part for py, world.add presets
    for js) and `parts_block` (one rendered line per retrieved part) are produced
    by harness.gen.retrieval.build_menu from the pinned bank; this only frames
    them with the always-present header + advisory footer.
    """
    tmpl = _read(BANK_MENU_TMPL)
    return tmpl.replace("{usage_line}", usage_line).replace("{parts}", parts_block).strip()
