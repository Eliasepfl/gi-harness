"""g3_prime — the RL-learnability certifier (LLM_RL_SYSTEMS §4.2, Phase 0).

`g3_prime(game_path, budget_steps)` trains a fresh PPO policy on the game,
evaluates it greedily over a fixed set of seeds, and emits a learnability verdict
plus the deterministic RL witness. The witness — a greedy, fixed-seed argmax
rollout reduced to an action-string sequence — MUST replay to success through the
NORMAL batch executor path; g3_prime asserts that, which is the certificate bridge:
RL slots into the determinism-first harness with zero change to the replay/witness
machinery (§4.1, risk #2).

ENGINE-NEUTRAL. The training env and the bridge executor are chosen by
`detect_engine(game_path)` — everything between (trainer, greedy/sampled eval,
witness ORACLE) is engine-agnostic:

    engine     train env         bridge executor
    -------     ---------         ---------------
    js / py     PlanckEnv         JsExecutor.run_batch
    godot       GodotServeEnv     GodotExecutor.run_batch   (.spec.json / runner.gd)
    gdscript    GodotServeEnv     GdExecutor.run_batch      (.gd GameAPI / serve_game.gd)

The two Godot dialects share ONE env class (`GodotServeEnv`, which auto-routes its
serve host) and one witness contract; only the batch executor used for the bridge
assert differs. This is what lets an RL policy PROVE a steering/vehicle `.gd` game
the G3 tree solver cannot: a learned greedy rollout reduces to a deterministic
{seed, argmax-actions} witness that wins through the frozen batch host (moat intact).

PARALLEL / farming. g3_prime is ONE game; a fleet runs one game per Slurm-array task
(`~/orcd/scratch/gi/g3p_farm.sbatch`). The gdscript manifest is
``find scenes/games -name '*.gd'`` (the godot manifest is the tracked ``*.spec.json``
set); task *t* reads line *t*. Each task MUST own a disjoint loopback port band —
``export GIP_PORT_BASE=$(( 47000 + SLURM_ARRAY_TASK_ID * 64 ))`` — because every
GodotServeEnv binds ``GIP_PORT_BASE + port_offset`` and g3_prime hands its vec/eval
slots increasing offsets (see the make_env port_seq below). Within a task, sb3's
DummyVecEnv gives the real-budget parallelism (num_envs slots, disjoint offsets).

`learnable` is TRUE when the greedy success rate over the eval seeds clears
LEARNABLE_SUCCESS_RATE. A negative (no success within budget, flat curve) is a
valid datapoint — recorded honestly, never hung on (the "declare UNSOLVABLE-BY-RL
and move on" rule lives in ppo.train's plateau early-stop).
"""

from __future__ import annotations

import json
import os
import time

from harness.rl.env import PlanckEnv
from harness.verify.chord import wire_actions

# --- Constants ([eng.]) ------------------------------------------------------
DEFAULT_BUDGET = 2_000_000       # env-steps per game (LLM_RL_SYSTEMS §4.1) [eng.]
N_EVAL = 32                      # greedy eval episodes (fixed seeds) [eng.]
LEARNABLE_SUCCESS_RATE = 0.5     # greedy success rate to call a game learnable [eng.]
TRAINERS = ("vendored", "sb3")   # RL trainer backends (sb3 default post-parity R1; vendored kept until one live curriculum round confirms)

# --- Demo-readiness / critic-competence thresholds ([eng.]) ------------------
# Elias's rule: don't ship a demo (or hand a critic to the value-inverse attacker) off the
# FIRST lucky witness — the policy may not be optimized and a single win can be chance. Wait
# until the trained greedy policy wins RELIABLY over the eval seeds AND is robust under
# action-sampling noise before we call it demo-ready / critic-competent.
#
#   DEMO_SR_MIN         GREEDY (deterministic-replay) success floor. Elias wants ~100% over a
#                       few episodes; 0.9 tolerates one flaky seed on an rng game while on the
#                       fully-deterministic showcase games (greedy is binary 0/1) it means
#                       "wins on every eval seed". The demo we ship is THIS greedy rollout.
#   DEMO_STOCHASTIC_FLOOR
#                       STOCHASTIC (sampled-rollout) success floor — the "not luck" guard.
#                       On deterministic games greedy collapses to a single trajectory, so the
#                       graded evidence that the policy found a ROBUST basin (not a knife-edge
#                       single path) comes from the sampled rollouts. Sits ABOVE
#                       LEARNABLE_SUCCESS_RATE (0.5) so demo_ready strictly implies learnable.
DEMO_SR_MIN = 0.9                # greedy success rate floor for a demo-ready policy [eng.]
DEMO_STOCHASTIC_FLOOR = 0.6      # stochastic success floor — robustness / not-luck guard [eng.]


def is_demo_ready(greedy_sr, stochastic_sr, *, sr_min: float = DEMO_SR_MIN,
                  stochastic_floor: float = DEMO_STOCHASTIC_FLOOR) -> bool:
    """A trained policy is DEMO-READY iff its GREEDY success rate clears ``sr_min`` AND its
    STOCHASTIC success rate clears ``stochastic_floor`` (BOTH — the greedy floor proves the
    deterministic demo replays reliably, the stochastic floor proves it is not a lucky single
    path). Pure; the thresholds are [eng.] knobs. A missing/None rate is NOT demo-ready."""
    try:
        g = float(greedy_sr)
        s = float(stochastic_sr)
    except (TypeError, ValueError):
        return False
    return bool(g >= sr_min and s >= stochastic_floor)


def critic_competent(g3_result) -> bool:
    """Is the TRAINED CRITIC in a g3' result converged enough to STEER g4's smart tiers
    (inverse-value / policy-descent / value_death)?

    The A/B showed a WEAK critic scores ~0 — its value map is noise, so a smart tier built on
    it is worse than the critic-free fuzz. Competence is demo_ready-style convergence: the
    greedy policy reliably solves AND is robust under sampling, which is exactly the signal
    that the value function separates on-path (high-V) from off-path/frozen (low-V) states —
    the structure the anti-policy / V-frontier / value-death attacks exploit. A missing or
    still-training result is NOT competent (honest default: downgrade to the critic-free
    ladder). ONE shared predicate, consumed by g4's model-gate AND harden's oracle step."""
    if not isinstance(g3_result, dict):
        return False
    return bool(g3_result.get("demo_ready"))


