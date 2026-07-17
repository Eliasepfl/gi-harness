"""Demo-readiness + critic-competence gate (Elias's reliability rule).

Two consumers of "the run was reliable" are wired here:
  (1) DEMOS replay a RELIABLE trained policy's greedy rollout (`demo_trajectory`), not the
      first lucky tree-solver witness;
  (2) the g4 smart tiers (inverse-value / descent / value_death) accept a critic ONLY once
      it has CONVERGED (the A/B showed weak-critic == 0).

OFFLINE + hermetic: the g3' trainer/env/bridge seams are stubbed (no node / Godot / sb3), the
g4 gate is driven with fabricated g3 results, and harden's oracle seams are monkeypatched. The
in-image smoke (real sb3 + Godot) is gated at the bottom.
"""
from __future__ import annotations

import json
import os
import socket
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness.rl import certify as C  # noqa: E402


# ====================================================================== #
# 1. Pure thresholds — is_demo_ready / critic_competent
# ====================================================================== #
def test_is_demo_ready_requires_both_floors():
    # BOTH the greedy floor AND the stochastic floor must clear (AND, not OR).
    assert C.is_demo_ready(1.0, 1.0) is True
    assert C.is_demo_ready(C.DEMO_SR_MIN, C.DEMO_STOCHASTIC_FLOOR) is True   # exactly on both
    assert C.is_demo_ready(C.DEMO_SR_MIN - 0.01, 1.0) is False              # greedy short
    assert C.is_demo_ready(1.0, C.DEMO_STOCHASTIC_FLOOR - 0.01) is False    # stochastic short
    # strictly stronger than `learnable` (which is an OR at 0.5)
    assert C.DEMO_SR_MIN > C.LEARNABLE_SUCCESS_RATE
    assert C.DEMO_STOCHASTIC_FLOOR > C.LEARNABLE_SUCCESS_RATE


def test_is_demo_ready_none_safe_and_tunable():
    assert C.is_demo_ready(None, 0.9) is False
    assert C.is_demo_ready(0.9, None) is False
    # thresholds are [eng.] knobs
    assert C.is_demo_ready(0.7, 0.7, sr_min=0.7, stochastic_floor=0.7) is True
    assert C.is_demo_ready(0.7, 0.7, sr_min=0.8, stochastic_floor=0.7) is False


def test_critic_competent_is_demo_ready():
    assert C.critic_competent({"demo_ready": True}) is True
    assert C.critic_competent({"demo_ready": False}) is False
    assert C.critic_competent({}) is False       # missing -> not competent (honest default)
    assert C.critic_competent(None) is False
    assert C.critic_competent("nope") is False


# ====================================================================== #
# 2. Trajectory selection + export shape + determinism
# ====================================================================== #
def _greedy_eps():
    # three winning greedy episodes (different seeds/ticks) + one loss.
    return [
        {"success": True, "ticks": 20, "seed": 1, "actions": ["a", "b"], "greedy": True},
        {"success": True, "ticks": 12, "seed": 3, "actions": ["x", "y", "z"], "greedy": True},
        {"success": False, "ticks": 4, "seed": 0, "actions": ["q"], "greedy": True},
    ]


def test_pick_demo_trajectory_selects_shortest_win():
    traj = C._pick_demo_trajectory(_greedy_eps())
    # fewest ticks among WINS (the 12-tick seed=3), never the 4-tick loss.
    assert traj == {"seed": 3, "actions": ["x", "y", "z"], "ticks": 12, "greedy": True}


def test_pick_demo_trajectory_none_without_a_win():
    assert C._pick_demo_trajectory([{"success": False, "ticks": 1, "seed": 0,
                                     "actions": ["q"]}]) is None
    assert C._pick_demo_trajectory([]) is None


def test_pick_demo_trajectory_is_deterministic():
    # same episodes -> byte-identical trajectory (pure), and a new list of the SAME
    # episode does not change the pick.
    a = C._pick_demo_trajectory(_greedy_eps())
    b = C._pick_demo_trajectory(_greedy_eps())
    assert a == b


def test_export_demo_trajectory_witness_shape(tmp_path):
    traj = {"seed": 3, "actions": ["x", "y", "z"], "ticks": 12, "greedy": True}
    path = str(tmp_path / "nested" / "demo_trajectory.json")
    out = C.export_demo_trajectory(traj, path)
    assert out == path and os.path.exists(path)
    doc = json.loads(open(path, encoding="utf-8").read())
    # capture --actions consumes {seed, actions}; extras are harmless provenance.
    assert doc["seed"] == 3 and doc["actions"] == ["x", "y", "z"]
    assert doc["ticks"] == 12 and doc["greedy"] is True and doc["source"] == "g3_demo"
    # types are JSON-plain (int seed, list of str) so the capture CLI replays it verbatim.
    assert isinstance(doc["seed"], int) and all(isinstance(a, str) for a in doc["actions"])


