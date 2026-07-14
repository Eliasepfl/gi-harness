# G4 — Delegated adversarial testing (design)

> Implementation-ready design for the **G4 oracle family**: many free/cheap models,
> in parallel, attacking a certified game to prove it is bulletproof — or to find
> the crack and hand it back to the author. Written to be built alongside the
> Planck port (`nodeworld/`), riding on `run_episode` (the referee) and the
> executor seam sketched in `nodeworld/SPIKE_REPORT.md`.
>
> Foundations: OBJECTIVES.md "Adversarial / edge-case testing requirement" +
> DELEGATION ARCHITECTURE block; CONTRACTS §2 (game format / runner), §4
> (`run_episode`, report, witness); `harness/gameverify.py`; `harness/telemetry.py`;
> `notes/PARTS_BANK.md`. Everything new is English; no game-specific hardcoding.

---

## 0. One-paragraph summary

The smart **author model** (`claude-opus-4-8`, or the OpenRouter volume model) writes
and repairs the game — it is the only thing that ever emits code. A pool of cheap
**attacker models** reads the *certified game source + report* and proposes **pure
data**: JSON action sequences or parameterized patterns. The proposals are validated
by **mechanical replay** (`run_episode`) — never by trusting the attacker. Attackers
run in **parallel lanes** (one lane per model; per-model OpenRouter rate limits give
free parallelism). A shared, append-only **game dossier** accumulates only
*mechanically verified facts*; the moment an attack fails, the dossier update flows
into every relaunched attacker's prompt, and the moment an attack *finds* something,
that finding is routed to the author's repair loop — both immediately, not at
round end. Every failed attack is classified by the referee as **incomprehension**
(its model of the rules was wrong) or **misconception** (understanding fine, strategy
failed), and each attacker's whole attempt tree — hypothesis → plan → outcome →
self-maintained note — is preserved for the smart-vs-weak comparison view.

Generator/verifier asymmetry is the whole economics: proposing an attack is far
easier than designing a game, wrong attacks cost nothing (a millisecond replay), so
weak models suffice and the referee is the sole authority.

---

## 1. Roles & data contract

### 1.1 Two roles, one hard boundary

| Role | Who | Emits | Trust |
|---|---|---|---|
| **Author** | the smart model that wrote the game (`claude-opus-4-8` via `anthropic`, or the OpenRouter `OPENROUTER_MODEL`) | **Code** (the game module, §2 format) + repairs | Verified by G0–G3, then G4 |
| **Attacker pool** | 3–4 cheap/free OpenRouter models in parallel lanes | **Pure data only** — an ATTACK RECORD (never code) | Never trusted; every claim is a *hypothesis* until an episode confirms it |

