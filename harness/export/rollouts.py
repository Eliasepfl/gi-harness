"""rollouts.py -- NEGATIVE and behaviorally-diverse trajectory sources for THE EXPORTER.

A reward model trained only on winning demos learns that *everything is progress*. The
failure/timeout episodes are free, perfectly-labelled NEGATIVES. This module feeds two extra
trajectory sources into the SAME episode-package machinery (:func:`harness.export.episode.
_write_package`, whose reward labels come from the SAME ``env.step_reward`` import), so a losing
frame's label is training truth exactly like a winning frame's:

  * RANDOM ROLLOUTS (:func:`export_random_rollouts`) -- ``N`` seeded random-policy episodes,
    deterministic from their seed, rolled through the batch executor ``gd_exec.run_batch`` in ONE
    process. Whatever outcome each reaches (failure, timeout, the rare lucky win) becomes an
    honest episode package. On a STAKES game a random policy mostly fails fast -- those
    fast-failure frames ARE the negative class, which is the point.

  * PERTURBATIONS (:func:`export_perturbations`) -- the winning witness with ``K`` variants, each
    injecting a few seeded action corruptions (swap / drop / replace at random ticks). These are
    NEAR-MISSES: they look almost like a winning trajectory and then are not -- the most
    informative negatives for a reward model (they teach the boundary, not just the extremes).

The two policy-generating helpers (:func:`random_actions`, :func:`perturb_actions`) are PURE
and deterministic (stdlib ``random`` seeded), so they unit-test offline with no engine. Only the
``export_*`` drivers touch Godot (via ``GdExecutor``), which is why they are gdscript-only.
"""

from __future__ import annotations

import random
from pathlib import Path

from harness.export.episode import _trail_from_rec, _write_package


# --------------------------------------------------------------------------- #
# Pure, deterministic policy generators (offline-testable; no engine)
# --------------------------------------------------------------------------- #
def random_actions(verbs: list, seed: int, horizon: int) -> list:
    """A seeded random-policy action sequence: ``horizon`` verbs drawn uniformly (with
    replacement) from the game's declared ``verbs``. Deterministic in ``seed`` -- the SAME
    seed always yields the SAME sequence -- so the rollout it drives is reproducible. Mirrors
    the default Discrete (single-verb-per-tick) action space the RL env uses."""
    if not verbs:
        raise ValueError("random_actions: the game declares no action verbs")
    rng = random.Random(int(seed))
    return [rng.choice(verbs) for _ in range(int(horizon))]


def perturb_actions(base_actions: list, verbs: list, seed: int,
                    n_corruptions: int) -> tuple:
    """Inject ``n_corruptions`` seeded corruptions into the winning witness ``base_actions`` and
    return ``(perturbed_actions, ops)``. Each corruption is one of, chosen uniformly:

      * ``swap``    -- exchange the verbs at two distinct ticks;
      * ``drop``    -- delete the verb at one tick (the plan gets shorter / de-synced);
      * ``replace`` -- overwrite one tick with a DIFFERENT declared verb.

    Deterministic in ``seed``. ``ops`` is the applied-corruption log (op / position / verbs) for
    honest provenance in ``episode.json``. Degenerate cases are handled: ``swap``/``drop`` on a
    length < 2 plan fall back to ``replace`` so a corruption always lands (the result differs from
    the base). ``replace`` needs >= 2 declared verbs to guarantee a change; with a single verb it
    is a no-op-safe fallback to ``drop``."""
    acts = list(base_actions)
    ops: list = []
    rng = random.Random(int(seed))
    multi_verb = len(set(verbs)) >= 2

    def do_replace(pos: int) -> dict:
        old = acts[pos]
        choices = [v for v in verbs if v != old] or list(verbs)
        new = rng.choice(choices)
        acts[pos] = new
        return {"op": "replace", "pos": pos, "from": old, "to": new}

    def do_swap(i: int, j: int) -> dict:
        acts[i], acts[j] = acts[j], acts[i]
        return {"op": "swap", "pos": [i, j], "verbs": [acts[j], acts[i]]}

    def do_drop(pos: int) -> dict:
        removed = acts.pop(pos)
        return {"op": "drop", "pos": pos, "verb": removed}

    for _ in range(int(n_corruptions)):
        kinds = ["swap", "drop", "replace"]
        if len(acts) < 2:
            kinds = ["replace"] if acts else []
        if not kinds:
            break
        kind = rng.choice(kinds)
        if kind == "replace" and not multi_verb and len(acts) >= 2:
            kind = "drop"                      # a 1-verb game can't "replace"-differ; drop instead
        if kind == "swap":
            i, j = rng.sample(range(len(acts)), 2)
            ops.append(do_swap(i, j))
        elif kind == "drop":
            ops.append(do_drop(rng.randrange(len(acts))))
        else:
            ops.append(do_replace(rng.randrange(len(acts))))
    return acts, ops


# --------------------------------------------------------------------------- #
# Engine plumbing (gdscript serve executor; reuse run_batch)
# --------------------------------------------------------------------------- #
def _require_gdscript(engine: str, mode: str) -> None:
    if engine != "gdscript":
        raise ValueError(
            f"{mode} require the gdscript serve executor (gd_exec.run_batch); "
            f"game engine is {engine!r}. Export a .gd GameAPI game.")


