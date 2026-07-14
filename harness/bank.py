# DEPRECATED compatibility shim — moved to harness/core/bank.py (see VERSIONS.md).
# Import from `harness.core.bank` instead. Kept only for backward compatibility.
from harness.core.bank import *  # noqa: F401,F403
from harness.core.bank import (  # noqa: F401
    CATEGORIES,
    CATEGORY_INVARIANTS,
    JOINT_TYPES,
    SHAPES,
    Bank,
    BankError,
    BankOverrideError,
    BankValidationError,
    ResolvedPart,
    bank_dir,
    catalog_path,
    clear_cache,
    content_hash,
    load_bank,
    lock_path,
    resolve_part,
    validate_bank,
    write_lock,
)
