"""Phase 48.K — weekly newsletter subscribers.

Per-(email, company_slug) subscription store. Users auto-subscribe on
login (api/routes/legacy_adapter._mint_login_response); the Sunday cron
sends each company's active subscribers the weekly Morning-Brew brief.

Postgres-backed via engine.db.connect (SQLite hard-disabled, Phase 48.0).

Public surface:
  * subscribe(email, company_slug) → None       (idempotent, reactivates)
  * deactivate(email, company_slug) → bool
  * list_active(company_slug) → list[str]        (emails)
  * is_subscribed(email, company_slug) → bool
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from engine.db import connect as _db_connect

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS newsletter_subscribers (
    email         TEXT NOT NULL,
    company_slug  TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (email, company_slug)
);
CREATE INDEX IF NOT EXISTS idx_newsletter_subs_company
    ON newsletter_subscribers(company_slug, active);
"""

_SCHEMA_READY = False


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _db_connect() as conn:
        conn.executescript(_SCHEMA_SQL)
    _SCHEMA_READY = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def subscribe(email: str, company_slug: str) -> None:
    """Idempotent subscribe (reactivates if previously unsubscribed)."""
    email = (email or "").strip().lower()
    company_slug = (company_slug or "").strip().lower()
    if not email or "@" not in email or not company_slug:
        return
    ensure_schema()
    with _db_connect() as conn:
        # UPDATE-then-INSERT (portable upsert across sqlite + postgres)
        cur = conn.execute(
            "UPDATE newsletter_subscribers SET active = 1 "
            "WHERE email = ? AND company_slug = ?",
            (email, company_slug),
        )
        if not cur.rowcount:
            conn.execute(
                "INSERT INTO newsletter_subscribers (email, company_slug, active, created_at) "
                "VALUES (?, ?, 1, ?)",
                (email, company_slug, _now()),
            )
        conn.commit()


def deactivate(email: str, company_slug: str) -> bool:
    email = (email or "").strip().lower()
    company_slug = (company_slug or "").strip().lower()
    if not email or not company_slug:
        return False
    ensure_schema()
    with _db_connect() as conn:
        cur = conn.execute(
            "UPDATE newsletter_subscribers SET active = 0 "
            "WHERE email = ? AND company_slug = ?",
            (email, company_slug),
        )
        conn.commit()
        return bool(cur.rowcount)


def list_active(company_slug: str) -> list[str]:
    company_slug = (company_slug or "").strip().lower()
    if not company_slug:
        return []
    ensure_schema()
    with _db_connect() as conn:
        cur = conn.execute(
            "SELECT email FROM newsletter_subscribers "
            "WHERE company_slug = ? AND active = 1",
            (company_slug,),
        )
        return [r["email"] for r in cur.fetchall()]


def is_subscribed(email: str, company_slug: str) -> bool:
    email = (email or "").strip().lower()
    company_slug = (company_slug or "").strip().lower()
    if not email or not company_slug:
        return False
    ensure_schema()
    with _db_connect() as conn:
        cur = conn.execute(
            "SELECT 1 FROM newsletter_subscribers "
            "WHERE email = ? AND company_slug = ? AND active = 1",
            (email, company_slug),
        )
        return cur.fetchone() is not None
