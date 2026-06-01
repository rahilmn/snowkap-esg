"""Phase 48.K — newsletter endpoints.

  * GET  /api/newsletter/unsubscribe?token=...  — one-click unsubscribe
        (HMAC token over email|slug, no auth needed — the token IS the auth).
  * POST /api/newsletter/send-me                — email the caller this
        week's brief for their company (JWT-gated). Mirrors the existing
        /api/articles/{id}/email-self pattern.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from api.auth import require_auth
from api.auth_context import get_bearer_claims

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/newsletter", tags=["newsletter"])


def _secret() -> str:
    return os.environ.get("JWT_SECRET", "").strip() or "snowkap-dev-secret"


def make_unsub_token(email: str, company_slug: str) -> str:
    """HMAC-SHA256 token binding email+slug. The unsubscribe link carries
    `email|slug|token`; the token can't be forged without JWT_SECRET."""
    msg = f"{email.strip().lower()}|{company_slug.strip().lower()}".encode("utf-8")
    return hmac.new(_secret().encode("utf-8"), msg, hashlib.sha256).hexdigest()[:32]


def _verify_unsub_token(email: str, company_slug: str, token: str) -> bool:
    return hmac.compare_digest(make_unsub_token(email, company_slug), (token or "").strip())


@router.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe(
    email: str = Query(..., min_length=3, max_length=320),
    company: str = Query(..., min_length=1, max_length=120),
    token: str = Query(..., min_length=8, max_length=64),
) -> HTMLResponse:
    if not _verify_unsub_token(email, company, token):
        raise HTTPException(status_code=400, detail="Invalid unsubscribe token.")
    try:
        from engine.models import newsletter_subscribers
        newsletter_subscribers.deactivate(email, company)
    except Exception as exc:  # noqa: BLE001
        logger.warning("unsubscribe failed for %s/%s: %s", email, company, exc)
        raise HTTPException(status_code=500, detail="Could not process unsubscribe.")
    return HTMLResponse(
        "<html><body style='font-family:sans-serif;padding:40px;text-align:center'>"
        "<h2>You're unsubscribed</h2>"
        f"<p>{email} will no longer receive the weekly Snowkap brief for this company.</p>"
        "</body></html>"
    )


class SendMeResponse(BaseModel):
    status: str
    company_slug: str
    subject: str = ""
    detail: str = ""


@router.post("/send-me", response_model=SendMeResponse)
def send_me(
    _: None = Depends(require_auth),
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> SendMeResponse:
    """Email the caller this week's brief for their bound company."""
    email = (claims.get("sub") or claims.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="No email on the token.")

    # Resolve the caller's company slug from their domain via tenant_registry.
    domain = email.split("@", 1)[1]
    company_slug = ""
    try:
        from engine.index import tenant_registry
        rec = tenant_registry.get_tenant_by_domain(domain)
        if rec:
            company_slug = rec.get("slug") or ""
    except Exception:  # noqa: BLE001
        pass
    if not company_slug:
        raise HTTPException(
            status_code=404,
            detail="No company is bound to your email domain yet. Onboard it first.",
        )

    from engine.output.weekly_brief import top_article_for_company
    top = top_article_for_company(company_slug)
    if not top or not top[0]:
        return SendMeResponse(
            status="no_articles", company_slug=company_slug,
            detail="No deck articles available for your company this week.",
        )

    try:
        from engine.output.share_service import share_article_by_email
        r = share_article_by_email(
            article_id=top[0],
            company_slug=company_slug,
            recipient_email=email,
            layout="morning_brew",
            cta_label="Open your weekly deck →",
        )
        return SendMeResponse(
            status=getattr(r, "status", "sent"),
            company_slug=company_slug,
            subject=getattr(r, "subject", "") or "",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("send-me failed for %s: %s", email, exc)
        raise HTTPException(status_code=500, detail=f"Send failed: {type(exc).__name__}")