# ====================================================================== #
# 2b. Eval action histogram (Elias: does a 3D policy actually use altitude?)
# ====================================================================== #
def test_action_histogram_sums_to_ticks_and_serializes():
    eps = [{"actions": ["up", "up", "forward", "left", "brake"]},
           {"actions": ["down", "up", "right"]}]
    actions = ["up", "down", "left", "right", "forward", "brake"]
    hist = C.action_histogram(eps, actions, with_axes=True)
    total = sum(len(e["actions"]) for e in eps)                 # 8 ticks
    # per_action seeds EVERY declared action and sums to the tick total.
    assert set(hist["per_action"]) == set(actions)
    assert hist["total_ticks"] == total
    assert sum(hist["per_action"].values()) == total
    # 3D axis aggregates are EXCLUSIVE and also sum to the tick total.
    ax = hist["per_axis"]
    assert ax["vertical"] == 4        # up×3 + down×1
    assert ax["lateral"] == 2         # left + right
    assert ax["forward_brake"] == 2   # forward + brake
    assert ax["other"] == 0
    assert sum(ax.values()) == total
    assert hist["per_axis_frac"]["vertical"] == pytest.approx(4 / total)
    # JSON round-trips (the eval artifact persists it verbatim).
    assert json.loads(json.dumps(hist)) == hist


def test_action_histogram_no_axes_when_2d_and_empty_safe():
    hist = C.action_histogram([{"actions": ["up", "left"]}], ["up", "down", "left", "right"])
    assert "per_axis" not in hist                               # with_axes defaults off (2D)
    assert hist["total_ticks"] == 2 and hist["per_action"]["down"] == 0
    empty = C.action_histogram([], ["a", "b"], with_axes=True)
    assert empty["total_ticks"] == 0 and sum(empty["per_axis"].values()) == 0


# ====================================================================== #
# 3. g3_prime end-to-end with a STUB trainer/env/bridge (offline)
# ====================================================================== #
class _StubAgent:
    def save(self, path):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("stub-model")


class _StubEnv:
    """Minimal PlanckEnv stand-in: only the probe surface g3_prime reads (the eval episodes
    are produced by the stub trainer, not by stepping this env)."""
    def __init__(self, *a, **k):
        self.observation_space = type("S", (), {"shape": (3,)})()
        self.action_space = type("A", (), {"n": 2})()
        self.title = "Stub Game"
        self._body_order = ["player"]
        self._cp_keys = ["m1"]

    def close(self):
        pass


def _stub_trainer(*, greedy_sr: float, stochastic_sr: float, n_eval: int):
    """A trainer module stub whose greedy/sampled eval hit prescribed success rates. Greedy
    episodes are DETERMINISTIC per seed (same seed -> same actions) so the demo trajectory is
    reproducible; the first `k` seeds win to realise the requested rate."""
    n_g = round(greedy_sr * n_eval)
    n_s = round(stochastic_sr * n_eval)

    class _T:
        @staticmethod
        def train(make_env, obs_dim, n_actions, *, total_steps, seed=0, log=None,
                  wall_clock_budget_s=None, **kw):
            return {"agent": _StubAgent(), "curve_return": [0.1, 0.5, 0.9],
                    "curve_latched": [0.0, 0.5, 1.0], "curve_success": [0.0, 0.5, 1.0],
                    "steps_to_first_success": 10, "global_steps": total_steps,
                    "updates": 3, "stopped_early": False, "plateau_stopped": True,
                    "train_wall_s": 0.01, "hp": {}}

        @staticmethod
        def greedy_episode(env, agent, seed, **kw):
            win = seed < n_g
            # deterministic actions keyed on seed -> "same policy + seed -> same actions"
            return {"seed": seed, "actions": ["up", "right", "up"], "ticks": 3,
                    "success": win, "result": "success" if win else "timeout",
                    "return": 1.0 if win else 0.0, "latched": {"m1": 1} if win else {},
                    "greedy": True}

        @staticmethod
        def sample_episode(env, agent, seed, torch_seed=0, **kw):
            win = seed < n_s
            return {"seed": seed, "actions": ["up", "right", "up", "right"], "ticks": 4,
                    "success": win, "result": "success" if win else "timeout",
                    "return": 1.0 if win else 0.0, "latched": {"m1": 1} if win else {},
                    "greedy": False}

    return _T


