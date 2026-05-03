"""Backend-aware database connection wrapper.

Returns a :class:`Connection` object whose API matches the ``sqlite3``
``Connection`` closely enough that legacy call sites work unchanged
when the backend is flipped via the ``SNOWKAP_DB_BACKEND`` env var.

API mirrored from sqlite3:

* ``conn.execute(sql, params=None)`` → returns a :class:`Cursor`
* ``conn.executescript(sql)`` → splits on ``;`` and runs sequentially
* ``conn.commit()`` / ``conn.rollback()`` / ``conn.close()``
* ``conn.row_factory`` — ignored on Postgres (we always use
  ``RealDictCursor``); set explicitly on SQLite for back-compat

Cursors yield :class:`Row` objects supporting both index-style
(``row[0]``) and dict-style (``row['col']``) access. Calling
``dict(row)`` returns the column→value mapping. This matches both
SQLite's ``sqlite3.Row`` and psycopg2's ``RealDictRow`` semantics.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from engine.db.dialect import rewrite_placeholders, translate_to_postgres

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


def get_backend() -> str:
    """Return ``'sqlite'`` or ``'postgres'`` based on ``SNOWKAP_DB_BACKEND``.

    Default: ``'sqlite'`` (legacy back-compat). Setting the env var to
    ``'postgres'`` switches every call site that uses ``engine.db.connect()``
    to use Supabase (URL from ``SUPABASE_DATABASE_URL``).
    """
    backend = os.environ.get("SNOWKAP_DB_BACKEND", "sqlite").strip().lower()
    if backend not in ("sqlite", "postgres"):
        logger.warning(
            "Unknown SNOWKAP_DB_BACKEND=%r; falling back to sqlite", backend
        )
        return "sqlite"
    return backend


def is_postgres() -> bool:
    return get_backend() == "postgres"


def is_sqlite() -> bool:
    return get_backend() == "sqlite"


# ---------------------------------------------------------------------------
# Row wrapper — uniform access across backends
# ---------------------------------------------------------------------------


class Row:
    """Uniform row that supports `row[i]`, `row['col']`, and `dict(row)`.

    Wraps either a `sqlite3.Row` (already supports both) or a psycopg2
    ``RealDictRow`` / dict (needs an ordered keys list for index access).
    """

    __slots__ = ("_row", "_keys")

    def __init__(self, row: Any, keys: Sequence[str] | None = None) -> None:
        self._row = row
        self._keys = list(keys) if keys is not None else None

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            if isinstance(self._row, sqlite3.Row):
                return self._row[key]
            if self._keys is not None:
                return self._row[self._keys[key]]
            # dict-only with int key — try ordered keys()
            return list(self._row.values())[key]
        # str key
        return self._row[key]

    def keys(self) -> list[str]:
        if isinstance(self._row, sqlite3.Row):
            return list(self._row.keys())
        if self._keys is not None:
            return list(self._keys)
        return list(self._row.keys())

    def __iter__(self) -> Iterator[Any]:
        if isinstance(self._row, sqlite3.Row):
            for k in self._row.keys():
                yield self._row[k]
        elif self._keys is not None:
            for k in self._keys:
                yield self._row[k]
        else:
            yield from self._row.values()

    def __len__(self) -> int:
        return len(self.keys())

    # dict(row) calls keys() then __getitem__(key) — both supported
    def __repr__(self) -> str:
        return f"Row({dict(self)!r})"


# ---------------------------------------------------------------------------
# Cursor wrapper
# ---------------------------------------------------------------------------


class Cursor:
    """Thin wrapper over the underlying driver's cursor.

    Translates SQLite-style SQL/params to the active backend on every
    ``execute()`` call and wraps fetched rows in :class:`Row` for uniform
    access.
    """

    def __init__(self, cur: Any, backend: str) -> None:
        self._cur = cur
        self._backend = backend
        self._last_keys: list[str] | None = None

    def execute(self, sql: str, params: Any = None) -> "Cursor":
        if self._backend == "postgres":
            sql = translate_to_postgres(sql)
            sql, params = rewrite_placeholders(sql, params)
        if params is None:
            self._cur.execute(sql)
        else:
            self._cur.execute(sql, params)
        # Capture column names for Row materialisation. psycopg2's
        # description is None for non-SELECT statements; that's fine.
        if self._cur.description:
            self._last_keys = [d[0] for d in self._cur.description]
        else:
            self._last_keys = None
        return self

    def executemany(self, sql: str, seq_of_params: Any) -> "Cursor":
        if self._backend == "postgres":
            sql = translate_to_postgres(sql)
            sql, _ = rewrite_placeholders(sql, [None] if seq_of_params else None)
        self._cur.executemany(sql, seq_of_params)
        return self

    def fetchone(self) -> Row | None:
        row = self._cur.fetchone()
        if row is None:
            return None
        return Row(row, self._last_keys)

    def fetchall(self) -> list[Row]:
        return [Row(r, self._last_keys) for r in self._cur.fetchall()]

    def fetchmany(self, size: int = 1) -> list[Row]:
        return [Row(r, self._last_keys) for r in self._cur.fetchmany(size)]

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def lastrowid(self) -> Any:
        # psycopg2 doesn't expose lastrowid for a generic INSERT; the
        # caller should use RETURNING. SQLite path delegates.
        if self._backend == "sqlite":
            return self._cur.lastrowid
        return None

    def close(self) -> None:
        self._cur.close()


# ---------------------------------------------------------------------------
# Connection wrapper
# ---------------------------------------------------------------------------


class Connection:
    """Backend-aware connection. Mimics sqlite3.Connection's hot-path API."""

    def __init__(self, raw_conn: Any, backend: str) -> None:
        self._conn = raw_conn
        self._backend = backend
        # Default cursor for `conn.execute(...)` shorthand. Mirrors
        # sqlite3 semantics where conn.execute() implicitly creates a
        # transient cursor.
        self.row_factory: Any = None  # ignored on postgres

    @property
    def backend(self) -> str:
        return self._backend

    def cursor(self) -> Cursor:
        if self._backend == "sqlite":
            return Cursor(self._conn.cursor(), self._backend)
        # psycopg2: import lazily so SQLite-only deploys don't need it
        import psycopg2.extras  # type: ignore[import-not-found]

        return Cursor(
            self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor),
            self._backend,
        )

    def execute(self, sql: str, params: Any = None) -> Cursor:
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def executescript(self, sql: str) -> None:
        """Run a multi-statement SQL script.

        Splits on ``;`` boundaries — naive but works for the simple DDL
        we use. SQLite's ``executescript`` runs each statement in
        autocommit mode; we replicate that.
        """
        if self._backend == "sqlite":
            self._conn.executescript(sql)
            return
        # Postgres: psycopg2 doesn't have executescript. We split and
        # execute one at a time. Empty statements are skipped.
        cur = self.cursor()
        # Strip line comments first to avoid `;` mistakes inside them
        cleaned = "\n".join(
            line for line in sql.splitlines() if not line.strip().startswith("--")
        )
        for stmt in cleaned.split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            cur.execute(stmt)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Public connect() context manager
# ---------------------------------------------------------------------------


@contextmanager
def connect(*, sqlite_path: Path | str | None = None) -> Iterator[Connection]:
    """Yield a :class:`Connection` to whichever backend is active.

    On commit, exception, or context exit, the connection is committed
    (success) or rolled back (exception) and then closed. Mirrors
    sqlite3's `with` behaviour.

    ``sqlite_path``: path to the SQLite database file when the active
    backend is sqlite. Default: ``data/snowkap.db`` via ``get_data_path``.
    Ignored on postgres.
    """
    backend = get_backend()
    raw: Any
    if backend == "sqlite":
        from engine.config import get_data_path

        path = Path(sqlite_path) if sqlite_path else get_data_path("snowkap.db")
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(path)
        raw.row_factory = sqlite3.Row
    else:
        url = os.environ.get("SUPABASE_DATABASE_URL", "").strip()
        if not url:
            raise RuntimeError(
                "SNOWKAP_DB_BACKEND=postgres but SUPABASE_DATABASE_URL is empty. "
                "Set it in .env or export it before launching the API."
            )
        import psycopg2  # type: ignore[import-not-found]

        raw = psycopg2.connect(url, connect_timeout=15)
    conn = Connection(raw, backend)
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
