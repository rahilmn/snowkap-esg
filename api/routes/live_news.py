"""Phase 31 — Live news endpoint.

Two surfaces:

* ``GET /api/news/live`` — hybrid feed. LLM-crafted sustainability +
  general queries hit Google News on the fly; merged + 30-day filtered
  + tagged with ``is_analyzed``.

* ``POST /api/news/live/analyze`` — bootstrap a live (un-analyzed)
  article into ``article_index`` by running stages 1-9 of the pipeline
  on its URL + summary. Stages 10-12 (deep insight + per-role
  explainer) run later on-demand via the existing
  ``/api/news/{id}/trigger-analysis`` flow that the ArticleDetailSheet
  already polls.

Tenant-scoped via the same dependency the legacy /news/feed uses —
regular users can only call this for their own slug; super-admins can
pass any.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import require_auth
from api.routes.legacy_adapter import tenant_scoped, _require_tenant_scope
from api.auth_context import get_bearer_claims
from engine.ingestion.live_fetcher import (
    fetch_live_for_company,
    _article_id as live_article_id,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["news-live"])


@router.get("/api/news/live")
def news_live(
    company: str = Query(
        ...,
        min_length=1,
        max_length=120,
        description="Company slug (canonical or alias).",
    ),
    limit: int = Query(10, ge=1, le=30),
    _: None = Depends(require_auth),
    _claims: dict[str, Any] = Depends(tenant_scoped(company_id_param="company")),
) -> dict[str, Any]:
    """Return live + hybrid news for a company.

    Sustainability-first 60/40 merge, 30-day window, deduped.
    See module docstring for the full response shape.
    """
    slug = (company or "").strip().lower()
    if not slug:
        raise HTTPException(status_code=422, detail="company is required")

    # Translate login-time aliases (e.g. "yesbank" from yesbank.com) to the
    # canonical baseline slug ("yes-bank") so the live feed reads the
    # cached entries instead of fetching from cold against a new tenant.
    # `resolve_slug` returns None when no alias is registered — fall
    # back to the raw slug in that case.
    from engine.index.sqlite_index import resolve_slug
    canonical = resolve_slug(slug) or slug

    try:
        result = fetch_live_for_company(canonical, limit=limit)
    except Exception as exc:  # noqa: BLE001 — live path must not 500
        logger.exception("news_live: live fetch failed for slug=%s: %s", slug, exc)
        return {
            "company_slug": slug,
            "items": [],
            "count": 0,
            "sustainability_count": 0,
            "general_count": 0,
            "queries_used": {},
            "cached": False,
            "error": f"live_fetch_failed: {type(exc).__name__}",
        }

    return result.to_dict(limit=limit)


# ---------------------------------------------------------------------------
# POST /api/news/live/analyze — bootstrap a live article into the index
# ---------------------------------------------------------------------------


class LiveAnalyzeRequest(BaseModel):
    """Body for POST /api/news/live/analyze.

    Sent by the frontend when the user opens a live article whose
    ``is_analyzed`` was false. Frontend has the title + URL from the
    live response and passes them through so the backend can build a
    full pipeline article dict without re-fetching the headline list.
    """
    url: str = Field(..., min_length=8, max_length=2048)
    company_slug: str = Field(..., min_length=1, max_length=120)
    title: str = Field(..., min_length=1, max_length=600)
    summary: str | None = Field(None, max_length=4000)
    source: str | None = Field(None, max_length=120)
    published_at: str | None = Field(None, max_length=64)
    image_url: str | None = Field(None, max_length=2048)


@router.post("/api/news/live/analyze")
def news_live_analyze(
    body: LiveAnalyzeRequest,
    _: None = Depends(require_auth),
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    """Bootstrap an un-analysed live article into the pipeline.

    Flow:
      1. Tenant-scope check on ``company_slug``.
      2. Resolve the company via ``engine.config.get_company``.
      3. Compute the canonical article_id from the URL (SHA256[:16]),
         matching what ``live_fetcher._article_id`` produced.
      4. Build the article dict + call ``_run_article()`` which runs
         stages 1-9 (NLP, themes, event, relevance, cascade, risk,
         frameworks, …) and writes the result to ``article_index`` +
         disk. If the relevance scorer rates it HOME, stages 10-12
         also fire here.
      5. Return ``{article_id, tier, status}``.

    After this returns, the frontend can hit the existing
    ``GET /api/news/{id}/analysis`` to read the result. If the result
    was SECONDARY, the user can still click View Insights to trigger
    stages 10-12 on-demand (same path SECONDARY articles already use).

    On failure returns 503 + structured error so the UI can render an
    informative retry banner instead of a generic spinner-timeout.
    """
    slug = (body.company_slug or "").strip().lower()
    _require_tenant_scope(slug, claims)

    # Resolve the company so the pipeline has all it needs (industry,
    # framework_region, calibration, painpoints, …).
    try:
        from engine.config import get_company
        company = get_company(slug)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown company slug '{slug}'. Onboard first.",
        )

    article_id = live_article_id(body.url)

    # Short-circuit if we already indexed this exact URL. Saves a
    # duplicate pipeline run when the frontend retries.
    from engine.index.sqlite_index import get_by_id
    existing = get_by_id(article_id)
    if existing:
        return {
            "article_id": article_id,
            "company_slug": existing.get("company_slug") or slug,
            "tier": existing.get("tier"),
            "status": "already_indexed",
        }

    article_dict = {
        "id": article_id,
        "title": body.title or "",
        "content": body.summary or body.title or "",
        "summary": body.summary or "",
        "source": body.source or "Google News",
        "url": body.url,
        "published_at": body.published_at or "",
        "metadata": {
            "source_type": "google_news_live",
            "image_url": body.image_url or "",
        },
    }

    # Phase 30 cost cap — refuse to run new LLM stages when the tenant's
    # daily LLM cap is exhausted. The check inside _run_article only
    # fires for HOME-tier (Stage 10-12); SECONDARY skips Stage 10 so
    # there's no LLM spend to guard. Still call it up-front so the user
    # sees a structured 503 instead of a partial run.
    try:
        from engine.llm.budget import assert_under_cap, TenantBudgetExceeded
        assert_under_cap(slug)
    except TenantBudgetExceeded as exc:
        return {
            "article_id": article_id,
            "company_slug": slug,
            "tier": None,
            "status": "daily_cap_reached",
            "spent_usd": getattr(exc, "spent", None),
            "cap_usd": getattr(exc, "cap", None),
        }
    except Exception:  # noqa: BLE001 — budget check is best-effort
        pass

    try:
        from engine.main import _run_article
        summary = _run_article(article_dict, company)
    except Exception as exc:  # noqa: BLE001 — return structured error, not a 500
        logger.exception(
            "news_live_analyze: pipeline failed for %s (%s): %s",
            article_id, body.url, exc,
        )
        raise HTTPException(
            status_code=503,
            detail=f"pipeline_failed: {type(exc).__name__}: {str(exc)[:200]}",
        )

    return {
        "article_id": article_id,
        "company_slug": slug,
        "tier": getattr(summary, "tier", None),
        "rejected": bool(getattr(summary, "rejected", False)),
        "status": "indexed",
    }