@pytest.fixture
def stub_js_game(tmp_path):
    src = ('const TITLE = "Stub";\nconst ACTIONS = ["up","right"];\n'
           'function build(world){ world.add("player","circle",{pos:[1,1]}); '
           'world.control("player"); }\nfunction act(world,action){}\n'
           'function success(world){ return false; }\n')
    p = tmp_path / "stub.js"
    p.write_text(src, encoding="utf-8")
    return str(p)


def _install_g3_stubs(monkeypatch, trainer):
    monkeypatch.setattr(C, "PlanckEnv", _StubEnv)
    monkeypatch.setattr(C, "_resolve_trainer", lambda t: trainer)
    # skip the real JsExecutor/node bridge — the bridge itself is proven elsewhere.
    monkeypatch.setattr(C, "_bridge_replay", lambda src, wit: {"result": "success"})


def test_g3_prime_flips_demo_ready_and_exports_trajectory(monkeypatch, stub_js_game, tmp_path):
    _install_g3_stubs(monkeypatch, _stub_trainer(greedy_sr=1.0, stochastic_sr=1.0, n_eval=8))
    model = str(tmp_path / "art" / "policy.zip")
    res = C.g3_prime(stub_js_game, budget_steps=100, n_eval=8, save_model=model)

    # the reliability gate + its two rates are exposed
    assert res["demo_ready"] is True
    assert res["greedy_sr"] == 1.0 and res["stochastic_sr"] == 1.0
    assert res["n_eval"] == 8
    # the trained policy's OWN winning greedy rollout is exported BESIDE the model artifact
    assert res["demo_trajectory"] is not None
    assert set(res["demo_trajectory"]) == {"seed", "actions", "ticks", "greedy"}
    assert res["demo_trajectory"]["greedy"] is True
    expected = os.path.join(os.path.dirname(model), "demo_trajectory.json")
    assert res["demo_trajectory_path"] == expected and os.path.exists(expected)
    doc = json.loads(open(expected, encoding="utf-8").read())
    assert doc["seed"] == res["demo_trajectory"]["seed"]
    assert doc["actions"] == res["demo_trajectory"]["actions"]
    # critic_competent reads the SAME gate off the result
    assert C.critic_competent(res) is True


def test_g3_prime_demo_trajectory_is_deterministic(monkeypatch, stub_js_game, tmp_path):
    # same policy (stub) + same seeds -> identical demo trajectory across two runs.
    _install_g3_stubs(monkeypatch, _stub_trainer(greedy_sr=1.0, stochastic_sr=1.0, n_eval=6))
    r1 = C.g3_prime(stub_js_game, budget_steps=100, n_eval=6,
                    demo_out=str(tmp_path / "a.json"))
    r2 = C.g3_prime(stub_js_game, budget_steps=100, n_eval=6,
                    demo_out=str(tmp_path / "b.json"))
    assert r1["demo_trajectory"] == r2["demo_trajectory"]
    assert (json.loads(open(tmp_path / "a.json").read())
            == json.loads(open(tmp_path / "b.json").read()))


def test_g3_prime_not_demo_ready_exports_no_trajectory(monkeypatch, stub_js_game, tmp_path):
    # learnable (greedy 1.0) but NOT robust (stochastic below the floor) -> not demo-ready.
    below = C.DEMO_STOCHASTIC_FLOOR - 0.2
    _install_g3_stubs(monkeypatch,
                      _stub_trainer(greedy_sr=1.0, stochastic_sr=below, n_eval=10))
    res = C.g3_prime(stub_js_game, budget_steps=100, n_eval=10,
                     save_model=str(tmp_path / "policy.zip"))
    assert res["learnable"] is True          # still learnable
    assert res["demo_ready"] is False        # but not reliable enough to ship / hand off
    assert res["demo_trajectory"] is None and res["demo_trajectory_path"] is None
    assert C.critic_competent(res) is False
    assert not os.path.exists(str(tmp_path / "demo_trajectory.json"))


# ====================================================================== #
# 4. g4 critic-competence gate — honest downgrade in the g4 block
# ====================================================================== #
from test_g4 import CONTROL, SOFTLOCK, SMALL, _report, factory  # noqa: E402
from harness.verify import g4  # noqa: E402

_IV = dict(stale_H=30, stale_budget=2500, top_m=6)


def _incompetent_g3(**over):
    d = {"demo_ready": False, "greedy_sr": 0.3, "stochastic_sr": 0.1,
         "saved_model_path": "/tmp/does_not_matter.zip"}
    d.update(over)
    return d


