# SNOWKAP ESG Platform — CLAUDE.md

## Project Identity

**POWER-OF-NOW (SNOWKAP ESG)** — Production ESG intelligence platform serving 21+ companies.
Transforming into a multi-tenant SaaS with Smart ESG Ontology, causal chain reasoning,
MiroFish multi-agent prediction, and 9 specialist AI agent personalities.

## Architecture Overview

```
React 19 + Vite (client/)  →  Nginx  →  FastAPI (backend/)    port 8000
                                      →  MiroFish (prediction/) port 5001
                                      →  Socket.IO (/ws/*)

Shared Intelligence Layer:
  Apache Jena (Fuseki+TDB2+OWL)  |  Zep Cloud (agent memory)  |  PostgreSQL 16 + pgvector
  Redis 7 (cache+queue)          |  Celery 5.4 (workers)       |  MinIO (files)
```

### Docker Services (8)

| Service | Port | Purpose |
|---------|------|---------|
| `esg-api` | 8000 | FastAPI main backend |
| `esg-worker` | — | Celery worker |
| `mirofish` | 5001 | MiroFish prediction engine |
| `postgres` | 5432 | PostgreSQL 16 + pgvector |
| `redis` | 6379 | Cache, pub/sub, Celery broker |
| `jena-fuseki` | 3030 | Apache Jena knowledge graph |
| `minio` | 9000 | S3-compatible file storage |
| `nginx` | 80/443 | Reverse proxy |

## Directory Structure

