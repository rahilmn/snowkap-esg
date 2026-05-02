# SNOWKAP ESG Platform

## Overview
ESG (Environmental, Social, and Governance) intelligence platform with Smart Ontology, causal chain reasoning, and multi-agent AI simulation engine.

## Architecture
- **Backend**: Python 3.12 FastAPI (async) serving on port 5000
- **Frontend**: React 19 + Vite + Tailwind CSS (pre-built, served as static files by FastAPI)
- **Database**: PostgreSQL (Replit-managed) with SQLAlchemy 2.0 async + asyncpg
- **AI**: Anthropic Claude via LangGraph agent framework
- **Auth**: Passwordless 3-way login (domain + designation + company) with magic links

## Key Configuration
- `backend/core/config.py` — Pydantic Settings, auto-converts `DATABASE_URL` from `postgresql://` to `postgresql+asyncpg://`
- `backend/core/database.py` — SQLAlchemy async engine and session factory
- `backend/migrations/` — Alembic migrations (sync driver via `DATABASE_URL_SYNC`)
- `client/dist/` — Pre-built React SPA served by FastAPI catch-all route

## Environment Variables
- `DATABASE_URL` — Auto-provided by Replit PostgreSQL (auto-converted to asyncpg format)
- `ANTHROPIC_API_KEY` — For AI/LLM features
- `JWT_SECRET` — JWT token signing
- `SECRET_KEY` — Application secret
- `ENVIRONMENT` — Set to "production"
- `DEBUG` — Set to "false"
- `SNOWKAP_INTERNAL_EMAILS` — Comma-separated allowlist of emails granted
  `super_admin` permission on login (cross-tenant "All Companies" view,
  CompanySwitcher, RoleViewSwitcher). Phase 22: must contain ONLY
  `sales@snowkap.co.in`. Anyone outside this list lands on their own
  company even if they sign in from `@snowkap.co.in`.
- `REQUIRE_SIGNED_JWT` — **MUST be `1` in production**. When unset,
  `api/auth_context.decode_bearer` falls back to accepting unsigned
  base64-only tokens (legacy compat), which would let an attacker forge
  `permissions:["super_admin"]` or another tenant's `company_id` and
  bypass the Phase 22 tenant-scope gate. The Replit secret is set.

## Phase 22.3 — BASF walkthrough punch list + Phase 23 reviewer follow-up + magic-link OTP

Surgical small/medium fixes shipped together so the platform is hostable
to a small allowlist of prospects (BASF, Lloyds, etc.) without an admin
in the loop. End-to-end retested with Lloyds Banking Group.

- **T1 Alias-aware tenant scope gate** (`api/routes/legacy_adapter.py::_require_tenant_scope`):
  pre-fix the JWT-bound slug was compared verbatim against
  `?company_id=`, so a tenant whose login slug got remapped to a
  canonical (e.g. `basf` → `basf-se` after yfinance) couldn't query
  their own data via the canonical. Both sides now run through
  `sqlite_index.resolve_slug` before equality check. Pinned by
  `tests/test_phase22_3_alias_gate.py` (5 cases).
- **T2 Reconcile `total` vs `query_feed` default tier**
  (`engine/index/sqlite_index.py`): `count` and `query_feed` now thread
  the same pillar/content_type filters end-to-end so a feed call no
  longer returns `{"total": 1, "items": []}`. Verified with Lloyds
  (total=0, len(items)=0, reconciled=True).
- **T3 Empty-Home copy nuance** (`client/src/pages/HomePage.tsx`): the
  empty state now picks one of four messages based on the most-specific
  signal (still-onboarding / failed / found-but-none-high-impact /
  genuinely-empty) instead of always saying "didn't find anything".
