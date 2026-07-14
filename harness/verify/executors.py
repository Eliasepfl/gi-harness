"""Episode executors — the engine seam under the v2 verifier funnel.

The rung-4 port makes Planck.js a first-class second engine. The clean seam
(SPIKE_REPORT.md "integration sketch") is an *episode executor*: the only place
that knows HOW an episode is computed. Everything above it — G1's determinism /
agency / efficacy checks, G3's witness search, dead-milestone and order
diagnostics — eats plain episode dicts and never touches the engine.

Two implementations share one surface:

- ``PyExecutor``  wraps the in-process §2 runner (``gameverify.run_episode``) +
  a ``World`` factory. Byte-identical to the pre-seam pymunk path: it uses the
  very same ``run_episode`` and helpers, so routing G1/G3 through it is a pure
  refactor.
- ``JsExecutor``  batches EVERY episode of a layer into ONE ``node
  nodeworld/runner.js`` process (amortising the ~70 ms cold start — the
  "batch all G3 episodes in one invocation" discipline), parses the JSONL back
  into the same dict shape, and also exposes a ``run_check`` job feeding the JS
  G0/G2 static+goal probes.

Common surface::

    run_batch(game_source, episodes, max_ticks, frames_every=0, escape_margin=None)
        -> list[episode_dict]

``episodes`` is ``[{"seed": int, "actions": [str|None, ...]}, ...]``. Each
returned dict matches ``run_episode``'s contract, keyed for cross-engine use:
``result / ticks / checkpoints / final_snapshot / actions`` plus ``frames`` (when
``frames_every>0``) and ``nan`` + ``oob`` (when ``escape_margin`` is a number —
the G1 rollout extras). ``.batched`` tells G3 whether to stream per-episode (Py,
early-stop) or run one batch (Js).

Engine/infra failures (node missing, crash, timeout, unparseable output) raise
``VerifyError``; ``.as_report()`` yields the VERIFY_ERROR-shaped dict the repair
loop already recognises (``{"error": {...}}`` with no ``layers``).
"""

from __future__ import annotations

import json
import os
import subprocess


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class VerifyError(Exception):
    """An engine/infra failure while running a batch (NOT a game failure).

    Shaped so the caller can surface it exactly like ``sandbox.run_sandboxed``'s
    trouble reports and ``gamegen._is_verify_error`` (``{"error": {...}}`` with no
    ``layers`` key).
    """

    def __init__(self, kind: str, message: str, **extra):
        self.kind = kind
        self.message = message
        self.extra = extra
        super().__init__(f"{kind}: {message}")

    def as_report(self) -> dict:
        err = {"type": self.kind, "message": self.message}
        err.update(self.extra)
        return {"error": err}


