"""Phase 34.4 — Email myself the technical report endpoint.

Single endpoint: `POST /api/articles/{article_id}/email-self`.

The authenticated user requests the Morning-Brew technical report for an
article be emailed to **their own** address (extracted from the JWT
`sub` claim). Re-uses the Phase-33 Morning-Brew renderer
(`engine.output.share_service.share_article_by_email(layout="morning_brew")`).

Distinct from the admin-only `/api/news/{id}/share` endpoint (which lets
super-admins send TO other people). Here:
  - Recipient is always the JWT subject — no recipient param accepted.
  - No `manage_drip_campaigns` permission required — any authenticated
    user can email themselves.
  - The outbound-criticality floor still applies (so users can't email
    themselves random LOW-band noise).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from api.auth import require_api_key
from api.auth_context import get_bearer_claims
from engine.index.sqlite_index import get_by_id
from engine.output.share_service import share_article_by_email

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["article-email-self"],
    dependencies=[Depends(require_api_key)],
)

# Loose RFC-5322-ish check; strict validation lives in Resend's API.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_MAX_EXTRA_RECIPIENTS = 10


def _normalise_recipients(raw: Any) -> list[str]:
    """Parse a recipients payload into a clean, deduped, capped list.

    Accepts either a list of strings or a single comma/semicolon-separated
    string. Drops anything that doesn't look like an email; preserves order.
    """
    if not raw:
        return []
    if isinstance(raw, str):
        candidates = re.split(r"[,;\s]+", raw)
    elif isinstance(raw, list):
        candidates = []
        for entry in raw:
            if isinstance(entry, str):
                candidates.extend(re.split(r"[,;\s]+", entry))
    else:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        e = c.strip().lower()
        if not e or e in seen:
            continue
        if not _EMAIL_RE.match(e):
            continue
        seen.add(e)
        out.append(e)
        if len(out) >= _MAX_EXTRA_RECIPIENTS:
            break
    return out


@router.post("/api/articles/{article_id}/email-self")
def email_article_to_self(
    article_id: str,
    body: dict[str, Any] | None = Body(default=None),
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    """Send the Morning-Brew technical report for `article_id` to the
    authenticated user's email — and optionally to up to 10 additional
    recipients passed as ``{"recipients": ["a@b.com", "c@d.com"]}``.

    Returns ``{"status": "sent", "recipient": <email>, "additional": [...]}``
    where ``additional`` carries the per-recipient outcome. 404 when the
    article isn't indexed. 422 when the JWT has no `sub` claim
    (shouldn't happen with current auth flow).
    """
    recipient = (claims.get("sub") or "").strip()
    if not recipient or "@" not in recipient:
        raise HTTPException(
            status_code=422,
            detail=(
                "JWT subject is not a valid email — cannot resolve a "
                "recipient. Re-authenticate and retry."
            ),
        )

    row = get_by_id(article_id)
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"article not found: {article_id}",
        )
    company_slug = row.get("company_slug") or ""
    if not company_slug:
        raise HTTPException(
            status_code=500,
            detail=f"article {article_id} has no company_slug in index",
        )

    extras = _normalise_recipients((body or {}).get("recipients"))
    # Don't double-send to the JWT user if they typed themselves in.
    extras = [e for e in extras if e != recipient.lower()]

    result = share_article_by_email(
        article_id=article_id,
        company_slug=company_slug,
        recipient_email=recipient,
        layout="morning_brew",
    )
    if result.status == "failed":
        logger.warning(
            "email-self failed for article=%s recipient=%s err=%s",
            article_id, recipient, result.error,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error": "send_failed",
                "message": (
                    "The email service couldn't deliver the report. "
                    "Try again in a few minutes."
                ),
                "underlying": (result.error or "")[:160],
            },
        )

    additional: list[dict[str, Any]] = []
    for extra in extras:
        try:
            extra_result = share_article_by_email(
                article_id=article_id,
                company_slug=company_slug,
                recipient_email=extra,
                layout="morning_brew",
            )
            if extra_result.status == "failed":
                logger.warning(
                    "email-self extra recipient failed article=%s to=%s err=%s",
                    article_id, extra, extra_result.error,
                )
                additional.append({
                    "recipient": extra,
                    "status": "failed",
                    "error": (extra_result.error or "send_failed")[:160],
                })
            else:
                additional.append({"recipient": extra, "status": "sent"})
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "email-self extra recipient crash article=%s to=%s err=%s",
                article_id, extra, exc,
            )
            additional.append({
                "recipient": extra,
                "status": "failed",
                "error": str(exc)[:160],
            })

    return {
        "status": "sent",
        "recipient": recipient,
        "subject": result.subject,
        "article_id": article_id,
        "html_length": result.html_length,
        "additional": additional,
    }
