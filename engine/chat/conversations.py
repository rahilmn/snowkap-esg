"""Conversation CRUD + search.

Per-(tenant, user) scoping enforced at every read by filter; no SQLite RLS.
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from engine.chat.schema import ensure_schema


@dataclass
class ConversationSummary:
    conversation_id: str
    tenant_id: str
    user_id: str
    title: str | None
    created_at: str
    last_message_at: str
    message_count: int
    archived_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "title": self.title,
            "created_at": self.created_at,
            "last_message_at": self.last_message_at,
            "message_count": self.message_count,
            "archived_at": self.archived_at,
        }


@contextmanager
def _connect() -> Iterator[Any]:
    from engine.db import connect as _db_connect
    with _db_connect() as conn:
        yield conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_summary(row: Any) -> ConversationSummary:
    """sqlite Row → ConversationSummary."""
    return ConversationSummary(
        conversation_id=row[0],
        tenant_id=row[1],
        user_id=row[2],
        title=row[3],
        created_at=row[4],
        last_message_at=row[5],
        message_count=int(row[6] or 0),
        archived_at=row[7],
    )


def ensure_conversation(
    *,
    conversation_id: str | None,
    tenant_id: str,
    user_id: str,
    title_seed: str | None = None,
) -> str:
    """Create a conversation if absent. Returns the conversation_id.

    When `conversation_id` is None, generates a uuid hex.
    When it's provided and exists, verifies ownership (`tenant_id` + `user_id` must match).
    """
    ensure_schema()
    if not conversation_id:
        conversation_id = uuid.uuid4().hex

    with _connect() as conn:
        row = conn.execute(
            "SELECT tenant_id, user_id FROM chat_conversations WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            now = _now()
            title = title_seed or "New conversation"
            conn.execute(
                """
                INSERT INTO chat_conversations
                (conversation_id, tenant_id, user_id, title, created_at, last_message_at, message_count)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (conversation_id, tenant_id, user_id, title, now, now),
            )
        else:
            owner_tenant, owner_user = row[0], row[1]
            if owner_tenant != tenant_id or owner_user != user_id:
                raise PermissionError(
                    f"conversation {conversation_id} owned by another (tenant, user)"
                )
    return conversation_id


def list_conversations(
    *,
    tenant_id: str,
    user_id: str,
    include_archived: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[ConversationSummary]:
    """List conversations for one user (within their tenant), newest first."""
    ensure_schema()
    sql = """
    SELECT conversation_id, tenant_id, user_id, title, created_at,
           last_message_at, message_count, archived_at
      FROM chat_conversations
     WHERE tenant_id=? AND user_id=?
    """
    if not include_archived:
        sql += " AND archived_at IS NULL"
    sql += " ORDER BY last_message_at DESC LIMIT ? OFFSET ?"
    with _connect() as conn:
        rows = conn.execute(sql, (tenant_id, user_id, limit, offset)).fetchall()
    return [_row_to_summary(r) for r in rows]


def get_conversation(
    *, conversation_id: str, tenant_id: str, user_id: str,
) -> ConversationSummary | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT conversation_id, tenant_id, user_id, title, created_at,
                   last_message_at, message_count, archived_at
              FROM chat_conversations
             WHERE conversation_id=? AND tenant_id=? AND user_id=?
            """,
            (conversation_id, tenant_id, user_id),
        ).fetchone()
    return _row_to_summary(row) if row else None


def rename_conversation(
    *, conversation_id: str, tenant_id: str, user_id: str, title: str,
) -> bool:
    ensure_schema()
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE chat_conversations SET title=?
                WHERE conversation_id=? AND tenant_id=? AND user_id=?""",
            (title[:300], conversation_id, tenant_id, user_id),
        )
    return cur.rowcount > 0


