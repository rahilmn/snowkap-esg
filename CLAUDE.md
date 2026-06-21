# Snowkap ESG Intelligence Engine — CLAUDE.md

> **Phase 46 rebuild — 2026-05-28.** This file is the single current-state
> spec. Historical phase logs (38 prior phases) are archived to
> [`docs/CHANGELOG.md`](docs/CHANGELOG.md). Read that file for the chronology;
> read this one for what's true today.

> **Phase 48 update — 2026-06-01 (NewsAPI.ai-only relaunch on Railway).**
> Material changes since the Phase 46 rebuild below:
> - **News source: NewsAPI.ai ONLY.** Google News RSS + `googlenewsdecoder`
>   + `trafilatura` full-text backfill are removed. One complex EventRegistry
>   query per company (company-in-title AND any ESG term, `dateStart`=30d)
>   returns full bodies + hero images. `engine/ingestion/news_fetcher.py::
>   fetch_newsapi_ai_for_company`. ~18 tokens/company.
> - **Tier gate is BACK (clean).** The deck is **3 critical** (full Stage
>   10-12 + lede + Opus approval) + **7 light** (Stages 1-9 only — a valid
>   `build_light_analysis` card, no lede/recs/₹). Orchestrated by
>   `engine/analysis/deck_builder.py::build_company_deck` (shared by onboard
>   v3 AND the Sunday cron). This REVERSES old rule #5 — see §12.
> - **Approval LLM (Opus 4.6).** `engine/analysis/approval_gate.py` reviews
>   every critical article's analysis against the source body before display.
>   Rejected criticals are DEMOTED to the light tier (never shown with their
>   fabricated lede/recs). Backfill capped at `n_critical+3` pipeline runs.
> - **Hero images** flow via `shared_analysis.image_url` (no migration).
> - **Postgres ONLY, hard.** SQLite raises unless `SNOWKAP_ALLOW_SQLITE=1`
>   (tests only). Enforced at boot in every env.
> - **Hosting: Railway** (`Dockerfile` + `railway.toml`, 1 worker). Replit
>   artifacts removed.
> - **Weekly Sunday cron** (`run_weekly_deck_refresh_job`) + **Morning-Brew
>   newsletter** to auto-subscribed users (`SNOWKAP_NEWSLETTER_ENABLED=1`).
> - Relaunch scripts: `scripts/relaunch_clean_slate.py`,
>   `scripts/reonboard_nine.py`.

> **Phase 52 update — 2026-06-21 (cost-effective model).**
> - **`reasoning_heavy` is now `anthropic/claude-sonnet-4.6`, NOT Opus 4.6.**
>   Every "Opus 4.6" reference below (Stage 10 deep insight, Stage 12 recs, the
>   editorial lede, the approval gate, and the company resolver) now runs on
>   **Sonnet 4.6 via OpenRouter** — ~5× cheaper ($3/$15 vs $15/$75 per M),
>   output still protected by the approval + quality gates. Onboard/rebuild cost
>   drops from ~$4.50 to ~$0.90. Opus is restorable for a one-off high-value
>   rebuild via `SNOWKAP_REASONING_MODEL=anthropic/claude-opus-4.6` (no redeploy).
> - **`chat` → `anthropic/claude-sonnet-4.6`** (the in-app Ask).
> - Routing health is provider-based now (`reasoning_on_openrouter` /
>   `/api/health/routing` / `snowkap_llm_reasoning_on_openrouter` gauge), not
>   "opus_active" — the alert is "degraded to gpt-4.1 fallback", not "not Opus".

---

## 1. What Snowkap is

A daily ESG intelligence briefing for CFOs, CEOs, Heads of ESG, Risk Officers,
and Heads of IR at top-1000 listed companies — globally. A user enters their
company domain; the engine resolves the company via an LLM (Opus 4.6), fetches
ESG-relevant news, and produces:

1. A **deck** of 3 top-priority articles, each with a 4-bullet brief
   (`what_changed` · `why_it_matters` · `what_it_triggers` · `what_to_watch`)
2. A **2-3 sentence editorial lede** above each brief (story-driven opener
   in Mint / FT / Bloomberg register)
3. **Professional-grade recommendations** per article (named peer benchmark +
   real framework section + ₹ budget + payback months + ≥2 audit_trail entries
   — recs failing the gate get dropped, not surfaced)
4. A **daily 8 AM email brief** with the top-3 articles and one lead rec each
5. An **ask-anything chat** with full article context

