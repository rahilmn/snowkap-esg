"""Prediction Celery tasks — MiroFish trigger + result storage.

Per CLAUDE.md: MiroFish triggered only on high-impact news (score >70).
Per MASTER_BUILD_PLAN Phase 4.3: Celery task triggers prediction.
Stage 8.1: Use asgiref.sync.async_to_sync instead of creating new event loops.
Stage 8.2: Idempotency — skip if PredictionReport exists for same (article, company, tenant) within 24h.
"""

from datetime import datetime, timedelta, timezone

import structlog
from asgiref.sync import async_to_sync

from backend.tasks.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(
    name="prediction.trigger_simulation",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=600,
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

    Stage 8.2: Skips if a PredictionReport for the same (article, company, tenant)
    was created within the last 24 hours, unless user_requested=True.
    """
    async def _run():
        from sqlalchemy import select
        from backend.core.database import create_worker_session_factory
        from backend.models.prediction import PredictionReport
        from backend.services.prediction_service import run_prediction_pipeline

        session_factory = create_worker_session_factory()
        async with session_factory() as db:
            # Stage 8.2: Idempotency check
            if not user_requested:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
                existing = await db.execute(
                    select(PredictionReport.id).where(
                        PredictionReport.article_id == article_id,
                        PredictionReport.company_id == company_id,
                        PredictionReport.tenant_id == tenant_id,
                        PredictionReport.created_at >= cutoff,
                    ).limit(1)
                )
                if existing.scalar_one_or_none() is not None:
                    logger.info(
                        "prediction_task_skipped_duplicate",
                        tenant_id=tenant_id,
                        article_id=article_id,
                        company_id=company_id,
                    )
                    return {"status": "skipped", "reason": "duplicate_within_24h"}

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
        result = async_to_sync(_run)()
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
        raise self.retry(exc=e)


@celery_app.task(
    name="prediction.auto_trigger_check",
    soft_time_limit=300,
)
def auto_trigger_check_task(tenant_id: str, article_id: str) -> dict:
    """Check if an article meets trigger conditions for any company, and dispatch.

    Called after article impact analysis completes.
    """
    async def _check():
        from sqlalchemy import select
        from backend.core.database import create_worker_session_factory
        from backend.models.news import ArticleScore
        from backend.services.prediction_service import should_trigger_prediction

        session_factory = create_worker_session_factory()
        async with session_factory() as db:
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
                    trigger_simulation_task.delay(
                        tenant_id=tenant_id,
                        article_id=article_id,
                        company_id=score.company_id,
                    )
                    triggered.append(score.company_id)

            return {"article_id": article_id, "triggered_for": triggered}

    try:
        return async_to_sync(_check)()
    except Exception as e:
        logger.error("auto_trigger_check_failed", article_id=article_id, error=str(e))
        return {"article_id": article_id, "error": str(e)}