def _pick_demo_trajectory(greedy_eps: list[dict]) -> dict | None:
    """The trained agent's OWN deterministic winning rollout, reduced to a replayable demo:
    the best SUCCESSFUL GREEDY (argmax) episode — fewest ticks, then lowest seed. GREEDY only,
    so the demo is reproducible (same policy + seed -> same actions) — this is the reliable
    trained policy playing, NOT the first lucky tree-solver witness. Same {seed, actions}
    shape as an rl_witness; ``None`` when no greedy episode won."""
    wins = [e for e in (greedy_eps or []) if e.get("success")]
    if not wins:
        return None
    best = min(wins, key=lambda e: (e["ticks"], e["seed"]))
    return {"seed": best["seed"], "actions": list(best["actions"]),
            "ticks": best["ticks"], "greedy": True}


def export_demo_trajectory(trajectory: dict, path: str) -> str:
    """Persist a demo trajectory ({seed, actions, ...}) to ``path`` as the witness-shaped
    JSON the capture CLI replays verbatim::

        harness game capture <game.gd> --actions <path>

    Capture reads only ``seed`` + ``actions`` (extras are harmless provenance). The demo then
    IS the trained agent playing, deterministically. Creates the parent dir; returns ``path``."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    # Canonicalize each action to its WIRE form through the ONE chord boundary: a single
    # verb stays a plain str (byte-identical legacy demo), a chord is a sorted list, and an
    # all-off IDLE tick is the empty list []. Routing through wire_actions (never str(a),
    # which would flatten a chord list into "['a','b']") keeps a Phase-2 demo replayable.
    _raw = list(trajectory["actions"])
    _has_idle = any(isinstance(a, (list, tuple)) and len(a) == 0 for a in _raw)
    payload = {"seed": int(trajectory["seed"]),
               "actions": wire_actions(_raw, allow_empty=_has_idle),
               "ticks": trajectory.get("ticks"),
               "greedy": bool(trajectory.get("greedy", True)),
               "source": "g3_demo"}       # provenance: the trained-policy demo (not a witness)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    return path


def still_improving_from_curve(curve_return, *, patience: int, window: int,
                               min_delta: float) -> bool:
    """Reconstruct the plateau early-stop bookkeeping over a return curve and report
    whether the curve was STILL IMPROVING when it ended (patience never exhausted).

    PURE and deterministic — the offline counterpart of the trainer callback's live
    plateau logic (sb3_trainer/ppo: smoothed rolling mean over `window` updates, a new
    best requires `> best + min_delta`, stop after `patience` updates with no new best).
    A monotonically-climbing curve never plateaus (True); a curve flat for >= patience
    updates has converged (False). Used only as a FALLBACK when a trainer result does
    not carry the authoritative ``plateau_stopped`` flag."""
    curve = [float(c) for c in (curve_return or [])]
    if not curve:
        return False
    best = -1e9
    since = 0
    for i in range(len(curve)):
        smoothed = sum(curve[max(0, i - window + 1):i + 1]) / float(
            min(i + 1, window))
        if smoothed > best + min_delta:
            best = smoothed
            since = 0
        else:
            since += 1
    return since < patience


def _still_improving(train_res: dict) -> bool:
    """Was the learning curve still improving when the budget ended?

    Prefer the trainer's authoritative ``plateau_stopped`` flag (set ONLY by the
    patience-plateau branch — a wall-clock or budget-exhaustion stop leaves it False,
    since neither means convergence). Fall back to reconstructing the plateau from the
    return curve when a trainer does not surface the flag."""
    plateau_stopped = train_res.get("plateau_stopped")
    if plateau_stopped is not None:
        return not bool(plateau_stopped)
    if not train_res.get("stopped_early", False):
        return True                       # ran to the full budget -> still climbing
    hp = train_res.get("hp") or {}
    return still_improving_from_curve(
        train_res.get("curve_return") or [],
        patience=int(hp.get("patience", 40)),
        window=int(hp.get("plateau_window", 10)),
        min_delta=float(hp.get("min_delta", 0.05)))


def _per_checkpoint_latch_rate(eval_eps: list[dict], cp_keys: list[str]) -> dict:
    """Fraction of eval episodes in which each declared checkpoint latched.

    The per-CHECKPOINT companion to `checkpoints_curve` (which is only the per-update
    MEAN latch COUNT during training). Each eval episode carries a `latched` dict
    (checkpoint -> tick when reached, None/absent otherwise); a checkpoint counts as
    latched for an episode when its value is not None. Over BOTH the greedy and sampled
    eval pools so the rate is robust on fully-deterministic games (where greedy is a
    single trajectory). Enables the feedback compiler's checkpoint-pair directive."""
    eps = list(eval_eps or [])
    n = len(eps)
    if not n or not cp_keys:
        return {k: 0.0 for k in (cp_keys or [])}
    rates = {}
    for k in cp_keys:
        hits = sum(1 for e in eps if (e.get("latched") or {}).get(k) is not None)
        rates[k] = round(hits / float(n), 3)
    return rates


# GENERIC movement-axis keywords (substring match on the lowercased action NAME — never a
# game-specific list). A game whose actions use these common words gets per-axis aggregates;
# any action that matches none lands in "other", so the axis buckets always sum to the tick
# total. This is a DIAGNOSTIC lens (does a 3D policy actually use altitude, or collapse to a
# plane?), not a control surface — the raw per-action counts are always the ground truth.
_AXIS_KEYWORDS = {
    "vertical": ("up", "down", "rise", "fall", "ascend", "descend", "climb", "lift", "drop", "hover"),
    "lateral": ("left", "right", "strafe", "bank", "yaw", "roll"),
    "forward_brake": ("forward", "fwd", "ahead", "thrust", "accel", "throttle", "boost",
                      "back", "reverse", "brake", "decel", "coast", "stop"),
}


def _axis_of(action_name: str):
    """The movement axis an action name trivially maps to (first match), or ``None``."""
    al = str(action_name).lower()
    for axis, kws in _AXIS_KEYWORDS.items():
        if any(kw in al for kw in kws):
            return axis
    return None


def _wire_action_verbs(a) -> list:
    """The verbs a single WIRE action pressed this tick (a diagnostic COUNT lens, not the
    canonicalization boundary): a plain str -> ``[str]`` (a noop ``""`` -> ``[]``); a chord
    list/tuple -> its elements (an empty chord [] -> ``[]``, the IDLE tick). Anything else
    is stringified into a single bucket so the histogram never crashes on odd data."""
    if isinstance(a, str):
        return [] if a == "" else [a]
    if isinstance(a, (list, tuple)):
        return [str(v) for v in a]
    return [str(a)]


