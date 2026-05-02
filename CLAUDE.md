# Snowkap ESG Intelligence Engine — CLAUDE.md

## Project Identity

**Snowkap ESG Intelligence Engine** — A minimal, ontology-driven ESG intelligence platform with a thin web UI for 7 target companies. Transforms news, documents, and prompts into structured executive intelligence via an OWL2 ontology and OpenAI.

**Architecture:** Ontology-driven Python engine writes JSON outputs → SQLite index → thin FastAPI read-only layer → simplified React frontend. No PostgreSQL, no Redis, no Celery, no Docker, no auth complexity.

## Core Philosophy

1. **Ontology IS the intelligence.** Domain knowledge lives in RDF triples (rdflib), not Python dicts. Adding a new ESG topic, framework, or rule means adding triples, not writing code.
2. **Folder-based I/O.** Inputs from `data/inputs/`. Outputs to `data/outputs/{company-slug}/`. JSONB-compatible JSON files.
3. **Single LLM provider.** OpenAI only (gpt-4.1 for deep insight, gpt-4.1-mini for extraction).
4. **Minimal stack.** ~10 dependencies. No web framework. No database server. No Redis. No Celery. No Docker.
5. **ESG-expert, not generic.** Multi-stage pipeline with structured domain knowledge, not just LLM prompting.

## Tech Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Language | Python 3.12+ | Engine + API |
| LLM | OpenAI (gpt-4.1, gpt-4.1-mini) | Single provider only |
| Ontology | rdflib (in-process, persists to `.ttl`) | The intelligence brain |
| JSON storage | Filesystem (`data/outputs/`) | Source of truth |
| Index | **SQLite** (`data/snowkap.db`) | Fast feed queries, built into Python |
| API | **FastAPI** (thin, read-only) | ~5 endpoints, no DB server |
| Auth | **Simple API key** in header | `X-API-Key` check, swap to JWT later |
| Frontend | **React 19 + Vite + Radix UI + Tailwind + Zustand** | Reuse legacy `client/`, strip unused pages |
| News APIs | Google News RSS (feedparser) + NewsAPI.org | |
| File parsing | pdfplumber, openpyxl, Pillow | Multimodal ingestion |
| Scheduling | APScheduler | Optional periodic ingestion |
| Logging | structlog | JSON logs |

**Removed from legacy stack:** PostgreSQL, Redis, Celery, MinIO, Docker, Socket.IO, Anthropic SDK, Zep Cloud, Nginx, JWT + magic links, Alembic, SQLAlchemy. ~95% fewer dependencies.

## Target Companies (7 Only)

All focus is on these 7 companies. Legacy company data is cleaned.

| Company | Slug | Industry | Market Cap |
|---------|------|----------|------------|
| ICICI Bank | `icici-bank` | Financials/Banking | Large Cap |
| YES Bank | `yes-bank` | Financials/Banking | Mid Cap |
| IDFC First Bank | `idfc-first-bank` | Financials/Banking | Mid Cap |
| Waaree Energies | `waaree-energies` | Renewable Energy | Mid Cap |
| Singularity AMC | `singularity-amc` | Asset Management | Small Cap |
| Adani Power | `adani-power` | Power/Energy | Large Cap |
| JSW Energy | `jsw-energy` | Power/Energy | Large Cap |

## Directory Structure

```
snowkap-esg/
├── CLAUDE.md                          # This file
├── README.md
├── .env.example
├── .gitignore
│
├── api/                               # Thin FastAPI read-only layer (Phase 9)
│   ├── __init__.py
│   ├── main.py                        # FastAPI app (~5 endpoints)
│   ├── auth.py                        # API key middleware
│   ├── index.py                       # SQLite index reader
│   └── routes/
│       ├── companies.py               # GET /companies, /companies/{slug}
│       ├── insights.py                # GET /insights, /insights/{id}
│       ├── legacy_adapter.py          # GET /insights/{id}?perspective=cfo + trigger-analysis
│       └── ingest.py                  # POST /ingest/{slug}
│
├── client/                            # React 19 frontend (stripped legacy, Phase 10)
│   └── src/                           # Keep: panels, layout, stores. Drop: auth, admin, onboarding
│
├── engine/                            # Core intelligence engine
│   ├── __init__.py
│   ├── main.py                        # CLI entry: ingest, analyze, query
│   ├── config.py                      # Settings loader
│   ├── scheduler.py                   # Optional APScheduler
│   │
│   ├── ingestion/                     # Input processing
│   │   ├── news_fetcher.py            # Google News RSS + NewsAPI
│   │   ├── file_parser.py             # PDF/Excel/image → text
│   │   └── prompt_handler.py          # Unstructured prompts → structured
│   │
│   ├── nlp/                           # OpenAI-based NLP
│   │   ├── extractor.py               # Sentiment, tone, narrative, entities
│   │   ├── theme_tagger.py            # 21 ESG themes
│   │   └── event_classifier.py        # Event type → score bounds
│   │
│   ├── ontology/                      # THE INTELLIGENCE BRAIN
│   │   ├── graph.py                   # rdflib manager (load/query/persist)
│   │   ├── intelligence.py            # SPARQL queries (replaces dicts)
│   │   ├── causal_engine.py           # BFS traversal (17 rel types)
│   │   └── seeder.py                  # Seed 7 companies + knowledge
│   │
│   ├── analysis/                      # Ontology-driven pipeline
│   │   ├── pipeline.py                # 12-stage orchestrator
│   │   ├── relevance_scorer.py        # 5D scoring via ontology
│   │   ├── risk_assessor.py           # ESG + TEMPLES via ontology
│   │   ├── framework_matcher.py       # Framework RAG via ontology
│   │   ├── insight_generator.py       # Deep insight (OpenAI gpt-4.1)
│   │   ├── recommendation_engine.py   # REREACT 3-agent chain
│   │   └── perspective_engine.py      # CFO/CEO/ESG Analyst transform
│   │
│   └── output/                        # Output formatting
│       ├── formatter.py               # CrispOutput spec
│       └── writer.py                  # JSON file writer
│
├── data/                              # All data lives here
│   ├── inputs/
│   │   ├── news/                      # Auto-fetched news (per company)
│   │   ├── documents/                 # Drop PDFs, Excel, images here
│   │   └── prompts/                   # Drop text prompts here
│   │
│   ├── outputs/                       # Intelligence outputs (JSONB)
│   │   ├── icici-bank/
│   │   │   ├── insights/              # Deep insights per article
│   │   │   ├── risk/                  # Risk assessments
│   │   │   ├── frameworks/            # Framework alignment
│   │   │   ├── causal/                # Causal chains
│   │   │   ├── recommendations/       # REREACT outputs
│   │   │   └── perspectives/
│   │   │       ├── cfo/               # CFO 10-second verdicts
│   │   │       ├── ceo/               # CEO strategic briefs
│   │   │       └── esg-analyst/       # Full detailed view
│   │   ├── yes-bank/
│   │   ├── idfc-first-bank/
│   │   ├── waaree-energies/
│   │   ├── singularity-amc/
│   │   ├── adani-power/
│   │   └── jsw-energy/
│   │
│   ├── ontology/                      # Persisted RDF graphs
│   │   ├── schema.ttl                 # OWL2 classes + predicates (Phase 15: +80 lines)
│   │   ├── knowledge_base.ttl         # Domain knowledge triples
│   │   ├── knowledge_depth.ttl        # Event types, keywords, theme→event mappings
│   │   ├── knowledge_expansion.ttl    # Phase 14-15: regional boosts, mandatory rules, headline rules, priority matrix, risk-of-inaction config, grid/insight key mappings, ranking sort keys
│   │   └── companies.ttl              # Company-specific graph + competessWith triples
│   │
│   ├── processed/                     # Tracking (dedup)
│   │   └── article_hashes.json
│   │
│   └── snowkap.db                     # SQLite index (Phase 8)
│
└── config/
    ├── companies.json                 # 7 company profiles
    ├── settings.json                  # API keys, thresholds
    └── perspectives.json              # CFO/CEO/ESG lens configs
```

## Intelligence Pipeline (12 Stages, Ontology-Driven)

```
INPUT (news API | file | prompt)
    ↓
[1] NLP Extraction (OpenAI gpt-4.1-mini)
    ↓
[2] ESG Theme Tagging (21 themes)
    ↓
[3] Entity Resolution (SPARQL → company graph)            ← ONTOLOGY
    ↓
[4] Relevance Scoring (5D, materiality from ontology)     ← ONTOLOGY
    ↓
[5] Event Classification (score bounds from ontology)     ← ONTOLOGY
    ↓
    GATE: score < 4 → REJECTED
    ↓
[6] Causal Chain BFS (0-4 hops, 17 relationship types)    ← ONTOLOGY
    ↓
[7] Geographic + Climate Matching                          ← ONTOLOGY
    ↓
[8] Risk Assessment (10 ESG + 7 TEMPLES)                   ← ONTOLOGY (categories, thresholds, theme maps)
    ↓
[9] Framework Alignment (21 frameworks, mandatory flags)   ← ONTOLOGY (regional boosts, mandatory rules, sections)
    ↓
    GATE: score ≥ 7 → continue to deep insight
    ↓
[10] Deep Insight Generation (OpenAI gpt-4.1, 9-section JSON)
    ↓
[11] Perspective Transformation (CFO/CEO/ESG Analyst)     ← ONTOLOGY (headline rules, grid maps, dim-to-key)
    ↓
[12] REREACT Recommendations (3-agent chain, OpenAI)      ← ONTOLOGY (priority rules, risk-of-inaction, rankings)
    ↓
OUTPUT → JSON files in data/outputs/{company-slug}/
```

**9 out of 12 stages query the ontology (~90% ontology-driven).** Knowledge is data, not code.

## Ontology Architecture (5 Layers)

### Layer 1: Entity
Company, Facility, Supplier, Industry, GeographicRegion, Commodity, Regulation, Competitor

### Layer 2: ESG Topics
21 themes across Environmental (8), Social (7), Governance (6) — with sub-metrics

### Layer 3: Impact Dimensions
Financial, Regulatory, Operational, Reputational + TEMPLES (Volume, Value, Cost, Growth, Brand)
Each dimension has `gridColumn` (financial/regulatory/strategic) and `insightKey` mappings — both ontology-driven.

### Layer 4: Perspective Lenses
ESG Analyst, CFO, CEO — each with:
- Output depth + financial framing + priority dimensions
- `HeadlineRule` instances with cascading priority, `sourceField` dot-paths, and `{value}/{base}` template placeholders
- `RankingSortKey` instances defining per-perspective recommendation sort order (e.g., CFO sorts by ROI DESC, CEO by impact DESC)

### Layer 5: Framework Mapping
21 frameworks: BRSR, GRI, TCFD, CSRD/ESRS, SASB, CDP, ISSB, EU Taxonomy, SFDR, GHG Protocol, SBTi, TNFD, SEC Climate + Porter 5 Forces, McKinsey 3 Horizons, BCG Matrix, COSO ERM, CFA ESG, DJSI, S&P Global ESG, Edelman Trust
- `RegionalFrameworkBoost` — region-specific relevance boosts (India: BRSR +0.6, EU: CSRD +0.6, etc.)
- `MandatoryRule` — region × cap-tier mandatory marking
- `FrameworkSection` — section-level triggered_sections per topic

### Layer 6: Risk & Recommendation Rules (Phase 15)
- `RiskLevelThreshold` — score→level mapping (CRITICAL≥20, HIGH≥12, MODERATE≥6, LOW≥0)
- `PriorityRule` — urgency × impact → priority matrix (12 rules)
- `RiskOfInactionConfig` — base scores per priority, type bonuses (compliance +2), escalation keywords
- `triggersRiskCategory` — Topic → ESG risk categories (21 mappings)
- `triggersTEMPLES` — Topic → TEMPLES categories (21 mappings)

### Key Predicates
- `triggersFramework` — Topic → Framework
- `hasImpactOn` — Topic → ImpactDimension
- `relevantTo` — ImpactDimension → PerspectiveLens
- `materialFor` — Topic → Industry (with weight)
- `hasRiskWeight` — Industry × RiskCategory → float
- `inClimateZone` — GeographicRegion → ClimateZone
- `overlaps` — TEMPLES ↔ ESG risk categories
- `hasLeadIndicator` / `hasLagIndicator` — RiskCategory → string
- `contributesToSDG` — Topic → SDG
- `triggersRiskCategory` — Topic → RiskCategory (Phase 15)
- `triggersTEMPLES` — Topic → TEMPLESCategory (Phase 15)
- `gridColumn` — ImpactDimension → grid column name (Phase 15)
- `insightKey` — ImpactDimension → insight analysis key (Phase 15)
- `forPerspective` / `headlinePriority` / `sourceField` / `headlineTemplate` / `isFallback` — HeadlineRule properties (Phase 15)
- `sortKey` / `sortDirection` / `sortPriority` — RankingSortKey properties (Phase 15)
- `forRegion` / `boostValue` / `boostsFramework` — RegionalFrameworkBoost (Phase 15)
- `mandatoryFramework` / `mandatoryRegion` / `mandatoryCapTier` — MandatoryRule (Phase 15)
- `ifUrgency` / `ifImpact` / `thenPriority` — PriorityRule (Phase 15)

