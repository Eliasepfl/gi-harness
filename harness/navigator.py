# DEPRECATED compatibility shim — moved to harness/legacy/navigator.py (see VERSIONS.md).
# Import from `harness.legacy.navigator` instead. Kept only for backward compatibility.
from harness.legacy.navigator import *  # noqa: F401,F403
from harness.legacy.navigator import (  # noqa: F401
    _GreedyPolicy,
    _load_scene,
    _run_episode,
    navigate,
    observe_text,
)
