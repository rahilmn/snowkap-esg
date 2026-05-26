-- Phase 34.6 — User-generated forum threads + replies (non-anonymous).
--
-- The Power-of-Now spec: "There is a separate forum section for user-
-- generated content and discussion boards." Threads are tagged with one
-- of a fixed taxonomy (BRSR / Climate / CBAM / Governance / Audit) so
-- the UI can render tag-filter chips.
--
-- Tables:
--   forum_threads          — top-level posts (title + body + tag + author)
--   forum_thread_replies   — one-level deep replies. No nesting.
--
-- Identity = JWT `sub` claim email. Author-only soft-delete is via the
-- `deleted_at` column. Pinned threads (admin-only in UI; backend allows
-- any caller to read pinned=1 but only the author can set it) appear
-- at the top of the per-tag listing.
--
-- Idempotent: every CREATE uses `IF NOT EXISTS`. Safe to re-run.

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
