# CONTRACTS — module interfaces (v2: generated games)

> Normative document for parallel implementation. Each module is built by a different
> agent: **only touch your assigned files** (§8), code against these exact signatures.
> Python 3.12, pymunk 7.3.0 (installed — verify the real API empirically, don't code from memory).
> ALL code, comments, docstrings, prompts and error messages are in ENGLISH.
> Conventions: world 800×600, y UP, default gravity (0, -900), dt = 1/60, deterministic seeds.
> No pixels anywhere in verification: everything reads engine state.

## 0. Design shift v1 → v2

v1 (legacy, keep working): scenes written against a curated `SceneSDK` (add_platform,
spawn_agent, add_zone) — the harness pre-decided the genre; the LLM filled in parameters.

v2 (this contract): the LLM designs a WHOLE GAME against a minimal physics substrate
(`World`). The game declares its own action set, writes the action semantics, its own
per-tick rules, and its own win/lose conditions. The harness keeps only:
substrate + sandbox + universal oracles (checks no game can redefine) + solvability probe.

## 1. `harness/world.py` — World (minimal substrate)

```python
class World:
    """Instrumented minimal wrapper over pymunk. The ONLY object game code sees."""

    def __init__(self, seed: int = 0, size: tuple[int, int] = (800, 600),
                 gravity: tuple[float, float] = (0, -900)): ...

    # ---- construction (used by build / on_step) ----
    def add(self, name: str, shape: str = "box", *, pos, size=None, radius=None,
            a=None, b=None, vertices=None, mass: float = 1.0, static: bool = False,
            sensor: bool = False, friction: float = 0.7, elasticity: float = 0.3,
            velocity=(0, 0), angle: float = 0.0, locked_rotation: bool = False) -> str
        # shape in {"box","circle","segment","poly"}; box needs size=(w,h);
        # circle needs radius; segment needs a=(x,y), b=(x,y); poly needs vertices.
        # static=True -> STATIC body; sensor=True -> no physical collision.
    def remove(self, name: str) -> None
    def pin(self, a: str, b: str, anchor_a=None, anchor_b=None) -> None       # PinJoint
    def pivot(self, a: str, b: str, point) -> None                            # PivotJoint at world point
    def spring(self, a: str, b: str, rest_length: float, stiffness: float,
               damping: float, anchor_a=None, anchor_b=None) -> None          # DampedSpring
    def set_gravity(self, gx: float, gy: float) -> None
    def control(self, name: str) -> None       # designate THE controlled body (required, exactly one)

    # ---- game dynamics (used by act / on_step) ----
    def impulse(self, name: str, vec) -> None
    def force(self, name: str, vec) -> None
    def set_velocity(self, name: str, vec) -> None
    def set_flag(self, key: str, value) -> None
    def flag(self, key: str, default=None)
    def on_contact(self, a: str, b: str, flag: str, once: bool = True) -> None
    @property
    def rng(self) -> random.Random             # the ONLY allowed randomness (seeded)
    @property
    def steps(self) -> int                     # physics steps elapsed

    # ---- pure queries (used by success / failure / policies / verifier) ----
    def entities(self) -> list[str]
    def query(self, name: str) -> dict   # {"pos":[x,y],"vel":[vx,vy],"angle":a,"angular_vel":w,
                                         #  "bbox":[l,b,r,t],"shape":"box|circle|segment|poly",
                                         #  "static":bool,"sensor":bool,"controlled":bool}
    def contacts(self, a: str, b: str) -> bool
    def touching(self, name: str) -> list[str]      # names in contact with `name` (non-sensor)
    def grounded(self, name: str) -> bool           # supported from below (contact normal ~vertical)
    def in_bounds(self, name: str, margin: float = 0.0) -> bool
    def penetration_depth(self, a: str, b: str) -> float    # 0.0 if either shape is a sensor

    # ---- harness side (verifier / renderer; NOT documented to the game LLM) ----
    def step(self, n: int = 1) -> None         # n * space.step(1/60), NaN/explosion sentinel
    def snapshot(self) -> dict                 # {name: {"pos":..,"vel":..,"angle":..}}
    def events(self) -> list[dict]             # [{"type":"flag_set","key":..,"step":n}, ...]
    def teleport(self, name: str, pos) -> None # + reindex_shapes_for_body (future L3 injection)
    def kinetic_energy(self, names=None) -> float
    def controlled(self) -> str | None         # name of the controlled body
```

## 2. Game module format (generated artifact)

A Python module containing EXACTLY these symbols (no imports — only `world` is used):

```python
TITLE = "short game title"
PROMPT = "the original user prompt"
ACTIONS = ["thrust", "tilt_left", "tilt_right", "wait"]   # 2..8 short strings, game-chosen

def build(world):
    """Create all entities; MUST call world.control(<name>) on exactly one dynamic body."""

def act(world, action: str):
    """Apply ONE action's effect (impulse/force/flags). Called once per decision tick."""

def on_step(world):
    """OPTIONAL. Game rules evaluated once per physics step (timers, hazards, scoring)."""

def success(world) -> bool:
    """PURE predicate: reads state only, no side effects."""

def failure(world) -> bool:
    """OPTIONAL. Pure predicate: lose condition."""

def checkpoints(world) -> dict[str, bool]:
    """REQUIRED (v2.1 amendment). 1..6 ordered milestone predicates — insertion
    order = intended progression toward success. Pure like success; every value
    False at t=0. Keys are short snake_case identifiers. The game decomposes its
    OWN rules into these milestones; the harness only checks the general frame."""
```

Runner semantics (shared by verifier, probe, replay — implement once in `gameverify`):
decision tick = `act(world, action)` then K=6 × [`world.step(1)` then `on_step(world)`],
check `failure` then `success` after each tick. Episode ends on success/failure/step budget.

CHECKPOINT LATCHING (v2.1): the runner (not the game) latches milestones — after each
tick it evaluates `checkpoints(world)` and records the FIRST tick each key became True
(`{"checkpoints": {name: tick|None}}` in the episode dict). Latching lives in the runner
so a milestone counts as "passed" even if the underlying state later regresses (a ship
may leave the pad it once touched); game predicates stay pure and stateless.
`success` remains the terminal authority: a binary, unshaped certificate (OMNI-EPIC
lesson — hack-resistant). Checkpoints are the *structure of progress between t=0 and
success*, and double as the dense programmatic signal a reward model can later train on.

## 3. `harness/gamegen.py` — open-ended generator

```python
def generate_game(prompt: str, out_dir: str = "scenes/games", backend: str = "auto",
                  max_repairs: int = 4) -> dict
# -> {"game_path": str|None, "attempts": [...], "verdict":
#     "COMPLETED|PARTIAL|ENV_ERROR|GOAL_ERROR|UNSOLVED", "backend": ..., "design": str}
```
- Backend "anthropic": `anthropic.Anthropic()` zero-arg; `messages.create(model="claude-opus-4-8",
  max_tokens=16000, thinking={"type":"adaptive"}, ...)`. NO temperature/top_p/prefill (400).
- THE PROMPT IS OPEN-ENDED — this is the point of v2:
  * ask first for a short DESIGN block (theme, entities, the mechanic twist, actions, win/lose),
    then the code in one ```python block;
  * give the World API reference (§1 construction/dynamics/queries parts ONLY) + format (§2);
  * NO complete example game (a 6-line skeletal stub at most, explicitly marked
    "structure only — do NOT imitate its design");
  * explicitly invite variety: custom gravity, joints (pin/pivot/spring), sensors,
    timers via world.steps, moving hazards, counters via flags — "invent a mechanic";
  * constraints: ≤ 14 bodies, 2..8 actions, only world.rng for randomness, success must
    be false at t=0, require player agency, and be reachable within ~800 physics steps;
  * CHECKPOINTS (v2.1): the game MUST also define `checkpoints(world)` — 1..6 ordered
    milestone predicates decomposing ITS OWN rules into progression steps (listed in the
    DESIGN block too). Tell the model: "milestones are how the harness will tell you
    exactly where your game is stuck if it fails — make them meaningful stages, not
    restatements of success".
- Repair loop: feed the gameverify JSON report + hint back, ≤ max_repairs (compile cap 5,
  then discard — OMNI-EPIC pattern). UNSOLVED (G3) counts as a failure with the hint
  "no random rollout reached success — make the goal easier to reach".
- Backend "template": 2 tiny built-in v2 games (offline tests/demo fallback only).

## 4. `harness/gameverify.py` — universal oracles (game-agnostic)

The game defines WHAT the game is; it can never define what SANITY is. These checks are
outside the game's reach:

```python
def verify_game(game_path: str, sandboxed: bool = True, *, world_factory=None) -> dict
```
Report schema:
```python
{"passed": bool,
 "failure_class": None|"ENV_ERROR"|"GOAL_ERROR"|"UNSOLVED",
 "layers": {
   "G0_static":  {...},   # sandbox scan; module loads; required symbols; ACTIONS is list[str] 2..8;
                          # build runs; exactly one controlled dynamic body; >= 2 entities;
                          # no initial dynamic penetration (> 1.5 px, sensors excluded);
                          # dynamic bodies in bounds
   "G1_rollout": {...},   # 600-step noop rollout: no NaN/explosion; no dynamic body escapes
                          # (margin 200 px, removed bodies OK); success NEVER true under noop
                          # (agency check); determinism (two identical seeded runs -> same
                          # final snapshot); action efficacy: each declared action, held from
                          # t0 for 90 steps, diverges from the noop baseline (dead-action check)
   "G2_goal":    {...},   # success callable -> bool; false at t=0; pure (2 calls, snapshot
                          # unchanged); failure (if present) same checks; failure false at t=0;
                          # v2.1: checkpoints(world) callable -> dict[str,bool], 1..6 entries,
                          # ALL False at t=0, pure (same purity protocol), snake_case keys
   "G3_solve":   {...},   # random-search probe: E=40 episodes × H=120 decision ticks,
                          # seeded macro-actions (hold 1-4 ticks); stop at first success;
                          # -> witness {"seed": s, "actions": [...], "ticks": n} stored in report;
                          # anti-triviality: witness needs >= 5 decision ticks (else "trivial"
                          # -> fail with hint); no witness -> failure_class "UNSOLVED".
                          # v2.1 checkpoint semantics inside G3:
                          #  - the runner latches first-True tick per checkpoint every episode;
                          #  - on the witness: every checkpoint must have fired at/before the
                          #    success tick; never-fired = dead milestone -> GOAL_ERROR + hint;
                          #  - empirical firing order vs declared order mismatch -> non-fatal
                          #    entry in "warnings" (the model mis-ordered its milestones);
                          #  - CHECKPOINT-GUIDED SECOND PASS: if the first E episodes all fail
                          #    but some reached milestone k, run E2=20 more episodes reusing the
                          #    best episode's action prefix (up to its last checkpoint tick) +
                          #    random continuation — cheap beam step for multi-stage games;
                          #  - UNSOLVED diagnosis -> "progress": {"reach_counts": {name: episodes
                          #    that latched it}, "stuck_after": <last milestone reached>|None} and
                          #    hint "N/E episodes reached '<k>', none reached '<k+1>' — the game
                          #    is stuck between <k> and <k+1>"
 },
 "hint": "one English sentence naming the offending entities/actions/milestones",
 "warnings": [ ... ],
 "progress": {...} | None,
 "witness": {..., "checkpoints": {name: tick|None}} | None}
```
Also expose `run_episode(game, world, actions_iter, max_ticks) -> dict` (the §2 runner) —
single implementation reused by G1/G3, the replay renderer, and future policies.
Sandbox: reuse `harness/sandbox.py` (AST scan; only `math` importable; worker job "gameverify").
Constants live in `gameverify.py`, marked `[eng.]` where they are engineering choices.

## 5. `harness/render.py` — generic replay renderer (GIFs for demos/site)

```python
def replay_gif(game_path: str, out_path: str, *, actions=None, seed=0, label=None,
               max_ticks=400, scale=0.6, every=2) -> dict   # {"ticks":..,"result":..}
```
- If `actions` is None: replay the G3 witness (re-run verify or accept a witness dict).
- Generic drawing from `world.query()` only: circle/box/poly/segment; palette:
  controlled=green, sensor zones=amber outline+translucent fill, static=slate,
  other dynamics=blue/violet (stable per-name); grid background; step counter + label.
- Anti-clipping (timeboxed, visual only): draw dynamic bodies masked by static solids
  (erase the overlap region) and inset every drawn bbox by 1 px. Physics untouched.
- v2.1 (optional, only if cheap): when a checkpoint latches during replay, flash its
  name briefly under the label — milestones become visible in the demo GIFs.
- No pygame dependency; PIL only.

## 6. CLI (`harness/cli.py`)

Keep v1 commands working. Add:
```
python -m harness game new "prompt..." [--backend auto|anthropic|template]
python -m harness game verify scenes/games/<file>.py
python -m harness game replay scenes/games/<file>.py [--gif out.gif]
```
`game new` prints verdict + design block + path; `replay` uses the witness. `--json` everywhere.

## 7. Language & legacy

- Everything new in English. v1 files (sdk.py, navigator.py, verifier/, sandbox.py,
  templates.py, generator.py, scenes/examples, cli.py) get their French comments/docstrings/
  messages translated to English with ZERO behavior change — the 68 existing tests must stay
  green (update test assertions only if they matched French strings).
- v1 stays functional (day-1 demos, legacy tests). New work targets v2 only.

## 8. File assignment (do NOT stray)

| Agent | Owns (create/edit) |
|---|---|
| E | `harness/world.py`, `tests/test_world.py`; translate `harness/sdk.py`, `scenes/examples/*.py` |
| F | `harness/gameverify.py`, `tests/test_gameverify.py`; translate `harness/sandbox.py`, `harness/verifier/*`, `tests/test_verifier.py` |
| G | `harness/gamegen.py`, `tests/test_gamegen.py`; translate `harness/generator.py`, `harness/templates.py`, `tests/test_generator.py` |
| H | `harness/render.py`, `tests/test_render.py`, `harness/cli.py` (+ translate), translate `harness/navigator.py` + `tests/test_navigator.py`; site day-1 layout (`C:\Users\Elias\OneDrive\Bureau\gi-site\day1\index.html`) |

`harness/__init__.py`, `requirements.txt`, `CONTRACTS.md`: orchestrator only.
Each agent runs its own tests (`python -m pytest tests/test_<x>.py -q`) plus the legacy suite
for files it translated, before finishing. If another agent's module doesn't exist yet:
mock it in your tests (FakeWorld etc.), never implement it.
