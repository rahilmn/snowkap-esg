"""Phase 36 — Scheduler state persistence.

Tiny SQLite table that lets cron jobs record their last-run timestamp +
last-run summary JSON. Read by the `/metrics` endpoint + the per-tenant
body-coverage admin view so operators can answer "did the retry cron
actually fire last night" and "what did it find" without scraping logs.

Public surface:
    ensure_schema() -> None
    record_run(job_id, *, result, status) -> None
    get_last_run(job_id) -> dict | None
    list_all_runs() -> list[dict]
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from engine.db import connect as _db_connect

logger = logging.getLogger(__name__)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS scheduler_state (
    job_id            TEXT PRIMARY KEY,
    last_run_at       REAL NOT NULL,
    last_status       TEXT NOT NULL,
    last_result_json  TEXT,
    updated_at        REAL NOT NULL
);
"""

_SCHEMA_READY = False


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _db_connect() as conn:
        conn.executescript(_SCHEMA_SQL)
    _SCHEMA_READY = True


def record_run(
    job_id: str,
    *,
    result: dict[str, Any] | None = None,
    status: str = "ok",
) -> None:
    """Persist a job's last-run timestamp + result summary.

    Called at the end of a cron job execution. `status` is one of
    'ok' / 'error' / 'partial'. `result` is serialized to JSON; pass
    the same dict the job returns so operators see actual counts.
    """
    ensure_schema()
    now = time.time()
    payload_json = json.dumps(result or {}, default=str)
    try:
        with _db_connect() as conn:
            conn.execute(
                """
                INSERT INTO scheduler_state
                  (job_id, last_run_at, last_status, last_result_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                  last_run_at = excluded.last_run_at,
                  last_status = excluded.last_status,
                  last_result_json = excluded.last_result_json,
                  updated_at = excluded.updated_at
                """,
                (job_id, now, status, payload_json, now),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("scheduler_state.record_run failed for %s: %s", job_id, exc)


def get_last_run(job_id: str) -> dict | None:
    """Return the last run row for a job, or None if never run.

    Shape: {job_id, last_run_at, last_status, last_result, updated_at}.
    """
    ensure_schema()
    try:
        with _db_connect() as conn:
            row = conn.execute(
                "SELECT * FROM scheduler_state WHERE job_id = ?",
                (job_id,),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning("scheduler_state.get_last_run failed: %s", exc)
        return None
    if not row:
        return None
    return {
        "job_id": row["job_id"],
        "last_run_at": row["last_run_at"],
        "last_status": row["last_status"],
        "last_result": json.loads(row["last_result_json"] or "{}"),
        "updated_at": row["updated_at"],
    }


def list_all_runs() -> list[dict]:
    """Return every job's last-run row. Used by /metrics + admin views."""
    ensure_schema()
    try:
        with _db_connect() as conn:
            rows = conn.execute("SELECT * FROM scheduler_state").fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning("scheduler_state.list_all_runs failed: %s", exc)
        return []
    return [
        {
            "job_id": r["job_id"],
            "last_run_at": r["last_run_at"],
            "last_status": r["last_status"],
            "last_result": json.loads(r["last_result_json"] or "{}"),
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]
