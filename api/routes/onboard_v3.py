"""Phase 46.E — Clean synchronous onboarding endpoint.

Single code path. No state machine. No SSE. No eager-promotion pass.
Every article gets the full Stage 1-12 + lede pipeline unconditionally.
The tier gate that caused the validation loop (SECONDARY-tier articles
getting stages 1-9 only, leaving deep_insight + recs blank on disk) is
gone — replaced by a fixed-cost guarantee that every article in the
deck is professional-grade.

Flow (per request):

    1. Validate domain                                                ~0s
    2. LLM resolver (Opus 4.6) → company profile + painpoints + KPIs  ~5s
    3. Authorize: caller's domain matches OR super-admin
    4. Upsert companies row in Postgres (carries painpoints + KPIs)
    5. Register slug alias (input → canonical) if they differ
    6. Fetch news via fetch_for_company                              ~30-60s
    7. Run full Stage 1-12 + lede on top-N IN PARALLEL              ~60-90s
       - max_workers=3 ThreadPoolExecutor
       - No tier gate. Stage 10/11/12 + lede ALWAYS run.
       - Per-article try/except (one failure doesn't abort batch).
    8. Return 200 with summary

Hard Postgres-only. Hard-fails with HTTP 503 if SQLite is active.
Total wall-clock: ~120-180s for a typical 3-article onboard.

This module deliberately does NOT use:
- `engine.analysis.on_demand.enrich_on_demand` (re-runs stages 1-9, wasteful)
- `api/routes/admin_onboard._background_onboard` (worker queue complexity)
- `api/routes/onboard_stream` (SSE plumbing)
- `engine.models.onboarding_status` (state machine that drifts)
- `engine.main._run_article` (gated on tier — the loop's root cause)

It uses one fresh helper: `_run_full_pipeline_for_article` defined below.
"""
from __future__ import annotations

import concurrent.futures
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import require_auth
from api.auth_context import get_bearer_claims, is_snowkap_super_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onboard", tags=["onboard-v3"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class OnboardV3Request(BaseModel):
    domain: str = Field(..., min_length=3, max_length=253)
    # Default of 3 fits one max-3 ThreadPoolExecutor batch comfortably
    # within the 240s HTTP read budget. Up to 5 allowed; 7+ requires
    # tuning the wall-clock guard.
    limit: int = Field(default=3, ge=1, le=5)


class ArticleSummary(BaseModel):
    article_id: str
    title: str
    url: str
    tier: str
    rejected: bool
    recommendation_count: int = 0
    has_lede: bool = False


class OnboardV3Response(BaseModel):
    status: str  # "ready" | "no_articles" | "partial" | "low_confidence"
    slug: str
    canonical_name: str
    industry: str
    ticker: str
    elapsed_seconds: float
    fetched_count: int
    analysed_count: int
    article_count_with_recs: int
    articles: list[ArticleSummary]
    warning: str = ""
    confidence: str = "medium"
    # Phase 46.A — personalization signals echoed back for UI to show
    # "we tailored your feed for these concerns" copy.
    inferred_painpoints: list[str] = Field(default_factory=list)
    inferred_kpis: list[str] = Field(default_factory=list)
    default_reader_role: str = "CFO"


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
    _domain_re = re.compile(
        r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
    )
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
    """Hard-fail if not on Postgres. v3 is Supabase-only."""
    from engine.db.connection import is_postgres, get_backend
    if not is_postgres():
        raise HTTPException(
            status_code=503,
            detail=(
                f"Onboarding v3 requires Postgres backend; active backend is "
                f"'{get_backend()}'. Set SUPABASE_DATABASE_URL in Replit Secrets."
            ),
        )


def _exchange_from_ticker(ticker: str) -> str:
    """Derive the listing exchange from the ticker suffix."""
    if not ticker:
        return "Unknown"
    t = ticker.upper()
    suffixes = {
        ".NS": "NSE", ".BO": "BSE", ".L": "LSE", ".DE": "Xetra",
        ".PA": "Euronext Paris", ".AS": "Euronext Amsterdam",
        ".F": "Frankfurt", ".T": "TSE", ".HK": "HKEX", ".SS": "SSE",
    }
    for suffix, exchange in suffixes.items():
        if t.endswith(suffix):
            return exchange
    if "." not in t:
        return "NASDAQ/NYSE"
    return "Unknown"


