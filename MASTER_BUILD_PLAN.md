# SNOWKAP ESG Platform — Master Build Plan

## Context

POWER-OF-NOW (SNOWKAP ESG) is a production ESG intelligence platform serving 21+ companies. This master plan transforms it into a highly scalable, multi-tenant SaaS with a **Smart ESG Ontology** that understands causal chains (e.g., "truck drivers eat at roadside dhabas with gas stoves → LPG crisis due to war → cost impact on Mahindra Logistics"), performs predictions via MiroFish multi-agent simulation, and delivers actionable recommendations.

The plan integrates four systems:
1. **Universal Build Framework** — 10-layer production SaaS architecture
2. **MiroFish** — Multi-agent prediction engine with GraphRAG + OASIS simulation
3. **Smart ESG Ontology** — Causal chain reasoning connecting any news event to any company's material ESG impact through supply chain, operations, and regulatory linkages
4. **The Agency** — 127+ specialized AI agent personalities for development, testing, design, and operations

**Auth model:** Passwordless — Domain + Designation + Company Name login (no OTP, no passwords). Users authenticate via domain verification and are auto-provisioned.

---

## Part 1: Smart ESG Ontology — The Intelligence Engine

### What Makes It "Smart"

The current system treats news as flat articles scored by keyword relevance. The smart ontology adds **causal depth** — understanding that a water scarcity crisis in Kolhapur affects your manufacturing plant THERE, which affects YOUR production output, which affects YOUR Scope 1 emissions reporting, which affects YOUR BRSR compliance.

### The Causal Chain Architecture

```
[NEWS EVENT] → [ENTITY EXTRACTION] → [CAUSAL GRAPH TRAVERSAL] → [IMPACT SCORING] → [PREDICTION] → [RECOMMENDATION]

Example Flow:
"LPG prices surge 40% due to Russia-Ukraine conflict"
    ↓
Entity: LPG, Price Surge, Geopolitical Conflict
    ↓
Causal Graph Traversal:
  → LPG → cooking fuel → roadside food vendors → truck drivers
  → truck drivers → Mahindra Logistics fleet operations
  → fleet operations → driver welfare costs (Social - S)
  → fleet operations → fuel cost increase (operational cost)
  → operational cost → Scope 3 Category 6 (business travel/transport)
    ↓
Impact Score: Mahindra Logistics — Medium (indirect, 2-hop supply chain)
  Financial: ₹2-5Cr annual driver welfare cost increase
  ESG: Social (workforce welfare), Governance (supply chain management)
  Framework: BRSR P3 (Employee Wellbeing), GRI 403 (Occupational Health)
    ↓
Prediction (via MiroFish simulation):
  "If LPG prices remain elevated for 6+ months, driver attrition increases 15-20%"
    ↓
Recommendation:
  "Negotiate bulk LPG contracts for fleet rest stops; budget ₹3Cr for driver meal subsidies"
```

### Ontology Layers (5-Layer Intelligence Stack)

```
Layer 5: PREDICTION & SIMULATION (MiroFish)
  Multi-agent simulation of scenarios, what-if analysis
  ↑
Layer 4: IMPACT PROPAGATION
  Causal chain traversal, financial quantification, timeline estimation
  ↑
Layer 3: KNOWLEDGE GRAPH (Apache Jena + Zep)
  Companies, supply chains, regulations, ESG frameworks, geographic dependencies
  ↑
Layer 2: ENTITY EXTRACTION & LINKING
  NER from news, entity resolution to knowledge graph nodes
  ↑
Layer 1: NEWS INGESTION & CLASSIFICATION
  Google News RSS, domain-based curation, topic tagging, sentiment
```

### Causal Relationship Types

