"""Phase 4 §6.6 — outbound-touch tracker for CTA differentiation.

Counts how many emails the sales tool has previously sent to a given
(recipient_email, company_slug) pair. The CTA renderer uses this to
choose between:

  - **first-touch CTA**: "Read full analysis →"   (educational, no demo ask)
  - **second-touch CTA**: "Book a 20-min walkthrough →"  (qualified prospect)

The plan calls for ``outbound_touches`` as a new table; we mirror the
existing ``onboarding_status`` pattern (sqlite, WAL, lazy schema, backend
abstraction via ``engine.db.connect``).

Usage:

    from engine.models.outbound_touches import (
        record_touch, count_touches, is_first_touch
    )

    if is_first_touch(recipient, slug):
        cta_label = FIRST_TOUCH_CTA
    else:
        cta_label = SECOND_TOUCH_CTA

    # After successful send (call from share endpoint):
    record_touch(recipient, slug, article_id)

Schema:
    outbound_touches(
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      recipient_email TEXT NOT NULL,
      company_slug    TEXT NOT NULL,
      article_id      TEXT,
      sent_at         TEXT NOT NULL
    )

This is append-only. We never delete rows — the count is the historical
truth used to gate the CTA choice. If a future product change wants to
"reset" a relationship, that's a deletion script the operator runs
explicitly.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from engine.db import connect as _db_connect
from engine.db import schema_ready, mark_schema_ready

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


FIRST_TOUCH_CTA = "Read full analysis →"
SECOND_TOUCH_CTA = "Book a 20-min walkthrough →"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS outbound_touches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_email TEXT NOT NULL,
    company_slug    TEXT NOT NULL,
    article_id      TEXT,
    sent_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outbound_touches_pair
    ON outbound_touches(recipient_email, company_slug);
"""

@contextmanager
def _connect() -> Iterator[Any]:
    with _db_connect() as conn:
        yield conn


def ensure_schema() -> None:
    """Lazy schema init — safe to call at every entry point."""
    if schema_ready("outbound_touches"):
        return
    # Lazy import so this module stays importable in test contexts that
    # don't need WAL mode (e.g. in-memory sqlite for unit tests).
    try:
        from engine.index.sqlite_index import _ensure_wal_mode
        _ensure_wal_mode()
    except Exception:  # noqa: BLE001 — non-fatal in test contexts
        pass

    with _connect() as conn:
        # Postgres uses ; in the same script fine; sqlite needs executescript
        if hasattr(conn, "executescript"):
            conn.executescript(SCHEMA_SQL)
        else:
            for stmt in [s.strip() for s in SCHEMA_SQL.split(";") if s.strip()]:
                conn.execute(stmt)
    mark_schema_ready("outbound_touches")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalise_email(email: str) -> str:
    """Lowercase + strip so 'CFO@Acme.com' and 'cfo@acme.com  ' are one
    contact for touch-counting purposes. The display address stays
    whatever was passed; only the lookup key normalises."""
    return (email or "").strip().lower()


def _normalise_slug(slug: str) -> str:
    return (slug or "").strip().lower()


def record_touch(
    recipient_email: str,
    company_slug: str,
    article_id: str | None = None,
) -> int:
    """Append a touch row. Returns the new row id (0 on failure).

    Idempotent in the sense that calling twice with the same args
    INSERTS twice — that IS the touch count. Callers should only call
    once per successful send.
    """
    if not recipient_email or not company_slug:
        return 0
    ensure_schema()
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO outbound_touches "
                "(recipient_email, company_slug, article_id, sent_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    _normalise_email(recipient_email),
                    _normalise_slug(company_slug),
                    article_id or None,
                    _now(),
                ),
            )
            return int(cur.lastrowid or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("record_touch failed: %s", exc)
        return 0


def count_touches(recipient_email: str, company_slug: str) -> int:
    """How many touches have we already sent to this pair? 0 = first-touch."""
    if not recipient_email or not company_slug:
        return 0
    ensure_schema()
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM outbound_touches "
                "WHERE recipient_email = ? AND company_slug = ?",
                (
                    _normalise_email(recipient_email),
                    _normalise_slug(company_slug),
                ),
            )
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("count_touches failed: %s", exc)
        return 0


def is_first_touch(recipient_email: str, company_slug: str) -> bool:
    """Convenience: True iff no prior touch exists for this pair."""
    return count_touches(recipient_email, company_slug) == 0


def cta_label_for(recipient_email: str, company_slug: str) -> str:
    """Pick the CTA per §6.6 cadence rules."""
    return (
        FIRST_TOUCH_CTA
        if is_first_touch(recipient_email, company_slug)
        else SECOND_TOUCH_CTA
    )


# ---------------------------------------------------------------------------
# Aggregations for /metrics
# ---------------------------------------------------------------------------


def total_count() -> int:
    """Total touches sent across all (recipient, company) pairs."""
    ensure_schema()
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM outbound_touches")
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    except Exception as exc:  # noqa: BLE001
        logger.debug("total_count failed: %s", exc)
        return 0


def first_touch_ratio() -> dict[str, int]:
    """Return {'first_touch': N, 'subsequent_touch': M} aggregates over all
    distinct (recipient, company) pairs ever recorded.

    A pair counts as 'first_touch' iff it has exactly 1 row;
    'subsequent_touch' iff it has 2+ rows. Used for the CTA cadence
    health metric: a healthy product has >0 in BOTH buckets — pure
    first_touch means no one is converting, pure subsequent means
    cold outreach has stopped.
    """
    ensure_schema()
    try:
        with _connect() as conn:
            cur = conn.cursor()
            # Histogram of touch-counts per pair → bucket
            cur.execute(
                "SELECT cnt, COUNT(*) FROM ("
                "  SELECT COUNT(*) AS cnt FROM outbound_touches "
                "  GROUP BY recipient_email, company_slug"
                ") t GROUP BY cnt"
            )
            first = 0
            subsequent = 0
            for row in cur.fetchall():
                pair_touch_count = int(row[0])
                num_pairs = int(row[1])
                if pair_touch_count == 1:
                    first = num_pairs
                else:
                    subsequent += num_pairs
            return {"first_touch": first, "subsequent_touch": subsequent}
    except Exception as exc:  # noqa: BLE001
        logger.debug("first_touch_ratio failed: %s", exc)
        return {"first_touch": 0, "subsequent_touch": 0}
