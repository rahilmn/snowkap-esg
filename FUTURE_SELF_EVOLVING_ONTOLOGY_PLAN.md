# Self-Evolving Ontology — Making the System Learn From Every Article

## Context

The Snowkap ESG ontology is 97% read-driven: 9 of 12 pipeline stages query the ontology, but 0 stages write back. Every article produces rich structured data (entities, themes, events, causal mechanisms, stakeholder framing) that is written to JSON files and never fed back into the ontology. The system gets smarter at analyzing articles but never grows its own knowledge base.

The mutation infrastructure EXISTS (`graph.insert_triples()`, `persist_companies()`, seeder batch pattern) but is completely unused after initialization. This plan wires the feedback loop.

---

## The Problem

Today when the system processes an article mentioning "Tata Power" as a competitor to Adani Power, it extracts the entity but discards it. Next time an article mentions Tata Power, it extracts it again — never learning. Same for novel ESG themes, new event patterns, undiscovered causal links, and emerging regulatory frameworks.

## What the System Should Learn (7 Categories)

| # | Category | Example | Source Signal | Risk |
|---|----------|---------|--------------|------|
| 1 | **New Entities** | "Tata Power" mentioned as competitor | `NLPExtraction.entities` + `entity_types` | Low (appendable) |
| 2 | **New ESG Themes** | "Carbon Offset Fraud", "AI Ethics in ESG" | `ESGThemeTags.primary_theme` not in 21 known | Medium (needs weights) |
| 3 | **New Event Types** | "Carbon Border Tax", "Green Bond Default" | Event classifier falls to "Unclassified" | Medium (needs score bounds) |
| 4 | **New Causal Edges** | "drought → hydropower generation drop" (WA→EU) | `narrative_implied_causation` | High (corrupts cascades if wrong) |
| 5 | **Materiality Refinement** | Data Privacy scores HIGH for Banking but weight is 0.5 | Relevance score divergence over time | Medium (needs 10+ articles) |
| 6 | **Stakeholder Concerns** | "Greenwashing litigation" as new investor concern | `narrative_stakeholder_framing` | Medium (interpretive) |
| 7 | **Framework Updates** | "SEBI new ESG circular Q3 2026" | `regulatory_references` not in ontology | Low (factual) |

## Architecture

### Feedback Loop (Stage 12.5 — post-write, pre-next-article)

```
Article → Pipeline stages 1-12 → Writer (JSON + SQLite)
                                       ↓
                              collect_discoveries()     ← NEW (inline, ~5ms)
                                       ↓
                            DiscoveryBuffer (in-memory)
                                       ↓
                              [batch promoter]           ← NEW (async, every 30min)
                                       ↓
                    confidence filter + dedup SPARQL
                                       ↓
                       data/ontology/discovered.ttl      ← NEW (runtime-learned layer)
                                       ↓
                       graph.insert_triples() → live graph
                                       ↓
                    Next article reads enriched ontology  ← LOOP CLOSED
```

### File Structure

```
engine/ontology/discovery/
  __init__.py
  candidates.py           # DiscoveryCandidate dataclass + buffer
  collector.py            # collect_discoveries() — inline extraction
  promoter.py             # batch_promote() — filter, dedup, insert
  modules/
    entity_discoverer.py       # Category 1
    theme_discoverer.py        # Category 2
    event_discoverer.py        # Category 3
    edge_discoverer.py         # Category 4
    weight_refiner.py          # Category 5
    stakeholder_discoverer.py  # Category 6
    framework_discoverer.py    # Category 7

data/ontology/
  discovered.ttl           # Runtime-learned triples (loaded by graph.py)
  discovery_audit.jsonl    # Append-only audit log
```

### Separation: Authored vs Discovered

- **Authored** (repo, never modified at runtime): `schema.ttl`, `knowledge_base.ttl`, `knowledge_expansion.ttl`, `primitives_*.ttl`
- **Discovered** (runtime, grown by the system): `discovered.ttl` — uses same `snowkap:` namespace, same classes, picked up by existing SPARQL queries automatically

### Confidence Thresholds

| Category | Min Confidence | Min Articles | Auto-Promote? |
|----------|---------------|-------------|---------------|
| Entity (company) | 0.80 | 3 across 2+ sources | Yes |
| Entity (regulator) | 0.70 | 1 from Tier-1 source | Yes |
| Theme (novel) | 0.70 | 5 across 2+ companies | No — always human review |
| Event type | 0.75 | 3 across 2+ sources | Conditional |
| Causal edge | 0.80 | 5 across 3+ sources | No — always human review |
| Weight adjustment | N/A | 10+ articles | No — always human review |
| Stakeholder concern | 0.70 | 5 across 2+ sources | No — always human review |
| Framework/regulation | 0.70 | 1 from Tier-1 | Conditional |

### Growth Governance

- **Max discovered triples:** 10,000 (5,000 authored + 10,000 discovered = 15,000 total, loads in <10s)
- **Max pending candidates:** 500 (overflow drops lowest confidence)
- **Archival:** Triples not referenced in 90 days → `archived` status → excluded from graph after 120 days
- **Dedup:** Jaro-Winkler ≥ 0.90 for entities, Jaccard ≥ 0.5 for event keywords, exact match for edges

### Provenance (every discovered triple)

```turtle
snowkap:disc_tata_power a snowkap:DiscoveredTriple ;
    snowkap:discoveredFrom "article_abc123" ;
    snowkap:discoveredAt "2026-04-14T10:30:00Z"^^xsd:dateTime ;
    snowkap:discoveryConfidence 0.85 ;
    snowkap:discoveryCategory "entity" ;
    snowkap:discoveryStatus "promoted" .
```

## Implementation Phases

### Phase A: Foundation (5 days)
- Add `DiscoveredTriple` class to schema.ttl
- Create `engine/ontology/discovery/` module structure
- Implement `DiscoveryCandidate` dataclass + `DiscoveryBuffer`
- Modify `graph.py:load()` to parse `discovered.ttl`
- Add `graph.persist_discovered()` method
- Wire `collect_discoveries()` into `on_demand.py`
- JSONL audit writer

### Phase B: Entity + Theme Discovery (5 days)
- Entity discoverer with SPARQL existence check + Jaro-Winkler dedup
- Theme discoverer with taxonomy comparison + frequency gating
- Entity auto-promotion (3+ mentions, confidence ≥ 0.80)
- Theme staging (always pending)

### Phase C: Event + Framework Discovery (4 days)
- Event discoverer triggered by "Unclassified" events
- Framework discoverer triggered by unmatched regulatory references
- Conditional auto-promotion for Tier-1 regulatory sources

### Phase D: Edge + Weight + Stakeholder Discovery (5 days)
- Edge discoverer with primitive mapping from narrative causation
- Weight refiner with running average divergence tracking
- Stakeholder concern discoverer
- All always-pending (no auto-promotion)

### Phase E: Governance + Admin UI (4 days)
- Triple count cap enforcement
- 90-day archival scanner (weekly via scheduler)
- `GET /api/discovery/pending` — surfaces candidates for review
- `POST /api/discovery/{id}/approve|reject` — manual curation
- SQLite `discovery_log` table

**Total: ~23 days across 5 phases**

## What Changes Over Time

After 6 months of processing ~100 articles/week:

| Metric | Today | +6 months |
|--------|-------|-----------|
| Entities (companies, regulators) | ~30 | ~130 (+100 discovered) |
| ESG Themes | 21 | ~25 (+4 novel, curated) |
| Event Types | 22 | ~30 (+8 discovered) |
| Causal Edges | 123 | ~140 (+17 candidate, curated) |
| Frameworks/Deadlines | 21 + 5 deadlines | 21 + ~15 deadlines |
| Total Triples | 5,000 | ~8,000 |
| "Unclassified" event rate | ~15-20% | <5% |
| Dead-end entity lookups | ~40% of articles | <10% |

The 97% ontology-driven metric stays the same (pipeline architecture unchanged), but the QUALITY of every ontology query improves because the graph contains more relevant knowledge. The system becomes more accurate over time without any code changes — just ontology growth.

## Critical Files

| File | Change |
|------|--------|
| `data/ontology/schema.ttl` | Add `DiscoveredTriple` class + provenance properties |
| `engine/ontology/graph.py` | Load `discovered.ttl` + `persist_discovered()` |
| `engine/analysis/on_demand.py` | Hook `collect_discoveries()` after write step |
| `engine/ontology/discovery/` | NEW module (7 discoverers + collector + promoter) |
| `data/ontology/discovered.ttl` | NEW runtime file (grows over time) |
| `data/ontology/discovery_audit.jsonl` | NEW append-only audit log |

**Why backend, not frontend:** The PDF needs data from all 3 perspectives simultaneously + computed cascade trace + pipeline methodology. Backend has direct access to the JSON files. Frontend would need 3 API calls and complex layout logic.

### PDF Report Structure (single document, ~4-6 pages)

