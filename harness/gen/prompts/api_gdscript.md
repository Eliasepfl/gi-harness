# GDScript game contract (GameAPI)

You write ONE GDScript file: a game that implements the GameAPI contract below.
Any game that speaks this contract is verifiable through the frozen serve seam.
This CONTRACT is binding. Emit exactly one ```gdscript module and nothing else
that pretends to be the module.

## The GameAPI your script MUST implement

A single `Node` (or `RefCounted`) class with these methods, driven headlessly by
a frozen runner — never by user input, wall-clock, or the display:

- `init() -> void` — build the world once (nodes, bodies, constants). No RNG here.
- `reset(seed: int) -> Dictionary` — start a fresh episode seeded ONLY by `seed`;
  return the initial typed state. Same seed MUST give the same episode.
- `act(action: String, n_ticks: int) -> Dictionary` — apply `action`, advance the
  physics exactly `n_ticks` fixed steps, and return the new typed state.
- `state() -> Dictionary` — the current typed observation (flat, JSON-serialisable
  numbers/strings/bools only; stable keys across the whole episode).
- `checkpoints() -> Array` — ordered milestone names the runner drives toward.
- `done_term() -> bool` / `done_trunc() -> bool` — terminal (goal/failure reached)
  vs truncated (step budget exhausted).
- `ACTIONS` — the constant list of action strings your `act` accepts.

## Hard constraints (G0 — a violation fails the game before it runs)

- Determinism: identical `(seed, action sequence)` MUST reproduce identical state.
  Seed every random draw from `reset`'s `seed`; never read wall-clock time.
- BANNED APIs: no `OS.*`, no `FileAccess`/disk writes, no network, no threads, no
  reflection escapes, no wall-clock time, no unseeded RNG. Your only world is the
  physics you build in `init`/`reset`.
- No `_input`/`_process` gameplay: all motion happens inside `act`'s fixed steps.
- Typed state only: return flat dictionaries of numbers/strings/bools.

## What to return

The DESIGN block (theme + the checkpoint milestones), then exactly one
```gdscript module implementing the GameAPI for the user's prompt.
