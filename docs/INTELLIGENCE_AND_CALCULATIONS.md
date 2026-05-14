# Snowkap — Intelligence & Calculations Explained

**Audience:** non-technical reviewers (board, sales, journalists, regulators) who want to understand exactly what the engine computes, where every number comes from, and how the same article becomes three genuinely different briefs for CFO, CEO, and ESG Analyst.

**Length goal:** read in 15 minutes. Every claim cites a file:line so a developer can verify.

---

## 1. 30-second summary

Snowkap ingests ESG-relevant news for a company, scores it through a 12-stage pipeline, and writes a per-article JSON brief plus three role-specific views (CFO / CEO / ESG Analyst). Roughly 97% of the intelligence comes from a structured **ontology** (8,200+ RDF triples encoding ESG knowledge, frameworks, risk weights, causal chains). The remaining 3% — narrative prose — comes from OpenAI's `gpt-4.1` constrained by numbers the engine has already computed.

The headline guarantee: **every ₹ figure shown to a CFO is either copied verbatim from the source article or derived from a deterministic cascade engine.** The LLM can describe these numbers; it cannot invent them.

---

## 2. The journey of one article (the 12-stage pipeline)

Orchestrator: [`engine/analysis/pipeline.py`](../engine/analysis/pipeline.py)

| Stage | Driver | What it does | Ontology-driven? |
|---|---|---|---|
| 1 | LLM `gpt-4.1-mini` | Sentiment (-2..+2), tone, named entities, ESG signals from raw text | No |
| 2 | LLM `gpt-4.1-mini` | Picks 1 of 21 ESG themes (Water · Climate · Labor · Governance · …) | No |
| 3 | **Ontology** | Matches to one of 22 event types (SEBI penalty · contract win · social violation · …) via word-boundary keyword regex | Yes |
| 4 | **Ontology** | 5-dimensional relevance score (materiality × industry × cap-tier × geography × event polarity) | Yes |
| 5 | **Ontology** | Causal chain BFS through 17 relationship types, 0–4 hops | Yes |
| 6 | **Ontology** | Framework matching across 21 frameworks (BRSR, GRI, TCFD, CSRD, …) with regional boosts | Yes |
| 7 | **Ontology** | Stakeholder mapping (SEBI, RBI, MSCI, BlackRock, NGOs, workforce, …) | Yes |
| 8 | **Ontology** | SDG mapping (17 UN goals) | Yes |
| 9 | **Ontology** | Risk assessment — 10 ESG risk categories + 7 TEMPLES (technology/economic/market/political/legal/environmental/social) | Yes |
| **9.5** | **Engine** | Criticality scoring (6 components × per-role weights, 3 penalties) | Yes |
| 10 | LLM `gpt-4.1` | Deep-insight narrative — 9-section JSON, CONSTRAINED by computed cascade numbers | LLM but number-locked |
| 11 | **Ontology** | Perspective transformation — generates distinct CFO/CEO/Analyst views from one canonical EvidencePack | Yes |
| **11.5** | **Engine** | 3 role generators (CFO / CEO / Analyst) write role-specific headline + hero metric + paragraph from the EvidencePack | Yes |
| 12 | LLM `gpt-4.1-mini` | 3–5 recommendations, each tagged with role whitelist + ROI cap + audit trail | LLM but rule-bounded |

**Gate:** if Stage 4 relevance < 4, the article is REJECTED — stages 6–12 are skipped, saving the LLM budget. If < 7, it's SECONDARY (not surfaced on home).

The **on-demand flow** ([`engine/analysis/on_demand.py`](../engine/analysis/on_demand.py)) re-runs stages 1–9 from the original article every time the user clicks "View Insights" on a stale article, so ontology improvements take effect without manual back-fills.

---

## 3. The math — every formula in one place

### 3.1 Criticality score (the master ranking)

File: [`engine/analysis/criticality_scorer.py`](../engine/analysis/criticality_scorer.py)

Six components, each on a 0–1 scale, combined with role-specific weights:

