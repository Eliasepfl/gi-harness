"""Bank-CI — offline certification pass for the parts bank (CONTRACTS §9, B.4).

A part is not just a shape; it is a shape AND a machine-checkable guarantee. This
module is the one-time, offline oracle that admits a part to the catalog: it spawns
every entry (across a small grid of its override ranges) into a minimal World, runs
a settle, and asserts the universal sanity checks plus the part's per-category
invariant. Certifying parts once, here, is what lets in-game checks stay cheap: at
game time a part is already proven correct.

It reuses the live-state machinery of the verifier (``harness.gameverify`` constants
and the World's NaN sentinel) rather than reimplementing sanity:

    universal        no NaN/explosion; dynamic sub-bodies stay in bounds; no
                     initial self-penetration; no resting penetration with ground
    terrain          every sub-body static; Δpos ~ 0 under settle
    prop             primary dynamic; comes to rest (bounded settle speed)
    hazard           every sub-body is a sensor (never a physical collision impulse)
    mobile           joint(s) present; settles without NaN; bounded motion/spin
    trigger          primary is a sensor (never blocks a body)

CLI: ``python -m harness.bank_ci [--version v1] [--json]`` prints a table and exits
non-zero if any entry fails (naming the offending entry + checks).
"""

from __future__ import annotations

import argparse
import json
import math
import sys

from harness.core.bank import load_bank
from harness.verify.gameverify import ESCAPE_MARGIN, NAN_EVENT_TYPES, PEN_INIT_TOL
from harness.core.world import World

# --- Certification constants ([eng.] = engineering choice) --------------- #
SETTLE_STEPS = 480          # physics steps of noop settle per instantiation [eng.]
REST_SPEED = 35.0           # px/s: a settled prop's primary must move slower [eng.]
SPIN_LIMIT = 60.0           # rad/s: bounded angular velocity for mobile parts [eng.]
SPEED_LIMIT = 1000.0        # px/s: bounded linear speed for mobile parts [eng.]
REST_PEN_TOL = 2.5          # px: tolerated resting interpenetration after settle [eng.]
GROUND_TOP = 20.0           # test-ground top edge (box at (400,10), size (_,20))
PROP_SPAWN = (400.0, 140.0)  # default spawn for parts with no cert.pos [eng.]
_GROUND_NAME = "__ground"


def _grid(entry: dict) -> list[tuple[str, dict]]:
    """Small override grid: default + scale extremes + the signature param extremes.

    Kept to <=5 instantiations per part (the whole bank certifies in a burst).
    """
    over = entry.get("overridable", {})
    grid: list[tuple[str, dict]] = [("default", {})]
    if "scale" in over:
        lo, hi = over["scale"]["range"]
        grid += [("scale=lo", {"scale": lo}), ("scale=hi", {"scale": hi})]
    for key, spec in over.items():
        if key == "scale" or "path" not in spec:
            continue
        lo, hi = spec["range"]
        grid += [(f"{key}=lo", {key: lo}), (f"{key}=hi", {key: hi})]
        break  # only the first (signature) numeric override, to bound the grid
    return grid


def _has_nan(world) -> bool:
    return any(e.get("type") in NAN_EVENT_TYPES for e in world.events())


def _speed(world, name: str) -> float:
    vx, vy = world.query(name)["vel"]
    return math.hypot(vx, vy)


def _penetrations(world, names: list[str], tol: float) -> list:
    """Offending (a, b, depth) pairs over `names` (dynamic-involving, non-sensor)."""
    offenders = []
    for i, a in enumerate(names):
        qa = world.query(a)
        for b in names[i + 1:]:
            qb = world.query(b)
            if qa["static"] and qb["static"]:
                continue  # static-static never certified as penetration (as in G0)
            if qa["sensor"] or qb["sensor"]:
                continue
            depth = float(world.penetration_depth(a, b) or 0.0)
            if depth > tol:
                offenders.append([a, b, round(depth, 2)])
    return offenders