```
POWER-OF-NOW/
├── CLAUDE.md                    # This file
├── MASTER_BUILD_PLAN.md         # Full phased build plan
├── client/                      # React 19 + Vite frontend
│   └── src/
│       ├── components/          # 30+ Radix UI components
│       ├── pages/               # 31 pages
│       ├── hooks/               # Custom React hooks
│       └── lib/                 # Utilities
├── server/                      # LEGACY Express backend (being replaced)
│   ├── index.ts                 # Express entry point
│   ├── agents/                  # Existing agent logic
│   ├── rag/                     # RAG pipeline
│   └── *.ts                     # 43 service/route files
├── shared/
│   └── schema.ts                # Drizzle ORM schema (47 tables)
├── backend/                     # NEW FastAPI backend (Python 3.12)
│   ├── main.py                  # FastAPI app entry
│   ├── core/                    # Config, security, dependencies
│   │   ├── config.py            # Pydantic Settings
│   │   ├── security.py          # JWT + magic link auth
│   │   ├── database.py          # SQLAlchemy async engine
│   │   └── dependencies.py      # TenantContext, current_user
│   ├── models/                  # SQLAlchemy 2.0 models
│   │   ├── base.py              # TenantMixin, BaseModel
│   │   ├── tenant.py            # Tenant, TenantMembership, TenantConfig
│   │   ├── company.py           # Company, Facility, Supplier
│   │   ├── user.py              # User, MagicLink, Session
│   │   ├── news.py              # Article, ArticleScore, CausalChain
│   │   ├── analysis.py          # Analysis, Recommendation, Framework
│   │   ├── prediction.py        # PredictionReport, SimulationRun
│   │   └── ontology.py          # OntologyRule, Assertion, InferenceLog
│   ├── routers/                 # FastAPI route modules
│   │   ├── auth.py              # 3-way login + magic link
│   │   ├── companies.py         # Company CRUD
│   │   ├── analysis.py          # ESG analysis endpoints
│   │   ├── news.py              # News feed + curation
│   │   ├── predictions.py       # MiroFish trigger + results
│   │   ├── ontology.py          # SPARQL, rules, causal chains
│   │   ├── campaigns.py         # Newsletter campaigns
│   │   └── admin.py             # Platform admin
│   ├── services/                # Business logic layer
│   │   ├── auth_service.py      # Domain resolution, magic links
│   │   ├── news_service.py      # Google News RSS, scoring
│   │   ├── ontology_service.py  # Jena SPARQL, causal chain engine
│   │   ├── prediction_service.py # MiroFish bridge
│   │   ├── agent_service.py     # LangGraph + Agency agents
│   │   └── email_service.py     # Resend via Celery
│   ├── agent/                   # LangGraph AI agent
│   │   ├── graph.py             # State machine definition
│   │   ├── tools.py             # Agent tools (SPARQL, DB, predict)
│   │   ├── router.py            # Intent → specialist routing
│   │   └── personalities/       # Agency agent system prompts
│   ├── ontology/                # Smart ESG Ontology
│   │   ├── sustainability.ttl   # Base OWL ontology
│   │   ├── causal_engine.py     # Causal chain traversal + scoring
│   │   ├── entity_extractor.py  # NER from news articles
│   │   ├── jena_client.py       # Apache Jena Fuseki client
│   │   └── rule_compiler.py     # Business rules → OWL axioms
│   ├── tasks/                   # Celery tasks
│   │   ├── news_tasks.py        # RSS ingestion, scoring
│   │   ├── prediction_tasks.py  # MiroFish trigger
│   │   └── email_tasks.py       # Async email sending
│   └── migrations/              # Alembic migrations
│       ├── alembic.ini
│       └── versions/
├── prediction/                  # MiroFish prediction engine
│   ├── graph_builder.py
│   ├── ontology_generator.py
│   ├── simulation_manager.py
│   ├── simulation_runner.py
│   ├── report_agent.py
│   ├── zep_entity_reader.py
│   ├── zep_graph_memory_updater.py
│   ├── oasis_profile_generator.py
│   └── simulation_config_generator.py
├── agency/                      # The Agency agent personalities
│   └── agents/                  # 127+ agent markdown files
├── docker/                      # Dockerfiles per service
│   ├── api.Dockerfile
│   ├── worker.Dockerfile
│   ├── mirofish.Dockerfile
│   └── nginx/
│       └── nginx.conf
├── docker-compose.yml           # Dev environment (8 services)
├── docker-compose.prod.yml      # Production config
├── requirements.txt             # Python dependencies
├── .env.example                 # All environment variables
└── .github/
    └── workflows/
        └── ci.yml               # GitHub Actions pipeline
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 19 + Vite + Tailwind + Radix UI + Zustand + TanStack Query |
| API | FastAPI + Pydantic v2 (Python 3.12) |
| Prediction | MiroFish (OASIS + Zep + GraphRAG) |
| Database | PostgreSQL 16 + pgvector + Alembic |
| ORM | SQLAlchemy 2.0 async + asyncpg |
| Knowledge Graph | Apache Jena (Fuseki + TDB2 + OWL Reasoner) |
| Agent Memory | Zep Cloud |
| AI/LLM | Claude claude-sonnet-4-6 (primary) + GPT-4o (legacy) |
| Agent Framework | LangGraph 0.2+ |
| Cache/Queue | Redis 7 |
| Task Queue | Celery 5.4+ |
| Real-Time | Socket.IO + Redis pub/sub |
| File Storage | MinIO |
| Auth | JWT + Magic Links + RBAC (no passwords, no OTP) |
| Email | Resend (async via Celery) |
| Monitoring | structlog + Sentry |
| Infrastructure | Docker Compose (8 services) + Nginx + GitHub Actions |

## Auth Model — Passwordless 3-Way Login

```
Domain → Designation → Company Name → Magic Link → JWT
```

- No passwords, no OTP codes
- Domain-gated: @mahindra.com email → Mahindra tenant only
- Auto-provision: first user from new domain creates tenant
- Returning users: email → magic link → JWT (skip steps)
- JWT claims: {tenant_id, user_id, company_id, designation, permissions[], domain}

## Key Conventions

### Python Backend

- **Python 3.12**, strict type hints everywhere
- **async/await** for all I/O (database, HTTP, file)
- **Pydantic v2** for all request/response schemas
- **SQLAlchemy 2.0** async with `asyncpg` driver
- Every table has `tenant_id` — enforced via `TenantMixin`
- Every query filters by `tenant_id` — enforced via `TenantContext` dependency
- Use `structlog` for logging, include `tenant_id` on every log line
- Tests: `pytest` + `pytest-asyncio` + `httpx` (AsyncClient)
- Imports: stdlib → third-party → local, alphabetical within groups

### React Frontend

- Existing: React 18 (upgrading to 19), Vite, Tailwind, Radix UI, Wouter routing
- State: Zustand stores (authStore, tenantConfigStore, newsStore)
- Data fetching: TanStack Query
- All API calls through typed client functions
- Radix UI + Tailwind for all UI components

### Database

- PostgreSQL 16 with pgvector extension
- LEGACY: Drizzle ORM (shared/schema.ts) — do not modify
- NEW: SQLAlchemy 2.0 async models (backend/models/) — all new work here
- Alembic for migrations — never raw SQL in application code
- Row-level tenant isolation: `tenant_id` on every tenant-scoped table
- `companies` table = ESG analysis targets (not tenants)
- `tenants` table = Snowkap customers (NEW — the multi-tenant split)

### Ontology (Apache Jena)

- Base ontology: `sustainability.ttl` (OWL2)
- Each tenant gets a named graph: `urn:snowkap:tenant:{tenant_id}`
- SPARQL queries always scoped to tenant named graph
- Causal chain traversal: BFS, max 4 hops, decay scoring (1.0 → 0.7 → 0.4 → 0.2)
- Entity extraction via Claude NER
- Business rules compiled to OWL axioms

### MiroFish Predictions

- Triggered only on high-impact news (score >70, financial exposure >₹10L)
- 20-50 agents per simulation, 10-40 rounds
- Runs as separate Docker service on port 5001
- Results stored in `prediction_reports` table + Jena triples
- AGPL-3.0 license — runs as separate microservice (process isolation)

### The Agency (Runtime Agents)

- 9 specialist agents embedded in product via LangGraph
- Each agent: Agency personality prompt + SNOWKAP tools
- Agent routing: Claude classifies user intent → selects specialist
- Shared Zep Cloud memory with MiroFish agents

## Critical Rules

1. **NEVER** return data from Tenant A to Tenant B — every query MUST filter by tenant_id
2. **NEVER** store passwords — auth is magic-link only
3. **NEVER** run MiroFish on every article — only high-impact (trigger conditions)
4. **NEVER** modify `shared/schema.ts` — that's the legacy Drizzle schema
5. **NEVER** expose Jena SPARQL endpoint directly — always proxy through FastAPI
6. **ALWAYS** use async/await for database and HTTP operations
7. **ALWAYS** include tenant_id in structlog context
8. **ALWAYS** validate email domain matches company domain at login
9. Keep Express server running during migration — Nginx routes traffic
10. MiroFish runs as separate process (AGPL-3.0 compliance)

## Common Commands

```bash
# Development
docker compose up -d                    # Start all 8 services
docker compose logs -f esg-api          # Follow API logs
docker compose restart esg-api          # Restart after code change

