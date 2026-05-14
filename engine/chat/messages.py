"""Chat message insert + load.

User + assistant messages are written atomically with metadata
(Toulmin, phase_k_tags, skill_invocations, usage). Conversation
message_count + last_message_at are kept consistent on every insert.
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

from engine.chat.schema import ensure_schema


@dataclass
class ChatMessage:
    message_id: str
    conversation_id: str
    tenant_id: str
    user_id: str | None
    role: str  # user | assistant | tool | system
    content: str | None
    toulmin: dict[str, Any] | None = None
    phase_k_tags: dict[str, Any] | None = None
    skill_invocations: list[dict[str, Any]] = field(default_factory=list)
    model_used: str | None = None
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "role": self.role,
            "content": self.content,
            "toulmin": self.toulmin,
            "phase_k_tags": self.phase_k_tags,
            "skill_invocations": self.skill_invocations,
            "model_used": self.model_used,
            "usage": self.usage,
            "finish_reason": self.finish_reason,
            "created_at": self.created_at,
        }


@contextmanager
def _connect() -> Iterator[Any]:
    from engine.db import connect as _db_connect
    with _db_connect() as conn:
        yield conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_or_none(v: Any) -> str | None:
    if v is None:
        return None
    try:
        return json.dumps(v, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


def _parse_json_or_none(s: str | None) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def _insert_message(
    *,
    tenant_id: str,
    conversation_id: str,
    user_id: str | None,
    role: str,
    content: str | None,
    toulmin: dict[str, Any] | None = None,
    phase_k_tags: dict[str, Any] | None = None,
    skill_invocations: list[dict[str, Any]] | None = None,
    model_used: str | None = None,
    usage: dict[str, Any] | None = None,
    finish_reason: str | None = None,
) -> ChatMessage:
    """Atomic write: insert message + bump conversation counters."""
    ensure_schema()
    if role not in ("user", "assistant", "tool", "system"):
        raise ValueError(f"role must be user|assistant|tool|system, got {role!r}")

    message_id = uuid.uuid4().hex
    ts = _now()

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO chat_messages
            (message_id, tenant_id, conversation_id, user_id, role, content,
             toulmin, phase_k_tags, skill_invocations, model_used, usage,
             finish_reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id, tenant_id, conversation_id, user_id, role, content,
                _json_or_none(toulmin),
                _json_or_none(phase_k_tags),
                _json_or_none(skill_invocations or []),
                model_used,
                _json_or_none(usage),
                finish_reason,
                ts,
            ),
        )
        conn.execute(
            """
            UPDATE chat_conversations
               SET last_message_at=?, message_count=message_count + 1
             WHERE conversation_id=?
            """,
            (ts, conversation_id),
        )

    return ChatMessage(
        message_id=message_id,
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        content=content,
        toulmin=toulmin,
        phase_k_tags=phase_k_tags,
        skill_invocations=skill_invocations or [],
        model_used=model_used,
        usage=usage,
        finish_reason=finish_reason,
        created_at=ts,
    )


def insert_user_message(
    *,
    tenant_id: str,
    conversation_id: str,
    user_id: str,
    content: str,
    skill_invocations: list[dict[str, Any]] | None = None,
) -> ChatMessage:
    return _insert_message(
        tenant_id=tenant_id, conversation_id=conversation_id,
        user_id=user_id, role="user", content=content,
        skill_invocations=skill_invocations,
    )


def insert_assistant_message(
    *,
    tenant_id: str,
    conversation_id: str,
    content: str,
    toulmin: dict[str, Any] | None = None,
    phase_k_tags: dict[str, Any] | None = None,
    skill_invocations: list[dict[str, Any]] | None = None,
    model_used: str | None = None,
    usage: dict[str, Any] | None = None,
    finish_reason: str | None = None,
) -> ChatMessage:
    return _insert_message(
        tenant_id=tenant_id, conversation_id=conversation_id,
        user_id=None, role="assistant", content=content,
        toulmin=toulmin, phase_k_tags=phase_k_tags,
        skill_invocations=skill_invocations,
        model_used=model_used, usage=usage,
        finish_reason=finish_reason,
    )


def load_conversation_history(
    *,
    conversation_id: str,
    tenant_id: str,
    user_id: str,
    limit: int = 200,
) -> list[ChatMessage]:
    """Load every message for a conversation (ascending by created_at).

    Owner verification: rejects with empty list if (tenant_id, user_id)
    doesn't own the conversation.
    """
    ensure_schema()
    with _connect() as conn:
        owner = conn.execute(
            "SELECT 1 FROM chat_conversations WHERE conversation_id=? AND tenant_id=? AND user_id=?",
            (conversation_id, tenant_id, user_id),
        ).fetchone()
        if owner is None:
            return []

        rows = conn.execute(
            """
            SELECT message_id, conversation_id, tenant_id, user_id, role, content,
                   toulmin, phase_k_tags, skill_invocations, model_used, usage,
                   finish_reason, created_at
              FROM chat_messages
             WHERE conversation_id=?
             ORDER BY created_at ASC
             LIMIT ?
            """,
            (conversation_id, limit),
        ).fetchall()

    out: list[ChatMessage] = []
    for r in rows:
        out.append(ChatMessage(
            message_id=r[0],
            conversation_id=r[1],
            tenant_id=r[2],
            user_id=r[3],
            role=r[4],
            content=r[5],
            toulmin=_parse_json_or_none(r[6]),
            phase_k_tags=_parse_json_or_none(r[7]),
            skill_invocations=_parse_json_or_none(r[8]) or [],
            model_used=r[9],
            usage=_parse_json_or_none(r[10]),
            finish_reason=r[11],
            created_at=r[12],
        ))
    return out


def load_messages_for_llm(
    *,
    conversation_id: str,
    tenant_id: str,
    user_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Load messages in the OpenAI chat-format shape.

    Returns `[{"role": str, "content": str}, ...]` ordered ascending.
    Limits to the most-recent `limit` messages (sliding window).
    """
    msgs = load_conversation_history(
        conversation_id=conversation_id,
        tenant_id=tenant_id, user_id=user_id, limit=10_000,
    )
    if len(msgs) > limit:
        msgs = msgs[-limit:]
    return [{"role": m.role, "content": m.content or ""} for m in msgs]