- **T4 Self-service prospect retry** (`POST /api/news/onboarding-retry`
  in `api/routes/legacy_adapter.py` + `client/src/pages/HomePage.tsx`
  retry button): a prospect whose pipeline failed or finished
  ready-but-empty can re-run onboarding from the empty state without
  emailing sales. Backend resets `onboarding_status`, re-claims pending,
  schedules `_background_onboard`. Returns 409 if a run is already in
  flight, 400 if JWT lacks tenant scope. Added
  `engine/models/onboarding_status.reset(slug)`.
- **T5 Login rate-limit** (`api/rate_limit.py` +
  `_enforce_login_rate_limit` helper): in-memory token-bucket per
  (email, ip) — 5/min and 20/hr each. Applied to `/auth/login`,
  `/auth/returning-user`, `/auth/verify`. Returns 429 with `Retry-After`.
  Pinned by `tests/test_phase22_3_rate_limit.py` (6 cases).
- **T6 causal_engine hot-reload for new slugs** (cache-bust on cache
  miss, consults tenant_registry): post-onboarding queries for the
  freshly canonical slug no longer log `unknown company slug`.
- **T7 Currency-aware financial fetch** (`engine/ingestion/financial_fetcher.py`):
  pre-fix `_cr` figures were yfinance's raw foreign-currency values
  re-labelled "crore", so BASF's €42B market cap classified as "Small
  Cap" because 42000 << 50000-cr threshold. Now reads
  `info.financialCurrency`, multiplies by hardcoded FX→INR rates
  (`_FX_TO_INR`: INR/USD/EUR/GBP/JPY/CHF/CAD/AUD/CNY/HKD/SGD/ZAR/BRL/MXN/KRW/TWD,
  Jan 2026 spot, no live FX call), tags `FinancialData.currency`. Tier
  classification (`_infer_cap_tier`) runs AFTER conversion in
  `company_onboarder._kickoff`. BASF backfilled in `config/companies.json`
  (market_cap_cr=378000.0, market_cap=Large Cap, currency=EUR). Pinned
  by `tests/test_phase22_3_currency.py` (9 cases).
- **T8 Phase 23 reviewer fix — framework_region threading**
  (`engine/config.py` + `engine/analysis/framework_matcher.py` +
  `engine/analysis/pipeline.py:385`): added explicit `framework_region`
  field to `Company` dataclass that wins over country/region heuristic
  in `_region_key`. Pinned by
  `tests/test_phase23x_framework_region_routing.py` (6 cases).
- **T9 OTP magic-link auth — REVERTED in Phase 22.4** (see below).
  Originally shipped: when `RESEND_API_KEY` was set, `/auth/login` +
  `/auth/returning-user` returned a `{step:"verify"}` challenge and
  `POST /auth/verify` consumed a 6-digit OTP to mint the JWT. After
  user feedback the extra friction was deemed too high for the current
  prospect allowlist, so `is_email_otp_enabled()` now returns False
  unconditionally (Phase 22.4) and login is single-step again. The OTP
  module + DB schema + `/auth/verify` endpoint are retained dormant
  so we can re-enable later by flipping that one flag. The 9
  `tests/test_phase22_3_otp.py` cases still pass — they exercise the
  module directly, not the route gate.

### Phase 22.3 orthogonal quality fixes (Reliance perspectives)
Three small fixes shipped alongside the BASF/Lloyds work to clean up
the deep-insight panel without re-architecting the perspective engine:
- **`engine/analysis/perspective_engine.py`** — `full_insight` is now
  populated for ALL three perspectives (executive / operations /
  capital), not just executive. Pre-fix the ops/capital tabs rendered
  the trimmed `summary` only and lost the supporting paragraph.
- **`engine/analysis/perspective_engine.py::_extract_what_matters`**
  now reorders bullets that lead with negation patterns
  (`_NEGATIVE_LEAD_PATTERNS`: "no impact", "no exposure", "no material",
  "not material", "no supply chain", "no regulatory", "n/a", …) to the
  bottom of the list so positive / actionable bullets surface first in
  the UI.
