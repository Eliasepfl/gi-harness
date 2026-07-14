"""Run-telemetry ledger — failures and repairs counted as first-class statistics.

Every `gamegen.generate_game` run appends ONE JSON line to `runs/ledger.jsonl`
(the `runs/` dir is created on demand). The ledger is the raw data of the
base-of-games campaign: which prompts, which model, how many repair attempts,
which failure classes, and — crucially — which failures are FLAGRANT.

Flagrant = model-discipline errors, where the model ignored the instructions it
was given: no valid python emitted (syntax error at the sandbox scan), forbidden
imports, missing required symbols, module load or build(world) crashes. These
are distinct from HEALTHY design failures (UNSOLVED goals, dead actions, goal
errors), which are the productive part of the repair loop. The flagrant/healthy
ratio per model is the core signal for choosing a volume backend.

`record_run` extracts everything from the machine-readable result dict — no
narration. `stats` aggregates the ledger per (backend, model).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

DEFAULT_LEDGER = "runs/ledger.jsonl"


# --------------------------------------------------------------------------
# Extraction from one verifier report
# --------------------------------------------------------------------------
def _failed_checks(report: dict) -> list[str]:
    """Names ('layer.check') of every failed check in a verification report."""
    failed = []
    for layer, body in (report.get("layers") or {}).items():
        if isinstance(body, dict) and not body.get("passed", True):
            for name, res in (body.get("checks") or {}).items():
                ok = res.get("pass", True) if isinstance(res, dict) else bool(res)
                if not ok:
                    failed.append(f"{layer}.{name}")
    return failed


def _flagrant_labels(report: dict) -> list[str]:
    """Model-discipline error labels detected in one failed attempt's report.

    These map to "the model ignored the instructions", as opposed to healthy
    design failures (UNSOLVED / GOAL_ERROR / dead actions):
      - no_python_block : sandbox scan hit a syntax error (raw prose/truncated
                          text was written instead of a valid python module)
      - forbidden_import: the game imported something (only `world` is allowed)
      - dunder_access   : sandbox rejected dunder introspection
      - load_error      : module executed but crashed at load time
      - missing_symbols : required section-2 symbols absent/not callable
      - build_failure   : build(world) raised
    """
    labels: list[str] = []
    checks = ((report.get("layers") or {}).get("G0_static") or {}).get("checks") or {}

    scan = checks.get("sandbox_scan")
    if isinstance(scan, dict) and not scan.get("pass", True):
        violations = [str(v) for v in (scan.get("violations") or [])]
        if any("syntax error" in v for v in violations):
            labels.append("no_python_block")
        if any("forbidden import" in v for v in violations):
            labels.append("forbidden_import")
        if any("dunder" in v for v in violations):
            labels.append("dunder_access")

    for check_name, label in (("loads", "load_error"),
                              ("symbols", "missing_symbols"),
                              ("builds", "build_failure")):
        res = checks.get(check_name)
        if isinstance(res, dict) and not res.get("pass", True):
            labels.append(label)
    return labels


# --------------------------------------------------------------------------
# Ledger writing
# --------------------------------------------------------------------------
def record_run(result: dict, prompt: str, model, wall_s: float,
               path: str | None = None) -> dict:
    """Append one machine-readable ledger line for a generate_game run.

    Returns the entry dict that was written. Creates the ledger dir on demand.
    Path resolution: explicit arg > HARNESS_LEDGER env var > DEFAULT_LEDGER —
    the env var is the per-task shard hook for parallel generation farms
    (cross-node NFS appends to one file corrupt; shards + `ledger merge`).
    """
    path = path or os.environ.get("HARNESS_LEDGER") or DEFAULT_LEDGER
    attempts = result.get("attempts") or []

    failures = []
    flagrant: list[str] = []
    for att in attempts:
        rep = att.get("report") if isinstance(att, dict) else None
        if not isinstance(rep, dict) or rep.get("passed"):
            continue
        if "error" in rep and "layers" not in rep:
            # Error-shaped report (sandbox timeout, worker crash): an
            # INFRASTRUCTURE failure — count it visibly, never as a model failure.
            failures.append({
                "failure_class": "VERIFY_ERROR",
                "failed_checks": [],
                "hint": f"verification infrastructure: {rep['error'].get('type', 'unknown')}",
            })
            continue
        failures.append({
            "failure_class": rep.get("failure_class"),
            "failed_checks": _failed_checks(rep),
            "hint": (rep.get("hint") or "").strip(),
        })
        flagrant.extend(_flagrant_labels(rep))

    final = attempts[-1].get("report") if attempts and isinstance(attempts[-1], dict) else None
    witness = final.get("witness") if isinstance(final, dict) else None
    witness = witness if isinstance(witness, dict) else None

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "prompt": prompt,
        "backend": result.get("backend"),
        "engine": result.get("engine"),
        "model": model,
        "verdict": result.get("verdict"),
        "attempts": len(attempts),
        "failures": failures,
        "witness_ticks": witness.get("ticks") if witness else None,
        "checkpoints": dict(witness.get("checkpoints") or {}) if witness else {},
        "integrity": result.get("integrity"),
        "wall_s": round(float(wall_s), 2),
        "flagrant": flagrant,
    }
    # Parts-bank pipeline block (retrieved menu / mode / parts used), when the
    # generator produced one. Passed straight through — gamegen owns its content.
    pipeline = result.get("pipeline")
    if isinstance(pipeline, dict):
        entry["pipeline"] = pipeline

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return entry


# --------------------------------------------------------------------------
# Shard merge (cluster farms -> canonical ledger)
# --------------------------------------------------------------------------
_VOLATILE_KEYS = ("ts", "wall_s")  # differ across re-runs of the same verdict


def _dedupe_key(obj: dict) -> tuple:
    """(game_id, seed, verdict_hash) — the merge identity of one ledger line.

    game_id comes from whichever id field the line carries (farm shards write
    `game`, generation entries write `prompt`); seed from the embedded witness
    when present. verdict_hash covers the whole line minus volatile fields, so
    identical re-runs collapse while genuinely different verdicts survive.
    """
    import hashlib

    game = obj.get("game") or obj.get("game_path") or obj.get("game_id") or obj.get("prompt")
    seed = obj.get("seed")
    if seed is None:
        report = obj.get("report")
        witness = report.get("witness") if isinstance(report, dict) else None
        if isinstance(witness, dict):
            seed = witness.get("seed")
    stable = {k: v for k, v in obj.items() if k not in _VOLATILE_KEYS}
    payload = json.dumps(stable, sort_keys=True, ensure_ascii=False, default=str)
    return (str(game), seed, hashlib.sha256(payload.encode("utf-8")).hexdigest())


def merge_shards(shards: list[str], into: str = DEFAULT_LEDGER) -> dict:
    """Merge per-task ledger shards into the canonical ledger, idempotently.

    Cross-node NFS appends are racy, so cluster arrays write one shard per
    task (`ledger.$JOBID.$TASKID.jsonl`, ORCD_DEPLOYMENT §5); this merge is
    the single-writer step that owns `into`. Lines already present (by
    `_dedupe_key`) are never re-appended, so re-merging after a preemption
    requeue or a partial rsync is safe. Corrupt lines are counted, not fatal.

    -> {"shards": n, "lines": m, "appended": k, "duplicates": d, "corrupt": c}
    """
    seen = {_dedupe_key(e) for e in _read_ledger(into)}
    summary = {"shards": 0, "lines": 0, "appended": 0, "duplicates": 0, "corrupt": 0}

    directory = os.path.dirname(into)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(into, "a", encoding="utf-8") as out:
        for shard in sorted(shards):
            summary["shards"] += 1
            if not os.path.isfile(shard):
                continue
            with open(shard, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    summary["lines"] += 1
                    try:
                        obj = json.loads(line)
                        if not isinstance(obj, dict):
                            raise ValueError("not an object")
                    except (json.JSONDecodeError, ValueError):
                        summary["corrupt"] += 1
                        continue
                    key = _dedupe_key(obj)
                    if key in seen:
                        summary["duplicates"] += 1
                        continue
                    seen.add(key)
                    out.write(line + "\n")
                    summary["appended"] += 1
    return summary


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------
def _read_ledger(path: str) -> list[dict]:
    entries: list[dict] = []
    if not os.path.isfile(path):
        return entries
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # a corrupt line must not sink the aggregation
            if isinstance(obj, dict):
                entries.append(obj)
    return entries


def stats(path: str = DEFAULT_LEDGER) -> dict:
    """Aggregate the ledger per (backend, model).

    -> {"total_runs": N, "groups": [{backend, model, runs, completed,
        completion_rate, invalidated, mean_attempts_to_completed,
        failure_classes: {cls: n}, flagrant: {label: n}, mean_wall_s}]}
    """
    entries = _read_ledger(path)
    groups: dict = {}
    for e in entries:
        key = (str(e.get("backend")), str(e.get("model")))
        g = groups.setdefault(key, {
            "backend": e.get("backend"), "model": e.get("model"),
            "runs": 0, "completed": 0, "invalidated": 0,
            "_attempts_to_completed": [], "_walls": [],
            "failure_classes": {}, "flagrant": {},
        })
        g["runs"] += 1
        verdict = e.get("verdict")
        if verdict == "COMPLETED":
            g["completed"] += 1
            g["_attempts_to_completed"].append(int(e.get("attempts") or 0))
        elif verdict == "INVALIDATED":
            g["invalidated"] += 1
        for f in e.get("failures") or []:
            cls = (f.get("failure_class") if isinstance(f, dict) else None) or "unknown"
            g["failure_classes"][cls] = g["failure_classes"].get(cls, 0) + 1
        for label in e.get("flagrant") or []:
            g["flagrant"][str(label)] = g["flagrant"].get(str(label), 0) + 1
        wall = e.get("wall_s")
        if isinstance(wall, (int, float)):
            g["_walls"].append(float(wall))

    out = []
    for g in groups.values():
        atc = g.pop("_attempts_to_completed")
        walls = g.pop("_walls")
        g["completion_rate"] = round(g["completed"] / g["runs"], 3) if g["runs"] else 0.0
        g["mean_attempts_to_completed"] = (round(sum(atc) / len(atc), 2)
                                           if atc else None)
        g["mean_wall_s"] = round(sum(walls) / len(walls), 1) if walls else None
        out.append(g)
    out.sort(key=lambda g: (str(g["backend"]), str(g["model"])))
    return {"total_runs": len(entries), "groups": out}