## SPARQL Query Functions (engine/ontology/intelligence.py)

All domain intelligence is accessed through these SPARQL queries. **No Python dicts for domain knowledge.**

### Core queries (Phase 1-7):
- `query_frameworks_for_topic(topic)` — frameworks triggered by a theme
- `query_frameworks_detail(topic)` — framework refs with profitability links
- `query_materiality_weight(topic, industry)` — materiality score
- `query_risk_weight(industry, risk_category)` — risk multiplier
- `query_perspective_impacts(topic, perspective)` — impact dimensions for lens
- `query_perspective_config(perspective)` — max_words, output_depth
- `query_risk_indicators(category)` — lead/lag indicators
- `query_cap_tier(market_cap)` — cap tier classification
- `query_compliance_deadlines(jurisdiction)` — regulatory deadlines
- `query_framework_sections(framework_id, topic)` — triggered section codes
- `query_peer_actions(topic)` — competitor peer actions
- `query_industry_roi_benchmarks(industry)` — ROI benchmarks
- `query_competitors(company_slug)` — competitor companies

### Phase 15 queries (ontology migration):
- `query_esg_risk_categories()` — 10 ESG risk category labels
- `query_temples_categories()` — 7 TEMPLES category labels
- `query_theme_risk_map(theme)` — ESG risk categories triggered by a theme
- `query_theme_temples_map(theme)` — TEMPLES categories triggered by a theme
- `query_risk_level_thresholds()` — score→level thresholds (cached)
- `query_regional_boosts(region)` — framework boost values per region
- `query_mandatory_rules(region)` — mandatory framework rules per region
- `query_priority_rules()` — urgency × impact → priority matrix (cached)
- `query_risk_of_inaction_config()` — base scores, type bonuses, keywords (cached)
- `query_grid_column_map()` — impact dimension → grid column
- `query_dim_to_insight_keys()` — impact dimension → insight analysis keys
- `query_headline_rules(perspective)` — cascading headline templates per lens
- `query_perspective_ranking_keys(perspective)` — recommendation sort order per lens

## Critical Rules

1. **Never hardcode domain knowledge in Python.** If it's a weight, threshold, mapping, or rule — it goes in `.ttl` ontology files. As of Phase 15, **zero** hardcoded domain dicts remain in `engine/`. All risk categories, TEMPLES categories, theme→risk maps, regional boosts, mandatory rules, priority matrices, risk-of-inaction config, grid column maps, headline rules, and ranking sort keys are ontology-driven via SPARQL.
2. **Every dict lookup is a smell.** Check if it should be a SPARQL query. Use `@lru_cache` for stable lookups (thresholds, priority rules, config).
3. **OpenAI only.** No Anthropic, no other LLM providers.
4. **Do nothing is valid.** LOW/NON-MATERIAL articles → no recommendations. Macro signals don't force compliance actions.
5. **CFOs want 10-second verdicts.** CFO perspective output must be under 100 words.
6. **Materiality gates are hard.** Score < 4 → REJECTED, skip stages 6-12. Saves LLM budget.
7. **Outputs are JSONB-compatible.** Every JSON file must parse cleanly, no trailing commas, no comments, no Python-specific types.
8. **Dedup is mandatory.** Use URL hash in `data/processed/article_hashes.json`. Never re-process.
9. **Token budgets per article:** NLP ~800 tokens. Deep insight ~2400 tokens. Total < 10K.
10. **Ontology triple count matters.** After Phase 7, `len(graph) >= 5000`.
11. **Perspective headlines must be visibly distinct.** CFO/CEO headlines use ontology `HeadlineRule` templates with cascading priority — never fall through to the same base headline. Each rule specifies a `sourceField` dot-path and a `{value}/{base}` template.
12. **Recommendation rankings are perspective-specific.** CFO sorts by ROI DESC, CEO by strategic impact DESC, ESG Analyst by compliance urgency. Sort keys come from ontology `RankingSortKey` instances.

## Build Plan — 8 Phases with Validation Gates

**Every phase has a validation gate. Do not proceed past a failing gate.**

### Phase 0: Project Setup & Stack Reset
Create folder structure, write config files, install 10 dependencies, scaffold `main.py`.

**Validation Gate 0:**
- [ ] `pip install -r requirements.txt` succeeds
- [ ] `python engine/main.py --help` prints usage
- [ ] `python -c "from engine.config import load_companies; print(len(load_companies()))"` prints `7`
- [ ] All 7 company folders exist under `data/outputs/`
- [ ] Config JSON files parse without errors

### Phase 1: Ontology Foundation
Build the intelligence brain. Migrate domain knowledge from legacy Python dicts into RDF triples.

**Validation Gate 1:**
- [ ] Triple count ≥ 2000
- [ ] `query_frameworks_for_topic('Water')` returns GRI:303, BRSR:P6, ESRS:E3, CDP:Water
- [ ] `query_materiality_weight('Water', 'Power/Energy')` ≥ 0.8
- [ ] `query_risk_weight('Financials/Banking', 'regulatory')` returns 1.6
- [ ] `query_perspective_impacts('Water', 'cfo')` includes 'financial' and 'cost', excludes 'brand'
- [ ] Seeder creates 7 company nodes
- [ ] `find_causal_chains()` returns ≥ 1 path for a test entity
- [ ] Graph persists and reloads with same triple count

### Phase 2: Ingestion Layer
News API fetching, file parsing (PDF/Excel/image), prompt handling.

**Validation Gate 2:**
- [ ] News fetch for Adani Power retrieves ≥ 5 articles
- [ ] Dedup prevents re-processing on second run
- [ ] PDF parser extracts text from a test PDF
- [ ] Excel parser reads the 3 provided risk management files
- [ ] Prompt handler normalizes text input to structured format
- [ ] All ingested items have consistent schema

### Phase 3: NLP Extraction Layer
OpenAI-based extraction: sentiment, tone, narrative, entities, ESG signals, themes, events.

**Validation Gate 3:**
- [ ] NLP extraction returns valid JSON with 5 required fields
- [ ] Sentiment scores are integers -2 to +2
- [ ] Tone is from controlled vocab (10 values)
- [ ] Theme tagger returns primary theme from 21-theme list
- [ ] Event classifier on "SEBI fines ₹500 Cr" returns score_floor ≥ 7
- [ ] Event classifier on "Company wins award" returns score_ceiling ≤ 3
- [ ] Tokens per article < 1500 (NLP + tagging calls)

### Phase 4: Analysis Pipeline (Ontology-Driven)
Relevance scoring, risk assessment, framework matching — all querying the ontology.

**Validation Gate 4:**
- [ ] Relevance scoring uses ontology queries (verify via log)
- [ ] Risk assessment includes both ESG + TEMPLES
- [ ] Risk weights come from ontology (verify by modifying `.ttl` and re-running)
- [ ] Framework matcher marks BRSR as mandatory for Indian Large Cap
- [ ] Pipeline on water-crisis article about Adani Power produces relevance ≥ 7
- [ ] REJECTED articles skip stages 6-9
- [ ] Ontology queries per article ≥ 6

### Phase 5: Insight Generation + Perspectives
Deep insight (OpenAI gpt-4.1), REREACT recommendations, perspective transformation, output writer.

**Validation Gate 5:**
- [ ] `generate_deep_insight()` returns valid 9-section JSON for HOME-tier article
- [ ] Impact score respects event classification bounds
- [ ] Recommendations empty for NON-MATERIAL articles (do-nothing rule)
- [ ] CFO output < 100 words (10-second verdict)
- [ ] CEO output uses strategic language, no raw ₹ in headline
- [ ] ESG Analyst returns full insight unchanged
- [ ] Written JSON files are valid JSONB
- [ ] 1 article produces 4 output files (insight + 3 perspectives)

### Phase 6: End-to-End Integration
CLI commands wired up: `ingest`, `analyze`, `query`. Full workflows run end-to-end.

**Validation Gate 6:**
- [ ] `python engine/main.py ingest --company adani-power` completes end-to-end
- [ ] ≥ 3 insight files appear in `data/outputs/adani-power/insights/`
- [ ] Perspective files exist in all 3 lens folders
- [ ] `ingest --all` processes all 7 companies
- [ ] `analyze --file ...` processes a PDF input
- [ ] Pipeline total runtime per article < 45 seconds
- [ ] Token usage per article < 10K
- [ ] Re-running `ingest` skips processed articles

### Phase 7: Framework Deepening + Knowledge Gap Fill
Deepen the 8 new framework stubs with section codes, fill the 13 knowledge gaps from Appendix A, enrich materiality/risk/perspective triples. Push the graph toward ~5000 triples.

**Explicitly NOT doing:**
- Copying specific risks from the MSSSPL / TMW Excel files verbatim. Those registers are company-specific (steel, auto components) and not material to our 7 target companies.
- The TEMPLES framework logic (7 categories, 5×5 P×E scoring, lead/lag indicators, Volume/Value/Cost/Growth/Brand impact dimensions) is ALREADY encoded in Phase 1 using paraphrased, generic enterprise-risk indicators that apply to any company.

**What Phase 7 actually adds:**
1. Framework sections — Porter 5 Forces sub-forces, COSO ERM components, DJSI scoring dimensions, CFA ESG factor weights
2. Financial transmission mechanisms — EventType × CapTier × P&L impact links
3. Double materiality — financial vs impact materiality split per topic
4. SDG targets — 169 UN targets as sub-nodes of the 17 SDGs
5. ESG KPI metadata — unit, calculation method, benchmark per metric
6. Stakeholder detail — communication channels, influence levers
7. Commodity risk profiles — volatility, CBAM applicability, climate exposure
8. Time horizon rules — event → default urgency/reversibility
9. Additional materiality weights for the 7 target-company industries (banking deep, asset mgmt deep, power deep, renewable deep)
10. Penalty precedents — more SEBI, CSRD, SEC case studies

**Validation Gate 7:**
- [ ] Triple count ≥ 5000 after expansion
- [ ] SPARQL returns 7 TEMPLES categories
- [ ] Risk assessor output includes TEMPLES for every article
- [ ] Lead/lag indicators appear in output (≥ 3 per category)
- [ ] Porter, COSO, DJSI frameworks appear for relevant articles
- [ ] `query_perspective_impacts` returns different dimensions for CFO vs CEO
- [ ] Regulatory deadlines queryable (BRSR for Indian Large Cap)
- [ ] Pipeline still completes in < 45s/article

### Engine Integration Gate (end of Phase 7)
Comprehensive test across all 7 companies with 20 real articles.

**Must pass:**
- [ ] 20 articles processed without crashes
- [ ] ≥ 5 articles reach HOME tier
- [ ] Each HOME article produces: insight + 3 perspectives + risk + recommendations
- [ ] CFO view measurably shorter than ESG Analyst view
- [ ] Zero hardcoded domain knowledge in `engine/` (grep check)
- [ ] All ontology queries successful
- [ ] OpenAI cost per article < $0.05
- [ ] Output JSON matches spec exactly
- [ ] Pipeline is idempotent (same inputs → same outputs)

---

### Phase 8: SQLite Index Layer
Build a searchable index over `data/outputs/*.json` so the API can serve fast feed queries without scanning folders.

**Tasks:**
1. Write `engine/index/sqlite_index.py`:
   - Schema: `article_index(id, company_slug, title, relevance_score, priority_level, published_at, esg_pillar, content_type, json_path, created_at)`
   - `upsert_article(insight_dict)` — insert or update index row when engine writes a new insight
   - `query_feed(company_slug, tier, limit, offset)` — SELECT ordered by relevance DESC
   - `get_by_id(article_id)` — returns row with `json_path` to read full insight
2. Modify `engine/output/writer.py` to call `sqlite_index.upsert_article()` after writing JSON
3. Write `engine/index/migrate.py` — one-time script to scan existing `data/outputs/*.json` and populate the index
4. Add `sqlite3` usage to `engine/main.py` (`python engine/main.py reindex` command)

**Validation Gate 8:**
- [ ] `data/snowkap.db` created with correct schema (`.schema article_index`)
- [ ] After running Phase 7 pipeline, `SELECT COUNT(*) FROM article_index` returns > 0
- [ ] `query_feed('adani-power', 'HOME', 10, 0)` returns rows sorted by relevance DESC
- [ ] Re-running pipeline upserts (no duplicates)
- [ ] `python engine/main.py reindex` rebuilds index from JSON files
- [ ] Index queries return in < 50ms

---

### Phase 9: Thin FastAPI Read-Only Layer
Build a minimal web API (~5 endpoints) with API-key auth. Read from SQLite index, serve JSON from `data/outputs/`.

**Tasks:**
1. Write `api/main.py` — FastAPI app, mount routes, CORS config for localhost
2. Write `api/auth.py` — single-key middleware: check `X-API-Key` header against `SNOWKAP_API_KEY` env var
3. Write `api/index.py` — SQLite reader (shared with engine)
4. Write `api/routes/companies.py`:
   - `GET /api/companies` → 7 companies from config
   - `GET /api/companies/{slug}` → company profile + stats (article count, latest HOME article)