def _competent_g3(**over):
    d = {"demo_ready": True, "greedy_sr": 1.0, "stochastic_sr": 0.8,
         "saved_model_path": "/tmp/does_not_matter.zip"}
    d.update(over)
    return d


def _control_report():
    return _report(["push"] * 8, 8, checkpoints={"halfway": 4})


def test_incompetent_critic_downgrades_smart_tiers_with_honest_note():
    out = g4.run_g4(CONTROL, _control_report(), engine="py", world_factory=factory(),
                    tiers=(0,), model_path="/tmp/x.zip", g3_result=_incompetent_g3(), **SMALL)
    # top-level gate summary is honest (never silent)
    cg = out["critic_gate"]
    assert cg["model_armed"] is True and cg["competent"] is False and cg["downgraded"] is True
    assert cg["demo_ready"] is False
    # both smart blocks were dropped to the critic-free ladder with the reason recorded
    for blk in (out["inverse_value"], out["descent"]):
        assert blk["critic_downgraded"] is True
        assert "DOWNGRADED" in blk["reason"] and "NOT competent" in blk["reason"]
        assert blk["findings"] == []            # the unconverged critic steered nothing


def test_incompetent_critic_leaves_the_mechanical_ladder_intact():
    # the downgrade only removes the SMART tiers; tier-0 findings are byte-identical to a
    # plain no-model run (the critic-free ladder is exactly what remains).
    base = g4.run_g4(CONTROL, _control_report(), engine="py", world_factory=factory(),
                     tiers=(0,), **SMALL)
    down = g4.run_g4(CONTROL, _control_report(), engine="py", world_factory=factory(),
                     tiers=(0,), model_path="/tmp/x.zip", g3_result=_incompetent_g3(), **SMALL)
    assert down["findings"] == base["findings"]
    assert base["critic_gate"]["downgraded"] is False       # no model -> nothing to downgrade


def test_competent_g3_result_does_not_downgrade_injected_seam():
    # a competent g3 result + an injected candidate seam -> the tier RUNS (armed), and the
    # gate reports competent, not downgraded.
    out = g4.run_g4(SOFTLOCK, _report(["run", "run", "leap"] + ["run"] * 6, 9,
                                      checkpoints={"lip": 2, "crossed": 3}),
                    engine="py", world_factory=factory(), tiers=(0,),
                    iv_candidates=[["run", "run", "run", "run"]],
                    g3_result=_competent_g3(), **_IV, **SMALL)
    assert out["inverse_value"]["status"] == "run"
    assert out["critic_gate"]["competent"] is True and out["critic_gate"]["downgraded"] is False


def test_no_g3_result_is_backward_compatible():
    # no g3_result -> the artifact path is unchanged (competent None, not downgraded); an
    # injected seam still runs exactly as before.
    out = g4.run_g4(CONTROL, _control_report(), engine="py", world_factory=factory(),
                    tiers=(0,), iv_candidates=[["push", "coast"]], **_IV, **SMALL)
    cg = out["critic_gate"]
    assert cg["competent"] is None and cg["downgraded"] is False
    assert out["inverse_value"]["status"] == "run"


# ====================================================================== #
# 5. harden surfaces demo_ready + the difficulty signal + the critic handoff
# ====================================================================== #
from harness.gen import harden as H  # noqa: E402


def _g4_clean():
    return {"schema": "g4_report/v1", "grade": "hardened", "findings": [],
            "critic_gate": {"model_armed": False, "competent": None, "downgraded": False}}


def _seed_harden(monkeypatch, g3):
    monkeypatch.setattr(H, "verify_fn", lambda p: {"passed": True})
    seen = {}

    def attack(gp, src, rep, **kw):
        seen.update(kw)
        return _g4_clean()

    monkeypatch.setattr(H, "attack_fn", attack)
    monkeypatch.setattr(H, "render_skills_fn", lambda text, **kw: "")
    monkeypatch.setattr(H, "g3_fn", lambda gp, budget_steps, **kw: g3(kw))
    return seen


