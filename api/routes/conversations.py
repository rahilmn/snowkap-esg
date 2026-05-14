"""Phase C — Conversation CRUD + search + fork.

Scoped to (tenant, user) at every read site. The tenant is taken from
the bearer-token claims (`tenant_slug` claim, with a header override
for legacy callers); the user comes from the `sub` claim.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth_context import get_bearer_claims
from engine.chat.conversations import (
    archive_conversation,
    delete_conversation,
    ensure_conversation,
    fork_conversation,
    get_conversation,
    list_conversations,
    rename_conversation,
    search_conversations,
)
from engine.chat.messages import load_conversation_history

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _scope(claims: dict[str, Any]) -> tuple[str, str]:
    tenant = str(claims.get("tenant_slug") or claims.get("tenant") or "default")
    user = str(claims.get("sub") or "anonymous")
    return tenant, user


class RenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)


class ForkRequest(BaseModel):
    up_to_message_id: str | None = None


@router.get("")
def list_my_conversations(
    include_archived: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    claims: dict[str, Any] = Depends(get_bearer_claims),
):
    tenant, user = _scope(claims)
    rows = list_conversations(
        tenant_id=tenant, user_id=user,
        include_archived=include_archived, limit=limit,
    )
    return {"conversations": [r.__dict__ for r in rows], "count": len(rows)}


@router.get("/search")
def search_my_conversations(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(default=20, ge=1, le=100),
    claims: dict[str, Any] = Depends(get_bearer_claims),
):
    tenant, user = _scope(claims)
    rows = search_conversations(
        tenant_id=tenant, user_id=user, query=q, limit=limit,
    )
    # search_conversations returns dicts already; pass through unchanged.
    return {"hits": rows, "q": q, "count": len(rows)}


@router.get("/{conversation_id}")
def get_my_conversation(
    conversation_id: str,
    claims: dict[str, Any] = Depends(get_bearer_claims),
):
    tenant, user = _scope(claims)
    summary = get_conversation(
        conversation_id=conversation_id, tenant_id=tenant, user_id=user,
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    messages = load_conversation_history(
        conversation_id=conversation_id, tenant_id=tenant, user_id=user,
    )
    return {
        "summary": summary.__dict__,
        "messages": [m.__dict__ for m in messages],
    }


@router.patch("/{conversation_id}/rename")
def rename(
    conversation_id: str,
    body: RenameRequest,
    claims: dict[str, Any] = Depends(get_bearer_claims),
):
    tenant, user = _scope(claims)
    ok = rename_conversation(
        conversation_id=conversation_id, tenant_id=tenant, user_id=user,
        title=body.title,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"ok": True}


@router.post("/{conversation_id}/archive")
def archive(
    conversation_id: str,
    claims: dict[str, Any] = Depends(get_bearer_claims),
):
    tenant, user = _scope(claims)
    ok = archive_conversation(
        conversation_id=conversation_id, tenant_id=tenant, user_id=user,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"ok": True}


@router.delete("/{conversation_id}")
def delete(
    conversation_id: str,
    claims: dict[str, Any] = Depends(get_bearer_claims),
):
    tenant, user = _scope(claims)
    ok = delete_conversation(
        conversation_id=conversation_id, tenant_id=tenant, user_id=user,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"ok": True}


@router.post("/{conversation_id}/fork")
def fork(
    conversation_id: str,
    body: ForkRequest,
    claims: dict[str, Any] = Depends(get_bearer_claims),
):
    tenant, user = _scope(claims)
    new_cid = fork_conversation(
        source_conversation_id=conversation_id,
        tenant_id=tenant, user_id=user,
        up_to_message_id=body.up_to_message_id,
    )
    if new_cid is None:
        raise HTTPException(status_code=404, detail="source conversation not found")
    return {"conversation_id": new_cid}