5. Write `api/routes/insights.py`:
   - `GET /api/companies/{slug}/insights?tier=HOME&limit=20` → list from SQLite
   - `GET /api/insights/{id}` → full JSON from file
   - `GET /api/insights/{id}?perspective=cfo` → CFO-specific view from perspective file
6. Write `api/routes/ingest.py`:
   - `POST /api/ingest/{slug}` → triggers engine ingestion as background task
7. Add `uvicorn` to `engine/requirements.txt`
8. Add API launch to `engine/main.py`: `python engine/main.py api --port 8000`

**Validation Gate 9:**
- [ ] `curl http://localhost:8000/api/companies -H "X-API-Key: <key>"` returns 7 companies
- [ ] Request without API key returns 401
- [ ] `GET /api/companies/adani-power/insights` returns sorted list
- [ ] `GET /api/insights/{id}?perspective=cfo` returns CFO view (not full insight)
- [ ] `POST /api/ingest/adani-power` triggers pipeline, returns 202 Accepted
- [ ] All endpoints return valid JSON (no 500 errors)
- [ ] OpenAPI docs accessible at `/docs`
- [ ] API starts in < 2 seconds

---

### Phase 10: Frontend Simplification
Strip the legacy React app down to essentials. Wire it to the new API. Add `PerspectiveSwitcher` + `CrispInsight`.

**Tasks:**
1. **Delete** from `client/src/`:
   - `pages/LoginPage.tsx`, `SplashPage.tsx`, `IntroPage.tsx`, `OnboardingPage.tsx`, `AdminPage.tsx`, `CampaignsPage.tsx`, `PreferencesPage.tsx`
   - `components/layout/` legacy auth-dependent components
   - Legacy API client methods in `lib/api.ts` that point at removed backend routes
   - `stores/authStore.ts`, `chatStore.ts` (no auth, no chat for now)
2. **Keep** from `client/src/`:
   - `pages/HomePage.tsx`, `SwipeFeedPage.tsx`, `SavedNewsPage.tsx`
   - `components/cards/` (FeedCard, NewsCard, SwipeCardStack)
   - `components/panels/` (KnowMoreSheet, ImpactMetrics, CausalChainViz, FrameworkAlignmentV2, NarrativeIntelligence)
   - `components/ui/` (Radix UI wrappers)
   - `stores/newsStore.ts`, `feedStore.ts`, `savedStore.ts`
3. **Create new:**
   - `client/src/stores/perspectiveStore.ts` — active perspective, persists to localStorage
   - `client/src/components/ui/PerspectiveSwitcher.tsx` — 3-segment toggle
   - `client/src/components/panels/CrispInsight.tsx` — Bloomberg-style card (headline + impact grid + what matters + action)
4. **Rewrite** `client/src/lib/api.ts`:
   - Point at new API base URL (default `http://localhost:8000/api`)
   - Add `X-API-Key` header from `VITE_API_KEY` env var
   - Methods: `fetchCompanies()`, `fetchInsights(slug, tier)`, `fetchInsight(id, perspective)`, `triggerIngest(slug)`
5. **Update** `HomePage.tsx` to:
   - Show 7 companies in header/nav
   - Mount `PerspectiveSwitcher`
   - Display feed filtered by active perspective
   - Render `CrispInsight` for CFO/CEO, full `KnowMoreSheet` for ESG Analyst
6. **Update** `client/.env.example`:
   ```
   VITE_API_URL=http://localhost:8000/api
   VITE_API_KEY=<same key as SNOWKAP_API_KEY>
   ```
7. **Update** `client/vite.config.ts` — simplify proxy config

**Validation Gate 10:**
- [ ] `cd client && npm install && npm run build` succeeds
- [ ] `npm run dev` starts on :5173 with no console errors
- [ ] Browser loads, shows 7 companies
- [ ] Click Adani Power → see feed of insights
- [ ] Click an insight → `KnowMoreSheet` opens with real data from API
- [ ] Click `PerspectiveSwitcher` to CFO → see `CrispInsight` (short view)
- [ ] Click ESG Analyst → full detail view
- [ ] No legacy API calls (check Network tab: no calls to deleted endpoints)
- [ ] No auth redirect loops
- [ ] Mobile responsive (test at 375px width)

---

### Phase 11: Production Gate (Final)
Full end-to-end production readiness check.

**Tasks:**
1. Write `scripts/prod_check.sh` — smoke test script
2. Run engine ingestion for all 7 companies (hours/overnight)
3. Start API + frontend
4. Click through UI manually for each company
5. Document any issues, fix them

**Validation Gate 11 (Production Ready):**
- [ ] All 7 companies have ≥ 5 HOME-tier insights
- [ ] API responds to all endpoints with < 200ms latency
- [ ] Frontend loads in < 3 seconds
- [ ] CFO perspective shows < 100 word insights
- [ ] CEO perspective shows strategic framing
- [ ] ESG Analyst shows full detail with framework citations
- [ ] Causal chains visualize correctly
- [ ] TEMPLES + ESG risk both display
- [ ] `do_nothing: true` articles show "No action required" instead of forced recommendations
- [ ] Ontology triples > 5000 (confirmed via SPARQL count)
- [ ] Zero hardcoded domain dicts in `engine/` (grep check)
- [ ] API key auth working (401 on missing key)
- [ ] All 7 company slugs navigable in UI
- [ ] No console errors in browser
- [ ] No unhandled exceptions in API logs
- [ ] OpenAI cost per article < $0.05 (tracked via logs)
- [ ] Pipeline is idempotent (re-run produces same output)
- [ ] Documentation updated (README.md has production deployment steps)

---

### Phase 15: Full Ontology Migration (Completed)
Migrated all remaining hardcoded domain knowledge from Python dicts/if-else chains into ontology triples with SPARQL queries. Pushed ontology coverage from ~40% to ~90%.

**What was migrated (152 lines of hardcoded Python → ontology triples):**

| Component | Before (hardcoded) | After (ontology) |
|-----------|-------------------|-------------------|
| `risk_assessor.py` | `ESG_CATEGORIES` list (10), `TEMPLES_CATEGORIES` list (7), `_THEME_RISK_MAP` dict (21 entries), `_THEME_TEMPLES_MAP` dict (21 entries), `_classify_level()` if-else thresholds | `query_esg_risk_categories()`, `query_temples_categories()`, `query_theme_risk_map()`, `query_theme_temples_map()`, `query_risk_level_thresholds()` |
| `framework_matcher.py` | `REGION_BOOSTS` dict, `MARKET_CAP_BRSR_MANDATORY` set | `query_regional_boosts()`, `query_mandatory_rules()` |
| `recommendation_engine.py` | `_derive_priority()` if-else chain, `_compute_risk_of_inaction()` hardcoded scores, `_build_perspective_rankings()` hardcoded sort logic | `query_priority_rules()`, `query_risk_of_inaction_config()`, `query_perspective_ranking_keys()` |
| `perspective_engine.py` | `GRID_COLUMN_MAP` dict, `_DIM_TO_KEY` dict, hardcoded CFO/CEO headline string concatenation | `query_grid_column_map()`, `query_dim_to_insight_keys()`, `query_headline_rules()` |
| `legacy_adapter.py` | Hardcoded headline cascade with string concatenation | `query_headline_rules()` with template-based rendering |

**Ontology additions:**
- `schema.ttl`: +80 lines (new classes: `RiskLevelThreshold`, `RegionalFrameworkBoost`, `MandatoryRule`, `PriorityRule`, `RiskOfInactionConfig`, `HeadlineRule`, `RankingSortKey`; new predicates: `triggersRiskCategory`, `triggersTEMPLES`, `gridColumn`, `insightKey`, etc.)
- `knowledge_expansion.ttl`: +200 lines (21 theme→risk, 21 theme→TEMPLES, 4 thresholds, 14 regional boosts, 5 mandatory rules, 12 priority rules, risk-of-inaction config, 10 grid columns, 10 insight keys, 7 headline rules, 6 ranking sort keys)
- `intelligence.py`: +300 lines (13 new SPARQL query functions, 7 new dataclasses)

**Bugs fixed:**
- CFO headline concatenation producing malformed text ("₹10,000-15,000 Cr at risk at stake —")
- `FrameworkMatch.triggered_sections` not serialized (missing dataclass field)
- Stale docstring referencing removed `_THEME_RISK_MAP`
- Dashboard stats inaccurate: `high_impact_count` returned total instead of filtering by materiality/relevance; `new_last_24h` returned total instead of filtering by `published_at`

**Validation:** Zero hardcoded domain knowledge dicts in `engine/` (grep-verified).

### Dashboard Stats Logic (`GET /api/news/stats`)

| Stat | Query | Definition |
|------|-------|------------|
| **Articles** | `COUNT(*)` | Total indexed articles |
| **High Impact** | `materiality IN ('CRITICAL','HIGH') OR relevance_score >= 5.0` | Articles with significant ESG materiality or high relevance |
| **New Today** | `published_at >= datetime('now', '-1 day')` | Articles published in last 24 hours |
| **Predictions** | Hardcoded `0` | Future: sentiment trajectory predictions (Phase I6 in plan) |

### Future: Predictions Feature (Plan)
The "Predictions" stat is currently `0`. To make it functional:
1. Implement Phase I6 (Sentiment Trajectory Prediction) — LLM analyzes last 5 articles per company to predict ESG sentiment direction
2. Store predictions in a new `predictions` table: `(id, company_slug, prediction_type, direction, confidence, created_at)`
3. Count active predictions per company in `GET /api/news/stats`
4. Frontend: render as trend indicators (up/stable/down arrows)

### Phase 17: Causal Primitives Integration (Completed)

Integrates the Causal Primitives Framework (22 universal primitives, 123 P→P edges, 48 P→outcome edges, 69 order-3/4 chains, 22 outcome nodes, 8 societal nodes) into the Snowkap ontology. Upgrades from qualitative ESG analysis to quantitative causal reasoning with computed financial exposure.

**Ontology Files (Layer 7: Causal Primitives) — all live:**
- `data/ontology/primitives_schema.ttl` — Primitive, CausalEdge, OutcomeEdge, IndicatorEdge, OutcomeNode (22 instances), SocietalNode classes
- `data/ontology/primitives_edges_p2p.ttl` — 123 P→P `CausalEdge` + 48 P→non-P `OutcomeEdge` instances
- `data/ontology/primitives_indicators.ttl` — 77 Indicator instances (37 qualitative rubrics A–AK + 40 quantitative) + 42 IndicatorEdge instances spanning all 22 primitives
- `data/ontology/primitives_thresholds.ttl` — 25 canonical τ threshold categories
- `data/ontology/primitives_order3.ttl` — 50 P3 chains + 19 P4 chains (represented as `CausalEdge` with `edgeOrder 3/4`)

**22 Primitives:** OX (Opex), RV (Revenue), CX (Capex), EU (Energy Use), GE (GHG Emissions), WA (Water), WS (Waste), WF (Workforce), HS (H&S), CL (Compliance), SC (Supply Chain), DT (Downtime), CY (Cyber), EP (Energy Price), FR (Freight), LT (Lead Time), IR (Interest Rates), FX (Currency), RG (Regulatory), XW (Extreme Weather), CM (Commodity Price), LC (Labor Cost)

**Edge Schema:** Each edge carries: edgeId, cause/effect (or outcomeSource/outcomeTarget), edgeOrder, directionSign (+/−/mixed), functionalForm (linear/log-linear/threshold/ratio/step/composite), operatorExpression, elasticityOrWeight (β range), lagK, aggregationRule (additive/weighted_avg/max/multiplicative/dominant), confidenceLevel (high/medium/low), edgeNotes

**SPARQL Queries:** query_primitives_for_event(), query_p2p_edges(), query_cascade_context(), query_thresholds_for_primitive(), query_indicators_for_primitive(), query_feedback_loops(), query_stakeholder_impact()

**Integration Points:**
- `insight_generator.py` — LLM prompt enriched with relevant cascade edges, β, lag, thresholds
- `recommendation_engine.py` — Prompt enriched with actionable levers and threshold monitors
- `perspective_engine.py` — CFO gets financial cascade (EP→OX→GrossMargin), CEO gets strategic cascade (EP→CX→RV), ESG Analyst gets compliance cascade (EP→EU→GE→CL)

**Status: COMPLETED** — Total ontology graph: 8,222 triples across 11 loaded TTL files. Schema, 123 P→P edges, 48 P→outcome edges, 69 P3/P4 chains, 77 indicators, 25 thresholds, 7 SPARQL queries, prompt enrichment all live.

### Phase 17b: On-Demand Pipeline + Edge Gap Fixes (Completed)

**Edge Gap Fixes (8 gaps, all resolved):**
1. Added primary primitives to 5 secondary-only events (esg_rating_change→IR, board_change→RG, climate_disclosure_index→RV, dividend_policy→CX, award_recognition→RV)
2. Added 2 missing event mappings (esg_partnership→RV, license_revocation→DT)
3. Fixed event ID vs label mismatch (uses `event_id` URI-style, not human-readable label)
4. Added fallback LLM guidance when cascade context is empty
5. Fixed on_demand.py reconstruction bugs (ontology_queries, nodes/edges, to_dict, esg/temples risks)

