"""Tests for harness.gen.prompts — the modular, section-file system prompt.

No network, no physics. Covers: every section is present in a composed prompt,
composition is deterministic (byte-stable) and engine-aware, the shims match
compose(), and the run-integrity manifest now freezes the prompt section files
exactly like base code (a mid-run prompt edit invalidates the run).
"""
from __future__ import annotations

from harness.gen import prompts as P
from harness.gen import retrieval as R
from harness.core import integrity as INT


# --- Section coverage ---------------------------------------------------------

def test_all_sections_present_in_composed_py():
    sp = P.compose("py")
    # contract.md — substrate + module symbols + checkpoints contract + how-it-runs.
    assert "minimal 2D physics substrate (pymunk underneath)" in sp
    assert "checkpoints(world)" in sp
    assert "Milestones are how the harness will tell you" in sp
    assert "each decision tick calls act(world" in sp
    # api_py.md — the Python World API + a structure-only stub.
    assert "world.add(name, shape=" in sp
    assert "Structure-only stub" in sp
    assert "```python" in sp
    # rules.md — the hard constraints incl. the no-dead-action lesson.
    assert "Hard constraints" in sp
    # orientation.md — invent-a-mechanic + composition idioms.
    assert "do NOT default to a platformer" in sp
    assert "Composition idioms" in sp
    # design_block.md — DESIGN output format incl. Milestones + Parts used lines.
    assert "DESIGN" in sp and "Milestones:" in sp and "Parts used:" in sp


def test_all_sections_present_in_composed_js():
    sp = P.compose("js")
    assert "Planck.js / Box2D underneath" in sp
    assert "world.add(name, shape, opts)" in sp
    assert "```javascript" in sp
    assert "No require, import, exports" in sp
    assert "do NOT default to a platformer" in sp
    assert "Parts used:" in sp


def test_no_dead_action_rule_is_explicit():
    # The #1 recurring lesson must be stated in words, both engines.
    for engine in ("py", "js"):
        sp = P.compose(engine)
        flat = " ".join(sp.lower().split())
        assert "never include a" in flat
        for dead in ("wait", "idle", "noop", "stay"):
            assert dead in flat, (engine, dead)
        assert "every action must do something" in flat


# --- Determinism / engine awareness -------------------------------------------

def test_compose_is_deterministic():
    assert P.compose("py") == P.compose("py")
    assert P.compose("js") == P.compose("js")
    menu = "# Parts available for this prompt (optional menu)\nfoo\n"
    assert P.compose("py", menu) == P.compose("py", menu)


def test_compose_engine_differs():
    py, js = P.compose("py"), P.compose("js")
    assert py != js
    # Python placeholders resolved to Python, not left raw or cross-substituted.
    assert "{lang}" not in py and "{substrate}" not in py
    assert "pymunk underneath" in py and "Planck.js" not in py
    assert "Planck.js / Box2D underneath" in js and "pymunk underneath" not in js


def test_menu_is_spliced_before_design_block():
    menu = "# Parts available for this prompt (optional menu)\nMENU_MARKER_LINE\n"
    sp = P.compose("py", menu)
    assert "MENU_MARKER_LINE" in sp
    # The menu comes AFTER orientation and BEFORE the DESIGN output format.
    assert sp.index("Composition idioms") < sp.index("MENU_MARKER_LINE")
    assert sp.index("MENU_MARKER_LINE") < sp.index("# Output format")
    # Without a menu the marker is absent and the prompt is shorter.
    assert "MENU_MARKER_LINE" not in P.compose("py")
    assert len(P.compose("py", menu)) > len(P.compose("py"))


def test_shims_match_compose():
    from harness.gen import gamegen as GG
    from harness.gen.prompts_js import SYSTEM_PROMPT_JS
    assert GG._SYSTEM_PROMPT == P.compose("py")
    assert SYSTEM_PROMPT_JS == P.compose("js")


def test_section_text_helper_renders_placeholders():
    # section_text is a single rendered section for introspection/tests.
    contract_py = P.section_text(P.CONTRACT, "py")
    assert "{lang}" not in contract_py and "Python" in contract_py
    contract_js = P.section_text(P.CONTRACT, "js")
    assert "JavaScript" in contract_js


# --- Run integrity now freezes the prompt section files -----------------------

def _tree_with_prompts(root):
    """A minimal repo-shaped tree that includes harness/gen/prompts/*.md."""
    pdir = root / "harness" / "gen" / "prompts"
    pdir.mkdir(parents=True)
    (root / "harness" / "core").mkdir(parents=True)
    (root / "harness" / "core" / "bank.py").write_text("X", encoding="utf-8")
    (pdir / "__init__.py").write_text("# pkg", encoding="utf-8")
    (pdir / "contract.md").write_text("CONTRACT SECTION", encoding="utf-8")
    (pdir / "api_py.md").write_text("PY API", encoding="utf-8")
    (pdir / "bank_menu.md.tmpl").write_text("{parts}", encoding="utf-8")
    (root / "CONTRACTS.md").write_text("SPEC", encoding="utf-8")


