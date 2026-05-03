"""/api/companies routes — list companies + per-company stats.

Phase 24.1 — the "All Companies" view is gated to the Snowkap Sales admin
only (sales@snowkap.co.in by default; overridable via
``SNOWKAP_SALES_ADMIN_EMAIL``). Every other authenticated user sees only
the tenant their JWT is bound to. Unauthenticated callers (machine-to-
machine via ``X-API-Key``) keep the legacy unrestricted access.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_api_key
from api.auth_context import (
    get_bearer_claims,
    is_snowkap_sales_admin,
    is_snowkap_super_admin,
)
from engine.config import Company, get_company, load_companies
from engine.index.sqlite_index import count, query_feed, resolve_slug

router = APIRouter(
    prefix="/api/companies",
    tags=["companies"],
    dependencies=[Depends(require_api_key)],
)


def _company_payload(c: Company) -> dict:
    return {
        "slug": c.slug,
        "name": c.name,
        "domain": c.domain,
        "industry": c.industry,
        "sasb_category": c.sasb_category,
        "market_cap": c.market_cap,
        "listing_exchange": c.listing_exchange,
        "headquarter_city": c.headquarter_city,
        "headquarter_country": c.headquarter_country,
        "headquarter_region": c.headquarter_region,
    }


def _visible_companies_for(claims: dict[str, Any]) -> tuple[list[Company], bool]:
    """Return ``(visible_companies, is_sales_admin)``.

    Phase 24.1 visibility rules:

    * Unauthenticated callers (no ``sub`` in claims) — full list. The route
      is API-key gated by the router-level dependency, so this only fires
      for trusted machine-to-machine callers.
    * Snowkap Sales admin (``sales@snowkap.co.in`` or whatever
      ``SNOWKAP_SALES_ADMIN_EMAIL`` points at) — full list. The "All
      Companies" tab is theirs.
    * Everyone else (regular customers, other Snowkap super-admins) — only
      the company their JWT is bound to. Other Snowkap admins keep their
      super-admin permissions for onboarding / sharing — they just don't
      get the cross-tenant aggregate view.
    """
    sub_email = (claims.get("sub") or "").strip()
    if not sub_email:
        return load_companies(), False

    if is_snowkap_sales_admin(sub_email):
        return load_companies(), True

    own_slug = (claims.get("company_id") or "").strip() or None
    if not own_slug:
        # Token has no tenant scope — non-sales super-admin or a stale
        # token without ``company_id``. Return empty list; the frontend
        # will hide the switcher.
        return [], False

    own_canon = resolve_slug(own_slug) or own_slug
    visible = [
        c for c in load_companies() if c.slug in (own_slug, own_canon)
    ]
    return visible, False


@router.get("")
def list_companies(claims: dict[str, Any] = Depends(get_bearer_claims)) -> dict:
    visible, _ = _visible_companies_for(claims)
    return {
        "count": len(visible),
        "companies": [_company_payload(c) for c in visible],
    }


@router.get("/{slug}")
def get_company_detail(
    slug: str,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict:
    """Per-company detail. Phase 24.1 — non-sales users can only fetch
    their own bound tenant. Everyone else gets 403.
    """
    sub_email = (claims.get("sub") or "").strip()
    if sub_email and not is_snowkap_sales_admin(sub_email):
        own_slug = (claims.get("company_id") or "").strip() or None
        if not own_slug:
            raise HTTPException(
                status_code=403,
                detail="Token has no tenant scope. Re-authenticate.",
            )
        requested_canon = resolve_slug(slug) or slug
        own_canon = resolve_slug(own_slug) or own_slug
        if requested_canon != own_canon:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Cross-tenant access denied — your account is bound to "
                    f"{own_canon!r}, not {requested_canon!r}."
                ),
            )

    try:
        company = get_company(slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    total = count(company_slug=slug)
    home = count(company_slug=slug, tier="HOME")
    secondary = count(company_slug=slug, tier="SECONDARY")
    latest = query_feed(company_slug=slug, limit=1)
    latest_id = latest[0]["id"] if latest else None
    return {
        **_company_payload(company),
        "stats": {
            "total_insights": total,
            "home_tier": home,
            "secondary_tier": secondary,
            "latest_insight_id": latest_id,
        },
    }