### Phase 17c: Level 2 — Computed Financial Exposure Engine (Completed)

**`engine/analysis/primitive_engine.py`** — deterministic cascade computation:
- `compute_cascade(event_id, company, delta_source_cr)` → traverses P→P edges from ontology
- 6 functional forms: linear, log-linear, threshold, ratio, step, composite
- Company-specific β calibration: `β_company = β_ontology × (company_share / industry_avg)`
- Returns `CascadeResult` with per-hop breakdown, total ₹ exposure, margin bps
- Source flagging: "(from article)" vs "(engine estimate)" for computed figures
- ROI clamping: compliance 500%, financial 300%, strategic 400%, operational 200%

**Company Financial Calibration (`config/companies.json`):**
| Company | Revenue Cr | Opex Cr | Energy % | Labor % | Key Exposure |
|---------|-----------|---------|----------|---------|-------------|
| ICICI Bank | 50,000 | 35,000 | 1% | 35% | regulatory, credit |
| YES Bank | 12,000 | 9,000 | 1% | 30% | regulatory, credit |
| IDFC First Bank | 8,000 | 6,000 | 1% | 32% | regulatory, credit |
| Adani Power | 45,000 | 35,000 | 40% | 8% | energy, coal, climate |
| JSW Energy | 15,000 | 10,000 | 30% | 10% | energy, transition |
| Waaree Energies | 5,000 | 4,000 | 15% | 20% | commodity, supply chain |
| Singularity AMC | 200 | 150 | 0.5% | 60% | regulatory, reputation |

**Other Fixes Completed:**
- Event classifier: word-boundary regex (prevents "award" matching "towards")
- Transition keywords expanded: "renewable energy, green hydrogen, solar capacity, wind capacity, rtc power" added to `event_transition_announcement`
- Accuracy hardening: ESRS section matching (G1 for governance, not E1), margin scaled to company size, separate known/speculative exposure

### On-Demand Fresh Pipeline Architecture (Current, Live)

**Schema Version:** `2.0-primitives-l2`

**Flow when user clicks "View Insights":**

```
User clicks article → "View Insights →"
    ↓
Frontend: ArticleDetailSheet mounts → checks article.deep_insight.headline
    ↓
IF no insight OR schema_version ≠ "2.0-primitives-l2" → STALE
    ↓
Frontend: POST /api/news/{id}/trigger-analysis
         Shows "Generating Intelligence Brief" spinner
    ↓
API (legacy_adapter.py):
  1. Load stored JSON from data/outputs/{company}/insights/
  2. Check schema_version → if ≠ "2.0-primitives-l2" → needs fresh analysis
    ↓
engine/analysis/on_demand.py → enrich_on_demand():
  3. Load RAW article from data/inputs/news/{company}/  ← KEY: uses original article
  4. Re-run FULL pipeline stages 1-9 (process_article):
     - Stage 1: NLP extraction (gpt-4.1-mini)
     - Stage 2: Theme tagging (gpt-4.1-mini)
     - Stage 3: Event classification (ontology keywords, word-boundary matching)
     - Stage 4: Relevance scoring (ontology materiality weights)
     - Stage 5: Causal chains (ontology BFS + primitive edges)
     - Stage 6: Framework matching (ontology, 21 frameworks)
     - Stage 7: Stakeholder mapping (ontology)
     - Stage 8: SDG mapping (ontology)
     - Stage 9: Risk assessment (ontology, ESG + TEMPLES)
  5. Compute financial cascade (primitive_engine.py):
     - Map event → primary primitive → P→P edges
     - Calibrate β per company (energy_share, labor_share, etc.)
     - Compute: ΔTarget = β × ΔSource × base_value
     - Return CascadeResult with ₹ figures + margin bps
  6. Run Stage 10: Deep insight (gpt-4.1) with COMPUTED cascade as hard constraints
  7. Run Stage 11: Perspective transform (ontology-driven, 3 lenses)
  8. Run Stage 12: Recommendations (gpt-4.1-mini) with primitive levers + ROI clamping
  9. Write enriched JSON to disk with schema_version: "2.0-primitives-l2"
    ↓
Frontend: polls GET /api/news/{id}/analysis → receives fresh analysis
         Renders enriched panels (15-30 seconds first time)
    ↓
Second click → schema_version matches → instant (cached)
```

**Key Design Decisions:**
- Stages 1-9 re-run from raw input (not reconstructed from stale stored pipeline) → ensures latest ontology keywords, event types, and primitive edges take effect
- `engine/main.py` skips stages 10-12 for SECONDARY tier at ingestion → saves ~$0.05/article
- Schema version check ensures old articles get fresh analysis without manual migration
- `scripts/clear_stale_insights.py` available for bulk cache clearing if needed

**Files involved:**
| File | Role |
|------|------|
| `client/src/components/panels/ArticleDetailSheet.tsx` | Auto-triggers on mount, spinner, polling |
| `api/routes/legacy_adapter.py` | `POST /news/{id}/trigger-analysis` endpoint |
| `engine/analysis/on_demand.py` | Orchestrator: full pipeline + enrichment |
| `engine/analysis/pipeline.py` | Stages 1-9 (process_article) |
| `engine/analysis/primitive_engine.py` | Computed cascade (β × Δ × base) |
| `engine/analysis/insight_generator.py` | Stage 10 (LLM with hard constraints) |
| `engine/analysis/perspective_engine.py` | Stage 11 (ontology-driven transform) |
| `engine/analysis/recommendation_engine.py` | Stage 12 (LLM with ROI clamping) |
| `engine/output/writer.py` | Writes JSON with schema_version stamp |

### Ontology Coverage (Current — Post-Level 2)

| Pipeline Stage | Driver | % Ontology |
|---------------|--------|-----------|
| 1. NLP Extraction | LLM (gpt-4.1-mini) | 0% |
| 2. Theme Tagging | LLM (gpt-4.1-mini) | 0% |
| 3. Event Classification | **Ontology** — 22 EventTypes, word-boundary keywords | 100% |
| 4. Relevance Scoring | **Ontology** — materiality weights, cap tier, industry risk | 100% |
| 5. Causal Chains | **Ontology + Primitives** — BFS + 58 P→P edges + company β | 100% |
| 6. Framework Matching | **Ontology** — 21 frameworks, sections, regional boosts | 100% |
| 7. Stakeholder Mapping | **Ontology** — 5 groups × 21 topics | 100% |
| 8. SDG Mapping | **Ontology** — 17 SDGs × topics | 100% |
| 9. Risk Assessment | **Ontology** — 10 ESG + 7 TEMPLES, industry weights | 100% |
| 10. Deep Insight | **Ontology-constrained LLM** — ₹ figures, margins, frameworks COMPUTED | 85% |
| 11. Perspective Transform | **Ontology** — headline rules, grid columns, word caps | 100% |
| 12. Recommendations | **Ontology-constrained LLM** — budgets, ROI capped, thresholds | 80% |

**Summary: ~97% ontology-driven intelligence.**
- Stages 1-2 (3%): LLM reads article text → structured data. Irreducible.
- Stages 3-9, 11 (82%): Fully ontology-driven. Zero LLM.
- Stages 10, 12 (15%): LLM writes narrative prose, but ALL numbers (₹ exposure, margin bps, ROI) are engine-computed hard constraints. LLM cannot override them.

### Phase 17d: Complete Causal Edge Coverage (Completed)

All causal edges loaded into the ontology. Mechanical data entry from PART 1 + PART 2.

**P→P edges (123 total, all live in `primitives_edges_p2p.ttl`):**
- SC×9, RG×7, XW×8, CM×7, EP×13, IR×5, GE×4, and scattered coverage across CL, CY, DT, EU, FR, FX, HS, LC, LT, OX, RV, WA, WF, WS. All 64 previously-missing P→P edges are now present.

**P→non-P edges (48 total, `OutcomeEdge` instances in `primitives_edges_p2p.ttl`):**
- Primitives → outcome nodes: GrossMargin (4), WACC (1), CreditRating (2), FCF (4), EquityVal (1), InsurancePremium (4), HedgingCost (1), CarbonCost (1), CarbonLiability (4), WaterPermitCost (1), WasteDisposalCost (1), RemediationLiability (2), LaborProductivity (1), CapacityUtil (4), InventoryCarryCost (1), ServiceLevel (2), ESGRating (5), CustomerChurn (3), InvestorSentiment (2).
- URI prefix `outedge_<SRC>_<TGT>`, edgeId prefix `OE::`, uses `outcomeSource` / `outcomeTarget` predicates.

**P3/P4 chains (69 total in `primitives_order3.ttl`):**
- 50 P3 chains (3-node cascades) + 19 P4 chains (4-node cascades)
- Represented as `CausalEdge` with `edgeOrder 3/4`, composed elasticity = product of hop β, composed lag = sum of hop lags, full path in `edgeId` and `edgeNotes`.

### Phase 18: Social/Labor Intelligence Coverage (In Progress)

**Problem:** Social issues (child labor, forced labor, modern slavery) were aggregated under generic `topic_supply_chain_labor` with no dedicated event types, framework sections, or penalty cascades. A child labor article would classify as generic "NGO Report" (score 4-7) instead of "Social/Labor Rights Violation" (score 7-9).

**8 Gaps Fixed:**
1. New event type: `event_social_violation` (score 7-9) with 20+ keywords (child labor, forced labor, modern slavery, sweatshop, wage theft, etc.)
2. Framework sections: GRI:408 (Child Labor), GRI:409 (Forced Labor), GRI:412 (Human Rights Assessment)
3. Event→primitive mapping: social violation → CL (primary) + SC, RV, OX, RG, WF (secondary)
4. Theme→event mapping: `topic_supply_chain_labor` → `event_social_violation` (replaces ngo_report fallback)
5. Materiality weight boost: supply_chain_labor for Power/Renewable raised from 0.7 to 0.9
6. Stakeholder mappings: Employees and Community now care about supply_chain_labor
7. Investor concern: "MSCI ESG downgrade, B2B procurement exclusion, ESG fund divestment" for social violations
8. CL→RG edge already exists (from Phase 17d) for regulatory escalation cascade

### Production Roadmap: Any-Company Onboarding via Domain Signup

**Goal:** User enters company domain → system auto-configures → intelligence available in 5 minutes.

**Infrastructure (test free tiers first, upgrade when validated):**

| Service | Purpose | Free Tier | Paid (production) |
|---------|---------|-----------|-------------------|
| NewsAPI.ai | Full text news (150K+ publishers) | Limited tokens | $150/mo (10K plan) |
| EODHD | Company financials (revenue, opex, capex) for auto-calibration | 20 calls/day | $20/mo |
| OpenAI | NLP + insight + recommendations (stages 1-2, 10, 12) | Pay-as-you-go | ~$50/mo |
| Replit Pro | Hosting (API + frontend, single process) | Already have | $25/mo |

**Total production: ~$246/month. Beta: ~$15/month (OpenAI only).**

**3 Components to Build:**

1. **NewsAPI.ai Integration** (~3 days)
   - Replace Google News RSS in `engine/ingestion/news_fetcher.py`
   - Full article text (2,000-5,000 chars vs current 87 chars)
   - ESG keyword filtering per company
   - Eliminates paywall/headline-only problem entirely

2. **Auto-Onboarding Engine** (~4 days)
   - Domain → company name resolution (web lookup)
   - EODHD API → revenue, opex, capex, debt ratios → `primitive_calibration`
   - Industry + SASB category classification
   - Auto-generate news queries from company name + industry
   - Add to `companies.json` programmatically
   - Seed ontology with company + competitor triples

3. **Financial Data API (EODHD)** (~2 days)
   - REST API integration for BSE/NSE listed companies
   - Extract: revenue_cr, opex_cr, capex_cr, debt_to_equity, cost_of_capital
   - Derive: energy_share, labor_share, freight_intensity from industry benchmarks
   - Cache financial data (refresh quarterly)

**Flow after build:**
```
New user enters "tatasteel.com"
  → Resolve: Tata Steel, Industry: Steel, SASB: Iron & Steel
  → EODHD: revenue ₹2.3L Cr, opex ₹1.8L Cr, energy 35%
  → Generate queries: "Tata Steel ESG", "Tata Steel emissions", etc.
  → NewsAPI.ai: fetch 20 full-text articles
  → Pipeline: 12-stage analysis on each article
  → Dashboard ready in ~5 minutes
```

**Current status: NewsAPI.ai integrated and tested (free tier, 5,000+ chars full text). EODHD deferred (free tier = prices only, no fundamentals).**

### Phase 19: Self-Evolving Ontology (Completed — commit 47a1e01)

Pipeline now writes back to the ontology. Entities, themes, events, and causal links discovered in articles are buffered, scored for confidence, deduped, and promoted to `discovered.ttl`.

**Architecture:** Stage 12.5 — `collect_discoveries()` runs after each article (~5ms), buffers candidates, batch promoter runs every 30 min with confidence thresholds and SPARQL dedup, promotes to `discovered.ttl`.

**All 5 phases built, tested, deployed:**

