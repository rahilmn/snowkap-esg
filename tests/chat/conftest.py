"""Chat-test fixtures — wipe the chat tables before each test.

Phase C chat persistence uses the shared `data/snowkap.db` (matches
the existing `engine/index/sqlite_index.py` pattern), so without
this fixture old rows from prior test runs leak across tests.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _wipe_chat_tables():
    from engine.chat.schema import _connect, ensure_schema

    ensure_schema()
    with _connect() as conn:
        conn.execute("DELETE FROM chat_messages")
        conn.execute("DELETE FROM chat_conversations")
        conn.commit()
    yield
