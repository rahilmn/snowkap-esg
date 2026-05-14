"""Tier 1 — per-tenant autoresearcher specifics.

- corpus: held-out articles filtered to one tenant
- metric: per-tenant calibration score (re-uses core composite metric)
- promoter: routes accepted knobs through R6 → CompanyAgent belief update
- runner: entry point invoked by `scripts/run_autoresearcher.py --tier tenant`
"""
