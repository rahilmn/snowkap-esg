"""Phase 34.5 — Article comment thread model.

CRUD helpers over the `article_comments` + `article_comment_votes`
tables (migration 008). Backend-agnostic — works on SQLite (dev) and
Postgres (prod) via the existing `engine.db.connect` dispatcher.

Public surface:
  * add_comment(article_id, parent_id, author_email, author_name, body) → CommentRow
  * list_comments(article_id, viewer_email) → list[CommentRow] (with vote totals)
  * soft_delete(comment_id, requester_email) → bool (author-only)
  * vote(comment_id, voter_email, direction) → None  (direction ∈ {+1, -1, 0}; 0 = retract)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from engine.db import connect as _db_connect

logger = logging.getLogger(__name__)


# ─── Schema bootstrap (idempotent) ──────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS article_comments (
    id           TEXT PRIMARY KEY,
    article_id   TEXT NOT NULL,
    parent_id    TEXT,
    author_email TEXT NOT NULL,
    author_name  TEXT NOT NULL,
    body         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    deleted_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_article_comments_article
    ON article_comments(article_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_article_comments_parent
    ON article_comments(parent_id);
CREATE TABLE IF NOT EXISTS article_comment_votes (
    comment_id  TEXT NOT NULL,
    voter_email TEXT NOT NULL,
    direction   INTEGER NOT NULL,
    voted_at    TEXT NOT NULL,
    PRIMARY KEY (comment_id, voter_email)
);
CREATE INDEX IF NOT EXISTS idx_article_comment_votes_comment
    ON article_comment_votes(comment_id);
"""

_SCHEMA_READY = False


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _db_connect() as conn:
        conn.executescript(_SCHEMA_SQL)
    _SCHEMA_READY = True


# ─── Row shape ──────────────────────────────────────────────────────────────

