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
    rules_godot.md /   - godot-specific siblings of rules/orientation/design: the
    orientation_godot.md  declarative-spec lane emits DATA (no world.add/control/rng
    design_block_godot.md idioms, no pymunk-only capabilities), so it gets clean,
                         high-level versions instead of the py/js code-module ones.
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
# The GDScript lane's SELF-CONTAINED contract: the GameAPI serve vocabulary the
# agent-written .gd game must implement. Minimal + contract-only — the craft
# KNOWLEDGE is injected separately (harness.gen.skill_context), never here.
API_GDSCRIPT = "api_gdscript.md"
RULES = "rules.md"
ORIENTATION = "orientation.md"
DESIGN_BLOCK = "design_block.md"
BANK_MENU_TMPL = "bank_menu.md.tmpl"

# The Godot lane emits a declarative JSON spec, not a code module, so the py/js
# rules/orientation/design sections (steeped in world.add / world.control / rng
# idioms and pymunk-only capabilities like world.set_gravity) do not fit it. It
# swaps them for godot-specific siblings that speak the spec vocabulary and carry
# HIGH-LEVEL structure only — the model infers the specifics.
RULES_GODOT = "rules_godot.md"
ORIENTATION_GODOT = "orientation_godot.md"
DESIGN_BLOCK_GODOT = "design_block_godot.md"

# The GDScript lane emits CODE (a .gd game class), not the declarative JSON spec, so
# it cannot reuse the spec-flavoured godot sections (predicate DSL, `on_step` kinds,
# ```json fence). api_gdscript.md is the SELF-CONTAINED engine section (contract +
# base-class services + the BANNED list + physics + failures); design_block_gdscript.md
# carries only the DESIGN output format with a ```gdscript fence.
DESIGN_BLOCK_GDSCRIPT = "design_block_gdscript.md"

# Every section a composed prompt draws from (used by tests to assert coverage).
SECTIONS = (CONTRACT, API_PY, API_JS, API_GODOT, API_GDSCRIPT, RULES, ORIENTATION,
            DESIGN_BLOCK, RULES_GODOT, ORIENTATION_GODOT, DESIGN_BLOCK_GODOT,
            DESIGN_BLOCK_GDSCRIPT, BANK_MENU_TMPL)

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
    # The GDScript lane emits a .gd game CLASS: real code, but only game logic through
    # the base-class services. api_gdscript.md / design_block_gdscript.md are written
    # without {..} placeholders (they are engine-specific and fully concrete), so these
    # values are a defensive fallback for any placeholder that might be added later.
    "gdscript": {
        "{lang}": "GDScript",
        "{fence}": "gdscript",
        "{artifact}": "GDScript game class",
        "{substrate}": "stock Godot Physics 2D behind a frozen host",
        "{dict_word}": "a Dictionary",
        "{false}": "false",
        "{import_rule}": ("No imports, no dynamic loads, no engine/OS/network/thread "
                          "access: you write ordinary GDScript game logic that reaches "
                          "the world ONLY through the base-class services."),
        "{rng_forbid}": "draw randomness from world_seed (the host pins the global "
                        "rng from it), never randomize()",
    },
}


def _engine_key(engine) -> str:
    e = str(engine).lower()
    if e == "js":
        return "js"
    if e == "godot":
        return "godot"
    if e == "gdscript":
        return "gdscript"
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


def gdscript_contract() -> str:
    """The GDScript lane's GameAPI contract text (api_gdscript.md), verbatim.

    Contract-only and engine-specific, so it carries no {..} placeholders and is
    returned as-is. This is the whole system prompt the gdscript lane sends before
    the advisory skill-knowledge section is appended (harness.gen.gamegen)."""
    return _read(API_GDSCRIPT).strip()


