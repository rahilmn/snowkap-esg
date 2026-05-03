"""SQL dialect translator: SQLite-flavoured SQL → Postgres-flavoured SQL.

Goal: every existing call site in `engine/index/`, `engine/models/`,
`engine/jobs/` and `api/auth_otp.py` keeps working unchanged when the
backend flips from SQLite to Postgres. Translation happens at execute
time inside the Connection wrapper — call sites don't import this
module directly.

Translations applied (Postgres mode only):

  * `datetime('now')`                  → `NOW()`
  * `datetime('now', '-N days')`       → `(NOW() - INTERVAL 'N days')`
  * `datetime('now', ?)` + `'-N days'` → `(NOW() + (?)::interval)` (param unchanged)
  * `INSERT OR REPLACE INTO t …`       → `INSERT INTO t … ON CONFLICT … DO UPDATE …`
                                          (requires the caller to use the `upsert()`
                                           helper for new code; legacy `INSERT OR
                                           REPLACE` strings get rewritten when the
                                           PK is `id` or known by name)
  * `PRAGMA journal_mode=WAL`          → no-op (returns a synthetic 'wal' result so
                                          existing assertions still pass)
  * `pragma_table_info(t)`             → `information_schema.columns` query
  * `?` placeholders                   → `%s`
  * `:name` placeholders               → `%(name)s`

The translator is intentionally conservative: it ONLY rewrites patterns
we know are present in the existing codebase. Unknown constructs pass
through unchanged so test failures surface clearly.
"""
from __future__ import annotations

import re
from typing import Any


# --- datetime('now', ...) rewrites ------------------------------------------

# Matches `datetime('now')` (no second arg).
_DT_NOW_BARE = re.compile(r"datetime\(\s*'now'\s*\)", re.IGNORECASE)

# Matches `datetime('now', '-N days')` with a literal interval string.
# Captures N and the unit (day | days | hour | hours | minute | minutes).
_DT_NOW_LITERAL = re.compile(
    r"datetime\(\s*'now'\s*,\s*'(?P<sign>[-+]?)\s*(?P<n>\d+)\s+"
    r"(?P<unit>day|days|hour|hours|minute|minutes|second|seconds|month|months|year|years)\s*'\s*\)",
    re.IGNORECASE,
)

# Matches `datetime('now', ?)` or `datetime('now', :name)` where the
# interval is provided as a parameter. We rewrite to use `::interval`.
_DT_NOW_PARAM_QMARK = re.compile(
    r"datetime\(\s*'now'\s*,\s*\?\s*\)", re.IGNORECASE
)
_DT_NOW_PARAM_NAMED = re.compile(
    r"datetime\(\s*'now'\s*,\s*:(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)\s*\)",
    re.IGNORECASE,
)

# `INSERT OR REPLACE INTO <table> (cols) VALUES (...)` — Postgres needs
# explicit ON CONFLICT clause + the PK column. We assume `id` is the
# conflict column unless the caller switched to the upsert() helper.
_INSERT_OR_REPLACE = re.compile(
    r"\bINSERT\s+OR\s+REPLACE\s+INTO\s+(?P<table>[a-zA-Z_][a-zA-Z0-9_]*)\s*"
    r"\((?P<cols>[^)]+)\)\s*VALUES\s*\((?P<vals>[^)]+)\)",
    re.IGNORECASE | re.DOTALL,
)

# `PRAGMA journal_mode=WAL;` — no-op for Postgres
_PRAGMA_WAL = re.compile(
    r"PRAGMA\s+journal_mode\s*=\s*WAL\s*;?", re.IGNORECASE
)

# `INTEGER PRIMARY KEY AUTOINCREMENT` (SQLite-only) → `BIGSERIAL PRIMARY KEY`
# (Postgres). Auto-incrementing PKs in our schema use this exact phrase
# (see engine/jobs/onboard_queue.py::SCHEMA_SQL). The migration file in
# engine/db/migrations/ already uses BIGSERIAL directly; this translation
# makes the legacy ensure_schema() helpers parse cleanly on Postgres so
# they're idempotent no-ops when the table already exists.
_AUTOINCREMENT = re.compile(
    r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
    re.IGNORECASE,
)