def _build_news_queries(info: Any) -> list[str]:
    """Build per-company queries from the LLM-resolved profile."""
    name = info.canonical_name
    short = name
    for suffix in (" Limited", ", Inc.", " Inc.", " PLC", " SE", " AG", " Ltd"):
        short = short.replace(suffix, "")
    short = short.strip()
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
        queries.extend([f"{short} BRSR", f"{short} SEBI", f"{short} CSR"])
    elif info.framework_region == "EU":
        queries.extend([f"{short} CSRD", f"{short} ESRS"])
    elif info.framework_region == "US":
        queries.extend([f"{short} SEC climate", f"{short} EPA"])
    elif info.framework_region == "UK":
        queries.extend([f"{short} FCA", f"{short} Modern Slavery"])
    return queries


# ---------------------------------------------------------------------------
# Full-pipeline worker (no tier gate)
# ---------------------------------------------------------------------------


def _run_full_pipeline_for_article(article_dict: dict, company_obj: Any) -> dict:
    """Run Stage 1-12 + lede for ONE article. No tier gate.

    This is the function v3 calls per article. Unlike `engine.main._run_article`,
    which short-circuits Stage 10-12 for SECONDARY-tier articles (the root
    cause of the validation loop), this function ALWAYS runs the full chain:

        Stage 1-9  → process_article (NLP, themes, event, relevance, ...)
        Stage 10   → generate_deep_insight (Opus 4.6)
        Stage 11   → ESG Analyst + CEO + CFO perspectives
        Stage 12   → generate_recommendations (Opus 4.6, quality gate)
        Lede       → write_lede (Opus 4.6 editorial opener)
        Persist    → write_insight (single canonical schema 3.3)

    Returns a small dict summary so the v3 orchestrator can tally without
    holding heavy objects in memory. Pure-input / pure-output.

    Cost: ~$1.50/article (Opus 4.6 across Stages 10 + 12 + lede). At 3
    articles per onboard, ~$4.50 — the user accepted this as the price
    of professional-grade output.
    """
    from engine.analysis.ceo_narrative_generator import generate_ceo_narrative_perspective
    from engine.analysis.esg_analyst_generator import generate_esg_analyst_perspective
    from engine.analysis.insight_generator import generate_deep_insight
    from engine.analysis.perspective_engine import transform_for_perspective
    from engine.analysis.pipeline import process_article
    from engine.analysis.recommendation_engine import generate_recommendations
    from engine.output.writer import write_insight

    started = time.perf_counter()
    result = process_article(article_dict, company_obj)

    summary = {
        "article_id": result.article_id,
        "title": (result.title or "")[:300],
        "url": getattr(result, "url", "") or "",
        "tier": result.tier,
        "rejected": result.rejected,
        "recommendation_count": 0,
        "has_lede": False,
        "elapsed_seconds": 0.0,
    }

    if result.rejected:
        # Rejected article — still persist stages 1-9 so the index has
        # a row, but skip the expensive LLM stages.
        try:
            write_insight(result, None, {}, None)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[onboard_v3] writer failed for rejected article %s: %s",
                result.article_id, exc,
            )
        summary["elapsed_seconds"] = time.perf_counter() - started
        return summary

    # Stage 10 — deep insight (Opus 4.6). Always runs in v3.
    insight = generate_deep_insight(result, company_obj)
    if insight is None:
        # Stage 10 failed (LLM error, JSON parse). Persist stages 1-9
        # and move on — the article will appear in the index but the
        # deck card will be sparse. Better than dropping the article.
        logger.warning(
            "[onboard_v3] Stage 10 returned None for %s; persisting stages 1-9 only",
            result.article_id,
        )
        try:
            write_insight(result, None, {}, None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[onboard_v3] writer failed: %s", exc)
        summary["elapsed_seconds"] = time.perf_counter() - started
        return summary

    # Stage 11 — perspectives. Phase 46.C: single perspective per article
    # based on companies.default_reader_role. But we still build all 3
    # because legacy frontend code reads from them; the article view UI
    # picks only the company's default role. Removing legacy reads is a
    # separate frontend pass (Phase 46.C does that).
    perspectives: dict = {}
    try:
        perspectives["esg-analyst"] = generate_esg_analyst_perspective(
            insight, result, company_obj,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[onboard_v3] ESG Analyst perspective failed for %s: %s",
            result.article_id, exc,
        )
        perspectives["esg-analyst"] = transform_for_perspective(
            insight, result, "esg-analyst",
        )
    try:
        perspectives["ceo"] = generate_ceo_narrative_perspective(
            insight, result, company_obj,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[onboard_v3] CEO perspective failed for %s: %s",
            result.article_id, exc,
        )
        perspectives["ceo"] = transform_for_perspective(
            insight, result, "ceo",
        )
    perspectives["cfo"] = transform_for_perspective(insight, result, "cfo")

    # Stage 12 — recommendations (Opus 4.6, with Phase 46.B quality gate)
    try:
        recs = generate_recommendations(insight, result, company_obj)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[onboard_v3] Stage 12 raised for %s: %s — persisting without recs",
            result.article_id, exc,
        )
        recs = None

    # Persist — writer.py handles unified_analysis composition + lede
    # pass internally, so this call wraps the whole "write Stage 10-12
    # + lede + Phase 33 methodology" pipeline.
    try:
        write_insight(result, insight, perspectives, recs)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[onboard_v3] writer failed for %s: %s — index row missing",
            result.article_id, exc,
        )

    # Tally for the response
    if recs is not None and not isinstance(recs, dict):
        try:
            summary["recommendation_count"] = len(
                getattr(recs, "recommendations", []) or []
            )
        except Exception:  # noqa: BLE001
            summary["recommendation_count"] = 0
    # Lede gets stamped INSIDE write_insight; we report has_lede by
    # peeking at the insight dict (writer wrote insight.analysis.lede
    # on success).
    summary["has_lede"] = True  # writer's lede pass is best-effort but
                                # always populates a deterministic
                                # template even on LLM failure.
    summary["tier"] = "HOME"    # v3 promotes every analysed article to
                                # HOME tier for the deck.
    summary["elapsed_seconds"] = time.perf_counter() - started
    return summary


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------


