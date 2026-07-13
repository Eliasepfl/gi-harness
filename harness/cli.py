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
        from harness.generator import generate
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
        from harness.verifier import verify_scene
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
        from harness.navigator import navigate
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
        from harness.verifier import verify_scene
    except Exception:  # noqa: BLE001
        verify_scene = None
    try:
        from harness.navigator import navigate
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
        from harness.gamegen import generate_game
    except Exception as exc:  # noqa: BLE001
        return _module_missing("gamegen", exc, args.json)

    try:
        result = generate_game(args.prompt, out_dir=args.out_dir, backend=args.backend)
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
        from harness.gameverify import verify_game
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


def cmd_game_replay(args) -> int:
    """Replay a generated game (using its witness) to an animated GIF."""
    try:
        from harness.render import replay_gif
    except Exception as exc:  # noqa: BLE001
        return _module_missing("render", exc, args.json)

    gif = args.gif or str(Path(args.game_path).with_suffix(".gif"))
    result = replay_gif(args.game_path, gif, seed=args.seed)
    if args.json:
        _emit_json(result)
    else:
        if result.get("result") == "error":
            print(f"ERROR  {args.game_path}", file=sys.stderr)
            print(f"  {result.get('error')}", file=sys.stderr)
        else:
            print(f"{str(result.get('result')).upper()}  {args.game_path}")
            print(f"  ticks : {result.get('ticks')}   gif : {result.get('out_path')}")
    return 0 if result.get("result") in ("success", "failure", "timeout") else 1


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
    gn.add_argument("--backend", default="auto", choices=["auto", "anthropic", "template"])
    gn.add_argument("--out-dir", default="scenes/games")
    gn.add_argument("--json", action="store_true")
    gn.set_defaults(func=cmd_game_new)

    gv = gmsub.add_parser("verify", help="run the universal oracles on a game")
    gv.add_argument("game_path")
    gv.add_argument("--no-sandbox", action="store_true", help="run without subprocess")
    gv.add_argument("--json", action="store_true")
    gv.set_defaults(func=cmd_game_verify)

    gr = gmsub.add_parser("replay", help="replay a game's witness to a GIF")
    gr.add_argument("game_path")
    gr.add_argument("--gif", default=None, help="output GIF path (default: <game>.gif)")
    gr.add_argument("--seed", type=int, default=0)
    gr.add_argument("--json", action="store_true")
    gr.set_defaults(func=cmd_game_replay)

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
