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
            _apply_migration_resilient(conn, name, sql)
            print(f"  OK {name}")
    print(f"\nApplied {len(migrations)} migration(s).")
    return len(migrations)


def _split_statements(sql: str) -> list[str]:
    """Split a migration SQL body into individual statements.

    Strips line comments (``-- …``) FIRST so a leading comment block
    above a real statement doesn't cause the whole chunk to be
    discarded by a naive ``startswith("--")`` filter on the split
    output. That bug silently dropped the first ALTER in migration
    004 on Postgres — only the second one applied even though the
    runner reported "OK".

    Returns a list of non-empty trimmed statements (no trailing ``;``).
    """
    import re
    # 1. Strip line comments line-by-line. Block comments (/* … */) are
    #    not currently used in our migrations; if they appear in future
    #    we can extend this.
    cleaned_lines: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        # Also strip any inline `-- …` comment from the tail of the line.
        # Careful: don't eat `--` inside a string literal — none of our
        # current migrations use string literals with `--`, but if that
        # changes this becomes a real parser problem.
        if "--" in line:
            line = line.split("--", 1)[0]
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)
    # 2. Now split on `;`. Trailing empties + whitespace-only chunks dropped.
    return [s.strip() for s in cleaned.split(";") if s.strip()]


def _apply_migration_resilient(conn, name: str, sql: str) -> None:
    """Apply a migration SQL script statement-by-statement so a single
    already-applied statement (e.g. ALTER TABLE ADD COLUMN on SQLite
    where the column exists) doesn't abort the whole run.

    Errors classified as "harmless re-apply" are logged + skipped:
      - "duplicate column" (SQLite when ADD COLUMN already done)
      - "already exists" (some Postgres responses)
      - SQLite parse errors on Postgres-only constructs like
        ``ADD COLUMN IF NOT EXISTS`` (rewritten to plain ADD COLUMN
        retry).
    Every other error re-raises so real schema bugs stay visible
    instead of being swallowed and reported as success.
    """
    import re
    stmts = _split_statements(sql)
    if not stmts:
        # Empty migration (comments only) — log and move on. Not an
        # error but worth surfacing because it usually indicates a
        # mis-written file.
        print(f"    (no executable statements in {name})")
        return
    print(f"    {len(stmts)} statement(s) to apply")
    for idx, stmt in enumerate(stmts, start=1):
        try:
            conn.executescript(stmt + ";")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            # SQLite rejects "ADD COLUMN IF NOT EXISTS" syntax; retry
            # without the IF NOT EXISTS clause and treat duplicate-column
            # as already-applied.
            if "near \"exists\"" in msg or "syntax error" in msg:
                fallback = re.sub(
                    r"ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+",
                    "ADD COLUMN ",
                    stmt,
                    flags=re.IGNORECASE,
                )
                if fallback != stmt:
                    try:
                        conn.executescript(fallback + ";")
                        continue
                    except Exception as exc2:  # noqa: BLE001
                        if "duplicate column" in str(exc2).lower():
                            print(f"    [{idx}/{len(stmts)}] (already applied: ADD COLUMN — skipped)")
                            continue
                        raise
            if "duplicate column" in msg or "already exists" in msg:
                print(f"    [{idx}/{len(stmts)}] (already applied — skipped)")
                continue
            # Real failure — re-raise so the runner aborts on a true
            # schema bug instead of silently reporting "OK".
            raise


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
