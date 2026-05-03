"""Phase 10 — SQLite-backed drip campaign store.

Three tables in `data/snowkap.db` (same file as article index + tenant registry):

    campaigns              — one row per admin-configured campaign
    campaign_recipients    — email list attached to each campaign
    campaign_send_log      — append-only history of every attempted send

All writes go through the helpers in this module so the runner and API
layers share one schema definition. Schema init is idempotent (called
lazily on first access).

Schema rationale:
  * `template_type` is kept as a column even though only `'share_single'`
    is supported in V1 — so V2's newsletter-digest / CFO-brief templates
    can land without a migration.
  * `campaign_recipients` has UNIQUE(campaign_id, email) so repeated
    upserts of the same email are idempotent.
  * `campaign_send_log` has NO foreign key to campaigns — so deleting a
    campaign preserves its send history for audit/compliance.
  * `idx_campaigns_active_due` makes the runner's hot-path query
    (`WHERE status='active' AND next_send_at <= now`) O(log n).
  * `idx_sendlog_dedup` accelerates the per-recipient dedup check.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Literal

from engine.db import connect as _db_connect, is_postgres
from engine.index.sqlite_index import DB_PATH  # noqa: F401

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS campaigns (
    id                 TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    created_by         TEXT NOT NULL,
    template_type      TEXT NOT NULL DEFAULT 'share_single',
    target_company     TEXT NOT NULL,
    article_selection  TEXT NOT NULL,           -- 'latest_home' | 'specific'
    article_id         TEXT,                    -- required if article_selection='specific'
    cadence            TEXT NOT NULL,           -- 'once' | 'weekly' | 'monthly'
    day_of_week        INTEGER,                 -- 0=Monday ... 6=Sunday
    day_of_month       INTEGER,                 -- 1-28
    send_time_utc      TEXT,                    -- 'HH:MM'
    cta_url            TEXT,
    cta_label          TEXT,
    sender_note        TEXT,
    status             TEXT NOT NULL DEFAULT 'active',  -- 'active'|'paused'|'archived'
    last_sent_at       TEXT,
    next_send_at       TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_campaigns_active_due
    ON campaigns(status, next_send_at);

CREATE TABLE IF NOT EXISTS campaign_recipients (
    id             TEXT PRIMARY KEY,
    campaign_id    TEXT NOT NULL,
    email          TEXT NOT NULL,
    name_override  TEXT,
    last_sent_at   TEXT,
    created_at     TEXT NOT NULL,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE,
    UNIQUE(campaign_id, email)
);

CREATE INDEX IF NOT EXISTS idx_recipients_campaign
    ON campaign_recipients(campaign_id);

CREATE TABLE IF NOT EXISTS campaign_send_log (
    id                TEXT PRIMARY KEY,
    campaign_id       TEXT NOT NULL,
    recipient_email   TEXT NOT NULL,
    article_id        TEXT,
    subject           TEXT,
    html_length       INTEGER,
    status            TEXT NOT NULL,    -- 'sent'|'preview'|'failed'|'skipped_stale'|'skipped_dedup'
    provider_id       TEXT,
    error             TEXT,
    sent_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sendlog_campaign
    ON campaign_send_log(campaign_id, sent_at DESC);

CREATE INDEX IF NOT EXISTS idx_sendlog_dedup
    ON campaign_send_log(campaign_id, recipient_email, article_id, sent_at);
"""

_SCHEMA_READY = False


@contextmanager
def _connect() -> Iterator[Any]:
    """Backend-aware connection (Phase 24).

    On SQLite, ``PRAGMA foreign_keys = ON`` is set so the
    ``ON DELETE CASCADE`` on ``campaign_recipients`` actually fires.
    On Postgres, foreign keys are always enforced — no toggle needed.
    """
    with _db_connect() as conn:
        if not is_postgres():
            conn.execute("PRAGMA foreign_keys = ON")
        yield conn