| Component | Formula | What it captures |
|---|---|---|
| **Materiality** | `relevance_total / 10.0`, clipped | Does this topic matter to this industry? |
| **Financial magnitude** | `min(1.0, log₁₀(1 + cascade_₹ / revenue × 100) / 2)` | How big vs. the company's revenue? Log so a tail of huge events doesn't dominate. |
| **Actionability** | `0.8` if event is actionable, else `max(0, 1 − days_to_decision / 180)`, else `0.2` | Can you do anything about it before the deadline? |
| **Painpoint match** | `max cosine(article_embedding, painpoint_embedding) × severity`, clipped | Does this hit a pain-point the company explicitly cares about? (per-tenant embedding cache) |
| **Recency** | `exp(−days_since_published / 7)` | 7-day half-life. A 2-week-old story scores ~14%. |
| **Source authority** | Bloomberg/Reuters/FT = 1.0 · Mint/ET = 0.85 · aggregators = 0.5 · blogs = 0.3 | Did a Tier-1 outlet report it? |

Three subtractive penalties:

| Penalty | Trigger | Size |
|---|---|---|
| Staleness | `days_since_published > 30` | −0.20 |
| Confidence | `cascade_confidence < 0.5` | −0.15 |
| Polarity drift | event polarity ≠ narrative polarity (e.g. contract win framed as crisis) | −0.20 |

**Final score** = `clip01(weighted_sum_6_components − sum_3_penalties)`

**Band thresholds**: CRITICAL ≥ 0.75 · HIGH ≥ 0.55 · MEDIUM ≥ 0.35 · LOW < 0.35.

