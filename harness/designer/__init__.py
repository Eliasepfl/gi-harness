"""The designer-agent cage (DESIGNER_AGENT_PLAN.md §3-5).

This package is the *only* place the designer agent may touch the repo:

* ``budgets``  — parse the numeric caps from ``designer/BUDGETS.md`` (§4).
* ``write``    — ``designer_write``, the SOLE write capability (§4 P0). Every
  call (accepted or rejected) is appended to ``designer/ledger/designer.jsonl``;
  it is import-flag-gated by ``DESIGNER_WRITE_ENABLED`` (off ⇒ raises).
* ``tools``    — the three FROZEN read/oracle tools (§3 P1) as a typed
  function-calling registry: ``design`` / ``certify`` / ``retrieve_parts``.

Nothing here writes to the harness base code, the verifier, or the G-gate
thresholds — those are Tier-C and integrity-frozen (§4). The whole package is
itself Tier-C: the cage cannot rewrite its own bars.
"""
from __future__ import annotations
