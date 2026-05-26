-- Phase 34.7 — Personal Wiki (server-side bookmarks + notes).
--
-- Replaces the legacy Zustand `savedStore` localStorage-only bookmarks
-- with a per-(user, article) server-side store. Identity is the JWT
-- `sub` claim email; bookmarking is implicit on swipe-down in /now.
--
-- Sections give the Wiki UI a grouped view ("Pinned · Climate · Capital
-- · Social · Custom"). Default section is `pinned`; the user can move
-- bookmarks between sections from the Wiki UI.
--
-- Idempotent: every CREATE uses `IF NOT EXISTS`. Safe to re-run.

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