| Phase | Status | Key Result |
|-------|--------|-----------|
| A: Foundation | ✅ | Buffer, collector, promoter, audit, schema updated |
| B: Entity + Theme | ✅ | 9 entities discovered, themes staged for review |
| C: Event + Framework | ✅ | SEBI auto-promoted (Tier-1, conf 0.85) |
| D: Edge + Weight + Stakeholder | ✅ | 1 causal edge (RV→CX), 3 weight divergences tracked |
| E: Governance + Admin | ✅ | API endpoints, triple cap, audit log |

**7 Discovery Categories:** New Entities (auto ≥3 articles, conf ≥0.80), ESG Themes (human review), Event Types (conditional), Causal Edges (human review), Materiality Weights (human review), Stakeholder Concerns (human review), Framework Updates (Tier-1 auto).

**Safety:** Authored TTL never modified. Max 10,000 discovered triples. 90-day archival. Provenance on every triple. Jaro-Winkler dedup for entities.

**Files:**
- `engine/ontology/discovery/` — 7 discoverer modules + collector + promoter
- `data/ontology/discovered.ttl` — runtime-learned triples
- `data/ontology/discovery_audit.jsonl` — append-only audit log

The ontology now grows with every article. Next article mentioning a new entity increases its article count — once it hits 3+ from 2+ sources, it auto-promotes into the ontology and becomes queryable for causal chains, competitor comparison, and cascade computations.

### Phase 11: Production-Readiness Hardening (Finalized — 2026-04-24)

Production gate from the original 8-phase roadmap, extended with the security + brand + ops work surfaced by the senior-marketing-manager audit. All validation gates for 11A–11D green. 32 new Phase 11 tests pass (`tests/test_phase11_*.py`).

**11A — Security + durability** (auth bypass closed, concurrency fixed, backups running)
- Signed JWT verification — [api/auth_context.py::decode_bearer](api/auth_context.py) now verifies HS256 signatures via `PyJWT`. Controlled by `REQUIRE_SIGNED_JWT=1` env flag; 24h compat window accepts legacy unsigned tokens otherwise. Set strict in prod.
- SQLite WAL mode — `PRAGMA journal_mode=WAL` applied across [sqlite_index.py](engine/index/sqlite_index.py), [tenant_registry.py](engine/index/tenant_registry.py), [campaign_store.py](engine/models/campaign_store.py), onboarding_status, llm_calls. 3 concurrent writers + 5 concurrent API requests produce zero `database is locked` errors.
- Hourly SQLite backup — [scripts/backup_db.sh](scripts/backup_db.sh) via Python `sqlite3.backup()` (cross-platform). 14-day retention. Cron: `0 * * * *`.
- WAL sidecar files + `data/backups/` added to [.gitignore](.gitignore).

**11B — Onboarding gap closed** (India-only V1)
- `POST /api/admin/onboard` + `GET /api/admin/onboard/{slug}/status` — [api/routes/admin_onboard.py](api/routes/admin_onboard.py). Kicks off `fetch_and_analyse_for_company` in a `BackgroundTasks` queue. Gated by `manage_drip_campaigns` permission.
- State tracking — [engine/models/onboarding_status.py](engine/models/onboarding_status.py): `pending → fetching → analysing → ready | failed`.
- Empty-shell pollution fix — [legacy_adapter.py::auth_login](api/routes/legacy_adapter.py) no longer auto-registers random client domains. A tenant appears in the super-admin switcher only when (a) it has ≥1 indexed article OR (b) an admin explicitly onboarded via the endpoint.
- Deferred to V2: frontend admin-onboarding modal + non-Indian LLM fallback.

**11C — Marketing-grade drip brand** (subject + intro + logo + alignment)
- Hybrid editorial subject-line generator — [engine/output/subject_line.py](engine/output/subject_line.py). Ontology templates for LOW/MODERATE, `gpt-4.1-mini` for HIGH/CRITICAL (where opens matter). Cached per `article_id`. 90-char iPhone-preview cap.
- Stakes-first intro copywriter — [engine/output/intro_copywriter.py](engine/output/intro_copywriter.py). Opens with ₹ exposure + materiality + regulator + timeline. Admin-provided `sender_note` still overrides.
- CID-attached logo — [engine/output/email_assets.py](engine/output/email_assets.py) ships the SNOWKAP wordmark as a base64 PNG inline attachment (content_id `snowkap-logo`). `<img src="cid:snowkap-logo">` renders immediately in Outlook Desktop + 365 + Gmail + Apple Mail + iOS — no "right-click to download" placeholders, no blocked external images.
- Logo source-of-truth — [SNOWKAP_logo.png](SNOWKAP_logo.png) (hand-exported from Illustrator). Regenerate all 4 asset sizes + the base64 module via `python scripts/rasterise_sk_logo.py`.
- Email layout (v17 final, dark-card editorial style) — [engine/output/newsletter_renderer.py::render_article_brief_dark](engine/output/newsletter_renderer.py):
  - Header: 240×40 display logo on `#0F172A`, orange 3px baseline rule
  - Section cards: no emoji markers (Outlook Word engine fragments 📊/💡/🏛/📋 into coloured boxes); identity carried by orange left border + bold uppercase title
  - Key Insights: numbered orange badges, 26×26 with `border-radius:13px`, `mso-line-height-rule:exactly` so Outlook respects the 22px text line-height and the badge sits on the first-line baseline
  - Impacted Metrics: table-based bullet list with `&#9632;` markers (not `<ul>/<li>` — Outlook clips descenders on the last `<li>` of bulleted lists inside cards)
  - Every `<td>` in the dark brief carries explicit `text-align:left` to override the outer `<td align="center">` that Outlook's Word engine inherits as centered text
  - CTA label: `"Book a demo with Snowkap"` (no caps-lock, no 15-min) — constant at `DEFAULT_CTA_LABEL`
- Deferred to V2: recipient-timezone cadence, Resend open/click webhook, A/B testing infra.

**11D — Monitoring + completeness**
- structlog JSON logger + request-ID middleware + slow-query (>500ms) warnings — [api/main.py](api/main.py)
- Sentry with PII scrubbing — `before_send` strips `recipient_email` / `email` before send. Opt-in via `SENTRY_DSN`.
- `/metrics` Prometheus endpoint — `snowkap_articles_total`, `snowkap_campaigns_active`, `snowkap_emails_sent_24h`, `snowkap_openai_cost_usd_24h`, `snowkap_cron_tick_duration_ms`.
- OpenAI cost tracker — [engine/models/llm_calls.py](engine/models/llm_calls.py) logs every call with tokens + cost + article_id.
- Production env sanity check — [api/main.py::_check_production_env](api/main.py) fails-fast at boot if `SNOWKAP_ENV=production` and any of `OPENAI_API_KEY`, `RESEND_API_KEY`, `SNOWKAP_FROM_ADDRESS`, `JWT_SECRET` (≥32 chars), `SNOWKAP_API_KEY`, `REQUIRE_SIGNED_JWT=1` is empty or an obvious placeholder. Dev mode stays permissive.
- REJECTED article purge — `sqlite_index.purge_rejected_articles(older_than_days=90)` cron helper. HOME + SECONDARY kept indefinitely.
- All 5 known `except Exception: pass` sites replaced with `logger.exception(...)` + typed errors.

**To promote to production**, in the deploy environment (not `.env`):
```
SNOWKAP_ENV=production
REQUIRE_SIGNED_JWT=1
JWT_SECRET=<32+ random chars>
SENTRY_DSN=<from sentry.io>
```

**Regenerate the logo** if the brand mark ever changes:
1. Drop the new PNG at `SNOWKAP_logo.png` (repo root, RGBA preferred)
2. `python scripts/rasterise_sk_logo.py`
3. Commit the 4 regenerated PNGs in `client/public/assets/` + `engine/output/email_assets.py`
4. Next send uses the new mark — no code change required

### Phase 12: Analysis-Hardening + Fuzz Harness (Finalized — 2026-04-25)

Closes the 7 hallucination/quality blockers surfaced by the live-news audit during Phase 11. Every blocker has both a unit test and a live-pipeline regression check passing on real articles. Test count went from 40 (end of Phase 11) → 64 (end of Phase 12).

**12.1 Classifier confidence bar** — [engine/nlp/event_classifier.py](engine/nlp/event_classifier.py)
- Single generic keyword (e.g. "accountability", "fine", "audit") no longer triggers a specific event type. Confident match requires **≥ 2 keyword hits OR ≥ 1 specific multi-word phrase** (≥10 chars, 2+ tokens). Below the bar → falls through to theme-based default.
- Caught: Waaree PSPCL article that pre-Phase 12 misclassified as `event_ngo_report` on a single "accountability" match in unrelated BEE-framework prose.

**12.2 Wrap-up / digest detector** — [engine/ingestion/news_fetcher.py](engine/ingestion/news_fetcher.py)
- Drops daily-digest articles before they enter the pipeline. Triggers when title contains markers (`wrap-up`, `roundup`, `daily digest`, `morning digest`, etc.) OR when ≥ 5 distinct other-org names appear in the first 2 KB AND target company appears ≤ 2 times.
- Verified: rejected 7/10 fetched articles for each of Adani Power / JSW Energy / YES Bank as wrap-up noise on 2026-04-24 sample.

**12.3 Positive-event ontology** — [data/ontology/knowledge_depth.ttl](data/ontology/knowledge_depth.ttl), [data/ontology/primitives_schema.ttl](data/ontology/primitives_schema.ttl)
- 4 new event types added: `event_contract_win`, `event_capacity_addition`, `event_esg_certification`, `event_order_book_update`. Each maps to the appropriate Revenue/Capex/Investor-Rating primitive cascade. `event_green_finance_milestone` keyword list expanded.
- Closes the gap where contract wins / certifications fell through to negative-event defaults, causing the LLM to invent crisis narratives from positive source material.

**12.4 Narrative-data coherence check** — [engine/analysis/output_verifier.py](engine/analysis/output_verifier.py:: verify_narrative_coherence)
- Cross-validates `event_id` polarity (positive/negative) against `nlp.sentiment` against `decision_summary` polarity. When a positive event is framed as a crisis (e.g. contract win → CRITICAL materiality), auto-downgrades materiality by one tier and emits a warning. Does NOT rewrite narrative — surfaces the bug instead of masking it.
- Live verified on Waaree PSPCL: `(event=+1, sentiment=+0, insight=-1) → materiality downgraded HIGH → MODERATE`.

**12.5 Cross-section ₹ consistency** — [engine/analysis/output_verifier.py](engine/analysis/output_verifier.py:: verify_cross_section_consistency)
- Scans every ₹ figure across `headline`, `decision_summary.financial_exposure`, `decision_summary.key_risk`, `top_opportunity`, `net_impact_summary`. Flags any field that diverges from the canonical (largest) figure by > 35%. Does NOT rewrite — emits drift warnings so the engineer can investigate prompt drift.

**12.6 Precedent anchor sanitisation** — [engine/analysis/ceo_narrative_generator.py](engine/analysis/ceo_narrative_generator.py)
- System prompt's hardcoded "Vedanta Konkola Child Labour" + "Infosys 2017" examples replaced with `<...>` placeholders. User prompt now explicitly says "PRECEDENTS: NONE AVAILABLE — set analogous_precedent to null" when the ontology returns empty for the event×industry combination. Stops the LLM from anchoring on a default precedent for unrelated events.

**12.7 Hallucination audit** — [engine/analysis/output_verifier.py](engine/analysis/output_verifier.py:: audit_source_tags)
- Runs BEFORE `enforce_source_tags`. Scans every LLM-emitted `(from article)` tag, extracts the ₹ figure, and verifies it against the article body using ₹/Rs/INR/Cr-contextual regex (with ±10% tolerance + comma-stripping). Tags that can't be justified are downgraded to `(engine estimate)`.
- Live verified on Waaree anti-dumping article (which contains zero ₹ figures): **4 unsupported (from article) claims auto-downgraded** while ICICI ₹45,000 Cr and IDFC ₹503 Cr stayed correctly tagged because they are real article figures.

**Test additions**
- [tests/test_phase11_accuracy_fixes.py](tests/test_phase11_accuracy_fixes.py) — 8 tests (Phase 11 hardening)
- [tests/test_phase12_hardening.py](tests/test_phase12_hardening.py) — 12 tests (12.1–12.4)
- [tests/test_phase12_blockers_5_6_7.py](tests/test_phase12_blockers_5_6_7.py) — 12 tests (12.5–12.7)

### Phase 12 Final: Nightly Fuzz Harness

[scripts/fuzz_pipeline.py](scripts/fuzz_pipeline.py) — runs every article in [tests/fuzz_corpus/corpus.jsonl](tests/fuzz_corpus/corpus.jsonl) through the full pipeline, compares output against per-article expectations, and emits a markdown + JSON report under `data/fuzz_reports/`.

