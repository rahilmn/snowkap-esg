"""Phase 4C: Backfill existing articles with trafilatura content + enhanced sentiment/priority.

Run: python -m backend.scripts.backfill_content
"""

import asyncio
import time

import structlog
from sqlalchemy import select, func

from backend.core.database import create_worker_session_factory
from backend.models.news import Article
from backend.services.content_extractor import extract_article_content
from backend.ontology.entity_extractor import extract_and_classify
from backend.services.priority_engine import calculate_priority_score

logger = structlog.get_logger()

BATCH_SIZE = 50
DELAY_BETWEEN_URLS = 1.0  # seconds


async def backfill():
    session_factory = create_worker_session_factory()

    async with session_factory() as db:
        # Count articles needing backfill
        count_result = await db.execute(
            select(func.count(Article.id)).where(
                Article.content.is_(None),
                Article.url.isnot(None),
            )
        )
        total = count_result.scalar() or 0
        logger.info("backfill_starting", total_articles=total)

        if total == 0:
            logger.info("backfill_nothing_to_do")
            return

        processed = 0
        offset = 0

        while offset < total:
            result = await db.execute(
                select(Article).where(
                    Article.content.is_(None),
                    Article.url.isnot(None),
                ).order_by(Article.created_at.desc())
                .limit(BATCH_SIZE)
                .offset(offset)
            )
            articles = result.scalars().all()
            if not articles:
                break

            for article in articles:
                # 1. Extract content via trafilatura
                extracted = await extract_article_content(article.url)
                if extracted.content:
                    article.content = extracted.content

                # 2. Re-run entity extraction with full content
                text = article.content or article.summary or ""
                if text:
                    extraction = await extract_and_classify(article.title, text)

                    # Populate enhanced fields
                    article.sentiment = extraction.sentiment
                    article.sentiment_score = extraction.sentiment_score
                    article.sentiment_confidence = extraction.sentiment_confidence
                    article.aspect_sentiments = extraction.aspect_sentiments
                    article.content_type = extraction.content_type
                    article.urgency = extraction.urgency
                    article.time_horizon = extraction.time_horizon
                    article.reversibility = extraction.reversibility
                    article.stakeholder_impact = extraction.stakeholder_impact
                    if extraction.financial_signal_detail:
                        article.financial_signal = extraction.financial_signal_detail

                    # 3. Calculate priority
                    priority_score, priority_level = calculate_priority_score(
                        sentiment_score=extraction.sentiment_score,
                        urgency=extraction.urgency,
                        impact_score=0.0,  # No causal chain recalculation in backfill
                        has_financial_signal=bool(extraction.financial_signal_detail),
                        reversibility=extraction.reversibility,
                        framework_count=len(extraction.frameworks_mentioned),
                    )
                    article.priority_score = priority_score
                    article.priority_level = priority_level

                processed += 1
                if processed % 10 == 0:
                    logger.info("backfill_progress", processed=processed, total=total)

                # Rate limit
                time.sleep(DELAY_BETWEEN_URLS)

            await db.commit()
            offset += BATCH_SIZE

        logger.info("backfill_complete", processed=processed, total=total)


def main():
    asyncio.run(backfill())


if __name__ == "__main__":
    main()
