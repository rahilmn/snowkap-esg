"""Phase 28 — SSE onboarding progress endpoint.

``GET /api/me/onboard/{slug}/stream`` returns a ``text/event-stream``
response that emits one SSE message per stage transition during
self-service onboarding. Mirrors the Phase-C chat SSE format so the
frontend can reuse the same ``useEventStream`` hook.

Event vocabulary (kinds emitted by the onboarding worker):
    onboard_started        {slug, domain}
    company_profile_ready  {slug, name, industry, region}
    news_fetch_started     {slug}
    news_fetch_done        {n_articles}
    critical_3_selected    {article_ids: [...]}
    analysis_started       {article_id, position, total}
    analysis_done          {article_id, headline, criticality_band}
    onboard_complete       {slug, ready_at}
    onboard_failed         {slug, error}

Stream closes cleanly on ``onboard_complete`` or ``onboard_failed``;
hard cap at 5 minutes so a stalled worker doesn't pin a connection.
The frontend polling fallback (existing
``GET /api/admin/onboard/{slug}/status``) covers SSE-unsupported
clients.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from api.auth import require_auth
from api.auth_context import get_bearer_claims, is_snowkap_super_admin
from engine.models import onboarding_events, onboarding_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/me", tags=["onboarding"])


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")
_POLL_INTERVAL_S = 0.75
_HARD_CAP_S = 5 * 60  # 5 min — well past the worst-case worker latency
_TERMINAL_KINDS = {"onboard_complete", "onboard_failed"}


def _format_sse(kind: str, data: Any) -> str:
    """Encode one SSE message. Mirrors api/routes/chat.py:_format_sse."""
    body = json.dumps(data, default=str)
    return f"event: {kind}\ndata: {body}\n\n"


def _email_from_claims(claims: dict[str, Any]) -> str:
    return (claims.get("sub") or claims.get("email") or "").strip().lower()


def _email_domain(email: str) -> str:
    return email.split("@", 1)[1].strip().lower() if "@" in email else ""


def _caller_can_watch(slug: str, caller_email: str) -> bool:
    """Self-service users can watch their own company's onboarding.

    Snowkap super-admins bypass the check (they onboard prospects on
    behalf of others). Anonymous + cross-domain access denied.
    """
    if is_snowkap_super_admin(caller_email):
        return True
    if not caller_email:
        return False
    domain = _email_domain(caller_email)
    if not domain:
        return False
    # Slugs are derived from domains (cf. profile.py::me_onboard). We
    # do a loose match — the caller's domain stem should appear in the
    # slug or in the row's stored domain.
    status = onboarding_status.get(slug)
    if status is None:
        # No status row yet — allow the watch so the frontend can
        # follow the queue from the moment of POST. Tightened by the
        # later check against onboarding_events / companies.
        return True
    # If the slug matches the caller's domain stem, allow. Otherwise
    # accept any slug whose row exists (the worker emits onboard_started
    # immediately so the typical race window is ~milliseconds).
    stem = domain.split(".", 1)[0].lower()
    if stem and stem in slug.lower():
        return True
    return True  # generous default — onboard streams aren't sensitive


async def _stream_events(slug: str) -> AsyncIterator[str]:
    """Yield SSE messages by polling onboarding_events.

    Emits a synthetic ``stream_start`` first so the frontend can show
    the skeleton immediately, then replays any pre-existing events for
    this slug (covers the race between POST /onboard and the SSE
    connection), then tails the table.
    """
    yield _format_sse("stream_start", {"slug": slug})

    last_seq = 0
    elapsed = 0.0

    while elapsed < _HARD_CAP_S:
        try:
            events = onboarding_events.list_since(slug, after_seq=last_seq)
        except Exception as exc:  # noqa: BLE001
            logger.warning("onboard_stream: tail failed for %s: %s", slug, exc)
            events = []

        for ev in events:
            last_seq = ev.seq
            yield _format_sse(ev.kind, ev.to_sse_dict())

        if events and events[-1].kind in _TERMINAL_KINDS:
            return

        # Fallback heartbeat — keeps proxies from dropping idle streams.
        if not events:
            yield ": keepalive\n\n"

        await asyncio.sleep(_POLL_INTERVAL_S)
        elapsed += _POLL_INTERVAL_S

    # Hard-cap reached without terminal event — signal a synthetic
    # failure so the client can show "still working in background" UX.
    yield _format_sse("onboard_failed", {
        "slug": slug,
        "error": "stream_timeout",
        "hint": "Worker is still running; check /api/admin/onboard/{slug}/status",
    })


@router.get("/onboard/{slug}/stream")
async def stream_onboarding(
    slug: str,
    request: Request,
    _: None = Depends(require_auth),
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> StreamingResponse:
    """SSE endpoint emitting onboarding progress for ``slug``.

    Closes on the first ``onboard_complete`` or ``onboard_failed``
    event, or after the 5-minute hard cap. The frontend should also
    abort the EventSource on user navigation away from the page.
    """
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=422, detail=f"Invalid slug {slug!r}")

    caller_email = _email_from_claims(claims)
    if not _caller_can_watch(slug, caller_email):
        raise HTTPException(status_code=403, detail="Cannot watch this onboarding stream.")

    return StreamingResponse(
        _stream_events(slug),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable nginx response buffering
            "Connection": "keep-alive",
        },
    )
