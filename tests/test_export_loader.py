"""Offline, torch-FREE / numpy-FREE unit tests for the episode-package loader
(harness/export/loader.py). Builds a tiny synthetic dataset by hand, then round-trips
it: Episode.validate(), frame_path(), EpisodeDataset iteration, and the BY-GAME split.
Imports ONLY the pure-stdlib loader (no engine, no numpy) so it runs anywhere.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.export.loader import Episode, EpisodeDataset, load_episode   # noqa: E402


def _write_episode(root, slug, seed, T, dim="2D", outcome="success"):
    """Hand-build a minimal package <root>/<slug>/<seed>/ with T steps and T frames."""
    ep_dir = os.path.join(root, slug, str(seed))
    frames_dir = os.path.join(ep_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    meta = {
        "schema_version": "1.0", "game_id": slug, "slug": slug, "seed": seed,
        "dimension": dim, "objective_text": f"{slug}\nCheckpoints (in order): cp0",
        "ticks": T, "n_steps": T, "n_frames": T, "outcome": outcome,
        "checkpoint_names": ["cp0"], "paths": {"steps": "steps.jsonl", "frames": "frames"},
    }
    with open(os.path.join(ep_dir, "episode.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    with open(os.path.join(ep_dir, "steps.jsonl"), "w", encoding="utf-8") as fh:
        for t in range(1, T + 1):
            fh.write(json.dumps({
                "t": t, "action": "go",
                "state": {"body": {"pos": [float(t), 0.0], "controlled": True}},
                "checkpoints": {"cp0": t >= T},
                "reward": {"shaping": 0.0, "terminal": 0.0, "total": 0.0},
                "done": t == T, "n_latched": 1 if t >= T else 0,
            }) + "\n")
        # one frame file per step
    for t in range(1, T + 1):
        open(os.path.join(frames_dir, f"t{t:05d}.png"), "wb").close()
    return ep_dir


def test_episode_round_trip_and_validate(tmp_path):
    root = str(tmp_path)
    ep_dir = _write_episode(root, "a_2d_demo_game", 0, T=4)
    ep = load_episode(ep_dir)
    assert ep.slug == "a_2d_demo_game"
    assert ep.dimension == "2D"
    assert ep.ticks == 4
    assert len(ep.steps) == 4
    assert ep.steps[0]["t"] == 1 and ep.steps[-1]["done"] is True
    # streaming iterator matches the eager list
    assert [s["t"] for s in ep.iter_steps()] == [1, 2, 3, 4]
    # frame path resolution
    assert ep.frame_path(2).endswith("frames/t00002.png")
    assert os.path.isfile(ep.frame_path(2))
    assert len(ep.frame_paths()) == 4
    # structural self-check: len(steps)==n_steps==ticks and one frame per step
    report = ep.validate(require_frames=True)
    assert report["ok"] and report["n_steps"] == 4 and report["n_frames"] == 4


def test_validate_catches_frame_mismatch(tmp_path):
    root = str(tmp_path)
    ep_dir = _write_episode(root, "a_3d_demo_game", 0, T=3, dim="3D")
    os.remove(os.path.join(ep_dir, "frames", "t00002.png"))   # drop a frame
    ep = load_episode(ep_dir)
    raised = False
    try:
        ep.validate(require_frames=True)
    except AssertionError:
        raised = True
    assert raised, "validate() must fail when a frame file is missing"


def test_dataset_split_by_game(tmp_path):
    root = str(tmp_path)
    for i in range(6):
        dim = "2D" if i % 2 == 0 else "3D"
        _write_episode(root, f"game_{i:02d}", 0, T=2 + i, dim=dim)
    # a manifest.jsonl written by hand (loader also discovers without one)
    with open(os.path.join(root, "manifest.jsonl"), "w", encoding="utf-8") as fh:
        for i in range(6):
            slug = f"game_{i:02d}"
            fh.write(json.dumps({
                "slug": slug, "seed": 0, "dim": "2D" if i % 2 == 0 else "3D",
                "outcome": "success", "ticks": 2 + i, "n_frames": 2 + i,
                "witness_source": "tree",
                "paths": {"episode": f"{slug}/0/episode.json",
                          "steps": f"{slug}/0/steps.jsonl", "frames": f"{slug}/0/frames"},
            }) + "\n")

    ds = EpisodeDataset(root)
    assert len(ds) == 6
    assert ds.games() == [f"game_{i:02d}" for i in range(6)]

    train, test = ds.split_by_game(frac=0.5, seed=0)
    assert set(train).isdisjoint(set(test))            # BY-GAME: disjoint game sets
    assert len(train) + len(test) == 6
    assert ds.split_by_game(frac=0.5, seed=0) == (train, test)   # deterministic

    # iterating held-out episodes yields only test-game episodes, all validating
    seen = set()
    for ep in ds.episodes(slugs=test):
        seen.add(ep.slug)
        ep.validate(require_frames=True)
    assert seen == set(test)


def test_dataset_discovery_without_manifest(tmp_path):
    root = str(tmp_path)
    _write_episode(root, "solo_game", 0, T=3)
    ds = EpisodeDataset(root)                            # no manifest.jsonl present
    assert ds.games() == ["solo_game"]
    eps = list(ds.episodes())
    assert len(eps) == 1 and eps[0].ticks == 3
