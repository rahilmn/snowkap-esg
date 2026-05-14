"""Memory-test fixtures — wipe tenant_memory before each test.

Same rationale as `tests/chat/conftest.py` — shared DB needs explicit
test-level cleanup or rows from prior runs bleed through.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _wipe_memory_tables():
    from engine.memory.schema import _connect, ensure_schema

    ensure_schema()
    with _connect() as conn:
        conn.execute("DELETE FROM tenant_memory")
        conn.commit()
    yield
