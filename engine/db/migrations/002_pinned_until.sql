-- Phase 24.3 — pinned_until column on article_index.
-- Enables "pin for N hours" demo seeding: an admin/sales operator
-- can promote a specific article to the top of the feed for the
-- duration of a media demo without affecting the natural relevance
-- ordering for everything else. query_feed() reads the column and
-- sorts (CASE WHEN pinned_until > now THEN 1 ELSE 0 END) DESC first,
-- which means rows with stale (past-now) timestamps silently revert
-- to natural ordering — no cleanup needed when the pin expires.
--
-- ISO-8601 string with `+00:00` offset (e.g. ``2026-05-04T20:00:00+00:00``)
-- so it lex-compares correctly to the same format produced by the
-- dialect translator's `datetime('now', ...)` -> `to_char(NOW(), 'YYYY-
-- MM-DD"T"HH24:MI:SS+00:00')` rewrite.

-- Postgres-specific: ADD COLUMN IF NOT EXISTS works since 9.6.
ALTER TABLE article_index ADD COLUMN IF NOT EXISTS pinned_until TEXT;

-- Partial index — only index rows with a non-NULL pin to keep the
-- index small. The CASE expression in query_feed's ORDER BY does NOT
-- benefit from this index (it's a sort key, not a filter), but
-- admin queries like "show me everything pinned right now" do.
CREATE INDEX IF NOT EXISTS idx_article_pinned
    ON article_index(pinned_until)
    WHERE pinned_until IS NOT NULL;
