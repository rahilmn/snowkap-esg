"""Phase 24 (W4) — analyst session state API.

Three endpoints, all gated by ``require_auth``. The user_id is extracted
from the bearer token's ``sub`` claim — clients never pass it in the
request body, so a malicious caller can't write to another user's row.

  * ``GET  /api/session/state`` — read current user's session
  * ``POST /api/session/state`` — partial-upsert any of phase /
    active_company_slug / active_perspective / activity
  * ``POST /api/session/follow-up`` — append an insight to the queue
  * ``DELETE /api/session/follow-up/{insight_id}`` — remove an insight

Backed by ``engine.models.analyst_session`` which writes through
``engine.db.connect()`` — SQLite by default, Supabase when
``SNOWKAP_DB_BACKEND=postgres``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import require_auth
from api.auth_context import get_bearer_claims
from engine.models import analyst_session

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/session",
    tags=["analyst-session"],
    dependencies=[Depends(require_auth)],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class StateUpdate(BaseModel):
    """Partial update payload — every field is optional. Pass only what
    changed; the row keeps existing values for unset fields."""
    phase: str | None = Field(
        default=None,
        pattern=r"^(monthly_review|ad_hoc_lookup|onboarding_new_company)$",
    )
    active_company_slug: str | None = Field(default=None, max_length=100)
    active_perspective: str | None = Field(
        default=None, pattern=r"^(cfo|ceo|esg-analyst)$"
    )
    activity: dict[str, Any] | None = None


class FollowUpRequest(BaseModel):
    insight_id: str = Field(..., min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=300)
    company_slug: str | None = Field(default=None, max_length=100)


class StateResponse(BaseModel):
    user_id: str
    phase: str | None
    active_company_slug: str | None
    active_perspective: str | None
    activity: dict[str, Any]
    follow_up_queue: list[dict[str, Any]]
    updated_at: str


def _user_id_from_claims(claims: dict[str, Any]) -> str:
    user_id = claims.get("sub") or ""
    if not user_id:
        raise HTTPException(status_code=401, detail="bearer token missing 'sub' claim")
    return str(user_id)


# ---------------------------------------------------------------------------
# GET /api/session/state
# ---------------------------------------------------------------------------


@router.get("/state", response_model=StateResponse)
def get_state(claims: dict[str, Any] = Depends(get_bearer_claims)) -> StateResponse:
    user_id = _user_id_from_claims(claims)
    session = analyst_session.get(user_id)
    if session is None:
        # Empty default — never 404; first-time-user UX
        return StateResponse(
            user_id=user_id, phase=None, active_company_slug=None,
            active_perspective=None, activity={}, follow_up_queue=[],
            updated_at="",
        )
    return StateResponse(**session.to_dict())


# ---------------------------------------------------------------------------
# POST /api/session/state
# ---------------------------------------------------------------------------


@router.post("/state", response_model=StateResponse)
def update_state(
    update: StateUpdate,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> StateResponse:
    user_id = _user_id_from_claims(claims)
    payload: dict[str, Any] = {
        k: v for k, v in update.model_dump(exclude_none=True).items()
    }
    session = analyst_session.upsert(user_id, **payload)
    return StateResponse(**session.to_dict())


# ---------------------------------------------------------------------------
# POST /api/session/follow-up
# ---------------------------------------------------------------------------


@router.post("/follow-up", response_model=StateResponse)
def add_follow_up(
    req: FollowUpRequest,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> StateResponse:
    user_id = _user_id_from_claims(claims)
    session = analyst_session.append_follow_up(
        user_id,
        insight_id=req.insight_id,
        reason=req.reason,
        company_slug=req.company_slug,
    )
    return StateResponse(**session.to_dict())


# ---------------------------------------------------------------------------
# DELETE /api/session/follow-up/{insight_id}
# ---------------------------------------------------------------------------


@router.delete("/follow-up/{insight_id}", response_model=StateResponse)
def remove_follow_up(
    insight_id: str,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> StateResponse:
    user_id = _user_id_from_claims(claims)
    session = analyst_session.remove_follow_up(user_id, insight_id)
    return StateResponse(**session.to_dict())
