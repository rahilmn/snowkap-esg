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
