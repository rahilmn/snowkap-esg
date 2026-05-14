"""SQLite schema for tenant_memory."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

MEMORY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tenant_memory (
    memory_id              TEXT PRIMARY KEY,
    tenant_id              TEXT NOT NULL,
    user_id                TEXT,
    scope                  TEXT CHECK(scope IN ('personal','shared')),
    fact_kind              TEXT CHECK(fact_kind IN ('fact','preference','decision','open_thread')),
    content                TEXT NOT NULL,
    source_conversation_id TEXT,
    source_message_id      TEXT,
    toulmin                TEXT,
    phase_k_tags           TEXT,
    confidence             REAL,
    created_at             TEXT NOT NULL,
    last_accessed          TEXT,
    access_count           INTEGER DEFAULT 0,
    superseded_by          TEXT,
    deactivated_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_mem_user_recent
    ON tenant_memory(user_id, last_accessed DESC);
CREATE INDEX IF NOT EXISTS idx_mem_tenant_recent
    ON tenant_memory(tenant_id, last_accessed DESC);
"""


_SCHEMA_READY = False


@contextmanager
def _connect() -> Iterator[Any]:
    from engine.db import connect as _db_connect
    with _db_connect() as conn:
        yield conn


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _connect() as conn:
        conn.executescript(MEMORY_SCHEMA_SQL)
    _SCHEMA_READY = True
