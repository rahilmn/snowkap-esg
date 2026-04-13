"""Optional periodic ingestion via APScheduler.

Run with::

    python engine/scheduler.py

Defaults to hourly ingestion for all 7 target companies. Override with
``--interval-minutes`` or ``--cron-expr``.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.main import cmd_ingest, setup_logging  # noqa: E402

logger = logging.getLogger(__name__)


class _IngestArgs:
    """Plain namespace matching what cmd_ingest expects."""

    def __init__(self, max_per_query: int | None, limit: int | None) -> None:
        self.all = True
        self.company = None
        self.max = max_per_query
        self.limit = limit


def run_ingest_job(max_per_query: int | None, limit: int | None) -> None:
    logger.info("scheduler: starting scheduled ingestion")
    cmd_ingest(_IngestArgs(max_per_query=max_per_query, limit=limit))
    logger.info("scheduler: finished scheduled ingestion")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scheduled ingestion for the Snowkap ESG engine"
    )
    parser.add_argument(
        "--interval-minutes",
        type=int,
        default=60,
        help="Run interval in minutes (default: 60)",
    )
    parser.add_argument(
        "--max-per-query",
        type=int,
        default=10,
        help="Max articles per news query",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Max articles processed per company per run (controls LLM cost)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run the ingest job once and exit (useful for cron)",
    )
    args = parser.parse_args(argv)
    setup_logging("INFO")

    if args.once:
        run_ingest_job(args.max_per_query, args.limit)
        return 0

    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
    except ImportError:
        logger.error(
            "APScheduler not installed. Install with: pip install apscheduler"
        )
        return 1

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_ingest_job,
        trigger="interval",
        minutes=args.interval_minutes,
        args=[args.max_per_query, args.limit],
        next_run_time=None,  # wait for first interval
    )
    logger.info(
        "scheduler: started, interval=%s minutes, max_per_query=%s, limit=%s",
        args.interval_minutes,
        args.max_per_query,
        args.limit,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("scheduler: shutting down")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
