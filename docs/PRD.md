# Snowkap ESG Intelligence Engine — Product Requirements Document

**Status:** Production (May 2026)
**Owner:** Snowkap product + engineering
**Audience:** Internal team · prospective customers · investors · regulators

---

## 1. Vision

Give every CFO, CEO, and ESG Analyst at a listed company a **10-second decision-grade verdict** on every ESG news event that touches their P&L, board narrative, or disclosure obligation — backed by a fully auditable trail of ontology rules, financial primitives, and verifier passes.

ESG intelligence today is either a 50-page analyst report (too slow, no role specificity) or an LLM summary (fast but fabricates ₹ figures, frameworks, and precedents). Snowkap is the third option: **ontology-driven analysis where every number is computed and every claim is provenance-tagged**, then re-rendered per role.

## 2. Problem

CXOs already drown in ESG news. The pain isn't volume — it's that:

1. **Generic LLM summaries hallucinate.** A "₹500 Cr SEBI penalty risk" appears in a contract-win article. A CFO who quotes it on an investor call loses credibility.
2. **One-size-fits-all is wrong.** A CFO needs the ₹ figure + payback. A CEO needs the 3-year board narrative. An ESG Analyst needs the BRSR section code and disclosure deadline. The same article must read differently to each.
3. **Macro signals force compliance theatre.** "Climate change is a risk → file BRSR" — that's not actionable. Real action requires industry · cap-tier · regulator · primitive cascade specificity.
4. **There's no audit trail.** "Why did you flag this as CRITICAL?" → no answer from a chat-style LLM.

## 3. Target users

### 3.1 CFO (primary persona)

**Role.** Owns P&L, capital allocation, investor communications.
**Job-to-be-done.** "In under 10 seconds, tell me whether this news moves my margin or my access to capital — and by how much."
**Output contract.**
- Headline leads with ₹ figure (e.g. *"P&L compresses ~₹1,900 Cr"*).
- Hero metric is **P&L exposure** in INR Cr (point estimate in headline, ±10% range in body).
- Payback period in months on every recommendation.
- ≤ 90 words in the narrative paragraph.
- ROI on recommendations capped per type (compliance 500%, financial 300%, etc.) with `roi_capped` flag when capped.

### 3.2 CEO

**Role.** Owns board narrative, competitive positioning, strategic optionality.
**Job-to-be-done.** "How does this change the story I tell the board in three years' time, and what does my best competitor do today that I should be doing?"
**Output contract.**
- Headline **never** leads with ₹ figure. Leads with competitive positioning or stakeholder signal (e.g. *"MSCI ESG upgrade lifts board narrative — FY27-29 capital allocation needs reframe"*).
- Hero metric is **Strategic position**.
- Time horizon is dynamic `FY{n+1}-{n+3}` (3-year), auto-rolls forward as the calendar advances.
- Cites at least one polarity-matched peer precedent (e.g. Tata Power SECI Auction for a contract-win event).
- ≤ 80 words narrative.

### 3.3 ESG Analyst

**Role.** Owns disclosure compliance, framework alignment, audit response.
**Job-to-be-done.** "Which framework section am I now obligated to disclose, by when, with what evidence, and at what β confidence?"
**Output contract.**
- Headline leads with a specific framework section code (e.g. *"BRSR:P6:Q14 disclosure trigger — due 2026-09-30"*).
- Hero metric is **Disclosure trigger**.
- Time horizon is the filing deadline.
- Every quantitative claim carries β, lag, and method confidence; unverified claims tagged `[unverified]`.
- KPI table preserves full precision (no 2-sig-fig rounding).
- ≤ 100 words narrative.

## 4. Success metrics