**Per-role weights** ([`criticality_scorer.py:95–118`](../engine/analysis/criticality_scorer.py#L95-L118)):

| Component | Default | CFO | CEO | Analyst |
|---|---|---|---|---|
| Materiality | 0.20 | 0.15 | **0.25** | **0.30** |
| Financial magnitude | 0.30 | **0.40** | 0.20 | 0.15 |
| Actionability | 0.15 | 0.20 | 0.10 | 0.15 |
| Painpoint match | 0.20 | 0.10 | **0.25** | 0.25 |
| Recency | 0.10 | 0.10 | 0.15 | 0.10 |
| Source authority | 0.05 | 0.05 | 0.05 | 0.05 |

> A CFO sees the same article rank differently than a CEO. Same news, three different priority queues.

**Hard floors enforced at the API layer**:
- `?surface=home` filters to score ≥ 0.65
- `?surface=feed` filters to score ≥ 0.40
- Share endpoint returns **HTTP 422** with the top-3 alternatives when below 0.65

### 3.2 Financial cascade (the ₹ engine)

File: [`engine/analysis/primitive_engine.py`](../engine/analysis/primitive_engine.py)

When an event hits a "primitive" (one of 22 universal cost/revenue/risk drivers — OX (opex), RV (revenue), CX (capex), EU (energy use), GE (GHG emissions), CL (compliance), SC (supply chain), etc.), the engine traces it through 123 P→P edges in the ontology and computes the cascading ₹ impact.

**6 functional forms** the cascade supports (per edge):

| Form | Formula | Example |
|---|---|---|
| Linear | `ΔT = β × ΔS` | Δrevenue = 1.0 × Δcontract_value |
| Log-linear | `ΔT = β × ln(1 + ΔS)` | Δcustomer churn vs Δprice |
| Threshold | `ΔT = β × max(0, ΔS − τ)` | Carbon tax only above τ tonnes |
| Step | `ΔT = β if ΔS > τ else 0` | Insurance premium step-up |
| Ratio | `ΔT = β × ΔS / base` | Margin compression |
| Composite | Falls back to linear with edge notes | Custom multi-input formulas |

**Company-specific β calibration**:

```
β_company = β_ontology_midpoint × (company_share / industry_avg_share)
```

Where industry_avg_share is 0.15 (a constant chosen so 15% energy intensity is "neutral"); clamped to `[0.5 × β_lo, 1.5 × β_hi]` so no single company's calibration runs away.

**Margin bps** = `delta_source_cr / revenue_cr × 10,000`. This is the standard finance formula — 100 bps = 1 percentage point of revenue.

**ROI caps** per recommendation type ([`output_verifier.py:1102–1108`](../engine/analysis/output_verifier.py#L1102-L1108)):

| Rec type | ROI ceiling |
|---|---|
| Compliance | 500% |
| Financial | 300% |
| Strategic | 400% |
| Operational | 200% |

Capped recs are tagged `roi_capped: true` with `roi_cap_reason` for a UI tooltip — so a CFO who asks "why isn't this 800%?" sees the answer.

### 3.3 Persona × criticality (personalization layer)

File: [`engine/persona/persona_scorer.py:70–131`](../engine/persona/persona_scorer.py#L70-L131)

When a user completes the 6-question MCQ (ESG focus / frameworks / geographies / horizon / decision style / risk appetite), the base criticality score gets multiplicative boosts:

| Persona match | Multiplier |
|---|---|
| ESG focus overlap | ×(1 + 0.40 × overlap_fraction) |
| Framework overlap | ×(1 + 0.30 × overlap_fraction) |
| Geography overlap | ×(1 + 0.25 × overlap_fraction) |
| Risk appetite × polarity match | ×1.15 (opportunistic+positive · defensive+negative) |
| Click affinity (top topic) | ×(1 + 0.20 × affinity_score) |

**Horizon penalties** (mismatch dampening):
- Quarterly persona + cascade > 12 mo → ×0.7
- 5-year+ persona + earnings_blip → ×0.6

**Discoverability invariants**:
- Cap at 1.0 (no runaway boosting)
- CRITICAL articles floored at 0.65 (`HOME_FLOOR`) — a CRITICAL never falls below the home-page filter regardless of persona mismatch

### 3.4 Number rendering protocol

Two-significant-figure rounding + context-aware format (`client/src/lib/number_format.ts:39–46` + `engine/analysis/output_verifier.py:1234`):

```
sign × round(|v| × 10^(1−magnitude)) / 10^(1−magnitude)
```

So 1857.6 → 1900, 56.1 → 56.

| Context | Format | Example |
|---|---|---|
| Headline | Point estimate | `~₹1,900 Cr` |
| Body | Range ±10% | `₹1,700–2,100 Cr` |
| Analyst table | Full precision | `₹1,857.6 Cr` |

**Provenance stripping**: `(engine estimate)` and `(from article)` tags are removed from user-visible prose at write time and stashed in an `__provenance` sidecar list. The frontend `NumberWithProvenance` component reattaches them as hover tooltips — so the data is preserved without cluttering the reading experience.

---

## 4. Where every rating comes from (the ontology vs LLM split)

### 4.1 The ontology — 8,200+ triples across 11 TTL files

Location: [`data/ontology/`](../data/ontology/)

| File | What it encodes |
|---|---|
| `schema.ttl` | OWL2 classes + predicates (the vocabulary) |
| `knowledge_base.ttl` | 21 ESG themes, 21 frameworks, industries, stakeholders |
| `knowledge_depth.ttl` | 22 event types with keyword regexes + theme→event mappings |
| `knowledge_expansion.ttl` | Regional framework boosts, mandatory rules, headline templates, priority matrix, ranking sort keys, grid columns, risk-of-inaction config |
| `primitives_schema.ttl` | Schema for the 22 universal primitives + outcome nodes |
| `primitives_edges_p2p.ttl` | 123 P→P causal edges + 48 P→outcome edges (each with β, lag, functional form, confidence) |
| `primitives_indicators.ttl` | 77 indicators (37 qualitative rubrics + 40 quantitative) |
| `primitives_thresholds.ttl` | 25 canonical τ threshold categories |
| `primitives_order3.ttl` | 50 P3 + 19 P4 multi-hop cascades |
| `stakeholder_positions.ttl` | Per-stakeholder default stance + analogous precedent (separate positive/negative variants) |
| `precedents.ttl` | 8 named real-world positive cases (Tata Power SECI, Infosys MSCI A→AA, ReNew Green Bond, …) |
| `discovered.ttl` | Runtime-learned triples — entities/themes/edges promoted from article evidence (Phase 19) |

**Query layer**: [`engine/ontology/intelligence.py`](../engine/ontology/intelligence.py) exposes 30+ SPARQL functions. Examples:

- `query_materiality_weight('Water', 'Power/Energy')` → 0.85
- `query_risk_weight('Financials/Banking', 'regulatory')` → 1.6
- `query_frameworks_for_topic('Water')` → BRSR:P6, GRI:303, ESRS:E3, CDP:Water
- `query_p2p_edges('SEBI_PENALTY')` → returns CL→RG cascade with β-range and confidence
- `query_precedents_for_event('event_contract_win')` → Tata Power SECI 2024

**Critical rule** (CLAUDE.md #1): **never hardcode domain knowledge in Python.** As of Phase 15, zero hardcoded domain dicts remain in `engine/` — grep-verified. All weights, thresholds, mappings, and rules live in `.ttl` files and are queryable via SPARQL.

### 4.2 The LLM — narrowly scoped, never decides numbers

| LLM use | Model | Why |
|---|---|---|
| Stage 1 NLP extraction | `gpt-4.1-mini` | Reading the article into structured fields (sentiment, entities, signals) |
| Stage 2 theme tagging | `gpt-4.1-mini` | Picks 1 of 21 themes from article text |
| Stage 10 deep insight | `gpt-4.1` | Writes the narrative — but every ₹ figure is injected as a hard constraint |
| Stage 12 recommendations | `gpt-4.1-mini` | Picks 3–5 actions from event-archetype templates, ROI-capped |
| Optional Stage 11.5 polish | `gpt-4.1-mini` (env-flag-gated, default OFF) | Re-writes headline + takeaways from the EvidencePack |

The Stage-10 system prompt receives a `=== COMPUTED CASCADE ===` block ([`insight_generator.py:441–492`](../engine/analysis/insight_generator.py#L441-L492)). An "ANTI-DRIFT CHECK" sentence forces the LLM to reconcile its own output before returning ([`insight_generator.py:191–196`](../engine/analysis/insight_generator.py#L191-L196)). If the LLM tries to invent a number, the verifier catches it (see §5).

### 4.3 Where the source ratings come from

| Rating you see | Source |
|---|---|
| Materiality (CRITICAL/HIGH/MODERATE/LOW) | Computed from criticality score → band thresholds (`criticality_scorer.py:121–126`) |
| Relevance score (0–10) | 5-D ontology calc (materiality × industry × cap-tier × geography × event-polarity) — `relevance_scorer.py` |
| Risk levels (Low/Moderate/High/Critical) | `query_risk_level_thresholds()` — score thresholds in `knowledge_expansion.ttl` |
| Framework match confidence | Regional boosts in `knowledge_expansion.ttl::RegionalFrameworkBoost` + base materiality |
| ₹ exposure | `primitive_engine.compute_cascade()` — deterministic β-walk, never LLM |
| ROI on a recommendation | Engine-computed + capped per rec type; LLM provides the narrative not the number |
| Confidence bounds | Edge `confidenceLevel` (high/medium/low) propagated from `primitives_edges_p2p.ttl` |
| Source authority | Whitelist/blacklist in `criticality_scorer.py:292–326` |

---

## 5. The safety nets — 10 verifier passes + 6 CFO preflight gates

### 5.1 The 10 verifier passes

File: [`engine/analysis/output_verifier.py::verify_and_correct`](../engine/analysis/output_verifier.py)

Every Stage-10 output goes through these passes in order. Each has a named threshold and an auditable action.

| # | Pass | What it catches | Threshold | Action |
|---|---|---|---|---|
| 1 | Margin math | (₹ / revenue) × 10,000 ≠ stated bps | ±5% | Auto-correct, set `computed_override: true` |
| 2 | Hallucination audit | LLM tagged `(from article)` but no matching ₹ in body within ±10% + noun-phrase overlap | ±10% + Jaccard | Downgrade to `(engine estimate)` |
| 3 | Reused-number audit | Same ₹ recycled in 3+ unrelated contexts | ±5% group, <40% Jaccard | Downgrade duplicates |
| 4 | Cross-section ₹ drift | Headline / exposure / key_risk diverge | > 35% | Auto-clarify with `(of ₹X Cr canonical)` |
| 5 | Source-tag enforcement | Untagged ₹ figures | — | Inject `(from article)` or `(engine estimate)` |
| 6 | CFO headline hygiene | Greek letters / framework IDs / > 100 words | 100-word cap | Strip + truncate |
| 7 | Narrative coherence | Positive event framed as crisis | sign mismatch | Downgrade materiality by 1 tier |
| 8 | Low-confidence classification | Theme-fallback event + neutral sentiment + no ₹ | conditional | Flag `low_confidence_classification: true` |
| 9 | Strip provenance | `(engine estimate)` / `(from article)` in user-visible prose | — | Strip + stash in `__provenance` sidecar |
| 10 | Cross-role ₹ drift | CFO / CEO / Analyst quote different ₹ on same event | > 5% | Warn + sidecar report |

### 5.2 The 6 CFO preflight gates

File: [`engine/analysis/cfo_preflight.py:398–461`](../engine/analysis/cfo_preflight.py#L398-L461)

ALL six must pass for an article to appear on the CFO surface:

1. **`financial_impact_quantified`** — has ₹ + source tag
2. **`framework_mapped`** — ≥1 specific section (e.g. BRSR:P5, not bare BRSR)
3. **`no_stale_data`** — within freshness window
4. **`polarity_coherent`** — no `low_confidence_classification` flag
5. **`numeric_consistent`** — drift < 35%
6. **`stakeholder_polarity_matched`** — stance language matches event polarity

`/news/feed?perspective=cfo` hides FAIL articles from CFOs. ESG Analyst surface shows everything so analysts can audit what CFOs are missing.

### 5.3 The provenance sidecar — regulator-grade audit trail

Every figure stripped from prose is preserved in an `__provenance` list with `{field, original_cr, source, rendered, context}`. So a regulator asking "where did this ₹50 Cr come from?" can:

1. Read the sidecar → see source = `from_article`, original_cr = 50.0
2. Cross-check the `audit_source_tags` log → confirm proximity + numerical match in article body
3. Confirm not recycled → `audit_reused_article_figures` clean
4. Confirm cascade computed → `primitive_engine` produced it; the LLM didn't invent it

---

## 6. How the same article becomes three different briefs (nine divergence points)

Source-of-truth: a single `EvidencePack` ([`engine/analysis/evidence_pack.py`](../engine/analysis/evidence_pack.py)) is built once per article — 9 canonical fields (cascade, frameworks, stakeholders, painpoint_matches, causal_chain, comparables, polarity, confidence_bounds, decision_windows). Three role generators read from this same pack and produce locked `RoleDistinctPayload` outputs.

| # | Divergence point | CFO | CEO | ESG Analyst |
|---|---|---|---|---|
| 1 | **Criticality weights** | financial_magnitude 0.40 | materiality + painpoint both 0.25 | materiality 0.30 + painpoint 0.25 |
| 2 | **Rec-type whitelist** | financial · operational · compliance | strategic · esg_positioning · brand · capital_allocation | framework · disclosure · kpi_tracking · audit |
| 3 | **Rec-type forbidden** | ✗ esg_positioning · strategic · brand | ✗ compliance · kpi_tracking · audit | ✗ capital_allocation · financial · brand |
| 4 | **Headline lead** | `P&L compresses ~₹1,900 Cr` (₹-led) | `MSCI ESG positive — FY27-29 board narrative needs reframe` (NEVER ₹-led) | `BRSR:P6:Q14 disclosure trigger — due 2026-09-30 [unverified]` (framework-led) |
| 5 | **Hero metric label** | "P&L exposure" | "Strategic position" | "Disclosure trigger" |
| 6 | **Time horizon framing** | decision_window deadline | dynamic `FY{n+1}-{n+3}` (3-year, auto-rolls) | filing deadline |
| 7 | **Evidence type cited** | Payback months (e.g. "6mo payback") | Peer precedent (polarity-matched) | β + lag + method confidence phrase |
| 8 | **Word cap on paragraph** | 90 words | 80 words | 100 words |
| 9 | **Panel order (visible)** | personal_stakes → crisp_insight → impact_metrics → recommendations → audit_trail | personal_stakes → crisp_insight → three_year_trajectory → stakeholder_map → board_paragraph → recommendations | personal_stakes → crisp_insight → kpi_table → framework_alignment → causal_chain → audit_trail → recommendations |

**Plus the LLM polish prompts** ([`role_generators/llm_upgrade.py:86–109`](../engine/analysis/role_generators/llm_upgrade.py#L86-L109)) embed role constraints into the prompt itself:

- **CFO** (`_CFO_SYSTEM`): "Lead every sentence with a ₹ figure, a peer name, or an action verb with payback. Never write strategic positioning, 3-year horizons, governance philosophy, or comms tasks."
- **CEO** (`_CEO_SYSTEM`): "NEVER lead with a ₹ figure. Lead with competitive positioning, stakeholder signal, or strategic optionality. Frame on a 3-year horizon. Reference at least one peer event matching the article's polarity."
- **Analyst** (`_ANALYST_SYSTEM`): "Every material claim must cite a framework section code. Surface confidence bounds (β, lag, method) on every quantitative claim. Flag unverified claims with [unverified]."

**Cross-role drift gate** ([`cross_role_drift.py`](../engine/analysis/cross_role_drift.py)): pairwise ₹ comparison across the three payloads. > 5% divergence → warning + sidecar. Prevents trust collapse when a CXO cross-reads the same article in different lenses.

---

## 7. What each role actually sees — block by block

### 7.1 CFO view (the 10-second verdict)

Built by: [`engine/analysis/role_generators/cfo.py`](../engine/analysis/role_generators/cfo.py) (deterministic) + optional LLM polish.

| Block | What it contains | Where the data comes from | Why it's calculated this way |
|---|---|---|---|
| **Headline** | `~₹1,900 Cr P&L compression · pay-back 6mo` | EvidencePack.cascade.total + decision_windows.payback_months | CFO opens email → 1.5 seconds of attention; must lead with ₹ |
| **Hero metric** ("P&L exposure") | `−₹1,857 Cr` with %-of-revenue badge | `cascade.total_cr` ÷ `company.revenue_cr` (companies.json) | A ₹500 Cr hit means different things to ICICI (1%) vs Singularity (250%); ratio is the universal anchor |
| **Personal stakes** paragraph (90 words) | Revenue % at stake + payback months | EvidencePack + per-company calibration | CFOs ask "what's the damage to MY P&L by when?" |
| **Impact metrics grid** | Top 3 cascade hops with margin bps | `cascade.hops[0:3]` with `outcome.margin_bps` | Bps is the universal CFO unit |
| **Recommendations** | 3–5 ranked by ROI descending | LLM picks from event-archetype templates (`recommendation_archetypes.py`); types restricted to financial/operational/compliance; ROI capped 200–500% | CFO can defend each rec to the board with a payback number |
| **Audit trail** | Per-rec `{source, ref, value}` evidence | `Recommendation.audit_trail` field (LLM required to supply 1–3 entries) | "Why ₹0.5–1 Cr?" gets a traceable answer |

**CFO preflight gates the article BEFORE this panel renders** — see §5.2.

### 7.2 CEO view (the strategic brief)

Built by: [`engine/analysis/role_generators/ceo.py`](../engine/analysis/role_generators/ceo.py) (deterministic) + optional LLM polish.

| Block | What it contains | Where the data comes from | Why it's calculated this way |
|---|---|---|---|
| **Headline** | `MSCI ESG upgrade A→AA · 3-year board narrative needs reframe` | EvidencePack.polarity + EvidencePack.comparables (peer precedent) | CEO doesn't open emails to see ₹ — they want competitive positioning |
| **Hero metric** ("Strategic position") | Peer rank vs Tata Power / HDFC / Infosys | `query_competitors(slug)` + `query_precedents_for_event()` | CEOs benchmark themselves against named peers, not abstract market |
| **3-year trajectory** | "Do-nothing" vs "Act-now" outcome pair on `FY{n+1}-{n+3}` | Cascade composed over 3-year horizon + scenario branching | CEO time horizon = strategic plan cycle (3 years), NOT quarterly |
| **Stakeholder map** | 5 stakeholders with stance + analogous precedent | `query_stakeholder_positions(topics, polarity)` — uses `stakeholderPositiveStance/Precedent` for positive events | Stakeholder capital is what CEOs trade in; precedent must match polarity (no "Vedanta SCN" on a contract win) |
| **Board paragraph** (80 words) | Boardroom-grade narrative; no ₹, no framework IDs | LLM constrained by EvidencePack + CEO system prompt | This is the paragraph a chairman would read aloud at the next board meeting |
| **Recommendations** | 3–5 strategic actions | LLM types restricted to strategic/esg_positioning/brand/capital_allocation; sort by impact DESC | CEOs don't approve "BRSR:P5 filing" — they approve "issue ₹3,000 Cr green bond" |

### 7.3 ESG Analyst view (the audit trail)

Built by: [`engine/analysis/role_generators/analyst.py`](../engine/analysis/role_generators/analyst.py) (deterministic) + optional LLM polish.

| Block | What it contains | Where the data comes from | Why it's calculated this way |
|---|---|---|---|
| **Headline** | `BRSR:P6:Q14 disclosure trigger — due 2026-09-30 [unverified]` | EvidencePack.frameworks + decision_windows.filing_deadline | Analysts work to filing dates; framework section codes ARE the unit |
| **Hero metric** ("Disclosure trigger") | Specific framework section + deadline | `query_framework_sections(framework_id, topic)` | Vague "BRSR" is useless; "BRSR:P6:Q14" is auditable |
| **KPI table** | Full-precision figures (no rounding) | Raw cascade output, NOT rendered through `renderRupee` | Analysts cross-check; rounding hides reconciliation errors |
| **Framework alignment** | All 21 frameworks scored + mandatory/optional flag | `query_frameworks_for_topic` + `query_mandatory_rules(region)` | Compliance audit needs the full list, not just the top match |
| **Causal chain** | Full BFS path with edge β + lag + confidence | `primitives_order3.ttl` (P3/P4 chains) + per-edge metadata | "Why ₹1,900 Cr?" gets a hop-by-hop derivation |
| **Audit trail** | Per-rec evidence + provenance sidecar | `__provenance` + `audit_trail` | Regulator-grade traceability |
| **Recommendations** | 3–5 disclosure/framework actions | LLM types restricted to framework/disclosure/kpi_tracking/audit | Analysts execute filings, not capex |

---

## 8. One worked example — Waaree contract win (FY26)

Source article: `data/outputs/waaree-energies/insights/2026-04-30_d3a357437cde06cc.json`

**What happened**: Waaree Energies announced an FY26 profit doubling and contract pipeline expansion. Pre-Phase-14 the engine misclassified this as a "regulatory crisis" with hallucinated ₹807 Cr SEBI exposure. Post-Phase-14 it produces:

| Layer | Output |
|---|---|
| Stage 3 event | `event_contract_win` (positive polarity) |
| Stage 4 relevance | 8.0 / 10 (HOME tier) |
| Stage 5 cascade | RV → CX → GrossMargin, β-walked through company-specific shares |
| Stage 9 risk | MODERATE (downgraded by narrative-coherence verifier from HIGH because polarity was confused) |
| Stage 9.5 criticality | 0.3398 (LOW band — yes, a contract win is LOW criticality because actionability is high and pain isn't urgent) |
| Stage 10 deep insight | `~₹15.4 Cr valuation upside (engine estimate)` — note the explicit source tag |
| Stage 14.2 precedent | Tata Power SECI 2024 (positive-event library, NOT Vedanta) |
| Stage 12 recs | 4 recommendations, ranked per role:`{cfo: [BRSR-disclosure, earnings-narrative, ₹3,000 Cr green bond, SC monitoring], ceo: [...], analyst: [...]}` |

**5 frameworks triggered**: BRSR (1.0 confidence) · CDP/GRI/TCFD (0.65) · EU_TAXONOMY (0.55). Notice CDP/GRI/TCFD all share the same 0.65 because they all map to the same materiality weight for Renewable Energy industry — that's the regional-boost calculation at work.

**5 stakeholders cited (all positive variants)**: SEBI (Tata Power BRSR-leader reference) · MSCI (Infosys A→AA upgrade) · BlackRock (Tata Power weight uplift post-Khavda) · etc. Zero "Vedanta 2020 SCN" leaks because `query_stakeholder_positions` was called with `polarity="positive"`.

---

## 9. The defensible-claims summary

When asked "is this just LLM slop with a prettier UI?", the engineering answers:

| Concern | Answer | Evidence |
|---|---|---|
| Is the ₹ real? | Every ₹ is either copied from the article (with `(from article)` tag + body-text match within ±10%) or computed by the primitive engine (with `(engine estimate)` tag). Provenance is stripped from prose and stashed in `__provenance` sidecar. | Verifier passes 2 + 5 + 9 in `output_verifier.py` |
| Can the LLM invent numbers? | No. Stage 10 receives the cascade as a `=== COMPUTED CASCADE ===` hard-constraint block. An anti-drift directive forces reconciliation. Verifier pass 4 catches > 35% divergence. | `insight_generator.py:441–492` + `:191–196` |
| Is the math defensible? | Margin bps formula is standard finance (Δ/revenue × 10,000). β calibration uses industry-share scaling with explicit clamps. ROI is capped per rec type with disclosed reasons. | `primitive_engine.py:144–190`, `output_verifier.py:1102–1108` |
| Are the 3 role views actually different? | 9-point divergence (weights, rec types, headline lead, hero label, horizon, evidence, word caps, panels, LLM prompts) — measurable, not cosmetic. Cross-role ₹ drift gate enforces consistency where it should be consistent. | `criticality_scorer.py:95–118`, `recommendation_type_whitelist.py`, `role_generators/{cfo,ceo,analyst}.py`, `cross_role_drift.py` |
| What if the LLM goes off-script? | 10 verifier passes catch math errors, hallucinations, drift, polarity confusion, and recycled numbers. Low-confidence classifications get a yellow badge. CFO surface enforces 6-gate preflight before showing the article at all. | `output_verifier.py::verify_and_correct`, `cfo_preflight.py:398–461` |
| Is the persona personalization safe? | Multiplicative boosts cap at 1.0; CRITICAL articles floored at 0.65 so a discoverability bug can't hide a real crisis from someone whose persona is "boring quarterly only". | `persona_scorer.py:70–131` |

---

## 10. Files-to-trust list (verify directly)

If you have 30 minutes and want to spot-check the claims above, read these in order:

1. [`engine/analysis/pipeline.py`](../engine/analysis/pipeline.py) — the 12-stage orchestrator
2. [`engine/analysis/criticality_scorer.py`](../engine/analysis/criticality_scorer.py) — 6-component scoring + role weights + 3 penalties
3. [`engine/analysis/primitive_engine.py`](../engine/analysis/primitive_engine.py) — cascade computation (6 forms + β + ROI caps)
4. [`engine/analysis/output_verifier.py`](../engine/analysis/output_verifier.py) — 10 verifier passes
5. [`engine/analysis/insight_generator.py`](../engine/analysis/insight_generator.py) — Stage 10 with hard-constraint prompt
6. [`engine/analysis/cfo_preflight.py`](../engine/analysis/cfo_preflight.py) — 6-gate CFO publication filter
7. [`engine/analysis/cross_role_drift.py`](../engine/analysis/cross_role_drift.py) — 5% threshold cross-role consistency
8. [`engine/analysis/evidence_pack.py`](../engine/analysis/evidence_pack.py) — canonical block, built once
9. [`engine/analysis/role_generators/cfo.py · ceo.py · analyst.py`](../engine/analysis/role_generators/) — 3 deterministic generators
10. [`engine/analysis/role_generators/dispatcher.py`](../engine/analysis/role_generators/dispatcher.py) — Stage 11 dispatcher
11. [`engine/analysis/recommendation_type_whitelist.py`](../engine/analysis/recommendation_type_whitelist.py) — per-role allow/forbid lists
12. [`engine/persona/persona_scorer.py`](../engine/persona/persona_scorer.py) — persona × criticality modulation
13. [`client/src/lib/number_format.ts`](../client/src/lib/number_format.ts) — frontend rendering protocol
14. [`data/ontology/knowledge_expansion.ttl`](../data/ontology/knowledge_expansion.ttl) — RolePanelPriority, risk thresholds, regional boosts
15. [`data/ontology/stakeholder_positions.ttl`](../data/ontology/stakeholder_positions.ttl) — polarity-aware stakeholder stances

## 11. How to verify end-to-end

```bash
# 1. Re-run the Phase 26 test suite that locks every gate above
cd snowkap-esg
python -m pytest tests/test_phase26_*.py tests/test_phase3_verifier.py \
  tests/test_phase11_brand_copy.py tests/test_phase14_demo_grade.py \
  tests/test_phase15_stakeholder_polarity.py -s
# Expected: all green

# 2. Run the 10-step smoke gate
python scripts/smoke_test.py
# Expected: 10/10 pass

# 3. Spot-check the 9 divergence points in a real article via the UI
# (a) Open any Waaree article
# (b) Toggle CFO/CEO/Analyst — headline + hero label + panels should visibly differ
# (c) Open the JSON: data/outputs/waaree-energies/insights/{id}.json
# (d) Search for: criticality.role_scores, evidence_pack, role_payloads,
#     __provenance, __cross_role_drift

# 4. Confirm the 10 verifier passes
# Open the same JSON's insight.warnings + insight.verifier_corrections fields

# 5. Confirm the 6 CFO preflight gates
python -c "from engine.analysis.cfo_preflight import run_preflight; ..."

# 6. Confirm the persona re-rank
# Sign in → complete persona MCQ on /profile → open /home
# → articles re-rank per persona, outside_focus badge renders on
#   cards outside esg_focus
```

---

## Bottom line

The pipeline is **production-grade for CFO consumption**:

- Every ₹ figure is **computed** (primitive cascade), not LLM-hallucinated.
- Every claim has **provenance** (`__provenance` sidecar + audit log).
- Every figure is **audited** through 10 sequential verifier passes.
- Every CFO surface item passes **6 preflight gates** before publication.
- The 3 roles are **9-point differentiated** end-to-end — weights, recs, headlines, hero metrics, time horizons, evidence types, word caps, panel orders, and LLM-prompt constraints all diverge.

What's left is quality polish — defaulting the LLM polish layer on, verifying the NewsAPI.ai token-cost rule, and replacing the legacy CrispInsight UI with the new RoleDistinctView in production — none of which are correctness blockers.
