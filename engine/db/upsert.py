"""Cross-backend upsert helper.

Builds the right SQL for both SQLite and Postgres given a dict-shaped
row, the conflict columns (PK or unique index), and the columns to
update on conflict.

Both engines support ``INSERT ... ON CONFLICT (cols) DO UPDATE SET
col = EXCLUDED.col`` since SQLite 3.24 (2018) and Postgres 9.5 (2016),
so the SQL we emit is identical apart from a small case-sensitivity
quirk: SQLite uses ``excluded`` lowercase, Postgres also accepts
``excluded`` (case-insensitive identifier). We emit ``EXCLUDED``
uppercase which is portable across both.

Usage::

    from engine.db import connect, upsert

    with connect() as conn:
        upsert(
            conn,
            table="article_index",
            row={"id": "abc", "title": "...", "company_slug": "..."},
            conflict_cols=["id"],
            update_cols=["title", "company_slug"],   # which cols to update on conflict
        )
"""
from __future__ import annotations

from typing import Any, Sequence

from engine.db.connection import Connection


def upsert(
    conn: Connection,
    *,
    table: str,
    row: dict[str, Any],
    conflict_cols: Sequence[str],
    update_cols: Sequence[str] | None = None,
) -> None:
    """Insert or update a single row by primary/unique key.

    * ``row`` – column→value dict; the order is preserved in the INSERT.
    * ``conflict_cols`` – columns the unique constraint is on (PK or
      a UNIQUE INDEX). At least one is required.
    * ``update_cols`` – columns to update when there is a conflict.
      Defaults to every column in ``row`` that is NOT a conflict column.
    """
    if not row:
        raise ValueError("upsert: row is empty")
    if not conflict_cols:
        raise ValueError("upsert: conflict_cols required")

    cols = list(row.keys())
    placeholders = ", ".join(f":{c}" for c in cols)

    if update_cols is None:
        update_cols = [c for c in cols if c not in conflict_cols]

    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(conflict_cols)}) "
    )
    if update_cols:
        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        sql += f"DO UPDATE SET {set_clause}"
    else:
        sql += "DO NOTHING"
    conn.execute(sql, row)
