-- POW-1 — Industry-shared article pool + per-company personalisation view.
--
-- Replaces `article_index` as the canonical store. One row per unique
-- article URL; the `material_industries` JSONB array determines which
-- companies see it on their `/now` deck.
--
-- The companion `company_article_view` table holds the per-(article,
-- company) personalised narrative ("For you", "How it impacts", "What to
-- do") computed by Stages 11-12 of the analysis pipeline.
--
-- A company opens an article -> the API JOINs both tables and returns
-- the merged payload:
--   article_pool.shared_analysis        (event facts, frameworks, source)
--   company_article_view.personalised_analysis  (why_it_matters, what_it_triggers, what_to_watch)
--
-- Within a single industry, every reader sees the SAME shared_analysis
-- and the SAME comment thread, but the personalised_analysis differs.
-- See: docs/POWER_OF_NOW_ARCHITECTURE.md §3.1, §3.2, §4.1, §8.
--
-- Postgres-native (JSONB + GIN). The local SQLite fixture path is for
-- unit tests only and does not require these tables to exist; tests
-- that touch the deck path are skipped on SQLite (see CLAUDE.md
-- "Supabase only").
--
-- Idempotent: every CREATE has IF NOT EXISTS. Safe to re-run.

CREATE TABLE IF NOT EXISTS article_pool (
    id                  TEXT PRIMARY KEY,
    url                 TEXT NOT NULL UNIQUE,
    title               TEXT NOT NULL,
    source              TEXT,
    published_at        TIMESTAMP,
    fetched_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    primary_industry    TEXT NOT NULL,
    material_industries JSONB NOT NULL DEFAULT '[]'::jsonb,
    primary_pillar      TEXT,
    primary_theme       TEXT,
    event_id            TEXT,
    event_polarity      TEXT,
    shared_analysis     JSONB,
    schema_version      TEXT NOT NULL DEFAULT 'p1.0-pool'
);

CREATE INDEX IF NOT EXISTS idx_article_pool_published
    ON article_pool (published_at DESC);

CREATE INDEX IF NOT EXISTS idx_article_pool_primary_ind
    ON article_pool (primary_industry, published_at DESC);

CREATE INDEX IF NOT EXISTS idx_article_pool_material_inds
    ON article_pool USING GIN (material_industries);


CREATE TABLE IF NOT EXISTS company_article_view (
    article_id            TEXT NOT NULL,
    company_slug          TEXT NOT NULL,
    personalised_analysis JSONB NOT NULL,
    criticality_score     REAL NOT NULL,
    criticality_band      TEXT NOT NULL,
    schema_version        TEXT NOT NULL DEFAULT 'p1.0-personalised',
    computed_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (article_id, company_slug)
);

CREATE INDEX IF NOT EXISTS idx_company_article_view_company
    ON company_article_view (company_slug, criticality_score DESC);

CREATE INDEX IF NOT EXISTS idx_company_article_view_band
    ON company_article_view (company_slug, criticality_band, criticality_score DESC);
