"""L6 — Advisor queue HTTP surface (read + resolve).

Endpoints for an analyst-facing review UI:

  GET  /api/advisor/queue                — list open events (all tenants)
  GET  /api/advisor/queue?tenant={slug}  — list open events for one tenant
  POST /api/advisor/resolve              — approve or reject one event

The queue is the L6 `advisor_queue.jsonl` (append-only). Resolutions
live in a parallel `advisor_resolutions.jsonl` so the queue file's
append-only invariant (required by L4 audit-the-audit) is preserved.

Auth: read endpoints use the standard `X-API-Key`. Resolve requires
`manage_drip_campaigns` (super-admin) since approving an unverified
candidate is a load-bearing data-quality decision.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import require_api_key
from api.auth_context import require_bearer_permission
from engine.audit import (
    apply_resolution_action,
    open_advisor_events,
    resolve_advisor_event,
)

router = APIRouter(prefix="/api/advisor", tags=["advisor"])


@router.get(
    "/queue",
    dependencies=[Depends(require_api_key)],
)
def list_queue(tenant: str | None = None) -> dict[str, Any]:
    """List currently-open advisor events.

    Optional `?tenant=<slug>` filters to events for that tenant.
    Events that have been approved or rejected do NOT appear here.
    """
    events = open_advisor_events(tenant=tenant)
    return {"count": len(events), "events": events}


class ResolveRequest(BaseModel):
    event_id: str = Field(..., min_length=4, max_length=64)
    resolution: str = Field(..., pattern="^(approve|reject)$")
    rationale: str = Field(default="", max_length=2000)


@router.post(
    "/resolve",
    dependencies=[
        Depends(require_api_key),
        Depends(require_bearer_permission("manage_drip_campaigns")),
    ],
)
def resolve(body: ResolveRequest, claims: dict = Depends(require_bearer_permission("manage_drip_campaigns"))) -> dict[str, Any]:
    """Approve or reject an open advisor event.

    The actor is derived from the bearer-token claims (`sub` →
    `manual:<email>`) so the resulting `advisor_resolutions.jsonl`
    entry carries an L4-compatible attribution.

    Returns the resolution entry that was appended.
    """
    actor_email = (claims.get("email") or claims.get("sub") or "").strip()
    if not actor_email:
        raise HTTPException(
            status_code=403,
            detail="Resolver actor cannot be determined from bearer token",
        )
    try:
        entry = resolve_advisor_event(
            event_id=body.event_id,
            resolution=body.resolution,
            actor=f"manual:{actor_email}",
            rationale=body.rationale,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Look up the resolved event so we can wire approve → promoter.
    # The event surface is computed each call; we hunt for the matching
    # event_id across BOTH open + resolved-but-listed events (the
    # resolution we just wrote already removed it from open_advisor_events
    # output, so we re-read the full queue and re-hash IDs).
    promoter_action: dict[str, Any] | None = None
    try:
        from engine.audit import _advisor_event_id, read_advisor_queue
        for ev in read_advisor_queue():
            if _advisor_event_id(ev) == body.event_id:
                promoter_action = apply_resolution_action(
                    event=ev,
                    resolution=body.resolution,
                    actor=f"manual:{actor_email}",
                    rationale=body.rationale,
                )
                break
    except Exception:  # noqa: BLE001 — secondary action MUST NOT mask the resolution
        promoter_action = None

    payload: dict[str, Any] = {"resolved": entry}
    if promoter_action is not None:
        payload["promoter_action"] = promoter_action
    return payload