def ensure_schema() -> None:
    """Create the 3 tables + indexes on first use. Idempotent."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Phase 11A: WAL via shared bootstrap so campaign writes don't lock
    # article_index reads running alongside in the cron runner.
    from engine.index.sqlite_index import _ensure_wal_mode
    _ensure_wal_mode()
    with _connect() as conn:
        conn.executescript(SCHEMA_SQL)
    _SCHEMA_READY = True


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Typed row dataclasses (for helpers that want typed access)
# ---------------------------------------------------------------------------


Cadence = Literal["once", "weekly", "monthly"]
CampaignStatus = Literal["active", "paused", "archived"]
ArticleSelection = Literal["latest_home", "specific"]
SendStatus = Literal["sent", "preview", "failed", "skipped_stale", "skipped_dedup"]


@dataclass
class Campaign:
    id: str
    name: str
    created_by: str
    template_type: str
    target_company: str
    article_selection: str
    cadence: str
    status: str
    created_at: str
    updated_at: str
    article_id: str | None = None
    day_of_week: int | None = None
    day_of_month: int | None = None
    send_time_utc: str | None = None
    cta_url: str | None = None
    cta_label: str | None = None
    sender_note: str | None = None
    last_sent_at: str | None = None
    next_send_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class Recipient:
    id: str
    campaign_id: str
    email: str
    created_at: str
    name_override: str | None = None
    last_sent_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class SendLogEntry:
    id: str
    campaign_id: str
    recipient_email: str
    status: str
    sent_at: str
    article_id: str | None = None
    subject: str | None = None
    html_length: int | None = None
    provider_id: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


# ---------------------------------------------------------------------------
# Campaign CRUD
# ---------------------------------------------------------------------------


def create_campaign(
    *,
    name: str,
    created_by: str,
    target_company: str,
    article_selection: ArticleSelection,
    cadence: Cadence,
    next_send_at: str | None,
    template_type: str = "share_single",
    article_id: str | None = None,
    day_of_week: int | None = None,
    day_of_month: int | None = None,
    send_time_utc: str | None = None,
    cta_url: str | None = None,
    cta_label: str | None = None,
    sender_note: str | None = None,
    status: CampaignStatus = "active",
) -> Campaign:
    """Insert a new campaign. Returns the created row with a fresh UUID."""
    ensure_schema()
    cid = uuid.uuid4().hex
    now = _now()

    if article_selection == "specific" and not article_id:
        raise ValueError("article_id is required when article_selection='specific'")
    if article_selection not in ("latest_home", "specific"):
        raise ValueError(f"invalid article_selection: {article_selection!r}")
    if cadence not in ("once", "weekly", "monthly"):
        raise ValueError(f"invalid cadence: {cadence!r}")
    if status not in ("active", "paused", "archived"):
        raise ValueError(f"invalid status: {status!r}")
    if cadence == "weekly" and day_of_week is None:
        raise ValueError("day_of_week is required for weekly cadence")
    if cadence == "monthly" and day_of_month is None:
        raise ValueError("day_of_month is required for monthly cadence")
    if day_of_month is not None and not (1 <= day_of_month <= 28):
        raise ValueError("day_of_month must be 1..28 (29-31 unsupported)")
    if day_of_week is not None and not (0 <= day_of_week <= 6):
        raise ValueError("day_of_week must be 0..6 (Mon=0)")

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO campaigns (
                id, name, created_by, template_type, target_company,
                article_selection, article_id, cadence, day_of_week,
                day_of_month, send_time_utc, cta_url, cta_label, sender_note,
                status, last_sent_at, next_send_at, created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?
            )
            """,
            (
                cid, name, created_by, template_type, target_company,
                article_selection, article_id, cadence, day_of_week,
                day_of_month, send_time_utc, cta_url, cta_label, sender_note,
                status, next_send_at, now, now,
            ),
        )

    return Campaign(
        id=cid, name=name, created_by=created_by, template_type=template_type,
        target_company=target_company, article_selection=article_selection,
        article_id=article_id, cadence=cadence, day_of_week=day_of_week,
        day_of_month=day_of_month, send_time_utc=send_time_utc, cta_url=cta_url,
        cta_label=cta_label, sender_note=sender_note, status=status,
        last_sent_at=None, next_send_at=next_send_at, created_at=now, updated_at=now,
    )


