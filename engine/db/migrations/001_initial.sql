-- Phase 24 — Initial Postgres schema for Snowkap ESG.
-- Idempotent. Safe to run multiple times.
-- Schemas mirror the existing SQLite tables in data/snowkap.db EXACTLY,
-- so the legacy code's INSERT/SELECT statements work unchanged once the
-- backend is flipped via SNOWKAP_DB_BACKEND=postgres.
-- Sourced by reading every CREATE TABLE statement in:
--   engine/index/sqlite_index.py
--   engine/index/tenant_registry.py
--   engine/models/article_analysis_status.py
--   engine/models/campaign_store.py
--   engine/models/llm_calls.py
--   engine/models/onboarding_status.py
--   engine/jobs/onboard_queue.py
--   api/auth_otp.py

-- ============================================================================
-- 1. article_index — fast feed/filter queries over the JSON insight files
-- ============================================================================
CREATE TABLE IF NOT EXISTS article_index (
    id                    TEXT PRIMARY KEY,
    company_slug          TEXT NOT NULL,
    title                 TEXT NOT NULL,
    source                TEXT,
    url                   TEXT,
    published_at          TEXT,
    tier                  TEXT,
    materiality           TEXT,
    action                TEXT,
    relevance_score       REAL,
    impact_score          REAL,
    esg_pillar            TEXT,
    primary_theme         TEXT,
    content_type          TEXT,
    framework_count       INTEGER DEFAULT 0,
    do_nothing            INTEGER DEFAULT 0,
    recommendations_count INTEGER DEFAULT 0,
    json_path             TEXT NOT NULL,
    written_at            TEXT,
    ontology_queries      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_company_tier
    ON article_index(company_slug, tier, relevance_score DESC);
CREATE INDEX IF NOT EXISTS idx_company_published
    ON article_index(company_slug, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_tier
    ON article_index(tier);
CREATE INDEX IF NOT EXISTS idx_content_type
    ON article_index(content_type);

-- ============================================================================
-- 2. slug_aliases — alias slug → canonical slug (Phase 22.1)
-- ============================================================================
CREATE TABLE IF NOT EXISTS slug_aliases (
    alias     TEXT PRIMARY KEY,
    canonical TEXT NOT NULL
);

-- ============================================================================
-- 3. tenant_registry — auto-registered onboarded tenants
-- ============================================================================
CREATE TABLE IF NOT EXISTS tenant_registry (
    slug         TEXT PRIMARY KEY,
    domain       TEXT NOT NULL UNIQUE,
    name         TEXT,
    industry     TEXT,
    source       TEXT NOT NULL DEFAULT 'onboarded',
    created_at   TEXT NOT NULL,
    last_seen_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tenant_source ON tenant_registry(source);
CREATE INDEX IF NOT EXISTS idx_tenant_last_seen ON tenant_registry(last_seen_at DESC);

-- ============================================================================
-- 4. article_analysis_status — per-article on-demand pipeline state (Phase 13 B2)
-- ============================================================================
CREATE TABLE IF NOT EXISTS article_analysis_status (
    article_id      TEXT PRIMARY KEY,
    company_slug    TEXT NOT NULL,
    state           TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    error_class     TEXT,
    error           TEXT,
    elapsed_seconds REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_aas_state   ON article_analysis_status(state);
CREATE INDEX IF NOT EXISTS idx_aas_company ON article_analysis_status(company_slug);

-- ============================================================================
-- 5. campaigns — drip-marketing scheduler (Phase 10 / 11C)
-- ============================================================================
CREATE TABLE IF NOT EXISTS campaigns (
    id                 TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    created_by         TEXT NOT NULL,
    template_type      TEXT NOT NULL DEFAULT 'share_single',
    target_company     TEXT NOT NULL,
    article_selection  TEXT NOT NULL,
    article_id         TEXT,
    cadence            TEXT NOT NULL,
    day_of_week        INTEGER,
    day_of_month       INTEGER,
    send_time_utc      TEXT,
    cta_url            TEXT,
    cta_label          TEXT,
    sender_note        TEXT,
    status             TEXT NOT NULL DEFAULT 'active',
    last_sent_at       TEXT,
    next_send_at       TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_campaigns_status_next ON campaigns(status, next_send_at);

-- ============================================================================
-- 6. campaign_recipients — many recipients per campaign
-- ============================================================================
CREATE TABLE IF NOT EXISTS campaign_recipients (
    id             TEXT PRIMARY KEY,
    campaign_id    TEXT NOT NULL,
    email          TEXT NOT NULL,
    name_override  TEXT,
    last_sent_at   TEXT,
    created_at     TEXT NOT NULL,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE,
    UNIQUE(campaign_id, email)
);
CREATE INDEX IF NOT EXISTS idx_recip_campaign ON campaign_recipients(campaign_id);
CREATE INDEX IF NOT EXISTS idx_recip_email    ON campaign_recipients(email);

-- ============================================================================
-- 7. campaign_send_log — audit trail of every email sent
-- ============================================================================
CREATE TABLE IF NOT EXISTS campaign_send_log (
    id                TEXT PRIMARY KEY,
    campaign_id       TEXT NOT NULL,
    recipient_email   TEXT NOT NULL,
    article_id        TEXT,
    subject           TEXT,
    html_length       INTEGER,
    status            TEXT NOT NULL,
    provider_id       TEXT,
    error             TEXT,
    sent_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_send_recipient ON campaign_send_log(recipient_email);
CREATE INDEX IF NOT EXISTS idx_send_sent_at   ON campaign_send_log(sent_at);
CREATE INDEX IF NOT EXISTS idx_send_campaign  ON campaign_send_log(campaign_id);

-- ============================================================================
-- 8. llm_calls — every OpenAI call: tokens, cost, article_id (Phase 11D)
-- ============================================================================
CREATE TABLE IF NOT EXISTS llm_calls (
    id                TEXT PRIMARY KEY,
    ts                TEXT NOT NULL,
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens      INTEGER DEFAULT 0,
    cost_usd          REAL DEFAULT 0,
    article_id        TEXT,
    stage             TEXT,
    error             TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_ts       ON llm_calls(ts);
CREATE INDEX IF NOT EXISTS idx_llm_article  ON llm_calls(article_id);
CREATE INDEX IF NOT EXISTS idx_llm_stage    ON llm_calls(stage);

-- ============================================================================
-- 9. onboarding_status — per-tenant pending/fetching/analysing/ready/failed
-- ============================================================================
CREATE TABLE IF NOT EXISTS onboarding_status (
    slug        TEXT PRIMARY KEY,
    state       TEXT NOT NULL,
    fetched     INTEGER DEFAULT 0,
    analysed    INTEGER DEFAULT 0,
    home_count  INTEGER DEFAULT 0,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_onboarding_state ON onboarding_status(state);

-- ============================================================================
-- 10. onboard_jobs — durable queue for the background onboarding worker
-- ============================================================================
CREATE TABLE IF NOT EXISTS onboard_jobs (
    id           BIGSERIAL PRIMARY KEY,
    slug         TEXT NOT NULL,
    name         TEXT,
    ticker_hint  TEXT,
    domain       TEXT,
    item_limit   INTEGER NOT NULL DEFAULT 10,
    state        TEXT NOT NULL DEFAULT 'queued',
    attempts     INTEGER NOT NULL DEFAULT 0,
    enqueued_at  TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    worker_id    TEXT,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_onboard_state_enqueued
    ON onboard_jobs(state, enqueued_at);

-- ============================================================================
-- 11. auth_otp — one-time-password login
-- ============================================================================
CREATE TABLE IF NOT EXISTS auth_otp (
    email      TEXT PRIMARY KEY,
    code       TEXT NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_otp_expires ON auth_otp(expires_at);
