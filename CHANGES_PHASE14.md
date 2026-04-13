# Phase 14: Intelligence Upgrade — Change Log

**Date:** 2026-04-12
**Scope:** Fix systemic analysis issues + make intelligence smarter via ontology + LLM

---

## Summary

- **7 systemic bugs fixed** (empty core claims, identical headlines, missing event labels, underscored relevance)
- **6 new ontology intelligence sources** activated (framework sections, peer comparison, cap tier, penalties, ROI benchmarks, peer actions)
- **On-demand hybrid architecture** — cheap ingestion ($0.008/article) + rich on-click enrichment ($0.09/article)
- **6 new LLM intelligence layers** — competitive brief, causal narrative, executive Q&A, sentiment trajectory
- **Smarter recommendations** — ROI/payback, peer benchmarks, priority matrix, perspective-specific ranking
- **Ontology expanded** from 2985 to 3080+ triples

---

## Phase A: Bug Fixes

### A1. Empty core_claim fallback
- **File:** `engine/nlp/extractor.py:222`
- **Before:** `str(parsed.get("narrative_core_claim", "") or "")[:500]` — preserved empty string from LLM
- **After:** `(str(parsed.get("narrative_core_claim", "") or "").strip() or title)[:500]` — falls back to article title

### A2. Theme-based event classification fallback
- **Files:** `engine/nlp/event_classifier.py`, `engine/ontology/intelligence.py`, `engine/analysis/pipeline.py`, `data/ontology/schema.ttl`, `data/ontology/knowledge_depth.ttl`
- **Before:** Articles with no keyword matches got "Unclassified" event type
- **After:** Falls back to `defaultEventForTheme` ontology lookup (21 triples mapping each ESG topic to its most-likely event type)
- **New predicate:** `snowkap:defaultEventForTheme` (ObjectProperty, ESGTopic → EventType)
- **New SPARQL query:** `query_default_event_for_theme(theme_label)`

### A3. Reputational-to-regulatory escalation
- **File:** `engine/analysis/relevance_scorer.py:94-111`
- **Before:** NGO naming articles got `compliance_risk=0`
- **After:** Reputational articles with NGO/activist keywords or negative sentiment get `compliance_risk=1` (latent escalation)

### A4. Expanded event type keywords
- **Files:** `data/ontology/knowledge_base.ttl`, `data/ontology/knowledge_depth.ttl`
- **Before:** ~5-8 keywords per event type
- **After:** ~10-15 keywords per event type (added demand, penalty order, show cause, tribunal, extreme weather, etc.)

---

## Phase B: Perspective Intelligence

### B1. Distinct headline generation
- **Files:** `engine/analysis/perspective_engine.py:141-156`, `api/routes/legacy_adapter.py:136-162`
- **Before:** CFO/CEO headlines fell through to base headline when `financial_exposure` or `top_opportunity` were "N/A"
- **After:** Cascading priority with always-different last-resort prefix:
  - CFO: financial_exposure → revenue_at_risk → key_risk → "P&L signal — {base}"
  - CEO: top_opportunity → competitive_position → "Board-level signal — {base}"
  - ESG Analyst: base headline unchanged

### B3. LLM prompt hardening
- **File:** `engine/analysis/insight_generator.py:106-120`
- Added 6 "PERSPECTIVE-AWARE OUTPUT RULES" to system prompt ensuring `financial_exposure`, `top_opportunity`, `key_risk`, `competitive_position`, `revenue_at_risk` are never "N/A" when business impact exists

---

## Phase C: Ontology Deepening

### C1. Framework triggered_sections
- **Files:** `engine/ontology/intelligence.py` (new query), `engine/analysis/framework_matcher.py`
- **New SPARQL:** `query_framework_sections(framework_id, topic)` — walks Framework → hasSection → FrameworkSection
- **Before:** `triggered_sections` always `[]`
- **After:** Populated with relevant section codes based on topic keyword matching

### C2. Peer comparison
- **Files:** `data/ontology/companies.ttl`, `engine/ontology/intelligence.py`, `engine/analysis/insight_generator.py`
- **New triples:** 10 `competessWith` relationships (Adani↔JSW, ICICI↔YES↔IDFC, Waaree↔Adani,JSW)
- **New SPARQL:** `query_competitors(company_slug)`
- **Insight generator:** Adds "Key competitors: X, Y, Z" to LLM context

### C4. Regulatory penalty precedents
- **Files:** `engine/ontology/intelligence.py`, `engine/analysis/insight_generator.py`
- **New SPARQL:** `query_penalty_precedents(jurisdiction)`
- **Insight generator:** When article has regulatory references, injects penalty precedent context

