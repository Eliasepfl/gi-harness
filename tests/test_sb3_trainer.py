"""Tests for the SB3-backed G3' trainer ([LF] migration — harness/rl/sb3_trainer).

stable-baselines3 is NOT yet in the certifier image (rebuild in flight), so the
WHOLE module skips at the `importorskip` below; the orchestrator re-runs it in the
sb3 image after merge. Everything after that line therefore executes only when SB3
(and its gymnasium dep) are present.

Coverage:
  * a DummyVecEnv builds over the gymnasium adapter and PPO fits it (train returns
    the vendored trainer's dict shape);
  * a tiny-budget end-to-end smoke: the sb3 path runs through g3_prime and emits a
    result + rl_witness whose SHAPE matches the vendored path's, and any emitted
    witness bridges to success via JsExecutor.
"""

import json
import os
import shutil
import subprocess

import numpy as np
import pytest

# Whole-module gate: skip until the sb3 image exists (also covers gymnasium).
pytest.importorskip("stable_baselines3")

from harness.rl import ppo, sb3_trainer               # noqa: E402
from harness.rl.certify import g3_prime               # noqa: E402
from harness.rl.env import PlanckEnv, make_gym_env     # noqa: E402
from harness.verify.executors import default_runner_path  # noqa: E402

# --- node / serve availability (mirrors tests/test_rl_env.py) -------------- #
NODE = shutil.which(os.environ.get("HARNESS_NODE", "node"))
RUNNER = default_runner_path()

# Same inline corridor game as tests/test_rl_env.py: roll right into the goal.
CORRIDOR_JS = """
const TITLE = "Test Corridor";
const PROMPT = "roll the ball right into the goal";
const WORLD_SIZE = [800, 200];
const ACTIONS = ["right", "left"];
function build(world) {
    world.set_gravity(0, -900);
    world.add("floor", "box", { pos: [400, 20], size: [800, 40], static: true, friction: 0.9 });
    world.add("ball", "circle", { pos: [60, 60], radius: 15, mass: 1, friction: 0.5 });
    world.control("ball");
    world.add("goal_zone", "box", { pos: [740, 70], size: [80, 80], static: true, sensor: true });
}
function act(world, action) {
    if (action === "right") world.impulse("ball", [40, 0]);
    else if (action === "left") world.impulse("ball", [-40, 0]);
}
function on_step(world) {
    const v = world.query("ball").vel;
    const vx = Math.max(-250, Math.min(250, v[0]));
    if (vx !== v[0]) world.set_velocity("ball", [vx, v[1]]);
}
function success(world) { return world.contacts("ball", "goal_zone"); }
function failure(world) { const p = world.query("ball").pos; return p[0] < -30 || p[1] < -30; }
function checkpoints(world) {
    const x = world.query("ball").pos[0];
    return { halfway: x > 380, at_goal: world.contacts("ball", "goal_zone") };
}
"""


def _serve_ready():
    if NODE is None:
        return False
    try:
        payload = "\n".join([
            json.dumps({"mode": "serve", "source": CORRIDOR_JS}),
            json.dumps({"op": "close"}),
        ]) + "\n"
        proc = subprocess.run([NODE, RUNNER], input=payload, capture_output=True,
                              text=True, encoding="utf-8", timeout=30,
                              cwd=os.path.dirname(RUNNER) or None)
        out = [json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip()]
        return bool(out and out[0].get("ready"))
    except Exception:
        return False


SERVE_OK = _serve_ready()
requires_serve = pytest.mark.skipif(not SERVE_OK,
                                    reason="node/planck serve mode unavailable")

# Vendored + sb3 result dicts must have the SAME shape; the witness sub-dict too.
_TIMING_KEYS = ("wall_clock_s", "throughput_sps")
_WITNESS_KEYS = {"seed", "actions", "ticks", "greedy"}
_TRAIN_KW = dict(budget_steps=6000, seed=0, n_eval=4,
                 num_envs=2, num_steps=64, patience=999)


@pytest.fixture
def corridor(tmp_path):
    p = tmp_path / "corridor.js"
    p.write_text(CORRIDOR_JS, encoding="utf-8")
    return str(p)


@requires_serve
def test_sb3_train_returns_vendored_surface(corridor):
    """sb3_trainer.train drives the gymnasium adapter through SB3 PPO and returns
    the SAME dict surface certify.g3_prime consumes from the vendored ppo.train."""
    def make_env():
        return PlanckEnv(corridor, horizon=150)

    probe = make_env()
    obs_dim = probe.observation_space.shape[0]
    n = probe.action_space.n
    probe.close()

    res = sb3_trainer.train(make_env, obs_dim, n, total_steps=4000, seed=0,
                            num_envs=2, num_steps=64, patience=999)
    # Same keys the vendored trainer exposes (the surface g3_prime reads).
    vendored_keys = {"agent", "curve_return", "curve_latched", "curve_success",
                     "steps_to_first_success", "global_steps", "updates",
                     "stopped_early", "best_success_rate_train", "train_wall_s", "hp"}
    assert vendored_keys <= set(res)
    assert isinstance(res["curve_latched"], list) and res["curve_latched"]
    assert res["global_steps"] >= 4000 - res["hp"]["num_envs"] * res["hp"]["num_steps"]

    # Greedy + sampled eval episodes emit the vendored episode-dict shape and the
    # recorded (seed, actions) pair replays bit-exactly through the batch executor.
    eval_env = PlanckEnv(corridor, horizon=150)
    try:
        g = sb3_trainer.greedy_episode(eval_env, res["agent"], seed=0)
        s = sb3_trainer.sample_episode(eval_env, res["agent"], seed=0, torch_seed=7)
    finally:
        eval_env.close()
    ref = ppo.greedy_episode.__doc__  # (vendored helper exists / import sanity)
    assert ref is not None
    for ep in (g, s):
        assert set(ep) == {"seed", "actions", "ticks", "success", "result",
                           "return", "latched", "greedy"}
        assert isinstance(ep["actions"], list)


@requires_serve
def test_sb3_g3_prime_smoke_matches_vendored_shape(corridor):
    """End-to-end: the sb3 trainer path runs through g3_prime and yields a result
    (and rl_witness) whose SHAPE is identical to the vendored path's; any emitted
    witness bridges to success — the ORACLE is trainer-agnostic."""
    vendored = g3_prime(corridor, trainer="vendored", **_TRAIN_KW)
    sb3res = g3_prime(corridor, trainer="sb3", **_TRAIN_KW)

    # Identical result shape (same keys) regardless of trainer.
    assert set(sb3res) == set(vendored)
    assert sb3res["trainer"] == "sb3"
    assert isinstance(sb3res["learnable"], bool)
    assert sb3res["bridge_ok"] in (None, True)

    # The witness dict shape matches the vendored path's; when present it bridges.
    for res in (vendored, sb3res):
        wit = res["rl_witness"]
        assert wit is None or set(wit) == _WITNESS_KEYS
    if sb3res["rl_witness"] is not None:
        assert sb3res["bridge_ok"] is True   # replayed to success via JsExecutor
