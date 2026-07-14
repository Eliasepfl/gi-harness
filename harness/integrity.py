# DEPRECATED compatibility shim — moved to harness/core/integrity.py (see VERSIONS.md).
# Import from `harness.core.integrity` instead. Kept only for backward compatibility.
from harness.core.integrity import *  # noqa: F401,F403
from harness.core.integrity import snapshot, tracked_files, violations  # noqa: F401
