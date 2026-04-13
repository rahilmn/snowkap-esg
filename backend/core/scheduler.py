"""APScheduler — Embedded scheduler for FastAPI.

Track B2: Fallback scheduler that runs inside the uvicorn process.
Ensures news ingestion and RSS polling fire even when no Celery worker is running.
This is critical for dev environments and single-server deployments.

Runs alongside Celery in production (tasks are idempotent — duplicate runs are safe
because the ingest pipeline deduplicates by URL).
"""

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = structlog.get_logger()

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


async def _poll_rss_feeds() -> None:
    """Hourly: Poll all publication RSS feeds and ingest articles for all active tenants."""
    try:
        from sqlalchemy import select
        from backend.core.database import async_session_factory
        from backend.models.tenant import Tenant
        from backend.models.company import Company
        from backend.services.rss_feed_service import fetch_publication_feeds_for_company
        from backend.tasks.news_tasks import _store_articles_for_tenant

        async with async_session_factory() as db:
            tenants_result = await db.execute(
                select(Tenant).where(Tenant.is_active.is_(True))
            )
            tenants = tenants_result.scalars().all()

            for tenant in tenants:
                try:
                    comp_result = await db.execute(
                        select(Company).where(Company.tenant_id == tenant.id).limit(1)
                    )
                    company = comp_result.scalars().first()
                    company_name = company.name if company else tenant.name

                    articles = await fetch_publication_feeds_for_company(
                        company_name=company_name,
                        max_age_hours=6,   # Only very fresh articles on hourly poll
                        max_per_feed=10,
                        industry=company.industry if company else None,
                    )
                    if articles:
                        await _store_articles_for_tenant(tenant.id, articles)
                        logger.info(
                            "rss_ingest_complete",
                            tenant_id=tenant.id,
                            company=company_name,
                            articles=len(articles),
                        )
                except Exception as tenant_err:
                    logger.warning("rss_ingest_tenant_failed", tenant_id=tenant.id, error=str(tenant_err))

    except Exception as e:
        logger.error("rss_poll_job_failed", error=str(e))


async def _refresh_all_tenants() -> None:
    """Every 4h: Full news refresh (Google News + NewsAPI + RSS) for all active tenants."""
    try:
        from sqlalchemy import select
        from backend.core.database import async_session_factory
        from backend.models.tenant import Tenant
        from backend.models.company import Company
        from backend.services.news_service import curate_domain_news
        from backend.tasks.news_tasks import _store_articles_for_tenant

        async with async_session_factory() as db:
            tenants_result = await db.execute(
                select(Tenant).where(Tenant.is_active.is_(True))
            )
            tenants = tenants_result.scalars().all()

            for tenant in tenants:
                try:
                    comp_result = await db.execute(
                        select(Company).where(Company.tenant_id == tenant.id).limit(1)
                    )
                    company = comp_result.scalars().first()
                    company_name = company.name if company else tenant.name
                    industry = (company.industry or "").lower() if company else ""

                    is_financial = any(
                        term in industry
                        for term in ["bank", "nbfc", "finance", "insurance", "amc",
                                     "lending", "investment", "brokerage", "mutual fund"]
                    )

                    if is_financial:
                        sus_query = tenant.sustainability_query or (
                            f'"{company_name}" ESG sustainability financed emissions '
                            f'climate risk disclosure green bond sustainable finance'
                        )
                        gen_query = tenant.general_query or (
                            f'"{company_name}" financial inclusion diversity ESG investing'
                        )
                    else:
                        sus_query = tenant.sustainability_query or (
                            f'"{company_name}" ESG sustainability emissions climate workforce'
                        )
                        gen_query = tenant.general_query or f'"{company_name}" corporate responsibility'

                    articles = await curate_domain_news(company_name, sus_query, gen_query)
                    if articles:
                        await _store_articles_for_tenant(tenant.id, articles)
                        logger.info(
                            "4h_refresh_complete",
                            tenant_id=tenant.id,
                            company=company_name,
                            articles=len(articles),
                        )
                except Exception as tenant_err:
                    logger.warning("4h_refresh_tenant_failed", tenant_id=tenant.id, error=str(tenant_err))

    except Exception as e:
        logger.error("4h_refresh_job_failed", error=str(e))


def start_scheduler() -> None:
    """Start the embedded APScheduler with all registered jobs."""
    scheduler = get_scheduler()

    if scheduler.running:
        logger.info("scheduler_already_running")
        return

    # Hourly RSS feed poll — lightweight, no LLM, just fetch + dedup
    scheduler.add_job(
        _poll_rss_feeds,
        trigger=IntervalTrigger(hours=1),
        id="rss_feed_poll",
        name="Poll publication RSS feeds",
        replace_existing=True,
        max_instances=1,
    )

    # 4-hourly full refresh — Google News + NewsAPI + competitor news
    scheduler.add_job(
        _refresh_all_tenants,
        trigger=IntervalTrigger(hours=4),
        id="full_news_refresh",
        name="Full news refresh (all tenants)",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.start()
    logger.info(
        "apscheduler_started",
        jobs=["rss_feed_poll (1h)", "full_news_refresh (4h)"],
    )


def stop_scheduler() -> None:
    """Gracefully stop the scheduler on app shutdown."""
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("apscheduler_stopped")
