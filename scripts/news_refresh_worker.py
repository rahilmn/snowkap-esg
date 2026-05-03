#!/usr/bin/env python
"""24h news refresh + retention worker.

Phase 24 — runs in its own Replit workflow (or local cron) and
performs one full refresh cycle every 24 hours:

    1. Retention pass — drop article_index rows + JSON insight files
       older than ``SNOWKAP_RETENTION_DAYS`` (default 7).
    2. Ingest + analyse for every tenant in the registry — calls
       ``cmd_ingest`` with ``--all`` so the same code path the in-process
       scheduler uses also drives the 24h pass. NewsAPI.ai is the
       primary source; Google News RSS is the fallback (Phase 24
       orchestrator change).

The interval is configurable via ``SNOWKAP_REFRESH_INTERVAL_HOURS``
(default 24). Set to a small number for development.

Graceful shutdown on SIGINT / SIGTERM: the current cycle finishes,
then the loop exits cleanly.

Tunables (env, all optional):

    SNOWKAP_REFRESH_INTERVAL_HOURS   default 24
    SNOWKAP_RETENTION_DAYS           default 7
    SNOWKAP_REFRESH_MAX_PER_QUERY    default 10  (per ingest call)
    SNOWKAP_REFRESH_LIMIT            default 5   (per ingest call)
    SNOWKAP_LOG_LEVEL                default INFO

Usage:

    python scripts/news_refresh_worker.py            # daemon: forever, 24h cadence
    python scripts/news_refresh_worker.py --once     # one cycle, then exit
    python scripts/news_refresh_worker.py --skip-retention   # just the ingest

Replit workflow definition (add to .replit run sequence):

    [[workflows.workflow]]
    name = "News Refresh"
    [[workflows.workflow.tasks]]
    task = "shell.exec"
    args = "python scripts/news_refresh_worker.py"
    waitForPort = false
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make sure the project root is importable when this script is invoked
# directly (e.g. ``python scripts/news_refresh_worker.py``).
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.maintenance.retention import purge_articles_older_than  # noqa: E402

logging.basicConfig(
    level=os.environ.get("SNOWKAP_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("news_refresh_worker")


REFRESH_INTERVAL_HOURS = float(
    os.environ.get("SNOWKAP_REFRESH_INTERVAL_HOURS", "24")
)
RETENTION_DAYS = int(os.environ.get("SNOWKAP_RETENTION_DAYS", "7"))
MAX_PER_QUERY = int(os.environ.get("SNOWKAP_REFRESH_MAX_PER_QUERY", "10"))
PER_RUN_LIMIT = int(os.environ.get("SNOWKAP_REFRESH_LIMIT", "5"))


_SHOULD_EXIT = False


def _request_shutdown(signum: int, _frame: object) -> None:
    global _SHOULD_EXIT
    logger.info("received signal %s — finishing current cycle and exiting", signum)
    _SHOULD_EXIT = True


for _sig in (signal.SIGINT, signal.SIGTERM):
    try:
        signal.signal(_sig, _request_shutdown)
    except (ValueError, OSError):
        # ValueError on non-main thread, OSError on Windows for SIGTERM
        pass


# ---------------------------------------------------------------------------
# Cycle
# ---------------------------------------------------------------------------


class _IngestArgs:
    """Plain namespace matching what cmd_ingest expects."""

    def __init__(self, max_per_query: int, limit: int) -> None:
        self.all = True
        self.company = None
        self.max = max_per_query
        self.limit = limit


def _run_retention(days: int) -> dict:
    """Drop articles older than ``days``. Logs structured summary."""
    if days <= 0:
        logger.info("retention: SNOWKAP_RETENTION_DAYS<=0 — skipping")
        return {"rows_deleted": 0, "files_deleted": 0}
    try:
        return purge_articles_older_than(days=days, dry_run=False, delete_json=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("retention: failed: %s", exc)
        return {"error": str(exc), "rows_deleted": 0, "files_deleted": 0}


def _run_ingest(max_per_query: int, limit: int) -> None:
    """Run one ingest+analyse pass across every tenant."""
    # Lazy-import so a `--once --skip-retention` flow that only wants
    # the retention pass doesn't pay the multi-second ontology load cost.
    from engine.main import cmd_ingest  # noqa: E402

    cmd_ingest(_IngestArgs(max_per_query=max_per_query, limit=limit))


def run_cycle(
    *,
    skip_retention: bool = False,
    skip_ingest: bool = False,
    retention_days: int = RETENTION_DAYS,
    max_per_query: int = MAX_PER_QUERY,
    limit: int = PER_RUN_LIMIT,
) -> dict:
    """Run one full refresh cycle. Returns a structured summary."""
    start = datetime.now(timezone.utc)
    logger.info("=== refresh cycle start at %s ===", start.isoformat())

    summary: dict = {
        "started_at": start.isoformat(),
        "retention": None,
        "ingest_status": "skipped" if skip_ingest else "ok",
    }

    if not skip_retention:
        logger.info("step 1/2: retention (drop articles older than %d days)", retention_days)
        summary["retention"] = _run_retention(retention_days)
    else:
        logger.info("step 1/2: retention SKIPPED (--skip-retention)")

    if not skip_ingest:
        logger.info(
            "step 2/2: ingest (max_per_query=%d, limit=%d, all tenants)",
            max_per_query,
            limit,
        )
        try:
            _run_ingest(max_per_query=max_per_query, limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.exception("ingest: failed: %s", exc)
            summary["ingest_status"] = f"failed: {exc}"
    else:
        logger.info("step 2/2: ingest SKIPPED (--skip-ingest)")

    finished = datetime.now(timezone.utc)
    summary["finished_at"] = finished.isoformat()
    summary["elapsed_seconds"] = round((finished - start).total_seconds(), 1)
    logger.info("=== refresh cycle done in %.1fs ===", summary["elapsed_seconds"])
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle then exit (default: loop every SNOWKAP_REFRESH_INTERVAL_HOURS).",
    )
    parser.add_argument("--skip-retention", action="store_true", help="Skip the retention step.")
    parser.add_argument("--skip-ingest", action="store_true", help="Skip the ingest step (retention only).")
    parser.add_argument("--retention-days", type=int, default=RETENTION_DAYS, help="Override SNOWKAP_RETENTION_DAYS.")
    parser.add_argument("--max-per-query", type=int, default=MAX_PER_QUERY, help="Articles per query (per source).")
    parser.add_argument("--limit", type=int, default=PER_RUN_LIMIT, help="Max articles to analyse per company per cycle.")
    parser.add_argument("--interval-hours", type=float, default=REFRESH_INTERVAL_HOURS, help="Loop cadence (default 24).")
    args = parser.parse_args(argv)

    logger.info(
        "news_refresh_worker starting: backend=%s interval=%.1fh retention=%dd max_per_query=%d limit=%d",
        os.environ.get("SNOWKAP_DB_BACKEND", "sqlite"),
        args.interval_hours,
        args.retention_days,
        args.max_per_query,
        args.limit,
    )

    if args.once:
        run_cycle(
            skip_retention=args.skip_retention,
            skip_ingest=args.skip_ingest,
            retention_days=args.retention_days,
            max_per_query=args.max_per_query,
            limit=args.limit,
        )
        return 0

    sleep_seconds = max(60.0, args.interval_hours * 3600)
    while not _SHOULD_EXIT:
        try:
            run_cycle(
                skip_retention=args.skip_retention,
                skip_ingest=args.skip_ingest,
                retention_days=args.retention_days,
                max_per_query=args.max_per_query,
                limit=args.limit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("refresh cycle crashed: %s", exc)

        if _SHOULD_EXIT:
            break

        next_at = datetime.now(timezone.utc).timestamp() + sleep_seconds
        logger.info(
            "next cycle in %.1fh (at %s UTC)",
            args.interval_hours,
            datetime.fromtimestamp(next_at, tz=timezone.utc).isoformat(),
        )
        # Use short sleep slices so SIGTERM can interrupt promptly.
        slept = 0.0
        while slept < sleep_seconds and not _SHOULD_EXIT:
            time.sleep(min(30.0, sleep_seconds - slept))
            slept += 30.0

    logger.info("news_refresh_worker exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
