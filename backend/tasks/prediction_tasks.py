"""Prediction Celery tasks — MiroFish trigger + result storage.

Per CLAUDE.md: MiroFish triggered only on high-impact news (score >70).
Per MASTER_BUILD_PLAN Phase 4.3: Celery task triggers prediction.
"""

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


@celery_app.task(
    name="prediction.trigger_simulation",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def trigger_simulation_task(
    self,
    tenant_id: str,
    article_id: str,
    company_id: str,
    causal_chain_id: str | None = None,
    user_requested: bool = False,
) -> dict:
    """Trigger MiroFish prediction simulation as background task.

    Per MASTER_BUILD_PLAN Phase 4.3:
    Celery task: trigger_prediction(news_event, company, causal_chain)
    """
    async def _run():
        from backend.core.database import async_session_factory
        from backend.services.prediction_service import run_prediction_pipeline

        async with async_session_factory() as db:
            result = await run_prediction_pipeline(
                tenant_id=tenant_id,
                article_id=article_id,
                company_id=company_id,
                causal_chain_id=causal_chain_id,
                db=db,
                user_requested=user_requested,
            )
            await db.commit()
            return result

    try:
        result = _run_async(_run())
        logger.info(
            "prediction_task_complete",
            tenant_id=tenant_id,
            article_id=article_id,
            company_id=company_id,
            status=result.get("status"),
        )
        return result
    except Exception as e:
        logger.error(
            "prediction_task_failed",
            tenant_id=tenant_id,
            article_id=article_id,
            error=str(e),
        )
        # Retry on transient failures
        raise self.retry(exc=e)


@celery_app.task(name="prediction.auto_trigger_check")
def auto_trigger_check_task(tenant_id: str, article_id: str) -> dict:
    """Check if an article meets trigger conditions for any company, and dispatch.

    Called after article impact analysis completes.
    """
    async def _check():
        from sqlalchemy import select
        from backend.core.database import async_session_factory
        from backend.models.news import ArticleScore
        from backend.services.prediction_service import should_trigger_prediction

        async with async_session_factory() as db:
            result = await db.execute(
                select(ArticleScore).where(
                    ArticleScore.article_id == article_id,
                    ArticleScore.tenant_id == tenant_id,
                )
            )
            scores = result.scalars().all()
            triggered = []

            for score in scores:
                if should_trigger_prediction(
                    impact_score=score.impact_score,
                    causal_hops=score.causal_hops,
                    financial_exposure=score.financial_exposure,
                ):
                    # Dispatch prediction for this company
                    trigger_simulation_task.delay(
                        tenant_id=tenant_id,
                        article_id=article_id,
                        company_id=score.company_id,
                    )
                    triggered.append(score.company_id)

            return {"article_id": article_id, "triggered_for": triggered}

    try:
        return _run_async(_check())
    except Exception as e:
        logger.error("auto_trigger_check_failed", article_id=article_id, error=str(e))
        return {"article_id": article_id, "error": str(e)}
