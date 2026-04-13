"""/api/ingest routes — trigger engine ingestion as a background task."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from api.auth import require_api_key
from engine.config import get_company

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingest", tags=["ingest"], dependencies=[Depends(require_api_key)])


def _run_ingest_job(slug: str, limit: int | None, max_per_query: int | None) -> None:
    """Run the engine pipeline for one company. Called in a background task."""
    # Lazy import — avoids triggering the heavy pipeline at module import time
    from engine.analysis.insight_generator import generate_deep_insight
    from engine.analysis.perspective_engine import transform_for_perspective
    from engine.analysis.pipeline import process_article
    from engine.analysis.recommendation_engine import generate_recommendations
    from engine.ingestion.news_fetcher import fetch_for_company
    from engine.output.writer import write_insight

    try:
        company = get_company(slug)
        fresh = fetch_for_company(company, max_per_query=max_per_query)
        processed = 0
        for idx, article in enumerate(fresh, 1):
            if limit and idx > limit:
                break
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
            try:
                result = process_article(article_dict, company)
            except Exception as exc:  # noqa: BLE001
                logger.exception("background ingest: pipeline failed for %s: %s", article.id, exc)
                continue
            if result.rejected:
                processed += 1
                continue
            insight = generate_deep_insight(result, company)
            if not insight:
                continue
            perspectives = {
                lens: transform_for_perspective(insight, result, lens)
                for lens in ("esg-analyst", "cfo", "ceo")
            }
            recs = generate_recommendations(insight, result, company)
            write_insight(result, insight, perspectives, recs)
            processed += 1
        logger.info("background ingest: %s processed %s articles", slug, processed)
    except Exception as exc:  # noqa: BLE001
        logger.exception("background ingest failed: %s", exc)


@router.post("/{slug}")
def trigger_ingest(
    slug: str,
    background_tasks: BackgroundTasks,
    limit: int | None = 5,
    max_per_query: int | None = 5,
) -> dict:
    try:
        get_company(slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    background_tasks.add_task(_run_ingest_job, slug, limit, max_per_query)
    return {
        "status": "accepted",
        "company_slug": slug,
        "limit": limit,
        "max_per_query": max_per_query,
        "message": "Ingest job queued in background",
    }
