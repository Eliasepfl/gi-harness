"""Navigator — observation-state -> action loop to test a scene's solvability.

The "greedy" policy (v1, NO LLM) is a solvability demonstration: it aims at the
relevant target (zone / object of get_success) and unsticks itself with jumps and
small seeded perturbations. The "llm" policy is a stub (phase 4).

No pixels: the navigator only talks to the engine state via SceneSDK.query().
"""

from __future__ import annotations

import json
import random
import sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace

# ---- heuristic constants (eng., to calibrate) ----------------------------
CONTROL_STEPS = 6        # K: number of engine steps between two decisions (reactive control)
DEADZONE = 8.0           # px: horizontal tolerance before moving
BEHIND_OFFSET = 42.0     # px: placement distance behind the object to push
NEAR_X = 34.0            # px: "plumb" with the target to attempt a climb
CLIMB_DY = 22.0          # px: target judged "higher" beyond this delta
STUCK_EPS = 3.0          # px: horizontal progress judged null below this
STUCK_WINDOW = 6         # decisions (~36 steps) over which progress is measured
STUCK_LIMIT = 5          # stuck decisions before a random perturbation


class SceneError(Exception):
    """Loading / validation error of a scene artifact."""


# ==========================================================================
#  Scene loading
# ==========================================================================
def _load_scene(scene_path: str):
    """Load a scene module into a restricted namespace.

    Reuses harness.sandbox.scan_source (lazy import) to reject any forbidden
    import/call. If the sandbox is not present yet (parallel dev), we execute
    directly — strong security then rests on the harness.sandbox subprocess,
    outside this module's scope.
    """
    src = Path(scene_path).read_text(encoding="utf-8")

    try:
        from harness.sandbox import scan_source  # lazy import
    except Exception:
        scan_source = None
    if scan_source is not None:
        violations = scan_source(src)
        if violations:
            raise SceneError(f"scene rejected by the sandbox: {violations}")

    ns: dict = {"__name__": "scene", "__builtins__": __builtins__}
    try:
        exec(compile(src, str(scene_path), "exec"), ns)
    except Exception as exc:  # noqa: BLE001
        raise SceneError(f"scene execution failed: {exc}") from exc

    for required in ("build_scene", "get_success"):
        if not callable(ns.get(required)):
            raise SceneError(f"invalid scene: function '{required}' missing")

    return SimpleNamespace(
        build_scene=ns["build_scene"],
        get_success=ns["get_success"],
        SCENE_DESCRIPTION=ns.get("SCENE_DESCRIPTION", ""),
        AVAILABLE_ACTIONS=ns.get("AVAILABLE_ACTIONS", ["left", "right", "jump", "noop"]),
    )


# ==========================================================================
#  Text observation (will feed the LLM policy)
# ==========================================================================
def _xy(v):
    """Normalize an (x, y) pair from list/tuple/object with .x/.y."""
    if v is None:
        return (0.0, 0.0)
    if hasattr(v, "x") and hasattr(v, "y"):
        return (float(v.x), float(v.y))
    return (float(v[0]), float(v[1]))


def _round2(v):
    x, y = _xy(v)
    return [round(x, 2), round(y, 2)]


def _collect_flags(sdk) -> dict:
    """Rebuild the set flags, via the event log or an attribute."""
    flags: dict = {}
    try:
        for ev in sdk.events():
            if ev.get("type") == "flag_set":
                flags[ev.get("key")] = ev.get("value", True)
    except Exception:  # noqa: BLE001
        for attr in ("_flags", "flags"):
            f = getattr(sdk, attr, None)
            if isinstance(f, dict):
                flags = {k: v for k, v in f.items()}
                break
    return flags


def _current_step(sdk):
    for attr in ("step_count", "_step", "_steps", "n_steps"):
        val = getattr(sdk, attr, None)
        if isinstance(val, int):
            return val
    return None


