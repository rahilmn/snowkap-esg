# SNOWKAP ESG — Beta Release Plan for 7 Indian Companies

## Context

Building the Snowkap ESG platform for beta release targeting 7 specific companies across 4 industries. The platform is ~85% ready — multi-tenant auth, news pipeline, causal chains, and agent chat all work. Key gaps: no pre-seeded company data (beta users would wait for LLM discovery), industry mappings incomplete for banking/power/solar/AMC, and no production deployment config.

## Beta Companies

| # | Company | Domain | SASB Category | HQ | Industry |
|---|---------|--------|---------------|-----|----------|
| 1 | ICICI Bank Ltd | icicibank.com | Commercial Banks | Mumbai | Financials |
| 2 | YES Bank Ltd | yesbank.in | Commercial Banks | Mumbai | Financials |
| 3 | IDFC First Bank Ltd | idfcfirstbank.com | Commercial Banks | Mumbai | Financials |
| 4 | Waaree Energies Ltd | waaree.com | Solar Technology | Mumbai/Surat | Renewable Resources & Alternative Energy |
| 5 | Singularity AMC Pvt Ltd | singularityamc.com | Asset Management | Mumbai | Financials |
| 6 | Adani Power Ltd | adanipower.com | Electric Utilities & Power Generators | Ahmedabad | Infrastructure |
| 7 | JSW Energy Ltd | jsw.in | Electric Utilities & Power Generators | Mumbai | Infrastructure |

---

## Architecture Overview

```
User Signup (beta@icicibank.com)
       │
       ▼
  ┌────────────────────────────┐
  │  Auth (Passwordless JWT)   │  ← Domain validation, auto-tenant
  └────────────┬───────────────┘
               │
       ┌───────┼───────────────────────────┐
       ▼       ▼                           ▼
  ┌─────────┐ ┌─────────────────┐  ┌──────────────────┐
  │ Company │ │ Jena Knowledge  │  │ News Ingestion    │
  │ + Facil │ │ Graph (Fuseki)  │  │ (Google News RSS) │
  │ + Suppl │ │ OWL2 + SPARQL   │  │ + Trafilatura     │
  └────┬────┘ └────────┬────────┘  └────────┬──────────┘
       │               │                     │
       └───────────────┼─────────────────────┘
                       ▼
  ┌─────────────────────────────────────────────────────┐
  │              Article Analysis Pipeline               │
  │                                                      │
  │  1. Entity Extraction (GPT-4o-mini)                 │
  │  2. Jena Entity Resolution (SPARQL label match)     │
  │  3. Causal Chain BFS (max 4 hops, decay scoring)    │
  │  4. Geographic Intelligence (facility proximity)     │
  │  5. Priority Scoring (6-component formula, 0-100)   │
  │  6. 5D Relevance Scoring (0-10, quality gate)       │
  │  7. Executive Insight (specialist personality)       │
  │  8. Deep Insight (7-section, HOME tier only)         │
  │  9. REREACT 3-Agent Validation (Celery background)  │
  └─────────────────────────────────────────────────────┘
               │
               ▼
  ┌──────────────────────────────────────┐
  │         Frontend (React 19)          │
  │                                      │
  │  Home: FOMO stats + priority cards   │
  │  Feed: Swipe cards + detail sheets   │
  │  Agent: 11 specialist AI agents      │
  │  Saved: Bookmarked articles          │
  └──────────────────────────────────────┘
```

---

## Intelligence Pipeline Detail

### 1. Entity Extraction (GPT-4o-mini, ~$0.001/article)
Extracts from article title + content:
- Named entities (company, person, location, event, industry)
- Sentiment (positive/negative/neutral) + confidence score
- ESG pillar (E/S/G) + aspect sentiments per pillar
- Content type (regulatory/financial/operational/reputational/narrative)
- Urgency (low/medium/high/critical)
- 5D Relevance data (ESG correlation, financial impact, compliance risk, supply chain impact, people impact)
- Climate events (water_scarcity, flood, heatwave, etc.)

### 2. Jena Entity Resolution
- Each entity text matched against tenant's knowledge graph via SPARQL label search
- Follows `sameCompany` links to find canonical company URI
- Resolves company names, competitor names, supplier names, facility locations

### 3. Causal Chain BFS (4-hop max)
- BFS from resolved entity URI to company URI in Jena graph
- 17 relationship types: directOperational, supplyChainUpstream/Downstream, workforceIndirect, regulatoryContagion, geographicProximity, industrySpillover, commodityChain, waterSharedBasin, climateRiskExposure, etc.
- Impact decay per hop: 0-hop=1.0, 1-hop=0.7, 2-hop=0.4, 3-hop=0.2, 4-hop=0.1
- Returns up to 5 unique paths, edge-aware deduplication
- Enriched with framework data from Jena (BRSR, GRI, ESRS, TCFD, CDP, etc.)

### 4. Priority Scoring (0-100)
```
Priority = sentiment×25 + urgency×25 + impact×20 + financial×15 + irreversibility×10 + frameworks×5
```
- CRITICAL: ≥85, HIGH: ≥70, MEDIUM: ≥40, LOW: <40