| Relationship | Example | Hops |
|---|---|---|
| **directOperational** | "Your factory in Kolhapur" → water scarcity | 0 |
| **supplyChainUpstream** | Your steel supplier's coal mine → emissions | 1 |
| **supplyChainDownstream** | Your customer's ESG policy → your sales | 1 |
| **workforceIndirect** | Truck driver food costs → your logistics costs | 2 |
| **regulatoryContagion** | EU CBAM → your export costs | 1 |
| **geographicProximity** | Flood in district X → your plant in district X | 0 |
| **industrySpillover** | Competitor's ESG scandal → your sector scrutiny | 1 |
| **commodityChain** | Oil price → plastic → packaging → your FMCG costs | 3 |

---

## Part 2: MiroFish Integration — Layering Strategy

### What MiroFish Brings

MiroFish is a multi-agent AI prediction engine that:
1. **Graph Construction** — Extracts entities from seed data, builds knowledge graph via GraphRAG
2. **Agent Generation** — Creates thousands of autonomous agents with personalities + memory (via Zep)
3. **Simulation** — Runs parallel simulations using OASIS framework (CAMEL-AI)
4. **Report Generation** — ReportAgent analyzes simulation outcomes
5. **Deep Interaction** — Conversational interface with simulated agents

### System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    SNOWKAP ESG PLATFORM                  │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐ │
│  │  React 19 UI  │  │ Mobile App   │  │ Admin Console │ │
│  └──────┬───────┘  └──────┬───────┘  └───────┬───────┘ │
│         │                  │                   │         │
│  ┌──────▼──────────────────▼───────────────────▼──────┐ │
│  │              Nginx Reverse Proxy                    │ │
│  └──────┬─────────────────┬────────────────────┬──────┘ │
│         │                  │                    │        │
│  ┌──────▼───────┐  ┌──────▼───────┐  ┌────────▼──────┐ │
│  │ FastAPI       │  │ MiroFish     │  │ Socket.IO     │ │
│  │ /api/*        │  │ /predict/*   │  │ /ws/*         │ │
│  │ Port 8000     │  │ Port 5001    │  │ (real-time)   │ │
│  └──────┬───────┘  └──────┬───────┘  └───────────────┘ │
│         │                  │                             │
│  ┌──────▼──────────────────▼──────────────────────────┐ │
│  │           Shared Intelligence Layer                 │ │
│  │                                                     │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │ │
│  │  │ Apache Jena  │  │ Zep Cloud   │  │ PostgreSQL │ │ │
│  │  │ (OWL+SPARQL  │  │ (Agent      │  │ + pgvector │ │ │
│  │  │  +Reasoner)  │  │  Memory)    │  │            │ │ │
│  │  └─────────────┘  └─────────────┘  └────────────┘ │ │
│  │                                                     │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │ │
│  │  │ Redis        │  │ Celery      │  │ MinIO      │ │ │
│  │  │ (cache+queue)│  │ (workers)   │  │ (files)    │ │ │
│  │  └─────────────┘  └─────────────┘  └────────────┘ │ │
│  └────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### MiroFish Service Integration Points

| MiroFish Component | SNOWKAP Integration | How It Connects |
|---|---|---|
| `graph_builder.py` | Feeds from Apache Jena knowledge graph | Jena SPARQL → MiroFish GraphRAG seed |
| `ontology_generator.py` | Uses SNOWKAP's `sustainability.ttl` base ontology | Shared OWL classes + ESG domain |
| `simulation_manager.py` | Triggered by Celery task on high-impact news | `POST /predict/simulate` from FastAPI |
| `simulation_runner.py` | OASIS framework runs agent simulation | Parallel process, results → PostgreSQL |
| `report_agent.py` | Generates prediction reports for SNOWKAP UI | Report JSON → React dashboard cards |
| `zep_entity_reader.py` | Reads company entities from shared Zep memory | Zep Cloud as shared agent memory |
| `zep_graph_memory_updater.py` | Updates causal graph after each simulation | Simulation outcomes → Jena triples |
| `oasis_profile_generator.py` | Creates ESG analyst agent personas per company | Company context → agent personality |
| `simulation_config_generator.py` | Config from tenant settings + news context | Tenant config JSONB → sim params |

### MiroFish Trigger Conditions

```python
TRIGGER_CONDITIONS = {
    "impact_score_threshold": 70,        # High-impact news only
    "causal_chain_hops": 2,              # At least 2-hop indirect impact
    "financial_exposure_min": 1_000_000,  # ₹10L+ estimated exposure
    "framework_criticality": "high",      # Critical framework alignment
    "user_requested": True,               # Manual "predict impact" button
}
```

---

## Part 3: 3-Way Login System (No OTP, No Passwords)

### Login Flow

```
Step 1: Enter Company Domain → e.g., "mahindra.com"
  System: Auto-resolves company name, industry, existing ESG profile
  Validation: Domain must be a real corporate domain (not gmail/yahoo)

Step 2: Select/Enter Designation → e.g., "Head of Sustainability"
  System: Determines role permissions, dashboard focus, game access

Step 3: Confirm Company Name + Enter Work Email → e.g., "Mahindra Logistics Ltd"
  System: Provisions tenant if new, loads existing if returning
  Validation: Email domain must match company domain entered in Step 1

→ Magic link sent to work email → Click link → JWT issued with:
  {tenant_id, company_id, designation, permissions[], domain}

No passwords. No OTP codes. Just domain verification via magic link.
Returning users: just enter email → magic link → in.
```

### Why No OTP / No Passwords
- **Faster onboarding**: 3 clicks + email check, no password to remember
- **More secure**: No password database to breach, no OTP interception
- **Domain-gated**: Only users with @mahindra.com email can access Mahindra's tenant
- **Auto-provisioning**: First user from a new domain creates the tenant automatically

### Domain-Driven App Behavior

```
Domain "mahindra.com" entered at login
    ↓
1. COMPANY RESOLUTION
   - Check existing companies table for domain match
   - If new: Claude classifies industry (45 SASB categories)
   - Generate sustainabilityQuery + generalQuery

2. NEWS CURATION (automatic, domain-driven)
   - Google News RSS: "{Company Name}" + ESG keywords
   - Google News RSS: "{Company Name}" + industry keywords
   - Google News RSS: "{domain}" general news
   - Filter by industry material issues

3. ONTOLOGY SEEDING (automatic)
   - Create company node in Jena knowledge graph
   - Link to industry → material issues → frameworks
   - Link to geographic locations (from company profile)
   - Link to known supply chain entities

4. SMART ANALYSIS (continuous)
   - Every new article → entity extraction → causal chain check
   - Score: "Does this news affect THIS company through ANY path?"
   - If yes → generate impact card with causal explanation
   - If high impact → trigger MiroFish prediction
```

---

## Part 3B: The Agency — AI Specialist Agents Integration

### Build-Time Agents (used during development)

| Agent | Division | How It's Used |
|---|---|---|
| Frontend Developer | Engineering | React 19 UI, causal chain visualizations |
| Backend Architect | Engineering | FastAPI service layer, Jena integration |
| AI Engineer | Engineering | LangGraph agent, MiroFish config, prompts |
| DevOps Automator | Engineering | Docker Compose, GitHub Actions, Nginx |
| Security Engineer | Engineering | JWT auth, tenant isolation audit, RBAC |
| Database Optimizer | Engineering | PostgreSQL + pgvector, SQLAlchemy, Alembic |
| Software Architect | Engineering | System design, API contracts |
| Code Reviewer | Engineering | PR reviews, quality gates |
| Technical Writer | Engineering | API docs, architecture docs |
| UI Designer | Design | News feed cards, causal chain UI |
| UX Researcher | Design | Login usability, feed UX |
| Senior PM | PM | Phase tracking, milestones |
| API Tester | Testing | Endpoint validation, tenant isolation |
| Performance Benchmarker | Testing | Load testing |
| Reality Checker | Testing | Causal chain accuracy |
| Accessibility Auditor | Testing | WCAG compliance |

### Runtime Agents (embedded in product as ESG specialists)

| Agent | Product Role | What It Does |
|---|---|---|
| Supply Chain Specialist | ESG Supply Chain Analyst | Maps supply chain graphs, Scope 3 exposure, upstream/downstream risks |
| Compliance Auditor | ESG Compliance Monitor | BRSR/GRI/TCFD/ESRS compliance, gap flags, disclosure checklists |
| Analytics Reporter | ESG Analytics Agent | Executive summaries, trend reports, KPI dashboards |
| Executive Summary Generator | CXO Briefing Agent | C-suite ESG briefings from news + predictions |
| Trend Researcher | ESG Trend Scout | Emerging ESG trends from news patterns, regulatory signals |
| Feedback Synthesizer | Stakeholder Voice Agent | Stakeholder feedback, ESG ratings, investor concerns |
| Growth Hacker | ESG Opportunity Finder | Green revenue opportunities, ESG-driven market advantages |
| Content Creator | ESG Report Writer | Sustainability report sections, newsletter content |
| Legal Compliance Checker | Regulatory Intelligence Agent | Regulatory changes (EU CBAM, SEBI ESG, EPA), company exposure |

### Agent Integration with LangGraph

```python
# In backend/agent/graph.py
AGENT_ROSTER = {
    "supply_chain": load_agency_agent("specialized/supply-chain-specialist"),
    "compliance": load_agency_agent("support/legal-compliance-checker"),
    "analytics": load_agency_agent("support/analytics-reporter"),
    "executive": load_agency_agent("support/executive-summary-generator"),
    "trend": load_agency_agent("product/trend-researcher"),
    "content": load_agency_agent("marketing/content-creator"),
}

# Agent routing in classify_intent node:
def classify_intent(state):
    question = state["question"]
    selected_agent = route_to_specialist(question, AGENT_ROSTER)
    return {**state, "active_agent": selected_agent}
```

Each agent's system prompt (from Agency markdown files) becomes the personality layer on top of SNOWKAP tools (SPARQL, DB queries, MiroFish predictions).

---

## Part 4: Complete Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| Frontend | React 19 + Vite + Tailwind + Radix UI + Zustand + TanStack Query | UI with real-time updates |
| API | FastAPI + Pydantic v2 (Python 3.12) | Main backend, async-first |
| Prediction | MiroFish (Python 3.12, OASIS, Zep) | Multi-agent simulation |
| Database | PostgreSQL 16 + pgvector + Alembic | Data + vector search |
| ORM | SQLAlchemy 2.0 async + asyncpg | Type-safe DB access |
| Knowledge Graph | Apache Jena (Fuseki + TDB2 + OWL Reasoner) | Causal ontology + inference |
| Agent Memory | Zep Cloud | Persistent agent memory |
| AI/LLM | Anthropic Claude (claude-sonnet-4-6) + OpenAI GPT-4o (legacy) | Analysis + generation |
| Agent Framework | LangGraph 0.2+ | Stateful AI agent with tools |
| Cache/Queue | Redis 7 | Caching, pub/sub, Celery broker |
| Task Queue | Celery 5.4+ | Background processing |
| Real-Time | Socket.IO + Redis pub/sub | Live updates |
| File Storage | MinIO | S3-compatible self-hosted |
| Auth | JWT + Magic Links + RBAC | 3-way login, no passwords |
| AI Agents | The Agency (127+ personalities) | 9 runtime + 16 build-time agents |
| News | Google News RSS + NewsAPI | Domain-driven curation |
| Email | Resend (async via Celery) | Magic links, newsletters, alerts |
| Monitoring | structlog + Sentry | Structured logging + errors |
| Infrastructure | Docker Compose (8 services) + Nginx + GitHub Actions | Containerized CI/CD |
| Mobile | Capacitor 7.4 (Android) | Native wrapper |

---

## Part 5: Phased Build Plan

### Phase 0 — Project Init (1 day)
- [ ] Create `CLAUDE.md`
- [ ] Create `backend/` Python directory structure alongside existing `server/`
- [ ] Create `requirements.txt` with all dependencies
- [ ] Create `.env.example` with all variables
- [ ] Git init, `.gitignore`, connect to GitHub

### Phase 1 — Dev Environment (2 days)
- [ ] Python 3.12 venv + all dependencies installed
- [ ] Docker Compose with 8 services: `esg-api`, `esg-worker`, `mirofish`, `postgres`, `redis`, `jena-fuseki`, `minio`, `nginx`
- [ ] Keep existing Express as `esg-legacy` temporarily (Nginx routes traffic)
- [ ] Verify: all services healthy, FastAPI `/health` → 200, Fuseki `/$/ping` → 200
- [ ] Clone MiroFish repo into `prediction/` directory, configure as submodule or vendored

### Phase 2 — Domain Database & API (5-7 days)
- [ ] Translate 47 Drizzle tables → SQLAlchemy models with `tenant_id` on every table
- [ ] **Critical split:** Separate `tenants` (Snowkap customers) from `companies` (ESG analysis targets)
- [ ] New tables: `tenants`, `tenant_memberships`, `tenant_roles`, `tenant_config`, `causal_chains`, `supply_chain_links`, `geographic_dependencies`, `prediction_reports`
- [ ] Alembic initial migration against existing data
- [ ] Build modular FastAPI routers: `auth.py`, `companies.py`, `analysis.py`, `news.py`, `predictions.py`, `ontology.py`, `campaigns.py`, `admin.py`
- [ ] Seed script with 2 test tenants + company data

### Phase 2B — Multitenancy Foundation (6-8 days)
- [ ] `TenantContext` dependency injected into every route
- [ ] `tenant_id` filter on every SELECT/INSERT
- [ ] Redis: tenant-namespaced caching (config TTL 5min, news TTL 15min)
- [ ] Socket.IO: tenant-scoped rooms for real-time updates
- [ ] Tenant config API: workflow stages, custom fields, business rules (JSONB)
- [ ] **Gate test:** Tenant A data invisible to Tenant B

### Phase 2C — 3-Way Login System (3-4 days)
- [ ] Login flow: Domain → Designation → Company Name → Magic Link
- [ ] `POST /auth/resolve-domain` — takes domain, returns company info or creates prospect
- [ ] `POST /auth/magic-link` — sends login link to work email (domain must match)
- [ ] `GET /auth/verify/{token}` — validates magic link, issues JWT
- [ ] Industry auto-classification via Claude (45 SASB categories)
- [ ] Auto-generate `sustainabilityQuery` + `generalQuery` from domain + industry
- [ ] Designation → role mapping: CXO → executive_view, Head of Sustainability → sustainability_manager, Analyst → data_entry_analyst
- [ ] JWT claims: `{tenant_id, user_id, company_id, designation, permissions[], domain}`
- [ ] Auto-provision company node in Jena knowledge graph on first login
- [ ] Domain-driven news curation starts automatically after login
- [ ] Returning users: email-only login → magic link → JWT (skip domain/designation)

### Phase 3 — Smart ESG Ontology (8-12 days) **[THE CORE DIFFERENTIATOR]**
- [ ] **3.1 Base Ontology** (`sustainability.ttl`)
  - Core classes: Company, Facility, Supplier, Regulation, MaterialIssue, ESGFramework, GeographicRegion, Industry, Commodity, WorkforceSegment
  - 8 ESG framework classes (BRSR, ESRS, GRI, IFRS, CDP, TCFD, CSRD, SASB)
  - Causal relationship properties: `directlyImpacts`, `indirectlyImpacts`, `suppliesTo`, `sourcesFrom`, `locatedIn`, `regulatedBy`, `competessWith`, `employsWorkforce`
- [ ] **3.2 Causal Chain Engine**
  - Entity extraction from news (Claude NER)
  - Entity resolution against Jena knowledge graph (fuzzy match + semantic similarity)
  - Causal graph traversal: BFS/DFS from news entity to company node, max 4 hops
  - Impact scoring: decay function per hop (direct=1.0, 1-hop=0.7, 2-hop=0.4, 3-hop=0.2)
  - Path explanation: human-readable causal chain
- [ ] **3.3 Geographic Intelligence**
  - Company → facility locations (lat/lng, district, state)
  - News → location extraction
  - Proximity matching: "water scarcity in Kolhapur" → "you have a plant in Kolhapur"
  - Climate/disaster risk zones per geography
- [ ] **3.4 Supply Chain Graph**
  - Company → Tier 1 suppliers (public data + user input)
  - Industry → typical supply chain shape (auto-generated via Claude)
  - Commodity dependency mapping
  - Scope 3 category linkage
- [ ] **3.5 Tenant Business Rules as OWL Axioms**
  - BusinessRuleCompiler: tenant rules → OWL axioms → Jena named graph
  - Mathematical inference: threshold-based auto-classification
  - Human assertion: domain-specific classifications via admin UI
  - Permission-gated: admin creates rules, users assert facts
- [ ] **3.6 Ontology API**
  - SPARQL queries scoped to tenant named graph
  - Rule CRUD
  - Inference dashboard
  - Causal chain explorer

### Phase 4 — MiroFish Prediction Engine (8-10 days)
- [ ] **4.1 MiroFish Setup** — Deploy as Docker service, configure Zep Cloud + LLM
- [ ] **4.2 ESG-Specific Agent Profiles** — CEO, Sustainability Officer, Supply Chain Manager, Regulator, Competitor, Community/NGO agents (20-50 per sim)
- [ ] **4.3 Integration Pipeline** — Celery task triggers prediction, Jena subgraph as seed, 10-40 rounds, results → DB + Jena
- [ ] **4.4 Prediction UI** — "What If" cards, scenario explorer, confidence scoring, accuracy tracking

### Phase 5 — LangGraph AI Agent + The Agency (8-10 days)
- [ ] Clone `msitarzewski/agency-agents` → `agency/` directory
- [ ] Copy relevant agents to `~/.claude/agents/` for build-time use
- [ ] LangGraph state machine: `load_context → classify_intent → route_to_specialist → query → synthesise`
- [ ] Agent Router: classify user intent → select specialist
- [ ] Load 9 runtime Agency agent personalities as LangGraph specialist nodes
- [ ] Each agent gets: personality prompt + SNOWKAP tools (sparql, db, causal_chain, prediction, ontology_rule)
- [ ] `TenantMemoryManager` with Zep Cloud
- [ ] Switch primary LLM to Claude
- [ ] Agent selection visible in chat UI

### Phase 6 — Frontend Overhaul (7-10 days)
- [ ] Upgrade React 18 → 19
- [ ] Add Zustand stores: `authStore`, `tenantConfigStore`, `newsStore`
- [ ] 3-way login UI
- [ ] Smart News Feed with causal impact chains
- [ ] Impact Cards: visual causal chain (node graph)
- [ ] Prediction Dashboard: MiroFish results, scenario explorer
- [ ] Ontology Explorer: visual knowledge graph
- [ ] Socket.IO real-time updates
- [ ] Keep existing Radix UI, Tailwind, Recharts, all 31 pages

### Phase 7 — RBAC & Platform Admin (4-5 days)
- [ ] ESG permission sets
- [ ] Designation → role mapping
- [ ] JWT with embedded permissions
- [ ] Platform admin console
- [ ] Ontology Rules UI: Rule Builder, Inference Dashboard, Assertion Queue

### Phase 8 — Docker & Deployment (2-3 days)
- [ ] Multi-stage Dockerfiles for all 8 services
- [ ] Production `docker-compose.prod.yml`
- [ ] Nginx routing: `/api/` → FastAPI, `/predict/` → MiroFish, `/ws/` → Socket.IO, `/` → frontend
- [ ] Health checks, non-root users, named volumes

### Phase 9 — CI/CD & Monitoring (2-3 days)
- [ ] GitHub Actions: lint → test → build → deploy
- [ ] structlog with tenant_id on every line
- [ ] Sentry error tracking
- [ ] Automated backups (daily 2am): pg_dump + LZMA + Jena graph archive
- [ ] Uptime monitoring on `/health`

### Phase 10 — Multimodal Intelligence (10-14 days)
- [ ] Celery pipeline: upload → MinIO → processor → pgvector embedding
- [ ] Processors: PDF (pdfplumber), image (Claude Vision), audio (Whisper), spreadsheet (openpyxl)
- [ ] Extracted data → entity extraction → feed into Jena ontology
- [ ] Semantic search across all tenant media

### Phase 11 — User-Scoped Agent Chat (6-8 days)
- [ ] `UserAgentContext` with auth-parity
- [ ] Confirmation-gated writes
- [ ] Conversation threads with Zep memory
- [ ] "Ask about this news" → agent explains causal chain + triggers prediction

---

## Part 6: Priority Order

| # | Phase | Why This Order | Est. Days |
|---|-------|---------------|-----------|
| 1 | 0+1 (Init + Dev Env) | Foundation | 3 |
| 2 | 2+2B+2C (Database + Multitenancy + Login) | Can't scale without tenant isolation + new login | 14-19 |
| 3 | 3 (Smart ESG Ontology) | **The product differentiator** | 8-12 |
| 4 | 4 (MiroFish Predictions) | Builds on ontology, delivers "wow" factor | 8-10 |
| 5 | 8 (Docker) | Enables horizontal scaling | 2-3 |
| 6 | 7 (RBAC) | Enterprise readiness | 4-5 |
| 7 | 5 (LangGraph Agent + The Agency) | 9 specialist agents as conversational interface | 8-10 |
| 8 | 6 (Frontend) | New UI for causal chains + predictions | 7-10 |
| 9 | 9 (CI/CD + Monitoring) | Operational maturity | 2-3 |
| 10 | 10 (Multimodal) | Enhancement | 10-14 |
| 11 | 11 (Agent Chat) | Polish | 6-8 |
| **TOTAL** | | | **74-101 days** |

---

## Part 7: Critical Risks & Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Data migration (company_id → tenant_id split) | CRITICAL | Run parallel databases, migrate in stages |
| MiroFish resource consumption | HIGH | Limit 20-50 agents, 10-40 rounds, trigger only on score >70 |
| Zep Cloud dependency | MEDIUM | Can self-host Zep; PostgreSQL-backed memory as fallback |
| Apache Jena complexity | HIGH | Start with rdflib for dev, Jena for prod |
| OpenAI → Claude prompt drift | MEDIUM | Migrate prompts incrementally, A/B test outputs |
| Zero test coverage | HIGH | Write integration tests for existing API before migrating |
| Mobile app API break | MEDIUM | Keep Express serving mobile during transition, API versioning |
| Causal chain accuracy | MEDIUM | Start with 2-hop max, human validation queue, confidence scoring |
| MiroFish AGPL-3.0 license | HIGH | Run as separate microservice (process isolation) |

---

## Part 8: What to Preserve vs Rebuild

### Preserve
- React frontend (31 pages, 30+ components) — upgrade in-place
- ESG domain knowledge (8 frameworks, 45 industries, 20+ company profiles) — encode into ontology
- Recommendation engine logic (scoring, topics, embeddings) — port to Python
- Google News RSS integration — keep, enhance with causal chain post-processing
- pgvector embeddings — keep
- Capacitor mobile setup — keep
- Database data — migrate with schema transforms

### Rebuild in Python
- All 169 Express routes → modular FastAPI routers
- Auth system → JWT with permissions + 3-way login
- Database layer → SQLAlchemy 2.0 async + Alembic
- Scheduler → Celery beat
- AI pipeline → LangGraph + Claude
- Email → Celery async tasks

### Create New
- Smart ESG Ontology (Jena + causal chains)
- MiroFish prediction integration
- The Agency — 9 runtime ESG specialist agents + 16 build-time dev agents
- 3-way login (domain → designation → company → magic link)
- Redis, Celery, MinIO, Nginx, Socket.IO
- GitHub Actions CI/CD
- Sentry + structured logging
- Test suite (pytest)
- Zustand stores
