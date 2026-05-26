"""Phase 34.6 — Forum threads + replies model.

CRUD helpers over `forum_threads` + `forum_thread_replies` tables
(migration 009). Backend-agnostic (SQLite dev, Postgres prod via the
existing `engine.db.connect` dispatcher).

Identity = JWT `sub` claim email. Author-only soft-delete via
`deleted_at`. Threads tagged with one of the ALLOWED_TAGS so the UI
can render fixed tag-filter chips.

Public surface:
  * create_thread(title, body, tag, author_email, author_name) → ThreadRow
  * list_threads(tag=None, limit=50) → list[ThreadRow] (newest-first, pinned-first)
  * get_thread(thread_id) → ThreadRow | None  (returns deleted threads too — caller filters)
  * soft_delete_thread(thread_id, requester_email) → bool (author-only)
  * pin_thread(thread_id, requester_email, pinned) → bool (author-only)
  * add_reply(thread_id, body, author_email, author_name) → ReplyRow
  * list_replies(thread_id) → list[ReplyRow]
  * soft_delete_reply(reply_id, requester_email) → bool
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from engine.db import connect as _db_connect

logger = logging.getLogger(__name__)

ALLOWED_TAGS = ("BRSR", "Climate", "CBAM", "Governance", "Audit")


# ─── Forum v1.1 — onboarding-time seed thread library ──────────────────────
# 5 starter threads (one per tag) authored by Snowkap, pinned=1 so they
# sort to the top of every reader's deck. Static copy — no LLM cost.
# Renders cleanly on mobile at ~80 words each.

SEED_AUTHOR_EMAIL = "snowkap-team@snowkap.com"
SEED_AUTHOR_NAME = "Snowkap"

_SEED_THREAD_LIBRARY: dict[str, dict[str, str]] = {
    "BRSR": {
        "title": "Welcome — what BRSR cycle are you on right now?",
        "body": (
            "Snowkap pulls every BRSR-relevant article into your /now deck. "
            "Drop your team's biggest BRSR-FY26 blocker below — Principle 6 "
            "disclosures, Principle 7 stakeholder engagement, or something "
            "else? We'll surface peer answers in your feed."
        ),
    },
    "Climate": {
        "title": "Welcome — what's your single biggest Scope 3 unknown?",
        "body": (
            "Climate is the heaviest theme in our ontology. Whether you're "
            "modelling financed emissions, supplier carbon disclosure, or "
            "transition finance — share the one number your finance team "
            "still hand-waves. Snowkap can suggest comparable peers."
        ),
    },
    "CBAM": {
        "title": "Welcome — are you exporting to the EU?",
        "body": (
            "CBAM transition phase started Oct 2023; mandatory reporting "
            "kicks in 2026. Drop your sector + EU exposure share and we'll "
            "route the next CBAM-relevant article straight to your desk."
        ),
    },
    "Governance": {
        "title": "Welcome — what's your board's ESG cadence?",
        "body": (
            "Most CFOs we work with say their board hears ESG once a year — "
            "too late. Share your current cadence (quarterly / annual / "
            "ad-hoc) and one governance KPI your board actually tracks."
        ),
    },
    "Audit": {
        "title": "Welcome — what's your assurance scope this year?",
        "body": (
            "Limited assurance vs reasonable assurance, AA1000 vs ISAE3000 — "
            "the auditor market is fragmenting. Share who's auditing your "
            "ESG report this year and what scope they're covering. Peer "
            "benchmarks live in your /wiki."
        ),
    },
}

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS forum_threads (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    body         TEXT NOT NULL,
    tag          TEXT NOT NULL,
    author_email TEXT NOT NULL,
    author_name  TEXT NOT NULL,
    pinned       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    deleted_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_forum_threads_tag
    ON forum_threads(tag, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_forum_threads_author
    ON forum_threads(author_email);
CREATE TABLE IF NOT EXISTS forum_thread_replies (
    id           TEXT PRIMARY KEY,
    thread_id    TEXT NOT NULL,
    author_email TEXT NOT NULL,
    author_name  TEXT NOT NULL,
    body         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    deleted_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_forum_thread_replies_thread
    ON forum_thread_replies(thread_id, created_at ASC);
"""