### 5. 5D Relevance Scoring (0-10)
| Dimension | Max | Description |
|-----------|-----|-------------|
| ESG Correlation | 2 | Direct ESG topic relevance |
| Financial Impact | 2 | Revenue/cost/valuation effect |
| Compliance Risk | 2 | Regulatory framework exposure |
| Supply Chain Impact | 2 | Upstream/downstream disruption |
| People Impact | 2 | Workforce/community effect |

**Content Quality Gate:**
- ≥7: **HOME** tier → deep insight + REREACT
- 4-6: **SECONDARY** tier → basic analysis
- <4: **REJECTED** → filtered from feed

### 6. Executive Insight
Generated for articles with priority ≥ 40. Uses specialist agent personality matching the article's content_type (e.g., supply_chain specialist for operational articles, compliance specialist for regulatory).

### 7. Deep Insight (HOME tier, relevance ≥ 7)
7-section structured analysis:
1. Core Mechanism — what happened and why
2. ESG Impact — E/S/G pillar analysis
3. Financial Impact — revenue/cost/valuation
4. Compliance Impact — framework-specific gaps
5. Risk Mapping — threat matrix
6. Time Horizon — immediate/short/medium/long
7. Final Synthesis — executive summary

### 8. REREACT 3-Agent Validation (Background, Celery)
Triggered for HOME-tier articles that have deep_insight:

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   GENERATOR     │───→│    ANALYZER      │───→│   VALIDATOR     │
│                 │    │                  │    │                  │
│ Produces 3-5    │    │ Critiques each   │    │ Independent      │
│ actionable ESG  │    │ recommendation   │    │ hallucination    │
│ recommendations │    │ for feasibility, │    │ check, assigns   │
│ with framework  │    │ data grounding,  │    │ confidence score │
│ references      │    │ ROI estimate     │    │ (High/Med/Low)   │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

**Output stored in `article.rereact_recommendations` JSONB:**
```json
{
  "validated_recommendations": [
    {
      "title": "Strengthen Water Risk Disclosure",
      "description": "...",
      "framework_ref": "CDP:Water, GRI:303",
      "urgency": "high",
      "confidence": "High",
      "feasibility": "Achievable within 6 months",
      "roi_estimate": "Avoids potential $5M regulatory penalty"
    }
  ],
  "analysis_summary": "...",
  "validation_notes": "..."
}
```

---

## Phase 1: Industry-Specific Data Enhancements

### 1A. Expand `INDUSTRY_MATERIAL_ISSUES`
**File:** `backend/ontology/tenant_provisioner.py`

Add entries for beta industries:

```python
"Commercial Banks": [
    "data_privacy", "business_ethics", "systemic_risk",
    "financial_inclusion", "anti_corruption", "cybersecurity"
],
"Electric Utilities & Power Generators": [
    "emissions", "water_management", "community_relations",
    "climate_adaptation", "waste_management", "worker_safety"
],
"Solar Technology & Project Developers": [
    "lifecycle_impacts", "ecological_impacts", "workforce_safety",
    "waste", "supply_chain_labor"
],
"Investment Banking & Brokerage": [
    "business_ethics", "systemic_risk", "data_privacy", "anti_corruption"
],
```

### 1B. Add framework mappings
```python
"cybersecurity": [("BRSR", "P9"), ("GRI", "418"), ("ESRS", "S4")],
```

### 1C. Expand Climate Risk Zones + City Coordinates
**File:** `backend/ontology/geographic_intelligence.py`

New zones: Kutch (heat_stress), Barmer (drought_prone), Gondia (heat_stress), Baran (drought_prone), Surat (industrial_pollution)

New coordinates: Mundra, Vijayanagar, Ratnagiri, Barmer, Gondia, Baran, Tumb, Chikhli

---

## Phase 2: Beta Seed Script

### `backend/scripts/seed_beta.py`

Master script creating all 7 companies with real data.

**Per company creates:**
1. Tenant (domain, industry, SASB category, search queries)
2. User + TenantMembership (beta@{domain}, sustainability_manager role)
3. Company (with competitors JSONB)
4. Facilities (3-6 real locations with lat/lng, climate_risk_zone)
5. Suppliers (3-5 real supply chain with commodity, tier)
6. Jena knowledge graph (ontology + facilities + supply chain + competitors)
7. News ingestion trigger

### Company Data Summary

| Company | Facilities | Suppliers | Competitors |
|---------|-----------|-----------|-------------|
| ICICI Bank | BKC HQ, Hyderabad Tech Park, Kolkata, Chennai | TCS, Infosys, AWS, Wipro | HDFC Bank, SBI, Axis, Kotak |
| YES Bank | Mumbai HQ, Lower Parel, Pune Ops | TCS, Infosys, Oracle | RBL, Bandhan, Federal, IndusInd |
| IDFC First | Mumbai HQ, Chennai, Gurugram | TCS, Wipro, AWS | AU SFB, Kotak, IndusInd, Bandhan |
| Waaree | Mumbai HQ, Surat Factory, Tumb, Chikhli | Tongwei, Daqo, Saint-Gobain, Hindalco | Tata Solar, Adani Solar, Vikram, Renewsys |
| Singularity AMC | Mumbai BKC | Bloomberg, NSE, CRISIL, Kotak Sec | Quant AMC, PPFAS, Motilal Oswal |
| Adani Power | Ahmedabad HQ, Mundra, Tiroda, Kawai, Udupi | Coal India, BHEL, Siemens, IR | NTPC, Tata Power, JSW, Torrent |
| JSW Energy | Mumbai HQ, Vijayanagar, Ratnagiri, Barmer, Salboni | Coal India, BHEL, Siemens, L&T | NTPC, Adani, Tata Power, Torrent |

