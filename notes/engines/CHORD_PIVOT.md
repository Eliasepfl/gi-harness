# The CHORD Pivot

**Status:** Phase 1 landed (host-only). Phases 2-3 designed, not yet built.
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

- **Phase 2 — MultiBinary PPO.** Give the RL policy a **MultiBinary** action space (one
  bit per declared verb) matching General Intuition's simultaneous-controller model.
  The bridge from the policy's binary vector to a wire chord is direct: the set bits map
  to a **sorted array** — the exact canonical form Phase 1's boundary already consumes.
  No new wire format; Phase 2 is a new *producer* of the Phase-1 wire. Touches
  `sb3_trainer` / `env.py` (the action space + the vector→chord bridge).

- **Phase 3 — Escalade formalized.** Turn §2's doctrine into a certification path: a
  game the tree cannot solve on the single-key subspace is *escaladed* to the RL court,
  whose verdict becomes the certificate; the G1 layer gains a single-chord-win
  extension. **Tow Stitch is the first client** — the first game whose feasibility lives
  in chord space and is certified by escalade rather than by the tree.
