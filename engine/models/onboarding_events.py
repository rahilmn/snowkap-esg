"""Phase 28 — Onboarding progress events.

The worker emits one row per stage transition (``onboard_started``,
``company_profile_ready``, ``news_fetch_started``, ``news_fetch_done``,
``critical_3_selected``, ``analysis_started``, ``analysis_done``,
``onboard_complete``, ``onboard_failed``). The SSE endpoint at
``GET /api/me/onboard/{slug}/stream`` tails this table to push events
into the frontend skeleton-fill UI without a refresh.

Backed by SQLite (or Postgres) so multi-worker deployments and the
poll-based SSE consumer see a consistent stream. Events are write-once
+ append-only; the only mutation is the auto-incremented ``seq``
ordering key.

Schema:
    onboarding_events(
        seq      INTEGER PRIMARY KEY AUTOINCREMENT,
        slug     TEXT NOT NULL,
        ts       TEXT NOT NULL,
        kind     TEXT NOT NULL,
        payload  TEXT                  -- JSON-serialised dict
    )
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from engine.db import connect as _db_connect, schema_ready, mark_schema_ready
from engine.index.sqlite_index import DB_PATH, _ensure_wal_mode  # noqa: F401

logger = logging.getLogger(__name__)

# Stage-transition vocabulary. Frontend useChatStream-style state machine
# treats unknown kinds as no-op so the backend can add new ones without
# breaking older clients.
EVENT_KINDS = frozenset({
    "onboard_started",
    "company_profile_ready",
    "news_fetch_started",
    "news_fetch_done",
    # Phase 36 — body-capture guarantee stage. Fires between
    # news_fetch_done and critical_3_selected; payload carries
    # {candidates_checked, bodies_added, already_grounded, paywalled_skipped}.
    "full_text_capture_done",
    "critical_3_selected",
    "analysis_started",
    "analysis_done",
    "onboard_complete",
    "onboard_failed",
})

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS onboarding_events (
    seq      INTEGER PRIMARY KEY AUTOINCREMENT,
    slug     TEXT NOT NULL,
    ts       TEXT NOT NULL,
    kind     TEXT NOT NULL,
    payload  TEXT
);
CREATE INDEX IF NOT EXISTS idx_onboarding_events_slug_seq
    ON onboarding_events(slug, seq);
"""

@contextmanager
def _connect() -> Iterator[Any]:
    with _db_connect() as conn:
        yield conn


def ensure_schema() -> None:
    if schema_ready("onboarding_events"):
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ensure_wal_mode()
    with _connect() as conn:
        conn.executescript(SCHEMA_SQL)
    mark_schema_ready("onboarding_events")


@dataclass
class OnboardingEvent:
    seq: int
    slug: str
    ts: str
    kind: str
    payload: dict[str, Any]

    def to_sse_dict(self) -> dict[str, Any]:
        """Shape consumed by the React useEventStream hook."""
        return {
            "seq": self.seq,
            "ts": self.ts,
            "kind": self.kind,
            "payload": self.payload,
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def emit_event(slug: str, kind: str, payload: dict[str, Any] | None = None) -> int:
    """Append one event to the stream. Returns the assigned ``seq``.

    Never raises — onboarding progress events are non-load-bearing
    metadata; a failure here must NOT halt the worker. Falls through to
    the caller after a warning log.
    """
    if kind not in EVENT_KINDS:
        logger.warning("emit_event: unknown kind=%r (allowed: %s)", kind, sorted(EVENT_KINDS))
        return -1
    payload_json = json.dumps(payload or {}, sort_keys=True, default=str)
    try:
        ensure_schema()
        with _connect() as conn:
            cur = conn.execute(
                "INSERT INTO onboarding_events (slug, ts, kind, payload) "
                "VALUES (?, ?, ?, ?)",
                (slug, _now(), kind, payload_json),
            )
            seq = int(cur.lastrowid or 0)
        return seq
    except Exception as exc:  # noqa: BLE001
        logger.warning("emit_event failed (slug=%s kind=%s): %s", slug, kind, exc)
        return -1


def list_since(slug: str, *, after_seq: int = 0, limit: int = 100) -> list[OnboardingEvent]:
    """Read events for a slug whose seq > after_seq. SSE consumer uses
    this with the last-seen seq to deliver only new events on each poll.

    Returns events in ascending seq order. ``limit`` defaults to 100
    (more than enough — the full onboarding pipeline emits ~10 events).
    """
    ensure_schema()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT seq, slug, ts, kind, payload FROM onboarding_events "
            "WHERE slug = ? AND seq > ? ORDER BY seq ASC LIMIT ?",
            (slug, after_seq, limit),
        ).fetchall()
    out: list[OnboardingEvent] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"]) if row["payload"] else {}
        except (TypeError, ValueError):
            payload = {}
        out.append(OnboardingEvent(
            seq=row["seq"], slug=row["slug"], ts=row["ts"],
            kind=row["kind"], payload=payload,
        ))
    return out


def is_terminal(events: list[OnboardingEvent]) -> bool:
    """True iff any event in the list is a terminal state.

    Terminal kinds: ``onboard_complete`` (success) and
    ``onboard_failed`` (terminal failure). The SSE endpoint uses this
    to close the stream cleanly once the worker is done.
    """
    return any(e.kind in {"onboard_complete", "onboard_failed"} for e in events)


def _truncate_for_slug(slug: str) -> None:
    """Test/wipe helper. Removes ALL events for a given slug."""
    ensure_schema()
    with _connect() as conn:
        conn.execute("DELETE FROM onboarding_events WHERE slug = ?", (slug,))


def _truncate_all() -> None:
    """Wipe helper used by scripts/wipe_clean_slate.py."""
    ensure_schema()
    with _connect() as conn:
        conn.execute("DELETE FROM onboarding_events")
