# DEPRECATED compatibility shim — moved to harness/gen/gamegen.py (see VERSIONS.md).
# Import from `harness.gen.gamegen` instead. Kept only for backward compatibility.
from harness.gen.gamegen import *  # noqa: F401,F403
from harness.gen.gamegen import (  # noqa: F401
    _LEDGER_PATH,
    _TEMPLATE_GAMES,
    _model_used,
    _openrouter_content,
    _openrouter_json,
    generate_game,
)