def action_histogram(episodes: list[dict], action_names: list[str],
                     with_axes: bool = False, chord: bool = False) -> dict:
    """Action-usage histogram over a pool of eval episodes (each carrying an ``actions`` list).

    DISCRETE (default): each tick is one action-name string. Returns ``per_action`` counts
    (every declared action seeded to 0) and ``total_ticks``; ``per_action`` ALWAYS sums to
    ``total_ticks``. When ``with_axes`` (3D games) it also returns ``per_axis`` — vertical /
    lateral / forward_brake / other — derived GENERICALLY from action names (``_AXIS_KEYWORDS``),
    plus ``per_axis_frac``; the exclusive (first-match) axis buckets also sum to ``total_ticks``.

    CHORD (``chord=True``, Phase 2): each tick is a WIRE action — a str (single key), a sorted
    list (a chord), or ``[]`` (an IDLE tick). ``per_action`` becomes per-KEY PRESS frequency (a
    2-key tick increments two keys), so it sums to ``total_key_presses`` (>= total_ticks), NOT
    total_ticks. It ALSO returns the CHORD-SIZE distribution ``chord_size`` (``0``/``1``/``2``/``3+``
    keys per tick) + ``chord_size_frac`` + ``mean_chord_size`` — the signal that tells us whether
    the policy actually uses SIMULTANEITY or degenerates to single keys (or idle-spam, which STAKES
    punishes). ``with_axes`` aggregates the per-key counts by axis. Pure + JSON-serializable."""
    counts = {str(a): 0 for a in (action_names or [])}
    if not chord:
        total = 0
        for ep in (episodes or []):
            for a in ep.get("actions", []):
                key = str(a)
                counts[key] = counts.get(key, 0) + 1
                total += 1
        hist = {"per_action": counts, "total_ticks": total}
        axis_denom = total
    else:
        total_ticks = 0
        key_presses = 0
        size_counts = {"0": 0, "1": 0, "2": 0, "3+": 0}
        for ep in (episodes or []):
            for a in ep.get("actions", []):
                verbs = _wire_action_verbs(a)
                total_ticks += 1
                n = len(verbs)
                size_counts["3+" if n >= 3 else str(n)] += 1
                for v in verbs:
                    counts[v] = counts.get(v, 0) + 1
                    key_presses += 1
        hist = {
            "per_action": counts,                 # per-KEY press frequency (sums to key_presses)
            "total_ticks": total_ticks,
            "total_key_presses": key_presses,
            "chord_size": size_counts,            # 0/1/2/3+ keys pressed per tick
            "chord_size_frac": {k: (round(v / total_ticks, 3) if total_ticks else 0.0)
                                for k, v in size_counts.items()},
            "mean_chord_size": (round(key_presses / total_ticks, 3) if total_ticks else 0.0),
        }
        axis_denom = key_presses
    if with_axes:
        axes = {"vertical": 0, "lateral": 0, "forward_brake": 0, "other": 0}
        for a, c in counts.items():
            axes[_axis_of(a) or "other"] += c
        hist["per_axis"] = axes
        hist["per_axis_frac"] = {k: (round(v / axis_denom, 3) if axis_denom else 0.0)
                                 for k, v in axes.items()}
    return hist


def _resolve_trainer(trainer: str):
    """Return the trainer module exposing ``train`` / ``greedy_episode`` /
    ``sample_episode``. ``vendored`` is the CleanRL-mirror PPO (`harness.rl.ppo`,
    the unchanged default); ``sb3`` is the library-first SB3 migration
    (`harness.rl.sb3_trainer`, GODOT_RL_AGENTS_CAPABILITIES.md §6.7). Imported
    lazily so the vendored lane never touches the optional stable-baselines3 dep."""
    if trainer == "vendored":
        from harness.rl import ppo
        return ppo
    if trainer == "sb3":
        from harness.rl import sb3_trainer
        return sb3_trainer
    raise ValueError(f"unknown trainer {trainer!r} (expected one of {TRAINERS})")


def _pick_witness(greedy_eps: list[dict], sampled_eps: list[dict]) -> dict | None:
    """Best witness = a successful GREEDY episode (fewest ticks) if one exists,
    else the shortest successful SAMPLED episode. Either is a concrete (seed,
    actions) pair that replays bit-exactly through the batch executor — the
    'greedy' preference keeps the determinism-first spirit, the sampled fallback
    lets us still emit a witness for a learnable-but-not-yet-sharp policy."""
    for pool in (greedy_eps, sampled_eps):
        wins = [e for e in pool if e["success"]]
        if wins:
            best = min(wins, key=lambda e: (e["ticks"], e["seed"]))
            return {"seed": best["seed"], "actions": list(best["actions"]),
                    "ticks": best["ticks"], "greedy": best.get("greedy", False)}
    return None


def _bridge_replay(game_source: str, witness: dict) -> dict:
    """Replay the RL witness through the NORMAL batch executor (JsExecutor) and
    return its record. This is the certificate bridge — the caller asserts success."""
    from harness.verify.executors import JsExecutor
    ex = JsExecutor()
    recs = ex.run_batch(
        game_source,
        [{"seed": witness["seed"], "actions": list(witness["actions"])}],
        max_ticks=len(witness["actions"]))
    return recs[0]


def _bridge_replay_godot(game_source: str, witness: dict) -> dict:
    """Godot twin of :func:`_bridge_replay`: replay the RL witness through the
    NORMAL batch executor (``GodotExecutor.run_batch``, which already exists) and
    return its record. Same certificate-bridge contract — a serve-recorded (seed,
    actions) pair MUST win through the frozen ``runner.gd``'s batch mode. The witness
    ORACLE (:func:`_pick_witness`) and the js/py :func:`_bridge_replay` are untouched;
    only the executor differs by engine."""
    from harness.verify.executors import GodotExecutor
    ex = GodotExecutor()
    recs = ex.run_batch(
        game_source,
        [{"seed": witness["seed"], "actions": list(witness["actions"])}],
        max_ticks=len(witness["actions"]))
    return recs[0]


def _bridge_replay_gdscript(game_source: str, witness: dict) -> dict:
    """GDScript twin of :func:`_bridge_replay`: replay the RL witness through the
    NORMAL gdscript batch executor (``GdExecutor.run_batch`` — the serve-contract
    executor that compiles + drives a `.gd` GameAPI game via ``serve_game.gd``) and
    return its record. Same certificate-bridge contract as the js/godot twins — a
    serve-recorded (seed, actions) pair MUST win through the batch host, which shares
    the serve host's per-tick semantics byte for byte; only the executor differs by
    engine, so :func:`_pick_witness` and the sibling bridges stay untouched."""
    from harness.verify.gd_exec import GdExecutor
    ex = GdExecutor()
    recs = ex.run_batch(
        game_source,
        [{"seed": witness["seed"], "actions": list(witness["actions"])}],
        max_ticks=len(witness["actions"]))
    return recs[0]