def archive_conversation(
    *, conversation_id: str, tenant_id: str, user_id: str,
) -> bool:
    ensure_schema()
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE chat_conversations SET archived_at=?
                WHERE conversation_id=? AND tenant_id=? AND user_id=?""",
            (_now(), conversation_id, tenant_id, user_id),
        )
    return cur.rowcount > 0


def delete_conversation(
    *, conversation_id: str, tenant_id: str, user_id: str,
) -> bool:
    """Hard delete + cascade to messages (ON DELETE CASCADE)."""
    ensure_schema()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM chat_conversations WHERE conversation_id=? AND tenant_id=? AND user_id=?",
            (conversation_id, tenant_id, user_id),
        )
    return cur.rowcount > 0


def fork_conversation(
    *,
    source_conversation_id: str,
    tenant_id: str,
    user_id: str,
    up_to_message_id: str | None = None,
) -> str:
    """Create a new conversation as a fork. Copies messages up to
    (and including) `up_to_message_id`, or all messages if None.

    Returns the new conversation_id.
    """
    ensure_schema()
    source = get_conversation(
        conversation_id=source_conversation_id,
        tenant_id=tenant_id, user_id=user_id,
    )
    if source is None:
        raise PermissionError("source conversation not found or not owned by user")

    new_id = uuid.uuid4().hex
    now = _now()
    forked_title = f"Fork of {source.title or 'conversation'}"

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO chat_conversations
            (conversation_id, tenant_id, user_id, title, created_at, last_message_at, message_count)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (new_id, tenant_id, user_id, forked_title, now, now),
        )

        # Copy messages from source
        sql = """
        SELECT role, content, toulmin, phase_k_tags, skill_invocations,
               model_used, usage, finish_reason, created_at, user_id, message_id
          FROM chat_messages WHERE conversation_id=? ORDER BY created_at
        """
        rows = conn.execute(sql, (source_conversation_id,)).fetchall()

        copied = 0
        for row in rows:
            (role, content, toulmin, phase_k_tags, skill_invocations,
             model_used, usage, finish_reason, created_at, msg_user_id, msg_id) = row
            new_msg_id = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO chat_messages
                (message_id, tenant_id, conversation_id, user_id, role, content,
                 toulmin, phase_k_tags, skill_invocations, model_used, usage,
                 finish_reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (new_msg_id, tenant_id, new_id, msg_user_id, role, content,
                 toulmin, phase_k_tags, skill_invocations, model_used, usage,
                 finish_reason, created_at),
            )
            copied += 1
            if up_to_message_id and msg_id == up_to_message_id:
                break

        conn.execute(
            "UPDATE chat_conversations SET message_count=? WHERE conversation_id=?",
            (copied, new_id),
        )
    return new_id


def _sanitise_fts_query(q: str) -> str:
    """Build an FTS5 MATCH expression that's safe + meaningful.

    Splits on whitespace, drops tokens shorter than 2 chars, escapes
    double-quotes by doubling them, joins with implicit AND.
    """
    tokens = [t.strip().replace('"', '""') for t in q.split() if len(t.strip()) >= 2]
    if not tokens:
        return ""
    return " ".join(f'"{t}"' for t in tokens)


def search_conversations(
    *,
    tenant_id: str,
    user_id: str,
    query: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search message content for the (tenant, user). Uses FTS5 when the host
    SQLite build supports it; falls back to LIKE otherwise.

    Returns hits with `conversation_id`, `message_id`, snippet, and
    `conversation_title`.
    """
    from engine.chat.schema import fts5_available
    ensure_schema()
    if not query.strip():
        return []

    rows: list[tuple] = []
    use_fts = fts5_available()
    if use_fts:
        match_expr = _sanitise_fts_query(query)
        if match_expr:
            try:
                with _connect() as conn:
                    rows = conn.execute(
                        """
                        SELECT m.conversation_id, m.message_id, m.role, m.content,
                               m.created_at, c.title
                          FROM chat_messages_fts fts
                          JOIN chat_messages m ON m.rowid = fts.rowid
                          JOIN chat_conversations c USING(conversation_id)
                         WHERE fts.chat_messages_fts MATCH ?
                           AND c.tenant_id=? AND c.user_id=?
                         ORDER BY m.created_at DESC
                         LIMIT ?
                        """,
                        (match_expr, tenant_id, user_id, limit),
                    ).fetchall()
            except Exception:
                # FTS5 query syntax error → fall through to LIKE
                rows = []
                use_fts = False
    if not rows and not use_fts:
        needle = f"%{query.strip()}%"
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT m.conversation_id, m.message_id, m.role, m.content, m.created_at,
                       c.title
                  FROM chat_messages m
                  JOIN chat_conversations c USING(conversation_id)
                 WHERE c.tenant_id=? AND c.user_id=?
                   AND m.content LIKE ?
                 ORDER BY m.created_at DESC
                 LIMIT ?
                """,
                (tenant_id, user_id, needle, limit),
            ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        content = row[3] or ""
        # Render a small snippet around the first hit
        idx = content.lower().find(query.lower())
        snippet = content[max(0, idx-40):idx+len(query)+80] if idx >= 0 else content[:120]
        out.append({
            "conversation_id": row[0],
            "message_id": row[1],
            "role": row[2],
            "snippet": snippet,
            "created_at": row[4],
            "conversation_title": row[5],
        })
    return out
