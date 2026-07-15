---
id: budgets
kind: config
created_by: human (Elias-directed rewrite, 2026-07-15 — GDScript-lane era)
status: active
---

# Designer cage budgets — GDScript lane

Human-authored, Tier-C (never agent-writable). The `budgets` block below is
machine-parsed by `harness/designer/budgets.py`; the code's defaults yield to
this file. Spec-lane prose (prompt-section caps, self-authored skill library
rules) was deleted with the lane — knowledge now comes from the downloaded
libraries (/home/enaha/GI/gd-agentic-skills, examples repo); these caps
govern only what the agent may WRITE in its own workspace.

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

Hard rules that survive every pivot: `designer_write` is the sole write
path; memory is append-only delta; selective loading (route per task, never
load-all); auto-revert on gate regression; the kill-switch env flag.
