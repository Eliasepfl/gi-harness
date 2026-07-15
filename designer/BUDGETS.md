---
id: budgets
kind: policy
created_by: human (fable-orchestrator, Elias-ratified)
run_id: seed-2026-07-14
wave: 0
created_ts: 2026-07-14T00:00:00Z
parent: null
status: active
rationale: The section-4 caps of DESIGNER_AGENT_PLAN.md, frozen as the numeric contract the cage enforces. Human-authored, NOT agent-writable (Tier-C).
provenance: notes/engines/DESIGNER_AGENT_PLAN.md §4; SELF_IMPROVING_DESIGNER.md §4; ACE arXiv:2510.04618; Reflexion arXiv:2303.11366
---

# Designer budgets (the numeric cage)

These are the caps `harness.designer.write.designer_write` enforces on every
accepted write, and the caps the `scripts/hooks/pre-commit` hook re-checks on a
designer-attributed commit. This file is **human-authored and Tier-C** — the
agent can never edit it (not directly, not via a proposal). The fenced
`budgets` block below is the machine-readable source of truth parsed by
`harness.designer.budgets`; the prose around it explains each number. If a key
is missing from the block the code falls back to the same default, so the block
and the defaults must agree.

## Skills (`designer/skills/*.md`, Tier-A direct)

- **≤ 200 lines** and **~1500 tokens** per skill file — a skill is a focused
  craft topic, not an essay (Voyager skill-library discipline).
- **≤ 25 active** skill files — the library is a curated working set; the 26th
  create is rejected until an existing skill is evicted to `memory/attic/`.
- **~5000 tokens** total across all active skills — the whole library must fit
  a bounded slice of context.

## Prompt sections (Tier-B, staged as proposals only)

- **≤ 120 lines** each for `rules.md` / `orientation.md` and their siblings —
  a bounded prompt section, never a rewrite.

## Memory (`designer/memory/*.md`, Tier-A append-only)

- **Append-only delta** (ACE, arXiv:2510.04618): memory is only ever *appended*
  to. A whole-file rewrite (mode `w`) is rejected outright; a `> 5`-line
  overwrite of prior lessons is the classic context-collapse failure and is
  never allowed unless a human flags an eviction.

## Proposals (Tier-B packets, one wave = one commit)

- **≤ 3** Tier-B proposals per wave.
- **≤ 2** sections touched per proposal, **+15 lines** added per section.
- **≤ 40 lines** net change per wave.
- **≤ 2** repair iterations per proposal (Reflexion saturates, arXiv:2303.11366).
- **1 wave/day** — the designer moves at a reviewable cadence.

## Machine-readable caps (the enforced source of truth)

```budgets
skill_max_lines = 200
skill_max_tokens = 1500
skills_max_active = 25
skills_total_tokens = 5000
prompt_max_lines = 120
memory_delta_max_lines = 5
proposals_per_wave = 3
proposal_max_sections = 2
proposal_section_max_added_lines = 15
wave_net_max_lines = 40
repair_iters_per_proposal = 2
waves_per_day = 1
```
