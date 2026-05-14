"""Phase 24 (W4) — analyst session state.

Persists per-user analyst workflow state so a returning user resumes
where they left off (which company they're reviewing, which perspective
is active, which insights they marked for follow-up).

The point isn't a chatbot — it's a stateful workflow companion. A
power user opening the dashboard at 9am should see "Resume monthly
review · Adani Power · CFO view · 3 follow-ups queued" instead of a
generic feed.

Two state buckets:

  * **phase** — macro context: ``monthly_review`` | ``ad_hoc_lookup`` |
    ``onboarding_new_company``. Drives banner copy + nav defaults.
  * **activity** — micro context: which insight is open RIGHT NOW
    (current_action, target_id, started_at). Used by `/snowkap-status`
    skill + the session_start hook to compose the 80-line context banner.

Plus a **follow_up_queue** — list of insights the analyst marked
"come back to this", with a reason. Read by `/snowkap-memory` to surface
unfinished work across sessions.

Backend
-------
Uses ``engine.db.connect()`` so it works on:

  * SQLite default (local dev)
  * Supabase / Postgres when ``SNOWKAP_DB_BACKEND=postgres`` is set
    (``SUPABASE_DATABASE_URL`` from .env)

Both backends store ``activity`` and ``follow_up_queue`` as JSON-encoded
TEXT — the dialect translator handles the placeholder rewriting and
type coercion. Postgres-native JSONB is a future optimisation; the
read/write path is small enough that text encoding is fine for now.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

from engine.db import connect as _db_connect
from engine.index.sqlite_index import DB_PATH, _ensure_wal_mode  # noqa: F401

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

# `activity` and `follow_up_queue` are JSON-encoded TEXT in both backends
# for portability. SQLite has no native JSONB; Postgres has both JSON and
# JSONB but the dialect translator handles TEXT identically.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS analyst_session_state (
    user_id              TEXT PRIMARY KEY,
    phase                TEXT,
    active_company_slug  TEXT,
    active_perspective   TEXT,
    activity             TEXT,
    follow_up_queue      TEXT,
    updated_at           TEXT NOT NULL
);
"""

_SCHEMA_READY = False


@contextmanager
def _connect() -> Iterator[Any]:
    """Backend-aware connection (SQLite default, Postgres if configured)."""
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


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class AnalystSession:
    """Per-user analyst workflow state."""
    user_id: str
    phase: str | None = None
    active_company_slug: str | None = None
    active_perspective: str | None = None
    activity: dict[str, Any] = field(default_factory=dict)
    follow_up_queue: list[dict[str, Any]] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "phase": self.phase,
            "active_company_slug": self.active_company_slug,
            "active_perspective": self.active_perspective,
            "activity": self.activity,
            "follow_up_queue": self.follow_up_queue,
            "updated_at": self.updated_at,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row_to_session(row: Any) -> AnalystSession:
    """Convert a DB row to an AnalystSession instance."""
    d = dict(row) if not isinstance(row, dict) else row

    def _decode_json(value: Any, fallback: Any) -> Any:
        if value in (None, ""):
            return fallback
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError) as exc:
            logger.warning("analyst_session: bad JSON in %r — %s", value, exc)
            return fallback

    return AnalystSession(
        user_id=d.get("user_id") or "",
        phase=d.get("phase"),
        active_company_slug=d.get("active_company_slug"),
        active_perspective=d.get("active_perspective"),
        activity=_decode_json(d.get("activity"), {}),
        follow_up_queue=_decode_json(d.get("follow_up_queue"), []),
        updated_at=d.get("updated_at") or "",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get(user_id: str) -> AnalystSession | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM analyst_session_state WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return _row_to_session(row)


def upsert(user_id: str, **fields: Any) -> AnalystSession:
    """Create or update the analyst session row.

    Any of these may be passed; unset fields are left alone:
      * phase (str)
      * active_company_slug (str)
      * active_perspective (str)
      * activity (dict)
      * follow_up_queue (list[dict])

    Always touches ``updated_at``.
    """
    ensure_schema()
    allowed = {
        "phase",
        "active_company_slug",
        "active_perspective",
        "activity",
        "follow_up_queue",
    }
    payload: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in allowed:
            continue
        # JSON-encode the dict / list fields
        if key in {"activity", "follow_up_queue"} and value is not None:
            payload[key] = json.dumps(value, ensure_ascii=False, default=str)
        else:
            payload[key] = value

    with _connect() as conn:
        existing = conn.execute(
            "SELECT user_id FROM analyst_session_state WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO analyst_session_state (
                    user_id, phase, active_company_slug, active_perspective,
                    activity, follow_up_queue, updated_at
                ) VALUES (
                    :user_id, :phase, :active_company_slug, :active_perspective,
                    :activity, :follow_up_queue, :updated_at
                )
                """,
                {
                    "user_id": user_id,
                    "phase": payload.get("phase"),
                    "active_company_slug": payload.get("active_company_slug"),
                    "active_perspective": payload.get("active_perspective"),
                    "activity": payload.get("activity") or json.dumps({}),
                    "follow_up_queue": payload.get("follow_up_queue") or json.dumps([]),
                    "updated_at": _now(),
                },
            )
        else:
            if not payload:
                # Touch updated_at only
                conn.execute(
                    "UPDATE analyst_session_state SET updated_at = :ts "
                    "WHERE user_id = :user_id",
                    {"ts": _now(), "user_id": user_id},
                )
            else:
                assignments = ", ".join(f"{k} = :{k}" for k in payload.keys())
                conn.execute(
                    f"UPDATE analyst_session_state SET {assignments}, "
                    f"updated_at = :updated_at WHERE user_id = :user_id",
                    {**payload, "updated_at": _now(), "user_id": user_id},
                )

    return get(user_id)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Follow-up queue helpers
# ---------------------------------------------------------------------------


MAX_FOLLOW_UP = 50


def append_follow_up(
    user_id: str,
    insight_id: str,
    reason: str | None = None,
    *,
    company_slug: str | None = None,
) -> AnalystSession:
    """Add an insight to the user's follow-up queue.

    Idempotent: re-adding the same insight_id refreshes the
    ``marked_at`` timestamp instead of duplicating the entry. The queue
    is capped at ``MAX_FOLLOW_UP`` — oldest entries are evicted.
    """
    session = get(user_id) or AnalystSession(user_id=user_id)
    queue = list(session.follow_up_queue)
    # Drop existing entry for this insight so we re-add at head
    queue = [e for e in queue if e.get("insight_id") != insight_id]
    queue.insert(0, {
        "insight_id": insight_id,
        "reason": reason or "",
        "company_slug": company_slug or "",
        "marked_at": _now(),
    })
    queue = queue[:MAX_FOLLOW_UP]
    return upsert(user_id, follow_up_queue=queue)


def remove_follow_up(user_id: str, insight_id: str) -> AnalystSession:
    """Remove an insight from the follow-up queue."""
    session = get(user_id) or AnalystSession(user_id=user_id)
    queue = [e for e in session.follow_up_queue if e.get("insight_id") != insight_id]
    return upsert(user_id, follow_up_queue=queue)
