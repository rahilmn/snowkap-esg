"""News ingestion Celery tasks — RSS fetching, scoring, impact analysis.

Stage 8.1: Use asgiref.sync.async_to_sync instead of creating new event loops.
Stage 8.2: Idempotency — skip articles whose URL already exists in tenant scope.
"""

import structlog
from asgiref.sync import async_to_sync

from backend.tasks.celery_app import celery_app

logger = structlog.get_logger()


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
    1. Fetch articles via Google News RSS
    2. Deduplicate by URL (Stage 8.2)
    3. Store in articles table
    4. Trigger entity extraction + impact analysis for each article
    """
    async def _ingest():
        from sqlalchemy import select
        from backend.core.database import create_worker_session_factory
        from backend.models.news import Article
        from backend.services.news_service import curate_domain_news

        articles_data = await curate_domain_news(
            company_name, sustainability_query, general_query,
        )

        session_factory = create_worker_session_factory()
        async with session_factory() as db:
            # Stage 8.2: Load existing URLs for this tenant to skip duplicates
            existing_result = await db.execute(
                select(Article.url).where(
                    Article.tenant_id == tenant_id,
                    Article.url.isnot(None),
                )
            )
            existing_urls = {row[0] for row in existing_result.all()}

            # Phase 1B: Import content extractor
            from backend.services.content_extractor import extract_article_content

            stored_ids = []
            skipped = 0
            for a in articles_data:
                url = a.get("url")
                if url and url in existing_urls:
                    skipped += 1
                    continue

                article = Article(
                    tenant_id=tenant_id,
                    title=a["title"],
                    url=url,
                    source=a.get("source"),
                    published_at=a.get("published_at"),
                    summary=a.get("summary"),
                    image_url=a.get("image_url"),
                )

                # Phase 1B: Extract full article content + image via trafilatura
                if url:
                    try:
                        extracted = await extract_article_content(url)
                        if extracted.content:
                            article.content = extracted.content
                        # Phase 1: Update image_url from og:image if RSS didn't provide one
                        if not article.image_url and extracted.image_url:
                            article.image_url = extracted.image_url
                        if extracted.content or extracted.image_url:
                            logger.debug(
                                "content_extracted",
                                url=url[:80],
                                chars=len(extracted.content) if extracted.content else 0,
                                has_image=bool(extracted.image_url),
                            )
                    except Exception as exc:
                        logger.debug("content_extraction_skipped", url=url[:80], error=str(exc))

                db.add(article)
                try:
                    await db.flush()
                    stored_ids.append(article.id)
                    if url:
                        existing_urls.add(url)
                except Exception as flush_err:
                    # QA: Catch duplicate URL from unique index (race condition between concurrent tasks)
                    await db.rollback()
                    logger.debug("article_insert_skipped", url=(url or "")[:80], error=str(flush_err))
                    skipped += 1
                    continue

            await db.commit()
            return stored_ids, skipped

    try:
        article_ids, skipped = async_to_sync(_ingest)()
        logger.info(
            "news_ingested",
            tenant_id=tenant_id,
            company=company_name,
            articles=len(article_ids),
            skipped_duplicates=skipped,
        )

        # Trigger impact analysis for each article (background)
        from backend.tasks.ontology_tasks import analyze_article_impact_task
        for article_id in article_ids:
            analyze_article_impact_task.delay(article_id, tenant_id)

        return {
            "tenant_id": tenant_id,
            "articles_ingested": len(article_ids),
            "duplicates_skipped": skipped,
        }
    except Exception as e:
        logger.error("news_ingest_failed", tenant_id=tenant_id, error=str(e))
        return {"tenant_id": tenant_id, "error": str(e)}


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
