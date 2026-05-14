"""/api/news/{article_id}/share and /preview-share endpoints (Phase 9).

Single-article share workflow for the UI "Share" button:
  - POST /api/news/{article_id}/share          — actually send
  - POST /api/news/{article_id}/share/preview  — render HTML + return for inline preview
"""

from __future__ import annotations

import logging
from typing import Any

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
    # Phase 4 §6.4 — sales-tool role toggle. The frontend passes the
    # active role (CFO / CEO / Analyst) the salesperson selected. Today
    # the value is captured + audit-logged but the rendered HTML is
    # role-agnostic; a follow-up workstream wires
    # render_article_brief_dark() to swap content per role using the
    # already-stored insight.perspectives[role] block. Null defaults
    # preserve legacy callers' behaviour byte-for-byte.
    role: str | None = Field(
        default=None,
        description=(
            "Optional: 'cfo' | 'ceo' | 'esg-analyst' | 'analyst'. Picks "
            "which perspective the email body emphasises. Today this is "
            "advisory only — the underlying renderer is role-agnostic."
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


# Phase 1.6 — outbound criticality floor. Articles below this score
# cannot be sent or previewed. Tunable via env var for staging tests.
import os as _os
OUTBOUND_CRITICALITY_FLOOR = float(
    _os.environ.get("SNOWKAP_OUTBOUND_FLOOR", "0.65")
)


def _enforce_outbound_floor(article_id: str) -> None:
    """Raise HTTP 422 when an article's criticality is below the outbound
    floor. Articles with NULL criticality (pre-Phase-1 backfill or no-cascade
    SECONDARY-tier baselines) are LET THROUGH so we don't block sales during
    rollout. Once Phase 1.7's backfill_criticality.py runs, every row
    carries a score and the gate bites.

    Surfaces the 3 highest-criticality alternatives for the same company
    so the sales tool can offer a one-click swap.
    """
    row = get_by_id(article_id)
    if not row:
        return  # 404 already handled by _resolve_company_slug
    score = row.get("criticality_score")
    band = row.get("criticality_band")
    if score is None:
        return  # rollout grace — null means "not scored yet", let through
    try:
        s = float(score)
    except (TypeError, ValueError):
        return
    if s >= OUTBOUND_CRITICALITY_FLOOR:
        return

    # Find the top 3 alternatives for the same company that DO clear the floor
    alternatives: list[dict[str, Any]] = []
    try:
        from engine.index import sqlite_index
        company_slug = row.get("company_slug", "")
        if company_slug:
            alts = sqlite_index.query_feed(
                company_slug=company_slug,
                limit=3,
                min_criticality=OUTBOUND_CRITICALITY_FLOOR,
                max_age_days=0,
            )
            for a in alts:
                if a.get("id") == article_id:
                    continue
                alternatives.append({
                    "id": a.get("id"),
                    "title": (a.get("title") or "")[:120],
                    "criticality_score": a.get("criticality_score"),
                    "criticality_band": a.get("criticality_band"),
                })
    except Exception:  # noqa: BLE001
        alternatives = []

    raise HTTPException(
        status_code=422,
        detail={
            "error": "below_outbound_floor",
            "message": (
                f"This article's criticality score ({s:.2f}) is below the "
                f"outbound floor ({OUTBOUND_CRITICALITY_FLOOR:.2f}). "
                "Sales policy: only CRITICAL or HIGH-band articles may be "
                "shared with prospects."
            ),
            "criticality_score": s,
            "criticality_band": band,
            "outbound_floor": OUTBOUND_CRITICALITY_FLOOR,
            "alternatives": alternatives[:3],
        },
    )


@router.post("/api/news/{article_id}/share", response_model=ShareResponse)
def share_article(article_id: str, req: ShareRequest) -> ShareResponse:
    """Send a single-article share email via Resend.

    Extracts recipient name from email (e.g. ambalika.m@x.com → Ambalika) for
    the greeting. Returns provider_id on success; returns status=preview with
    a warning if RESEND_API_KEY is absent.

    Phase 1.6 — gated by OUTBOUND_CRITICALITY_FLOOR (default 0.65). Articles
    below the floor return 422 with the top-3 alternatives for the same
    company, so the sales tool can offer a one-click swap.
    """
    slug = _resolve_company_slug(article_id)
    _enforce_outbound_floor(article_id)
    result = share_article_by_email(
        article_id=article_id,
        company_slug=slug,
        recipient_email=str(req.recipient_email),
        sender_note=req.sender_note,
        read_more_base=req.read_more_base,
        role=req.role,
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

    # Phase 4 §6.6 — record this touch so the next send to the same
    # (recipient, company) pair surfaces the second-touch CTA. Only on
    # confirmed-sent (status != failed); preview/dry-run paths don't
    # increment the count. Failure to record is non-fatal — the email
    # already went out, we just lose the cadence signal for next time.
    if result.status not in {"failed", "preview"}:
        try:
            from engine.models.outbound_touches import record_touch
            record_touch(
                recipient_email=str(req.recipient_email),
                company_slug=slug,
                article_id=article_id,
            )
        except Exception:  # noqa: BLE001 — never block on touch tracking
            pass

    return ShareResponse(**result.to_dict())


@router.post("/api/news/{article_id}/share/preview", response_model=PreviewResponse)
def share_article_preview(article_id: str, req: ShareRequest) -> PreviewResponse:
    """Render the share email without sending — for inline UI preview.

    Phase 1.6 — same outbound floor as the live send so previews of
    below-floor articles also 422. Sales tool surfaces alternatives.
    """
    slug = _resolve_company_slug(article_id)
    _enforce_outbound_floor(article_id)
    html, result = preview_share_html(
        article_id=article_id,
        company_slug=slug,
        recipient_email=str(req.recipient_email),
        sender_note=req.sender_note,
        read_more_base=req.read_more_base,
        role=req.role,
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
