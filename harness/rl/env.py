"""PlanckEnv — a Gymnasium-style RL environment over a game's "serve" subprocess.

One `PlanckEnv` owns ONE long-lived `node nodeworld/runner.js` process in the
additive interactive "serve" mode: `reset` rebuilds the world, each `step`
advances exactly ONE decision tick (act + K=6 physics steps + latch + terminal
checks — identical to `gameverify.run_episode`). Because the semantics match the
batch `episodes` mode bit-for-bit, a greedy action sequence recorded here replays
to success through `JsExecutor.run_batch` — the certificate bridge in certify.py.

OBSERVATION (code-state, NOT pixels — the challenge's "code-defined truth"):
a fixed-layout flat float32 vector, frozen at the first reset. Per body, sorted
by name with the CONTROLLED body first, padded to the game's body count:

    [present, x/W, y/H, vx/VS, vy/VS, sin(angle), cos(angle),
     is_static, is_sensor, is_controlled]                        (10 floats/body)

`present` is 1.0 while the body exists and 0.0 once a game removes it (gems,
gates) or for pad slots — a clean, Markov-preserving way to encode disappearance
(the raw serve frame simply omits removed bodies). Positions are normalized by
world size, velocities by VEL_SCALE; everything is clipped to [-OBS_CLIP, OBS_CLIP].
Appended once at the end: the latched-checkpoint one-hot (declared order) and the
normalized tick — the stateful progress signal that makes gated multi-stage games
(latched switches open doors) observable to a feed-forward policy.

REWARD (the OMNI-EPIC lesson, LLM_RL_SYSTEMS §4.1): `+1.0 per NEWLY latched
checkpoint + 5.0 on success - 1.0 on failure`. `success` stays the unshaped binary
certificate — the "solved?" decision never reads this shaped reward. Episode ends
on a terminal `result` or at HORIZON (300) decision ticks.

======================================================================
godot_rl_agents AIController mapping  (GODOT_RL_MERGE.md §2 — pin this)
----------------------------------------------------------------------
The obs/action surface deliberately MIRRORS godot_rl_agents' AIController so the
Godot lane can replace this Node shell with zero retraining-code changes:

  AIController member / method          | PlanckEnv equivalent
  --------------------------------------|-------------------------------------
  get_obs() -> {"obs":[float,...]}      | reset()/step() return this flat vector
  get_action_space() ->                 | action_space = Discrete(n)
    {"act":{"size":n,                   |   n = len(ACTIONS); head index i maps
            "action_type":"discrete"}}  |   to ACTIONS[i] (the game's own string)
  set_action(action)                    | step(i) sends {"op":"act",
                                        |   "action": ACTIONS[i]} (one serve op)
  action_repeat == 6                    | one serve "act" = act + K=6 steps (== our K)
  get_reward() -> float                 | reward computed env-side (godot_rl has no
                                        |   runner latch, so the latch bookkeeping
                                        |   lives in the controller — here in step())
  done / needs_reset                    | terminated (success/failure) / truncated
  reset()                               | reset() sends {"op":"reset","seed":...}

Swapping in godot_rl_agents' Sync/TCP AIController later is thus a shell change,
not a policy or training-code change (the outer rung, GODOT_RL_MERGE.md §3/Phase 4).
======================================================================
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from dataclasses import dataclass

import numpy as np

# --- Constants ([eng.] = engineering choice) ---------------------------------
HORIZON = 300              # decision ticks per episode (matches PROBE_HORIZON) [eng.]
K_STEPS = 6                # physics steps per decision tick (CONTRACTS §2)
PER_BODY = 10             # obs features per body (see module docstring) [eng.]
VEL_SCALE = 1000.0        # px/s velocity normalizer [eng.]
OBS_CLIP = 10.0           # clip normalized obs into [-OBS_CLIP, OBS_CLIP] [eng.]
R_CHECKPOINT = 1.0        # reward per newly latched checkpoint [eng.]
R_SUCCESS = 5.0           # terminal bonus on the unshaped success certificate [eng.]
R_FAILURE = -1.0          # terminal penalty on failure/error [eng.]
SERVE_TIMEOUT_S = 60.0    # per-op read budget before declaring the node dead [eng.]


# --- Minimal Gymnasium-compatible spaces (duck types; no gymnasium dep) ------
@dataclass
class Discrete:
    """Gymnasium-compatible discrete space (exposes ``.n``)."""
    n: int

    def sample(self, rng: np.random.Generator) -> int:
        return int(rng.integers(0, self.n))


@dataclass
class Box:
    """Gymnasium-compatible continuous box space (exposes ``.shape``/``.low``/``.high``)."""
    low: float
    high: float
    shape: tuple

    @property
    def size(self) -> int:
        return int(np.prod(self.shape))


def build_obs_vector(obs_state: dict, latched: dict, body_order: list[str],
                     cp_keys: list[str], world_size, tick: int,
                     horizon: int) -> np.ndarray:
    """Pure obs-vector builder (see the module docstring for the layout). Kept a
    free function so the layout can be unit-tested without a node subprocess."""
    w, h = world_size
    obs_dim = len(body_order) * PER_BODY + len(cp_keys) + 1
    vec = np.zeros(obs_dim, dtype=np.float32)
    i = 0
    for name in body_order:
        q = obs_state.get(name)
        if q is not None:
            px, py = q.get("pos", (0.0, 0.0))
            vx, vy = q.get("vel", (0.0, 0.0))
            ang = float(q.get("angle", 0.0))
            vec[i + 0] = 1.0                              # present
            vec[i + 1] = px / w
            vec[i + 2] = py / h
            vec[i + 3] = vx / VEL_SCALE
            vec[i + 4] = vy / VEL_SCALE
            vec[i + 5] = math.sin(ang)
            vec[i + 6] = math.cos(ang)
            vec[i + 7] = 1.0 if q.get("static") else 0.0
            vec[i + 8] = 1.0 if q.get("sensor") else 0.0
            vec[i + 9] = 1.0 if q.get("controlled") else 0.0
        # else: removed/pad slot stays all-zero (present == 0)
        i += PER_BODY
    for key in cp_keys:                                  # latched one-hot
        vec[i] = 1.0 if latched.get(key) is not None else 0.0
        i += 1
    vec[i] = min(1.0, tick / float(horizon))             # normalized tick
    np.clip(vec, -OBS_CLIP, OBS_CLIP, out=vec)
    return vec


class PlanckEnv:
    """Gymnasium-style single env over one game's serve-mode node subprocess.

    API: ``reset(seed=None) -> (obs, info)`` and
    ``step(action_idx) -> (obs, reward, terminated, truncated, info)``, plus
    ``observation_space`` / ``action_space`` (available after construction) and
    ``close()``. One process is spawned per env and reused across episodes.
    """

    def __init__(self, game_path: str, *, runner_path: str | None = None,
                 node: str | None = None, horizon: int = HORIZON):
        self.game_path = game_path
        self.horizon = int(horizon)
        with open(game_path, "r", encoding="utf-8") as fh:
            self._source = fh.read()

        self._node = node or os.environ.get("HARNESS_NODE", "node")
        if runner_path is None:
            from harness.verify.executors import default_runner_path
            runner_path = default_runner_path()
        self._runner_path = runner_path

        self._stderr = tempfile.TemporaryFile(mode="w+")
        self._proc = subprocess.Popen(
            [self._node, self._runner_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr,
            text=True, encoding="utf-8", bufsize=1,
            cwd=os.path.dirname(self._runner_path) or None,
        )
        # Handshake: send the init line, read the ready line.
        ready = self._exchange({"mode": "serve", "source": self._source})
        if not ready.get("ready"):
            self.close()
            raise RuntimeError(f"serve init failed for {game_path}: {ready.get('error')}")
        self.actions: list[str] = list(ready.get("actions") or [])
        self.title: str = ready.get("title") or os.path.basename(game_path)
        self.world_size = tuple(ready.get("world_size") or (800, 600))

        # Layout is discovered on the FIRST reset and then frozen.
        self._body_order: list[str] | None = None
        self._cp_keys: list[str] | None = None
        self.action_space = Discrete(len(self.actions))
        self.observation_space: Box | None = None  # set after first reset

        self._tick = 0
        self._done = True
        self._prev_latched: set[str] = set()

        # Priming reset: freezes the body layout / obs space so observation_space
        # and action_space are available right after construction (gym convention).
        self.reset(seed=0)

    # -- process I/O ------------------------------------------------------
    def _exchange(self, op: dict) -> dict:
        """Send one op line, read exactly one reply line. Raises if node died."""
        if self._proc.poll() is not None:
            raise RuntimeError(f"serve process exited (code {self._proc.returncode})"
                               f"\n{self._read_stderr()}")
        try:
            self._proc.stdin.write(json.dumps(op) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError(f"serve stdin write failed: {exc}\n{self._read_stderr()}")
        line = self._proc.stdout.readline()
        if line == "":
            raise RuntimeError("serve process closed stdout unexpectedly"
                               f"\n{self._read_stderr()}")
        return json.loads(line)

    def _read_stderr(self) -> str:
        try:
            self._stderr.seek(0)
            return "STDERR: " + self._stderr.read()[-2000:]
        except Exception:
            return ""

    # -- layout / observation --------------------------------------------
    def _freeze_layout(self, frame: dict) -> None:
        obs_state = frame.get("obs_state", {})
        controlled = [n for n, q in obs_state.items() if q.get("controlled")]
        others = sorted(n for n in obs_state if n not in controlled)
        # Controlled body first (LLM_RL_SYSTEMS §4.1), then the rest sorted by name.
        self._body_order = list(controlled) + others
        self._cp_keys = list((frame.get("latched") or {}).keys())
        obs_dim = len(self._body_order) * PER_BODY + len(self._cp_keys) + 1
        self.observation_space = Box(-OBS_CLIP, OBS_CLIP, (obs_dim,))

    def _observe(self, frame: dict) -> np.ndarray:
        return build_obs_vector(
            frame.get("obs_state", {}), frame.get("latched") or {},
            self._body_order, self._cp_keys, self.world_size, self._tick,
            self.horizon)

    @staticmethod
    def _latched_set(frame: dict) -> set[str]:
        return {k for k, v in (frame.get("latched") or {}).items() if v is not None}

    # -- Gymnasium API ----------------------------------------------------
    def reset(self, seed: int = 0):
        frame = self._exchange({"op": "reset", "seed": int(seed)})
        if self._body_order is None:
            self._freeze_layout(frame)
        self._tick = 0
        self._done = False
        self._prev_latched = self._latched_set(frame)
        return self._observe(frame), {"latched": dict(frame.get("latched") or {})}

    def step(self, action_idx: int):
        if self._done:
            raise RuntimeError("step() after episode end — call reset() first")
        action = self.actions[int(action_idx)]
        frame = self._exchange({"op": "act", "action": action})
        self._tick = int(frame.get("tick", self._tick + 1))
        result = frame.get("result")

        latched_now = self._latched_set(frame)
        new_latches = len(latched_now - self._prev_latched)
        self._prev_latched = latched_now
        reward = R_CHECKPOINT * new_latches

        terminated = False
        truncated = False
        if result == "success":
            reward += R_SUCCESS
            terminated = True
        elif result in ("failure", "error"):
            reward += R_FAILURE
            terminated = True
        elif self._tick >= self.horizon:
            truncated = True
        self._done = terminated or truncated

        info = {
            "result": result,
            "tick": self._tick,
            "latched": dict(frame.get("latched") or {}),
            "n_latched": len(latched_now),
            "success": result == "success",
        }
        return self._observe(frame), float(reward), terminated, truncated, info

    def close(self) -> None:
        proc = getattr(self, "_proc", None)
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.write(json.dumps({"op": "close"}) + "\n")
                proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        stderr = getattr(self, "_stderr", None)
        if stderr is not None:
            try:
                stderr.close()
            except Exception:
                pass

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