```
Page 1: Cover + Executive Summary
  - Snowkap logo + "ESG Intelligence Report"
  - Article title, source, date, company name
  - Headline (from deep_insight)
  - Materiality badge (CRITICAL/HIGH/MODERATE/LOW)
  - Financial exposure (computed)
  - Key risk + Top opportunity

Page 2: Three-Perspective Analysis
  ┌─────────────────────────────────────────────┐
  │ CFO VIEW                                     │
  │ Headline: P&L exposure: ₹49.6 Cr...         │
  │ Impact Grid: Financial HIGH | Regulatory HIGH │
  │ What Matters: (2-3 bullets)                  │
  │ Action: (1-2 bullets)                        │
  ├─────────────────────────────────────────────┤
  │ CEO VIEW                                     │
  │ Headline: Strategic angle — ₹500 Cr...       │
  │ Impact Grid: ...                             │
  │ What Matters: (2-3 bullets)                  │
  │ Action: (1-2 bullets)                        │
  ├─────────────────────────────────────────────┤
  │ ESG ANALYST VIEW                             │
  │ Headline: Full detail...                     │
  │ Impact Grid: ...                             │
  │ What Matters: (2-3 bullets)                  │
  │ Action: (1-3 bullets)                        │
  └─────────────────────────────────────────────┘

Page 3: Financial Impact & Risk
  - Financial Timeline (immediate / structural / long-term)
  - Risk Assessment table (category, probability, exposure, score, level)
  - ESG Relevance Scores (6 dimensions with rationale)

Page 4: AI Recommendations
  - Each recommendation: type, title, description, framework, budget, ROI, deadline
  - Priority matrix if available

Page 5: Methodology & Computation Trace
  - "How this analysis was computed"
  - 12-stage pipeline diagram (text-based)
  - Causal primitive cascade trace (e.g., "CL→OX: +₹5.04 Cr (β=0.10)")
  - Ontology coverage: "97% ontology-driven, 3% LLM"
  - Framework alignment (which frameworks triggered and why)

Footer: "Generated by Snowkap ESG Intelligence Engine | {date} | Confidential"
```

### Files to create/modify

| File | Action |
|------|--------|
| `engine/export/pdf_report.py` | **NEW** — PDF generation using reportlab |
| `api/routes/legacy_adapter.py` | ADD `POST /news/{id}/export-pdf` endpoint returning PDF bytes |
| `client/src/lib/api.ts` | ADD `exportPdf(articleId)` method |
| `client/src/components/panels/ArticleDetailSheet.tsx` | ADD "Share as PDF" button in hero card |
| `requirements.txt` | ADD `reportlab>=4.0` |

### API Endpoint

```python
@router.post("/news/{article_id}/export-pdf")
def export_pdf(article_id: str, _: None = Depends(require_auth)):
    """Generate a PDF intelligence report for an article across all perspectives."""
    from engine.export.pdf_report import generate_report_pdf
    
    row = sqlite_index.get_by_id(article_id)
    payload = _load_payload(row.get("json_path"))
    company = get_company(row.get("company_slug", ""))
    
    pdf_bytes = generate_report_pdf(payload, company)
    
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=snowkap-{article_id}.pdf"}
    )
```

### Frontend Button

Inside ArticleDetailSheet hero card (around line 735):
```tsx
<button onClick={() => handleExportPdf(article.id)} title="Share as PDF">
  📄 Share as PDF
</button>
```

Handler downloads the blob:
```tsx
const handleExportPdf = async (id: string) => {
  const resp = await fetch(`/api/news/${id}/export-pdf`, {
    method: "POST",
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `snowkap-intelligence-${id}.pdf`;
  a.click();
  URL.revokeObjectURL(url);
};
```

### Verification

1. Click "Share as PDF" on an enriched article → PDF downloads
2. Open PDF — 4-5 pages with all 3 perspectives, computed figures, methodology
3. ₹ figures in PDF match what's shown in the app
4. Framework sections cited correctly
5. Methodology page shows the cascade trace (β values, hops)

---

## ARCHIVED: Previous plans

| # | Gap | Current State | Impact | Fix |
|---|-----|--------------|--------|-----|
| **1** | **No company financials** | `companies.json` has name/industry/market_cap only — no revenue, opex, capex, energy share, debt | LLM guesses ₹ figures ±50%. Waaree (₹500 Cr revenue) gets same estimates as ICICI (₹50,000 Cr) | Add `primitive_calibration` to each company |
| **2** | **LLM computes instead of engine** | Primitives provide β to LLM as text context; LLM *interprets* "β=0.25" but doesn't *compute* 0.25 × 30% × ₹15,000 Cr | ROI inflated (1500% for routine assurance), margin pressure wrong (8 bps → should be 1 bps) | Build `primitive_engine.py` that COMPUTES deterministically |
| **3** | **Event misclassification** | Substring keyword matching; "India's largest green hydrogen plant" → "Routine Capex" instead of "Transition Announcement" | Score ceiling capped too low; cascade context from wrong primitive | Word-boundary matching + multi-keyword scoring + confidence tiers |
| **4** | **Edge coverage 64%** | 58 of 90+ P→P edges loaded; no sector-specific β | Banking (energy <1% opex) and Power (energy 40% opex) use same β=0.15-0.35 | Complete edges + sector-specific β ranges in TTL |
| **5** | **On-demand data loss** | `on_demand.py` reconstruction drops `triggered_sections`, `esg_risks[]`, `temples_risks[]`, stakeholder structure | Recommendations cite generic framework (GRI:305) not sections (GRI:305-1.a) | Fix reconstruction to preserve all fields |

---

## Level 2: Computed Financial Exposure Engine

### What it does

Instead of passing β values to the LLM as text and hoping it computes correctly, build a deterministic Python engine that:

1. Maps article → event → primary primitive (from ontology)
2. Looks up company-specific β calibration (from `companies.json`)
3. Computes: `ΔTarget = β × ΔSource × base_value` using the correct functional form
4. Traverses order-2 and order-3 cascade paths
5. Applies aggregation rules (additive/max/dominant)
6. Outputs a `CascadeResult` with per-hop breakdown
7. Injects COMPUTED numbers into LLM prompt as hard constraints

### Files to create/modify

**1. `config/companies.json` — add financial calibration per company**

```json
{
  "name": "ICICI Bank",
  "slug": "icici-bank",
  "primitive_calibration": {
    "revenue_cr": 50000,
    "opex_cr": 35000,
    "capex_cr": 5000,
    "energy_share_of_opex": 0.01,
    "labor_share_of_opex": 0.35,
    "freight_intensity": 0.005,
    "water_intensity": 0.001,
    "commodity_exposure": {},
    "debt_to_equity": 8.5,
    "cost_of_capital_pct": 8.5
  }
}
```

For all 7 companies:
| Company | Revenue Cr | Opex Cr | Energy % | Labor % | Key exposure |
|---------|-----------|---------|----------|---------|-------------|
| ICICI Bank | 50,000 | 35,000 | 1% | 35% | regulatory, credit |
| YES Bank | 12,000 | 9,000 | 1% | 30% | regulatory, credit |
| IDFC First Bank | 8,000 | 6,000 | 1% | 32% | regulatory, credit |
| Adani Power | 45,000 | 35,000 | 40% | 8% | energy, coal, climate |
| JSW Energy | 15,000 | 10,000 | 30% | 10% | energy, transition |
| Waaree Energies | 5,000 | 4,000 | 15% | 20% | commodity (polysilicon), supply chain |
| Singularity AMC | 200 | 150 | 0.5% | 60% | regulatory, reputation |

**2. `engine/analysis/primitive_engine.py` — NEW (~400 lines)**

```python
@dataclass
class CascadeHop:
    edge_id: str
    source: str
    target: str
    delta_pct: float          # % change at this hop
    delta_cr: float           # ₹ Cr impact at this hop
    functional_form: str
    beta_used: float          # actual β applied (sector-calibrated)
    lag: str
    confidence: str

@dataclass
class CascadeResult:
    primary_primitive: str
    hops: list[CascadeHop]
    total_exposure_cr: float
    confidence: str           # min across all hops
    computation_trace: str    # human-readable trace for LLM

def compute_cascade(
    event_id: str,
    company: Company,
    delta_source: float | None = None,  # from article's financial quantum
    max_order: int = 2,
) -> CascadeResult | None:
    """Deterministic financial cascade computation."""
```

The engine implements all 6 functional forms:
- `linear`: Δ = β × Δsource
- `log-linear`: Δ = β × ln(1 + Δsource)
- `threshold`: Δ = β × Δsource × 𝟙[Δsource > τ]
- `ratio`: Δ = β × (source/base - 1)
- `step`: Δ = β × 𝟙[source ≥ τ]
- `composite`: sum of sub-functions

Company-specific β calibration: `β_company = β_ontology × company.primitive_calibration[primitive_share]`

Example for ICICI GST (₹50.38 Cr demand):
```
Event: heavy_penalty → Primary: CL (Compliance)
Company: ICICI Bank (revenue=₹50,000 Cr, opex=₹35,000 Cr)

Hop 1: CL→OX (β=0.05-0.25, step, lag=0-2q)
  β_used = 0.10 (midpoint for banking)
  ΔOpex = step(₹50.38 Cr demand) × 0.10 = ₹5.04 Cr
  (fines + legal fees as % of opex base)

Hop 2: CL→RV (β=0.05-0.20, step, lag=1-4q)
  β_used = 0.08 (banking, reputational channel)
  ΔRevenue = -₹50.38 × 0.08 = -₹4.03 Cr (if enforcement action public)

Margin impact: ₹5.04 / ₹50,000 × 10,000 = 1.0 bps ✓

Total exposure: ₹50.38 Cr (direct) + ₹5.04 Cr (opex) + ₹4.03 Cr (revenue risk) = ₹59.45 Cr
Confidence: high (CL→OX is high, CL→RV is medium → min = medium)
```

**3. `engine/analysis/insight_generator.py` — inject computed cascade**

Replace the current cascade context block with hard constraints:
```
COMPUTED FINANCIAL CASCADE (verified, do NOT override):
  Direct exposure: ₹50.38 Cr (article quantum)
  Hop 1: CL→OX: +₹5.04 Cr opex (β=0.10, step, lag=0-2q)
  Hop 2: CL→RV: -₹4.03 Cr revenue (β=0.08, step, lag=1-4q)
  Margin impact: 1.0 bps (₹5.04 Cr / ₹50,000 Cr revenue)
  Total exposure: ₹59.45 Cr (medium confidence)
  
  USE THESE EXACT NUMBERS in your output. financial_exposure = "₹50.38 Cr direct + ₹9 Cr cascade (₹59 Cr total)".
  margin_pressure = "1 bps". Do NOT estimate different figures.
```

