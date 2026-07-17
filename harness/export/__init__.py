"""harness.export -- THE EXPORTER (General Intuition challenge, bullet 3).

Turns a certified game + its winning trajectory into an EPISODE PACKAGE that
bridges CODE-DEFINED TRUTH (the programmatic state + reward the harness certifies
against) and a PIXEL CHANNEL (the in-engine rendered frame at each tick). The
package is the training substrate for a reward model ``R(frame_t, objective_text)
-> reward_t`` whose encoder is the transferable "intuition" (see the exported
README.md and :mod:`harness.export.episode`).

Public surface:
  * :func:`~harness.export.episode.export_episode` -- export ONE (game, witness)
    pair to ``<out>/<slug>/<seed>/`` (episode.json + steps.jsonl + frames/*.png).
  * :func:`~harness.export.loader.load_episode` / :class:`~harness.export.loader.Episode`
    / :class:`~harness.export.loader.EpisodeDataset` -- a torch-FREE, numpy-FREE,
    pure-stdlib reader (round-trips the package; the dataset splits BY GAME).

The reward labels are recomputed per tick through
:func:`harness.rl.env.step_reward` (imported, never reimplemented) so a label in
``steps.jsonl`` is byte-identical to the RL training signal.
"""

from __future__ import annotations

from harness.export.episode import (
    SCHEMA_VERSION,
    TRAJECTORY_KINDS,
    export_episode,
)
from harness.export.loader import Episode, EpisodeDataset, load_episode
from harness.export.rollouts import (
    export_perturbations,
    export_random_rollouts,
    perturb_actions,
    random_actions,
)

__all__ = [
    "SCHEMA_VERSION",
    "TRAJECTORY_KINDS",
    "export_episode",
    "export_random_rollouts",
    "export_perturbations",
    "random_actions",
    "perturb_actions",
    "Episode",
    "EpisodeDataset",
    "load_episode",
]
