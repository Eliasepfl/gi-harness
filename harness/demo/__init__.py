"""Demo asset bank: real low-poly 3D assets for render-only game dressing.

Public API (offline, pure-python):
    from harness.demo.asset_bank import load_manifest, match

Companion tooling:
    curate_bank.py   -- reproducible rebuild (copy assets + write manifest)
    measure_aabb.gd  -- headless Godot AABB measurement
Godot-side loader:  godotworld/asset_loader.gd
"""
