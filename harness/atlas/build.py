"""Build THE ATLAS: walk a list of certified games, aggregate their descriptors, and
emit ``atlas.jsonl`` + ``atlas.svg`` + a printed COVERAGE number (ATLAS D1, read-only).

Pure aggregation over EXISTING artifacts:

  * ``--reports <glob>`` indexes existing ``gen_*.json`` verify outputs and matches the
    best (COMPLETED, newest) report to each game by slug — ZERO engine cost.
  * ``--facts`` fetches each game's t=0 geometry facts via the serve host's cheap
    ``run_check`` (body counts + a fresh space-utilisation ratio). ENGINE — in-image only.
  * ``--verify`` re-runs the full funnel for a game that has no indexed report. ENGINE —
    in-image, bounded (each ``verify_game`` is sandboxed with its own timeout).

Nothing here generates, mutates, or re-certifies existing games; the funnel is untouched.

CLI::

    python -m harness.atlas.build --games '<glob-or-list>' --out runs/atlas/ [--reports ...] [--facts] [--verify]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

from harness.atlas.descriptors import DESCRIPTOR_KEYS, describe_game, slug_of
from harness.atlas.ghosts import build_ghosts
from harness.atlas.frontier import scan_frontier
from harness.atlas.render import render_atlas

_ATTEMPT_STEMS = {"a1", "a2", "a3", "a4", "a5"}


# ======================================================================== #
# Resolving the game list
# ======================================================================== #
def _slug_gd_in_dir(d):
    """The certified game file in a game dir: ``<dir>/<basename>.gd`` (the slug-named
    module), else the single non-attempt ``.gd`` if that convention does not hold."""
    base = os.path.basename(d.rstrip("/"))
    cand = os.path.join(d, base + ".gd")
    if os.path.isfile(cand):
        return cand
    gds = [g for g in sorted(glob.glob(os.path.join(d, "*.gd")))
           if os.path.splitext(os.path.basename(g))[0] not in _ATTEMPT_STEMS]
    return gds[0] if len(gds) == 1 else (gds[0] if gds else None)


def resolve_games(patterns):
    """Expand ``patterns`` (globs, dirs, ``.gd`` files, or a ``.txt``/``.list`` manifest of
    paths) into a sorted, de-duplicated list of game ``.gd`` paths. ``harden/`` sandbox
    games are skipped."""
    out = []
    for pat in patterns:
        pat = str(pat)
        if pat.endswith((".txt", ".list")) and os.path.isfile(pat):
            with open(pat, "r", encoding="utf-8") as fh:
                out += resolve_games([ln.strip() for ln in fh if ln.strip()
                                      and not ln.lstrip().startswith("#")])
            continue
        if os.path.isdir(pat):
            gd = _slug_gd_in_dir(pat)
            if gd:
                out.append(gd)
            continue
        if any(ch in pat for ch in "*?[") or not os.path.exists(pat):
            for m in sorted(glob.glob(pat)):
                if os.path.isdir(m):
                    gd = _slug_gd_in_dir(m)
                    if gd:
                        out.append(gd)
                elif m.endswith(".gd"):
                    out.append(m)
            continue
        if pat.endswith(".gd"):
            out.append(pat)
    # De-dup (preserve absolute identity), skip harden sandboxes, sort for determinism.
    seen = set()
    games = []
    for g in out:
        ap = os.path.abspath(g)
        if ap in seen or os.sep + "harden" + os.sep in ap + os.sep:
            continue
        seen.add(ap)
        games.append(g)
    return sorted(games, key=slug_of)


# ======================================================================== #
# Report index (existing gen_*.json)
# ======================================================================== #
def _slug_of_report(gen):
    gp = gen.get("game_path") or ""
    parts = str(gp).split("/")
    if "games" in parts:
        i = parts.index("games")
        if i + 1 < len(parts):
            return parts[i + 1]
    return os.path.splitext(os.path.basename(str(gp)))[0] or None


def index_reports(patterns):
    """Index existing ``gen_*.json`` files by slug -> the BEST report (COMPLETED first,
    then most recent). Returns ``{slug: {"gen": <dict>, "path": str, "verdict": str}}``."""
    idx = {}
    for pat in patterns or []:
        for path in sorted(glob.glob(pat)):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    gen = json.load(fh)
            except Exception:
                continue
            slug = _slug_of_report(gen)
            if not slug:
                continue
            verdict = gen.get("verdict")
            rank = (1 if verdict == "COMPLETED" else 0, os.path.getmtime(path))
            prev = idx.get(slug)
            if prev is None or rank > prev["_rank"]:
                idx[slug] = {"gen": gen, "path": path, "verdict": verdict, "_rank": rank}
    return idx


# ======================================================================== #
# Engine artifacts (in-image only) — facts + optional fresh verify
# ======================================================================== #
def fetch_facts(game_path, source=None):
    """Fetch a game's t=0 geometry facts via the serve host's ``run_check`` (cheap, no
    solve). ENGINE — in-image only. Returns the facts dict or ``None`` on any failure
    (missing engine, parse error, crash) — never raises."""
    try:
        if source is None:
            with open(game_path, "r", encoding="utf-8") as fh:
                source = fh.read()
        from harness.verify.gameverify import detect_engine
        engine = detect_engine(game_path, source)
        if engine == "gdscript":
            from harness.verify.gd_exec import GdExecutor
            ex = GdExecutor()
        elif engine == "js":
            from harness.verify.executors import JsExecutor
            ex = JsExecutor()
        elif engine == "godot":
            from harness.verify.godot_exec import GodotExecutor
            ex = GodotExecutor()
        else:
            return None
        try:
            return ex.run_check(source)
        finally:
            close = getattr(ex, "close", None)
            if callable(close):
                close()
    except Exception:
        return None


def fresh_verify(game_path):
    """Run the full funnel for a game with no indexed report. ENGINE — in-image, bounded
    (``verify_game`` is sandboxed with its own timeout). Returns the report or ``None``."""
    try:
        from harness.verify.gameverify import verify_game
        return verify_game(game_path)
    except Exception:
        return None


# ======================================================================== #
# The build
# ======================================================================== #
def _games_root_of(game_paths):
    """The ``scenes/games`` root shared by resolved game ``.gd`` paths (the parent of each
    game's slug dir). Used to gate the frontier ring to games that still exist."""
    for gp in game_paths:
        root = os.path.dirname(os.path.dirname(os.path.abspath(gp)))
        if os.path.isdir(root):
            return root
    return None


def build_atlas(games, out_dir, *, reports_glob=None, do_facts=False, do_verify=False,
                n_bins=6, ghost_globs=None, frontier_reports=None, games_root=None,
                log=None):
    """Aggregate descriptors for ``games`` into ``out_dir/atlas.jsonl`` + ``atlas.svg``.

    Returns ``(rows, summary)`` where ``summary`` carries the coverage math + chosen axes.
    ``do_facts`` / ``do_verify`` gate the (in-image) engine calls; with both off the build
    is pure over the report index + game sources (safe on a login node).

    ``ghost_globs`` overlays human-authored REFERENCE games (geometry-only descriptors from
    their ``.tscn`` sources). ``frontier_reports`` (a glob of ``gen_*.json``) overlays the
    OVER-BUDGET FRONTIER: UNSOLVED-but-progressing games whose dirs still exist under
    ``games_root`` (defaults to the resolved library root). Both are OVERLAYS — neither
    affects the coverage math, which stays over the certified ``games`` only."""
    log = log or (lambda *a: None)
    os.makedirs(out_dir, exist_ok=True)
    game_paths = resolve_games(games)
    report_idx = index_reports([reports_glob] if isinstance(reports_glob, str)
                               else (reports_glob or []))
    cache_dir = os.path.join(out_dir, "artifacts")
    os.makedirs(cache_dir, exist_ok=True)

    rows = []
    for gp in game_paths:
        slug = slug_of(gp)
        prov = {"game_path": os.path.abspath(gp), "built_at": None,
                "report_source": None, "report_verdict": None, "facts_source": None}

        # --- report ---
        # Prefer an indexed COMPLETED report (zero engine cost). A missing OR stale
        # (non-COMPLETED) indexed report triggers a fresh in-image verify when --verify
        # is set — every game dir under scenes/games/ IS certified, so a stale ENV/VERIFY
        # error from an earlier attempt should be refreshed against the CURRENT source.
        report_arg = None
        hit = report_idx.get(slug)
        if hit is not None:
            report_arg = hit["gen"]
            prov["report_source"] = hit["path"]
            prov["report_verdict"] = hit["verdict"]
        stale = hit is None or (hit["verdict"] != "COMPLETED")
        if do_verify and stale:
            log(f"  [verify] {slug} (indexed={None if hit is None else hit['verdict']}) ...")
            fresh = fresh_verify(gp)
            # Use the fresh report only if we had nothing, or the fresh one certifies
            # (never downgrade a game on a flaky re-run).
            if fresh is not None and (hit is None or fresh.get("passed")):
                report_arg = fresh
                prov["report_source"] = "fresh_verify"
                prov["report_verdict"] = "COMPLETED" if fresh.get("passed") else \
                    fresh.get("failure_class")
                try:
                    with open(os.path.join(cache_dir, f"{slug}.report.json"), "w",
                              encoding="utf-8") as fh:
                        json.dump(fresh, fh)
                except OSError:
                    pass

        # --- facts (geometry) ---
        facts = None
        if do_facts:
            facts = fetch_facts(gp)
            if facts is not None:
                prov["facts_source"] = "run_check"
                try:
                    with open(os.path.join(cache_dir, f"{slug}.facts.json"), "w",
                              encoding="utf-8") as fh:
                        json.dump(facts, fh)
                except OSError:
                    pass

        desc = describe_game(gp, verify_report=report_arg, extras={"facts": facts})
        prov["built_at"] = int(time.time())
        rows.append({"slug": slug, "game_path": os.path.abspath(gp),
                     "descriptors": desc, "provenance": prov})
        n_present = sum(1 for k in DESCRIPTOR_KEYS if desc.get(k) is not None)
        log(f"  {slug:44s} descriptors={n_present}/{len(DESCRIPTOR_KEYS)} "
            f"report={prov['report_verdict']} facts={prov['facts_source']}")

    # --- overlays: GHOST references + OVER-BUDGET FRONTIER (do not affect coverage) ---
    ghost_rows = build_ghosts(ghost_globs) if ghost_globs else []
    for g in ghost_rows:
        log(f"  [ghost]    {g['slug']:44s} "
            f"dim={g['descriptors'].get('dimension')} "
            f"nodes={g['descriptors'].get('n_nodes')} "
            f"bodies={g['descriptors'].get('n_bodies')} "
            f"scenes={g['descriptors'].get('n_scenes')}")
    frontier_rows = []
    if frontier_reports:
        root = games_root or _games_root_of(game_paths)
        frontier_rows = scan_frontier(frontier_reports, games_root=root)
        for f in frontier_rows:
            log(f"  [frontier] {f['slug']:44s} {f['label']}")

    # A library game that lands in the frontier ring is represented THERE (unsolved,
    # over budget), not as a certified point — each game appears in exactly one class.
    frontier_slugs = {f["slug"] for f in frontier_rows}
    if frontier_slugs:
        before = len(rows)
        rows = [r for r in rows if r["slug"] not in frontier_slugs]
        if before != len(rows):
            log(f"  (moved {before - len(rows)} unsolved library game(s) to the frontier ring)")

    # --- emit atlas.jsonl (certified rows + tagged overlays) ---
    for row in rows:
        row.setdefault("kind", "certified")
    jsonl_path = os.path.join(out_dir, "atlas.jsonl")
    with open(jsonl_path, "w", encoding="utf-8") as fh:
        for row in rows + ghost_rows + frontier_rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    # --- render atlas.svg + coverage (coverage over certified rows only) ---
    svg_path = os.path.join(out_dir, "atlas.svg")
    summary = render_atlas(rows, svg_path, n_bins=n_bins,
                           ghosts=ghost_rows, frontier=frontier_rows)
    # persist a machine-readable summary next to the artifacts
    with open(os.path.join(out_dir, "atlas.summary.json"), "w", encoding="utf-8") as fh:
        json.dump({k: v for k, v in summary.items() if k != "svg"}, fh, indent=2,
                  sort_keys=True)
    return rows, summary


def load_rows(jsonl_path):
    """Read atlas rows back from a jsonl file (round-trip helper for tests/re-render)."""
    rows = []
    with open(jsonl_path, "r", encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))
    return rows