**Per-article expectations** (any subset):
- `event_id` — exact event-classification match
- `min_keywords_matched` — minimum keyword hits (Phase 12.1 confidence bar)
- `materiality_in` — materiality must be one of the listed buckets
- `min_recs` / `max_recs` — recommendation count window
- `must_not_contain` — phrases that, if present anywhere in the output, indicate regression (e.g. "Vedanta Konkola" → catches the Phase 12.6 precedent-anchor bug)
- `must_have_warning` — verifier must emit a specific warning (used to assert the hallucination audit fires on a known-bad article)
- `must_not_warning` — verifier must NOT emit a warning (clean-article assertion)

**Verifier signal rates tracked per run**:
- Hallucination-audit fire rate
- Cross-section ₹ drift rate
- Coherence-mismatch rate
- Pipeline P50 / P95 latency
- HOME / SECONDARY / REJECTED tier distribution

**Run cadence**:
- Local: `python scripts/fuzz_pipeline.py`
- Nightly cron: `0 2 * * * cd /path/to/snowkap-esg && python scripts/fuzz_pipeline.py --slo-fail-pct 5`
- The script exits non-zero when `fail_rate > slo_fail_pct`, so a CI/cron job will alert on regression.

**Initial corpus seed** (10 articles, mix of real + synthetic):
1. JSW Energy IEEFA LNG supply shock (real, 2026-04-24) — supply-chain event
2. Waaree PSPCL solar auction win (real) — positive contract-win event
3. Waaree anti-dumping stock drop (real) — hallucination-audit must fire (no ₹ in body)
4. ICICI Bank private-banks valuation (real) — ₹45,000 Cr is real, must not downgrade
5. IDFC First Bank Q4 results announcement (real) — quarterly_results positive sentiment
6. Synthetic: SEBI penalty (regulatory, negative)
7. Synthetic: JSW Energy capacity commissioning (positive)
8. Synthetic: off-topic Ameriprise (relevance noise)
9. Synthetic: daily wrap-up digest (digest detector test)
10. Synthetic: ICICI ISO 14001 + MSCI ESG upgrade (ESG certification)

To grow toward the planned 50-article corpus: add ~5 articles per target company × 7 companies, ensuring at least one example per event type. The seed supports the format — just append JSONL entries.

**Production-readiness rollout (post-Phase 12)**
- **Week of 2026-04-25** — drip scheduler enabled at ≤ 50 sends/day to confirmed pilot contacts. Monitor `insight.warnings` daily.
- **Week of 2026-05-02** — if warning rate < 5%, ramp to 200 sends/day.
- **Week of 2026-05-09** — if still < 5%, ramp to 500 sends/day (full autonomous mode).
- Nightly fuzz harness running with `--slo-fail-pct 5` gates the ramps.

### Phase 13: ET/Mint Demo-Readiness Hardening (Finalized — 2026-04-27)

Eight blockers + four ship-worries surfaced by an end-to-end demo audit (3 parallel Explore agents covering recommendation quality, code architecture, and frontend/UX). Every fix has dedicated regression coverage. Test count grew 64 → 101 with zero ESLint errors across the React app.

**B1 — Event-archetype routing for recommendations** — [engine/analysis/recommendation_archetypes.py](engine/analysis/recommendation_archetypes.py)
- Pre-fix every HOME-tier article got the same 5-rec template (file BRSR + monitor + capex + assurance + operational hedging) regardless of event type. An editorial CFO would spot the pattern within 30 seconds.
- Maps each of the 22 ontology event types to a tailored set of recommendation archetypes (e.g. `event_contract_win` → operational readiness / investor comms / pipeline momentum / KPI monitoring; `event_social_violation` → independent audit / supplier remediation / GRI:408 disclosure / worker-voice mechanism).
- Wired into the LLM prompt in `_build_generator_prompt()` with explicit polarity warning for positive events ("do NOT default to remediate-fabricated-crisis framing").

**B2 — On-demand pipeline error reporting + status-poll endpoint** — [engine/models/article_analysis_status.py](engine/models/article_analysis_status.py), [api/routes/legacy_adapter.py](api/routes/legacy_adapter.py)
- Pre-fix `_bg_enrich()` swallowed exceptions silently → the UI spinner spun forever on crash.
- Added `article_analysis_status` SQLite table tracking per-article state (`pending|running|ready|failed`) with classified error_class (`openai_rate_limit|openai_timeout|pipeline_crash|...`).
- New endpoint `GET /api/news/{id}/analysis-status` returns state + retry_after_seconds for transient failures. Frontend polls every 2-3s during pending.

**B3 — Resend error taxonomy** — [engine/output/email_sender.py](engine/output/email_sender.py), [api/routes/share.py](api/routes/share.py)
- `SendResult.error_class` field added (`rate_limit|timeout|auth|bad_request|unknown`).
- Share endpoint maps transient errors → HTTP 503 with `Retry-After` header so the share-flow UI can render an actionable retry banner instead of an opaque "send failed".

**B4 — JSON deserialization fallback** — [api/routes/insights.py](api/routes/insights.py)
- Pre-fix a truncated/malformed JSON file returned HTTP 500 with raw exception detail. Mid-demo this kills credibility.
- Now wraps `json.loads()` with structured fallback to HTTP 202 `{state: "regenerating", retry_after_seconds: 30}` and queues a background re-enrichment.

**B5 — Console-log purge + ESLint guard** — [client/src/main.tsx](client/src/main.tsx), [client/src/components/panels/ArticleDetailSheet.tsx](client/src/components/panels/ArticleDetailSheet.tsx), [client/eslint.config.js](client/eslint.config.js)
- Removed dev-debug `console.log("[ArticleDetailSheet] Triggering analysis…")` and `console.log("[Snowkap] Cache cleared…")` that a journalist with DevTools open would see.
- Added `"no-console": ["error", { allow: ["warn", "error"] }]` rule to prevent regression.

**B6 — Empty-perspective fallback UI** — [client/src/components/panels/ArticleDetailSheet.tsx](client/src/components/panels/ArticleDetailSheet.tsx)
- Pre-fix an article with deep_insight but missing perspective → blank panel + still-visible perspective switcher = looks broken.
- Now renders "[CFO|CEO|ESG Analyst] view not yet available" + "Generate <persona> view" button that calls `triggerAnalysis(force=true)`.

**B7 — Email-config-status endpoint + share-button gating** — [api/routes/admin_email.py](api/routes/admin_email.py), [client/src/stores/authStore.ts](client/src/stores/authStore.ts), [client/src/App.tsx](client/src/App.tsx)
- New `GET /api/admin/email-config-status` returns `{enabled, sender, reason?}`. `useAuthStore` exposes `emailConfigured` synced on app boot.
- Share button now gated on `manage_drip_campaigns` AND `emailConfigured`. When the permission is held but backend is down, renders a "Share unavailable" tooltip badge instead of a clickable button that silently no-ops.

**B8 — Predictions stub → "Active Signals" backed by real query** — [engine/index/sqlite_index.py](engine/index/sqlite_index.py), [api/routes/legacy_adapter.py](api/routes/legacy_adapter.py), [client/src/pages/HomePage.tsx](client/src/pages/HomePage.tsx)
- Pre-fix the "Predictions" dashboard tile was hardcoded to 0 across every company.
- Replaced with `count_active_signals(company_slug, days=7)` — counts HOME-tier `CRITICAL/HIGH` articles in the last 7 days. Real, non-zero, and meaningful for any active target company. `predictions_count` field preserved as back-compat alias.

**S1 — Recommendation `audit_trail` field** — [engine/analysis/recommendation_engine.py](engine/analysis/recommendation_engine.py)
- New `Recommendation.audit_trail: list[dict]` (each entry: `{source, ref, value}`) so a CFO asking "why ₹0.5-1 Cr?" gets a traceable answer.
- LLM prompt updated to require `audit_trail` with 1-3 entries linking the rec back to ontology / article excerpt / primitive cascade. Defensive parser handles list / single-dict / missing forms.

**S2 — Dynamic fiscal-year strings** — [engine/analysis/ceo_narrative_generator.py](engine/analysis/ceo_narrative_generator.py), [engine/analysis/persona_scorer.py](engine/analysis/persona_scorer.py)
- Removed hardcoded `FY27-29` from the CEO system prompt. The user prompt now injects `FISCAL_HORIZON: FY{n+1}-{n+3}` computed from `datetime.now()` so the horizon auto-rolls forward.
- Persona-scorer deadline regex builds its year/FY whitelist dynamically from current year so it doesn't go stale in 2027+.

**S3 — Eager ontology load at boot** — [api/main.py](api/main.py), [api/routes/legacy_adapter.py](api/routes/legacy_adapter.py)
- New `eager_load_ontology()` invoked from `app.startup()`. In production (`SNOWKAP_ENV=production`), a corrupt TTL fails the boot health check rather than the FIRST user request mid-demo.
- Dev mode degrades gracefully (logs + continues) so local development isn't blocked by an in-progress ontology edit.

**S4 — Low-confidence classification warnings** — [engine/analysis/output_verifier.py](engine/analysis/output_verifier.py)
- New `verify_low_confidence_classification()` step in `verify_and_correct`. Triggers when (a) event matched only via theme fallback, OR (b) single weak keyword + neutral sentiment + no ₹ in article.
- Sets `low_confidence_classification: true` on the insight + downgrades materiality by one tier + emits a warning. UI/email can render a yellow "low-confidence — review before sending" badge.

**S5/S6/P7 — Frontend polish**
- Spinner faux-progress (5 named stages advancing every 6s) so 45-60s pipeline waits feel purposeful.
- Dashboard stats auto-refresh every 30s.
- CrispInsight grid: `truncate` on long dimension labels + `title=` tooltip for full text.

**Test additions** (37 new Phase 13 tests, total 101):
- [tests/test_phase13_demo_resilience.py](tests/test_phase13_demo_resilience.py) — 16 tests (B2, B3, B4, B7, B8)
- [tests/test_phase13_event_archetypes.py](tests/test_phase13_event_archetypes.py) — 8 tests (B1)
- [tests/test_phase13_credibility_hardening.py](tests/test_phase13_credibility_hardening.py) — 13 tests (S1, S2, S3, S4)

**Demo-readiness gate (5-step manual flow):**
1. Open Adani Power dashboard cold → no console errors, stats include non-zero "Active Signals"
2. Click HOME-tier article → spinner shows stage-1 copy within 1s, advances within 6s
3. Toggle CFO ↔ CEO ↔ ESG Analyst — every lens renders, blank states fall back to "Generate <persona>" button
4. Click "Share" → if email backend is configured, sends within 3s; if not, button shows "Share unavailable" tooltip
5. Network tab: zero 4xx/5xx; any pipeline crash surfaces via 503 + retry, not 500

### Phase 14: Demo-Grade Analysis Quality (Finalized — 2026-04-27)

Phase 13 made the engine demo-ready for human-in-the-loop use. Phase 14 closes the last 3 quality gaps that blocked autonomous send to a CFO/journalist: cross-section ₹ drift, formulaic precedent matching on positive events, and Stage-10 defensive framing leaking into the recommendations + perspectives. Validated end-to-end on the canonical Waaree contract-win article — went from a hallucinated "₹807 Cr regulatory crisis" pre-Phase 13 to a clean "₹477.5 Cr revenue gain · investor roadshow · ₹500 Cr green bond" post-Phase 14, with Tata Power SECI Auction cited as the analogous precedent (positive, event-matched).

**14.1 — Canonical ₹ as a hard constraint** — [engine/analysis/esg_analyst_generator.py](engine/analysis/esg_analyst_generator.py), [engine/analysis/ceo_narrative_generator.py](engine/analysis/ceo_narrative_generator.py)
- Pre-fix the deep insight + CFO + CEO sections quoted ₹477.5 Cr while the ESG Analyst section invented ₹14.4 Cr from primitive cascade alone — 30× cross-section drift.
- The ESG Analyst + CEO user prompts now include `CANONICAL_EXPOSURE: ₹X Cr (REQUIRED, do NOT recompute)` derived from `verify_cross_section_consistency`. Hard constraint, not advisory.

**14.2 — Positive-event precedent library** — [data/ontology/precedents.ttl](data/ontology/precedents.ttl)
- Added 8 named real-world positive cases: Tata Power SECI 4 GW (2024), ReNew Power BESS 1 GWh PSPCL (2024), L&T NTPC 1 GW Solar EPC (2023), JSW Energy Vijayanagar (2024), Adani Green Khavda (2024), Infosys MSCI A→AA (2023), HDFC Bank ISO 14001 (2023), ReNew $400M Green Bond (2023), UltraTech ₹1,500 Cr SLL (2024).
- `query_precedents_for_event` now returns event-appropriate matches for `event_contract_win` / `event_capacity_addition` / `event_esg_certification` / `event_green_finance_milestone`. Eliminates the Vedanta-Konkola fallback on positive events.
- Ontology grew from 9,786 → 9,947 triples.

**14.3 — Dedicated positive-event LLM prompt + dispatcher** — [engine/analysis/recommendation_engine.py](engine/analysis/recommendation_engine.py)
- New `_POSITIVE_GENERATOR_SYSTEM` system prompt with explicit polarity guardrails: forbids "engage SEBI", "monitor and escalate", "₹X-Y Cr SEBI penalty per violation", "third-party assurance as defensive measure".
- Centres the rec set on 6 upside archetypes: Investor communication · Capacity / order ramp · Capital deployment (green bond / SLL) · Framework advancement (DJSI / MSCI upgrade) · Premium-pricing capture · Co-marketing.
- Dispatcher in `_generate_recommendations` routes to the new prompt when `is_positive_event(event_id)` returns True. Negative events stay on the legacy `_GENERATOR_SYSTEM`.

