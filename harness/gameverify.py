# DEPRECATED compatibility shim — moved to harness/verify/gameverify.py (see VERSIONS.md).
# Import from `harness.verify.gameverify` instead. Kept only for backward compatibility.
from harness.verify.gameverify import *  # noqa: F401,F403
from harness.verify.gameverify import (  # noqa: F401
    ESCAPE_MARGIN,
    GUIDED_SEED_BASE,
    K_STEPS,
    NAN_EVENT_TYPES,
    PEN_INIT_TOL,
    TRIVIAL_TICKS,
    load_game,
    run_episode,
    verify_game,
)