def observe_text(sdk) -> str:
    """Compact JSON serialization of the engine state.

    -> {"entities": {name: {pos, vel, is_agent, body_type}}, "flags": {...}, "step": n}
    """
    entities: dict = {}
    for name in sdk.list_entities():
        q = sdk.query(name)
        entities[name] = {
            "pos": _round2(q.get("pos")),
            "vel": _round2(q.get("vel")),
            "is_agent": bool(q.get("is_agent", False)),
            "body_type": q.get("body_type"),
        }
    payload = {
        "entities": entities,
        "flags": _collect_flags(sdk),
        "step": _current_step(sdk),
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


# ==========================================================================
#  Entity classification (by name + body type)
# ==========================================================================
def _classify(sdk) -> dict:
    """Sort entities into agent / zone / pushable / others."""
    agent = zone = pushable = None
    others = []
    for name in sdk.list_entities():
        q = dict(sdk.query(name))
        q["name"] = name
        n = name.lower()
        if q.get("is_agent") or n == "agent":
            agent = q
        elif n.startswith("zone"):
            if zone is None:
                zone = q
        elif n == "ground" or n.startswith("wall"):
            continue
        else:
            others.append(q)
            is_dyn = q.get("body_type", "dynamic") == "dynamic"
            if is_dyn and pushable is None:
                pushable = q
    return {"agent": agent, "zone": zone, "pushable": pushable, "others": others}


# ==========================================================================
#  Greedy policy
# ==========================================================================
class _GreedyPolicy:
    """Reactive heuristic without LLM: aim at the target, jump if stuck, explore a bit."""

    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)
        self.hist: deque = deque(maxlen=STUCK_WINDOW)
        self.stuck = 0

    def _target(self, ents: dict, agent: dict):
        """Return (target_x, target_y, wants_to_climb)."""
        ax, ay = _xy(agent["pos"])
        zone, push = ents["zone"], ents["pushable"]

        # "Push an object into a zone" task: get behind it, then push.
        if zone is not None and push is not None:
            ox, oy = _xy(push["pos"])
            zx, _zy = _xy(zone["pos"])
            push_dir = 1.0 if zx > ox else -1.0
            behind = (ax - ox) * push_dir < 0        # agent on the side opposite the zone
            near = abs(ax - ox) < BEHIND_OFFSET + 24 and abs(ay - oy) < 70
            if behind and near:
                return zx, oy, False                 # push the object toward the zone
            return ox - push_dir * BEHIND_OFFSET, oy, False

        # "Reach" task: aim at the zone, else the object, else the right-most entity.
        if zone is not None:
            zx, zy = _xy(zone["pos"])
            return zx, zy, True
        if push is not None:
            ox, oy = _xy(push["pos"])
            return ox, oy, True
        if ents["others"]:
            t = max(ents["others"], key=lambda q: _xy(q["pos"])[0])
            tx, ty = _xy(t["pos"])
            return tx, ty, True
        return ax, ay, False

    def decide(self, sdk) -> str:
        ents = _classify(sdk)
        agent = ents.get("agent")
        if agent is None:
            return "noop"

        ax, ay = _xy(agent["pos"])
        self.hist.append(ax)
        tx, ty, _climb = self._target(ents, agent)

        # Horizontal progress over the recent window.
        if len(self.hist) < self.hist.maxlen:
            progressing = True
        else:
            progressing = (max(self.hist) - min(self.hist)) > STUCK_EPS
        self.stuck = 0 if progressing else self.stuck + 1

        dx = tx - ax
        if dx > DEADZONE:
            move = "right"
        elif dx < -DEADZONE:
            move = "left"
        else:
            move = "noop"

        # Target clearly higher and plumb -> climb.
        if ty is not None and ty > ay + CLIMB_DY and abs(dx) < NEAR_X:
            return "jump"

        # Prolonged block -> seeded random perturbation.
        if not progressing and self.stuck >= STUCK_LIMIT:
            self.stuck = 0
            return self.rng.choice(("left", "right", "jump", "jump"))

        # Stuck while pushing toward the target -> likely head-on obstacle -> jump.
        if not progressing and move != "noop":
            return "jump"

        return move


# ==========================================================================
#  Optional rendering (never required)
# ==========================================================================
def _glyph(name: str, q: dict) -> str:
    n = name.lower()
    if q.get("is_agent") or n == "agent":
        return "A"
    if n.startswith("zone"):
        return "Z"
    if n == "ground":
        return "="
    if n.startswith("wall"):
        return "|"
    if n.startswith("platform"):
        return "-"
    if n.startswith("ball"):
        return "o"
    if n.startswith("box"):
        return "#"
    return "*"


class _AsciiRenderer:
    """Fallback ASCII rendering: 80x24 grid, console cleared on each frame."""

    W, H = 80, 24

    def __init__(self, world=(800, 600)):
        self.world = world

    def draw(self, sdk, step: int) -> None:
        try:
            grid = [[" "] * self.W for _ in range(self.H)]
            for name in sdk.list_entities():
                x, y = _xy(sdk.query(name).get("pos"))
                cx = int(x / self.world[0] * (self.W - 1))
                cy = int((1 - y / self.world[1]) * (self.H - 1))  # y up
                cx = max(0, min(self.W - 1, cx))
                cy = max(0, min(self.H - 1, cy))
                grid[cy][cx] = _glyph(name, sdk.query(name))
            out = "\033[H\033[J" + f"step {step}\n" + "\n".join("".join(r) for r in grid) + "\n"
            sys.stdout.write(out)
            sys.stdout.flush()
        except Exception:  # noqa: BLE001  — rendering must never break the simulation
            pass


