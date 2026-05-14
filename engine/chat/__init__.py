"""Phase C — Chat persistence layer (SQLite, in `data/snowkap.db`).

Schema mirrors Base Version's Postgres pattern (chat_conversations +
chat_messages) but adapted to SQLite + WAL. Per-(tenant, user)
isolation enforced by filter at every read site (no SQLite RLS).
"""
from engine.chat.conversations import (
    ConversationSummary,
    archive_conversation,
    delete_conversation,
    ensure_conversation,
    fork_conversation,
    get_conversation,
    list_conversations,
    rename_conversation,
    search_conversations,
)
from engine.chat.messages import (
    ChatMessage,
    insert_assistant_message,
    insert_user_message,
    load_conversation_history,
    load_messages_for_llm,
)
from engine.chat.schema import ensure_schema

__all__ = [
    "ChatMessage",
    "ConversationSummary",
    "archive_conversation",
    "delete_conversation",
    "ensure_conversation",
    "ensure_schema",
    "fork_conversation",
    "get_conversation",
    "insert_assistant_message",
    "insert_user_message",
    "list_conversations",
    "load_conversation_history",
    "load_messages_for_llm",
    "rename_conversation",
    "search_conversations",
]