**4. Fix event classifier — word boundary matching**

File: `engine/nlp/event_classifier.py`

Change substring matching to word-boundary regex:
```python
# Before (line 58):
if kw.lower() in lowered

# After:
import re
if re.search(r'\b' + re.escape(kw.lower()) + r'\b', lowered)
```

Add multi-keyword scoring with confidence tiers:
```python
# Score each event type by keyword hit count + specificity
# "green hydrogen plant commission" matches:
#   event_transition_announcement: 3 keywords (green, hydrogen, commission) → score 3
#   event_routine_capex: 1 keyword (commission) → score 1
# Winner: transition_announcement (correct)
```

**5. Complete P→P edge coverage**

Add ~32 missing edges to `primitives_edges_p2p.ttl`:
- CX→RV, RV→WF, GE→RG (already in primitives framework but not loaded)
- Sector-specific β sub-ranges as `edgeNotes`:
  ```turtle
  snw:edge_EP_OX snw:edgeNotes "Banking β=0.005-0.02; Power β=0.30-0.50; Renewable β=0.10-0.25" .
  ```

**6. Fix on_demand.py reconstruction**

- Preserve `triggered_sections` in `_FW` constructor (line 387 — already exists but not assigned from JSON)
- Reconstruct `esg_risks[]` and `temples_risks[]` from stored JSON (currently hardcoded to `[]`)
- Preserve stakeholder structure (dict with weights, not flattened string)

---

## Verification: What 10/10 Looks Like

Test matrix: 3 articles × 3 companies × 3 perspectives = 27 verification points

| Article Type | Company | Expected Behavior |
|-------------|---------|------------------|
| **Negative/Governance** (GST demand) | ICICI Bank (Large Cap banking) | ₹ exposure computed from CL→OX β=0.005 × ₹35K opex; margin = 1 bps; ESRS G1 cited |
| **Positive/Environmental** (hydrogen plant) | JSW Energy (Large Cap power) | ₹0 direct; strategic CX→EU→GE cascade; ESRS E1 cited; Adani Power named as competitor |
| **Negative/Climate** (coal emissions report) | Adani Power (Large Cap power) | EP→OX β=0.40 × ₹35K opex = ₹X Cr; GHG threshold τ fires; GRI:305, TCFD cited |

Per perspective:
- **CFO**: sees COMPUTED ₹ figures, margin bps, cost of capital impact — NO guessed ranges
- **CEO**: sees competitive position with named peers, strategic opportunity with specific ₹ target
- **ESG Analyst**: sees specific framework sections (GRI:207-1.a not just GRI:207), compliance deadlines, stakeholder risk

### Regression checks:
1. All existing SPARQL queries still return correct results
2. On-demand enrichment completes in <20 seconds
3. `triggered_sections` non-empty for framework-relevant articles
4. No ESRS E1 cited for non-climate events
5. Margin bps scales correctly with company revenue
6. ROI estimates within 2x of ontology industry benchmarks

---

## Effort Estimate

| Component | Lines | Days |
|-----------|-------|------|
| `primitive_engine.py` (new) | ~400 | 2 |
| `companies.json` financial data | ~100 | 0.5 |
| Event classifier word-boundary + scoring | ~50 | 0.5 |
| Complete P→P edges (32 more) | ~300 | 1 |
| Fix on_demand.py reconstruction | ~40 | 0.5 |
| Insight generator: inject computed cascade | ~60 | 0.5 |
| Testing + verification (3×3×3 matrix) | — | 1 |
| **TOTAL** | ~950 | **6 days** |

### What already exists (fully wired):
- `engine/analysis/on_demand.py` — `enrich_on_demand()` runs stages 10-12 on stored pipeline data
- `POST /api/news/{id}/trigger-analysis` — API endpoint that calls `enrich_on_demand()`
- `ArticleDetailSheet.tsx` — auto-triggers on mount when `!article.deep_insight.headline`, shows spinner, polls for result

### What needs to change:

**1. `engine/main.py` — skip stages 10-12 for SECONDARY at ingestion (lines 109-114)**

Currently ALL articles get deep insight + recs at ingestion ($0.05/article). Change to only run for HOME tier:
```python
if not result.rejected and result.relevance.tier == "HOME":
    insight = generate_deep_insight(result, company)
    ...
else:
    # SECONDARY: write pipeline-only data (stages 1-9), skip 10-12
    written = write_insight(result, None, {}, None)
```

**2. `engine/analysis/on_demand.py` — force re-enrichment for stale articles (line 54)**

Currently checks `if existing_insight.get("headline") and existing_insight.get("core_mechanism")` and returns cached. Add a `force=True` parameter so the API can force re-enrichment of articles processed with old prompts.

**3. `api/routes/legacy_adapter.py` — add `force` query param (line 821)**

Add `?force=true` to `trigger-analysis` endpoint so frontend can force re-analysis:
```python
def news_trigger_analysis(article_id: str, force: bool = False, ...):
    if not force and payload.get("insight", {}).get("headline"):
        return {"status": "cached"}
    result = enrich_on_demand(article_id, company_slug, force=force)
```

**4. Frontend already handles this** — no changes needed. `ArticleDetailSheet.tsx` already:
- Triggers on mount when `!hasAnalysis` (line 562)
- Shows "Generating Intelligence Brief" spinner (line 769)
- Polls every 5 seconds (line 567)
- Renders enriched panels when done (line 827)

**5. Clear stale insight data so on-demand triggers fresh analysis**

The 128 articles processed with old prompts have `insight.headline` set (vague content). The on-demand check `if headline: return cached` will skip them. Options:
- (a) NULL out `insight` field in all existing JSONs → on-demand runs fresh
- (b) Add a `schema_version` check → if version < "2.0" (primitives), re-run
- (c) Use `force=true` from UI

**Recommended: (a)** — write a one-time migration script that nulls out `insight`, `perspectives`, and `recommendations` from all existing JSONs, keeping only `pipeline` and `article` data. Next time user clicks → fresh on-demand analysis with primitive-enriched prompts.

### Files to modify:
| File | Change |
|------|--------|
| `engine/main.py:109` | Skip stages 10-12 for SECONDARY tier |
| `engine/analysis/on_demand.py:27,54` | Add `force` parameter |
| `api/routes/legacy_adapter.py:821` | Add `force` query param |
| New: `scripts/clear_stale_insights.py` | One-time migration to null out old insights |

---

## Part B: Fix Edge Gaps (8 total)

### B1. Add primary primitives to 5 secondary-only events

File: `data/ontology/primitives_schema.ttl`

| Event | Add Primary | Rationale |
|-------|-----------|-----------|
| `event_esg_rating_change` | `IR` | Rating changes affect cost of capital |
| `event_board_change` | `RG` | Governance signal affects regulatory posture |
| `event_climate_disclosure_index` | `RV` | Index inclusion drives investor demand |
| `event_dividend_policy` | `CX` | Capital allocation decision |
| `event_award_recognition` | `RV` | Brand uplift drives revenue |

### B2. Add 2 missing event→primitive mappings

File: `data/ontology/primitives_schema.ttl`

| Event | Primary | Secondary |
|-------|---------|-----------|
| `event_esg_partnership` | `RV` | `CX`, `IR` |
| `event_license_revocation` | `DT` | `OX`, `RV`, `CL` |

### B3. Fix event name mismatch

The event `event_systemic_regulatory_change` in primitives_schema.ttl references a URI that doesn't match the actual definition. Need to verify which name the knowledge_base.ttl uses and align.

### B4. Add fallback guidance when cascade context is empty

File: `engine/analysis/insight_generator.py:244-248`

When `query_cascade_context()` returns empty, append a warning to the prompt so the LLM knows to estimate conservatively rather than silently getting no context.

### B5. Add logging in on_demand.py for "Unclassified" events

File: `engine/analysis/on_demand.py:309-318`

Log a warning when event reconstruction produces "Unclassified" label.

---

## Verification

1. Click an article in the UI → spinner appears → analysis loads in 5-15 seconds
2. Second click → instant (cached)
3. Run `scripts/clear_stale_insights.py` → all articles show spinner on next click
4. Events like `heavy_penalty`, `climate_event`, `cyber_incident` → cascade context present in LLM prompt
5. Events like `esg_rating_change`, `board_change` → now have primary primitive → cascade context populated
6. Check server logs for "cascade_context returned empty" warnings → should only appear for truly unmapped events

---

## TL;DR Verdict (Primitives integration from earlier session)

**YES — integrating Primitives would be transformative.** It would upgrade Snowkap from a *qualitative ESG news analyzer* to a *quantitative causal reasoning engine*. The current ontology tells you WHAT happened and WHICH frameworks apply. The Primitives tell you HOW MUCH it costs, THROUGH WHAT transmission path, WITH WHAT lag, and WHERE the threshold triggers are.

**But**: full integration is a 3-4 week build. I recommend a phased approach — Level 1 (ontology + prompt enrichment, 3-5 days) delivers 70% of the value.

---

## Gap Analysis: What Snowkap Has vs What Primitives Add

