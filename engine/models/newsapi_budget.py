"""C#3 — persist the NewsAPI monthly token budget across restarts.

``engine.ingestion.news_router.BudgetState`` lives in memory, so every Railway
restart zeroed ``spent_this_month`` — the monthly cap (2000) was never enforced
across a whole month, and a restart loop could quietly blow the real NewsAPI.ai
quota while ``/metrics`` reported full budget. This module persists the
per-month counters to a tiny Postgres row (SQLite in tests) so the budget
survives restarts. Backend-aware (mirrors ``engine/models/insight_payload.py``).
Best-effort + non-raising: a tracking failure must never block ingestion.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from engine.db import (
    connect as _db_connect,
    is_postgres,
    mark_schema_ready,
    schema_ready,
)

logger = logging.getLogger(__name__)


def ensure_schema() -> None:
    # DB-identity-keyed guard (engine.db.schema_guard): in production the
    # CREATE runs once per process (no per-call DDL); under tests that point
    # connect() at different SQLite files, each DB gets its own CREATE, so the
    # table always exists where the caller reads it. A plain module-global
    # boolean latched True against the wrong DB and silently no-op'd save/load.
    if schema_ready("newsapi_budget"):
        return
    with _db_connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS newsapi_budget ("
            "  month_anchor TEXT PRIMARY KEY,"
            "  spent_this_month INTEGER NOT NULL DEFAULT 0,"
            "  burst_spent INTEGER NOT NULL DEFAULT 0,"
            "  updated_at TEXT"
            ")"
        )
    mark_schema_ready("newsapi_budget")


def load(month_anchor: str) -> dict | None:
    """Return ``{spent_this_month, burst_spent}`` for the month, or None."""
    if not month_anchor:
        return None
    try:
        ensure_schema()
        with _db_connect() as conn:
            row = conn.execute(
                "SELECT spent_this_month, burst_spent FROM newsapi_budget "
                "WHERE month_anchor = ?",
                (month_anchor,),
            ).fetchone()
        if not row:
            return None
        spent = row["spent_this_month"] if hasattr(row, "keys") else row[0]
        burst = row["burst_spent"] if hasattr(row, "keys") else row[1]
        return {"spent_this_month": int(spent or 0), "burst_spent": int(burst or 0)}
    except Exception as exc:  # noqa: BLE001 — telemetry never blocks ingestion
        logger.debug("newsapi_budget.load failed (non-fatal): %s", exc)
        return None


def save(month_anchor: str, spent_this_month: int, burst_spent: int) -> None:
    if not month_anchor:
        return
    try:
        ensure_schema()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with _db_connect() as conn:
            if is_postgres():
                sql = (
                    "INSERT INTO newsapi_budget "
                    "  (month_anchor, spent_this_month, burst_spent, updated_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT (month_anchor) DO UPDATE SET "
                    "  spent_this_month = EXCLUDED.spent_this_month, "
                    "  burst_spent = EXCLUDED.burst_spent, "
                    "  updated_at = EXCLUDED.updated_at"
                )
            else:
                sql = (
                    "INSERT OR REPLACE INTO newsapi_budget "
                    "  (month_anchor, spent_this_month, burst_spent, updated_at) "
                    "VALUES (?, ?, ?, ?)"
                )
            conn.execute(sql, (month_anchor, int(spent_this_month), int(burst_spent), now))
    except Exception as exc:  # noqa: BLE001 — telemetry never blocks ingestion
        logger.debug("newsapi_budget.save failed (non-fatal): %s", exc)