| Metric | Definition | Target |
|---|---|---|
| Hallucination rate | ₹ figures tagged `(from article)` that fail the proximity + value match in the article body | < 2% |
| Cross-role ₹ drift | Same article cited with > 5% divergence across CFO / CEO / Analyst | < 1% of articles |
| CFO surface noise | Articles surfaced to CFO that fail the 6-gate preflight | 0 |
| Latency p95 | Per-article 12-stage pipeline runtime | < 130s |
| Token cost per article | Sum of all OpenAI calls (NLP + insight + recs) | < $0.05 |
| Verifier-warning rate | Articles emitting ≥ 1 verifier warning | < 5% (autonomous-send gate) |
| Time-to-value | Domain entered → dashboard ready with HOME insights | < 5 min |
| Persona match | Articles in user's `esg_focus` shown above those outside | ≥ 80% top-10 |

## 5. Scope

### 5.1 In scope (V1, shipped)

- Ingestion from NewsAPI.ai (full-text) + Google News RSS (back-compat).
- 12-stage analysis pipeline driven by a 9,900-triple OWL2 ontology.
- Three role lenses (CFO / CEO / ESG Analyst) with role-distinct headlines, hero metrics, panel orders, and rec whitelists.
- Persona personalisation (6-question MCQ feeds the criticality scorer).
- Self-evolving ontology (entities + frameworks discovered from articles, gated by confidence + human review).
- Email drip campaigns with role-specific subject lines + briefs.
- Onboarding by domain ("enter tatachemicals.com → analysis in 5 min") — covers 14 country locales, 4 regulatory regions (INDIA, EU, US, UK), 8 stock exchanges.
- Frontend (React 19 + Vite) with role switcher, persona profile, share dialog, drip-campaign admin.

### 5.2 Out of scope (intentional)

