"""Phase 13 B2 — Per-article on-demand-analysis status tracker.

Mirrors the `onboarding_status` pattern but at article granularity. When a
user clicks "View Insights" on a HOME-tier article, the on-demand pipeline
runs for ~30-100s in a background thread. If anything goes wrong (OpenAI
429, malformed input, OOM), the previous flow swallowed the exception and
the UI spun forever.

This table records each job's lifecycle so the frontend can poll
`GET /api/news/{id}/analysis-status` and render explicit pending / ready /
failed states.

Schema:
    article_analysis_status(
      article_id   TEXT PRIMARY KEY,
      company_slug TEXT NOT NULL,
      state        TEXT NOT NULL,    -- 'pending'|'running'|'ready'|'failed'
      started_at   TEXT NOT NULL,
      finished_at  TEXT,
      error_class  TEXT,             -- 'openai_rate_limit'|'pipeline_crash'|...
      error        TEXT,
      elapsed_seconds REAL DEFAULT 0
    )
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterator, Literal

from engine.db import connect as _db_connect
from engine.index.sqlite_index import DB_PATH, _ensure_wal_mode  # noqa: F401

logger = logging.getLogger(__name__)

AnalysisState = Literal["pending", "running", "ready", "failed"]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS article_analysis_status (
    article_id      TEXT PRIMARY KEY,
    company_slug    TEXT NOT NULL,
    state           TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    error_class     TEXT,
    error           TEXT,
    elapsed_seconds REAL DEFAULT 0
);
"""

_SCHEMA_READY = False


@contextmanager
def _connect() -> Iterator[Any]:
    """Backend-aware connection (Phase 24)."""
    with _db_connect() as conn:
        yield conn


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ensure_wal_mode()
    with _connect() as conn:
        conn.executescript(SCHEMA_SQL)
    _SCHEMA_READY = True


@dataclass
class AnalysisStatus:
    article_id: str
    company_slug: str
    state: str
    started_at: str
    finished_at: str | None
    error_class: str | None
    error: str | None
    elapsed_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def mark_pending(article_id: str, company_slug: str) -> None:
    """Insert or replace a status row in 'pending' state."""
    ensure_schema()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO article_analysis_status
              (article_id, company_slug, state, started_at, finished_at, error_class, error, elapsed_seconds)
            VALUES (?, ?, 'pending', ?, NULL, NULL, NULL, 0)
            ON CONFLICT(article_id) DO UPDATE SET
              state='pending',
              started_at=excluded.started_at,
              finished_at=NULL,
              error_class=NULL,
              error=NULL,
              elapsed_seconds=0
            """,
            (article_id, company_slug, _now_iso()),
        )


def mark_running(article_id: str) -> None:
    ensure_schema()
    with _connect() as conn:
        conn.execute(
            "UPDATE article_analysis_status SET state='running' WHERE article_id=?",
            (article_id,),
        )


def mark_ready(article_id: str, started_perf_counter: float) -> None:
    """Mark success and record elapsed wall time."""
    ensure_schema()
    elapsed = round(max(0.0, time.perf_counter() - started_perf_counter), 2)
    with _connect() as conn:
        conn.execute(
            """
            UPDATE article_analysis_status
            SET state='ready', finished_at=?, elapsed_seconds=?,
                error_class=NULL, error=NULL
            WHERE article_id=?
            """,
            (_now_iso(), elapsed, article_id),
        )


def mark_failed(
    article_id: str,
    error_class: str,
    error: str,
    started_perf_counter: float | None = None,
) -> None:
    """Record a failure with classification.

    error_class examples:
      - 'openai_rate_limit'
      - 'openai_timeout'
      - 'pipeline_crash'
      - 'article_not_found'
      - 'company_not_found'
      - 'unknown'
    """
    ensure_schema()
    elapsed = (
        round(max(0.0, time.perf_counter() - started_perf_counter), 2)
        if started_perf_counter is not None
        else 0.0
    )
    with _connect() as conn:
        conn.execute(
            """
            UPDATE article_analysis_status
            SET state='failed', finished_at=?, error_class=?, error=?, elapsed_seconds=?
            WHERE article_id=?
            """,
            (_now_iso(), error_class, error[:300], elapsed, article_id),
        )


def get_status(article_id: str) -> AnalysisStatus | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM article_analysis_status WHERE article_id=?",
            (article_id,),
        ).fetchone()
    if not row:
        return None
    return AnalysisStatus(
        article_id=row["article_id"],
        company_slug=row["company_slug"],
        state=row["state"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        error_class=row["error_class"],
        error=row["error"],
        elapsed_seconds=row["elapsed_seconds"] or 0.0,
    )


def classify_pipeline_error(exc: BaseException) -> str:
    """Best-effort classification of pipeline exceptions for ops + UI."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "ratelimit" in name or "rate_limit" in name or "rate limit" in msg or "429" in msg:
        return "openai_rate_limit"
    if "timeout" in name or "timeout" in msg:
        return "openai_timeout"
    if "filenotfound" in name or "no such file" in msg:
        return "article_not_found"
    if "keyerror" in name and "company" in msg:
        return "company_not_found"
    if "json" in name and "decode" in name:
        return "malformed_input"
    return "pipeline_crash"
