"""Tier 0 — system-wide autoresearcher specifics.

- corpus: held-out articles across ALL tenants
- metric: cross-tenant calibration score (the core composite)
- promoter: routes accepted knob changes to the advisor queue (NEVER
  auto-commits — every Tier-0 promotion needs an admin approval)
- runner: entry point invoked by `scripts/run_autoresearcher.py`
"""
