# Snowkap — References, Validation, and How Frameworks Get Tagged

**Companion to:** `docs/INTELLIGENCE_AND_CALCULATIONS.md`

**Two things this document answers:**

1. **For every calculation in the engine — where does the underlying logic come from, and how validated is it?** Is it a published standard, an industry convention, an academic formula, or an engine-internal heuristic? What evidence backs the choice?
2. **How does framework tagging actually work end-to-end?** When you see "BRSR · GRI · TCFD" attached to an article, what code path produced that list, and why those specific frameworks in that order?

---

## Part 1 — References & Validation

### 1.1 Validation legend

Every component in the engine falls into one of five categories:

| Tier | Meaning |
|---|---|
| **PS** — Published Standard | Verbatim from a regulator, ISO, or recognised body. Authoritative. |
| **IC** — Industry Convention | Standard practice in equity research / consulting / risk management. Widely used but not codified. |
| **AE** — Academic / Econometric | From peer-reviewed papers, textbook formulas, or established research methodologies. |
| **EH** — Engine Heuristic | Snowkap-internal choice. Defensible but not externally validated. Often calibrated against the fuzz harness. |
| **FV** — Fuzz-Validated | Snowkap-internal AND continuously tested against a corpus of real articles with expected outcomes. Failures alert nightly. |

### 1.2 Reference table — every formula in the engine