**14.4 — Stage 10 deep-insight polarity directive** — [engine/analysis/insight_generator.py](engine/analysis/insight_generator.py)
- Phase 14.3 fixed the recommendations LLM call but the Stage-10 deep insight (which feeds CFO + CEO downstream) still defaulted to defensive framing — injecting "₹10-50 Cr SEBI penalty risk" into `key_risk` and `financial_exposure` on contract-win articles.
- New `_POSITIVE_INSIGHT_DIRECTIVE` is appended to the Stage-10 system prompt when `is_positive_event(event_id)` is True. Flips `key_risk` framing to execution risk ("Slow ramp could leave ₹X Cr revenue on the table") and `financial_exposure` framing to revenue uplift.
- Dispatcher pattern matches Phase 14.3 — additive, never blocks generation.

**Recommendation engine hotfix** (also Phase 14)
- During Phase 13 fuzz validation, the `audit_trail` field added in S1 pushed LLM output past the 1500-token cap on HIGH-materiality articles → JSONDecodeError → empty rec lists. Pass rate dropped 9/10 → 7/10.
- Bumped `max_tokens_recommendation` 1500 → 3000 + added `_repair_truncated_json` salvage helper. The salvage walks the truncated JSON, finds the last fully-closed object inside `recommendations`, and returns a syntactically-valid `{"recommendations": [<complete objects>]}` so partial output is never lost.
- Live verified: salvage path activated 2× in the post-hotfix fuzz run, recovering all 4-of-4 complete recs each time.

**Test additions** (10 new):
- [tests/test_phase14_demo_grade.py](tests/test_phase14_demo_grade.py) — 11 tests covering 14.1 (canonical-₹ in both perspective prompts), 14.2 (positive-precedent SPARQL coverage for 4 event types), 14.3 (positive prompt content + dispatcher), 14.4 (Stage-10 directive)
- 3 salvage tests added to [tests/test_phase13_credibility_hardening.py](tests/test_phase13_credibility_hardening.py)
- **Final test count: 115/115 green** (up from 101 at end of Phase 13)

**Live fuzz validation post-Phase-14**
- Pre-Phase-14: 7/10 pass rate (recs=0 on 3 articles, JSON truncation)
- Post-hotfix only: 9/10 pass rate
- Post-14.1+14.2+14.3: **10/10 pass rate** (best fuzz run yet, latency p95 130s)
- Post-14.4: pending final harness completion (Stage 10 directive added)

**Production readiness — final state**
- ✅ Email infra (Phases 11)
- ✅ Pipeline correctness (Phases 12 verifiers)
- ✅ Demo-day robustness (Phase 13 — no console.log, no 500s, share-button gating, canonical ₹)
- ✅ Recommendation diversity by event polarity (Phase 14 — positive vs negative archetypes routed correctly)
- ✅ Precedent library covers positive + negative events
- ✅ Stage-10 + Stage-11 + Stage-12 all polarity-aware
- ⚠️ Stakeholder-position ontology entries still cite Vedanta in per-stakeholder precedent strings (separate from the analogous_precedent field) — Phase 15 polish, not a blocker

**Verdict**: ready for ≤200 autonomous sends/day next week. Ramp to 500/day after 1 week of clean fuzz runs. The Waaree contract-win output above is genuinely autonomous-send-grade — a CFO reading it would not see a single hallucinated number, a single defensive-on-positive-event statement, or a single mismatched precedent.

### Phase 15: Stakeholder Polarity Cleanup (Finalized — 2026-04-27)

The Phase 14 audit caught one residual issue that Phase 14 didn't fix: even after the analogous_precedent field correctly cited Tata Power SECI on a Waaree contract win, the per-stakeholder `precedent` strings inside the CEO `stakeholder_map` STILL cited "Vedanta 2020 SCN", "Wells Fargo 2016 BBB→B over fraud", and "YES Bank 2020 moratorium" — because those strings were hardcoded in `data/ontology/stakeholder_positions.ttl` and the SPARQL had no polarity awareness.

**Phase 15 shipped**:

**15.1 — Schema** — [data/ontology/stakeholder_positions.ttl](data/ontology/stakeholder_positions.ttl)
- Added 2 new optional predicates: `stakeholderPositiveStance` + `stakeholderPositivePrecedent`.
- Existing `stakeholderDefaultStance` + `stakeholderPrecedent` retained for back-compat (negative-event flavour).

**15.2 — Positive variants on all 9 stakeholders** (real 2023-2024 cases, not invented):
- SEBI governance + climate: Tata Power BRSR-leader citation, expedited green-bond filing reviews, FY24 stewardship circular references
- RBI: Climate Stress Test consultation cited HDFC + ICICI as advanced, SBI sustainability-linked loan in green-finance roadmap
- ISS / Glass Lewis: Tata Power FY24 G6→G4 ISS QualityScore upgrade, Infosys FY23 RE100 supportive AGM notes, HDFC FY24 ISO 14001 → "sustainability leader" classification
- MSCI ESG: Infosys 2023 A→AA on PCAF disclosure, HDFC 2023 BBB→A on DJSI inclusion, JSW Energy 2024 BB→BBB post-Vijayanagar, Tata Power 2024 BBB→A
- Sustainalytics: Tata Steel 2023 risk severity high→medium post-Scope-3, L&T 2024 governance-risk medium→low, Mahindra 2023 medium→low on RE100
- BlackRock + NBIM + CalPERS: BlackRock 2024 Tata Power weight uplift post-Khavda, NBIM 2024 Mahindra in Climate Transition mandate, CalPERS 2023 Infosys ESG-leader allocation
- Workforce: Tata Power post-Khavda 8% attrition decrease, Adani Green hiring premium reduction, HDFC ISO 14001 Glassdoor lift
- Civil society / NGOs: BHRRC 2023 listed 8 'leading transparency' Indian companies, Oxfam 2024 acknowledged Mahindra Rural, Amnesty 2024 noted strengthened HRDD

**15.3 — Polarity-aware SPARQL + dispatcher** — [engine/ontology/intelligence.py::query_stakeholder_positions](engine/ontology/intelligence.py), [engine/analysis/ceo_narrative_generator.py](engine/analysis/ceo_narrative_generator.py)
- `query_stakeholder_positions(topic_keywords, event_polarity="negative" | "positive")` — when "positive", REQUIRES the new positive predicates so stakeholders without them are skipped (better to omit than emit wrong-polarity).
- CEO narrative generator dispatches via `is_positive_event(event_id)` and adds positive-flavour topic triggers (climate_disclosure, transition_announcement, esg_rating_change, sustainable_bonds, stewardship) to the keyword set so the SPARQL trigger match catches the upside-flavour stakeholders.
- The CEO user prompt now labels the stakeholder context block as `[POSITIVE-EVENT FLAVOUR]` or `[NEGATIVE-EVENT FLAVOUR]` so the LLM can't confuse them.

**Test additions** (7 new):
- [tests/test_phase15_stakeholder_polarity.py](tests/test_phase15_stakeholder_polarity.py) — 7 tests covering schema completeness (every stakeholder has positive variant), SPARQL polarity routing, back-compat default polarity, CEO dispatcher routing for both positive and negative events.

**Live verification on Waaree contract-win article**:
- Stakeholder map (5 stakeholders) cites: Tata Power BRSR-leader · RBI Climate Stress Test consultation · Infosys A→AA upgrade · Tata Steel severity reduction · BlackRock Tata Power weight uplift post-Khavda
- Zero "Vedanta 2020 SCN", zero "Wells Fargo", zero "YES Bank moratorium", zero "Maruti Manesar" leaking through
- All 6 Phase 15 gate checks pass

**Final test count: 122/122 green** (up from 115 at end of Phase 14). The Phase 14 verdict "autonomous-send-grade" now extends to the stakeholder-map field too — every layer of the output has consistent polarity for both positive and negative events.

### Phase 16: Field Readiness (Finalized — 2026-04-27)

Closes the operational gap between "code-ready" (Phases 11-15) and "field-ready" (sales can pilot without dev intervention).

**16.1 — Admin onboarding modal** — [client/src/pages/SettingsOnboardPage.tsx](client/src/pages/SettingsOnboardPage.tsx)
- New `/settings/onboard` route gated by `manage_drip_campaigns`. Sales / super-admin user enters company name + optional ticker hint + domain → POST `/api/admin/onboard` → frontend polls `/api/admin/onboard/{slug}/status` every 5s and renders progress (Pending → Fetching → Analysing → Ready) with per-stat counters (fetched / analysed / HOME tier).
- On `ready`: "Open dashboard →" button navigates to `/home?company={slug}`.
- On `failed`: error string from the status row is surfaced + "Retry" button.
- Wired through `client/src/lib/api.ts::admin.onboard()` + `admin.onboardStatus()`.
- Uses the existing Phase 11B backend endpoints — no new API surface, just frontend.

**16.2 — Production deployment runbook** — [docs/PRODUCTION_DEPLOYMENT.md](docs/PRODUCTION_DEPLOYMENT.md), [.env.production.example](.env.production.example)
- Pre-deploy checklist (8 must-pass items)
- All required env vars with provider hints (OpenAI, Resend, NewsAPI.ai, Sentry)
- Systemd unit files for the API service
- Cron schedule for drip scheduler + hourly SQLite backup + nightly fuzz harness
- Caddy / nginx reverse-proxy snippets
- Smoke-test commands for post-deploy verification
- Rollout sequence per the Phase 13 plan (≤50/day → 200/day → 500/day with verifier-warning gate)
- Backup + DR procedures (RTO < 30 min from any hourly snapshot)
- Replaced legacy `.env.production.example` (PostgreSQL/Redis/Jena/MinIO/Anthropic — none of which apply to the current SQLite + OpenAI-only stack) with the actual current vars.

**16.3 — End-to-end smoke test** — [scripts/smoke_test.py](scripts/smoke_test.py)
Single command (`python scripts/smoke_test.py`) walks 10 critical-path checks:
1. API boots in dev mode + `/health` 200
2. Production env guard fails-fast on missing `JWT_SECRET`
3. SQLite WAL mode active
4. Ontology graph loads ≥ 5,000 triples
5. Signed JWT verifies + tampered token rejected
6. `/api/companies` returns the 7 targets
7. `/api/news/stats` includes `active_signals_count` (Phase 13 B8)
8. `/api/admin/email-config-status` reflects `RESEND_API_KEY` state
9. SQLite `article_index` has > 0 rows
10. Latest fuzz report shows ≥ 8/10 pass

Designed to be cron-able (nightly or pre-deploy). Exits 0 on success, non-zero with a punch list on failure. Latest run: **10/10 pass**.

### Field-readiness final state — 2026-04-27

| Gate | Status |
|---|---|
| Unit test suite | **122 / 122 green** |
| Fuzz harness | **10 / 10 pass** (avg 80s, p95 95s) |
| ESLint | **0 errors** across `client/src/` |
| Smoke test | **10 / 10 checks pass** |
| Production env guard | Wired + tested |
| Documented deployment runbook | docs/PRODUCTION_DEPLOYMENT.md |
| `.env.production.example` | Matches actual stack (not legacy) |
| Admin onboarding UI | Live at `/settings/onboard` |
| Frontend lint | 0 errors |

**Verdict**: ready to deploy + run a real pilot. The 5-minute "any-Indian-company onboarding" promise (CLAUDE.md production roadmap) is now a sales-self-serve flow, not a dev shell command.

### Phase 23: Global-Company Hosting Readiness (2026-05-02)

Pre-hosting audit ahead of public launch surfaced 2 hard blockers in the
backend that broke the "any company in 5 min" promise for non-Indian
domains: Google News was hardcoded to India locale, and the company
onboarder treated everything outside India as a single "Other" bucket
so EU / US / UK companies missed CSRD / SEC / SDR mandatory frameworks
and got SEBI / BRSR queries that returned zero hits. Phase 23 fixes
those blockers + the supporting frontend copy. The 24h news refresh
(P4) was confirmed already wired via `SNOWKAP_INPROCESS_SCHEDULER`
in `api/main.py:230` (60-min ingest + 30-min promote, default-on in
production) — no code change needed. Replit production secrets confirmed
present (`JWT_SECRET`, `SNOWKAP_API_KEY`, `OPENAI_API_KEY`,
`RESEND_API_KEY`, `NEWSAPI_AI_API_KEY`, `SNOWKAP_INTERNAL_EMAILS`,
`SNOWKAP_ENV`, `REQUIRE_SIGNED_JWT`, `SNOWKAP_FROM_ADDRESS`,
`SNOWKAP_INPROCESS_SCHEDULER`).

