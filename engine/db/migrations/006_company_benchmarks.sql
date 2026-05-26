-- Phase 32 — External benchmarks table.
--
-- Holds CSV-imported snapshots of third-party ESG/sustainability ratings
-- for each company. Surfaced on the "What to watch" bullet of the
-- unified analysis card. Live MSCI / SBTI / CRISIL / NSE-ESG API
-- integrations are out of scope (Phase 30 procurement effort) — this
-- table backs a CSV-import scaffold so the UI shape is right today.
--
-- Columns:
--   slug         — company slug (FK by convention to companies.slug)
--   source       — "MSCI ESG", "SBTI", "CRISIL", "NSE ESG", etc.
--   metric       — "rating", "target_status", "score", ...
--   value        — free-form text ("A", "Committed", "AA", "68")
--   as_of_date   — ISO date when the rating/value was observed
--   imported_at  — timestamp this row was imported
--
-- Idempotent — composite PK (slug, source, metric, as_of_date) means
-- re-importing the same CSV row is a no-op. To force-overwrite, delete
-- the row first.

CREATE TABLE IF NOT EXISTS company_benchmarks (
    slug          TEXT NOT NULL,
    source        TEXT NOT NULL,
    metric        TEXT NOT NULL,
    value         TEXT,
    as_of_date    TEXT NOT NULL,
    imported_at   TEXT NOT NULL,
    PRIMARY KEY (slug, source, metric, as_of_date)
);

CREATE INDEX IF NOT EXISTS idx_company_benchmarks_slug
    ON company_benchmarks(slug, as_of_date DESC);
