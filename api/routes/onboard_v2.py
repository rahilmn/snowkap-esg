"""Phase 45 — Single synchronous onboarding endpoint.

Replaces the worker-queue + SSE + state-machine + alias-bridge complexity
of the legacy `/api/me/onboard` flow with one HTTP request that does
everything in-process and returns when done.

Flow (per request):

  1. Validate domain        (~0s)
  2. Resolve company via Opus 4.6 (LLM company resolver)   (~5s)
  3. Authorize: caller's email domain matches OR super-admin
  4. Upsert company row in Postgres (companies table)
  5. Register slug alias (input → canonical) if they differ
  6. Construct Company dataclass for the pipeline
  7. Fetch news via fetch_for_company  (~30-60s)
  8. Run pipeline on top-5 articles IN PARALLEL (~90-130s)
     - Stages 1-12 + lede per article via _run_article
     - Concurrency: ThreadPoolExecutor(max_workers=3)
     - Per-article try/except — no single failure aborts the batch
  9. Return 200 with full summary {slug, articles, elapsed}

Hard Postgres-only — fails fast with HTTP 503 if the active backend is
SQLite. No SQLite fallback paths, no dual-write.

Total expected wall-clock: ~120-180s for a fresh onboard with 3-5
fetched articles. Within the user's 2-3 min bar.
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
import traceback
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import require_auth
from api.auth_context import get_bearer_claims, is_snowkap_super_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onboard", tags=["onboard-v2"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class OnboardV2Request(BaseModel):
    domain: str = Field(..., min_length=3, max_length=253)
    limit: int = Field(default=5, ge=1, le=10)
    force_refresh: bool = Field(
        default=False,
        description=(
            "When True, re-fetches news + re-runs pipeline even if the "
            "company is already in Postgres. Default False = idempotent."
        ),
    )


class ArticleSummary(BaseModel):
    article_id: str
    title: str
    url: str
    tier: str
    rejected: bool


class OnboardV2Response(BaseModel):
    status: str  # "ready" | "no_articles" | "partial"
    slug: str
    canonical_name: str
    industry: str
    ticker: str
    elapsed_seconds: float
    fetched_count: int
    analysed_count: int
    home_count: int
    articles: list[ArticleSummary]
    warning: str = ""
    confidence: str = "medium"  # from LLM resolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_domain(raw: str) -> str:
    """Lowercase + strip protocol/path. Returns "" if not parseable."""
    import re
    s = (raw or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"^https?://", "", s)
    s = s.split("/", 1)[0]
    s = s.split("?", 1)[0]
    s = s.removeprefix("www.")
    _domain_re = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
    return s if _domain_re.match(s) else ""


def _email_domain(email: str | None) -> str:
    if not email or "@" not in email:
        return ""
    return email.split("@", 1)[1].strip().lower()


def _domain_matches_caller(target: str, caller_email: str) -> bool:
    """True when `target` is the caller's own email domain. Super-admins bypass."""
    if is_snowkap_super_admin(caller_email):
        return True
    own = _email_domain(caller_email)
    if not own:
        return False
    return target == own or target.endswith("." + own) or own.endswith("." + target)


def _ensure_postgres() -> None:
    """Hard-fail if not on Postgres. Phase 45 is Supabase-only."""
    from engine.db.connection import is_postgres, get_backend
    if not is_postgres():
        raise HTTPException(
            status_code=503,
            detail=(
                f"Onboarding v2 requires Postgres backend; active backend is "
                f"'{get_backend()}'. Set SUPABASE_DATABASE_URL in Replit Secrets."
            ),
        )


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------


