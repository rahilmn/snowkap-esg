"""MCP adapters for memory-recall / memory-list.

Both honour the per-(tenant, user) scope enforced inside the memory
store helpers — handlers do NOT short-circuit the filter.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.memory.retrieval import retrieve_for_injection
from engine.memory.store import list_memories


def _serialise_record(rec: Any) -> dict[str, Any]:
    return {
        "memory_id": rec.memory_id,
        "tenant_id": rec.tenant_id,
        "user_id": rec.user_id,
        "scope": rec.scope,
        "fact_kind": rec.fact_kind,
        "content": rec.content,
        "confidence": rec.confidence,
        "created_at": rec.created_at,
        "last_accessed": rec.last_accessed,
        "access_count": rec.access_count,
    }


def handle_memory_recall(payload: dict[str, Any], base_data: Path) -> dict[str, Any]:
    """Top-N memories most relevant to the query, BM25-ranked."""
    records = retrieve_for_injection(
        tenant_id=payload["tenant"],
        user_id=payload["user"],
        query=payload["query"],
        top_n=payload.get("top_n", 8),
    )
    return {
        "tenant": payload["tenant"],
        "user": payload["user"],
        "memories": [_serialise_record(r) for r in records],
        "count": len(records),
    }


def handle_memory_list(payload: dict[str, Any], base_data: Path) -> dict[str, Any]:
    """Browse stored memories for a (tenant, user) — most-recent first."""
    records = list_memories(
        tenant_id=payload["tenant"],
        user_id=payload.get("user"),
        limit=payload.get("limit", 50),
    )
    return {
        "tenant": payload["tenant"],
        "user": payload.get("user"),
        "memories": [_serialise_record(r) for r in records],
        "count": len(records),
    }