def g3_prime(game_path: str, budget_steps: int = DEFAULT_BUDGET, *,
             n_eval: int = N_EVAL, seed: int = 0, log=None,
             wall_clock_budget_s=None, trainer: str = "sb3",
             method: str = "ppo", save_model: str | None = None,
             demo_out: str | None = None, demo_sr_min: float = DEMO_SR_MIN,
             demo_stochastic_floor: float = DEMO_STOCHASTIC_FLOOR,
             rays: dict | None = None, obs_profile: str = "positions",
             best_checkpoint: bool = True,
             chord_mode: bool = False, allow_idle: bool | None = None,
             ban_contradictions: bool = True,
             **train_kwargs) -> dict:
    """Train, greedily evaluate, and emit the learnability certificate for one game.

    `trainer` selects the RL backend: ``"vendored"`` (default, the CleanRL-mirror
    PPO in `harness.rl.ppo`) or ``"sb3"`` (the library-first SB3 PPO migration,
    GODOT_RL_AGENTS_CAPABILITIES.md §6.7). BOTH drive the same PlanckEnv seam and
    the same greedy/sampled eval-episode emission, so the witness ORACLE below
    (`_pick_witness`/`_bridge_replay`) is identical regardless of trainer.

    `method` selects the SB3 algorithm (``ppo`` default / ``a2c`` / ``dqn``) and is
    a pass-through to the SB3 trainer ONLY — the algo registry is an sb3-lane seam,
    so a non-default `method` on ``trainer="vendored"`` is rejected with a clear
    error. It is recorded in the result dict (``method``) for the ledger.

    `best_checkpoint` (default True, SB3 lane) makes the trainer snapshot the best-by-success
    policy during training and evaluates THAT (reloaded from disk) for the demo-readiness gate
    + witness — not the final policy, which the high-variance Godot-serve training can degrade
    (Elias, 2026-07-16). It also records the final policy's rates (``last_greedy_sr`` /
    ``last_stochastic_sr``) and a from-disk reload-parity flag (``reload_parity_ok``). With no
    snapshot saved (nothing beat 0 success) or on the vendored lane it is a no-op: the final
    in-memory agent is evaluated exactly as before.

    Returns (task-required keys + provenance extras):
        learnable, steps_to_first_success, checkpoints_curve (per-update mean
        latches), final_success_rate (over n_eval greedy episodes),
        rl_witness ({seed, actions, ticks} | None), wall_clock_s,
        still_improving (curve improving when the budget ended -> the compiler emits
        `continue_training`), per_checkpoint_latch_rate ({checkpoint: fraction of eval
        episodes that latched it} -> the compiler's checkpoint-pair localiser),
        demo_ready (greedy_sr >= demo_sr_min AND stochastic_sr >= demo_stochastic_floor —
        the policy wins RELIABLY, so its greedy rollout is worth shipping as the demo AND
        its critic is worth handing to g4; see `is_demo_ready`/`critic_competent`),
        greedy_sr / stochastic_sr (the two demo-gate rates), demo_trajectory
        ({seed, actions, ticks, greedy} | None — the trained agent's own winning greedy
        rollout when demo_ready), demo_trajectory_path (where it was written, or None).

    `demo_out` overrides where the demo trajectory JSON is written; when omitted it lands
    beside the saved model artifact (`<save_model dir>/demo_trajectory.json`). The trajectory
    replays deterministically via `harness game capture <game> --actions <path>`.
    """
    # The algo registry lives on the SB3 lane only; the vendored CleanRL-mirror PPO
    # exposes no `method` seam, so reject a non-default method up front (before any
    # env/training work) with a message that points at the right lane.
    if trainer == "vendored" and method != "ppo":
        raise ValueError(
            f"trainer='vendored' does not support method={method!r}: the algo "
            f"registry (ppo|a2c|dqn) is exposed only by the SB3 trainer — use "
            f"trainer='sb3', or keep method='ppo'")

    t0 = time.time()
    trainer_mod = _resolve_trainer(trainer)

    # Engine-neutral seam: both Godot dialects — godot ('.spec.json', via runner.gd)
    # and gdscript ('.gd' GameAPI game, via serve_game.gd) — run over GodotServeEnv (the
    # serve/TCP sibling of PlanckEnv, which auto-routes the host by detect_engine); js/py
    # stay on PlanckEnv. All expose the same obs/action + seeded-reset surface, so the
    # trainer, eval, witness extraction and the bridge assert below are engine-agnostic —
    # only the env class and the batch executor used for the bridge differ.
    from harness.verify.gameverify import detect_engine
    with open(game_path, "r", encoding="utf-8") as fh:
        game_source = fh.read()
    engine = detect_engine(game_path, game_source)

    # CHORD (Phase 2) is a Godot-lane feature: the MultiBinary action space + the empty-chord
    # IDLE capability live in GodotServeEnv/serve_game.gd. The js/py PlanckEnv lane has no such
    # seam, so reject chord_mode there up front rather than silently degrading to Discrete.
    if chord_mode and engine not in ("godot", "gdscript"):
        raise ValueError(
            f"chord_mode (MultiBinary Phase 2) is a Godot-lane feature; engine "
            f"{engine!r} is not supported")

    make_batch_venv = None
    make_shard_venv = None
    # CONTRADICTORY-CHORD projection (Phase 2, Elias): the ONE single-instance probe env
    # (the `probe = make_env()` below) mechanically discovers near-antiparallel action pairs;
    # they are shared to every training/eval env via this holder so the whole run uses ONE
    # measured opposition set (the batched host never self-probes). None -> "discover" (the
    # probe env); a list -> "use these" (every later env). Empty when chord_mode/ban is off.
    _oppose = {"pairs": None, "named": []}

    if engine in ("godot", "gdscript"):
        import itertools
        from harness.rl.godot_env import GodotServeEnv
        # Each concurrent env needs a disjoint loopback port; hand out increasing
        # offsets off GIP_PORT_BASE (§6.2 — one Slurm task's base, its vec slots).
        _port_seq = itertools.count()

        def make_env():
            # `obs_profile` (positions | positions+rays | rays) + `rays` (the egocentric
            # raycast grid config) are additive kwargs; the default "positions" keeps the
            # env byte-identical to the pre-rays obs. Captured here so the probe, training,
            # and eval envs all size the obs identically. `oppose_pairs=None` on the FIRST
            # (probe) call -> that env self-discovers; later calls receive the shared list.
            return GodotServeEnv(game_path, port_offset=next(_port_seq), rays=rays,
                                 obs_profile=obs_profile,
                                 chord_mode=chord_mode, allow_idle=allow_idle,
                                 ban_contradictions=ban_contradictions,
                                 oppose_pairs=_oppose["pairs"])

        # MULTI-CPU PER GAME: the GDScript lane (serve_game.gd) can serve N in-scene
        # instances over ONE process/socket, so hand the SB3 trainer a batch-vec-env
        # factory (used for num_envs>1 unless HARNESS_VECENV=dummy). Its ONE process
        # takes the next disjoint port offset. The godot/.spec.json (runner.gd) serve
        # is not batched yet, so it stays on the sequential DummyVecEnv slots below.
        if engine == "gdscript":
            def make_batch_venv(n_instances):
                from harness.rl.godot_vec_env import GodotBatchVecEnv
                return GodotBatchVecEnv(game_path, n_instances,
                                        port_offset=next(_port_seq), seed=seed,
                                        rays=rays, obs_profile=obs_profile,
                                        chord_mode=chord_mode, allow_idle=allow_idle,
                                        ban_contradictions=ban_contradictions,
                                        oppose_pairs=_oppose["pairs"])

            # SHARDING (Elias, 2026-07-16: "32 cores per run"): M INDEPENDENT batch
            # shards stepped concurrently = M*K logical envs, so ONE learner saturates
            # many cores. The trainer uses THIS factory only when num_shards>1; the
            # cluster owns a strided sub-band [base_off, base_off+(M-1)*PORT_STRIDE] off
            # the SAME _port_seq (one offset consumed for the whole cluster). Shard i is
            # seeded base_seed+i*K — the per-slot fixed-seed scheme, extended across M*K.
            # Rays/obs_profile flow to every shard via env_kwargs (same obs everywhere).
            def make_shard_venv(num_shards, n_instances):
                from harness.rl.godot_shard_env import GodotShardVecEnv
                return GodotShardVecEnv(game_path, num_shards, n_instances,
                                        base_seed=seed,
                                        port_offset_base=next(_port_seq),
                                        env_kwargs={"rays": rays,
                                                    "obs_profile": obs_profile,
                                                    "chord_mode": chord_mode,
                                                    "allow_idle": allow_idle,
                                                    "ban_contradictions": ban_contradictions,
                                                    "oppose_pairs": _oppose["pairs"]})
    else:
        def make_env():
            return PlanckEnv(game_path)

    # Probe the game once to size the policy (spaces are frozen at construction).
    probe = make_env()
    obs_dim = probe.observation_space.shape[0]
    n_actions = probe.action_space.n
    title = probe.title
    n_bodies = len(probe._body_order)
    cp_keys = list(probe._cp_keys)
    action_names = list(getattr(probe, "actions", []) or [])   # for the eval action histogram
    is_3d = int(getattr(probe, "_dim", 2)) == 3    # per-axis aggregates are for 3D games
    # The probe env self-discovered the MEASURED contradictory-chord pairs (chord mode + ban);
    # share them to every training/eval env via the holder and record them (index + action-name
    # pairs) for the eval artifact — transparency (Elias reads these) and reproducibility.
    _disc = [tuple(p) for p in (getattr(probe, "oppose_pairs", []) or [])]
    _oppose["pairs"] = _disc
    _oppose["named"] = [[action_names[i], action_names[j]] for (i, j) in _disc
                        if i < len(action_names) and j < len(action_names)]
    probe.close()

    # --- Train ---
    # `method` is an SB3-only kwarg (the vendored ppo.train takes no such arg), so
    # forward it only on the sb3 lane; the vendored lane was already gated above.
    method_kw = {} if trainer == "vendored" else {"method": method}
    # The batched in-scene vec env is an SB3-lane seam (the vendored ppo.train takes
    # no such arg); forward the factory only there, and only when one was built (the
    # gdscript lane). The trainer itself decides whether to use it (num_envs>1, not
    # HARNESS_VECENV=dummy) — a None factory keeps the sequential DummyVecEnv path.
    if trainer != "vendored" and make_batch_venv is not None:
        method_kw["make_batch_venv"] = make_batch_venv
    # The sharded (M concurrent batch shards) vec env is likewise an SB3-lane seam; the
    # trainer engages it only when a caller passes num_shards>1 (a None factory or the
    # default num_shards=1 keeps the single-process batch path byte-identical). num_shards
    # itself rides in via **train_kwargs -> sb3_trainer.train's explicit num_shards param.
    if trainer != "vendored" and make_shard_venv is not None:
        method_kw["make_shard_venv"] = make_shard_venv
    # BEST-CHECKPOINT (Elias, 2026-07-16): the Godot-serve training is high-variance, so the
    # LAST policy under-reports the run. Ask the SB3 trainer to snapshot the best-by-success
    # policy to a temp .zip; we evaluate THAT below (falling back to the final agent when no
    # snapshot beat 0 success). SB3-lane only, and off unless `best_checkpoint`. The temp dir
    # lives until after eval (we reload from it), then is cleaned in the finally.
    import tempfile
    _best_dir = None
    _eval_probe_env = None
    if best_checkpoint and trainer != "vendored":
        _best_dir = tempfile.mkdtemp(prefix="g3_bestckpt_")
        method_kw["best_model_path"] = os.path.join(_best_dir, "best_policy.zip")
        # Dedicated eval env for the periodic best-checkpoint GREEDY eval. Created BEFORE the
        # trainer builds its (batch/shard) venv so it takes a LOWER loopback offset, disjoint
        # from the training env's port band (the shard cluster reserves a contiguous higher
        # block). Stepped only inside the callback (single-threaded), closed after training.
        _eval_probe_env = make_env()
        _n_pg, _n_ps = 8, 4

        def _eval_fn(model):
            g = [trainer_mod.greedy_episode(_eval_probe_env, model, seed=s) for s in range(_n_pg)]
            st = [trainer_mod.sample_episode(_eval_probe_env, model, seed=s, torch_seed=2000 + s)
                  for s in range(_n_ps)]
            return {"greedy_sr": sum(1 for e in g if e["success"]) / float(_n_pg),
                    "stochastic_sr": sum(1 for e in st if e["success"]) / float(_n_ps)}

        method_kw["eval_fn"] = _eval_fn
    train_res = trainer_mod.train(make_env, obs_dim, n_actions,
                                  total_steps=budget_steps, seed=seed, log=log,
                                  wall_clock_budget_s=wall_clock_budget_s,
                                  **method_kw, **train_kwargs)
    if _eval_probe_env is not None:
        _eval_probe_env.close()
    agent = train_res["agent"]

    # BEST-CHECKPOINT: the policy we certify is the best-by-success snapshot the trainer saved
    # (reloaded FROM DISK), falling back to the final in-memory agent when no snapshot beat 0
    # success. The demo-readiness gate, the witness, AND the saved artifact therefore reflect the
    # BEST policy the run reached, not the last (which the high-variance Godot training can
    # degrade). `eval_agent` may be the final agent, so everything downstream is unchanged when
    # best_checkpoint is off / nothing was saved.
    best_ckpt_path = train_res.get("best_model_path")
    used_best_checkpoint = bool(best_ckpt_path)
    if used_best_checkpoint:
        eval_agent = type(agent).load(best_ckpt_path)     # SB3 .load — a from-disk reload
    else:
        eval_agent = agent

    # Optional: persist the certified policy cheaply (SB3 .zip) so the G4 inverse-value attacker
    # (harness.rl.adversary) can reload the critic/policy artifact without retraining — the BEST
    # snapshot, so its critic matches the demo_ready verdict. Only the SB3 lane exposes .save().
    saved_model_path = None
    if save_model:
        _save = getattr(eval_agent, "save", None)
        if callable(_save):
            _save(save_model)
            saved_model_path = save_model

    # --- Evaluation over fixed seeds ---
    # NB: the showcase games use no world.rng, so they are fully DETERMINISTIC —
    # every greedy episode is the SAME trajectory, making the greedy success rate
    # binary (0 or 1). The graded learnability signal therefore comes from the
    # STOCHASTIC (sampled) rollouts; greedy is reported too (it is the witness's
    # preferred form and the determinism-first certificate).
    eval_env = make_env()
    greedy_eps = [trainer_mod.greedy_episode(eval_env, eval_agent, seed=s)
                  for s in range(n_eval)]
    sampled_eps = [trainer_mod.sample_episode(eval_env, eval_agent, seed=s,
                                              torch_seed=1000 + s)
                   for s in range(n_eval)]
    n_greedy = sum(1 for e in greedy_eps if e["success"])
    n_sampled = sum(1 for e in sampled_eps if e["success"])
    final_success_rate = round(n_greedy / float(n_eval), 3)     # greedy (task key)
    stochastic_success_rate = round(n_sampled / float(n_eval), 3)  # graded signal

    # best-vs-last provenance + from-disk RELOAD PARITY (Elias's checkpoint-loading suspicion):
    # when a best snapshot is in play, also evaluate the FINAL (in-memory) policy AND a
    # from-disk reload of those same final weights — the two MUST match (a mismatch would mean
    # a low greedy SR was a reload bug, not true divergence). last_* records the final policy.
    last_greedy_sr = last_stochastic_sr = reload_parity_ok = None
    if used_best_checkpoint and callable(getattr(agent, "save", None)):
        last_greedy = [trainer_mod.greedy_episode(eval_env, agent, seed=s)
                       for s in range(n_eval)]
        last_sampled = [trainer_mod.sample_episode(eval_env, agent, seed=s,
                                                   torch_seed=1000 + s) for s in range(n_eval)]
        last_greedy_sr = round(sum(1 for e in last_greedy if e["success"]) / float(n_eval), 3)
        last_stochastic_sr = round(sum(1 for e in last_sampled if e["success"]) / float(n_eval), 3)
        _rl_dir = tempfile.mkdtemp(prefix="g3_reload_")
        try:
            _rl_path = os.path.join(_rl_dir, "final.zip")
            agent.save(_rl_path)
            reloaded = type(agent).load(_rl_path)
            rel_greedy = [trainer_mod.greedy_episode(eval_env, reloaded, seed=s)
                          for s in range(n_eval)]
            # identical greedy action strings in-memory vs from-disk == byte-faithful reload.
            reload_parity_ok = all(a["actions"] == b["actions"]
                                   for a, b in zip(last_greedy, rel_greedy))
        finally:
            import shutil
            shutil.rmtree(_rl_dir, ignore_errors=True)
    eval_env.close()
    if _best_dir is not None:
        import shutil
        shutil.rmtree(_best_dir, ignore_errors=True)

    # Per-checkpoint latch rate over ALL eval episodes (greedy + sampled) — WHICH
    # declared milestones the trained policy actually reaches. The feedback compiler
    # reads this to name the last-reliably-latched / first-never-latched checkpoint
    # pair (a stalled agent) or to detect that NOTHING ever latched (unsolvable).
    per_checkpoint_latch_rate = _per_checkpoint_latch_rate(
        greedy_eps + sampled_eps, cp_keys)

    # ACTION HISTOGRAM (Elias, 2026-07-16): what the trained policy actually DOES over the eval
    # rounds — per-action counts for the greedy and stochastic pools separately, plus (3D only)
    # generic vertical/lateral/forward_brake axis aggregates, so we can see whether a 3D policy
    # uses altitude or collapses to planar movement. Persisted in this eval artifact (result).
    # CHORD (Phase 2): per-KEY press frequency + the chord-size distribution (0/1/2/3+ keys per
    # tick) — the signal that says whether the policy uses SIMULTANEITY or collapses to single
    # keys / idle-spam. Discrete keeps the per-action-string counts byte-identical.
    action_hist = {
        "greedy": action_histogram(greedy_eps, action_names, with_axes=is_3d,
                                   chord=chord_mode),
        "stochastic": action_histogram(sampled_eps, action_names, with_axes=is_3d,
                                       chord=chord_mode),
    }

    # --- RL witness + the certificate bridge (assert it replays via JsExecutor) ---
    witness = _pick_witness(greedy_eps, sampled_eps)
    bridge_ok = None
    bridge_result = None
    if witness is not None:
        # Replay through the matching batch executor (js/py -> JsExecutor, godot ->
        # GodotExecutor, gdscript -> GdExecutor); the witness ORACLE / bridge machinery
        # is unchanged.
        if engine == "godot":
            rec = _bridge_replay_godot(game_source, witness)
        elif engine == "gdscript":
            rec = _bridge_replay_gdscript(game_source, witness)
        else:
            rec = _bridge_replay(game_source, witness)
        bridge_result = rec.get("result")
        bridge_ok = bridge_result == "success"
        # The bridge is the whole point: a greedy witness recorded in serve mode
        # MUST win through the batch executor (identical semantics). Fail loud.
        assert bridge_ok, (
            f"RL witness failed to replay to success via the batch executor "
            f"(got {bridge_result!r}) — serve/batch determinism broken")

    # Learnability is judged on the GRADED (stochastic) success rate — robust to
    # the deterministic-env degeneracy above — OR a clean greedy solve.
    learnable = (stochastic_success_rate >= LEARNABLE_SUCCESS_RATE
                 or final_success_rate >= LEARNABLE_SUCCESS_RATE)

    # DEMO-READY / CRITIC-COMPETENT gate (Elias): a demo (and the value-inverse attacker's
    # critic) must come from a RELIABLY-winning policy, not the first lucky witness. Both the
    # greedy floor (the deterministic demo replays reliably) AND the stochastic floor (the win
    # is not a knife-edge single path) must clear. STRICTLY stronger than `learnable`.
    greedy_sr = final_success_rate
    stochastic_sr = stochastic_success_rate
    demo_ready = is_demo_ready(greedy_sr, stochastic_sr, sr_min=demo_sr_min,
                               stochastic_floor=demo_stochastic_floor)

    # When demo-ready, export the trained policy's OWN winning greedy rollout as a replayable
    # {seed, actions} demo trajectory (the demo IS the certified reliable agent playing). It
    # is written beside the model artifact (or to `demo_out`) so the capture CLI can replay it:
    # `harness game capture <game> --actions <demo_trajectory.json>`.
    demo_trajectory = _pick_demo_trajectory(greedy_eps) if demo_ready else None
    demo_trajectory_path = None
    if demo_trajectory is not None:
        target = demo_out
        if target is None and saved_model_path:
            target = os.path.join(os.path.dirname(saved_model_path) or ".",
                                  "demo_trajectory.json")
        if target is not None:
            demo_trajectory_path = export_demo_trajectory(demo_trajectory, target)

    return {
        # --- task-required keys ---
        "learnable": bool(learnable),
        "steps_to_first_success": train_res["steps_to_first_success"],
        "checkpoints_curve": train_res["curve_latched"],
        "final_success_rate": final_success_rate,             # greedy (deterministic)
        "rl_witness": witness,
        "wall_clock_s": round(time.time() - t0, 1),
        # Progress-gated budget signal: True IFF the curve was still improving when the
        # budget ended (patience plateau never tripped) — the compiler emits
        # `continue_training` instead of a repair directive (LLM_RL_SYSTEMS / feedback
        # loop). False means the run CONVERGED (or was budget/wall-clock limited).
        "still_improving": _still_improving(train_res),
        # WHICH milestones the policy reaches over the eval episodes (see helper).
        "per_checkpoint_latch_rate": per_checkpoint_latch_rate,
        "saved_model_path": saved_model_path,          # SB3 .zip artifact (if save_model)
        # --- demo-readiness / critic-competence (Elias's reliability gate) ---
        # demo_ready gates BOTH consumers: (1) the demo replays this greedy rollout, (2) g4's
        # smart tiers accept this critic (critic_competent == demo_ready). See `is_demo_ready`.
        "demo_ready": bool(demo_ready),
        "greedy_sr": greedy_sr,                        # greedy (deterministic) demo-gate rate
        "stochastic_sr": stochastic_sr,                # stochastic (robustness) demo-gate rate
        "demo_trajectory": demo_trajectory,            # {seed, actions, ticks, greedy} | None
        "demo_trajectory_path": demo_trajectory_path,  # capture --actions target (or None)
        # --- best-checkpoint provenance (Elias): the eval above is the BEST snapshot; these
        #     record whether one was used, when, and how the LAST (final) policy compared ---
        "used_best_checkpoint": used_best_checkpoint,
        "best_ckpt_update": train_res.get("best_ckpt_update"),
        "best_ckpt_greedy_train": train_res.get("best_ckpt_greedy_sr"),    # eval greedy at snapshot
        "best_ckpt_stochastic_train": train_res.get("best_ckpt_stochastic_sr"),
        "last_greedy_sr": last_greedy_sr,              # final policy greedy SR (best-vs-last)
        "last_stochastic_sr": last_stochastic_sr,      # final policy stochastic SR
        "reload_parity_ok": reload_parity_ok,          # in-memory eval == from-disk reload eval
        # WHAT the policy does over the eval rounds (per-action counts + 3D axis aggregates).
        "action_histogram": action_hist,
        # --- provenance / diagnostics ---
        "title": title,
        "game_path": game_path,
        "trainer": trainer,
        "method": method,                                     # algo (ledger key)
        "chord_mode": bool(chord_mode),                       # Phase-2 MultiBinary action space
        "chord_ban_contradictions": bool(chord_mode and ban_contradictions),
        "chord_opposition_pairs": _oppose["named"],           # MEASURED near-antiparallel pairs
        "stochastic_success_rate": stochastic_success_rate,   # graded learnability
        "budget_steps": budget_steps,
        "trained_steps": train_res["global_steps"],
        "updates": train_res["updates"],
        "stopped_early": train_res["stopped_early"],
        "plateau_stopped": train_res.get("plateau_stopped"),
        "curve_return": train_res["curve_return"],
        "curve_success": train_res["curve_success"],
        "greedy_success_count": n_greedy,
        "sampled_success_count": n_sampled,
        "n_eval": n_eval,
        "witness_greedy": None if witness is None else witness.get("greedy"),
        "bridge_ok": bridge_ok,
        "bridge_result": bridge_result,
        "n_bodies": n_bodies,
        "n_actions": n_actions,
        "obs_dim": obs_dim,
        "checkpoint_keys": cp_keys,
        "throughput_sps": int(train_res["global_steps"] / max(1e-6, train_res["train_wall_s"])),
    }