The product is consumed by humans who decide on multi-million-rupee
disclosure, transition, and compliance moves. Every claim must be sourced;
every ₹ figure must trace back to an article quote or an engine cascade
computation tagged `(engine estimate)`.

---

## 2. Tech stack (current)

| Layer | Technology | Notes |
|---|---|---|
| Language | Python 3.12+ | Engine + API |
| LLM gateway | `engine/llm/` → OpenRouter | task-class routing |
| Reasoning-heavy LLM | `anthropic/claude-opus-4.6` | Stage 10 deep insight, Stage 12 recs, lede |
| Composition LLM | `openai/gpt-4.1` | chat, perspective transforms |
| Extraction LLM | `openai/gpt-4.1-mini` | Stage 1-3 NLP |
| Search-aided LLM | `perplexity/sonar-pro` | sentiment forecaster |
| Ontology | rdflib in-process, persists to `.ttl` | ~8,200 triples |
| Storage | Supabase Postgres | strict — SQLite paths exist as test fixtures only |
| Index | Postgres `article_pool` + `company_article_view` | replaces legacy SQLite `article_index` |
| API | FastAPI | ~40 routes |
| Auth | HS256 JWT via `JWT_SECRET` | `api/auth_context.py::mint_bearer` / `decode_bearer` |
| Frontend | React 19 + Vite + Radix UI + Tailwind + Zustand | `client/` |
| News fetch | NewsAPI.ai (EventRegistry) | one query/company (company-in-title AND ESG term, 30d) → full body + hero image. Phase 48: Google RSS + `googlenewsdecoder` + `trafilatura` removed |
| Email | Resend | CID-attached SNOWKAP logo + Morning-Brew layout |
| Scheduling | APScheduler in-process | gated by `SNOWKAP_INPROCESS_SCHEDULER=1` |
| Logging | structlog (JSON) | request-ID middleware + slow-query warnings |
| Monitoring | Sentry + `/metrics` Prometheus endpoint | 15+ series |

**Removed since pre-Phase-46:** PostgreSQL with SQLAlchemy/Alembic, Redis,
Celery, MinIO, Docker, Socket.IO, Zep Cloud, MiroFish forecaster. Replaced
with the slimmer stack above.

---

## 3. The flow that actually matters

```
POST /api/onboard/v3 {domain}
    ↓
[Auth] caller's email domain matches OR caller is super-admin
    ↓
[LLM Resolver] domain → company profile (Opus 4.6, ~5s)
    ↓ Returns canonical_name, ticker, industry, framework_region,
      inferred_painpoints (5-7), inferred_kpis (3-5), default_reader_role
    ↓
[Upsert] companies row in Postgres
    ↓ primitive_calibration_json carries the painpoints + KPIs + role
    ↓
[News fetch] ≤3 articles via fetch_for_company (~30-60s)
    ↓ NewsAPI.ai (EventRegistry): company-in-title AND ESG term, 30d, full body + image
    ↓
[Per-article, parallel max=3, NO tier gate]
    Stage 1-9  →  Stage 10  →  Stage 11  →  Stage 12  →  lede  →  write
   (~10s each)    (~15s)       (~10s)       (~15s)       (~5s)     (~1s)

    Stage 10 = deep insight (Opus 4.6)
    Stage 11 = perspectives (ESG / CEO via dedicated generators; CFO via legacy transform)
    Stage 12 = recommendations (Opus 4.6) + quality gate
    Lede     = 2-3 sentence editorial opener (Opus 4.6)
    Write    = unified_analysis composer + persist insight JSON + Postgres rows
    ↓
[Return 200] {slug, articles: [...], inferred_painpoints, inferred_kpis,
              default_reader_role, elapsed_seconds}
```

**Total wall-clock: ~120-180s** for a typical 3-article onboard. Postgres-only.
Returns when done — no SSE polling, no state machine, no worker queue.

**Cost: ~$4.50 per onboard** (3 articles × ~$1.50 Opus 4.6 across Stages 10
+ 12 + lede). Higher than the legacy tier-gated path; accepted as the price
of professional-grade output on every article.

---

## 4. Critical files (read these first)

