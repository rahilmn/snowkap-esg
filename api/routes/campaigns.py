"""Phase 10 — Campaign REST API.

Surface:
  GET    /api/campaigns                          list (all statuses)
  POST   /api/campaigns                          create (+ parse recipients textarea)
  GET    /api/campaigns/{id}
  PATCH  /api/campaigns/{id}                     edit fields (recomputes next_send_at
                                                 automatically if cadence/time changed)
  DELETE /api/campaigns/{id}                     hard delete (send_log survives)
  POST   /api/campaigns/{id}/send-now            fire immediately, don't advance schedule
  POST   /api/campaigns/{id}/pause               status → 'paused'
  POST   /api/campaigns/{id}/resume              status → 'active'
  POST   /api/campaigns/{id}/archive             status → 'archived'
  GET    /api/campaigns/{id}/send-log?limit=50   recent sends (audit trail)
  POST   /api/campaigns/{id}/recipients          replace bulk recipient list
  GET    /api/campaigns/{id}/preview             render next email HTML without sending

All endpoints gated by `require_bearer_permission("manage_drip_campaigns")`.
The router is mounted in api/main.py BEFORE legacy_adapter.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from pydantic import BaseModel, EmailStr, Field, field_validator

from api.auth import require_auth
from api.auth_context import require_bearer_permission
from engine.models import campaign_store
from engine.output.cadence import compute_next_send
from engine.output.campaign_runner import run_due_campaigns
from engine.output.share_service import preview_share_html

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/campaigns",
    tags=["campaigns"],
    dependencies=[
        Depends(require_auth),
        Depends(require_bearer_permission("manage_drip_campaigns")),
    ],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RecipientIn(BaseModel):
    email: EmailStr
    name_override: str | None = Field(default=None, max_length=80)


class CampaignCreate(BaseModel):
    name: str = Field(min_length=3, max_length=60)
    target_company: str = Field(min_length=1, max_length=80)
    article_selection: str = Field(pattern="^(latest_home|specific)$")
    article_id: str | None = None
    cadence: str = Field(pattern="^(once|weekly|monthly)$")
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    send_time_utc: str | None = Field(default=None, pattern=r"^\d{1,2}:\d{2}$")
    cta_url: str | None = Field(default="https://snowkap.com/contact-us/", max_length=400)
    cta_label: str | None = Field(default="Book a demo with Snowkap", max_length=80)
    sender_note: str | None = Field(default=None, max_length=800)
    recipients: list[RecipientIn] = Field(default_factory=list)
    status: str = Field(default="active", pattern="^(active|paused|archived)$")

    @field_validator("recipients")
    @classmethod
    def _recipients_not_empty(cls, v: list[RecipientIn]) -> list[RecipientIn]:
        if not v:
            raise ValueError("at least one recipient required")
        return v


class CampaignPatch(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=60)
    article_selection: str | None = Field(default=None, pattern="^(latest_home|specific)$")
    article_id: str | None = None
    cadence: str | None = Field(default=None, pattern="^(once|weekly|monthly)$")
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    send_time_utc: str | None = Field(default=None, pattern=r"^\d{1,2}:\d{2}$")
    cta_url: str | None = None
    cta_label: str | None = None
    sender_note: str | None = Field(default=None, max_length=800)


class RecipientsReplace(BaseModel):
    recipients: list[RecipientIn]

    @field_validator("recipients")
    @classmethod
    def _recipients_not_empty(cls, v: list[RecipientIn]) -> list[RecipientIn]:
        if not v:
            raise ValueError("at least one recipient required")
        return v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_or_404(campaign_id: str):
    c = campaign_store.get_campaign(campaign_id)
    if not c:
        raise HTTPException(status_code=404, detail=f"campaign {campaign_id} not found")
    return c


def _creator_from_claims(claims: dict[str, Any]) -> str:
    sub = claims.get("sub") or "unknown"
    return str(sub)


def _campaign_to_dict(c) -> dict[str, Any]:
    d = c.to_dict() if hasattr(c, "to_dict") else {**c.__dict__}
    d["recipient_count"] = campaign_store.count_recipients(c.id)
    return d


def _compute_next_or_none(body: CampaignCreate | CampaignPatch, fallback: dict[str, Any]) -> str | None:
    """Compute next_send_at from cadence + schedule. Mixes PATCH overrides
    with existing-row fallbacks so partial updates work."""
    cadence = body.cadence or fallback.get("cadence")
    if cadence is None:
        return fallback.get("next_send_at")
    try:
        return compute_next_send(
            cadence,  # type: ignore[arg-type]
            day_of_week=body.day_of_week if body.day_of_week is not None else fallback.get("day_of_week"),
            day_of_month=body.day_of_month if body.day_of_month is not None else fallback.get("day_of_month"),
            send_time_utc=body.send_time_utc or fallback.get("send_time_utc"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"cadence: {exc}")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
def list_campaigns(status: str | None = Query(default=None, pattern="^(active|paused|archived)$")) -> dict[str, Any]:
    items = campaign_store.list_campaigns(status)  # type: ignore[arg-type]
    return {
        "campaigns": [_campaign_to_dict(c) for c in items],
        "total": len(items),
    }


@router.post("", status_code=201)
def create_campaign(
    body: CampaignCreate,
    claims: dict[str, Any] = Depends(require_bearer_permission("manage_drip_campaigns")),
) -> dict[str, Any]:
    try:
        next_ts = compute_next_send(
            body.cadence,  # type: ignore[arg-type]
            day_of_week=body.day_of_week,
            day_of_month=body.day_of_month,
            send_time_utc=body.send_time_utc,
        )
        c = campaign_store.create_campaign(
            name=body.name,
            created_by=_creator_from_claims(claims),
            target_company=body.target_company,
            article_selection=body.article_selection,  # type: ignore[arg-type]
            article_id=body.article_id,
            cadence=body.cadence,  # type: ignore[arg-type]
            day_of_week=body.day_of_week,
            day_of_month=body.day_of_month,
            send_time_utc=body.send_time_utc,
            cta_url=body.cta_url,
            cta_label=body.cta_label,
            sender_note=body.sender_note,
            status=body.status,  # type: ignore[arg-type]
            next_send_at=next_ts,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Populate recipients
    entries = [(r.email, r.name_override) for r in body.recipients]
    campaign_store.replace_recipients(c.id, entries)

    return _campaign_to_dict(c)


@router.get("/{campaign_id}")
def get_campaign(campaign_id: str) -> dict[str, Any]:
    c = _get_or_404(campaign_id)
    return _campaign_to_dict(c)


@router.patch("/{campaign_id}")
def patch_campaign(campaign_id: str, body: CampaignPatch) -> dict[str, Any]:
    c = _get_or_404(campaign_id)
    changes: dict[str, Any] = {}
    for field_name in (
        "name", "article_selection", "article_id", "cadence",
        "day_of_week", "day_of_month", "send_time_utc",
        "cta_url", "cta_label", "sender_note",
    ):
        val = getattr(body, field_name)
        if val is not None:
            changes[field_name] = val

    # Recompute next_send_at whenever schedule fields change.
    schedule_fields = {"cadence", "day_of_week", "day_of_month", "send_time_utc"}
    if schedule_fields & changes.keys():
        fallback = {
            "cadence": c.cadence,
            "day_of_week": c.day_of_week,
            "day_of_month": c.day_of_month,
            "send_time_utc": c.send_time_utc,
            "next_send_at": c.next_send_at,
        }
        changes["next_send_at"] = _compute_next_or_none(body, fallback)

    try:
        updated = campaign_store.update_campaign(campaign_id, **changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if updated is None:
        raise HTTPException(status_code=404, detail="campaign vanished during update")
    return _campaign_to_dict(updated)


@router.delete("/{campaign_id}", status_code=204, response_class=Response)
def delete_campaign(campaign_id: str) -> Response:
    _get_or_404(campaign_id)
    campaign_store.delete_campaign(campaign_id)
    return Response(status_code=204)


@router.post("/{campaign_id}/pause")
def pause_campaign(campaign_id: str) -> dict[str, Any]:
    _get_or_404(campaign_id)
    updated = campaign_store.set_status(campaign_id, "paused")
    return _campaign_to_dict(updated)  # type: ignore[arg-type]


@router.post("/{campaign_id}/resume")
def resume_campaign(campaign_id: str) -> dict[str, Any]:
    _get_or_404(campaign_id)
    updated = campaign_store.set_status(campaign_id, "active")
    return _campaign_to_dict(updated)  # type: ignore[arg-type]


@router.post("/{campaign_id}/archive")
def archive_campaign(campaign_id: str) -> dict[str, Any]:
    _get_or_404(campaign_id)
    updated = campaign_store.set_status(campaign_id, "archived")
    return _campaign_to_dict(updated)  # type: ignore[arg-type]


@router.post("/{campaign_id}/send-now", status_code=202)
def send_now(
    campaign_id: str,
    background: BackgroundTasks,
    dry_run: bool = Query(default=False),
) -> dict[str, Any]:
    _get_or_404(campaign_id)
    # Fire in the background so the admin UI gets instant feedback.
    # `force=True` means paused campaigns also send, and schedule is NOT
    # advanced — this is an out-of-band manual fire.
    background.add_task(run_due_campaigns, campaign_id=campaign_id, force=True, dry_run=dry_run)
    return {"status": "queued", "campaign_id": campaign_id, "dry_run": dry_run}


@router.get("/{campaign_id}/send-log")
def send_log(campaign_id: str, limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
    _get_or_404(campaign_id)
    entries = campaign_store.list_send_log(campaign_id, limit=limit)
    return {
        "campaign_id": campaign_id,
        "total": len(entries),
        "entries": [e.to_dict() for e in entries],
    }


@router.post("/{campaign_id}/recipients")
def replace_recipients(campaign_id: str, body: RecipientsReplace) -> dict[str, Any]:
    _get_or_404(campaign_id)
    entries = [(r.email, r.name_override) for r in body.recipients]
    rows = campaign_store.replace_recipients(campaign_id, entries)
    return {
        "campaign_id": campaign_id,
        "total": len(rows),
        "recipients": [r.to_dict() for r in rows],
    }


@router.get("/{campaign_id}/preview")
def campaign_preview(campaign_id: str) -> dict[str, Any]:
    """Render the next scheduled send's HTML without actually firing.
    Admin UI uses this to show the form's live preview pane."""
    c = _get_or_404(campaign_id)

    # Pick the same article the runner would pick
    if c.article_selection == "specific":
        article_id = c.article_id
    else:
        from engine.config import get_data_path
        from engine.output.newsletter_renderer import build_articles_from_outputs
        articles = build_articles_from_outputs(
            slugs=[c.target_company],
            outputs_root=get_data_path("outputs"),
            max_count=20,
        )
        article_id = articles[0].article_id if articles else None

    if not article_id:
        raise HTTPException(status_code=404, detail="no HOME article available to preview")

    # Use the first recipient as the "you" in the preview; fallback to a generic
    recipients = campaign_store.list_recipients(c.id)
    preview_email = recipients[0].email if recipients else "preview@example.com"

    html, result = preview_share_html(
        article_id=article_id,
        company_slug=c.target_company,
        recipient_email=preview_email,
        sender_note=c.sender_note,
    )
    if result.status == "failed":
        raise HTTPException(status_code=400, detail=result.error or "preview failed")

    return {
        "campaign_id": c.id,
        "article_id": article_id,
        "subject": result.subject,
        "recipient": preview_email,
        "recipient_name": result.recipient_name,
        "html": html,
        "html_length": len(html),
    }