---

## Phase D: Recommendation Upgrade

### D1. ROI/payback in recommendations
- **File:** `engine/analysis/recommendation_engine.py`
- **Before:** `roi_percentage` and `payback_months` always None
- **After:** LLM prompt requests these fields + parser extracts them
- **New field:** `peer_benchmark` on Recommendation dataclass

### D2. Compliance deadlines + peer actions in prompt context
- Recommendation generator now receives: PEER ACTIONS, ROI BENCHMARKS, REGULATORY DEADLINES from ontology

---

## Phase F: On-Demand Hybrid Intelligence

### F1. New module: `engine/analysis/on_demand.py`
- `enrich_on_demand(article_id, company_slug)` — runs stages 10-12 + Phase I intelligence layers
- Cached to disk — first click takes 5-15s, subsequent clicks instant

### F2. Trigger-analysis endpoint upgraded
- **File:** `api/routes/legacy_adapter.py`
- `POST /api/news/{id}/trigger-analysis` now calls `enrich_on_demand()` instead of returning "cached"
- Returns `{"status": "done"}` when enrichment completes

---

## Phase G: Smarter Recommendations

### G3. Priority matrix
- 2x2 urgency × impact matrix: `immediate_high`, `immediate_low`, `deferred_high`, `deferred_low`
- Added to `RecommendationResult.priority_matrix`

### G4. Perspective-specific ranking
- CFO: sorted by ROI DESC, payback ASC
- CEO: sorted by impact DESC, urgency ASC
- ESG Analyst: sorted by compliance-first, urgency ASC
- Added to `RecommendationResult.recommendation_rankings`

---

## Phase I: Strategic LLM Intelligence (On-Demand)

All 4 layers run inside `enrich_on_demand()` — zero cost at ingestion, ~$0.035 per click.

### I2. Competitive intelligence brief
- Asks gpt-4.1-mini how competitors would respond to the ESG event
- Output: `intelligence.competitive_brief`

### I4. Causal narrative
- Converts structured causal chain data into human-readable 3-4 sentence prose
- Output: `intelligence.causal_narrative`

### I5. Executive Q&A pre-generation
- Generates 5 anticipated CFO/CEO/Board questions with concise answers
- Output: `intelligence.anticipated_qa`

### I6. Sentiment trajectory
- Analyzes sentiment trend across last 5 articles for the same company
- Output: `intelligence.sentiment_trajectory` (direction: improving/declining/stable)

---

## Ontology Changes

| File | Triples Added | Purpose |
|------|--------------|---------|
| `schema.ttl` | 1 | `defaultEventForTheme` predicate |
| `knowledge_depth.ttl` | 21 | Theme → default event type mappings |
| `knowledge_depth.ttl` | ~30 | Expanded event keywords |
| `knowledge_base.ttl` | ~40 | Expanded event keywords |
| `companies.ttl` | 10 | `competessWith` relationships |
| `knowledge_expansion.ttl` | 5 | Peer action examples |
| `knowledge_expansion.ttl` | 6 | ROI benchmarks by industry |

**Total triples:** 2985 → 3080+

---

## Files Modified

| File | Lines Changed | Phase |
|------|--------------|-------|
| `engine/nlp/extractor.py` | 1 | A1 |
| `engine/nlp/event_classifier.py` | +20 | A2 |
| `engine/analysis/pipeline.py` | 1 | A2 |
| `engine/analysis/relevance_scorer.py` | +12 | A3 |
| `engine/analysis/perspective_engine.py` | +35 | B1 |
| `engine/analysis/insight_generator.py` | +25 | B3, C2, C4 |
| `engine/analysis/framework_matcher.py` | +8 | C1 |
| `engine/analysis/recommendation_engine.py` | +80 | D1, D2, G3, G4 |
| `engine/analysis/on_demand.py` | +300 (new) | F1, I2-I6 |
| `engine/ontology/intelligence.py` | +180 | A2, C1, C2, C4, G1 |
| `api/routes/legacy_adapter.py` | +30 | B2, F2 |
| `data/ontology/schema.ttl` | +3 | A2 |
| `data/ontology/knowledge_depth.ttl` | +30 | A2, A4 |
| `data/ontology/knowledge_base.ttl` | +30 | A4 |
| `data/ontology/companies.ttl` | +10 | C2 |
| `data/ontology/knowledge_expansion.ttl` | +55 | G1, G2 |
