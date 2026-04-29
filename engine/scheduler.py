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


def run_promote_job() -> None:
    """Phase 19 — periodic discovery promoter.

    Drains the candidate buffer (`data/ontology/discovery_staging.json`),
    applies confidence + frequency thresholds, and inserts qualifying
    candidates into `data/ontology/discovered.ttl`. Auto-promotes for
    entity / event / framework categories; theme / edge / weight /
    stakeholder candidates remain pending until a human approves them
    via the discovery review endpoint.

    Pre-fix: the design called for this to run every 30 min but it was
    never wired up — the only time it ran was when an admin manually
    POSTed `/api/discovery/promote`. As a result `discovered.ttl` had
    one promotion (April 15) and 17 candidates accumulated in the buffer
    for 12 days. Now it runs as part of the scheduler loop.
    """
    try:
        from engine.ontology.discovery.promoter import batch_promote
        result = batch_promote()
        logger.info("scheduler: discovery promoter ran -> %s", result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("scheduler: discovery promoter failed: %s", exc)


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
    parser.add_argument(
        "--promote-interval-minutes",
        type=int,
        default=30,
        help=(
            "Phase 19 — discovery promoter interval (default: 30 min). "
            "Set to 0 to disable the promoter (ingest-only mode)."
        ),
    )
    parser.add_argument(
        "--promote-once",
        action="store_true",
        help="Run only the discovery promoter once and exit (no ingest).",
    )
    args = parser.parse_args(argv)
    setup_logging("INFO")

    if args.promote_once:
        run_promote_job()
        return 0

    if args.once:
        run_ingest_job(args.max_per_query, args.limit)
        # Phase 19 — also drain the discovery buffer at the end of a one-shot
        # run so cron-based deployments don't need a separate cron entry.
        run_promote_job()
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
    # Phase 19 — discovery promoter on its own cadence (default 30 min).
    # Decoupled from ingestion because promotion is cheap (<1s) and we
    # want to drain the buffer even during quiet ingest periods.
    if args.promote_interval_minutes > 0:
        scheduler.add_job(
            run_promote_job,
            trigger="interval",
            minutes=args.promote_interval_minutes,
            next_run_time=None,
        )
    logger.info(
        "scheduler: started, ingest_interval=%s min, promote_interval=%s min, "
        "max_per_query=%s, limit=%s",
        args.interval_minutes,
        args.promote_interval_minutes,
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
