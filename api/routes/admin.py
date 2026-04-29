"""Phase 10 admin endpoints.

Super-admin-only routes for the CompanySwitcher and cross-tenant operations.
Mounted BEFORE legacy_adapter in api/main.py so these win over any overlapping
stubs.

Everything here is gated by `require_bearer_permission("super_admin")` — the
bearer-token decode path in api/auth_context.py. Regular client tokens
(which don't carry `super_admin` in their permissions claim) will see 403.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from api.auth import require_auth
from api.auth_context import require_bearer_permission
from engine.config import load_companies
from engine.index import sqlite_index, tenant_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/tenants")
def list_tenants(
    _: None = Depends(require_auth),
    __: dict[str, Any] = Depends(require_bearer_permission("super_admin")),
) -> list[dict[str, Any]]:
    """Return every tenant the super-admin can switch into.

    Merges two sources:

      1. **Target companies** — the 7 hardcoded entries in
         `config/companies.json`. Tagged `source='target'`.
      2. **Onboarded tenants** — every company that has logged in via
         `/api/auth/login`. Tagged `source='onboarded'`.

    Dedupes by slug (target companies win over onboarded ones with the same
    slug, so the curated name/industry survives). Target companies come
    first, then onboarded sorted by most recent login.

    Used by the CompanySwitcher dropdown in the header.
    """
    # Ensure target companies are seeded into the registry (idempotent).
    try:
        companies = load_companies()
        tenant_registry.seed_target_companies(companies)
    except Exception as exc:
        logger.warning("admin.tenants seed_target_companies failed: %s", exc)
        companies = []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Emit the 7 target companies first (stable order from config)
    for c in companies:
        if c.slug in seen:
            continue
        count, last_at = _tenant_stats(c.slug)
        out.append(
            {
                "id": c.slug,
                "slug": c.slug,
                "name": c.name,
                "domain": c.domain,
                "industry": c.industry,
                "source": "target",
                "article_count": count,
                "last_analysis_at": last_at,
            }
        )
        seen.add(c.slug)

    # Then onboarded tenants — every company/domain that has logged in
    try:
        onboarded = tenant_registry.list_tenants()
    except Exception as exc:
        logger.warning("admin.tenants list_tenants failed: %s", exc)
        onboarded = []

    for row in onboarded:
        slug = row.get("slug")
        if not slug or slug in seen:
            continue
        count, last_at = _tenant_stats(slug)
        out.append(
            {
                "id": slug,
                "slug": slug,
                "name": row.get("name") or slug.replace("-", " ").title(),
                "domain": row.get("domain"),
                "industry": row.get("industry"),
                "source": row.get("source") or "onboarded",
                "article_count": count,
                "last_analysis_at": last_at or row.get("last_seen_at"),
            }
        )
        seen.add(slug)

    return out


def _tenant_stats(slug: str) -> tuple[int, str | None]:
    """Return (article_count, most_recent_published_at_iso) for a company slug."""
    try:
        count = sqlite_index.count(company_slug=slug)
    except Exception as exc:
        logger.debug("admin.tenants count(%s) failed: %s", slug, exc)
        count = 0

    last_at: str | None = None
    try:
        rows = sqlite_index.query_feed(company_slug=slug, limit=1, offset=0)
        if rows:
            last_at = rows[0].get("published_at") or rows[0].get("created_at")
    except Exception as exc:
        logger.debug("admin.tenants query_feed(%s) failed: %s", slug, exc)

    return count, last_at
