"""Email Celery tasks — async email sending via Resend.

QA: Added time limits and retry policy.
"""

import structlog

from backend.tasks.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(
    name="email.send_magic_link",
    soft_time_limit=30,
    time_limit=45,
    max_retries=2,
    default_retry_delay=10,
)
def send_magic_link_task(email: str, token: str, domain: str) -> dict:
    """Send magic link email as background task."""
    logger.info("email_magic_link_task", email=email, domain=domain)
    try:
        from asgiref.sync import async_to_sync
        from backend.services.email_service import send_magic_link_email
        async_to_sync(send_magic_link_email)(email, token, domain)
        return {"status": "sent", "email": email}
    except Exception as e:
        logger.error("email_magic_link_failed", email=email, error=str(e))
        raise
