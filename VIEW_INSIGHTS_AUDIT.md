# View Insights Enhancement Plan — Audit Against Current Implementation

**Date**: 2026-04-03
**Scope**: Every item in `view-insights-enhancement-plan.md` checked against actual snowkap-esg code and data

---

## Section 0: Company Context Engine

| Item | Plan Requires | Current Status | Gap |
|---|---|---|---|
| 0.1 CompanyContext fields | name, market_cap_category, market_cap_value, listing_exchange, hq_country, hq_region, sasb_industry, sasb_sector, revenue_last_fy, employee_count | **PARTIAL** — Has: name, market_cap (str), listing_exchange, headquarter_country, headquarter_region, industry, sasb_category. Missing: market_cap_value (numeric ₹ Cr), revenue_last_fy, employee_count | Need 3 numeric fields: market_cap_value, revenue_last_fy, employee_count |
| 0.2 Cap-Driven Parameter Table | Centralized table with financial_impact_floor, budget_range, investor_sensitivity_weight, regulatory_scrutiny_multiplier, timeline_compression | **PARTIAL** — Budget range text is in REREACT prompt ("₹10-100 Cr for Large Cap"). No centralized Python constant/table. No investor_sensitivity_weight or regulatory_scrutiny_multiplier used in scoring. | Need a formal `CAP_PARAMETERS` dict consumed by all modules |
| 0.3 Region Framework Priority Map | Ordered list per region, +0.4 boost (not +0.3) | **IMPLEMENTED at +0.3** — `REGION_FRAMEWORK_BOOST` in framework_rag.py. Plan wants +0.4 for home-region mandatory frameworks. | Boost is +0.3, plan wants +0.4 for mandatory. No penalty for irrelevant regions. |

---

## Section 1: Section Reordering

| Item | Plan Requires | Current Status | Gap |
|---|---|---|---|
| New order | Tier 1: Key Takeaways → Financial Impact & Timeline → Risk. Tier 2: ESG Relevance → Impact Analysis → Frameworks. Tier 3: Recommendations → Executive Insight. Tier 4: Supporting (collapsed). | **DIFFERENT ORDER** — Current: Key Takeaways → Risk → Recommendations → Impact Analysis → Frameworks → Executive Insight → Relevance → Net Impact → Narrative → ... | Plan wants Financial Impact & Timeline at #2 (before Risk). Currently it's after Frameworks. Recommendations at #7 in plan vs #5 currently. |
| Tier 4 collapsed container | "View Supporting Evidence (6 sections)" toggle | **NOT IMPLEMENTED** — Each supporting section is individually collapsible, no grouped Tier 4 container. | Need a grouped collapsible for Tier 4 sections |
| section_order array | Frontend reads from API | **NOT IMPLEMENTED** — Order is hardcoded in JSX. | Low priority — JSX ordering works, API-driven ordering is over-engineering for beta |

---

## Section 2: Key Takeaways

| Item | Plan Requires | Current Status | Gap |
|---|---|---|---|
| Rename | "Core Mechanism" → "Key Takeaways" | **DONE** | None |
| Profitability anchor in prompt | Prompt must ask "How does this connect to profitability?" | **PARTIAL** — Company cap/listing is in the deep insight prompt, but there's no explicit "connect to profitability" instruction in the key_takeaways (core_mechanism) generation. | Add profitability connection to core_mechanism prompt |
| Cap-calibrated references | Large Cap: institutional investor, index inclusion. Mid Cap: growth, credit rating. Small Cap: survival risk. | **PARTIAL** — Company context is in the deep insight system prompt, but the core_mechanism instruction doesn't differentiate by cap. | The LLM sees company context but isn't explicitly told to calibrate by cap for key takeaways |

---

## Section 3: Financial Impact & Timeline

| Item | Plan Requires | Current Status | Gap |
|---|---|---|---|
| Merged section | Single section replacing Financial & Time Horizon | **DONE in code** — `financial_timeline` field in prompt + frontend renders merged cards | None |
| ₹-denominated estimates calibrated to cap | Each bucket has specific ₹ ranges proportional to company size | **PARTIAL** — Prompt says "specific financial numbers" but doesn't enforce proportionality to market cap. No `revenue_last_fy` available for calibration. | Need revenue data for % benchmarking ("X% of FY24 EBITDA") |
| Profitability pathway per bucket | "ESG Event → Business Mechanism → Financial Line Item → ₹ Amount" | **NOT IMPLEMENTED** — Current prompt asks for financial impact text, not a structured pathway chain. | Need `profitability_pathway` field in each bucket |
| Metrics sub-fields | Each bucket has: cost_of_capital_impact, margin_pressure, cash_flow_impact, revenue_at_risk (immediate); valuation_rerating, investor_flow_impact, competitive_position, credit_rating_risk (structural) | **NOT IMPLEMENTED** — Current output is free-text per bucket, not structured metrics. | Major gap — plan wants structured key-value metrics, not prose |
| UI: Arrow chain visualization | `Event → Mechanism → P&L Line → ₹ Amount` | **NOT IMPLEMENTED** — Current renders text in colored cards, no arrow chain. | Frontend enhancement needed |