| File | What it does |
|---|---|
| [`api/routes/onboard_v3.py`](api/routes/onboard_v3.py) | The one onboarding endpoint. No tier gate, no eager pass, no SSE. ~600 LoC. |
| [`engine/ingestion/llm_company_resolver.py`](engine/ingestion/llm_company_resolver.py) | Domain → CompanyInfo via Opus 4.6. Returns painpoints, KPIs, role. |
| [`engine/main.py`](engine/main.py) | `_run_article` legacy path with tier gate — used by ingest cron, NOT by onboard v3 |
| [`engine/analysis/pipeline.py`](engine/analysis/pipeline.py) | `process_article` — Stages 1-9 |
| [`engine/analysis/insight_generator.py`](engine/analysis/insight_generator.py) | Stage 10 — deep insight via Opus 4.6 |
| [`engine/analysis/recommendation_engine.py`](engine/analysis/recommendation_engine.py) | Stage 12 + quality gate (`enforce_quality_gate`) |
| [`engine/analysis/lede_writer.py`](engine/analysis/lede_writer.py) | Editorial lede pass via Opus 4.6 |
| [`engine/analysis/unified_analysis.py`](engine/analysis/unified_analysis.py) | The 4-bullet composer — single source of truth for `insight.analysis` |
| [`engine/output/writer.py`](engine/output/writer.py) | `write_insight` — the ONE persist path. Stamps schema `3.3-editorial-lede`. |
| [`engine/llm/`](engine/llm/) | OpenRouter gateway. `get_llm_client(task_class=...)` |
| [`engine/ontology/`](engine/ontology/) | rdflib graph + SPARQL queries (`intelligence.py`) |
| [`scripts/validate_phase46.py`](scripts/validate_phase46.py) | 11-test end-to-end validation. Run after any change. |

---

## 5. The 12-stage pipeline (current state)

```
INPUT (Article + Company)
    ↓
[1] NLP extraction (gpt-4.1-mini)                     — sentiment, tone, entities, ESG signals
[2] ESG theme tagging (gpt-4.1-mini)                  — 21 themes
[3] Event classification (ontology-driven)             — 22 event types, score bounds
    ↓
    GATE: irrelevant event → REJECTED (skip 4-12)
    ↓
[4] Relevance scoring (5D, ontology materiality)       — financial / regulatory / compliance / supply-chain / people
[5] Causal chain BFS (0-4 hops, 17 relationship types) — engine/ontology/causal_engine
[6] Geographic matching                                — engine/ontology (climate-zone query retired)
[7] Framework alignment (21 frameworks)                — regional boosts, mandatory rules, section codes
[8] Risk assessment (10 ESG + 7 TEMPLES)              — engine/analysis/risk_assessor
[9] Stakeholder + SDG mapping                         — ontology-driven
    ↓
    Score primitive cascade (computed financial exposure)
    ↓
[10] Deep insight generation (Opus 4.6)               — 9-section JSON, ontology-constrained
[11] Perspective transformation                       — ESG Analyst + CEO via dedicated generators, CFO via legacy
[12] REREACT recommendations (Opus 4.6)               — gate-required: peer + framework + ₹ budget + payback + ≥2 audit_trail
     Lede pass (Opus 4.6)                              — 2-3 sentence editorial opener
    ↓
[Write] unified_analysis composer → insight.analysis
[Persist] data/outputs/{slug}/insights/{id}.json + Postgres article_pool + company_article_view
    ↓
OUTPUT (schema 3.3-editorial-lede)
```

**Stages 3, 4, 5, 6, 7, 8, 9, 11 are ontology-driven** (~80% of intelligence
lives in `.ttl` files, not Python). Only Stages 1-2, 10, 12, lede involve
LLM calls.

---

## 6. Ontology (5 layers, ~8,200 triples)

| Layer | Path | Purpose |
|---|---|---|
| Entity | `data/ontology/companies.ttl` | Company, Facility, Supplier, Industry, GeographicRegion, Commodity, Regulation, Competitor |
| ESG Topics | `data/ontology/knowledge_base.ttl` | 21 themes across Environmental / Social / Governance + sub-metrics |
| Impact Dimensions | `data/ontology/knowledge_expansion.ttl` | Financial, Regulatory, Operational, Reputational + TEMPLES |
| Perspective Lenses | `data/ontology/knowledge_expansion.ttl` | ESG Analyst, CFO, CEO with headline rules + ranking sort keys |
| Framework Mapping | `data/ontology/knowledge_base.ttl` | 21 frameworks: BRSR, GRI, TCFD, CSRD/ESRS, SASB, CDP, ISSB, EU Taxonomy, SFDR, GHG Protocol, SBTi, TNFD, SEC Climate, Porter, McKinsey, BCG, COSO, CFA ESG, DJSI, S&P Global ESG, Edelman Trust |
| Causal Primitives | `data/ontology/primitives_*.ttl` | 22 primitives, 123 P→P edges, 48 P→outcome edges, 69 P3/P4 chains, 77 indicators |
| Risk & Recommendation Rules | `data/ontology/knowledge_expansion.ttl` | RiskLevelThreshold, PriorityRule, RiskOfInactionConfig, framework regional boosts, mandatory rules |
| SASB Materiality | `data/ontology/sasb_materiality.ttl` | Sector → topic materiality weights (banks ≠ industrials) |

