"""JavaScript system prompt for the open-ended generator (rung-4 port).

Same open-ended DESIGN-then-code contract as the Python prompt (CONTRACTS §2/§3),
but the World API and module format are re-expressed in JavaScript as implemented
by ``nodeworld/world.js`` (Planck.js / Box2D underneath): the verbs are identical,
``world.add`` takes an options OBJECT instead of keyword arguments, ``checkpoints``
returns a plain object, and the game runs inside a ``node:vm`` sandbox with only a
frozen ``Math`` available — no ``require`` / ``import`` / module wrapper.

The prompt itself now lives in the shared, single-concern SECTION FILES under
``harness/gen/prompts/`` and is assembled by ``prompts.compose("js")``. This module
keeps the historical name ``SYSTEM_PROMPT_JS`` as a thin, menu-free shim so existing
callers and tests are unchanged; a per-run system prompt carrying a retrieved
Tier-1b parts menu is composed on demand inside ``gamegen``.
"""
from __future__ import annotations

from harness.gen.prompts import compose

# The JS variant of gamegen._SYSTEM_PROMPT — the menu-free baseline (Tier-1b
# parts menus are spliced in per run by gamegen via prompts.compose("js", menu)).
SYSTEM_PROMPT_JS = compose("js")
