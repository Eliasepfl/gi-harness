#!/usr/bin/env python
"""bench.py -- Python driver for the Planck.js substrate spike.

Proves/refutes "rung-4 step 1" (port the pymunk substrate to Planck.js in pure
Node) with measurements. Uses ONLY the Python standard library + subprocess to
drive `node runner.js`, and imports the FROZEN harness (read-only) to benchmark
the pymunk substrate side by side.

Criteria (printed as a PASS/FAIL table):
  (a) startup      -- median cold `node runner.js` invocation time
  (b) throughput   -- episodes/sec: Planck (Node) vs pymunk (Python), same batch
  (c) determinism  -- byte-identical output across processes and within a process
  (d) solvability  -- the seeded random probe solves sample_drift in <= 40 episodes

Run with the anaconda base python (has pymunk):  python bench.py
"""

import json
import os
import random
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)  # import the frozen harness read-only

NODE = "node"
RUNNER = os.path.join(HERE, "runner.js")
DRIFT_JS = os.path.join(HERE, "sample_drift.js")

# G3-probe parameters -- mirror harness/gameverify.py exactly.
ACTIONS = ["left", "right", "up", "down"]
E_EPISODES = 40      # PROBE_EPISODES
H_TICKS = 120        # PROBE_HORIZON
MACRO_MIN, MACRO_MAX = 1, 4  # macro-action hold length
DECLARED_CPS = ["moved_off_start", "crossed_midline", "entered_upper_half"]

# PASS thresholds (engineering choices, documented in SPIKE_REPORT.md).
STARTUP_MAX_MS = 1500.0   # cold start is amortized (whole G3 batch = ONE process)
THROUGHPUT_MIN_RATIO = 0.2  # Node must be within ~5x of pymunk to count as "parity"


def macro_plan(seed, horizon=H_TICKS):
    """Flat per-tick action list -- byte-for-byte identical to gameverify._macro_plan."""
    rng = random.Random(seed)
    plan = []
    while len(plan) < horizon:
        action = rng.choice(ACTIONS)
        hold = rng.randint(MACRO_MIN, MACRO_MAX)
        plan.extend([action] * hold)
    return plan[:horizon]


def run_node(job, timeout=180):
    proc = subprocess.run([NODE, RUNNER], input=json.dumps(job),
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"node exit {proc.returncode}: {proc.stderr[:400]}")
    return proc.stdout


# ======================================================================== #
# (a) startup
# ======================================================================== #
def bench_startup(source):
    trivial = {"source": source,
               "episodes": [{"seed": 0, "actions": ["up", "right"]}],
               "max_ticks": 2, "frames_every": 0}
    payload = json.dumps(trivial)
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        p = subprocess.run([NODE, RUNNER], input=payload,
                           capture_output=True, text=True, timeout=60)
        dt = (time.perf_counter() - t0) * 1000.0
        if p.returncode != 0:
            raise RuntimeError(f"startup node exit {p.returncode}: {p.stderr[:300]}")
        times.append(dt)
    median = statistics.median(times)
    return {"times_ms": [round(t, 1) for t in times], "median_ms": round(median, 1),
            "pass": median < STARTUP_MAX_MS}


# ======================================================================== #
# (b) throughput -- Node vs pymunk, identical 40x120 batch
# ======================================================================== #
def bench_throughput_node(source):
    plans = [macro_plan(e) for e in range(E_EPISODES)]
    job = {"source": source,
           "episodes": [{"seed": 0, "actions": pl} for pl in plans],
           "max_ticks": H_TICKS, "frames_every": 0}
    t0 = time.perf_counter()
    out = run_node(job)
    wall = time.perf_counter() - t0
    recs = [json.loads(l) for l in out.splitlines() if l.strip()]
    successes = sum(1 for r in recs if r["result"] == "success")
    return {"episodes": len(recs), "successes": successes, "wall_s": wall,
            "eps_per_sec": len(recs) / wall}


def bench_throughput_pymunk():
    from harness.verify.gameverify import run_episode, load_game
    from harness.gen.gamegen import _TEMPLATE_GAMES
    from harness.core.world import World

    game = load_game(_TEMPLATE_GAMES["drift"])
    plans = [macro_plan(e) for e in range(E_EPISODES)]
    t0 = time.perf_counter()
    successes = 0
    for pl in plans:
        world = World(seed=0)
        game.build(world)
        rec = run_episode(game, world, iter(pl), H_TICKS)
        if rec["result"] == "success":
            successes += 1
    wall = time.perf_counter() - t0
    return {"episodes": len(plans), "successes": successes, "wall_s": wall,
            "eps_per_sec": len(plans) / wall}


# ======================================================================== #
# (c) determinism -- the decisive test
# ======================================================================== #
def bench_determinism(source):
    plans = [macro_plan(e) for e in range(E_EPISODES)]
    job = {"source": source,
           "episodes": [{"seed": 0, "actions": pl} for pl in plans],
           "max_ticks": H_TICKS, "frames_every": 0}
    out_a = run_node(job)
    out_b = run_node(job)  # a SECOND, independent node process
    cross_process = (out_a == out_b)

    # Within one process: two identical (seed, actions) episodes must match.
    same_seed_job = {"source": source,
                     "episodes": [{"seed": 7, "actions": macro_plan(3)},
                                  {"seed": 7, "actions": macro_plan(3)}],
                     "max_ticks": H_TICKS, "frames_every": 0}
    lines = [l for l in run_node(same_seed_job).splitlines() if l.strip()]
    within_process = (len(lines) == 2 and lines[0] == lines[1])

    return {"cross_process_identical": cross_process,
            "within_process_identical": within_process,
            "bytes_a": len(out_a), "bytes_b": len(out_b),
            "pass": cross_process and within_process}