_SCHEMA_READY = False


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _db_connect() as conn:
        conn.executescript(_SCHEMA_SQL)
    _SCHEMA_READY = True


@dataclass
class ThreadRow:
    id: str
    title: str
    body: str
    tag: str
    author_email: str
    author_name: str
    pinned: bool
    created_at: str
    deleted_at: str | None = None
    reply_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title if not self.deleted_at else "[deleted by author]",
            "body": self.body if not self.deleted_at else "[deleted]",
            "tag": self.tag,
            "author_email": self.author_email,
            "author_name": self.author_name,
            "pinned": self.pinned,
            "created_at": self.created_at,
            "deleted_at": self.deleted_at,
            "reply_count": self.reply_count,
        }


@dataclass
class ReplyRow:
    id: str
    thread_id: str
    author_email: str
    author_name: str
    body: str
    created_at: str
    deleted_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "author_email": self.author_email,
            "author_name": self.author_name,
            "body": self.body if not self.deleted_at else "[deleted by author]",
            "created_at": self.created_at,
            "deleted_at": self.deleted_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalise_tag(tag: str) -> str:
    if not tag:
        raise ValueError("tag is required")
    t = tag.strip()
    # Case-insensitive whitelist
    for allowed in ALLOWED_TAGS:
        if allowed.lower() == t.lower():
            return allowed
    raise ValueError(f"tag must be one of {ALLOWED_TAGS}")


def _row_to_thread(row: Any, reply_count: int = 0) -> ThreadRow:
    if hasattr(row, "keys"):
        return ThreadRow(
            id=row["id"], title=row["title"], body=row["body"], tag=row["tag"],
            author_email=row["author_email"], author_name=row["author_name"],
            pinned=bool(row["pinned"]),
            created_at=row["created_at"], deleted_at=row["deleted_at"],
            reply_count=reply_count,
        )
    return ThreadRow(
        id=row[0], title=row[1], body=row[2], tag=row[3],
        author_email=row[4], author_name=row[5],
        pinned=bool(row[6]),
        created_at=row[7], deleted_at=row[8],
        reply_count=reply_count,
    )


def _row_to_reply(row: Any) -> ReplyRow:
    if hasattr(row, "keys"):
        return ReplyRow(
            id=row["id"], thread_id=row["thread_id"],
            author_email=row["author_email"], author_name=row["author_name"],
            body=row["body"], created_at=row["created_at"],
            deleted_at=row["deleted_at"],
        )
    return ReplyRow(
        id=row[0], thread_id=row[1],
        author_email=row[2], author_name=row[3],
        body=row[4], created_at=row[5], deleted_at=row[6],
    )


def create_thread(
    *,
    title: str,
    body: str,
    tag: str,
    author_email: str,
    author_name: str,
) -> ThreadRow:
    ensure_schema()
    if not title or not title.strip():
        raise ValueError("title cannot be empty")
    if not body or not body.strip():
        raise ValueError("body cannot be empty")
    if not author_email or "@" not in author_email:
        raise ValueError("author_email must be a real email")
    norm_tag = _normalise_tag(tag)

    tid = uuid.uuid4().hex
    created_at = _now()
    with _db_connect() as conn:
        conn.execute(
            "INSERT INTO forum_threads (id, title, body, tag, author_email, author_name, pinned, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            (tid, title.strip(), body.strip(), norm_tag, author_email, author_name, created_at),
        )
    return ThreadRow(
        id=tid, title=title.strip(), body=body.strip(), tag=norm_tag,
        author_email=author_email, author_name=author_name, pinned=False,
        created_at=created_at,
    )


