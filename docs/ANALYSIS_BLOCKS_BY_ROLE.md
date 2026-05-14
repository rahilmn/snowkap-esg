# Analysis Blocks by Role — What, Why, How Prioritised

**Audience:** product, sales, customer success, regulators, the curious CFO.
**Companion to:** [PRD.md](./PRD.md).

This document explains, for every block that appears in a Snowkap analysis output, **what** the block contains, **why** it matters to each role, and **how** it's prioritised in that role's view.

---

## 1. The shape of an analysis output

Every analysed article produces one JSON insight file at `data/outputs/{slug}/insights/{date}_{id}.json`. Top-level keys:

```
{article, pipeline, insight, recommendations, perspectives,
 evidence_pack,            # canonical structured block, Phase 26
 role_payloads,            # {cfo, ceo, esg-analyst} RoleDistinctPayloads
 __provenance,             # sidecar — every stripped (from article)/(engine estimate) tag
 __cross_role_drift,       # 5%-threshold consistency report
 meta: {schema_version: "2.1-role-distinct", written_at}}
```

The **same data** powers three role-specific UI surfaces. The role determines (a) which blocks render, (b) the order they render in, (c) the headline lead, (d) the hero metric label, (e) the word cap on prose, and (f) which recommendation types are allowed through.

The five things that **never** differ across roles:

- the `article` block (source URL, title, published_at)
- the `pipeline` block (event_id, themes, risk matrix)
- the `evidence_pack` (computed cascade, frameworks triggered, β / lag / method)
- the underlying ₹ figures (cross-role drift is gated at 5%)
- the framework section codes (BRSR:P6:Q14 is BRSR:P6:Q14 for everyone)

---

## 2. Prioritisation — the three levers

### 2.1 Criticality scoring (which articles make it to each role)

Six components × per-role weights → 0.0–1.0 score → 4 bands.

| Component | CFO weight | CEO weight | Analyst weight |
|---|---|---|---|
| materiality | 0.15 | **0.25** | **0.30** |
| financial_magnitude | **0.40** | 0.20 | 0.15 |
| actionability | 0.20 | 0.10 | 0.15 |
| painpoint_match | 0.10 | **0.25** | 0.25 |
| recency | 0.10 | 0.15 | 0.10 |
| source_authority | 0.05 | 0.05 | 0.05 |

Plus three subtractive penalties: staleness (−0.2 if > 30d old), confidence (−0.15 if cascade confidence < 0.5), polarity drift (−0.2 if event polarity disagrees with narrative polarity).

**Bands:** CRITICAL ≥ 0.75 · HIGH ≥ 0.55 · MEDIUM ≥ 0.35 · LOW < 0.35.

**Discoverability floors:**
- `/home` filters to ≥ 0.65 (or CRITICAL).
- `/feed` filters to ≥ 0.40.
- Share endpoint returns HTTP 422 below 0.65 with the top-3 alternatives.

**Why CFO weights financial_magnitude 0.40:** a CFO's job is to defend / grow margin. A 15% materiality with a ₹2,000 Cr cascade matters more to them than a 70% materiality with a ₹2 Cr cascade. A CEO weights materiality 0.25 because board narratives are about strategic positioning, not just ₹. An Analyst weights materiality + painpoint_match equally because their job is disclosure compliance, where the *kind* of issue matters more than its size.

### 2.2 Panel ordering (which block renders first inside a role's view)

| # | CFO | CEO | Analyst |
|---|---|---|---|
| 1 | Personal Stakes | Personal Stakes | Personal Stakes |
| 2 | Crisp Insight | Crisp Insight | Crisp Insight |
| 3 | Impact Metrics | Three-Year Trajectory | KPI Table |
| 4 | Recommendations | Stakeholder Map | Framework Alignment |
| 5 | Audit Trail | Board Paragraph | Causal Chain |
| 6 |  | Recommendations | Audit Trail |
| 7 |  |  | Recommendations |

Source of truth: `data/ontology/knowledge_expansion.ttl` `RolePanelPriority` triples — adding a panel for a role means adding triples, not code.

