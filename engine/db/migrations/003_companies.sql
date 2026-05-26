-- Phase 28 — companies table.
-- Persists onboarded companies in Supabase (or SQLite) so the live app
-- has a single source of truth. Before Phase 28, companies lived only
-- in config/companies.json + ephemeral SQLite cache; flipping
-- SNOWKAP_DB_BACKEND=postgres lost newly-onboarded tenants because
-- there was no companies table.
--
-- Idempotent — safe to re-run. Mirrors the Company dataclass in
-- engine/config.py (fields: slug, name, domain, industry, market_cap_tier,
-- yfinance_ticker, eodhd_ticker, framework_region, revenue_cr,
-- primitive_calibration). primitive_calibration_json holds the
-- JSON-serialised per-primitive β coefficients (Phase 17c) — JSONB on
-- Postgres, TEXT on SQLite. The dialect translator handles the type
-- difference at write time.

CREATE TABLE IF NOT EXISTS companies (
    slug                       TEXT PRIMARY KEY,
    name                       TEXT NOT NULL,
    domain                     TEXT,
    industry                   TEXT,
    market_cap_tier            TEXT,
    yfinance_ticker            TEXT,
    eodhd_ticker               TEXT,
    framework_region           TEXT,                       -- INDIA / EU / US / UK / APAC / GLOBAL
    revenue_cr                 REAL,
    primitive_calibration_json TEXT,
    created_at                 TEXT NOT NULL,
    updated_at                 TEXT NOT NULL,
    created_by_user            TEXT,
    status                     TEXT DEFAULT 'active'        -- active / archived
);

CREATE INDEX IF NOT EXISTS idx_companies_domain
    ON companies(domain);

CREATE INDEX IF NOT EXISTS idx_companies_status_updated
    ON companies(status, updated_at DESC);