| Capability | Current Snowkap Ontology | Primitives Framework | Gap Severity |
|-----------|-------------------------|---------------------|-------------|
| **Causal relationships** | 17 semantic labels ("directOperational", "regulatoryContagion") — qualitative only | 200+ edges with mathematical expressions (ΔTarget = β·f(ΔSource)) | **CRITICAL** |
| **Financial quantification** | LLM guesses "₹50-200 Cr" — hallucination-prone | Computable: β=0.15-0.35 × ΔEnergyPrice × company_energy_share | **CRITICAL** |
| **Multi-order propagation** | 0-4 hop decay factors (1.0, 0.7, 0.4, 0.2, 0.1) — no actual traversal | 4 orders + societal cascades, each with explicit intermediate nodes | **HIGH** |
| **Threshold triggering** | None — all events treated as continuous signals | 25+ canonical τ thresholds (drought PDSI≤-3, cyber CVSS≥7, carbon price ≥€50/tCO₂) | **HIGH** |
| **Leading/lagging indicators** | Generic ("draft regulations", "public consultations") | Per-primitive indicator maps with elasticity ranges and lag windows | **HIGH** |
| **Functional forms** | Not modeled | 6 forms: linear, log-linear, threshold, ratio, step, composite | **MEDIUM** |
| **Aggregation logic** | All impacts treated as additive | 5 rules: additive, weighted_avg, max, multiplicative, dominant | **MEDIUM** |
| **Confidence levels** | Point estimates only | high/medium/low per edge with empirical basis noted | **MEDIUM** |
| **ESG topic coverage** | 21 themes, 10 impact dimensions, 19 frameworks — strong qualitative layer | No ESG-specific content — purely causal/financial | N/A (complementary) |
| **Perspective lenses** | CFO/CEO/ESG Analyst with headline rules, ranking keys — functional | Not modeled — the Primitives are lens-agnostic | N/A (complementary) |

### Key Insight: The Two Ontologies Are Complementary, Not Competing

- **Snowkap ontology** = WHAT (ESG topics, frameworks, risk categories, perspectives) 
- **Primitives framework** = HOW MUCH and THROUGH WHAT PATH (causal edges, elasticities, lags, thresholds)

Together they form a complete intelligence stack: Snowkap classifies the ESG event, Primitives compute the financial cascade.

---

## What Specifically Would Improve

### 1. Financial Exposure Goes From Guessed → Computed

**Today (LLM hallucination):**
```
Article: "Energy prices spike 30% due to coal shortage"
LLM output: financial_exposure = "₹50-200 Cr" (vague range, no basis)
```

**With Primitives:**
```
1. Map article → Primitive: Energy Price (EP), ΔEP = +30%
2. Traverse EP→OX edge: β=0.15-0.35, functional_form=linear, lag=0-3m
3. Company: Adani Power (Power/Energy, energy ~25% of opex)
4. Compute: ΔOpex = 0.25 × 30% = 7.5% opex increase
5. Adani Power FY25 opex ~₹15,000 Cr → ₹1,125 Cr exposure
6. Order-2: EP→CX edge (β=-0.20, direction: -)  → ₹200 Cr capex deferral risk
7. Output: "₹1,125 Cr opex pressure + ₹200 Cr capex deferral. Lag: 0-3 months."
```

**Impact:** CFOs trust computed numbers with traceable methodology. LLM-generated ranges feel like guesswork.

### 2. Causal Chains Become Quantitative Cascades

**Today:**
```json
"causal_chain": {
  "event": "Energy price spike",
  "mechanism": "affects company operations",
  "company_impact": "increased costs",
  "transmission_type": "directOperational"
}
```

**With Primitives (order 1→2→3):**
```
EP(+30%) →[linear, β=0.25, lag=0-3m]→ OX(+7.5%)
  OX(+7.5%) →[log-linear, β=0.20, lag=1-3q]→ RV(-1.5%, price pass-through → demand elasticity)
  OX(+7.5%) →[ratio, β=0.30, lag=1-4q]→ CX(-2.3%, cash flow squeeze → capex deferral)
    CX(-2.3%) →[ratio, β=0.15, lag=4-16q]→ RV(-0.3%, deferred capacity → lost future revenue)
```

**Impact:** The system can trace a 3-hop cascade and explain WHY an energy price shock eventually affects revenue — with specific % and lag windows.

### 3. Threshold Detection Eliminates False Positives

**Today:** Every article gets treated as equally impactful (scaled only by generic relevance score).

**With Primitives:** 
- Energy price +5% → **below τ (25% YoY)** → threshold edges DON'T fire → genuinely low impact → "do nothing" is correct
- Energy price +30% → **above τ** → cascading edges fire → CRITICAL materiality is warranted
- Cyber CVSS 4.0 → **below τ (7.0)** → monitor only
- Cyber CVSS 8.5 → **above τ** → step function fires → immediate compliance + downtime risk

**Impact:** Eliminates the current problem where everything scores LOW/MODERATE and the system says "do nothing" — or where the LLM over-escalates trivial events.

### 4. Perspective Actions Become Cascade-Specific

**Today:** CFO/CEO/Analyst see the same `what_matters` text reshuffled.

**With Primitives, each perspective gets a different CASCADE PATH:**
- **CFO gets the financial cascade:** EP→OX→margin compression→credit spread
- **CEO gets the strategic cascade:** EP→CX→capacity deferral→competitive position loss
- **ESG Analyst gets the compliance cascade:** EP→EU→GE→CL (emissions cap breach)

Each perspective literally sees different nodes from the same causal graph. Not reshuffled text — different structural paths.

### 5. Recommendations Get Quantitative Backing

**Today:** "Implement energy efficiency measures to reduce costs" (generic)

**With Primitives:** "Reduce energy intensity by 10% (current β=0.25 → target β=0.15) to cap opex exposure at ₹675 Cr instead of ₹1,125 Cr. ROI: ₹450 Cr annual saving / ₹200 Cr capex = 225% over 3 years. Payback: 5 months. Threshold: if energy price stays below +25% YoY, no action needed."

---

## Integration Architecture: 3 Levels

### Level 1: Ontology Extension + Prompt Enrichment (3-5 days) → 70% of value

**What:** Port the OWL/Turtle code from §00f directly into new TTL files. Wire SPARQL queries. Pass relevant edges as LLM context.

**Changes:**

1. **`data/ontology/primitives_schema.ttl`** — new file (port from §00f):
   - `snw:Primitive` class (22 instances: OX, RV, CX, EU, GE, WA, WS, WF, HS, CL, SC, DT, CY, EP, FR, LT, IR, FX, RG, XW, CM, LC)
   - `snw:ImpactEdge` superclass with all 11 datatype properties (edgeId, directionSign, functionalForm, operatorExpression, elasticityOrWeight, lagK, aggregationRule, confidenceLevel, edgeNotes, order, sourceType)
   - `snw:CausalEdge` subclass (P→P edges) with `snw:cause` / `snw:effect` object properties
   - `snw:IndicatorEdge` subclass (IND→P edges) with `snw:leadsPrimitive` / `snw:lagsPrimitive` / `snw:proxiesPrimitive`
   - `snw:OutcomeNode` class (16 nodes: F::GrossMargin, F::WACC, F::CreditRating, F::FCF, F::EquityVal, F::InsurancePremium, F::HedgingCost, E::CarbonCost, E::CarbonLiability, E::WaterPermitCost, E::WasteDisposalCost, E::RemediationLiability, O::LaborProductivity, O::CapacityUtil, O::InventoryCarryCost, O::ServiceLevel)
   - `snw:SocietalNode` class (8 nodes: SW01-SW08)
   - Polarity properties: `snw:directlyIncreases`, `snw:directlyDecreases`, `snw:directlyModulates`

2. **`data/ontology/primitives_edges_p2p.ttl`** — new file:
   - All 90+ P→P order-2 edges with full schema (source, target, functionalForm, elasticity range, lag, aggregation, confidence, notes)
   - All P→non-P edges (~80 edges to F/E/O/R outcome nodes)
   - All non-P→non-P cascades (O2:: edges, ~16 core cascades)
   - Feedback arcs (FB:: prefix, 11 loops: R1-R6, B1-B5)

3. **`data/ontology/primitives_indicators.ttl`** — new file:
   - All order-1 indicator→primitive edges from §6 (Batches 1-5, ~200 edges)
   - 37 qualitative indicator definitions (A-AK) with 0-10 anchored scales

4. **`data/ontology/primitives_thresholds.ttl`** — new file:
   - 25 canonical τ threshold categories with default ranges
   - Per-edge threshold overrides where sector-specific

5. **`data/ontology/primitives_order3.ttl`** — new file:
   - Top 50 highest-confidence order-3 chains from the 332 total (focus on high-confidence paths)
   - 19 highest-confidence P4 chains (from §10 summary table)

6. **`engine/ontology/graph.py`** — load 5 new TTL files into the graph singleton

7. **`engine/ontology/intelligence.py`** — 6 new SPARQL queries:
   - `query_primitives_for_event(event_type)` → which primitives does this event affect?
   - `query_p2p_edges(source_primitive)` → all outgoing P→P edges with β, lag, form
   - `query_cascade_path(source_primitive, max_hops=3)` → BFS through causal edge graph
   - `query_threshold(primitive_pair)` → τ value for this edge
   - `query_indicators_for_primitive(primitive)` → leading/lagging/proxy indicator maps
   - `query_feedback_loops(primitive)` → relevant feedback arcs

8. **`engine/analysis/insight_generator.py`** — enrich LLM prompt with primitive context:
   ```
   CAUSAL PRIMITIVES CONTEXT:
   Primary primitive affected: Energy Price (EP)
   Direct edges: EP→OX (β=0.15-0.35, linear, lag=0-3m, aggregation=additive, confidence=high)
   Order-2: EP→OX→RV (composite β=0.04-0.14, log-linear, lag=1-4m)
   Order-2: EP→CX (β=-0.05-0.20, threshold τ=25%YoY, direction=−)
   Threshold: ΔEP ≥ 25% YoY triggers cascading impact
   Feedback loop: EP→OX→GrossMargin→FCF→CreditRating→WACC→CX→EU→GE→RG→EP (R1)
   Company energy share of opex: ~25% (Adani Power, Power/Energy sector)
   USE THESE PARAMETERS to compute financial_exposure. Do not guess.
   ```