@router.post("/v2", response_model=OnboardV2Response)
def onboard_v2(
    body: OnboardV2Request,
    _: None = Depends(require_auth),
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> OnboardV2Response:
    """Synchronous onboarding — Postgres only.

    Returns 200 with complete data when done. No background work, no
    state machine, no SSE polling. Times out at the HTTP-client level
    if it exceeds the client's timeout (typically 240s).
    """
    t0 = time.monotonic()

    # ── 0. Prerequisite: Postgres ────────────────────────────────────────
    _ensure_postgres()

    # ── 1. Validate + normalise domain ───────────────────────────────────
    domain = _normalise_domain(body.domain)
    if not domain:
        raise HTTPException(
            status_code=422,
            detail=f"Could not parse '{body.domain}' as a domain (e.g. 'acme.com').",
        )

    # ── 2. Resolve company via LLM ───────────────────────────────────────
    from engine.ingestion.llm_company_resolver import resolve_company_from_domain
    info = resolve_company_from_domain(domain)
    if info is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Could not identify the company for domain '{domain}'. "
                "The LLM resolver returned no match. Try the company's main "
                "corporate domain (e.g. 'reliance.com' not 'reliance-jio.com')."
            ),
        )
    logger.info(
        "[onboard_v2] resolved %s → %s (%s, ticker=%s, conf=%s)",
        domain, info.canonical_name, info.industry,
        info.primary_ticker, info.confidence,
    )

    # ── 3. Authorize ─────────────────────────────────────────────────────
    caller_email = (claims.get("sub") or claims.get("email") or "").strip().lower()
    if not _domain_matches_caller(domain, caller_email):
        logger.warning(
            "[onboard_v2] domain mismatch — caller=%s tried domain=%s",
            caller_email or "<anon>", domain,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                f"You can only onboard your own company's domain. "
                f"Your email is on '{_email_domain(caller_email) or 'unknown'}', "
                f"but you requested '{domain}'."
            ),
        )

    # ── 4. Upsert company row in Postgres ────────────────────────────────
    from engine.models import companies_store
    from engine.config import invalidate_companies_cache

    try:
        companies_store.upsert(
            slug=info.slug,
            name=info.canonical_name,
            domain=domain,
            industry=info.industry,
            market_cap_tier=info.market_cap_tier,
            yfinance_ticker=info.primary_ticker,
            framework_region=info.framework_region,
            primitive_calibration={},  # Phase 17 calibration deferred — Stage 17c handles
            created_by_user=caller_email or None,
            status="active",
        )
        invalidate_companies_cache()
    except Exception as exc:
        logger.exception("[onboard_v2] companies_store.upsert failed for %s: %s",
                         info.slug, exc)
        raise HTTPException(
            status_code=500,
            detail=f"Postgres write failed: {type(exc).__name__}: {exc}",
        )

    # ── 5. Register slug alias if input → canonical differs ──────────────
    from engine.ingestion.company_onboarder import _slugify as _input_slugify
    input_slug = _input_slugify(domain.split(".")[0])
    if input_slug and input_slug != info.slug:
        try:
            from engine.index import sqlite_index
            sqlite_index.register_alias(input_slug, info.slug)
            logger.info("[onboard_v2] alias registered: %s → %s",
                        input_slug, info.slug)
        except Exception as exc:
            # Non-fatal — the canonical slug still resolves directly
            logger.warning("[onboard_v2] alias register failed (non-fatal): %s", exc)

    # ── 6. Construct Company dataclass for the pipeline ─────────────────
    from engine.config import Company
    company_obj = Company(
        name=info.canonical_name,
        slug=info.slug,
        domain=domain,
        industry=info.industry,
        sasb_category=info.sasb_category,
        market_cap=info.market_cap_tier,
        listing_exchange=_exchange_from_ticker(info.primary_ticker),
        headquarter_city=info.headquarter_city or "Unknown",
        headquarter_country=info.headquarter_country or "",
        headquarter_region=info.framework_region,
        news_queries=_build_news_queries(info),
        primitive_calibration={},
        yfinance_ticker=info.primary_ticker,
        eodhd_ticker=None,
        framework_region=info.framework_region,
        sustainability_query=None,
        general_query=None,
    )

    # ── 7. Fetch news ────────────────────────────────────────────────────
    try:
        from engine.ingestion.news_fetcher import fetch_for_company
        fresh = fetch_for_company(company_obj, max_per_query=3)
    except Exception as exc:
        logger.exception("[onboard_v2] news fetch failed for %s: %s",
                         info.slug, exc)
        raise HTTPException(
            status_code=500,
            detail=f"News fetch failed: {type(exc).__name__}: {exc}",
        )

    logger.info("[onboard_v2] fetched %d fresh articles for %s",
                len(fresh), info.slug)

    if not fresh:
        elapsed = time.monotonic() - t0
        return OnboardV2Response(
            status="no_articles",
            slug=info.slug,
            canonical_name=info.canonical_name,
            industry=info.industry,
            ticker=info.primary_ticker,
            elapsed_seconds=elapsed,
            fetched_count=0,
            analysed_count=0,
            home_count=0,
            articles=[],
            warning="No fresh news articles found for this company.",
            confidence=info.confidence,
        )

    # ── 8. Run pipeline on top-N articles IN PARALLEL ───────────────────
    from engine.main import _run_article

    top_articles = fresh[:body.limit]

    def _safe_run(article) -> tuple[Any, Any]:
        """Returns (article, summary_or_exception)."""
        try:
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
            return article, _run_article(article_dict, company_obj)
        except Exception as exc:
            return article, exc

    results: list[tuple[Any, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=3,
        thread_name_prefix="onboard-v2",
    ) as pool:
        futures = [pool.submit(_safe_run, a) for a in top_articles]
        for fut in concurrent.futures.as_completed(futures):
            try:
                results.append(fut.result(timeout=180))
            except Exception as exc:
                logger.warning("[onboard_v2] worker exception: %s", exc)

    # ── 8b. Eager top-3 Stage 10+11+12 pass (mirrors legacy Phase 36) ────
    # _run_article gates Stage 10 on HOME tier. SECONDARY articles get
    # stages 1-9 only, leaving deep_insight + recommendations + lede unset.
    # That's correct for cost reasons in steady-state ingest, but on a
    # FRESH onboard the user expects the top-3 cards to land already
    # body-grounded with full analysis. Without this pass, validation
    # tests 05-08 fail: no analysis populated → no recs → email send
    # rejects with "no HOME-tier analysis" → chat reply empty.
    #
    # We invoke `enrich_on_demand(force=True)` on the top-3 non-rejected
    # articles. It's idempotent (won't re-fire if already HOME-grounded)
    # and runs the full 1-12 + lede chain, writing the canonical insight
    # JSON + article_pool + company_article_view rows.
    non_rejected = [
        (article, outcome) for (article, outcome) in results
        if not isinstance(outcome, Exception)
        and not getattr(outcome, "rejected", False)
    ]
    # Sort by impact_score DESC so the top-3 are the highest-criticality
    # articles the user actually sees in the deck hero strip.
    def _score(pair) -> float:
        _, outcome = pair
        return float(getattr(outcome, "impact_score", 0) or 0)
    non_rejected.sort(key=_score, reverse=True)
    top_candidates = non_rejected[:3]
    # Only SECONDARY-tier articles need the eager promotion — HOME-tier
    # articles already ran Stage 10/11/12 + lede inside _run_article.
    # Calling enrich_on_demand(force=True) on them would just burn LLM
    # cost for no quality gain.
    eager_top = [
        (a, o) for (a, o) in top_candidates
        if getattr(o, "tier", "") != "HOME"
    ]

    if eager_top:
        from engine.analysis.on_demand import enrich_on_demand
        logger.info(
            "[onboard_v2] eager top-3 enrichment for %d articles on %s",
            len(eager_top), info.slug,
        )

        def _enrich_one(article_id: str) -> tuple[str, bool, str]:
            try:
                payload = enrich_on_demand(article_id, info.slug, force=True)
                return article_id, payload is not None, ""
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[onboard_v2] eager enrich failed for %s: %s",
                    article_id, exc,
                )
                return article_id, False, f"{type(exc).__name__}: {exc}"

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=3,
            thread_name_prefix="onboard-v2-eager",
        ) as pool:
            eager_futs = [
                pool.submit(_enrich_one, a.id) for (a, _) in eager_top
            ]
            for fut in concurrent.futures.as_completed(eager_futs):
                try:
                    aid, ok, err = fut.result(timeout=180)
                    logger.info(
                        "[onboard_v2] eager %s: %s%s",
                        aid, "OK" if ok else "FAIL",
                        f" ({err})" if err else "",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[onboard_v2] eager worker exception: %s", exc)

    # ── 9. Tally + build response ────────────────────────────────────────
    article_summaries: list[ArticleSummary] = []
    analysed = 0
    home_count = 0
    eager_promoted_ids = {a.id for (a, _) in eager_top}
    for article, outcome in results:
        if isinstance(outcome, Exception):
            logger.warning(
                "[onboard_v2] article %s pipeline failed: %s",
                article.id, type(outcome).__name__,
            )
            article_summaries.append(ArticleSummary(
                article_id=article.id,
                title=(article.title or "")[:300],
                url=article.url or "",
                tier="FAILED",
                rejected=True,
            ))
            continue
        if not getattr(outcome, "rejected", False):
            analysed += 1
            # Eager-enriched articles were promoted to HOME tier on disk
            # even if _run_article tagged them SECONDARY. Reflect that
            # here so the response + dashboard counts match what the user
            # actually sees in the deck.
            base_tier = getattr(outcome, "tier", "")
            effective_tier = (
                "HOME"
                if article.id in eager_promoted_ids or base_tier == "HOME"
                else base_tier
            )
            if effective_tier == "HOME":
                home_count += 1
        else:
            effective_tier = getattr(outcome, "tier", "UNKNOWN")
        article_summaries.append(ArticleSummary(
            article_id=article.id,
            title=(article.title or "")[:300],
            url=article.url or "",
            tier=effective_tier,
            rejected=bool(getattr(outcome, "rejected", False)),
        ))

    elapsed = time.monotonic() - t0
    status = "ready" if analysed > 0 else "partial"
    warning = "" if analysed > 0 else "All articles failed pipeline analysis."

    logger.info(
        "[onboard_v2] DONE %s: %.1fs, fetched=%d, analysed=%d, home=%d",
        info.slug, elapsed, len(fresh), analysed, home_count,
    )

    return OnboardV2Response(
        status=status,
        slug=info.slug,
        canonical_name=info.canonical_name,
        industry=info.industry,
        ticker=info.primary_ticker,
        elapsed_seconds=elapsed,
        fetched_count=len(fresh),
        analysed_count=analysed,
        home_count=home_count,
        articles=article_summaries,
        warning=warning,
        confidence=info.confidence,
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _exchange_from_ticker(ticker: str) -> str:
    """Derive the listing exchange from the ticker suffix."""
    if not ticker:
        return "Unknown"
    t = ticker.upper()
    if t.endswith(".NS"):
        return "NSE"
    if t.endswith(".BO"):
        return "BSE"
    if t.endswith(".L"):
        return "LSE"
    if t.endswith(".DE"):
        return "Xetra"
    if t.endswith(".PA"):
        return "Euronext Paris"
    if t.endswith(".AS"):
        return "Euronext Amsterdam"
    if t.endswith(".F"):
        return "Frankfurt"
    if t.endswith(".T"):
        return "TSE"
    if t.endswith(".HK"):
        return "HKEX"
    if t.endswith(".SS"):
        return "SSE"
    if "." not in t:
        return "NASDAQ/NYSE"
    return "Unknown"


def _build_news_queries(info: Any) -> list[str]:
    """Build a small set of ESG-focused queries for the news fetcher.

    The full multi-region query template lives in
    `engine/ingestion/company_onboarder.py::_build_queries`. For Phase 45
    we just build the universal core + company-name variants. The news
    fetcher's regional flavour is handled via `company_obj.framework_region`.
    """
    name = info.canonical_name
    short = name.replace(" Limited", "").replace(", Inc.", "").replace(" Inc.", "")
    short = short.replace(" PLC", "").replace(" SE", "").replace(" AG", "").strip()
    queries = [
        f"{short} ESG",
        f"{short} sustainability",
        f"{short} climate",
        f"{short} emissions",
        f"{short} governance",
        f"{short} regulatory",
        f"{short} compliance",
        f"{short} disclosure",
    ]
    if info.framework_region == "INDIA":
        queries.extend([
            f"{short} BRSR",
            f"{short} SEBI",
            f"{short} CSR",
        ])
    elif info.framework_region == "EU":
        queries.extend([
            f"{short} CSRD",
            f"{short} ESRS",
        ])
    elif info.framework_region == "US":
        queries.extend([
            f"{short} SEC climate",
            f"{short} EPA",
        ])
    return queries