def test_integrity_tracks_prompt_section_files(tmp_path):
    _tree_with_prompts(tmp_path)
    snap = INT.snapshot(str(tmp_path))
    # Both .md and .md.tmpl prompt sections are tracked base content.
    assert "harness/gen/prompts/contract.md" in snap
    assert "harness/gen/prompts/api_py.md" in snap
    assert "harness/gen/prompts/bank_menu.md.tmpl" in snap
    # The package __init__.py is tracked as a normal .py.
    assert "harness/gen/prompts/__init__.py" in snap


def test_integrity_prompt_edit_is_a_violation(tmp_path):
    _tree_with_prompts(tmp_path)
    before = INT.snapshot(str(tmp_path))
    # Editing a prompt section mid-run must invalidate the run, like base code.
    (tmp_path / "harness" / "gen" / "prompts" / "contract.md").write_text(
        "CONTRACT SECTION (edited mid-run)", encoding="utf-8")
    assert INT.violations(before, str(tmp_path)) == [
        "harness/gen/prompts/contract.md"]


def test_integrity_real_repo_covers_a_prompt_md():
    """Sanity check against the real repo: the live prompt sections are frozen."""
    root = INT.__file__.rsplit("harness", 1)[0].rstrip("\\/")
    snap = INT.snapshot(root)
    assert "harness/gen/prompts/contract.md" in snap
    assert "harness/gen/prompts/rules.md" in snap
    # The godot engine section is frozen base content just like the others.
    assert "harness/gen/prompts/api_godot.md" in snap


# --- Godot lane (declarative JSON spec) ---------------------------------------

def test_all_sections_present_in_composed_godot():
    sp = P.compose("godot")
    # api_godot.md — the self-contained spec contract + format + worked example.
    assert "ONE JSON object" in sp                 # emit DATA, not code
    assert "a single FROZEN, audited runner interprets your spec" in sp
    assert "predicates" in sp and "on_step" in sp   # spec section tables
    assert "grounded(" in sp                        # the predicate DSL
    assert '"verb": "impulse"' in sp                # the act verb vocabulary
    assert "```json" in sp                          # the worked mini-example fence
    # rules.md — reused untouched (engine-neutral hard constraints).
    assert "Hard constraints" in sp
    # orientation.md — reused untouched.
    assert "do NOT default to a platformer" in sp
    assert "Composition idioms" in sp
    # design_block.md — DESIGN output format, fence resolved to json.
    assert "DESIGN" in sp and "Milestones:" in sp and "Parts used:" in sp
    assert "```json" in sp


def test_composed_godot_has_no_code_contract_or_raw_placeholders():
    sp = P.compose("godot")
    # The code-module contract.md and the py/js API sections are NOT composed
    # for godot (it carries its own spec contract in api_godot.md).
    assert "pymunk underneath" not in sp
    assert "Planck.js / Box2D underneath" not in sp
    assert "world.add(name, shape=" not in sp        # api_py signature
    # Every {..} placeholder the shared sections carry is resolved for godot.
    for token in ("{lang}", "{fence}", "{artifact}", "{substrate}", "{false}",
                  "{import_rule}", "{rng_forbid}", "{dict_word}"):
        assert token not in sp, token


def test_compose_godot_deterministic_and_distinct():
    assert P.compose("godot") == P.compose("godot")
    assert P.compose("godot") != P.compose("py")
    assert P.compose("godot") != P.compose("js")


def test_menu_splices_into_godot_before_design_block():
    menu = "# Parts available for this prompt (optional menu)\nGODOT_MENU_MARKER\n"
    sp = P.compose("godot", menu)
    assert "GODOT_MENU_MARKER" in sp
    # The menu comes AFTER orientation and BEFORE the DESIGN output format.
    assert sp.index("Composition idioms") < sp.index("GODOT_MENU_MARKER")
    assert sp.index("GODOT_MENU_MARKER") < sp.index("# Output format")
    assert "GODOT_MENU_MARKER" not in P.compose("godot")


def test_engine_key_maps_godot():
    assert P._engine_key("godot") == "godot"
    assert P._engine_key("GODOT") == "godot"
    # Rendered godot rules resolve the boolean to JSON `false`, not Python `False`.
    rules = P.section_text(P.RULES, "godot")
    assert "false" in rules and "{false}" not in rules


def test_godot_bank_menu_reuses_the_js_renderer():
    # "spec body NAMES drive sprite skinning exactly like js" — so the godot menu
    # is byte-identical to the js menu (same naming-rule usage line + preset lines).
    names = ["wrecking_ball"]
    assert R.build_menu(names, "godot") == R.build_menu(names, "js")
    assert R.build_menu(names, "godot") != R.build_menu(names, "py")
