"""SQLite-backed job queue for the onboarding pipeline.

Why SQLite (and not Redis / Celery)
-----------------------------------
Redis is not provisioned in the Replit deployment. The existing
``data/snowkap.db`` SQLite file already serves as the index + onboarding
status store, and SQLite's ``BEGIN IMMEDIATE`` gives us the atomic
claim semantics we need for a single-writer queue without pulling in a
broker.

Producer
--------
``api.routes.admin_onboard.enqueue_onboarding`` writes a row here in
response to:

* ``POST /api/admin/onboard`` — super-admin onboards a new prospect.
* ``POST /api/news/onboarding-retry`` — self-service retry from the
  empty Home state.
* ``POST /api/auth/login`` (first time for a brand-new prospect domain).

Consumer
--------
``scripts/onboarding_worker.py`` polls ``claim_next`` in a loop and runs
the actual pipeline (``engine.jobs.onboard_runner.run_onboarding``).
The worker runs as its own Replit workflow so it can be restarted /
scaled independently of the API.

Schema
------
``onboard_jobs(id PK, slug, name, ticker_hint, domain, item_limit,
state, attempts, enqueued_at, started_at, finished_at, worker_id,
error)``. ``state`` is one of ``queued | running | done | failed``.
``item_limit`` (not ``limit``) avoids the SQL reserved word.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from engine.db import connect as _db_connect
from engine.index.sqlite_index import DB_PATH, _ensure_wal_mode  # noqa: F401

logger = logging.getLogger(__name__)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS onboard_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    slug         TEXT NOT NULL,
    name         TEXT,
    ticker_hint  TEXT,
    domain       TEXT,
    item_limit   INTEGER NOT NULL DEFAULT 10,
    state        TEXT NOT NULL DEFAULT 'queued',
    attempts     INTEGER NOT NULL DEFAULT 0,
    enqueued_at  TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    worker_id    TEXT,
    error        TEXT
);

CREATE INDEX IF NOT EXISTS idx_onboard_jobs_state
    ON onboard_jobs(state, id);
"""


_SCHEMA_READY = False


@contextmanager
def _connect() -> Iterator[Any]:
    """Backend-aware connection (Phase 24)."""
    with _db_connect() as conn:
        yield conn


def ensure_schema() -> None:
    """Create the queue table if it doesn't exist. Idempotent."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ensure_wal_mode()
    with _connect() as conn:
        conn.executescript(SCHEMA_SQL)
    _SCHEMA_READY = True


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class OnboardJob:
    id: int
    slug: str
    name: str | None
    ticker_hint: str | None
    domain: str | None
    item_limit: int
    state: str
    attempts: int
    enqueued_at: str
    started_at: str | None
    finished_at: str | None
    worker_id: str | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "ticker_hint": self.ticker_hint,
            "domain": self.domain,
            "item_limit": self.item_limit,
            "state": self.state,
            "attempts": self.attempts,
            "enqueued_at": self.enqueued_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "worker_id": self.worker_id,
            "error": self.error,
        }


def enqueue(
    *,
    slug: str,
    name: str | None,
    ticker_hint: str | None,
    domain: str | None,
    item_limit: int = 10,
) -> int:
    """Append a new job to the queue. Returns the new row id.

    No de-duplication: the caller is expected to use
    ``onboarding_status.claim_pending`` / ``force_claim_pending`` to
    guard against double-enqueue. Those checks gate access to the
    ``onboarding_status`` row, not this queue, so even if two enqueues
    sneak through the worker's ``claim_next`` will simply process them
    sequentially against the same already-ready status row (idempotent
    because ``onboard_company`` re-uses the existing canonical slug).
    """
    ensure_schema()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO onboard_jobs
                (slug, name, ticker_hint, domain, item_limit, state, attempts, enqueued_at)
            VALUES (?, ?, ?, ?, ?, 'queued', 0, ?)
            """,
            (slug, name, ticker_hint, domain, int(item_limit), _now()),
        )
        return int(cur.lastrowid or 0)


def claim_next(worker_id: str | None = None) -> OnboardJob | None:
    """Atomically pull the oldest queued job and mark it ``running``.

    Uses ``BEGIN IMMEDIATE`` so two workers (or two retry threads) can't
    both grab the same row. Returns ``None`` when the queue is empty —
    the caller is expected to sleep + retry.
    """
    ensure_schema()
    wid = worker_id or f"worker-{os.getpid()}"
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM onboard_jobs WHERE state = 'queued' "
                "ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            now = _now()
            conn.execute(
                "UPDATE onboard_jobs SET state = 'running', started_at = ?, "
                "worker_id = ?, attempts = attempts + 1 WHERE id = ?",
                (now, wid, row["id"]),
            )
            conn.execute("COMMIT")
            updated = dict(row)
            updated["state"] = "running"
            updated["started_at"] = now
            updated["worker_id"] = wid
            updated["attempts"] = int(row["attempts"]) + 1
            return OnboardJob(**updated)
        except Exception:
            conn.execute("ROLLBACK")
            raise


def mark_done(job_id: int) -> None:
    ensure_schema()
    with _connect() as conn:
        conn.execute(
            "UPDATE onboard_jobs SET state = 'done', finished_at = ?, error = NULL "
            "WHERE id = ?",
            (_now(), int(job_id)),
        )


def mark_failed(job_id: int, error: str) -> None:
    ensure_schema()
    with _connect() as conn:
        conn.execute(
            "UPDATE onboard_jobs SET state = 'failed', finished_at = ?, error = ? "
            "WHERE id = ?",
            (_now(), error[:500], int(job_id)),
        )


def get(job_id: int) -> OnboardJob | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM onboard_jobs WHERE id = ?", (int(job_id),)
        ).fetchone()
        return OnboardJob(**dict(row)) if row else None


def queue_depth() -> int:
    """Return the number of queued (not yet running) jobs."""
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM onboard_jobs WHERE state = 'queued'"
        ).fetchone()
        return int(row["n"]) if row else 0


def _truncate_all() -> None:
    """Test helper — wipe every job. Never call from production code."""
    ensure_schema()
    with _connect() as conn:
        conn.execute("DELETE FROM onboard_jobs")
