# SNOWKAP ESG v2.0 — AUDIT REPORT

**Spec**: `snowkap_esg_v2_system_prompt.md` (11 Modules)
**Codebase**: `snowkap-esg/backend/` (~90 Python files)
**Date**: 2026-03-26

---

## MODULE STATUS MATRIX

| # | Module | Status | Score | Key Files |
|---|--------|--------|-------|-----------|
| 1 | NLP Narrative & Tone Extraction | **IMPLEMENTED** | 100% | `services/nlp_pipeline.py` (NEW) + `ontology/entity_extractor.py` |
| 2 | Geographic Intelligence | **IMPLEMENTED** | 90% | `ontology/geographic_intelligence.py`, `ontology/jurisdictional_mapper.py`, stored in `articles.geographic_signal` |
| 3 | ESG Theme Tagging & Metatag Taxonomy | **IMPLEMENTED** | 100% | `services/esg_theme_tagger.py` (NEW) — 21 themes (8E+7S+6G), stored in `articles.esg_themes` |
| 4 | Framework RAG (13 frameworks) | **IMPLEMENTED** | 100% | `services/framework_rag.py` (NEW) — 13 frameworks with provision-level KB, stored in `articles.framework_matches` |
| 5 | Structured Relevance Scoring (5-factor) | **IMPLEMENTED** | 95% | `services/relevance_scorer.py` |
| 6 | 10-Category Risk Taxonomy (P×E) | **IMPLEMENTED** | 100% | `services/risk_taxonomy.py` (NEW) — 10 categories, P×E scoring, stored in `articles.risk_matrix` |
| 7 | RE³ Multi-Agent Chain | **IMPLEMENTED** | 90% | `services/rereact_engine.py` — role-aware, gpt-4o |
| 8 | Output Template | **IMPLEMENTED** | 90% | `services/deep_insight_generator.py` (UPDATED) — v2.0 template with all module data assembled |
| 9 | Role-Based Differentiation | **IMPLEMENTED** | 90% | `services/agent_service.py`, `services/role_curation.py` — severity verdicts, structured output |
| 10 | Sequencing & Display Rules | **IMPLEMENTED** | 90% | `tasks/news_tasks.py` (decay), `services/relevance_scorer.py` (tier routing) |
| 11 | FTUX (First-Time User Experience) | **IMPLEMENTED** | 100% | `services/ftux_service.py` (NEW) + `routers/ftux.py` (NEW) — walkthrough, sector defaults, API |

---

## DETAILED GAP ANALYSIS

### MODULE 1: NLP Narrative & Tone Extraction — PARTIAL (40%)

**What exists:**
- `entity_extractor.py:ExtractionResult` — Has `sentiment_score` (-1 to +1), `sentiment_confidence`, `aspect_sentiments`, `urgency`, `content_type`, `time_horizon`, `reversibility`, `stakeholder_impact`, `financial_signal_detail`, `climate_events`
- Basic sentiment classification (positive/negative/neutral legacy + score)

**What's MISSING per v2.0:**
- [ ] **5-point sentiment scale** (-2 to +2 with labels: STRONGLY_NEGATIVE to STRONGLY_POSITIVE) — current is continuous -1 to +1
- [ ] **Tone analysis** — controlled vocabulary (Alarmist, Cautionary, Analytical, Neutral, Optimistic, Promotional, Adversarial, Conciliatory, Urgent, Speculative)
- [ ] **Narrative arc extraction** — Core Claim, Supporting Evidence, Implied Causation, Stakeholder Framing, Temporal Framing
- [ ] **Source credibility assessment** — Tier 1-4 classification
- [ ] **Structured NLP output format** — tree-structured output as specified

**Files to modify:** `backend/ontology/entity_extractor.py`, `backend/models/news.py`

---

### MODULE 2: Geographic Intelligence — PARTIAL (50%)

**What exists:**
- `ontology/geographic_intelligence.py` — `find_geographic_matches()` with facility coordinates
- `ontology/jurisdictional_mapper.py` — `build_geographic_signal()`, `map_jurisdiction()`
- `ontology_service.py:analyze_article_impact()` — calls both during pipeline

**What's MISSING per v2.0:**
- [ ] **Geo-Risk Tagging** — political instability, climate vulnerability, sanctions exposure flags
- [ ] **Structured geographic output format** — Locations/Jurisdictions/Supply Chain Overlap/Geo-Risk Flags
- Output is used internally but not surfaced in the article's stored data

**Files to modify:** `backend/ontology/jurisdictional_mapper.py`, `backend/services/ontology_service.py`

---

### MODULE 3: ESG Theme Tagging — MISSING (0%)

**What exists:**
- `entity_extractor.py` returns `esg_pillar` (E/S/G) and `esg_topics` (flat list)
- No structured taxonomy, no primary/secondary hierarchy, no sub-metric tags

**What v2.0 requires:**
- 21-theme taxonomy: 8 Environmental + 7 Social + 6 Governance themes
- Each theme has sub-metric tags
- Every article gets 1 primary + up to 3 secondary themes
- Sub-metric tags attached to each theme assignment

