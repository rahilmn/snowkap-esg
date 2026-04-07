"""News ingestion Celery tasks — RSS fetching, scoring, impact analysis.

Stage 8.1: Use asgiref.sync.async_to_sync instead of creating new event loops.
Stage 8.2: Idempotency — skip articles whose URL already exists in tenant scope.

Track B1: Refresh frequency 24h → 4h.
Track A1: poll_rss_feeds task for direct publication RSS polling (1h cycle).
"""

import structlog
from asgiref.sync import async_to_sync

from backend.tasks.celery_app import celery_app

logger = structlog.get_logger()


import re as _re


def _title_fingerprint(title: str) -> str:
    """Normalize title to a dedup fingerprint.

    Strips punctuation, lowercases, collapses whitespace, takes first 80 chars.
    Catches same story from different sources with slightly different wording.
    """
    normalized = _re.sub(r"[^a-z0-9\s]", "", title.lower())
    normalized = _re.sub(r"\s+", " ", normalized).strip()
    return normalized[:80]


async def _store_articles_for_tenant(tenant_id: str, articles_data: list[dict]) -> list[str]:
    """Shared helper: deduplicate + store article dicts for a tenant.

    Deduplication is two-layer:
    1. URL exact match (catches same article re-fetched)
    2. Title fingerprint match (catches same story from different sources)

    Used by both Celery tasks and the APScheduler jobs (scheduler.py).
    Returns list of stored article IDs.
    """
    from sqlalchemy import select
    from backend.core.database import async_session_factory
    from backend.models.news import Article
    from backend.services.content_extractor import extract_article_content

    async with async_session_factory() as db:
        # Layer 1: existing URLs
        existing_result = await db.execute(
            select(Article.url).where(
                Article.tenant_id == tenant_id,
                Article.url.isnot(None),
            )
        )
        existing_urls = {row[0] for row in existing_result.all()}

        # Layer 2: existing title fingerprints (last 7 days only — avoids full table scan)
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        title_result = await db.execute(
            select(Article.title).where(
                Article.tenant_id == tenant_id,
                Article.created_at >= cutoff,
                Article.title.isnot(None),
            )
        )
        existing_fingerprints = {_title_fingerprint(row[0]) for row in title_result.all()}

        stored_ids = []
        for a in articles_data:
            url = a.get("url")
            title = a.get("title", "")

            # Layer 1: URL dedup
            if url and url in existing_urls:
                continue

            # Layer 2: title fingerprint dedup
            fp = _title_fingerprint(title)
            if fp and fp in existing_fingerprints:
                logger.debug("title_dedup_skipped", fingerprint=fp[:50])
                continue

            article = Article(
                tenant_id=tenant_id,
                title=title,
                url=url,
                source=a.get("source"),
                published_at=a.get("published_at"),
                summary=a.get("summary"),
                image_url=a.get("image_url"),
            )

            if url:
                try:
                    extracted = await extract_article_content(url)
                    if extracted.content:
                        article.content = extracted.content
                    if not article.image_url and extracted.image_url:
                        article.image_url = extracted.image_url
                except Exception:
                    pass

            db.add(article)
            try:
                await db.flush()
                stored_ids.append(article.id)
                if url:
                    existing_urls.add(url)
                existing_fingerprints.add(fp)
            except Exception:
                await db.rollback()

        await db.commit()

    # Trigger impact analysis for each new article.
    # If we're already inside a running event loop (APScheduler, FastAPI endpoint),
    # run inline as background tasks — no Celery worker needed.
    # Otherwise fall back to Celery .delay().
    import asyncio as _asyncio

    async def _analyze_inline(aid: str, tid: str) -> None:
        try:
            from backend.core.database import async_session_factory
            from backend.services.ontology_service import analyze_article_impact
            async with async_session_factory() as db:
                await analyze_article_impact(aid, tid, db)
                await db.commit()
            logger.info("article_impact_analyzed_inline", article_id=aid)
        except Exception as exc:
            logger.warning("article_impact_inline_failed", article_id=aid, error=str(exc))

    try:
        loop = _asyncio.get_running_loop()
        # We're inside an async context — schedule as background tasks
        for article_id in stored_ids:
            loop.create_task(_analyze_inline(article_id, tenant_id))
    except RuntimeError:
        # No running event loop — use Celery
        from backend.tasks.ontology_tasks import analyze_article_impact_task
        for article_id in stored_ids:
            try:
                analyze_article_impact_task.delay(article_id, tenant_id)
            except Exception:
                pass

    return stored_ids