def test_harden_surfaces_demo_ready_and_hands_off_competent_critic(tmp_path, monkeypatch):
    def g3(kw):
        sm = kw.get("save_model")
        return {"demo_ready": True, "learnable": True, "still_improving": False,
                "greedy_sr": 1.0, "stochastic_sr": 0.8, "final_success_rate": 1.0,
                "stochastic_success_rate": 0.8, "saved_model_path": sm,
                "demo_trajectory_path": (os.path.join(os.path.dirname(sm),
                                         "demo_trajectory.json") if sm else None)}

    seen = _seed_harden(monkeypatch, g3)
    game = tmp_path / "g.gd"
    game.write_text("extends Node2D\n")
    rep = H.harden_game(str(game), out_dir=str(tmp_path / "sb"), backend="template",
                        run_g3=True, budget_steps=100, ledger_path=str(tmp_path / "l.jsonl"))

    assert rep["demo_ready"] is True
    g3s = rep["g3_summary"]
    assert g3s["demo_ready"] is True and g3s["critic_competent"] is True
    assert g3s["critic_handed_off"] is True and g3s["difficulty_signal"] is False
    # the trained artifact + result were HANDED to the g4 attack (the critic handoff)
    assert seen.get("model_path") is not None
    assert seen.get("g3_result", {}).get("demo_ready") is True


def test_harden_reads_learnable_not_demo_ready_as_difficulty_not_defect(tmp_path, monkeypatch):
    # learnable + converged (not still_improving) + NOT demo_ready -> a HARD-TO-MASTER signal,
    # NOT a defect: verdict stays HARDENED, difficulty_signal is flagged, no repair grind.
    def g3(kw):
        return {"demo_ready": False, "learnable": True, "still_improving": False,
                "greedy_sr": 0.6, "stochastic_sr": 0.55, "final_success_rate": 0.6,
                "stochastic_success_rate": 0.55, "saved_model_path": None,
                "demo_trajectory_path": None, "checkpoint_keys": ["m1"],
                "per_checkpoint_latch_rate": {"m1": 1.0}}

    _seed_harden(monkeypatch, g3)
    game = tmp_path / "g.gd"
    game.write_text("extends Node2D\n")
    rep = H.harden_game(str(game), out_dir=str(tmp_path / "sb"), backend="template",
                        run_g3=True, budget_steps=100, ledger_path=str(tmp_path / "l.jsonl"))

    assert rep["final_verdict"] == "HARDENED"          # not a DEFECT verdict
    assert rep["directives_issued"] == 0               # no repair directive compiled
    assert rep["demo_ready"] is False
    assert rep["g3_summary"]["difficulty_signal"] is True
    assert rep["g3_summary"]["critic_handed_off"] is False   # no artifact -> nothing handed


# ====================================================================== #
# 6. RL-WITNESS SECOND CERTIFICATION PATH — rescue_certify (offline stubs)
# ====================================================================== #
def _unsolved_with_progress(**over):
    d = {"passed": False, "failure_class": "UNSOLVED",
         "progress": {"reach_counts": {"m1": 5, "m2": 0}, "stuck_after": "m1"},
         "witness": None, "hint": "unsolved: stuck after m1"}
    d.update(over)
    return d


def _g3_converged(**over):
    """A stub g3_prime verdict that CONVERGED to demo-ready with a greedy demo trajectory."""
    d = {"demo_ready": True, "greedy_sr": 1.0, "stochastic_sr": 0.8, "n_eval": 32,
         "trained_steps": 234_000, "budget_steps": 500_000, "trainer": "sb3",
         "method": "ppo", "saved_model_path": None,
         "demo_trajectory": {"seed": 0, "actions": ["up", "right", "up"], "ticks": 3,
                             "greedy": True}}
    d.update(over)
    return d


def test_rescue_candidacy_only_unsolved_with_progress():
    assert C._rescue_candidacy({"passed": True})[0] is False
    assert C._rescue_candidacy(_unsolved_with_progress())[0] is True
    # a broken game is never a rescue candidate (no PPO on ENV/GOAL errors)
    assert C._rescue_candidacy(_unsolved_with_progress(failure_class="GOAL_ERROR"))[0] is False
    # hopeless UNSOLVED (nothing ever reached) -> no PPO
    hopeless = _unsolved_with_progress(progress={"reach_counts": {"m1": 0}, "stuck_after": None})
    ok, reason = C._rescue_candidacy(hopeless)
    assert ok is False and reason == "no_progress"


