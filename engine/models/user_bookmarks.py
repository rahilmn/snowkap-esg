"""Phase 34.7 — Personal Wiki: server-side article bookmarks.

CRUD helpers over the `user_bookmarks` table (migration 010). Replaces
the legacy Zustand `savedStore` (localStorage-only) with a per-(user,
article) server-side store so bookmarks survive device + browser
changes. Identity is the JWT `sub` claim email.

Public surface:
  * add(user_email, article_id, note=None, section='pinned') → BookmarkRow
  * remove(user_email, article_id) → bool
  * list_for_user(user_email, section=None) → list[BookmarkRow]
  * update_note(user_email, article_id, note) → bool
  * update_section(user_email, article_id, section) → bool
  * bulk_add(user_email, articles) → int  (count inserted; idempotent)
  * count_for_user(user_email) → int
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from engine.db import connect as _db_connect

logger = logging.getLogger(__name__)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS user_bookmarks (
    user_email    TEXT NOT NULL,
    article_id    TEXT NOT NULL,
    note          TEXT,
    section       TEXT NOT NULL DEFAULT 'pinned',
    bookmarked_at TEXT NOT NULL,
    PRIMARY KEY (user_email, article_id)
);
CREATE INDEX IF NOT EXISTS idx_user_bookmarks_user
    ON user_bookmarks(user_email, bookmarked_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_bookmarks_section
    ON user_bookmarks(user_email, section);
"""

ALLOWED_SECTIONS = {"pinned", "climate", "capital", "social", "custom"}

_SCHEMA_READY = False


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _db_connect() as conn:
        conn.executescript(_SCHEMA_SQL)
    _SCHEMA_READY = True


@dataclass
class BookmarkRow:
    user_email: str
    article_id: str
    note: str | None
    section: str
    bookmarked_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_email": self.user_email,
            "article_id": self.article_id,
            "note": self.note,
            "section": self.section,
            "bookmarked_at": self.bookmarked_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalise_section(section: str | None) -> str:
    if not section:
        return "pinned"
    s = section.strip().lower()
    return s if s in ALLOWED_SECTIONS else "custom"


def _row_to_bookmark(row: Any) -> BookmarkRow:
    if hasattr(row, "keys"):
        return BookmarkRow(
            user_email=row["user_email"],
            article_id=row["article_id"],
            note=row["note"],
            section=row["section"],
            bookmarked_at=row["bookmarked_at"],
        )
    return BookmarkRow(
        user_email=row[0],
        article_id=row[1],
        note=row[2],
        section=row[3],
        bookmarked_at=row[4],
    )


def add(
    *,
    user_email: str,
    article_id: str,
    note: str | None = None,
    section: str | None = "pinned",
) -> BookmarkRow:
    """Insert or update a bookmark for (user_email, article_id). Idempotent."""
    ensure_schema()
    if not user_email or "@" not in user_email:
        raise ValueError("user_email must be a real email")
    if not article_id:
        raise ValueError("article_id required")

    sec = _normalise_section(section)
    now = _now()
    clean_note = (note or "").strip() or None

    with _db_connect() as conn:
        existing = conn.execute(
            "SELECT bookmarked_at FROM user_bookmarks WHERE user_email = ? AND article_id = ?",
            (user_email, article_id),
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO user_bookmarks (user_email, article_id, note, section, bookmarked_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_email, article_id, clean_note, sec, now),
            )
            bookmarked_at = now
        else:
            conn.execute(
                "UPDATE user_bookmarks SET note = ?, section = ? "
                "WHERE user_email = ? AND article_id = ?",
                (clean_note, sec, user_email, article_id),
            )
            bookmarked_at = existing[0] if not hasattr(existing, "keys") else existing["bookmarked_at"]

    return BookmarkRow(
        user_email=user_email,
        article_id=article_id,
        note=clean_note,
        section=sec,
        bookmarked_at=bookmarked_at,
    )


def remove(user_email: str, article_id: str) -> bool:
    """Delete the bookmark row. Returns True when a row was deleted."""
    ensure_schema()
    with _db_connect() as conn:
        cur = conn.execute(
            "DELETE FROM user_bookmarks WHERE user_email = ? AND article_id = ?",
            (user_email, article_id),
        )
        return (cur.rowcount or 0) > 0


def list_for_user(user_email: str, section: str | None = None) -> list[BookmarkRow]:
    """Return the user's bookmarks, newest-first. Optional section filter."""
    ensure_schema()
    with _db_connect() as conn:
        if section:
            sql = (
                "SELECT user_email, article_id, note, section, bookmarked_at "
                "FROM user_bookmarks WHERE user_email = ? AND section = ? "
                "ORDER BY bookmarked_at DESC"
            )
            rows = conn.execute(sql, (user_email, section)).fetchall()
        else:
            sql = (
                "SELECT user_email, article_id, note, section, bookmarked_at "
                "FROM user_bookmarks WHERE user_email = ? "
                "ORDER BY bookmarked_at DESC"
            )
            rows = conn.execute(sql, (user_email,)).fetchall()
    return [_row_to_bookmark(r) for r in rows]


def update_note(user_email: str, article_id: str, note: str | None) -> bool:
    """Update the note field on an existing bookmark."""
    ensure_schema()
    clean_note = (note or "").strip() or None
    with _db_connect() as conn:
        cur = conn.execute(
            "UPDATE user_bookmarks SET note = ? WHERE user_email = ? AND article_id = ?",
            (clean_note, user_email, article_id),
        )
        return (cur.rowcount or 0) > 0


def update_section(user_email: str, article_id: str, section: str) -> bool:
    """Move a bookmark to a different section."""
    ensure_schema()
    sec = _normalise_section(section)
    with _db_connect() as conn:
        cur = conn.execute(
            "UPDATE user_bookmarks SET section = ? WHERE user_email = ? AND article_id = ?",
            (sec, user_email, article_id),
        )
        return (cur.rowcount or 0) > 0


def bulk_add(user_email: str, articles: Iterable[dict[str, Any]]) -> int:
    """Idempotent bulk insert. `articles` is a list of dicts with at least
    `article_id` and optional `note` + `section`. Returns the count of
    rows actually inserted (existing rows are left alone)."""
    ensure_schema()
    inserted = 0
    for entry in articles:
        article_id = entry.get("article_id") or entry.get("id")
        if not article_id:
            continue
        sec = _normalise_section(entry.get("section"))
        note = entry.get("note")
        clean_note = (note or "").strip() or None if isinstance(note, str) else None
        with _db_connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM user_bookmarks WHERE user_email = ? AND article_id = ?",
                (user_email, article_id),
            ).fetchone()
            if existing is not None:
                continue
            conn.execute(
                "INSERT INTO user_bookmarks (user_email, article_id, note, section, bookmarked_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_email, article_id, clean_note, sec, _now()),
            )
            inserted += 1
    return inserted


def count_for_user(user_email: str) -> int:
    ensure_schema()
    with _db_connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM user_bookmarks WHERE user_email = ?",
            (user_email,),
        ).fetchone()
        return int(row[0] if not hasattr(row, "keys") else row[0])


def is_bookmarked(user_email: str, article_id: str) -> bool:
    ensure_schema()
    with _db_connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM user_bookmarks WHERE user_email = ? AND article_id = ?",
            (user_email, article_id),
        ).fetchone()
        return row is not None