def list_threads(tag: str | None = None, limit: int = 50) -> list[ThreadRow]:
    ensure_schema()
    with _db_connect() as conn:
        if tag:
            norm_tag = _normalise_tag(tag)
            sql = (
                "SELECT id, title, body, tag, author_email, author_name, pinned, created_at, deleted_at "
                "FROM forum_threads WHERE tag = ? AND deleted_at IS NULL "
                "ORDER BY pinned DESC, created_at DESC LIMIT ?"
            )
            rows = conn.execute(sql, (norm_tag, limit)).fetchall()
        else:
            sql = (
                "SELECT id, title, body, tag, author_email, author_name, pinned, created_at, deleted_at "
                "FROM forum_threads WHERE deleted_at IS NULL "
                "ORDER BY pinned DESC, created_at DESC LIMIT ?"
            )
            rows = conn.execute(sql, (limit,)).fetchall()
        threads = [_row_to_thread(r) for r in rows]
        # Bulk-fetch reply counts in one round-trip
        if threads:
            ids = tuple(t.id for t in threads)
            placeholders = ",".join("?" for _ in ids)
            counts_sql = (
                f"SELECT thread_id, COUNT(*) FROM forum_thread_replies "
                f"WHERE thread_id IN ({placeholders}) AND deleted_at IS NULL "
                f"GROUP BY thread_id"
            )
            count_rows = conn.execute(counts_sql, ids).fetchall()
            count_map: dict[str, int] = {}
            for cr in count_rows:
                tid = cr[0] if not hasattr(cr, "keys") else cr["thread_id"]
                n = cr[1] if not hasattr(cr, "keys") else cr[1]
                count_map[tid] = int(n)
            for t in threads:
                t.reply_count = count_map.get(t.id, 0)
    return threads