def compose(engine="py", menu_text=None) -> str:
    """Assemble the full system prompt for `engine`, optionally with a parts menu.

    Order: contract -> World API (per engine) -> rules -> orientation ->
    [Tier-1b parts menu] -> DESIGN output format. `menu_text` is the already
    rendered menu (harness.gen.retrieval.build_menu); when None the prompt is the
    legend-only/no-bank baseline. Deterministic: same inputs -> identical bytes.

    The Godot lane emits DATA, not code, so it swaps the code-module contract.md +
    api_*.md pair for the SELF-CONTAINED api_godot.md (its own spec contract) AND
    the py/js rules/orientation/design sections for godot-specific siblings that
    speak the declarative-spec vocabulary (no world.add / world.control / rng
    idioms). The menu splice point is shared.

    The GDScript lane emits CODE (a .gd game class implementing the GameAPI
    contract) but of a shape neither the code-module contract.md nor the
    spec-flavoured godot sections fit, so it composes the SELF-CONTAINED
    api_gdscript.md (contract + base-class services + the BANNED list + physics +
    failures) plus its own design_block_gdscript.md (```gdscript output format).
    """
    # PARKED (Elias, 2026-07-15): the SPEC-lane prompt library (py / js / godot) is
    # deleted — the project pivoted to agent-written GDScript verified through the
    # GameAPI serve contract (notes/engines/GDSCRIPT_LANE.md). Those section .md files
    # no longer exist, so every parked lane returns the sentinel. The GDScript lane is
    # LIVE: its self-contained api_gdscript.md + design_block_gdscript.md still compose.
    key = _engine_key(engine)
    if key != "gdscript":
        return ("[SPEC-LANE PARKED 2026-07-15] prompt library purged; the gdscript "
                "lane owns the surviving prompt surfaces - notes/engines/GDSCRIPT_LANE.md")

    # The code lane's api_gdscript.md is SELF-CONTAINED (contract + services +
    # BANNED list + physics + failures), so it composes only itself + its own
    # DESIGN output format. The menu splice point is shared.
    parts = [_render(_read(API_GDSCRIPT), key)]
    design = DESIGN_BLOCK_GDSCRIPT
    if menu_text:
        parts.append(menu_text.replace("\r\n", "\n").replace("\r", "\n"))
    parts.append(_render(_read(design), key))
    return "\n\n".join(p.strip() for p in parts)


# The advisory footer's escape-hatch clause is engine-specific: py/js point at the
# `world.add` construction API; the declarative godot lane has no such call, so the
# raw body list in the spec IS the escape hatch. Kept out of the static template so
# the godot menu never leaks a py/js idiom.
ESCAPE_HATCH_CODE = ("The full construction API (world.add and the joints) remains "
                     "fully available for anything the bank lacks.")
ESCAPE_HATCH_GODOT = ("Invent any bodies your game needs directly in the spec's body "
                      "list - the menu never constrains the design.")
ESCAPE_HATCH_GDSCRIPT = ("Invent any bodies your game needs directly with add_body / "
                         "add_static in build_world - the menu never constrains the design.")


def render_bank_menu(parts_block: str, usage_line: str,
                     escape_hatch: str = ESCAPE_HATCH_CODE) -> str:
    """Fill the Tier-1b menu template with the usage line and the per-part lines.

    `usage_line` (how to consume the parts: world.part for py, world.add presets
    for js, advisory name/physics vocabulary for godot) and `parts_block` (one
    rendered line per retrieved part) are produced by harness.gen.retrieval.build_menu
    from the pinned bank; this only frames them with the always-present header +
    advisory footer. `escape_hatch` fills the footer's construction-API clause so the
    declarative godot menu never advertises `world.add`.
    """
    # Template file purged 2026-07-15; the frame is inlined (menu survives for
    # the gdscript lane's advisory volume vocabulary).
    tmpl = ("## Parts menu (advisory)\n{usage_line}\n\n{parts_block}\n\n"
            "These are suggestions, never a catalog: {escape_hatch}\n")
    return (tmpl.replace("{usage_line}", usage_line)
                .replace("{parts}", parts_block)
                .replace("{escape_hatch}", escape_hatch).strip())
