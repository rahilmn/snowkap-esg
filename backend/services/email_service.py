"""Email service — Resend via Celery.

Per CLAUDE.md: Email via Resend, sent async through Celery tasks.
"""

import structlog

from backend.core.config import settings

logger = structlog.get_logger()


async def send_magic_link_email(email: str, token: str, domain: str) -> bool:
    """Send a magic link email to the user.

    Per CLAUDE.md Auth Model: magic link sent to work email for passwordless auth.
    """
    if not settings.RESEND_API_KEY:
        logger.warning("resend_api_key_missing", email=email)
        # In development, log the link instead
        logger.info("magic_link_dev", email=email, token=token, link=f"/auth/verify/{token}")
        return True

    try:
        import resend
        resend.api_key = settings.RESEND_API_KEY

        resend.Emails.send({
            "from": settings.EMAIL_FROM,
            "to": email,
            "subject": "Sign in to SNOWKAP ESG Platform",
            "html": f"""
                <h2>Sign in to SNOWKAP ESG</h2>
                <p>Click the link below to sign in. This link expires in {settings.MAGIC_LINK_EXPIRE_MINUTES} minutes.</p>
                <a href="https://{domain}/auth/verify/{token}" style="
                    display: inline-block; padding: 12px 24px;
                    background-color: #2563eb; color: white;
                    text-decoration: none; border-radius: 6px;
                ">Sign In</a>
                <p style="color: #666; font-size: 12px; margin-top: 16px;">
                    If you didn't request this, you can safely ignore this email.
                </p>
            """,
        })
        logger.info("magic_link_email_sent", email=email)
        return True
    except Exception as e:
        logger.error("magic_link_email_failed", email=email, error=str(e))
        return False
