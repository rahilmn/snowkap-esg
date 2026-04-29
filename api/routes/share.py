"""/api/news/{article_id}/share and /preview-share endpoints (Phase 9).

Single-article share workflow for the UI "Share" button:
  - POST /api/news/{article_id}/share          — actually send
  - POST /api/news/{article_id}/share/preview  — render HTML + return for inline preview
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from api.auth import require_api_key
from api.auth_context import require_bearer_permission
from engine.index.sqlite_index import get_by_id
from engine.output.share_service import (
    preview_share_html,
    share_article_by_email,
)

logger = logging.getLogger(__name__)

# Phase 10 decision: share endpoints are admin-only. The recipient sees a
# From: newsletter@snowkap.co.in address — if any client could trigger that,
# they would look like they're sending mail from Snowkap. Only emails on
# SNOWKAP_INTERNAL_EMAILS (which carry manage_drip_campaigns perm) are
# allowed to fire a share / preview render.
router = APIRouter(
    tags=["share"],
    dependencies=[
        Depends(require_api_key),  # outer: valid bearer token
        Depends(require_bearer_permission("manage_drip_campaigns")),  # inner: admin
    ],
)


class ShareRequest(BaseModel):
    recipient_email: EmailStr
    sender_note: str | None = Field(
        default=None, max_length=800,
        description="Optional intro paragraph above the article. If omitted, a default is used.",
    )
    read_more_base: str | None = Field(
        default=None,
        description=(
            "Optional base URL for 'Read the full Snowkap brief' links. "
            "If omitted, the original article URL is used."
        ),
    )


class ShareResponse(BaseModel):
    status: str
    recipient: str
    recipient_name: str | None
    subject: str
    article_id: str
    company_slug: str
    company_name: str
    html_length: int
    provider_id: str = ""
    error: str = ""


class PreviewResponse(BaseModel):
    status: str
    recipient: str
    recipient_name: str | None
    subject: str
    html: str
    article_id: str
    company_slug: str
    company_name: str
    error: str = ""


def _resolve_company_slug(article_id: str) -> str:
    """Look up an article's company_slug via the SQLite index."""
    row = get_by_id(article_id)
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"article not found: {article_id}",
        )
    slug = row.get("company_slug", "")
    if not slug:
        raise HTTPException(
            status_code=500,
            detail=f"article {article_id} has no company_slug in index",
        )
    return slug


@router.post("/api/news/{article_id}/share", response_model=ShareResponse)
def share_article(article_id: str, req: ShareRequest) -> ShareResponse:
    """Send a single-article share email via Resend.

    Extracts recipient name from email (e.g. ambalika.m@x.com → Ambalika) for
    the greeting. Returns provider_id on success; returns status=preview with
    a warning if RESEND_API_KEY is absent.
    """
    slug = _resolve_company_slug(article_id)
    result = share_article_by_email(
        article_id=article_id,
        company_slug=slug,
        recipient_email=str(req.recipient_email),
        sender_note=req.sender_note,
        read_more_base=req.read_more_base,
    )
    # Phase 13 B3 — error taxonomy. Transient errors (rate-limit, timeout)
    # surface as HTTP 503 with Retry-After so the UI can render a retry
    # banner instead of an opaque "send failed". Permanent errors (auth,
    # validation, unknown) stay as HTTP 400.
    if result.status == "failed":
        err_class = getattr(result, "error_class", "") or "unknown"
        if err_class in {"rate_limit", "timeout"}:
            retry_after = "30" if err_class == "rate_limit" else "10"
            raise HTTPException(
                status_code=503,
                detail={
                    "error_class": err_class,
                    "message": (
                        "Email service is briefly unavailable; please retry."
                        if err_class == "rate_limit"
                        else "Email service connection timed out; retrying."
                    ),
                    "retry_after_seconds": int(retry_after),
                    "underlying": result.error[:120],
                },
                headers={"Retry-After": retry_after},
            )
        raise HTTPException(
            status_code=400,
            detail={
                "error_class": err_class,
                "message": result.error or "share failed",
            },
        )
    return ShareResponse(**result.to_dict())


@router.post("/api/news/{article_id}/share/preview", response_model=PreviewResponse)
def share_article_preview(article_id: str, req: ShareRequest) -> PreviewResponse:
    """Render the share email without sending — for inline UI preview."""
    slug = _resolve_company_slug(article_id)
    html, result = preview_share_html(
        article_id=article_id,
        company_slug=slug,
        recipient_email=str(req.recipient_email),
        sender_note=req.sender_note,
        read_more_base=req.read_more_base,
    )
    if result.status == "failed":
        raise HTTPException(status_code=400, detail=result.error or "preview failed")
    return PreviewResponse(
        status=result.status,
        recipient=result.recipient,
        recipient_name=result.recipient_name,
        subject=result.subject,
        html=html,
        article_id=article_id,
        company_slug=result.company_slug,
        company_name=result.company_name,
        error=result.error,
    )
