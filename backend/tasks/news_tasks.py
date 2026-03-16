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
                db.add(article)
                await db.flush()
                stored_ids.append(article.id)
                if url:
                    existing_urls.add(url)

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