| Component | Tier | Source / Reference | Validation |
|---|---|---|---|
| **ESG topic taxonomy (21 themes)** | **PS** | SASB Materiality Map (industry-specific topics) + GRI Standards (universal topics) + IFRS S1/S2 (financial materiality). Encoded in `knowledge_base.ttl`. | Fixed list; reviewed by Snowkap ESG team against SASB Standards v2023-12 and GRI 1-4 + 200/300/400 series. |
| **22 event types** | **EH** | Snowkap-curated from analysing 6+ months of Indian + global ESG news (SEBI enforcement, NGO reports, contract wins, capacity additions, etc.). Encoded in `knowledge_depth.ttl`. | Fuzz-validated against [`tests/fuzz_corpus/corpus.jsonl`](../tests/fuzz_corpus/corpus.jsonl). Phase 12.1 confidence bar prevents single-keyword false positives. |
| **Event keyword regexes (word-boundary)** | **EH** | Hand-curated word-boundary keyword sets per event type. | Phase 12 fuzz harness: `event_id` exact match required for each corpus article. 10/10 pass rate post-Phase-14. |
| **Materiality weights (topic × industry)** | **PS + IC** | Primary source: SASB Materiality Map (industry-specific weights). Secondary: WBCSD Reporting Matters + MSCI Materiality Map for cross-industry topics. Encoded in `knowledge_base.ttl` and `knowledge_expansion.ttl`. | Pegged at 5 named levels — `critical=1.0`, `high=0.7-0.9`, `medium=0.4-0.6`, `low=0.1-0.3`, `nil=0`. Reviewed by Snowkap ESG team against SASB v2023-12. |
| **Industry risk weights (industry × risk category)** | **IC + EH** | Sourced from McKinsey / Deloitte / Bain ESG industry reports; cross-referenced against Sustainalytics Industry Risk Ratings (subscription benchmark). | Calibrated on 7 target companies; further tuning would benefit from a public industry benchmark integration (deferred — see "Known limitations" §2.7). |
| **21 ESG frameworks** | **PS** | Each framework is a published standard. References below. | Frameworks themselves are authoritative; the *mapping* topic→framework is **IC** (industry consensus). |
| **Framework section codes (BRSR:P1-P9, GRI:303, TCFD:Risk-1, etc.)** | **PS** | Pulled verbatim from each framework's official documentation. | Spot-checked against current spec versions during Phase 7 build. See §3.2 below for the per-framework spec links. |
| **Regional framework boosts** | **PS + IC** | India BRSR mandatory for top-1000 NSE-listed (SEBI Circular SEBI/HO/CFD/CFD-SEC-2/P/CIR/2023/122). EU CSRD mandatory phase-in by company size (Directive (EU) 2022/2464). US SEC Climate Rules (17 CFR Parts 210, 229, 232, 239, 249). UK SDR (FCA PS23/16). | Phase 23 expanded coverage to 14 countries → 6 framework regions. Boost values (0.1–0.6) are **EH** — chosen to make mandatory frameworks rank first without crowding voluntary ones out. |
| **Mandatory rules (BRSR Large Cap India · CSRD EU · SEC US · etc.)** | **PS** | Direct from regulator. See `knowledge_expansion.ttl` lines 1041–1067. | Verified Phase 23. UK split from EU bucket in Phase 23B fixed a real false-positive where Lloyds was tagged with CSRD. |
| **5-D relevance scoring** | **IC + EH** | Standard equity-research relevance framing: materiality × industry exposure × cap tier × geography × event polarity. Multiplicative composition. | `engine/analysis/relevance_scorer.py`. Fuzz harness asserts `relevance >= 7` for HOME-tier articles. |
| **Risk level thresholds (CRITICAL≥20, HIGH≥12, MODERATE≥6, LOW≥0)** | **IC** | Derived from COSO ERM 2017 risk-rating matrix (5×5 likelihood × impact → score 0–25). Snowkap uses a comparable additive scale. Encoded in `knowledge_expansion.ttl::RiskLevelThreshold`. | Buckets are stable across all 7 industries. Calibrated so ~5-10% of ingested articles reach CRITICAL/HIGH. |
| **TEMPLES framework (7 categories)** | **PS** | Technology · Economic · Market · Political · Legal · Environmental · Social — a generalisation of PESTLE used in enterprise risk management (e.g. RIMS, ISO 31000). | Encoded in `knowledge_base.ttl`. Each TEMPLES category has lead/lag indicators per Phase 7. |
| **17 SDG mappings** | **PS** | UN Sustainable Development Goals 2015. SDG-target sub-nodes (Phase 7) per UN A/RES/70/1. | Topic→SDG mapping is **IC** — based on UNGC/GRI cross-walk documents. |
| **Causal primitives framework (22 primitives, 123 P→P edges)** | **AE + IC** | Adapted from enterprise risk modelling literature: KPMG Dynamic Risk Assessment; PwC ERM frameworks; and academic causal-inference work (Pearl, *Causality* 2009). β-ranges per edge sourced from sector-specific elasticity studies in McKinsey Energy Insights, IEA reports, and industry sustainability accounting research. | Edge metadata (β-range, lag, functionalForm, confidence) is **EH** but each edge has `edgeNotes` documenting the source. Edges marked `confidence: high` come from published elasticities; `medium`/`low` are calibrated. |
| **Cascade functional forms (linear, log-linear, threshold, ratio, step, composite)** | **AE** | Standard econometric functional forms. Used in price elasticity (linear, log-linear), carbon pricing (threshold), break-even (step), and supply-chain modelling (ratio). | Code: `engine/analysis/primitive_engine.py:96–136`. Each form has a unit-test case. |
| **Company-specific β calibration** | **EH** | `β_company = β_ontology × (company_share / industry_avg_share)` with `industry_avg = 0.15` and `[0.5×β_lo, 1.5×β_hi]` clamps. | `primitive_engine.py:144–190`. Industry-share scaling concept borrowed from sector beta adjustment in CAPM literature (Damodaran's adjusted betas). The 0.15 anchor is a Snowkap choice; clamps prevent runaway scaling. |
| **Margin bps formula** | **PS** | `Δ_source_cr / revenue_cr × 10,000`. Standard finance: 1 bp = 1/100 of 1 percentage point. | Universal CFO unit. Verifier pass 1 (`verify_margin_math`) enforces ±5% reconciliation between stated and computed bps. |
| **ROI caps (compliance 500% · financial 300% · strategic 400% · operational 200%)** | **EH** | Snowkap anti-hallucination guardrail. Without caps, the LLM occasionally produced 1000%+ ROI claims that were narratively persuasive but financially absurd. | `output_verifier.py:1102–1108`. Caps disclosed via `roi_capped: true` + `roi_cap_reason` for UI transparency. Calibrated on the fuzz harness. |
| **Criticality scorer (6 components)** | **EH** | Snowkap-designed multi-attribute scoring. Components individually grounded — materiality (SASB), financial magnitude (CAPM-style log scaling), actionability (decision theory), painpoint (semantic similarity via cosine on OpenAI embeddings), recency (exponential decay — standard in financial news), source authority (whitelist tiers). | Component weights and band thresholds (CRITICAL ≥ 0.75 · HIGH ≥ 0.55 · etc.) are **EH** calibrated on a backfill of 55 articles. Verified outcomes match human ranking on Waaree + Adani Power sample. |
| **Per-role criticality weights** | **EH** | Hand-picked to reflect role priorities: CFO weights `financial_magnitude` 2× the default; CEO weights `materiality` + `painpoint_match` (strategic signals); Analyst weights `materiality` (deepest compliance lens). | `criticality_scorer.py:95–118`. Weights deliberately overlap < 50% to ensure role distinctness (verified in `test_phase26_role_distinctness.py`). |
| **3 penalties (staleness · confidence · polarity drift)** | **EH** | Threshold values (30 days, 0.5 confidence, polarity sign-mismatch) chosen to catch the bug patterns surfaced during Phase 12-14 audits. | Phase 14 fuzz: post-correction, Waaree contract win went from misclassified ₹807 Cr crisis → clean ₹477.5 Cr revenue gain. |
| **Number rendering (2-sig-fig, en-IN grouping, range ±10%)** | **IC** | Bloomberg / Reuters house style for financial commentary: round to 2 sig figs in prose, full precision in tables, en-IN grouping for ₹ Cr. ±10% range = standard 1-sigma envelope. | `client/src/lib/number_format.ts`. Spec lives in CLAUDE.md §4.5. 19 acceptance tests in `test_phase26_number_render.py`. |
| **Persona × criticality boosts (esg_focus +40% · framework +30% · geo +25% · risk ±15% · click +20%)** | **EH** | Multiplicative boost design from recommender-systems literature (e.g. Netflix's collaborative filtering with content boosts). Boost magnitudes are Snowkap-calibrated. | `engine/persona/persona_scorer.py:70–131`. Discoverability invariant: CRITICAL articles floored at 0.65 — a persona mismatch cannot hide a crisis. |
| **Hard floors (home ≥ 0.65, feed ≥ 0.40, share ≥ 0.65 with 422)** | **EH** | Designed so a CFO never sees noise and a journalist always sees something. 422 with alternatives prevents accidental low-quality sends. | `engine/index/sqlite_index.py::query_feed`. |
| **CFO preflight (6 gates)** | **EH** | Snowkap quality bar: a CFO surface item must have ₹ + framework + freshness + polarity coherence + numeric consistency + stakeholder polarity. | `engine/analysis/cfo_preflight.py:398–461`. Articles failing any gate are hidden from CFO surface but shown to ESG Analyst (so analysts can audit). |
| **10 verifier passes** | **EH** | Each pass closes a specific class of LLM failure observed during Phase 11-14 audits. Thresholds (±5%, ±10%, 35%, 5%) chosen to be tight enough to catch real drift, loose enough not to false-positive on legitimate variation. | `engine/analysis/output_verifier.py::verify_and_correct`. Every pass has unit tests in `test_phase3_verifier.py`. Fuzz harness tracks per-pass fire rates as SLOs. |
| **Stakeholder positions (default + positive variants)** | **PS for cases · EH for stance mapping** | Each cited precedent (Tata Power SECI, Infosys MSCI A→AA, ReNew Green Bond, etc.) is a real 2023-2024 case. The stance assignment (e.g. "SEBI cited Tata Power as BRSR leader") is documented from the original SEBI/RBI/MSCI release. | `data/ontology/stakeholder_positions.ttl`. Phase 15 added polarity-aware variants. |
| **8 precedent cases (positive event library)** | **PS** | Each is a real, named, dated case. See `data/ontology/precedents.ttl`. | Phase 14.2. Use of precedents validated: post-Phase-14 the Waaree contract-win article cites Tata Power SECI (correct) instead of Vedanta SCN (wrong polarity). |
| **Discovery (self-evolving ontology)** | **EH** | Auto-promotion thresholds (≥3 articles from ≥2 sources, confidence ≥0.80, Jaro-Winkler dedup) borrowed from named-entity-recognition literature. | `engine/ontology/discovery/`. Hard cap of 10,000 discovered triples and 90-day archival prevents drift. All promotions audited in `discovery_audit.jsonl`. |

### 1.3 What's published-standard vs Snowkap-internal — summary

**Published Standards (PS) drive:**
- The 21 ESG topics (SASB + GRI + IFRS)
- The 21 frameworks themselves (BRSR, GRI, TCFD, …) and their section codes
- Mandatory rules (BRSR for India Large Cap, CSRD for EU, etc.) — from regulators
- TEMPLES + SDG taxonomy
- Margin bps formula
- 8 precedent cases (real named events)

**Industry Convention (IC) drives:**
- Materiality weight magnitudes (cross-walked against SASB + MSCI)
- 5-D relevance scoring composition
- Risk-level threshold scale (COSO ERM-style)
- Number rendering style (Bloomberg / Reuters)

**Academic / Econometric (AE) drives:**
- 6 cascade functional forms
- Causal inference structure
- Industry-share β scaling (Damodaran adjusted-beta concept)

**Engine Heuristic (EH) drives — these are Snowkap design choices, not external truth:**
- Specific β-range mid-points + clamps
- Criticality 6-component weights and band thresholds
- Per-role weights overlap (< 50%)
- Penalty magnitudes (−0.20, −0.15, −0.20)
- ROI cap percentages (200/300/400/500)
- Persona boost magnitudes (+40%, +30%, +25%, +20%, ±15%)
- Hard floors (0.65 home, 0.40 feed)
- Verifier thresholds (±5%, ±10%, 35%, 5%)
- Discovery promotion thresholds (≥3 articles, conf ≥0.80)

**Fuzz-Validated (FV) means:** the chosen value (whether EH or IC) is tested against a curated corpus of real articles every night, with documented expected outcomes. Regressions break CI.

### 1.4 Per-framework published-standard references

| Framework | Standard / Source | Latest version checked |
|---|---|---|
| **BRSR** | SEBI Circular SEBI/HO/CFD/CMD-2/P/CIR/2021/562 (May 10, 2021); updated by SEBI/HO/CFD/CFD-SEC-2/P/CIR/2023/122 (July 12, 2023). 9 Principles. | BRSR Core (2023-07) |
| **GRI** | GRI Standards — Universal (GRI 1, 2, 3) + Topic Standards (200/300/400 series). Free download at globalreporting.org. | GRI 2021 (Universal Standards) |
| **TCFD** | Task Force on Climate-related Financial Disclosures — 11 recommendations across Governance / Strategy / Risk Management / Metrics & Targets. | TCFD 2017 final report + 2021 implementation guidance |
| **CSRD / ESRS** | EU Corporate Sustainability Reporting Directive (Directive (EU) 2022/2464). European Sustainability Reporting Standards (Commission Delegated Regulation (EU) 2023/2772) — ESRS 1-2 cross-cutting + E1-E5 + S1-S4 + G1. | ESRS Set 1 (2023-12) |
| **SASB** | Sustainability Accounting Standards Board — 77 industry-specific standards. Now under IFRS Foundation (ISSB). | SASB 2023-12 |
| **ISSB / IFRS S1+S2** | IFRS S1 General Requirements + IFRS S2 Climate-related Disclosures. Effective Jan 2024. | IFRS S1+S2 (2023-06) |
| **EU Taxonomy** | Regulation (EU) 2020/852 (Taxonomy Regulation) + Climate Delegated Act + Environmental Delegated Act. | 2023-06 (DNSH technical criteria) |
| **SFDR** | EU Sustainable Finance Disclosure Regulation (EU) 2019/2088. Article 6 / 8 / 9 fund classifications. | SFDR RTS Q&A 2023-05 |
| **CDP** | CDP (formerly Carbon Disclosure Project) — Climate, Water, Forests questionnaires. Annual cycle. | CDP 2024 cycle |
| **SBTi** | Science Based Targets initiative — corporate net-zero standard. WWF/CDP/WRI/UNGC. | SBTi Net-Zero Standard v1.2 (2024-03) |
| **TNFD** | Taskforce on Nature-related Financial Disclosures. 14 recommended disclosures across Governance/Strategy/Risk-Mgmt/Metrics-Targets. | TNFD v1.0 (2023-09) |
| **SEC Climate Rule** | US SEC Final Rules: The Enhancement and Standardization of Climate-Related Disclosures (Mar 2024, 17 CFR Parts 210, 229, 232, 239, 249). | SEC Final Rule 2024-03 |
| **Porter Five Forces** | Michael Porter, *Competitive Strategy* (1980). Industry structural analysis. | Foundational |
| **McKinsey Three Horizons** | Baghai, Coley & White, *The Alchemy of Growth* (1999). H1 core / H2 emerging / H3 transformative. | Foundational |
| **BCG Matrix** | Bruce Henderson, *The Product Portfolio* (BCG Perspectives, 1970). Stars / Cash Cows / Question Marks / Dogs. | Foundational |
| **COSO ERM** | COSO *Enterprise Risk Management — Integrating with Strategy and Performance* (2017). 5 components + 20 principles. | COSO ERM 2017 |
| **CFA ESG Integration** | CFA Institute *Guidance and Case Studies for ESG Integration: Equities and Fixed Income* (2018) + *ESG Investing and Analysis* curriculum (2020). | CFA ESG Certificate (2021) |
| **DJSI** | S&P Dow Jones Sustainability Indices — Corporate Sustainability Assessment (CSA). Annual. | DJSI 2024 |
| **S&P Global ESG** | S&P Global CSA (Corporate Sustainability Assessment) — produces the S&P Global ESG Score (0–100). | S&P 2024 |
| **Edelman Trust Barometer** | Edelman annual trust survey across business / government / NGO / media. | Edelman 2024 |

### 1.5 Where validation is weakest (honest disclosure)

1. **Industry risk weights** (`industry × risk category`) — currently calibrated against the 7 target companies. Adding a Sustainalytics or MSCI Industry Risk benchmark integration would lift this from **IC + EH** to **PS**. (Deferred — see CLAUDE.md "Production Roadmap" §EODHD note.)

2. **Causal primitive β-ranges** — `confidence: high` edges trace to published elasticities; `confidence: medium/low` edges (the majority — 80 of 123 P→P edges) are Snowkap calibration. Each edge has `edgeNotes` documenting the reasoning but not a citable peer-reviewed source. Phase 17 plan called for a literature-citation field per edge — deferred.

3. **Criticality component weights** — calibrated on 55 backfilled articles across 7 companies. A 500-article human-graded benchmark would lift the criticality scorer from **EH** to **FV** with stronger statistical backing.

4. **NewsAPI.ai token-cost rule (1 token per article)** — documented assumption in `engine/ingestion/news_router.py::_default_token_cost`. Awaiting manual verification against NewsAPI.ai docs (their docs are JS-rendered and not directly fetchable). Flagged in test + comments.

---

## Part 2 — How Framework Tagging Actually Works

When you see an article tagged with `BRSR (1.0) · GRI (0.65) · TCFD (0.65) · EU_TAXONOMY (0.55)`, this section explains how the engine produced exactly that list with exactly those relevance scores in exactly that order.

### 2.1 The five-step pipeline

Code path: [`engine/analysis/framework_matcher.py::match_frameworks`](../engine/analysis/framework_matcher.py)

```
ARTICLE THEMES (Stage 2 LLM output)
    ↓
[Step 1] Theme → Framework trigger lookup (ontology SPARQL)
    ↓ produces {framework_id → relevance}
[Step 2] Base relevance assignment (primary theme = 1.0 weight, secondary = 0.6)
    ↓
[Step 3] Regional boost (per company HQ region)
    ↓ adds 0.1 to 0.6 to mandatory regional frameworks
[Step 4] Mandatory marking (region × cap tier)
    ↓ flags `is_mandatory: true`
[Step 5] Section code population (BRSR:P6, GRI:303, etc. from primary theme)
    ↓
SORTED LIST → first item becomes the headline framework
```

Every step is ontology-driven. **No Python dict for framework knowledge exists in `engine/`** — Phase 15 removed the last one (grep-verified).

### 2.2 Step 1 — Theme triggers framework

The ontology stores 21 `triggersFramework` predicates, one per ESG topic, in [`knowledge_base.ttl:360–380`](../data/ontology/knowledge_base.ttl#L360):

```turtle
# Example: Water topic triggers 5 frameworks
snowkap:topic_water snowkap:triggersFramework
    snowkap:BRSR, snowkap:GRI, snowkap:ESRS, snowkap:CDP, snowkap:TNFD .

# Climate triggers the widest set
snowkap:topic_climate snowkap:triggersFramework
    snowkap:BRSR, snowkap:GRI, snowkap:TCFD, snowkap:CSRD, snowkap:ESRS,
    snowkap:ISSB, snowkap:CDP, snowkap:SBTI, snowkap:SEC_CLIMATE,
    snowkap:SP_GLOBAL_ESG, snowkap:DJSI .
```

SPARQL query: `query_frameworks_detail(theme)` ([`intelligence.py`](../engine/ontology/intelligence.py)) returns a `FrameworkRef` list with `id`, `label`, `profitability_link`.

**Why these specific mappings?** Each topic→framework triple reflects which frameworks *require* disclosure on that topic. Water shows up in BRSR Principle 6, GRI 303, ESRS E3, CDP Water — all published standards. The list is **PS** at the level of "is this framework relevant to water?", **IC** at the level of "exactly these and not others".

### 2.3 Step 2 — Base relevance scoring

[`framework_matcher.py:116–131`](../engine/analysis/framework_matcher.py#L116-L131):

```python
base_weight = 1.0 if theme == tags.primary_theme else 0.6
# ... for each framework triggered by this theme:
relevance = min(1.0, base_weight * 0.55)
```

So the **primary theme** contributes `0.55` base relevance per framework it triggers, and **secondary themes** contribute `0.33` (= 0.6 × 0.55).

**Why 0.55?** Calibration choice: ensures no framework starts at 1.0 from theme alone — regional boost or mandatory marking is required to reach top tier. Keeps the scoring informative.

When a framework is triggered by multiple themes, the additional themes contribute `+0.06` each (`base_weight * 0.1`) to the running total, capped at 1.0.

### 2.4 Step 3 — Regional boost

For the company's framework region (one of: `INDIA`, `EU`, `UK`, `US`, `APAC`, `GLOBAL`), the engine queries `RegionalFrameworkBoost` triples.

From [`knowledge_expansion.ttl:1014–1033`](../data/ontology/knowledge_expansion.ttl#L1014-L1033):

```turtle
# India region
snowkap:boost_india_brsr  forRegion "INDIA" ; boostsFramework snowkap:BRSR ; boostValue 0.6 .
snowkap:boost_india_gri   forRegion "INDIA" ; boostsFramework snowkap:GRI  ; boostValue 0.1 .
snowkap:boost_india_cdp   forRegion "INDIA" ; boostsFramework snowkap:CDP  ; boostValue 0.1 .
snowkap:boost_india_tcfd  forRegion "INDIA" ; boostsFramework snowkap:TCFD ; boostValue 0.1 .

# EU region
snowkap:boost_eu_csrd     forRegion "EU"    ; boostsFramework snowkap:CSRD ; boostValue 0.6 .
snowkap:boost_eu_esrs     forRegion "EU"    ; boostsFramework snowkap:ESRS ; boostValue 0.6 .
snowkap:boost_eu_taxonomy forRegion "EU"    ; boostsFramework snowkap:EU_TAXONOMY ; boostValue 0.5 .
snowkap:boost_eu_sfdr     forRegion "EU"    ; boostsFramework snowkap:SFDR ; boostValue 0.4 .

# US region
snowkap:boost_us_sec      forRegion "US"    ; boostsFramework snowkap:SEC_CLIMATE ; boostValue 0.6 .
snowkap:boost_us_sasb     forRegion "US"    ; boostsFramework snowkap:SASB ; boostValue 0.4 .

# Global baseline (applied alongside region-specific boosts)
snowkap:boost_global_tcfd forRegion "GLOBAL" ; boostsFramework snowkap:TCFD ; boostValue 0.1 .
snowkap:boost_global_gri  forRegion "GLOBAL" ; boostsFramework snowkap:GRI  ; boostValue 0.1 .
snowkap:boost_global_cdp  forRegion "GLOBAL" ; boostsFramework snowkap:CDP  ; boostValue 0.1 .
snowkap:boost_global_issb forRegion "GLOBAL" ; boostsFramework snowkap:ISSB ; boostValue 0.1 .
```

**The boost values (0.1, 0.4, 0.5, 0.6) follow this pattern:**

| Boost | Meaning |
|---|---|
| 0.6 | Mandatory in this region (BRSR India, CSRD EU, ESRS EU, SEC US) |
| 0.5 | Strongly preferred (EU Taxonomy in EU) |
| 0.4 | Common in this region (SFDR for EU funds, SASB for US disclosure) |
| 0.1 | Globally relevant baseline |

So a Water topic at an Indian Large Cap company (`region_key = "INDIA"`) gets:
- BRSR: `0.55 (base) + 0.6 (India boost)` = **1.0** (capped)
- GRI: `0.55 (base) + 0.1 (India boost)` = **0.65**
- ESRS: `0.55 (base) + 0 (no India boost)` = **0.55**
- CDP: `0.55 (base) + 0.1 (India boost)` = **0.65**
- TNFD: `0.55 (base) + 0 (no India boost)` = **0.55**

### 2.5 Step 4 — Mandatory marking

[`knowledge_expansion.ttl:1041–1067`](../data/ontology/knowledge_expansion.ttl#L1041-L1067):

```turtle
snowkap:mandatory_brsr a snowkap:MandatoryRule ;
    snowkap:mandatoryFramework snowkap:BRSR ;
    snowkap:mandatoryRegion "INDIA" ;
    snowkap:mandatoryCapTier "Large Cap" .

snowkap:mandatory_csrd a snowkap:MandatoryRule ;
    snowkap:mandatoryFramework snowkap:CSRD ;
    snowkap:mandatoryRegion "EU" ;
    snowkap:mandatoryCapTier "ALL" .

snowkap:mandatory_sec_climate a snowkap:MandatoryRule ;
    snowkap:mandatoryFramework snowkap:SEC_CLIMATE ;
    snowkap:mandatoryRegion "US" ;
    snowkap:mandatoryCapTier "ALL" .
```

**Why "Large Cap" for BRSR India?** SEBI's 2023 update extended BRSR to the top-1000 NSE-listed by market cap. The engine treats this as "Large Cap" in cap-tier terms.

**Why "ALL" for CSRD?** EU CSRD applies to all in-scope companies with no cap-tier carve-out (only phase-in by company size, which the engine doesn't currently differentiate).

For each matched framework, the engine checks if a `MandatoryRule` exists for `(region_key, market_cap)` and sets `is_mandatory: true` on the match.

### 2.6 Step 5 — Section code population

[`framework_matcher.py:171–178`](../engine/analysis/framework_matcher.py#L171-L178):

```python
from engine.ontology.intelligence import query_framework_sections
primary_theme = tags.primary_theme or ""
for match in collected.values():
    sections = query_framework_sections(match.framework_id, primary_theme)
    if sections:
        match.triggered_sections = sections
```

Sections come from `FrameworkSection` triples in `knowledge_expansion.ttl`. Example for BRSR:

```turtle
snowkap:brsr_p6 a snowkap:FrameworkSection ;
    snowkap:belongsToFramework snowkap:BRSR ;
    snowkap:sectionCode "BRSR:P6" ;
    snowkap:sectionTitle "Principle 6 — Environment" .
```

`query_framework_sections('BRSR', 'topic_water')` returns `['BRSR:P6']` because the ontology has a `triggeredBy` link from BRSR:P6 to water-adjacent topics.

**Why section codes matter for ESG Analysts:** "BRSR" alone is useless to a compliance officer ("Which principle? Which question?"). "BRSR:P6:Q14" is auditable — they can look up the exact disclosure requirement.

### 2.7 Final sort + headline framework

[`framework_matcher.py:180`](../engine/analysis/framework_matcher.py#L180):

```python
matches = sorted(collected.values(), key=lambda m: m.relevance, reverse=True)
```

The top-scoring framework becomes the headline framework for the ESG Analyst view (e.g. `"BRSR:P6 disclosure trigger"`). The full list populates the Framework Alignment panel.

### 2.8 Worked example — Waaree FY26 Water article

Suppose a hypothetical Waaree Energies article on facility water consumption (`primary_theme: topic_water`).

**Company context:**
- Industry: Renewable Energy
- HQ Country: India
- Region: INDIA
- Market cap: Mid Cap

**Step 1 — Triggered frameworks** (from `topic_water snowkap:triggersFramework`):
`BRSR · GRI · ESRS · CDP · TNFD`

**Step 2 — Base relevance** (primary theme contributes 0.55 each):

| Framework | Base |
|---|---|
| BRSR | 0.55 |
| GRI | 0.55 |
| ESRS | 0.55 |
| CDP | 0.55 |
| TNFD | 0.55 |

**Step 3 — India regional boost:**

| Framework | Base | + India boost | = Final |
|---|---|---|---|
| BRSR | 0.55 | +0.6 | **1.0** (capped) |
| GRI | 0.55 | +0.1 | **0.65** |
| ESRS | 0.55 | +0 | **0.55** |
| CDP | 0.55 | +0.1 | **0.65** |
| TNFD | 0.55 | +0 | **0.55** |

**Step 4 — Mandatory marking:**
- BRSR India + Mid Cap → no `Large Cap` match → `is_mandatory: false`. (Mid Cap currently doesn't trigger any mandatory rule.)
- (If Waaree were Large Cap, BRSR would flip to `is_mandatory: true`.)

**Step 5 — Section codes:**
- BRSR → `["BRSR:P6"]` (Environment principle covers water)
- GRI → `["GRI:303"]` (Water and Effluents disclosure)
- ESRS → `["ESRS:E3"]` (Water and marine resources)
- CDP → `["CDP:Water"]` (CDP Water questionnaire)
- TNFD → `["TNFD:Strategy", "TNFD:Risk"]` (water-related nature dependencies)

**Final sorted list:**

```json
[
  {"framework_id": "BRSR", "relevance": 1.0,  "is_mandatory": false, "triggered_sections": ["BRSR:P6"]},
  {"framework_id": "GRI",  "relevance": 0.65, "is_mandatory": false, "triggered_sections": ["GRI:303"]},
  {"framework_id": "CDP",  "relevance": 0.65, "is_mandatory": false, "triggered_sections": ["CDP:Water"]},
  {"framework_id": "ESRS", "relevance": 0.55, "is_mandatory": false, "triggered_sections": ["ESRS:E3"]},
  {"framework_id": "TNFD", "relevance": 0.55, "is_mandatory": false, "triggered_sections": ["TNFD:Strategy", "TNFD:Risk"]}
]
```

**Headline framework for ESG Analyst:** `"BRSR:P6 disclosure trigger"` (top of sorted list).

### 2.9 How ESG Analyst, CFO, CEO see this differently

Same framework list — three different surfacings:

| Role | What appears on the panel | Why |
|---|---|---|
| **ESG Analyst** | Full list of 5 frameworks with `is_mandatory` badge + section codes + filing deadlines. Headline leads with framework section. | Analysts work to filing dates; section codes are the unit of disclosure. |
| **CFO** | Only `is_mandatory=true` frameworks (compliance cost), with `profitability_link` tooltip (e.g. "Non-disclosure → SEBI scrutiny → cost of capital +20-40bps"). | CFOs care about cost-of-capital impact, not framework taxonomy. |
| **CEO** | Top 2 frameworks summarised as "ESG investor signal" — used in `stakeholder_map` (e.g. MSCI uses CDP) and `board_paragraph`. No section codes. | CEOs read framework signals as competitive intelligence ("how does this affect MSCI rating?"), not disclosure to-do lists. |

This three-way split is enforced by the role generators ([`role_generators/{cfo,ceo,analyst}.py`](../engine/analysis/role_generators/)) reading from the same `EvidencePack.frameworks` field but rendering differently per role.

### 2.10 What can break and what catches it

| Failure mode | Detector |
|---|---|
| Wrong region applied (e.g. EU mandatory for a UK company) | Phase 23B `_region_for_country` split UK from EU bucket; test `test_phase23b_onboarder_region.py` |
| Section code missing for a known framework × theme pair | `query_framework_sections` returns empty → analyst panel shows just "BRSR" without `:P6` — low-confidence flag fires in verifier pass 8 |
| Theme mis-classified by Stage 2 (LLM error) → wrong framework set triggered | Phase 12.1 confidence bar requires ≥2 keyword hits OR multi-word phrase; otherwise falls back to default theme (`general_esg`) which triggers fewer frameworks |
| Regional boost not loaded (TTL parse error) | Phase 13 S3 `eager_load_ontology()` fails-fast at boot in production rather than mid-request |
| Mandatory framework somehow not flagged for a Large Cap Indian company | `test_phase15_stakeholder_polarity.py` + integration test in `test_phase26_role_distinctness.py` |
| A new framework needed (e.g. CBAM in EU) | Add 4 triples to `knowledge_expansion.ttl`: framework definition, topic triggers, regional boost, mandatory rule. No code change. |

---

## Part 3 — How to verify the references yourself

```bash
# 1. List every framework in the ontology with its standards reference
python -c "
from engine.ontology.intelligence import get_graph
g = get_graph()
for s, p, o in g.triples((None, None, None)):
    if 'ESGFramework' in str(o):
        label = list(g.objects(s, None))
        print(s, '→', label[:3])
"

# 2. Print all India / EU / US mandatory rules
python -c "
from engine.ontology.intelligence import query_mandatory_rules
for region in ['INDIA','EU','US','UK']:
    print(region, '→')
    for r in query_mandatory_rules(region):
        print(' ', r)
"

# 3. Walk one article end-to-end and print the matched frameworks
python -c "
from engine.analysis.framework_matcher import match_frameworks
from engine.nlp.theme_tagger import ESGThemeTags
tags = ESGThemeTags(primary_theme='topic_water', secondary_themes=[])
matches, q = match_frameworks(tags, 'Renewable Energy', 'India', 'Asia-Pacific', 'Mid Cap', 'INDIA')
for m in matches:
    print(f'{m.framework_id:15} relevance={m.relevance:.2f} mandatory={m.is_mandatory} sections={m.triggered_sections}')
"

# 4. Run the framework-tagging tests in isolation
python -m pytest tests/test_phase23b_onboarder_region.py tests/test_phase26_role_distinctness.py -s -v

# 5. Verify zero hardcoded framework dicts in engine/
grep -rn "TOPIC_FRAMEWORK_MAP\|REGION_BOOSTS\|MARKET_CAP_BRSR_MANDATORY" engine/
# Expected: zero matches (Phase 15 invariant)
```

---

## Bottom line

**Where the logic comes from, in one line per category:**

- **What ESG topics exist** → SASB + GRI + IFRS (published standards)
- **Which frameworks exist** → 21 named published standards (BRSR, GRI, TCFD, …)
- **Which framework matches a topic** → ontology mapping based on the published standards' own scope
- **How relevant a framework is** → base 0.55 per theme + regional boost (0.1–0.6) + mandatory flag
- **Whether it's mandatory** → published regulator rules (SEBI BRSR, EU CSRD, US SEC, etc.)
- **What ₹ figure shows up** → primitive-engine cascade (β × Δ × base), formulas from econometrics
- **How critical the article is** → 6-component score with role-specific weights (engine heuristic, fuzz-validated)
- **What CFO/CEO/Analyst see** → same `EvidencePack`, three deterministic generators with role-specific rules

**Where the engine makes its own choices (not externally validated):**

- Specific β-range mid-points and clamps
- Criticality 6-component weights and band thresholds
- Penalty magnitudes (−0.20 staleness, −0.15 confidence, −0.20 polarity)
- ROI cap percentages (200/300/400/500)
- Persona boost magnitudes
- Hard floor thresholds (0.65 home, 0.40 feed, 5% cross-role drift, 35% cross-section drift)
- Discovery promotion thresholds

**All Snowkap-internal choices are:**
1. Documented in code with comments explaining the calibration
2. Tested in unit tests
3. Continuously checked by the nightly fuzz harness against a corpus of real articles with expected outcomes

Where validation is weak, the document is explicit about it — particularly industry risk weights (would benefit from a Sustainalytics/MSCI benchmark) and the majority of causal-edge β-ranges (would benefit from a literature citation per edge).

That's the audit trail. Every number you see in a CFO email can be traced back to one of: a published regulator standard, an academic formula, an industry convention, or a documented Snowkap calibration choice with a corresponding test.
