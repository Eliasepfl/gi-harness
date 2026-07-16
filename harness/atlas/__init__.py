"""THE ATLAS (creative direction D1) — the READ-ONLY MVP slice.

Turns the certification by-products of the harness into a *map of certified
game-space*. Every module here only READS existing artifacts (verify reports,
t=0 geometry facts) and aggregates them into deterministic behaviour descriptors;
nothing here generates, mutates, re-certifies, or otherwise touches the funnel.

Layout::

    descriptors.describe_game(game_path, verify_report, extras) -> dict
        one game's deterministic descriptor row (missing artifact -> None field).
    build.build_atlas(...) / CLI ``python -m harness.atlas.build``
        walk a list of games, emit ``atlas.jsonl`` + ``atlas.svg`` + a coverage number.
    render.render_atlas(rows, out_svg)
        the 2D map: pick the two most-discriminating axes for THIS dataset, grid
        them, shade the colonised cells, and make the empty territory obvious.
    breeding.placement(child, parent_a, parent_b, rows) / render_breeding_svg(...)
        the breeding experiment's read-only companion: classify a bred child's
        position vs its two parents on the (solver-effort, entropy) plane and
        overlay the parent-parent-child triangles on the map.
"""

from harness.atlas.breeding import placement, render_breeding_svg  # noqa: F401
from harness.atlas.descriptors import describe_game  # noqa: F401

__all__ = ["describe_game", "placement", "render_breeding_svg"]
