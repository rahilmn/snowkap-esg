"""Reprocess existing articles through the updated v2.0 pipeline.

After applying ESG SME gap fixes (GAPs 1-10), existing articles need
reprocessing to get corrected:
- Impact scores (GAP 4: calibrated anchors)
- ESG themes (GAP 2: banking sector fix)
- Priority scores (GAP 1: positive opportunity)
- Source tiers (GAP 3: Indian financial media)
- Supply chain scores (GAP 5: financial services)
- Recommendations (GAP 6: actionable specificity)
- Causal chain labels (GAP 7: confidence threshold)
- Event deduplication (GAP 8: cluster detection)

Run: python -m backend.scripts.reprocess_articles
Options:
  --tenant-id <id>       Reprocess only one tenant
  --article-id <id>      Reprocess a single article
  --limit <n>            Max articles to reprocess (default: all)
"""

import argparse
import asyncio

import structlog
from asgiref.sync import async_to_sync
from sqlalchemy import select, func, delete

from backend.core.database import create_worker_session_factory
from backend.models.news import Article, ArticleScore, CausalChain

logger = structlog.get_logger()


def reprocess(
    tenant_id: str | None = None,
    article_id: str | None = None,
    limit: int | None = None,
):
    """Clear stale scores and re-queue articles through the Celery pipeline."""

    async def _collect_articles():
        """Collect article IDs + tenant IDs, and clear their stale data."""
        session_factory = create_worker_session_factory()
        async with session_factory() as db:
            query = select(Article.id, Article.tenant_id, Article.title).where(
                Article.url.isnot(None)
            )
            if tenant_id:
                query = query.where(Article.tenant_id == tenant_id)
            if article_id:
                query = query.where(Article.id == article_id)
            query = query.order_by(Article.created_at.desc())
            if limit:
                query = query.limit(limit)

            result = await db.execute(query)
            articles = result.all()
            total = len(articles)
            logger.info("reprocess_found_articles", total=total)

            # Clear stale LLM-generated data for all articles
            cleared = 0
            for aid, tid, title in articles:
                try:
                    await db.execute(
                        delete(ArticleScore).where(
                            ArticleScore.article_id == aid,
                            ArticleScore.tenant_id == tid,
                        )
                    )
                    await db.execute(
                        delete(CausalChain).where(
                            CausalChain.article_id == aid,
                            CausalChain.tenant_id == tid,
                        )
                    )
                    # Clear LLM fields so they regenerate
                    art_result = await db.execute(
                        select(Article).where(Article.id == aid)
                    )
                    art = art_result.scalar_one_or_none()
                    if art:
                        art.nlp_extraction = None
                        art.esg_themes = None
                        art.framework_matches = None
                        art.risk_matrix = None
                        art.deep_insight = None
                        art.geographic_signal = None
                        art.executive_insight = None
                        art.rereact_recommendations = None
                        art.relevance_score = None
                        art.relevance_breakdown = None
                    cleared += 1
                except Exception as e:
                    logger.warning("clear_failed", article_id=aid, error=str(e))

            await db.commit()
            logger.info("stale_data_cleared", cleared=cleared)
            return [(aid, tid, title) for aid, tid, title in articles]

    # Step 1: Collect and clear
    article_list = async_to_sync(_collect_articles)()

    if not article_list:
        logger.info("nothing_to_reprocess")
        return

    # Step 2: Queue each article through the existing Celery pipeline
    from backend.tasks.ontology_tasks import analyze_article_impact_task

    queued = 0
    for aid, tid, title in article_list:
        try:
            analyze_article_impact_task.delay(aid, tid)
            queued += 1
            if queued % 10 == 0:
                logger.info("reprocess_queued", queued=queued, total=len(article_list))
        except Exception as e:
            logger.error("queue_failed", article_id=aid, error=str(e))

    logger.info(
        "reprocess_all_queued",
        queued=queued,
        total=len(article_list),
        note="Articles will be reprocessed by Celery workers in background",
    )


def main():
    parser = argparse.ArgumentParser(description="Reprocess articles through updated pipeline")
    parser.add_argument("--tenant-id", help="Reprocess only one tenant")
    parser.add_argument("--article-id", help="Reprocess a single article")
    parser.add_argument("--limit", type=int, help="Max articles to reprocess")
    args = parser.parse_args()

    reprocess(
        tenant_id=args.tenant_id,
        article_id=args.article_id,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
