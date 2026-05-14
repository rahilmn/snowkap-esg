"""Governance subsystem — read-only-by-default checks and prelude gates.

Per the Base Version → Snowkap adoption build sequence (L0):
- L0: ``probe`` — search **6 sources** for prior art before any TTL/config
  mutation (decision_log, discovery_audit, discovery_staging, discovered_ttl,
  tenant_painpoints, live_sparql). Spec listed 5; staging was added as an
  explicit 6th source so gate 1 (live Lloyds Transparency divergence
  detection) could be satisfied — see ``probe.py`` module docstring for the
  rationale. Future layers add ``signoff``, ``probe`` extensions,
  audit-the-audit gates.

This package never imports from ``engine.advisor`` (avoids circular imports
with the L6 reactive advisor layer). It may be imported by ``engine.audit``
and the ``snowkap`` Claude Code skill manifests.
"""