---

## Section 4: Framework Alignment

| Item | Plan Requires | Current Status | Gap |
|---|---|---|---|
| Region boost +0.4 for mandatory | Tiered: +0.6 mandatory, +0.4 home region, +0.1 global, -0.2 irrelevant region | **PARTIAL** — Current: flat +0.3 for home region, +0.1 for global. No -0.2 penalty. No mandatory detection. | Need tiered boost + negative penalty for irrelevant |
| Mandatory framework detection | Lookup table: BRSR mandatory for top 1000 NSE, CSRD for EU large companies | **NOT IMPLEMENTED** — No `MANDATORY_FRAMEWORKS` table. No `[MANDATORY]` badge. | Need mandatory lookup + badge |
| Score threshold: ≥0.5 high, 0.2-0.49 low, <0.2 hidden | Three tiers | **PARTIAL** — Current: ≥0.3 shown, <0.3 behind "View more". Plan wants ≥0.5 for high, and <0.2 completely hidden. | Adjust thresholds: 0.5/0.2 instead of 0.3/0 |
| Provision-level mapping | Specific BRSR questions (Q14, Q15), not just principle names | **PARTIAL** — `triggered_sections` exist but are principle-level, not question-level. | Need provision-level keyword mapping (~2000 line data file) |
| Profitability link per framework | "BRSR non-compliance → SEBI scrutiny → trading restrictions → liquidity risk" | **NOT IMPLEMENTED** — No per-framework profitability link. | Add to FrameworkMatch output |
| Impact on company per framework | 1-sentence LLM-generated explanation | **PARTIAL** — `compliance_implications` exist but are generic, not company-specific. | Need company-specific impact sentence |

---

## Section 5: AI Recommendations (RE3ACT)

| Item | Plan Requires | Current Status | Gap |
|---|---|---|---|
| Company-specific generator prompt | revenue, employee_count, market_cap_value in prompt | **PARTIAL** — Has market_cap (str), listing_exchange, headquarter_country. Missing: revenue, employee_count, market_cap_value (numeric). | Need 3 numeric fields |
| Budget breakdown | "₹2.5 Cr: ₹1.5 Cr technology + ₹1 Cr consulting" | **NOT IMPLEMENTED** — Budget is a single range string, no breakdown. | Add `budget_breakdown` field |
| ROI calculation | `roi_percentage` and `payback_months` computed in post-processing | **NOT IMPLEMENTED** — No ROI/payback computation. | Need numeric extraction from budget and profitability_link |
| Priority field | CRITICAL / HIGH / MEDIUM based on deadline urgency + financial impact | **NOT IMPLEMENTED** — Has `urgency` (immediate/short_term/ongoing) and `estimated_impact` (High/Medium/Low) but not a combined `priority`. | Could derive from existing fields |
| Risk of inaction score | 1-10 per recommendation | **NOT IMPLEMENTED** | Add to analyzer output |
| Interactive Q&A chat | Expandable chat panel beneath recommendations, with context injection | **PARTIAL** — "Ask AI about this" button navigates to `/agent` page with sessionStorage context. Plan wants an INLINE expandable chat panel, not page navigation. | Change from page navigation to inline chat panel |
| Suggested questions | Pre-generated based on highest-risk findings | **NOT IMPLEMENTED** | Generate 3 suggested questions from findings |
| POST /api/insights/{article_id}/chat | Dedicated endpoint for insight-contextual chat | **NOT IMPLEMENTED** — Uses generic `/api/agent` endpoint. | Could use existing agent endpoint with enhanced context |
| Streaming responses | SSE or WebSocket for chat | **NOT IMPLEMENTED** — Agent chat returns full response, not streamed. | Enhancement for production |

---

## Section 6: Risk Assessment

| Item | Plan Requires | Current Status | Gap |
|---|---|---|---|
| Industry-weighted scoring | AdjustedScore = P × E × industry_weight | **NOT IMPLEMENTED** — Raw P × E used. No industry weight matrix. | Need 10×15 industry weight matrix |
| Profitability note per top risk | "SEBI penalty ₹25L-2Cr + trading restriction → liquidity risk" | **NOT IMPLEMENTED** — Rationale exists but no explicit profitability note. | Add `profitability_note` to top 3 risks |
| 10×15 weight matrix | 10 risk categories × 15 SASB sectors | **NOT IMPLEMENTED** | Major data file needed |

---

## Section 7: Data Model Changes