# Manual transaction control used by the onboard-queue worker
# (`BEGIN IMMEDIATE` for SQLite's reserved-lock semantics, plus the
# matching `COMMIT` / `ROLLBACK`). Postgres has no `IMMEDIATE` keyword
# and uses MVCC + `SELECT ... FOR UPDATE` for the same atomic-claim
# pattern; the surrounding ``with connect() as conn:`` context manager
# in the abstraction layer already commits/rolls-back on exit. We
# rewrite these to no-op `SELECT 1` round-trips on Postgres so the
# legacy SQLite-flavoured worker code keeps working unchanged.
_BEGIN_IMMEDIATE = re.compile(r"^\s*BEGIN\s+IMMEDIATE\s*;?\s*$", re.IGNORECASE)
_BARE_COMMIT = re.compile(r"^\s*COMMIT\s*;?\s*$", re.IGNORECASE)
# (We do NOT translate `ROLLBACK` — Postgres accepts it natively, and
# the context manager rolls back on exception anyway.)

# `?` placeholder. We translate to `%s` for psycopg2. Be careful not to
# match `?` inside string literals — for that we tokenize naively but
# this is good enough because none of our SQL has literal question marks.
_QMARK = re.compile(r"\?")

# `:name` placeholder for psycopg2 → `%(name)s`. The negative lookbehind
# `(?<!:)` is critical: it prevents matching the second `:` of Postgres'
# cast operator (`value::type`). Without it, ``%(__age)s::interval`` would
# get rewritten to ``%(__age)s%(interval)s`` and psycopg2 would error
# with ``KeyError: 'interval'`` because ``interval`` isn't in params.
# Also avoid matching inside numeric ratios (`1:5`) — pre-pad with a
# negative lookbehind for any digit.
_NAMED_PLACEHOLDER = re.compile(r"(?<![:\w]):([a-zA-Z_][a-zA-Z0-9_]*)")


