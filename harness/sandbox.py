# DEPRECATED compatibility shim — moved to harness/core/sandbox.py (see VERSIONS.md).
# Import from `harness.core.sandbox` instead. Kept only for backward compatibility.
from harness.core.sandbox import *  # noqa: F401,F403
from harness.core.sandbox import (  # noqa: F401
    ALLOWED_IMPORTS,
    FORBIDDEN_NAMES,
    SandboxViolation,
    load_scene_namespace,
    run_sandboxed,
    scan_source,
)