def get_thread(thread_id: str) -> ThreadRow | None:
    ensure_schema()
    with _db_connect() as conn:
        row = conn.execute(
            "SELECT id, title, body, tag, author_email, author_name, pinned, created_at, deleted_at "
            "FROM forum_threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        if row is None:
            return None
        thread = _row_to_thread(row)
        count_row = conn.execute(
            "SELECT COUNT(*) FROM forum_thread_replies WHERE thread_id = ? AND deleted_at IS NULL",
            (thread_id,),
        ).fetchone()
        thread.reply_count = int(count_row[0] if not hasattr(count_row, "keys") else count_row[0])
    return thread


def soft_delete_thread(thread_id: str, requester_email: str) -> bool:
    ensure_schema()
    with _db_connect() as conn:
        row = conn.execute(
            "SELECT author_email, deleted_at FROM forum_threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        if row is None:
            return False
        author = row[0] if not hasattr(row, "keys") else row["author_email"]
        deleted_at = row[1] if not hasattr(row, "keys") else row["deleted_at"]
        if author != requester_email:
            return False
        if deleted_at:
            return True
        conn.execute(
            "UPDATE forum_threads SET deleted_at = ? WHERE id = ?",
            (_now(), thread_id),
        )
    return True


def pin_thread(thread_id: str, requester_email: str, pinned: bool) -> bool:
    ensure_schema()
    with _db_connect() as conn:
        row = conn.execute(
            "SELECT author_email FROM forum_threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        if row is None:
            return False
        author = row[0] if not hasattr(row, "keys") else row["author_email"]
        if author != requester_email:
            return False
        conn.execute(
            "UPDATE forum_threads SET pinned = ? WHERE id = ?",
            (1 if pinned else 0, thread_id),
        )
    return True


def add_reply(
    *,
    thread_id: str,
    body: str,
    author_email: str,
    author_name: str,
) -> ReplyRow:
    ensure_schema()
    if not body or not body.strip():
        raise ValueError("body cannot be empty")
    if not author_email or "@" not in author_email:
        raise ValueError("author_email must be a real email")

    rid = uuid.uuid4().hex
    created_at = _now()
    with _db_connect() as conn:
        # Ensure thread exists and isn't deleted
        row = conn.execute(
            "SELECT deleted_at FROM forum_threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"thread not found: {thread_id}")
        deleted_at = row[0] if not hasattr(row, "keys") else row["deleted_at"]
        if deleted_at:
            raise ValueError("cannot reply to a deleted thread")
        conn.execute(
            "INSERT INTO forum_thread_replies (id, thread_id, author_email, author_name, body, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rid, thread_id, author_email, author_name, body.strip(), created_at),
        )
    return ReplyRow(
        id=rid, thread_id=thread_id,
        author_email=author_email, author_name=author_name,
        body=body.strip(), created_at=created_at,
    )


def list_replies(thread_id: str) -> list[ReplyRow]:
    ensure_schema()
    with _db_connect() as conn:
        rows = conn.execute(
            "SELECT id, thread_id, author_email, author_name, body, created_at, deleted_at "
            "FROM forum_thread_replies WHERE thread_id = ? "
            "ORDER BY created_at ASC",
            (thread_id,),
        ).fetchall()
    return [_row_to_reply(r) for r in rows]


def soft_delete_reply(reply_id: str, requester_email: str) -> bool:
    ensure_schema()
    with _db_connect() as conn:
        row = conn.execute(
            "SELECT author_email, deleted_at FROM forum_thread_replies WHERE id = ?",
            (reply_id,),
        ).fetchone()
        if row is None:
            return False
        author = row[0] if not hasattr(row, "keys") else row["author_email"]
        deleted_at = row[1] if not hasattr(row, "keys") else row["deleted_at"]
        if author != requester_email:
            return False
        if deleted_at:
            return True
        conn.execute(
            "UPDATE forum_thread_replies SET deleted_at = ? WHERE id = ?",
            (_now(), reply_id),
        )
    return True


# ─── Forum v1.1 — seed thread helper ───────────────────────────────────────

def seed_welcome_threads(
    *,
    author_email: str = SEED_AUTHOR_EMAIL,
    author_name: str = SEED_AUTHOR_NAME,
) -> dict[str, int]:
    """Idempotently insert the 5 welcome threads (one per tag).

    The forum is global (Reddit-style — see docs/POWER_OF_NOW_ARCHITECTURE.md
    §3.4, O9). Seeds are tenant-agnostic; the helper is wired into the
    onboarding flow so the FIRST new tenant onboarded post-deploy creates
    the seeds and every subsequent onboarding is a no-op (the per-tag
    existence check below short-circuits).

    Returns a per-tag count dict: ``{tag: 1}`` for newly-inserted tags,
    ``{tag: 0}`` for tags that already had a pinned seed by this author.
    """
    ensure_schema()
    out: dict[str, int] = {}
    now = _now()
    with _db_connect() as conn:
        for tag, copy in _SEED_THREAD_LIBRARY.items():
            existing = conn.execute(
                "SELECT id FROM forum_threads "
                "WHERE author_email = ? AND tag = ? AND pinned = 1 "
                "AND deleted_at IS NULL LIMIT 1",
                (author_email, tag),
            ).fetchone()
            if existing is not None:
                out[tag] = 0
                continue

            # `create_thread()` rejects pinned=1 because the public API
            # doesn't expose it — we insert directly with pinned=1 here.
            # The fields are still the same shape `create_thread()` writes.
            import uuid
            tid = uuid.uuid4().hex
            norm_tag = _normalise_tag(tag)
            conn.execute(
                "INSERT INTO forum_threads "
                "(id, title, body, tag, author_email, author_name, pinned, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                (tid, copy["title"], copy["body"], norm_tag,
                 author_email, author_name, now),
            )
            out[tag] = 1
    inserted = sum(out.values())
    logger.info("seed_welcome_threads: inserted %d (of 5)", inserted)
    return out