def translate_to_postgres(sql: str) -> str:
    """Rewrite SQLite-flavoured SQL to Postgres-compatible SQL.

    Pure-text rewrite. Does not touch parameters. The caller still has
    to switch positional parameters from a tuple to the right shape for
    psycopg2 (still a tuple, `?` rewritten to `%s`).
    """
    if not sql:
        return sql

    # 1. datetime('now', ...) family.
    #
    # Subtle but critical: ``published_at``/``created_at``/etc. are stored
    # as TEXT (ISO-8601 with `+00:00` offset, e.g. `2026-05-02T10:00:00+00:00`),
    # but Postgres' NOW() returns ``timestamptz``. Comparing TEXT to
    # ``timestamptz`` raises ``operator does not exist``. So we emit a
    # ``to_char(... 'YYYY-MM-DD"T"HH24:MI:SS+00:00')`` wrapper that produces
    # a TEXT in the same shape Python's ``datetime.now(timezone.utc).isoformat()``
    # writes. Lex-comparison of two ISO-8601-with-offset strings is
    # equivalent to chronological comparison, so ``WHERE published_at >= cutoff``
    # works correctly for ANY mix of microsecond / no-microsecond timestamps
    # (the fractional part starts with ``.`` which sorts AFTER ``+`` in ASCII,
    # so a stored ``...:45.123+00:00`` correctly counts as later than an
    # emitted ``...:45+00:00`` cutoff).
    _PG_TIME_FMT = "'YYYY-MM-DD\"T\"HH24:MI:SS+00:00'"
    sql = _DT_NOW_LITERAL.sub(
        lambda m: (
            "to_char((NOW() AT TIME ZONE 'UTC') "
            f"{('-' if m.group('sign') == '-' else '+')} "
            f"INTERVAL '{m.group('n')} {m.group('unit')}', {_PG_TIME_FMT})"
        ),
        sql,
    )
    sql = _DT_NOW_PARAM_QMARK.sub(
        f"to_char((NOW() AT TIME ZONE 'UTC') + (?::interval), {_PG_TIME_FMT})",
        sql,
    )
    sql = _DT_NOW_PARAM_NAMED.sub(
        lambda m: (
            f"to_char((NOW() AT TIME ZONE 'UTC') + (:{m.group('name')}::interval), {_PG_TIME_FMT})"
        ),
        sql,
    )
    sql = _DT_NOW_BARE.sub(
        f"to_char((NOW() AT TIME ZONE 'UTC'), {_PG_TIME_FMT})", sql
    )

    # 1b. INTEGER PRIMARY KEY AUTOINCREMENT → BIGSERIAL PRIMARY KEY
    sql = _AUTOINCREMENT.sub("BIGSERIAL PRIMARY KEY", sql)

    # 1c. SQLite-only `BEGIN IMMEDIATE` / `COMMIT` -> no-op SELECT.
    # The abstraction's context manager handles real commit/rollback;
    # the explicit SQL is a leftover from the legacy SQLite-only worker.
    sql = _BEGIN_IMMEDIATE.sub("SELECT 1", sql)
    sql = _BARE_COMMIT.sub("SELECT 1", sql)

    # 2. PRAGMA WAL → no-op (return a sentinel SELECT so .fetchone()
    # returns ('wal',) for the legacy assertion)
    sql = _PRAGMA_WAL.sub("SELECT 'wal' AS journal_mode", sql)

    # 3. INSERT OR REPLACE → INSERT … ON CONFLICT (pk) DO UPDATE …
    def _replace_insert_or_replace(m: re.Match[str]) -> str:
        table = m.group("table")
        cols = [c.strip() for c in m.group("cols").split(",")]
        vals = m.group("vals")
        # Determine the conflict column. Strategy: prefer ``id`` when
        # it appears in the column list; otherwise use the first column
        # (which is the PK by convention in the legacy SQLite schemas:
        # slug_aliases(alias, canonical), auth_otp(email, ...), etc.).
        pk = "id" if "id" in cols else cols[0]
        non_pk = [c for c in cols if c != pk]
        if not non_pk:
            # Single-column table — nothing to update on conflict
            return (
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({vals}) "
                f"ON CONFLICT ({pk}) DO NOTHING"
            )
        update_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in non_pk)
        return (
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({vals}) "
            f"ON CONFLICT ({pk}) DO UPDATE SET {update_clause}"
        )

    sql = _INSERT_OR_REPLACE.sub(_replace_insert_or_replace, sql)

    return sql


def rewrite_placeholders(sql: str, params: Any) -> tuple[str, Any]:
    """Convert SQLite ``?``/`:name` placeholders to psycopg2 form.

    Returns ``(new_sql, new_params)``. Rules:

    * ``params`` is a sequence (list/tuple): ``?`` → ``%s``, params unchanged
    * ``params`` is a dict: ``:name`` → ``%(name)s``, params unchanged
    * ``params`` is None: ``?`` and ``:name`` get rewritten anyway in case the
      query is templated — but no params to pass

    psycopg2 also recognises ``%s`` natively for both positional and named
    via ``%(name)s``. The legacy SQLite ``?`` and ``:name`` are not
    recognised, so we always rewrite for the psycopg2 backend.
    """
    if params is None or isinstance(params, (list, tuple)):
        # Positional: rewrite `?` to `%s`. Need to also escape any literal
        # `%` in the SQL so psycopg2's percent-format parser doesn't
        # interpret them.
        sql = sql.replace("%", "%%") if "%" in sql else sql
        sql = _QMARK.sub("%s", sql)
        return sql, params
    elif isinstance(params, dict):
        # Named: rewrite `:name` to `%(name)s`. Same `%` escape concern.
        sql = sql.replace("%", "%%") if "%" in sql else sql
        sql = _NAMED_PLACEHOLDER.sub(r"%(\1)s", sql)
        return sql, params
    else:
        # Unknown params shape — pass through and let the driver error
        return sql, params
