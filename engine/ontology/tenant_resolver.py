"""Phase 24 (W5) — multi-tenant ontology resolver.

Lays the foundation for external SaaS deployment: each tenant
(consulting firm, asset manager, internal product) gets its own
``data/ontology/tenants/<tenant_id>/extension.ttl`` overlaid on the
shared Layer 1 core.

Design choices
--------------

1. **Layer 1 is the existing TTL set in ``data/ontology/``** —
   ``schema.ttl``, ``knowledge_base.ttl``, ``knowledge_*.ttl``,
   ``primitives_*.ttl``, etc. They stay where they are; we add a header
   comment marking them read-only. The actual extraction of
   tenant-customisable triples (HeadlineRule, MaterialityWeight,
   StakeholderPosition, etc.) into Layer 3 is **deferred** to the first
   external-tenant onboarding so we don't risk regressing the 169-test
   internal product on a speculative migration. This module ships the
   *resolver pattern* now; the *content move* lands when there's a
   second tenant to validate it against.

2. **Per-tenant overrides via TTL union.** A tenant's
   ``extension.ttl`` is parsed into the shared graph after Layer 1 —
   meaning any URI re-asserted in the extension wins (rdflib treats
   identical triples as a no-op; conflicting rdfs:label / weight
   triples for the same subject coexist as multiple values, which the
   SPARQL query layer can then choose between via ORDER BY DESC of a
   provenance predicate). Default tenant is ``_global`` (matches the
   pre-W5 behaviour exactly).

3. **Active-tenant ContextVar instead of plumbing tenant_id everywhere.**
   ``active_tenant("acme_capital")`` is a context manager that sets a
   ``contextvars.ContextVar``. The graph singleton (``get_graph()``)
   reads that var to decide which tenant's graph to return. This means
   the existing 28 SPARQL query functions in
   ``engine.ontology.intelligence`` need **zero signature changes** —
   their ``_graph(graph)`` helper resolves to the active tenant's graph
   automatically. API middleware sets the var per-request from
   ``X-Tenant-Id``; tests set it explicitly via the context manager.

4. **Cross-process safety not required.** Tenant graphs are per-process
   in-memory (rdflib). The on-disk TTL is the durable artefact — every
   pipeline restart re-loads from disk, so a worker on host A and a
   worker on host B each maintain their own tenant graph cache.
"""

from __future__ import annotations

import contextvars
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TENANT = "_global"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TENANTS_DIR = REPO_ROOT / "data" / "ontology" / "tenants"


# ---------------------------------------------------------------------------
# Active tenant ContextVar
# ---------------------------------------------------------------------------


_ACTIVE_TENANT: contextvars.ContextVar[str] = contextvars.ContextVar(
    "snowkap_active_tenant", default=DEFAULT_TENANT,
)


def get_active_tenant() -> str:
    """Return the tenant_id for the current execution context.

    Defaults to ``_global``. Set by:
      * ``with active_tenant("acme_capital"): ...`` (tests, scripts)
      * The API tenant-id middleware (per-request, from ``X-Tenant-Id``)
    """
    return _ACTIVE_TENANT.get()


@contextmanager
def active_tenant(tenant_id: str) -> Iterator[None]:
    """Context manager: set the active tenant for the duration of the
    ``with`` block. Resets to the previous value on exit (works
    correctly in nested blocks + async contexts).
    """
    if not tenant_id:
        tenant_id = DEFAULT_TENANT
    token = _ACTIVE_TENANT.set(tenant_id)
    try:
        yield
    finally:
        _ACTIVE_TENANT.reset(token)


# ---------------------------------------------------------------------------
# Tenant directory helpers
# ---------------------------------------------------------------------------


def tenant_dir(tenant_id: str) -> Path:
    """Return the directory holding a tenant's Layer 3 artefacts.

    Layout::

        data/ontology/tenants/<tenant_id>/
            extension.ttl       # Layer 3 RDF overrides
            discovery.json      # /discover-tenant-config output (W5 skill)
            metadata.json       # display name + onboarded_at

    Directory is NOT auto-created here; ``ensure_tenant_dir`` does that
    explicitly so callers reading-only don't accidentally materialise a
    tenant on disk.
    """
    return TENANTS_DIR / tenant_id


def tenant_extension_path(tenant_id: str) -> Path:
    """Path to a tenant's Layer 3 extension TTL file."""
    return tenant_dir(tenant_id) / "extension.ttl"


def ensure_tenant_dir(tenant_id: str) -> Path:
    """Create the tenant's directory if missing. Returns the path."""
    d = tenant_dir(tenant_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_tenants() -> list[str]:
    """Return all tenant_ids present on disk (each is a subdirectory of
    ``data/ontology/tenants/``). Sorted alphabetically. Returns at least
    ``[DEFAULT_TENANT]`` even when the directory is empty."""
    if not TENANTS_DIR.exists():
        return [DEFAULT_TENANT]
    found = sorted(
        p.name for p in TENANTS_DIR.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )
    if DEFAULT_TENANT not in found:
        found.insert(0, DEFAULT_TENANT)
    return found


def tenant_exists(tenant_id: str) -> bool:
    """True iff the tenant has a directory on disk. ``_global`` always
    counts as existing (it's the implicit default)."""
    if tenant_id == DEFAULT_TENANT:
        return True
    return tenant_dir(tenant_id).is_dir()
