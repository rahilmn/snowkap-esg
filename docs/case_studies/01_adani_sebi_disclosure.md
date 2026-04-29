# Case Study 1 — SEBI's ₹275 Cr penalty on Adani Power

**The test:** A single breaking article — "SEBI imposes ₹275 crore penalty on Adani Power for alleged disclosure lapses in related-party transactions" — fed to three systems: Snowkap, GPT-4o, Gemini. What does a CFO, CEO, or ESG analyst actually get?

**Headline result:** Snowkap **81/100** vs GPT-4o **43/100** on a 30-dimension professional scorecard. **1.87× ratio.** Zero CFO or CEO dimensions lost to GPT-4o.

---

## The scoreboard

| Persona | Snowkap | GPT-4o | Gap |
|---|---|---|---|
| CFO | 21/30 (70%) | 12/30 (40%) | **+30 pts** |
| CEO | 26/30 (87%) | 9/30 (30%) | **+57 pts** |
| ESG Analyst | 26/30 (87%) | 18/30 (60%) | **+27 pts** |
| **Total** | **73/90 (81%)** | **39/90 (43%)** | **+38 pts** |

Win rate by persona: Snowkap wins **15 dimensions**, GPT-4o wins **1**, 14 ties.

---

## The three things ChatGPT didn't do

### 1. Every ₹ figure carries its origin

Snowkap:
> *"P&L exposure: **₹275 Cr penalty (from article)** + **₹19.2 Cr revenue impact (engine estimate)**"*

GPT-4o:
> *"Estimated financial impact: ₹275 crore (from article) plus potential indirect costs."*

Snowkap tags every ₹ figure as either `(from article)` or `(engine estimate)` so the CFO knows which numbers are stated in the source vs derived from our cascade engine. The output verifier enforces this on every field. GPT-4o tags the article figure but presents "potential indirect costs" without distinguishing its estimate from the article's statements — the single most common credibility failure in LLM-generated ESG analysis.

### 2. Named precedents with company + year + ₹ + outcome

Snowkap:
> *"Precedent: Vedanta Konkola Child Labour NGO Escalation — Vedanta Resources (2020) — cost: ₹450 Cr — outcome: MSCI ESG B → CCC; bond spread +28 bps for 6 months; ERM-led supplier audit restored rating"*

GPT-4o:
> *"Similar regulatory enforcement cases in Indian capital markets have historically triggered spread widening and rating reviews."*

Snowkap cites one of **30 named precedents** from a curated library (Vedanta 2020, PNB 2018, Adani Hindenburg 2023, Azure Power 2022, YES Bank 2020, etc.) — each with date, ₹ cost, duration, outcome, recovery path. ChatGPT cites "historically" without naming a single case. A CFO cannot act on "historically" — a CFO acts on "Vedanta 2020 ₹450 Cr, 24-month recovery."

### 3. Stakeholder map with specific escalation windows

Snowkap:
> *"Stakeholder: SEBI — stance: Enforcement escalation if remediation plan absent within 30-60 days. Formal show-cause → interim penalty → appeal window. Settlement via Consent Mechanism in 40-60% of cases — precedent: Vedanta 2020 SCN → ₹450 Cr settlement"*
>
> *"Stakeholder: MSCI ESG Ratings — stance: Rating action (e.g., BBB → BB) within 90-180 days of material event — precedent: Vedanta 2020 B→CCC → MSCI ESG Leaders exclusion"*

GPT-4o:
> *"Stakeholders affected include regulators, investors, and rating agencies who may respond negatively."*

Snowkap names **4-6 stakeholders** per output, with their **specific escalation window** (30-60 days for SEBI, 90-180 days for MSCI, 30-60 days private engagement for BlackRock/NBIM/CalPERS) and **specific precedent behavior**. The nine stakeholder positions in the ontology come from real regulatory precedent and proxy-advisor voting records, not generalised statements.

---

## The ESG Analyst depth gap

### Confidence bounds on every engine-estimated ₹

Snowkap output:
> *"Confidence: ₹294.2 Cr total cascade exposure, source_type=engine_estimate, β=0.15-0.40, lag=2-8 quarters, functional form=linear"*

GPT-4o output:
> *"₹294 crore (estimate) — indirect exposure"*

A senior MSCI analyst looks for β (elasticity), lag window, and functional form before accepting a derived ₹ figure. Snowkap attaches all three for every engine-computed number. Our scorer: **Snowkap 3/3, GPT-4o 0/3**.

### Framework citations with rationale

Snowkap:
> *"BRSR:P6:Q14 — because supply chain labour audit is mandatory for Large Cap Indian listed entities; deadline 2026-05-30"*

GPT-4o:
> *"BRSR applies."*

Framework citation at section level (**BRSR:P6:Q14** not just "BRSR") paired with the mandatory/regional rationale pulled from the ontology. Our scorer: **Snowkap 3/3, GPT-4o 1/3**.

### Peer quartile positioning

Snowkap:
> *"Scope 1 emissions: Adani Power ~80 Mt CO2e, peer quartile P75 (peer median 60 Mt). Peers: NTPC 250 Mt, Tata Power 20 Mt, JSW Energy 22 Mt"*

GPT-4o:
> *"Adani Power's emissions are high relative to peers."*

ESG analysts need quartile positioning, not vague comparisons. Our scorer: **Snowkap 3/3, GPT-4o 0/3**.

---

## The one dimension we lose

| Dimension | Snowkap | GPT-4o |
|---|---|---|
| **ESG Analyst — kpi_table keyword count** | 2/3 (structured, 5 rich KPIs) | 3/3 (mentions 8 KPIs in prose) |

GPT-4o name-drops more ESG KPI keywords ("Scope 1", "LTIFR", "women on board" etc.) without providing values, units, peer medians, or data sources. Our scorer counts keyword mentions, so GPT-4o wins 3-2 on raw count. But on the **professional bar** (KPI with unit + peer median + data source + quartile), Snowkap wins every column where it's populated.

This is a scorer-calibration gap, not a product gap. GPT-4o's advantage here is **surface vocabulary, not analytical depth.**

---

## What this means for the Mint partnership

**The moat is visible in the output.** Readers of Mint's ESG vertical need professional-grade analysis, not keyword-stuffed prose. Snowkap's advantage is three things GPT-4o cannot replicate without a comparable ontology + precedent library:

1. **Computed ₹ exposure tied to the company's own P&L**, calibrated from EODHD/yfinance financials, expressed with β, lag, and functional form
2. **Named precedents with outcomes** — a curated 30-case library (Vedanta 2020, PNB 2018, Azure 2022, YES Bank 2020, Adani Hindenburg 2023…) the LLM cites by reference, not invention
3. **Stakeholder positions with specific escalation windows** — regulator, proxy advisor, rating agency, and institutional investor behavior drawn from real precedent, not LLM hallucination

---

## Technical reproducibility

Run the same comparison yourself:

```bash
python scripts/compare_vs_chatgpt.py \
    --prompt data/inputs/prompts/test_phase3_adani_sebi.txt \
    --company adani-power
```

Runtime ~3 min. Cost ~$0.30 OpenAI spend. Full report including side-by-side raw outputs, per-dimension scoring, and win matrix lands in `docs/comparisons/`.

Report from this run: [`adani-power_2026-04-22_1211.md`](../comparisons/adani-power_2026-04-22_1211.md)
