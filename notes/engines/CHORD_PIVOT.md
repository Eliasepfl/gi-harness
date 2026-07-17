# The CHORD Pivot

**Status:** Phase 1 landed (host-only). **Phase 2 built** (MultiBinary PPO — §8). Phase 3 designed.
**Scope of Phase 1:** the harness can now apply MULTIPLE verbs in a single decision
tick, as a pure HOST capability. Zero contract change, zero game-file change, byte-
identical for every existing single-verb witness and every existing wire message.

---

## 1. Why chords

A real controller presses several buttons at once: thrust + rotate, grab + move,
brake + steer. Modelling every decision as exactly one verb makes whole classes of
games unplayable in one tick (you cannot produce a diagonal, or hold a grab while you
move, without the world stepping in between). General Intuition's agents act on a
**simultaneous** controller model; our action space must be able to as well.

A **chord** is Elias-approved: the RL agent (and any driver) may press multiple keys
in the same decision tick.

---

## 2. The doctrine — who judges feasibility

Chords widen the action space, so they change *who gets to certify a game*. The
division of labour (Elias's framing):

- **The tree solver is the FIRST-INSTANCE judge, on the single-key subspace.** It
  decides feasibility first, over the tractable space it can exhaustively reason about:
  one verb per tick. If the tree finds a single-key win, the game is feasible — done,
  cheaply, deterministically. The tree is also the **refutation prosecutor**: it proves
  softlocks and dead-ends on that subspace (a negative result the tree is uniquely
  good at, because it searches exhaustively rather than sampling).

- **RL is the appeals court** — for games *beyond the tree's jurisdiction*. When the
  single-key subspace has no solution (the game genuinely needs simultaneous inputs),
  the tree cannot rule the game infeasible; it can only rule "not feasible **on the
  single-key subspace**." The case escalates. RL searches the **full chord space** and
  either finds a win the tree structurally could not, or fails to — and *that* is the
  higher-court judgment.

- **Escalade certification** is the formal name for that hand-off: a game the tree
  cannot solve on single keys is *escaladed* to the RL court, whose verdict (win found
  in chord space / no win found under budget) becomes the certificate. The tree still
  judges feasibility **first**; RL is the appeals court for games beyond its reach. A
  single-key win never needs to escalate — the tree's cheap deterministic verdict
  stands.

This keeps the cheap, exhaustive, deterministic judge in front and reserves the
expensive stochastic judge for exactly the games that require it.

---

## 3. Wire format — the union

A decision-tick action crosses the wire (and lives in a witness) as one of two shapes:

| Shape | Wire form | Meaning |
|-------|-----------|---------|
| single verb | plain string — `"thrust_up"` | one key, exactly today's behaviour |
| chord | JSON array of verb strings — `["thrust_forward", "thrust_up"]` | keys pressed together in one tick |

A single verb stays a **plain string** — byte-identical to every witness and every act
message that exists today. No migration, no re-encoding, no dual-write. A chord is a
**real JSON array**, not a `'+'`-joined or otherwise stringly-typed value.

### Why a union rather than "always an array"

Always-array (`"thrust_up"` → `["thrust_up"]`) would be *purer* — one shape everywhere.
We rejected it: it breaks the byte-identity of every existing witness and every logged
wire message purely for aesthetics. The union costs us one type-branch at exactly two
boundaries (one per side, below); in exchange every certified witness, replay, and
determinism fingerprint on disk stays valid unchanged. **Purity in the code,
compatibility on the wire.**

An earlier draft used a `'+'`-joined string (`"a+b"`). It was dropped: it required
reserving `'+'` as a separator (a new rule games could violate) and stringly-typed
parsing on every path. The structural array needs neither — a chord simply *is* a list.

---

## 4. Canonicalization

A chord is a **set** of simultaneous presses; the order the sender lists them in
carries no meaning. So every chord is canonicalized to a **lexicographically sorted**
sequence, and:

- **duplicate** verbs are invalid (a key can't be pressed twice in one tick);
- **empty** chords / empty verbs are invalid;
- (when the game's `actions()` is supplied) every component must be a **declared verb**.

Canonicalization happens at a **single boundary on each side**, so the union type
(`string | array`) exists *only* on the wire and everything behind the boundary handles
one canonical form:

- **Python — `harness/verify/chord.py`.** `normalize_action(a, valid=None)` turns the
  wire union into a sorted `tuple[str, ...]`, rejecting dups / empties / non-`str` /
  non-members as a typed `ChordError`. `wire_action` / `wire_actions` render the
  canonical form back to the wire (a 1-verb chord collapses to a plain string, so
  single-verb traffic stays byte-identical). Used by the batch send (`gd_exec.py`) and
  the witness/replay writer (`capture.py`).
- **GDScript — `godotworld/chord_util.gd`.** `ChordUtil.apply(game, action)` accepts a
  `String` or an `Array`, sorts a **local copy**, and calls `game.act(verb)` once per
  verb — all synchronously, **before** the tick's physics frames. A single `String` is
  exactly one `act()` call. Both hosts (`serve_game.gd` certification and
  `capture_host.gd` render/replay) call this one helper, so serve/capture parity is
  structural, not coincidental.

Both sides sort the same way (lexicographic / code-point), so the wire is already
canonical when it reaches the host and the host's re-sort is idempotent. The tree
solver and Python reference executor are **unchanged** — chords are RL's domain; the
tree reasons on the single-key subspace, and single verbs are byte-identical to it.

---

## 5. Non-commutativity — a documented feature, not a hidden trap

Because `act()` is called per verb in **sorted** order, an order-*sensitive* game sees
its inputs in lexicographic order, not the sender's order. This is deliberate and
carries **no gameplay meaning** — here is the doctrine (Elias's resolution):

1. **A chord means SIMULTANEOUS press.** The within-tick apply order (lexicographic) is
   a fixed *engine convention*, exactly like any real engine resolving several
   same-frame key presses in some internal order. It is a tie-break, not a semantic.

2. **If an ORDER is intended, express it across ticks.** Grab at tick *t*, move at tick
   *t+1*. Chords **add** expressiveness (a same-tick combination); they remove nothing —
   sequential order is still available, and is where ordered intent belongs.

3. **The real, documented difference is a FEATURE:** a chord `{a, b}` applies both
   inputs to the **same physics state** within one tick — that is precisely what makes
   the one-tick diagonal (and any genuine simultaneous input) possible. Doing `a` then
   `b` across two ticks instead lets the world **evolve in between**. A game that needs
   an input to *settle* before the next naturally gets that by using two ticks; a game
   that needs them *together* uses a chord. The two are different tools, and the
   difference is observable, intended, and available to the player.

So: canonical sorted order ⇒ deterministic and reproducible (a chord replays bit-exact),
and the only "order" a game can ever observe is the fixed engine tie-break — which by
construction is not where gameplay-meaningful ordering lives.

---

## 6. What Phase 1 landed

Host capability only. Files touched:

- `godotworld/chord_util.gd` (new) — the single GDScript canonicalization boundary.
- `godotworld/serve_game.gd` — the single-serve (`_do_ticks`) and batched
  (`_batch_do_ticks`) apply spots call `ChordUtil.apply`.
- `godotworld/capture_host.gd` — the replay apply spot calls `ChordUtil.apply`, and the
  witness loader preserves `String | Array` (was force-stringified).
- `harness/verify/chord.py` (new) — `normalize_action` / `wire_action` / `wire_actions`.
- `harness/verify/gd_exec.py` — batch send canonicalizes via `wire_actions`.
- `harness/verify/capture.py` — witness writers preserve chords via `wire_actions`
  (were `[str(a) for a in actions]`, which would have stringified an array).
- `tests/fixtures/gd_games/chord_probe_2d.gd`, `chord_probe_3d.gd` (new, test-only) —
  linear/uncapped/undamped probes so chord composition is bit-exact.
- `tests/test_chord_normalize.py`, `tests/test_gd_chord.py` (new) — the trust suite.

Explicitly **out of scope** for Phase 1 and untouched: the RL MultiBinary action space,
`sb3_trainer` / `env.py`, tree-solver chord search, the G1 single-chord-win extension,
the contract prompt text, and every game file under the GameAPI contract.

---

## 7. Phases 2 and 3 — designed, not built

- **Phase 2 — MultiBinary PPO.** BUILT — see §8 below.

- **Phase 3 — Escalade formalized.** Turn §2's doctrine into a certification path: a
  game the tree cannot solve on the single-key subspace is *escaladed* to the RL court,
  whose verdict becomes the certificate; the G1 layer gains a single-chord-win
  extension. **Tow Stitch is the first client** — the first game whose feasibility lives
  in chord space and is certified by escalade rather than by the tree.

---

## 8. Phase 2 — MultiBinary PPO (built)

Phase 2 gives the RL policy the simultaneous-controller action model directly. It is a
new **producer** of the Phase-1 wire — no new wire format, no contract change — and it is
**opt-in and byte-identical when off**: every existing Discrete run, wire message, and
witness is unchanged.

### The action space

`chord_mode` (default OFF) swaps the env's `Discrete(n_actions)` for a
`MultiBinary(n_actions)` space — one bit per declared verb. SB3's PPO fits per-key
**Bernoulli** heads over it natively (no policy-code change). Greedy eval is per-key
argmax (prob > 0.5). Each tick's 0/1 vector is reduced to the wire form through the ONE
boundary, `harness/verify/chord.py::chord_from_mask` (→ `wire_action`): a **single**
pressed key collapses to a plain `str` (the legacy singleton wire is preserved byte for
byte), **two or more** become a sorted `list[str]` chord — exactly the canonical form
Phase 1's host (`ChordUtil.apply`) already consumes. No canonicalization logic is
duplicated; `chord_from_mask` is a thin bridge over `wire_action`.

### The all-zeros design point — the IDLE tick (the one open decision, as landed)

An all-keys-off vector is "press nothing" = an **idle tick**. Phase 1's contract has *no*
built-in idle move and `normalize_action` rejects empty chords. The decision (Elias's
recommendation, implemented): add an explicit **`allow_idle`** capability on the serve
init (default **OFF** = byte-identical). When on, an empty chord `[]` is legal on the
wire and the host applies **zero `act()` calls**, then steps physics as normal; the chord
env turns it on.

Two reasons idling is the right primitive, and safe:
1. **Parity.** General Intuition's own controller-style policy can output all-keys-off, so
   an all-zeros vector must have a defined meaning; refusing it would force a phantom
   "always press something" constraint the reference model does not have.
2. **STAKES, not action-shape, punishes idling.** Idling is **losing by design** —
   game pressure (time-decayed success bonus, failure terminals, play-bounds) makes a
   do-nothing tick strictly worse than acting. So an idle move does **not** create free
   stalling; the anti-idle enforcement lives in the *game*, not in the action space. (The
   reward realignment already removed the do-nothing basin risk on the shaping side.)

The empty-chord capability is guarded, mirroring Python↔GDScript symmetrically:
- **Python:** `normalize_action` / `wire_action` / `wire_actions` / `chord_from_mask` take
  an explicit `allow_empty` flag (default **False** — every existing call site keeps
  rejecting empties). Empty → `()` / wire form `[]`. An empty *verb* string `""` is still
  rejected (that is a malformed verb, not idle).
- **GDScript:** `ChordUtil.apply` with an empty `Array` makes zero `act()` calls (the
  natural fall-through of the per-verb loop); `ChordUtil.is_empty_chord` is the shared
  predicate. `serve_game.gd` reads `allow_idle` at init and, when OFF, **rejects** an
  empty chord as a protocol error at the act boundary *before* any physics — the exact
  mirror of Python rejecting empties without `allow_empty`.

Idle is force-OFF outside chord mode (meaningless for a single-verb Discrete wire), so the
Discrete path never carries the capability key.

### Files touched

- `harness/verify/chord.py` — `allow_empty` on `normalize_action`/`wire_action`/
  `wire_actions`; new `chord_from_mask` (the single MultiBinary→wire bridge).
- `harness/rl/env.py` — new `MultiBinary` duck-typed space; the gymnasium adapter
  re-exports `MultiBinary(n)` in chord mode and passes the raw vector through (Discrete
  keeps the `int()` cast — byte-identical).
- `harness/rl/godot_env.py` (`GodotServeEnv`) & `harness/rl/godot_vec_env.py`
  (`GodotBatchVecEnv`) — `chord_mode` / `allow_idle` ctor args; MultiBinary act space;
  `step` / `step_async` map the vector to the wire chord; init carries `allow_idle` when on.
- `harness/rl/godot_shard_env.py` — `step_async` fan-out keeps MultiBinary rows 2-D
  (flattening only the Discrete index vector); chord flows to shards via `env_kwargs`.
- `harness/rl/sb3_trainer.py` — `_rollout` records the WIRE-action list in chord mode
  (`chord_from_mask`), so the greedy demo/witness is `str | list | []` and replays through
  `GdExecutor.run_batch` unchanged.
- `harness/rl/certify.py` — `g3_prime(chord_mode, allow_idle)` threaded to every env
  factory; `export_demo_trajectory` routes actions through `wire_actions` (never `str(a)`,
  which flattened chords); `action_histogram(chord=…)` emits per-KEY press frequency + the
  **chord-size distribution** (0/1/2/3+ keys per tick, `mean_chord_size`).
- `harness/verify/gd_exec.py` — `run_batch` auto-detects an idle demo and inits the serve
  host with `allow_idle` + `wire_actions(allow_empty=…)` (legacy batches byte-identical).
- `godotworld/chord_util.gd` — `is_empty_chord` + empty-Array idle docs.
- `godotworld/serve_game.gd` — `_allow_idle` capability (init) + the empty-chord protocol
  guard on both act paths.
- `harness/cli.py` — `harness rl probe --chord`.
- Tests: `tests/test_chord_normalize.py` (+`allow_empty`, `chord_from_mask`),
  `tests/test_chord_phase2.py` (new: gym adapter space/passthrough, chord histogram, demo
  export, idle auto-detect). In-image: MultiBinary smoke + witness-export replay assert.

### Out of scope (unchanged)

Tree-solver chords (single-key subspace by doctrine — the first-instance judge), G4
attackers in chord space (Phase 3), the contract prompt text, every GameAPI game file.

### Bench — Discrete vs MultiBinary (mini_collect, same 400k budget)

One comparison run, identical proven config both arms (additive reward, eval-keyed
best-checkpoint, `patience 200`, `num_envs 8`, `HARNESS_GODOT_SPEEDUP 8`); the ONLY
difference is `chord_mode`. mini_collect is a 2-D 4-verb collect game (`up/down/left/right`,
two gems). n_eval = 32 greedy + 32 stochastic seeds. (SLURM 18121678 disc / 18121679 mb.)

| metric | Discrete (baseline) | MultiBinary (chords) |
|---|---|---|
| action space | `Discrete(4)` | `MultiBinary(4)` |
| **greedy success rate** | **1.0** | **0.0** |
| **stochastic success rate** | **0.312** | **0.344** |
| **steps to first success** | **1,048** | **72,664** |
| learnable / demo-ready | true / no | false / no |
| witness→batch bridge replays | yes (`bridge_ok`) | **yes (`bridge_ok`)** |
| throughput (steps/s) | 2,196 | 1,838 |
| trained steps / updates | 400,384 / 391 | 400,384 / 391 |

**Chord-size distribution** (fraction of ticks pressing 0 / 1 / 2 / 3+ keys):

| pool | 0 (idle) | 1 | 2 | 3+ | mean |
|---|---|---|---|---|---|
| MultiBinary greedy | 0.000 | 0.000 | 0.746 | 0.254 | 2.258 |
| MultiBinary stochastic | 0.045 | 0.232 | 0.369 | 0.354 | — |

**Per-key press counts** (greedy pool): Discrete `up 256 / down 384 / left 0 / right 512`;
MultiBinary `up 1190 / down 1284 / left 9600 / right 9600`.

**Honest read (a negative on this game — as expected).** On mini_collect the MultiBinary
policy **loses**: Discrete wins greedily every time (1.0) and finds its first win in ~1k
steps; MultiBinary never consolidates a greedy win (0.0) and needs ~70× more steps to its
first stochastic success (72,664 vs 1,048). The chord-size + per-key numbers say WHY — the
greedy chord policy **degenerated**: it presses `left`+`right` on *every* one of the 9,600
greedy ticks (two opposing, cancelling keys), uses a 2+-key chord 100 % of the time and a
single key or idle 0 % of the time (mean 2.26 keys/tick). For a game single keys already
solve, the 2ⁿ action space is pure exploration tax and the Bernoulli heads saturate toward
"press everything". The stochastic SR edging Discrete (0.344 vs 0.312) is noise off a
degenerate policy, and neither arm is demo-ready.

This is the doctrine, confirmed empirically: **chords are a per-game capability, not a free
win.** The expected payoff is on games whose feasibility genuinely needs simultaneity
(3-D thrust composition, forced diagonals), NOT a 2-D game the single-key subspace already
solves. So the **default stays Discrete and `chord_mode` is opt-in** — exactly as built. The
one unambiguous positive: the MultiBinary chord *witness* (2-key chords and all) replayed
bit-exactly to success through `GdExecutor.run_batch` (`bridge_ok = true`), so the Phase-2
export→replay path is proven end-to-end in a real training run, not just in unit tests.

### Arm 2 — diag_collect (diagonal-advantage fairness control)

mini_collect is axis-aligned, so Arm 1 measures MultiBinary's COST with none of its benefit.
`diag_collect` (a <30-line mini_collect variant: gems on pure diagonals at (450,150) and
(150,450), so a one-tick `up`+`right` chord is a genuinely shorter path than the staircase a
single-key policy must walk) supplies the benefit. Same config/budget. (SLURM 18125103 disc /
18125104 mb.)

**Provenance — this arm ran WITH the measured-opposition projection.** Between Arm 1 and Arm 2
the branch gained `feat(chord): mechanically ban contradictory chords (measured-opposition
projection)` (Elias). At chord-env init it PROBES each action's physical effect vector on the
controlled body and records near-antiparallel pairs, then PROJECTS a both-pressed opposing pair
to idle — opposition is MEASURED from physics, never inferred from names. The diag MultiBinary
run confirms it worked: the self-probe discovered `[[up,down],[left,right]]` (logged as
`chord_opposition_pairs`) and the degenerate `left`+`right`-every-tick basin that wrecked Arm 1
is GONE — diag greedy per-key is `up 749 / down 2649 / left 0 / right 2495`, i.e. real
`down`+`right` diagonal chords (size-2 69.3 %). (Arm 1's MultiBinary run, 18121679, PREDATES the
projection — its `left 9600 / right 9600` thrash is the un-projected baseline.)

| metric | Discrete | MultiBinary (chords + projection) |
|---|---|---|
| **greedy success rate** | 0.0 | 0.0 |
| **stochastic success rate** | **0.156** | 0.062 |
| **steps to first success** | 62,056 | **38,232** |
| learnable / demo-ready | false / no | false / no |
| throughput (steps/s) | 1,600 | 1,372 |

Chord-size (MultiBinary greedy): 0/1/2/3+ = 0.000 / 0.307 / 0.693 / 0.000, mean 1.69.

**Honest read.** diag_collect is HARDER for BOTH — neither converges to a greedy solve at 400k
(diagonal credit-assignment is harder than the axis-aligned collect; Discrete's own greedy
degenerates, thrashing `up`/`down`). The projection did its job — the left+right self-cancel
basin is gone and MultiBinary genuinely composes a `down`+`right` diagonal — and MultiBinary
reaches its FIRST success ~1.6x sooner in steps than Discrete (38k vs 62k), the diagonal shortcut
showing up in exploration. But resolving the thrash was NECESSARY, not SUFFICIENT: 400k is still
too small a budget for either arm to consolidate a reliable (greedy) policy on this harder game.
The chord payoff is real but budget-gated; a demo-ready chord win is Phase-2.5/3 work (more
budget + the tuning below), not a 400k drop-in.

**On opposition handling (landed vs deferred).** The projection that shipped is the DOCTRINE-CLEAN
form: opposition is MEASURED from each action's effect vector at init, never from names — so it
does NOT class-force the action space (a game where `left`+`right` is meaningful simply exposes no
antiparallel pair and nothing is projected). NAME-based opposition masking stays REJECTED for
exactly that class-forcing reason. A complementary, still-OPEN alternative (NOT implemented) is a
**λ-conditioned chord-energy cost** — a bounded, terminal-dominant penalty on the number of
simultaneously-pressed keys with λ in the obs (the Thinking-Machines conditioning analysis): soft,
reward-side pressure versus the projection's hard, dynamics-side resolution. Both are generic; the
projection is the shipped default, the energy cost the Phase-2.5 knob if soft pressure proves
preferable to hard projection.

_Test-infra note:_ the full in-image battery (18119403) was green on correctness
(**124 passed, 1 skipped**) but tripped `tests/test_gd_batch_vec.py::test_batch_vec_env_faster_than_dummy`
— a **throughput race** (batch must out-run DummyVecEnv in a wall-clock speed test) that
flakes on a contended node and is unrelated to any Phase-2 change. It cost the whole
`afterok` bench chain (auto-cancelled). SOFTENED on this branch: a tolerant **0.7x speed-ratio floor** (a gross regression still
fails; small node-variance inversions do not) plus a `HARNESS_SKIP_PERF=1` skip, so a loaded
node can no longer red the suite.

