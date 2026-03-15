"""Celery application configuration.

Per CLAUDE.md: Redis 7 as Celery broker, Celery 5.4+ for background processing.
"""

from celery import Celery

from backend.core.config import settings

celery_app = Celery(
    "snowkap",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

celery_app.autodiscover_tasks([
    "backend.tasks.news_tasks",
    "backend.tasks.prediction_tasks",
    "backend.tasks.email_tasks",
    "backend.tasks.ontology_tasks",
    "backend.tasks.media_tasks",
])