def test_rescue_certify_upgrades_on_convergence_and_replay(monkeypatch, tmp_path):
    game = tmp_path / "hard.gd"
    game.write_text("extends Node2D\n")
    # deterministic success replay through the (stubbed) serve bridge, with checkpoints.
    monkeypatch.setattr(C, "_bridge_replay_for_engine",
                        lambda eng, src, wit: {"result": "success", "ticks": 3,
                                               "checkpoints": {"m1": 1, "m2": 3}})
    rep = C.rescue_certify(str(game), verify_report=_unsolved_with_progress(),
                           g3_fn=lambda gp, **kw: _g3_converged())

    # UPGRADED to a first-class certified game carrying an RL witness (tree witness shape).
    assert rep["passed"] is True and rep["failure_class"] is None
    assert rep["witness_source"] == "rl"
    assert rep["witness"] == {"seed": 0, "actions": ["up", "right", "up"], "ticks": 3,
                              "checkpoints": {"m1": 1, "m2": 3}}
    # the UNSOLVED (solvable-but-hard) diagnosis is PRESERVED for the difficulty tuner.
    assert rep["unsolved_diagnosis"] == {"reach_counts": {"m1": 5, "m2": 0},
                                         "stuck_after": "m1"}
    # rl provenance for the Atlas prover-effort axis rides on the rescue block.
    assert rep["rescue"]["rescued"] is True and rep["rescue"]["rl_steps"] == 234_000
    assert rep["rescue"]["greedy_sr"] == 1.0 and rep["rescue"]["n_eval"] == 32


def test_rescue_certify_honest_failure_on_no_convergence(monkeypatch, tmp_path):
    game = tmp_path / "hard.gd"
    game.write_text("extends Node2D\n")
    # never touches the bridge (no trajectory) -> stays UNSOLVED with an honest block.
    monkeypatch.setattr(C, "_bridge_replay_for_engine",
                        lambda *a, **k: pytest.fail("bridge must not run without convergence"))
    flat = {"demo_ready": False, "greedy_sr": 0.2, "stochastic_sr": 0.1, "n_eval": 32,
            "trained_steps": 500_000, "demo_trajectory": None}
    rep = C.rescue_certify(str(game), verify_report=_unsolved_with_progress(),
                           g3_fn=lambda gp, **kw: flat)
    assert rep["passed"] is False and rep["failure_class"] == "UNSOLVED"
    assert rep["witness"] is None
    assert rep["rescue"]["attempted"] is True and rep["rescue"]["rescued"] is False
    assert rep["rescue"]["reason"] == "no_convergence"


def test_rescue_certify_honest_failure_on_replay_mismatch(monkeypatch, tmp_path):
    game = tmp_path / "hard.gd"
    game.write_text("extends Node2D\n")
    # converged, but the greedy demo trajectory does NOT replay to success -> honest failure
    # (the RL witness must clear the SAME deterministic-replay bar as a tree witness).
    monkeypatch.setattr(C, "_bridge_replay_for_engine",
                        lambda eng, src, wit: {"result": "timeout", "ticks": 3})
    rep = C.rescue_certify(str(game), verify_report=_unsolved_with_progress(),
                           g3_fn=lambda gp, **kw: _g3_converged())
    assert rep["passed"] is False and rep["failure_class"] == "UNSOLVED"
    assert rep["rescue"]["rescued"] is False
    assert rep["rescue"]["reason"] == "replay_mismatch"
    assert rep["rescue"]["replay_result"] == "timeout"


def test_rescue_certify_skips_non_candidates_without_ppo(tmp_path):
    game = tmp_path / "broken.gd"
    game.write_text("extends Node2D\n")

    def boom(*a, **k):
        raise AssertionError("g3_prime must NOT run on a non-candidate report")

    # a GOAL_ERROR game is broken, not solvable-but-hard -> no PPO, honest skip block.
    rep = C.rescue_certify(str(game),
                           verify_report=_unsolved_with_progress(failure_class="GOAL_ERROR"),
                           g3_fn=boom)
    assert rep["passed"] is False
    assert rep["rescue"] == {"attempted": False, "rescued": False,
                             "reason": "not_unsolved (GOAL_ERROR)"}


def test_rescue_certify_passes_through_already_certified(tmp_path):
    game = tmp_path / "ok.gd"
    game.write_text("extends Node2D\n")
    rep = C.rescue_certify(str(game),
                           verify_report={"passed": True,
                                          "witness": {"seed": 0, "actions": ["x"] * 30}},
                           g3_fn=lambda *a, **k: pytest.fail("no PPO on a certified game"))
    assert rep["passed"] is True and rep["witness_source"] == "tree"
    assert "rescue" not in rep


def test_rescue_certify_train_error_is_honest(tmp_path):
    game = tmp_path / "hard.gd"
    game.write_text("extends Node2D\n")

    def crash(*a, **k):
        raise RuntimeError("obs unpack failed")

    rep = C.rescue_certify(str(game), verify_report=_unsolved_with_progress(), g3_fn=crash)
    assert rep["passed"] is False and rep["failure_class"] == "UNSOLVED"
    assert rep["rescue"]["reason"] == "train_error"
    assert "obs unpack failed" in rep["rescue"]["error"]


