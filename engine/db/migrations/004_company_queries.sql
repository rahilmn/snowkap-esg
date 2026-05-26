-- Phase 31 — live-fetch hybrid news ingestion.
-- Adds two free-form query strings per company so the live news fetcher
-- can hit Google News on the fly without rebuilding the 30-query
-- regulator-flavoured set every request.
--
--   sustainability_query — single best ESG / climate / labour / governance
--                          query for this company. Stamped at onboard time
--                          by engine/ingestion/llm_query_generator.py.
--   general_query        — single best general business news query for
--                          this company (used as the "secondary feed" so
--                          we don't miss high-signal non-ESG events that
--                          still matter to a CFO).
--
-- Idempotent — both columns use `ADD COLUMN IF NOT EXISTS`. The resilient
-- migrate runner (engine/db/migrate.py::_apply_migration_resilient)
-- rewrites this to plain `ADD COLUMN` + duplicate-column skip on SQLite.

ALTER TABLE companies ADD COLUMN IF NOT EXISTS sustainability_query TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS general_query TEXT;
