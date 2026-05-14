"""Memory CRUD over SQLite."""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from engine.memory.schema import ensure_schema


@dataclass
class MemoryRecord:
    memory_id: str
    tenant_id: str
    user_id: str | None
    scope: str        # 'personal' | 'shared'
    fact_kind: str    # 'fact' | 'preference' | 'decision' | 'open_thread'
    content: str
    source_conversation_id: str | None = None
    source_message_id: str | None = None
    confidence: float | None = None
    created_at: str = ""
    last_accessed: str | None = None
    access_count: int = 0
    deactivated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "scope": self.scope,
            "fact_kind": self.fact_kind,
            "content": self.content,
            "source_conversation_id": self.source_conversation_id,
            "source_message_id": self.source_message_id,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
            "deactivated_at": self.deactivated_at,
        }


@contextmanager
def _connect() -> Iterator[Any]:
    from engine.db import connect as _db_connect
    with _db_connect() as conn:
        yield conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def insert_memory(
    *,
    tenant_id: str,
    user_id: str | None,
    scope: str,
    fact_kind: str,
    content: str,
    source_conversation_id: str | None = None,
    source_message_id: str | None = None,
    confidence: float | None = None,
) -> MemoryRecord:
    ensure_schema()
    if scope not in ("personal", "shared"):
        raise ValueError(f"scope must be personal|shared, got {scope!r}")
    if fact_kind not in ("fact", "preference", "decision", "open_thread"):
        raise ValueError(f"fact_kind invalid: {fact_kind!r}")
    if not content.strip():
        raise ValueError("content must be non-empty")
    content = content[:2000]

    mid = uuid.uuid4().hex
    ts = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO tenant_memory
            (memory_id, tenant_id, user_id, scope, fact_kind, content,
             source_conversation_id, source_message_id, confidence,
             created_at, last_accessed, access_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (mid, tenant_id, user_id, scope, fact_kind, content,
             source_conversation_id, source_message_id, confidence,
             ts, ts),
        )
    return MemoryRecord(
        memory_id=mid, tenant_id=tenant_id, user_id=user_id,
        scope=scope, fact_kind=fact_kind, content=content,
        source_conversation_id=source_conversation_id,
        source_message_id=source_message_id,
        confidence=confidence, created_at=ts, last_accessed=ts, access_count=0,
    )


def list_memories(
    *,
    tenant_id: str,
    user_id: str | None,
    include_deactivated: bool = False,
    limit: int = 100,
) -> list[MemoryRecord]:
    """List memories visible to this (tenant, user).

    'shared' memories for the tenant are always included. 'personal'
    memories only if user_id matches.
    """
    ensure_schema()
    sql = """
    SELECT memory_id, tenant_id, user_id, scope, fact_kind, content,
           source_conversation_id, source_message_id, confidence,
           created_at, last_accessed, access_count, deactivated_at
      FROM tenant_memory
     WHERE tenant_id=?
       AND (scope='shared' OR user_id=?)
    """
    params: list[Any] = [tenant_id, user_id]
    if not include_deactivated:
        sql += " AND deactivated_at IS NULL"
    sql += " ORDER BY last_accessed DESC, created_at DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        MemoryRecord(
            memory_id=r[0], tenant_id=r[1], user_id=r[2], scope=r[3],
            fact_kind=r[4], content=r[5],
            source_conversation_id=r[6], source_message_id=r[7],
            confidence=r[8], created_at=r[9], last_accessed=r[10],
            access_count=int(r[11] or 0), deactivated_at=r[12],
        )
        for r in rows
    ]


def delete_memory(
    *, memory_id: str, tenant_id: str, user_id: str | None,
) -> bool:
    """Soft-delete (sets deactivated_at). Owner check enforced."""
    ensure_schema()
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE tenant_memory SET deactivated_at=?
                WHERE memory_id=? AND tenant_id=?
                  AND (scope='shared' OR user_id=?)
                  AND deactivated_at IS NULL""",
            (_now(), memory_id, tenant_id, user_id),
        )
    return cur.rowcount > 0


def _touch_access(memory_ids: list[str]) -> None:
    """Bump last_accessed + access_count for retrieved memories."""
    if not memory_ids:
        return
    ts = _now()
    placeholders = ",".join("?" * len(memory_ids))
    with _connect() as conn:
        conn.execute(
            f"""UPDATE tenant_memory
                  SET last_accessed=?, access_count=access_count+1
                WHERE memory_id IN ({placeholders})""",
            [ts, *memory_ids],
        )