**The boundary is load-bearing.** Attackers output JSON, so there is zero sandbox
risk (nothing is `exec`'d), and validation is mechanical: did `success` trigger under
avoidance? NaN? escape? a faster-than-witness shortcut? A wrong attack is free.

### 1.2 The ATTACK RECORD

The unit of attacker output. One proposal call returns a **batch** of K records
(cheaper than K calls; see §6). Persisted verbatim per game.

```jsonc
{
  "schema": "attack_record/v1",
  "attacker_id": "qwen-coder#lane0",      // stable per (model, lane)
  "model": "qwen/qwen-2.5-coder-32b-instruct:free",
  "game": "ice-drift-pad",                // slug
  "round": 2,                             // G4 round index (0-based)
  "iteration": 5,                         // this attacker's attempt counter (across rounds)

  // Stated BEFORE acting — the attacker's model of the rules.
  "hypothesis": {
    "prose": "Gravity flips upward around tick 50; if I do nothing until then and
              drift up, success may fire without touching the pad.",
    "beliefs": [                          // machine-checkable claims (§4.2 schema)
      {"channel": "mechanic", "kind": "gravity_flip", "op": "~", "tick": 50, "tol": 10},
      {"channel": "kill", "entity": "spikes", "present": false}
    ]
  },

  // The self-maintained prompt the attacker carries + updates between attempts.
  // Persisted VERBATIM. Attackers append a structured BELIEFS block (parsed for
  // the drift check, §4.4); the prose is never machine-interpreted.
  "strategy_note": "Round1: spam thrust -> nothing, agent just oscillates.\nRound2:
                    dossier says gravity flips ~50 (ep#12). Try wait-then-coast.\n
                    BELIEFS: [{\"channel\":\"mechanic\",\"kind\":\"gravity_flip\",\"tick\":50}]",

  // Pure data. Either an explicit flat sequence OR a parameterized pattern the
  // deterministic expander (§3.4) turns into a flat per-tick list. Every token
  // MUST be in the game's declared ACTIONS or it is rejected before replay.
  "action_plan": {
    "kind": "pattern",                    // "sequence" | "pattern"
    "pattern": "hold_then_random",        // one of the registered expanders
    "params": {"hold": "wait", "hold_ticks": 55, "then": "thrust", "seed": 7},
    "horizon": 120
  },

  // FILLED BY THE REFEREE (never by the attacker):
  "outcome": "nothing",                   // §1.3 vocabulary
  "evidence": {                           // §4.1 observable channels, from run_episode
    "result": "budget",
    "ticks": 120,
    "success_tick": null,
    "checkpoints": {"moved_off_start": 3, "crossed_midline": null, "entered_upper_half": 22},
    "nan": false,
    "escape": null,
    "final_digest": "a91f…",
    "engine": "py"
  },
  "failure_class": "incomprehension",     // §4.1, only when outcome == "nothing"
  "falsified_beliefs": [                   // which claims the episode contradicted
    {"channel": "mechanic", "kind": "gravity_flip", "reason": "no gravity event observed; flag map empty"}
  ]
}
```

### 1.3 Outcome vocabulary (referee verdicts)

Every record's `outcome` is one of these — decided **only** from `run_episode`
output + `world.events()`, on the game's own engine (`py`/`js` tag; see §3.5):

| `outcome` | Finding? | Mechanical signal |
|---|---|---|
| `unintended_success` | ✅ | `success` fired under an **avoidance** plan (a plan that minimizes checkpoint progress) or a degenerate plan — the goal is reachable without playing → degenerate/unavoidable goal |
| `shortcut_beats_witness` | ✅ | `success` fired with `ticks < witness.ticks − MARGIN` (or `ticks < TRIVIAL_TICKS`) — a faster-than-intended solution the certified witness missed |
| `nan` | ✅ | `nan_detected`/explosion event during replay (World sentinel freeze) |
| `escape` | ✅ | a dynamic body left `world+ESCAPE_MARGIN` (200 px) and was not removed |
| `stuck` | ✅ (soft) | controlled body immobile (speed < ε) with **no new checkpoint latch** for a window ≥ `STUCK_WINDOW`, episode non-terminal → soft-lock a real player could hit |
| `nothing` | ❌ | none of the above — the attack failed to break the game. This is the **common** case and the input to the failure taxonomy (§4) |

`run_episode` already returns `{"result","ticks","steps","actions","snapshot","checkpoints","error"}`
with `result ∈ {success,failure,budget,exhausted,error}` — the referee reads that
plus `world.events()`/`world.snapshot()` and maps to the table above. It **reuses the
existing runner verbatim**; G4 adds only the classifier on top.

---

## 2. The shared GAME DOSSIER (blackboard)

A per-game, append-only store of **verified facts** — derived from *outcomes*, never
from attacker *claims*. It is the memory that makes "everyone learns immediately"
real: a fact proven by one episode is injected into every relaunched attacker's
prompt and into the author's repair prompt.

### 2.1 What qualifies as a FACT

> **A fact is a statement over the observable channels (§4.1) that some episode
> produced as evidence, and that is reproducible from a fixed seed + action_plan.**
> Attacker hypotheses stay hypotheses; a hypothesis is *promoted* to a fact only when
> a replay's evidence matches it, and it is stored with the provenance of the episode
> that proved it.

Nothing an attacker *says* enters the dossier. Only what the referee *observed*.

### 2.2 Fact schema (append-only, provenance-carrying)

```jsonc
{
  "schema": "dossier_fact/v1",
  "id": "f_00017",
  "kind": "mechanic",                 // mechanic | kill_confirmed | kill_absent |
                                      // milestone_rate | pattern_survived |
                                      // pattern_broke | min_solve_ticks |
                                      // avoidance_floor
  "statement": {                      // shape depends on kind; shares §4.1 channels
    "channel": "mechanic", "detail": "gravity_flip", "tick": 51, "tol": 6
  },
  "provenance": {                     // which episode PROVED it — replayable
    "episode": "ep_000012",
    "engine": "py",
    "seed": 0,                        // WORLD_SEED
    "action_plan_digest": "sha256:7c…",
    "round": 1, "attacker_id": "llama#lane2"
  },
  "confidence": {"episodes": 3},      // # independent episodes exhibiting it
  "ts": "2026-07-13T…Z"
}
```

Representative fact kinds (all mechanically derivable):
- `mechanic` — a flag toggle / gravity sign change observed at ~tick N (from
  `world.events()` `flag_set` steps and gravity reads).
- `kill_confirmed(entity)` / `kill_absent(entity)` — `failure` fired (or provably
  never fires) on contact with `entity`.
- `milestone_rate(name, k/E)` — checkpoint `name` latched in k of E episodes
  (aggregated from the latch maps).
- `pattern_survived(digest)` / `pattern_broke(digest, outcome)` — an action pattern
  the game withstood, resp. that produced a finding.
- `min_solve_ticks(N)` — fastest observed `success` (feeds `shortcut_beats_witness`
  and tightens the anti-triviality bar).
- `avoidance_floor(name)` — the *least* checkpoint progress reachable while actively
  avoiding (feeds `unintended_success`).

### 2.3 Update protocol

1. After each batch replay, the referee derives candidate facts from the evidence.
2. **Append-only**: a new observation either adds a fact or bumps an existing fact's
   `confidence.episodes` (matched by `(kind, statement)` up to tolerance). Facts are
   never mutated or deleted — a later contradicting observation is *itself* a new
   fact (`pattern_broke`, or a mechanic with a different tick), and the divergence is
   surfaced, not silently overwritten. This preserves the audit trail the whole
   harness values (OMNI-EPIC: unshaped, hack-resistant certificates).
3. Deduping is by content; provenance always points at the first episode that proved
   it.

### 2.4 Injection points

- **(i) Relaunched attackers.** Each proposal prompt carries a compact, rendered
  **fact sheet** (top-N facts by confidence + all findings so far + the digests of
  patterns already known to survive, so weak models don't re-propose them). Because
  the dossier is append-only and shared, an attacker relaunched *right after* a
  peer's failure sees the freshest facts — this is the "fresh knowledge flows to
  everyone immediately" requirement.
- **(ii) Author repair prompt.** When any attack yields a finding, the finding +
  the relevant facts are folded into a repair report (§3.6) and handed to the author
  *at once*, so it "knows immediately if something needs fixing." The author never
  sees attacker prose — only verified facts and a replayable reproducer.

---

## 3. Async orchestration

### 3.1 The asymmetry that sets the design

**Replays are milliseconds; LLM proposals are the entire latency budget.** (Spike:
Planck does ~160 k physics steps/s; a 40-episode batch is ~180 ms in one Node
process; pymunk is ~parity in-process.) So the orchestrator is built to **maximize
LLM parallelism** and treat replay as a cheap synchronous step.

### 3.2 Lanes = models (per-model rate limits → free parallelism)

One **lane** per attacker model. OpenRouter rate limits are per-model, so N models on
one key run genuinely in parallel; within a lane, calls are serialized behind a
small semaphore sized to that model's limit. This is exactly the DELEGATION
ARCHITECTURE Tier-1 "separate rate limits = free parallelism" point.

```
        ┌── lane: qwen-coder ──┐   propose (async HTTP)  ─┐
prompt ─┼── lane: llama       ─┤   propose (async HTTP)  ─┼─▶ [K attacks each]
        └── lane: glm         ─┘   propose (async HTTP)  ─┘
                                                            │
                                   ┌────────────────────────▼───────────────┐
                                   │  REFEREE (batch replay via run_episode) │  ms-cheap
                                   │  → outcome + evidence per record        │
                                   └────────────────────────┬───────────────┘
                                                            │
                     ┌───────── finding? ──────────┐        │  no finding
                     ▼                              ▼        ▼
             route to AUTHOR repair         append facts to DOSSIER  ──▶ relaunch
             (immediately, §3.6)            (immediately visible)        next round
```

### 3.3 Concurrency mechanics (asyncio over the Planck executor)

- **Proposal**: `asyncio` tasks, one per lane, each awaiting the OpenRouter HTTP
  call (reuse `gamegen._openrouter_complete`, made async or run in a thread executor).
- **Replay**: collect all proposed `action_plan`s and run them as **one batch**. For
  `py` games, loop `run_episode` in-process (or a `ProcessPoolExecutor` for isolation
  + hard timeout, as `harness/sandbox.run_sandboxed` already does). For `js` games,
  batch every episode into **one** `node runner.js` job via the executor seam
  (`execute(game_source, [episode specs]) -> [episode dicts]`) — exactly the
  "batch all G3 episodes in one process" discipline the spike proved. Every oracle
  stays in Python and eats dicts; the engine is never touched by G4 logic.
- **Immediacy**: the referee publishes to an `asyncio` event bus. A `finding` event
  pushes onto the author-repair queue *and* the dossier; a `nothing` event pushes a
  fact onto the dossier. In-flight relaunch tasks read the dossier at prompt-build
  time, so they pick up peers' just-published facts without waiting for a round
  barrier.

### 3.4 Action-plan validation & expansion (deterministic, no LLM)

Before any replay:
1. **Reject out-of-vocabulary**: every action token must be in `game.actions`
   (the declared `ACTIONS`); unknown tokens → record dropped with
   `outcome="nothing"`, `failure_class="incomprehension"` (it doesn't know the moves).
2. **Expand patterns**: a small registry of deterministic expanders turns a
   parameterized pattern into a flat per-tick list, mirroring `gameverify._macro_plan`.
   Registered patterns (these also *are* the Tier-0 fuzz seeds, §3.7):
   - `spam(action, ticks)` — single-action-win / max-frequency spam.
   - `alternate(actions[], period)` — alternating extremes.
   - `boundary_hug(action, ticks)` — push toward a wall for the whole horizon.
   - `hold_then_random(hold, hold_ticks, then, seed)` — avoidance-style stall.
   - `avoid(seed)` — greedy minimize-checkpoint-progress (uses `checkpoints` latches
     as the signal; the OBJECTIVES "avoidance probe").
   - `sequence(list)` — verbatim flat list (escape hatch for a precise exploit).

Weak models thus only need to name a pattern + a couple of params — the design
deliberately keeps the surface tiny so cheap models succeed.

### 3.5 Determinism / engine routing (from the spike)

Replays are **byte-identical per engine** but **not portable across engines**
(Planck `mulberry32` ≠ Python Mersenne; Box2D ≠ Chipmunk numbers). Therefore the
referee always replays on the **game's own engine** (the `engine: "py"|"js"` tag the
generator stamps on each game). Attack `action_plan`s are engine-neutral (just action
strings), so the same attack runs on either engine; only the physics verdict is
engine-specific — which is correct, because the game was certified on that engine.
Every finding stores `{engine, seed=0, action_plan}` so it replays exactly, like the
G3 witness.

### 3.6 Plugging into `gameverify` (G4 layer, optional/async)

G4 sits **after G3** and is **optional + async** (it needs network + is slow relative
to G0–G3):

```python
# harness/gameverify.py — new, does not change the G0–G3 funnel
def run_g4(game_source: str, *, engine="py", dossier=None, pool=None,
           rounds=3, tiers=(0, 1)) -> dict:
    """Adversarial layer. Reuses run_episode as referee + the executor seam.
    Returns the g4 report block (§4.3). Pure-Python orchestration; the engine
    is reached only through the batch executor."""
```

`verify_game(game_path, *, g4=False)` stays default-off; when a caller opts in
(campaign hardening pass, or `harness game verify --g4`), G4 runs after a passing
G3 and attaches its block to the report. A G4 finding does **not** retroactively fail
G0–G3; it is a *new* signal that either grades the game "bulletproof" or routes a
repair — mirroring how every other layer feeds the repair loop.

### 3.7 Round structure & the three tiers

Each round: **propose (parallel) → replay (batch) → dossier update → relaunch with
updated dossier.**

- **Tier 0 — mechanical fuzz, NO LLM.** Seeded runs of the registered patterns
  (§3.4) at native speed (thousands eps/s). Free. Runs first every round; its facts
  seed the dossier before any LLM spends a token. This *is* OBJECTIVES's breaker /
  single-action-win / avoidance probes, unified under the same referee + vocabulary.
- **Tier 1 — cheap-LLM proposers (the sweet spot).** The parallel lanes. Each reads
  the certified source + report + dossier fact sheet and proposes K targeted attacks.
- **Tier 2 — smart-model attacks.** Reserved for games surviving Tiers 0–1; only the
  "bulletproof" grade requires surviving all three.

### 3.8 Stop conditions

- **Bulletproof grade** ← `N_CLEAN` consecutive rounds with **zero findings** across
  the enabled tiers (default across Tier 0 + Tier 1; the top grade adds Tier 2).
- **Finding** ← route to the author (§2.4-ii) *immediately*; the author repairs
  (its normal `_repair_loop`, one more attempt), G0–G3 re-verify the repair, then G4
  re-runs on the repaired game with the dossier carried forward (surviving facts stay;
  the fixed finding becomes a `pattern_broke`→(now)`pattern_survived` regression fact).
- **Budget** ← `rounds` exhausted or the daily quota ceiling (§5) reached →
  "hardened (N rounds, no finding)" short of the bulletproof bar.

### 3.9 Telemetry

`telemetry.record_run` gains a `g4` block (best-effort, never breaks a run):

```jsonc
"g4": {
  "graded": "bulletproof|hardened|repaired|open",
  "rounds": 3,
  "tiers": [0, 1],
  "findings": [{"outcome": "shortcut_beats_witness", "round": 1,
                "attacker_id": "glm#lane1", "reproducer_digest": "sha256:…"}],
  "attackers": [
    {"attacker_id": "qwen-coder#lane0", "model": "…", "attempts": 18,
     "findings": 0, "incomprehension": 11, "misconception": 7}
  ],
  "facts": 24
}
```

`harness game stats` can then aggregate a per-model attacker leaderboard
(finding-rate, incomprehension/misconception split) exactly as it already
aggregates author completion-rate + flagrant-error histograms.

---

## 4. Failure taxonomy & the comparison view

### 4.1 Observable channels (the shared vocabulary)

Facts, beliefs, and outcomes all speak the **same** channels, all derived from
`run_episode` output + `world.events()`/`world.snapshot()` — never from prose:

| channel | source |
|---|---|
| `result`, `ticks` | `ep["result"]`, `ep["ticks"]` |
| `success_tick` / `failure_tick` | first tick `success`/`failure` fired |
| `checkpoint[name]` | `ep["checkpoints"][name]` (runner latch tick or `None`) |
| `nan` | `nan_detected`/explosion in `world.events()` |
| `escape` | dynamic body beyond `world+ESCAPE_MARGIN` |
| `mechanic` | gravity sign flips + `flag_set` step from `world.events()` |
| `final_digest` | rounded hash of `world.snapshot()` |
| `immobility` | longest run of ticks with controlled speed < ε and no new latch |

### 4.2 Belief / prediction schema (makes the taxonomy mechanical)

A `belief` is a typed assertion over one channel — this is what lets the referee
*check* a hypothesis instead of judging prose:

```jsonc
{"channel": "mechanic", "kind": "gravity_flip", "op": "~", "tick": 50, "tol": 10}
{"channel": "kill", "entity": "spikes", "present": true}
{"channel": "checkpoint", "name": "crossed_midline", "reachable": true}
{"channel": "success", "op": "<", "ticks": 8}          // "I can win in <8 ticks"
```

The attacker states beliefs before acting (in `hypothesis.beliefs`) and maintains a
BELIEFS block in its `strategy_note`. Both are checked against evidence + dossier.

### 4.3 The two classes (mechanical definitions)

> Applies **only to failed attacks** (`outcome == "nothing"`; a `stuck` soft-lock is
> a finding, not a failure).

- **INCOMPREHENSION — its model of the rules was wrong.** At least one of:
  1. a stated `belief` **contradicts a verified dossier fact** known at proposal
     time (it ignored a truth already proven), or
  2. a stated `belief` is **falsified by its own episode's evidence** (it predicted a
     mechanic/kill/reachability the replay showed false), or
  3. the `action_plan` used **tokens outside `ACTIONS`** (it didn't know the moves).

- **MISCONCEPTION — understanding fine, strategy failed.** The hypothesis is
  **consistent with every known fact and not falsified by its own episode**, yet the
  plan produced no finding. Plausible model, flawed strategy/execution.

The referee decides by (a) diffing `hypothesis.beliefs` against the dossier facts,
and (b) diffing them against the episode evidence via §4.1; any contradiction ⇒
incomprehension, otherwise misconception. `falsified_beliefs[]` records exactly which
claim broke and why, so the classification is auditable, not a black box.

### 4.4 The attempt tree & strategy-note drift

Per attacker, the full tree is preserved across rounds:

```
attacker_id
└─ iteration 1 → hypothesis → action_plan → outcome=nothing (misconception) → note_v1
   └─ iteration 2 → hypothesis' → action_plan' → outcome=nothing (incomprehension) → note_v2
      └─ iteration 3 → hypothesis'' → action_plan'' → outcome=shortcut_beats_witness ★
```

Each node = one ATTACK RECORD (§1.2). Edges are the `strategy_note` updates the
attacker carries between attempts, **persisted verbatim**.

**Note-drift flag (mechanical).** The referee parses the BELIEFS block out of each
`strategy_note` and diffs it against the dossier. A belief in the note that
contradicts a verified fact is flagged `note_drift` (e.g. the note still asserts
"gravity never flips" after fact `f_00017` proved it flips at ~51). This is the
mechanical hook for "spot when something looks off in the prompt it maintains."

### 4.5 Smart-vs-weak comparison view

Given a strong attacker's tree and a weak one's on the same game:

- **Convergence diff** — which *beliefs* the strong tree converged on (stabilized,
  fact-consistent) that the weak tree never reached, or reached and then abandoned.
  Rendered as: strong-only beliefs, weak-only beliefs, shared beliefs; plus the
  outcome each tree ultimately achieved.
- **Note-drift comparison** — count + list of `note_drift` flags per tree; the weak
  model typically carries more contradicted claims longer (the visible signature of
  incomprehension), which the view surfaces directly.
- **Depth-to-finding** — iterations each tree needed before its first finding (or ∞).

### 4.6 CLI report — `harness game g4-report <game>`

Reads the persisted G4 artifact (`scenes/games/<slug>/g4.json`) and prints a
human summary; `--json` emits the raw block; `--attacker <id>` drills into one tree;
`--compare <strong_id> <weak_id>` prints the diff of §4.5.

```
$ python -m harness game g4-report ice-drift-pad
=== G4 REPORT  ice-drift-pad  [engine=py] ===
grade: HARDENED (3 rounds, tiers 0+1)      facts: 24     findings: 1

FINDINGS
  ★ shortcut_beats_witness  round1  glm#lane1
    reproducer: pattern spam(thrust, 120) -> success @ tick 6  (witness: 61)
    routed to author repair -> COMPLETED (a3.py); now pattern_survived (regression)

ATTACKERS                     attempts  found  incomp  misconc  note_drift
  qwen-coder#lane0                  18      0      11        7          2
  llama#lane2                       15      0      13        2          5
  glm#lane1                         12      1       4        7          0

COMPARE glm#lane1 (strong) vs llama#lane2 (weak)
  strong converged on: [success<8 ticks], [gravity_flip~51]
  weak  never reached: [success<8 ticks]
  weak  note-drift    : [kill spikes present]  contradicts fact f_00009 (kill_absent spikes)
  depth-to-finding    : strong=8   weak=inf
```

### 4.7 Author-repair injection (reuses the existing contract)

A finding is turned into a report-shaped object and handed to `gamegen._repair_loop`
via the same channel as any layer, so it drops in with no new author machinery:

```jsonc
{
  "passed": false,
  "failure_class": "G4_FINDING",          // hint maps by outcome:
                                          //  unintended_success/shortcut → goal-degenerate
                                          //  nan/escape → physics-robustness
                                          //  stuck → soft-lock
  "hint": "an adversarial player used spam(thrust) and won in 6 ticks (witness 61);
           tighten the goal so a single repeated action cannot solve it",
  "g4_reproducer": {"engine": "py", "seed": 0,
                    "action_plan": {"kind": "pattern", "pattern": "spam",
                                    "params": {"action": "thrust", "ticks": 120}}},
  "g4_facts": [ /* the handful of relevant verified facts */ ]
}
```

`gamegen._repair_user_msg` already renders `failure_class` + `hint` + JSON report;
G4 adds the reproducer + facts blocks. The author sees "here is exactly how a player
broke your game, and here are proven facts — fix ONLY the module," identical in shape
to every existing repair.

---

## 5. API keys & quotas (one OpenRouter key)

**Assume one OpenRouter key; lanes are different models on the same key.** The key +
`OPENROUTER_MODEL` already live gitignored in `env.py` (`_resolve_secret`), env vars
override. G4 adds an `OPENROUTER_ATTACKER_MODELS` config (comma-separated model ids)
so the pool is a one-line change, exactly like the author `OPENROUTER_MODEL` switch.

**Free-tier ceiling (factual, to be confirmed by the orchestrator against live docs).**
OpenRouter's documented free-tier caps for `:free` models are the scaling ceiling:
- ~**20 requests/minute** per free model, and
- a **per-account/day** cap on `:free` requests — **~50/day** by default, rising to
  **~1000/day** once the account holds a **$10+ lifetime credit balance**.

Because lanes are different models, the per-*minute* limit is effectively multiplied
by the pool size, but the per-*day* free cap is **account-level** (shared across all
`:free` models on the key) — so the day cap, not the minute cap, is the true ceiling
for G4 volume. These figures move; the orchestrator should confirm current numbers
against OpenRouter's docs before the campaign and answer Elias separately.

**When a second key or small paid models become worth it.** With the ~1000/day
account cap and the §6 sizing (~6–12 attacker calls/game), one $10-funded key covers
roughly **80–160 games/day** of Tier-1 G4 — comfortable for the base-of-games
campaign. Escalate only when: (a) daily throughput saturates the account cap (add a
second key, or move a lane to a **small paid** model like a cheap Qwen/Llama endpoint
whose paid RPD is far higher and whose per-attack cost is fractions of a cent — recall
wrong attacks cost nothing and the referee is free), or (b) reaching the *bulletproof*
grade needs Tier-2 smart-model attacks, which are deliberately rare and can run on the
author's own `anthropic` budget. Tier 0 (mechanical fuzz) is always free and unlimited
— push as much coverage there as possible before spending a single LLM call.

---

## 6. Sizing & sequencing

### 6.1 Cost per game per round

Per the DELEGATION ARCHITECTURE economics ("~1 cheap call per attacker per game +
millisecond replays"):

```
per round:  Tier0 fuzz      = 0 LLM calls   (thousands eps/s, free)
            Tier1 proposals = N calls        (one call returns K attacks)
            replays         = (N·K + fuzz) episodes × ~1 ms each  ≈ negligible
```

Defaults: **pool N = 3–4** free models, **K = 4–6** attacks/call, **rounds = 2–3**.
So a game costs **≈ N·rounds ≈ 6–12 attacker calls**, all replays effectively free.
Bulletproof (adds Tier 2) adds a small handful of smart calls only for survivors.

### 6.2 Defaults summary

| knob | default |
|---|---|
| `pool` | `["qwen-coder:free", "llama-3.x:free", "glm:free"]` (3–4 diverse) |
| `attacks_per_call` K | 5 |
| `rounds` | 3 |
| `tiers` | `(0, 1)`; bulletproof adds `2` |
| `N_CLEAN` | 2 clean rounds → hardened; +Tier 2 clean → bulletproof |
| `horizon` | `PROBE_HORIZON` (120) — matches G3 |
| `seed` | `WORLD_SEED` (0) — replayable like the witness |
| `STUCK_WINDOW` | ~20 ticks immobile + no latch |
| `shortcut MARGIN` | witness `ticks` × 0.5 (and always flag `< TRIVIAL_TICKS`) |

### 6.3 v1 scope (ships with the Planck port)

- **Tier 0** mechanical fuzz (no LLM) — the pattern registry + referee.
- **Tier 1** cheap-LLM proposers in per-model lanes over one OpenRouter key.
- The **dossier** blackboard (append-only, provenance) + both injection points.
- Referee via **`run_episode`** on the game's engine (`py` in-process; `js` via the
  batch executor seam) — the outcome vocabulary + evidence extraction.
- **Failure taxonomy** (incomprehension/misconception) + `falsified_beliefs`.
- **Attempt-tree artifact** (`g4.json`) + `harness game g4-report` incl. the
  smart-vs-weak compare and note-drift flag.
- **Finding → author repair** injection (reusing `_repair_loop`) + G4 re-run.
- Telemetry `g4` block; `verify_game(..., g4=False)` opt-in + `--g4` CLI flag.

### 6.4 v2 (after the campaign)

- **Dossier-driven author pre-hardening**: feed accumulated attack patterns/facts
  into the *author's initial generation prompt* (a compact "known exploits to
  preempt" note), so the game is hardened *before* attack — closing the loop the
  PARTS_BANK synthesis anticipates (nouns pre-certified; here, *exploits* pre-known).
- **Cross-GAME pattern library**: attacks that generalize (e.g. `boundary_hug`
  escapes, `spam` single-action wins) graduate into a standing regression suite +
  extra Tier-0 fuzz seeds every new game must survive — a growing, verified corpus of
  "ways games break," the adversarial mirror of the parts bank.
- **Tier 2** smart-model attacks as a routine grade step; **dense retrieval** of the
  most relevant past attacks per new game (reusing the BM25/Model2Vec stack the
  retrieval studies already spec).

---

## 7. Five key design decisions (+ the taxonomy)

1. **Attackers emit pure data; the referee is the sole authority.** ATTACK RECORDs
   carry JSON action sequences / parameterized patterns — never code — so there is
   zero sandbox risk and validation is a millisecond `run_episode`. Generator/verifier
   asymmetry means weak/free models suffice and wrong attacks are free.

2. **The dossier stores only mechanically verified facts, append-only with
   provenance.** Attacker claims stay hypotheses until an episode confirms them; a
   fact points at the exact seed+plan that proved it. This is what makes "everyone
   learns immediately" trustworthy rather than a rumor mill.

3. **Lanes = models over one OpenRouter key; async, finding-driven, not
   round-locked.** Per-model rate limits give free parallelism; replays are batched
   and cheap; a finding is published the instant it's classified — to the author's
   repair queue *and* the shared dossier — so relaunched peers and the author both
   react at once, not at round end.

4. **A shared observable vocabulary + typed beliefs make the whole thing mechanical.**
   Facts, hypotheses, outcomes, and note-drift all speak the same channels derived
   from `run_episode`; the failure taxonomy and the "note looks off" flag are computed
   diffs, not LLM judgments.

5. **G4 rides existing seams and stays optional.** It reuses `run_episode` as referee
   and the spike's batch-executor abstraction for the JS engine; it sits after G3 as
   an opt-in async layer, routes findings back through the *existing* author repair
   contract, and reports via a new `g4` telemetry block + `game g4-report` CLI — no
   changes to the G0–G3 funnel.

**Failure taxonomy (definition).** For a failed attack (`outcome == "nothing"`):
**INCOMPREHENSION** = the attacker's stated model of the rules was wrong — a belief
contradicts a verified dossier fact known at proposal time, *or* is falsified by its
own episode's evidence, *or* the plan used actions outside `ACTIONS`.
**MISCONCEPTION** = the hypothesis is consistent with every known fact and unfalsified
by its own episode, yet the strategy produced no finding — a plausible understanding
with flawed strategy or execution. The split is decided by the referee diffing the
record's `beliefs` against the dossier and the episode evidence; the offending claim
is recorded in `falsified_beliefs[]`.

---

*File: `notes/adversarial/G4_DESIGN.md` — the only file created by this task.*