9. **`engine/analysis/recommendation_engine.py`** — enrich prompt:
   ```
   PRIMITIVE CONTEXT FOR RECOMMENDATIONS:
   Affected primitive: EP (Energy Price)
   CFO-relevant cascade: EP→OX→GrossMargin (β=0.30-0.60, lag=0-1q)
   Threshold to monitor: ΔEP < 25% YoY → no cascading impact
   Leading indicators to track: wholesale spot price (lag 0-1m), commodity benchmarks (0-1m)
   Actionable levers: reduce β (energy efficiency), hedge EP exposure, diversify energy sources
   ```

**Why this works:** §00f already has materialised OWL/Turtle — we're porting, not inventing. The LLM gets calibrated parameters instead of guessing. No computation engine needed yet.

**Effort:** ~2,000 lines of TTL (mostly ported from §00f) + ~200 lines SPARQL + ~80 lines prompt enrichment

### Level 2: Computed Financial Exposure Engine (1-2 weeks) → 90% of value

**What:** Build `primitive_engine.py` that implements the operator expressions from §5-§6, computes ΔTarget deterministically using the 6 functional forms, applies aggregation rules, and feeds results as hard constraints to the LLM.

**Additional changes over Level 1:**

1. **`engine/analysis/primitive_engine.py`** — new file (~600 lines):
   - `compute_cascade(primitive_id, Δsource, company, max_order=3)` → BFS through SPARQL edge graph
   - Implements all 6 functional forms from the schema:
     - `linear`: f(x) = β·x
     - `log-linear`: f(x) = β·ln(1+x)
     - `threshold`: f(x) = β·x·𝟙[x>τ]
     - `ratio`: f(x) = β·(x/x_base − 1)
     - `step`: f(x) = β·𝟙[x≥τ]
     - `composite`: documented sub-functions
   - Implements all 5 aggregation rules per target node:
     - `additive`: ΔT = Σ(wᵢ·fᵢ(xᵢ))
     - `weighted_avg`: ΔT = Σ(wᵢ·fᵢ(xᵢ)) / Σ(wᵢ)
     - `max`: ΔT = max(wᵢ·fᵢ(xᵢ))
     - `multiplicative`: ΔT = Π(fᵢ(xᵢ)^wᵢ)
     - `dominant`: ΔT = wⱼ·fⱼ(xⱼ)
   - Returns `CascadeResult` with per-hop breakdown, confidence (min across chain), and cumulative lag

2. **`engine/analysis/insight_generator.py`** — replace LLM estimation with computed values:
   ```
   COMPUTED FINANCIAL CASCADE (do NOT override — these are calibrated):
   ΔOpex: +₹1,125 Cr (EP→OX: β=0.25, ΔEP=30%, opex_base=₹15,000 Cr, linear, additive)
   ΔCapex: -₹200 Cr (EP→CX: β=-0.20, threshold τ=25% met, cash flow compression)
   ΔRevenue: -₹225 Cr (EP→OX→RV: composite β=0.10, log-linear, lag=1-3q)
   ΔGrossMargin: -2.1pp (P2::OX→GrossMargin: β=0.30-0.60, lag=0-1q)
   Total exposure: ₹1,550 Cr over 0-6 months
   Confidence: high (all edges empirically grounded)
   Feedback risk: R1 loop (EP→OX→GrossMargin→FCF→CreditRating→WACC→CX) amplifies if sustained
   ```

3. **`config/companies.json`** — add per-company β calibration:
   ```json
   {
     "slug": "adani-power",
     "primitive_calibration": {
       "energy_share_of_opex": 0.25,
       "labor_share_of_opex": 0.15,
       "freight_intensity": 0.08,
       "water_intensity": 0.03,
       "commodity_exposure": { "coal": 0.60, "natural_gas": 0.10 },
       "opex_base_cr": 15000,
       "revenue_base_cr": 45000,
       "capex_base_cr": 8000
     }
   }
   ```

4. **Perspective-specific cascade selection:**
   - CFO view: traverse financial cascades (EP→OX→GrossMargin→FCF→CreditRating)
   - CEO view: traverse strategic cascades (EP→CX→RV, EP→OX→CX→capacity deferred)
   - ESG Analyst: traverse compliance cascades (EP→EU→GE→CL, EP→GE→ESGRating)
   - Each perspective gets DIFFERENT computed numbers from DIFFERENT graph paths

**Effort:** ~600 lines engine + ~200 lines config + ~100 lines perspective routing + testing

### Level 3: Full Causal Graph Engine + Societal Layer (3-4 weeks) → 100% value

**What:** Complete graph with order-4 propagation, feedback loop detection, societal cascades (SW01-SW08), threshold monitoring, and scenario modeling.

**Additional over Level 2:**
- Full 332 order-3 chains + all order-4 chains loaded and traversable
- 11 feedback loop detection (R1-R6 reinforcing, B1-B5 balancing) with loop-break recommendations
- 16 outcome node cascades (F/E/O/R) with O2:: edges
- 8 societal primitives (SW01-SW08) for ESRS double-materiality impact reporting
- Threshold monitoring against live data feeds (energy prices, drought indices, CVSS scores)
- Scenario modeling API: "what if energy price +50%?" → full cascade computation
- Visual causal graph rendering in frontend (D3.js force-directed or similar)
- SASB metric bindings (every SASB metric ID → 1-3 primitives)

---

## My Recommendation

**Start with Level 1 immediately. Plan Level 2 for next sprint.**

### Why Level 1 First:
- 70% of the value for 20% of the effort
- No new computation engine — just better LLM context
- Fully backwards-compatible (existing pipeline unchanged)
- Validates the integration before committing to Level 2

### Why Level 2 Matters:
- Removes LLM hallucination from financial estimates entirely
- Makes the system auditable ("this ₹1,125 Cr comes from EP→OX edge with β=0.25")
- Enables genuine perspective differentiation (CFO sees financial cascade, CEO sees strategic cascade)
- CFOs won't trust LLM-guessed numbers. They WILL trust computed numbers with explicit methodology.

### Files to Create/Modify for Level 1:

| File | Action | Lines | Source |
|------|--------|-------|--------|
| `data/ontology/primitives_schema.ttl` | **NEW** — Primitive + CausalEdge + IndicatorEdge + OutcomeNode + SocietalNode classes | ~250 | §00f OWL code |
| `data/ontology/primitives_edges_p2p.ttl` | **NEW** — 90+ P→P edges + 80 P→non-P edges + 16 O2:: cascades + 11 FB:: loops | ~900 | §7, §7b, §8 |
| `data/ontology/primitives_indicators.ttl` | **NEW** — 200+ IND→P edges (Batches 1-5) + 37 qualitative rubrics | ~700 | §5, §6, §00d |
| `data/ontology/primitives_thresholds.ttl` | **NEW** — 25 canonical τ categories with ranges | ~150 | Canonical τ table |
| `data/ontology/primitives_order3.ttl` | **NEW** — Top 50 high-confidence P3 chains + 19 P4 chains | ~400 | §9, §10 |
| `engine/ontology/graph.py` | Load 5 new TTL files | ~10 | — |
| `engine/ontology/intelligence.py` | ADD 6 SPARQL queries | ~150 | — |
| `engine/analysis/insight_generator.py` | Enrich prompt with primitive cascade context | ~60 | — |
| `engine/analysis/recommendation_engine.py` | Enrich prompt with primitive levers + thresholds | ~40 | — |
| `engine/analysis/perspective_engine.py` | Route different cascade paths per lens | ~30 | — |

**Total: ~2,690 lines across 10 files. 3-5 days effort.**

### Event-to-Primitive Mapping (bridge between Snowkap events → Primitives):

| Snowkap EventType | Primary Primitive | Secondary Primitives |
|-------------------|------------------|---------------------|
| climate_event | XW (extreme weather) | EP, WA, SC, DT |
| heavy_penalty | CL (compliance) | OX, RV, RG |
| criminal_indictment | CL | RV, WF |
| esg_rating_change | — (outcome: R::ESGRating) | RV, CX, IR |
| green_bond | CX (capex) | IR, GE |
| credit_rating | IR | CX, WACC |
| labour_strike | WF (workforce) | OX, DT, HS |
| cyber_incident | CY | DT, CL, SC |
| community_protest | WA or WS | CL, RV, SocialLicence |
| board_change | — (governance) | RV, CL |
| routine_capex | CX | EU, GE |
| systemic_regulatory | RG | CL, CX, OX |
| transition_announcement | GE | CX, EP, ESGRating |
| ngo_report | CL | RV, ESGRating |

### Verification Plan:

1. **Unit test:** Query `query_p2p_edges("EP")` → expect 10+ edges with β, lag, form populated
2. **Unit test:** Query `query_cascade_path("EP", max_hops=2)` → expect EP→OX→RV chain
3. **Integration test:** Reprocess ICICI GST article → `financial_exposure` should cite specific edge (CL→OX, β=0.05-0.25)
4. **Integration test:** Reprocess Adani coal article → cascade through EP→OX→GrossMargin with ₹ figures
5. **Perspective test:** CFO gets EP→OX→GrossMargin→FCF cascade; CEO gets EP→CX→RV cascade; ESG Analyst gets EP→EU→GE→CL cascade
6. **Threshold test:** Article with <25% energy price change → no EP threshold edges fire → lower materiality
7. **Regression test:** All existing SPARQL queries still work; triple count increases by ~2,400

---

---

## Inventory: What's in the Primitives Framework (660KB total)