- **`data/ontology/knowledge_base.ttl::event_routine_capex`** — keyword
  list expanded to cover green ammonia, hydrogen, and decarbonisation
  capex announcements that were previously misclassified as
  `event_unspecified` and dropped from the framework matcher.

### T10 E2E retest (Lloyds Banking Group, UK prospect, 2026-05-02)
- Login: `analyst@lloydsbankinggroup.com` mints JWT with
  `company_id=lloyds-banking-group-plc` (canonical slug from yfinance
  resolution), single-step (no OTP challenge per Phase 22.4).
- Tenant gate: same JWT querying `?company_id=lloyds-banking-group-plc`
  → 200; querying `?company_id=basf-se` → 403.
- Stats/feed reconcile: `/news/stats.total=0` matches
  `/news/feed.total=0, items=[]` (no `{total:1, items:[]}` mismatch).
- Currency conversion: `config/companies.json` records Lloyds with
  `market_cap_cr=602285.8` (~£57B at GBP 105/INR), `market_cap=Large
  Cap` (was Small Cap pre-fix), `_currency=GBP` audit-tagged.
- Self-service retry: `POST /api/news/onboarding-retry` → 200.
- All 86 surgical-fix tests pass (alias gate, currency, rate limit,
  framework_region, OTP module, onboarder region, entity gate).

## Phase 22.4 — OTP login disabled (single-step restored)
- **`api/auth_otp.is_email_otp_enabled()`** now returns False
  unconditionally. The 2-step magic-link UX has been removed.
- **`client/src/pages/LoginPage.tsx`**: OTP step deleted; Step type
  is back to `"domain" | "confirm" | "returning"`. Defensive guard
  retained: if the server ever returns a verify challenge again, the
  UI surfaces an explicit error instead of silently dropping the user.
- **`auth.verify` + `news.retryOnboarding` client methods retained**
  for future use. Backend `/auth/verify` route also retained.

### Documented for follow-up (NOT shipped this phase)
The walkthrough surfaced four larger items deferred to dedicated tasks:
- **Postgres migration of the analysis layer**: `data/snowkap.db`
  (article_index, article_signals, slug_aliases, onboarding_status,
  auth_otp) is still SQLite. The async FastAPI engine + LangGraph already
  use Replit Postgres for app data; the analysis SQLite store needs to
  follow so multi-instance deployments (replit autoscale, multi-region)
  see the same article corpus. Tracking work: schema parity migration
  + replace `_connect()` shims with the asyncpg session factory in
  `backend/core/database.py`.
- **Worker isolation for `_background_onboard`**: pipeline runs inside
  the API process via `BackgroundTasks`, so a slow yfinance/NewsAPI call
  blocks event-loop threads. Move to a dedicated Celery/ARQ worker
  consuming a Redis/Postgres queue.
- **GDPR controls**: prospect emails (incl. EU residents like BASF/PUMA)
  now persist in `tenant_registry` + `auth_otp`. Need: export endpoint,
  delete endpoint, retention policy on `auth_otp` rows, DPA documentation.
- **Observability**: structured request logging with trace IDs across
  ingestion → analysis → feed; OTP issue/verify counters; rate-limit
  buckets exposed as Prom metrics. Currently only Python `logging`.
- **X-API-Key audit**: confirm every admin/internal endpoint either
  requires JWT super_admin OR a valid `X-API-Key` (not both bypass).
  Smoke #7 still flags `/api/news/stats.active_signals_count` mismatch
  between the two auth paths.