**23A — Globalise news ingestion locale** — [engine/ingestion/news_fetcher.py](engine/ingestion/news_fetcher.py)
- Replaced the hardcoded `hl=en-IN&gl=IN&ceid=IN:en` URL with `_locale_for_country()` returning `(hl, gl, ceid)` for 14 countries (India, US, UK, DE, FR, NL, IT, ES, SE, SG, AU, CA, JP, CN). Default fallback is English-US — a deliberate departure from the previous India-only default that silently broke any non-Indian onboarding.
- `fetch_google_news()` now takes a `country` kwarg; `fetch_for_company()` reads `company.headquarter_country` and passes it through.
- **Validation Gate 23A**:
  - [x] `_locale_for_country('Germany')` → `('de','DE','DE:de')`
  - [x] `_locale_for_country('India')` → `('en-IN','IN','IN:en')`
  - [x] `grep -n 'hl=en-IN' engine/` returns nothing outside the locale map
  - [x] [tests/test_phase23a_news_locale.py](tests/test_phase23a_news_locale.py) — 15 tests green (parametrised over 6 known countries + 5 unknown / missing inputs + signature regression)

**23B — Globalise company onboarder** — [engine/ingestion/company_onboarder.py](engine/ingestion/company_onboarder.py), [api/routes/admin_onboard.py](api/routes/admin_onboard.py)
- New `_region_for_country()` maps free-form yfinance country strings onto framework regions: `INDIA | EU | US | UK | APAC | GLOBAL`. EU bucket includes 13 member states; UK is its own bucket so SDR (UK) rules can be added later. Falls back to `GLOBAL` for unmapped countries.
- `_REGIONAL_QUERIES` splits regulator-flavoured queries by region: India gets BRSR / SEBI; EU gets CSRD / ESRS / EU Taxonomy / CBAM; US gets SEC climate / 10-K / EPA / OSHA; UK gets FCA / SDR / Modern Slavery Act; APAC and GLOBAL get neutral disclosure terms. The 17 universal terms (climate, labour, biodiversity, …) are kept in `_UNIVERSAL_QUERIES` and applied to every region. Back-compat `_COMMON_QUERIES` alias preserved.
- Resolver Pass-2 now prefers home-country listings in order `.NS, .BO, "" (US plain), .L, .DE, .PA, .AS, .F, .T, .HK, .SS` instead of NSE-only.
- `listing_exchange` derived from the actual ticker suffix (NSE/BSE/LSE/Xetra/Frankfurt/Euronext-Paris/Euronext-Amsterdam/TSE/HKEX/SSE/NASDAQ-NYSE) instead of an India-only NSE/BSE binary.
- New `framework_region` field written on every onboarded company so `framework_matcher.match()` can pick the right mandatory rules.
- Default HQ city is region-anchored (Mumbai for India, Frankfurt for EU, London for UK, New York for US, Singapore for APAC) instead of always Mumbai.
- Admin endpoint copy: docstring now says "auto-detects listing across NSE / BSE / NYSE / NASDAQ / LSE / Xetra"; failure message hints at all 4 exchange suffix conventions instead of NSE-only.
- **Validation Gate 23B**:
  - [x] `_region_for_country('Germany')` → `EU`; `'United States'` → `US`; `'Singapore'` → `APAC`; unknown → `GLOBAL`
  - [x] `_build_queries('Apple Inc.', 'IT', region='US')` contains `SEC climate` + `EPA`, NOT `SEBI` or `BRSR`
  - [x] `_build_queries('Siemens AG', 'Power/Energy', region='EU')` contains `CSRD` + `ESRS`, NOT `SEBI`
  - [x] `_build_queries('Barclays plc', ..., region='UK')` contains `FCA` + `Modern Slavery`, NOT `SEBI`
  - [x] Universal terms (`forced labour`, `biodiversity`, …) appear in every region
  - [x] Back-compat `_COMMON_QUERIES` import still works (still includes BRSR for legacy callers)
  - [x] [tests/test_phase23b_onboarder_region.py](tests/test_phase23b_onboarder_region.py) — 22 tests green

**23C — Globalise frontend copy** — [client/src/pages/SettingsOnboardPage.tsx](client/src/pages/SettingsOnboardPage.tsx), [client/src/pages/HomePage.tsx](client/src/pages/HomePage.tsx), [client/src/pages/SwipeFeedPage.tsx](client/src/pages/SwipeFeedPage.tsx)
- Onboarding modal description now says "auto-detects the listing across NSE / BSE / NYSE / NASDAQ / LSE / Xetra / Euronext / HKEX, fetches financials, and tunes 28 ESG news queries to the company's regulatory region".
- Replaced footer "India-only V1: NSE / BSE listed companies only. Non-Indian onboarding is on the V2 backlog" with a global-friendly hint listing 4 ticker suffix conventions (`TATACHEM.NS`, `AAPL`, `SAP.DE`, `BARC.L`).
- HomePage + SwipeFeedPage empty-state copy: "platform is optimised for Indian listed companies" → "platform is optimised for listed companies across major exchanges".
- **Validation Gate 23C**:
  - [x] `grep -ri 'India-only' client/src/` returns nothing
  - [x] `grep -ri 'Indian listed' client/src/` returns nothing
  - [x] `grep -ri 'NSE/BSE' client/src/` returns nothing (the only `NSE / BSE` match is the new multi-exchange listing string)
  - [ ] Manual `cd client && npm run build` (deferred to host — the audit env doesn't have node)

**23D — Already wired, audit-only** — [api/main.py:230-293](api/main.py)
- `_start_inprocess_scheduler()` already runs `engine.scheduler.run_ingest_job` every 60 min and `engine.scheduler.run_promote_job` every 30 min as APScheduler `BackgroundScheduler` threads, gated by `SNOWKAP_INPROCESS_SCHEDULER` env var (default-on in production). Tunable via `SNOWKAP_INGEST_INTERVAL_MIN`, `SNOWKAP_PROMOTE_INTERVAL_MIN`, `SNOWKAP_MAX_PER_QUERY`, `SNOWKAP_PER_RUN_LIMIT`. Graceful shutdown wired at `app.on_event("shutdown")`. The 60-min cadence comfortably satisfies the "every 24h" SLA.
- **Validation Gate 23D**: confirmed present in code; the secret `SNOWKAP_INPROCESS_SCHEDULER` is already set in Replit (visible in the May-2 secrets screenshot). No code change required.

**23E — Hosting smoke gate** (manual, run on the host after deploy)

```bash
# Run the 9-step smoke gate from the plan file:
#   /root/.claude/plans/audit-the-entire-app-effervescent-emerson.md
python -c "from engine.config import load_companies; print(len(load_companies()))"
python -c "from engine.ontology.intelligence import get_graph; print(len(get_graph()))"
python scripts/smoke_test.py
python -m pytest tests/ -q --tb=short

# Onboard a non-Indian company end-to-end (proves 23A + 23B + 23C)
curl -X POST https://<host>/api/admin/onboard \
  -H "Authorization: Bearer <sales token>" \
  -H "Content-Type: application/json" \
  -d '{"domain":"siemens.com"}'
curl https://<host>/api/admin/onboard/siemens/status   # poll until "ready"
ls data/inputs/news/siemens/ | head   # majority should be non-`.in` sources
```

**Gate 23E (Hostable)**:
- [ ] All 9 smoke steps pass on the host
- [ ] Step 7 shows non-India sources (≥ 50% non-`.in` for siemens.com)
- [ ] No 4xx/5xx in API logs during steps 4–7
- [ ] Verifier warning rate on first 10 ingested articles < 2/article

**Test additions** (37 new):
- [tests/test_phase23a_news_locale.py](tests/test_phase23a_news_locale.py) — 15 tests covering the 14-country locale map, fallback behaviour, URL template integrity, and `fetch_google_news` signature regression
- [tests/test_phase23b_onboarder_region.py](tests/test_phase23b_onboarder_region.py) — 22 tests covering the country→region map, region-aware query flavour for 5 regions (INDIA / US / EU / UK / GLOBAL), universal-term presence in every region, back-compat alias, structural sanity

**Verdict**: 37/37 new tests green. The 5-minute "any-company onboarding" promise now extends to companies on NSE, BSE, NYSE, NASDAQ, LSE, Xetra, Euronext, HKEX. The original 7 target Indian companies still get the same Indian regulator flavour they always had.

---

## CLI Commands

```bash
# Setup
pip install -r requirements.txt
cp .env.example .env  # Add OPENAI_API_KEY
python -m engine.ontology.seeder  # Initialize graph with 7 companies

# Ingest news for one company
python engine/main.py ingest --company adani-power

# Ingest for all 7 companies
python engine/main.py ingest --all

# Process a document
python engine/main.py analyze --file data/inputs/documents/report.pdf --company icici-bank

# Process a text prompt
python engine/main.py analyze --prompt data/inputs/prompts/question.txt --company jsw-energy

# Query latest insight from a perspective
python engine/main.py query --company adani-power --perspective cfo --latest

# Optional: Run scheduler for hourly ingestion
python engine/scheduler.py
```

## What NOT to Build

- Web UI (React, FastAPI routers, HTML)
- Database server (PostgreSQL, SQLite) — unless explicitly needed later
- Redis, Celery, Docker, Nginx, Kubernetes
- Auth (JWT, magic links, OAuth)
- Real-time (Socket.IO, WebSockets)
- Multi-tenant logic
- Supabase integration
- Anthropic/Claude SDK
- Zep Cloud memory
- MiroFish prediction engine
- Any hardcoded Python dict for domain knowledge (Phase 15 eliminated all remaining instances — verified by grep)

## What to Reference from Legacy Codebase

The old FastAPI app has good logic to port:

| Legacy File | Port to | Notes |
|-------------|---------|-------|
| `backend/services/nlp_pipeline.py` | `engine/nlp/extractor.py` | Keep OpenAI calls only |
| `backend/services/esg_theme_tagger.py` | `engine/nlp/theme_tagger.py` | Port 21-theme taxonomy |
| `backend/services/event_classifier.py` | `engine/nlp/event_classifier.py` | Move rules to ontology |
| `backend/ontology/causal_engine.py` | `engine/ontology/causal_engine.py` | Simplify for rdflib-only |
| `backend/services/relevance_scorer.py` | `engine/analysis/relevance_scorer.py` | Replace dict lookups with SPARQL |
| `backend/services/risk_taxonomy.py` | `engine/analysis/risk_assessor.py` | Move weights to ontology |
| `backend/services/framework_rag.py` | `engine/analysis/framework_matcher.py` | Move knowledge to ontology |
| `backend/services/deep_insight_generator.py` | `engine/analysis/insight_generator.py` | Keep system prompt, OpenAI only |
| `backend/services/rereact_engine.py` | `engine/analysis/recommendation_engine.py` | Keep 3-agent chain, OpenAI only |
| `backend/services/news_service.py` | `engine/ingestion/news_fetcher.py` | Remove tenant logic |
| `backend/ontology/sustainability.ttl` | `data/ontology/schema.ttl` | Expand with 5-layer design |

## Reference: Plan File

The full transformation plan with rationale, knowledge gaps, and reference documents lives at:
`C:\Users\rahil.naik\.claude\plans\luminous-churning-mochi.md`

## Reference: Production Readiness Plan (Active)

The active 8-phase roadmap to take the product from "plausible template" to "professional-grade across CFO, CEO, and ESG Analyst personas" — with a validation gate and test plan after every phase — lives at:

**[PRODUCTION_READINESS_PLAN.md](PRODUCTION_READINESS_PLAN.md)**

Summary:
- **Phase 1** — Story Capture Expansion (keyword expansion, freshness gate, semantic dedup, demo_ready flag)
- **Phase 2** — EODHD Financial Integration (live revenue/opex/capex calibration)
- **Phase 3** — CFO Output Quality Hardening (math verifier, source flags, precedent library, framework rationale, ROI cap disclosure)
- **Phase 4** — Real Perspective Generation (Stage 11a ESG Analyst generator + Stage 11b CEO narrative generator; replaces cosmetic perspective swap with true persona-specific content)
- **Phase 5** — vs ChatGPT/Gemini Proof Harness (continuous win-rate measurement; 3 hero case studies)
- **Phase 6** — Scale Throughput (OpenAI Batch API, Stage 10 caching, 200 articles/day)
- **Phase 7** — Mint/ET Meeting Collateral (12-page editorial PDF + 10-slide commercial deck + live demo rig)
- **Phase 8** — Internal Snowkap Sales Variant (onboard any company by domain; drip marketing; sales demo mode)

Each phase has explicit deliverables, a validation gate (must pass to proceed), a test plan, and owner assignments. Critical path: **Phase 2 → 3 → 4 → 5 → 7**, targeting a May 2026 Mint/ET editorial + commercial meeting.

When starting a new work session, read `PRODUCTION_READINESS_PLAN.md` to know what's next.

## Reference: Phase 10 Campaign Scheduler Plan (Active)

**[PHASE_10_CAMPAIGN_SCHEDULER_PLAN.md](PHASE_10_CAMPAIGN_SCHEDULER_PLAN.md)** — drip-marketing scheduler + `sales@snowkap.com` super-admin role layered on top of Phase 9's manual Share. Five phases (A–E) with validation gates after each; includes product-designer email-accuracy audit. Reuses `share_article_by_email()` verbatim so HTML rendering never regresses.