def _repo_root() -> str:
    """Repo root = grandparent of this module's package dir (harness/verify/)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def default_runner_path() -> str:
    return os.path.join(_repo_root(), "nodeworld", "runner.js")


# ---------------------------------------------------------------------------
# Python (in-process pymunk) executor
# ---------------------------------------------------------------------------
class PyExecutor:
    """In-process executor over the real ``run_episode`` + a ``World`` factory.

    ``world_factory(seed=...) -> World`` defaults to ``gameverify``'s real World;
    tests inject a ``FakeWorld`` factory. Reuses ``gameverify`` helpers verbatim,
    so the produced episode dicts (and hence every G1/G3 check) are byte-for-byte
    what the pre-seam funnel produced.
    """

    batched = False  # per-episode capable, no process spawn -> G3 streams + early-stops

    def __init__(self, world_factory=None):
        self._factory = world_factory

    def _factory_or_default(self):
        if self._factory is not None:
            return self._factory
        from harness.verify.gameverify import _default_world_factory
        return _default_world_factory

    @staticmethod
    def _as_game(game_source):
        # Accept a pre-loaded Game (verifier funnel) or raw source (parity tests).
        if isinstance(game_source, str):
            from harness.verify.gameverify import load_game
            return load_game(game_source)
        return game_source

    def run_batch(self, game_source, episodes, max_ticks, frames_every=0,
                  escape_margin=None) -> list[dict]:
        from harness.verify.gameverify import (
            NAN_EVENT_TYPES, _dynamic_entities, _safe_events, _truthy,
            _world_size_of, run_episode,
        )
        game = self._as_game(game_source)
        factory = self._factory_or_default()
        size = _world_size_of(game)
        out: list[dict] = []
        for ep in episodes:
            seed = int(ep.get("seed", 0))
            actions = list(ep.get("actions", []))
            world = factory(seed=seed, size=size) if size else factory(seed=seed)
            game.build(world)
            if frames_every and frames_every > 0:
                rec = _run_episode_with_frames(game, world, actions, max_ticks,
                                               frames_every)
            else:
                rec = run_episode(game, world, iter(actions), max_ticks)
            item = {
                "result": rec["result"],
                "ticks": rec["ticks"],
                "checkpoints": dict(rec.get("checkpoints", {})),
                "final_snapshot": rec.get("snapshot", {}),
                "actions": rec.get("actions", actions[:rec["ticks"]]),
                "world_size": list(getattr(world, "size", (800, 600))),
                "error": rec.get("error"),
            }
            if "frames" in rec:
                item["frames"] = rec["frames"]
            if escape_margin is not None:
                # Read exactly as the pre-seam run_g1 did, on the SAME post-rollout
                # world (events for NaN; dynamic bodies failing in_bounds(margin)).
                item["nan"] = any(
                    e.get("type") in NAN_EVENT_TYPES for e in _safe_events(world))
                item["oob"] = [
                    n for n in _dynamic_entities(world)
                    if not _truthy(lambda n=n: world.in_bounds(n, escape_margin))]
            out.append(item)
        return out


def _run_episode_with_frames(game, world, actions, max_ticks, frames_every) -> dict:
    """A frame-capturing §2 rollout (only for the render adapter; verify uses the
    plain ``run_episode``). Mirrors its decision-tick semantics and adds a
    ``frames`` list of ``{tick, entities:{name: query}}`` snapshots."""
    from harness.verify.gameverify import K_STEPS, _safe_snapshot, _safe_steps
    import traceback

    applied: list = []
    latches: dict = {}
    frames: list = []

    def frame_of():
        ents = {}
        for name in world.entities():
            try:
                ents[name] = dict(world.query(name))
            except Exception:
                pass
        return ents

    frames.append({"tick": 0, "entities": frame_of()})
    result = "budget"
    it = iter(actions)
    for _ in range(max_ticks):
        try:
            action = next(it)
        except StopIteration:
            result = "exhausted"
            break
        try:
            applied.append(action)
            if action is not None:
                game.act(world, action)
            for _s in range(K_STEPS):
                world.step(1)
                if game.on_step is not None:
                    game.on_step(world)
            if game.checkpoints is not None:
                for key, value in game.checkpoints(world).items():
                    latches.setdefault(key, None)
                    if latches[key] is None and value:
                        latches[key] = len(applied)
            if len(applied) % frames_every == 0:
                frames.append({"tick": len(applied), "entities": frame_of()})
            if game.failure is not None and game.failure(world):
                result = "failure"
                break
            if bool(game.success(world)):
                result = "success"
                break
        except Exception:
            return {"result": "error", "ticks": len(applied),
                    "steps": _safe_steps(world), "actions": applied,
                    "snapshot": _safe_snapshot(world), "checkpoints": latches,
                    "frames": frames, "error": traceback.format_exc(limit=4)}
    if not frames or frames[-1]["tick"] != len(applied):
        frames.append({"tick": len(applied), "entities": frame_of()})
    return {"result": result, "ticks": len(applied), "steps": _safe_steps(world),
            "actions": applied, "snapshot": _safe_snapshot(world),
            "checkpoints": latches, "frames": frames, "error": None}


# ---------------------------------------------------------------------------
# JavaScript (Planck.js in Node) executor
# ---------------------------------------------------------------------------
class JsExecutor:
    """Out-of-process executor spawning ``node nodeworld/runner.js`` per batch.

    ONE node process runs the whole layer's episodes (amortising cold start).
    stdout is JSONL — one episode record per line, in order. ``run_check`` runs
    the runner's static+goal ("check") mode feeding the JS G0/G2 layers.
    """

    batched = True  # one node process per batch -> G3 runs the batch, no early-stop

    def __init__(self, node: str | None = None, runner_path: str | None = None,
                 timeout_s: float = 60.0):
        self.node = node or os.environ.get("HARNESS_NODE", "node")
        self.runner_path = runner_path or default_runner_path()
        self.timeout_s = timeout_s

    # -- process plumbing --------------------------------------------------
    def _run_node(self, job: dict) -> str:
        if not os.path.isfile(self.runner_path):
            raise VerifyError("node_runner_missing",
                              f"runner.js not found at {self.runner_path}")
        payload = json.dumps(job)
        try:
            proc = subprocess.run(
                [self.node, self.runner_path], input=payload,
                capture_output=True, text=True, encoding="utf-8",
                timeout=self.timeout_s)
        except FileNotFoundError:
            raise VerifyError("node_missing",
                              f"node executable not found: {self.node!r}")
        except subprocess.TimeoutExpired:
            # subprocess.run has already killed the child on timeout.
            raise VerifyError("js_timeout",
                              f"node runner exceeded {self.timeout_s}s",
                              timeout_s=self.timeout_s)
        if proc.returncode != 0:
            # The runner reports episode-level errors in-band and exits 0; a
            # non-zero code means node itself crashed (bad install, OOM, ...).
            stderr = (proc.stderr or "").strip()[:500]
            raise VerifyError("js_crash",
                              f"node exit {proc.returncode}: {stderr}")
        return proc.stdout

    # -- episode batch -----------------------------------------------------
    def run_batch(self, game_source, episodes, max_ticks, frames_every=0,
                  escape_margin=None) -> list[dict]:
        specs = [{"seed": int(e.get("seed", 0)), "actions": list(e.get("actions", []))}
                 for e in episodes]
        job = {"mode": "episodes", "source": game_source, "episodes": specs,
               "max_ticks": int(max_ticks), "frames_every": int(frames_every or 0)}
        if escape_margin is not None:
            job["escape_margin"] = float(escape_margin)
        out = self._run_node(job)

        lines = [ln for ln in out.splitlines() if ln.strip()]
        if len(lines) != len(specs):
            raise VerifyError(
                "js_bad_output",
                f"expected {len(specs)} episode line(s), got {len(lines)}")
        recs: list[dict] = []
        for spec, line in zip(specs, lines):
            try:
                rec = json.loads(line)
            except ValueError as exc:
                raise VerifyError("js_bad_output", f"unparseable JSONL: {exc}")
            # The runner does not echo actions; attach the applied prefix so the
            # dict matches run_episode's contract (G3 reads ep["actions"]).
            ticks = int(rec.get("ticks", 0))
            rec.setdefault("actions", spec["actions"][:ticks])
            recs.append(rec)
        return recs

    # -- static + goal check (G0/G2 facts) --------------------------------
    def run_check(self, game_source) -> dict:
        out = self._run_node({"mode": "check", "source": game_source})
        line = next((ln for ln in out.splitlines() if ln.strip()), "")
        try:
            obj = json.loads(line)
        except ValueError as exc:
            raise VerifyError("js_bad_output", f"unparseable check output: {exc}")
        if obj.get("error"):
            raise VerifyError("js_check_fatal", str(obj["error"]))
        return obj


# ---------------------------------------------------------------------------
# Render adapter — a JS game replayed to a GIF via render.py's frame primitives
# ---------------------------------------------------------------------------
class _FrameWorld:
    """A read-only stand-in exposing the ``world.query`` surface render.py draws
    from, backed by ONE captured frame's per-entity query dicts (physics ran in
    Node). render.py never mutates it — it only reads entities()/query()."""

    def __init__(self, entities: dict, size=(800, 600)):
        self._ents = entities
        self.size = size

    def entities(self):
        return list(self._ents)

    def query(self, name):
        return self._ents[name]


def render_js_replay(game_source, out_path, *, actions, seed: int = 0, label=None,
                     max_ticks: int = 400, scale: float = 0.6, every: int = 2,
                     node=None, runner_path=None, timeout_s: float = 60.0) -> dict:
    """Render a JS game end-to-end via the executor's frames path to a GIF.

    Node computes the physics and emits ``frames`` (tick + per-entity query dicts,
    exactly the format render.py consumes); this adapter draws each with render's
    ``_render_frame`` and saves the GIF — reusing render.py WITHOUT touching it.
    """
    from harness import render  # PIL-only; imported lazily

    ex = JsExecutor(node=node, runner_path=runner_path, timeout_s=timeout_s)
    every = max(1, int(every))
    recs = ex.run_batch(game_source, [{"seed": seed, "actions": list(actions)}],
                        max_ticks, frames_every=every)
    ep = recs[0]
    if ep.get("result") == "error":
        return {"ticks": ep.get("ticks", 0), "result": "error",
                "error": ep.get("error")}

    frames_data = ep.get("frames", [])
    world_size = tuple(ep.get("world_size") or (800, 600))
    cam = render.FollowCamera(world_size)
    imgs = []
    for fr in frames_data:
        ents = fr.get("entities", {})
        target = next((tuple(q["pos"]) for q in ents.values()
                       if q.get("controlled")), None)
        fw = _FrameWorld(ents, world_size)
        imgs.append(render._render_frame(fw, fr.get("tick", 0), label or "",
                                         scale, world_size,
                                         camera=cam.update(target)))
    if not imgs:
        return {"ticks": ep.get("ticks", 0), "result": ep.get("result"),
                "frames": 0, "error": "no frames emitted"}
    imgs.extend([imgs[-1]] * render.HOLD_FRAMES)
    render._save_gif(imgs, out_path)
    return {"ticks": ep.get("ticks", 0), "result": ep.get("result"),
            "frames": len(imgs), "out_path": str(out_path)}


# ---------------------------------------------------------------------------
# Frames substrate — persist a scrubbable replay for the web canvas player
# ---------------------------------------------------------------------------
def _round_floats(obj, dp: int = 2):
    """Recursively round every float to ``dp`` decimals (shrinks the JSON without
    changing structure). Booleans/ints/strings/None pass through untouched."""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        return round(obj, dp)
    if isinstance(obj, list):
        return [_round_floats(v, dp) for v in obj]
    if isinstance(obj, dict):
        return {k: _round_floats(v, dp) for k, v in obj.items()}
    return obj


def replay_frames_doc(game_source, *, engine, actions, witness=None, seed: int = 0,
                      max_ticks: int = 400, round_dp: int = 2, node=None,
                      runner_path=None, timeout_s: float = 60.0) -> dict:
    """Run ONE every-frame episode and assemble the replay SUBSTRATE document::

        {"meta": {"title", "prompt", "world_size", "engine",
                  "witness": {"seed", "ticks", "checkpoints"}},
         "frames": [{"tick", "entities": {name: query-dict}}, ...]}

    Engine-agnostic: js goes through the Node runner (which now echoes
    title/prompt in a frames record), py through the in-process PyExecutor (title/
    prompt are harvested from the loaded game). ``actions`` is the witness plan
    replayed on ``seed`` (the world seed the frames are generated on); ``witness``
    (from a fresh verify) supplies ticks + checkpoints for the meta. Floats are
    rounded to ``round_dp`` decimals. Returns the document plus ``result`` /
    ``error`` keys describing the replay's terminal classification."""
    if engine == "js":
        ex = JsExecutor(node=node, runner_path=runner_path, timeout_s=timeout_s)
    else:
        ex = PyExecutor()
    recs = ex.run_batch(game_source, [{"seed": int(seed), "actions": list(actions)}],
                        int(max_ticks), frames_every=1)
    rec = recs[0]

    title = rec.get("title")
    prompt = rec.get("prompt")
    if title is None or prompt is None:  # py path: harvest from the loaded game
        try:
            from harness.verify.gameverify import load_game
            game = game_source if not isinstance(game_source, str) else load_game(game_source)
            title = getattr(game, "title", None) if title is None else title
            prompt = getattr(game, "prompt", None) if prompt is None else prompt
        except Exception:  # noqa: BLE001 — meta is cosmetic, never break the export
            pass

    witness = witness or {}
    meta = {
        "title": title or "",
        "prompt": prompt or "",
        "world_size": [int(x) if float(x).is_integer() else x
                       for x in (rec.get("world_size") or (800, 600))],
        "engine": engine,
        "witness": {
            "seed": int(seed),
            "ticks": witness.get("ticks", rec.get("ticks")),
            "checkpoints": dict(witness.get("checkpoints") or {}),
        },
    }
    frames = _round_floats(rec.get("frames", []), int(round_dp))
    return {"meta": meta, "frames": frames,
            "result": rec.get("result"), "error": rec.get("error")}
