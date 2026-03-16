"""Re-trigger article impact analysis for a tenant.

Usage:
    cd D:/ClaudePowerofnow/snowkap-esg/snowkap-esg
    PYTHONPATH=. python scripts/retrigger_analysis.py
"""

import asyncio

from backend.core.database import create_worker_session_factory
from backend.models.news import Article, ArticleScore, CausalChain
from sqlalchemy import delete, select


TENANT_ID = "6908c18b-6c5d-4a1a-b5a6-7e2783d90d1a"


async def main():
    session_factory = create_worker_session_factory()

    async with session_factory() as db:
        result = await db.execute(
            select(Article.id).where(Article.tenant_id == TENANT_ID)
        )
        article_ids = [row[0] for row in result.all()]
        print(f"Found {len(article_ids)} articles for tenant")

        # Clear stale scores/chains so analysis creates fresh ones
        del_scores = await db.execute(
            delete(ArticleScore).where(ArticleScore.tenant_id == TENANT_ID)
        )
        del_chains = await db.execute(
            delete(CausalChain).where(CausalChain.tenant_id == TENANT_ID)
        )
        await db.commit()
        print(f"Cleared {del_scores.rowcount} old scores, {del_chains.rowcount} old chains")

    # Queue analysis tasks
    from backend.tasks.ontology_tasks import analyze_article_impact_task
    print(f"\nQueuing impact analysis for {len(article_ids)} articles...")
    for aid in article_ids:
        analyze_article_impact_task.delay(aid, TENANT_ID)

    print(f"Done! Queued {len(article_ids)} analysis tasks.")
    print("Start the Celery worker to process them.")


if __name__ == "__main__":
    asyncio.run(main())