# ======================================================================== #
# RL-WITNESS SECOND CERTIFICATION PATH — rescue_certify (scope extension, Elias)
#
# A game the G3 TREE solver leaves UNSOLVED but WITH PROGRESS (some milestones reached) is
# "solvable-but-hard", not broken. Train a policy (the SAME g3_prime + demo_ready + trajectory
# primitives), and if it CONVERGES to a demo-ready policy whose greedy winning rollout
# REPLAYS bit-exactly through the frozen serve host (the SAME bar as a tree witness), upgrade
# the report to a first-class CERTIFIED game carrying an RL witness (witness_source="rl"). The
# UNSOLVED diagnosis is PRESERVED (a valuable difficulty signal). This never runs inside the
# plain verify path (a PPO per game) — it is an explicit second pass (CLI verb / harden flag).
# ======================================================================== #
RESCUE_BUDGET = 500_000          # bounded RL budget for ONE rescue attempt [eng.]
RESCUE_NUM_ENVS = 8              # batched in-scene vec width (K) for a rescue train [eng.]
# SHARD COUNT (Elias, 2026-07-16: "32 cores per run"). Default 1 = today's single-process
# batch (byte-identical). M INDEPENDENT batch shards stepped concurrently give M*K logical
# envs, so ONE rescue train can saturate a 16/32-core allocation. The bounded RESCUE_BUDGET
# is intentionally NOT bumped here (that would silently change every caller); the recommended
# WALL-TIME-matched FARM presets live in notes/rl_agent/SHARDED_VEC_ENV.md — e.g. 1x8 @ -c 8
# (500k), 2x8 @ -c 16, 4x8 @ -c 32 (~1-2M budget at similar wall time). Opt in per call via
# `rescue_certify(num_shards=..., budget_steps=...)` / the `game rescue --shards/--budget` CLI.
RESCUE_NUM_SHARDS = 1            # concurrent batch shards (M); M*K logical envs [eng.]


