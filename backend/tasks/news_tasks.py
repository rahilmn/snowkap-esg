"""News ingestion Celery tasks — RSS fetching, scoring, impact analysis."""

import asyncio

import structlog

from backend.tasks.celery_app import celery_app

logger = structlog.get_logger()


def _run_async(coro):
    """Run an async coroutine from a sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="news.ingest_for_tenant")
def ingest_news_for_tenant(
    tenant_id: str,
    company_name: str,
    sustainability_query: str,
    general_query: str,
) -> dict:
    """Fetch and score news articles for a tenant's company.

    Per MASTER_BUILD_PLAN: Domain-driven news curation starts automatically after login.
    Pipeline:
    1. Fetch articles via Google News RSS
    2. Store in articles table
    3. Trigger entity extraction + impact analysis for each article
    """
    async def _ingest():
        from backend.core.database import async_session_factory
        from backend.models.news import Article
        from backend.services.news_service import curate_domain_news

        articles_data = await curate_domain_news(
            company_name, sustainability_query, general_query,
        )

        async with async_session_factory() as db:
            stored_ids = []
            for a in articles_data:
                article = Article(
                    tenant_id=tenant_id,
                    title=a["title"],
                    url=a["url"],
                    source=a.get("source"),
                    published_at=a.get("published_at"),
                    summary=a.get("summary"),
                )
                db.add(article)
                await db.flush()
                stored_ids.append(article.id)

            await db.commit()
            return stored_ids

    try:
        article_ids = _run_async(_ingest())
        logger.info(
            "news_ingested",
            tenant_id=tenant_id,
            company=company_name,
            articles=len(article_ids),
        )

        # Trigger impact analysis for each article (background)
        from backend.tasks.ontology_tasks import analyze_article_impact_task
        for article_id in article_ids:
            analyze_article_impact_task.delay(article_id, tenant_id)

        return {"tenant_id": tenant_id, "articles_ingested": len(article_ids)}
    except Exception as e:
        logger.error("news_ingest_failed", tenant_id=tenant_id, error=str(e))
        return {"tenant_id": tenant_id, "error": str(e)}
