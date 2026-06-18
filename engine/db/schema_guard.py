"""Multi-DB-safe ``ensure_schema()`` guard.

Replaces the per-module ``_SCHEMA_READY = False`` boolean that several
``engine/models`` (and a few other) modules used to avoid re-running
``CREATE TABLE IF NOT EXISTS`` on every call.

The boolean latched ``True`` after the first ``ensure_schema()`` call
*regardless of which database the call targeted*. In a single-DB process
(production, on Supabase Postgres) that is correct and optimal — the CREATE
runs once and every later call is free. But the test suite points
``engine.db.connect()`` at different SQLite files across tests: the
``isolated_db`` fixture in ``tests/test_phase26_metrics.py`` monkeypatches
``get_data_path`` to a tmp dir, so a module's ``ensure_schema()`` can run
(and latch) against ``<tmp>/snowkap.db`` while a later test reads from the
real ``data/snowkap.db``. The table was never created there, so every
``save``/``load`` silently no-op'd (the ``test_phase51j`` regression).

This keys the "already ensured" state by ``(db-identity, schema-key)``:

  * production — one identity → the CREATE runs exactly once (fast path kept,
    no per-request DDL roundtrip to Supabase);
  * tests — each DB target gets its own entry → the CREATE runs once per DB,
    so the table always exists where the test actually reads it.

``_db_identity()`` mirrors ``connect()``'s own sqlite-path resolution and
imports ``get_data_path`` at call-time, so monkeypatched data dirs are
honoured exactly as ``connect()`` honours them.
"""
from __future__ import annotations

import os

# (db-identity, schema-key) pairs whose DDL has already run this process.
_ensured: set[tuple[str, str]] = set()


def _db_identity() -> str:
    """Stable string identifying the database ``connect()`` would open now."""
    from engine.db.connection import get_backend

    if get_backend() == "sqlite":
        # Mirror connect(): the default sqlite target is get_data_path("snowkap.db").
        # Import at call-time so a monkeypatched get_data_path (test fixtures
        # such as isolated_db) resolves to the same tmp path connect() uses.
        from engine.config import get_data_path

        return f"sqlite:{get_data_path('snowkap.db')}"
    return f"postgres:{os.environ.get('SUPABASE_DATABASE_URL', '')}"


def schema_ready(key: str) -> bool:
    """Return True if ``key``'s schema was already ensured for the active DB.

    Callers use this to short-circuit ``ensure_schema()``::

        def ensure_schema() -> None:
            if schema_ready("my_table"):
                return
            with connect() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS my_table (...)")
            mark_schema_ready("my_table")
    """
    return (_db_identity(), key) in _ensured


def mark_schema_ready(key: str) -> None:
    """Record that ``key``'s schema has been ensured for the active DB."""
    _ensured.add((_db_identity(), key))


def reset_schema_guard(key: str | None = None) -> None:
    """Forget ensured-state so the next ``ensure_schema()`` re-runs its DDL.

    ``key=None`` clears every entry (a blunt reset for tests that recreate
    the database). Otherwise only the given key (for the active DB) is
    cleared. Production code never needs this; it exists for test isolation
    and for modules (e.g. ``engine.chat.schema``) that expose a public reset.
    """
    if key is None:
        _ensured.clear()
        return
    _ensured.discard((_db_identity(), key))