# --------------------------------------------------------------------------- #
# Random rollouts -- N seeded random-policy negatives (default state-only)
# --------------------------------------------------------------------------- #
def export_random_rollouts(game_path: str, out_dir: str, *, n: int, horizon: int,
                           seed_base: int = 0, render_frames: bool = False,
                           follow: bool | None = None, width: int = 960,
                           height: int = 540, fps: int = 20,
                           cam_dist: float | None = None,
                           views: list | None = None) -> list:
    """Export ``n`` seeded random-policy rollouts of the game to
    ``<out>/<slug>/random-<seed>/`` each. The world seeds are ``seed_base .. seed_base+n-1``
    (each seed drives BOTH the world and the random policy, so the rollout is reproducible).

    ONE ``run_batch`` rolls all ``n`` episodes (frames_every=1) for the code-state trails;
    ``render_frames`` (default OFF -- these are bulk negatives) additionally renders the pixel
    channel per episode. Whatever outcome each rollout reaches is recorded honestly. Returns the
    manifest-ready records; a degenerate rollout (a build-time failure with < 1 tick) is skipped,
    not written."""
    import os
    game_path = os.path.abspath(game_path)
    source = Path(game_path).read_text(encoding="utf-8")
    from harness.verify.gameverify import detect_engine
    engine = detect_engine(game_path, source)
    _require_gdscript(engine, "random rollouts")

    from harness.verify.gd_exec import GdExecutor
    seeds = [int(seed_base) + i for i in range(int(n))]
    gd = GdExecutor()
    try:
        verbs = gd.declared_verbs(source)
        if not verbs:
            raise ValueError(f"{Path(game_path).parent.name}: game declares no action "
                             f"verbs -- cannot roll a random policy")
        episodes = [{"seed": s, "actions": random_actions(verbs, s, horizon)} for s in seeds]
        recs = gd.run_batch(source, episodes, int(horizon), frames_every=1)
    finally:
        gd.close()

    records = []
    for s, ep, rec in zip(seeds, episodes, recs):
        trail = _trail_from_rec(rec, engine, source)
        try:
            records.append(_write_package(
                game_path, out_dir, source, engine, ep["actions"], s, trail,
                trajectory_kind="random", witness_source="random", witness_path=None,
                episode_key=f"random-{s}", render_frames=render_frames, follow=follow,
                width=width, height=height, fps=fps, cam_dist=cam_dist, views=views))
        except ValueError as exc:
            # A degenerate rollout (build-time failure, < 1 decision tick) carries no signal.
            print(f"  [skip] random-{s}: {exc}")
    return records


# --------------------------------------------------------------------------- #
# Perturbations -- K near-miss negatives from the winning witness (default framed)
# --------------------------------------------------------------------------- #
def export_perturbations(game_path: str, out_dir: str, *, witness_path: str, k: int,
                         n_corruptions: int = 2, seed_base: int = 0,
                         render_frames: bool = True, follow: bool | None = None,
                         width: int = 960, height: int = 540, fps: int = 20,
                         cam_dist: float | None = None,
                         views: list | None = None) -> list:
    """Export ``k`` NEAR-MISS negatives to ``<out>/<slug>/perturbed-<base_seed>-<i>/`` each, by
    taking the winning witness at ``witness_path`` and injecting ``n_corruptions`` seeded action
    corruptions per variant (:func:`perturb_actions`). Every variant replays at the witness's OWN
    world seed (so it is a corruption of the same solve); variant ``i`` corrupts with perturb-seed
    ``seed_base + i``.

    Frames are rendered by DEFAULT (``render_frames=True``) -- perturbed near-misses are few and
    most valuable as pixels. The state trails are batched through ONE ``run_batch``; the per-episode
    pixel capture (when framed) happens in :func:`_write_package`. Returns manifest-ready records."""
    import os
    game_path = os.path.abspath(game_path)
    source = Path(game_path).read_text(encoding="utf-8")
    from harness.verify.gameverify import detect_engine
    engine = detect_engine(game_path, source)
    _require_gdscript(engine, "perturbations")

    from harness.cli import _read_witness_json
    doc = _read_witness_json(witness_path)
    if not doc:
        raise ValueError(f"unusable perturb witness (want a JSON object with a non-empty "
                         f"'actions' list): {witness_path}")
    base_actions = doc["actions"]
    base_seed = int(doc.get("seed", 0))

    from harness.verify.gd_exec import GdExecutor
    gd = GdExecutor()
    try:
        verbs = gd.declared_verbs(source)
        variants = []                          # (episode_key, seed, actions, ops, perturb_seed)
        episodes = []
        for i in range(int(k)):
            pseed = int(seed_base) + i
            pert, ops = perturb_actions(base_actions, verbs, pseed, n_corruptions)
            if not pert:                       # everything dropped -> nothing to replay
                print(f"  [skip] perturbed-{base_seed}-{i}: corruptions emptied the plan")
                continue
            variants.append((f"perturbed-{base_seed}-{i}", base_seed, pert, ops, pseed))
            episodes.append({"seed": base_seed, "actions": pert})
        max_ticks = max((len(e["actions"]) for e in episodes), default=1) + 8
        recs = gd.run_batch(source, episodes, max_ticks, frames_every=1) if episodes else []
    finally:
        gd.close()

    records = []
    for (key, seed, pert, ops, pseed), rec in zip(variants, recs):
        trail = _trail_from_rec(rec, engine, source)
        extra = {"perturbation": {"base_witness": str(witness_path), "base_seed": base_seed,
                                  "n_corruptions": int(n_corruptions), "perturb_seed": pseed,
                                  "ops": ops}}
        try:
            records.append(_write_package(
                game_path, out_dir, source, engine, pert, seed, trail,
                trajectory_kind="perturbed", witness_source="perturbed",
                witness_path=str(witness_path), episode_key=key,
                render_frames=render_frames, follow=follow, width=width, height=height,
                fps=fps, cam_dist=cam_dist, views=views, extra_meta=extra))
        except ValueError as exc:
            print(f"  [skip] {key}: {exc}")
    return records