### 2.3 Recommendation type whitelist (which actions a role sees)

| Type | CFO | CEO | Analyst |
|---|---|---|---|
| financial | ✓ | ✗ | ✗ |
| operational | ✓ | ✓ | ✓ |
| compliance | ✓ | ✗ | ✓ |
| esg_positioning | ✗ | ✓ | ✓ |
| strategic | ✗ | ✓ | ✓ |
| brand | ✗ | ✓ | ✗ |
| capital_allocation | ✓ | ✓ | ✗ |
| framework | ✗ | ✓ | ✓ |
| disclosure | ✗ | ✓ | ✓ |
| kpi_tracking | ✗ | ✗ | ✓ |
| audit | ✗ | ✗ | ✓ |

A CEO never sees "file BRSR P6". An Analyst never sees "increase EBITDA by 200 bps". Source: `engine/analysis/recommendation_type_whitelist.py`.

---

## 3. Block-by-block reference

Blocks are grouped by which role(s) render them. Within each group, ordered by panel priority.

### 3.1 Universal blocks (all three roles see these)

#### 3.1.1 Personal Stakes

**What.** A 1-2 sentence paragraph framing what this article means for *you specifically* given your role.
- CFO: revenue % at stake + payback months. *"₹1,900 Cr (4.2% of FY26 revenue) cascades through Opex via the EP→OX edge with 6-month payback on a green-PPA hedge."*
- CEO: 3-year strategic shift. *"Forces a board-narrative pivot from cost-leader to ESG-aligned operator over FY27-29; matches Tata Power's 2024 SECI inflection."*
- Analyst: framework gap + disclosure deadline. *"Triggers BRSR:P6:Q14 + GRI:305-1 disclosure for FY26 annual report, due 2026-09-30."*

**Why.** Without personal stakes, every article reads like a press release. CXOs need the answer to *"why should I keep reading this"* in the first 80 ms.

**How prioritised.** Always panel #1, regardless of role. The text is generated by `engine/analysis/personal_stakes_generator.py`, which dispatches on role and pulls from EvidencePack fields (cascade ₹, peer match, framework section, deadline).

#### 3.1.2 Crisp Insight (headline + hero metric + bulleted takeaways + paragraph)

**What.** The 10-second verdict. Headline + a single hero metric tile + 2-3 bulleted takeaways + an italic narrative paragraph.

| Element | CFO | CEO | Analyst |
|---|---|---|---|
| Headline lead | ₹ figure | competitive positioning | framework section |
| Hero metric label | "P&L exposure" | "Strategic position" | "Disclosure trigger" |
| Hero metric value | `~₹1,900 Cr` | text (e.g. *"MSCI A→AA tailwind"*) | section code (e.g. *"BRSR:P6:Q14"*) |
| Takeaway count | 2-3 | 2-3 | 3 |
| Paragraph word cap | 90 | 80 | 100 |
| Polarity verb | *"compresses"* / *"lifts"* | implicit in framing | neutral; `[unverified]` tagged where applicable |

**Why.** This is what gets quoted. If the CFO reads only one thing, this is it. Hence the verifier passes: subject_line ≤ 90 chars, no `(engine estimate)` noise in subjects, ₹ figure rounded to 2-sig-fig in headline (`~₹1,900 Cr`) but full range in body (`₹1,700-2,100 Cr`).

**How prioritised.** Always panel #2. Generated by `engine/analysis/role_generators/{cfo,ceo,analyst}.py` from a single `EvidencePack`. Optional LLM polish (`SNOWKAP_LLM_ROLE_GENERATORS=1`) replaces only headline / takeaways / paragraph — hero metric stays deterministic. Falls back to deterministic on any LLM failure.

#### 3.1.3 Recommendations (the actions)

