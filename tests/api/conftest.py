"""Phase C — Test client + DB-wipe fixtures shared across api/ tests."""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def api_key(monkeypatch):
    """Set a known API key in env so the legacy auth middleware passes."""
    key = "test-api-key-phase-c"
    monkeypatch.setenv("SNOWKAP_API_KEY", key)
    return key


@pytest.fixture
def headers(api_key):
    return {"X-API-Key": api_key}


@pytest.fixture
def client(api_key):
    from fastapi.testclient import TestClient

    from api.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _wipe_chat_memory():
    """Phase C uses the shared snowkap.db — wipe chat + memory before each test."""
    from engine.chat.schema import _connect as _chat_connect, ensure_schema as _chat_schema
    from engine.memory.schema import _connect as _mem_connect, ensure_schema as _mem_schema

    _chat_schema()
    _mem_schema()
    with _chat_connect() as conn:
        conn.execute("DELETE FROM chat_messages")
        conn.execute("DELETE FROM chat_conversations")
        conn.commit()
    with _mem_connect() as conn:
        conn.execute("DELETE FROM tenant_memory")
        conn.commit()
    yield
