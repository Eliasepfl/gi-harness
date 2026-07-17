"""loader.py -- a torch-FREE, numpy-FREE, pure-stdlib reader for episode packages.

Ships INSIDE the exported dataset so a consumer can round-trip the package with only
the Python standard library -- no harness install, no torch, no numpy. A downstream
reward-model trainer wraps :class:`Episode` in whatever tensor library it likes; this
module only parses JSON and resolves frame paths.

    from harness.export.loader import EpisodeDataset
    ds = EpisodeDataset("<out>")
    train_slugs, test_slugs = ds.split_by_game(frac=0.8, seed=0)   # split BY GAME
    for ep in ds.episodes(slugs=test_slugs):
        for step in ep.steps:
            frame = ep.frame_path(step["t"])          # -> <...>/frames/t00001.png
            label = step["reward"]["total"]           # the training reward label

Cross-game generalization is THE claim, so the split is by game slug (never by
episode): a held-out game is one the reward model has never seen a single frame of.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class Episode:
    """One loaded episode package (``episode.json`` + lazily-read ``steps.jsonl`` + frames)."""

    def __init__(self, ep_dir: str):
        self.dir = Path(ep_dir)
        meta_path = self.dir / "episode.json"
        if not meta_path.is_file():
            raise FileNotFoundError(f"no episode.json in {ep_dir}")
        self.meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self._steps = None

    # -- identity / meta --------------------------------------------------------
    @property
    def slug(self) -> str:
        return self.meta.get("slug", self.dir.parent.name)

    @property
    def seed(self):
        return self.meta.get("seed")

    @property
    def dimension(self) -> str:
        return self.meta.get("dimension", "2D")

    @property
    def objective_text(self) -> str:
        return self.meta.get("objective_text", "")

    @property
    def outcome(self) -> str:
        return self.meta.get("outcome", "")

    @property
    def trajectory_kind(self) -> str:
        """How this episode's trajectory was produced: ``demo`` / ``witness`` (clean wins) or
        ``random`` / ``perturbed`` (the negatives). Legacy packages (all wins) default to
        ``demo``."""
        return self.meta.get("trajectory_kind", "demo")

    @property
    def ticks(self) -> int:
        return int(self.meta.get("ticks", 0))

    # -- steps ------------------------------------------------------------------
    @property
    def steps(self) -> list:
        """The decision-tick records (parsed from ``steps.jsonl`` on first access)."""
        if self._steps is None:
            path = self.dir / self.meta.get("paths", {}).get("steps", "steps.jsonl")
            steps = []
            if path.is_file():
                with path.open(encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            steps.append(json.loads(line))
            self._steps = steps
        return self._steps

    def iter_steps(self):
        """Yield step records one at a time (streams ``steps.jsonl``; no full parse)."""
        path = self.dir / self.meta.get("paths", {}).get("steps", "steps.jsonl")
        if not path.is_file():
            return
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    # -- frames -----------------------------------------------------------------
    @property
    def frames_dir(self) -> Path:
        return self.dir / self.meta.get("paths", {}).get("frames", "frames")

    def frame_path(self, t: int) -> str:
        """Absolute path of the PNG rendered at decision tick ``t`` (1-based)."""
        return str(self.frames_dir / f"t{int(t):05d}.png")

    def frame_paths(self) -> list:
        """All frame paths in tick order."""
        return [str(p) for p in sorted(self.frames_dir.glob("t*.png"))]

    # -- integrity --------------------------------------------------------------
    def validate(self, require_frames: bool = True) -> dict:
        """Structural self-check: ``len(steps) == n_steps == ticks``, monotone 1-based
        ticks, and (when ``require_frames``) one frame file per step. Returns a report
        dict; raises AssertionError on a violation."""
        steps = self.steps
        n_steps = int(self.meta.get("n_steps", len(steps)))
        assert len(steps) == n_steps, f"{self.slug}: len(steps) {len(steps)} != n_steps {n_steps}"
        assert len(steps) == self.ticks, f"{self.slug}: len(steps) {len(steps)} != ticks {self.ticks}"
        for i, s in enumerate(steps, start=1):
            assert s.get("t") == i, f"{self.slug}: step {i} has t={s.get('t')} (not 1-based monotone)"
        n_frames = 0
        if require_frames:
            frames = self.frame_paths()
            n_frames = len(frames)
            assert n_frames == len(steps), (
                f"{self.slug}: {n_frames} frames != {len(steps)} steps")
            for s in steps:
                fp = self.frame_path(s["t"])
                assert os.path.isfile(fp), f"{self.slug}: missing frame {fp}"
        return {"slug": self.slug, "seed": self.seed, "n_steps": len(steps),
                "n_frames": n_frames, "ticks": self.ticks, "ok": True}


class EpisodeDataset:
    """A dataset over an export root: reads ``manifest.jsonl`` (or discovers episode
    dirs) and iterates :class:`Episode` objects, with a BY-GAME train/test split."""

    def __init__(self, root: str):
        self.root = Path(root)
        self._records = self._load_manifest()

    def _load_manifest(self) -> list:
        manifest = self.root / "manifest.jsonl"
        records = []
        if manifest.is_file():
            for line in manifest.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except ValueError:
                        continue
            return records
        # No manifest -> discover <slug>/<seed>/episode.json (bounded, depth 2).
        for slug_dir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            for seed_dir in sorted(p for p in slug_dir.iterdir() if p.is_dir()):
                if (seed_dir / "episode.json").is_file():
                    records.append({
                        "slug": slug_dir.name,
                        "seed": seed_dir.name,
                        "paths": {"episode": f"{slug_dir.name}/{seed_dir.name}/episode.json"},
                    })
        return records

    def __len__(self) -> int:
        return len(self._records)

    def games(self) -> list:
        """Sorted unique game slugs present in the dataset."""
        return sorted({r.get("slug") for r in self._records if r.get("slug")})

    def _ep_dir(self, record: dict) -> Path:
        ep_rel = record.get("paths", {}).get("episode")
        if ep_rel:
            return (self.root / ep_rel).parent
        key = record.get("episode_key") or record.get("seed")
        return self.root / record["slug"] / str(key)

    def _record_kind(self, record: dict) -> str:
        """The ``trajectory_kind`` of a manifest record: taken from the manifest line when
        present (the fast path), else read once from the episode's ``episode.json`` (a package
        discovered without a manifest). Legacy packages default to ``demo``."""
        k = record.get("trajectory_kind")
        if k:
            return k
        try:
            return Episode(str(self._ep_dir(record))).trajectory_kind
        except Exception:  # noqa: BLE001 -- a missing/broken package just has no kind
            return ""

    def kinds(self) -> list:
        """Sorted unique ``trajectory_kind`` values present (demo|witness|random|perturbed)."""
        return sorted({self._record_kind(r) for r in self._records if self._record_kind(r)})

    def episodes(self, slugs=None, kinds=None):
        """Yield :class:`Episode` for every record, optionally filtered to ``slugs`` and/or
        ``kinds`` (a ``trajectory_kind`` string or an iterable of them)."""
        allow = set(slugs) if slugs is not None else None
        want = ({kinds} if isinstance(kinds, str) else set(kinds)) if kinds is not None else None
        for r in self._records:
            if allow is not None and r.get("slug") not in allow:
                continue
            if want is not None and self._record_kind(r) not in want:
                continue
            yield Episode(str(self._ep_dir(r)))

    def filter_by_kind(self, kinds, slugs=None):
        """Yield :class:`Episode` for every episode whose ``trajectory_kind`` is in ``kinds`` (a
        string or an iterable), optionally restricted to ``slugs``. The behavioral-diversity
        filter: e.g. ``filter_by_kind(("demo", "witness"))`` for the clean WINS,
        ``filter_by_kind(("random", "perturbed"))`` for the NEGATIVES. The by-GAME split
        (:meth:`split_by_game`) is orthogonal and unchanged -- compose them for a held-out
        wins/negatives view."""
        want = {kinds} if isinstance(kinds, str) else set(kinds)
        yield from self.episodes(slugs=slugs, kinds=want)

    def split_by_game(self, frac: float = 0.8, seed: int = 0):
        """Deterministic BY-GAME split: returns ``(train_slugs, test_slugs)`` -- disjoint
        sets of game slugs, so no episode of a held-out game leaks into training. THE
        cross-game generalization split the reward-model experiment evaluates on."""
        import random
        games = self.games()
        rng = random.Random(seed)
        rng.shuffle(games)
        k = int(round(len(games) * float(frac)))
        train = sorted(games[:k])
        test = sorted(games[k:])
        return train, test


def load_episode(ep_dir: str) -> Episode:
    """Load a single episode package directory (``<slug>/<seed>/``)."""
    return Episode(ep_dir)