**What.** 3-5 specific actions with: action verb · owner · budget · payback · ROI · audit_trail · type · priority.
Example (CFO, contract-win event):
```
1. Lock 70 MW solar PPA at sub-₹4.5/kWh by FY27 Q2
   owner: CFO + Treasury  budget: ₹140 Cr  payback: 38 mo  ROI: 220% (capped)
   type: financial  priority: HIGH
   audit_trail:
     - {source: "primitive_engine", ref: "EP→OX", value: "β=0.018"}
     - {source: "ontology", ref: "event_capacity_addition", value: "actionability=0.8"}
```

**Why.** Without specific actions, ESG news is just news. Snowkap's promise is "now do something". The audit_trail field is the answer to *"why this rec, why this ₹ figure"* — every recommendation has 1-3 entries linking back to ontology / article excerpt / primitive cascade.

**How prioritised.**
- Type whitelist per role (table in §2.3).
- Sort order per role (ontology `RankingSortKey` triples): CFO sorts by ROI DESC, CEO by strategic impact DESC, Analyst by compliance urgency.
- Polarity dispatcher: positive events (contract wins, ESG upgrades) route to `_POSITIVE_GENERATOR_SYSTEM` prompt that forbids "engage SEBI" / "monitor & escalate" defensive language and centres on investor comms, capacity ramp, capital deployment, framework advancement, premium-pricing capture.
- ROI cap by type: compliance 500% · financial 300% · strategic 400% · operational 200%. Capped recs carry `roi_capped: true` + `roi_cap_reason` for the UI tooltip.
- LOW / NON_MATERIAL articles produce **zero** recommendations. "Do nothing" is valid.

### 3.2 CFO-specific blocks

#### 3.2.1 Impact Metrics

**What.** A grid showing the computed cascade in role-appropriate format.
- *P&L exposure*: `₹1,700-2,100 Cr` (range, body context)
- *Margin impact*: `−180 bps`
- *Cash-flow shift*: `₹540 Cr over 4 quarters`
- *Pay-back window*: `6 months`
- *Capital deployed (recommendation total)*: `₹140 Cr`

**Why.** A CFO doesn't read prose for ₹. They scan tiles. Layout mirrors what they'd see in a Bloomberg terminal earnings card.

**How prioritised.** CFO panel #3. Source: `evidence_pack.cascade` + `evidence_pack.confidence_bounds`. Frontend `renderRupee(amount_cr, {context: "body"})` does the 2-sig-fig rounding + range formatting. Tile-level cells use the canonical ₹ figure (not the LLM's prose paraphrase).

#### 3.2.2 Audit Trail (CFO version)

**What.** A compressed table — one row per claim — with: claim · ₹ figure · source · verifier check · provenance.

**Why.** A CFO who's about to quote ₹1,900 Cr on an investor call needs to know exactly where it came from in 5 seconds. Not 5 minutes.

**How prioritised.** CFO panel #5 (after recommendations because CFOs read briefs front-to-back). Source: `__provenance` sidecar + `insight.warnings` + `insight.verifier_corrections`. The Analyst version (§3.4.4) is more verbose because Analysts read briefs back-to-front.

### 3.3 CEO-specific blocks

#### 3.3.1 Three-Year Trajectory

**What.** Two panels: *"Do nothing"* vs *"Act now"*, framed over the dynamic horizon `FY{n+1}-{n+3}`.
Example (Waaree contract-win):
- Do nothing: *"Capacity at risk to ReNew + Adani Green. By FY29 market share −300 bps; board narrative shifts to defensive."*
- Act now: *"Lock 4 GW order book, raise ₹3,000 Cr green bond at sub-7%, claim MSCI A rating by FY28."*

**Why.** Board agendas run on 3-year horizons. A CEO who walks into a board meeting with a 1-quarter ESG metric loses the room. Snowkap forces the 3-year framing.

**How prioritised.** CEO panel #3. Time horizon auto-rolls forward (computed from `datetime.now()`, not hardcoded). The 3-year window was hardcoded `FY27-29` until Phase 13 S2 made it dynamic.

#### 3.3.2 Stakeholder Map

