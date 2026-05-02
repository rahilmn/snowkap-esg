#!/usr/bin/env python
"""Standalone worker that drains the onboarding job queue.

Runs in its OWN Replit workflow (see ``.replit`` → ``Onboarding
Worker``) so a slow yfinance lookup or LLM pipeline cannot block the
API event loop.

Loop:

    1. ``claim_next()`` — atomically pull the oldest queued job.
    2. Run ``_background_onboard(...)`` (the existing pipeline body).
    3. ``mark_done`` / ``mark_failed`` and continue.
    4. When the queue is empty, sleep ``POLL_INTERVAL_SECONDS`` and retry.

Graceful shutdown: SIGINT / SIGTERM finishes the current job (if any)
before exiting. The Replit workflow restart story is non-destructive
— half-finished jobs stay marked ``running`` and an operator can
re-enqueue if needed.

Tunables (all optional):

    SNOWKAP_ONBOARD_POLL_SECONDS   — default 2.0
    SNOWKAP_ONBOARD_WORKER_ID      — default "worker-<pid>"
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
import traceback
from pathlib import Path

# Make sure the project root is importable when this script is invoked
# directly (e.g. ``python scripts/onboarding_worker.py``).
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.jobs import onboard_queue  # noqa: E402

logging.basicConfig(
    level=os.environ.get("SNOWKAP_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("onboarding_worker")


POLL_INTERVAL_SECONDS = float(os.environ.get("SNOWKAP_ONBOARD_POLL_SECONDS", "2.0"))
WORKER_ID = os.environ.get("SNOWKAP_ONBOARD_WORKER_ID") or f"worker-{os.getpid()}"


_SHOULD_EXIT = False


def _request_shutdown(signum: int, _frame: object) -> None:
    global _SHOULD_EXIT
    logger.info("received signal %s — finishing current job and exiting", signum)
    _SHOULD_EXIT = True


def _run_one(job: onboard_queue.OnboardJob) -> None:
    """Execute a single onboarding job. Errors are caught and recorded
    on the job row — we never let an exception kill the worker loop."""
    # Lazy import keeps boot fast and avoids importing FastAPI route
    # modules until we actually have work.
    from api.routes.admin_onboard import _background_onboard

    logger.info(
        "claimed job id=%s slug=%s domain=%s name=%s",
        job.id, job.slug, job.domain, job.name,
    )
    try:
        _background_onboard(
            slug=job.slug,
            name=job.name,
            ticker_hint=job.ticker_hint,
            domain=job.domain,
            limit=job.item_limit,
        )
        onboard_queue.mark_done(job.id)
        logger.info("finished job id=%s slug=%s", job.id, job.slug)
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()[:500]
        msg = f"{exc}\n{tb}"
        logger.exception("job id=%s slug=%s failed: %s", job.id, job.slug, exc)
        onboard_queue.mark_failed(job.id, msg)


def main() -> int:
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    logger.info(
        "onboarding worker starting (id=%s, poll=%.1fs, db=%s)",
        WORKER_ID, POLL_INTERVAL_SECONDS, onboard_queue.DB_PATH,
    )
    onboard_queue.ensure_schema()

    while not _SHOULD_EXIT:
        try:
            job = onboard_queue.claim_next(worker_id=WORKER_ID)
        except Exception:  # noqa: BLE001
            logger.exception("claim_next failed; backing off")
            time.sleep(min(POLL_INTERVAL_SECONDS * 5, 30.0))
            continue

        if job is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        _run_one(job)

    logger.info("onboarding worker exited cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
