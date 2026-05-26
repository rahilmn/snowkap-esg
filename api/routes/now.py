"""POW-4 — `/api/now/*` endpoints (industry-shared deck + per-company view).

Replaces the legacy `/api/news/live` + `/api/news/feed` paths for the
new Power-of-Now UI. Reads from `article_pool` (industry-shared) joined
with `company_article_view` (per-company personalised analysis).

Endpoints (all JWT-gated; identity from the `sub` claim):
  GET /api/now/feed?company={slug}&limit={n}
       → Up to 10 articles for the user's company deck. Top-3 are the
         pre-personalised CRITICAL articles; slots 4-10 fill from
         HIGH → MEDIUM → LOW within the 30-day window.
  GET /api/now/article/{id}
       → Full article: shared_analysis + the caller's personalised_analysis.
         Returns 404 when the article isn't visible to the caller's
         industry. Returns 202 with `status: "warming"` when a row
         in `company_article_view` is missing (cold-view path).

See: docs/POWER_OF_NOW_ARCHITECTURE.md §5.1, §4.4.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import require_api_key
from api.auth_context import get_bearer_claims
from engine.config import get_company
from engine.index.sqlite_index import resolve_slug
from engine.models import article_pool, company_article_view

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["now"],
    dependencies=[Depends(require_api_key)],
)


def _caller_company(claims: dict[str, Any], company_param: str | None) -> tuple[str, str]:
    """Resolve the canonical company_slug + industry for this request.

    The `company` query param is the contract; we also enforce that it
    matches the JWT's `company_id` (with alias resolution) so no user
    can read another tenant's deck.
    """
    own = (claims.get("company_id") or "").strip()
    requested = (company_param or "").strip()
    if not requested and not own:
        raise HTTPException(status_code=422, detail="company query parameter required")
    if not requested:
        requested = own

    canonical = resolve_slug(requested) or requested
    own_canonical = resolve_slug(own) or own
    if own and canonical != own_canonical and "super_admin" not in (claims.get("permissions") or []):
        raise HTTPException(
            status_code=403,
            detail="Cross-tenant access denied. Pass your own company slug.",
        )

    # Phase 36 fix — gracefully handle the case where the tenant's slug
    # exists in their JWT but the company record never persisted (e.g.
    # auto-onboard kickoff failed silently). Pre-fix `get_company`
    # raised KeyError → 500 "Unknown company slug" → frontend showed
    # the unhelpful "Couldn't load the feed" toast. Now: return a clean
    # 404 with an actionable message so the frontend can show a
    # "Your company is still being set up" empty state.
    try:
        company = get_company(canonical)
    except KeyError:
        company = None
    industry = getattr(company, "industry", None) if company else None
    if not industry:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Company '{canonical}' is not yet onboarded. "
                "Submit the company via /settings/onboard or wait for "
                "the onboarding pipeline to complete."
            ),
        )
    return canonical, industry


@router.get("/api/now/feed")
def now_feed(
    company: str = Query(..., min_length=1, max_length=120),
    limit: int = Query(10, ge=1, le=20),
    max_age_days: int = Query(30, ge=1, le=365),
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    """The /now deck for a company.

    Returns up to `limit` articles (default 10), sorted by criticality
    band ASC (CRITICAL first) → criticality_score DESC → published_at
    DESC. Each row includes the industry-shared `shared_analysis` AND
    the caller's company-specific `personalised_analysis` so the
    frontend can render the WHY THIS MATTERS block with no extra
    fetch on swipe-up.
    """
    canonical, industry = _caller_company(claims, company)
    rows = company_article_view.deck_for_company(
        canonical, industry, max_age_days=max_age_days, limit=limit,
    )
    return {
        "company_slug": canonical,
        "industry": industry,
        "count": len(rows),
        "limit": limit,
        "max_age_days": max_age_days,
        "articles": rows,
    }


@router.get("/api/now/article/{article_id}")
def now_article(
    article_id: str,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    """Full article: shared facts + the caller's personalised view.

    Behaviour:
      - 200 with merged payload when both rows exist.
      - 202 with `status: "warming"` when `article_pool` has the row
        but `company_article_view` doesn't (cold-view path — the
        on-demand personalisation should kick off; for now we just
        return the shared view and surface a warming flag).
      - 404 when the article isn't in `article_pool` at all OR when
        it isn't material to the caller's industry.
    """
    if not article_id:
        raise HTTPException(status_code=422, detail="article_id required")

    pool_row = article_pool.get(article_id)
    if pool_row is None:
        raise HTTPException(status_code=404, detail=f"Article {article_id} not in pool.")

    # Industry visibility gate.
    canonical, industry = _caller_company(claims, None)
    if industry not in pool_row.material_industries:
        raise HTTPException(
            status_code=404,
            detail=f"Article {article_id} is not material to your industry.",
        )

    view_row = company_article_view.get(article_id, canonical)
    if view_row is None:
        # Cold view — personalisation hasn't been computed yet. Surface
        # the shared block + a warming flag so the UI can show
        # "Snowkap is analysing this article…" instead of an empty
        # narrative. The /api/news/trigger-analysis path already
        # handles the actual compute.
        return {
            "status": "warming",
            "article_id": article_id,
            "company_slug": canonical,
            "industry": industry,
            "shared": pool_row.to_dict(),
            "personalised": None,
        }

    return {
        "status": "ready",
        "article_id": article_id,
        "company_slug": canonical,
        "industry": industry,
        "shared": pool_row.to_dict(),
        "personalised": view_row.to_dict(),
    }