- Bring-your-own-LLM (single-provider: OpenAI only — gpt-4.1 + gpt-4.1-mini).
- ESG fund / portfolio analytics (we're news intelligence, not portfolio mgmt).
- Stock-price prediction or trading signals.
- Carbon accounting / Scope 1-2-3 calculation (we surface disclosure obligations, we don't compute the underlying numbers).
- Real-time (sub-second) alerts. Our cadence is hourly ingestion, 30-min ontology promotion, 24h drip.
- Multi-language: Indian + Western English only. Hindi / regional languages V3.
- Personal-email signup (corporate domains only).

### 5.3 Non-goals

- Becoming a general LLM chat product. The chat surface exists only for context-aware Q&A on a specific article; there is no free-form ESG consulting interface.
- Replacing the human auditor or sustainability head. Snowkap surfaces signals and computes exposures; the human decides what to do.

## 6. Core capabilities

### 6.1 The 12-stage pipeline (the product)

Each article runs through:

1. NLP extraction (sentiment, tone, entities, narrative) — `gpt-4.1-mini`.
2. ESG theme tagging across 21 themes — `gpt-4.1-mini`.
3. Event classification across 22 event types — **ontology** (word-boundary keywords + 2-hit confidence bar).
4. Relevance scoring (materiality × industry × cap-tier) — **ontology** SPARQL.
5. Causal chain BFS (0-4 hops over 17 relationship types + 123 primitive edges) — **ontology**.
6. Geographic + climate matching — **ontology**.
7. Risk assessment (10 ESG + 7 TEMPLES categories) — **ontology**.
8. Framework alignment (21 frameworks × regional boosts × mandatory rules) — **ontology**.
9. **Stage 9.5** Criticality scoring (6 components × per-role weights, 3 penalties, 4 bands).
10. Deep insight generation — `gpt-4.1`, **constrained by computed cascade** (₹ figures injected as hard prompt constraints; LLM describes, never invents).
11. Perspective transform — ontology-driven headline rules + per-role generators.
12. **Stage 11.5** Role-distinct payload — EvidencePack → 3 deterministic generators (CFO / CEO / Analyst) with optional LLM polish.
13. REREACT recommendations — `gpt-4.1-mini`, polarity-aware (positive vs negative event archetypes), ROI-capped, role-whitelisted.

10 verifier passes run between Stage 10 and write: margin math · hallucination audit · reused-number audit · cross-section drift · source-tag enforcement · CFO headline hygiene · narrative coherence · low-confidence classification · provenance strip · cross-role drift.

### 6.2 Onboarding

- Operator enters a domain (e.g. `siemens.com`).
- Onboarder resolves the canonical ticker (preferring home-country: NS, BO, "", L, DE, PA, AS, F, T, HK, SS) via yfinance.
- Region is inferred from country (INDIA / EU / US / UK / APAC / GLOBAL).
- 28+ region-tailored ESG news queries are generated (CSRD/ESRS for EU, SEC climate for US, BRSR for India, etc.).
- Industry calibration is fetched (revenue, opex, energy share, labor share) and persisted to `config/companies.json`.
- Pipeline runs end-to-end. First HOME-tier insight visible within ~5 minutes.

### 6.3 Personalisation

- 6-question MCQ at first login: `esg_focus` (multi), `frameworks` (multi), `geographies` (multi), `horizon`, `decision_style`, `risk_appetite`.
- Feed re-rank applies multiplicative boosts (focus +40%, framework +30%, geo +25%, risk_appetite ±15%, click_affinity +20%) on the base criticality score.
- CRITICAL articles are floored at 0.65 so a persona mismatch never hides a CRITICAL.
- Outside-focus articles render an `OutsideFocusBadge` so the user knows when they're seeing something off-axis.

### 6.4 Distribution

- **In-app**: role switcher (CFO / CEO / Analyst) on every article.
- **Email drip**: per-recipient cadence, ontology-templated subject lines for LOW/MODERATE, LLM-crafted for HIGH/CRITICAL where opens matter. CFO/CEO/Analyst briefs are rendered from the same insight with role-specific headline + takeaways. CTA flips from "Read full analysis →" to "Book a 20-min walkthrough →" on the second touch.
- **API**: signed-JWT-gated REST surface for integration with customer dashboards.

## 7. Architecture

### 7.1 Stack (deliberately minimal)

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| LLM | OpenAI only (gpt-4.1 + gpt-4.1-mini) |
| Ontology | rdflib (in-process, persisted to .ttl) |
| JSON storage | Filesystem (`data/outputs/`) |
| Index | SQLite (`data/snowkap.db`) — WAL mode, hourly backups |
| API | FastAPI (thin, ~30 endpoints, read-mostly) |
| Auth | Signed JWT (HS256) + machine-to-machine X-API-Key |
| Frontend | React 19 + Vite + Radix + Tailwind + Zustand |
| News | NewsAPI.ai (primary) + Google News RSS (fallback) |
| Email | Resend |
| Hosting | Replit (production); deployable to any Linux box |
| Scheduling | APScheduler in-process |
| Observability | structlog (JSON) + Sentry + Prometheus `/metrics` |

Removed from the legacy stack: PostgreSQL, Redis, Celery, MinIO, Docker, Kubernetes, Anthropic SDK, Zep Cloud, JWT magic links, Alembic, SQLAlchemy. ~95% fewer dependencies than the V0 design.

### 7.2 Five inviolable principles

1. **Ontology IS the intelligence.** Domain knowledge lives in RDF triples, not Python dicts. Adding a new ESG topic, framework, or rule means adding triples, not writing code. (Phase 15 grep-verified zero hardcoded domain dicts in `engine/`.)
2. **Numbers are computed, not generated.** Every ₹ figure that touches a user comes from `primitive_engine.compute_cascade()`. The LLM describes computed values; it never invents them. 10 verifier passes enforce this.
3. **"Do nothing" is valid.** Macro signals don't force compliance recommendations. LOW / NON_MATERIAL articles produce zero recommendations. CFOs do not get spam.
4. **Role distinctness is end-to-end.** CFO / CEO / Analyst differ at 9 points (criticality weights · rec whitelist · headline lead · hero metric · time horizon · evidence type · word cap · panel order · LLM-prompt constraints). It is not a cosmetic relabel.
5. **Outputs are JSONB-compatible.** Every insight file is valid JSON, no trailing commas, no Python-specific types. SQLite index rows reference JSON paths; the JSON is the source of truth.

## 8. Target companies (V1)

Initial seeded coverage:

| Company | Slug | Industry | Cap |
|---|---|---|---|
| ICICI Bank | `icici-bank` | Financials | Large |
| YES Bank | `yes-bank` | Financials | Mid |
| IDFC First Bank | `idfc-first-bank` | Financials | Mid |
| Waaree Energies | `waaree-energies` | Renewable | Mid |
| Singularity AMC | `singularity-amc` | Asset Mgmt | Small |
| Adani Power | `adani-power` | Power | Large |
| JSW Energy | `jsw-energy` | Power | Large |

Any other listed company can be onboarded in < 5 minutes via the domain-entry flow (Phase 23 globalised the onboarder beyond India-only).

## 9. Roadmap (post-V1 signal — not commitments)

| Quarter | Focus |
|---|---|
| Q2 2026 | Default-on LLM polish on role generators (currently env-flag-gated); NewsAPI.ai 1-token billing verification; replace legacy CrispInsight with RoleDistinctView in production |
| Q3 2026 | EODHD live financial calibration (replace industry-default revenue/opex with live quarterly figures); Stage 10 caching for cost reduction |
| Q4 2026 | Mint/ET hero case studies + commercial deck; vs-ChatGPT/Gemini benchmark harness; portfolio-mode (group of companies) |
| 2027+ | Hindi + regional language briefs; mobile-first reading surface; analyst-team collaboration features |

## 10. Risks and mitigations

| Risk | Mitigation in place |
|---|---|
| LLM hallucinates a ₹ figure | Computed-cascade hard constraints + audit_source_tags verifier downgrades unjustified `(from article)` tags |
| LLM frames a positive event as a crisis | Polarity-aware Stage 10 directive + positive-event recommendation system prompt + narrative-coherence verifier |
| Cross-role inconsistency erodes trust | Cross-role drift detector emits `__cross_role_drift` sidecar on > 5% divergence |
| CFO surface noise | 6-gate preflight (financial_impact_quantified · framework_mapped · no_stale_data · polarity_coherent · numeric_consistent · stakeholder_polarity_matched) gates everything CFOs see |
| Stale ontology | Self-evolving ontology promoter (Phase 19) auto-learns entities from articles with confidence ≥ 0.8 + 3+ sightings + 2+ sources |
| Operator can't trace a number | `__provenance` sidecar + `audit_trail` field + 10 verifier reports persisted on every insight |
| Single LLM vendor | OpenAI dependency is acknowledged; switching cost is moderate (only Stages 1, 2, 10, 12 — 15% of intelligence is LLM). Stages 3-9, 11 are ontology-driven and vendor-independent. |

## 11. Distribution & access model

- Customer logs in with corporate email (personal domains blocked).
- JWT carries `company_id` claim → tenant-scoped feed.
- Snowkap sales admin (`sales@snowkap.co.in`) has cross-tenant view ("All Companies" tab).
- Other Snowkap super-admins (`ci@`, `newsletter@`) can onboard + share but **cannot** see cross-tenant aggregate (Phase 24.1 lockdown).
- Articles flagged `cfo_preflight_status = FAIL` are hidden from CFO surface, visible to Analyst surface (for audit).
- Share endpoint returns HTTP 422 with the top-3 alternatives when invoked on a sub-0.65 article — the email composer refuses to send LOW-criticality items.

## 12. Open questions / not-yet-decided

1. **Pricing model.** Per-tenant seat? Per-article? Per-newsletter-send? Currently free for the 7 pilot tenants.
2. **NewsAPI.ai 1-token billing.** Documented as an assumption in `engine/ingestion/news_router.py`; needs operator verification once the NewsAPI.ai docs are reachable.
3. **Multi-tenant ontology editing.** Currently every tenant shares the same global `data/ontology/*.ttl`. Per-tenant overrides are deferred.
4. **Mobile app.** No native app planned for V1. Web is mobile-responsive but not optimised for swipe-feed reading; SwipeFeedPage was retired during Phase 10 simplification.

---

*See also*: [Analysis Blocks by Role](./ANALYSIS_BLOCKS_BY_ROLE.md) for the per-block breakdown of what each CFO / CEO / Analyst panel renders, why it matters, and how it's prioritised.