## Phase 22.2 — Alias mirror helper + on_demand alias-bypass fix
- **`sqlite_index.mirror_to_slug(canonical, alias)`** — explicit
  named helper called from `_background_onboard` after the analysis
  loop. Delegates to `register_alias` (because `article_index.id` is
  the primary key, physical row duplication would force synthetic IDs
  and fan out to every downstream lookup). Returns the canonical row
  count for caller diagnostics. The user-visible outcome is identical
  to Phase 22.1 (alias session sees canonical's articles) but the call
  site now reads as the intent it serves.
- **`on_demand.py` alias bypass** (`engine/analysis/on_demand.py` I6
  sentiment-trajectory block): the raw SQL bound `company.slug`
  directly, silently bypassing the alias rewrite for any session
  whose JWT slug differed from the canonical. Now imports
  `resolve_slug` at module scope and binds `resolve_slug(company.slug)`
  so on-demand enrichment for an alias-tenant article pulls its
  canonical's prior history. Pinned by source-level + behavioural
  tests in `tests/test_phase22_2_mirror_and_counter.py`.

## Phase 22.1 — Empty-state honesty for newly-onboarded prospects
- **Slug aliasing** (`engine/index/sqlite_index.py`): a new
  `slug_aliases` table stores `alias → canonical` mappings. All read
  paths (`query_feed`, `count`, `count_high_impact`,
  `count_new_last_24h`, `count_active_signals`) call
  `resolve_slug(slug)` so a JWT bound to the login-time slug (e.g.
  `puma`, derived from the email domain) transparently sees rows the
  pipeline indexed under the canonical slug yfinance returned (e.g.
  `puma-se`). Without this the dashboard stayed empty even when the
  pipeline succeeded. Aliases are registered in
  `_background_onboard` after the analysis loop completes.
- **Honest analysed counter**
  (`api/routes/admin_onboard.py::_background_onboard`): pre-fix,
  `analysed += 1` ran for every article including ones rejected by the
  India-only relevance scorer, so a German prospect whose 2 articles
  were both rejected showed "ready 2/2" but had no feed rows. We now
  only count `not summary.rejected`, track `attempted` separately, and
  log both numbers.
- **Self-service onboarding-status endpoint**
  (`GET /api/news/onboarding-status` in `api/routes/legacy_adapter.py`):
  returns `{slug, state, fetched, analysed, home_count, started_at,
  finished_at, error}` for the caller's own tenant (super-admins may
  scope to any). Slug enumeration is gated by `_require_tenant_scope`
  (the same gate as `/news/feed`); the JWT slug is used as the default
  target so the frontend can poll without args. Curated tenants with
  no `onboarding_status` row return `state="ready"`.
- **Frontend empty-state branching** (`client/src/pages/HomePage.tsx`,
  `client/src/pages/SwipeFeedPage.tsx`): both pages poll
  `news.onboardingStatus()` every 5 s while state is `pending|fetching|
  analysing` and stop on `ready|failed`. Empty-state copy now
  distinguishes (a) "Setting up your dashboard… N/M articles
  processed" while the pipeline runs, (b) "We searched the web for
  your company but didn't find ESG-relevant articles in this scan…"
  when state=ready and total=0, and (c) "We hit a snag onboarding
  your company" with a Scan Now button when state=failed. Removes the
  permanent "Fetching ESG intelligence…" spinner that prospects like
  PUMA hit after onboarding completed with zero relevant articles.

## Phase 22 — Onboarding & Tenant Gating
- **Login auto-onboarding** (`api/routes/legacy_adapter.py::auth_login` /
  `auth_returning_user`): every non-super-admin login derives a concrete
  `company_id` via `_ensure_tenant_for_login` — the function never
  returns `None`. If the email matches one of the 7 hardcoded targets,
  that slug is returned. Otherwise the prospect is registered in
  `tenant_registry` immediately and a background task is scheduled
  (FastAPI `BackgroundTasks` → `_background_onboard` from
  `api/routes/admin_onboard.py`, atomic via
  `onboarding_status.claim_pending`) so the dashboard isn't empty when
  the user reaches Home. The ONLY path to `company_id=None` is the
  super-admin allowlist check (`is_snowkap_super_admin`) at the caller —
  so a non-allowlisted `@snowkap.co.in` employee correctly lands on
  their own concrete tenant ("snowkap"), not on the cross-tenant view.
