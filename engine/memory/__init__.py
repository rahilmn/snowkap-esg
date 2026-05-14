"""Phase C — Tenant + user memory store.

Persistent facts / preferences / decisions / open_threads extracted
from conversations via a secondary LLM pass after the conversation
ends. Stored in SQLite (`tenant_memory` table) and retrieved at
chat-time via BM25 over content (filtered to the requesting (tenant,
user) scope).
"""
from engine.memory.extractor import extract_memories_from_conversation
from engine.memory.retrieval import retrieve_for_injection
from engine.memory.schema import ensure_schema
from engine.memory.store import (
    MemoryRecord,
    delete_memory,
    insert_memory,
    list_memories,
)

__all__ = [
    "MemoryRecord",
    "delete_memory",
    "ensure_schema",
    "extract_memories_from_conversation",
    "insert_memory",
    "list_memories",
    "retrieve_for_injection",
]