**What.** A 4-6 row table: stakeholder · stance · precedent · likely action.
Polarity-aware via `stakeholderPositiveStance` + `stakeholderPositivePrecedent` predicates (Phase 15 fix). On a Waaree contract win:
- SEBI: BRSR-leader citation, FY24 stewardship circular references
- RBI: Climate Stress Test consultation cited HDFC + ICICI as advanced
- MSCI ESG: 2023 Infosys A→AA on PCAF disclosure precedent
- BlackRock: 2024 Tata Power weight uplift post-Khavda
- Employees: Adani Green attrition decrease post-hiring premium reduction

**Why.** CEOs translate news into stakeholder narratives. *"What will SEBI say?"* / *"What will my biggest investor do?"* / *"What does my workforce see?"* The legacy `query_stakeholder_positions` defaulted to negative-event flavour (Vedanta SCN, YES Bank moratorium); Phase 15 added positive variants so a contract win doesn't trigger a crisis-flavour stakeholder map.

**How prioritised.** CEO panel #4. Source: `data/ontology/stakeholder_positions.ttl` queried with polarity-aware SPARQL.

#### 3.3.3 Board Paragraph

**What.** A ~3-sentence script the CEO can read verbatim to the board. Strategic framing only — no ₹ figures.
Example: *"Waaree's 4 GW SECI win positions us as the cost-credible alternative to Adani Green in the Tier-1 solar EPC bracket. Capital allocation over FY27-29 should pivot 20 bps of capex toward green-finance issuance. The MSCI A→AA pathway opens DJSI eligibility by FY28 Q3."*

**Why.** CEOs don't want talking points. They want the actual sentence. Snowkap writes the sentence.

**How prioritised.** CEO panel #5. Generated by `engine/analysis/ceo_narrative_generator.py` with the `_CEO_SYSTEM` prompt: *"NEVER lead with a ₹ figure. Lead with competitive positioning, stakeholder signal, or strategic optionality. Frame on a 3-year horizon. Reference at least one peer event matching the article's polarity."*

### 3.4 Analyst-specific blocks

#### 3.4.1 KPI Table

**What.** Full-precision table of every quantitative claim: KPI · current value · forecast · unit · calculation method · benchmark · β · lag · confidence.

**Why.** This is the only panel where 2-sig-fig rounding is **disabled**. The Analyst needs `₹1,873.4 Cr` not `~₹1,900 Cr` because the disclosure-report figure has to match the underlying calculation cell-exactly.

**How prioritised.** Analyst panel #3. Source: `evidence_pack.cascade` (full precision) + `evidence_pack.confidence_bounds`. The frontend `renderRupee(amount_cr, {context: "table"})` short-circuits the 2-sig-fig pass.

#### 3.4.2 Framework Alignment

**What.** A 5-row checklist with: framework · section · trigger reason · disclosure status (Disclose / Review / N/A) · deadline.
Capped at 5 rows. Mandatory frameworks (e.g. BRSR for Indian Large Cap) sort first.

Example:
- BRSR : P6:Q14 (Emissions intensity) — **Disclose** by 2026-09-30
- GRI : 305-1 (Direct GHG) — **Disclose** by 2026-09-30
- TCFD : Metrics & Targets — **Review** (voluntary)
- CDP : C6.1 — **Review**
- EU Taxonomy : Article 8 — **N/A** (non-EU listing)

**Why.** Analysts live and die by section codes. A framework name alone is useless ("BRSR" — *which section?*). Snowkap always emits section-level granularity (`BRSR:P6:Q14`) and tags mandatory rules per region × cap-tier via `data/ontology/knowledge_expansion.ttl::MandatoryRule`.

**How prioritised.** Analyst panel #4. Source: `evidence_pack.frameworks`. Region-aware via `_REGIONAL_QUERIES` and `_region_for_country()` (Phase 23 globalisation).

#### 3.4.3 Causal Chain

**What.** Visualised cascade — typically a 3-4 node graph: source primitive → intermediate primitives → outcome node.
Example (EP-OX cascade):
*Energy Price (+12%) → Opex (+₹1,840 Cr) → Gross Margin (−180 bps) → ROE (−40 bps)*

