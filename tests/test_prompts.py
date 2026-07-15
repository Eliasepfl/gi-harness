"""Tests for harness.gen.prompts — the modular, section-file system prompt.

No network, no physics. Covers: every section is present in a composed prompt,
composition is deterministic (byte-stable) and engine-aware, the shims match
compose(), and the run-integrity manifest now freezes the prompt section files
exactly like base code (a mid-run prompt edit invalidates the run).
"""
from __future__ import annotations

import re

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


def test_godot_bank_menu_is_advisory_vocabulary_not_a_catalog():
    # The declarative godot lane emits DATA (no world.add / world.part), so its menu
    # is ADVISORY name+physics vocabulary, NOT the js/py construction catalog (Elias:
    # "the menu is advisory vocabulary, NEVER a catalog"). It therefore differs from
    # BOTH the js and py menus and carries none of their code idioms / sprite-binding.
    names = ["wrecking_ball"]
    godot = R.build_menu(names, "godot")
    assert godot != R.build_menu(names, "js")
    assert godot != R.build_menu(names, "py")
    # No py/js construction idioms and no retired sprite-binding rule in the godot menu.
    for banned in ("world.add", "world.part(", "renderer binds sprites by name",
                   "EXACT part name"):
        assert banned not in godot, banned
    # The retrieved part is still surfaced as advisory vocabulary + the raw-body
    # escape hatch stays visible.
    assert "wrecking_ball" in godot
    assert "advisory" in godot.lower()
    assert "spec's body list" in godot


# --- Track P: designer-prompt overhaul (variety + precision + mined physics) --

def test_godot_has_mined_physics_block_with_attribution():
    sp = P.compose("godot")
    # The mined "## Physics guidance" block is folded in, with its numeric priors.
    assert "## Physics guidance (mined)" in sp
    assert "SPEED PRIORS" in sp
    assert "SOLID THICK SUPPORTS" in sp
    # Apache-2.0 §4(d) NOTICE + MIT credit must ride along with the lifted prose.
    assert "awesome-gamedev-agent-skills" in sp
    assert "Apache-2.0" in sp
    assert "godogen" in sp


def test_godot_archetype_variety_section():
    sp = P.compose("godot")
    # A menu of genuinely DISTINCT, DSL-expressible mechanic archetypes...
    assert "pick ONE mechanic archetype and COMMIT" in sp
    for arch in ("PRECISION HOPS", "HEAVY-BODY MOMENTUM", "RISING-HAZARD ESCAPE",
                 "COLLECT-UNDER-PRESSURE", "SWITCH-GATED PATH", "TOPPLE / KNOCKDOWN",
                 "PENDULUM SWING"):
        assert arch in sp, arch
    # ...and an explicit instruction to name the committed archetype in metadata.
    assert "meta.archetype" in sp
    # Honesty about what the DSL cannot express (drops, not false promises).
    assert "NOT yet expressible" in sp


def test_godot_fun_and_precision_section():
    sp = P.compose("godot")
    assert "Fun and precision" in sp
    for rule in ("TIGHT FEEDBACK", "NEAR-MISS TENSION", "ESCALATION",
                 "TIMING IN TICKS", "MASS / IMPULSE COHERENCE"):
        assert rule in sp, rule
    # The forbidden anti-patterns, each stated with a reason.
    for anti in ("DEAD ACTIONS", "DECORATIVE BODIES THAT NEVER MATTER",
                 "SINGLE-ACTION WIN"):
        assert anti in sp, anti


def test_godot_common_failures_table():
    sp = P.compose("godot")
    assert "Common failures" in sp
    # A few of the silent-at-load / break-at-replay quirk rows.
    assert "G3 grounded-gated jump never fires" in sp
    assert "G1 containment escape" in sp
    assert "joint has no effect" in sp