---

## Phase 3: News Pipeline + REREACT

### 3A. News Ingestion
Trigger Google News RSS + NewsAPI for all 7 tenants. Each gets:
- Sustainability query: `"Company" ESG sustainability [industry terms]`
- General query: `"Company" corporate [industry terms]`
- Competitor news: queries for each competitor

### 3B. Article Analysis
Full pipeline per article: extraction → Jena resolution → causal chains → priority → relevance → executive insight → deep insight → REREACT

### 3C. REREACT Validation
- Requires Celery worker: `celery -A backend.tasks.celery_app worker`
- Processes `news.run_rereact_background` tasks for HOME-tier articles
- Each gets `validated_recommendations[]` with confidence scores
- Stored in `article.rereact_recommendations` JSONB

### 3D. Verification
```sql
-- Articles per tenant
SELECT t.name, COUNT(a.id) FROM tenants t JOIN articles a ON a.tenant_id=t.id GROUP BY t.name;

-- HOME tier count
SELECT t.name, COUNT(*) FROM tenants t JOIN articles a ON a.tenant_id=t.id
WHERE a.relevance_breakdown->>'tier'='HOME' GROUP BY t.name;

-- REREACT count
SELECT t.name, COUNT(*) FROM tenants t JOIN articles a ON a.tenant_id=t.id
WHERE a.rereact_recommendations IS NOT NULL GROUP BY t.name;

-- Causal chain hops
SELECT hops, COUNT(*) FROM causal_chains GROUP BY hops ORDER BY hops;
```

---

## Phase 4: Frontend Build

```bash
cd client && npm run build
```
Fix TypeScript errors. Verify `client/dist/` output.

---

## Phase 5: End-to-End Verification

### Per-Domain Test
For each of the 7 domains:
- [ ] `/api/auth/resolve-domain` returns `is_existing: true`
- [ ] `/api/auth/login` returns JWT with correct tenant_id/company_id
- [ ] `/api/news/feed` returns articles with priority scores
- [ ] `/api/news/stats` returns FOMO stats
- [ ] Causal chains exist with multi-hop paths
- [ ] Agent chat returns company-specific responses
- [ ] No cross-tenant data leakage

---

## Files to Create/Modify

### New Files
| File | Purpose |
|------|---------|
| `backend/scripts/seed_beta.py` | Master seed: 7 companies + facilities + suppliers + Jena + news |

### Modified Files
| File | Change |
|------|--------|
| `backend/ontology/tenant_provisioner.py` | Industry material issues for banking, power, solar, AMC |
| `backend/ontology/geographic_intelligence.py` | Climate risk zones + coordinates for beta facility cities |

---

## Full Verification Checklist

```
# Phase 1: Industry data
[ ] INDUSTRY_MATERIAL_ISSUES has entries for Commercial Banks, Electric Utilities, Solar, Asset Mgmt
[ ] CLIMATE_RISK_ZONES covers Mundra, Barmer, Gondia, Baran, Surat, Kutch
[ ] CITY_COORDINATES has lat/lng for all beta facility cities

# Phase 2: Seeding
[ ] All 7 tenants created with correct domains
[ ] All companies have 3-6 facilities with climate_risk_zone
[ ] All companies have 3-5 suppliers with commodity and tier
[ ] All companies have competitors JSONB populated
[ ] Jena graphs provisioned with 200+ triples each
[ ] News ingestion triggered for all 7 tenants

# Phase 3: Pipeline + REREACT
[ ] Each tenant has 5+ articles with priority scores
[ ] At least 2 HOME-tier articles per tenant
[ ] Causal chains include multi-hop paths (hops > 0)
[ ] Executive insights generated for priority >= 40 articles
[ ] Deep insights generated for HOME-tier articles (relevance >= 7)
[ ] REREACT recommendations generated for HOME-tier articles with deep insight
[ ] Celery worker processes run_rereact_background tasks successfully

# Phase 4: Frontend
[ ] npm run build succeeds with no TypeScript errors
[ ] client/dist/ output is complete

# Phase 5: E2E
[ ] All 7 domains resolve correctly via /auth/resolve-domain
[ ] Login returns valid JWT for each domain
[ ] Feed API returns scored articles per tenant
[ ] Home page shows FOMO stats + priority cards
[ ] Agent chat responds with company-specific knowledge
[ ] No cross-tenant data leakage
[ ] REREACT recommendations visible in article detail view
```
