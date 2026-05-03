"""Retention helpers — delete article rows and their JSON files older than N days.

Phase 24 — supports both the legacy SQLite article_index and the
post-migration Postgres article_index transparently via the engine.db
abstraction. The JSON-file deletion is backend-agnostic; only the SQL
DELETE differs by backend.

Usage::

    from engine.maintenance.retention import purge_articles_older_than

    summary = purge_articles_older_than(days=7, dry_run=False)
    # summary = {
    #   "rows_deleted": 142,
    #   "files_deleted": 142,
    #   "files_missing": 3,
    #   "by_company": {"icici-bank": 12, ...},
    # }

The 7-day window matches the user-facing freshness gate
(``SNOWKAP_FEED_MAX_AGE_DAYS``) so anything purged is already invisible
in the UI. Articles are dropped from BOTH the index AND disk —
recovering one would require re-fetching from NewsAPI.ai. Run this
once per refresh cycle (24h).

CLI::

    python -m engine.maintenance.retention --days 7
    python -m engine.maintenance.retention --days 7 --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from engine.config import get_data_path
from engine.db import connect, get_backend

logger = logging.getLogger(__name__)


def _outputs_root() -> Path:
    """Root path for per-company JSON outputs (data/outputs/)."""
    return get_data_path("outputs")


def _delete_json_file(rel_path: str | None) -> str:
    """Delete the JSON insight file at ``rel_path`` (relative to data/).

    Returns ``"deleted"`` | ``"missing"`` | ``"skipped"``.
    """
    if not rel_path:
        return "skipped"
    abs_path = get_data_path().parent / rel_path
    if not abs_path.exists():
        return "missing"
    try:
        abs_path.unlink()
        return "deleted"
    except Exception as exc:  # noqa: BLE001
        logger.warning("retention: could not delete %s: %s", abs_path, exc)
        return "skipped"


def purge_articles_older_than(
    days: int = 7,
    *,
    dry_run: bool = False,
    delete_json: bool = True,
) -> dict[str, Any]:
    """Drop article_index rows whose ``published_at`` is older than ``days``.

    * ``dry_run`` – count what would be deleted but don't write
    * ``delete_json`` – also unlink the JSON insight file pointed at
      by ``json_path`` for each row. Default ON.

    Returns a structured summary suitable for logging.
    """
    if days <= 0:
        raise ValueError(f"retention: days must be > 0 (got {days})")

    cutoff_iso = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).isoformat()
    summary: dict[str, Any] = {
        "backend": get_backend(),
        "days": days,
        "cutoff": cutoff_iso,
        "dry_run": dry_run,
        "rows_deleted": 0,
        "files_deleted": 0,
        "files_missing": 0,
        "files_skipped": 0,
        "by_company": {},
    }

    # 1. Find candidate rows (read-only) — works on both backends.
    # We use the cutoff timestamp directly as a parameter so the same
    # SQL works on SQLite (TEXT comparison) and Postgres (TEXT comparison).
    # `published_at` was stored ISO-8601 by the writer; lex-compare is
    # equivalent to chronological compare for ISO-8601 timestamps.
    select_sql = (
        "SELECT id, company_slug, json_path, published_at "
        "FROM article_index "
        "WHERE COALESCE(published_at, '') != '' "
        "  AND published_at < ? "
        "ORDER BY published_at"
    )
    candidates: list[dict[str, Any]] = []
    with connect() as conn:
        for row in conn.execute(select_sql, (cutoff_iso,)).fetchall():
            candidates.append(
                {
                    "id": row["id"],
                    "company_slug": row["company_slug"],
                    "json_path": row["json_path"],
                    "published_at": row["published_at"],
                }
            )

    summary["candidates"] = len(candidates)
    if not candidates:
        logger.info("retention: 0 articles older than %d days", days)
        return summary

    if dry_run:
        for c in candidates:
            slug = c["company_slug"] or "(unknown)"
            summary["by_company"][slug] = summary["by_company"].get(slug, 0) + 1
        logger.info(
            "retention: DRY RUN — would delete %d row(s) older than %d days",
            len(candidates),
            days,
        )
        return summary

    # 2. Delete rows + JSON files.
    delete_sql = "DELETE FROM article_index WHERE id = ?"
    with connect() as conn:
        for c in candidates:
            conn.execute(delete_sql, (c["id"],))
            summary["rows_deleted"] += 1
            slug = c["company_slug"] or "(unknown)"
            summary["by_company"][slug] = summary["by_company"].get(slug, 0) + 1
            if delete_json:
                outcome = _delete_json_file(c["json_path"])
                if outcome == "deleted":
                    summary["files_deleted"] += 1
                elif outcome == "missing":
                    summary["files_missing"] += 1
                else:
                    summary["files_skipped"] += 1

    logger.info(
        "retention: deleted %d row(s) (%d files removed, %d missing) older than %d days",
        summary["rows_deleted"],
        summary["files_deleted"],
        summary["files_missing"],
        days,
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, help="Drop articles older than N days (default 7).")
    parser.add_argument("--dry-run", action="store_true", help="Count only; don't delete.")
    parser.add_argument("--keep-json", action="store_true", help="Drop index rows but keep JSON files.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = purge_articles_older_than(
        days=args.days,
        dry_run=args.dry_run,
        delete_json=not args.keep_json,
    )
    print()
    print(f"Backend       : {summary['backend']}")
    print(f"Cutoff        : {summary['cutoff']}")
    print(f"Candidates    : {summary['candidates']}")
    print(f"Rows deleted  : {summary['rows_deleted']}")
    print(f"Files deleted : {summary['files_deleted']}")
    print(f"Files missing : {summary['files_missing']}")
    print(f"By company    : {summary['by_company']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