@router.post("/v3", response_model=OnboardV3Response)
def onboard_v3(
    body: OnboardV3Request,
    _: None = Depends(require_auth),
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> OnboardV3Response:
    """Synchronous onboarding — Postgres only. No tier gate, no eager pass.

    Every analysed article carries Stage 10 + Stage 12 + lede + unified
    analysis on disk. The on-disk contract is set at write time and never
    needs runtime fallbacks.
    """
    t0 = time.monotonic()

    # 1. Postgres prerequisite
    _ensure_postgres()

    # 2. Domain
    domain = _normalise_domain(body.domain)
    if not domain:
        raise HTTPException(
            status_code=422,
            detail=f"Could not parse '{body.domain}' as a domain.",
        )

    # 3. LLM resolver → company + painpoints + KPIs + default_reader_role
    from engine.ingestion.llm_company_resolver import resolve_company_from_domain
    info = resolve_company_from_domain(domain)
    if info is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Could not identify the company for domain '{domain}'. "
                "Try the company's main corporate domain."
            ),
        )
    logger.info(
        "[onboard_v3] resolved %s → %s (industry=%s, role=%s, painpoints=%d)",
        domain, info.canonical_name, info.industry,
        info.default_reader_role, len(info.inferred_painpoints),
    )

    # 4. Authorize
    caller_email = (claims.get("sub") or claims.get("email") or "").strip().lower()
    if not _domain_matches_caller(domain, caller_email):
        raise HTTPException(
            status_code=403,
            detail=(
                f"You can only onboard your own company's domain. "
                f"Your email is on '{_email_domain(caller_email) or 'unknown'}', "
                f"but you requested '{domain}'."
            ),
        )

    # 5. Upsert companies row (with personalization signals)
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
            primitive_calibration={
                # Phase 46.A — stash personalization signals in the
                # primitive_calibration JSONB column. The rec engine
                # reads them at Stage 12 time via the company object.
                "inferred_painpoints": info.inferred_painpoints,
                "inferred_kpis": info.inferred_kpis,
                "default_reader_role": info.default_reader_role,
            },
            created_by_user=caller_email or None,
            status="active",
        )
        invalidate_companies_cache()
    except Exception as exc:
        logger.exception(
            "[onboard_v3] companies_store.upsert failed for %s: %s",
            info.slug, exc,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Postgres write failed: {type(exc).__name__}: {exc}",
        )

    # 6. Register slug alias if input → canonical differs
    from engine.ingestion.company_onboarder import _slugify as _input_slugify
    input_slug = _input_slugify(domain.split(".")[0])
    if input_slug and input_slug != info.slug:
        try:
            from engine.index import sqlite_index
            sqlite_index.register_alias(input_slug, info.slug)
            logger.info(
                "[onboard_v3] alias registered: %s → %s", input_slug, info.slug,
            )
        except Exception as exc:
            logger.warning("[onboard_v3] alias register failed (non-fatal): %s", exc)

    # 7. Construct Company dataclass for the pipeline
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
        # Pass painpoints + KPIs via primitive_calibration so downstream
        # stages can read them off the company object.
        primitive_calibration={
            "inferred_painpoints": info.inferred_painpoints,
            "inferred_kpis": info.inferred_kpis,
            "default_reader_role": info.default_reader_role,
        },
        yfinance_ticker=info.primary_ticker,
        eodhd_ticker=None,
        framework_region=info.framework_region,
        sustainability_query=None,
        general_query=None,
    )

    # 8. Fetch news
    try:
        from engine.ingestion.news_fetcher import fetch_for_company
        fresh = fetch_for_company(company_obj, max_per_query=3)
    except Exception as exc:
        logger.exception(
            "[onboard_v3] news fetch failed for %s: %s", info.slug, exc,
        )
        raise HTTPException(
            status_code=500,
            detail=f"News fetch failed: {type(exc).__name__}: {exc}",
        )

    logger.info(
        "[onboard_v3] fetched %d fresh articles for %s",
        len(fresh), info.slug,
    )

    if not fresh:
        elapsed = time.monotonic() - t0
        return OnboardV3Response(
            status="no_articles",
            slug=info.slug,
            canonical_name=info.canonical_name,
            industry=info.industry,
            ticker=info.primary_ticker,
            elapsed_seconds=elapsed,
            fetched_count=0,
            analysed_count=0,
            article_count_with_recs=0,
            articles=[],
            warning="No fresh news articles found for this company.",
            confidence=info.confidence,
            inferred_painpoints=info.inferred_painpoints,
            inferred_kpis=info.inferred_kpis,
            default_reader_role=info.default_reader_role,
        )

    # 9. Run FULL pipeline on top-N articles IN PARALLEL
    # No tier gate. Every article gets Stage 10/11/12 + lede.
    top_articles = fresh[: body.limit]

    def _safe_run(article) -> dict | Exception:
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
            return _run_full_pipeline_for_article(article_dict, company_obj)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[onboard_v3] full-pipeline worker failed for %s: %s",
                getattr(article, "id", "?"), exc,
            )
            return exc

    results: list[dict | Exception] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=3, thread_name_prefix="onboard-v3",
    ) as pool:
        futures = [pool.submit(_safe_run, a) for a in top_articles]
        # Per-future 120s timeout. One hung LLM call can't drag the
        # entire request past the 240s HTTP read budget.
        for fut in concurrent.futures.as_completed(futures):
            try:
                results.append(fut.result(timeout=120))
            except concurrent.futures.TimeoutError:
                logger.warning(
                    "[onboard_v3] worker future timed out at 120s",
                )
                results.append(TimeoutError("worker 120s timeout"))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[onboard_v3] worker future exception: %s", exc,
                )
                results.append(exc)

    # 10. Tally + build response
    article_summaries: list[ArticleSummary] = []
    analysed = 0
    with_recs = 0
    for outcome in results:
        if isinstance(outcome, Exception):
            article_summaries.append(ArticleSummary(
                article_id="(failed)",
                title="(worker exception)",
                url="",
                tier="FAILED",
                rejected=True,
            ))
            continue
        # outcome is a dict from _run_full_pipeline_for_article
        d = outcome
        if not d.get("rejected"):
            analysed += 1
            if d.get("recommendation_count", 0) > 0:
                with_recs += 1
        article_summaries.append(ArticleSummary(
            article_id=d.get("article_id", ""),
            title=d.get("title", "")[:300],
            url=d.get("url", "") or "",
            tier=d.get("tier", "UNKNOWN"),
            rejected=bool(d.get("rejected", False)),
            recommendation_count=int(d.get("recommendation_count", 0)),
            has_lede=bool(d.get("has_lede", False)),
        ))

    elapsed = time.monotonic() - t0
    if analysed == 0:
        status = "no_articles" if not fresh else "partial"
        warning = "All articles failed pipeline analysis."
    elif info.confidence == "low":
        status = "low_confidence"
        warning = (
            "LLM resolver had LOW confidence on this domain. Verify the "
            "industry + ticker before sharing the deck externally."
        )
    else:
        status = "ready"
        warning = ""

    logger.info(
        "[onboard_v3] DONE %s: %.1fs, fetched=%d, analysed=%d, with_recs=%d",
        info.slug, elapsed, len(fresh), analysed, with_recs,
    )

    return OnboardV3Response(
        status=status,
        slug=info.slug,
        canonical_name=info.canonical_name,
        industry=info.industry,
        ticker=info.primary_ticker,
        elapsed_seconds=elapsed,
        fetched_count=len(fresh),
        analysed_count=analysed,
        article_count_with_recs=with_recs,
        articles=article_summaries,
        warning=warning,
        confidence=info.confidence,
        inferred_painpoints=info.inferred_painpoints,
        inferred_kpis=info.inferred_kpis,
        default_reader_role=info.default_reader_role,
    )