| Layer | Content | Count | Source |
|-------|---------|-------|--------|
| **Primitives** | Universal + highest-propagation causal nodes | 22 | §2 |
| **Outcome Nodes** | Financial (F::7), Environmental (E::5), Operational (O::6), Reputational (R::5) | 23 | §7 |
| **Societal Nodes** | Community health, food security, worker welfare, displacement, biodiversity, credit access, price burden, energy poverty | 8 | §11 |
| **Order-1 Edges** | Indicator→Primitive (leading/lagging/proxy) with elasticity, lag, form | ~200 | §5-§6 |
| **Order-2 P→P Edges** | Primitive→Primitive with full schema | 90+ | §7-§8 |
| **Order-2 P→non-P** | Primitive→Outcome Node | ~80 | §7 |
| **Order-2 non-P→non-P** | Outcome→Outcome cascades (O2:: prefix) | ~16 | §7b |
| **Feedback Arcs** | Reinforcing (R1-R6) + Balancing (B1-B5) loops | 11 | §7b, §00c |
| **Order-3 Chains** | P→P→P fully enumerated | 332 | §9 |
| **Order-3 Hybrid** | P→P→non-P | ~100+ | §9b |
| **Order-3 Pure Outcome** | P→non-P→non-P | ~80+ | §9c |
| **Order-4 Chains** | P→P→P→P (8 roots: EP,CM,XW,IR,RG,LC,FR,LT + SC,DT,CY,HS) | ~60 | §10 |
| **Order-4 Terminal** | P→P→P→non-P | ~40 | §10b |
| **Thresholds** | Canonical τ categories with default ranges | 25 | §00 |
| **Qualitative Indicators** | 0-10 anchored scoring rubrics (A-AK) | 37 | §00d |
| **OWL/Turtle Code** | Ready-to-port Primitive, CausalEdge, IndicatorEdge classes + instances | 5 batches | §00f |
| **ESRS/GRI/TNFD Alignment** | Societal nodes mapped to disclosure standards | SW01-SW08 | §11 |

**Total unique edge records across all orders: ~1,000+**

---

## ARCHIVED: Phase 14 plan (already implemented, kept for reference)

## Phase A: Bug Fixes (highest impact, lowest risk)

### A1. Empty `core_claim` fallback
**File:** `engine/nlp/extractor.py:222`
**Bug:** `str(parsed.get("narrative_core_claim", "") or "")` preserves empty string from LLM (the `or ""` only catches `None`).
**Fix:** Change line 222 to:
```python
narrative_core_claim=(str(parsed.get("narrative_core_claim", "") or "").strip() or title)[:500],
```
Fallback to article title when LLM returns empty — matches the existing `_default_extraction()` at line 157.

### A2. Theme-based event classification fallback
**File:** `engine/nlp/event_classifier.py:108-118`
**Bug:** When no keywords match, returns "Unclassified" even for articles with clear theme matches (e.g., "Climate Change" article gets no event type).
**Fix (3 parts):**

1. **Ontology** — Add `snowkap:defaultEventForTheme` predicate to `data/ontology/schema.ttl` + 21 triples in `data/ontology/knowledge_depth.ttl` mapping each topic to its most-likely event type
2. **SPARQL** — New `query_default_event_for_theme(theme_label)` in `engine/ontology/intelligence.py`
3. **Classifier** — Add `theme: str = ""` parameter to `classify_event()`. After keyword match fails (line 108), try theme fallback before returning "Unclassified"
4. **Pipeline** — Pass `themes.primary_theme` to `classify_event()` at `engine/analysis/pipeline.py:150`

### A3. Reputational-to-regulatory escalation
**File:** `engine/analysis/relevance_scorer.py:94-111` (`_score_compliance_risk()`)
**Bug:** NGO naming articles (content_type="reputational") get `compliance_risk=0` because scoring only checks explicit regulatory references.
**Fix:** After existing keyword check, add escalation signal block:
```python
if extraction.content_type == "reputational":
    escalation_kw = ("ngo", "greenpeace", "oxfam", "amnesty", "dirty list", "polluter", "boycott", "campaign")
    if any(k in text for k in escalation_kw):
        return 1  # latent compliance risk (1, not 2 — implied not confirmed)
    if extraction.sentiment <= -1:
        return 1
```

### A4. Expand event type keywords
**File:** `data/ontology/knowledge_depth.ttl`
**Issue:** 21 event types have narrow keyword lists. Add ~5-10 keywords per type covering common variations. Data-only change — append to existing `snowkap:eventKeyword` comma-separated lists.
**Examples:**
- `event_ngo_report`: add "climate activist, environmental group, civil society, watchdog, report card, accountability, naming and shaming"
- `event_regulatory_action`: add "notice, demand, penalty order, show cause, adjudication, tribunal"
- `event_climate_event`: add "heatwave, wildfire, flooding, extreme weather, el nino, la nina"

---

## Phase B: Perspective Intelligence (makes the 3 lenses visibly different)

### B1. Distinct headline generation — ALWAYS different per lens
**File:** `engine/analysis/perspective_engine.py:141-156` (`_perspective_headline()`)
**Bug:** CFO/CEO headlines fall through to base headline when `financial_exposure` or `top_opportunity` are "N/A".
**Fix:** Replace function with cascading priority:
- **CFO cascade:** financial_exposure → revenue_at_risk (from `financial_timeline.immediate`) → key_risk → always-different fallback `"P&L signal — {base}"`
- **CEO cascade:** top_opportunity → competitive_position (from `financial_timeline.structural`) → always-different fallback `"Board-level signal — {base}"`
- **ESG Analyst:** base headline unchanged

Key design: **last-resort always prepends a perspective-specific prefix**, guaranteeing visible differentiation.

### B2. Match in legacy adapter
**File:** `api/routes/legacy_adapter.py:136-162` (`_reshape_perspective()`)
Apply same cascading logic. The CFO branch at line 139 and CEO branch at line 148 get the same priority cascade.

### B3. LLM prompt hardening — never return "N/A" for financial fields
**File:** `engine/analysis/insight_generator.py:45-112` (`_SYSTEM_PROMPT`)
**Fix:** Add to CRITICAL RULES section (zero-cost, same LLM call):
```
- decision_summary.financial_exposure: estimate a ₹ range (e.g. "₹50-200 Cr") for ANY business impact article. Only use "N/A" for purely informational macro articles.
- decision_summary.top_opportunity: always identify at least one strategic angle. Reserve "None" only for pure crisis articles.
- financial_timeline.structural.competitive_position: compare to sector peers by name when possible.
```
This ensures the perspective headline cascade in B1 has data to work with.

---

## Phase C: Ontology Deepening (untapped intelligence)

### C1. Populate `triggered_sections` from ontology
**Files:** `engine/ontology/intelligence.py` (new query) + `engine/analysis/framework_matcher.py` (wire in)
**Issue:** `FrameworkMatch.triggered_sections` is always `[]`. Ontology has 30+ framework sections in `knowledge_expansion.ttl`.
**Fix:**
1. New `query_framework_sections(framework_id, topic)` SPARQL — walks `Framework → hasSection → FrameworkSection` and filters by topic keyword match against `sectionTitle`
2. In `framework_matcher.py`, after building each `FrameworkMatch`, populate `triggered_sections`
**Frontend:** `FrameworkAlignmentV2.tsx` already conditionally renders sections — they'll appear automatically.

### C2. Peer comparison via `competessWith`
**Files:** `data/ontology/companies.ttl` + `engine/ontology/intelligence.py` + `engine/analysis/insight_generator.py`
**Issue:** No competitive context in any analysis. Ontology has `competessWith` predicate but no triples.
**Fix:**
1. Add `competessWith` triples to `companies.ttl`:
   - Adani Power ↔ JSW Energy
   - ICICI Bank ↔ YES Bank ↔ IDFC First Bank
   - Waaree Energies ↔ Adani Power, JSW Energy
2. New `query_competitors(company_slug)` SPARQL in intelligence.py
3. Add "Key competitors: X, Y, Z" to insight generator user prompt + instruct LLM to reference peers in `competitive_position`
**Cost:** Zero LLM calls. Adds ~20 tokens to existing prompt.

### C3. Cap tier scaling in relevance scoring
**File:** `engine/analysis/relevance_scorer.py`
**Issue:** `query_cap_tier()` is defined in intelligence.py but never used. A ₹50Cr event hits differently for Large Cap vs Small Cap.
**Fix:** After `query_materiality_weight()`, call `query_cap_tier(market_cap)`:
- Large Cap + high investor_sensitivity: escalate moderate `financial_impact` (1→2)
- Small Cap + high regulatory_scrutiny: escalate moderate `compliance_risk` (1→2)
**Requires:** Adding `company_market_cap` parameter to `score_relevance()`, threading from `pipeline.py`.

### C4. Regulatory penalty precedents
**Files:** `engine/ontology/intelligence.py` + `engine/analysis/insight_generator.py`
**Issue:** `knowledge_expansion.ttl` defines `RegulatoryPenalty` class with `medianFineRange` but no query function.
**Fix:**
1. New `query_penalty_precedents(jurisdiction)` SPARQL
2. When article has regulatory references, inject penalty context into insight generator prompt: "PENALTY PRECEDENTS: SEBI fine range ₹X-Y Cr"
**Cost:** Zero LLM calls. ~50-100 tokens added to regulatory articles only.

---

## Phase D: Recommendation Upgrade

### D1. ROI/payback in recommendation prompt
**File:** `engine/analysis/recommendation_engine.py:94-121` (`_GENERATOR_SYSTEM`)
**Bug:** `roi_percentage` and `payback_months` fields exist in `Recommendation` dataclass but LLM prompt doesn't request them.
**Fix:** Add to JSON schema in `_GENERATOR_SYSTEM`:
```json
"roi_percentage": "<estimated ROI % over 3 years, or null>",
"payback_months": "<months to break even, or null>"
```
Add parsing at lines 181-196 where `Recommendation` is constructed.