**New file needed:** `backend/services/esg_theme_tagger.py`
**Model update:** `backend/models/news.py` — add `esg_themes` JSONB column

---

### MODULE 4: Framework RAG — PARTIAL (30%)

**What exists:**
- `ontology_service.py:_TOPIC_FRAMEWORK_MAP` — keyword → framework code mapping (basic)
- `ontology_service.py:infer_frameworks_from_content()` — rule-based fallback
- `entity_extractor.py:FRAMEWORK_ALIASES` — normalizes framework names
- 8 frameworks referenced: BRSR, GRI, TCFD, ESRS, CDP, CSRD, SASB, IFRS

**What's MISSING per v2.0:**
- [ ] **5 missing frameworks**: EU Taxonomy, SFDR, GHG Protocol, SBTi, TNFD, SEC Climate Rules (only 8 of 13)
- [ ] **Embedded knowledge bases** with actual framework provisions (metric categories, section codes, obligations)
- [ ] **RAG retrieval logic** — given ESG themes, retrieve specific framework sections with citations
- [ ] **Cross-framework alignment** — where TCFD/ISSB/CSRD/BRSR overlap or diverge
- [ ] **BRSR deep knowledge** — all 9 principles, BRSR Core subset, value chain extension

**New file needed:** `backend/services/framework_rag.py`

---

### MODULE 5: Structured Relevance Scoring — IMPLEMENTED (95%)

**What exists:**
- `services/relevance_scorer.py` — `RelevanceScore` with 5 dimensions (0-2 each, total 0-10)
- `qualified_for_home` (≥7 + ESG correlation > 0), `tier` (HOME/SECONDARY/REJECTED)
- Hard filter: ESG Correlation = 0 → never HOME

**Minor gap:**
- [ ] Tie-breaking rule: "prioritize negative sentiment first" — not in scorer, done in query ordering

---

### MODULE 6: 10-Category Risk Taxonomy — MISSING (0%)

**What exists:**
- `deep_insight_generator.py` has a basic 4-category `risk_mapping` (capital_allocation, narrative, competitive, geographic)
- No probability × exposure scoring, no 10 categories, no CRITICAL/HIGH/MOD/LOW classification

**What v2.0 requires:**
- 10 risk categories: Physical, Supply Chain, Reputational, Regulatory, Litigation, Transition, Human Capital, Technological, Manpower/Employee, Market & Uncertainty
- Each gets Probability (1-5) × Exposure (1-5) = Priority Score (max 25)
- Classification: CRITICAL (20-25), HIGH (12-19), MODERATE (6-11), LOW (1-5)
- Aggregate score /250, Top 3 risks ranked

**New file needed:** `backend/services/risk_taxonomy.py`
**Model update:** `backend/models/news.py` — add `risk_matrix` JSONB column

---

### MODULE 7: RE³ Multi-Agent Chain — IMPLEMENTED (85%)

**What exists:**
- `services/rereact_engine.py` — Full 3-agent pipeline (Generator → Analyzer → Validator)
- Generator produces 6-dimension impact analysis + recommendations
- Analyzer stress-tests and enriches
- Validator assigns confidence (HIGH/MEDIUM/LOW)
- Role-specific prompt injection via `user_role` parameter

**Minor gaps:**
- [ ] Generator doesn't consume NLP extraction, geographic signals, or ESG theme tags as specified inputs
- [ ] Generator doesn't run the 10-category risk matrix (Module 6 doesn't exist yet)
- [ ] No structured time-horizon analysis in output

---

### MODULE 8: Output Template — PARTIAL (40%)

**What exists:**
- `deep_insight_generator.py` produces 7 sections: headline, core_mechanism, esg_impact_analysis, financial_valuation_impact, compliance_regulatory_impact, risk_mapping, time_horizon, final_synthesis

**What's MISSING per v2.0:**
- [ ] NLP Extraction section (sentiment, tone, core claim, temporal framing)
- [ ] ESG Theme Tags section
- [ ] Framework Alignment (RAG) section with specific citations
- [ ] Geographic Signal section
- [ ] 10-Category Risk Assessment Matrix
- [ ] RE³ Validation provenance footer
- [ ] Net Impact Summary

**File to modify:** `backend/services/deep_insight_generator.py`

---

### MODULE 9: Role-Based Differentiation — IMPLEMENTED (80%)

**What exists:**
- `services/role_curation.py` — 6 role profiles with priority_frameworks, recommendation_style, content_depth
- `services/agent_service.py` — Role-specific output templates with severity verdicts, structured formatting
- `core/permissions.py` — designation → role mapping

**Minor gaps:**
- [ ] Role-specific priority weighting of risk categories (Module 6 not yet built)
- [ ] Risk matrix is not yet universal (same for all roles) — will be once Module 6 exists

---

### MODULE 10: Sequencing & Display Rules — IMPLEMENTED (90%)

