#!/usr/bin/env python3
"""validate_export.py -- end-to-end validation of an exported episode dataset (WINS + NEGATIVES).

Reads <out>/manifest.jsonl, and for EACH episode asserts the exporter's contract:

  1. STRUCTURE   len(steps) == n_steps == ticks, 1-based monotone ticks, and -- WHEREVER frames
                 exist -- exactly one real PNG per step (state-only negatives skip the pixel check).
  2. REWARD      a success episode's total return > every strict prefix (bounded shaping, dominant
                 terminal), done True only on the terminal tick, and the stored per-tick ``total``
                 equals a re-computation through env.step_reward (holds for wins AND negatives).
  3. T=0 BUILD   episode.json:build_state equals a FRESH 0-action deterministic replay of the
                 game (the tick-0 state IS the game's build) -- for every trajectory_kind.
  4. ROUND-TRIP  the torch-free loader re-reads the package (state-only aware).

Then, at the DATASET level (the whole point of exporting negatives):

  5. DIVERSITY   the outcome distribution is NOT all-success (there ARE failure/timeout negatives).
  6. SEPARATION  every failure/timeout episode's return is STRICTLY below every success return.
  7. KIND FILTER loader.filter_by_kind round-trips wins (demo|witness) vs negatives (random|
                 perturbed), and every filtered episode re-validates.

Prints a per-episode table (kind / outcome / ticks / frames / return / bytes) and exits non-zero
on any failure. Run INSIDE the capture container (needs harness + Godot for the fresh-build
re-replay).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # repo root

from harness.export import episode as X             # noqa: E402
from harness.export.loader import EpisodeDataset     # noqa: E402
from harness.rl import env as E                      # noqa: E402
from harness.verify.gameverify import detect_engine  # noqa: E402


def _dir_bytes(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _fresh_build(game_file):
    """Tick-0 entities from a FRESH 0-action deterministic replay (the game's build)."""
    src = open(game_file, encoding="utf-8").read()
    engine = detect_engine(game_file, src)
    trail = X._state_trail(src, engine, [], 0, max_ticks=1)
    return X._entities_of(trail["frames"][0])


def validate_episode(ep):
    slug = ep.slug
    has_frames = int(ep.meta.get("n_frames", 0)) > 0
    # 1. structure; and WHEREVER frames exist, one real PNG per step (state-only skips pixels)
    rep = ep.validate(require_frames=has_frames)
    if has_frames:
        from PIL import Image
        for s in ep.steps:
            with Image.open(ep.frame_path(s["t"])) as im:
                assert im.width > 0 and im.height > 0
    # 2. reward: telescoping dominance + done placement + label re-computation
    steps = ep.steps
    totals = [s["reward"]["total"] for s in steps]
    full = sum(totals)
    prefixes = [sum(totals[:k]) for k in range(1, len(totals))]
    n_cp = ep.meta["reward_scheme"]["n_checkpoints"]
    horizon = ep.meta["horizon"]
    if ep.outcome == "success":
        assert all(full > p for p in prefixes), f"{slug}: return {full} not > every prefix"
    assert [i for i, s in enumerate(steps) if s["done"]] in ([len(steps) - 1], []), \
        f"{slug}: done flag not solely on the terminal tick"
    latch = ep.meta["checkpoints_latch"]
    for s in steps:
        # Independently reconstruct c_before/c_after from the episode's latch-tick map.
        c_before = X._latched_count(latch, s["t"] - 1)
        c_after = X._latched_count(latch, s["t"])
        res = "success" if (s["done"] and ep.outcome == "success") else \
              ("failure" if (s["done"] and ep.outcome == "failure") else None)
        expect = E.step_reward(c_before, c_after, n_cp, res, s["t"], horizon)
        assert abs(s["reward"]["total"] - expect) < 1e-9, \
            f"{slug}: tick {s['t']} label {s['reward']['total']} != env {expect}"
        assert c_after == s["n_latched"], f"{slug}: tick {s['t']} n_latched mismatch"
    # 3. t=0 build equals a fresh 0-action replay
    build = ep.meta["build_state"]
    fresh = _fresh_build(ep.meta["game_file"])
    assert set(build.keys()) == set(fresh.keys()), \
        f"{slug}: build_state bodies {set(build)} != fresh {set(fresh)}"
    controlled = [n for n, q in build.items() if q.get("controlled")]
    assert controlled, f"{slug}: build_state has no controlled body"
    for name, q in fresh.items():
        assert build[name].get("pos") == q.get("pos"), \
            f"{slug}: build_state[{name}].pos {build[name].get('pos')} != fresh {q.get('pos')}"
    # 4. round-trip: re-open and re-validate independently
    ep2 = EpisodeDataset(str(ep.dir.parent.parent)).episodes(slugs=[slug])
    _ = list(ep2)
    return {"slug": slug, "kind": ep.trajectory_kind, "dim": ep.dimension,
            "outcome": ep.outcome, "ticks": ep.ticks, "steps": len(steps),
            "frames": len(ep.frame_paths()), "return": round(full, 4),
            "bytes": _dir_bytes(str(ep.dir))}


def validate_dataset(out, rows):
    """DATASET-level asserts (the point of exporting negatives): behavioral diversity, the
    win/negative return separation, and the loader's kind-filter round-trip."""
    problems = []
    outcomes = [r["outcome"] for r in rows]
    # 5. DIVERSITY -- not all-success (there ARE negatives)
    if outcomes and all(o == "success" for o in outcomes):
        problems.append("outcome distribution is all-success -- no negatives were exported")
    # 6. SEPARATION -- every failure/timeout return strictly below every success return
    wins = [r["return"] for r in rows if r["outcome"] == "success"]
    negs = [(r["slug"], r["kind"], r["outcome"], r["return"])
            for r in rows if r["outcome"] != "success"]
    if wins and negs:
        min_win = min(wins)
        for slug, kind, oc, ret in negs:
            if not (ret < min_win):
                problems.append(f"{slug} [{kind}] {oc} return {ret} NOT < min win return {min_win}")
    # 7. KIND FILTER -- wins vs negatives round-trip through the loader (state-only aware)
    ds = EpisodeDataset(out)
    n_win = sum(1 for _ in ds.filter_by_kind(("demo", "witness")))
    n_neg = sum(1 for _ in ds.filter_by_kind(("random", "perturbed")))
    exp_win = sum(1 for r in rows if r["kind"] in ("demo", "witness"))
    exp_neg = sum(1 for r in rows if r["kind"] in ("random", "perturbed"))
    if n_win != exp_win or n_neg != exp_neg:
        problems.append(f"filter_by_kind count mismatch: wins {n_win}!={exp_win} or "
                        f"negs {n_neg}!={exp_neg}")
    for ep in ds.filter_by_kind(("demo", "witness", "random", "perturbed")):
        ep.validate(require_frames=int(ep.meta.get("n_frames", 0)) > 0)
    return problems


def main(argv):
    out = argv[0]
    ds = EpisodeDataset(out)
    if len(ds) == 0:
        print(f"VALIDATION FAIL: no episodes under {out}")
        return 1
    rows = []
    ok = True
    for ep in ds.episodes():
        try:
            rows.append(validate_episode(ep))
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  [FAIL] {ep.slug} [{ep.trajectory_kind}]: {exc}")
    print(f"\n{'slug':40} {'kind':10} {'dim':4} {'outcome':8} {'ticks':>5} {'steps':>5} "
          f"{'frames':>6} {'return':>9} {'MB':>7}")
    print("-" * 108)
    for r in sorted(rows, key=lambda x: (x["slug"], x["kind"], -x["return"])):
        print(f"{r['slug'][:40]:40} {r['kind']:10} {r['dim']:4} {r['outcome']:8} "
              f"{r['ticks']:>5} {r['steps']:>5} {r['frames']:>6} {r['return']:>9} "
              f"{r['bytes'] / 1e6:>7.2f}")
    print("-" * 108)

    # outcome distribution + per-kind return ranges (the negatives-diversity summary)
    from collections import Counter, defaultdict
    dist = Counter(r["outcome"] for r in rows)
    print(f"outcome distribution: {dict(dist)}")
    by_kind = defaultdict(list)
    for r in rows:
        by_kind[r["kind"]].append(r["return"])
    for kind in sorted(by_kind):
        rs = by_kind[kind]
        print(f"  {kind:10} n={len(rs):<3} return [{min(rs):+.3f} .. {max(rs):+.3f}]")

    problems = validate_dataset(out, rows) if rows else ["no episodes"]
    for p in problems:
        ok = False
        print(f"  [DATASET-FAIL] {p}")
    print(f"VALIDATION {'PASS' if ok and rows else 'FAIL'}: "
          f"{len(rows)}/{len(ds)} episodes validated")
    return 0 if (ok and rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