Each edge labelled with β · lag · confidenceLevel. Hops over 6 functional forms (linear / log-linear / threshold / step / ratio / composite). Drawn from `data/ontology/primitives_edges_p2p.ttl` (123 P→P + 48 P→outcome edges).

**Why.** This is the audit reviewer's smoking gun: *"the ₹1,840 Cr Opex figure comes from β=0.018 on the EP→OX edge with a 2-quarter lag, calibrated via β_company = β_ontology × (Adani Power's 40% energy share / 0.15 industry avg)"*. Reproducible by hand.

**How prioritised.** Analyst panel #5. Source: `evidence_pack.causal_chain`. The CFO / CEO views don't show this because they don't need it — but they can still query `evidence_pack` if curious.

#### 3.4.4 Audit Trail (Analyst version)

**What.** Verbose table — every verifier pass that fired, every drift detection, every provenance-stripped tag.

**Why.** The Analyst's job *is* the audit trail. They reference this when responding to a regulator query.

**How prioritised.** Analyst panel #6. Source: `__provenance` + `__cross_role_drift` + `insight.warnings` + `insight.verifier_corrections`.

### 3.5 Cross-cutting blocks (rendered conditionally on all three roles)

#### 3.5.1 Impact Analysis sub-blocks (6 named sections, generated by Stage 10)

These are sub-fields of `insight.impact_analysis` and render across all three roles, but each role highlights different ones first.

| Sub-block | What it is | Why it matters per role |
|---|---|---|
| `esg_positioning` | Competitive ESG narrative shift | CEO leads here |
| `capital_allocation` | Capex / opex / capital structure shift | CFO + CEO co-lead |
| `valuation_cashflow` | DCF / WACC / FCF impact | CFO leads here |
| `compliance_regulatory` | Framework triggers, deadlines, penalties | Analyst leads here |
| `supply_chain_transmission` | β-calibrated cascade across SC primitives | Analyst + CFO co-lead |
| `people_demand` | Workforce, talent, customer-side ripple | CEO leads here |

Source: `engine/analysis/insight_generator.py::_DEEP_INSIGHT_SCHEMA`. Each sub-block is 1-2 sentences, polarity-aware (positive-event articles get a positive `_POSITIVE_INSIGHT_DIRECTIVE` flip on `key_risk` framing).

#### 3.5.2 Confidence Bounds

**What.** A small inline disclosure attached to any quantitative claim: `β=0.018 · lag=2q · method=primitive_cascade · confidence=high`.

**Why.** A CFO who reads ₹1,900 Cr without knowing it has a "low" method confidence may over-commit. The phrase always appears in the Analyst view; for CFO + CEO it surfaces only when method confidence is < `medium` (so the strong cases stay quiet).

**How prioritised.** Inline (not a separate panel). Sourced from the per-edge `confidenceLevel` in the ontology's `CausalEdge` triples.

#### 3.5.3 Decision Window

**What.** A small pill: *"Decide by 2026-09-30 (filing deadline)"* or *"6-month opportunity window"* or *"⚠ Stale: published 47 days ago"*.

**Why.** Without a deadline, "monitor" is the default rec. Decision windows force prioritisation.

**How prioritised.** Inline on every block. Source: `evidence_pack.decision_windows` — for compliance articles it's the filing deadline; for capacity events it's the lag of the primary primitive cascade.

#### 3.5.4 Outside-Focus Badge

**What.** A small orange chip on cards in the persona-personalised feed: *"Outside your focus: governance"*.

**Why.** Persona personalisation re-ranks articles by overlap with the user's `esg_focus`. A CRITICAL article outside the user's focus would otherwise look like a recommendation; the badge clarifies that it's surfaced because of base materiality, not match.

**How prioritised.** Card-level metadata. Source: `apply_persona_to_feed()` returns `outside_focus: bool` per article. Renders on `MiniArticleCard` and `NewsCard`.

---

## 4. End-to-end example — a single article through three lenses

Article: *"Waaree Energies secures 4 GW PSPCL solar order at ₹2.62/kWh, surpasses Tata Power bid by 8%"* (positive contract-win event, FY26 Q1).

### 4.1 What the CFO sees