def test_harden_rl_rescue_flag_adopts_upgraded_report(tmp_path, monkeypatch):
    game = tmp_path / "hard.gd"
    game.write_text("extends Node2D\n")
    monkeypatch.setattr(H, "verify_fn", lambda p: _unsolved_with_progress())
    monkeypatch.setattr(H, "attack_fn",
                        lambda gp, src, rep, **kw: {"schema": "g4_report/v1",
                                                    "grade": "hardened", "findings": []})
    monkeypatch.setattr(H, "render_skills_fn", lambda t, **k: "")

    def fake_rescue(gp, rep, *, budget_steps, **kw):
        up = dict(rep)
        up.update({"passed": True, "failure_class": None, "witness_source": "rl",
                   "witness": {"seed": 0, "actions": ["a"] * 30, "ticks": 30,
                               "checkpoints": {"m1": 5}},
                   "rescue": {"attempted": True, "rescued": True, "reason": "rl_certified",
                              "rl_steps": 234_000, "greedy_sr": 1.0}})
        return up

    monkeypatch.setattr(H, "rescue_fn", fake_rescue)
    rep = H.harden_game(str(game), out_dir=str(tmp_path / "sb"), backend="template",
                        run_g3=False, rl_rescue=True, ledger_path=str(tmp_path / "l.jsonl"))
    # the rescued (now certified) game hardens normally; the rescue block is surfaced.
    assert rep["rescue"]["rescued"] is True and rep["rescue"]["rl_steps"] == 234_000
    assert rep["final_verdict"] in ("HARDENED", "BULLETPROOF")


def test_verify_game_rescue_is_additive_orchestration(monkeypatch, tmp_path):
    # verify_game_rescue = plain verify_game + rescue_certify (the plain path is untouched).
    from harness.verify import gameverify as GV
    game = tmp_path / "hard.gd"
    game.write_text("extends Node2D\n")
    monkeypatch.setattr(GV, "verify_game", lambda gp, sandboxed=True, world_factory=None:
                        _unsolved_with_progress())
    monkeypatch.setattr(C, "_bridge_replay_for_engine",
                        lambda eng, src, wit: {"result": "success", "ticks": 3,
                                               "checkpoints": {"m1": 1}})
    rep = GV.verify_game_rescue(str(game), g3_fn=lambda gp, **kw: _g3_converged())
    assert rep["passed"] is True and rep["witness_source"] == "rl"


# ====================================================================== #
# 7. IN-IMAGE SMOKE — real sb3 + Godot: mini_collect converges to demo_ready and its
#    exported demo_trajectory.json replays to SUCCESS through GdExecutor.run_batch.
# ====================================================================== #
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MINI = os.path.join(_ROOT, "tests", "fixtures", "gd_games", "mini_collect.gd")

try:
    from harness.verify.executors import find_godot_exe
    _GODOT = find_godot_exe()
except Exception:  # noqa: BLE001
    _GODOT = None
requires_godot = pytest.mark.skipif(_GODOT is None, reason="Godot binary not present")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@requires_godot
def test_demo_trajectory_replays_through_gd_exec_in_image(monkeypatch, tmp_path):
    """mini_collect converges fast: a modest sb3 budget makes it demo-ready, the trained
    policy's greedy rollout is exported to demo_trajectory.json BESIDE the model, and that
    file replays to SUCCESS through the frozen GdExecutor.run_batch (the same certificate
    bridge the witness uses — so the demo IS the trained agent playing, deterministically)."""
    pytest.importorskip("stable_baselines3")
    from harness.rl.certify import g3_prime
    from harness.verify.gd_exec import GdExecutor

    monkeypatch.setenv("GIP_PORT_BASE", str(_free_port()))
    model = str(tmp_path / "policy.zip")
    # BUDGET (2026-07-16, reward realigned): the convergence probe fixed the reward to ADDITIVE
    # bounded shaping + a decayed terminal (harness.rl.env; PBRS is invariant-but-non-guiding and
    # stalls). mini_collect then reaches demo_ready — but only at a REAL budget: 8-env probes give
    # greedy 1.0 / stochastic 0.56 @ 400k (short of the 0.6 floor) and demo_ready (1.0/1.0) @ 1.5M.
    # So this in-image smoke runs the real 8-env pipeline at 1.5M with best_checkpoint (default),
    # which snapshots the best-by-greedy-eval policy — demo_ready is captured by ~500k steps
    # (~3 min < the 600s wall budget), robust to the last policy degrading (best-vs-last: last
    # greedy collapsed to 0 @ 400k while best held 1.0). ~4 min in-image, gated on Godot.
    # patience=200 matches the convergence probe: the default (40) stops the progress-plateau
    # at ~40 updates (~41k steps) before mini_collect makes checkpoint progress (the first smoke
    # run caught this — greedy 0 in 21s). 200 gives the policy room to climb to the win.
    res = g3_prime(MINI, budget_steps=1_500_000, trainer="sb3", seed=0, n_eval=8,
                   num_envs=8, patience=200, wall_clock_budget_s=600, save_model=model)

    assert res["demo_ready"] is True, (
        f"mini_collect did not converge to demo-ready "
        f"(greedy_sr={res['greedy_sr']} stochastic_sr={res['stochastic_sr']} "
        f"best_ckpt_update={res.get('best_ckpt_update')} last_greedy_sr={res.get('last_greedy_sr')})")
    path = res["demo_trajectory_path"]
    assert path == os.path.join(os.path.dirname(model), "demo_trajectory.json")
    assert os.path.exists(path)
    demo = json.loads(open(path, encoding="utf-8").read())
    assert demo["actions"] and isinstance(demo["seed"], int)

    # the exported demo trajectory replays to SUCCESS through the batch executor.
    with open(MINI, encoding="utf-8") as fh:
        src = fh.read()
    ex = GdExecutor(port_base=_free_port())
    try:
        rec = ex.run_batch(src, [{"seed": demo["seed"], "actions": demo["actions"]}],
                           max_ticks=len(demo["actions"]))
    finally:
        close = getattr(ex, "close", None)
        if callable(close):
            close()
    assert rec[0]["result"] == "success"