SPARQL queries live in [`engine/ontology/intelligence.py`](engine/ontology/intelligence.py).
~50+ query functions, all `@lru_cache`d for stable lookups.

**Critical rule: never hardcode domain knowledge in Python.** If it's a weight,
threshold, mapping, or rule — it goes in `.ttl`. Phase 15 eliminated all
remaining hardcoded dicts; Phase 46 maintains this.

---

## 7. Personalization (Phase 46.A + 46.D)

Onboarding is **domain-only** — the user provides a domain, the LLM resolver
infers everything else. Three personalization signals get stored on the
company row and feed downstream stages:

| Signal | Source | Used by |
|---|---|---|
| `inferred_painpoints` (5-7 strings) | LLM resolver | `criticality_scorer._painpoint_match_component` token-overlap fallback (Phase 46.D); rec engine prompt context |
| `inferred_kpis` (3-5 strings) | LLM resolver | Rec engine prompt context; future: explicit KPI scoring in unified analysis |
| `default_reader_role` (CFO / CEO / Head of ESG / Risk Officer / Head of IR) | LLM resolver | Single perspective shown per article (no UI toggle, Phase 46.C) |

Stored in `companies.primitive_calibration_json` (JSONB column). When the LLM
omits or malforms any signal, industry × region fallback defaults kick in —
no tenant ever has empty personalization signals.

---

## 8. Recommendation quality gate (Phase 46.B)

Every rec must satisfy ALL four:

1. **Named peer** — `peer_benchmark` contains a capitalized proper noun.
   "Tata Power" passes. "industry average" / "best practice" / "leading peers" fail.
2. **Framework section** — matches the regex of known frameworks
   (`BRSR|GRI|TCFD|TNFD|CSRD|ESRS|ISSB|SASB|SBTi|CDP|SEC|CBAM|RBI|SEBI|MCA|IFRS|...`).
3. **₹ budget + payback months** — both `estimated_budget` and `payback_months`
   populated (no "TBD" / "N/A" / null).
4. **Audit trail ≥ 2 entries** — Phase 35 already validates each entry has a
   canonical source (`ontology|article|primitive|peer|precedent|benchmark`)
   and a value ≥ 12 chars; Phase 46.B requires ≥2 such entries per rec.

Recs failing the gate are **dropped**. If ALL recs fail, the deterministic
monitoring rec (Phase 45.H fallback in `_build_monitoring_recommendation`)
guarantees the UI never shows blank "RECOMMENDED ACTIONS".

---

## 9. CLI commands

```bash
# Setup
pip install -r requirements.txt
cp .env.example .env  # Set OPENROUTER_API_KEY, OPENAI_API_KEY, JWT_SECRET, SUPABASE_DATABASE_URL

# Run validation against a live API
python scripts/validate_phase46.py --token <admin-jwt> --domain tatamotors.com

# Run engine ingestion via legacy CLI (cron-style)
python engine/main.py ingest --company adani-power

# Run on-demand enrichment for one article
python engine/main.py analyze --file data/inputs/news/yes-bank/2026-05-28_abc.json --company yes-bank

# Smoke test (10 backend checks)
python scripts/smoke_test.py

# Backend test suite
python -m pytest tests/ -q
```

---

## 10. Environment variables (required in production)

```bash
# Core
SNOWKAP_ENV=production
JWT_SECRET=<32+ random chars>
REQUIRE_SIGNED_JWT=1
SNOWKAP_API_KEY=<your API key>

# LLM
OPENROUTER_API_KEY=<openrouter key>   # Required for Opus 4.6 + Perplexity
OPENAI_API_KEY=<openai key>           # Used as direct fallback

# Storage
SUPABASE_DATABASE_URL=postgresql://...

# News
NEWSAPI_AI_API_KEY=<newsapi.ai key>   # Optional but recommended (full body)

# Email
RESEND_API_KEY=<resend key>
SNOWKAP_FROM_ADDRESS=brief@snowkap.com

# Monitoring (optional)
SENTRY_DSN=<sentry dsn>

# Background jobs
SNOWKAP_INPROCESS_SCHEDULER=1
SNOWKAP_INGEST_INTERVAL_MIN=60
SNOWKAP_PROMOTE_INTERVAL_MIN=30
SNOWKAP_FULL_TEXT_RETRY_HOURS=6
```

