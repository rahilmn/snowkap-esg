"""/api/companies routes — list companies + per-company stats."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_api_key
from engine.config import Company, get_company, load_companies
from engine.index.sqlite_index import count, query_feed

router = APIRouter(prefix="/api/companies", tags=["companies"], dependencies=[Depends(require_api_key)])


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


@router.get("")
def list_companies() -> dict:
    companies = load_companies()
    return {"count": len(companies), "companies": [_company_payload(c) for c in companies]}


@router.get("/{slug}")
def get_company_detail(slug: str) -> dict:
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