### D2. Compliance deadlines in recommendation context
**File:** `engine/analysis/recommendation_engine.py:124-147` (`_build_generator_prompt()`)
**Fix:** Add framework deadlines via `query_compliance_deadlines("India")` so LLM sets deadlines aligned with regulatory timelines.

---

## Phase E: Reprocess + Verify

### E1. Reprocess all 91 articles
Run `python scripts/reprocess_existing.py` — uses existing script that calls `_run_article()` for each cached input.

### E2. Rebuild SQLite index
`python engine/main.py reindex`

### E3. Verification checklist
- [ ] Core claim non-empty for all 91 articles (grep check)
- [ ] Event type != "Unclassified" for ≥80% of articles
- [ ] CFO headline differs from ESG Analyst headline for ALL articles
- [ ] CEO headline differs from both for ALL articles
- [ ] `triggered_sections` non-empty for BRSR/GRI frameworks
- [ ] Peer competitors appear in `competitive_position` field
- [ ] Recommendations have `roi_percentage` for ≥50% of non-do-nothing articles
- [ ] Adani Oxfam article compliance_risk ≥ 1 (was 0)
- [ ] Frontend renders all new data without errors

---

## Files Modified (16 total)

| File | Change Type | Phase |
|------|------------|-------|
| `engine/nlp/extractor.py` | Bug fix (1 line) | A1 |
| `engine/nlp/event_classifier.py` | Add theme fallback (~15 lines) | A2 |
| `engine/analysis/pipeline.py` | Pass theme to classifier (1 line) | A2 |
| `engine/analysis/relevance_scorer.py` | Add escalation + cap tier (~20 lines) | A3, C3 |
| `data/ontology/schema.ttl` | New predicate (2 lines) | A2 |
| `data/ontology/knowledge_depth.ttl` | Theme→event triples + expanded keywords (~50 lines) | A2, A4 |
| `data/ontology/companies.ttl` | competessWith triples (~10 lines) | C2 |
| `engine/ontology/intelligence.py` | 4 new SPARQL queries (~120 lines) | A2, C1, C2, C4 |
| `engine/analysis/perspective_engine.py` | Rewrite headline function (~30 lines) | B1 |
| `api/routes/legacy_adapter.py` | Match headline cascade (~15 lines) | B2 |
| `engine/analysis/insight_generator.py` | Prompt hardening + peer/penalty context (~20 lines) | B3, C2, C4 |
| `engine/analysis/framework_matcher.py` | Wire triggered_sections (~10 lines) | C1 |
| `engine/analysis/recommendation_engine.py` | ROI/payback + deadlines (~25 lines) | D1, D2 |

**Total new/modified lines:** ~650
**Additional LLM calls:** 0 at ingestion; 1-2 per on-demand click (deep insight + recommendations)
**Additional LLM tokens per article:** ~50-150 (context enrichment in existing calls)
**Ontology triples added:** ~75 (21 theme→event + 10 competitor + ~44 expanded keywords)

---

## Phase F: On-Demand Hybrid Intelligence (user clicks article → live analysis)

### Architecture: Pre-process cheap stages at ingestion, run expensive stages on click

**At ingestion time (automatic, no LLM cost for SECONDARY):**
- Stage 1: NLP extraction (gpt-4.1-mini, ~$0.005)
- Stage 2: Theme tagging (gpt-4.1-mini, ~$0.003)
- Stage 3: Event classification (ontology, $0)
- Stage 4: Relevance scoring (ontology, $0)
- Stage 5: Causal chains (ontology, $0)
- Stage 6: Framework matching (ontology, $0)
- Stage 7: Stakeholder mapping (ontology, $0)
- Stage 8: SDG mapping (ontology, $0)
- Stage 9: Risk assessment LITE (ontology, $0) — for SECONDARY only

**On user click (on-demand, only when user opens article):**
- Stage 10: Deep insight generation (gpt-4.1, ~$0.03) — currently only runs for HOME tier
- Stage 11: Perspective transformation (ontology, $0)
- Stage 12: REREACT recommendations (gpt-4.1-mini × 3 agents, ~$0.02)

### Implementation

#### F1. New `engine/analysis/on_demand.py` — orchestrates stages 10-12
```python
def enrich_on_demand(article_id: str, company_slug: str) -> dict:
    """Run deep insight + perspectives + recommendations for a SECONDARY article on demand."""
    # 1. Load existing pipeline result from JSON
    # 2. Skip if already enriched (has insight.headline)
    # 3. Run generate_deep_insight() 
    # 4. Run transform_for_perspective() × 3 lenses
    # 5. Run generate_recommendations()
    # 6. Write enriched JSON back to disk
    # 7. Return the enriched payload
```

#### F2. New API endpoint: `POST /api/news/{article_id}/enrich`
**File:** `api/routes/legacy_adapter.py`
- Called when frontend opens an article that has no `deep_insight`
- Runs `enrich_on_demand()` synchronously (takes 5-15 seconds)
- Returns enriched analysis in legacy article shape
- Subsequent opens are instant (cached to disk)

#### F3. Frontend: loading state in ArticleDetailSheet
**File:** `client/src/components/panels/ArticleDetailSheet.tsx`
- Current behavior: calls `POST /api/news/{id}/trigger-analysis` then polls `GET /api/news/{id}/analysis`
- This already works! The existing `trigger-analysis` endpoint + polling loop at lines 542-598 handles the async flow
- **Change:** Wire `trigger-analysis` to call `enrich_on_demand()` instead of being a no-op
- Add a pulsing skeleton loader for the insight panels while enrichment runs

#### F4. Pipeline change: skip stages 10-12 for SECONDARY at ingestion
**File:** `engine/analysis/pipeline.py` and `engine/main.py`
- In `_run_article()`: after `process_article()`, only call `generate_deep_insight()` + `transform_for_perspective()` + `generate_recommendations()` if tier == HOME
- SECONDARY articles get saved with stages 1-9 only (NLP, themes, event, relevance, chains, frameworks, stakeholders, SDGs, risk_lite)
- **Cost savings:** ~$0.05 per SECONDARY article × 90 articles = ~$4.50 saved per full ingest run

---

## Phase G: Smarter Recommendations (ROI + Peer Benchmarks + Priority Matrix)

### G1. ROI calculation with ontology-sourced benchmarks
**Files:** `engine/analysis/recommendation_engine.py` + `engine/ontology/intelligence.py`

**Current state:** `roi_percentage` and `payback_months` are always None.

**Fix (3 layers):**

1. **LLM prompt** (already covered in D1) — ask LLM to estimate ROI% and payback
2. **Ontology validation** — New `query_industry_roi_benchmarks(industry, action_type)` SPARQL
   - Add triples to `knowledge_expansion.ttl`:
     ```turtle
     snowkap:roi_compliance_banking a snowkap:ROIBenchmark ;
         snowkap:forIndustry "Financials/Banking" ;
         snowkap:forActionType "compliance" ;
         snowkap:typicalROI "15-25%" ;
         snowkap:typicalPayback "12-18 months" .
     ```
   - When LLM returns ROI, cross-check against ontology benchmark. If wildly off (>3× benchmark), clamp to benchmark range + flag.
3. **Post-processing** — In `_post_process()`, if LLM didn't return ROI, derive from:
   ```python
   if not rec.roi_percentage and rec.estimated_budget:
       benchmark = query_industry_roi_benchmarks(company.industry, rec.type)
       if benchmark:
           rec.roi_percentage = benchmark.typical_roi_midpoint
           rec.payback_months = benchmark.typical_payback_midpoint
   ```

### G2. Peer benchmarking in recommendations
**Files:** `engine/analysis/recommendation_engine.py` + `engine/ontology/intelligence.py`

**New `query_peer_actions(topic, industry)` SPARQL:**
- Add triples to `knowledge_expansion.ttl` for notable peer actions:
  ```turtle
  snowkap:peer_action_jsw_netzero a snowkap:PeerAction ;
      snowkap:company "JSW Energy" ;
      snowkap:topic "Climate Change" ;
      snowkap:action "Announced 2030 net-zero target" ;
      snowkap:outcome "15% ESG score uplift, ₹2000 Cr green bond issued" ;
      snowkap:year 2024 .
  ```
- Inject into recommendation generator prompt: "PEER ACTIONS: JSW Energy responded to similar climate pressure by announcing net-zero targets → 15% ESG uplift"
- LLM uses this to generate benchmarked recommendations like "Follow JSW Energy's approach — announce science-based targets to unlock green bond access"

### G3. Action priority matrix
**File:** `engine/analysis/recommendation_engine.py` — new post-processing step

After generating recommendations, compute a 2D priority matrix:
```python
def _build_priority_matrix(recs: list[Recommendation]) -> dict:
    """Compute urgency × impact matrix for visual display."""
    matrix = {"immediate_high": [], "immediate_low": [], "deferred_high": [], "deferred_low": []}
    for rec in recs:
        urgency_bucket = "immediate" if rec.urgency in ("immediate", "short_term") else "deferred"
        impact_bucket = "high" if rec.estimated_impact == "High" else "low"
        matrix[f"{urgency_bucket}_{impact_bucket}"].append({
            "title": rec.title,
            "type": rec.type,
            "roi": rec.roi_percentage,
            "budget": rec.estimated_budget,
        })
    return matrix
```

Add `priority_matrix` to `RecommendationResult` dataclass.

### G4. Perspective-specific recommendation ranking
**File:** `engine/analysis/recommendation_engine.py`

After building recommendations, re-rank them per perspective:
- **CFO view:** Sort by `roi_percentage DESC` (highest ROI first), then by `payback_months ASC`
- **CEO view:** Sort by `estimated_impact DESC` then `urgency` (strategic impact first)
- **ESG Analyst:** Sort by `framework_section` then `urgency` (compliance-first)