def _bridge_replay_for_engine(engine: str, game_source: str, witness: dict) -> dict:
    """Dispatch the certificate-bridge replay to the engine's NORMAL batch executor."""
    if engine == "godot":
        return _bridge_replay_godot(game_source, witness)
    if engine == "gdscript":
        return _bridge_replay_gdscript(game_source, witness)
    return _bridge_replay(game_source, witness)


def _rescue_candidacy(report: dict):
    """Is this verify report a rescue candidate? Returns (ok, reason). ONLY an UNSOLVED
    verdict WITH PROGRESS (some declared milestone reached >0 times) qualifies — never a
    broken game (ENV_ERROR/GOAL_ERROR) and never a hopeless UNSOLVED (nothing reached: PPO
    would be wasted). An already-passed report is handled by the caller (tree-certified)."""
    if not isinstance(report, dict):
        return False, "no_report"
    if report.get("passed"):
        return False, "already_certified"
    fc = report.get("failure_class")
    if fc != "UNSOLVED":
        return False, f"not_unsolved ({fc})"
    reach = ((report.get("progress") or {}).get("reach_counts")) or {}
    if not any(int(v) > 0 for v in reach.values()):
        return False, "no_progress"      # hopeless — nothing ever reached; do not spend PPO
    return True, "unsolved_with_progress"