- **Tenant scope gate** (`_require_tenant_scope` in `legacy_adapter.py`):
  `/api/news/feed` and `/api/news/stats` enforce TWO checks: (a) a
  request with `company_id` null/empty is only allowed for super-admins;
  (b) a regular user's bearer token carries a `company_id` claim and the
  API rejects (403) any request whose `company_id` query param doesn't
  match — closes the "slug enumeration" hole where an `@yesbank.com`
  user could read ICICI's data by passing `?company_id=icici-bank`.
  The frontend `MinimalHeader` reinforces this by hiding the
  CompanySwitcher (and its "All Companies" entry) for non-admins —
  regular users see their company name as plain text.
- **Atomic onboarding scheduling**
  (`engine/models/onboarding_status.claim_pending`): wraps an
  `INSERT OR IGNORE` against the slug PK so two parallel first-time
  logins for the same prospect can't both enqueue `_background_onboard`
  (which would double-charge NewsAPI and produce duplicate rows).
- **Frontend abort fix** (`client/src/lib/api.ts`): `controller.abort()`
  now passes a `DOMException("Request timed out", "TimeoutError")` so the
  console no longer logs "signal is aborted without reason"; default
  request timeout raised to 60 s and `auth.login` accepts a `_timeout`
  override (its first call kicks off the pipeline and can be slow).

## Running
- Workflow "Start application" runs `python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000`
- Frontend is pre-built in `client/dist/` and served as static files
- To rebuild frontend: `cd client && npm install --legacy-peer-deps && npm run build`

## API Endpoints
- `/api/health` — Health check
- `/api/auth/` — Authentication (magic links)
- `/api/companies/` — Company management
- `/api/news/` — News ingestion and analysis
- `/api/predictions/` — MiroFish prediction engine
- `/api/ontology/` — ESG ontology and causal chains
- `/api/docs` — Swagger documentation

## Pipeline Quality Gates (Phase 22.4)
The Stage 10 LLM insight generator is wrapped by a multi-check verifier
(`engine/analysis/output_verifier.py`) that audits and corrects:
- **Margin math** — recomputes bps from revenue, auto-corrects.
- **Source tags** — every ₹ figure must carry `(from article)` or
  `(engine estimate)`. The "(from article)" tag is downgraded if the
  exact value isn't in the article body. The raw article body (≤6 KB)
  is now persisted on `PipelineResult.article_content` and passed both
  to the LLM prompt (in the `=== ARTICLE === Body` block) and to the
  verifier so source-tag claims are grounded in the actual article text.
- **Cross-section ₹ drift** — flags fields whose ₹ figure differs from
  the canonical headline number by >35%.
- **Semantic ₹ drift** — flags the same value reused in unrelated
  contexts.
- **Coherence** — flags positive-event articles that emit HIGH/CRITICAL
  materiality with heavy key_risk language. The Stage 10 prompt now
  caps positive events at MODERATE materiality and limits key_risk to
  ≤18 words (execution / timing / dilution framing only).

Fuzz harness (`scripts/fuzz_pipeline.py`) drives 10 corpus articles
through the pipeline and reports the warning rates. Latest baseline
(2026-04-30): hallucination 60→40%, drift 80→0% (auto-fix), coherence
50→0%. The drift verifier now AUTO-NORMALISES drifted figures by
appending `(of ₹X Cr canonical event exposure)` to fields whose ₹ value
deviates >35% from the canonical headline figure.

Article-body grounding: `PipelineResult.article_content` (≤6 KB) round-
trips through `to_dict()` and is wrapped in
`<<<ARTICLE_BODY_START>>>…<<<ARTICLE_BODY_END>>>` delimiters in the
Stage 10 prompt, marked as UNTRUSTED quoted source so any embedded
prompt-injection attempts are ignored.