def test_godot_designer_sections_ordered_stably():
    sp = P.compose("godot")
    # The vocabulary tables come first, then the mined physics, then the design
    # guidance (variety -> precision -> failures), then the worked example.
    order = [
        "the whitelisted expression DSL",      # predicates reference table
        "## Physics guidance (mined)",
        "pick ONE mechanic archetype and COMMIT",
        "Fun and precision",
        "Common failures",
        "Worked mini-example",
    ]
    idx = [sp.index(s) for s in order]
    assert idx == sorted(idx), idx


def test_godot_designer_sections_do_not_leak_into_js():
    js = P.compose("js")
    for marker in ("## Physics guidance (mined)", "PRECISION HOPS",
                   "meta.archetype", "Common failures - pass the loader",
                   "awesome-gamedev-agent-skills", "pick ONE mechanic archetype"):
        assert marker not in js, marker


# --- Track PROMPTS: legacy PURGE + godot-coherent designer prompt -------------

# Every pymunk/planck-era idiom the PIPELINE_MAP flagged as leaking onto the
# default (godot) path, plus the retired sprite-binding menu rule. A composed
# godot prompt is a declarative-spec brief and must contain NONE of them.
_BANNED_GODOT_IDIOMS = (
    "world.add", "world.rng", "world.control", "world.set_gravity",
    "world.steps", "world.touching", "world.on_contact", "world.remove",
    "world.query", "world.spring", "world.pivot", "world.part(",
    "WORLD_SIZE", "renderer binds sprites by name", "EXACT part name",
    "in act()",
)


def test_composed_godot_has_no_legacy_pyjs_idioms():
    sp = P.compose("godot")
    for tok in _BANNED_GODOT_IDIOMS:
        assert tok not in sp, tok


def test_composed_godot_with_menu_has_no_legacy_idioms():
    # The menu (retrieval.build_menu) is spliced into the per-run godot prompt;
    # it must be as clean as the section files — no world.add in the footer either.
    menu = R.build_menu(["wrecking_ball"], "godot")
    sp = P.compose("godot", menu)
    for tok in _BANNED_GODOT_IDIOMS:
        assert tok not in sp, tok


def test_godot_uses_its_own_section_files_not_the_pyjs_ones():
    # compose("godot") draws the godot-specific rules/orientation/design siblings,
    # never the py/js code-module sections (which are actively wrong for the spec).
    sp = P.compose("godot")
    # godot rules_godot.md — the moat, engine-neutral (no world.control/rng idiom).
    assert "Hard constraints" in sp
    assert "EXACTLY one controlled body" in sp
    assert "up to ~16 gameplay bodies" in sp          # relaxed body cap
    # godot orientation_godot.md — objective archetypes + composition idioms.
    assert "do NOT default to a platformer" in sp
    assert "Composition idioms" in sp
    # The py/js orientation's pymunk-only capability line must NOT be present.
    assert "flipping gravity" not in sp
    # godot design_block_godot.md — DESIGN format, json fence, no world.add.
    assert "DESIGN" in sp and "Milestones:" in sp and "Parts used:" in sp


def test_godot_sensors_raycast_documented():
    # The raycast observation-fan sensor (spec-v2) is taught in the api tables.
    sp = P.compose("godot")
    assert "raycast2d" in sp
    assert "attach_to" in sp
    assert "n_rays" in sp
    assert "cone_width_deg" in sp
    # It is DATA that never touches the win / physics (off-by-default).
    assert "never touches physics" in sp


def test_godot_steer_archetype_and_relaxed_blocklist():
    # The heading-control brick (thrust/torque + contained) landed, so a
    # steer-to-pose archetype is now offered and the motion blocklist is relaxed.
    sp = P.compose("godot")
    assert "STEER-TO-POSE" in sp
    assert "Heading control HAS landed" in sp
    # The verbs/predicate the brick relies on are in the api tables.
    assert '"verb": "thrust"' in sp or "thrust" in sp
    assert "torque" in sp
    assert "contained(" in sp
    # Still-honest about what remains out of scope (no false promises).
    assert "Still NOT yet expressible" in sp


