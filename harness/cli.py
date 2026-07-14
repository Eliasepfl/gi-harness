"""harness CLI — subcommands generate / verify / play / demo (v1) and the
`game` group (v2: new / verify / replay).

Every command imports its module lazily (parallel dev of the modules): if a
module is missing, a clean message and return code 1, never a crash. Human
output by default, machine JSON via --json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EXAMPLES_DIR = Path("scenes/examples")


# ---- utilities -----------------------------------------------------------
def _emit_json(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _module_missing(name: str, exc: Exception, as_json: bool) -> int:
    msg = f"module '{name}' unavailable (parallel dev?): {exc}"
    if as_json:
        _emit_json({"error": msg})
    else:
        print(msg, file=sys.stderr)
    return 1


def _call_error(name: str, exc: Exception, as_json: bool) -> int:
    """Report a runtime failure inside a lazily-loaded module (clean, no crash)."""
    msg = f"{name} failed: {exc}"
    if as_json:
        _emit_json({"error": msg})
    else:
        print(msg, file=sys.stderr)
    return 1


def _failed_checks(report: dict) -> list[str]:
    """Extract the names of failed checks from a verification report."""
    failed = []
    for layer, body in (report.get("layers") or {}).items():
        if isinstance(body, dict) and not body.get("passed", True):
            for check, res in (body.get("checks") or {}).items():
                ok = res.get("pass", res.get("passed", True)) if isinstance(res, dict) else res
                if not ok:
                    failed.append(f"{layer}.{check}")
    return failed


# ---- generate ------------------------------------------------------------
def cmd_generate(args) -> int:
    try:
        from harness.legacy.generator import generate
    except Exception as exc:  # noqa: BLE001
        return _module_missing("generator", exc, args.json)

    result = generate(args.command, out_dir=args.out_dir, backend=args.backend)
    if args.json:
        _emit_json(result)
    else:
        print(f"verdict : {result.get('verdict')}  (backend {result.get('backend')})")
        print(f"scene   : {result.get('scene_path')}")
        print(f"tries   : {len(result.get('attempts', []))}")
    return 0 if result.get("scene_path") else 1


# ---- verify --------------------------------------------------------------
def cmd_verify(args) -> int:
    try:
        from harness.legacy.verifier import verify_scene
    except Exception as exc:  # noqa: BLE001
        return _module_missing("verifier", exc, args.json)

    report = verify_scene(args.scene_path, sandboxed=not args.no_sandbox)
    if args.json:
        _emit_json(report)
    else:
        verdict = "PASS" if report.get("passed") else "FAIL"
        print(f"{verdict}  {args.scene_path}")
        if report.get("failure_class"):
            print(f"  failure class : {report['failure_class']}")
        for name in _failed_checks(report):
            print(f"  failed check  : {name}")
        if report.get("hint"):
            print(f"  hint          : {report['hint']}")
    return 0 if report.get("passed") else 1


# ---- play ----------------------------------------------------------------
def cmd_play(args) -> int:
    try:
        from harness.legacy.navigator import navigate
    except Exception as exc:  # noqa: BLE001
        return _module_missing("navigator", exc, args.json)

    try:
        result = navigate(args.scene_path, policy=args.policy,
                          max_steps=args.max_steps, render=args.render)
    except NotImplementedError as exc:
        if args.json:
            _emit_json({"error": str(exc)})
        else:
            print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        _emit_json(result)
    else:
        state = "SUCCESS" if result.get("success") else "FAILURE"
        print(f"{state}  {args.scene_path}")
        print(f"  reason : {result.get('reason')}   steps : {result.get('steps')}   "
              f"actions : {len(result.get('actions', []))}")
        if result.get("error"):
            print(f"  error  : {result['error']}")
    return 0 if result.get("success") else 1


# ---- demo ----------------------------------------------------------------
def cmd_demo(args) -> int:
    """Full offline pipeline over scenes/examples/: verify -> play -> table."""
    try:
        from harness.legacy.verifier import verify_scene
    except Exception:  # noqa: BLE001
        verify_scene = None
    try:
        from harness.legacy.navigator import navigate
    except Exception as exc:  # noqa: BLE001
        return _module_missing("navigator", exc, args.json)

    scenes = sorted(p for p in EXAMPLES_DIR.glob("*.py") if not p.name.startswith("_"))
    rows = []
    overall_ok = True

    for path in scenes:
        # Convention: fixtures named broken* MUST be rejected by the verifier;
        # any other scene must pass AND be solved in play.
        expect_fail = path.name.startswith("broken")
        row = {"scene": path.name, "verified": None, "played": None,
               "steps": None, "reason": None, "ok": True}
        try:
            if verify_scene is not None:
                rep = verify_scene(str(path), sandboxed=not args.no_sandbox)
                row["verified"] = bool(rep.get("passed"))
            else:
                row["verified"] = None  # verifier absent -> not evaluated
        except Exception as exc:  # noqa: BLE001
            row["verified"] = False
            row["reason"] = f"verify-error: {exc}"
            row["ok"] = False
            overall_ok = False
            rows.append(row)
            continue

        if row["verified"] is not None and row["verified"] == expect_fail:
            # Valid scene rejected, or broken fixture accepted: a real gap.
            row["ok"] = False
            overall_ok = False
            if row["reason"] is None:
                row["reason"] = ("unexpected rejection" if expect_fail is False
                                 else "broken fixture accepted")

        # Only play scenes that verified (or if the verifier is absent).
        if row["verified"] in (True, None):
            try:
                res = navigate(str(path), policy="greedy", render=args.render)
                row["played"] = bool(res.get("success"))
                row["steps"] = res.get("steps")
                row["reason"] = res.get("reason")
                if row["verified"] is True and not res.get("success"):
                    row["ok"] = False
                    overall_ok = False
            except Exception as exc:  # noqa: BLE001
                row["played"] = False
                row["reason"] = f"play-error: {exc}"
                row["ok"] = False
                overall_ok = False
        # A correctly rejected invalid scene (verified=False) stays "ok"
        # (expected demonstration of the L0/L1 funnel).
        rows.append(row)

    if args.json:
        _emit_json({"overall_ok": overall_ok, "scenes": rows})
        return 0 if overall_ok else 1

    if not scenes:
        print(f"no scene in {EXAMPLES_DIR}/ (module A in progress?)")
        return 1

    print(f"{'scene':32} {'verif':7} {'played':7} {'steps':>6}  reason")
    print("-" * 72)
    for r in rows:
        v = {True: "PASS", False: "FAIL", None: "n/a"}[r["verified"]]
        p = {True: "SUCCESS", False: "FAILURE", None: "-"}[r["played"]]
        steps = r["steps"] if r["steps"] is not None else ""
        print(f"{r['scene']:32} {v:7} {p:7} {str(steps):>6}  {r['reason'] or ''}")
    print("-" * 72)
    print("RESULT :", "OK" if overall_ok else "gaps detected")
    return 0 if overall_ok else 1


# ==========================================================================
#  game group (v2 generated games)
# ==========================================================================
def cmd_game_new(args) -> int:
    """Generate a whole game from an open-ended prompt (gamegen)."""
    try:
        from harness.gen.gamegen import generate_game
    except Exception as exc:  # noqa: BLE001
        return _module_missing("gamegen", exc, args.json)

    try:
        result = generate_game(args.prompt, out_dir=args.out_dir, backend=args.backend,
                               engine=getattr(args, "engine", None))
    except Exception as exc:  # noqa: BLE001
        return _call_error("game new", exc, args.json)
    if args.json:
        _emit_json(result)
    else:
        print(f"verdict : {result.get('verdict')}  (backend {result.get('backend')})")
        print(f"game    : {result.get('game_path')}")
        print(f"tries   : {len(result.get('attempts', []))}")
        if result.get("design"):
            print("design  :")
            for line in str(result["design"]).splitlines():
                print(f"  {line}")
    return 0 if result.get("game_path") else 1


def cmd_game_verify(args) -> int:
    """Run the universal oracles (gameverify) on a generated game."""
    try:
        from harness.verify.gameverify import verify_game
    except Exception as exc:  # noqa: BLE001
        return _module_missing("gameverify", exc, args.json)

    try:
        report = verify_game(args.game_path, sandboxed=not args.no_sandbox)
    except Exception as exc:  # noqa: BLE001
        return _call_error("game verify", exc, args.json)
    if args.json:
        _emit_json(report)
    else:
        verdict = "PASS" if report.get("passed") else "FAIL"
        print(f"{verdict}  {args.game_path}")
        if report.get("failure_class"):
            print(f"  failure class : {report['failure_class']}")
        for name in _failed_checks(report):
            print(f"  failed check  : {name}")
        if report.get("hint"):
            print(f"  hint          : {report['hint']}")
    return 0 if report.get("passed") else 1


def _game_witness(game_path: str) -> dict:
    """Resolve a game's winning witness ({seed, actions, ticks, checkpoints}) via
    a fresh verify (engine-agnostic: verify_game routes py/js internally)."""
    from harness.verify import gameverify
    report = gameverify.verify_game(game_path)
    return report.get("witness") or {}


def cmd_game_replay(args) -> int:
    """Replay a game's witness to a GIF and/or a scrubbable frames-JSON substrate.

    Dispatches by ENGINE (py -> render.replay_gif unchanged; js -> the executors'
    render_js_replay, which previously had no CLI verb and made `game replay
    foo.js` crash in the py-only loader). ``--frames PATH`` additionally persists
    the replay substrate ({meta, frames}) for both engines. When ``--frames`` is
    given without an explicit ``--gif``, only the substrate is written."""
    game_path = args.game_path
    try:
        source = Path(game_path).read_text(encoding="utf-8")
    except OSError as exc:  # noqa: BLE001
        return _call_error("game replay", exc, args.json)
    try:
        from harness.verify.gameverify import detect_engine, WORLD_SEED
    except Exception as exc:  # noqa: BLE001
        return _module_missing("gameverify", exc, args.json)
    engine = detect_engine(game_path, source)

    frames_path = getattr(args, "frames", None)
    want_gif = (frames_path is None) or (args.gif is not None)

    _witness_cache: dict = {}

    def witness() -> dict:
        if "w" not in _witness_cache:
            _witness_cache["w"] = _game_witness(game_path)
        return _witness_cache["w"]

    out: dict = {"engine": engine}

    # --- frames substrate (both engines) ---
    if frames_path:
        try:
            from harness.verify.executors import replay_frames_doc
        except Exception as exc:  # noqa: BLE001
            return _module_missing("executors", exc, args.json)
        try:
            w = witness()
            doc = replay_frames_doc(source, engine=engine, actions=w.get("actions", []),
                                    witness=w, seed=int(WORLD_SEED))
        except Exception as exc:  # noqa: BLE001
            return _call_error("game replay --frames", exc, args.json)
        import gzip
        # The persisted substrate is exactly {meta, frames}; result/error stay
        # in-memory for the CLI's verdict + reporting.
        file_doc = {"meta": doc["meta"], "frames": doc["frames"]}
        text = json.dumps(file_doc, ensure_ascii=False, separators=(",", ":"))
        fpath = Path(frames_path)
        if fpath.parent and not fpath.parent.exists():
            fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(text, encoding="utf-8")
        raw = len(text.encode("utf-8"))
        gz = len(gzip.compress(text.encode("utf-8")))
        out["frames"] = {"out_path": str(fpath), "n_frames": len(doc.get("frames", [])),
                         "raw_bytes": raw, "gzip_bytes": gz, "result": doc.get("result")}

    # --- GIF (default output; skipped for a frames-only run) ---
    if want_gif:
        gif = args.gif or str(Path(game_path).with_suffix(".gif"))
        if engine == "js":
            try:
                from harness.verify.executors import render_js_replay
            except Exception as exc:  # noqa: BLE001
                return _module_missing("executors", exc, args.json)
            try:
                w = witness()
                gif_res = render_js_replay(source, gif, actions=w.get("actions", []),
                                           seed=int(WORLD_SEED))
            except Exception as exc:  # noqa: BLE001
                return _call_error("game replay", exc, args.json)
        else:
            try:
                from harness.render import replay_gif
            except Exception as exc:  # noqa: BLE001
                return _module_missing("render", exc, args.json)
            gif_res = replay_gif(game_path, gif, seed=args.seed)
        out["gif"] = gif_res

    if args.json:
        _emit_json(out)
    else:
        gif_res = out.get("gif") or {}
        fr = out.get("frames")
        if gif_res.get("result") == "error":
            print(f"ERROR  {game_path}", file=sys.stderr)
            print(f"  {gif_res.get('error')}", file=sys.stderr)
        else:
            verdict = str((gif_res.get("result") or (fr or {}).get("result"))).upper()
            print(f"{verdict}  {game_path}  [{engine}]")
            if gif_res:
                print(f"  ticks : {gif_res.get('ticks')}   gif : {gif_res.get('out_path')}")
        if fr:
            print(f"  frames: {fr['n_frames']}   json: {fr['out_path']}   "
                  f"raw {fr['raw_bytes']}B / gzip {fr['gzip_bytes']}B")

    verdicts = {(out.get("gif") or {}).get("result"),
                (out.get("frames") or {}).get("result")}
    return 0 if verdicts & {"success", "failure", "timeout"} else 1


def cmd_game_attack(args) -> int:
    """Run the adversarial G4 suite on a certified game (g4.attack_game)."""
    try:
        from harness.verify.g4 import attack_game
    except Exception as exc:  # noqa: BLE001
        return _module_missing("g4", exc, args.json)

    tiers = (0,) if args.tier == 0 else (0, 1)
    try:
        report = attack_game(args.game_path, tiers=tiers)
    except Exception as exc:  # noqa: BLE001
        return _call_error("game attack", exc, args.json)

    if args.json:
        _emit_json(report)
        return 0 if report.get("passed") else 1

    print(f"{str(report.get('grade')).upper()}  {args.game_path}")
    if report.get("error"):
        print(f"  error    : {report['error']}")
        if report.get("funnel_hint"):
            print(f"  hint     : {report['funnel_hint']}")
        return 0 if report.get("passed") else 1
    findings = report.get("findings", [])
    n_hard = len(report.get("hard_findings", []))
    print(f"  tiers    : {report.get('tiers_run')}   witness : {report.get('witness_ticks')}")
    print(f"  findings : {len(findings)} ({n_hard} hard)")
    for f in findings[:10]:
        tag = "HARD" if f.get("hard") else "soft"
        print(f"    [{tag}] {f.get('outcome')} ({f.get('family')}): {f.get('detail')}")
    t1 = report.get("tier1") or {}
    if t1.get("status"):
        extra = f" - {t1.get('reason')}" if t1.get("reason") else ""
        print(f"  tier1    : {t1['status']}{extra}")
    return 0 if report.get("passed") else 1


def cmd_game_watch(args) -> int:
    """Watch a game play live in a pygame window (real-time witness replay)."""
    as_json = getattr(args, "json", False)
    try:
        from harness.viewer import watch
    except Exception as exc:  # noqa: BLE001
        return _module_missing("viewer", exc, as_json)

    try:
        result = watch(args.game_path, seed=args.seed, speed=args.speed,
                       scale=args.scale, loop=args.loop)
    except RuntimeError as exc:   # pygame missing / unavailable -> clean hint
        msg = str(exc)
        if as_json:
            _emit_json({"error": msg})
        else:
            print(msg, file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        return _call_error("game watch", exc, as_json)

    if as_json:
        _emit_json(result)
    else:
        if result.get("result") == "error":
            print(f"ERROR  {args.game_path}", file=sys.stderr)
            print(f"  {result.get('error')}", file=sys.stderr)
        else:
            print(f"{str(result.get('result')).upper()}  {args.game_path}")
            print(f"  ticks : {result.get('ticks')}   closed by : {result.get('closed_by')}")
    return 0 if result.get("result") in ("success", "failure", "timeout") else 1


# ---- game demo -----------------------------------------------------------
# Plain-English seeds, varied mechanics, NO design hints: the prompt is all the
# model gets. Demos are generated ON THE FLY at demo start (nothing pre-baked).
DEFAULT_DEMO_PROMPTS = [
    "a catapult that must fling a stone over a wall into a bucket",
    "keep a balloon from touching the ground for as long as it takes to drift "
    "across the screen",
    "a magnet crane that must pick up scrap and drop it in a bin",
]


def _demo_row(prompt: str, backend: str, generate_game, replay_gif) -> dict:
    """Run ONE live demo: generate -> replay -> structured summary.

    Everything here comes from the machine-readable result dict (no LLM
    narration): verdict, backend, attempt count, per-failed-attempt
    failure_class + hint, witness ticks, checkpoint latch ticks, integrity, gif.
    """
    row = {"prompt": prompt, "verdict": None, "backend": None, "attempts": 0,
           "failed_attempts": [], "witness_ticks": None, "checkpoints": {},
           "integrity": None, "note": None, "game_path": None, "gif": None,
           "gif_result": None, "error": None}
    try:
        result = generate_game(prompt, backend=backend)
    except Exception as exc:  # noqa: BLE001 — one bad prompt must not sink the demo
        row["verdict"] = "ERROR"
        row["error"] = f"generate failed: {exc}"
        return row

    row["verdict"] = result.get("verdict")
    row["backend"] = result.get("backend")
    row["note"] = result.get("note")
    row["integrity"] = result.get("integrity")
    row["game_path"] = result.get("game_path")
    attempts = result.get("attempts") or []
    row["attempts"] = len(attempts)

    # One line per FAILED attempt: its failure class + repair hint.
    for i, att in enumerate(attempts, start=1):
        rep = att.get("report") if isinstance(att, dict) else None
        if isinstance(rep, dict) and not rep.get("passed"):
            row["failed_attempts"].append({
                "n": i,
                "failure_class": rep.get("failure_class"),
                "hint": (rep.get("hint") or "").strip(),
            })

    # Witness ticks + checkpoint latch ticks come from the FINAL report's witness.
    final = attempts[-1].get("report") if attempts else None
    witness = final.get("witness") if isinstance(final, dict) else None
    if isinstance(witness, dict):
        row["witness_ticks"] = witness.get("ticks")
        row["checkpoints"] = dict(witness.get("checkpoints") or {})

    # Render the winning witness to a GIF next to the final game file.
    game_path = row["game_path"]
    if game_path and row["verdict"] == "COMPLETED":
        gif = str(Path(game_path).with_suffix(".gif"))
        try:
            rr = (replay_gif(game_path, gif, actions=witness)
                  if isinstance(witness, dict) else replay_gif(game_path, gif))
            row["gif"] = rr.get("out_path", gif)
            row["gif_result"] = rr.get("result")
        except Exception as exc:  # noqa: BLE001
            row["gif_result"] = f"render error: {exc}"
    return row


def _print_demo(demos: list[dict], backend: str, all_completed: bool) -> None:
    print(f"=== LIVE GAME DEMO (backend={backend}) ===")
    for i, d in enumerate(demos, start=1):
        print(f"\n[{i}] {d['prompt']}")
        print(f"    verdict    : {d['verdict']}   backend: {d['backend']}   "
              f"tries: {d['attempts']}")
        if d.get("error"):
            print(f"    error      : {d['error']}")
        if d["witness_ticks"] is not None:
            print(f"    witness    : solved in {d['witness_ticks']} decision ticks")
        if d["checkpoints"]:
            cps = ", ".join(f"{k}@{v if v is not None else '-'}"
                            for k, v in d["checkpoints"].items())
            print(f"    milestones : {cps}")
        for fa in d["failed_attempts"]:
            print(f"    attempt {fa['n']} : {fa['failure_class']} - {fa['hint']}")
        integ = d["integrity"]
        integ_str = "ok" if integ == "ok" else json.dumps(integ, default=str)
        print(f"    integrity  : {integ_str}")
        if d.get("note"):
            print(f"    note       : {d['note']}")
        if d["gif"]:
            print(f"    gif        : {d['gif']} ({d['gif_result']})")
    total = len(demos)
    ok = sum(1 for d in demos if d["verdict"] == "COMPLETED")
    print("\n" + "-" * 60)
    print(f"RESULT: {'OK' if all_completed else 'gaps detected'} "
          f"({ok}/{total} COMPLETED)")


def cmd_game_demo_live(args) -> int:
    """`game demo --live`: generate + WATCH each prompt in a pygame window."""
    try:
        from harness.viewer import demo_live
    except Exception as exc:  # noqa: BLE001
        return _module_missing("viewer", exc, args.json)
    try:
        summary = demo_live(prompts=args.prompts, backend=args.backend)
    except RuntimeError as exc:   # pygame missing -> clean hint
        msg = str(exc)
        if args.json:
            _emit_json({"error": msg})
        else:
            print(msg, file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        return _call_error("game demo --live", exc, args.json)

    demos = summary.get("demos") or []
    all_completed = bool(demos) and all(d.get("verdict") == "COMPLETED" for d in demos)
    if args.json:
        _emit_json(summary)
    else:
        print(f"\n{'-' * 60}\nRESULT: {'OK' if all_completed else 'gaps detected'} "
              f"({sum(1 for d in demos if d.get('verdict') == 'COMPLETED')}/{len(demos)} "
              f"COMPLETED)")
    return 0 if all_completed else 1


def cmd_game_demo(args) -> int:
    """Live product demo: generate + verify + replay N prompts on the fly.

    Exit 0 iff every prompt reached COMPLETED. No LLM narration anywhere.
    With --live, each generated game is WATCHED in a pygame window instead of
    being baked to a GIF.
    """
    if getattr(args, "live", False):
        return cmd_game_demo_live(args)
    try:
        from harness.gen.gamegen import generate_game
    except Exception as exc:  # noqa: BLE001
        return _module_missing("gamegen", exc, args.json)
    try:
        from harness.render import replay_gif
    except Exception as exc:  # noqa: BLE001
        return _module_missing("render", exc, args.json)

    prompts = args.prompts or DEFAULT_DEMO_PROMPTS
    demos = [_demo_row(p, args.backend, generate_game, replay_gif) for p in prompts]
    all_completed = bool(demos) and all(d["verdict"] == "COMPLETED" for d in demos)

    if args.json:
        _emit_json({"all_completed": all_completed, "backend": args.backend,
                    "demos": demos})
        return 0 if all_completed else 1

    _print_demo(demos, args.backend, all_completed)
    return 0 if all_completed else 1


# ---- game curriculum -------------------------------------------------------
def cmd_game_curriculum(args) -> int:
    """Run up to K curriculum rounds: verify(tree) -> G3' -> profile -> directive
    -> (target? stop : apply the directive to the next version). ``--mode revise``
    (default) applies the directive as a minimal edit to the certified source;
    ``--mode regenerate`` designs a fresh game from PROMPT + directive. Prints one
    record per round."""
    try:
        from harness.gen.curriculum import curriculum_round
    except Exception as exc:  # noqa: BLE001
        return _module_missing("curriculum", exc, args.json)

    records = []
    current = args.game_path
    for _ in range(max(1, args.rounds)):
        try:
            rec = curriculum_round(current, backend=args.backend,
                                   budget_steps=args.budget, out_dir=args.out_dir,
                                   mode=args.mode)
        except Exception as exc:  # noqa: BLE001
            return _call_error("game curriculum", exc, args.json)
        records.append(rec)
        action = rec.get("action_taken")
        # A round advances the chain only when it produced a NEW certified game —
        # "revised" (revise mode) or "regenerated" (regenerate mode).
        if action not in ("revised", "regenerated") or not rec.get("new_game_path"):
            break                         # certified target, or nothing new to grade
        current = rec["new_game_path"]

    if args.json:
        _emit_json({"rounds": records})
        return 0

    for i, rec in enumerate(records, start=1):
        prof = rec.get("profile") or {}
        rl = prof.get("rl") or {}
        print(f"=== round {i} : {rec.get('game_path')} ===")
        print(f"  grade        : {rec.get('grade')}   action: {rec.get('action_taken')}")
        if rl:
            print(f"  learnability : success_rate {rl.get('success_rate')}   "
                  f"first_success {rl.get('steps_to_first_success')}   "
                  f"stalls_at {rl.get('stalling_milestone')}")
        if rec.get("new_game_path"):
            print(f"  next version : {rec['new_game_path']}")
        if rec.get("directive"):
            print("  directive    :")
            for line in str(rec["directive"]).splitlines():
                print(f"    {line}")
    return 0


# ---- ledger merge ------------------------------------------------------------
def cmd_ledger_merge(args) -> int:
    """Merge per-task cluster ledger shards into the canonical ledger."""
    try:
        from harness.core.telemetry import merge_shards
    except Exception as exc:  # noqa: BLE001
        return _module_missing("telemetry", exc, args.json)

    shards: list[str] = []
    for item in args.shards:
        p = Path(item)
        if p.is_dir():
            shards.extend(str(x) for x in sorted(p.glob("ledger.*.jsonl")))
        else:
            shards.append(item)
    summary = merge_shards(shards, into=args.into)
    if args.json:
        _emit_json(summary)
    else:
        print(f"merged {summary['shards']} shard(s), {summary['lines']} line(s): "
              f"{summary['appended']} appended, {summary['duplicates']} duplicate(s) "
              f"dropped, {summary['corrupt']} corrupt -> {args.into}")
    return 0


# ---- game stats ------------------------------------------------------------
def cmd_game_stats(args) -> int:
    """Aggregate the runs ledger (telemetry) per backend/model."""
    try:
        from harness.core.telemetry import stats
    except Exception as exc:  # noqa: BLE001
        return _module_missing("telemetry", exc, args.json)

    data = stats(args.path)
    if args.json:
        _emit_json(data)
        return 0 if data.get("total_runs") else 1

    if not data.get("total_runs"):
        print(f"no runs recorded in {args.path}")
        return 1

    print(f"=== RUN LEDGER ({args.path}: {data['total_runs']} runs) ===")
    header = (f"{'backend':<12} {'model':<32} {'runs':>4} {'done':>4} {'rate':>5} "
              f"{'inval':>5} {'avg_tries':>9} {'avg_s':>7}")
    print(header)
    print("-" * len(header))
    for g in data["groups"]:
        rate = f"{int(round(g['completion_rate'] * 100))}%"
        tries = g["mean_attempts_to_completed"]
        wall = g["mean_wall_s"]
        tries_s = "" if tries is None else f"{tries}"
        wall_s = "" if wall is None else f"{wall}"
        print(f"{str(g['backend']):<12} {str(g['model']):<32} {g['runs']:>4} "
              f"{g['completed']:>4} {rate:>5} {g['invalidated']:>5} "
              f"{tries_s:>9} {wall_s:>7}")
        if g["failure_classes"]:
            hist = ", ".join(f"{k}={v}" for k, v in sorted(g["failure_classes"].items()))
            print(f"{'':<12}   failure_classes : {hist}")
        if g["flagrant"]:
            hist = ", ".join(f"{k}={v}" for k, v in sorted(g["flagrant"].items()))
            print(f"{'':<12}   flagrant        : {hist}")
    return 0


# ---- parser --------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="harness",
        description="Text to a playable 2D environment, verified 100% by code.")
    sub = p.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="generate a scene from a text command")
    g.add_argument("command", help="natural-language command")
    g.add_argument("--backend", default="auto", choices=["auto", "anthropic", "template"])
    g.add_argument("--out-dir", default="scenes/generated")
    g.add_argument("--json", action="store_true")
    g.set_defaults(func=cmd_generate)

    v = sub.add_parser("verify", help="verify a scene (funnel L0->L2)")
    v.add_argument("scene_path")
    v.add_argument("--no-sandbox", action="store_true", help="run without subprocess")
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_verify)

    pl = sub.add_parser("play", help="play a scene with a policy")
    pl.add_argument("scene_path")
    pl.add_argument("--policy", default="greedy", choices=["greedy", "llm"])
    pl.add_argument("--max-steps", type=int, default=1200)
    pl.add_argument("--render", action="store_true", help="pygame or ASCII rendering")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_play)

    d = sub.add_parser("demo", help="full offline pipeline over scenes/examples/")
    d.add_argument("--render", action="store_true")
    d.add_argument("--no-sandbox", action="store_true")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_demo)

    # ---- v2: game group ----
    gm = sub.add_parser("game", help="v2 generated games: new / verify / replay")
    gmsub = gm.add_subparsers(dest="game_command", required=True)

    gn = gmsub.add_parser("new", help="generate a whole game from an open-ended prompt")
    gn.add_argument("prompt", help="open-ended natural-language prompt")
    gn.add_argument("--backend", default="auto",
                    choices=["auto", "anthropic", "openrouter", "template"])
    gn.add_argument("--engine", default=None, choices=["py", "js"],
                    help="physics engine: py (pymunk) or js (Planck.js); "
                         "default from HARNESS_ENGINE env, else py")
    gn.add_argument("--out-dir", default="scenes/games")
    gn.add_argument("--json", action="store_true")
    gn.set_defaults(func=cmd_game_new)

    gv = gmsub.add_parser("verify", help="run the universal oracles on a game")
    gv.add_argument("game_path")
    gv.add_argument("--no-sandbox", action="store_true", help="run without subprocess")
    gv.add_argument("--json", action="store_true")
    gv.set_defaults(func=cmd_game_verify)

    gr = gmsub.add_parser("replay",
                          help="replay a game's witness to a GIF and/or frames JSON")
    gr.add_argument("game_path")
    gr.add_argument("--gif", default=None, help="output GIF path (default: <game>.gif)")
    gr.add_argument("--frames", default=None,
                    help="persist the scrubbable replay substrate (every-frame "
                         "{meta,frames} JSON) to this path; if set without --gif, "
                         "only the JSON is written")
    gr.add_argument("--seed", type=int, default=0)
    gr.add_argument("--json", action="store_true")
    gr.set_defaults(func=cmd_game_replay)

    ga = gmsub.add_parser(
        "attack", help="adversarial G4 suite on a certified game (tier 0/1)")
    ga.add_argument("game_path")
    ga.add_argument("--tier", type=int, default=0, choices=[0, 1],
                    help="max tier: 0 (mechanical fuzz, no LLM) or 1 (adds the "
                         "cheap-LLM attacker lane; needs an OpenRouter key)")
    ga.add_argument("--json", action="store_true")
    ga.set_defaults(func=cmd_game_attack)

    gw = gmsub.add_parser("watch", help="watch a game play live in a pygame window")
    gw.add_argument("game_path")
    gw.add_argument("--speed", type=float, default=1.0,
                    help="real-time multiplier (2.0 = 2x, 0.5 = slow-mo)")
    gw.add_argument("--seed", type=int, default=0)
    gw.add_argument("--scale", type=float, default=1.0, help="window scale factor")
    gw.add_argument("--loop", action="store_true", help="restart the episode on end")
    gw.add_argument("--json", action="store_true")
    gw.set_defaults(func=cmd_game_watch)

    gd = gmsub.add_parser(
        "demo", help="live product demo: generate + verify + replay N prompts")
    gd.add_argument("--prompts", nargs="+", default=None,
                    help="one or more prompts (default: 3 built-in demo prompts)")
    gd.add_argument("--backend", default="auto",
                    choices=["auto", "anthropic", "openrouter", "template"])
    gd.add_argument("--live", action="store_true",
                    help="watch each game in a pygame window instead of baking GIFs")
    gd.add_argument("--json", action="store_true")
    gd.set_defaults(func=cmd_game_demo)

    gs = gmsub.add_parser("stats", help="aggregate the runs ledger (telemetry)")
    gs.add_argument("--path", default="runs/ledger.jsonl")
    gs.add_argument("--json", action="store_true")
    gs.set_defaults(func=cmd_game_stats)

    gc = gmsub.add_parser(
        "curriculum",
        help="run the difficulty-driven curriculum loop (verify -> G3' -> directive)")
    gc.add_argument("game_path")
    gc.add_argument("--budget", type=int, default=200_000,
                    help="G3' RL budget in env-steps per round (default 200k)")
    gc.add_argument("--rounds", type=int, default=1,
                    help="max curriculum rounds (default 1; stops early on a "
                         "target-certified game)")
    gc.add_argument("--backend", default="auto",
                    choices=["auto", "anthropic", "openrouter", "template"])
    gc.add_argument("--mode", default="revise", choices=["revise", "regenerate"],
                    help="how a non-target game is advanced: 'revise' (default) "
                         "applies the directive as a minimal edit to the certified "
                         "source; 'regenerate' designs a fresh game from PROMPT + "
                         "directive")
    gc.add_argument("--out-dir", default="scenes/games/curriculum",
                    help="where next-version games are written")
    gc.add_argument("--json", action="store_true")
    gc.set_defaults(func=cmd_game_curriculum)

    lg = sub.add_parser("ledger", help="run-ledger utilities (cluster shard merge)")
    lgsub = lg.add_subparsers(dest="ledger_command", required=True)
    lm = lgsub.add_parser(
        "merge",
        help="merge per-task ledger shards into the canonical ledger "
             "(dedupe on (game_id, seed, verdict_hash); idempotent)")
    lm.add_argument("shards", nargs="+",
                    help="shard files, or directories scanned for ledger.*.jsonl")
    lm.add_argument("--into", default="runs/ledger.jsonl",
                    help="canonical ledger to append into (default runs/ledger.jsonl)")
    lm.add_argument("--json", action="store_true")
    lm.set_defaults(func=cmd_ledger_merge)

    return p


def main(argv=None) -> int:
    # Windows console in cp1252: force UTF-8 for non-ASCII output.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