def certify_instance(entry: dict, overrides: dict) -> dict:
    """Certify one instantiation. Returns {"ok": bool, "failed": [labels]}."""
    cat = entry["category"]
    cert = entry.get("cert", {})
    ground = cert.get("ground", cat == "prop")

    world = World(seed=0)
    if ground:
        world.add(_GROUND_NAME, "box", pos=(400, 10), size=(1000, 20),
                  static=True, friction=0.9)
    pos = list(cert.get("pos") or PROP_SPAWN)

    try:
        primary = world.part("cert", entry["name"], pos=pos, **overrides)
    except Exception as exc:  # noqa: BLE001 - instantiation failure is a hard fail
        return {"ok": False, "failed": [f"instantiate:{exc}"]}

    sub = [n for n in world.entities() if n != _GROUND_NAME]
    init = world.snapshot()

    failed: list[str] = []

    def need(ok: bool, label: str) -> None:
        if not ok:
            failed.append(label)

    # --- initial self-penetration (mobile subassemblies must abut, not overlap) ---
    need(not _penetrations(world, sub, PEN_INIT_TOL), "self_penetration")

    # --- settle ---
    world.step(SETTLE_STEPS)

    # --- universal sanity ---
    need(not _has_nan(world), "nan")
    dyn = [n for n in sub if not world.query(n)["static"]]
    escaped = [n for n in dyn if not world.in_bounds(n, ESCAPE_MARGIN)]
    need(not escaped, "escape")
    rest_names = sub + ([_GROUND_NAME] if ground else [])
    need(not _penetrations(world, rest_names, REST_PEN_TOL), "rest_penetration")

    # --- per-category invariant ---
    if cat == "terrain":
        need(all(world.query(n)["static"] for n in sub), "not_all_static")
        moved = max(
            abs(world.query(n)["pos"][0] - init[n]["pos"][0])
            + abs(world.query(n)["pos"][1] - init[n]["pos"][1]) for n in sub)
        need(moved < 1.0, "moved")
    elif cat == "prop":
        need(not world.query(primary)["static"], "not_dynamic")
        need(_speed(world, primary) < REST_SPEED, "not_settled")
    elif cat in ("hazard", "trigger"):
        need(world.query(primary)["sensor"], "primary_not_sensor")
        if cat == "hazard":
            need(all(world.query(n)["sensor"] for n in sub), "not_all_sensor")
    elif cat == "mobile":
        need(len(entry.get("joints", [])) >= 1, "no_joint")
        spin = max((abs(world.query(n)["angular_vel"]) for n in dyn), default=0.0)
        need(spin < SPIN_LIMIT, "spin")
        speed = max((_speed(world, n) for n in dyn), default=0.0)
        need(speed < SPEED_LIMIT, "unbounded_speed")

    return {"ok": not failed, "failed": failed}


def certify_entry(entry: dict) -> dict:
    """Certify a part across its override grid; aggregate to one row."""
    results = [(label, certify_instance(entry, ov)) for label, ov in _grid(entry)]
    ok = all(r["ok"] for _, r in results)
    failed = sorted({f"{label}:{f}" for label, r in results for f in r["failed"]})
    return {"name": entry["name"], "category": entry["category"],
            "grid": len(results), "ok": ok, "failed": failed}


def certify_bank(version: str = "v1") -> tuple:
    """Certify every entry; return (bank, [rows])."""
    bank = load_bank(version, use_cache=False)
    rows = [certify_entry(bank.get(name)) for name in bank.names()]
    return bank, rows


def _print_table(bank, rows) -> None:
    width = max((len(r["name"]) for r in rows), default=4)
    header = f"  {'PART'.ljust(width)}  {'CATEGORY':8}  GRID  RESULT"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in rows:
        status = "PASS" if r["ok"] else "FAIL"
        line = f"  {r['name'].ljust(width)}  {r['category']:8}  {r['grid']:>4}  {status}"
        if not r["ok"]:
            line += "  <- " + ", ".join(r["failed"])
        print(line)
    n_pass = sum(r["ok"] for r in rows)
    by_cat: dict[str, list] = {}
    for r in rows:
        by_cat.setdefault(r["category"], [0, 0])
        by_cat[r["category"]][0] += 1
        by_cat[r["category"]][1] += int(r["ok"])
    print("  " + "-" * (len(header) - 2))
    cat_summary = ", ".join(f"{c} {p}/{t}" for c, (t, p) in sorted(by_cat.items()))
    print(f"  {n_pass}/{len(rows)} parts certified  ({cat_summary})")
    print(f"  bank {bank.bank_version}  hash {bank.content_hash[:12]}  "
          f"lock_ok={bank.hash_ok}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m harness.bank_ci",
                                     description="Certify the parts bank.")
    parser.add_argument("--version", default="v1", help="bank version (default v1)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args(argv)

    bank, rows = certify_bank(args.version)
    n_pass = sum(r["ok"] for r in rows)
    all_ok = n_pass == len(rows)

    if args.json:
        print(json.dumps({
            "version": args.version,
            "bank_version": bank.bank_version,
            "content_hash": bank.content_hash,
            "lock_ok": bank.hash_ok,
            "passed": n_pass, "total": len(rows), "all_ok": all_ok,
            "rows": rows}, indent=2))
    else:
        _print_table(bank, rows)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