| Item | Plan Requires | Current Status | Gap |
|---|---|---|---|
| CompanyContext in API response | Always present | **NOT IN API** — Company data available on backend but not exposed in article API response. | Add company_context to news feed/detail response |
| section_order in API | Dynamic ordering | **NOT IMPLEMENTED** | Low priority |
| key_takeaways with profitability_connection | Separate field | **NOT IMPLEMENTED** — core_mechanism is a single string, no profitability_connection sub-field. | Structure change in deep insight output |
| TimelineBucket with metrics sub-object | headline + profitability_pathway + metrics dict | **NOT IMPLEMENTED** — Free text per bucket. | Major prompt + schema change |
| EnhancedRecommendation with priority, risk_of_inaction, payback_months | Extended fields | **NOT IMPLEMENTED** | Add fields to REREACT output |
| chat_enabled + suggested_questions | In recommendations response | **NOT IMPLEMENTED** | Add to API response |

---

## Section 8: Implementation Sequence Status

| Phase | Plan Phase | Status |
|---|---|---|
| Phase 1: Foundation | CompanyContext, cap table, region map, mandatory lookup | **60%** — Context fields added, region map exists, no cap table or mandatory lookup |
| Phase 2: Section Restructuring | Rename + reorder + Tier 4 container | **70%** — Renamed + reordered, no Tier 4 container |
| Phase 3: Financial Impact Merge | Merge + cap calibration + 3-card UI | **50%** — Merged in prompt + basic UI, no structured metrics or pathway visualization |
| Phase 4: Framework Overhaul | Enhanced scoring + mandatory detection + provision mapping | **30%** — Basic region boost, no mandatory detection, no provision-level mapping |
| Phase 5: RE3ACT Enhancement | All 3 agents enhanced + post-processing + Q&A chat | **50%** — Generator enhanced with cap context, post-processing added, Q&A via page nav not inline |
| Phase 6: Risk Assessment Polish | Industry weights + profitability notes | **0%** — Not started |
| Phase 7: Testing & Calibration | 5 companies × 3 caps × 5 regions | **0%** — Not started |

---

## Section 10: Quality Gates

| Gate | Status |
|---|---|
| Every ₹ amount proportional to company cap | **PARTIAL** — Budget ranges calibrated, financial impact not enforced proportionally |
| Every recommendation has quantified profitability | **DONE** — Post-processing flags unquantified links |
| Framework rankings change by region | **DONE** — India sees BRSR first, different from US |
| No framework with score < 0.2 in UI | **NOT DONE** — Current threshold is 0.3, and everything below is shown behind "View more" (not hidden) |
| BRSR first for India, CSRD for EU | **DONE** — Region boost works |
| Q&A can answer "what if we do nothing?" | **NOT DONE** — No inline Q&A, only page-level agent chat |
| All deadlines future | **DONE** — Post-processing corrects past dates |
| Tier 4 collapsed, doesn't push Tier 1-3 below fold | **NOT DONE** — No grouped Tier 4 container |

---

## Summary Scorecard

| Plan Section | Items | Implemented | Partial | Not Done | Score |
|---|---|---|---|---|---|
| 0. Company Context | 3 | 0 | 3 | 0 | 50% |
| 1. Section Reordering | 3 | 1 | 1 | 1 | 44% |
| 2. Key Takeaways | 3 | 1 | 2 | 0 | 50% |
| 3. Financial Impact & Timeline | 5 | 1 | 1 | 3 | 30% |
| 4. Framework Alignment | 6 | 0 | 3 | 3 | 25% |
| 5. AI Recommendations (RE3ACT) | 9 | 1 | 3 | 5 | 28% |
| 6. Risk Assessment | 3 | 0 | 0 | 3 | 0% |
| 7. Data Model | 6 | 0 | 0 | 6 | 0% |
| **Overall** | **38** | **4 (10%)** | **13 (34%)** | **21 (55%)** | **28%** |

---

## Top 10 Gaps to Close (Highest Impact)

1. **Industry-weighted risk scoring** (§6) — 10×15 matrix not built. Risk scores are flat across all industries.
2. **Mandatory framework detection** (§4.4) — No `[MANDATORY]` badge. BRSR should be flagged as legally required for top 1000 NSE companies.
3. **Financial timeline structured metrics** (§3) — Free text instead of structured headline + profitability_pathway + metrics per bucket.
4. **Revenue and employee_count on Company model** (§0.1) — Needed for proportional financial calibration.
5. **Inline Q&A chat panel** (§5.4) — Currently navigates away to /agent. Plan wants inline expandable panel within insights view.
6. **Framework profitability links** (§4.6) — "BRSR non-compliance → SEBI penalty → liquidity risk" not generated.
7. **Provision-level framework mapping** (§4.6) — Only principle-level, not question-level (BRSR Q14, Q15).
8. **Tier 4 grouped collapsible** (§1) — Supporting sections individually collapsible, not grouped.
9. **Region penalty for irrelevant frameworks** (§4.3) — No -0.2 penalty for showing SEC Climate to an Indian company.
10. **ROI and payback period on recommendations** (§5.3) — No computed ROI or payback months.