---

## 11. The validation contract

A successful Phase 46 production deploy passes `validate_phase46.py` with
**11/11 tests green**:

| # | Test | Asserts |
|---|---|---|
| 01 | Postgres backend active | `engine.db.connection.is_postgres()` |
| 02 | OpenRouter routing active | Opus 4.6 for reasoning_heavy, gpt-4.1 for composition |
| 03 | `POST /api/onboard/v3` returns 200 within 240s | Synchronous, no SSE |
| 04 | Personalization signals present | ≥3 painpoints, ≥2 KPIs, role in canonical set of 5 |
| 05 | `/api/now/feed` returns ≥1 article | Slug alias resolved cleanly |
| 06 | Every deck article has `lede` + `criticality_summary` | The contract that broke in Phase 45 |
| 07 | Every surfaced rec passes the 4-field quality gate | ≤25% may fall back to deterministic monitor |
| 08 | Email send + tone scan clean | No rating bureau strings, no score leaks |
| 09 | Chat with article context returns ≥50-char reply | SSE parser must read `event:` header separately |
| 10 | Postgres rows present (slug_aliases + companies + article_pool) | Write contract holds |
| 11 | `companies.primitive_calibration_json` has painpoints + KPIs + role | Phase 46.A persistence holds |

If any test fails → the failure message identifies the failing layer →
the fix is targeted, not speculative.

---

## 12. Things that NEVER cross into the codebase

Hard rules. Violating any of these is a P0:

1. **No hardcoded domain knowledge in Python.** Weights, thresholds, mappings,
   rules go in `.ttl`. Use SPARQL.
2. **No silent `try/except: pass`.** Every catch must log with full traceback
   (Phase 45.G installed a global FastAPI exception handler that enforces this).
3. **No fallback layers in the writer.** If a Stage produced empty output, the
   article should write the empty output and surface the gap — not paper over it
   with deterministic fillers (the Phase 45.I safety net is being phased out
   now that v3's full-pipeline guarantee makes it unnecessary).
4. **No new code paths in onboarding without removing an old one.** v3 is the
   one onboarding flow. v2 stays for back-compat reads but no new feature lands
   there.
5. **~~No tier gates.~~ REVERSED in Phase 48.** The deck is now a CLEAN tier
   gate: 3 critical (full Stage 10-12 + lede + approval) + 7 light (Stages 1-9
   only). The Phase 45 loop bug was a BLANK `deep_insight` on SECONDARY
   articles; the Phase 48 light tier instead writes a COMPLETE
   `build_light_analysis` block (what_changed + banded why_it_matters +
   frameworks + risks, LOW band, no lede/recs). Tiering lives in
   `engine/analysis/deck_builder.py`, NOT inside `engine.main._run_article`.
6. **No empty `criticality_summary` on disk.** Critical articles also need
   recs (quality gate + monitor fallback). Light articles legitimately have
   NO recs/lede — that's the tier contract, not a gap.
7. **Postgres ONLY — hard (Phase 48.0).** `engine.db.connection.connect()`
   RAISES on the SQLite branch unless `SNOWKAP_ALLOW_SQLITE=1` (tests only).
   `api/main.py` refuses to boot on a non-postgres backend in every env. No
   silent SQLite file creation, ever.
8. **No "Bearer" prefix in `mint_bearer` output.** Caller prepends `Bearer `
   when constructing the `Authorization` header.

---

## 13. Reading order for new contributors

1. **This file** — current architecture in one read
2. [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — phase history (read on-demand)
3. [`api/routes/onboard_v3.py`](api/routes/onboard_v3.py) — the one onboarding flow
4. [`engine/main.py`](engine/main.py) — legacy `_run_article` (cron ingest still uses it)
5. [`engine/analysis/insight_generator.py`](engine/analysis/insight_generator.py) — Stage 10
6. [`engine/analysis/recommendation_engine.py`](engine/analysis/recommendation_engine.py) — Stage 12 + quality gate
7. [`engine/analysis/unified_analysis.py`](engine/analysis/unified_analysis.py) — 4-bullet composer
8. [`engine/output/writer.py`](engine/output/writer.py) — persist path
9. [`engine/ontology/intelligence.py`](engine/ontology/intelligence.py) — SPARQL queries
10. [`engine/llm/`](engine/llm/) — LLM gateway

Once #1-3 are absorbed, the rest is reference material — read what you need.
