"""Phase 11B — Track the async onboarding pipeline for any new tenant.

When an admin onboards a new company via POST /api/admin/onboard, three
things happen in sequence in a background task:

  1. `fetch_for_company(slug, limit=10)` — pull ~10 ESG-filtered articles
     via NewsAPI.ai / Google News for the target company.
  2. For each fetched article → `process_article()` → 12-stage pipeline.
  3. Ready — dashboard returns real analysed insights at `/home?company=<slug>`.

This table records progress so the frontend modal can poll and show
("Fetching 10 articles…" → "Analysing 3/10…" → "Ready"). Errors bubble up
into `error` so the admin knows what broke without reading logs.

Schema:
    onboarding_status(
      slug         TEXT PRIMARY KEY,
      state        TEXT NOT NULL,    -- 'pending'|'fetching'|'analysing'|'ready'|'failed'
      fetched      INTEGER DEFAULT 0,
      analysed     INTEGER DEFAULT 0,
      home_count   INTEGER DEFAULT 0,
      started_at   TEXT NOT NULL,
      finished_at  TEXT,
      error        TEXT
    )
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator, Literal

from engine.index.sqlite_index import DB_PATH, _ensure_wal_mode

logger = logging.getLogger(__name__)

OnboardState = Literal["pending", "fetching", "analysing", "ready", "failed"]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS onboarding_status (
    slug        TEXT PRIMARY KEY,
    state       TEXT NOT NULL,
    fetched     INTEGER DEFAULT 0,
    analysed    INTEGER DEFAULT 0,
    home_count  INTEGER DEFAULT 0,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    error       TEXT
);
"""

_SCHEMA_READY = False


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


@dataclass
class OnboardingStatus:
    slug: str
    state: str
    fetched: int
    analysed: int
    home_count: int
    started_at: str
    finished_at: str | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "state": self.state,
            "fetched": self.fetched,
            "analysed": self.analysed,
            "home_count": self.home_count,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def upsert(slug: str, **fields: Any) -> OnboardingStatus:
    """Create or update the row. Always updates whatever fields are passed.
    State transitions: pending → fetching → analysing → ready | failed.

    Phase 21 — when transitioning to a non-failed state without an explicit
    `error=` argument, clear any stale error from a previous attempt. Bug
    surfaced 2026-04-29: an alias slug retained the failed-attempt error
    after a successful retry, making the onboarding modal show "Failed"
    when the canonical pipeline had succeeded.
    """
    ensure_schema()
    allowed = {"state", "fetched", "analysed", "home_count", "finished_at", "error"}
    payload = {k: v for k, v in fields.items() if k in allowed}
    # Clear stale error when transitioning to a non-failed state and the
    # caller hasn't explicitly set the error field.
    new_state = payload.get("state")
    if new_state and new_state != "failed" and "error" not in payload:
        payload["error"] = None

    with _connect() as conn:
        existing = conn.execute(
            "SELECT slug FROM onboarding_status WHERE slug = ?", (slug,)
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO onboarding_status (slug, state, fetched, analysed, home_count, started_at, finished_at, error)
                VALUES (:slug, :state, :fetched, :analysed, :home_count, :started_at, :finished_at, :error)
                """,
                {
                    "slug": slug,
                    "state": payload.get("state", "pending"),
                    "fetched": payload.get("fetched", 0),
                    "analysed": payload.get("analysed", 0),
                    "home_count": payload.get("home_count", 0),
                    "started_at": _now(),
                    "finished_at": payload.get("finished_at"),
                    "error": payload.get("error"),
                },
            )
        else:
            if not payload:
                return get(slug)  # type: ignore[return-value]
            assignments = ", ".join(f"{k} = :{k}" for k in payload.keys())
            conn.execute(
                f"UPDATE onboarding_status SET {assignments} WHERE slug = :slug",
                {**payload, "slug": slug},
            )

    return get(slug)  # type: ignore[return-value]


def get(slug: str) -> OnboardingStatus | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM onboarding_status WHERE slug = ?", (slug,)
        ).fetchone()
        return OnboardingStatus(**dict(row)) if row else None


def mark_failed(slug: str, error: str) -> None:
    upsert(slug, state="failed", error=error[:500], finished_at=_now())


def mark_ready(slug: str, *, fetched: int | None = None, analysed: int | None = None,
               home_count: int | None = None) -> None:
    """Mark a slug as ready. Phase 21 — accept stats so an alias slug can
    mirror the canonical row's progress when the onboarder adjusts slugs
    (e.g. "tatachemicals" → "tata-chemicals-limited").
    """
    extras: dict[str, Any] = {}
    if fetched is not None:
        extras["fetched"] = fetched
    if analysed is not None:
        extras["analysed"] = analysed
    if home_count is not None:
        extras["home_count"] = home_count
    upsert(slug, state="ready", finished_at=_now(), **extras)


def _truncate_all() -> None:
    ensure_schema()
    with _connect() as conn:
        conn.execute("DELETE FROM onboarding_status")
