"""Phase 11D — OpenAI cost tracking.

Every LLM call the pipeline makes logs a row here with token counts, model
used, and the article it was for. The `/metrics` endpoint aggregates 24h
spend so ops has a real-time view of cost — previously this was opaque and
only visible on the OpenAI dashboard.

Schema:
    llm_calls(
      id             TEXT PRIMARY KEY,
      ts             TEXT NOT NULL,    -- ISO UTC
      model          TEXT NOT NULL,
      prompt_tokens  INTEGER,
      completion_tokens INTEGER,
      total_tokens   INTEGER,
      cost_usd       REAL,
      article_id     TEXT,
      stage          TEXT,              -- 'nlp'|'theme_tagging'|'insight'|'recommendations'|'subject_line'
      error          TEXT
    )
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from engine.index.sqlite_index import DB_PATH, _ensure_wal_mode

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS llm_calls (
    id                TEXT PRIMARY KEY,
    ts                TEXT NOT NULL,
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens      INTEGER DEFAULT 0,
    cost_usd          REAL DEFAULT 0,
    article_id        TEXT,
    stage             TEXT,
    error             TEXT
);

CREATE INDEX IF NOT EXISTS idx_llm_calls_ts ON llm_calls(ts DESC);
CREATE INDEX IF NOT EXISTS idx_llm_calls_article ON llm_calls(article_id);
"""

_SCHEMA_READY = False


# Rough per-1K-token pricing in USD (as of 2026-04). Updated periodically;
# ops only cares about order-of-magnitude for daily spend alerts.
_PRICING_USD_PER_1K = {
    "gpt-4.1":        (0.0050, 0.0150),  # (prompt, completion)
    "gpt-4.1-mini":   (0.0004, 0.0016),
    "gpt-4o":         (0.0025, 0.0100),
    "gpt-4o-mini":    (0.00015, 0.0006),
    "o4-mini":        (0.00015, 0.0006),
    "text-embedding-3-small": (0.00002, 0.0),
}


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Rough USD cost. Unknown models → 0 (better than overcharging the log)."""
    prices = _PRICING_USD_PER_1K.get(model) or _PRICING_USD_PER_1K.get(model.split("-2")[0])
    if not prices:
        return 0.0
    p, c = prices
    return round((prompt_tokens / 1000) * p + (completion_tokens / 1000) * c, 6)


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ensure_wal_mode()
    with _connect() as conn:
        conn.executescript(SCHEMA_SQL)
    _SCHEMA_READY = True


def log_call(
    *,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    article_id: str | None = None,
    stage: str | None = None,
    error: str | None = None,
) -> None:
    """Non-raising. Never let a tracking failure block the pipeline."""
    try:
        ensure_schema()
        total = prompt_tokens + completion_tokens
        cost = _estimate_cost(model, prompt_tokens, completion_tokens)
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_calls
                (id, ts, model, prompt_tokens, completion_tokens, total_tokens, cost_usd, article_id, stage, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    model,
                    prompt_tokens,
                    completion_tokens,
                    total,
                    cost,
                    article_id,
                    stage,
                    error[:500] if error else None,
                ),
            )
    except Exception as exc:
        logger.debug("llm_calls.log_call failed (non-blocking): %s", exc)


def spend_last_24h_usd() -> float:
    """Sum of `cost_usd` for calls in the last 24h. Used by /metrics."""
    try:
        ensure_schema()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
        with _connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE ts >= ?",
                (cutoff,),
            ).fetchone()
            return float(row[0]) if row else 0.0
    except Exception:
        return 0.0


def count_last_24h() -> int:
    """Number of LLM calls in the last 24h."""
    try:
        ensure_schema()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
        with _connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM llm_calls WHERE ts >= ?",
                (cutoff,),
            ).fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


def _truncate_all() -> None:
    ensure_schema()
    with _connect() as conn:
        conn.execute("DELETE FROM llm_calls")