def rescue_certify(game_path: str, verify_report: dict | None = None, *,
                   budget_steps: int = RESCUE_BUDGET, n_eval: int = N_EVAL,
                   num_envs: int = RESCUE_NUM_ENVS, num_shards: int = RESCUE_NUM_SHARDS,
                   seed: int = 0, trainer: str = "sb3",
                   method: str = "ppo", demo_sr_min: float = DEMO_SR_MIN,
                   demo_stochastic_floor: float = DEMO_STOCHASTIC_FLOOR,
                   save_model: str | None = None, demo_out: str | None = None,
                   wall_clock_budget_s=None, log=None, g3_fn=None,
                   **train_kwargs) -> dict:
    """Second-path certification of ONE game via a trained RL witness (see banner above).

    `verify_report` is the game's tree verify report (run afresh when omitted). `g3_fn`
    overrides the trainer entry (defaults to `g3_prime`; tests inject a stub). Returns the
    (additively) updated report:

      * ALREADY tree-certified -> returned with ``witness_source="tree"`` (no PPO).
      * NOT an UNSOLVED-with-progress candidate -> ``rescue={attempted:False, reason:...}``.
      * TRAINED + CONVERGED (demo_ready) + demo trajectory REPLAYS to success -> UPGRADED:
        ``passed=True, failure_class=None, witness=<rl witness>, witness_source="rl"``,
        the preserved UNSOLVED diagnosis under ``unsolved_diagnosis``, and a ``rescue`` block
        with the rl provenance (``rl_steps``, greedy/stochastic SR, ``n_eval``, budget) the
        Atlas later reads for its composite prover-effort axis.
      * TRAINED but NO convergence / REPLAY MISMATCH / train error -> report stays UNSOLVED
        with an honest ``rescue={attempted:True, rescued:False, reason:...}`` block.
    """
    if verify_report is None:
        from harness.verify.gameverify import verify_game
        verify_report = verify_game(game_path)
    report = dict(verify_report) if isinstance(verify_report, dict) else {"passed": False}

    # (a) already certified by the tree solver -> first-class TREE witness, no PPO.
    if report.get("passed"):
        report.setdefault("witness_source", "tree")
        return report

    # (b) rescue candidacy: UNSOLVED WITH PROGRESS only.
    ok, reason = _rescue_candidacy(report)
    if not ok:
        report["rescue"] = {"attempted": False, "rescued": False, "reason": reason}
        return report

    # (c) TRAIN a policy (batched) — reuses the demo_ready + trajectory primitives.
    train = g3_fn or g3_prime
    try:
        g3 = train(game_path, budget_steps=budget_steps, n_eval=n_eval, seed=seed,
                   trainer=trainer, method=method, num_envs=num_envs,
                   num_shards=num_shards,
                   save_model=save_model, demo_out=demo_out, demo_sr_min=demo_sr_min,
                   demo_stochastic_floor=demo_stochastic_floor,
                   wall_clock_budget_s=wall_clock_budget_s, log=log, **train_kwargs)
    except Exception as exc:  # noqa: BLE001 - a training/bridge crash is an honest failure
        report["rescue"] = {"attempted": True, "rescued": False, "reason": "train_error",
                            "error": f"{type(exc).__name__}: {exc}"}
        return report

    rl_prov = {
        "rl_steps": g3.get("trained_steps"), "budget_steps": g3.get("budget_steps"),
        "greedy_sr": g3.get("greedy_sr"), "stochastic_sr": g3.get("stochastic_sr"),
        "n_eval": g3.get("n_eval"), "demo_ready": bool(g3.get("demo_ready")),
        "trainer": g3.get("trainer"), "method": g3.get("method"),
        "num_envs": num_envs, "num_shards": num_shards,   # M*K logical env provenance
        "saved_model_path": g3.get("saved_model_path"),
    }

    # (d) convergence gate: the SAME criterion as demo_ready.
    demo_traj = g3.get("demo_trajectory")
    if not g3.get("demo_ready") or demo_traj is None:
        report["rescue"] = {"attempted": True, "rescued": False,
                            "reason": "no_convergence", **rl_prov}
        return report

    # (e) REPLAY-VALIDATE the greedy demo trajectory bit-exactly through the serve host
    #     (the SAME bar as a tree witness — a deterministic replay to success).
    from harness.verify.gameverify import detect_engine
    with open(game_path, "r", encoding="utf-8") as fh:
        game_source = fh.read()
    engine = detect_engine(game_path, game_source)
    try:
        rec = _bridge_replay_for_engine(engine, game_source, demo_traj)
    except Exception as exc:  # noqa: BLE001 - a bridge crash is an honest replay failure
        report["rescue"] = {"attempted": True, "rescued": False, "reason": "replay_error",
                            "error": f"{type(exc).__name__}: {exc}", **rl_prov}
        return report
    if rec.get("result") != "success":
        report["rescue"] = {"attempted": True, "rescued": False, "reason": "replay_mismatch",
                            "replay_result": rec.get("result"), **rl_prov}
        return report

    # (f) SUCCESS -> UPGRADE to a first-class RL-certified game (witness parity with the tree
    #     witness shape: {seed, actions, ticks, checkpoints}), PRESERVING the UNSOLVED
    #     diagnosis (solvable-but-hard is a valuable signal, kept for the difficulty tuner).
    witness = {"seed": demo_traj["seed"], "actions": list(demo_traj["actions"]),
               "ticks": rec.get("ticks", demo_traj.get("ticks")),
               "checkpoints": dict(rec.get("checkpoints") or {})}
    report["unsolved_diagnosis"] = report.get("progress")   # preserved difficulty signal
    report["passed"] = True
    report["failure_class"] = None
    report["witness"] = witness
    report["witness_source"] = "rl"
    report["rescue"] = {"attempted": True, "rescued": True, "reason": "rl_certified",
                        "witness_ticks": witness["ticks"], **rl_prov}
    return report