```
1. Personal Stakes
   ₹3,400-4,200 Cr revenue uplift over FY27-29 (≈ 22% of FY26 revenue).
   6-month payback on lock-in financing.

2. Crisp Insight
   Headline: P&L lifts ~₹3,800 Cr — green bond at sub-7% locks 220% ROI
   Hero metric: P&L exposure → ~₹3,800 Cr
   Takeaways:
     - Revenue uplift: ₹3,400-4,200 Cr range (FY27-29)
     - Margin: +320 bps
     - Lock-in: ₹3,000 Cr green bond at sub-7% (220% ROI capped)
   Paragraph (89 words): "Waaree's PSPCL win lifts P&L by ~₹3,800 Cr…"

3. Impact Metrics (grid)
   P&L exposure: ₹3,400-4,200 Cr  |  Margin: +320 bps
   Cash flow: ₹920 Cr over 6 quarters  |  Payback: 6 months
   Capital deployed: ₹3,000 Cr (green bond)

4. Recommendations (sorted by ROI DESC)
   1. ₹3,000 Cr green bond at sub-7% — 220% ROI (capped, reason: financial cap)
   2. Lock 4 GW order book via PPA — 38 months payback
   3. Investor roadshow Q3 — Sept-Nov FY26

5. Audit Trail
   ₹3,800 Cr  source=primitive_engine  edge=event_contract_win→RV  β=0.022
   220% ROI   capped from 380% raw  reason=financial type 300% cap
```

### 4.2 What the CEO sees

```
1. Personal Stakes
   3-year strategic shift: Waaree moves from "cost alternative" to
   "cost-credible alternative" tier. Tata Power SECI 2024 precedent.

2. Crisp Insight
   Headline: PSPCL 4 GW win unlocks MSCI A path — board narrative pivots
   FY27-29 toward green-finance issuance
   Hero metric: Strategic position → "MSCI A path opens by FY28"
   Takeaways:
     - Polarity-matched peer: Tata Power SECI 2024
     - 3-yr competitive moat: 4 GW order book vs Adani Green's 2.8 GW
     - Capital allocation pivot: 20 bps capex toward green-finance
   Paragraph (78 words, NO ₹): "PSPCL's 4 GW win positions Waaree…"

3. Three-Year Trajectory
   Do nothing → ReNew + Adani Green close the gap; MSCI A path slips to FY30.
   Act now → Lock 4 GW, raise green bond, claim MSCI A by FY28.

4. Stakeholder Map (polarity-positive variants)
   SEBI    → BRSR leader, expedited green-bond filing
   MSCI    → 2023 Infosys A→AA precedent
   BlackRock → Climate Transition mandate; weight uplift likely
   Employees → Hiring-premium reduction; Tata Power post-Khavda parallel
   Civil society → BHRRC 2023 transparency-leader list

5. Board Paragraph (verbatim)
   "Waaree's 4 GW SECI win positions us as the cost-credible alternative
   to Adani Green in the Tier-1 solar EPC bracket. Capital allocation
   over FY27-29 should pivot 20 bps of capex toward green-finance
   issuance. The MSCI A→AA pathway opens DJSI eligibility by FY28 Q3."

6. Recommendations (sorted by strategic impact DESC, NO compliance/audit)
   1. Reframe FY28 board narrative around solar leadership
   2. Capital-allocation pivot toward green bond issuance
   3. Co-marketing with PSPCL on procurement milestones
```

### 4.3 What the Analyst sees

