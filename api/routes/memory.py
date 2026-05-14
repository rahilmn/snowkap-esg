"""Phase C — Memory CRUD + extraction trigger.

Same (tenant, user) scoping as conversations.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth_context import get_bearer_claims
from engine.memory.extractor import extract_memories_from_conversation
from engine.memory.store import delete_memory, insert_memory, list_memories

router = APIRouter(prefix="/api/memory", tags=["memory"])


def _scope(claims: dict[str, Any]) -> tuple[str, str]:
    tenant = str(claims.get("tenant_slug") or claims.get("tenant") or "default")
    user = str(claims.get("sub") or "anonymous")
    return tenant, user


class InsertMemoryRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    scope: str = Field(default="personal", pattern="^(personal|shared)$")
    fact_kind: str = Field(default="fact",
                           pattern="^(fact|preference|decision|open_thread)$")
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    source_conversation_id: str = Field(..., min_length=1)


@router.get("")
def list_my_memories(
    limit: int = Query(default=50, ge=1, le=200),
    claims: dict[str, Any] = Depends(get_bearer_claims),
):
    tenant, user = _scope(claims)
    records = list_memories(tenant_id=tenant, user_id=user, limit=limit)
    return {"memories": [r.__dict__ for r in records], "count": len(records)}


@router.post("", status_code=201)
def add_memory(
    body: InsertMemoryRequest,
    claims: dict[str, Any] = Depends(get_bearer_claims),
):
    tenant, user = _scope(claims)
    record = insert_memory(
        tenant_id=tenant,
        user_id=user if body.scope == "personal" else None,
        scope=body.scope,
        fact_kind=body.fact_kind,
        content=body.content,
        confidence=body.confidence,
        source_conversation_id=body.source_conversation_id,
    )
    return {"memory": record.__dict__}


@router.delete("/{memory_id}", status_code=204)
def delete(
    memory_id: str,
    claims: dict[str, Any] = Depends(get_bearer_claims),
):
    tenant, user = _scope(claims)
    ok = delete_memory(memory_id=memory_id, tenant_id=tenant, user_id=user)
    if not ok:
        raise HTTPException(status_code=404, detail="memory not found")


@router.post("/extract/{conversation_id}")
def trigger_extract(
    conversation_id: str,
    claims: dict[str, Any] = Depends(get_bearer_claims),
):
    tenant, user = _scope(claims)
    extracted = extract_memories_from_conversation(
        conversation_id=conversation_id,
        tenant_id=tenant, user_id=user,
    )
    return {
        "conversation_id": conversation_id,
        "extracted_count": len(extracted),
        "memories": [r.__dict__ for r in extracted],
    }