def test_godot_worked_examples_are_multiple_and_distinct():
    # The single Ledge-Hop attractor is replaced by 2-3 structurally distinct ones.
    sp = P.compose("godot")
    assert "Ledge Hop" not in sp
    assert "Worked mini-example" in sp
    # Three distinct win-shapes appear, each a separate ```json spec.
    assert sp.count("```json") >= 3
    assert "Dock the Crate" in sp        # DELIVER (cargo pose)
    assert "Park the Rover" in sp        # STEER-TO-POSE (contained finish)
    assert "Beat the Flood" in sp        # RISING-HAZARD ESCAPE


def test_godot_folds_in_examples_corpus_distillation():
    # EXAMPLES_STRUCTURE_GUIDE §5 universals/differentiators, high-level only.
    sp = P.compose("godot")
    assert "Design universals" in sp
    assert "COMPOUND latch" in sp                  # success is pose AND stillness AND state
    assert "DIFFERENTIATOR family" in sp           # the anti-sameness lever
    assert "anti-sameness" in sp


# --- GDScript lane (agent-written .gd game class) -----------------------------
# The code lane emits REAL GDScript (a class implementing the GameAPI contract),
# verified through the serve contract (notes/engines/GDSCRIPT_LANE.md). Its
# api_gdscript.md is SELF-CONTAINED and, per Elias, examples-free: NO complete game
# ever appears (worked examples steer the small model into one attractor).

def _gdscript_blocks(sp):
    return re.findall(r"```gdscript\n(.*?)```", sp, re.DOTALL)


def test_all_sections_present_in_composed_gdscript():
    sp = P.compose("gdscript")
    # api_gdscript.md — the self-contained code contract + tables.
    assert "ONE GDScript file" in sp                # emit a .gd class, real code
    assert "extends GameBase" in sp                 # the base class it extends
    assert "DESIGN BEFORE YOU CODE" in sp           # design-before-code scaffold
    assert "DIVERSITY IS THE JOB" in sp             # the diversity mandate
    # The GameAPI contract tables: required methods + base-class services.
    for method in ("game_meta()", "build_world()", "on_action(action)",
                   "checkpoints()", "success()"):
        assert method in sp, method
    for service in ("add_body(name, opts)", "add_static(name, opts)",
                    "add_sensor(name, opts)", "control(name)", "set_gravity(vec)",
                    "impulse(name, vec)", "force(name, vec)", "set_velocity(name, vec)",
                    "torque(name, mag)", "thrust(name, mag)"):
        assert service in sp, service
    for query in ("grounded(name)", "contacts(a, b)", "contained(a, b)", "dist(a, b)"):
        assert query in sp, query
    assert "```gdscript" in sp                       # the placeholder-skeleton fence
    # design_block_gdscript.md — DESIGN output format, gdscript fence.
    assert "DESIGN" in sp and "Milestones:" in sp and "Parts used:" in sp
    assert "# Output format" in sp


def test_composed_gdscript_has_no_complete_game_examples():
    # Elias rejected worked examples as attractors: the guide carries ONLY a
    # placeholder FORM, never a copyable game. Grep-test that discipline.
    sp = P.compose("gdscript")
    blocks = _gdscript_blocks(sp)
    assert blocks, "expected at least the skeleton block"
    # The skeleton is a form, not a design: placeholder slots + pass bodies.
    skeleton = max(blocks, key=lambda b: b.count("func "))
    assert "<" in skeleton and "pass" in skeleton
    assert "shape to fill, not a game to copy" in sp
    # Every OTHER fenced gdscript block is a <=3-line syntax fragment.
    for b in blocks:
        if b is skeleton:
            continue
        nonblank = [ln for ln in b.splitlines() if ln.strip()]
        assert len(nonblank) <= 3, nonblank
    # No fenced block is a filled, copyable game (real construction + a concrete win).
    for b in blocks:
        filled_build = b.count("add_body(") + b.count("add_static(")
        concrete_win = ("func success" in b and "return <" not in b
                        and "return false" not in b)
        assert not (filled_build >= 2 and concrete_win), "a worked game leaked in"
    # The offline fixture's own game must never appear in the designer prompt.
    assert "Arm and Dock" not in sp


