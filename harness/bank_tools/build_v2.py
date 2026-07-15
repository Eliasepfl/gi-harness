"""Assemble + materialise the v2 parts catalog (``banks/parts/v2/parts.json``).

The v2 bank is the union of two lanes, both offline and deterministic:

    migrated   the 60 pre-certified v1 nouns, mechanically re-viewed (migrate.py)
    parametric hundreds of geometry-derived volume families (parametric.py)

This module concatenates them (migrated first, in v1 order; then the parametric
families in fixed grid order), VALIDATES the whole catalog against the v2 schema,
and writes the committed catalog + its content-hash lock. Regenerating is a
pure function of the v1 bank + the code here, so two builds are byte-identical.

CLI: ``python -m harness.bank_tools.build_v2 [--check]`` — ``--check`` rebuilds
in memory and diffs against the committed file (CI guard) instead of writing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from harness.bank_tools.migrate import migrate_bank
from harness.bank_tools.parametric import generate_parametric
from harness.core.bank import (
    bank_dir, catalog_path, content_hash, load_bank, validate_bank, write_lock,
)

BANK_VERSION = "2.0.0"
SCHEMA_VERSION = "2.0"


def build_v2_catalog(v1_version: str = "v1") -> dict:
    """Return the fully-assembled, schema-VALID v2 catalog dict (does not write)."""
    v1 = load_bank(v1_version, use_cache=False).data
    parts = migrate_bank(v1) + generate_parametric()

    names = [p["name"] for p in parts]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ValueError(f"v2 build produced duplicate part names: {dupes}")

    data = {
        "schema_version": SCHEMA_VERSION,
        "bank_version": BANK_VERSION,
        "description": ("v2 parts bank: volume+role objects. Migrated v1 nouns "
                        "+ parametric volume families (ASSET_BANK_V2.md)."),
        "parts": parts,
    }
    validate_bank(data)  # raises BankValidationError on any malformed entry
    return data


def _serialize(data: dict) -> str:
    """Canonical on-disk form (stable across builds): indent=2, trailing newline."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def write_v2(v1_version: str = "v1") -> dict:
    """Build, validate, and write ``banks/parts/v2/{parts.json,bank.lock}``."""
    data = build_v2_catalog(v1_version)
    os.makedirs(bank_dir("v2"), exist_ok=True)
    with open(catalog_path("v2"), "w", encoding="utf-8") as fh:
        fh.write(_serialize(data))
    lock = write_lock("v2")
    return {"parts": len(data["parts"]), "content_hash": lock["content_hash"],
            "path": catalog_path("v2")}


def check_v2(v1_version: str = "v1") -> dict:
    """Rebuild in memory and compare against the committed file (no write)."""
    data = build_v2_catalog(v1_version)
    path = catalog_path("v2")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            on_disk = fh.read()
    except OSError:
        on_disk = None
    fresh = _serialize(data)
    ok = on_disk == fresh
    return {"ok": ok, "parts": len(data["parts"]),
            "content_hash": content_hash(data), "path": path}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m harness.bank_tools.build_v2",
                                     description="Build the v2 parts catalog.")
    parser.add_argument("--check", action="store_true",
                        help="rebuild in memory and diff against the committed "
                             "file (exit 1 on drift) instead of writing")
    args = parser.parse_args(argv)

    if args.check:
        res = check_v2()
        print(f"v2 build {'MATCHES' if res['ok'] else 'DRIFTED FROM'} "
              f"{res['path']}  ({res['parts']} parts, hash {res['content_hash'][:12]})")
        return 0 if res["ok"] else 1

    res = write_v2()
    print(f"wrote {res['path']}  ({res['parts']} parts, "
          f"hash {res['content_hash'][:12]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