def _row_to_campaign(row: sqlite3.Row) -> Campaign:
    return Campaign(**dict(row))


def get_campaign(campaign_id: str) -> Campaign | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        return _row_to_campaign(row) if row else None


def list_campaigns(status: CampaignStatus | None = None) -> list[Campaign]:
    ensure_schema()
    with _connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM campaigns WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM campaigns ORDER BY created_at DESC"
            ).fetchall()
        return [_row_to_campaign(r) for r in rows]


def list_due_campaigns(now: str | None = None) -> list[Campaign]:
    """Return active campaigns whose next_send_at has passed. Runner hot path."""
    ensure_schema()
    now = now or _now()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM campaigns
            WHERE status = 'active'
              AND next_send_at IS NOT NULL
              AND next_send_at <= ?
            ORDER BY next_send_at ASC
            """,
            (now,),
        ).fetchall()
        return [_row_to_campaign(r) for r in rows]


def update_campaign(campaign_id: str, **fields: Any) -> Campaign | None:
    """Partial update. Unknown fields are ignored. `updated_at` is bumped."""
    ensure_schema()
    allowed = {
        "name", "template_type", "target_company", "article_selection", "article_id",
        "cadence", "day_of_week", "day_of_month", "send_time_utc",
        "cta_url", "cta_label", "sender_note", "status",
        "last_sent_at", "next_send_at",
    }
    changes = {k: v for k, v in fields.items() if k in allowed}
    if not changes:
        return get_campaign(campaign_id)

    changes["updated_at"] = _now()
    assignments = ", ".join(f"{k} = :{k}" for k in changes.keys())
    params = {**changes, "id": campaign_id}

    with _connect() as conn:
        conn.execute(f"UPDATE campaigns SET {assignments} WHERE id = :id", params)
    return get_campaign(campaign_id)


def set_status(campaign_id: str, status: CampaignStatus) -> Campaign | None:
    if status not in ("active", "paused", "archived"):
        raise ValueError(f"invalid status: {status!r}")
    return update_campaign(campaign_id, status=status)


def mark_sent(campaign_id: str, last_sent_at: str, next_send_at: str | None) -> Campaign | None:
    """Bump `last_sent_at`/`next_send_at` after a successful runner pass."""
    return update_campaign(
        campaign_id, last_sent_at=last_sent_at, next_send_at=next_send_at,
    )


def delete_campaign(campaign_id: str) -> bool:
    """Hard-delete. Cascades recipients; send_log survives for audit."""
    ensure_schema()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Recipient CRUD
# ---------------------------------------------------------------------------


def replace_recipients(
    campaign_id: str,
    entries: list[tuple[str, str | None]],
) -> list[Recipient]:
    """Replace the full recipient list for a campaign.

    `entries` is a list of (email, name_override). Emails are lowercased +
    trimmed. Duplicates within the input are deduped. Returns the post-write
    recipient rows.
    """
    ensure_schema()
    now = _now()

    # Normalise + dedupe
    seen: set[str] = set()
    cleaned: list[tuple[str, str | None]] = []
    for email, override in entries:
        e = (email or "").strip().lower()
        if not e or e in seen:
            continue
        seen.add(e)
        cleaned.append((e, (override or None) if override else None))

    with _connect() as conn:
        conn.execute("DELETE FROM campaign_recipients WHERE campaign_id = ?", (campaign_id,))
        for email, override in cleaned:
            conn.execute(
                """
                INSERT INTO campaign_recipients (id, campaign_id, email, name_override, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, campaign_id, email, override, now),
            )

    return list_recipients(campaign_id)


