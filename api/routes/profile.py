"""W2 — Profile-driven self-service onboarding.

Replaces the admin-only `/api/admin/onboard` flow for end users. Any signed-in
user can onboard their own company by typing the domain in their profile page.

Security model:

  * Auth via `require_auth()` only — no `manage_drip_campaigns` permission
    needed. The endpoint runs in the user's session.
  * Domain-match guard: the requested onboarding domain must match the
    caller's email domain (so `pilot@acme.com` can only onboard `acme.com`,
    not `bigcorp.com`). Snowkap super-admins (sales@snowkap.co.in etc.)
    bypass this check so they can still onboard prospects on a customer's
    behalf.
  * Reuses the existing `engine.jobs.onboard_queue.enqueue()` so the work
    runs in the same separate worker process the admin endpoint uses; the
    API event loop is never blocked.
  * Reuses `engine.models.onboarding_status` so the status-poll endpoint
    `/api/admin/onboard/{slug}/status` works for both admin and self-service
    onboards (no new poll endpoint).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import require_auth
from api.auth_context import get_bearer_claims, is_snowkap_super_admin
from engine.jobs import onboard_queue
from engine.models import onboarding_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/me", tags=["profile"])


class MeOnboardRequest(BaseModel):
    """Self-service onboarding payload — domain only."""

    domain: str = Field(..., min_length=3, max_length=253)
    limit: int = Field(default=10, ge=1, le=20)


_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")


def _normalise_domain(raw: str) -> str:
    """Lowercase + strip protocol/path. Returns "" if not a parseable domain."""
    s = (raw or "").strip().lower()
    if not s:
        return ""
    # Strip http(s):// and any trailing path
    s = re.sub(r"^https?://", "", s)
    s = s.split("/", 1)[0]
    s = s.split("?", 1)[0]
    s = s.removeprefix("www.")
    return s if _DOMAIN_RE.match(s) else ""


def _email_domain(email: str | None) -> str:
    """Extract `acme.com` from `pilot@acme.com`."""
    if not email or "@" not in email:
        return ""
    return email.split("@", 1)[1].strip().lower()


def _domain_matches_caller(target: str, caller_email: str) -> bool:
    """True when `target` is the caller's own email domain (or a subdomain).

    Snowkap super-admins bypass this check (they can onboard any prospect).
    """
    if is_snowkap_super_admin(caller_email):
        return True
    own = _email_domain(caller_email)
    if not own:
        return False
    return target == own or target.endswith("." + own) or own.endswith("." + target)


@router.post("/onboard", status_code=202)
def me_onboard(
    body: MeOnboardRequest,
    _: None = Depends(require_auth),
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    """Kick off self-service onboarding for the caller's company.

    Returns 202 + {slug, poll_url} immediately; the worker drains the queue.
    Frontend polls `GET /api/admin/onboard/{slug}/status` every ~5s and
    redirects to `/home?company={slug}` once `state=ready`.
    """
    domain = _normalise_domain(body.domain)
    if not domain:
        raise HTTPException(
            status_code=422,
            detail=f"Could not parse '{body.domain}' as a domain (e.g. 'acme.com').",
        )

    caller_email = (claims.get("sub") or claims.get("email") or "").strip().lower()
    if not _domain_matches_caller(domain, caller_email):
        logger.warning(
            "me_onboard: domain mismatch — caller=%s tried to onboard domain=%s",
            caller_email or "<anon>", domain,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                f"You can only onboard your own company's domain. "
                f"Your email is on '{_email_domain(caller_email) or 'unknown'}', "
                f"but you requested '{domain}'."
            ),
        )

    # Lazy import keeps the request fast.
    from engine.ingestion.company_onboarder import _domain_to_search_term, _slugify

    seed = _domain_to_search_term(domain) or domain
    expected_slug = _slugify(seed)

    # Pre-seed the status row so pollers never race the worker pickup.
    onboarding_status.upsert(expected_slug, state="pending")

    try:
        onboard_queue.enqueue(
            slug=expected_slug,
            name=None,
            ticker_hint=None,
            domain=domain,
            item_limit=int(body.limit),
        )
    except Exception as exc:  # noqa: BLE001 — queue write failure must not 5xx the user
        logger.exception("me_onboard: enqueue failed for slug=%s: %s", expected_slug, exc)
        # Still return 202 — the user will see the pending state in the UI
        # and can retry from the empty-Home state. Better than a 500.

    logger.info(
        "me_onboard: queued slug=%s domain=%s requested_by=%s",
        expected_slug, domain, caller_email or "<anon>",
    )

    return {
        "status": "queued",
        "slug": expected_slug,
        "domain": domain,
        "poll_url": f"/api/admin/onboard/{expected_slug}/status",
    }


# ---------------------------------------------------------------------------
# Phase 6 — persona MCQ + upsert
# ---------------------------------------------------------------------------


class PersonaUpsertRequest(BaseModel):
    """MCQ submission. Every field is optional; missing fields keep the
    persona's existing value (or the role-default if no persona exists).
    """
    role: str | None = Field(default=None)
    esg_focus: list[str] | None = Field(default=None)
    frameworks: list[str] | None = Field(default=None)
    geographies: list[str] | None = Field(default=None)
    horizon: str | None = Field(default=None)
    decision_style: str | None = Field(default=None)
    risk_appetite: str | None = Field(default=None)


def _caller_user_id(claims: dict[str, Any] | None) -> str:
    if not isinstance(claims, dict):
        return ""
    sub = claims.get("sub") or claims.get("email") or ""
    return str(sub).strip().lower()


@router.get("/persona/questions")
def persona_questions() -> dict[str, Any]:
    """Phase 6 §8.2 — return the 6-question MCQ schema (static).

    Frontend renders this as a wizard. Field IDs match the keys on
    PersonaUpsertRequest so the response shape is round-trippable.
    No auth required: the schema is non-sensitive product copy.
    """
    from engine.persona import PERSONA_QUESTIONS
    return {"questions": PERSONA_QUESTIONS}


@router.get("/persona")
def get_my_persona(
    _: None = Depends(require_auth),
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    """Return the caller's stored persona, or a role-default fall-back
    when none has been saved yet.

    Includes a `mcq_completed` flag so the UI can show the "complete your
    profile" banner only when the user actually skipped the MCQ.
    """
    from engine.persona import default_persona_for_role, get_persona

    user_id = _caller_user_id(claims)
    if not user_id:
        raise HTTPException(status_code=401, detail="missing user identity")

    stored = get_persona(user_id)
    if stored is not None:
        return {"persona": stored.to_dict(), "mcq_completed": True}

    # Fall back to neutral default keyed by role hint (none in the JWT today
    # → "other" yields a safe-but-empty persona)
    role_hint = "other"
    default = default_persona_for_role(user_id, role_hint)
    return {"persona": default.to_dict(), "mcq_completed": False}


@router.put("/persona")
def upsert_my_persona(
    body: PersonaUpsertRequest,
    _: None = Depends(require_auth),
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    """Upsert the caller's persona from MCQ answers.

    - Caps multi-select fields at 3 entries (per plan §8.2)
    - Validates enum values via deserialise_persona (filters invalid
      tokens silently rather than 422-ing on a bad option label)
    - Bumps last_edited_at on every save
    - Returns the persona as stored
    """
    from engine.persona import (
        default_persona_for_role,
        deserialise_persona,
        get_persona,
        upsert_persona,
    )

    user_id = _caller_user_id(claims)
    if not user_id:
        raise HTTPException(status_code=401, detail="missing user identity")

    # Start from existing persona so partial updates don't blow away
    # untouched fields. Fall back to role-default for first-time saves.
    existing = get_persona(user_id) or default_persona_for_role(
        user_id, body.role or "other",
    )
    base = existing.to_dict()

    # Apply only the keys the caller actually sent — None means "leave alone"
    overrides: dict[str, Any] = {}
    for field_name in (
        "role", "esg_focus", "frameworks", "geographies",
        "horizon", "decision_style", "risk_appetite",
    ):
        v = getattr(body, field_name, None)
        if v is None:
            continue
        if isinstance(v, list):
            # Cap multi-select to 3 entries (plan §8.2)
            v = list(v)[:3]
        overrides[field_name] = v

    merged = {**base, **overrides, "user_id": user_id}
    persona = deserialise_persona(merged)
    saved = upsert_persona(persona)
    return {"persona": saved.to_dict(), "mcq_completed": True}