```
1. Personal Stakes
   Disclosure trigger: BRSR:P6:Q14 + GRI:305-1 + TCFD:Metrics&Targets
   FY26 annual report due 2026-09-30.

2. Crisp Insight
   Headline: BRSR:P6:Q14 disclosure trigger — FY26 due 2026-09-30
              [contract-win cascade; β=0.022, lag=2q, method=high]
   Hero metric: Disclosure trigger → BRSR:P6:Q14
   Takeaways:
     - 3 mandatory frameworks triggered
     - β=0.022 on event_contract_win → RV (high confidence)
     - 1 voluntary advancement: DJSI eligibility by FY28
   Paragraph (97 words, every claim with β/lag/method): "The PSPCL…"

3. KPI Table (full precision, no 2-sig-fig)
   Revenue (uplift, FY27): ₹3,847.2 Cr  β=0.022 lag=2q method=high
   Margin Δ: +321 bps  source=cascade
   Capex (green bond): ₹3,000.0 Cr
   Carbon intensity: −18 tCO₂/MWh by FY29

4. Framework Alignment (mandatory first)
   BRSR : P6:Q14 (Emissions intensity)  Disclose  2026-09-30
   GRI  : 305-1 (Direct GHG)            Disclose  2026-09-30
   TCFD : Metrics & Targets             Review   voluntary
   CDP  : C6.1                          Review   voluntary
   EU Taxonomy : Article 8              N/A      non-EU listing

5. Causal Chain
   event_contract_win → RV (β=0.022, lag=2q, linear)
     → CX (capacity_addition; β=0.18, lag=4q, log-linear)
     → GHG (emissions intensity; β=−0.12, lag=8q, threshold)
     → ESGRating (MSCI; β=0.06, lag=12q, step)

6. Audit Trail (verbose)
   verifier_pass=margin_math       OK
   verifier_pass=hallucination_audit  OK (4 ₹ figures, all primitive-sourced)
   verifier_pass=cross_section_drift  OK (max drift 3.1%)
   provenance: 8 figures stripped from prose, all sidecar-preserved
   __cross_role_drift: max 2.1% across CFO/CEO/Analyst views

7. Recommendations (sorted by compliance urgency, NO financial)
   1. File BRSR:P6:Q14 disclosure with PSPCL win cited
   2. Update CDP submission with new Scope 1 trajectory
   3. KPI tracker: add 4 GW order book to FY27 capacity tile
```

---

## 5. Why we believe this is the right shape

Snowkap's bet is that **role-distinct, ontology-grounded, audit-trailed analysis** is the only ESG news product a CFO will keep open. Every block in this document exists because a real CFO / CEO / Analyst told us — sometimes the hard way — what was missing or what was wrong.

| Hard lesson | Block / mechanism that fixed it |
|---|---|
| *"This contract-win brief reads like a crisis."* | Phase 14 polarity-aware Stage 10 directive + positive recommendation prompt + positive stakeholder variants |
| *"₹500 Cr appears nowhere in the article."* | Phase 12 hallucination audit + computed-cascade hard constraints |
| *"My CFO and CEO are reading different ₹ figures on the same article."* | Phase 26 cross-role drift detector (> 5% emits sidecar warning) |
| *"I quoted ₹50 Cr and got asked where it came from."* | `__provenance` sidecar + audit_trail on every recommendation |
| *"It told me to file BRSR for a generic climate news item."* | Phase 14 "do nothing is valid" rule + LOW materiality → zero recs |
| *"The action recommended a 12,000% ROI."* | Phase 17c ROI caps with `roi_capped: true` flag + tooltip |
| *"I'm a CEO; why am I reading framework section codes?"* | Phase 26 recommendation type whitelist (CEO ✗ compliance/audit) |
| *"The 3-year horizon is hardcoded to FY27-29 — it's stale."* | Phase 13 S2 dynamic `FY{n+1}-{n+3}` from `datetime.now()` |
| *"Vedanta SCN keeps appearing on Waaree articles."* | Phase 15 polarity-aware stakeholder positions |
| *"I onboard `siemens.com` and get BRSR queries."* | Phase 23 globalised onboarder — EU region → CSRD / ESRS queries |

The product is not finished — defaults-on for the LLM polish layer, EODHD live financials, and the Mint/ET hero case studies are all on the Q2-Q3 2026 roadmap. But the shape — five inviolable principles, three role lenses, nine divergence points, ten verifier passes, six preflight gates — is settled.

---

*See also*: [PRD.md](./PRD.md) for the high-level product spec, [CLAUDE.md](../CLAUDE.md) for the engineering build log across all 26 phases.
