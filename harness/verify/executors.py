"""Episode executors — the engine seam under the v2 verifier funnel.

The clean seam (SPIKE_REPORT.md "integration sketch") is an *episode executor*:
the only place that knows HOW an episode is computed. Everything above it — G1's
determinism / agency / efficacy checks, G3's witness search, dead-milestone and
order diagnostics — eats plain episode dicts and never touches the engine.

The executors live in their own modules (``GodotExecutor`` in ``godot_exec`` for
the declarative-spec lane, ``GdExecutor`` in ``gd_exec`` for the GDScript GameAPI
lane); this module holds the shared error type, the render/replay adapters, and
the frames-substrate document builder, and re-exports ``GodotExecutor`` so the
funnel imports its engines from one place.

Common executor surface::

    run_batch(game_source, episodes, max_ticks, frames_every=0, escape_margin=None)
        -> list[episode_dict]

``episodes`` is ``[{"seed": int, "actions": [str|None, ...]}, ...]``. Each
returned dict is keyed for cross-engine use: ``result / ticks / checkpoints /
final_snapshot / actions`` plus ``frames`` (when ``frames_every>0``) and ``nan``
+ ``oob`` (when ``escape_margin`` is a number — the G1 rollout extras).

Engine/infra failures (missing engine, crash, timeout, unparseable output) raise
``VerifyError``; ``.as_report()`` yields the VERIFY_ERROR-shaped dict the repair
loop already recognises (``{"error": {...}}`` with no ``layers``).
"""

from __future__ import annotations

import json


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


# ---------------------------------------------------------------------------
# Render adapter — an engine replay drawn to a GIF via render.py's frame primitives
# ---------------------------------------------------------------------------
class _FrameWorld:
    """A read-only stand-in exposing the ``world.query`` surface render.py draws
    from, backed by ONE captured frame's per-entity query dicts (physics ran in
    the engine). render.py never mutates it — it only reads entities()/query()."""

    def __init__(self, entities: dict, size=(800, 600)):
        self._ents = entities
        self.size = size

    def entities(self):
        return list(self._ents)

    def query(self, name):
        return self._ents[name]


def _episode_frames_to_gif(ep, out_path, *, label=None, scale: float = 0.6) -> dict:
    """Draw an executor episode record's ``frames`` (tick + per-entity query
    dicts, exactly the format render.py consumes) to a GIF via render's
    ``_render_frame``, following the controlled body with a ``FollowCamera``.

    Engine-agnostic: the godot and gdscript GIF adapters share it (godot's bbox-only
    entities fall back to the axis-aligned shape inside render). Reuses render.py
    WITHOUT touching it. Returns the CLI result dict (``ticks``/``result``/
    ``frames``/``out_path``, or an ``error`` on a failed/empty replay)."""
    from harness import render  # PIL-only; imported lazily

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


def render_godot_replay(game_source, out_path, *, actions, seed: int = 0, label=None,
                        max_ticks: int = 400, scale: float = 0.6, every: int = 2,
                        timeout_s: float = 120.0) -> dict:
    """Render a Godot-spec game to a GIF via the executor's frames path.

    GodotExecutor computes the physics headlessly and emits ``frames`` in the same
    shape render.py consumes (``bbox`` + ``shape`` per entity); the flat bbox
    fallback in render covers the ``verts`` / ``radius`` the Godot frames omit, so
    the renderer stays untouched.
    """
    ex = GodotExecutor(timeout_s=timeout_s)
    every = max(1, int(every))
    recs = ex.run_batch(game_source, [{"seed": int(seed), "actions": list(actions)}],
                        max_ticks, frames_every=every)
    return _episode_frames_to_gif(recs[0], out_path, label=label, scale=scale)


# A marker half-extent (world units) for the bbox synthesised below.
_GD_MARKER_R = 12.0


def _synthesize_gd_bboxes(rec: dict) -> dict:
    """The GameAPI (.gd) serve frames carry pos/vel/angle/controlled/static but NO
    shape geometry (``godotworld/GAME_API.md`` — no bbox/verts). render.py draws
    only entities with a ``bbox`` (skipping the rest), so for the GIF we synthesise
    a small axis-aligned marker box around each entity's position — enough to show
    the bodies MOVING (Elias: "any recording proving the game works"). Mutates rec
    in place; the frames-JSON substrate keeps the geometry-free shape."""
    for fr in rec.get("frames", []) or []:
        for q in (fr.get("entities") or {}).values():
            if q.get("bbox"):
                continue
            pos = q.get("pos")
            if not isinstance(pos, (list, tuple)) or len(pos) < 2:
                continue
            x, y = float(pos[0]), float(pos[1])
            q["bbox"] = [x - _GD_MARKER_R, y - _GD_MARKER_R,
                         x + _GD_MARKER_R, y + _GD_MARKER_R]
            q.setdefault("shape", "circle")
    return rec