def test_gdscript_banned_list_states_determinism_and_sandbox_reasons():
    sp = P.compose("gdscript")
    assert "BANNED" in sp
    # The banned families, each named so the G0 scanner's finding is teachable.
    for banned in ("OS.", "FileAccess", "HTTPRequest", "StreamPeerTCP", "Thread",
                   "WorkerThreadPool", "Time.", "randi()", "randf()", "preload(",
                   "set_script", "Expression", "get_tree()"):
        assert banned in sp, banned
    # The WHY is the two hard rules, not style.
    flat = " ".join(sp.lower().split())
    assert "sandbox escape" in flat
    assert "nondeterministic" in flat or "nondeterminism" in flat
    assert "seeded" in flat            # use the host's seeded rng, not global randi


def test_gdscript_gravity_is_the_games_own_choice():
    # View guidance: gravity is set by the GAME in build_world, not chosen for it.
    sp = P.compose("gdscript")
    assert "set_gravity(Vector2(0, -900))" in sp       # side view anchor
    assert "set_gravity(Vector2.ZERO)" in sp           # topdown anchor
    flat = " ".join(sp.lower().split())
    assert "gravity is yours to set" in flat or "gravity choice you make" in flat


def test_compose_gdscript_deterministic_and_distinct():
    assert P.compose("gdscript") == P.compose("gdscript")
    assert P.compose("gdscript") != P.compose("godot")
    assert P.compose("gdscript") != P.compose("py")
    assert P.compose("gdscript") != P.compose("js")


def test_engine_key_maps_gdscript():
    assert P._engine_key("gdscript") == "gdscript"
    assert P._engine_key("GDSCRIPT") == "gdscript"


def test_menu_splices_into_gdscript_before_design_block():
    menu = "# Parts available for this prompt (optional menu)\nGD_MENU_MARKER\n"
    sp = P.compose("gdscript", menu)
    assert "GD_MENU_MARKER" in sp
    # The menu comes AFTER the api contract and BEFORE the DESIGN output format.
    assert sp.index("extends GameBase") < sp.index("GD_MENU_MARKER")
    assert sp.index("GD_MENU_MARKER") < sp.index("# Output format")
    assert "GD_MENU_MARKER" not in P.compose("gdscript")


def test_composed_gdscript_has_no_spec_or_pyjs_idioms_or_placeholders():
    sp = P.compose("gdscript")
    # The code lane must not leak the declarative-spec vocabulary...
    for spec_tok in ('"verb": "impulse"', "predicate DSL", "```json",
                     "whitelisted predicate", "on_step\":"):
        assert spec_tok not in sp, spec_tok
    # ...nor the py/js construction idioms.
    for pyjs in ("world.add", "world.part(", "world.control", "pymunk", "Planck.js"):
        assert pyjs not in sp, pyjs
    # Every {..} placeholder the shared substitution carries is resolved.
    for token in ("{lang}", "{fence}", "{artifact}", "{substrate}", "{false}",
                  "{import_rule}", "{rng_forbid}", "{dict_word}"):
        assert token not in sp, token


def test_gdscript_physics_and_failures_are_self_contained():
    sp = P.compose("gdscript")
    # Physics guidance (derive sizes, do not memorize) + the code-gate failure table.
    assert "Physics the host enforces" in sp
    assert "TUNNELLING" in sp
    assert "Common failures" in sp
    for row in ("G0 parse error", "G0 banned API", "G0 contract probe",
                "G1 containment escape", "G1 dead action", "G3 grounded-gated jump"):
        assert row in sp, row


def test_gdscript_integrity_freezes_its_prompt_sections():
    """The real repo: the gdscript section files are tracked base content, so a
    mid-run edit invalidates the run exactly like the other prompt sections."""
    root = INT.__file__.rsplit("harness", 1)[0].rstrip("\\/")
    snap = INT.snapshot(root)
    assert "harness/gen/prompts/api_gdscript.md" in snap
    assert "harness/gen/prompts/design_block_gdscript.md" in snap
