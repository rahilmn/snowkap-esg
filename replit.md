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

## Phase 22 — Onboarding & Tenant Gating
- **Login auto-onboarding** (`api/routes/legacy_adapter.py::auth_login` /
  `auth_returning_user`): every corporate login derives a `company_id`
  via `_ensure_tenant_for_login`. If the email matches one of the 7
  hardcoded targets, that slug is returned. Otherwise the prospect is
  registered in `tenant_registry` immediately and a background task is
  scheduled (FastAPI `BackgroundTasks` → `_background_onboard` from
  `api/routes/admin_onboard.py`, idempotent on `onboarding_status`) so
  the dashboard isn't empty when the user reaches Home. Snowkap-internal
  domains (`snowkap.com`, `snowkap.co.in`) skip both registration and
  onboarding — we're the seller, not a customer.
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
