# Snowkap-ESG: Coverage & Quality Improvement Plan

> Based on Finding.txt audit — April 2026

---

## Problem Statement

The platform's core intelligence pipeline (stochastic + heuristic, causal chains, framework RAG, 6D scoring) is architecturally sound. The real gaps are operational:
- **News coverage volume** is thin (aggregator-dependent)
- **Scan frequency** is too slow for real-time CXO intelligence
- **Scoring calibration** drifts high on routine events
- **Causal chain logic** exists in code but operational status is unclear

---

## Track A — News Coverage (Findings 1 + 3)

*Root problem: source dependency on aggregators with no guaranteed publication-level coverage.*

### Phase A1 — Direct RSS Integration

Add a `RSSFeedService` that polls publication-specific feeds on schedule, bypassing Google News ranking.

| Publication | Feed | Priority |
|---|---|---|
| Mint (livemint.com) | `livemint.com/rss/...` | Critical |
| Economic Times | `economictimes.indiatimes.com/rssfeedstopstories.cms` | Critical |
| Business Standard | `business-standard.com/rss/home_page_top_stories.rss` | High |
| MoneyControl | `moneycontrol.com/rss/...` | High |
| ESG Today | `esgtoday.com/feed` | High |
| Reuters Sustainability | Reuters ESG section RSS | Medium |
| Business Today | `businesstoday.in` feed | Medium |

Articles from RSS feeds are deduplicated and routed into the existing `ingest_news_for_tenant()` pipeline — no changes to downstream logic needed.

**Files to create/modify:**
- `backend/services/rss_feed_service.py` — new RSS polling service
- `backend/tasks/news_tasks.py` — add RSS ingestion task
- `backend/tasks/celery_app.py` — register beat schedule for RSS

### Phase A2 — Query Breadth Expansion

Expand Google News RSS from single-string queries to a matrix:
- ESG-specific terms × company/sector pairs
- Indian regulatory terms (SEBI, RBI, BRSR, MoEFCC, MCA21)
- Industry-specific ESG terms per tenant's sector

**Files to modify:**
- `backend/services/news_service.py` — query matrix builder

---

## Track B — Scheduling & Operational Reliability (Finding 2)

*The 24h cycle and Celery dependency are the most operationally dangerous gaps.*

### Phase B1 — Frequency Increase

| Task | Current | Target |
|---|---|---|
| Full news ingest | Every 24h | Every 4h |
| RSS poll (new) | None | Every 1h |
| Deep insight generation | Async per-article | Unchanged |
| Article decay | Every 6h | Unchanged |

**Files to modify:**
- `backend/tasks/celery_app.py` — update beat schedule

### Phase B2 — APScheduler Fallback

Add APScheduler embedded in the FastAPI app so ingestion fires even without a running Celery worker. Critical for dev and single-server deployments.

```python
# runs inside uvicorn process, no Celery required
scheduler.add_job(rss_poll_all_tenants, "interval", hours=1)
scheduler.add_job(refresh_all_tenants, "interval", hours=4)
```

**Files to create/modify:**
- `backend/core/scheduler.py` — new APScheduler setup
- `backend/main.py` — start/stop scheduler with app lifecycle

### Phase B3 — Manual Scan Trigger in UI

Surface the existing `POST /api/news/refresh` endpoint as a "Scan Now" button in the admin panel.

**Files to modify:**
- `client/src/components/` — add scan trigger button to settings/admin panel

---

## Track C — Intelligence Quality (Findings 4, 5, 6)

*Making analytical output more reliable and differentiating.*

### Phase C1 — Causal Engine Audit + First/Downstream Labeling

1. Audit whether Apache Jena / triple store is configured and `analyze_article_impact()` is wired into the ingest pipeline
2. If Jena not set up: surface first-order/downstream distinction directly in the deep insight output using entity extraction
3. Every article's insight should explicitly label whether the company is:
   - **Direct subject** — company is the actor
   - **First-order affected** — same sector / supply chain
   - **Downstream affected** — indirect exposure

**Files to modify:**
- `backend/services/ontology_service.py` — audit pipeline wiring
- `backend/services/deep_insight_generator.py` — add causal label to output

### Phase C2 — Scoring Calibration + Guard Rails

Three-layer fix for LLM impact score drift:

**Layer 1 — Pre-score event classifier (heuristic)**
Before LLM runs, classify article into event type via keyword rules. Each event type gets a score ceiling:

| Event Type | Score Ceiling | Score Floor |
|---|---|---|
| Routine capex / expansion | 5 | — |
| Regulatory fine < ₹10 Cr | 4 | — |
| Policy / framework update | 6 | — |
| Criminal indictment / fraud | — | 8 |
| License revocation | — | 8 |
| Major M&A (>₹1000 Cr) | — | 7 |
| Systemic regulatory change (sector-wide) | — | 8 |

**Layer 2 — Prompt anchoring**
Add 3–5 real Indian market examples as calibration anchors in the system prompt.

**Layer 3 — Post-score validation**
After LLM generates score, heuristic check: if score ≥ 7 but no financial quantum (₹ amount or % impact) present in article, flag for downward adjustment.

**Files to modify:**
- `backend/services/deep_insight_generator.py` — pre/post score guards
- New: `backend/services/event_classifier.py` — keyword-based event type detector

### Phase C3 — Pipeline Trace + Heuristic Layer Strengthening

- Add `pipeline_trace` field to stored article data showing which stage was heuristic vs. LLM (useful for debugging and building user trust)
- Expand framework inference rules in `ontology_service.py` with more Indian regulatory references: MCA21, CPCB, POSH Act, FSSAI, DPDP Act

**Files to modify:**
- `backend/services/ontology_service.py` — framework inference rules
- `backend/tasks/news_tasks.py` — add pipeline_trace to article metadata

---

## Implementation Sequence

```
Week 1
├── Day 1–2: Track B — APScheduler fallback + frequency increase
├── Day 2–3: Track A — RSS feed integration (Mint, ET direct coverage)
└── Day 3–4: Track A — Query breadth expansion

Week 2
├── Day 1–2: Track C — Causal engine audit + first/downstream labeling
├── Day 2–4: Track C — Score pre-classifier + guard rails
└── Day 4–5: Track B — Scan Now UI trigger + Track C — pipeline trace field
```

## Quick Wins (ship in 1 day each)
- Direct RSS for Mint + ET → fixes Finding 3 definitively
- APScheduler in uvicorn → fixes Finding 2 in dev without Celery
- Score ceiling by event type → materially improves Finding 5

---

## Success Metrics

| Metric | Current | Target |
|---|---|---|
| Articles ingested per day (per tenant) | ~20 | 60–100 |
| Mint/ET articles in feed | Incidental | Guaranteed |
| Max story age at ingestion | 23h | 3h |
| Impact score accuracy (routine events scored ≤5) | ~60% | >90% |
| First/downstream distinction visible in output | No | Yes |
| Ingest works without Celery worker | No | Yes |