# Backend
cd backend && python -m pytest          # Run tests
alembic upgrade head                    # Run migrations
alembic revision --autogenerate -m "description"  # Create migration

# Frontend
cd client && npm run dev                # Vite dev server
cd client && npm run build              # Production build

# Database
docker compose exec postgres psql -U esg_user -d esg_platform

# Jena
# SPARQL endpoint: http://localhost:3030/esg/sparql
# Admin UI: http://localhost:3030

# Redis
docker compose exec redis redis-cli

# Legacy (keep running during migration)
npm run dev                             # Express + Vite dev
```

## Environment Variables

See `.env.example` for full list. Critical ones:

```
DATABASE_URL=postgresql+asyncpg://esg_user:esg_password@localhost:5432/esg_platform
REDIS_URL=redis://localhost:6379/0
JENA_FUSEKI_URL=http://localhost:3030
MINIO_ENDPOINT=localhost:9000
ANTHROPIC_API_KEY=sk-ant-...
ZEP_API_KEY=...
RESEND_API_KEY=re_...
JWT_SECRET=...  (min 32 chars)
MIROFISH_URL=http://localhost:5001
```

## ESG Domain Knowledge

### Frameworks Supported
BRSR, ESRS, GRI, IFRS S1/S2, CDP, TCFD, CSRD, SASB

### Industry Classification
45 SASB industry categories, auto-classified from company domain via Claude

### Causal Relationship Types
directOperational (0-hop), supplyChainUpstream (1-hop), supplyChainDownstream (1-hop),
workforceIndirect (2-hop), regulatoryContagion (1-hop), geographicProximity (0-hop),
industrySpillover (1-hop), commodityChain (3-hop)

## Phase Status

Track progress in MASTER_BUILD_PLAN.md
