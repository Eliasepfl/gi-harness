# MATERIAL REALITY — a goal in space is a thing, not a coordinate

> Wave 3 non-vacuity rule, 2026-07-16. Contract lives in
> `harness/gen/prompts/api_gdscript.md` (the section between STAKES and Visuals);
> the advisory gate is `gameverify._anchoring_gate`; the feedback bridge is
> `feedback.anchoring_finding` + `feedback._compile_anchoring`. Companion notes:
> `notes/engines/DEAD_SPACE_GATE.md` (the PROPORTION twin this mirrors),
> `notes/engines/DEMO_GAP_ANALYSIS.md`.

## The rule

Any milestone or win defined by WHERE something is must be anchored to a REAL node with a
collision shape — a body or an area the game `add_child`s in `build()` and reports in
`state()` — and latched off that node's overlap / contact / position, never off a bare
coordinate checked with distance math. A goal that is only arithmetic is invisible: it can
be memorised, never seen or drawn. Milestones NOT defined by a place (a time or motion
condition) need no anchor and are explicitly exempt.

This is the spatial-milestone twin of STAKES's `is_failure` rule: a section that gives a
bare signature (`checkpoints()` / `is_success()`) semantic teeth, so the two non-vacuity
rules cluster in the prompt.

## What ships now (advisory, non-gating)

The gate runs LAST on an already-certified GDScript game, parallel to the PRESSURE and
PROPORTION gates:

* replays the certified witness with `frames_every=1` (exactly as the G3 solidity replay),
  giving per-tick body POSITIONS;
* joins those with the t=0 CHECK geometry (each body's self-reported extent + sensor flag)
  by body name;
* for each latched milestone (and the win) computes `D` = the min, over the flip tick and
  the tick before it, of the controlled body's SURFACE distance (center distance minus both
  self-reported extents) to any non-controlled reported body;
* tolerance = `max(ANCHOR_TOL_FLOOR, ANCHOR_TOL_FRAC × occupancy-bounds diagonal)`, reusing
  the dead-space gate's playfield box (`reachability.space_utilization`). Numbers live in
  code as EVIDENCE thresholds — never in the contract;
* a milestone is `unanchored` iff `D > tolerance`.

Plumbing (mirrors PRESSURE/PROPORTION exactly): a NON-gating `material_anchoring` sub-check
under `G3_solve` (`advisory=True`, `pass=True` ALWAYS; the real signal is the `anchored`
bool); on a flip in empty space a `report["anchoring"]` stash + one `"ANCHORING: "` warning;
one directive PER offending milestone (`checkpoint_keys=(milestone,)`, so `_fingerprint`
dedups per-milestone and the convergence guard catches a stalled repair).

`report["passed"]` / `failure_class` are untouched either way: a certified game stays
certified, the existing library (its 10 ghost-goal games already certified) is never
re-flipped, and the gate bites only on NEW generations via the REVISE directive.

This is a NECESSARY-NOT-SUFFICIENT check — the same epistemic class as PRESSURE/PROPORTION —
which is exactly why it ships advisory, not fatal. Known blind spots (all bounded by the
advisory status): the pen class (a ghost Rect2 zone RINGED by real walls, e.g. a herd /
parking pad) flips near incidental bodies and passes; a game that reports a PHANTOM body at
the goal coordinate (a `state()` entry with no real node) passes; a large goal body latched
at its rim can exceed the center-distance tolerance (false positive); a legal non-spatial
milestone that flips in open space WILL be flagged (the escape clause in the hint tells the
REVISE model to leave it alone). The contract text still binds every one of these; only the
CODE enforcement is partial.

## DEFERRED — what the serve host must emit (do NOT implement here)

The center-proximity check is the strongest thing computable from TODAY's wire. True
geometric OVERLAP, anchoring to ANY Area (not just self-listed ones), and proof that a
reported "body" actually owns a collision shape all require the host to widen the wire.
Per-tick frames (`serve_game.gd::_body_obs_json`) carry only `{pos, vel, angle, controlled,
static}`; the host never enumerates Areas onto the wire (`_collect_collision_objects` is
ray-only); and `_geometry_of` reports only what the GAME self-lists.

Follow-up (a PURE-ADD wire change, gated so the un-requested wire stays byte-identical — the
G1 twin-rollout identity precedent is the geometry key at `serve_game.gd::_geometry_of`):

1. **Authoritative per-body extents** in `_geometry_of` — the host reads the actual
   `CollisionShape2D`/`CollisionShape3D` on each body and emits its true half-extents / radius
   (today the check trusts the game's self-reported footprint, defaulting to 0 when absent).
   `reachability._aabb_of` already consumes host-emitted extents, so this drops in.
2. **A `frames_every`-gated per-tick contact/overlap set** — for each captured frame, the
   host emits `get_overlapping_bodies()` / `get_overlapping_areas()` (and contact pairs) so
   the gate can test REAL overlap at the flip instead of center proximity, and can credit an
   Area anchor the game did not think to self-list.

Both MUST be gated behind the existing frames/geometry request so a run that does not ask for
them sees a byte-identical wire (protecting G1 determinism + witness replay). This phase makes
ZERO host changes on purpose. The extents follow-up (1) is already queued separately; reference
it, do not re-open it here. Once (2) lands, add a `frames_every=0` byte-identity regression for
the new fields (the determinism net for the new wire).

## Rollout monitor (not a unit test)

Compare the goal-mechanism distribution of the next generation batch against the survey
baseline (7 area-overlap / 3 body-contact / 10 ghost / 1 mixed / 0 sole-non-spatial):
success is ghosts → ~0 WITHOUT area-overlap swallowing the body-contact and non-spatial
mechanisms (the class re-bias risk the 2026-07-16 de-bias targets). Watch the convergence
guard for stalled loops keyed on the same milestone (bred children inherit parent ghosts
until the parents are hardened — expected repair pressure, not a bug).
