-- Phase 34.5 — Reddit-style article comment threads (non-anonymous).
--
-- The Power-of-Now spec: "multiple people would have received the news,
-- and although the summary box shows data unique to each, they can still
-- use the comment space as a forum, like Reddit (although non-anonymous)
-- to interact and share thoughts/ideas."
--
-- Tables:
--   article_comments       — one row per comment. `parent_id` NULL = top-level;
--                            non-null = a 1-level reply (no deeper nesting).
--                            Author identity is the JWT `sub` claim email,
--                            stamped at write time (never anonymous).
--   article_comment_votes  — one row per (comment, voter). Composite PK so
--                            a single voter cannot stack votes. `direction`
--                            is +1 (up) or -1 (down); changing your vote is
--                            an UPSERT.
--
-- Soft-delete only — `deleted_at` is set so threads stay legible even after
-- an author removes their comment ("[deleted by author]" placeholder).
--
-- Idempotent: every CREATE has `IF NOT EXISTS`. Safe to run twice.

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