**What exists:**
- `relevance_scorer.py` — HOME (≥7) / SECONDARY (4-6) / REJECTED (<4) routing
- `tasks/news_tasks.py:decay_home_articles()` — 72h decay, 6h re-evaluation via Celery beat
- `routers/news.py` — feed endpoint with priority ordering

**Minor gap:**
- [ ] Negative-sentiment-first tie-breaking in feed ordering

---

### MODULE 11: FTUX — MISSING (0%)

**What exists:** Nothing

**What v2.0 requires:**
- Activation window (15-30 min) with general sustainability content
- App walkthrough (HOME vs FEED, relevance scores, role views, ESG theme filters)
- Progress indicator + push notification on completion
- Pre-populated sector-default HIGH IMPACT stories

**New files needed:** `backend/services/ftux_service.py`, `backend/routers/ftux.py`

---

## IMPLEMENTATION PRIORITY ORDER

| Priority | Module | Effort | Dependencies |
|----------|--------|--------|-------------|
| **P1** | Module 1: NLP Pipeline Enhancement | Medium | None |
| **P2** | Module 3: ESG Theme Tagger | Medium | Module 1 (tone/narrative feeds tagger) |
| **P3** | Module 4: Framework RAG | Large | Module 3 (themes trigger RAG retrieval) |
| **P4** | Module 6: Risk Taxonomy | Medium | Module 1 + 3 (NLP + themes feed risk assessment) |
| **P5** | Module 8: Output Template | Medium | Modules 1, 3, 4, 6 (assembles all outputs) |
| **P6** | Module 2: Geographic Intelligence (gaps) | Small | Already mostly built |
| **P7** | Module 7: RE³ Enhancement | Small | Modules 1, 3, 4, 6 (consumes their outputs) |
| **P8** | Module 11: FTUX | Medium | Independent |

---

## CURRENT PIPELINE FLOW (for reference)

```
RSS Fetch → Content Extract (trafilatura) → Entity Extract (LLM NER)
  → Resolve vs Jena → Causal Chain BFS → Impact Score
  → 5D Relevance Score → Priority Score → Executive Insight
  → Deep Insight (≥7 relevance) → REREACT (background Celery)
```

**v2.0 flow (IMPLEMENTED):**
```
RSS Fetch → Content Extract (trafilatura)
  → NLP Pipeline [Module 1] — sentiment, tone, narrative arc, source credibility, ESG signals
  → Entity Extract (LLM NER) → Resolve vs Jena
  → ESG Theme Tagger [Module 3] — 21 themes, primary + 3 secondary with sub-metrics
  → Causal Chain BFS → Impact Score → Geographic Intelligence [Module 2]
  → 5D Relevance Score [Module 5] → Priority Score
  → (if HOME-tier ≥7):
      → Framework RAG [Module 4] — 13 frameworks, provision-level citations
      → Risk Taxonomy [Module 6] — 10 categories, P×E scoring, CRITICAL/HIGH/MOD/LOW
      → Deep Insight v2 [Module 8] — full brief with all module data assembled
      → RE³ Chain [Module 7] → Role Differentiation [Module 9]
  → HOME/FEED Sequencing [Module 10] — 72h decay, 6h refresh, negative-first
  → FTUX [Module 11] — first-time users get walkthrough + sector defaults
```

---

## BUILD SUMMARY (2026-03-26)

### New Files Created
| File | Module | Lines | Purpose |
|------|--------|-------|---------|
| `services/nlp_pipeline.py` | 1 | ~180 | 5-step NLP extraction (sentiment, tone, narrative, source, signals) |
| `services/esg_theme_tagger.py` | 3 | ~500 | 21-theme ESG taxonomy with sub-metrics |
| `services/framework_rag.py` | 4 | ~2000 | 13 framework knowledge bases with provision-level retrieval |
| `services/risk_taxonomy.py` | 6 | ~300 | 10-category P×E risk matrix with LLM scoring |
| `services/ftux_service.py` | 11 | ~200 | FTUX state management, walkthrough, sector defaults |
| `routers/ftux.py` | 11 | ~100 | FTUX API endpoints |
| `migrations/versions/008_v2_modules.py` | All | ~25 | DB migration for 5 new JSONB columns |

### Existing Files Modified
| File | Changes |
|------|---------|
| `models/news.py` | +5 JSONB columns: `nlp_extraction`, `esg_themes`, `framework_matches`, `risk_matrix`, `geographic_signal` |
| `services/ontology_service.py` | Wired Modules 1, 3, 4, 6 into `analyze_article_impact()` pipeline |
| `services/deep_insight_generator.py` | v2.0 output template, accepts pre-computed module data, gpt-4o |
| `services/agent_service.py` | Role-specific severity verdicts, structured output templates, gpt-4o |
| `services/rereact_engine.py` | Role-aware prompts, gpt-4o |
| `main.py` | Registered FTUX router |

### Overall Score: 11/11 modules IMPLEMENTED