def _make_renderer(render: bool):
    """Build a renderer if requested: pygame if importable, else ASCII."""
    if not render:
        return None
    try:
        import pygame  # noqa: F401  (lazy import)
        return _PygameRenderer()
    except Exception:  # noqa: BLE001
        return _AsciiRenderer()


class _PygameRenderer:
    """Minimal pygame rendering (rectangles from bboxes). Any error -> no effect."""

    def __init__(self, world=(800, 600)):
        import pygame

        self.pygame = pygame
        self.world = world
        pygame.init()
        self.screen = pygame.display.set_mode(world)
        pygame.display.set_caption("harness — play")

    def draw(self, sdk, step: int) -> None:
        try:
            pg = self.pygame
            for _ in pg.event.get():
                pass
            self.screen.fill((18, 18, 22))
            W, H = self.world
            for name in sdk.list_entities():
                q = sdk.query(name)
                bbox = q.get("bbox")
                if bbox:
                    l, b, r, t = bbox
                else:
                    x, y = _xy(q.get("pos"))
                    l, b, r, t = x - 10, y - 10, x + 10, y + 10
                rect = pg.Rect(l, H - t, max(2, r - l), max(2, t - b))  # y up
                color = {"A": (90, 200, 120), "Z": (220, 180, 60)}.get(_glyph(name, q), (150, 150, 160))
                pg.draw.rect(self.screen, color, rect)
            pg.display.flip()
        except Exception:  # noqa: BLE001
            pass


# ==========================================================================
#  Episode loop + public entry point
# ==========================================================================
def _run_episode(sdk, get_success, policy: str = "greedy",
                 max_steps: int = 1200, render: bool = False, seed: int = 0) -> dict:
    """Loop observation -> action -> apply -> step(K) -> get_success ?

    Separated from navigate() to be testable with a FakeSDK.
    """
    if policy == "llm":
        raise NotImplementedError("llm policy planned for phase 4")
    if policy != "greedy":
        raise ValueError(f"unknown policy: {policy!r}")

    driver = _GreedyPolicy(seed=seed)
    renderer = _make_renderer(render)
    actions: list = []
    steps = 0
    success = False
    reason = "timeout"

    while steps < max_steps:
        try:
            if get_success(sdk):
                success, reason = True, "goal"
                break
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "steps": steps, "actions": actions,
                    "reason": "error", "error": f"get_success: {exc}"}

        action = driver.decide(sdk)
        actions.append(action)
        # We HOLD the action over the K-step window: the SDK applies a "per-step"
        # impulse, so applying it once would be eaten by friction. Decide every K
        # steps, continuous control in between.
        try:
            for _ in range(CONTROL_STEPS):
                sdk.apply(action)
                sdk.step(1)
                steps += 1
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001  — NaN/explosion reported by the SDK
            return {"success": False, "steps": steps, "actions": actions,
                    "reason": "error", "error": str(exc)}

        if renderer is not None:
            renderer.draw(sdk, steps)

    if not success:
        try:
            if get_success(sdk):
                success, reason = True, "goal"
        except Exception:  # noqa: BLE001
            pass

    return {"success": success, "steps": steps, "actions": actions, "reason": reason}


def navigate(scene_path: str, policy: str = "greedy", max_steps: int = 1200,
             render: bool = False) -> dict:
    """Play a scene with the requested policy.

    -> {"success": bool, "steps": int, "actions": [...], "reason": "goal|timeout|error"}
    """
    if policy == "llm":
        raise NotImplementedError("llm policy planned for phase 4")

    try:
        module = _load_scene(scene_path)
    except SceneError as exc:
        return {"success": False, "steps": 0, "actions": [], "reason": "error", "error": str(exc)}

    try:
        from harness.sdk import SceneSDK
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "steps": 0, "actions": [], "reason": "error",
                "error": f"harness.sdk unavailable: {exc}"}

    try:
        sdk = SceneSDK()
        module.build_scene(sdk)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "steps": 0, "actions": [], "reason": "error",
                "error": f"build_scene: {exc}"}

    return _run_episode(sdk, module.get_success, policy=policy,
                        max_steps=max_steps, render=render)
