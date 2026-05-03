"""Database abstraction layer.

Phase 24 — supports both SQLite (legacy default) and Supabase Postgres.
Selects backend at import time via SNOWKAP_DB_BACKEND env var:

  SNOWKAP_DB_BACKEND=sqlite    (default, back-compat)
  SNOWKAP_DB_BACKEND=postgres  (uses SUPABASE_DATABASE_URL)

The public API is intentionally narrow:

  from engine.db import connect, upsert, get_backend

  with connect() as conn:
      rows = conn.execute(
          "SELECT * FROM article_index WHERE company_slug = ?",
          ("adani-power",),
      ).fetchall()

  with connect() as conn:
      upsert(
          conn,
          table="article_index",
          row={"id": "abc", "title": "..."},
          conflict_cols=["id"],
          update_cols=["title"],
      )

Files that previously did `sqlite3.connect(DB_PATH)` should switch to
`engine.db.connect()`. The rest of their SQL remains identical for the
common subset (SELECT, INSERT … ON CONFLICT, etc.); only the SQLite-only
functions (`datetime('now', ...)`, `PRAGMA journal_mode=WAL`,
`INSERT OR REPLACE`) are rewritten on the fly by the dialect translator.
"""
from __future__ import annotations

from engine.db.connection import (
    Connection,
    Cursor,
    Row,
    connect,
    get_backend,
    is_postgres,
    is_sqlite,
)
from engine.db.upsert import upsert

__all__ = [
    "Connection",
    "Cursor",
    "Row",
    "connect",
    "get_backend",
    "is_postgres",
    "is_sqlite",
    "upsert",
]
