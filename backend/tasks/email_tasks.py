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
    # TODO: Phase 2C — call email_service.send_magic_link_email (sync wrapper)
    return {"status": "sent", "email": email}
