"""Celery application configuration.

Per CLAUDE.md: Redis 7 as Celery broker, Celery 5.4+ for background processing.
"""

import sys

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
    # Prevent stale results accumulating in Redis
    result_expires=3600,  # 1 hour TTL
    # Windows: prefork pool has a fast_trace_task bug; use solo pool instead
    worker_pool="solo" if sys.platform == "win32" else "prefork",
)

celery_app.autodiscover_tasks([
    "backend.tasks.news_tasks",
    "backend.tasks.prediction_tasks",
    "backend.tasks.email_tasks",
    "backend.tasks.ontology_tasks",
    "backend.tasks.media_tasks",
])

# Periodic task: refresh news for all tenants every 24 hours
celery_app.conf.beat_schedule = {
    "refresh-all-tenant-news-daily": {
        "task": "news.refresh_all_tenants",
        "schedule": 86400.0,  # 24 hours in seconds
    },
    "decay-home-articles-6h": {
        "task": "news.decay_home_articles",
        "schedule": 21600.0,  # 6 hours in seconds
    },
}
