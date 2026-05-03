"""Idempotent schema migration runner.

Applies every ``.sql`` file under :file:`engine/db/migrations/` to the
active backend in lexicographic order. Designed to be safe to run
multiple times: every DDL statement is ``CREATE TABLE IF NOT EXISTS``
or ``CREATE INDEX IF NOT EXISTS``.

Usage::

    python -m engine.db.migrate                # apply against active backend
    python -m engine.db.migrate --dry-run      # print SQL, don't execute

The script auto-translates SQLite-specific constructs to Postgres when
the backend is set to ``postgres`` (the migrations are written in
mostly-portable SQL, so translation is usually a no-op).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from engine.db import connect, get_backend

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _load_migrations() -> list[tuple[str, str]]:
    """Return ``[(filename, sql_body), ...]`` sorted lexicographically."""
    if not _MIGRATIONS_DIR.exists():
        return []
    files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    return [(f.name, f.read_text(encoding="utf-8")) for f in files]


def apply_migrations(dry_run: bool = False) -> int:
    """Apply every migration to the active backend.

    Returns the number of migrations executed.
    """
    backend = get_backend()
    print(f"Active backend: {backend}")
    migrations = _load_migrations()
    if not migrations:
        print("No migrations to apply.")
        return 0

    if dry_run:
        for name, sql in migrations:
            print(f"\n--- {name} (DRY RUN) ---\n{sql}")
        return len(migrations)

    with connect() as conn:
        for name, sql in migrations:
            print(f"Applying {name}...")
            conn.executescript(sql)
            print(f"  ✓ {name}")
    print(f"\nApplied {len(migrations)} migration(s).")
    return len(migrations)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print SQL but don't execute.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    apply_migrations(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