def add_recipients(
    campaign_id: str,
    entries: list[tuple[str, str | None]],
) -> list[Recipient]:
    """Append recipients (ignoring duplicates by (campaign_id, email))."""
    ensure_schema()
    now = _now()
    with _connect() as conn:
        for email, override in entries:
            e = (email or "").strip().lower()
            if not e:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO campaign_recipients
                    (id, campaign_id, email, name_override, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, campaign_id, e, (override or None) if override else None, now),
            )
    return list_recipients(campaign_id)


def list_recipients(campaign_id: str) -> list[Recipient]:
    ensure_schema()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM campaign_recipients WHERE campaign_id = ? ORDER BY created_at ASC",
            (campaign_id,),
        ).fetchall()
        return [Recipient(**dict(r)) for r in rows]


def count_recipients(campaign_id: str) -> int:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM campaign_recipients WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        return int(row[0]) if row else 0


def touch_recipient_last_sent(campaign_id: str, email: str, sent_at: str) -> None:
    ensure_schema()
    with _connect() as conn:
        conn.execute(
            "UPDATE campaign_recipients SET last_sent_at = ? WHERE campaign_id = ? AND email = ?",
            (sent_at, campaign_id, email.lower().strip()),
        )


# ---------------------------------------------------------------------------
# Send log (append-only)
# ---------------------------------------------------------------------------


def append_send_log(
    *,
    campaign_id: str,
    recipient_email: str,
    status: SendStatus,
    article_id: str | None = None,
    subject: str | None = None,
    html_length: int | None = None,
    provider_id: str | None = None,
    error: str | None = None,
    sent_at: str | None = None,
) -> SendLogEntry:
    """Insert one send attempt. Always call this for every recipient the
    runner touches — including skipped_stale and skipped_dedup outcomes — so
    the admin UI can show a complete history."""
    ensure_schema()
    sid = uuid.uuid4().hex
    ts = sent_at or _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO campaign_send_log (
                id, campaign_id, recipient_email, article_id, subject,
                html_length, status, provider_id, error, sent_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sid, campaign_id, recipient_email.lower().strip(), article_id,
                subject, html_length, status, provider_id, error, ts,
            ),
        )
    return SendLogEntry(
        id=sid, campaign_id=campaign_id,
        recipient_email=recipient_email.lower().strip(),
        article_id=article_id, subject=subject, html_length=html_length,
        status=status, provider_id=provider_id, error=error, sent_at=ts,
    )


def list_send_log(
    campaign_id: str,
    limit: int = 50,
) -> list[SendLogEntry]:
    ensure_schema()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM campaign_send_log
            WHERE campaign_id = ?
            ORDER BY sent_at DESC
            LIMIT ?
            """,
            (campaign_id, max(1, min(limit, 500))),
        ).fetchall()
        return [SendLogEntry(**dict(r)) for r in rows]


def find_recent_send(
    campaign_id: str,
    recipient_email: str,
    article_id: str | None,
    since_iso: str,
) -> SendLogEntry | None:
    """Dedup probe — returns the most recent send-log row matching the key
    that is newer than `since_iso`, else None. Used by the runner to skip
    double-sending when cron retries within the same cadence window."""
    ensure_schema()
    sql = """
        SELECT * FROM campaign_send_log
        WHERE campaign_id = ?
          AND recipient_email = ?
          AND COALESCE(article_id, '') = COALESCE(?, '')
          AND sent_at > ?
          AND status IN ('sent', 'preview')
        ORDER BY sent_at DESC
        LIMIT 1
    """
    with _connect() as conn:
        row = conn.execute(
            sql,
            (campaign_id, recipient_email.lower().strip(), article_id, since_iso),
        ).fetchone()
        return SendLogEntry(**dict(row)) if row else None


# ---------------------------------------------------------------------------
# Test utility (used only by tests + CLI teardown)
# ---------------------------------------------------------------------------


def _truncate_all() -> None:
    """Wipe every Phase 10 table. NEVER call from production code paths."""
    ensure_schema()
    with _connect() as conn:
        conn.execute("DELETE FROM campaign_send_log")
        conn.execute("DELETE FROM campaign_recipients")
        conn.execute("DELETE FROM campaigns")
