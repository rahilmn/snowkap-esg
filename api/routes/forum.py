"""Phase 34.6 — Forum API.

Endpoints (all JWT-gated; identity is the `sub` claim, never anonymous):
  GET    /api/forum/threads                         → list threads (optional ?tag filter)
  POST   /api/forum/threads                         → create a new thread
  GET    /api/forum/threads/{thread_id}             → fetch one thread + replies
  DELETE /api/forum/threads/{thread_id}             → author-only soft-delete
  POST   /api/forum/threads/{thread_id}/replies     → post a reply
  DELETE /api/forum/replies/{reply_id}              → author-only soft-delete on a reply

Tag taxonomy is fixed (BRSR / Climate / CBAM / Governance / Audit) and
enforced by the model layer.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import require_api_key
from api.auth_context import get_bearer_claims
from engine.models import forum_threads as _ft

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["forum"],
    dependencies=[Depends(require_api_key)],
)


class ThreadCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    body: str = Field(..., min_length=1, max_length=8000)
    tag: str = Field(..., description="One of BRSR / Climate / CBAM / Governance / Audit")


class ReplyCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)


def _author_identity(claims: dict[str, Any]) -> tuple[str, str]:
    email = (claims.get("sub") or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=403, detail="JWT subject is not a valid email; cannot post.")
    name = (claims.get("name") or "").strip()
    if not name:
        name = email.split("@", 1)[0].title()
    return email, name


@router.get("/api/forum/threads")
def list_threads(
    tag: str | None = None,
    limit: int = 50,
    claims: dict[str, Any] = Depends(get_bearer_claims),  # noqa: ARG001 — JWT enforced
) -> dict[str, Any]:
    try:
        threads = _ft.list_threads(tag=tag, limit=min(max(limit, 1), 200))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "count": len(threads),
        "tag": tag,
        "threads": [t.to_dict() for t in threads],
    }


@router.post("/api/forum/threads")
def create_thread(
    req: ThreadCreate,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    email, name = _author_identity(claims)
    try:
        thread = _ft.create_thread(
            title=req.title, body=req.body, tag=req.tag,
            author_email=email, author_name=name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"thread": thread.to_dict()}


@router.get("/api/forum/threads/{thread_id}")
def get_thread(
    thread_id: str,
    claims: dict[str, Any] = Depends(get_bearer_claims),  # noqa: ARG001
) -> dict[str, Any]:
    thread = _ft.get_thread(thread_id)
    # FORUM-5 — a soft-deleted thread must 404, not return masked title/body
    # with all the real reply bodies still attached (list_threads already
    # filters deleted_at; get_thread did not).
    if thread is None or getattr(thread, "deleted_at", None):
        raise HTTPException(status_code=404, detail=f"thread not found: {thread_id}")
    replies = _ft.list_replies(thread_id)
    return {
        "thread": thread.to_dict(),
        "replies": [r.to_dict() for r in replies],
    }


@router.delete("/api/forum/threads/{thread_id}")
def delete_thread(
    thread_id: str,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    email, _ = _author_identity(claims)
    ok = _ft.soft_delete_thread(thread_id, email)
    if not ok:
        raise HTTPException(
            status_code=403,
            detail="thread not found or not owned by the requester",
        )
    return {"deleted": True, "thread_id": thread_id}


@router.post("/api/forum/threads/{thread_id}/replies")
def add_reply(
    thread_id: str,
    req: ReplyCreate,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    email, name = _author_identity(claims)
    try:
        reply = _ft.add_reply(
            thread_id=thread_id, body=req.body,
            author_email=email, author_name=name,
        )
    except ValueError as exc:
        # Distinguish "thread not found" → 404 from validation → 422.
        msg = str(exc)
        if "thread not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=422, detail=msg)
    return {"reply": reply.to_dict()}


@router.delete("/api/forum/replies/{reply_id}")
def delete_reply(
    reply_id: str,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    email, _ = _author_identity(claims)
    ok = _ft.soft_delete_reply(reply_id, email)
    if not ok:
        raise HTTPException(
            status_code=403,
            detail="reply not found or not owned by the requester",
        )
    return {"deleted": True, "reply_id": reply_id}
