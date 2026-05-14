"""W1 — Snowkap wiki layer.

3-tier hierarchical markdown wiki built over the existing Snowkap-ESG
intelligence outputs (ontology + audit + insights + persona).

  Tier 0 — System core   (cross-tenant institutional memory)
  Tier 1 — Tenant        (per-company filtering + analysis)
  Tier 2 — User          (per-analyst painpoints + history)

Pure derivation — every page is rebuildable from the existing
data/inputs/, data/outputs/, data/audit/, data/agents/ directories
plus the ontology TTL files. No new source of truth introduced.
"""