@celery_app.task(
    name="news.ingest_for_tenant",
    soft_time_limit=300,
    time_limit=360,
    max_retries=1,
    default_retry_delay=120,
)
def ingest_news_for_tenant(
    tenant_id: str,
    company_name: str,
    sustainability_query: str,
    general_query: str,
) -> dict:
    """Fetch and score news articles for a tenant's company.

    Pipeline:
    1. Fetch articles via Google News RSS + NewsAPI + publication RSS feeds
    2. Deduplicate by URL (Stage 8.2)
    3. Store in articles table
    4. Trigger entity extraction + impact analysis for each article
    """
    async def _ingest():
        from backend.services.news_service import curate_domain_news
        from backend.services.rss_feed_service import fetch_publication_feeds_for_company

        # Source 1+2: Google News RSS + NewsAPI
        articles_data = await curate_domain_news(
            company_name, sustainability_query, general_query,
        )

        # Source 3: Direct publication RSS feeds (Mint, ET, Business Standard, etc.)
        rss_articles = await fetch_publication_feeds_for_company(
            company_name=company_name,
            max_age_hours=48,
            max_per_feed=15,
        )
        # Merge, dedup by URL
        existing_urls_in_batch = {a["url"] for a in articles_data if a.get("url")}
        for a in rss_articles:
            if a.get("url") and a["url"] not in existing_urls_in_batch:
                articles_data.append(a)
                existing_urls_in_batch.add(a["url"])

        stored_ids = await _store_articles_for_tenant(tenant_id, articles_data)
        return stored_ids, len(articles_data) - len(stored_ids)

    try:
        article_ids, skipped = async_to_sync(_ingest)()
        logger.info(
            "news_ingested",
            tenant_id=tenant_id,
            company=company_name,
            articles=len(article_ids),
            skipped_duplicates=skipped,
        )
        return {
            "tenant_id": tenant_id,
            "articles_ingested": len(article_ids),
            "duplicates_skipped": skipped,
        }
    except Exception as e:
        logger.error("news_ingest_failed", tenant_id=tenant_id, error=str(e))
        return {"tenant_id": tenant_id, "error": str(e)}


@celery_app.task(
    name="news.poll_rss_feeds",
    soft_time_limit=300,
    time_limit=360,
    max_retries=1,
)
def poll_rss_feeds() -> dict:
    """Hourly: Poll direct publication RSS feeds for all active tenants.

    Lightweight — no LLM calls, just fetch + dedup + store.
    Track A1: Guarantees Mint, ET, Business Standard coverage every hour.
    """
    async def _poll():
        from sqlalchemy import select
        from backend.core.database import create_worker_session_factory
        from backend.models.tenant import Tenant
        from backend.models.company import Company
        from backend.services.rss_feed_service import fetch_publication_feeds_for_company

        session_factory = create_worker_session_factory()
        total_stored = 0
        async with session_factory() as db:
            tenants_result = await db.execute(
                select(Tenant).where(Tenant.is_active.is_(True))
            )
            tenants = tenants_result.scalars().all()

            for tenant in tenants:
                comp_result = await db.execute(
                    select(Company).where(Company.tenant_id == tenant.id).limit(1)
                )
                company = comp_result.scalars().first()
                company_name = company.name if company else tenant.name

                try:
                    articles = await fetch_publication_feeds_for_company(
                        company_name=company_name,
                        max_age_hours=6,
                        max_per_feed=10,
                    )
                    if articles:
                        stored = await _store_articles_for_tenant(tenant.id, articles)
                        total_stored += len(stored)
                        logger.info(
                            "rss_poll_stored",
                            tenant_id=tenant.id,
                            company=company_name,
                            stored=len(stored),
                        )
                except Exception as err:
                    logger.warning("rss_poll_tenant_failed", tenant_id=tenant.id, error=str(err))

        return total_stored

    try:
        count = async_to_sync(_poll)()
        logger.info("rss_poll_complete", articles_stored=count)
        return {"status": "ok", "articles_stored": count}
    except Exception as e:
        logger.error("rss_poll_failed", error=str(e))
        return {"status": "error", "error": str(e)}


@celery_app.task(
    name="news.run_rereact_background",
    soft_time_limit=300,
    time_limit=360,
    max_retries=2,
    default_retry_delay=60,
)
def run_rereact_background(
    article_id: str,
    tenant_id: str,
    company_name: str,
    frameworks: list[str],
    content_type: str | None,
    competitors: list[str] | None = None,
    market_cap: str | None = None,
    listing_exchange: str | None = None,
    headquarter_country: str | None = None,
) -> dict:
    """Run REREACT 3-agent recommendation pipeline in background."""
    async def _run():
        from sqlalchemy import select
        from backend.core.database import create_worker_session_factory
        from backend.models.news import Article
        from backend.services.rereact_engine import rereact_recommendations

        session_factory = create_worker_session_factory()
        async with session_factory() as db:
            result = await db.execute(
                select(Article).where(Article.id == article_id, Article.tenant_id == tenant_id)
            )
            article = result.scalar_one_or_none()
            if not article or not article.deep_insight:
                return {"status": "skipped", "reason": "no deep insight"}

            rereact = await rereact_recommendations(
                article_title=article.title,
                article_content=article.content,
                deep_insight=article.deep_insight,
                company_name=company_name,
                frameworks=frameworks,
                content_type=content_type,
                competitors=competitors,
                market_cap=market_cap,
                listing_exchange=listing_exchange,
                headquarter_country=headquarter_country,
            )
            if rereact:
                article.rereact_recommendations = rereact
                await db.commit()
                return {
                    "status": "ok",
                    "article_id": article_id,
                    "recommendations": len(rereact.get("validated_recommendations", [])),
                }
            return {"status": "no_recommendations"}

    try:
        return async_to_sync(_run)()
    except Exception as e:
        logger.error("rereact_background_failed", article_id=article_id, error=str(e))
        return {"status": "error", "error": str(e)}


