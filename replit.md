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