@dataclass
class CommentRow:
    id: str
    article_id: str
    parent_id: str | None
    author_email: str
    author_name: str
    body: str
    created_at: str
    deleted_at: str | None = None
    # Computed on read — not stored on the row.
    vote_score: int = 0
    your_vote: int = 0  # +1 / -1 / 0 (no vote)
    replies: list["CommentRow"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "article_id": self.article_id,
            "parent_id": self.parent_id,
            "author_email": self.author_email,
            "author_name": self.author_name,
            "body": self.body if self.deleted_at is None else "[deleted by author]",
            "created_at": self.created_at,
            "deleted_at": self.deleted_at,
            "vote_score": self.vote_score,
            "your_vote": self.your_vote,
            "replies": [r.to_dict() for r in self.replies],
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ─── Write paths ────────────────────────────────────────────────────────────

def add_comment(
    *,
    article_id: str,
    parent_id: str | None,
    author_email: str,
    author_name: str,
    body: str,
) -> CommentRow:
    """Insert a new comment row. `parent_id` must reference an existing
    top-level comment (depth = 1) on the same article — caller enforces."""
    ensure_schema()
    if not body or not body.strip():
        raise ValueError("body cannot be empty")
    if not author_email or "@" not in author_email:
        raise ValueError("author_email must be a real email")

    cid = uuid.uuid4().hex
    created_at = _now()
    with _db_connect() as conn:
        conn.execute(
            "INSERT INTO article_comments (id, article_id, parent_id, author_email, "
            "author_name, body, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cid, article_id, parent_id, author_email, author_name, body.strip(), created_at),
        )
    return CommentRow(
        id=cid, article_id=article_id, parent_id=parent_id,
        author_email=author_email, author_name=author_name,
        body=body.strip(), created_at=created_at,
    )


def soft_delete(comment_id: str, requester_email: str) -> bool:
    """Author-only soft-delete (set `deleted_at`). Returns True when a row
    was updated. Returns False when the comment doesn't exist OR the
    requester isn't the original author."""
    ensure_schema()
    with _db_connect() as conn:
        row = conn.execute(
            "SELECT author_email, deleted_at FROM article_comments WHERE id = ?",
            (comment_id,),
        ).fetchone()
        if row is None:
            return False
        author = row[0] if not hasattr(row, "keys") else row["author_email"]
        deleted_at = row[1] if not hasattr(row, "keys") else row["deleted_at"]
        if author != requester_email:
            return False
        if deleted_at:
            return True  # already deleted
        conn.execute(
            "UPDATE article_comments SET deleted_at = ? WHERE id = ?",
            (_now(), comment_id),
        )
    return True


# ─── Vote path ──────────────────────────────────────────────────────────────

def vote(comment_id: str, voter_email: str, direction: int) -> None:
    """Set the voter's direction for a comment.
    direction:
      +1 — upvote
      -1 — downvote
       0 — retract (delete the row)
    """
    ensure_schema()
    if direction not in (-1, 0, 1):
        raise ValueError(f"direction must be -1, 0, or +1; got {direction}")
    with _db_connect() as conn:
        if direction == 0:
            conn.execute(
                "DELETE FROM article_comment_votes WHERE comment_id = ? AND voter_email = ?",
                (comment_id, voter_email),
            )
            return
        # Upsert. SQLite uses ON CONFLICT REPLACE; Postgres handles via
        # ON CONFLICT...DO UPDATE. The translator in engine.db handles both.
        conn.execute(
            "INSERT INTO article_comment_votes (comment_id, voter_email, direction, voted_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (comment_id, voter_email) DO UPDATE "
            "SET direction = excluded.direction, voted_at = excluded.voted_at",
            (comment_id, voter_email, direction, _now()),
        )


# ─── Read path ──────────────────────────────────────────────────────────────

def list_comments(article_id: str, viewer_email: str | None = None) -> list[CommentRow]:
    """Return the comment thread for an article.

    Top-level comments are sorted by vote_score DESC then created_at ASC
    (most-upvoted recent first, with ties broken by age). Each top-level
    carries its replies inline (single-level depth — 1 layer of nesting).
    """
    ensure_schema()
    with _db_connect() as conn:
        rows = conn.execute(
            "SELECT id, article_id, parent_id, author_email, author_name, body, "
            "created_at, deleted_at FROM article_comments "
            "WHERE article_id = ? ORDER BY created_at ASC",
            (article_id,),
        ).fetchall()

        all_comments: list[CommentRow] = []
        for r in rows:
            def _g(k: str, i: int):
                return r[k] if hasattr(r, "keys") else r[i]
            all_comments.append(CommentRow(
                id=_g("id", 0),
                article_id=_g("article_id", 1),
                parent_id=_g("parent_id", 2),
                author_email=_g("author_email", 3),
                author_name=_g("author_name", 4),
                body=_g("body", 5),
                created_at=_g("created_at", 6),
                deleted_at=_g("deleted_at", 7),
            ))

        if not all_comments:
            return []

        # Aggregate vote scores in a single query.
        vote_rows = conn.execute(
            "SELECT comment_id, SUM(direction) AS score FROM article_comment_votes "
            "WHERE comment_id IN ("
            + ",".join(["?"] * len(all_comments))
            + ") GROUP BY comment_id",
            tuple(c.id for c in all_comments),
        ).fetchall()
        score_map: dict[str, int] = {}
        for v in vote_rows:
            cid = v[0] if not hasattr(v, "keys") else v["comment_id"]
            score = v[1] if not hasattr(v, "keys") else v["score"]
            score_map[cid] = int(score or 0)

        # Viewer-specific own-vote (when authenticated).
        own_vote_map: dict[str, int] = {}
        if viewer_email:
            vr = conn.execute(
                "SELECT comment_id, direction FROM article_comment_votes "
                "WHERE voter_email = ? AND comment_id IN ("
                + ",".join(["?"] * len(all_comments))
                + ")",
                (viewer_email, *(c.id for c in all_comments)),
            ).fetchall()
            for v in vr:
                cid = v[0] if not hasattr(v, "keys") else v["comment_id"]
                d = v[1] if not hasattr(v, "keys") else v["direction"]
                own_vote_map[cid] = int(d)

    # Stitch scores + own votes onto the in-memory comments.
    for c in all_comments:
        c.vote_score = score_map.get(c.id, 0)
        c.your_vote = own_vote_map.get(c.id, 0)

    # Build the 1-level reply tree.
    by_id: dict[str, CommentRow] = {c.id: c for c in all_comments}
    top: list[CommentRow] = []
    for c in all_comments:
        if c.parent_id and c.parent_id in by_id:
            by_id[c.parent_id].replies.append(c)
        else:
            top.append(c)

    # Sort top-level by score DESC, then created_at ASC.
    top.sort(key=lambda c: (-c.vote_score, c.created_at))
    for c in top:
        c.replies.sort(key=lambda r: r.created_at)
    return top


__all__ = [
    "CommentRow",
    "add_comment",
    "list_comments",
    "soft_delete",
    "vote",
    "ensure_schema",
]
