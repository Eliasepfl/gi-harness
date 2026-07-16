"""THE FRONTIER RING for THE ATLAS: the games that EXIST beyond the certifiable-under-
budget space (ATLAS D1, read-only).

The tick/solver budget bounds the map. Some games are ``UNSOLVED`` under that budget yet
are demonstrably PROGRESSING — the solver reaches real milestones, just not the goal
before it runs out of budget. Rendering these as an off-map ring around the certified
scatter is the honest visualization of the budget bound: here is the edge, and here are
the games sitting just beyond it.

Selection (``scan_frontier``):

  * source: existing ``gen_*.json`` funnel outputs,
  * keep verdict ``UNSOLVED`` whose game dir STILL EXISTS in the current library,
  * keep only the PROGRESSING ones (``progress.reach_counts`` has a milestone with
    count > 0); an ``UNSOLVED`` run that reached nothing is excluded,
  * per game slug, keep the LAST-known report (newest mtime) — the frontier marker is
    pinned at that game's most recent partial descriptors.

Each frontier row carries the game's PARTIAL descriptors (solver effort, dimension,
space ratio — whatever the UNSOLVED report honestly yields; there is no witness, so no
entropy) plus a ``progress`` summary and a display ``label``.

DETERMINISM — pure aggregation over existing JSON; no engine, no mutation.
"""

from __future__ import annotations

import glob
import json
import os

from harness.atlas.descriptors import describe_game, slug_of


def _last_report(gen):
    """The last attempt's report from a whole ``gen_*.json`` dict, else the dict itself if
    it already looks like a bare report. Returns ``(report|None, verdict|None)``."""
    if not isinstance(gen, dict):
        return None, None
    verdict = gen.get("verdict")
    atts = gen.get("attempts")
    if isinstance(atts, list) and atts:
        last = atts[-1]
        rep = last.get("report") if isinstance(last, dict) else None
        if isinstance(rep, dict):
            return rep, verdict
    if "layers" in gen or "progress" in gen or "witness" in gen:
        return gen, verdict or gen.get("failure_class")
    return None, verdict


def report_progress(report):
    """Normalise a report's ``progress`` block into a summary::

        {"reach_counts": {...}, "stuck_after": str|None, "milestones_total": int,
         "milestones_reached": int, "best_milestone": str|None, "best_count": int,
         "reached_fraction": float}

    ``reached_fraction`` = milestones_reached / milestones_total (0 when no milestones).
    Returns ``None`` when there is no usable ``reach_counts``."""
    prog = (report or {}).get("progress") if isinstance(report, dict) else None
    if not isinstance(prog, dict):
        return None
    rc = prog.get("reach_counts")
    if not isinstance(rc, dict) or not rc:
        return None
    counts = {k: (v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0)
              for k, v in rc.items()}
    total = len(counts)
    reached = sum(1 for v in counts.values() if v > 0)
    best_milestone, best_count = None, 0
    for k, v in counts.items():
        if v > best_count:
            best_milestone, best_count = k, v
    return {
        "reach_counts": counts,
        "stuck_after": prog.get("stuck_after"),
        "milestones_total": total,
        "milestones_reached": reached,
        "best_milestone": best_milestone,
        "best_count": int(best_count),
        "reached_fraction": round(reached / total, 4) if total else 0.0,
    }


def is_progressing(progress):
    """True iff the game reached at least one milestone (a non-zero ``reach_count``)."""
    return bool(progress) and progress.get("milestones_reached", 0) > 0


def _episodes_total(report):
    epi = ((((report or {}).get("layers") or {}).get("G3_solve") or {})
           .get("checks") or {}).get("episodes")
    if isinstance(epi, dict) and isinstance(epi.get("run"), (int, float)):
        return int(epi["run"])
    return None


def frontier_label(slug, progress, report=None):
    """A compact marker label: ``best_count/episodes -> stuck_after`` (e.g.
    ``2466/3360 -> spire_field_cleared``). Falls back gracefully when totals are absent."""
    denom = _episodes_total(report) if report is not None else None
    best = progress.get("best_count", 0)
    milestone = progress.get("stuck_after") or progress.get("best_milestone") or "?"
    if denom:
        return f"{best}/{denom} -> {milestone}"
    reached = progress.get("milestones_reached", 0)
    total = progress.get("milestones_total", 0)
    return f"{reached}/{total} milestones -> {milestone}"


def library_slugs(games_root):
    """The set of game slugs that currently EXIST as dirs under ``games_root`` (bounded,
    single-level listdir — no recursive scan of the shared filesystem)."""
    slugs = set()
    try:
        for name in os.listdir(games_root):
            if name == "harden":
                continue
            if os.path.isdir(os.path.join(games_root, name)):
                slugs.add(name)
    except OSError:
        pass
    return slugs


def _gen_slug(gen, path):
    gp = gen.get("game_path") if isinstance(gen, dict) else None
    if gp:
        parts = str(gp).split("/")
        if "games" in parts:
            i = parts.index("games")
            if i + 1 < len(parts):
                return parts[i + 1]
        return slug_of(gp)
    return None


def scan_frontier(reports_globs, existing_slugs=None, games_root=None):
    """Scan ``gen_*.json`` outputs for the OVER-BUDGET FRONTIER: ``UNSOLVED`` +
    progressing games whose dir still exists in the current library.

    ``existing_slugs`` (or, if omitted, the dirs under ``games_root``) gates membership so
    an archived/removed game never appears. Per slug the NEWEST report wins (last-known
    partial descriptors). Returns a list of frontier rows sorted by slug::

        {"slug", "game_path", "descriptors": {<partial>}, "progress": {...},
         "label": str, "report_source": path, "kind": "frontier"}
    """
    if existing_slugs is None:
        existing_slugs = library_slugs(games_root) if games_root else None
    globs = [reports_globs] if isinstance(reports_globs, str) else list(reports_globs or [])
    best = {}   # slug -> (mtime, row)
    for pat in globs:
        for path in sorted(glob.glob(pat)):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    gen = json.load(fh)
            except Exception:
                continue
            report, verdict = _last_report(gen)
            if verdict != "UNSOLVED" or report is None:
                continue
            slug = _gen_slug(gen, path)
            if not slug:
                continue
            if existing_slugs is not None and slug not in existing_slugs:
                continue
            progress = report_progress(report)
            if not is_progressing(progress):
                continue
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0.0
            prev = best.get(slug)
            if prev is not None and prev[0] >= mtime:
                continue
            gp = gen.get("game_path") or slug
            desc = describe_game(gp, verify_report=report)
            row = {"slug": slug, "game_path": gp, "descriptors": desc,
                   "progress": progress,
                   "label": frontier_label(slug, progress, report),
                   "report_source": path, "kind": "frontier"}
            best[slug] = (mtime, row)
    return [best[s][1] for s in sorted(best)]
