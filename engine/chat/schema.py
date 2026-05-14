"""SQLite schema for chat persistence.

Tables created lazily on first use via `ensure_schema()`. The
existing snowkap.db connection helper (from engine/index/sqlite_index.py)
handles WAL mode + cross-process safety.
"""
from __future__ import annotations

import logging
from typing import Any, Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)

CHAT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS chat_conversations (
    conversation_id  TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL,
    user_id          TEXT NOT NULL,
    title            TEXT,
    created_at       TEXT NOT NULL,
    last_message_at  TEXT NOT NULL,
    message_count    INTEGER DEFAULT 0,
    archived_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_conv_user_recent
    ON chat_conversations(user_id, last_message_at DESC);
CREATE INDEX IF NOT EXISTS idx_conv_tenant_recent
    ON chat_conversations(tenant_id, last_message_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    message_id        TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL,
    conversation_id   TEXT NOT NULL REFERENCES chat_conversations(conversation_id) ON DELETE CASCADE,
    user_id           TEXT,
    role              TEXT NOT NULL CHECK(role IN ('user','assistant','tool','system')),
    content           TEXT,
    toulmin           TEXT,
    phase_k_tags      TEXT,
    skill_invocations TEXT,
    model_used        TEXT,
    usage             TEXT,
    finish_reason     TEXT,
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_msg_conv_time
    ON chat_messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_msg_tenant_time
    ON chat_messages(tenant_id, created_at);
"""

# Phase C — FTS5 virtual table for full-text search over chat content.
# Created lazily by ensure_schema(); falls back gracefully if the host's
# SQLite build was compiled without FTS5 (in which case search uses
# LIKE %q% on the base table).
CHAT_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts USING fts5(
    content, tenant_id UNINDEXED, conversation_id UNINDEXED, message_id UNINDEXED,
    content='chat_messages', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS chat_messages_fts_insert AFTER INSERT ON chat_messages
BEGIN
    INSERT INTO chat_messages_fts(rowid, content, tenant_id, conversation_id, message_id)
    VALUES (new.rowid, new.content, new.tenant_id, new.conversation_id, new.message_id);
END;

CREATE TRIGGER IF NOT EXISTS chat_messages_fts_delete AFTER DELETE ON chat_messages
BEGIN
    INSERT INTO chat_messages_fts(chat_messages_fts, rowid, content, tenant_id, conversation_id, message_id)
    VALUES('delete', old.rowid, old.content, old.tenant_id, old.conversation_id, old.message_id);
END;

CREATE TRIGGER IF NOT EXISTS chat_messages_fts_update AFTER UPDATE ON chat_messages
BEGIN
    INSERT INTO chat_messages_fts(chat_messages_fts, rowid, content, tenant_id, conversation_id, message_id)
    VALUES('delete', old.rowid, old.content, old.tenant_id, old.conversation_id, old.message_id);
    INSERT INTO chat_messages_fts(rowid, content, tenant_id, conversation_id, message_id)
    VALUES (new.rowid, new.content, new.tenant_id, new.conversation_id, new.message_id);
END;
"""


_SCHEMA_READY = False


@contextmanager
def _connect() -> Iterator[Any]:
    """SQLite connection via the project's backend-aware helper."""
    from engine.db import connect as _db_connect
    with _db_connect() as conn:
        yield conn


_FTS5_AVAILABLE: bool | None = None


def fts5_available() -> bool:
    """Return True when the host SQLite build supports FTS5.

    Cached on first call. Used by `search_conversations` to pick the
    fast FTS5 path vs the LIKE fallback at runtime.
    """
    global _FTS5_AVAILABLE
    if _FTS5_AVAILABLE is not None:
        return _FTS5_AVAILABLE
    with _connect() as conn:
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe USING fts5(x)"
            )
            conn.execute("DROP TABLE IF EXISTS _fts5_probe")
            _FTS5_AVAILABLE = True
        except Exception:
            _FTS5_AVAILABLE = False
    return _FTS5_AVAILABLE


def ensure_schema() -> None:
    """Create chat tables + indexes if absent. Idempotent.

    FTS5 virtual table + triggers are created when the host SQLite
    build supports them. Skipped gracefully otherwise — search falls
    back to LIKE.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    try:
        from engine.index.sqlite_index import DB_PATH, _ensure_wal_mode
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ensure_wal_mode()
    except Exception as exc:
        logger.debug("chat.schema: WAL setup skipped: %s", exc)

    with _connect() as conn:
        conn.executescript(CHAT_SCHEMA_SQL)
    if fts5_available():
        try:
            with _connect() as conn:
                conn.executescript(CHAT_FTS_SQL)
                # Self-heal: rebuild the FTS aux structures from the base
                # table. Idempotent + fixes any partial-write state from
                # earlier schema versions.
                try:
                    conn.execute(
                        "INSERT INTO chat_messages_fts(chat_messages_fts) VALUES('rebuild')"
                    )
                except Exception:
                    # rebuild can fail on a fresh, empty table — that's fine
                    pass
                conn.commit()
        except Exception as exc:
            logger.warning("chat.schema: FTS5 setup failed (falling back to LIKE): %s", exc)
    _SCHEMA_READY = True


def _reset_schema_flag() -> None:
    """Test-only — forces a re-creation check on the next ensure_schema."""
    global _SCHEMA_READY
    _SCHEMA_READY = False
