"""harness.gen — v2 open-ended game generator and repair loop.

`gamegen` hands an LLM a minimal `World` substrate and an open prompt, then
drives the write -> verify -> repair loop against `harness.verify.gameverify`.
"""
