"""Agent harness — generate playable 2D environments from text commands, verified
100% programmatically (no VLM, no pixels).

Package layout (see VERSIONS.md at the repo root for the version map):
    core/    version-spanning substrate + services:
             world, sandbox, integrity, telemetry, bank
    verify/  v2 universal oracles: gameverify (G0-G3 funnel + episode runner)
    gen/     v2 open-ended generator + repair loop: gamegen
    legacy/  frozen v1 stack: sdk, verifier/ (L0-L2), generator, templates, navigator
    top-level (span versions): cli, render, bank_ci, __main__

Thin compatibility shims remain at the old flat module paths
(harness/world.py, harness/gameverify.py, ...) and simply re-export from the new
locations; they are deprecated and kept only for backward compatibility.
"""

__version__ = "0.1.0"