# ======================================================================== #
# CLI
# ======================================================================== #
def _print_summary(rows, summary):
    x, y = summary["axes"]
    print("=" * 70)
    print(f"ATLAS built over {len(rows)} games "
          f"({summary['n_placed']} placed, {summary['n_unplaced']} off-map)")
    print(f"axes: X = {x}   Y = {y}   size = {summary['size_axis']}")
    print(f"COVERAGE: {summary['coverage'] * 100:.1f}%  "
          f"({summary['n_colonized']}/{summary['n_cells']} cells colonised)")
    if summary.get("n_ghosts") or summary.get("n_frontier"):
        print(f"overlays: {summary.get('n_ghosts', 0)} ghost references, "
              f"{summary.get('n_frontier', 0)} over-budget frontier "
              f"(neither counted toward coverage)")
    print("emptiest frontiers (candidate generation targets):")
    seen = set()
    shown = 0
    for cell in summary["empty_cells"]:
        if cell["depth"] < 1 or cell["brief"] in seen:
            continue
        seen.add(cell["brief"])
        print(f"  - {cell['brief']}  (x∈{cell['x_range']}, y∈{cell['y_range']})")
        shown += 1
        if shown >= 3:
            break
    print("=" * 70)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m harness.atlas.build",
        description="Build the certified-game-space atlas (read-only aggregation).")
    ap.add_argument("--games", nargs="+", required=True,
                    help="game .gd paths, game dirs, globs, or a .txt/.list manifest")
    ap.add_argument("--out", required=True, help="output dir (atlas.jsonl + atlas.svg)")
    ap.add_argument("--reports", nargs="*", default=None,
                    help="glob(s) of existing gen_*.json to index by slug")
    ap.add_argument("--facts", action="store_true",
                    help="fetch t=0 geometry facts via run_check (ENGINE; in-image only)")
    ap.add_argument("--verify", action="store_true",
                    help="fresh-verify games with no indexed report (ENGINE; in-image only)")
    ap.add_argument("--bins", type=int, default=6, help="grid bins per axis (default 6)")
    ap.add_argument("--ghosts", nargs="*", default=None,
                    help="dirs/globs of human-authored REFERENCE game dirs (geometry-only "
                         "overlay from their .tscn sources; never affects coverage)")
    ap.add_argument("--frontier", nargs="*", default=None,
                    help="glob(s) of gen_*.json to scan for the OVER-BUDGET FRONTIER "
                         "(UNSOLVED-but-progressing games still present in the library)")
    ap.add_argument("--games-root", default=None,
                    help="games root dir gating the frontier (default: resolved library root)")
    args = ap.parse_args(argv)

    rows, summary = build_atlas(
        args.games, args.out, reports_glob=args.reports, do_facts=args.facts,
        do_verify=args.verify, n_bins=args.bins, ghost_globs=args.ghosts,
        frontier_reports=args.frontier, games_root=args.games_root,
        log=lambda *a: print(*a))
    _print_summary(rows, summary)
    print(f"wrote {os.path.join(args.out, 'atlas.jsonl')}")
    print(f"wrote {os.path.join(args.out, 'atlas.svg')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