@celery_app.task(
    name="news.refresh_all_tenants",
    soft_time_limit=1800,
    time_limit=2100,
)
def refresh_all_tenants() -> dict:
    """Periodic task: refresh news for ALL active tenants.

    Runs every 24 hours via Celery beat. For each tenant with a company,
    triggers a fresh news ingestion (including competitor news).
    """
    async def _refresh():
        from sqlalchemy import select
        from backend.core.database import create_worker_session_factory
        from backend.models.tenant import Tenant
        from backend.models.company import Company

        session_factory = create_worker_session_factory()
        async with session_factory() as db:
            tenants = await db.execute(
                select(Tenant).where(Tenant.is_active.is_(True))
            )
            all_tenants = tenants.scalars().all()

            triggered = 0
            for tenant in all_tenants:
                # Find company for this tenant
                comp_result = await db.execute(
                    select(Company).where(Company.tenant_id == tenant.id).limit(1)
                )
                company = comp_result.scalars().first()

                company_name = company.name if company else tenant.name
                industry = (company.industry or "").lower() if company else ""

                # GAP 9: Financial sector companies need explicit E/S terms
                # to avoid governance-only news clustering
                is_financial = any(
                    term in industry
                    for term in ["bank", "nbfc", "finance", "insurance", "amc", "asset management",
                                 "lending", "investment", "brokerage", "mutual fund"]
                )
                if is_financial:
                    sus_query = tenant.sustainability_query or (
                        f'"{company_name}" ESG sustainability financed emissions '
                        f'climate risk disclosure green bond sustainable finance '
                        f'green lending renewable energy financing'
                    )
                    gen_query = tenant.general_query or (
                        f'"{company_name}" financial inclusion workforce diversity '
                        f'social impact community investment responsible lending '
                        f'ESG investing climate portfolio'
                    )
                else:
                    sus_query = tenant.sustainability_query or (
                        f'"{company_name}" ESG sustainability emissions climate '
                        f'workforce diversity social impact governance'
                    )
                    gen_query = tenant.general_query or f'"{company_name}" corporate responsibility'

                # Queue individual ingestion task
                ingest_news_for_tenant.delay(
                    tenant.id, company_name, sus_query, gen_query,
                )
                triggered += 1

            return triggered

    try:
        count = async_to_sync(_refresh)()
        logger.info("daily_news_refresh_triggered", tenants=count)
        return {"status": "ok", "tenants_triggered": count}
    except Exception as e:
        logger.error("daily_news_refresh_failed", error=str(e))
        return {"status": "error", "error": str(e)}


@celery_app.task(
    name="news.decay_home_articles",
    soft_time_limit=120,
    time_limit=150,
)
def decay_home_articles() -> dict:
    """Periodic task (6h): Demote HOME articles older than 72 hours to SECONDARY.

    Per Module 5: Stories on HOME re-evaluated every 6 hours.
    Max age for HOME: 72 hours unless refreshed by new related news.
    """
    async def _decay():
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import select, update
        from backend.core.database import create_worker_session_factory
        from backend.models.news import Article

        cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
        session_factory = create_worker_session_factory()
        async with session_factory() as db:
            # Find HOME-tier articles older than 72 hours
            result = await db.execute(
                select(Article).where(
                    Article.relevance_score >= 7,
                    Article.priority_level.notin_(["REJECTED"]),
                    Article.created_at < cutoff,
                )
            )
            stale = result.scalars().all()
            demoted = 0
            for article in stale:
                if article.relevance_breakdown and article.relevance_breakdown.get("tier") == "HOME":
                    article.relevance_breakdown["tier"] = "DECAYED"
                    article.priority_score = max(0, (article.priority_score or 0) * 0.7)
                    demoted += 1
            await db.commit()
            return demoted

    try:
        count = async_to_sync(_decay)()
        logger.info("home_articles_decayed", demoted=count)
        return {"status": "ok", "demoted": count}
    except Exception as e:
        logger.error("decay_task_failed", error=str(e))
        return {"status": "error", "error": str(e)}