def render_gdscript_replay(game_source, out_path, *, actions, seed: int = 0, label=None,
                           max_ticks: int = 400, scale: float = 0.6, every: int = 2,
                           timeout_s: float = 120.0) -> dict:
    """Render a generated .gd (GameAPI) game to a GIF — the gdscript twin of
    ``render_godot_replay``.

    The serve host computes the physics headlessly and emits positional frames; the
    GameAPI lane exposes no shape geometry, so ``_synthesize_gd_bboxes`` fabricates
    a per-entity marker box before drawing (render.py stays untouched). The result
    is a positional recording proving the game plays — not a faithful skin."""
    from harness.verify.gd_exec import GdExecutor
    ex = GdExecutor(timeout_s=timeout_s)
    every = max(1, int(every))
    try:
        recs = ex.run_batch(game_source, [{"seed": int(seed), "actions": list(actions)}],
                            max_ticks, frames_every=every)
    finally:
        ex.close()
    return _episode_frames_to_gif(_synthesize_gd_bboxes(recs[0]), out_path,
                                  label=label, scale=scale)


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


def normalize_godot_record(rec, source):
    """Map a ``GodotExecutor`` episode record + its spec source onto the engine-
    agnostic replay-doc pieces ``(title, prompt, frames)``.

    Godot's ``runner.gd`` emits ``world_size`` on the record but not ``title`` /
    ``prompt`` (harvested here from the spec's ``meta`` block), and its per-frame
    entities already carry the shared query shape (``pos``/``vel``/``angle``/
    ``bbox``/``shape``/``static``/``sensor``/``controlled``) — the renderer and
    the web player fall back to the axis-aligned ``bbox`` for the ``verts`` /
    ``radius`` the Godot frames omit, so no field synthesis is needed. Frames are
    stripped to the shared ``{tick, entities}`` shape (dropping any sensor
    ``obs`` tail the runner appends for observation specs)."""
    title = prompt = None
    try:
        meta = (json.loads(source) or {}).get("meta") or {}
        title, prompt = meta.get("title"), meta.get("prompt")
    except (ValueError, TypeError, AttributeError):
        pass  # meta is cosmetic, never break the export
    frames = [{"tick": fr.get("tick", 0), "entities": fr.get("entities", {})}
              for fr in (rec.get("frames") or [])]
    return title, prompt, frames


def replay_frames_doc(game_source, *, engine, actions, witness=None, seed: int = 0,
                      max_ticks: int = 400, round_dp: int = 2,
                      timeout_s: float = 60.0) -> dict:
    """Run ONE every-frame episode and assemble the replay SUBSTRATE document::

        {"meta": {"title", "prompt", "world_size", "engine",
                  "witness": {"seed", "ticks", "checkpoints"}},
         "frames": [{"tick", "entities": {name: query-dict}}, ...]}

    Engine-agnostic: godot goes through the out-of-process GodotExecutor (title/
    prompt harvested from the spec ``meta`` via ``normalize_godot_record``), and
    gdscript through the serve-host GdExecutor (no title/prompt in the GameAPI
    contract -> blank meta). ``actions`` is the witness plan replayed on ``seed``
    (the world seed the frames are generated on); ``witness`` (from a fresh verify)
    supplies ticks + checkpoints for the meta. Floats are rounded to ``round_dp``
    decimals. Returns the document plus ``result`` / ``error`` keys describing the
    replay's terminal classification."""
    gd_ex = None
    if engine == "godot":
        ex = GodotExecutor(timeout_s=timeout_s)
    elif engine == "gdscript":
        from harness.verify.gd_exec import GdExecutor
        ex = gd_ex = GdExecutor(timeout_s=timeout_s)
    else:
        raise VerifyError("unsupported_engine",
                          f"engine {engine!r} is not supported (only godot / gdscript)")
    try:
        recs = ex.run_batch(game_source, [{"seed": int(seed), "actions": list(actions)}],
                            int(max_ticks), frames_every=1)
    finally:
        if gd_ex is not None:  # the serve host is a persistent process — tear it down
            gd_ex.close()
    rec = recs[0]

    if engine == "godot":
        title, prompt, frames_raw = normalize_godot_record(rec, game_source)
    else:  # gdscript
        # The .gd serve frames already carry the shared {tick, entities:{query}}
        # shape; title/prompt are not part of the GameAPI contract -> blank meta.
        title = prompt = None
        frames_raw = rec.get("frames", [])

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
    frames = _round_floats(frames_raw, int(round_dp))
    return {"meta": meta, "frames": frames,
            "result": rec.get("result"), "error": rec.get("error")}


# ---------------------------------------------------------------------------
# Godot (declarative-spec) executor — third engine seam. Imported at the bottom
# so godot_exec (which references VerifyError lazily) never forms an import cycle.
# ---------------------------------------------------------------------------
from harness.verify.godot_exec import (  # noqa: E402,F401
    GodotExecutor, find_godot_exe, default_godot_project,
    stepping_argv, speedup_from_env, speedup_user_args, _dotgodot_present,
)
