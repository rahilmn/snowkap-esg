# ESG SME Audit — Gaps & Issues Identified

**Date**: 2026-03-27
**Reviewer**: Senior ESG Advisory Practice
**Scope**: 7 companies, 72 articles, 12-stage intelligence pipeline
**Overall Score**: 7.4/10 — Strong foundation, needs calibration before client deployment

---

## GAP 1: Positive News Systematically Underscored

**Severity**: HIGH
**Affected**: All 7 companies

**Finding**: The priority scoring formula has an explicit downside bias:
```
sentiment_severity = max(0, -sentiment_score) × 25
```
Positive articles (sentiment > 0) get **0 sentiment points**. A breakthrough green bond issuance and a routine board appointment both score 0 on sentiment.

**Evidence**:
- JSW Energy ₹3,000 Cr green energy raise → Priority 65 (MEDIUM) despite being a material positive event
- SBI green finance strategy → Priority 65 (MEDIUM) despite competitive significance
- Bank of Baroda ₹10,000 Cr green bond → Priority 65 (MEDIUM) despite ₹10,000 Cr financial signal

**Impact**: Competitive intelligence (a rival's green bond, a regulation that benefits you) is deprioritized. CXOs miss upside opportunities.

**Recommendation**: Add a positive materiality component:
```
positive_opportunity = max(0, sentiment_score) × 10
```
Lower weight than negative (10 vs 25) to preserve downside bias, but non-zero so positive material events surface.

**File**: `backend/services/priority_engine.py` line 63

---

## GAP 2: Theme Homogeneity in Banking Sector

**Severity**: HIGH
**Affected**: ICICI Bank, YES Bank, IDFC First Bank

**Finding**: 5 out of 6 banking articles are tagged as **"Transparency & Disclosure (Governance)"** as primary theme. The industry-context fix overcorrected — it now routes everything through a governance lens for financial companies.

**Evidence**:
| Article | Primary Theme | Should Be |
|---------|--------------|-----------|
| RBI Climate Disclosure Pause | Transparency & Disclosure | **Risk Management** or **Climate Adaptation** |
| SBI Green Finance Strategy | Transparency & Disclosure | **Energy** (competitor's energy strategy) |
| Bank of Baroda Green Bond | Transparency & Disclosure | **Energy** (green bond for green projects) |
| IDFC ₹590 Cr Fraud | Ethics & Compliance | Ethics & Compliance (correct) |
| JSW ₹3,000 Cr Green Raise | Transparency & Disclosure | **Energy** (capital raise for green energy) |

**Impact**: ESG theme filters become useless for banking users — everything clusters under one theme. Users cannot filter by Environmental vs Governance topics.

**Recommendation**: Refine the banking-sector prompt instruction:
> "For financial sector companies, distinguish between: (a) the company's own disclosure/governance → Governance themes, (b) environmental exposure of the subject matter → Environmental themes, (c) workforce/community impacts → Social themes. A green bond issuance for renewable energy projects should be tagged as Energy (Environmental), not Transparency & Disclosure."

**File**: `backend/services/esg_theme_tagger.py` — `_USER_PROMPT_TEMPLATE`

---

## GAP 3: Source Credibility Undervalues Indian Financial Media

**Severity**: MEDIUM
**Affected**: All companies

**Finding**: All Whalesbook articles are rated Tier 3 (Secondary). However, articles from **Moneycontrol** (India's largest financial news platform) are also rated Tier 3. Moneycontrol should be Tier 2 alongside Bloomberg and Reuters.

**Evidence**:
| Source | Current Tier | Should Be |
|--------|-------------|-----------|
| Whalesbook | Tier 3 | Tier 3 (correct) |
| Moneycontrol | Tier 3 | **Tier 2** |
| Devdiscourse | Tier 3 | Tier 3 (correct) |
| Economic Times | Tier 2 | Tier 2 (correct, already in list) |
| .gov source (Adani bribery) | Tier 1 | Tier 1 (correct) |

**Impact**: Institutional clients will question credibility differentiation when mainstream Indian financial media is equated with blogs.

**Recommendation**: Add to `TIER_2_SOURCES` in `nlp_pipeline.py`:
```python
"moneycontrol", "mint", "business today", "cnbc tv18", "et now",
"zeebiz", "outlook business", "fortune india"
```

**File**: `backend/services/nlp_pipeline.py` — `TIER_2_SOURCES` constant

---

## GAP 4: Impact Scores Lack Calibration Anchors

**Severity**: HIGH
**Affected**: All companies

**Finding**: Deep insight `impact_score` ranges from 6.5 to 8.5 across all articles with no clear calibration against financial materiality. The RBI climate disclosure pause (genuinely systemic, affects all Indian banks) scores 6.5 while a generic "expansion projects" article scores 7.8.

**Evidence**:
| Article | Impact Score | Financial Materiality |
|---------|-------------|----------------------|
| RBI Climate Disclosure Pause (systemic) | 6.5 | Affects all Indian banks (should be 8+) |
| IDFC ₹590 Cr Fraud (company-specific) | 8.5 | Direct ₹590 Cr loss (correctly high) |
| Adani Bribery Indictment ($265M) | 8.5 | Existential (correctly high) |
| Waaree Expansion Projects | 7.8 | Routine capex (should be 5-6) |
| SBI Green Finance Strategy | 8.2 | Competitive signal (should be 7) |

**Impact**: Clients cannot trust that "8.5" means something specific. Without calibration, the number is decorative rather than decision-useful.

**Recommendation**: Add calibration anchors to the deep insight LLM prompt:
```
Impact Score Calibration:
- 9-10: Existential threat or transformation (>20% revenue/valuation impact)
- 7-8: Material and requires board/CXO attention (5-20% impact)
- 5-6: Notable, departmental action needed (1-5% impact)
- 3-4: Awareness item, monitor quarterly (<1% impact)
- 1-2: Noise, no action required
```

**File**: `backend/services/deep_insight_generator.py` — user_prompt

---

## GAP 5: Supply Chain Dimension Underscored for Financial Services

**Severity**: MEDIUM
**Affected**: ICICI Bank, YES Bank, IDFC First Bank

**Finding**: The 5D relevance breakdown shows `supply_chain_impact=0` for most banking articles. While technically correct (banks don't have physical supply chains), it misses that banks have **financing supply chains** — portfolio companies, borrower ESG exposure, counterparty risk.

**Evidence**:
| Article | SC Score | Issue |
|---------|---------|-------|
| RBI Climate Disclosure | SC=1 | Should be SC=2 (affects all bank lending portfolios) |
| Bank of Baroda Bond | SC=0 | Should be SC=1 (green bond funds flow to project companies) |
| IDFC Fraud | SC=0 | Correct (internal fraud, not supply chain) |

**Impact**: Banking clients see 0/2 on supply chain for every article, which undervalues the platform's relevance to their actual risk exposure (lending portfolio ESG).

**Recommendation**: Add to the entity extraction prompt:
> "For financial institutions, 'supply chain impact' includes: lending portfolio exposure, counterparty ESG risk, financed emissions (Scope 3 Category 15), and borrower ESG compliance. Score 1 if the article implies indirect portfolio impact, 2 if it directly affects lending decisions or portfolio valuation."

**File**: `backend/ontology/entity_extractor.py` — relevance scoring prompt

---

## GAP 6: REREACT Recommendations Lack Actionable Specificity

**Severity**: HIGH
**Affected**: All companies

**Finding**: Recommendations use vague language and relative timelines. A compliance officer needs specific department, process, framework section, and calendar deadline — not "enhance governance framework by Q2."

**Evidence**:
| Company | Recommendation | Issue |
|---------|---------------|-------|
| IDFC | "Enhance Internal Controls and Governance Framework" | Which controls? Which process? Who is responsible? |
| SBI | "Develop Comprehensive Green Financing Strategy by Q2 FY25" | FY25 is past. Calendar dates needed. |
| JSW | "Enhance Renewable Energy Storage Investments — immediate" | How much investment? Which storage technology? |
| Adani | "Strengthen Emissions Reporting Framework — short_term" | Which emissions? Scope 1/2/3? Which facility? |

**Impact**: Recommendations that say "enhance" and "strengthen" without specifics are advisory fluff. Institutional clients will dismiss them as LLM-generated boilerplate.

**Recommendation**: Add to REREACT Generator prompt:
```
Every recommendation MUST include:
1. Specific responsible party (e.g., "Chief Risk Officer", "Audit Committee")
2. Framework section code (e.g., BRSR:P1, GRI:205, ESRS:G1)
3. Calendar deadline (absolute date, not "Q2" or "short_term")
4. Estimated budget range if applicable (e.g., "₹2-5 Cr for forensic audit")
5. Measurable success criterion (e.g., "Zero material findings in next BRSR assurance")

DO NOT use vague verbs: "enhance", "strengthen", "improve", "develop".
USE specific verbs: "commission", "file", "appoint", "allocate", "disclose", "audit".
```

**File**: `backend/services/rereact_engine.py` — generator_prompt

---

## GAP 7: Cross-Entity Article Leakage in Tenant Feeds

**Severity**: MEDIUM
**Affected**: YES Bank, Singularity AMC

**Finding**: Some articles appear in the wrong company's feed with questionable relevance:
- **YES Bank** sees "Bank of Baroda Board Approves Rs 15,000 Crore Bond Issue" — a competitor article, relevant for competitive intelligence but the causal chain explanation says "Direct match: 'Bank of Baroda' linked to YES Bank Ltd" which is misleading.
- **Singularity AMC** sees "CrowdStrike Charlotte AI" article — zero ESG nexus to an Indian AMC.
- **Singularity AMC** sees "Star Health Urgent Investor Meeting" — tangential at best.

**Impact**: Noise articles erode trust. Institutional clients expect curated, relevant feeds — not keyword-matched spillover.

**Recommendation**:
1. Add a **minimum causal chain confidence threshold** (e.g., confidence > 0.7) before including an article in a tenant's feed
2. For "directOperational" matches based on name matching, require that the matched entity IS the tracked company or a named competitor — not just any company in the same sector
3. For articles with relevance_score < 5 and causal_hops = 0, add a human-readable label: "Competitive Intelligence" or "Sector News" to distinguish from direct-impact articles

**File**: `backend/services/ontology_service.py` — entity matching fallback logic (line ~390)

---

## GAP 8: Inconsistent Risk Scoring for Similar Events

**Severity**: MEDIUM
**Affected**: Cross-company comparison

**Finding**: Two articles about the same IDFC fraud score differently:

| Article | Priority | Risk Score | Top Risk |
|---------|----------|-----------|----------|
| "IDFC First Bank shares to be in focus post disclosure of Rs 590-crore fraud" | 86.2 (CRITICAL) | 62/250 | Reputational=20 (CRITICAL) |
| "Bank staff colluded with outsiders in ₹590 crore fraud" | 83.8 (HIGH) | 60/250 | Reputational=16 (HIGH) |
| "IDFC First Bank reports ₹590 crore fraud at its Chandigarh branch" | 81.8 (HIGH) | SPOTLIGHT | Reputational=HIGH |

The same event (₹590 Cr fraud) gets different Reputational Risk scores across articles: 20 (CRITICAL), 16 (HIGH), and "HIGH" (spotlight). The risk assessment should be consistent for the same underlying event.

**Impact**: Clients viewing multiple articles about the same event see inconsistent risk ratings, undermining confidence in the methodology.

**Recommendation**: Implement **event deduplication** — when multiple articles cover the same event, consolidate risk assessments:
1. Detect duplicate events via entity + date clustering
2. Use the highest risk score across all articles covering the same event
3. Show a "Related Coverage" section linking the duplicate articles

**File**: New module needed — `backend/services/event_deduplication.py`

---

## GAP 9: Missing E/S Balance in Governance-Heavy Companies

**Severity**: MEDIUM
**Affected**: ICICI Bank, YES Bank, Singularity AMC

**Finding**: For banking and AMC companies, almost all articles are tagged under Governance themes. There is no Environmental or Social coverage despite these being material ESG dimensions for financial services (financed emissions, financial inclusion, employee diversity).

**Evidence**:
- ICICI Bank: 3/3 top articles = Governance themes
- YES Bank: 2/2 top articles = Governance themes
- Singularity AMC: 3/3 top articles = Governance themes

**Impact**: The "balanced E/S/G coverage" promise of the platform is not delivered for financial sector clients. A CSO reviewing the dashboard would see zero Environmental or Social content.

**Recommendation**: The news ingestion queries (`sustainability_query`, `general_query`) for financial companies should explicitly include E and S terms:
```
sustainability_query: "{company} ESG sustainability financed emissions climate risk"
general_query: "{company} financial inclusion workforce diversity green lending"
```
This ensures the RSS feed captures E/S articles, not just governance/corporate news.

**File**: `backend/tasks/news_tasks.py` — query construction, `backend/services/news_service.py`

---

## GAP 10: No Peer Benchmarking in Recommendations

**Severity**: LOW
**Affected**: All companies

**Finding**: Recommendations reference the tracked company but never benchmark against named peers. The SBI article mentions "competitive pressure" on ICICI but doesn't cite SBI's specific green lending target (7.5-10% by 2030) as a benchmark for ICICI's response.

**Impact**: Without peer benchmarks, recommendations feel generic. A CEO wants to know: "SBI is targeting 10% green portfolio by 2030 — we're at 3%. We need to close the gap."

**Recommendation**: Pass competitor data (already available in `company.competitors`) into the REREACT Generator prompt:
```
Competitor benchmarks (from company profile):
- {competitor_1}: {recent ESG action or metric}
- {competitor_2}: {recent ESG action or metric}

Recommendations MUST reference specific competitor actions when available.
```

**File**: `backend/services/rereact_engine.py`, `backend/services/ontology_service.py` (competitor data already loaded)

---

## Priority Matrix

| Gap | Severity | Effort | Impact on Credibility | Fix First? |
|-----|----------|--------|----------------------|------------|
| GAP 6: Recommendation specificity | HIGH | Medium | Very High | Yes |
| GAP 1: Positive news underscored | HIGH | Low | High | Yes |
| GAP 4: Impact score calibration | HIGH | Low | High | Yes |
| GAP 2: Theme homogeneity banking | HIGH | Medium | High | Yes |
| GAP 7: Cross-entity leakage | MEDIUM | High | High | Next |
| GAP 3: Source credibility India | MEDIUM | Low | Medium | Next |
| GAP 5: SC scoring for banks | MEDIUM | Low | Medium | Next |
| GAP 8: Inconsistent duplicate scoring | MEDIUM | High | Medium | Later |
| GAP 9: E/S balance for banks | MEDIUM | Medium | Medium | Later |
| GAP 10: Peer benchmarking | LOW | Medium | Medium | Later |