# ---------------------------------------------------------------------- #
# ACCEPTANCE (the FIRST RL-CERTIFIED game) — the real UNSOLVED drone course through
# rescue_certify. PENDING the dimension-aware obs fix: build_obs_vector is 2D-only and
# CRASHES (ValueError, a 2-elem unpack on pos:[x,y,z]) on this true-3D game BEFORE the first
# learning step (notes/rl_agent/ARCH_3D_ANALYSIS.md). A sibling agent owns that env.py fix;
# this test is written + gated so it flips on the moment the obs branch lands. Skipped until
# then (do NOT add a 3D-obs workaround here — that lane is owned elsewhere).
# ---------------------------------------------------------------------- #
DRONE_DIR = os.path.expanduser(
    "~/gi/scenes/games/a_3d_drone_course_pilot_a_quadcopter_thr")
_RL_3D_OBS_READY = os.environ.get("HARNESS_RL_3D_OBS_READY") == "1"


@requires_godot
@pytest.mark.skipif(not _RL_3D_OBS_READY,
                    reason="pending dimension-aware obs (ARCH_3D_ANALYSIS.md); the true-3D "
                           "drone crashes build_obs_vector until the sibling env.py fix lands "
                           "— set HARNESS_RL_3D_OBS_READY=1 after merging it")
def test_rescue_certify_drone_course_acceptance(monkeypatch, tmp_path):
    """rescue_certify on the real UNSOLVED drone game. Either it CONVERGES within a bounded
    budget -> the first RL-certified game (assert witness_source=rl + the RL witness replays);
    OR it does NOT converge -> an EQUALLY VALID honest result (assert the report stays UNSOLVED
    with an honest rescue block, which feeds the difficulty-tuner case). Both outcomes pass;
    the point is that the second-path machinery runs end-to-end on a real 3D game."""
    pytest.importorskip("stable_baselines3")
    import glob
    from harness.verify.gameverify import verify_game_rescue

    gd = glob.glob(os.path.join(DRONE_DIR, "*.gd"))
    if not gd:
        pytest.skip(f"drone game not found under {DRONE_DIR}")
    monkeypatch.setenv("GIP_PORT_BASE", str(_free_port()))
    rep = verify_game_rescue(gd[0], sandboxed=False, budget_steps=400_000, num_envs=8,
                             n_eval=8, wall_clock_budget_s=1800)

    if rep.get("passed") and rep.get("witness_source") == "rl":
        wit = rep["witness"]
        print(f"\n[RL-CERTIFIED] drone: ticks={wit['ticks']} "
              f"greedy_sr={rep['rescue']['greedy_sr']} rl_steps={rep['rescue']['rl_steps']}")
        assert wit["actions"] and wit["ticks"] >= 1
    else:
        # honest non-convergence — a valid, informative result (solvable-but-hard signal).
        assert rep["failure_class"] == "UNSOLVED"
        assert rep["rescue"]["attempted"] is True and rep["rescue"]["rescued"] is False
        print(f"\n[rescue: no cert] drone reason={rep['rescue'].get('reason')} "
              f"greedy_sr={rep['rescue'].get('greedy_sr')}")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
