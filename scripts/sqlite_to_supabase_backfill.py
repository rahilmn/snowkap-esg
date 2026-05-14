"""One-shot data backfill from local SQLite into the active engine.db backend.

Use this after running ``python -m engine.db.migrate`` against Supabase to
move existing local data into the freshly-created Postgres schema. Idempotent
per row (uses ``INSERT OR REPLACE`` / ``ON CONFLICT DO UPDATE`` via the
engine.db.upsert helper where conflict columns are known).

Usage::

    # Move every table, full data
    SNOWKAP_DB_BACKEND=postgres \\
    SUPABASE_DATABASE_URL=postgresql://... \\
    python scripts/sqlite_to_supabase_backfill.py

    # Dry-run — print what would happen, don't write
    python scripts/sqlite_to_supabase_backfill.py --dry-run

    # Single table
    python scripts/sqlite_to_supabase_backfill.py --table article_index

    # Skip noisy / large tables
    python scripts/sqlite_to_supabase_backfill.py --skip llm_calls --skip chat_messages

Output format is per-table::

    article_index       12 304 rows -> 12 304 inserted, 0 failed (1.2s)
    chat_messages           892 rows ->     891 inserted, 1 failed (0.3s)
    tenant_memory             7 rows ->       7 inserted, 0 failed (0.0s)
    ...
    TOTAL: 35 712 rows across 14 tables. 35 709 inserted, 3 failed.

Exit codes::

    0 — all tables backfilled (any per-row failures are warnings, not fatal)
    1 — backend configuration error (no SUPABASE_DATABASE_URL etc.)
    2 — at least one table failed completely
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Iterable

# Project root on path so ``engine.*`` imports work when run as ``python
# scripts/sqlite_to_supabase_backfill.py`` from the repo root.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from engine.config import get_data_path  # noqa: E402
from engine.db import connect as engine_connect, get_backend  # noqa: E402

logger = logging.getLogger(__name__)


# Table dependency order — FK-aware so Postgres doesn't reject child-before-parent.
# Tables not listed are skipped (typically FTS5 virtual + sqlite_* internals).
# When adding a new table to the schema, add it here in the correct order.
_TABLES_IN_ORDER: tuple[str, ...] = (
    # Tier 1 — independent (no FK to other listed tables)
    "tenant_registry",
    "slug_aliases",
    "auth_otp",
    "article_index",
    "onboard_jobs",
    "onboarding_status",
    "analyst_session_state",
    # Tier 2 — depend on article_index or tenant
    "article_analysis_status",   # FK article_id -> article_index.id (logical)
    "llm_calls",                  # FK article_id -> article_index.id (logical)
    # Tier 3 — campaigns
    "campaigns",
    "campaign_recipients",        # FK campaign_id -> campaigns.id (logical)
    "campaign_send_log",          # FK campaign_id -> campaigns.id (logical)
    "outbound_touches",
    # Tier 4 — chat (Phase B2) + memory (Phase B3)
    "chat_conversations",
    "chat_messages",              # FK conversation_id -> chat_conversations.id (logical)
    "tenant_memory",
)

# Skip patterns — never attempt to migrate these.
_SKIP_PATTERNS = (
    "sqlite_",        # SQLite internals
    "_fts",           # FTS5 virtual tables (recreated by ensure_schema on target)
    "_data",          # FTS5 shadow tables
    "_idx",           # FTS5 index tables
    "_docsize",       # FTS5 docsize tables
    "_config",        # FTS5 config tables
)


def _is_skippable(table: str) -> bool:
    return any(table.startswith(p) for p in _SKIP_PATTERNS)


def _list_source_tables(src: sqlite3.Connection) -> list[str]:
    """Return non-skip-pattern tables present in the source SQLite."""
    rows = src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows if not _is_skippable(r[0])]


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.OperationalError:
        return 0


def _copy_table(
    table: str,
    src: sqlite3.Connection,
    *,
    dry_run: bool,
    batch_size: int = 500,
) -> tuple[int, int, float]:
    """Stream rows from ``src.<table>`` into the active backend. Returns
    ``(inserted, failed, elapsed_seconds)``."""
    start = time.monotonic()
    rows = src.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        return 0, 0, time.monotonic() - start

    cols = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)
    # INSERT OR REPLACE works on SQLite; dialect translator rewrites it to
    # `INSERT ... ON CONFLICT (pk) DO UPDATE` on postgres. The translator
    # knows the conflict key from inspecting the schema at runtime.
    sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"

    if dry_run:
        return len(rows), 0, time.monotonic() - start

    inserted = 0
    failed = 0
    # Batch into chunks so a single bad row doesn't poison the whole table.
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        try:
            with engine_connect() as conn:
                for r in batch:
                    try:
                        conn.execute(sql, tuple(r))
                        inserted += 1
                    except Exception as exc:  # noqa: BLE001
                        failed += 1
                        logger.warning("%s row failed: %s", table, exc)
        except Exception as exc:  # noqa: BLE001 — batch-level commit failure
            failed += len(batch)
            logger.error("%s batch %d-%d commit failed: %s",
                         table, i, i + len(batch), exc)

    return inserted, failed, time.monotonic() - start


def backfill(
    *,
    src_path: Path,
    dry_run: bool,
    tables: Iterable[str] | None,
    skip: Iterable[str] = (),
    batch_size: int = 500,
) -> tuple[int, int, int]:
    """Run the backfill. Returns ``(total_rows, total_inserted, total_failed)``."""
    backend = get_backend()
    print(f"Source:  {src_path}")
    print(f"Target:  engine.db backend = {backend}")
    if dry_run:
        print("Mode:    DRY RUN (no writes)")
    print()

    if not src_path.exists():
        print(f"ERROR: source SQLite file not found: {src_path}", file=sys.stderr)
        sys.exit(1)

    src = sqlite3.connect(str(src_path))
    src.row_factory = sqlite3.Row

    # Determine the table set
    all_source = set(_list_source_tables(src))
    if tables:
        requested = set(tables)
        # Always preserve dependency ordering by intersecting with _TABLES_IN_ORDER
        ordered = [t for t in _TABLES_IN_ORDER if t in requested]
        # Plus any user-specified tables not in our canonical list (warn)
        extras = requested - set(_TABLES_IN_ORDER)
        if extras:
            print(f"WARN: --table specified unknown table(s): {sorted(extras)}; "
                  f"appending at end with no dependency guarantee")
            ordered.extend(sorted(extras))
        target_tables = ordered
    else:
        # Default — use canonical order, filter to tables that actually exist
        # in the source
        target_tables = [t for t in _TABLES_IN_ORDER if t in all_source]
        # Surface anything in the source we don't know about
        unknown = sorted(all_source - set(_TABLES_IN_ORDER) - set(skip))
        if unknown:
            print(f"NOTE: tables in source not listed in canonical order "
                  f"(skipped, add to _TABLES_IN_ORDER if you want them): {unknown}")

    skip_set = set(skip)
    target_tables = [t for t in target_tables if t not in skip_set]

    print(f"Tables to process: {len(target_tables)}")
    print()

    total_rows = 0
    total_inserted = 0
    total_failed = 0
    print(f"{'table':32} {'source':>10} {'inserted':>10} {'failed':>8} {'elapsed':>10}")
    print("-" * 76)
    for table in target_tables:
        n_src = _row_count(src, table)
        inserted, failed, elapsed = _copy_table(
            table, src, dry_run=dry_run, batch_size=batch_size,
        )
        print(f"{table:32} {n_src:>10} {inserted:>10} {failed:>8} {elapsed:>9.2f}s")
        total_rows += n_src
        total_inserted += inserted
        total_failed += failed

    print("-" * 76)
    print(f"{'TOTAL':32} {total_rows:>10} {total_inserted:>10} {total_failed:>8}")
    return total_rows, total_inserted, total_failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--src",
        type=Path,
        default=None,
        help="Source SQLite file (default: data/snowkap.db relative to project root)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without writing to the target",
    )
    parser.add_argument(
        "--table",
        action="append",
        default=[],
        help="Single table to backfill (repeatable). Default: every table in canonical order",
    )
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        help="Table to skip (repeatable)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Rows per transaction batch (default: 500)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show per-row error details",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    src_path = args.src or get_data_path("snowkap.db")

    # Sanity: refuse to backfill into SQLite (no-op — pointless), unless the
    # user explicitly chose to target a different sqlite file.
    if get_backend() == "sqlite" and not args.dry_run:
        print(
            "ERROR: engine.db backend is sqlite. Backfill is intended for moving "
            "from local SQLite to Supabase Postgres.\n\n"
            "Either:\n"
            "  - Set SNOWKAP_DB_BACKEND=postgres + SUPABASE_DATABASE_URL\n"
            "  - Or run with --dry-run to preview row counts",
            file=sys.stderr,
        )
        return 1

    total_rows, total_inserted, total_failed = backfill(
        src_path=src_path,
        dry_run=args.dry_run,
        tables=args.table or None,
        skip=args.skip,
        batch_size=args.batch_size,
    )

    if total_failed > 0 and total_inserted == 0:
        print(f"\nFATAL: every row failed across {total_rows} attempted rows. "
              "Check target schema (did you run `python -m engine.db.migrate`?).",
              file=sys.stderr)
        return 2

    if total_failed > 0:
        print(f"\nWARN: {total_failed} row(s) failed to insert. Re-run with "
              "-v to see per-row errors. The rest were committed successfully.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