# ======================================================================== #
# (d) solvability -- probe solves sample_drift, checkpoints latch
# ======================================================================== #
def bench_solvability(source):
    plans = [macro_plan(e) for e in range(E_EPISODES)]
    job = {"source": source,
           "episodes": [{"seed": 0, "actions": pl} for pl in plans],
           "max_ticks": H_TICKS, "frames_every": 0}
    recs = [json.loads(l) for l in run_node(job).splitlines() if l.strip()]
    witness_idx = next((i for i, r in enumerate(recs) if r["result"] == "success"), None)
    solved = witness_idx is not None
    detail = {"solved": solved, "solved_within": witness_idx,
              "total_success": sum(1 for r in recs if r["result"] == "success")}
    if solved:
        w = recs[witness_idx]
        cps = w["checkpoints"]
        detail["witness_ticks"] = w["ticks"]
        detail["witness_checkpoints"] = cps
        dead = [k for k in DECLARED_CPS if cps.get(k) is None]
        detail["dead_milestones"] = dead
        # empirical latch order vs declared order (non-fatal, like the harness)
        latched = [(cps[k], k) for k in DECLARED_CPS if cps.get(k) is not None]
        empirical = [k for _, k in sorted(latched)]
        detail["empirical_order"] = empirical
        detail["order_matches_declared"] = (empirical == DECLARED_CPS)
        detail["non_trivial"] = w["ticks"] >= 5
        detail["pass"] = solved and not dead and detail["non_trivial"]
    else:
        detail["pass"] = False
    return detail


# ======================================================================== #
# main
# ======================================================================== #
def main():
    source = open(DRIFT_JS, "r", encoding="utf-8").read()

    print("=" * 72)
    print("PLANCK.JS SUBSTRATE SPIKE -- bench.py")
    print("=" * 72)

    print("\n[a] startup (5 cold `node runner.js` invocations) ...")
    a = bench_startup(source)
    print(f"    times_ms = {a['times_ms']}   median = {a['median_ms']} ms")

    print("\n[b] throughput (40 episodes x 120 ticks) ...")
    node_tp = bench_throughput_node(source)
    print(f"    Node/Planck : {node_tp['eps_per_sec']:8.1f} eps/s  "
          f"({node_tp['wall_s']*1000:7.1f} ms wall, {node_tp['successes']}/{node_tp['episodes']} solved)")
    pym_tp = bench_throughput_pymunk()
    print(f"    pymunk      : {pym_tp['eps_per_sec']:8.1f} eps/s  "
          f"({pym_tp['wall_s']*1000:7.1f} ms wall, {pym_tp['successes']}/{pym_tp['episodes']} solved)")
    ratio = node_tp["eps_per_sec"] / pym_tp["eps_per_sec"]
    print(f"    ratio Node/pymunk = {ratio:.2f}x")
    b_pass = ratio >= THROUGHPUT_MIN_RATIO

    print("\n[c] determinism (DECISIVE) ...")
    c = bench_determinism(source)
    print(f"    cross-process byte-identical : {c['cross_process_identical']} "
          f"({c['bytes_a']} vs {c['bytes_b']} bytes)")
    print(f"    within-process same-seed     : {c['within_process_identical']}")

    print("\n[d] solvability (probe on sample_drift) ...")
    d = bench_solvability(source)
    if d["solved"]:
        print(f"    solved at episode {d['solved_within']} in {d['witness_ticks']} ticks "
              f"({d['total_success']}/{E_EPISODES} episodes solved)")
        print(f"    witness checkpoints  = {d['witness_checkpoints']}")
        print(f"    dead milestones      = {d['dead_milestones']}")
        print(f"    declared order       = {DECLARED_CPS}")
        print(f"    empirical order      = {d['empirical_order']}  "
              f"(matches declared: {d['order_matches_declared']})")
    else:
        print(f"    NOT solved in {E_EPISODES} episodes")

    rows = [
        ("(a) startup < %.0f ms" % STARTUP_MAX_MS, a["pass"],
         f"median {a['median_ms']} ms"),
        ("(b) throughput parity (>= %.2fx pymunk)" % THROUGHPUT_MIN_RATIO, b_pass,
         f"Node {node_tp['eps_per_sec']:.0f} vs pymunk {pym_tp['eps_per_sec']:.0f} eps/s = {ratio:.2f}x"),
        ("(c) determinism (bitwise)", c["pass"],
         f"cross-proc={c['cross_process_identical']} within-proc={c['within_process_identical']}"),
        ("(d) solvability <= 40 episodes", d["pass"],
         f"solved@{d.get('solved_within')} ticks={d.get('witness_ticks')} dead={d.get('dead_milestones')}"),
    ]
    print("\n" + "=" * 72)
    print("PASS/FAIL TABLE")
    print("=" * 72)
    for name, ok, note in rows:
        print(f"  [{'PASS' if ok else 'FAIL'}]  {name:42s}  {note}")
    all_pass = all(ok for _, ok, _ in rows)
    print("=" * 72)
    print(f"  OVERALL: {'PASS' if all_pass else 'FAIL'}")
    print("=" * 72)

    summary = {"startup": a, "throughput_node": node_tp, "throughput_pymunk": pym_tp,
               "throughput_ratio": ratio, "determinism": c, "solvability": d,
               "overall_pass": all_pass}
    with open(os.path.join(HERE, "bench_results.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print("\nwrote bench_results.json")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