Store as `recommendation_rankings: dict[str, list[int]]` mapping lens → ordered recommendation indices.

### G5. Expose in legacy adapter + frontend
**File:** `api/routes/legacy_adapter.py`
- Add `priority_matrix` and `recommendation_rankings` to `build_legacy_article()` output under `rereact_recommendations`

**File:** `client/src/components/panels/UnlockFullAnalysis.tsx` (or new component)
- Render priority matrix as a 2×2 grid: Urgent+High Impact (red), Urgent+Low Impact (amber), Deferred+High Impact (blue), Deferred+Low Impact (gray)
- Show ROI badge on each recommendation card
- Show peer benchmark callout box when peer_action is referenced

---

## Phase H: Change Log File

### H1. Write `CHANGES_PHASE14.md` at project root
Document every change made in this phase with before/after comparisons, file paths, and line numbers. This serves as an audit trail.

---

---

## Phase I: Strategic LLM Intelligence — Where AI Makes It Smarter

Today the pipeline uses LLM in only 3 places: NLP extraction, deep insight, and recommendations. Here's where targeted LLM calls add *intelligence the ontology can't provide*:

### I1. Perspective-Specific Insight Generation (3 LLM calls instead of 1)
**Current:** 1 deep insight call → same JSON reshaped for 3 lenses (just text prefix changes)
**Upgrade:** 3 parallel gpt-4.1-mini calls, each with a perspective-specific system prompt:
- **CFO call:** "You are a CFO's intelligence brief writer. Lead with P&L impact, quantify financial exposure in ₹ Cr, highlight cost-of-capital shifts, rank risks by balance sheet impact. Max 100 words."
- **CEO call:** "You are a CEO's strategy advisor. Lead with competitive positioning, stakeholder sentiment, board-level decisions needed. Reference competitors by name. Max 150 words."
- **ESG Analyst call:** Use current prompt (full detail, no word limit)

Each call produces a DIFFERENT `headline`, `what_matters`, `impact_grid`, and `action` — not just reshuffled text from one response.

**Cost:** +$0.01 per article (2 extra gpt-4.1-mini calls). Only runs on-demand (Phase F).
**Impact:** HUGE — perspectives become genuinely distinct, not cosmetically different.

### I2. Competitive Intelligence Brief (LLM + ontology)
**Current:** No peer comparison at all.
**Upgrade:** After generating deep insight, make 1 additional gpt-4.1-mini call:
```
System: You are a competitive intelligence analyst for {company_name} in the {industry} sector.
User: Given this ESG event: "{headline}", how would competitors {peer_1}, {peer_2} be affected or respond? What has the industry done in similar situations? Be specific with names and ₹ figures. Max 200 words.
```
**Output:** `competitive_brief` field added to insight — rendered in a new "Competitive Context" panel.
**Cost:** +$0.005 per article (1 gpt-4.1-mini call). Only on-demand.

### I3. Recommendation Quality Gate (LLM self-critique)
**Current:** LLM generates 3-5 recommendations → no quality check → some are vague/generic.
**Upgrade:** After generating recommendations, run a **critic agent** (gpt-4.1-mini):
```
System: You are an ESG recommendation quality auditor. Score each recommendation 1-10 on: specificity (names ₹ amounts, dates, roles), actionability (clear next step), ROI clarity (quantified return). Reject any scoring <5 and provide a rewritten version.
User: [recommendations JSON]
```
**Output:** Only recommendations scoring ≥5 survive. Rewritten versions replace weak ones.
**Cost:** +$0.005 per article. Replaces the current validator agent that just checks for hallucination.

### I4. Causal Narrative Generation (LLM explains the graph)
**Current:** Causal chains are structured data: `["directOperational", hops=1, score=0.7, "Adani Power HQ → Adani Power"]`
**Upgrade:** 1 gpt-4.1-mini call to generate a human-readable causal narrative:
```
System: You are an ESG risk transmission analyst. Given these causal chains, write a 3-4 sentence narrative explaining HOW the ESG event transmits through the company's operations, supply chain, or regulatory environment to create financial risk. Be specific.
User: Event: {headline}. Chains: {chains_json}
```
**Output:** `causal_narrative` field — renders above the visual chain diagram. Turns "industrySpillover: Climate Change is material for Power/Energy → impacts Adani Power" into:
> "Adani Power faces direct operational exposure as a coal-heavy generator. The Oxfam naming creates a narrative shift that flows through ESG fund mandates — passive index rebalancing could trigger ~5% share price impact. Separately, climate change is material for the Power/Energy sector, creating regulatory contagion through BRSR and GRI disclosure obligations."

**Cost:** +$0.005 per article. Only on-demand.

### I5. Executive Q&A Pre-Generation (anticipate questions)
**Current:** User must manually chat to ask questions.
**Upgrade:** After generating insight, run 1 gpt-4.1-mini call:
```
System: You are a C-suite ESG advisor. Based on this intelligence brief, generate the 5 questions a CFO, CEO, or Board member would most likely ask, and provide concise answers (2-3 sentences each).
User: {full insight JSON}
```
**Output:** `anticipated_qa` field — renders as an expandable FAQ section in ArticleDetailSheet. Example:
- "What's the worst-case financial impact?" → "If ESG fund AUM exposed to Adani (~$2B) sees 10% outflow, bond spread widens ~50bps, adding ₹150-200 Cr in annual interest costs."
- "Should we issue a public response?" → "Not yet. Monitor for 2 weeks. If picked up by mainstream media or SEBI queries, prepare a statement referencing BRSR disclosures."

**Cost:** +$0.005 per article. Only on-demand.

### I6. Sentiment Trajectory Prediction (LLM + article history)
**Current:** Each article analyzed in isolation.
**Upgrade:** When enriching on-demand, fetch the last 5 articles for the same company from SQLite and pass to LLM:
```
System: You are a sentiment trend analyst. Given these 5 recent ESG articles about {company}, identify: (1) whether ESG sentiment is improving/declining/stable, (2) emerging risk themes, (3) whether this new article represents escalation, continuation, or reversal of the trend.
User: Previous articles: {titles + sentiments + dates}. New article: {current article}
```
**Output:** `sentiment_trajectory` field — shows as a trend indicator in the feed card: ↑ Improving | → Stable | ↓ Declining.
**Cost:** +$0.005 per article. Only on-demand.

### LLM Cost Summary (On-Demand per Article)

| Call | Model | Cost | When |
|------|-------|------|------|
| Existing: NLP extraction | gpt-4.1-mini | $0.005 | Ingestion |
| Existing: Theme tagging | gpt-4.1-mini | $0.003 | Ingestion |
| Existing: Deep insight | gpt-4.1 | $0.03 | On-demand |
| Existing: Recommendations (3 agents) | gpt-4.1-mini | $0.02 | On-demand |
| **New I1:** Perspective-specific insight (×2) | gpt-4.1-mini | $0.01 | On-demand |
| **New I2:** Competitive intelligence brief | gpt-4.1-mini | $0.005 | On-demand |
| **New I3:** Recommendation quality gate | gpt-4.1-mini | $0.005 | On-demand |
| **New I4:** Causal narrative | gpt-4.1-mini | $0.005 | On-demand |
| **New I5:** Executive Q&A | gpt-4.1-mini | $0.005 | On-demand |
| **New I6:** Sentiment trajectory | gpt-4.1-mini | $0.005 | On-demand |
| **TOTAL per click** | | **~$0.09** | |
| **Ingestion only (no click)** | | **~$0.008** | |

**Key principle:** Ingestion stays cheap ($0.008/article). Intelligence is generated when the user actually cares about an article (~$0.09/click). For 91 articles where maybe 10-15 get clicked, total LLM cost is ~$1-1.50 vs $8+ if pre-computing everything.

---

## Updated Execution Order

```
Phase A  (2-3 hrs)  — Bug fixes: core_claim, event labels, relevance scoring, keywords
Phase B  (2-3 hrs)  — Perspective headlines: cascade logic, LLM prompt hardening
Phase C  (3-4 hrs)  — Ontology: framework sections, peers, cap tier, penalties
Phase D  (1-2 hrs)  — Recommendation: ROI prompt, deadline context
Phase F  (3-4 hrs)  — On-demand: hybrid pipeline, enrich endpoint, frontend loading
Phase G  (3-4 hrs)  — Smart recs: ROI benchmarks, peer actions, priority matrix, per-lens ranking
Phase I  (4-5 hrs)  — LLM intelligence: perspective-specific insights, competitive brief, 
                       recommendation critic, causal narrative, executive Q&A, sentiment trajectory
Phase E  (1-2 hrs)  — Reprocess ingestion-time stages + verify
Phase H  (0.5 hrs)  — Write change log
```

## Verification

- [ ] Click SECONDARY article → loading spinner → enriched insight appears in 5-15s
- [ ] Second click is instant (cached)
- [ ] HOME articles still pre-processed at ingestion
- [ ] Recommendations show ROI% and payback_months
- [ ] Priority matrix renders as 2×2 grid
- [ ] CFO sees recommendations sorted by ROI
- [ ] CEO sees recommendations sorted by strategic impact
- [ ] Peer benchmark callout appears for climate/ESG articles
- [ ] Change log file exists at project root
- [ ] CFO insight is genuinely different text from ESG Analyst (not just prefix)
- [ ] Competitive brief mentions peer companies by name
- [ ] Causal narrative is human-readable prose (not just structured data)
- [ ] Executive Q&A has 5 pre-generated questions with answers
- [ ] Sentiment trajectory shows ↑/→/↓ indicator
- [ ] Recommendation quality: no vague recommendations survive critic gate
