"""Phase 11B — Admin onboarding endpoint.

Fixes the "any company in 5 minutes" promise that Phase 10 broke: new
tenants were appearing in the super-admin's switcher as empty shells
because `tenant_registry` auto-registered domains on login but nothing
ever triggered the first pipeline pass.

New flow:

  1. Super-admin POSTs `/api/admin/onboard {name, ticker_hint?, domain?}`.
  2. Endpoint calls [engine.ingestion.company_onboarder.onboard_company]
     (existing Phase 8 flow — yfinance ticker resolution + financials +
     industry + news queries written to companies.json).
  3. On success, schedules a BackgroundTask that:
     - Calls `fetch_for_company(slug, limit=~10)` to pull ESG-filtered
       articles via NewsAPI.ai + Google News RSS.
     - Runs each article through the 12-stage pipeline (reuses
       [engine.main._run_article]).
     - Writes insight + perspectives + recommendations JSONs to
       `data/outputs/<slug>/...` and indexes them in SQLite.
  4. `onboarding_status` table tracks progress so the frontend modal can
     poll GET `/api/admin/onboard/{slug}/status` every 5s.

Gated by `manage_drip_campaigns` (super-admin only). Phase 23B — auto-detects
listings across NSE/BSE/NYSE/NASDAQ/LSE/Xetra/Euronext/HKEX and applies the
right framework region (INDIA / EU / US / UK / APAC / GLOBAL) so news
queries + mandatory frameworks match the company's home jurisdiction.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import require_auth
from api.auth_context import require_bearer_permission
from engine.models import onboarding_status

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin",
    tags=["admin-onboard"],
    dependencies=[
        Depends(require_auth),
        Depends(require_bearer_permission("manage_drip_campaigns")),
    ],
)


class OnboardRequest(BaseModel):
    """Phase 16 — at least one of name / ticker_hint / domain must be set.
    Pre-Phase-16 the schema required `name`. Now domain-only entry is the
    fastest happy path (e.g. POST {"domain": "tatachemicals.com"}).
    """
    name: str | None = Field(default=None, max_length=100)
    ticker_hint: str | None = None
    domain: str | None = None
    limit: int = Field(default=10, ge=1, le=50)


def _background_onboard(slug: str, name: str | None, ticker_hint: str | None, domain: str | None, limit: int) -> None:
    """Runs in FastAPI's BackgroundTasks pool. `slug` is already computed by
    the HTTP handler and the row is already seeded (state=pending). All
    state changes happen on this same slug. Exceptions are written to
    onboarding_status.error rather than raised."""
    # Lazy imports keep the HTTP request fast when the task is queued.
    from engine.config import load_companies
    from engine.index import tenant_registry
    from engine.ingestion.company_onboarder import onboard_company
    from engine.ingestion.news_fetcher import fetch_for_company
    from engine.main import _run_article

    try:
        # Stage 1: company_onboarder resolves ticker + writes companies.json
        onboarding_status.upsert(slug, state="fetching")
        result = onboard_company(
            company_name=name,
            ticker_hint=ticker_hint,
            domain=domain,
        )
        if result is None:
            onboarding_status.mark_failed(
                slug,
                "could not resolve ticker — pass an explicit ticker_hint "
                "(e.g. 'TATACHEM.NS' for NSE, 'AAPL' for NASDAQ, 'SAP.DE' "
                "for Xetra, 'BARC.L' for LSE).",
            )
            return

        # Sanity: the onboarder may have derived a slightly different slug
        # (e.g. strips "Limited" / "Ltd", or expands "tatachemicals" →
        # "tata-chemicals-limited"). Use its authoritative value going
        # forward — and migrate the status row if they differ. Note: we
        # do NOT mark the alias as ready here; we wait until the canonical
        # finishes and then mirror its final stats so the UI shows the
        # right counts (Phase 21 fix).
        canonical_slug = result.slug
        alias_slug = slug if slug != canonical_slug else None
        if alias_slug:
            logger.info("[onboard] slug adjusted: requested=%s actual=%s",
                        alias_slug, canonical_slug)
            # Mirror the canonical's "fetching" state to keep the alias-poll
            # progress smooth; clears any stale error from a prior failed run.
            onboarding_status.upsert(alias_slug, state="fetching")
            onboarding_status.upsert(canonical_slug, state="fetching")

        # Register in tenant_registry so the super-admin switcher picks it up.
        tenant_registry.register_tenant(
            domain=(domain or result.slug + ".example.com"),
            name=result.name,
            industry=result.industry,
            source="onboarded",
        )

        # Stage 2: fetch ESG-filtered articles
        company_obj = next((c for c in load_companies() if c.slug == canonical_slug), None)
        if company_obj is None:
            onboarding_status.mark_failed(canonical_slug, "companies.json write succeeded but reload failed")
            return

        fresh = fetch_for_company(company_obj, max_per_query=3)
        onboarding_status.upsert(canonical_slug, state="analysing", fetched=len(fresh))
        logger.info("[onboard %s] fetched %d articles", canonical_slug, len(fresh))

        # Stage 3: run each article through the full 12-stage pipeline.
        # Phase 22.1 — only count NON-rejected articles in `analysed` so
        # the dashboard's "fetched/analysed" stats match the indexed
        # rows the user will actually see. Pre-fix: a German prospect
        # whose 2 articles were both relevance-rejected showed
        # "ready 2/2 analysed" but the feed was empty.
        attempted = 0
        analysed = 0
        home_count = 0
        for article in fresh:
            if attempted >= limit:
                break
            attempted += 1
            article_dict = {
                "id": article.id,
                "title": article.title,
                "content": article.content,
                "summary": article.summary,
                "source": article.source,
                "url": article.url,
                "published_at": article.published_at,
                "metadata": article.metadata,
            }
            try:
                summary = _run_article(article_dict, company_obj)
                if not summary.rejected:
                    analysed += 1
                    if summary.tier == "HOME":
                        home_count += 1
                onboarding_status.upsert(canonical_slug, analysed=analysed, home_count=home_count)
            except Exception as exc:
                logger.exception("[onboard %s] article %s failed: %s", canonical_slug, article.id, exc)
                # Continue with the rest — one bad article shouldn't fail the whole onboarding.
                continue

        # Phase 22.1 — register the alias→canonical mapping so the
        # user's session (JWT bound to `alias_slug` from the login-time
        # domain stem) transparently reads the article_index rows the
        # pipeline wrote under `canonical_slug`. Without this the
        # dashboard stays empty even when the analysis succeeded.
        if alias_slug:
            from engine.index import sqlite_index
            sqlite_index.register_alias(alias_slug, canonical_slug)

        onboarding_status.mark_ready(canonical_slug,
                                     fetched=len(fresh),
                                     analysed=analysed,
                                     home_count=home_count)
        # Mirror canonical's final stats to the alias slug so the
        # frontend (which is polling the placeholder) shows the right
        # numbers + clears the stale error from any earlier failed run.
        if alias_slug:
            onboarding_status.mark_ready(alias_slug,
                                         fetched=len(fresh),
                                         analysed=analysed,
                                         home_count=home_count)
        logger.info("[onboard %s] done: %d/%d analysed (rejected %d), %d HOME",
                    canonical_slug, analysed, attempted, attempted - analysed, home_count)
    except Exception as exc:
        logger.exception("[onboard] unexpected failure: %s", exc)
        tb = traceback.format_exc()[:500]
        # Mark failure on whichever slug is being polled. If the alias and
        # canonical both exist, mark both so neither shows stale state.
        onboarding_status.mark_failed(slug, f"{exc}\n{tb}")
        try:
            if 'canonical_slug' in dir() and canonical_slug != slug:
                onboarding_status.mark_failed(canonical_slug, f"{exc}\n{tb}")
        except Exception:
            pass


@router.post("/onboard", status_code=202)
def onboard(
    body: OnboardRequest,
    background: BackgroundTasks,
) -> dict[str, Any]:
    """Kick off the onboarding pipeline. Returns immediately with 202.

    Phase 16: accepts domain-only entry (no `name` required). The slug
    is derived from whichever input is present; the background task
    re-slugifies once yfinance returns the canonical company name, so
    a domain-only POST may produce a tighter slug than the placeholder
    returned here. Frontend should use the slug from the status endpoint
    rather than this 202 response for redirects.
    """
    from engine.ingestion.company_onboarder import _slugify, _domain_to_search_term
    if not (body.name or body.ticker_hint or body.domain):
        raise HTTPException(
            status_code=422,
            detail="Provide at least one of `name`, `ticker_hint`, or `domain`",
        )
    seed = body.name or _domain_to_search_term(body.domain or "") or body.ticker_hint or "pending"
    expected_slug = _slugify(seed)

    # Seed the status row up-front so pollers never race with the task start.
    onboarding_status.upsert(expected_slug, state="pending")

    background.add_task(
        _background_onboard,
        slug=expected_slug,
        name=body.name,
        ticker_hint=body.ticker_hint,
        domain=body.domain,
        limit=body.limit,
    )

    return {
        "status": "queued",
        "slug": expected_slug,
        "poll_url": f"/api/admin/onboard/{expected_slug}/status",
    }


@router.get("/onboard/{slug}/status")
def onboard_status(slug: str) -> dict[str, Any]:
    """Return the current onboarding progress for a slug. 404 if never kicked off."""
    status = onboarding_status.get(slug)
    if status is None:
        raise HTTPException(status_code=404, detail=f"no onboarding record for {slug}")
    return status.to_dict()
