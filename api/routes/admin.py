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
import time
from typing import Any

from fastapi import APIRouter, Depends

from api.auth import require_auth
from api.auth_context import require_bearer_permission
from engine.config import load_companies
from engine.index import sqlite_index, tenant_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# W1 — 30s in-memory TTL cache for the tenant list. Pre-fix every header
# render hit Supabase 27× to compute per-tenant counts; even after the bulk
# query that's still one round-trip per page load. The cache makes typical
# navigation cost zero round-trips.
_TENANT_CACHE_TTL_S = 30.0
_tenant_cache: dict[str, Any] = {"expires_at": 0.0, "payload": None}


def _reset_tenant_cache() -> None:
    """Test hook — drop the in-memory cache so a fresh call hits Supabase."""
    _tenant_cache["expires_at"] = 0.0
    _tenant_cache["payload"] = None


def _tenant_stats(slug: str) -> tuple[int, str | None]:
    """Back-compat shim — original per-slug helper.

    The hot path now uses `sqlite_index.tenant_stats_bulk()` (single GROUP BY).
    This function is kept only for the older test fixtures that import it
    directly. Returns (0, None) on backend error so callers stay resilient.
    """
    stats = sqlite_index.tenant_stats_bulk()
    s = stats.get(slug, {})
    return int(s.get("count", 0)), s.get("latest_at")


@router.get("/tenants")
def list_tenants(
    _: None = Depends(require_auth),
    __: dict[str, Any] = Depends(require_bearer_permission("super_admin")),
) -> dict[str, Any]:
    """Return every tenant the super-admin can switch into.

    Merges two sources:

      1. **Target companies** — the 7 hardcoded entries in
         `config/companies.json`. Tagged `source='target'`.
      2. **Onboarded tenants** — every company that has logged in via
         `/api/auth/login`. Tagged `source='onboarded'`.

    Dedupes by slug (target companies win over onboarded ones with the same
    slug, so the curated name/industry survives). Target companies come
    first, then onboarded sorted by most recent login.

    Response shape (W1):
      {
        "companies": [...AdminTenant],
        "meta": {"warnings": [...str]}
      }

    `meta.warnings` carries any non-fatal degradations (Supabase down, etc.)
    so the CompanySwitcher can render a "(degraded)" badge instead of
    blanking the dropdown.
    """
    now = time.time()
    if _tenant_cache["payload"] and _tenant_cache["expires_at"] > now:
        return _tenant_cache["payload"]

    warnings: list[str] = []

    # Ensure target companies are seeded into the registry (idempotent).
    try:
        companies = load_companies()
        tenant_registry.seed_target_companies(companies)
    except Exception as exc:
        msg = f"seed_target_companies: {type(exc).__name__}: {exc}"
        logger.warning("admin.tenants %s", msg)
        warnings.append(msg)
        companies = []

    # W1 — bulk per-slug stats in ONE Postgres query. Pre-fix this was
    # an N+1 (count + query_feed per slug × 27 tenants) which exceeded the
    # 15s pgbouncer pooler ceiling and made the dropdown render
    # "Couldn't load tenants".
    try:
        stats = sqlite_index.tenant_stats_bulk()
    except Exception as exc:
        msg = f"tenant_stats_bulk: {type(exc).__name__}: {exc}"
        logger.warning("admin.tenants %s", msg)
        warnings.append(msg)
        stats = {}

    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Emit the 7 target companies first (stable order from config)
    for c in companies:
        if c.slug in seen:
            continue
        s = stats.get(c.slug, {})
        out.append(
            {
                "id": c.slug,
                "slug": c.slug,
                "name": c.name,
                "domain": c.domain,
                "industry": c.industry,
                "source": "target",
                "article_count": int(s.get("count", 0)),
                "last_analysis_at": s.get("latest_at"),
            }
        )
        seen.add(c.slug)

    # Then onboarded tenants — every company/domain that has logged in
    try:
        onboarded = tenant_registry.list_tenants()
    except Exception as exc:
        msg = f"list_tenants: {type(exc).__name__}: {exc}"
        logger.warning("admin.tenants %s", msg)
        warnings.append(msg)
        onboarded = []

    for row in onboarded:
        slug = row.get("slug")
        if not slug or slug in seen:
            continue
        s = stats.get(slug, {})
        out.append(
            {
                "id": slug,
                "slug": slug,
                "name": row.get("name") or slug.replace("-", " ").title(),
                "domain": row.get("domain"),
                "industry": row.get("industry"),
                "source": row.get("source") or "onboarded",
                "article_count": int(s.get("count", 0)),
                "last_analysis_at": s.get("latest_at") or row.get("last_seen_at"),
            }
        )
        seen.add(slug)

    payload: dict[str, Any] = {"companies": out, "meta": {"warnings": warnings}}
    _tenant_cache["payload"] = payload
    _tenant_cache["expires_at"] = now + _TENANT_CACHE_TTL_S
    return payload
